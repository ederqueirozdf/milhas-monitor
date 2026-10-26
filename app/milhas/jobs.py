import functools
import logging
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape

from . import db, flights, notify, rss, seats
from .config import CONFIG
from .extract import Extrator, chave_promo, menciona_trecho, normalizar

log = logging.getLogger(__name__)
extrator = Extrator(CONFIG["programas"])
_travas: dict[str, threading.Lock] = defaultdict(threading.Lock)

# Última execução de cada tarefa e situação de cada feed (exposto em /status).
ESTADO: dict = {"tarefas": {}, "feeds": {}}


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def exclusivo(fn):
    """Evita que a mesma tarefa rode duas vezes ao mesmo tempo (agenda + chamada manual)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        trava = _travas[fn.__name__]
        if not trava.acquire(blocking=False):
            log.info("%s já está em execução; ignorando", fn.__name__)
            return
        nome = fn.__name__.removeprefix("run_")
        ESTADO["tarefas"][nome] = {"inicio": _agora(), "fim": None, "erro": None}
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.exception("falha em %s", fn.__name__)
            ESTADO["tarefas"][nome]["erro"] = str(exc)[:300]
        finally:
            ESTADO["tarefas"][nome]["fim"] = _agora()
            trava.release()

    return wrapper


def _registrar_alerta(chave: str, valor: float | None = None) -> bool:
    """True se o alerta é inédito (e o registra)."""
    with db.tx() as c:
        cur = c.execute("INSERT OR IGNORE INTO alertas(chave, valor) VALUES (?, ?)", (chave, valor))
        return cur.rowcount == 1


@exclusivo
def run_rss():
    limite = datetime.now(timezone.utc) - timedelta(hours=CONFIG.get("janela_horas", 48))
    alertar = set(CONFIG.get("alertar_tipos", []))
    trechos = db.trechos_ativos()
    for feed in CONFIG["feeds"]:
        try:
            posts = rss.buscar(feed["url"])
        except Exception as exc:
            log.warning("feed %s falhou: %s", feed["nome"], exc)
            ESTADO["feeds"][feed["nome"]] = {"em": _agora(), "ok": False, "erro": str(exc)[:200]}
            continue
        novos = 0
        for post in posts:
            with db.tx() as c:
                cur = c.execute(
                    "INSERT OR IGNORE INTO posts(url, fonte, titulo, publicado) VALUES (?, ?, ?, ?)",
                    (post.url, feed["nome"], post.titulo, post.publicado.isoformat() if post.publicado else None),
                )
            if cur.rowcount == 0:
                continue
            novos += 1
            if post.publicado and post.publicado < limite:
                continue
            _processar_post(post, feed["nome"], alertar, trechos)
        log.info("feed %s: %d posts novos", feed["nome"], novos)
        ESTADO["feeds"][feed["nome"]] = {"em": _agora(), "ok": True, "posts_novos": novos}


def _processar_post(post: rss.Post, fonte: str, alertar: set[str], trechos: list[dict]):
    promo = extrator.analisar(post.titulo, post.resumo)
    if promo and promo.tipo != "passagem":
        with db.tx() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO promocoes
                   (tipo, origem, destino, programas, bonus_pct, desconto_pct, milheiro,
                    validade, titulo, url, fonte, chave)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    promo.tipo, promo.origem, promo.destino, ", ".join(promo.programas),
                    promo.bonus_pct, promo.desconto_pct, promo.milheiro, promo.validade,
                    post.titulo, post.url, fonte, chave_promo(promo, post.url),
                ),
            )
        if cur.rowcount == 1 and promo.tipo in alertar:
            notify.enviar(notify.formatar_promo(promo, post.titulo, post.url, fonte))
        return

    # Passagem ou post não classificado: só interessa se citar um trecho monitorado.
    texto = normalizar(f"{post.titulo} {post.resumo}")
    for t in trechos:
        if menciona_trecho(texto, t, CONFIG.get("cidades", {})) and _registrar_alerta(f"post|{t['id']}|{post.url}"):
            notify.enviar(notify.formatar_post_trecho(t, post.titulo, post.url, fonte))


@exclusivo
def run_voos(trecho_id: int | None = None):
    if not flights.SERPAPI_KEY:
        log.info("SERPAPI_KEY não configurada; busca de voos desativada")
        return
    cfg = CONFIG.get("voos", {})
    trechos = [t for t in db.trechos_ativos() if trecho_id is None or t["id"] == trecho_id]
    log.info("buscando preços de %d trecho(s)", len(trechos))
    for t in trechos:
        with db.tx() as c:
            hist = c.execute(
                "SELECT MIN(valor) AS minimo, COUNT(*) AS n FROM precos WHERE trecho_id = ?", (t["id"],)
            ).fetchone()
        cotacoes = []
        for ida, volta in flights.datas_candidatas(t, cfg.get("max_buscas_por_trecho", 2)):
            try:
                cot = flights.buscar(t, ida, volta)
            except Exception as exc:
                log.warning("busca trecho #%s %s falhou: %s", t["id"], ida, flights.erro_seguro(exc))
                continue
            if cot:
                cotacoes.append(cot)
                with db.tx() as c:
                    c.execute(
                        """INSERT INTO precos(trecho_id, data_ida, data_volta, valor, cia, link, fonte)
                           VALUES (?, ?, ?, ?, ?, ?, 'serpapi')""",
                        (t["id"], cot.data_ida, cot.data_volta, cot.valor, cot.cia, cot.link),
                    )
        if cotacoes:
            _avaliar_voo(t, min(cotacoes, key=lambda x: x.valor), hist, cfg)


def _avaliar_voo(t: dict, melhor: flights.Cotacao, hist, cfg: dict):
    motivo = None
    if t.get("max_reais") and melhor.valor <= t["max_reais"]:
        motivo = f"abaixo do seu teto de {notify._reais(t['max_reais'])}"
    elif hist["n"] >= cfg.get("amostras_historico", 4) and hist["minimo"] and melhor.valor < hist["minimo"]:
        motivo = f"menor preço já visto (antes: {notify._reais(hist['minimo'])})"
    if not motivo:
        return

    chave = f"voo|{t['id']}"
    with db.tx() as c:
        ultimo = c.execute(
            "SELECT valor, enviado_em >= datetime('now', '-7 days') AS recente FROM alertas WHERE chave = ?",
            (chave,),
        ).fetchone()
    queda = 1 - cfg.get("queda_minima_pct", 5) / 100
    if ultimo and ultimo["recente"] and melhor.valor > ultimo["valor"] * queda:
        return
    if notify.enviar(notify.formatar_voo(t, melhor, motivo)):
        with db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO alertas(chave, valor, enviado_em) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (chave, melhor.valor),
            )


def _pode_realertar(chave: str, valor: float, queda_pct: float) -> bool:
    """Inédito, ou caiu queda_pct% desde o último alerta, ou o último tem mais de 7 dias."""
    with db.tx() as c:
        ultimo = c.execute(
            "SELECT valor, enviado_em >= datetime('now', '-7 days') AS recente FROM alertas WHERE chave = ?", (chave,)
        ).fetchone()
    return not (ultimo and ultimo["recente"] and valor > ultimo["valor"] * (1 - queda_pct / 100))


def _gravar_alerta(chave: str, valor: float):
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO alertas(chave, valor, enviado_em) VALUES (?, ?, CURRENT_TIMESTAMP)", (chave, valor))


def resumo_milhas(c, t: dict) -> dict | None:
    """Melhor oferta de cada sentido na coleta mais recente, total, valor do milheiro e motivos."""
    cfg = CONFIG.get("seats", {})
    sentidos = {}
    for sentido in (["ida", "volta"] if t.get("data_volta") else ["ida"]):
        ultima = c.execute("SELECT MAX(coletado_em) FROM milhas_cotacoes WHERE trecho_id = ? AND sentido = ?",
                           (t["id"], sentido)).fetchone()[0]
        if not ultima:
            return None
        melhor = c.execute(
            "SELECT * FROM milhas_cotacoes WHERE trecho_id = ? AND sentido = ? AND coletado_em = ? ORDER BY milhas LIMIT 1",
            (t["id"], sentido, ultima)).fetchone()
        hist = c.execute(
            "SELECT MIN(milhas) AS minimo, COUNT(DISTINCT coletado_em) AS coletas FROM milhas_cotacoes WHERE trecho_id = ? AND sentido = ?",
            (t["id"], sentido)).fetchone()
        sentidos[sentido] = {"melhor": dict(melhor), "minimo": hist["minimo"], "coletas": hist["coletas"], "coletado_em": ultima}
    total = sum(s["melhor"]["milhas"] for s in sentidos.values())
    preco = c.execute("SELECT MIN(valor) FROM precos WHERE trecho_id = ? AND coletado_em >= datetime('now', '-3 days')",
                      (t["id"],)).fetchone()[0]
    valor = round(preco / (total / 1000), 2) if preco and total else None
    programas = {s["melhor"]["programa"] for s in sentidos.values()}
    alvo = CONFIG.get("milheiro_alvo", {}).get(seats.PROGRAMAS.get(programas.pop(), "")) if len(programas) == 1 else None
    motivos = seats.avaliar(total, t.get("max_milhas"), sum(s["minimo"] for s in sentidos.values()),
                            min(s["coletas"] for s in sentidos.values()), cfg.get("amostras_historico", 4), valor, alvo)
    return {"sentidos": sentidos, "total": total, "preco_dinheiro": preco, "valor_milheiro": valor,
            "alvo": alvo, "motivos": motivos, "oportunidade": bool(motivos)}


@exclusivo
def run_milhas(trecho_id: int | None = None):
    if seats.fonte_ativa() == "desligado":
        log.info("Seats.aero desligada (fonte: api sem SEATS_AERO_API_KEY)")
        return
    cfg = CONFIG.get("seats", {})
    trechos = [t for t in db.trechos_ativos() if trecho_id is None or t["id"] == trecho_id]
    log.info("buscando milhas de %d trecho(s) na Seats.aero (%s)", len(trechos), seats.fonte_ativa())
    for t in trechos:
        pernas = [("ida", t["origem"], t["destino"], t["data_ida"])]
        if t.get("data_volta"):
            pernas.append(("volta", t["destino"], t["origem"], t["data_volta"]))
        for sentido, origem, destino, data_voo in pernas:
            try:
                ofertas = seats.buscar_trecho(origem, destino, date.fromisoformat(data_voo), t.get("flex_dias") or 0,
                                              t.get("cabine") or "economica", cfg.get("programas") or None, t.get("cias"))
            except Exception as exc:
                log.warning("milhas trecho #%s (%s) falhou: %s", t["id"], sentido, seats.erro_seguro(exc))
                continue
            with db.tx() as c:
                agora = c.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
                c.executemany(
                    """INSERT INTO milhas_cotacoes (trecho_id, sentido, data_voo, programa, cabine, milhas, assentos, cias,
                                                    direto, origem, destino, availability_id, taxas, fonte, coletado_em)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(t["id"], sentido, o.data_voo, o.programa, o.cabine, o.milhas, o.assentos, o.cias, int(o.direto),
                      o.origem, o.destino, o.availability_id, o.taxas, o.fonte, agora)
                     for o in ofertas[: cfg.get("ofertas_por_sentido", 10)]])
            log.info("trecho #%s %s: %d ofertas em milhas", t["id"], sentido, len(ofertas))
        _alertar_milhas(t, cfg)


