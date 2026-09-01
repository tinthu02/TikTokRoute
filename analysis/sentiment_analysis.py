"""
sentiment_analysis.py
─────────────────────
Pipeline:
  04_poi_gmaps_matched.csv
      └─► phân tích sentiment từng POI (video_caption) qua Ollama qwen2.5:7b
          └─► 04_poi_sentiment.csv  (thêm cột: sentiment_score, sentiment_label, sentiment_raw)

Chèn TRƯỚC scoring.py trong pipeline tổng.
"""

import csv
import json
import time
import logging
import argparse
import urllib.request
import urllib.error
from pathlib import Path

import sentiment_cache as cache

# ──────────────────────────────────────────────
# CẤU HÌNH
# ──────────────────────────────────────────────
INPUT_FILE   = "04_poi_gmaps_matched.csv"
OUTPUT_FILE  = "04_poi_sentiment.csv"

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"

MAX_CAPTION_CHARS = 1500   # cắt caption dài để tránh token overflow
RETRY_LIMIT       = 3      # số lần retry khi Ollama lỗi
RETRY_DELAY       = 2.0    # giây giữa các retry

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# PROMPT
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """Bạn là chuyên gia phân tích cảm xúc (sentiment analysis) cho nội dung du lịch tiếng Việt.
Nhiệm vụ: đọc caption TikTok về một địa điểm du lịch và trả về điểm cảm xúc.

Quy tắc:
- Chỉ trả về JSON, không thêm bất kỳ văn bản nào khác.
- score: số thực từ -1.0 (rất tiêu cực) đến +1.0 (rất tích cực), 0.0 là trung tính.
- label: một trong ba giá trị "positive" | "neutral" | "negative".
- reason: 1 câu ngắn giải thích bằng tiếng Việt.

Ví dụ output hợp lệ:
{"score": 0.8, "label": "positive", "reason": "Caption khen ngợi cảnh đẹp và trải nghiệm thú vị."}
"""

def build_user_prompt(place_name: str, caption: str) -> str:
    caption_trimmed = caption.strip()[:MAX_CAPTION_CHARS]
    return (
        f"Địa điểm: {place_name}\n\n"
        f"Caption TikTok (tổng hợp):\n{caption_trimmed}\n\n"
        f"Hãy phân tích sentiment và trả về JSON."
    )


