"""
=============================================================
GIAI ĐOẠN 1 — Thu thập & Trích xuất dữ liệu TikTok Đà Lạt (FIXED)
=============================================================
Các lỗi đã fix:
1. Mỗi video chỉ sinh ra tối đa 1 POI (địa điểm chính)
2. Từ chối video dạng tổng hợp / list (nhiều địa điểm)
3. Kiểm tra tên địa điểm có xuất hiện trong nội dung gốc không
4. Lưu video_caption để hỗ trợ relevance sau này

Yêu cầu: pip install apify-client python-dotenv requests
Cài Ollama: https://ollama.com
Sau đó chạy: ollama pull qwen2.5:7b

Sửa lỗi NameError 'place_name', thêm hàm is_list_video() lọc video tổng hợp, 
kiểm tra tên địa điểm xuất hiện trong nội dung, mỗi video chỉ sinh tối đa 1 POI, 
gộp 'săn mây' thành 'Cà phê săn mây Đà Lạt'
"""

import os
import csv
import time
import json
import datetime
import requests
import subprocess
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from apify_client import ApifyClient
from dotenv import load_dotenv

import sys
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_TOKEN")
if not APIFY_TOKEN:
    raise ValueError("Thieu APIFY_TOKEN trong .env")

# Gộp tất cả hashtag từ yêu cầu (loại trùng lặp)
DALAT_HASHTAGS_BASE = [
    "dalat", "dulichdalat", "reviewdalat", "khamphadalat",
    "cafedalat", "dalatfoodtour", "anvatdalat", "quanngondalat",
    "homestaydalat", "dalatcheckin", "sanmaydalat", "dalatdep", "thacdalat",
]

EXTRA_HASHTAGS_1 = [
    "datanla", "langbiang", "fooddalat", "camdalat",
]

EXTRA_HASHTAGS_2 = [
    "cafeviewdalat", "caphedalat", "dalatcafe",
    "anvatdalat2024", "banhmidalat", "comdalat",
    "dalat2024", "checkindalat", "khamphadalat2024",
]

EXTRA_HASHTAGS_3 = [
    "quanngondalat2024", "diadiemanuongdalat", "reviewanvatdalat",
    "caphengondalat", "cafedalat2024",
    "diaDiemdalat", "gocnhodalat",
]

EXTRA_HASHTAGS_4 = [
    "diadiemanuongdalat",   # tổng hợp quán ăn ngon (có thể trùng)
    "reviewanvatdalat",     # review chi tiết
    "quanandalat",
    "caphengondalat",
    "cafedalat2026",
    "checkindalat",
    "diaDiemdalat",
    "gocnhodalat",
    "diachodalat",
]

# Hợp nhất và loại trùng
ALL_EXTRA = set(EXTRA_HASHTAGS_1 + EXTRA_HASHTAGS_2 + EXTRA_HASHTAGS_3 + EXTRA_HASHTAGS_4)
DALAT_HASHTAGS = list(set(DALAT_HASHTAGS_BASE) | ALL_EXTRA)

# Số video tối đa mỗi hashtag (tăng nhẹ để lấy đủ dữ liệu)
MAX_VIDEOS_PER_HASHTAG = 30

# Free plan RAM 8GB → 2 worker song song
MAX_WORKERS = 2

RAW_CSV  = "dalat_videos_raw_fix.csv"
POI_CSV  = "dalat_poi_extracted_fix.csv"
ACTOR_ID = "clockworks/tiktok-scraper"

# Ollama config
OLLAMA_MODEL   = "qwen2.5:7b"
OLLAMA_URL     = "http://localhost:11434/api/chat"
OLLAMA_TIMEOUT = 180

apify_client = ApifyClient(APIFY_TOKEN)

# ══════════════════════════════════════════════════════════════
# KIỂM TRA OLLAMA
# ══════════════════════════════════════════════════════════════

def check_ollama():
    try:
        requests.get("http://localhost:11434", timeout=5)
        r2 = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r2.json().get("models", [])]
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
    run = apify_client.actor(ACTOR_ID).call(run_input=run_input)
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
    subtitles = item.get("videoMeta", {}).get("subtitles", [])
    voice_text = ""
    if subtitles:
        vi = [s for s in subtitles if s.get("languageCode", "") in ("vie", "vi")]
        chosen = vi[0] if vi else subtitles[0]
        voice_text = chosen.get("text") or ""
    overlays = item.get("videoMeta", {}).get("textOverlays", [])
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
    print("="*55)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {
            executor.submit(collect_with_retry, tag, MAX_VIDEOS_PER_HASHTAG): tag
            for tag in DALAT_HASHTAGS
        }
        for future in as_completed(future_map):
            items = future.result()
            tag = future_map[future]
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
# BƯỚC 2 — TRÍCH XUẤT ĐỊA ĐIỂM BẰNG OLLAMA (FIXED)
# ══════════════════════════════════════════════════════════════

