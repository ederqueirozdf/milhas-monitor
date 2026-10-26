"""Fonte MCP da Seats.aero com cliente simulado (respostas no formato real do get_flights/get_flight_details)."""

from datetime import date, timedelta

import pytest

from milhas import seats, seats_mcp

CAPS_PUBLICO = {"max_advance_days": 60, "max_date_range_days": 60, "max_results": 50, "live_search": False}


def voo(ref, origem, destino, dia, milhas, programa="smiles", paradas=0, taxas="$13.60 USD"):
    return {"flight_ref": ref, "origin": origem, "destination": destino, "cabin": "economy",
            "departs_at": f"{dia} 22:40", "arrives_at": f"{dia} 23:59", "stops": paradas, "flights": "G37648",
            "miles_price": milhas, "mileage_program": programa, "operating_carriers": ["G3"], "taxes": taxas,
            "duration_minutes": 180, "minutes_old": 30, "remaining_seats": 4}


class ClienteFalso:
    chamadas: list = []

    def __init__(self, autenticado=True, caps=CAPS_PUBLICO, voos=None, modo="mcp-publico"):
        self.caps, self.voos, self.modo = caps, voos or [], modo

    def chamar(self, ferramenta, args):
        ClienteFalso.chamadas.append((ferramenta, args))
        if ferramenta == "get_capabilities":
            return self.caps
        if ferramenta == "get_flights":
            return {"flights": self.voos, "status": "results"}
        if ferramenta == "get_flight_details":
            return {"flight": voo(args["flight_ref"], "GRU", "EZE", "2026-11-22", 10000, "aeroplan"),
                    "segments": [{"origin": "GRU", "destination": "EZE", "flight": "TK15", "departs_at": "2026-11-22 19:20",
                                  "arrives_at": "2026-11-22 22:25", "aircraft": "Airbus A350-900"}],
                    "url": "https://seats.aero/i/abc", "warning": "Confirme antes de transferir."}
        raise AssertionError(ferramenta)

    def fechar(self):
        pass


@pytest.fixture
def mcp(monkeypatch):
    ClienteFalso.chamadas = []
    seats_mcp._CAPACIDADES.clear()
    monkeypatch.setattr(seats, "API_KEY", "")
    monkeypatch.setattr(seats_mcp.Credenciais, "configurado", staticmethod(lambda: False))

    def usar(**kw):
        monkeypatch.setattr(seats_mcp, "Cliente", lambda autenticado=True: ClienteFalso(**kw))
    return usar


def test_sem_chave_usa_mcp_publico_e_converte(mcp):
    dia = (date.today() + timedelta(days=20)).isoformat()
    mcp(voos=[voo("b", "GRU", "AEP", dia, 30000), voo("a", "VCP", "EZE", dia, 10000, "aeroplan", paradas=1)])
    assert seats.fonte_ativa() == "mcp-publico"
    ofertas = seats.buscar_trecho("SAO", "BUE", date.today() + timedelta(days=20), 2, "economica", ["smiles"], "G3,AD")
    assert [(o.availability_id, o.milhas, o.direto, o.taxas, o.fonte) for o in ofertas] == [
        ("mcp:a", 10000, False, "$13.60 USD", "mcp"), ("mcp:b", 30000, True, "$13.60 USD", "mcp")]
    _, args = [c for c in ClienteFalso.chamadas if c[0] == "get_flights"][0]
    assert args["origins"] == ["GRU", "CGH", "VCP"] and args["destinations"] == ["EZE", "AEP"]
    assert args["programs"] == ["smiles"] and args["operating_carriers"] == ["G3", "AD"] and args["cabin"] == "economy"


def test_mcp_publico_nao_alcanca_alem_de_60_dias(mcp):
    mcp(voos=[voo("x", "GRU", "LIS", "2027-05-10", 50000)])
    assert seats.buscar_trecho("GRU", "LIS", date.today() + timedelta(days=200), 0, "economica") == []
    assert not [c for c in ClienteFalso.chamadas if c[0] == "get_flights"]  # nem gasta chamada


def test_janelas_de_60_dias():
    ini = date(2027, 1, 1)
    janelas = seats._janelas(ini, ini + timedelta(days=129), 60)
    # "end_date: at most 60 days after start_date"
    assert [(a.isoformat(), b.isoformat()) for a, b in janelas] == [
        ("2027-01-01", "2027-03-02"), ("2027-03-03", "2027-05-02"), ("2027-05-03", "2027-05-10")]


def test_api_com_falha_cai_para_o_mcp(mcp, monkeypatch):
    dia = (date.today() + timedelta(days=10)).isoformat()
    mcp(voos=[voo("m", "GRU", "EZE", dia, 12000)])
    monkeypatch.setattr(seats, "API_KEY", "x")

    def quebra(*a, **k):
        raise RuntimeError("HTTP 429")
    monkeypatch.setattr(seats, "_get", quebra)
    assert seats.fonte_ativa() == "api"
    ofertas = seats.buscar_trecho("GRU", "EZE", date.today() + timedelta(days=10), 0, "economica")
    assert [o.fonte for o in ofertas] == ["mcp"]


def test_fonte_api_nao_usa_mcp(mcp, monkeypatch):
    monkeypatch.setitem(seats.CONFIG["seats"], "fonte", "api")
    assert seats.fonte_ativa() == "desligado"  # sem chave e sem permissão de MCP
    assert seats.buscar_trecho("GRU", "EZE", date.today() + timedelta(days=10), 0, "economica") == []


def test_explorar_mcp_um_grupo_por_regiao(mcp, monkeypatch):
    dia = (date.today() + timedelta(days=15)).isoformat()
    mcp(voos=[voo("e", "GRU", "LIS", dia, 45000)])
    monkeypatch.setitem(seats.CONFIG["explorar"], "destinos_mcp", {"Europe": ["LIS", "MAD"], "Africa": ["JNB"]})
    ofertas, chamadas, fonte = seats.explorar_cabine("economica", date.today(), date.today() + timedelta(days=180))
    assert fonte == "mcp-publico" and chamadas == 2  # 2 regiões x 1 janela (público só alcança 60 dias)
    assert {o.regiao_destino for o in ofertas} == {"Europe", "Africa"}


def test_detalhes_mcp(mcp):
    mcp()
    d = seats.detalhes("mcp:3KOs8")
    assert d["fonte"] == "mcp" and d["links"][0]["url"] == "https://seats.aero/i/abc"
    v = d["viagens"][0]
    assert (v["milhas"], v["taxas"], v["segmentos"][0]["voo"], v["programa"]) == (10000, "$13.60 USD", "TK15", "aeroplan")
