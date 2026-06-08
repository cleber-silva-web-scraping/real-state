#!/usr/bin/env python3
"""Clicker real (xtest/Xlib) p/ auto-capturar o hash do persistedQuery.

O Zillow so dispara o /zg-graph do detalhe com clique REAL (anti-bot). O content.js
acha os cards de casa na pagina rent-houses, calcula a posicao na tela e enfileira no
backend (/v1/click?x&y). Este processo pega a fila (/v1/clicks) e da o clique real no
SO -> a modal/detalhe abre -> o for-rent-sub-app dispara o /zg-graph -> o interceptor
MAIN-world captura o hash. Esc fecha a modal entre cliques.
"""

import json
import os
import time
import urllib.request

os.environ["DISPLAY"] = os.environ.get("DISPLAY", ":1")

from Xlib import display, X
from Xlib.ext import xtest

BACKEND = os.environ.get("POC_BACKEND_URL", "http://localhost:8000")
POLL_SEC = 1.0
KEY_ESCAPE = 9  # keycode do Esc no X


def get_clicks():
    try:
        with urllib.request.urlopen(f"{BACKEND}/v1/clicks", timeout=4) as r:
            return json.loads(r.read().decode("utf-8")).get("clicks", [])
    except Exception:
        return []


def real_click(d, x, y):
    d.screen().root.warp_pointer(x, y)
    d.sync()
    time.sleep(0.4)
    xtest.fake_input(d, X.ButtonPress, 1)
    d.sync()
    time.sleep(0.12)
    xtest.fake_input(d, X.ButtonRelease, 1)
    d.sync()


def press_escape(d):
    xtest.fake_input(d, X.KeyPress, KEY_ESCAPE)
    d.sync()
    time.sleep(0.05)
    xtest.fake_input(d, X.KeyRelease, KEY_ESCAPE)
    d.sync()


def main():
    print("[clicker] hash clicker iniciado", flush=True)
    while True:
        clicks = get_clicks()
        if not clicks:
            time.sleep(POLL_SEC)
            continue
        try:
            d = display.Display()
        except Exception as exc:
            print(f"[clicker] erro display: {exc}", flush=True)
            time.sleep(POLL_SEC)
            continue
        for x, y in clicks:
            try:
                print(f"[clicker] clique real ({x},{y})", flush=True)
                real_click(d, x, y)
                time.sleep(3.0)   # modal abre e o GET dispara
                press_escape(d)
                time.sleep(0.6)
            except Exception as exc:
                print(f"[clicker] erro clique: {exc}", flush=True)
        try:
            d.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
