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
OpenClaw (chat Telegram) ──skill milhas──► milhas:8000 (API + agendador + SQLite) ──Bot API──► Telegram
```

## Deploy na VPS

### 1. Subir o serviço

```bash
mkdir -p /docker/milhas-monitor && cd /docker/milhas-monitor
# copie o conteúdo deste repositório para cá (git clone ou scp)
cp .env.example .env
openssl rand -hex 32          # cole em MILHAS_API_TOKEN
nano .env                     # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SERPAPI_KEY (opcional)
docker compose up -d --build
docker compose logs -f milhas # deve aparecer "feed ...: N posts novos"
```

**Seu `TELEGRAM_CHAT_ID`:** numa conversa privada com o bot, é o seu user id do Telegram. Ele já
deve estar no `allowFrom` do canal Telegram do seu `openclaw.json`. Também dá para pegar com o
@userinfobot. Não use `getUpdates`: o OpenClaw já consome as atualizações desse bot.

Para testar:

```bash
TOKEN=$(grep MILHAS_API_TOKEN .env | cut -d= -f2)
curl -s -X POST -H "Authorization: Bearer $TOKEN" localhost:8090/executar/teste
```

### 2. Conectar o OpenClaw

No `docker-compose.yml` do seu OpenClaw, adicione no serviço do gateway:

```yaml
    environment:
      MILHAS_API_URL: http://milhas:8000
      MILHAS_API_TOKEN: ${MILHAS_API_TOKEN}   # mesmo valor do .env do milhas-monitor
    networks:
      - default
      - milhas_net

networks:
  milhas_net:
    external: true
```

Copie o skill para o workspace do agente. Ajuste o caminho ao seu volume: no seu padrão,
`/docker/<openclaw>/data` corresponde a `/home/node/.openclaw`. Se o agente tem workspace
próprio, como `workspace-series-trail`, use a pasta `skills/` dele.

```bash
cp -r openclaw-skill/milhas /docker/<openclaw>/data/workspace/skills/
chown -R 1000:1000 /docker/<openclaw>/data/workspace/skills/milhas   # usuário node
cd /docker/<openclaw> && docker compose up -d
```

Teste no Telegram: *"monitora Brasília → Lisboa, ida 10/05/2027 volta 25/05, executiva, até R$ 6 mil"*.

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
