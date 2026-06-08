#!/usr/bin/env python3
"""Estagio 1: coleta de URLs via busca facetada."""
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


class UrlCollectMixin:
    """Estagio 1: coleta de URLs via busca facetada."""
    def _load_collected_urls_from_csv(self):
        urls = set()
        if not self._urls_csv_file.exists():
            return urls
        try:
            with self._urls_csv_file.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    u = str(row.get("url", "")).strip()
                    if u:
                        urls.add(u)
        except Exception:
            return set()
        return urls
    def _seed_api_stack_locked(self):
        # DFS: um no "probe" por (estado, categoria) com faixa de pagamento full.
        for state in COLLECT_STATES:
            if state not in zillow_search.STATES:
                continue
            for category in zillow_search.RENT_CRITERIAS:
                self._api_stack.append({
                    "state": state,
                    "category": category,
                    "sort": zillow_search.RENT_SORT_PRIMARY,
                    "payment": {"min": 0, "max": None},
                    "sqft": None,
                    "phase": "probe",
                    "page": 1,
                    "total_pages": None,
                })
    def _api_build_query_body(self, node, page):
        return zillow_search.build_request_body(
            node["state"], node["category"], page,
            sort_value=node.get("sort", zillow_search.RENT_SORT_PRIMARY),
            payment_range=node.get("payment"),
            sqft_range=node.get("sqft"),
        )

    def _max_urls_reached(self):
        return COLLECT_MAX_URLS > 0 and len(self._api_collected_urls) >= COLLECT_MAX_URLS
    def _process_api_query_locked(self, selector_data, payload):
        node = self._active_task.get("node", {})
        parsed = zillow_search.parse_results(selector_data)
        properties = parsed["properties"]
        total_pages = parsed["total_pages"]
        total_items = parsed.get("total_items", -1)

        self._accumulate_metrics_locked(payload)
        # Coleta sempre o que veio (dedup global cobre sobreposicao de facets).
        self._collect_api_properties_locked(node, properties)

        if node.get("phase", "probe") == "probe":
            # Decide pelo total_items REAL (o total_pages e capado pelo Zillow em ~19
            # mesmo quando ha milhares -> paginar nao alcanca tudo).
            if total_items is not None and total_items > COLLECT_MAX_ITEMS:
                self._subdivide_api_node_locked(node)  # nao da p/ paginar tudo
            elif total_pages and total_pages > 0:
                for p in range(2, total_pages + 1):
                    child = dict(node)
                    child["phase"] = "paginate"
                    child["page"] = p
                    child["total_pages"] = total_pages
                    self._api_stack.append(child)
            # senao: faixa vazia / sem total -> nada a enfileirar
        # phase "paginate": ja coletou acima.

        self._api_queries_done += 1
        self._active_task = None
        self._prepare_next_api_query_task_locked()
        self._persist_locked()
        return {
            "ok": True,
            "status": self._status,
            "collected_total": self._api_urls_saved,
            "queries_done": self._api_queries_done,
            "stack_remaining": len(self._api_stack),
            "page_total_pages": total_pages,
        }

    def _subdivide_api_node_locked(self, node):
        # 1) parte a faixa de pagamento ao meio
        subs = zillow_search.split_payment_range(node.get("payment") or {"min": 0})
        if subs:
            for rng in subs:
                child = dict(node)
                child["payment"] = rng
                child["phase"] = "probe"
                child["page"] = 1
                child["total_pages"] = None
                self._api_stack.append(child)
            return
        # 2) pagamento no minimo de granularidade: subdivide por sqft (uma vez)
        if node.get("sqft") is None:
            for rng in zillow_search.sqft_ranges():
                child = dict(node)
                child["sqft"] = rng
                child["phase"] = "probe"
                child["page"] = 1
                child["total_pages"] = None
                self._api_stack.append(child)
            return
        # 3) faixas esgotadas (bucket irredutivel: mesmo preco+sqft, > teto). Pagina
        # AS DUAS pontas: preco asc (pgs 2..N; pg1 ja veio no probe) + preco desc
        # (pgs 1..N). Cada ponta alcanca ~780 -> juntas cobrem ate ~1560 do bucket.
        if node.get("sort") == zillow_search.RENT_SORT_PRIMARY:
            n = max(1, COLLECT_SORT_TAKE_PAGES)
            for p in range(2, n + 1):  # resto do sort primario
                child = dict(node)
                child["phase"] = "paginate"
                child["page"] = p
                self._api_stack.append(child)
            for p in range(1, n + 1):  # inversao completa
                child = dict(node)
                child["sort"] = zillow_search.RENT_SORT_SECONDARY
                child["phase"] = "paginate"
                child["page"] = p
                self._api_stack.append(child)
        # se ja era secondary: desiste (page1 ja coletada acima).

    def _collect_api_properties_locked(self, node, properties):
        rows = []
        date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for prop in properties:
            url = str(prop.get("url", "")).strip()
            if url.startswith("/"):
                url = "https://www.zillow.com" + url
            if not url or url in self._api_collected_urls:
                continue
            # descarta artefatos de mapa: casa (/homedetails/) precisa de zpid numerico
            # (as vezes vem uma coordenada no lugar do zpid -> nao e imovel real).
            if not zillow_search.is_apartment_url(url):
                try:
                    int(prop.get("zpid"))
                except (TypeError, ValueError):
                    continue
            if self._max_urls_reached():
                break
            self._api_collected_urls.add(url)
            state_hash = storage_db.compute_state_hash(
                zillow_search.state_hash_fields(prop)
            )
            zpid = prop.get("zpid")
            # persiste no SQLite (fonte da verdade, dedupe por zpid + state_hash)
            storage_db.upsert_url({
                "zpid": zpid,
                "url": url,
                "address": prop.get("address", ""),
                "beds": prop.get("beds", ""),
                "baths": prop.get("baths", ""),
                "area": prop.get("area", ""),
                "price": prop.get("price", ""),
                "state": node.get("state", ""),
                "category": node.get("category", ""),
                "listing_type": "rent",
                "state_hash": state_hash,
            })
            rows.append({
                "url": url,
                "address": prop.get("address", ""),
                "beds": prop.get("beds", ""),
                "baths": prop.get("baths", ""),
                "area": prop.get("area", ""),
                "state": node.get("state", ""),
                "category": node.get("category", ""),
                "date": date_str,
            })
        if rows:
            self._append_url_rows_locked(rows)  # CSV de conveniencia
            self._api_urls_saved += len(rows)
    def _append_url_rows_locked(self, rows):
        self._urls_csv_file.parent.mkdir(parents=True, exist_ok=True)
        file_exists = self._urls_csv_file.exists()
        with self._urls_csv_file.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLLECT_URLS_FIELDS)
            if not file_exists:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)
