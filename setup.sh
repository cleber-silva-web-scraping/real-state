#!/usr/bin/env bash
# Setup 1x na maquina de producao (Debian): permissoes + cron + build.
# Uso:  sudo bash setup.sh
#
# Ordem proposital: permissoes -> CRON -> build (por ultimo, nao-fatal). Assim o
# cron fica instalado mesmo que o build demore/falhe. Idempotente.

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

if [ "$(id -u)" != "0" ]; then
    echo "rode como root:  sudo bash setup.sh"
    exit 1
fi

# 1) pasta de dados gravavel pelo container (rpa = uid 1000) + scripts executaveis
mkdir -p "$ROOT/out"
chown -R 1000:1000 "$ROOT/out" || true
chmod -R u+rwX "$ROOT/out" || true
chmod +x "$ROOT/scheduled_run.sh" "$ROOT/dev.sh" 2>/dev/null || true
echo "[ok] out/ pronto (uid 1000) + scripts executaveis"

# 2) CRON do root (antes do build, pra nao depender dele). cron precisa estar instalado.
if ! command -v crontab >/dev/null 2>&1; then
    echo "[..] 'cron' ausente -> instalando (apt)..."
    apt-get update -y >/dev/null 2>&1 && apt-get install -y cron >/dev/null 2>&1 || \
        echo "[aviso] nao consegui instalar 'cron' automaticamente -> 'sudo apt install cron'"
fi
systemctl enable --now cron >/dev/null 2>&1 || service cron start >/dev/null 2>&1 || true

CRON_BLOCK="# === zillow scrape (1 estado por horario; container sai e maquina desliga) ===
15 6  * * *  $ROOT/scheduled_run.sh WY
15 12 * * *  $ROOT/scheduled_run.sh SD
15 18 * * *  $ROOT/scheduled_run.sh KS
15 0  * * *  $ROOT/scheduled_run.sh KY"

# remove linhas antigas do scheduled_run.sh e re-instala (idempotente)
{ crontab -l 2>/dev/null | grep -v "scheduled_run.sh" | grep -v "=== zillow scrape" || true; \
  echo "$CRON_BLOCK"; } | crontab -
echo "[ok] cron instalado. Confira com:  sudo crontab -l"
crontab -l 2>/dev/null | grep -E "zillow scrape|scheduled_run.sh" || echo "[ERRO] cron NAO ficou! ver 'sudo crontab -l'"

# 3) checagens
[ -f "$ROOT/.env" ] || echo "[aviso] .env ausente em $ROOT (o container monta ele)"
[ -f "$ROOT/out/zillow.db" ] && echo "[ok] banco presente: out/zillow.db" || \
    echo "[info] sem out/zillow.db ainda -> sera criado na 1a rodada"

# 4) BUILD por ULTIMO (nao-fatal: se falhar, cron ja esta instalado)
if ! command -v docker >/dev/null 2>&1; then
    echo "[aviso] docker nao encontrado -> instale o docker e rode 'bash dev.sh build'"
elif docker image inspect "local/zillow:1.0.0" >/dev/null 2>&1; then
    echo "[ok] imagem local/zillow:1.0.0 ja existe (atualizar: bash dev.sh build)"
else
    echo "[..] buildando imagem (nao toca no banco)..."
    if ( cd "$ROOT" && docker build -t local/zillow:1.0.0 . ); then
        echo "[ok] imagem buildada"
    else
        echo "[aviso] build FALHOU -> rode manual 'bash dev.sh build' depois (cron ja ok)"
    fi
fi

echo
echo "setup concluido. Falta so o BOOT AGENDADO (RTC/BIOS) em 06/12/18/24h (firmware)."
