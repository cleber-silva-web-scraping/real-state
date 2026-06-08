#!/usr/bin/env python3
"""Estagio 2: captura do hash do GraphQL (clique real)."""
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


class CaptureMixin:
    """Estagio 2: captura do hash do GraphQL (clique real)."""
    def queue_click(self, x, y):
        with self._lock:
            self._pending_clicks.append([int(x), int(y)])

    def pop_clicks(self):
        with self._lock:
            # serve UM clique por vez e SO durante a captura. Assim, quando a captura
            # encerra o backend para de servir e o clicker para no meio do lote (nao
            # bleeda cliques na fase de detalhe -> nao navega a aba -> nao trava).
            if self._detail_capture_done or not self._pending_clicks:
                self._pending_clicks = []
                return []
            return [self._pending_clicks.pop(0)]
    def _pick_house(self, candidates):
        """Casa = query da PROPERTY completa: hash + zpid + deviceType. Os widgets
        (ListingContactDetails etc) tem zpid mas NAO deviceType -> ignora."""
        for c in candidates:
            if c.get("hash") and c.get("hasZpid") and c.get("hasDeviceType"):
                return c
        return None

    def _pick_apt(self, candidates):
        for c in candidates:
            if c.get("hash") and (
                "building" in str(c.get("op") or "").lower() or c.get("hasBuildingKey")
            ):
                return c
        return None

    def _process_api_capture_locked(self, selector_data, payload):
        self._accumulate_metrics_locked(payload)
        candidates = (selector_data or {}).get("candidates") or []
        house = self._pick_house(candidates)
        apt = self._pick_apt(candidates)
        if house and house.get("hash"):
            self._house_op, self._house_hash = house.get("op"), house["hash"]
            self._house_captured = True
        if apt and apt.get("hash"):
            self._apt_op, self._apt_hash = apt.get("op"), apt["hash"]
        self._last_capture_info = {
            "count": len(candidates),
            "page_url": (selector_data or {}).get("page_url"),
            "house_op": self._house_op if house else None,
            "house_hash": (self._house_hash or "")[:16] if house else None,
            "apt_hash": (self._apt_hash or "")[:16] if apt else None,
            "sample": [c for c in candidates if c.get("hash")][:8] or candidates[:6],
        }
        # captura concluida se pegamos o hash da casa (ou esgotou tentativas)
        if (house and house.get("hash")) or self._capture_attempts >= 3:
            self._detail_capture_done = True
        # SEMPRE drena cliques pendentes apos a captura: senao o hash_clicker segue
        # clicando na fase de detalhe -> navega a aba p/ um homedetails -> trava.
        self._pending_clicks = []
        self._api_queries_done += 1
        self._active_task = None
        self._prepare_next_api_query_task_locked()
        self._persist_locked()
        return {
            "ok": True,
            "status": self._status,
            "house_op": self._house_op,
            "captured": bool(house),
            "candidates_seen": len(candidates),
        }
