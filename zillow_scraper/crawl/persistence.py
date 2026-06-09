#!/usr/bin/env python3
"""Checkpoint, finalizacao e snapshot do modo api."""
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


class PersistenceMixin:
    """Checkpoint, finalizacao e snapshot do modo api."""
    def _load_api_checkpoint(self):
        if not CHECKPOINT_FILE.exists():
            return
        try:
            with CHECKPOINT_FILE.open("r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            return
        if saved.get("mode") != "api_collect":
            return
        stack = saved.get("api_stack")
        if isinstance(stack, list):
            self._api_stack = stack
        self._api_stage = saved.get("api_stage", "urls")
        self._api_urls_saved = parse_int(saved.get("api_urls_saved"), 0)
        self._api_queries_done = parse_int(saved.get("api_queries_done"), 0)
        self._details_saved = parse_int(saved.get("details_saved"), 0)
    def _finalize_state_locked(self):
        # relatorio do ESTADO atual (delta desde o inicio deste estado). Chamado ao
        # esvaziar a fila de detalhe de cada estado da sequencia.
        state = self._current_state() or "?"
        b = self._state_base or {}
        new = self._details_new - b.get("new", 0)
        upd = self._details_updated - b.get("upd", 0)
        exp = self._hash_expired_count - b.get("exp", 0)
        capt = self._captcha_count - b.get("capt", 0)
        mb = ((self._m_json_bytes - b.get("jb", 0)) + (self._m_page_bytes - b.get("pb", 0))) / 1048576
        try:
            removed = storage_db.mark_removed(self._state_started_at, [state])
        except Exception:
            removed = 0
        try:
            total_ativo = storage_db.count_active(state)  # apos o mark_removed
        except Exception:
            total_ativo = 0
        elapsed = int(time.time() - self._state_started_epoch)
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        dur = f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"
        msg = (
            f"Zillow {state} — concluido\n"
            f"inicio: {self._state_started_at}\n"
            f"duracao: {dur}\n"
            f"detalhes novos: {new}\n"
            f"detalhes atualizados: {upd}\n"
            f"detalhes removidos: {removed}\n"
            f"total ativo: {total_ativo}\n"
            f"hash expirou: {exp}x\n"
            f"captcha: {capt}x\n"
            f"baixado: {round(mb, 2)} MB"
        )
        send_telegram_message(msg)
        print("[estado]\n" + msg, flush=True)

    def _finalize_run_locked(self):
        # fim de TODOS os estados: salva metricas da run (relatorio ja foi por estado)
        if self._finalization_done:
            return
        self._finalization_done = True
        try:
            storage_db.save_run_metrics(self._process_id, self._metrics_summary_locked())
        except Exception:
            pass
        print("[run] todos os estados concluidos", flush=True)

    def _persist_api_locked(self):
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            "saved_at": now_iso(),
            "mode": "api_collect",
            "stage": self._api_stage,
            "status": self._status,
            "process_id": self._process_id,
            "listing_type": "rent",
            "states": COLLECT_STATES,
            "max_urls": COLLECT_MAX_URLS,
            "urls_csv_file": str(self._urls_csv_file),
            "db_file": storage_db.DB_FILE,
            "api_urls_saved": self._api_urls_saved,
            "api_queries_done": self._api_queries_done,
            "details_saved": self._details_saved,
            "stack_remaining": len(self._api_stack),
            "detail_queue_remaining": len(self._detail_queue),
            "metrics": self._metrics_summary_locked(),
            "finalization_done": self._finalization_done,
            "active_task": self._active_task,
            "api_stack": self._api_stack,
        }
        with CHECKPOINT_FILE.open("w", encoding="utf-8") as file:
            json.dump(checkpoint, file, ensure_ascii=False, indent=2)

    def _api_snapshot_locked(self):
        active_retry = self._active_task.get("retry_count", 0) if self._active_task else 0
        active_node = self._active_task.get("node") if self._active_task else None
        return {
            "mode": "api_collect",
            "stage": self._api_stage,
            "status": self._status,
            "browser_id": self._browser_id,
            "listing_type": "rent",
            "states": COLLECT_STATES,
            "max_urls": COLLECT_MAX_URLS,
            "page_threshold": COLLECT_PAGE_THRESHOLD,
            "urls_csv_file": str(self._urls_csv_file),
            "db_file": storage_db.DB_FILE,
            "urls_collected": self._api_urls_saved,
            "queries_done": self._api_queries_done,
            "details_saved": self._details_saved,
            "details_new": self._details_new,
            "details_updated": self._details_updated,
            "hash_expired_count": self._hash_expired_count,
            "captcha_count": self._captcha_count,
            "stack_remaining": len(self._api_stack),
            "detail_queue_remaining": len(self._detail_queue),
            "detail_capture_done": self._detail_capture_done,
            "house_op": self._house_op,
            "house_hash": (self._house_hash or "")[:16],
            "apt_hash": (self._apt_hash or "")[:16],
            "last_capture_info": self._last_capture_info,
            "metrics": self._metrics_summary_locked(),
            "active_node": active_node,
            "active_retry_count": active_retry,
            "last_received_profile": (self._last_received or {}).get("profile"),
            "last_error": self._last_error,
        }
