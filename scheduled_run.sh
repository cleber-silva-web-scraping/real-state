#!/usr/bin/env bash
# Rodada agendada (cron): atualiza UM estado, acumulando no mesmo banco, e DESLIGA
# a maquina ao terminar. A maquina liga agendada (RTC/BIOS) no topo da hora; o cron
# dispara 15min depois (boot ja estabilizado).
#
# Uso:  scheduled_run.sh <ESTADO>     (ex: scheduled_run.sh WY)
#
# Pre-requisito (1x): a imagem precisa existir -> rode antes `bash dev.sh build`.
#
# Crontab do ROOT (sudo crontab -e) — um estado por horario (6/12/18/24h + 15min):
#   15 6  * * *  /CAMINHO/new-zillow/scheduled_run.sh WY
#   15 12 * * *  /CAMINHO/new-zillow/scheduled_run.sh SD
#   15 18 * * *  /CAMINHO/new-zillow/scheduled_run.sh KS
#   15 0  * * *  /CAMINHO/new-zillow/scheduled_run.sh KY   # 24h
#
# Precisa de root (docker + poweroff). Log em out/cron.log.
set -u

REGION="${1:?uso: scheduled_run.sh <ESTADO>  (ex: WY)}"
cd "$(dirname "$(readlink -f "$0")")" || exit 1
mkdir -p out
LOG="out/cron.log"

echo "[$(date '+%F %T')] === inicio REGION=$REGION ===" >> "$LOG"

# forca re-coleta de URLs (acha novos/mudancas); MANTEM o banco (acumula)
rm -f out/checkpoint.json out/zillow_urls_*.csv

# sobe o container (imagem ja buildada, SEM build aqui); EXIT_AFTER=1 -> sai ao terminar
REGION="$REGION" bash dev.sh run >> "$LOG" 2>&1

# bloqueia ate o container terminar; failsafe de 3h se travar
if ! timeout 3h docker wait zillow >> "$LOG" 2>&1; then
    echo "[$(date '+%F %T')] TIMEOUT/erro -> forcando stop" >> "$LOG"
    docker stop zillow >/dev/null 2>&1 || true
fi

echo "[$(date '+%F %T')] === fim REGION=$REGION, desligando a maquina ===" >> "$LOG"
sync
/sbin/poweroff
