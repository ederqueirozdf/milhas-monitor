import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db, flights, jobs, notify
from .config import CONFIG

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# O httpx registra a URL de cada requisição em INFO, e as URLs da SerpAPI e do Telegram contêm segredos.
logging.getLogger("httpx").setLevel(logging.WARNING)

API_TOKEN = os.environ.get("MILHAS_API_TOKEN", "")
TAREFAS = {
    "rss": jobs.run_rss,
    "voos": jobs.run_voos,
    "resumo": jobs.run_resumo,
    "limpeza": jobs.run_limpeza,
    "teste": jobs.run_teste,
}


def _proxima_busca_voos(tz: ZoneInfo, horas: int) -> datetime:
    """Retoma o ciclo a partir da última cotação, para um restart não atrasar (nem repetir) a busca."""
    agora = datetime.now(tz)
    with db.tx() as c:
        ultima = c.execute("SELECT MAX(coletado_em) FROM precos").fetchone()[0]
    if not ultima:
        return agora + timedelta(minutes=1)
    proxima = datetime.fromisoformat(ultima).replace(tzinfo=timezone.utc) + timedelta(hours=horas)
    return max(proxima, agora + timedelta(minutes=1))


def agendador() -> BackgroundScheduler:
    tz = ZoneInfo(CONFIG["timezone"])
    ag = CONFIG["agenda"]
    s = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900})
    s.add_job(jobs.run_rss, "interval", minutes=ag["rss_minutos"], id="rss", next_run_time=datetime.now(tz))
    s.add_job(jobs.run_voos, "interval", hours=ag["voos_horas"], id="voos",
              next_run_time=_proxima_busca_voos(tz, ag["voos_horas"]))
    hora, minuto = (int(x) for x in ag["resumo_diario"].split(":"))
    s.add_job(jobs.run_resumo, CronTrigger(hour=hora, minute=minuto, timezone=tz), id="resumo")
    s.add_job(jobs.run_limpeza, CronTrigger(hour=3, minute=30, timezone=tz), id="limpeza")
    return s


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not API_TOKEN:
        raise RuntimeError("defina MILHAS_API_TOKEN no .env")
    db.init()
    s = agendador()
    s.start()
    yield
    s.shutdown(wait=False)


