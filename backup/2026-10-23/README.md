# Backup 2026-10-23 — agente openclaw-milhas e app milhas-monitor

Retrato da VPS `srv1767526` (Hostinger) em 23/10/2026, commit da app `be7bb36`.
O repositório é **público**: este backup contém só estrutura, sem segredos nem dados pessoais.

## Conteúdo

| Pasta | Origem na VPS | O que tem |
|---|---|---|
| `openclaw-milhas/` | `/docker/openclaw-milhas` | `docker-compose.yml` (template Hostinger), `openclaw.json` com segredos mascarados, `.env.example` só com nomes das variáveis |
| `openclaw-milhas/workspace/` | `.../workspace` | `AGENTS.md`, `SOUL.md`, `IDENTITY.md` e os skills `milhas` e `milhas-valor` em uso |
| `milhas-monitor/` | `/docker/milhas-monitor` | App implantada: código, `config.yaml`, compose + override Traefik, workspace do agente (templates); `.env.example.vps` só com os nomes das variáveis |

Configuração relevante do agente (em `openclaw.json`): imagem `ghcr.io/openclaw/openclaw:2026.9.9`,
modelo `deepseek/deepseek-flash`, voz ElevenLabs persona `comissaria` (`tts.auto: inbound`, voz
`xPnmQf6Ow3GGYWWURFPi`), transcrição Scribe v2, plugins `deepseek`, `telegram`, `elevenlabs`.

## Fora do backup (de propósito)

- **Segredos:** `.env` dos dois projetos; tokens do gateway e do bot e chaves em `openclaw.json`
  (aparecem como `<REDACTED>`); `openclaw.json.bak*`; `auth-secrets/`; `config-journal-fingerprint.key`.
- **Dados pessoais:** `USER.md`, `MEMORY.md` e `memory/` do agente; rotas cadastradas e histórico
  de preços (banco SQLite no volume `milhas-monitor_milhas_data`).
- **Estado de runtime:** `data/state/*.sqlite` (sessões e credenciais do OpenClaw), `cache/`, `tmp/`, `media/`.

Para guardar esses itens, faça um backup separado fora do repositório, por exemplo:

```bash
tar czf /root/openclaw-milhas-privado-$(date +%F).tgz -C /docker openclaw-milhas
docker run --rm -v milhas-monitor_milhas_data:/d -v /root:/out alpine tar czf /out/milhas-db-$(date +%F).tgz -C /d .
```

## Restauração

1. **App:** copie `milhas-monitor/` para `/docker/milhas-monitor`, crie o `.env` a partir de
   `.env.example.vps` (preencha `MILHAS_API_TOKEN`, `TELEGRAM_*`, `SERPAPI_KEY`) e rode
   `docker compose -f docker-compose.yml -f docker-compose.traefik.yml up -d --build`.
   O serviço entra na rede `openclaw-milhas_default`: suba o OpenClaw antes.
2. **OpenClaw:** use `openclaw-milhas/docker-compose.yml`, crie o `.env` a partir do
   `.env.example` e copie `openclaw.json` para `data/`, trocando cada `<REDACTED>` pelo valor real
   (ou remova a chave para usar a variável de ambiente correspondente). Copie `workspace/` para
   `/docker/openclaw-milhas/workspace` e rode `chown -R 1000:1000` nas pastas `data` e `workspace`.
