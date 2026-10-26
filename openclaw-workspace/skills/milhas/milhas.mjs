#!/usr/bin/env node
// CLI do skill "milhas": fala com a API do milhas-monitor.
// Requer MILHAS_API_URL (padrão http://milhas:8000) e MILHAS_API_TOKEN no ambiente.

const BASE = (process.env.MILHAS_API_URL || "http://milhas:8000").replace(/\/$/, "");
const TOKEN = process.env.MILHAS_API_TOKEN;

const AJUDA = `Uso:
  status
  trechos listar [--todos]
  trechos adicionar --origem BSB --destino LIS --ida 2027-05-10 [--volta 2027-05-25]
                    [--flex 3] [--cabine economica|premium|executiva|primeira] [--adultos 2]
                    [--max-reais 6000] [--max-milhas 150000] [--palavras "lisboa,portugal"] [--obs "texto"]
                    [--cias LA,AD,G3]
  trechos editar ID [mesmas opções de adicionar]
  pesquisar [mesmas opções de adicionar]   (1 busca na SerpAPI; não grava nada)
  milhas ID                                (ofertas em milhas da rota, Seats.aero)
  explorar [--programa smiles|azul|...] [--cabine economica|executiva] [--regiao Europe|"North America"|...]
           [--origem GRU] [--destino LIS] [--max 80000] [--direto] [--limite 15]
  viagem ID_OFERTA                         (voos, taxas e links de emissão; 1 chamada)
  trechos pausar ID | ativar ID | remover ID
  trechos precos ID
  promocoes [--dias 7] [--tipo transferencia|compra|clube|cartao] [--programa Smiles]
  executar rss|voos|resumo|teste`;

const CAMPOS = {
  origem: ["origem", String], destino: ["destino", String], ida: ["data_ida", String],
  volta: ["data_volta", String], flex: ["flex_dias", Number], cabine: ["cabine", String],
  adultos: ["adultos", Number], "max-reais": ["max_reais", Number], "max-milhas": ["max_milhas", Number],
  palavras: ["palavras", String], obs: ["observacao", String], cias: ["cias", String],
};

function opcoes(args) {
  const out = { _: [] };
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--")) {
      const nome = args[i].slice(2);
      const prox = args[i + 1];
      out[nome] = prox === undefined || prox.startsWith("--") ? true : (i++, prox);
    } else out._.push(args[i]);
  }
  return out;
}

function corpoTrecho(o) {
  const corpo = {};
  for (const [flag, [campo, tipo]] of Object.entries(CAMPOS)) {
    if (o[flag] !== undefined) corpo[campo] = tipo(o[flag]);
  }
  return corpo;
}

async function api(metodo, caminho, corpo) {
  const resp = await fetch(BASE + caminho, {
    method: metodo,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: corpo ? JSON.stringify(corpo) : undefined,
  });
  const texto = await resp.text();
  if (!resp.ok) {
    console.error(`ERRO ${resp.status}: ${texto}`);
    process.exit(1);
  }
  return texto ? JSON.parse(texto) : null;
}

