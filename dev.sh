#!/usr/bin/env bash
# Helper estavel p/ ciclo de dev do container (invocacao sempre identica ->
# aprovacao de permissao gruda). Uso: bash dev.sh <cmd>
set -e
cd "$(dirname "$0")"

IMG=local/zillow:1.0.0
NAME=zillow
STATES="${REGION:-${STATES:-WY}}"   # REGION=CA|WY|TX ... (atalho); aceita CSV tb
MAXURLS="${MAXURLS:-0}"   # 0 = todos os imoveis do estado (sem limite)
HPORT="${HPORT:-8010}"    # porta do host p/ o backend (8000 ja usado por outro projeto)
OUTDIR="${OUTDIR:-$(pwd)/out}"        # diretorio persistido no disco (DB/CSV/debug)
EXIT_AFTER="${EXIT_AFTER:-1}"         # 1 = encerra o container ao terminar (default)

case "${1:-}" in
  build)
    docker build -t "$IMG" .
    ;;
  run)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    mkdir -p "$OUTDIR"
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" --privileged \
      -p 6911:6901 -p 5911:5901 -p "$HPORT":8000 -p 9232:9222 \
      -v "$OUTDIR:/home/rpa/out" \
      -v "$(pwd)/.env:/home/rpa/.env:ro" \
      -v /dev/shm:/dev/shm -v /run/dbus:/run/dbus --shm-size=2g \
      -e POC_MODE=1 -e POC_BROWSER_SEQUENCE=chrome -e POC_BROWSER_ROTATION_ENABLED=0 \
      -e POC_BROWSER_DISABLE_SANDBOX=1 -e POC_BROWSER_DISABLE_GPU=1 -e POC_BROWSER_HIDE_AUTOMATION=1 \
      -e POC_CLEAN_OUTPUT_AFTER_SEND=0 -e POC_EXIT_AFTER_FINISH="$EXIT_AFTER" -e POC_CAPTCHA_DRYRUN=0 \
      -e POC_COLLECT_MODE=api -e POC_COLLECT_STATES="$STATES" -e POC_COLLECT_MAX_URLS="$MAXURLS" \
      "$IMG" >/dev/null
    echo "container $NAME up (states=$STATES max_urls=$MAXURLS exit_after=$EXIT_AFTER out=$OUTDIR)"
    ;;
  resume)
    # build + run mantendo out/ (DB/checkpoint) -> retoma de onde parou
    bash "$0" build && bash "$0" run
    ;;
  diag)
    # build + run com poucas urls (acumula no banco; NAO limpa) -> teste rapido
    MAXURLS=5 bash "$0" build && MAXURLS=5 bash "$0" run
    ;;
  status)
    curl -s "http://localhost:$HPORT/status"
    ;;
  stop)
    docker stop "$NAME" >/dev/null 2>&1 || true
    echo "stopped"
    ;;
  logs)
    docker logs --tail 60 "$NAME" 2>&1
    ;;
  exec)
    shift
    docker exec "$NAME" bash -lc "$*"
    ;;
  daily|"")
    # Rodada diaria (default): sobe o container em daemon coletando a REGION (WY),
    # ACUMULANDO no mesmo banco (out/zillow.db). Apaga so o checkpoint p/ forcar
    # re-coleta de URLs (acha imoveis novos e mudancas); o 2-hash pula os inalterados.
    # Encerra sozinho ao terminar. Agende no cron pra rodar todo dia.
    echo "== daily: REGION=$STATES, acumulando em $OUTDIR/zillow.db =="
    bash "$0" build
    mkdir -p "$OUTDIR"
    rm -f "$OUTDIR/checkpoint.json" "$OUTDIR"/zillow_urls_*.csv 2>/dev/null
    bash "$0" run
    ;;
  *)
    echo "uso: bash dev.sh [daily]|build|run|resume|diag|status|stop|logs|exec"
    echo "  (sem comando destrutivo: o banco se cria sozinho e nunca e apagado pelo tooling;"
    echo "   pra zerar de proposito: rm out/zillow.db)"
    ;;
esac
