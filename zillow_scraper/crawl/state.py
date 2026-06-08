#!/usr/bin/env python3
"""CrawlState: orquestrador da maquina de estados (api_collect).

Composto por mixins, um por responsabilidade:
- UrlCollectMixin  (estagio 1: coleta de URLs)
- CaptureMixin     (estagio 2: captura do hash via clique real)
- DetailMixin      (estagio 3: detalhe via GraphQL)
- PersistenceMixin (checkpoint/finalizacao/snapshot)
- MetricsMixin     (metricas)
"""
import csv
import datetime
import json
import os
import threading
import time
import uuid
from pathlib import Path

from zillow_scraper import search as zillow_search
from zillow_scraper import storage as storage_db
from zillow_scraper.config import *  # noqa: F401,F403
from zillow_scraper.util import now_iso, parse_int
from zillow_scraper.notifications import send_telegram_message
from zillow_scraper.crawl.urls import UrlCollectMixin
from zillow_scraper.crawl.capture import CaptureMixin
from zillow_scraper.crawl.detail import DetailMixin
from zillow_scraper.crawl.persistence import PersistenceMixin
from zillow_scraper.crawl.metrics import MetricsMixin


class CrawlState(UrlCollectMixin, CaptureMixin, DetailMixin, PersistenceMixin, MetricsMixin):
    def __init__(self):
        self._lock = threading.Lock()
        self._task_id = parse_int(os.getenv("POC_TASK_ID", "1"), 1)
        self._start_str = datetime.datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self._process_id = f"poc-{uuid.uuid4().hex[:8]}"
        self._queue_name = "poc-queue"

        self._mode = "api_collect"
        self._status = "pending"
        self._next_try_at = 0.0
        self._processing_started_at = 0.0
        self._browser_id = ""
        self._last_received = None
        self._last_error = ""
        self._active_task = None
        self._finalization_done = False

        # --- api_collect (Route A) ---
        self._api_stage = "urls"        # "urls" -> "detail"
        self._api_stack = []            # pilha DFS de nos de query (estagio urls)
        self._detail_queue = []         # fila de (zpid, url, state_hash) p/ detalhe
        # hashes/operationNames por tipo -> SO EM MEMORIA (dado VOLATIL: expira rapido).
        self._house_op = zillow_search.HOUSE_OP or None
        self._house_hash = zillow_search.HOUSE_HASH
        self._apt_op = zillow_search.APT_OP or "BuildingQuery"
        self._apt_hash = zillow_search.APT_HASH
        self._house_captured = False
        self._detail_capture_done = False
        self._capture_attempts = 0
        self._pending_clicks = []
        self._last_capture_info = {}
        self._api_collected_urls = set()
        self._api_urls_saved = 0
        self._api_queries_done = 0
        self._details_saved = 0
        self._details_skipped = 0
        self._details_new = 0          # imoveis sem detalhe anterior
        self._details_updated = 0      # detalhe re-buscado (state_hash mudou)
        self._hash_expired_count = 0   # vezes que o hash do GraphQL expirou
        self._captcha_count = 0        # bloqueios PX (renderizam captcha)
        self._start_epoch = time.time()
        self._run_started_at = time.strftime("%Y-%m-%d %H:%M:%S")  # formato do storage
        self._urls_csv_file = COLLECT_URLS_CSV
        # metricas
        self._m_queries = 0
        self._m_sum_fetch_ms = 0.0
        self._m_sum_cycle_ms = 0.0
        self._m_json_bytes = 0
        self._m_page_bytes = 0
        self._m_page_loads = 0
        self._last_cycle_ms = 0.0

        self._init_api_collect_mode()
        self._persist_locked()

    def _init_api_collect_mode(self):
        storage_db.init_db()
        self._urls_csv_file = COLLECT_URLS_CSV
        self._api_collected_urls = self._load_collected_urls_from_csv()
        self._load_api_checkpoint()
        if self._api_stage == "detail":
            # resume em detail: repopula a fila com os zpids que ainda precisam de
            # detalhe (retoma os que faltaram, pulando os ja salvos por hash).
            self._enter_detail_stage_locked()
        elif not self._api_stack:
            self._seed_api_stack_locked()
        self._prepare_next_api_query_task_locked()

    def _prepare_next_api_query_task_locked(self):
        if self._active_task is not None:
            return

        if self._api_stage == "urls":
            if self._max_urls_reached() or not self._api_stack:
                self._enter_detail_stage_locked()
            else:
                node = self._api_stack.pop()
                self._active_task = {
                    "kind": "api_query",
                    "node": node,
                    "page": parse_int(node.get("page"), 1),
                    "url": zillow_search.SEARCH_PAGE_URL,
                    "step": "collect-urls-api",
                    "profile": "zillow-api",
                    "retry_count": 0,
                }
                self._status = "pending"
                return

        # estagio detail
        if not self._detail_queue:
            self._status = "finished"
            self._active_task = None
            self._finalize_api_locked()
            return

        # 1o: auto-capturar o hash da CASA em /{estado}/rent-houses/ + CLIQUE REAL numa
        # casa (o GET /graphql full-property so dispara via clique SPA, nao no load SSR).
        if not self._detail_capture_done and self._capture_attempts < 4:
            self._capture_attempts += 1
            state = COLLECT_STATES[0] if COLLECT_STATES else "ca"
            self._active_task = {
                "kind": "api_capture",
                "url": zillow_search.rent_houses_url(state),
                "page": 1,
                "step": "capture-detail-query",
                "profile": "zillow-api",
                "retry_count": 0,
            }
            self._status = "pending"
            return

        zpid, url, state_hash = self._detail_queue.pop()
        self._active_task = {
            "kind": "api_detail",
            "zpid": str(zpid),
            "detail_url": url,
            "state_hash": state_hash,
            "page": 1,
            "url": zillow_search.SEARCH_PAGE_URL,
            "step": "collect-detail-api",
            "profile": "zillow-api",
            "retry_count": 0,
        }
        self._status = "pending"

    def _build_task_payload(self, task):
        base = {
            "id": self._task_id,
            "step": task["step"],
            "queue_name": self._queue_name,
            "start_str": self._start_str,
            "profile": task["profile"],
            "process_id": self._process_id,
            "isError": [{"type": "exist", "selector": text} for text in BLOCK_SELECTORS],
            "page": task["page"],
            "attempt": task["retry_count"] + 1,
        }
        kind = task.get("kind")
        if kind == "api_capture":
            return {**base, "url": task["url"], "actions": [{"type": "capture_query"}]}
        if kind == "api_detail":
            req = zillow_search.build_detail_request(
                task.get("detail_url"), task["zpid"], self._detail_hashes()
            )
            return {**base, "url": zillow_search.SEARCH_PAGE_URL, "actions": [{
                "type": "api_fetch", "method": req["method"], "endpoint": req["endpoint"],
                "body": req["body"], "client_id": req.get("client_id"),
            }]}
        if kind == "api_query":
            body = self._api_build_query_body(task["node"], task["page"])
            return {**base, "url": task["url"], "actions": [{
                "type": "api_fetch", "method": "PUT",
                "endpoint": zillow_search.SEARCH_ENDPOINT, "body": body,
            }]}
        return {**base, "url": task["url"], "actions": task.get("actions", [])}

    def _prepare_next_task_locked(self):
        if self._status in ("finished", "failed"):
            return
        self._prepare_next_api_query_task_locked()

    def _persist_locked(self):
        self._persist_api_locked()

    def _extract_selector_data(self, payload):
        content = (payload or {}).get("content")
        if not isinstance(content, dict):
            return {}
        return content

    def _write_debug_locked(self, reason, payload):
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            page = (self._active_task or {}).get("page", "unknown")
            kind = (self._active_task or {}).get("kind", "unknown")
            file_path = DEBUG_DIR / f"{ts}-page-{page}-{kind}.json"
            with file_path.open("w", encoding="utf-8") as file:
                json.dump({
                    "saved_at": now_iso(), "reason": reason, "mode": self._mode,
                    "status": self._status, "active_task": self._active_task,
                    "payload": payload,
                }, file, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._last_error = f"debug save failed: {exc}"

    def next_payload(self, browser_id):
        with self._lock:
            self._browser_id = browser_id
            self._prepare_next_task_locked()
            if self._active_task is None:
                return None
            now = time.time()
            if (
                self._status == "processing"
                and self._processing_started_at > 0
                and (now - self._processing_started_at) >= PROCESSING_TIMEOUT_SECONDS
            ):
                self._handle_error_locked("processing timeout", {
                    "profile": "is-error", "reason": "processing timeout",
                    "task": self._active_task,
                })
                return None
            if self._status in ("pending", "retry_waiting") and now >= self._next_try_at:
                self._status = "processing"
                self._processing_started_at = now
                self._persist_locked()
                return self._build_task_payload(self._active_task)
            return None

    def _handle_error_locked(self, reason, payload):
        is_transient_block = reason in ("blocked or browser error", "processing timeout")
        if self._active_task is None:
            self._last_error = reason
            self._status = "retry_waiting"
            self._next_try_at = time.time() + RETRY_DELAY_SECONDS
            self._persist_locked()
            return {"ok": True, "status": self._status, "message": reason}
        self._active_task["retry_count"] += 1
        self._last_error = reason
        self._processing_started_at = 0.0
        self._write_debug_locked(reason, payload)
        if self._active_task["retry_count"] > MAX_RETRIES and not is_transient_block:
            self._status = "failed"
            self._persist_locked()
            return {"ok": True, "status": self._status, "message": "max retries reached",
                    "reason": reason, "page": self._active_task.get("page"),
                    "step": self._active_task.get("step")}
        if self._active_task["retry_count"] > MAX_RETRIES and is_transient_block:
            # bloqueio PX e transitorio (renderiza captcha, o solver resolve) -> nao falha
            self._active_task["retry_count"] = 0
        self._status = "retry_waiting"
        self._next_try_at = time.time() + RETRY_DELAY_SECONDS
        self._persist_locked()
        return {"ok": True, "status": self._status, "reason": reason,
                "retry_count": self._active_task["retry_count"],
                "retry_after_seconds": RETRY_DELAY_SECONDS,
                "page": self._active_task.get("page")}

    def process_result(self, payload):
        with self._lock:
            self._last_received = payload
            started = self._processing_started_at
            self._last_cycle_ms = (time.time() - started) * 1000 if started > 0 else 0.0
            self._processing_started_at = 0.0
            profile = (payload or {}).get("profile", "")
            if profile == "is-error":
                content = (payload or {}).get("content") or {}
                if content.get("blocked"):  # bloqueio PX -> renderiza captcha
                    self._captcha_count += 1
                return self._handle_error_locked("blocked or browser error", payload)
            if self._active_task is None:
                return {"ok": True, "status": self._status, "message": "no active task"}
            kind = self._active_task.get("kind")
            selector_data = self._extract_selector_data(payload)
            if not selector_data:
                # resposta vazia no api = bloqueio leve -> retry transitorio (nunca fatal)
                return self._handle_error_locked("blocked or browser error", payload)
            if kind == "api_query":
                return self._process_api_query_locked(selector_data, payload)
            if kind == "api_capture":
                return self._process_api_capture_locked(selector_data, payload)
            if kind == "api_detail":
                return self._process_api_detail_locked(selector_data, payload)
            return self._handle_error_locked(f"unknown task kind: {kind}", payload)

    def snapshot(self):
        with self._lock:
            return self._api_snapshot_locked()

    def result(self):
        with self._lock:
            return sorted(self._api_collected_urls)

