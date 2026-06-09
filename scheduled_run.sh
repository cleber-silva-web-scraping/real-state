#!/usr/bin/env bash
# Rodada agendada (cron): atualiza UM estado, acumulando no mesmo banco, e DESLIGA
# a maquina ao terminar. A maquina liga agendada (RTC/BIOS) no topo da hora; o cron
# dispara 15min depois (boot ja estabilizado).
#
# Uso:  scheduled_run.sh <ESTADO>            (ex: scheduled_run.sh WY)
#       scheduled_run.sh "<E1,E2,E3>"        (varios -> rodam EM SEQUENCIA, relatorio
#                                             por estado, 1 start listando todos)
#
# Pre-requisito (1x): a imagem precisa existir -> rode antes `bash dev.sh build`.
#
# Crontab do ROOT (sudo crontab -e). Pode ser 1 estado por linha OU agrupar pequenos:
#   15 6  * * *  /CAMINHO/new-zillow/scheduled_run.sh "WY,SD,KY"  # 3 pequenos juntos
#   15 18 * * *  /CAMINHO/new-zillow/scheduled_run.sh KS          # 1 maior sozinho
# (agrupar = menos liga/desliga + da p/ atualizar 2x/dia: manha e noite)
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

# bloqueia ate o container terminar; failsafe de 6h se travar
if ! timeout 6h docker wait zillow >> "$LOG" 2>&1; then
    echo "[$(date '+%F %T')] TIMEOUT/erro -> forcando stop" >> "$LOG"
    docker stop zillow >/dev/null 2>&1 || true
fi

echo "[$(date '+%F %T')] === fim REGION=$REGION, desligando a maquina ===" >> "$LOG"
sync
/sbin/poweroff
