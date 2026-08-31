"""
=============================================================
Cache cho kết quả sentiment analysis (Ollama)
=============================================================
Cùng pattern với gmaps_cache.py, áp dụng cho sentiment_analysis.py:
  - Ghi cache ngay sau mỗi POI được xử lý — nếu script crash giữa
    chừng (Ollama treo, máy tắt...), phần đã xử lý không bị mất.
  - Rerun script sau đó sẽ SKIP các POI đã có trong cache, không
    gọi lại Ollama — quan trọng vì trước đây script này không ghi
    gì ra đĩa cho tới tận cuối vòng lặp (giữ hết trong RAM).
  - Cho phép retry CHỈ những POI bị lỗi qua --retry-errors.

Key = hash(place_name + caption) — nếu caption của 1 POI thay đổi
(crawl lại dữ liệu mới), cache tự coi là POI khác, gọi lại Ollama.
=============================================================
"""

import sqlite3
import json
import hashlib
from datetime import datetime
from contextlib import contextmanager

CACHE_DB = "sentiment_cache.db"


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
            CREATE TABLE IF NOT EXISTS sentiment_cache (
                item_key    TEXT PRIMARY KEY,
                place_name  TEXT,
                score       REAL,
                label       TEXT,
                reason      TEXT,
                raw         TEXT,
                status      TEXT,   -- success | error
                fetched_at  TEXT
            )
        """)
        conn.commit()


def make_key(place_name, caption):
    normalized = f"{place_name.strip().lower()}||{(caption or '').strip()}"
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def get_cached(item_key):
    with _conn() as conn:
        row = conn.execute(
            "SELECT score, label, reason, raw, status FROM sentiment_cache WHERE item_key=?",
            (item_key,)
        ).fetchone()
    if row is None:
        return None, None
    score, label, reason, raw, status = row
    result = {"score": score, "label": label, "reason": reason, "raw": raw or ""}
    return result, status


def save_cache(item_key, place_name, result, status):
    with _conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sentiment_cache
                (item_key, place_name, score, label, reason, raw, status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (item_key, place_name, result["score"], result["label"], result["reason"],
              result.get("raw", ""), status, datetime.now().isoformat()))
        conn.commit()  # commit ngay từng POI, không đợi hết batch


def clear_errors():
    with _conn() as conn:
        n = conn.execute("DELETE FROM sentiment_cache WHERE status='error'").rowcount
        conn.commit()
    return n


def cache_stats():
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sentiment_cache").fetchone()[0]
        errors = conn.execute("SELECT COUNT(*) FROM sentiment_cache WHERE status='error'").fetchone()[0]
    return {"total": total, "errors": errors}
