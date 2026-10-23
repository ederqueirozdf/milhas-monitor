"""Preços em dinheiro via SerpAPI (Google Flights)."""

import os
import random
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
CLASSES = {"economica": 1, "premium": 2, "executiva": 3, "primeira": 4}


@dataclass
class Cotacao:
    data_ida: str
    data_volta: str | None
    valor: float
    cia: str | None
    link: str | None


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


def buscar(trecho: dict, ida: date, volta: date | None) -> Cotacao | None:
    params = {
        "engine": "google_flights",
        "departure_id": trecho["origem"],
        "arrival_id": trecho["destino"],
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
    resp = httpx.get("https://serpapi.com/search.json", params=params, timeout=60)
    resp.raise_for_status()
    dados = resp.json()

    opcoes = [o for o in dados.get("best_flights", []) + dados.get("other_flights", []) if o.get("price")]
    if not opcoes:
        return None
    melhor = min(opcoes, key=lambda o: o["price"])
    voos = melhor.get("flights") or [{}]
    return Cotacao(
        data_ida=ida.isoformat(),
        data_volta=volta.isoformat() if volta else None,
        valor=float(melhor["price"]),
        cia=voos[0].get("airline"),
        link=dados.get("search_metadata", {}).get("google_flights_url"),
    )
