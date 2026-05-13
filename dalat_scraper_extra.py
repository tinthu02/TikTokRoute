"""
=============================================================
GIAI ĐOẠN 1 — Thu thập & Trích xuất dữ liệu TikTok Đà Lạt
=============================================================
Yêu cầu: pip install apify-client python-dotenv requests

Cài Ollama: https://ollama.com
Sau đó chạy: ollama pull qwen2.5:7b
"""

# Dòng đầu file, thêm subprocess:
import os, csv, time, json, datetime, requests, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN = os.getenv("APIFY_TOKEN")
if not APIFY_TOKEN:
    raise ValueError("Thieu APIFY_TOKEN trong .env")

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

DALAT_HASHTAGS = [
    "dalat", "dulichdalat", "reviewdalat", "khamphadalat",
    "cafedalat", "dalatfoodtour", "anvatdalat", "quanngondalat",
    "homestaydalat", "dalatcheckin", "sanmaydalat", "dalatdep", "thacdalat",
]

EXTRA_HASHTAGS = [
    "datanla",    # thiên nhiên — đang thiếu hoàn toàn
    "langbiang",  # thiên nhiên — đang thiếu
    "fooddalat",  # ẩm thực — đang mỏng
    "camdalat",   # cắm trại — trend mới
]

DALAT_HASHTAGS = EXTRA_HASHTAGS

MAX_VIDEOS_PER_HASHTAG = 30

# Free plan RAM = 8GB, mỗi actor ~4GB → chỉ 2 worker song song
MAX_WORKERS = 2

RAW_CSV  = "dalat_videos_raw_extra.csv"
POI_CSV  = "dalat_poi_extracted_extra.csv"
ACTOR_ID = "clockworks/tiktok-scraper"

# ── Ollama config ──────────────────────────────────────────────
# Chọn model phù hợp với máy:
#   8GB  RAM, không GPU  → "gemma2:2b"
#   16GB RAM, có GPU rời → "qwen2.5:7b"
#   32GB RAM+            → "qwen2.5:14b"
OLLAMA_MODEL   = "qwen2.5:7b"
OLLAMA_URL     = "http://localhost:11434/api/chat"
OLLAMA_TIMEOUT = 180   # giây, tăng lên nếu máy chậm

apify_client = ApifyClient(APIFY_TOKEN)

# ══════════════════════════════════════════════════════════════
# BƯỚC 0 — KIỂM TRA OLLAMA ĐANG CHẠY
# ══════════════════════════════════════════════════════════════

def check_ollama():
    try:
        requests.get("http://localhost:11434", timeout=5)
        r2 = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r2.json().get("models", [])]

        # So sánh EXACT tên model, không dùng model_base nữa
        if OLLAMA_MODEL not in models:
            print("  Model chưa có, đang pull: " + OLLAMA_MODEL + " ...")
            subprocess.run(["ollama", "pull", OLLAMA_MODEL], check=True)
            print("  Pull xong: " + OLLAMA_MODEL)
        else:
            print("  Ollama OK - đang dùng model: " + OLLAMA_MODEL)

    except requests.exceptions.ConnectionError:
        print("\n  OLLAMA CHƯA CHẠY!")
        print("  Tải Ollama tại https://ollama.com, mở app rồi chạy lại script.")
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        print("  Pull thất bại: " + str(e))
        raise SystemExit(1)
    try:
        r = requests.get("http://localhost:11434", timeout=5)
        # Kiểm tra model đã pull chưa
        r2 = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r2.json().get("models", [])]
        model_base = OLLAMA_MODEL.split(":")[0]
        has_model  = any(model_base in m for m in models)
        if not has_model:
            print("\n  MODEL CHƯA ĐƯỢC PULL!")
            print("  Chạy lệnh: ollama pull " + OLLAMA_MODEL)
            print("  Danh sách model hiện có: " + str(models))
            raise SystemExit(1)
        print("  Ollama OK - đang dùng model: " + OLLAMA_MODEL)
    except requests.exceptions.ConnectionError:
        print("\n  OLLAMA CHƯA CHẠY!")
        print("  1. Tải Ollama tại https://ollama.com")
        print("  2. Mở Ollama (nếu không tự động chạy server)")
        print("  3. Chạy: ollama pull " + OLLAMA_MODEL)
        raise SystemExit(1)

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — SCRAPE TIKTOK
# ══════════════════════════════════════════════════════════════

