"""SQLite storage p/ coleta Zillow (URLs + detalhe), com sistema de 2 hashes.

PK = zpid (id canonico do imovel). state_hash = hash dos campos volateis
disponiveis na paginacao (price/address/fotos/beds/baths/area/status). Se o
state_hash do imovel ja salvo bate com o atual -> imovel inalterado -> nao
re-busca o detalhe (economiza a 2a batida). Espelha a deteccao por hash do
old-code (mongo_db.save_data).
"""

import hashlib
import json
import os
import sqlite3
import threading
import time

DB_FILE = os.getenv("POC_DB_FILE", "/home/rpa/out/zillow.db")

_lock = threading.Lock()
_conn = None


def _connect():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(DB_FILE) or ".", exist_ok=True)
        _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db():
    with _lock:
        conn = _connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS urls (
                zpid TEXT PRIMARY KEY,
                url TEXT,
                address TEXT,
                beds TEXT,
                baths TEXT,
                area TEXT,
                price TEXT,
                state TEXT,
                category TEXT,
                listing_type TEXT,
                state_hash TEXT,
                first_seen TEXT,
                last_seen TEXT
            );
            CREATE TABLE IF NOT EXISTS details (
                zpid TEXT PRIMARY KEY,
                json TEXT,
                state_hash TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS run_metrics (
                run_id TEXT,
                saved_at TEXT,
                json TEXT
            );
            """
        )
        # delete LOGICO: active=1 ativo, 0 removido (sumiu da busca). Mantem a linha
        # + o detalhe; so marca. Migracao idempotente p/ bases antigas.
        for col, ddl in (("active", "INTEGER DEFAULT 1"), ("removed_at", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE urls ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        conn.commit()


def compute_state_hash(fields):
    """Hash SHA-256 estavel de um dict de campos volateis (sort_keys)."""
    serialized = json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def upsert_url(row):
    """row: dict com zpid, url, address, beds, baths, area, price, state,
    category, listing_type, state_hash. Atualiza last_seen; preserva first_seen."""
    zpid = str(row.get("zpid") or "").strip()
    if not zpid:
        return False
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO urls (zpid, url, address, beds, baths, area, price,
                              state, category, listing_type, state_hash,
                              first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(zpid) DO UPDATE SET
                url=excluded.url, address=excluded.address, beds=excluded.beds,
                baths=excluded.baths, area=excluded.area, price=excluded.price,
                state=excluded.state, category=excluded.category,
                listing_type=excluded.listing_type, state_hash=excluded.state_hash,
                last_seen=excluded.last_seen, active=1, removed_at=NULL
            """,
            (
                zpid, row.get("url"), row.get("address"), str(row.get("beds", "")),
                str(row.get("baths", "")), str(row.get("area", "")),
                str(row.get("price", "")), row.get("state"), row.get("category"),
                row.get("listing_type", "rent"), row.get("state_hash"), now, now,
            ),
        )
        conn.commit()
    return True


def needs_detail(zpid, state_hash):
    """True se o imovel ainda nao tem detalhe OU mudou (state_hash diferente)."""
    zpid = str(zpid or "").strip()
    if not zpid:
        return False
    with _lock:
        conn = _connect()
        cur = conn.execute("SELECT state_hash FROM details WHERE zpid=?", (zpid,))
        r = cur.fetchone()
    if r is None:
        return True
    return r["state_hash"] != state_hash


def has_detail(zpid):
    """True se ja existe detalhe salvo p/ o zpid (p/ classificar novo vs atualizado)."""
    zpid = str(zpid or "").strip()
    if not zpid:
        return False
    with _lock:
        conn = _connect()
        r = conn.execute("SELECT 1 FROM details WHERE zpid=?", (zpid,)).fetchone()
    return r is not None


def count_active(state=None):
    """Quantos imoveis ATIVOS (active=1), opcionalmente de um estado."""
    sql = "SELECT COUNT(*) AS n FROM urls WHERE COALESCE(active,1)=1"
    params = []
    if state:
        sql += " AND state=?"
        params.append(state.upper())
    with _lock:
        conn = _connect()
        r = conn.execute(sql, params).fetchone()
    return r["n"] if r else 0


def mark_removed(before_ts, states=None):
    """DELETE LOGICO: marca active=0 + removed_at nos imoveis nao-vistos desde
    before_ts (sumiram da busca). Mantem a linha + o detalhe. Retorna quantos
    marcou. SEMPRE escopar por estado(s): senao um run de SD marcaria todo o WY."""
    sql = "UPDATE urls SET active=0, removed_at=? WHERE last_seen < ? AND active=1"
    params = [_now(), before_ts]
    if states:
        sql += " AND state IN (%s)" % ",".join("?" * len(states))
        params += [s.upper() for s in states]
    with _lock:
        conn = _connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount or 0


def upsert_detail(zpid, detail_obj, state_hash):
    zpid = str(zpid or "").strip()
    if not zpid:
        return False
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO details (zpid, json, state_hash, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(zpid) DO UPDATE SET
                json=excluded.json, state_hash=excluded.state_hash,
                updated_at=excluded.updated_at
            """,
            (zpid, json.dumps(detail_obj, ensure_ascii=False), state_hash, _now()),
        )
        conn.commit()
    return True


def iter_zpids_needing_detail(limit=0, states=None):
    """Lista (zpid, url, state_hash) de imoveis sem detalhe ou com hash mudado.
    states: escopa por estado(s) do run (ex ['SD']) -> nao detalha outros estados."""
    sql = (
        "SELECT u.zpid AS zpid, u.url AS url, u.state_hash AS state_hash "
        "FROM urls u LEFT JOIN details d ON u.zpid=d.zpid "
        "WHERE COALESCE(u.active,1)=1 AND (d.zpid IS NULL OR d.state_hash != u.state_hash)"
    )
    params = []
    if states:
        sql += " AND u.state IN (%s)" % ",".join("?" * len(states))
        params += [s.upper() for s in states]
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    with _lock:
        conn = _connect()
        rows = conn.execute(sql, params).fetchall()
    return [(r["zpid"], r["url"], r["state_hash"]) for r in rows]


def count_urls():
    with _lock:
        conn = _connect()
        return conn.execute("SELECT COUNT(*) AS c FROM urls").fetchone()["c"]


def count_details():
    with _lock:
        conn = _connect()
        return conn.execute("SELECT COUNT(*) AS c FROM details").fetchone()["c"]


def count_needing_detail():
    with _lock:
        conn = _connect()
        return conn.execute(
            "SELECT COUNT(*) AS c FROM urls u LEFT JOIN details d ON u.zpid=d.zpid "
            "WHERE d.zpid IS NULL OR d.state_hash != u.state_hash"
        ).fetchone()["c"]


def save_run_metrics(run_id, metrics):
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO run_metrics (run_id, saved_at, json) VALUES (?,?,?)",
            (run_id, _now(), json.dumps(metrics, ensure_ascii=False)),
        )
        conn.commit()
