import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("MILHAS_DB", "/data/milhas.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    url TEXT PRIMARY KEY,
    fonte TEXT,
    titulo TEXT,
    publicado TEXT,
    coletado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS promocoes (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    origem TEXT,
    destino TEXT,
    programas TEXT,
    bonus_pct INTEGER,
    desconto_pct INTEGER,
    milheiro REAL,
    validade TEXT,
    titulo TEXT,
    url TEXT,
    fonte TEXT,
    chave TEXT UNIQUE,
    criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trechos (
    id INTEGER PRIMARY KEY,
    origem TEXT NOT NULL,
    destino TEXT NOT NULL,
    data_ida TEXT NOT NULL,
    data_volta TEXT,
    flex_dias INTEGER DEFAULT 0,
    cabine TEXT DEFAULT 'economica',
    adultos INTEGER DEFAULT 1,
    max_reais REAL,
    max_milhas INTEGER,
    palavras TEXT,
    observacao TEXT,
    ativo INTEGER DEFAULT 1,
    criado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS precos (
    id INTEGER PRIMARY KEY,
    trecho_id INTEGER NOT NULL REFERENCES trechos(id) ON DELETE CASCADE,
    data_ida TEXT,
    data_volta TEXT,
    valor REAL NOT NULL,
    cia TEXT,
    link TEXT,
    fonte TEXT,
    coletado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_precos_trecho ON precos(trecho_id, coletado_em);
CREATE TABLE IF NOT EXISTS milhas_cotacoes (
    id INTEGER PRIMARY KEY,
    trecho_id INTEGER NOT NULL REFERENCES trechos(id) ON DELETE CASCADE,
    sentido TEXT NOT NULL,              -- ida | volta
    data_voo TEXT,
    programa TEXT,
    cabine TEXT,
    milhas INTEGER NOT NULL,
    assentos INTEGER,
    cias TEXT,
    direto INTEGER,
    origem TEXT,
    destino TEXT,
    availability_id TEXT,
    coletado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_milhas_trecho ON milhas_cotacoes(trecho_id, sentido, coletado_em);
CREATE TABLE IF NOT EXISTS explorar (
    id INTEGER PRIMARY KEY,
    programa TEXT,
    cabine TEXT,
    origem TEXT,
    destino TEXT,
    regiao_destino TEXT,
    data_voo TEXT,
    milhas INTEGER,
    assentos INTEGER,
    cias TEXT,
    direto INTEGER,
    availability_id TEXT,
    coletado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_explorar ON explorar(programa, cabine, milhas);
CREATE TABLE IF NOT EXISTS alertas (
    chave TEXT PRIMARY KEY,
    valor REAL,
    enviado_em TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


MIGRACOES = {
    # tabela: [(coluna, definição)] — colunas adicionadas depois da primeira versão
    "trechos": [("cias", "TEXT")],
}


def init() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with tx() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        for tabela, colunas in MIGRACOES.items():
            existentes = {r["name"] for r in c.execute(f"PRAGMA table_info({tabela})")}
            for nome, definicao in colunas:
                if nome not in existentes:
                    c.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {definicao}")


def trechos_ativos() -> list[dict]:
    with tx() as c:
        rows = c.execute(
            "SELECT * FROM trechos WHERE ativo = 1 AND data_ida >= date('now') ORDER BY data_ida"
        ).fetchall()
    return [dict(r) for r in rows]