def _alertar_milhas(t: dict, cfg: dict):
    with db.tx() as c:
        r = resumo_milhas(c, t)
    if not r or not r["oportunidade"]:
        return
    chave = f"milhas|{t['id']}"
    if _pode_realertar(chave, r["total"], cfg.get("queda_minima_pct", 5)) and notify.enviar(notify.formatar_milhas(t, r)):
        _gravar_alerta(chave, r["total"])


@exclusivo
def run_explorar():
    if seats.fonte_ativa() == "desligado":
        log.info("Seats.aero desligada; Explorar não roda")
        return
    cfg = CONFIG.get("explorar", {})
    hoje = date.today()
    fim = hoje + timedelta(days=cfg.get("dias", 180))
    origens = set(cfg.get("origens") or [])
    limites = cfg.get("limites", {})
    ano, semana, _ = hoje.isocalendar()
    achados = []
    for cabine in cfg.get("cabines", ["economica"]):
        try:
            ofertas, chamadas, fonte = seats.explorar_cabine(cabine, hoje, fim)
        except Exception as exc:
            log.warning("explorar %s falhou: %s", cabine, seats.erro_seguro(exc))
            continue
        melhores: dict[tuple, seats.Oferta] = {}
        for o in ofertas:
            if origens and o.origem not in origens:
                continue
            k = (o.programa, o.origem, o.destino)
            if k not in melhores or o.milhas < melhores[k].milhas:
                melhores[k] = o
        with db.tx() as c:
            c.execute("DELETE FROM explorar WHERE cabine = ?", (cabine,))
            c.executemany(
                """INSERT INTO explorar (programa, cabine, origem, destino, regiao_destino, data_voo, milhas, assentos,
                                         cias, direto, availability_id, taxas, fonte) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(o.programa, cabine, o.origem, o.destino, o.regiao_destino, o.data_voo, o.milhas, o.assentos, o.cias,
                  int(o.direto), o.availability_id, o.taxas, o.fonte) for o in melhores.values()])
        log.info("explorar %s via %s: %d ofertas, %d rotas, %d chamadas", cabine, fonte, len(ofertas), len(melhores), chamadas)
        for o in melhores.values():
            limite = limites.get(cabine, {}).get(o.regiao_destino or "")
            if limite and o.milhas <= limite:
                achados.append((o.milhas / limite, o, f"explorar|{o.programa}|{cabine}|{o.origem}|{o.destino}|{ano}-{semana}"))
    novos = []
    for _, o, chave in sorted(achados, key=lambda x: x[0]):
        if len(novos) >= 10:
            break
        if _registrar_alerta(chave, o.milhas):
            novos.append(o)
    if novos:
        notify.enviar(notify.formatar_explorar(novos))


@exclusivo
def run_resumo():
    with db.tx() as c:
        promos = c.execute(
            "SELECT * FROM promocoes WHERE criado_em >= datetime('now', '-1 day') ORDER BY tipo, bonus_pct DESC"
        ).fetchall()
        precos = c.execute(
            """SELECT t.*,
                      (SELECT MIN(valor) FROM precos p WHERE p.trecho_id = t.id
                         AND p.coletado_em >= datetime('now', '-1 day')) AS min_24h,
                      (SELECT MIN(valor) FROM precos p WHERE p.trecho_id = t.id) AS min_total
               FROM trechos t WHERE t.ativo = 1 AND t.data_ida >= date('now') ORDER BY t.data_ida"""
        ).fetchall()
    if not promos and not any(r["min_24h"] for r in precos):
        return

    linhas = ["☀️ <b>Resumo de milhas — últimas 24h</b>"]
    if promos:
        linhas.append("")
        for p in promos:
            detalhe = []
            if p["origem"] or p["destino"]:
                detalhe.append(f"{p['origem'] or 'Parceiros'} → {p['destino'] or '?'}" if p["tipo"] == "transferencia" else (p["destino"] or ""))
            if p["bonus_pct"]:
                detalhe.append(f"{p['bonus_pct']}% bônus")
            if p["desconto_pct"]:
                detalhe.append(f"{p['desconto_pct']}% desc.")
            if p["validade"]:
                detalhe.append(f"até {p['validade']}")
            icone = notify.ICONES.get(p["tipo"], "•")
            resumo = " · ".join(d for d in detalhe if d) or p["titulo"]
            linhas.append(f'{icone} <a href="{escape(p["url"], quote=True)}">{escape(resumo)}</a>')
    if precos:
        linhas.append("")
        linhas.append("<b>Trechos</b>")
        for t in precos:
            atual = notify._reais(t["min_24h"]) if t["min_24h"] else "sem coleta"
            menor = f" (mín. histórico {notify._reais(t['min_total'])})" if t["min_total"] else ""
            linhas.append(escape(f"{notify.descrever_trecho(dict(t))}: {atual}{menor}"))
    notify.enviar("\n".join(linhas))


@exclusivo
def run_limpeza():
    with db.tx() as c:
        c.execute("UPDATE trechos SET ativo = 0 WHERE ativo = 1 AND data_ida < date('now')")
        c.execute("DELETE FROM posts WHERE coletado_em < datetime('now', '-120 days')")
        c.execute("DELETE FROM alertas WHERE enviado_em < datetime('now', '-60 days')")
        c.execute("DELETE FROM precos WHERE coletado_em < datetime('now', '-365 days')")
        c.execute("DELETE FROM milhas_cotacoes WHERE coletado_em < datetime('now', '-365 days')")


def run_teste():
    notify.enviar("✅ milhas-monitor conectado ao Telegram.")
