#!/usr/bin/env python3
"""Configuracao: constantes e variaveis de ambiente (sem logica)."""
import datetime
import os
from pathlib import Path


def _flag(name, default):
    return os.getenv(name, default).strip() not in ("0", "false", "False", "")


# --- API local / servidor ---
API_KEY = os.getenv("CBA_API_KEY", "30b7b231-be58-4d7e-859e-753c52d10173")
BACKEND_HOST = os.getenv("POC_BACKEND_HOST", "0.0.0.0")
BACKEND_PORT = int(os.getenv("POC_BACKEND_PORT", "8000"))

# --- retry / timeout ---
MAX_RETRIES = int(os.getenv("POC_MAX_RETRIES", "5"))
RETRY_DELAY_SECONDS = float(os.getenv("POC_RETRY_DELAY_SECONDS", "8"))
PROCESSING_TIMEOUT_SECONDS = float(os.getenv("POC_PROCESSING_TIMEOUT_SECONDS", "70"))
EXIT_AFTER_FINISH = _flag("POC_EXIT_AFTER_FINISH", "1")

# --- arquivos de saida ---
RUN_STAMP = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
CHECKPOINT_FILE = Path(os.getenv("POC_CHECKPOINT_FILE", "/home/rpa/out/checkpoint.json"))
DEBUG_DIR = Path(os.getenv("POC_DEBUG_DIR", "/home/rpa/out/debug"))
OUT_DIR = Path(os.getenv("POC_OUT_DIR", str(CHECKPOINT_FILE.parent)))

# --- notificacao (Telegram) ---
TELEGRAM_BOT_KEY = os.getenv("BOT_KEY", "").strip()
TELEGRAM_CHAT_ID = os.getenv("BOT_CHAT_ID", "").strip()

# --- deteccao de bloqueio PerimeterX ---
BLOCK_SELECTORS = [
    value.strip()
    for value in os.getenv("POC_BLOCK_SELECTORS", "noticed some unusual activity").split("|")
    if value.strip()
]

# --- coleta de URLs (Route A) ---
COLLECT_STATES = [
    s.strip().upper()
    for s in os.getenv("POC_COLLECT_STATES", "WY").split(",")
    if s.strip()
]
try:
    COLLECT_MAX_URLS = max(0, int(os.getenv("POC_COLLECT_MAX_URLS", "0")))
except (TypeError, ValueError):
    COLLECT_MAX_URLS = 0
try:
    COLLECT_PAGE_THRESHOLD = int(os.getenv("POC_COLLECT_PAGE_THRESHOLD", "19"))
except (TypeError, ValueError):
    COLLECT_PAGE_THRESHOLD = 19
# O Zillow capa a paginacao em ~20 pags (~800 imoveis) mesmo com total maior. Se o
# total_items (real) passa disto, NAO da p/ paginar tudo -> subdivide por preco.
# Subdivide quando total_items passa disto (seguro: bem abaixo do teto ~780).
try:
    COLLECT_MAX_ITEMS = int(os.getenv("POC_COLLECT_MAX_ITEMS_FACET", "400"))
except (TypeError, ValueError):
    COLLECT_MAX_ITEMS = 400
# Paginas por ordenacao no fallback irredutivel. 20 = pega as ~780 de CADA ponta
# (preco asc+desc) -> cobre ate ~1560 do bucket. CRITICO p/ 100%.
try:
    COLLECT_SORT_TAKE_PAGES = int(os.getenv("POC_COLLECT_SORT_TAKE_PAGES", "20"))
except (TypeError, ValueError):
    COLLECT_SORT_TAKE_PAGES = 20

_urls_csv_raw = os.getenv("POC_COLLECT_URLS_CSV", "").strip()
if _urls_csv_raw == "":
    COLLECT_URLS_CSV = OUT_DIR / f"zillow_urls_{RUN_STAMP}.csv"
else:
    COLLECT_URLS_CSV = Path(_urls_csv_raw.replace("{timestamp}", RUN_STAMP))
COLLECT_URLS_FIELDS = [
    "url", "address", "beds", "baths", "area", "state", "category", "date",
]
