#!/usr/bin/env python3
"""Estagio 3: detalhe via GraphQL (zg-graph/graphql)."""
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


class DetailMixin:
    """Estagio 3: detalhe via GraphQL (zg-graph/graphql)."""
    def _enter_detail_stage_locked(self):
        self._api_stage = "detail"
        self._api_stack = []
        limit = COLLECT_MAX_URLS if COLLECT_MAX_URLS > 0 else 0
        # so o estado atual da fila (sequencial; detalha 1 estado por vez)
        state = self._current_state()
        self._detail_queue = storage_db.iter_zpids_needing_detail(
            limit, [state] if state else COLLECT_STATES
        )
        # hash so em memoria: captura 1x por run (se ja capturou, nao repete).
        self._detail_capture_done = self._house_captured
        self._capture_attempts = 0
    def _detail_hashes(self):
        return {
            "house_op": self._house_op,
            "house_hash": self._house_hash,
            "apt_op": self._apt_op,
            "apt_hash": self._apt_hash,
        }

    def _process_api_detail_locked(self, selector_data, payload):
        self._accumulate_metrics_locked(payload)
        task = self._active_task
        zpid = task.get("zpid")
        state_hash = task.get("state_hash")
        # hash expirou -> re-enfileira este zpid e dispara recaptura
        if (selector_data or {}).get("expired"):
            self._detail_queue.append((zpid, task.get("detail_url"), state_hash))
            self._hash_expired_count += 1
            self._house_captured = False
            self._detail_capture_done = False
            self._capture_attempts = 0
            self._last_error = f"hash expirado p/ zpid={zpid}; recapturando"
            self._active_task = None
            self._prepare_next_api_query_task_locked()
            self._persist_locked()
            return {"ok": True, "status": self._status, "expired": True}
        detail = zillow_search.parse_detail(selector_data)
        if detail:
            existed = storage_db.has_detail(zpid)  # classifica novo vs atualizado
            storage_db.upsert_detail(zpid, detail, state_hash)
            self._details_saved += 1
            if existed:
                self._details_updated += 1
            else:
                self._details_new += 1
        else:
            self._last_error = f"detail vazio p/ zpid={zpid}"
        self._api_queries_done += 1
        self._active_task = None
        self._prepare_next_api_query_task_locked()
        self._persist_locked()
        return {
            "ok": True,
            "status": self._status,
            "details_saved": self._details_saved,
            "detail_queue_remaining": len(self._detail_queue),
            "zpid": zpid,
        }
