#!/usr/bin/env node
// Calculadora do skill "milhas-valor": custo do milheiro e valor de resgate. Sem rede, sem estado.

const AJUDA = `Uso:
  milheiro --preco 70 [--pontos 1000] [--bonus 100] [--paridade 1] [--desconto 0]
      Custo de 1.000 milhas no programa de destino.
      preco = R$ pagos por --pontos pontos (padrão 1000) no programa de origem;
      bonus = % de bônus na transferência; paridade = milhas por ponto (1:1 = 1, 1:2 = 0.5);
      desconto = % de desconto sobre o preço.

  resgate --reais 3500 --milhas 120000 [--taxas 300]
      Quanto cada milheiro "vale" nesse resgate (preço em R$ da mesma passagem vs milhas + taxas).

  comparar --reais 3500 --milhas 120000 [--taxas 300] --milheiro 18
      Compara pagar em dinheiro vs pagar em milhas, dado o custo do seu milheiro.`;

function opcoes(args) {
  const out = {};
  for (let i = 0; i < args.length; i++) {
    if (!args[i].startsWith("--")) continue;
    const valor = Number(String(args[i + 1]).replace(",", "."));
    if (Number.isNaN(valor)) throw new Error(`valor inválido para ${args[i]}: ${args[i + 1]}`);
    out[args[i].slice(2)] = valor;
    i++;
  }
  return out;
}

const brl = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const n = (v) => Math.round(v * 100) / 100;

function exigir(o, ...campos) {
  const faltando = campos.filter((c) => o[c] === undefined);
  if (faltando.length) throw new Error(`faltando: ${faltando.map((c) => "--" + c).join(", ")}`);
}

const comandos = {
  milheiro(o) {
    exigir(o, "preco");
    const pontos = o.pontos ?? 1000;
    const preco = o.preco * (1 - (o.desconto ?? 0) / 100);
    const milhas = pontos * (o.paridade ?? 1) * (1 + (o.bonus ?? 0) / 100);
    return {
      preco_pago: n(preco),
      milhas_recebidas: Math.round(milhas),
      custo_milheiro: n((preco / milhas) * 1000),
      texto: `${brl(preco)} → ${Math.round(milhas).toLocaleString("pt-BR")} milhas = ${brl((preco / milhas) * 1000)} por milheiro`,
    };
  },
  resgate(o) {
    exigir(o, "reais", "milhas");
    const valor = ((o.reais - (o.taxas ?? 0)) / o.milhas) * 1000;
    return { valor_milheiro: n(valor), texto: `cada milheiro rende ${brl(valor)} neste resgate` };
  },
  comparar(o) {
    exigir(o, "reais", "milhas", "milheiro");
    const custoMilhas = (o.milhas / 1000) * o.milheiro + (o.taxas ?? 0);
    const economia = o.reais - custoMilhas;
    const valor = ((o.reais - (o.taxas ?? 0)) / o.milhas) * 1000;
    return {
      custo_em_milhas: n(custoMilhas),
      custo_em_dinheiro: n(o.reais),
      economia: n(economia),
      valor_milheiro_resgate: n(valor),
      recomendacao: economia > 0 ? "milhas" : "dinheiro",
      texto:
        economia > 0
          ? `Pague em milhas: sai ${brl(custoMilhas)} vs ${brl(o.reais)} (economia de ${brl(economia)}; resgate a ${brl(valor)}/milheiro, acima do seu custo de ${brl(o.milheiro)})`
          : `Pague em dinheiro: em milhas sairia ${brl(custoMilhas)} vs ${brl(o.reais)} (resgate a ${brl(valor)}/milheiro, abaixo do seu custo de ${brl(o.milheiro)})`,
    };
  },
};

try {
  const [cmd, ...args] = process.argv.slice(2);
  if (!comandos[cmd]) {
    console.log(AJUDA);
    process.exit(2);
  }
  console.log(JSON.stringify(comandos[cmd](opcoes(args)), null, 2));
} catch (e) {
  console.error(`ERRO: ${e.message}\n\n${AJUDA}`);
  process.exit(1);
}
