"""Cliente do MCP da Seats.aero (https://seats.aero/mcp, Streamable HTTP + JSON-RPC).

Sem login funciona em modo público (até 60 dias de antecedência). Com OAuth (feito uma vez com
`python -m milhas.mcp_login`) usa a conta Pro, com a mesma cota diária da API.
"""

import json
import logging
import threading
import time
from datetime import date

import httpx

from . import db

log = logging.getLogger(__name__)

URL = "https://seats.aero/mcp"
OAUTH = "https://seats.aero/mcp/oauth"
# Só leitura e busca em tempo real: o serviço nunca cria nem apaga alertas na conta.
ESCOPOS = "seats.profile.read seats.reference.read seats.awards.read seats.awards.live seats.alerts.read"
REDIRECT = "http://127.0.0.1:8976/callback"
PROTOCOLO = "2025-06-18"

CHAMADAS = {"dia": date.today().isoformat(), "n": 0}


class ErroMCP(Exception):
    pass


class Credenciais:
    """Access token de 10 min renovado pelo refresh token, que roda a cada uso (guardado no SQLite)."""

    _trava = threading.Lock()
    _token: str | None = None
    _expira = 0.0

    @staticmethod
    def configurado() -> bool:
        return bool(db.kv_get("seats_mcp_client_id") and db.kv_get("seats_mcp_refresh_token"))

    @classmethod
    def limpar_cache(cls):
        cls._token, cls._expira = None, 0.0

    @classmethod
    def access_token(cls) -> str | None:
        if not cls.configurado():
            return None
        with cls._trava:
            if cls._token and time.time() < cls._expira - 60:
                return cls._token
            resp = httpx.post(f"{OAUTH}/token", timeout=30, data={
                "grant_type": "refresh_token", "client_id": db.kv_get("seats_mcp_client_id"),
                "refresh_token": db.kv_get("seats_mcp_refresh_token"), "resource": URL,
            })
            if resp.status_code != 200:
                log.warning("renovação do token do MCP falhou (HTTP %s); seguindo em modo público", resp.status_code)
                if resp.status_code in (400, 401):  # refresh token revogado/expirado: precisa de novo login
                    db.kv_set("seats_mcp_erro", f"HTTP {resp.status_code} ao renovar; refaça o login")
                return None
            dados = resp.json()
            cls._token = dados["access_token"]
            cls._expira = time.time() + int(dados.get("expires_in", 600))
            if dados.get("refresh_token"):
                db.kv_set("seats_mcp_refresh_token", dados["refresh_token"])
            db.kv_set("seats_mcp_erro", None)
            return cls._token


def _ler(resp: httpx.Response) -> dict:
    texto = resp.text
    if texto.lstrip().startswith(("event:", "data:")):  # resposta em SSE
        texto = "".join(l[5:] for l in texto.splitlines() if l.startswith("data:"))
    return json.loads(texto) if texto.strip() else {}


class Cliente:
    def __init__(self, autenticado: bool = True):
        self.token = Credenciais.access_token() if autenticado else None
        self.sessao: str | None = None
        self.http = httpx.Client(timeout=90)
        self._id = 0

    @property
    def modo(self) -> str:
        return "mcp-pro" if self.token else "mcp-publico"

    def _post(self, corpo: dict) -> httpx.Response:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOLO}
        if self.sessao:
            h["Mcp-Session-Id"] = self.sessao
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        resp = self.http.post(URL, headers=h, json=corpo)
        if resp.status_code >= 400:
            raise ErroMCP(f"HTTP {resp.status_code} do MCP da Seats.aero")
        return resp

    def _iniciar(self):
        resp = self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": PROTOCOLO, "capabilities": {}, "clientInfo": {"name": "milhas-monitor", "version": "1.0"}}})
        self.sessao = resp.headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def chamar(self, ferramenta: str, argumentos: dict) -> dict:
        if self._id == 0:
            self._iniciar()
        self._id += 1
        hoje = date.today().isoformat()
        if CHAMADAS["dia"] != hoje:
            CHAMADAS.update(dia=hoje, n=0)
        CHAMADAS["n"] += 1
        dados = _ler(self._post({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                                 "params": {"name": ferramenta, "arguments": argumentos}}))
        if "error" in dados:
            raise ErroMCP(f"{ferramenta}: {dados['error'].get('message', 'erro')}")
        resultado = dados.get("result") or {}
        textos = [p.get("text", "") for p in resultado.get("content") or [] if p.get("type") == "text"]
        if resultado.get("isError"):
            raise ErroMCP(f"{ferramenta}: {' '.join(textos)[:200]}")
        if resultado.get("structuredContent") is not None:
            return resultado["structuredContent"]
        try:
            return json.loads(textos[0]) if textos else {}
        except ValueError:
            return {"texto": "\n".join(textos)}

    def fechar(self):
        self.http.close()


_CAPACIDADES: dict[str, tuple[float, dict]] = {}


def capacidades(cliente: Cliente) -> dict:
    """get_capabilities com cache de 1h por modo (janela de datas, máximo de resultados, busca ao vivo)."""
    cache = _CAPACIDADES.get(cliente.modo)
    if cache and time.time() - cache[0] < 3600:
        return cache[1]
    caps = cliente.chamar("get_capabilities", {})
    _CAPACIDADES[cliente.modo] = (time.time(), caps)
    return caps
