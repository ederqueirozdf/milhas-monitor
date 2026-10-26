from pathlib import Path

import yaml

from milhas.extract import Extrator, menciona_trecho, normalizar

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text(encoding="utf-8"))
ex = Extrator(CFG["programas"])


def test_transferencia_livelo_smiles():
    p = ex.analisar("Livelo oferece até 100% de bônus na transferência para a Smiles")
    assert (p.tipo, p.origem, p.destino, p.bonus_pct) == ("transferencia", "Livelo", "Smiles", 100)


def test_transferencia_latam_bonus_baixo():
    p = ex.analisar("LATAM Pass: ganhe 25% de bônus ao transferir pontos do Itaú")
    assert (p.tipo, p.origem, p.destino, p.bonus_pct) == ("transferencia", "Itaú", "LATAM Pass", 25)


def test_transferencia_sem_palavra_transferir():
    p = ex.analisar("Esfera e Azul Fidelidade: 70% de bônus até 31/10")
    assert (p.tipo, p.origem, p.destino, p.bonus_pct, p.validade) == (
        "transferencia", "Esfera", "Azul Fidelidade", 70, "31/10")


def test_compra_desconto():
    p = ex.analisar("Compre pontos Livelo com até 50% de desconto")
    assert (p.tipo, p.destino, p.desconto_pct) == ("compra", "Livelo", 50)


def test_compra_milheiro():
    p = ex.analisar("Smiles: milheiro a R$ 14,00 na compra de milhas")
    assert (p.tipo, p.destino, p.milheiro) == ("compra", "Smiles", 14.0)


def test_clube():
    assert ex.analisar("Clube Smiles com bônus de 20 mil milhas na assinatura").tipo == "clube"


def test_cartao():
    assert ex.analisar("Novo cartão Itaú Azul acumula 3 pontos por dólar").tipo == "cartao"


def test_passagem():
    assert ex.analisar("Passagens para Lisboa a partir de R$ 2.900 ida e volta").tipo == "passagem"


def test_noticia_ignorada():
    assert ex.analisar("Aeroporto de Guarulhos inaugura nova sala VIP") is None


def test_menciona_trecho():
    t = {"destino": "LIS", "palavras": None}
    assert menciona_trecho(normalizar("Voos para Lisboa saindo de Brasília"), t, CFG["cidades"])
    assert not menciona_trecho(normalizar("Voos para Paris"), t, CFG["cidades"])


def test_transferencia_bb():
    p = ex.analisar("Smiles oferece até 70% de bônus nas transferências de pontos do BB Empresas")
    assert (p.origem, p.destino, p.bonus_pct) == ("Banco do Brasil", "Smiles", 70)


def test_menciona_trecho_varios_aeroportos():
    t = {"destino": "EZE,AEP", "palavras": None}
    assert menciona_trecho(normalizar("Passagens para Buenos Aires em promoção"), t, CFG["cidades"])


def test_aeroportos_expande_cidade_e_remove_repetidos():
    from milhas.flights import aeroportos
    assert aeroportos("SAO,gru, VCP") == "GRU,CGH,VCP"
    assert aeroportos("BUE") == "EZE,AEP"
    assert aeroportos("BSB,GYN") == "BSB,GYN"
