#!/bin/bash
set -e

BACKEND_PID=""
BROWSER_PID=""
BROWSER_MANAGER_PID=""
BROWSER_WATCHER_PID=""
GUI_POC_PID=""
CAPTCHA_SOLVER_PID=""
CURRENT_BROWSER=""
BROWSER_EXTENSION_ENABLED=1
XVFB_PID=""
LXDE_PID=""
X11VNC_PID=""

cleanup() {
  local exit_code=$?
  [ -n "${CAPTCHA_SOLVER_PID}" ] && kill "${CAPTCHA_SOLVER_PID}" 2>/dev/null || true
  [ -n "${BACKEND_PID}" ] && kill "${BACKEND_PID}" 2>/dev/null || true
  exit "${exit_code}"
}
trap cleanup EXIT

EXTENSION_DIR="/home/rpa/chrome-agents"
DEFAULT_STARTUP_URL="http://127.0.0.1:8000/health"
STARTUP_URL="${POC_STARTUP_URL:-${DEFAULT_STARTUP_URL}}"
POC_BROWSER_STARTUP_DELAY="${POC_BROWSER_STARTUP_DELAY:-0}"
BROWSER_ROTATION_INTERVAL_SECONDS="${POC_BROWSER_ROTATION_INTERVAL_SECONDS:-300}"
BROWSER_SEQUENCE_RAW="${POC_BROWSER_SEQUENCE:-chrome,opera}"
BROWSER_ROTATION_ENABLED_RAW="${POC_BROWSER_ROTATION_ENABLED:-1}"
BROWSER_USER_DATA_BASE_DIR="${POC_BROWSER_USER_DATA_BASE_DIR:-${POC_CHROME_USER_DATA_DIR:-/home/rpa/chrome-user-data}}"
BROWSER_DISABLE_SANDBOX_RAW="${POC_BROWSER_DISABLE_SANDBOX:-0}"
BROWSER_DISABLE_GPU_RAW="${POC_BROWSER_DISABLE_GPU:-0}"
BROWSER_REMOTE_DEBUGGING_ENABLED_RAW="${POC_BROWSER_REMOTE_DEBUGGING_ENABLED:-0}"
BROWSER_HIDE_AUTOMATION_RAW="${POC_BROWSER_HIDE_AUTOMATION:-0}"
BROWSER_VERBOSE_LOGS_RAW="${POC_BROWSER_VERBOSE_LOGS:-0}"
declare -a BROWSER_SEQUENCE=()

normalize_bool() {
  local value
  value="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "${value}" in
    1|true|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

trim_whitespace() {
  local value="${1:-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  echo "${value}"
}

resolve_browser_command() {
  local browser="${1:-}"
  case "${browser}" in
    chrome)
      if command -v google-chrome >/dev/null 2>&1; then
        echo "google-chrome"
        return 0
      fi
      if command -v google-chrome-stable >/dev/null 2>&1; then
        echo "google-chrome-stable"
        return 0
      fi
      ;;
    opera)
      if command -v opera >/dev/null 2>&1; then
        echo "opera"
        return 0
      fi
      if command -v opera-stable >/dev/null 2>&1; then
        echo "opera-stable"
        return 0
      fi
      ;;
  esac
  return 1
}

build_browser_sequence() {
  local raw="${BROWSER_SEQUENCE_RAW}"
  local item=""
  local lowered=""
  IFS=',' read -r -a requested <<< "${raw}"

  BROWSER_SEQUENCE=()
  for item in "${requested[@]}"; do
    item="$(trim_whitespace "${item}")"
    lowered="$(echo "${item}" | tr '[:upper:]' '[:lower:]')"
    if [ -z "${lowered}" ]; then
      continue
    fi
    if [ "${lowered}" != "chrome" ] && [ "${lowered}" != "opera" ]; then
      echo "warning: unsupported browser '${lowered}', skipping."
      continue
    fi
    if ! resolve_browser_command "${lowered}" >/dev/null 2>&1; then
      echo "warning: browser '${lowered}' not installed, skipping."
      continue
    fi
    BROWSER_SEQUENCE+=("${lowered}")
  done

  if [ "${#BROWSER_SEQUENCE[@]}" -eq 0 ]; then
    if resolve_browser_command chrome >/dev/null 2>&1; then
      BROWSER_SEQUENCE=("chrome")
    elif resolve_browser_command opera >/dev/null 2>&1; then
      BROWSER_SEQUENCE=("opera")
    else
      echo "error: no supported browser executable found (chrome/opera)."
      exit 1
    fi
  fi
}

