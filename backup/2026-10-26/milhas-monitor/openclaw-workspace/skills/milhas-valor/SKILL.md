---
name: milhas-valor
description: Calcula o custo do milheiro (compra de pontos, transferência bonificada, paridade, desconto), o valor obtido num resgate e se compensa pagar uma passagem em milhas ou em dinheiro. Use quando o usuário perguntar "vale a pena?", "quanto sai o milheiro?", "compro pontos nessa promo?", "pago em milhas ou em dinheiro?".
metadata: {"openclaw":{"emoji":"🧮","requires":{"bins":["node"]}}}
---

# Valor de milhas

Nunca faça essas contas de cabeça: use a calculadora e responda a partir do JSON.

```bash
node {baseDir}/valor.mjs <comando> [opções]
```

## Comandos

| Pergunta | Comando |
|---|---|
| "Livelo vendendo 1.000 pontos a R$ 70 com 100% de bônus pra Smiles, quanto sai o milheiro?" | `milheiro --preco 70 --bonus 100` |
| "comprar 10 mil pontos por R$ 700 com 40% de desconto e transferir 1:1 com 80%" | `milheiro --preco 700 --pontos 10000 --desconto 40 --bonus 80` |
| "a passagem custa R$ 3.500 ou 120 mil milhas + R$ 300 de taxa, quanto vale o milheiro?" | `resgate --reais 3500 --milhas 120000 --taxas 300` |
| "pago em milhas ou em dinheiro? meu milheiro custa R$ 18" | `comparar --reais 3500 --milhas 120000 --taxas 300 --milheiro 18` |

## Como raciocinar

- **Custo do milheiro** = quanto o usuário paga para ter 1.000 milhas no programa da cia.
  Transferência bonificada reduz o custo; compra com desconto também; os dois juntos
  ("compre com desconto e transfira com bônus") se multiplicam: passe os dois no mesmo
  comando.
- **Valor do resgate** = quanto cada milheiro "economiza" numa passagem concreta. Só
  compensa usar milhas quando o valor do resgate é **maior** que o custo do milheiro.
- Pontos que o usuário **já tem** (acumulados no cartão) têm custo de oportunidade: use
  o milheiro-alvo dele em `USER.md` como referência. Se não houver, pergunte uma vez e
  registre lá.
- Para avaliar uma promoção, consulte também o histórico com o skill `milhas`
  (`promocoes --programa <nome> --dias 90`) e diga se o bônus atual está acima ou
  abaixo do que costuma aparecer.
- Paridade: 1 ponto vira 1 milha → `--paridade 1` (padrão). Se 2 pontos viram 1 milha →
  `--paridade 0.5`.
- Mostre o resultado em uma ou duas linhas, com o número principal em negrito.
