"""Disponibilidade de resgate em milhas via Seats.aero (Partner API, dados em cache).

Docs: https://developers.seats.aero/reference — autenticação pelo header Partner-Authorization.
"""

import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import httpx

from .flights import aeroportos

API_KEY = os.environ.get("SEATS_AERO_API_KEY", "")
BASE = "https://seats.aero/partnerapi"

# cabine do sistema → (nome na API, letra usada nos campos YAvailable/JMileageCost...)
CABINES = {"economica": ("economy", "Y"), "premium": ("premium", "W"),
           "executiva": ("business", "J"), "primeira": ("first", "F")}
LETRA_CABINE = {letra: cab for cab, (_, letra) in CABINES.items()}

PROGRAMAS = {
    "smiles": "Smiles", "azul": "Azul Fidelidade", "aeroplan": "Aeroplan", "flyingblue": "Flying Blue",
    "american": "AAdvantage", "united": "United MileagePlus", "connectmiles": "ConnectMiles",
    "qatar": "Qatar Privilege Club", "turkish": "Turkish Miles&Smiles", "emirates": "Emirates Skywards",
    "delta": "Delta SkyMiles", "alaska": "Alaska Mileage Plan", "virginatlantic": "Virgin Atlantic",
    "aeromexico": "Club Premier", "etihad": "Etihad Guest", "lufthansa": "Miles&More",
    "singapore": "KrisFlyer", "qantas": "Qantas", "velocity": "Velocity", "eurobonus": "EuroBonus",
    "jetblue": "TrueBlue", "ethiopian": "ShebaMiles", "saudia": "AlFursan", "finnair": "Finnair Plus",
    "frontier": "Frontier", "spirit": "Spirit",
}
REGIOES = {"North America": "América do Norte", "South America": "América do Sul", "Africa": "África",
           "Asia": "Ásia", "Europe": "Europa", "Oceania": "Oceania"}


@dataclass
class Oferta:
    availability_id: str
    data_voo: str
    programa: str
    cabine: str
    milhas: int
    assentos: int | None
    cias: str
    direto: bool
    origem: str
    destino: str
    regiao_destino: str | None

    def dict(self) -> dict:
        return asdict(self)


def erro_seguro(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} da Seats.aero {exc.response.text[:120]}".strip()
    texto = str(exc)
    return texto.replace(API_KEY, "***") if API_KEY else texto


# Chamadas feitas hoje (cota do plano Pro: 1.000/dia). Zera ao reiniciar; é só indicativo.
CHAMADAS = {"dia": date.today().isoformat(), "n": 0}


def _get(caminho: str, params: dict) -> dict | list:
    hoje = date.today().isoformat()
    if CHAMADAS["dia"] != hoje:
        CHAMADAS.update(dia=hoje, n=0)
    CHAMADAS["n"] += 1
    resp = httpx.get(f"{BASE}{caminho}", params=params, timeout=60,
                     headers={"Partner-Authorization": API_KEY, "Accept": "application/json"})
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _lista(resposta) -> list[dict]:
    # A busca devolve {"data": [...]}; a disponibilidade em massa não tem formato documentado.
    if isinstance(resposta, list):
        return resposta
    return resposta.get("data") or []


def _milhas(valor) -> int:
    try:
        return int(str(valor or "0").replace(",", "").split(".")[0])
    except ValueError:
        return 0


def ofertas_de(av: dict, letras: list[str]) -> list[Oferta]:
    """Converte um objeto Availability nas ofertas das cabines pedidas (só as disponíveis)."""
    rota = av.get("Route") or {}
    saida = []
    for letra in letras:
        milhas = _milhas(av.get(f"{letra}MileageCost"))
        if not av.get(f"{letra}Available") or milhas <= 0:
            continue
        saida.append(Oferta(
            availability_id=av.get("ID") or "",
            data_voo=av.get("Date") or "",
            programa=av.get("Source") or rota.get("Source") or "",
            cabine=LETRA_CABINE[letra],
            milhas=milhas,
            assentos=av.get(f"{letra}RemainingSeats") or None,
            cias=av.get(f"{letra}Airlines") or "",
            direto=bool(av.get(f"{letra}Direct")),
            origem=rota.get("OriginAirport") or "",
            destino=rota.get("DestinationAirport") or "",
            regiao_destino=rota.get("DestinationRegion"),
        ))
    return saida