browser_user_data_dir() {
  local browser="${1:-chrome}"
  if [ "${#BROWSER_SEQUENCE[@]}" -eq 1 ] && [ "${browser}" = "chrome" ]; then
    echo "${POC_CHROME_USER_DATA_DIR:-/home/rpa/chrome-user-data}"
    return
  fi
  echo "${BROWSER_USER_DATA_BASE_DIR}/${browser}"
}

stop_active_browser() {
  if [ -n "${BROWSER_PID}" ] && kill -0 "${BROWSER_PID}" >/dev/null 2>&1; then
    echo "stopping browser '${CURRENT_BROWSER}' (pid=${BROWSER_PID}) ..."
    kill "${BROWSER_PID}" >/dev/null 2>&1 || true
    wait "${BROWSER_PID}" 2>/dev/null || true
  fi
  BROWSER_PID=""
  CURRENT_BROWSER=""
}

start_browser() {
  local browser="${1:-chrome}"
  local cmd=""
  local user_data_dir=""
  local -a browser_args=()

  if ! cmd="$(resolve_browser_command "${browser}")"; then
    echo "warning: could not resolve command for browser '${browser}'."
    return 1
  fi

  user_data_dir="$(browser_user_data_dir "${browser}")"
  echo "resetting browser profile: ${user_data_dir}"
  rm -rf "${user_data_dir}"
  mkdir -p "${user_data_dir}"

  echo "starting browser '${browser}' with command '${cmd}' ..."
  echo "browser profile dir: ${user_data_dir}"
  browser_args=(
    --no-first-run
    --no-default-browser-check
    --start-maximized
    --disable-dev-shm-usage
    --user-data-dir="${user_data_dir}"
  )

  if normalize_bool "${BROWSER_DISABLE_SANDBOX_RAW}"; then
    browser_args+=(--no-sandbox --disable-setuid-sandbox)
    echo "browser flag enabled: --no-sandbox"
  else
    echo "browser sandbox mode: enabled"
  fi

  if normalize_bool "${BROWSER_DISABLE_GPU_RAW}"; then
    browser_args+=(--disable-gpu)
    echo "browser flag enabled: --disable-gpu"
  else
    echo "browser gpu mode: enabled (no forced --disable-gpu)"
  fi

  if normalize_bool "${BROWSER_REMOTE_DEBUGGING_ENABLED_RAW}"; then
    browser_args+=(
      --remote-debugging-address=0.0.0.0
      --remote-debugging-port=9222
    )
    echo "browser remote debugging: enabled on 9222"
  else
    echo "browser remote debugging: disabled"
  fi

  if normalize_bool "${BROWSER_HIDE_AUTOMATION_RAW}"; then
    browser_args+=(--disable-blink-features=AutomationControlled)
    echo "browser automation exposure reduction: enabled"
  fi

  if normalize_bool "${BROWSER_VERBOSE_LOGS_RAW}"; then
    browser_args+=(
      --enable-logging=stderr
      --v=1
    )
    echo "browser verbose logs: enabled"
  fi

  if [ "${BROWSER_EXTENSION_ENABLED}" = "1" ]; then
    browser_args+=(
      --disable-features=DisableLoadExtensionCommandLineSwitch,DisableDisableExtensionsExceptCommandLineSwitch
      --disable-extensions-except="${EXTENSION_DIR}"
      --load-extension="${EXTENSION_DIR}"
    )
  else
    echo "browser extension disabled for GUI PoC run."
  fi

  if [ "${POC_BROWSER_STARTUP_DELAY}" -gt 0 ] 2>/dev/null; then
    echo "delaying browser startup by ${POC_BROWSER_STARTUP_DELAY}s ..."
    sleep "${POC_BROWSER_STARTUP_DELAY}"
  fi

  browser_args+=("${STARTUP_URL}")
  "${cmd}" "${browser_args[@]}" &

  BROWSER_PID=$!
  CURRENT_BROWSER="${browser}"
  echo "browser '${CURRENT_BROWSER}' started (pid=${BROWSER_PID})"
}

