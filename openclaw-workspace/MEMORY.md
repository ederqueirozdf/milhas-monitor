# MEMORY.md - Fatos e decisões duráveis

Carregar só na sessão principal. Resumo curado, não log.

## Arquitetura (2026-10-23)

- O serviço `milhas-monitor` (container `milhas`, `/docker/milhas-monitor`) faz a coleta e
  os alertas. Este agente só conversa com ele pela API (skills `milhas` e `milhas-valor`).
- Alertas vão direto pela Bot API do Telegram, sem passar pelo LLM, para não gastar tokens.
  O mesmo bot é usado pelo OpenClaw; só o OpenClaw consome `getUpdates`.
- O serviço entra na rede `openclaw-milhas_default`; o compose do OpenClaw é o template
  genérico da Hostinger e não deve ser alterado.
- API publicada via Traefik em `https://milhas-monitor.srv1767526.hstgr.cloud` (token
  obrigatório, exceto `/health`).
- Fontes: RSS de Passageiro de Primeira, Melhores Destinos, Pontos pra Voar, Mestre das
  Milhas. Preços em dinheiro via SerpAPI (Google Flights), se `SERPAPI_KEY` estiver definida.

## Decisões

- 2026-10-22: alertar qualquer % de bônus; monitorar transferência e compra de pontos;
  trechos sem limite de quantidade.
- 2026-10-23: OpenClaw desta instância fixado em `2026.9.9`. A `2026.10.6` (`:latest`)
  não traz o plugin `deepseek` embutido e entrava em loop no doctor. Não voltar para
  `:latest` sem instalar `@openclaw/deepseek-provider` antes.

- 2026-10-23: voz no Telegram via ElevenLabs. `tts.auto: inbound` (responde em áudio só
  quando o usuário manda áudio), persona `comissaria` (voz Sarah `EXAVITQu4vr4xnSDxMaL`,
  `eleven_multilingual_v2`, pt), transcrição com Scribe v2. Chave em `ELEVENLABS_API_KEY`
  no `.env` do OpenClaw. Modelo padrão do agente: `deepseek/deepseek-flash`.

- 2026-10-26: integração Seats.aero (plano Pro, 1.000 chamadas/dia, uso não comercial).
  Milhas por rota a cada 6h (`max_milhas` = total ida+volta), Explorar diário (Smiles e Azul,
  América do Sul, econômica e executiva, limites por região no config.yaml), detalhes e links
  de emissão sob demanda. LATAM Pass não é suportado pela Seats.aero. Versão anterior: tag
  `v1.0.0-pre-seats` (backup privado de banco e .env em `/root/backups/` na VPS).

## Próximas fases

- Fallback com LLM para posts que o classificador por regex não reconhece.

## Programas e saldos do usuário

_(registre aqui quando o usuário contar — sem dados de login)_
