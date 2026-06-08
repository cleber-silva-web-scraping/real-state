#!/usr/bin/env python3
"""Servidor HTTP local (API p/ a extensao) + ponto de entrada."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from zillow_scraper.config import *  # noqa: F401,F403
from zillow_scraper.util import parse_int
from zillow_scraper.notifications import send_telegram_message
from zillow_scraper.crawl.state import CrawlState

STATE = CrawlState()


class Handler(BaseHTTPRequestHandler):
    server_version = "poc-browser-backend/0.3"

    def _json_response(self, status_code, payload):
        response = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def _empty_response(self, status_code):
        self.send_response(status_code)
        self.end_headers()

    def _read_json(self):
        content_length = parse_int(self.headers.get("Content-Length", "0"), 0)
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        if raw == "":
            return {}
        return json.loads(raw)

    def _is_authorized(self):
        if API_KEY == "":
            return True
        return self.headers.get("x-api-key", "") == API_KEY

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._json_response(200, {"ok": True, "service": "poc-browser-backend"})
            return

        if path == "/status":
            self._json_response(200, STATE.snapshot())
            return

        if path == "/result":
            self._json_response(200, STATE.result())
            return

        if path == "/v1/click":
            qs = parse_qs(urlparse(self.path).query)
            try:
                STATE.queue_click(qs.get("x", ["0"])[0], qs.get("y", ["0"])[0])
            except Exception:
                pass
            self._empty_response(204)
            return

        if path == "/v1/clicks":
            self._json_response(200, {"clicks": STATE.pop_clicks()})
            return

        if path.startswith("/v1/next/"):
            if not self._is_authorized():
                self._json_response(401, {"ok": False, "error": "unauthorized"})
                return

            browser_id = path.rsplit("/", 1)[-1]
            payload = STATE.next_payload(browser_id)
            if payload is None:
                self._empty_response(204)
                return
            self._json_response(200, payload)
            return

        self._json_response(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/v1/process":
            if not self._is_authorized():
                self._json_response(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                payload = self._read_json()
            except json.JSONDecodeError:
                self._json_response(400, {"ok": False, "error": "invalid json"})
                return

            result = STATE.process_result(payload)
            self._json_response(200, result)
            return

        self._json_response(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = fmt % args
        print(f"[{timestamp}] {self.client_address[0]} {self.command} {self.path} :: {message}")


def main():
    snap = STATE.snapshot()
    print("Starting Zillow backend...")
    print(f"Listening: http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"Mode: {snap.get('mode')}  states: {snap.get('states')}")
    print(f"Checkpoint: {CHECKPOINT_FILE}  Debug: {DEBUG_DIR}")
    print(f"Retries: max={MAX_RETRIES} delay={RETRY_DELAY_SECONDS}s timeout={PROCESSING_TIMEOUT_SECONDS}s")
    print(f"Block selectors: {BLOCK_SELECTORS}")

    sent, err = send_telegram_message("Start Zillow extraction")
    if not sent and err:
        print(f"warning: startup telegram failed: {err}")

    server = ThreadingHTTPServer((BACKEND_HOST, BACKEND_PORT), Handler)

    if EXIT_AFTER_FINISH:
        def shutdown_watcher():
            while True:
                time.sleep(1.0)
                if STATE.snapshot().get("status") == "finished":
                    print("Pipeline finished. Stopping backend...")
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
        threading.Thread(target=shutdown_watcher, daemon=True).start()

    try:
        server.serve_forever()
    finally:
        server.server_close()
        print("Backend server stopped.")
