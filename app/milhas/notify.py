"""Envio direto pela Bot API do Telegram (sem passar pelo LLM do OpenClaw)."""

import logging
import os
from html import escape

import httpx

from .extract import Promo

log = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
LIMITE = 4000

ICONES = {"transferencia": "🔁", "compra": "💰", "clube": "🎟️", "cartao": "💳", "passagem": "✈️"}
NOMES = {
    "transferencia": "Transferência bonificada",
    "compra": "Compra de pontos/milhas",
    "clube": "Clube",
    "cartao": "Cartão",
    "passagem": "Passagem",
}


def enviar(texto: str) -> bool:
    if not TOKEN or not CHAT_ID:
        log.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID ausentes; mensagem não enviada:\n%s", texto)
        return False
    ok = True
    for i in range(0, len(texto), LIMITE):
        resp = httpx.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": texto[i : i + LIMITE],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log.error("Telegram respondeu %s: %s", resp.status_code, resp.text[:300])
            ok = False
    return ok


def _link(url: str, rotulo: str) -> str:
    return f'<a href="{escape(url, quote=True)}">{escape(rotulo)}</a>'


def _reais(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_promo(p: Promo, titulo: str, url: str, fonte: str) -> str:
    linhas = [f"{ICONES[p.tipo]} <b>{NOMES[p.tipo]}</b>"]
    if p.tipo == "transferencia":
        rota = f"{p.origem or 'Parceiros'} → {p.destino or '?'}"
        bonus = f" · <b>{p.bonus_pct}% de bônus</b>" if p.bonus_pct else ""
        linhas.append(escape(rota) + bonus)
    elif p.tipo == "compra":
        partes = [escape(p.destino or "Programa não identificado")]
        if p.desconto_pct:
            partes.append(f"<b>{p.desconto_pct}% de desconto</b>")
        if p.milheiro:
            partes.append(f"milheiro <b>{_reais(p.milheiro)}</b>")
        if p.bonus_pct:
            partes.append(f"{p.bonus_pct}% de bônus")
        linhas.append(" · ".join(partes))
    elif p.programas:
        linhas.append(escape(", ".join(p.programas)))
    if p.validade:
        linhas.append(f"⏳ até {p.validade}")
    linhas.append(escape(titulo))
    linhas.append(_link(url, fonte))
    return "\n".join(linhas)


def descrever_trecho(t: dict) -> str:
    datas = t["data_ida"] + (f" ↔ {t['data_volta']}" if t.get("data_volta") else "")
    flex = f" (±{t['flex_dias']}d)" if t.get("flex_dias") else ""
    return f"#{t['id']} {t['origem']}→{t['destino']} · {datas}{flex} · {t.get('cabine') or 'economica'}"


def formatar_post_trecho(t: dict, titulo: str, url: str, fonte: str) -> str:
    return "\n".join(
        [
            "✈️ <b>Post sobre trecho monitorado</b>",
            escape(descrever_trecho(t)),
            escape(titulo),
            _link(url, fonte),
        ]
    )


CABINE_NOME = {"economica": "econômica", "premium": "premium", "executiva": "executiva", "primeira": "primeira"}


def _milhas_txt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _dia(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}" if iso and len(iso) >= 10 else ""


def _oferta_txt(o: dict, programa_nome: str) -> str:
    extras = [o.get("cias") or "", "direto" if o.get("direto") else "com conexão"]
    if o.get("assentos"):
        extras.append(f"{o['assentos']} assento(s)")
    return (f"<b>{_milhas_txt(o['milhas'])} milhas</b> {escape(programa_nome)}, {escape(_dia(o['data_voo']))} "
            f"{escape(o.get('origem', ''))}→{escape(o.get('destino', ''))}, " + escape(", ".join(e for e in extras if e)))


def formatar_milhas(t: dict, r: dict) -> str:
    from .seats import PROGRAMAS
    linhas = ["🎫 <b>Resgate em milhas na sua rota</b>", escape(descrever_trecho(t))]
    for sentido, s in r["sentidos"].items():
        o = s["melhor"]
        linhas.append(f"{'Ida' if sentido == 'ida' else 'Volta'}: " + _oferta_txt(o, PROGRAMAS.get(o["programa"], o["programa"])))
    if len(r["sentidos"]) > 1:
        linhas.append(f"Total: <b>{_milhas_txt(r['total'])} milhas</b> + taxas")
    if r.get("valor_milheiro"):
        linhas.append(f"Em dinheiro: {_reais(r['preco_dinheiro'])}, cada milheiro rende {_reais(r['valor_milheiro'])}")
    linhas.append("📉 " + escape("; ".join(r["motivos"])))
    linhas.append("Detalhes e links de emissão no painel, em Emissões.")
    return "\n".join(linhas)


def formatar_explorar(ofertas: list) -> str:
    from .seats import PROGRAMAS, REGIOES
    linhas = ["🧭 <b>Achados do Explorar (Seats.aero)</b>"]
    for o in ofertas:
        linhas.append(
            f"• {escape(o.origem)}→{escape(o.destino)} ({escape(REGIOES.get(o.regiao_destino or '', o.regiao_destino or ''))}), "
            f"{escape(_dia(o.data_voo))}: <b>{_milhas_txt(o.milhas)}</b> {escape(PROGRAMAS.get(o.programa, o.programa))}, "
            f"{CABINE_NOME.get(o.cabine, o.cabine)}{', direto' if o.direto else ''}")
    linhas.append("Só ida, sem taxas. Veja todos no painel, em Explorar.")
    return "\n".join(linhas)


def formatar_voo(t: dict, c, motivo: str) -> str:
    datas = c.data_ida + (f" ↔ {c.data_volta}" if c.data_volta else "")
    linhas = [
        "🛫 <b>Preço baixo no trecho</b>",
        escape(descrever_trecho(t)),
        f"<b>{_reais(c.valor)}</b> · {escape(c.cia or 'cia ?')} · {escape(datas)}",
        f"📉 {escape(motivo)}",
    ]
    if c.link:
        linhas.append(_link(c.link, "Abrir no Google Flights"))
    return "\n".join(linhas)
