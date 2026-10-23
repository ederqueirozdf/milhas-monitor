import functools
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape

from . import db, flights, notify, rss
from .config import CONFIG
from .extract import Extrator, chave_promo, menciona_trecho, normalizar

log = logging.getLogger(__name__)
extrator = Extrator(CONFIG["programas"])
_travas: dict[str, threading.Lock] = defaultdict(threading.Lock)


def exclusivo(fn):
    """Evita que a mesma tarefa rode duas vezes ao mesmo tempo (agenda + chamada manual)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        trava = _travas[fn.__name__]
        if not trava.acquire(blocking=False):
            log.info("%s já está em execução; ignorando", fn.__name__)
            return
        try:
            return fn(*args, **kwargs)
        except Exception:
            log.exception("falha em %s", fn.__name__)
        finally:
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
def run_voos():
    if not flights.SERPAPI_KEY:
        log.info("SERPAPI_KEY não configurada; busca de voos desativada")
        return
    cfg = CONFIG.get("voos", {})
    for t in db.trechos_ativos():
        with db.tx() as c:
            hist = c.execute(
                "SELECT MIN(valor) AS minimo, COUNT(*) AS n FROM precos WHERE trecho_id = ?", (t["id"],)
            ).fetchone()
        cotacoes = []
        for ida, volta in flights.datas_candidatas(t, cfg.get("max_buscas_por_trecho", 2)):
            try:
                cot = flights.buscar(t, ida, volta)
            except Exception as exc:
                log.warning("busca trecho #%s %s falhou: %s", t["id"], ida, exc)
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


def run_teste():
    notify.enviar("✅ milhas-monitor conectado ao Telegram.")