def collect_hashtag(hashtag, max_items):
    run_input = {
        "hashtags":                      [hashtag],
        "resultsPerPage":                max_items,
        "shouldDownloadVideos":          False,
        "shouldDownloadCovers":          False,
        "shouldDownloadSubtitles":       True,
        "shouldDownloadSlideshowImages": False,
    }
    run   = apify_client.actor(ACTOR_ID).call(run_input=run_input)
    items = apify_client.dataset(run["defaultDatasetId"]).list_items().items

    if items and not getattr(collect_hashtag, "_debugged", False):
        collect_hashtag._debugged = True
        print("\n  DEBUG keys: " + str(list(items[0].keys())[:20]))

    return items


def collect_with_retry(hashtag, max_items, retries=3):
    for attempt in range(retries):
        try:
            print("  Scraping [" + hashtag + "]...")
            items = collect_hashtag(hashtag, max_items)
            print("  OK [" + hashtag + "] -> " + str(len(items)) + " video")
            return items
        except Exception as e:
            wait = 2 ** attempt
            print("  Loi [" + hashtag + "] lan " + str(attempt+1) + ": " + str(e)[:80])
            time.sleep(wait)
    print("  Bo qua [" + hashtag + "]")
    return []


def normalize_video(item, source_hashtag):
    subtitles  = item.get("videoMeta", {}).get("subtitles", [])
    voice_text = ""
    if subtitles:
        vi = [s for s in subtitles if s.get("languageCode", "") in ("vie", "vi")]
        chosen = vi[0] if vi else subtitles[0]
        voice_text = chosen.get("text") or ""

    overlays     = item.get("videoMeta", {}).get("textOverlays", [])
    overlay_text = " | ".join([t.get("text", "") for t in overlays if t.get("text")])

    return {
        "video_id":         item.get("id", ""),
        "source_hashtag":   source_hashtag,
        "collected_at":     datetime.datetime.now().isoformat(timespec="seconds"),
        "create_time":      item.get("createTime", ""),
        "author_name":      item.get("authorMeta", {}).get("name", ""),
        "author_followers": item.get("authorMeta", {}).get("fans", 0),
        "description":      item.get("text", ""),
        "hashtags":         ", ".join(item.get("hashtagNames", [])),
        "video_url":        item.get("webVideoUrl", ""),
        "digg_count":       item.get("diggCount", 0),
        "share_count":      item.get("shareCount", 0),
        "comment_count":    item.get("commentCount", 0),
        "play_count":       item.get("playCount", 0),
        "voice_to_text":    voice_text,
        "in_video_text":    overlay_text,
    }


def scrape_all():
    all_raw = []
    print("\n" + "="*55)
    print("  Scrape " + str(len(DALAT_HASHTAGS)) + " hashtag (" + str(MAX_WORKERS) + " worker)")
    print("  (Bao gom " + str(len(EXTRA_HASHTAGS)) + " hashtag bo sung: " + ", ".join(EXTRA_HASHTAGS) + ")")
    print("="*55)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(collect_with_retry, tag, MAX_VIDEOS_PER_HASHTAG): tag
            for tag in DALAT_HASHTAGS
        }
        for future in as_completed(future_map):
            items = future.result()
            tag   = future_map[future]
            for item in items:
                all_raw.append(normalize_video(item, tag))

    seen, unique = set(), []
    for i, v in enumerate(all_raw):
        vid = v["video_id"] or ("_noid_" + str(i))
        if vid not in seen:
            unique.append(v)
            seen.add(vid)

    print("\nTong video: " + str(len(all_raw)) + " | Sau dedup: " + str(len(unique)))
    return unique


# ══════════════════════════════════════════════════════════════
# BƯỚC 2 — TRÍCH XUẤT ĐỊA ĐIỂM BẰNG OLLAMA
# ══════════════════════════════════════════════════════════════

