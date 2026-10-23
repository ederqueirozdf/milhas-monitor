#!/usr/bin/env bash
# Instala o workspace do agente de milhas no OpenClaw da VPS.
#
# Uso (na VPS):  bash /docker/milhas-monitor/openclaw-workspace/instalar.sh [destino]
# Destino padrão: /docker/openclaw-milhas/workspace
#
# - Faz backup dos arquivos existentes antes de sobrescrever.
# - Não sobrescreve memória já criada pelo agente (MEMORY.md, USER.md, memory/) se ela já
#   tiver sido personalizada; nesses casos, deixa a versão nova ao lado como *.novo.
set -euo pipefail

ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO="${1:-/docker/openclaw-milhas/workspace}"
BACKUP="$DESTINO/.backup-$(date +%Y%m%d-%H%M%S)"

[ -d "$DESTINO" ] || { echo "destino não existe: $DESTINO" >&2; exit 1; }
mkdir -p "$BACKUP"

# Arquivos que o OpenClaw gera vazios na 1ª execução: substitui (com backup).
for f in AGENTS.md SOUL.md IDENTITY.md BOOTSTRAP.md; do
  [ -f "$DESTINO/$f" ] && cp -p "$DESTINO/$f" "$BACKUP/"
  cp "$ORIGEM/$f" "$DESTINO/$f"
done

# Memória: só substitui se não existir ou ainda for o template padrão do OpenClaw.
for f in USER.md MEMORY.md; do
  if [ -f "$DESTINO/$f" ] && ! grep -q "Replace the example below" "$DESTINO/$f"; then
    cp "$ORIGEM/$f" "$DESTINO/$f.novo"
    echo "mantido $f existente (versão nova em $f.novo)"
  else
    [ -f "$DESTINO/$f" ] && cp -p "$DESTINO/$f" "$BACKUP/"
    cp "$ORIGEM/$f" "$DESTINO/$f"
  fi
done

# Skills: substitui sempre (código versionado aqui).
mkdir -p "$DESTINO/skills"
for s in "$ORIGEM"/skills/*/; do
  nome="$(basename "$s")"
  [ -d "$DESTINO/skills/$nome" ] && cp -rp "$DESTINO/skills/$nome" "$BACKUP/skill-$nome"
  rm -rf "$DESTINO/skills/$nome"
  cp -r "$s" "$DESTINO/skills/$nome"
done

chown -R 1000:1000 "$DESTINO"
echo "workspace instalado em $DESTINO (backup em $BACKUP)"
ls -la "$DESTINO" "$DESTINO/skills"