async function main() {
  if (!TOKEN) {
    console.error("MILHAS_API_TOKEN não definido no ambiente do OpenClaw.");
    process.exit(1);
  }
  const [grupo, acao, ...resto] = process.argv.slice(2);
  const o = opcoes(resto);
  const id = o._[0];
  let r;

  if (grupo === "status") {
    r = await api("GET", "/status");
  } else if (grupo === "trechos") {
    if (acao === "listar") r = await api("GET", `/trechos${o.todos ? "?todos=true" : ""}`);
    else if (acao === "adicionar") r = await api("POST", "/trechos", corpoTrecho(o));
    else if (acao === "editar" && id) r = await api("PATCH", `/trechos/${id}`, corpoTrecho(o));
    else if (acao === "pausar" && id) r = await api("PATCH", `/trechos/${id}`, { ativo: false });
    else if (acao === "ativar" && id) r = await api("PATCH", `/trechos/${id}`, { ativo: true });
    else if (acao === "remover" && id) r = await api("DELETE", `/trechos/${id}`);
    else if (acao === "precos" && id) r = await api("GET", `/trechos/${id}/precos`);
  } else if (grupo === "pesquisar") {
    // pesquisar --origem GRU,VCP --destino EZE,AEP --ida 2027-03-10 [...]: 1 busca, nada é gravado
    const p = opcoes(process.argv.slice(3));
    const res = await api("POST", "/pesquisar", corpoTrecho(p));
    r = {
      nivel_preco: res.nivel_preco, faixa_tipica: res.faixa_tipica, erro: res.erro, link: res.link,
      opcoes: res.opcoes.map((x) => ({ preco: x.preco, cias: x.cias, de: x.de, para: x.para,
        paradas: x.paradas, escalas: x.escalas, duracao_h: x.duracao_min ? +(x.duracao_min / 60).toFixed(1) : null })),
    };
  } else if (grupo === "milhas" && acao) {
    // milhas ID: ofertas em milhas (Seats.aero) da rota, coleta mais recente
    const res = await api("GET", `/trechos/${acao}/milhas`);
    const enxuto = (l) => l.slice(0, 5).map((x) => ({ milhas: x.milhas, programa: x.programa, data: x.data_voo, de: x.origem,
      para: x.destino, cias: x.cias, direto: !!x.direto, assentos: x.assentos, id_oferta: x.availability_id }));
    r = { seats_ativo: res.seats_ativo, resumo: res.resumo && { total: res.resumo.total, preco_dinheiro: res.resumo.preco_dinheiro,
          valor_milheiro: res.resumo.valor_milheiro, alvo: res.resumo.alvo, motivos: res.resumo.motivos },
          ida: enxuto(res.ofertas.ida), volta: enxuto(res.ofertas.volta) };
  } else if (grupo === "explorar") {
    // explorar [--programa smiles] [--cabine executiva] [--regiao Europe] [--origem GRU] [--destino LIS] [--max 80000] [--direto]
    const p = opcoes(process.argv.slice(3));
    const q = new URLSearchParams({ limite: String(p.limite || 15) });
    for (const [k, v] of [["programa", p.programa], ["cabine", p.cabine], ["regiao", p.regiao], ["origem", p.origem],
                          ["destino", p.destino], ["max_milhas", p.max]]) if (typeof v === "string") q.set(k, v);
    if (p.direto) q.set("direto", "true");
    const res = await api("GET", `/explorar?${q}`);
    r = { atualizado_em: res.atualizado_em, total_no_retrato: res.total, seats_ativo: res.seats_ativo,
          ofertas: res.ofertas.map((x) => ({ de: x.origem, para: x.destino, regiao: x.regiao_destino, data: x.data_voo,
            programa: x.programa, cabine: x.cabine, milhas: x.milhas, cias: x.cias, direto: !!x.direto, id_oferta: x.availability_id })) };
  } else if (grupo === "viagem" && acao) {
    // viagem ID_OFERTA: voos, taxas e links de emissão (1 chamada à Seats.aero)
    r = await api("GET", `/seats/viagem/${encodeURIComponent(acao)}`);
  } else if (grupo === "promocoes") {
    const p = opcoes(process.argv.slice(3));
    const q = new URLSearchParams();
    for (const k of ["dias", "tipo", "programa"]) if (typeof p[k] === "string") q.set(k, p[k]);
    r = await api("GET", `/promocoes?${q}`);
  } else if (grupo === "executar" && acao) {
    r = await api("POST", `/executar/${acao}`);
  }

  if (r === undefined) {
    console.log(AJUDA);
    process.exit(2);
  }
  console.log(JSON.stringify(r, null, 2));
}

main().catch((e) => {
  console.error(`Falha ao acessar ${BASE}: ${e.message}`);
  process.exit(1);
});