EXTRACT_SYSTEM = (
    "Bạn là chuyên gia trích xuất địa điểm du lịch Đà Lạt từ nội dung TikTok tiếng Việt. "
    "Chỉ trả về JSON, không có text thêm vào, không có markdown. "
    "Chỉ lấy địa điểm ở Đà Lạt hoặc Lâm Đồng. "
    "Không bịa đặt. Nếu không có địa điểm rõ ràng thì trả về: {\"places\": []}. "
    "Chuẩn hóa tên: 'quán cà phê mây' / 'Cafe May' / 'cà phê mây đà lạt' đều thành 'Cafe May'. "
    "Phân loại type: cafe | nhà hàng | homestay | khách sạn | địa điểm checkin | thiên nhiên | chợ quán | khác. "
    "Format JSON bat buoc: "
    "{\"places\": [{\"name\": \"Ten dia diem\", \"type\": \"loai\", \"price_mention\": \"gia neu co hoac empty string\", \"confidence\": \"high|medium|low\"}]}"
)


def call_ollama(text_input):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user",   "content": text_input},
        ],
        "stream": False,
        "options": {
            "temperature": 0,       # 0 = deterministic, nhất quán hơn cho extraction
            "num_predict": 500,
        }
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def extract_places_from_video(video):
    text_input = (
        "Description: " + str(video["description"]) + "\n"
        "Voice-to-text: " + str(video["voice_to_text"]) + "\n"
        "In-video text: " + str(video["in_video_text"]) + "\n"
        "Hashtags: " + str(video["hashtags"])
    )

    try:
        raw    = call_ollama(text_input)
        raw    = raw.replace("```json", "").replace("```", "").strip()

        # Tìm JSON object trong response (model đôi khi thêm text thừa)
        start  = raw.find("{")
        end    = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return []
        raw    = raw[start:end]

        data   = json.loads(raw)
        places = data.get("places", [])

        for p in places:
            p["video_id"]         = video["video_id"]
            p["video_url"]        = video["video_url"]
            p["source_hashtag"]   = video["source_hashtag"]
            p["digg_count"]       = video["digg_count"]
            p["play_count"]       = video["play_count"]
            p["author_followers"] = video["author_followers"]

        return places

    except (json.JSONDecodeError, KeyError) as e:
        print("    Parse lỗi video " + str(video["video_id"]) + ": " + str(e))
        return []
    except requests.exceptions.Timeout:
        print("    Timeout video " + str(video["video_id"]) + " (tăng OLLAMA_TIMEOUT nếu cần)")
        return []
    except Exception as e:
        print("    Lỗi Ollama video " + str(video["video_id"]) + ": " + str(e)[:80])
        return []


def aggregate_places(places):
    agg = {}
    for p in places:
        key = p["name"].lower().strip()
        if key not in agg:
            agg[key] = {
                "place_name":       p["name"],
                "type":             p["type"],
                "mention_count":    0,
                "total_digg":       0,
                "total_plays":      0,
                "max_followers":    0,
                "price_mentions":   [],
                "video_urls":       [],
                "confidence_high":  0,
                "popularity_score": 0.0,
            }
        e = agg[key]
        e["mention_count"]  += 1
        e["total_digg"]     += int(p.get("digg_count", 0) or 0)
        e["total_plays"]    += int(p.get("play_count",  0) or 0)
        e["max_followers"]   = max(e["max_followers"], int(p.get("author_followers", 0) or 0))
        if p.get("price_mention"):      e["price_mentions"].append(p["price_mention"])
        if p.get("video_url"):          e["video_urls"].append(p["video_url"])
        if p.get("confidence") == "high": e["confidence_high"] += 1

    for e in agg.values():
        e["popularity_score"] = round(
            e["mention_count"] * 10 + e["total_digg"] * 0.001 + e["total_plays"] * 0.0001, 2
        )
        e["price_mentions"] = " | ".join(set(e["price_mentions"]))
        e["video_urls"]     = e["video_urls"][0] if e["video_urls"] else ""

    return sorted(agg.values(), key=lambda x: x["popularity_score"], reverse=True)


