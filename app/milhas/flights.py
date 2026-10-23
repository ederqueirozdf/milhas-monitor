"""Preços em dinheiro via SerpAPI (Google Flights)."""

import os
import random
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
CLASSES = {"economica": 1, "premium": 2, "executiva": 3, "primeira": 4}

# Códigos de cidade (metropolitanos) não existem no Google Flights; a SerpAPI aceita
# vários aeroportos separados por vírgula.
CIDADES_MULTI = {
    "SAO": "GRU,CGH,VCP", "RIO": "GIG,SDU", "BHZ": "CNF,PLU",
    "NYC": "JFK,EWR,LGA", "LON": "LHR,LGW,STN,LCY,LTN", "PAR": "CDG,ORY", "ROM": "FCO,CIA",
    "MIL": "MXP,LIN", "BUE": "EZE,AEP", "TYO": "NRT,HND", "CHI": "ORD,MDW", "WAS": "IAD,DCA,BWI",
}


def aeroportos(codigo: str) -> str:
    return CIDADES_MULTI.get(codigo.upper(), codigo.upper())


@dataclass
class Cotacao:
    data_ida: str
    data_volta: str | None
    valor: float
    cia: str | None
    link: str | None


def erro_seguro(exc: Exception) -> str:
    """Descreve a falha sem a URL da requisição (que contém a api_key)."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detalhe = exc.response.json().get("error", "")
        except ValueError:
            detalhe = ""
        return f"HTTP {exc.response.status_code} da SerpAPI {detalhe}".strip()
    texto = str(exc)
    return texto.replace(SERPAPI_KEY, "***") if SERPAPI_KEY else texto


def datas_candidatas(trecho: dict, maximo: int, hoje: date | None = None) -> list[tuple[date, date | None]]:
    """Data exata sempre; o restante sorteado dentro da flexibilidade (cobre a janela ao longo dos dias)."""
    hoje = hoje or date.today()
    ida = date.fromisoformat(trecho["data_ida"])
    volta = date.fromisoformat(trecho["data_volta"]) if trecho.get("data_volta") else None
    flex = trecho.get("flex_dias") or 0
    deslocamentos = [d for d in range(-flex, flex + 1) if d != 0]
    escolhidos = [0] + random.sample(deslocamentos, min(len(deslocamentos), max(maximo - 1, 0)))
    pares = []
    for d in escolhidos:
        i = ida + timedelta(days=d)
        if i <= hoje:
            continue
        pares.append((i, volta + timedelta(days=d) if volta else None))
    return pares


def _consultar(trecho: dict, ida: date, volta: date | None) -> dict:
    params = {
        "engine": "google_flights",
        "departure_id": aeroportos(trecho["origem"]),
        "arrival_id": aeroportos(trecho["destino"]),
        "outbound_date": ida.isoformat(),
        "type": 1 if volta else 2,
        "travel_class": CLASSES.get(trecho.get("cabine") or "economica", 1),
        "adults": trecho.get("adultos") or 1,
        "currency": "BRL",
        "hl": "pt",
        "gl": "br",
        "api_key": SERPAPI_KEY,
    }
    if volta:
        params["return_date"] = volta.isoformat()
    if trecho.get("cias"):
        params["include_airlines"] = trecho["cias"]
    resp = httpx.get("https://serpapi.com/search.json", params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _opcoes(dados: dict) -> list[dict]:
    opcoes = [o for o in dados.get("best_flights", []) + dados.get("other_flights", []) if o.get("price")]
    return sorted(opcoes, key=lambda o: o["price"])


def _cias(opcao: dict) -> str:
    nomes = dict.fromkeys(v.get("airline") for v in opcao.get("flights", []) if v.get("airline"))
    return ", ".join(nomes)


def buscar(trecho: dict, ida: date, volta: date | None) -> Cotacao | None:
    dados = _consultar(trecho, ida, volta)
    opcoes = _opcoes(dados)
    if not opcoes:
        return None
    melhor = opcoes[0]
    return Cotacao(
        data_ida=ida.isoformat(),
        data_volta=volta.isoformat() if volta else None,
        valor=float(melhor["price"]),
        cia=_cias(melhor) or None,
        link=dados.get("search_metadata", {}).get("google_flights_url"),
    )


def pesquisar(trecho: dict, ida: date, volta: date | None, limite: int = 10) -> dict:
    """Busca avulsa para o painel: opções detalhadas, sem gravar nada."""
    dados = _consultar(trecho, ida, volta)
    insights = dados.get("price_insights") or {}
    opcoes = []
    for o in _opcoes(dados)[:limite]:
        voos = o.get("flights") or [{}]
        opcoes.append({
            "preco": o["price"],
            "cias": _cias(o),
            "logo": o.get("airline_logo"),
            "paradas": max(len(voos) - 1, 0),
            "duracao_min": o.get("total_duration"),
            "partida": voos[0].get("departure_airport", {}).get("time"),
            "chegada": voos[-1].get("arrival_airport", {}).get("time"),
            "de": voos[0].get("departure_airport", {}).get("id"),
            "para": voos[-1].get("arrival_airport", {}).get("id"),
        })
    return {
        "opcoes": opcoes,
        "menor_preco": insights.get("lowest_price"),
        "nivel_preco": insights.get("price_level"),
        "faixa_tipica": insights.get("typical_price_range"),
        "link": dados.get("search_metadata", {}).get("google_flights_url"),
        "erro": dados.get("error"),
    }
