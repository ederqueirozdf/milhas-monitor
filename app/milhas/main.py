import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db, jobs
from .config import CONFIG

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

API_TOKEN = os.environ.get("MILHAS_API_TOKEN", "")
TAREFAS = {
    "rss": jobs.run_rss,
    "voos": jobs.run_voos,
    "resumo": jobs.run_resumo,
    "limpeza": jobs.run_limpeza,
    "teste": jobs.run_teste,
}


def agendador() -> BackgroundScheduler:
    tz = ZoneInfo(CONFIG["timezone"])
    ag = CONFIG["agenda"]
    s = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 900})
    s.add_job(jobs.run_rss, "interval", minutes=ag["rss_minutos"], id="rss", next_run_time=datetime.now(tz))
    s.add_job(jobs.run_voos, "interval", hours=ag["voos_horas"], id="voos")
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


app = FastAPI(title="milhas-monitor", lifespan=lifespan)


def autenticar(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(401, "token inválido")


Cabine = Literal["economica", "premium", "executiva", "primeira"]


class TrechoBase(BaseModel):
    @field_validator("origem", "destino", mode="before", check_fields=False)
    @classmethod
    def maiusculo(cls, v):
        return v.upper().strip() if isinstance(v, str) else v


class TrechoIn(TrechoBase):
    origem: str = Field(pattern=r"^[A-Z]{3}$")
    destino: str = Field(pattern=r"^[A-Z]{3}$")
    data_ida: date
    data_volta: date | None = None
    flex_dias: int = Field(0, ge=0, le=15)
    cabine: Cabine = "economica"
    adultos: int = Field(1, ge=1, le=9)
    max_reais: float | None = Field(None, gt=0)
    max_milhas: int | None = Field(None, gt=0)
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
    origem: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    destino: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    data_ida: date | None = None
    data_volta: date | None = None
    flex_dias: int | None = Field(None, ge=0, le=15)
    cabine: Cabine | None = None
    adultos: int | None = Field(None, ge=1, le=9)
    max_reais: float | None = Field(None, gt=0)
    max_milhas: int | None = Field(None, gt=0)
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


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/trechos", dependencies=[Depends(autenticar)])
def listar_trechos(todos: bool = False):
    sql = "SELECT * FROM trechos" + ("" if todos else " WHERE ativo = 1") + " ORDER BY data_ida"
    with db.tx() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


@app.post("/trechos", status_code=201, dependencies=[Depends(autenticar)])
def criar_trecho(t: TrechoIn):
    dados = {k: _sql_valor(v) for k, v in t.model_dump().items()}
    colunas = ", ".join(dados)
    with db.tx() as c:
        cur = c.execute(f"INSERT INTO trechos ({colunas}) VALUES ({', '.join('?' * len(dados))})", tuple(dados.values()))
        return _trecho(c, cur.lastrowid)


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


@app.post("/executar/{tarefa}", status_code=202, dependencies=[Depends(autenticar)])
def executar(tarefa: str, bg: BackgroundTasks):
    if tarefa not in TAREFAS:
        raise HTTPException(404, f"tarefas válidas: {', '.join(TAREFAS)}")
    bg.add_task(TAREFAS[tarefa])
    return {"agendada": tarefa}
