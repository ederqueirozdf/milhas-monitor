# milhas-monitor

Serviço que roda 24/7 ao lado do OpenClaw:

- **Coleta a cada 30 min** os feeds de Passageiro de Primeira, Melhores Destinos, Pontos pra Voar
  e Mestre das Milhas, e classifica cada post sem usar LLM: transferência bonificada (qualquer %),
  compra de pontos/milhas, clube e cartão. Alerta imediato no Telegram, sem duplicar a mesma
  promo publicada por vários blogs.
- **Trechos** cadastrados por conversa com o OpenClaw (skill `milhas`). Cada trecho é monitorado
  de duas formas: posts de blog que citam o destino e, se houver `SERPAPI_KEY`, preço no Google
  Flights a cada 12h, com alerta quando fica abaixo do seu teto ou do menor preço já visto.
- **Resumo diário** às 08:00.

```
OpenClaw (chat Telegram) ──skill milhas──► milhas:8000 (na rede do OpenClaw) (API + agendador + SQLite) ──Bot API──► Telegram
```

## Deploy na VPS

### 1. Subir o serviço

```bash
mkdir -p /docker/milhas-monitor && cd /docker/milhas-monitor
# copie o conteúdo deste repositório para cá (git clone ou scp)
cp .env.example .env
openssl rand -hex 32          # cole em MILHAS_API_TOKEN
nano .env                     # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SERPAPI_KEY (opcional)
docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d --build
docker compose logs -f milhas # deve aparecer "feed ...: N posts novos"
```

Com o override do Traefik (mesmo padrão do `gestao-fdp`: Traefik em host mode, resolver
`letsencrypt`, sem rede externa), a API fica em `https://milhas-monitor.srv1767526.hstgr.cloud`.
Sem o override (`docker compose up -d --build`), fica só interna e em `127.0.0.1:8090`.

**Seu `TELEGRAM_CHAT_ID`:** numa conversa privada com o bot, é o seu user id do Telegram. Ele já
deve estar no `allowFrom` do canal Telegram do seu `openclaw.json`. Também dá para pegar com o
@userinfobot. Não use `getUpdates`: o OpenClaw já consome as atualizações desse bot.

Para testar:

```bash
TOKEN=$(grep MILHAS_API_TOKEN .env | cut -d= -f2)
curl -s -X POST -H "Authorization: Bearer $TOKEN" localhost:8090/executar/teste
```

### 2. Conectar o OpenClaw

O compose do OpenClaw (template genérico da Hostinger) **não muda**. O milhas-monitor entra
na rede default do OpenClaw (`OPENCLAW_NETWORK` no `.env`, veja com
`docker network ls | grep openclaw`). Por isso, **suba o OpenClaw antes**.

No `.env` do OpenClaw (o compose já faz `env_file: .env`), acrescente:

```env
MILHAS_API_URL=http://milhas:8000
MILHAS_API_TOKEN=<mesmo valor do .env do milhas-monitor>
```

Instale o workspace do agente (personalidade, regras, memória inicial e skills). O
script faz backup do que já existir e não sobrescreve memória personalizada:

```bash
bash /docker/milhas-monitor/openclaw-workspace/instalar.sh /docker/openclaw-milhas/workspace
cd /docker/openclaw-milhas && docker compose up -d
```

### Estrutura do workspace (`openclaw-workspace/`)

| Arquivo | Papel |
|---|---|
| `AGENTS.md` | Missão, divisão de trabalho com o serviço, regras, memória, automações |
| `SOUL.md` | Personalidade e limites ("número antes de adjetivo") |
| `IDENTITY.md` | Nome (Rota), emoji ✈️, vibe |
| `USER.md` | Preferências do usuário como diretivas datadas |
| `MEMORY.md` | Arquitetura e decisões duráveis (só sessão principal) |
| `BOOTSTRAP.md` | Onboarding da 1ª conversa; o agente apaga ao concluir |
| `skills/milhas/` | Trechos, promoções, status e execução de tarefas (via API) |
| `skills/milhas-valor/` | Calculadora: custo do milheiro, valor de resgate, milhas vs dinheiro |

Teste no Telegram: *"monitora Brasília → Lisboa, ida 10/05/2027 volta 25/05, executiva, até R$ 6 mil"*.

## Seats.aero: API ou MCP

Milhas por rota e Explorar vêm da Seats.aero por duas fontes (`seats.fonte` no `config.yaml`):

| Fonte | Como ativar | Alcance |
|---|---|---|
| API | `SEATS_AERO_API_KEY` no `.env` (seats.aero/settings → API) | Conta Pro, Explorar nativo por região |
| MCP com login | `docker exec -it milhas python -m milhas.mcp_login` (uma vez) | Conta Pro, mesma cota de 1.000/dia |
| MCP público | nada | Só voos até 60 dias à frente |

Com `fonte: auto` (padrão) o serviço usa a API se houver chave, senão o MCP, e cai para o MCP
se a API falhar. O login guarda um refresh token (renovado a cada uso) no SQLite; para sair:
`docker exec -it milhas python -m milhas.mcp_login --sair`. O Radar do painel mostra a fonte ativa.

O OpenClaw também tem o servidor MCP `seats` conectado (ferramentas direto no chat). Para
usar a conta Pro nele: `openclaw mcp configure seats --auth oauth` e `openclaw mcp login seats`.

## Ajustes

Tudo fica em `app/config.yaml`: frequências, feeds, programas e apelidos, mapa IATA→cidade e
regras de re-alerta. Depois de editar, rode `docker compose restart milhas`.

**Cota da SerpAPI:** cada trecho consome `max_buscas_por_trecho` × (24 / `voos_horas`) buscas
por dia. O padrão é 2 × 2 = 4 por dia, cerca de 120 por mês por trecho.

## API (uso interno)

| Método | Rota | |
|---|---|---|
| GET | `/trechos?todos=true` | lista |
| POST | `/trechos` | cria (`origem`, `destino`, `data_ida`, `data_volta`, `flex_dias`, `cabine`, `adultos`, `max_reais`, `max_milhas`, `palavras`, `observacao`) |
| PATCH | `/trechos/{id}` | edita, pausa (`ativo: false`) |
| DELETE | `/trechos/{id}` | remove |
| GET | `/trechos/{id}/precos` | histórico |
| GET | `/promocoes?dias=7&tipo=&programa=` | promoções coletadas |
| POST | `/executar/{rss,voos,resumo,limpeza,teste}` | roda na hora |

Todas, menos `/health`, exigem `Authorization: Bearer $MILHAS_API_TOKEN`.

## Testes

```bash
cd app && python -m venv ../.venv && ../.venv/bin/pip install -r requirements.txt pytest
../.venv/bin/python -m pytest -q tests
```

## Próximas fases

- **Milhas nos trechos:** integrar Seats.aero (API paga) para disponibilidade em Smiles, LATAM
  Pass etc. e usar o `max_milhas` já cadastrado.
- **Fallback com LLM** para posts que as regex não classificam.
