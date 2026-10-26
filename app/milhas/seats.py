"""Disponibilidade de resgate em milhas via Seats.aero, por duas fontes intercambiáveis.

- API (Partner API, header Partner-Authorization com SEATS_AERO_API_KEY): conta Pro, Explorar nativo.
- MCP (https://seats.aero/mcp): com login OAuth usa a conta Pro; sem login, modo público (60 dias).

`seats.fonte` no config.yaml: auto (API se houver chave, senão MCP; se a API falhar, tenta o MCP),
api (só API) ou mcp (só MCP). Docs: https://developers.seats.aero/reference
"""

import logging
import os
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import httpx

from . import seats_mcp
from .config import CONFIG
from .flights import aeroportos

log = logging.getLogger(__name__)

API_KEY = os.environ.get("SEATS_AERO_API_KEY", "")
BASE = "https://seats.aero/partnerapi"

# cabine do sistema → (nome na API/MCP, letra usada nos campos YAvailable/JMileageCost...)
CABINES = {"economica": ("economy", "Y"), "premium": ("premium", "W"),
           "executiva": ("business", "J"), "primeira": ("first", "F")}
LETRA_CABINE = {letra: cab for cab, (_, letra) in CABINES.items()}
NOME_CABINE = {nome: cab for cab, (nome, _) in CABINES.items()}

PROGRAMAS = {
    "smiles": "Smiles", "azul": "Azul Fidelidade", "aeroplan": "Aeroplan", "flyingblue": "Flying Blue",
    "american": "AAdvantage", "united": "United MileagePlus", "connectmiles": "ConnectMiles",
    "qatar": "Qatar Privilege Club", "turkish": "Turkish Miles&Smiles", "emirates": "Emirates Skywards",
    "delta": "Delta SkyMiles", "alaska": "Alaska Mileage Plan", "virginatlantic": "Virgin Atlantic",
    "aeromexico": "Club Premier", "etihad": "Etihad Guest", "lufthansa": "Miles&More",
    "singapore": "KrisFlyer", "qantas": "Qantas", "velocity": "Velocity", "eurobonus": "EuroBonus",
    "jetblue": "TrueBlue", "ethiopian": "ShebaMiles", "saudia": "AlFursan", "finnair": "Finnair Plus",
    "frontier": "Frontier", "spirit": "Spirit", "asiamiles": "Asia Miles",
}
REGIOES = {"North America": "América do Norte", "South America": "América do Sul", "Africa": "África",
           "Asia": "Ásia", "Europe": "Europa", "Oceania": "Oceania"}

# Chamadas à API hoje (cota Pro: 1.000/dia, compartilhada com o MCP autenticado). Zera ao reiniciar.
CHAMADAS = {"dia": date.today().isoformat(), "n": 0}


@dataclass
class Oferta:
    availability_id: str          # ID da API, ou "mcp:<flight_ref>" quando veio do MCP
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
    taxas: str | None = None      # o MCP já informa (ex.: "$58.10 CAD"); a API só em detalhes()
    fonte: str = "api"

    def dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- escolha da fonte

def _modo() -> str:
    return (CONFIG.get("seats", {}).get("fonte") or "auto").lower()


def usar_api() -> bool:
    return bool(API_KEY) and _modo() in ("auto", "api")


def permite_mcp() -> bool:
    return _modo() in ("auto", "mcp")


def fonte_ativa() -> str:
    """api | mcp-pro | mcp-publico | desligado (sem chave e com fonte: api)."""
    if usar_api():
        return "api"
    if permite_mcp():
        return "mcp-pro" if seats_mcp.Credenciais.configurado() else "mcp-publico"
    return "desligado"


def chamadas_hoje() -> int:
    return CHAMADAS["n"] + seats_mcp.CHAMADAS["n"]


def erro_seguro(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} da Seats.aero {exc.response.text[:120]}".strip()
    texto = str(exc)
    return texto.replace(API_KEY, "***") if API_KEY else texto