# Sem /docs nem /openapi.json: a API fica exposta pelo Traefik.
app = FastAPI(title="milhas-monitor", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def autenticar(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(401, "token inválido")


Cabine = Literal["economica", "premium", "executiva", "primeira"]


RE_CIAS = r"^[A-Z0-9]{2}(,[A-Z0-9]{2})*$"
# Um ou mais aeroportos/códigos de cidade (ex.: "GRU,VCP"), pesquisados juntos numa só busca.
RE_AEROPORTOS = r"^[A-Z]{3}(,[A-Z]{3}){0,5}$"


class TrechoBase(BaseModel):
    @field_validator("origem", "destino", mode="before", check_fields=False)
    @classmethod
    def maiusculo(cls, v):
        if isinstance(v, str):
            return ",".join(dict.fromkeys(c.strip().upper() for c in v.split(",") if c.strip()))
        return v

    @field_validator("cias", mode="before", check_fields=False)
    @classmethod
    def normalizar_cias(cls, v):
        if isinstance(v, str):
            v = ",".join(c.strip().upper() for c in v.split(",") if c.strip())
            return v or None
        return v


class TrechoIn(TrechoBase):
    origem: str = Field(pattern=RE_AEROPORTOS)
    destino: str = Field(pattern=RE_AEROPORTOS)
    data_ida: date
    data_volta: date | None = None
    flex_dias: int = Field(0, ge=0, le=15)
    cabine: Cabine = "economica"
    adultos: int = Field(1, ge=1, le=9)
    max_reais: float | None = Field(None, gt=0)
    max_milhas: int | None = Field(None, gt=0)
    cias: str | None = Field(None, pattern=RE_CIAS)
    palavras: str | None = None
    observacao: str | None = None

    @model_validator(mode="after")
    def datas_validas(self):
        if self.data_ida < date.today():
            raise ValueError("data_ida já passou")
        if self.data_volta and self.data_volta < self.data_ida:
            raise ValueError("data_volta antes da data_ida")
        return self


class TrechoPatch(TrechoBase):
    origem: str | None = Field(None, pattern=RE_AEROPORTOS)
    destino: str | None = Field(None, pattern=RE_AEROPORTOS)
    data_ida: date | None = None
    data_volta: date | None = None
    flex_dias: int | None = Field(None, ge=0, le=15)
    cabine: Cabine | None = None
    adultos: int | None = Field(None, ge=1, le=9)
    max_reais: float | None = Field(None, gt=0)
    max_milhas: int | None = Field(None, gt=0)
    cias: str | None = Field(None, pattern=RE_CIAS)
    palavras: str | None = None
    observacao: str | None = None
    ativo: bool | None = None


def _sql_valor(v):
    return v.isoformat() if isinstance(v, date) else v


def _trecho(c, trecho_id: int) -> dict:
    row = c.execute("SELECT * FROM trechos WHERE id = ?", (trecho_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"trecho {trecho_id} não encontrado")
    return dict(row)


PAINEL = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def painel():
    # A página é pública; os dados vêm da API, que exige o token digitado no login.
    return FileResponse(PAINEL, headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"})


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status", dependencies=[Depends(autenticar)])
def status():
    with db.tx() as c:
        contagens = c.execute(
            """SELECT (SELECT COUNT(*) FROM posts) AS posts,
                      (SELECT COUNT(*) FROM promocoes WHERE criado_em >= datetime('now', '-1 day')) AS promocoes_24h,
                      (SELECT COUNT(*) FROM trechos WHERE ativo = 1 AND data_ida >= date('now')) AS trechos_ativos,
                      (SELECT MAX(coletado_em) FROM precos) AS ultima_cotacao"""
        ).fetchone()
    return {
        **dict(contagens),
        "telegram_configurado": bool(notify.TOKEN and notify.CHAT_ID),
        "busca_voos_ativa": bool(flights.SERPAPI_KEY),
        "agenda": CONFIG["agenda"],
        **jobs.ESTADO,
    }


@app.get("/trechos", dependencies=[Depends(autenticar)])
def listar_trechos(todos: bool = False):
    sql = "SELECT * FROM trechos" + ("" if todos else " WHERE ativo = 1") + " ORDER BY data_ida"
    with db.tx() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


@app.post("/trechos", status_code=201, dependencies=[Depends(autenticar)])
def criar_trecho(t: TrechoIn, bg: BackgroundTasks):
    dados = {k: _sql_valor(v) for k, v in t.model_dump().items()}
    colunas = ", ".join(dados)
    with db.tx() as c:
        cur = c.execute(f"INSERT INTO trechos ({colunas}) VALUES ({', '.join('?' * len(dados))})", tuple(dados.values()))
        novo = _trecho(c, cur.lastrowid)
    if flights.SERPAPI_KEY:
        bg.add_task(jobs.run_voos, novo["id"])
    return novo


@app.post("/trechos/{trecho_id}/buscar", status_code=202, dependencies=[Depends(autenticar)])
def buscar_precos(trecho_id: int, bg: BackgroundTasks):
    if not flights.SERPAPI_KEY:
        raise HTTPException(409, "Busca de preços desligada: falta SERPAPI_KEY no .env do milhas-monitor")
    with db.tx() as c:
        t = _trecho(c, trecho_id)
    if not t["ativo"]:
        raise HTTPException(409, "Rota pausada: retome antes de buscar preços")
    bg.add_task(jobs.run_voos, trecho_id)
    return {"agendada": trecho_id}


@app.patch("/trechos/{trecho_id}", dependencies=[Depends(autenticar)])
def editar_trecho(trecho_id: int, t: TrechoPatch):
    dados = {k: _sql_valor(v) for k, v in t.model_dump(exclude_unset=True).items()}
    with db.tx() as c:
        _trecho(c, trecho_id)
        if dados:
            sets = ", ".join(f"{k} = ?" for k in dados)
            c.execute(f"UPDATE trechos SET {sets} WHERE id = ?", (*dados.values(), trecho_id))
        return _trecho(c, trecho_id)


@app.delete("/trechos/{trecho_id}", dependencies=[Depends(autenticar)])
def remover_trecho(trecho_id: int):
    with db.tx() as c:
        t = _trecho(c, trecho_id)
        c.execute("DELETE FROM trechos WHERE id = ?", (trecho_id,))
    return {"removido": t}


@app.get("/trechos/{trecho_id}/precos", dependencies=[Depends(autenticar)])
def precos_trecho(trecho_id: int, limite: int = 30):
    with db.tx() as c:
        t = _trecho(c, trecho_id)
        rows = c.execute(
            "SELECT data_ida, data_volta, valor, cia, coletado_em FROM precos WHERE trecho_id = ? ORDER BY coletado_em DESC LIMIT ?",
            (trecho_id, limite),
        ).fetchall()
        minimo = c.execute("SELECT MIN(valor) FROM precos WHERE trecho_id = ?", (trecho_id,)).fetchone()[0]
    return {"trecho": t, "minimo_historico": minimo, "precos": [dict(r) for r in rows]}


@app.get("/promocoes", dependencies=[Depends(autenticar)])
def listar_promocoes(dias: int = 7, tipo: str | None = None, programa: str | None = None):
    sql = "SELECT * FROM promocoes WHERE criado_em >= datetime('now', ?)"
    params: list = [f"-{dias} days"]
    if tipo:
        sql += " AND tipo = ?"
        params.append(tipo)
    if programa:
        sql += " AND (programas LIKE ? OR origem LIKE ? OR destino LIKE ?)"
        params += [f"%{programa}%"] * 3
    with db.tx() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY criado_em DESC LIMIT 100", params).fetchall()]


@app.post("/pesquisar", dependencies=[Depends(autenticar)])
def pesquisar(t: TrechoIn):
    """Busca avulsa (consome 1 busca da SerpAPI); não grava rota nem cotação."""
    if not flights.SERPAPI_KEY:
        raise HTTPException(409, "Busca de preços desligada: falta SERPAPI_KEY no .env do milhas-monitor")
    try:
        return flights.pesquisar(t.model_dump(), t.data_ida, t.data_volta)
    except Exception as exc:
        raise HTTPException(502, f"Falha na busca: {flights.erro_seguro(exc)}")


@app.get("/oportunidades", dependencies=[Depends(autenticar)])
def oportunidades(dias: int = 3):
    """Melhor cotação recente de cada rota ativa, com o motivo de ser (ou não) uma oportunidade."""
    amostras = CONFIG.get("voos", {}).get("amostras_historico", 4)
    with db.tx() as c:
        rows = c.execute(
            """SELECT t.*, p.valor, p.data_ida AS cot_ida, p.data_volta AS cot_volta, p.cia, p.link,
                      p.coletado_em,
                      (SELECT MIN(valor) FROM precos WHERE trecho_id = t.id) AS min_hist,
                      (SELECT AVG(valor) FROM precos WHERE trecho_id = t.id) AS media,
                      (SELECT COUNT(*) FROM precos WHERE trecho_id = t.id) AS amostras
               FROM trechos t
               JOIN precos p ON p.id = (SELECT id FROM precos WHERE trecho_id = t.id
                                        AND coletado_em >= datetime('now', ?) ORDER BY valor LIMIT 1)
               WHERE t.ativo = 1 AND t.data_ida >= date('now')""",
            (f"-{dias} days",),
        ).fetchall()
        alertas_posts = c.execute(
            "SELECT chave, enviado_em FROM alertas WHERE chave LIKE 'post|%' AND enviado_em >= datetime('now', '-30 days')"
        ).fetchall()
        posts = []
        for a in alertas_posts:
            _, tid, url = a["chave"].split("|", 2)
            p = c.execute("SELECT titulo, fonte FROM posts WHERE url = ?", (url,)).fetchone()
            t = c.execute("SELECT origem, destino FROM trechos WHERE id = ?", (tid,)).fetchone()
            if p and t:
                posts.append({"trecho_id": int(tid), "rota": f"{t['origem']}→{t['destino']}", "url": url,
                              "titulo": p["titulo"], "fonte": p["fonte"], "em": a["enviado_em"]})

    voos = []
    for r in rows:
        d = dict(r)
        motivos = []
        if d["max_reais"] and d["valor"] <= d["max_reais"]:
            motivos.append("abaixo do seu teto")
        if d["amostras"] >= amostras and d["valor"] <= d["min_hist"]:
            motivos.append("menor preço já visto")
        abaixo = (d["media"] - d["valor"]) / d["media"] * 100 if d["media"] else 0
        if d["amostras"] >= amostras and abaixo >= 10:
            motivos.append(f"{abaixo:.0f}% abaixo da média")
        d.update(motivos=motivos, oportunidade=bool(motivos), pct_abaixo_media=round(abaixo, 1))
        voos.append(d)
    voos.sort(key=lambda v: (not v["oportunidade"], -v["pct_abaixo_media"]))
    posts.sort(key=lambda p: p["em"], reverse=True)
    return {"voos": voos, "posts": posts}


@app.get("/metricas", dependencies=[Depends(autenticar)])
def metricas(dias: int = 90):
    """Contagens por tipo (7 dias), milheiro observado em promoções de compra e bônus de transferência."""
    janela = f"-{dias} days"
    with db.tx() as c:
        por_tipo = {r["tipo"]: r["n"] for r in c.execute(
            "SELECT tipo, COUNT(*) AS n FROM promocoes WHERE criado_em >= datetime('now', '-7 days') GROUP BY tipo")}
        milheiro = [dict(r) for r in c.execute(
            """SELECT destino AS programa, COUNT(*) AS promocoes, ROUND(AVG(milheiro), 2) AS media, MIN(milheiro) AS menor
               FROM promocoes WHERE tipo = 'compra' AND milheiro IS NOT NULL AND destino IS NOT NULL
                 AND criado_em >= datetime('now', ?) GROUP BY destino ORDER BY media""", (janela,))]
        bonus = [dict(r) for r in c.execute(
            """SELECT destino AS programa, COUNT(*) AS promocoes, ROUND(AVG(bonus_pct)) AS media, MAX(bonus_pct) AS maior
               FROM promocoes WHERE tipo = 'transferencia' AND bonus_pct IS NOT NULL AND destino IS NOT NULL
                 AND criado_em >= datetime('now', ?) GROUP BY destino ORDER BY media DESC""", (janela,))]
    return {
        "promocoes_7d": por_tipo,
        "milheiro_observado": milheiro,
        "bonus_transferencia": bonus,
        "milheiro_alvo": CONFIG.get("milheiro_alvo", {}),
        "dias": dias,
    }


@app.post("/executar/{tarefa}", status_code=202, dependencies=[Depends(autenticar)])
def executar(tarefa: str, bg: BackgroundTasks):
    if tarefa not in TAREFAS:
        raise HTTPException(404, f"tarefas válidas: {', '.join(TAREFAS)}")
    bg.add_task(TAREFAS[tarefa])
    return {"agendada": tarefa}
