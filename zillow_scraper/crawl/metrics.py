#!/usr/bin/env python3
"""Acumulo e resumo de metricas do crawl."""
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


class MetricsMixin:
    """Acumulo e resumo de metricas do crawl."""
    def _accumulate_metrics_locked(self, payload):
        self._m_queries += 1
        if self._last_cycle_ms > 0:
            self._m_sum_cycle_ms += self._last_cycle_ms
        m = (payload or {}).get("metrics") or {}
        try:
            self._m_sum_fetch_ms += float(m.get("fetch_ms") or 0)
            self._m_json_bytes += int(m.get("json_bytes") or 0)
        except (TypeError, ValueError):
            pass
        pm = (payload or {}).get("page_metrics") or {}
        try:
            pb = int(pm.get("page_bytes") or 0)
        except (TypeError, ValueError):
            pb = 0
        if pb > 0:
            self._m_page_bytes += pb
            self._m_page_loads += 1
    def _metrics_summary_locked(self):
        q = max(1, self._m_queries)
        return {
            "queries_measured": self._m_queries,
            "avg_fetch_ms": round(self._m_sum_fetch_ms / q, 1),
            "avg_cycle_ms": round(self._m_sum_cycle_ms / q, 1),
            "total_json_bytes": self._m_json_bytes,
            "total_page_bytes": self._m_page_bytes,
            "page_loads": self._m_page_loads,
            "total_bytes": self._m_json_bytes + self._m_page_bytes,
            "total_mb": round((self._m_json_bytes + self._m_page_bytes) / 1048576, 3),
            "json_bytes_per_query": round(self._m_json_bytes / q, 0),
        }