# ---------------------------------------------------------------- API (Partner API)

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
    """Converte um objeto Availability da API nas ofertas das cabines pedidas (só as disponíveis)."""
    rota = av.get("Route") or {}
    saida = []
    for letra in letras:
        milhas = _milhas(av.get(f"{letra}MileageCost"))
        if not av.get(f"{letra}Available") or milhas <= 0:
            continue
        saida.append(Oferta(
            availability_id=av.get("ID") or "", data_voo=av.get("Date") or "",
            programa=av.get("Source") or rota.get("Source") or "", cabine=LETRA_CABINE[letra], milhas=milhas,
            assentos=av.get(f"{letra}RemainingSeats") or None, cias=av.get(f"{letra}Airlines") or "",
            direto=bool(av.get(f"{letra}Direct")), origem=rota.get("OriginAirport") or "",
            destino=rota.get("DestinationAirport") or "", regiao_destino=rota.get("DestinationRegion"),
        ))
    return saida


def _api_buscar(origem, destino, inicio: date, fim: date, cabine, programas, cias) -> list[Oferta]:
    nome, letra = CABINES.get(cabine, CABINES["economica"])
    params = {"origin_airport": aeroportos(origem), "destination_airport": aeroportos(destino),
              "start_date": inicio.isoformat(), "end_date": fim.isoformat(),
              "cabins": nome, "order_by": "lowest_mileage", "take": 200}
    if programas:
        params["sources"] = ",".join(programas)
    if cias:
        params["carriers"] = cias
    return [o for av in _lista(_get("/search", params)) for o in ofertas_de(av, [letra])]


def _api_explorar(programa, cabine, regiao_origem, inicio: date, fim: date, max_paginas=2,
                  regiao_destino=None) -> tuple[list[Oferta], int]:
    nome, letra = CABINES.get(cabine, CABINES["economica"])
    params = {"source": programa, "cabin": nome, "start_date": inicio.isoformat(), "end_date": fim.isoformat(), "take": 1000}
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


def _api_detalhes(availability_id: str) -> dict:
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
    return {"viagens": viagens, "links": links, "fonte": "api"}


# ---------------------------------------------------------------- MCP

def _oferta_mcp(f: dict, regiao: str | None = None) -> Oferta:
    return Oferta(
        availability_id=f"mcp:{f.get('flight_ref', '')}", data_voo=(f.get("departs_at") or "")[:10],
        programa=f.get("mileage_program") or "", cabine=NOME_CABINE.get(f.get("cabin"), "economica"),
        milhas=int(f.get("miles_price") or 0), assentos=f.get("remaining_seats"),
        cias=", ".join(f.get("operating_carriers") or []), direto=f.get("stops") == 0,
        origem=f.get("origin") or "", destino=f.get("destination") or "", regiao_destino=regiao,
        taxas=f.get("taxes"), fonte="mcp",
    )


def _janelas(inicio: date, fim: date, passo: int) -> list[tuple[date, date]]:
    """Divide o intervalo em janelas com fim até `passo` dias após o início (limite do get_flights)."""
    janelas, atual = [], inicio
    while atual <= fim:
        ultimo = min(atual + timedelta(days=passo), fim)
        janelas.append((atual, ultimo))
        atual = ultimo + timedelta(days=1)
    return janelas


def _mcp_voos(cli: seats_mcp.Cliente, origens: list[str], destinos: list[str], inicio: date, fim: date,
              cabine: str, programas, cias, max_paradas: int, regiao=None) -> tuple[list[Oferta], int]:
    caps = seats_mcp.capacidades(cli)
    limite = date.today() + timedelta(days=int(caps.get("max_advance_days") or 60))
    fim = min(fim, limite)
    if inicio > fim:
        log.info("MCP (%s): datas além da janela de %s dias", cli.modo, caps.get("max_advance_days"))
        return [], 0
    ofertas, chamadas = [], 0
    for ini, ult in _janelas(inicio, fim, int(caps.get("max_date_range_days") or 60)):
        args = {"origins": origens, "destinations": destinos, "start_date": ini.isoformat(), "end_date": ult.isoformat(),
                "cabin": CABINES.get(cabine, CABINES["economica"])[0], "max_stops": max_paradas,
                "sort": "mileage_cost", "sort_direction": "asc", "max_results": int(caps.get("max_results") or 50)}
        if programas:
            args["programs"] = list(programas)
        if cias:
            args["operating_carriers"] = cias.split(",")
        dados = cli.chamar("get_flights", args)
        chamadas += 1
        ofertas += [_oferta_mcp(f, regiao) for f in dados.get("flights") or [] if f.get("miles_price")]
    return ofertas, chamadas