browser_rotation_loop() {
  local idx=0
  local total="${#BROWSER_SEQUENCE[@]}"
  local browser=""
  local started_at=0
  local now=0

  while true; do
    browser="${BROWSER_SEQUENCE[${idx}]}"
    if ! start_browser "${browser}"; then
      sleep 2
      idx=$(( (idx + 1) % total ))
      continue
    fi

    started_at="$(date +%s)"
    while true; do
      sleep 1

      if [ -z "${BROWSER_PID}" ]; then
        break
      fi
      if ! kill -0 "${BROWSER_PID}" >/dev/null 2>&1; then
        echo "browser '${CURRENT_BROWSER}' exited unexpectedly; rotating now."
        BROWSER_PID=""
        CURRENT_BROWSER=""
        break
      fi

      now="$(date +%s)"
      if [ $((now - started_at)) -ge "${BROWSER_ROTATION_INTERVAL_SECONDS}" ]; then
        stop_active_browser
        break
      fi
    done

    idx=$(( (idx + 1) % total ))
  done
}

single_browser_watch_loop() {
  local browser="${1:-chrome}"
  while true; do
    if [ -n "${BACKEND_PID}" ] && ! kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
      break
    fi
    if [ -n "${GUI_POC_PID}" ] && ! kill -0 "${GUI_POC_PID}" >/dev/null 2>&1; then
      break
    fi

    if [ -z "${BROWSER_PID}" ] || ! kill -0 "${BROWSER_PID}" >/dev/null 2>&1; then
      echo "browser '${browser}' exited (possible error code 5); restarting ..."
      BROWSER_PID=""
      CURRENT_BROWSER=""
      sleep 2
      start_browser "${browser}" || true
    fi

    sleep 1
  done
}

cleanup() {
  set +e
  if [ -n "${GUI_POC_PID}" ] && kill -0 "${GUI_POC_PID}" >/dev/null 2>&1; then
    kill "${GUI_POC_PID}" >/dev/null 2>&1 || true
    wait "${GUI_POC_PID}" 2>/dev/null || true
  fi
  if [ -n "${X11VNC_PID}" ] && kill -0 "${X11VNC_PID}" >/dev/null 2>&1; then
    kill "${X11VNC_PID}" >/dev/null 2>&1 || true
    wait "${X11VNC_PID}" 2>/dev/null || true
  fi
  if [ -n "${LXDE_PID}" ] && kill -0 "${LXDE_PID}" >/dev/null 2>&1; then
    kill "${LXDE_PID}" >/dev/null 2>&1 || true
    wait "${LXDE_PID}" 2>/dev/null || true
  fi
  if [ -n "${XVFB_PID}" ] && kill -0 "${XVFB_PID}" >/dev/null 2>&1; then
    kill "${XVFB_PID}" >/dev/null 2>&1 || true
    wait "${XVFB_PID}" 2>/dev/null || true
  fi
  if [ -n "${BROWSER_MANAGER_PID}" ] && kill -0 "${BROWSER_MANAGER_PID}" >/dev/null 2>&1; then
    kill "${BROWSER_MANAGER_PID}" >/dev/null 2>&1 || true
    wait "${BROWSER_MANAGER_PID}" 2>/dev/null || true
  fi
  if [ -n "${BROWSER_WATCHER_PID}" ] && kill -0 "${BROWSER_WATCHER_PID}" >/dev/null 2>&1; then
    kill "${BROWSER_WATCHER_PID}" >/dev/null 2>&1 || true
    wait "${BROWSER_WATCHER_PID}" 2>/dev/null || true
  fi
  stop_active_browser
  if [ -n "${BACKEND_PID}" ] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
    wait "${BACKEND_PID}" 2>/dev/null || true
  fi
  vncserver -kill :1 >/dev/null 2>&1 || true
}

trap cleanup EXIT INT TERM

echo "entrypoint starting..."
echo "flags: POC_MODE=${POC_MODE:-0} POC_GUI_CLICK_DEMO=${POC_GUI_CLICK_DEMO:-0} POC_BROWSER_ROTATION_ENABLED=${POC_BROWSER_ROTATION_ENABLED:-1}"

if [ -f /home/rpa/.env ]; then
  echo "loading env vars from /home/rpa/.env ..."
  set -a
  # shellcheck disable=SC1091
  source /home/rpa/.env
  set +a
fi

echo "starting VNC server ..."
export USER=rpa
if [ -f /tmp/.X1-lock ]; then
  rm -f /tmp/.X1-lock
fi
if [ -S /tmp/.X11-unix/X1 ]; then
  rm -f /tmp/.X11-unix/X1
fi
websockify -D --web=/usr/share/novnc/ 6901 localhost:5901
vncserver :1 -geometry 1280x800 -depth 24

# serve captcha test page on port 9999
cd /home/rpa && python3 -m http.server 9999 &
sleep 1

