#!/usr/bin/env bash
# Setup 1x na maquina de producao (Debian): permissoes da pasta de dados + cron.
# Uso:  sudo bash setup.sh
#
# - ajusta out/ p/ o container (uid 1000 = usuario 'rpa') poder escrever
# - instala no crontab do ROOT as 4 rodadas (1 estado por horario; desliga ao fim)
# - idempotente: pode rodar de novo sem duplicar
set -e

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

if [ "$(id -u)" != "0" ]; then
    echo "rode como root:  sudo bash setup.sh"
    exit 1
fi

# 1) pasta de dados gravavel pelo container (rpa = uid 1000)
mkdir -p "$ROOT/out"
chown -R 1000:1000 "$ROOT/out"
chmod -R u+rwX "$ROOT/out"
chmod +x "$ROOT/scheduled_run.sh" "$ROOT/dev.sh" 2>/dev/null || true
echo "[ok] out/ pronto (uid 1000) e scripts executaveis"

# 2) imagem: SEMPRE buildar na maquina (sistema pode diferir; build local e o certo).
#    build NAO apaga o banco. So builda se faltar; pra atualizar apos novo codigo,
#    rode 'bash dev.sh build' (ou apague a imagem e rode o setup de novo).
command -v docker >/dev/null 2>&1 || { echo "[erro] docker nao encontrado"; exit 1; }
if docker image inspect "local/zillow:1.0.0" >/dev/null 2>&1; then
    echo "[ok] imagem local/zillow:1.0.0 ja existe (pra atualizar: bash dev.sh build)"
else
    echo "[..] buildando imagem local/zillow:1.0.0 (nao toca no banco)..."
    ( cd "$ROOT" && docker build -t local/zillow:1.0.0 . )
    echo "[ok] imagem buildada"
fi
[ -f "$ROOT/.env" ] || echo "[aviso] .env ausente em $ROOT (o container monta ele)"
[ -f "$ROOT/out/zillow.db" ] && echo "[ok] banco presente: out/zillow.db" || \
    echo "[info] sem out/zillow.db ainda -> sera criado na 1a rodada"

# 3) cron do root: 4 estados em 6/12/18/24h (+15min), 1 por horario
CRON_BLOCK="# === zillow scrape (1 estado por horario; container sai e maquina desliga) ===
15 6  * * *  $ROOT/scheduled_run.sh WY
15 12 * * *  $ROOT/scheduled_run.sh SD
15 18 * * *  $ROOT/scheduled_run.sh KS
15 0  * * *  $ROOT/scheduled_run.sh KY"

# remove qualquer linha antiga do scheduled_run.sh e re-instala
{ crontab -l 2>/dev/null | grep -v "scheduled_run.sh" | grep -v "=== zillow scrape"; \
  echo "$CRON_BLOCK"; } | crontab -
echo "[ok] cron instalado (crontab do root):"
crontab -l | grep -E "zillow scrape|scheduled_run.sh"

echo
echo "setup concluido. Falta so o BOOT AGENDADO (RTC/BIOS) em 06/12/18/24h -- isso e"
echo "no firmware da maquina, fora do SO."
