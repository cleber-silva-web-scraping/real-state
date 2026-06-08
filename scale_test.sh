#!/usr/bin/env bash
# Teste de escala: roda N=100,200,300 (CA), limpando a base entre cada, e salva
# tempo (fases) + banda (NetIO real + bytes backend) p/ projecao de proxy.
set -u
cd "$(dirname "$0")"
NAME=zillow
HPORT=8010
STATES=CA
REPORT="scale_results.md"
SIZES=(100 200 300)

st() { curl -s "http://localhost:$HPORT/status" 2>/dev/null; }
jget() { python3 -c "import sys,json;d=json.load(sys.stdin);print(eval(\"d$1\"))" 2>/dev/null; }

echo "# Scale test CA — $(date)" > "$REPORT"
echo "" >> "$REPORT"

for N in "${SIZES[@]}"; do
  echo "=== N=$N ==="
  docker stop "$NAME" >/dev/null 2>&1
  docker run --rm -v "$(pwd)/out:/out" busybox chown -R 1000:1000 /out >/dev/null 2>&1
  rm -rf out/* 2>/dev/null
  START=$(date +%s)
  MAXURLS=$N STATES=$STATES bash dev.sh run >/dev/null 2>&1

  t_urls=""; t_cap=""; t_fin=""; last_det=0
  while :; do
    now=$(date +%s); el=$((now-START))
    [ $el -ge 1800 ] && { echo "timeout N=$N"; break; }
    s=$(st)
    [ -z "$s" ] && { sleep 5; continue; }
    stage=$(echo "$s" | jget "['stage']")
    det=$(echo "$s" | jget "['details_saved']")
    cap=$(echo "$s" | jget "['detail_capture_done']")
    status=$(echo "$s" | jget "['status']")
    [ -z "$t_urls" ] && [ "$stage" = "detail" ] && t_urls=$el
    [ -z "$t_cap" ] && [ "$cap" = "True" ] && t_cap=$el
    echo "  [${el}s] stage=$stage det=$det cap=$cap $status"
    if [ "$status" = "finished" ]; then t_fin=$el; break; fi
    sleep 8
  done

  # metricas finais
  s=$(st)
  netio=$(docker stats "$NAME" --no-stream --format '{{.NetIO}}' 2>/dev/null)
  urls=$(echo "$s" | jget "['urls_collected']")
  det=$(echo "$s" | jget "['details_saved']")
  jbytes=$(echo "$s" | jget "['metrics']['total_json_bytes']")
  pbytes=$(echo "$s" | jget "['metrics']['total_page_bytes']")
  ploads=$(echo "$s" | jget "['metrics']['page_loads']")
  tbytes=$(echo "$s" | jget "['metrics']['total_mb']")
  dbdet=$(python3 -c "import sqlite3;print(sqlite3.connect('out/zillow.db').execute('SELECT COUNT(*) FROM details').fetchone()[0])" 2>/dev/null)

  {
    echo "## N=$N"
    echo "- tempo total: ${t_fin}s | urls_done: ${t_urls}s | capture_done: ${t_cap}s"
    echo "- fase URLs: ${t_urls}s | fase captura: $((${t_cap:-0}-${t_urls:-0}))s | fase detalhes: $((${t_fin:-0}-${t_cap:-0}))s"
    echo "- urls=$urls details=$det (db=$dbdet)"
    echo "- NetIO (banda real total): $netio"
    echo "- backend bytes: json=${jbytes}B page=${pbytes}B page_loads=${ploads} total=${tbytes}MB"
    echo "- s/detalhe (fase det): $(python3 -c "print(round((${t_fin:-0}-${t_cap:-0})/max(1,$det),2))" 2>/dev/null)"
    echo ""
  } >> "$REPORT"
  echo "  -> salvo N=$N (total ${t_fin}s, netio $netio)"
done

docker stop "$NAME" >/dev/null 2>&1
echo "=== FIM. Relatorio: $REPORT ==="
cat "$REPORT"