# ──────────────────────────────────────────────
# GỌI OLLAMA
# ──────────────────────────────────────────────
def call_ollama(prompt: str, system: str = SYSTEM_PROMPT) -> str:
    """Gửi request đến Ollama, trả về text thô từ model."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.1,   # thấp để output ổn định
            "top_p": 0.9,
        },
    }).encode("utf-8")

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("response", "")


def parse_sentiment(raw_text: str) -> dict:
    """Parse JSON từ response của model, fallback nếu lỗi."""
    try:
        # Tìm block JSON trong response (phòng model thêm text thừa)
        start = raw_text.find("{")
        end   = raw_text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("Không tìm thấy JSON trong response.")
        parsed = json.loads(raw_text[start:end])

        score = float(parsed.get("score", 0.0))
        score = max(-1.0, min(1.0, score))  # clamp [-1, 1]

        label = parsed.get("label", "neutral").lower()
        if label not in ("positive", "neutral", "negative"):
            label = "neutral"

        reason = parsed.get("reason", "")
        return {"score": score, "label": label, "reason": reason, "raw": raw_text.strip()}

    except Exception as e:
        log.warning(f"  ⚠ Parse JSON lỗi: {e} | raw: {raw_text[:120]!r}")
        return {"score": 0.0, "label": "neutral", "reason": "parse_error", "raw": raw_text.strip()}


def analyze_sentiment(place_name: str, caption: str) -> dict:
    """Phân tích sentiment với retry logic."""
    if not caption or not caption.strip():
        log.info(f"  → '{place_name}': caption trống → neutral 0.0")
        return {"score": 0.0, "label": "neutral", "reason": "caption_empty", "raw": ""}

    prompt = build_user_prompt(place_name, caption)

    for attempt in range(1, RETRY_LIMIT + 1):
        try:
            raw = call_ollama(prompt)
            result = parse_sentiment(raw)
            log.info(
                f"  → '{place_name}': {result['label']} ({result['score']:+.2f}) | {result['reason']}"
            )
            return result
        except urllib.error.URLError as e:
            log.warning(f"  ⚠ Ollama không phản hồi (lần {attempt}/{RETRY_LIMIT}): {e}")
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            log.warning(f"  ⚠ Lỗi không xác định (lần {attempt}/{RETRY_LIMIT}): {e}")
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)

    log.error(f"  ✗ '{place_name}': thất bại sau {RETRY_LIMIT} lần → neutral 0.0")
    return {"score": 0.0, "label": "neutral", "reason": "ollama_error", "raw": ""}


# ──────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="Sentiment analysis cho POI Đà Lạt")
    parser.add_argument("--retry-errors", action="store_true",
                         help="Xóa các entry 'error' trong cache rồi phân tích lại CHỈ cho các POI đó")
    parser.add_argument("--no-cache", action="store_true",
                         help="Bỏ qua cache hoàn toàn, phân tích lại toàn bộ POI")
    return parser.parse_args()


def main():
    args = parse_args()

    input_path  = Path(INPUT_FILE)
    output_path = Path(OUTPUT_FILE)

    if not input_path.exists():
        log.error(f"Không tìm thấy file input: {input_path}")
        raise FileNotFoundError(input_path)

    if args.no_cache:
        import os
        if os.path.exists(cache.CACHE_DB):
            os.remove(cache.CACHE_DB)
        log.info("(--no-cache) Đã xóa cache, sẽ phân tích lại toàn bộ POI")

    cache.init_cache()

    if args.retry_errors:
        n = cache.clear_errors()
        log.info(f"(--retry-errors) Đã xóa {n} entry lỗi trong cache")

    # Đọc CSV
    with open(input_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows   = list(reader)
        fieldnames = reader.fieldnames or []

    log.info(f"📂 Đọc {len(rows)} POI từ {input_path.name}")

    # Kiểm tra Ollama còn sống không
    try:
        urllib.request.urlopen("http://localhost:11434", timeout=5)
        log.info(f"✅ Ollama đang chạy tại localhost:11434 | model: {OLLAMA_MODEL}")
    except Exception:
        log.warning("⚠ Không kết nối được Ollama — kiểm tra lại `ollama serve`")

    # Thêm các cột mới nếu chưa có
    new_cols = ["sentiment_score", "sentiment_label", "sentiment_reason", "sentiment_raw"]
    out_fields = list(fieldnames) + [c for c in new_cols if c not in fieldnames]

    results = []
    total   = len(rows)
    from_cache = 0
    api_calls  = 0

    for i, row in enumerate(rows, 1):
        place_name = row.get("place_name", f"POI_{i}").strip()
        caption    = row.get("video_caption", "") or ""

        item_key = cache.make_key(place_name, caption)
        cached_result, status = cache.get_cached(item_key)

        if status is not None:
            sentiment = cached_result
            from_cache += 1
            log.info(f"[{i}/{total}] {place_name} → (cache) {sentiment['label']} ({sentiment['score']:+.2f})")
        else:
            log.info(f"[{i}/{total}] {place_name}")
            sentiment = analyze_sentiment(place_name, caption)
            api_calls += 1
            # ollama_error sau khi hết retry -> đáng để --retry-errors thử lại sau;
            # các trường hợp khác (caption rỗng, parse lỗi format) coi là kết quả hợp lệ,
            # không cần gọi lại vì Ollama đã phản hồi/đã xử lý xong.
            entry_status = "error" if sentiment["reason"] == "ollama_error" else "success"
            cache.save_cache(item_key, place_name, sentiment, entry_status)

        row["sentiment_score"]  = round(sentiment["score"], 4)
        row["sentiment_label"]  = sentiment["label"]
        row["sentiment_reason"] = sentiment["reason"]
        row["sentiment_raw"]    = sentiment["raw"]

        results.append(row)

        # Nhỏ nghỉ giữa các request THẬT SỰ gọi Ollama (không cần nghỉ khi lấy từ cache)
        if status is None and i < total:
            time.sleep(0.3)

    # Ghi output — luôn ghi lại từ toàn bộ `results` (cache + mới), an toàn kể cả khi
    # chạy rải rác nhiều lần
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    # Thống kê
    pos     = sum(1 for r in results if r["sentiment_label"] == "positive")
    neg     = sum(1 for r in results if r["sentiment_label"] == "negative")
    neutral = sum(1 for r in results if r["sentiment_label"] == "neutral")
    avg_score = sum(float(r["sentiment_score"]) for r in results) / len(results) if results else 0

    log.info("─" * 50)
    log.info(f"✅ Hoàn thành! Ghi {len(results)} dòng → {output_path.name}")
    log.info(f"   Positive : {pos}  |  Neutral : {neutral}  |  Negative : {neg}")
    log.info(f"   Avg score: {avg_score:+.3f}")
    log.info(f"   Cache: {from_cache} lấy từ cache, {api_calls} gọi Ollama mới")
    cs = cache.cache_stats()
    if cs["errors"]:
        log.info(f"   Còn {cs['errors']} POI lỗi trong cache -> chạy lại với --retry-errors")
    log.info("─" * 50)


if __name__ == "__main__":
    main()