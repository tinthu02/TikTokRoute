"""
=============================================================
Cache cho Google Maps API (search + place details)
=============================================================
Mục đích: tách cache ra khỏi file CSV output, để:
  - Không gọi lại API cho POI đã xử lý thành công khi rerun script.
  - Cho phép retry CHỈ những POI bị lỗi (--retry-errors), không đụng
    vào phần đã cache thành công.
  - Ghi cache ngay sau mỗi request (không đợi hết batch) -> nếu
    script crash giữa chừng, phần đã gọi vẫn được giữ lại.

Có 2 bảng, vì 2 loại API call độc lập nhau:
  - search_cache : key = tên POI đã chuẩn hóa -> danh sách candidates
  - detail_cache : key = place_id -> chi tiết địa điểm

Dùng chung 1 file DB: gmaps_cache.db
=============================================================
"""

import sqlite3
import json
import hashlib
from datetime import datetime
from contextlib import contextmanager

CACHE_DB = "data/cache/gmaps_cache.db"


@contextmanager
def _conn():
    conn = sqlite3.connect(CACHE_DB)
    try:
        yield conn
    finally:
        conn.close()


def init_cache():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                query_key     TEXT PRIMARY KEY,
                poi_name      TEXT,
                response_json TEXT,
                status        TEXT,   -- success | zero_results | error
                fetched_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS detail_cache (
                place_id      TEXT PRIMARY KEY,
                response_json TEXT,
                status        TEXT,   -- success | error
                fetched_at    TEXT
            )
        """)
        conn.commit()


def make_key(text):
    """Chuẩn hóa nhẹ trước khi hash để giảm cache-miss do khác hoa/thường, khoảng trắng thừa."""
    normalized = " ".join(text.strip().lower().split())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


# ── search_cache ────────────────────────────────────────────

def get_search_cached(query_key):
    with _conn() as conn:
        row = conn.execute(
            "SELECT response_json, status FROM search_cache WHERE query_key=?",
            (query_key,)
        ).fetchone()
    if row is None:
        return None, None
    response_json, status = row
    candidates = json.loads(response_json) if response_json else []
    return candidates, status


def save_search_cache(query_key, poi_name, candidates, status):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO search_cache
                (query_key, poi_name, response_json, status, fetched_at)
            VALUES (?, ?, ?, ?, ?)
        """, (query_key, poi_name, json.dumps(candidates), status, datetime.now().isoformat()))
        conn.commit()  # commit ngay, không đợi hết batch


# ── detail_cache ────────────────────────────────────────────

def get_detail_cached(place_id):
    with _conn() as conn:
        row = conn.execute(
            "SELECT response_json, status FROM detail_cache WHERE place_id=?",
            (place_id,)
        ).fetchone()
    if row is None:
        return None, None
    response_json, status = row
    detail = json.loads(response_json) if response_json else {}
    return detail, status


def save_detail_cache(place_id, detail, status):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO detail_cache
                (place_id, response_json, status, fetched_at)
            VALUES (?, ?, ?, ?)
        """, (place_id, json.dumps(detail) if detail else None, status, datetime.now().isoformat()))
        conn.commit()


# ── quản lý / thống kê ──────────────────────────────────────

def clear_errors():
    """Xóa các entry lỗi để lần chạy tiếp theo gọi lại API cho đúng các POI đó."""
    with _conn() as conn:
        c1 = conn.execute("DELETE FROM search_cache WHERE status='error'").rowcount
        c2 = conn.execute("DELETE FROM detail_cache WHERE status='error'").rowcount
        conn.commit()
    return c1, c2


def cache_stats():
    with _conn() as conn:
        s_total = conn.execute("SELECT COUNT(*) FROM search_cache").fetchone()[0]
        s_err = conn.execute("SELECT COUNT(*) FROM search_cache WHERE status='error'").fetchone()[0]
        d_total = conn.execute("SELECT COUNT(*) FROM detail_cache").fetchone()[0]
        d_err = conn.execute("SELECT COUNT(*) FROM detail_cache WHERE status='error'").fetchone()[0]
    return {
        "search_total": s_total, "search_error": s_err,
        "detail_total": d_total, "detail_error": d_err,
    }