def _mcp_detalhes(flight_ref: str) -> dict:
    cli = seats_mcp.Cliente()
    try:
        d = cli.chamar("get_flight_details", {"flight_ref": flight_ref})
    finally:
        cli.fechar()
    f = d.get("flight") or {}
    viagem = {
        "milhas": f.get("miles_price"), "taxas": f.get("taxes"), "moeda": None, "simbolo": None,
        "cabine": f.get("cabin"), "paradas": f.get("stops"), "duracao_min": f.get("duration_minutes"),
        "voos": f.get("flights"), "cias": ", ".join(f.get("operating_carriers") or []), "assentos": f.get("remaining_seats"),
        "partida": f.get("departs_at"), "chegada": f.get("arrives_at"), "programa": f.get("mileage_program"),
        "idade_min": f.get("minutes_old"),
        "segmentos": [{"voo": s.get("flight"), "de": s.get("origin"), "para": s.get("destination"),
                       "partida": s.get("departs_at"), "chegada": s.get("arrives_at"), "aviao": s.get("aircraft")}
                      for s in d.get("segments") or []],
    }
    links = [{"rotulo": "Instruções de emissão (Seats.aero)", "url": d["url"], "principal": True}] if d.get("url") else []
    return {"viagens": [viagem] if f else [], "links": links, "aviso": d.get("warning"), "fonte": "mcp"}


# ---------------------------------------------------------------- funções usadas pelo sistema

def buscar_trecho(origem: str, destino: str, data_voo: date, flex: int, cabine: str,
                  programas: list[str] | None = None, cias: str | None = None) -> list[Oferta]:
    """Ofertas do sentido, das mais baratas para as mais caras (API; MCP se não houver chave ou a API falhar)."""
    inicio = max(data_voo - timedelta(days=flex), date.today())
    fim = data_voo + timedelta(days=flex)
    if usar_api():
        try:
            return sorted(_api_buscar(origem, destino, inicio, fim, cabine, programas, cias), key=lambda o: o.milhas)
        except Exception as exc:
            if not permite_mcp():
                raise
            log.warning("API da Seats.aero falhou (%s); tentando o MCP", erro_seguro(exc))
    if not permite_mcp():
        return []
    cli = seats_mcp.Cliente()
    try:
        ofertas, _ = _mcp_voos(cli, aeroportos(origem).split(","), aeroportos(destino).split(","), inicio, fim,
                               cabine, programas, cias, max_paradas=2)
    finally:
        cli.fechar()
    return sorted(ofertas, key=lambda o: o.milhas)


def explorar_cabine(cabine: str, inicio: date, fim: date) -> tuple[list[Oferta], int, str]:
    """Explorar de uma cabine para os programas configurados. Retorna (ofertas, chamadas, fonte)."""
    cfg = CONFIG.get("explorar", {})
    programas = cfg.get("programas") or []
    if usar_api():
        try:
            todas, chamadas = [], 0
            for programa in programas:
                ofertas, n = _api_explorar(programa, cabine, cfg.get("regiao_origem"), inicio, fim, cfg.get("max_paginas", 2))
                todas += ofertas
                chamadas += n
            return todas, chamadas, "api"
        except Exception as exc:
            if not permite_mcp():
                raise
            log.warning("API da Seats.aero falhou no Explorar (%s); tentando o MCP", erro_seguro(exc))
    if not permite_mcp():
        return [], 0, "desligado"
    # O MCP não tem busca por região: uma busca por grupo de destinos populares.
    origens = cfg.get("origens") or ["GRU", "GIG", "BSB"]
    cli = seats_mcp.Cliente()
    todas, chamadas = [], 0
    try:
        for regiao, destinos in (cfg.get("destinos_mcp") or {}).items():
            ofertas, n = _mcp_voos(cli, origens, destinos, inicio, fim, cabine, programas, None, max_paradas=1, regiao=regiao)
            todas += ofertas
            chamadas += n
    finally:
        cli.fechar()
    return todas, chamadas, cli.modo


def detalhes(availability_id: str) -> dict:
    """Voos, taxas e link de emissão de uma oferta (1 chamada, na fonte de onde a oferta veio)."""
    if availability_id.startswith("mcp:"):
        return _mcp_detalhes(availability_id[4:])
    return _api_detalhes(availability_id)


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