EXTRACT_SYSTEM = (
    "Bạn là chuyên gia trích xuất địa điểm du lịch Đà Lạt từ nội dung TikTok tiếng Việt.\n"
    "Chỉ trả về JSON, không có text thêm vào, không có markdown.\n"
    "CHỈ LẤY ĐỊA ĐIỂM CHÍNH của video - nơi video tập trung review, check-in, giới thiệu hoặc gắn tag.\n"
    "TỪ CHỐI (trả về {\"places\": []}) nếu video thuộc một trong các dạng sau:\n"
    "  - Video liệt kê nhiều địa điểm (ví dụ: 'top 5 quán cafe', 'những quán cafe đẹp', 'các địa điểm check-in', 'gợi ý homestay'...)\n"
    "  - Video dạng 'review chung chung' không tập trung vào một địa điểm cụ thể\n"
    "  - Video chỉ có cảnh đẹp, không nêu tên địa điểm\n"
    "Nếu video chỉ nhắc đến một địa điểm duy nhất một cách rõ ràng (tên cụ thể) thì trả về địa điểm đó.\n"
    "Chuẩn hóa tên: 'quán cà phê mây' / 'Cafe May' / 'cà phê mây đà lạt' đều thành 'Cafe May'.\n"
    "Phân loại type: cafe | nhà hàng | homestay | khách sạn | địa điểm checkin | thiên nhiên | chợ quán | khác.\n"
    "Format JSON bắt buộc:\n"
    "{\"places\": [{\"name\": \"Ten dia diem\", \"type\": \"loai\", \"price_mention\": \"gia neu co\", \"confidence\": \"high|medium|low\"}]}\n"
    "Mảng places chỉ chứa TỐI ĐA 1 phần tử (hoặc rỗng)."
)

def is_list_video(text):
    """Kiểm tra nội dung có dấu hiệu liệt kê nhiều địa điểm không"""
    if not text:
        return False
    list_patterns = [
        r"những quán", r"các quán", r"top\s*\d+", r"gợi ý", r"tổng hợp",
        r"list", r"danh sách", r"những địa điểm", r"những nơi", r"cafe đẹp",
        r"những homestay", r"những check-in", r"những chỗ", r"nơi đẹp"
    ]
    for pat in list_patterns:
        if re.search(pat, text.lower()):
            return True
    return False

def call_ollama(text_input):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user",   "content": text_input},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 500,
        }
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()

def extract_places_from_video(video):
    # Lọc sơ bộ: nếu description hoặc voice_to_text có dấu hiệu list video thì bỏ qua
    if is_list_video(video["description"]) or is_list_video(video["voice_to_text"]):
        print("    Bỏ qua video dạng tổng hợp (list): " + video["video_id"])
        return []

    text_input = (
        "Description: " + str(video["description"]) + "\n"
        "Voice-to-text: " + str(video["voice_to_text"]) + "\n"
        "In-video text: " + str(video["in_video_text"]) + "\n"
        "Hashtags: " + str(video["hashtags"])
    )

    try:
        raw = call_ollama(text_input)
        raw = raw.replace("```json", "").replace("```", "").strip()

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return []
        raw = raw[start:end]

        data = json.loads(raw)
        places = data.get("places", [])
        if not places:
            return []

        # Chỉ lấy địa điểm đầu tiên (ưu tiên chính)
        main_place = places[0]

        # Kiểm tra tên địa điểm có xuất hiện trong nội dung gốc không
        place_name = main_place["name"].lower()
        full_text = (video["description"] + " " + video["voice_to_text"]).lower()
        if place_name not in full_text:
            print(f"    Bỏ qua: tên '{main_place['name']}' không xuất hiện trong nội dung video {video['video_id']}")
            return []

        # Gắn metadata
        main_place["video_id"] = video["video_id"]
        main_place["video_url"] = video["video_url"]
        main_place["source_hashtag"] = video["source_hashtag"]
        main_place["digg_count"] = video["digg_count"]
        main_place["play_count"] = video["play_count"]
        main_place["author_followers"] = video["author_followers"]
        main_place["video_caption"] = video["description"]

        return [main_place]

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
                "video_caption":    p.get("video_caption", ""),
            }
        e = agg[key]
        e["mention_count"]  += 1
        e["total_digg"]     += int(p.get("digg_count", 0) or 0)
        e["total_plays"]    += int(p.get("play_count",  0) or 0)
        e["max_followers"]   = max(e["max_followers"], int(p.get("author_followers", 0) or 0))
        if p.get("price_mention"):      e["price_mentions"].append(p["price_mention"])
        if p.get("video_url"):          e["video_urls"].append(p["video_url"])
        if p.get("confidence") == "high": e["confidence_high"] += 1
        # Ưu tiên cập nhật caption nếu caption mới có chứa tên địa điểm rõ hơn
        if p.get("video_caption"):
            if p["name"].lower() in p["video_caption"].lower():
                e["video_caption"] = p["video_caption"]

    for e in agg.values():
        e["popularity_score"] = round(
            e["mention_count"] * 10 + e["total_digg"] * 0.001 + e["total_plays"] * 0.0001, 2
        )
        e["price_mentions"] = " | ".join(set(e["price_mentions"]))
        e["video_urls"]     = e["video_urls"][0] if e["video_urls"] else ""
        if "video_caption" in e and not e["video_caption"]:
            e["video_caption"] = ""
    return sorted(agg.values(), key=lambda x: x["popularity_score"], reverse=True)

def extract_all_places(videos):
    all_places = []
    total = len(videos)
    print("\n" + "="*55)
    print("  Trích xuất địa điểm từ " + str(total) + " video bằng Ollama (" + OLLAMA_MODEL + ")")
    print("  (Đã bật lọc video list và kiểm tra tên xuất hiện)")
    print("="*55)
    t0 = time.time()
    for i, video in enumerate(videos, 1):
        if not any([video["description"], video["voice_to_text"], video["in_video_text"]]):
            continue
        places = extract_places_from_video(video)
        all_places.extend(places)
        if i % 10 == 0:
            elapsed = round(time.time() - t0, 0)
            remaining = round(elapsed / i * (total - i), 0) if i > 0 else 0
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
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    start = time.time()
    print("\nBắt đầu Giai đoạn 1 (FIX) lúc " + datetime.datetime.now().strftime("%H:%M:%S"))
    check_ollama()
    videos = scrape_all()
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
    print("="*55)

if __name__ == "__main__":
    main()