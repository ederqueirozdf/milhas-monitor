"""Integração Seats.aero com respostas no formato documentado (sem rede)."""

from datetime import date, timedelta

import pytest

from milhas import db, jobs, notify, seats


def av(id_, origem, destino, dia, y=None, j=None, fonte="smiles", regiao="Europe", direto=True):
    """Objeto Availability como em developers.seats.aero (milhas vêm como string)."""
    return {
        "ID": id_, "Date": dia, "Source": fonte,
        "Route": {"OriginAirport": origem, "DestinationAirport": destino, "DestinationRegion": regiao, "Source": fonte},
        "YAvailable": y is not None, "YMileageCost": str(y or 0), "YRemainingSeats": 4, "YAirlines": "G3", "YDirect": direto,
        "JAvailable": j is not None, "JMileageCost": str(j or 0), "JRemainingSeats": 2, "JAirlines": "TP", "JDirect": False,
    }


def test_ofertas_so_cabines_disponiveis():
    ofertas = seats.ofertas_de(av("a1", "GRU", "LIS", "2027-05-10", y=55000), ["Y", "J"])
    assert len(ofertas) == 1
    o = ofertas[0]
    assert (o.cabine, o.milhas, o.programa, o.origem, o.destino, o.direto) == ("economica", 55000, "smiles", "GRU", "LIS", True)


def test_buscar_trecho_monta_parametros_e_ordena(monkeypatch):
    chamadas = []

    def falso(caminho, params):
        chamadas.append((caminho, params))
        return {"data": [av("b", "GRU", "EZE", "2027-03-16", y=30000), av("a", "VCP", "AEP", "2027-03-15", y=18000)]}

    monkeypatch.setattr(seats, "_get", falso)
    ida = date.today() + timedelta(days=100)
    ofertas = seats.buscar_trecho("SAO", "BUE", ida, 2, "economica", ["smiles", "azul"], "G3,AD")
    caminho, p = chamadas[0]
    assert caminho == "/search"
    assert p["origin_airport"] == "GRU,CGH,VCP" and p["destination_airport"] == "EZE,AEP"
    assert p["cabins"] == "economy" and p["sources"] == "smiles,azul" and p["carriers"] == "G3,AD"
    assert p["start_date"] == (ida - timedelta(days=2)).isoformat()
    assert [o.milhas for o in ofertas] == [18000, 30000]


def test_explorar_pagina_e_remove_repetidos(monkeypatch):
    paginas = [
        {"data": [av("1", "GRU", "LIS", "2027-01-10", y=60000), av("2", "GRU", "MAD", "2027-01-11", y=70000)],
         "hasMore": True, "cursor": 1700000000},
        {"data": [av("2", "GRU", "MAD", "2027-01-11", y=70000), av("3", "BSB", "MIA", "2027-02-01", y=40000, regiao="North America")],
         "hasMore": False, "cursor": 1700000000},
    ]
    recebidos = []
    monkeypatch.setattr(seats, "_get", lambda c, p: (recebidos.append(dict(p)), paginas[len(recebidos) - 1])[1])
    ofertas, chamadas = seats.explorar("smiles", "economica", "South America", date.today(), date.today() + timedelta(days=90))
    assert chamadas == 2
    assert sorted(o.availability_id for o in ofertas) == ["1", "2", "3"]
    assert recebidos[1]["cursor"] == 1700000000 and recebidos[1]["skip"] == 2


def test_avaliar_motivos():
    assert seats.avaliar(90000, 100000, None, 1, 4, None, None) == ["dentro do seu teto de 100.000 milhas"]
    assert "menor quantidade de milhas já vista" in seats.avaliar(80000, None, 80000, 5, 4, None, None)
    assert seats.avaliar(80000, None, 80000, 2, 4, None, None) == []  # histórico curto
    assert seats.avaliar(50000, None, None, 1, 4, 30.0, 17.0)[0].startswith("milheiro rende R$ 30,00")


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init()
    ida = (date.today() + timedelta(days=120)).isoformat()
    volta = (date.today() + timedelta(days=130)).isoformat()
    with db.tx() as c:
        c.execute("INSERT INTO trechos (origem, destino, data_ida, data_volta, cabine, max_milhas) VALUES ('GRU','LIS',?,?,'economica',120000)",
                  (ida, volta))
        c.execute("INSERT INTO precos (trecho_id, data_ida, valor) VALUES (1, ?, 3300)", (ida,))
    return ida, volta


def test_run_milhas_grava_avalia_e_alerta(banco, monkeypatch):
    ida, volta = banco
    respostas = {"GRU,CGH,VCP": [], "GRU": [av("i1", "GRU", "LIS", ida, y=55000), av("i2", "GRU", "LIS", ida, y=70000)],
                 "LIS": [av("v1", "LIS", "GRU", volta, y=50000)]}
    monkeypatch.setattr(seats, "API_KEY", "x")
    monkeypatch.setattr(seats, "_get", lambda c, p: {"data": respostas[p["origin_airport"]]})
    enviadas = []
    monkeypatch.setattr(notify, "enviar", lambda texto: enviadas.append(texto) or True)

    jobs.run_milhas()

    with db.tx() as c:
        r = jobs.resumo_milhas(c, dict(c.execute("SELECT * FROM trechos WHERE id = 1").fetchone()))
    assert r["total"] == 105000
    assert r["valor_milheiro"] == round(3300 / 105, 2)
    # teto de milhas + resgate rende R$ 31,43/milheiro, acima do alvo da Smiles (R$ 17) no config.yaml
    assert r["motivos"] == ["dentro do seu teto de 120.000 milhas", "milheiro rende R$ 31,43, acima do alvo de R$ 17,00"]
    assert len(enviadas) == 1 and "105.000 milhas" in enviadas[0]

    jobs.run_milhas()  # mesmo total: não repete o alerta
    assert len(enviadas) == 1


def test_run_explorar_agrupa_e_alerta_abaixo_do_limite(banco, monkeypatch):
    dados = [av("e1", "GRU", "LIS", "2027-01-10", y=50000), av("e2", "GRU", "LIS", "2027-01-12", y=45000),
             av("e3", "GRU", "MAD", "2027-01-11", y=90000), av("e4", "SCL", "LIS", "2027-01-11", y=20000)]
    monkeypatch.setattr(seats, "API_KEY", "x")
    monkeypatch.setattr(seats, "_get", lambda c, p: {"data": dados if p["source"] == "smiles" and p["cabin"] == "economy" else []})
    enviadas = []
    monkeypatch.setattr(notify, "enviar", lambda texto: enviadas.append(texto) or True)

    jobs.run_explorar()

    with db.tx() as c:
        linhas = [dict(r) for r in c.execute("SELECT origem, destino, milhas FROM explorar ORDER BY milhas")]
    assert linhas == [{"origem": "GRU", "destino": "LIS", "milhas": 45000}, {"origem": "GRU", "destino": "MAD", "milhas": 90000}]
    assert len(enviadas) == 1 and "GRU→LIS" in enviadas[0] and "MAD" not in enviadas[0]  # SCL fora das origens