# REGION=CA|WY|TX ... -> regiao (estado) a coletar. Atalho p/ POC_COLLECT_STATES.
if [ -n "${REGION:-}" ]; then
  export POC_COLLECT_STATES="${REGION}"
  echo "REGION=${REGION} -> POC_COLLECT_STATES=${POC_COLLECT_STATES}"
fi

if [ "${POC_MODE:-0}" = "1" ]; then
  echo "starting local poc backend ..."
  PYTHONPATH=/home/rpa python3 -u -m zillow_scraper &
  BACKEND_PID=$!
  echo "backend pid=${BACKEND_PID}"

  echo "starting captcha solver in visible terminal window ..."
  CAPTCHA_DRYRUN="${POC_CAPTCHA_DRYRUN:-0}"
  mkdir -p /home/rpa/out
  CAPTCHA_CMD="source /home/rpa/.venv/bin/activate; cd /home/rpa; POC_CAPTCHA_DRYRUN=${CAPTCHA_DRYRUN} python3 -u -m zillow_scraper.solvers.captcha 2>&1 | tee /home/rpa/out/captcha_solver.log; echo '[captcha] processo encerrou'; exec bash"
  if command -v lxterminal >/dev/null 2>&1; then
    DISPLAY=:1 lxterminal --title="Captcha Solver" -e bash -c "${CAPTCHA_CMD}" &
  elif command -v xterm >/dev/null 2>&1; then
    DISPLAY=:1 xterm -title "Captcha Solver" -hold -e bash -c "${CAPTCHA_CMD}" &
  else
    echo "warn: nenhum terminal grafico (lxterminal/xterm); rodando captcha solver em background."
    source /home/rpa/.venv/bin/activate
    PYTHONPATH=/home/rpa POC_CAPTCHA_DRYRUN=${CAPTCHA_DRYRUN} python3 -u -m zillow_scraper.solvers.captcha >/home/rpa/out/captcha_solver.log 2>&1 &
  fi
  CAPTCHA_SOLVER_PID=$!
  echo "captcha solver pid=${CAPTCHA_SOLVER_PID} (DRYRUN=${CAPTCHA_DRYRUN}, log=/home/rpa/out/captcha_solver.log)"

  echo "starting hash clicker (clique real p/ capturar hash) ..."
  source /home/rpa/.venv/bin/activate
  PYTHONPATH=/home/rpa DISPLAY=:1 python3 -u -m zillow_scraper.solvers.clicker >/home/rpa/out/hash_clicker.log 2>&1 &
  HASH_CLICKER_PID=$!
  echo "hash clicker pid=${HASH_CLICKER_PID} (log=/home/rpa/out/hash_clicker.log)"
fi

if ! [[ "${BROWSER_ROTATION_INTERVAL_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "warning: invalid POC_BROWSER_ROTATION_INTERVAL_SECONDS='${BROWSER_ROTATION_INTERVAL_SECONDS}', using 300."
  BROWSER_ROTATION_INTERVAL_SECONDS=300
fi
if [ "${BROWSER_ROTATION_INTERVAL_SECONDS}" -lt 10 ]; then
  echo "warning: POC_BROWSER_ROTATION_INTERVAL_SECONDS too low, clamping to 10."
  BROWSER_ROTATION_INTERVAL_SECONDS=10
fi

build_browser_sequence
echo "browser sequence: ${BROWSER_SEQUENCE[*]}"

if normalize_bool "${BROWSER_ROTATION_ENABLED_RAW}" && [ "${#BROWSER_SEQUENCE[@]}" -gt 1 ]; then
  echo "browser rotation enabled: interval=${BROWSER_ROTATION_INTERVAL_SECONDS}s"
  browser_rotation_loop &
  BROWSER_MANAGER_PID=$!
else
  echo "browser rotation disabled; starting single browser '${BROWSER_SEQUENCE[0]}'"
  start_browser "${BROWSER_SEQUENCE[0]}"
  single_browser_watch_loop "${BROWSER_SEQUENCE[0]}" &
  BROWSER_WATCHER_PID=$!
fi

if [ -n "${BACKEND_PID}" ]; then
  echo "waiting for backend to finish ..."
  set +e
  wait "${BACKEND_PID}"
  BACKEND_EXIT=$?
  set -e
  echo "backend finished with exit code ${BACKEND_EXIT}"
  exit "${BACKEND_EXIT}"
fi

if [ -n "${BROWSER_MANAGER_PID}" ]; then
  wait "${BROWSER_MANAGER_PID}" || true
else
  wait "${BROWSER_PID}" || true
fi