def buscar_trecho(origem: str, destino: str, data_voo: date, flex: int, cabine: str,
                  programas: list[str] | None = None, cias: str | None = None) -> list[Oferta]:
    """Cached Search (1 chamada): ofertas do sentido, das mais baratas para as mais caras."""
    inicio = max(data_voo - timedelta(days=flex), date.today())
    nome, letra = CABINES.get(cabine, CABINES["economica"])
    params = {
        "origin_airport": aeroportos(origem), "destination_airport": aeroportos(destino),
        "start_date": inicio.isoformat(), "end_date": (data_voo + timedelta(days=flex)).isoformat(),
        "cabins": nome, "order_by": "lowest_mileage", "take": 200,
    }
    if programas:
        params["sources"] = ",".join(programas)
    if cias:
        params["carriers"] = cias
    ofertas = [o for av in _lista(_get("/search", params)) for o in ofertas_de(av, [letra])]
    return sorted(ofertas, key=lambda o: o.milhas)


def explorar(programa: str, cabine: str, regiao_origem: str | None, inicio: date, fim: date,
             max_paginas: int = 2, regiao_destino: str | None = None) -> tuple[list[Oferta], int]:
    """Bulk Availability (equivalente ao Explore do site). Retorna (ofertas, chamadas feitas)."""
    nome, letra = CABINES.get(cabine, CABINES["economica"])
    params = {"source": programa, "cabin": nome, "start_date": inicio.isoformat(),
              "end_date": fim.isoformat(), "take": 1000}
    if regiao_origem:
        params["origin_region"] = regiao_origem
    if regiao_destino:
        params["destination_region"] = regiao_destino
    vistos, ofertas, chamadas, skip = set(), [], 0, 0
    for _ in range(max_paginas):
        resposta = _get("/availability", params)
        chamadas += 1
        lote = _lista(resposta)
        for av in lote:
            if av.get("ID") in vistos:  # a paginação pode repetir objetos
                continue
            vistos.add(av.get("ID"))
            ofertas += ofertas_de(av, [letra])
        skip += len(lote)
        cursor = resposta.get("cursor") if isinstance(resposta, dict) else None
        tem_mais = resposta.get("hasMore") if isinstance(resposta, dict) else False
        if not lote or not cursor or not tem_mais:
            break
        params.update(cursor=cursor, skip=skip)
    return ofertas, chamadas


def detalhes(availability_id: str) -> dict:
    """Get Trips (1 chamada): voos, horários, taxas e links de emissão de uma oferta."""
    resposta = _get(f"/trips/{availability_id}", {})
    if not isinstance(resposta, dict):
        return {"viagens": [], "links": []}
    viagens = []
    for t in (resposta.get("data") or [])[:10]:
        segmentos = sorted(t.get("AvailabilitySegments") or [], key=lambda s: s.get("Order", 0))
        viagens.append({
            "milhas": t.get("MileageCost"), "taxas": t.get("TotalTaxes"),
            "moeda": t.get("TaxesCurrency"), "simbolo": t.get("TaxesCurrencySymbol"),
            "cabine": t.get("Cabin"), "paradas": t.get("Stops"), "duracao_min": t.get("TotalDuration"),
            "voos": t.get("FlightNumbers"), "cias": t.get("Carriers"), "assentos": t.get("RemainingSeats"),
            "partida": t.get("DepartsAt"), "chegada": t.get("ArrivesAt"), "programa": t.get("Source"),
            "segmentos": [{"voo": s.get("FlightNumber"), "de": s.get("OriginAirport"), "para": s.get("DestinationAirport"),
                           "partida": s.get("DepartsAt"), "chegada": s.get("ArrivesAt"), "aviao": s.get("AircraftName")}
                          for s in segmentos],
        })
    links = [{"rotulo": l.get("label"), "url": l.get("link"), "principal": bool(l.get("primary"))}
             for l in resposta.get("booking_links") or []]
    return {"viagens": viagens, "links": links}


def avaliar(total: int, teto: int | None, minimo_hist: int | None, amostras: int, amostras_min: int,
            valor_milheiro: float | None, alvo: float | None) -> list[str]:
    """Motivos para considerar o resgate uma oportunidade (lista vazia = não é)."""
    motivos = []
    if teto and total <= teto:
        motivos.append(f"dentro do seu teto de {teto:,} milhas".replace(",", "."))
    if minimo_hist and amostras >= amostras_min and total <= minimo_hist:
        motivos.append("menor quantidade de milhas já vista")
    if valor_milheiro and alvo and valor_milheiro >= alvo:
        motivos.append(f"milheiro rende R$ {valor_milheiro:.2f}, acima do alvo de R$ {alvo:.2f}".replace(".", ","))
    return motivos
