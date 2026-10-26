"""Login OAuth no MCP da Seats.aero (rodar uma vez, interativo).

    docker exec -it milhas python -m milhas.mcp_login          # entrar
    docker exec -it milhas python -m milhas.mcp_login --sair   # revogar e apagar as credenciais

Fluxo: registra um cliente público (PKCE S256), mostra o link de autorização, você aprova no
navegador e cola de volta o endereço http://127.0.0.1:8976/callback?... para onde ele te levou
(a página não abre — é esperado). Nenhum token é exibido; tudo fica no SQLite do serviço.
"""

import base64
import hashlib
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import db
from .seats_mcp import ESCOPOS, OAUTH, REDIRECT, URL, Cliente, Credenciais, capacidades


def _pkce() -> tuple[str, str]:
    verificador = secrets.token_urlsafe(64)
    desafio = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, desafio


def sair():
    token = db.kv_get("seats_mcp_refresh_token")
    if token:
        httpx.post(f"{OAUTH}/revoke", timeout=30, data={"token": token, "client_id": db.kv_get("seats_mcp_client_id") or ""})
    for chave in ("seats_mcp_client_id", "seats_mcp_refresh_token", "seats_mcp_erro"):
        db.kv_set(chave, None)
    print("Credenciais do MCP removidas. O serviço volta ao modo público (60 dias).")


def entrar():
    registro = httpx.post(f"{OAUTH}/register", timeout=30, json={
        "client_name": "milhas-monitor", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
    })
    registro.raise_for_status()
    client_id = registro.json()["client_id"]
    verificador, desafio = _pkce()
    estado = secrets.token_urlsafe(16)
    link = f"{OAUTH}/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": ESCOPOS,
        "resource": URL, "state": estado, "code_challenge": desafio, "code_challenge_method": "S256",
    })
    print("\n1. Abra este link no navegador, entre na sua conta Seats.aero e aprove o acesso:\n")
    print(link)
    print("\n2. O navegador vai tentar abrir http://127.0.0.1:8976/callback?code=... e mostrar erro.")
    print("   Isso é esperado: copie o endereço COMPLETO da barra do navegador e cole abaixo.\n")
    colado = input("Endereço (ou só o code): ").strip()
    params = parse_qs(urlparse(colado).query) if "://" in colado else {"code": [colado], "state": [estado]}
    if params.get("error"):
        sys.exit(f"Autorização recusada: {params['error'][0]}")
    if params.get("state", [""])[0] != estado:
        sys.exit("O 'state' não confere: copie o endereço da mesma tentativa e rode de novo.")
    resp = httpx.post(f"{OAUTH}/token", timeout=30, data={
        "grant_type": "authorization_code", "code": params["code"][0], "redirect_uri": REDIRECT,
        "client_id": client_id, "code_verifier": verificador, "resource": URL,
    })
    if resp.status_code != 200:
        sys.exit(f"Troca do código falhou: HTTP {resp.status_code} {resp.text[:200]}")
    dados = resp.json()
    if not dados.get("refresh_token"):
        sys.exit("A Seats.aero não devolveu refresh token; sem ele o serviço não renova o acesso.")
    db.kv_set("seats_mcp_client_id", client_id)
    db.kv_set("seats_mcp_refresh_token", dados["refresh_token"])
    db.kv_set("seats_mcp_erro", None)
    Credenciais.limpar_cache()
    caps = capacidades(Cliente())
    print(f"\nPronto. Antecedência máxima: {caps.get('max_advance_days')} dias | busca ao vivo: {caps.get('live_search')}"
          f" | resultados por busca: {caps.get('max_results')} | escopos: {caps.get('scope')}")
    print("Não precisa reiniciar: a próxima busca já usa a sua conta.")


if __name__ == "__main__":
    db.init()
    sair() if "--sair" in sys.argv else entrar()
