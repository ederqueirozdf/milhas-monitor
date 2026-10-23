"""Classificação determinística de posts de blogs de milhas (sem LLM)."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(ch for ch in sem_acento if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def _palavra(termo: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])")


@dataclass
class Promo:
    tipo: str
    programas: list[str] = field(default_factory=list)
    origem: str | None = None
    destino: str | None = None
    bonus_pct: int | None = None
    desconto_pct: int | None = None
    milheiro: float | None = None
    validade: str | None = None


RE_COMPRA = re.compile(
    r"\bcompr\w*\b.{0,40}\b(pontos|milhas|avios|milheiro)\b"
    r"|\bmilheiro\b|\bvenda de (pontos|milhas)\b"
)
RE_TRANSF = re.compile(r"\btransf\w*")
RE_BONUS_PALAVRA = re.compile(r"\bbonus\b|\bbonifica\w*")
RE_CLUBE = re.compile(r"\bclube\b")
RE_CARTAO = re.compile(r"\bcart(ao|oes)\b")
RE_CARTAO_CTX = re.compile(r"pontos|milhas|anuidade|boas.vindas|pontua\w*|acumul\w*")
RE_PASSAGEM = re.compile(
    r"passage(m|ns)|\bvoos?\b|ida e volta|\btrechos?\b|a partir de r\$|mil milhas"
)

RE_BONUS = [
    re.compile(r"(\d{1,3})\s?%\s*(de\s+)?(bonus|bonifica\w*|a mais|extra)"),
    re.compile(r"(?:bonus|bonifica\w*)\s+(?:de\s+)?(?:ate\s+)?(\d{1,3})\s?%"),
]
RE_PCT = re.compile(r"(\d{1,3})\s?%")
RE_DESCONTO = re.compile(r"(\d{1,2})\s?%\s*(de\s+)?(desconto|off)")
RE_MILHEIRO = re.compile(r"milheiro.{0,25}?r\$\s?(\d+(?:[.,]\d{1,2})?)")
RE_VALIDADE = re.compile(r"\bate (?:o dia |dia |as \S+ de )?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")


class Extrator:
    def __init__(self, programas: list[dict]):
        self.programas = [
            (p["nome"], p["tipo"], [_palavra(a) for a in p["apelidos"]]) for p in programas
        ]

    def programas_citados(self, texto: str) -> list[tuple[str, str]]:
        achados = []
        for nome, tipo, padroes in self.programas:
            posicoes = [m.start() for pad in padroes if (m := pad.search(texto))]
            if posicoes:
                achados.append((min(posicoes), nome, tipo))
        achados.sort()
        return [(nome, tipo) for _, nome, tipo in achados]

    def analisar(self, titulo: str, resumo: str = "") -> Promo | None:
        t = normalizar(titulo)
        r = normalizar(resumo)
        citados = self.programas_citados(t) or self.programas_citados(r)
        nomes = [n for n, _ in citados]

        if RE_COMPRA.search(t):
            tipo = "compra"
        elif RE_TRANSF.search(t) or (RE_BONUS_PALAVRA.search(t) and len(citados) >= 2):
            tipo = "transferencia"
        elif RE_CLUBE.search(t) and citados:
            tipo = "clube"
        elif RE_CARTAO.search(t) and RE_CARTAO_CTX.search(t):
            tipo = "cartao"
        elif RE_PASSAGEM.search(t):
            tipo = "passagem"
        else:
            return None

        promo = Promo(tipo=tipo, programas=nomes)
        promo.bonus_pct = _maior_pct(RE_BONUS, t) or _maior_pct(RE_BONUS, r)
        if tipo == "transferencia":
            if promo.bonus_pct is None:
                promo.bonus_pct = _maior_pct([RE_PCT], t)
            promo.origem = next((n for n, tp in citados if tp == "banco"), None)
            promo.destino = next((n for n, tp in citados if tp == "cia"), None)
        if tipo == "compra":
            promo.destino = nomes[0] if nomes else None
            if m := RE_DESCONTO.search(t) or RE_DESCONTO.search(r):
                promo.desconto_pct = int(m.group(1))
            if m := RE_MILHEIRO.search(t) or RE_MILHEIRO.search(r):
                promo.milheiro = float(m.group(1).replace(",", "."))
        promo.validade = _validade(t) or _validade(r)
        return promo


def _maior_pct(padroes: list[re.Pattern], texto: str) -> int | None:
    valores = [int(m.group(1)) for p in padroes for m in p.finditer(texto)]
    valores = [v for v in valores if 0 < v <= 500]
    return max(valores) if valores else None


def _validade(texto: str) -> str | None:
    m = RE_VALIDADE.search(texto)
    if not m:
        return None
    dia, mes = int(m.group(1)), int(m.group(2))
    if not (1 <= dia <= 31 and 1 <= mes <= 12):
        return None
    return f"{dia:02d}/{mes:02d}"


def chave_promo(p: Promo, url: str, hoje: date | None = None) -> str:
    """Mesma promo divulgada por vários blogs na mesma semana vira um único alerta."""
    ano, semana, _ = (hoje or date.today()).isocalendar()
    if p.tipo == "transferencia" and p.destino and p.bonus_pct:
        return f"transf|{p.origem}|{p.destino}|{p.bonus_pct}|{ano}-{semana}"
    if p.tipo == "compra" and p.destino and (p.desconto_pct or p.milheiro):
        return f"compra|{p.destino}|{p.desconto_pct}|{p.milheiro}|{ano}-{semana}"
    return f"url|{url}"


def termos_do_trecho(trecho: dict, cidades: dict[str, list[str]]) -> list[str]:
    destino = trecho["destino"].upper()
    termos = [destino.lower(), *cidades.get(destino, [])]
    if trecho.get("palavras"):
        termos += [normalizar(p) for p in trecho["palavras"].split(",") if p.strip()]
    return termos


def menciona_trecho(texto_normalizado: str, trecho: dict, cidades: dict[str, list[str]]) -> bool:
    return any(_palavra(t).search(texto_normalizado) for t in termos_do_trecho(trecho, cidades))