def extract_all_places(videos):
    all_places = []
    total      = len(videos)

    print("\n" + "="*55)
    print("  Trích xuất địa điểm từ " + str(total) + " video bằng Ollama (" + OLLAMA_MODEL + ")")
    print("  (Có thể mất vài phút nếu máy không có GPU)")
    print("="*55)

    t0 = time.time()
    for i, video in enumerate(videos, 1):
        if not any([video["description"], video["voice_to_text"], video["in_video_text"]]):
            continue

        places = extract_places_from_video(video)
        all_places.extend(places)

        if i % 10 == 0:
            elapsed   = round(time.time() - t0, 0)
            remaining = round(elapsed / i * (total - i), 0)
            print("  [" + str(i) + "/" + str(total) + "] "
                  + str(len(all_places)) + " địa điểm | "
                  + "~" + str(int(remaining)) + "s còn lại")

    result = aggregate_places(all_places)
    print("\nTổng số địa điểm unique: " + str(len(result)))
    return result


# ══════════════════════════════════════════════════════════════
# BƯỚC 3 — LƯU
# ══════════════════════════════════════════════════════════════

def save_csv(data, filepath):
    if not data:
        print("Khong co du lieu -> " + filepath)
        return
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    print("Đã lưu " + str(len(data)) + " dòng -> " + filepath)


# ══════════════════════════════════════════════════════════════
# BƯỚC 4 — GỘP VỚI FILE CŨ
# ══════════════════════════════════════════════════════════════

def merge_csvs(old_path, new_path, out_path, dedup_key):
    """
    Gộp 2 CSV, dedup theo dedup_key (ưu tiên giữ dòng cũ nếu trùng key).
    Nếu old_path không tồn tại thì chỉ copy new_path -> out_path.
    """
    def read_csv(path):
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    old_rows = read_csv(old_path)
    new_rows = read_csv(new_path)

    if not old_rows and not new_rows:
        print(f"  Không có dữ liệu để gộp cho {out_path}")
        return

    # Gộp: old trước, new sau — dedup theo key (old được ưu tiên)
    seen = {}
    for row in old_rows:
        k = row.get(dedup_key, "").strip()
        if k and k not in seen:
            seen[k] = row

    added = 0
    for row in new_rows:
        k = row.get(dedup_key, "").strip()
        if k and k not in seen:
            seen[k] = row
            added += 1

    merged = list(seen.values())
    fieldnames = list(merged[0].keys()) if merged else []

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)

    print(f"  Gộp xong: {len(old_rows)} (cũ) + {added} mới = {len(merged)} dòng -> {out_path}")




def main():
    start = time.time()
    print("\nBắt đầu Giai đoạn 1 lúc " + datetime.datetime.now().strftime("%H:%M:%S"))

    # Kiểm tra Ollama trước khi chạy
    check_ollama()

    videos   = scrape_all()
    save_csv(videos, RAW_CSV)

    poi_list = extract_all_places(videos)
    save_csv(poi_list, POI_CSV)

    elapsed = round(time.time() - start, 1)
    print("\n" + "="*55)
    print("  HOÀN THÀNH sau " + str(elapsed) + "s")
    print("  Video thu thập: " + str(len(videos)))
    print("  POI unique:     " + str(len(poi_list)))
    if poi_list:
        print("  Top 5 địa điểm hot nhất:")
        for i, p in enumerate(poi_list[:5], 1):
            print("    " + str(i) + ". " + p["place_name"]
                  + " (" + p["type"] + ") score=" + str(p["popularity_score"]))

    # Bước 4: Gộp với file cũ
    print("\nBước 4: Gộp với file cũ...")
    merge_csvs(
        old_path  = "dalat_videos_raw.csv",
        new_path  = RAW_CSV,
        out_path  = "dalat_videos_raw.csv",
        dedup_key = "video_id",
    )
    merge_csvs(
        old_path  = "dalat_poi_extracted.csv",
        new_path  = POI_CSV,
        out_path  = "dalat_poi_extracted.csv",
        dedup_key = "place_name",
    )
    print("="*55)


if __name__ == "__main__":
    main()