"""
=============================================================
GIAI ĐOẠN 3 — Join POI với Google Maps
=============================================================
Input:  dalat_poi_clean.csv        (output của giai đoạn 2)
Output: dalat_poi_gmaps.csv        (đã có tọa độ, rating, giờ mở cửa)
        dalat_poi_unmatched.csv    (POI không match được)

Yêu cầu:
  pip install requests python-dotenv rapidfuzz

.env cần có:
  GMAPS_API_KEY=AIza...
  (Bật: Places API + Distance Matrix API trong Google Cloud Console)
  
Cập nhật INPUT_CSV = 'dalat_poi_clean_fix.csv', 
OUTPUT_MATCHED = 'dalat_poi_gmaps_fix.csv', 
tăng FUZZY_THRESHOLD từ 60 lên 65 để match chính xác hơn
=============================================================
"""

import csv, json, os, time, re, argparse
import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz

import gmaps_cache as cache

load_dotenv()

GMAPS_API_KEY = os.getenv("GMAPS_API_KEY")
if not GMAPS_API_KEY:
    raise ValueError("Thiếu GMAPS_API_KEY trong .env")

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV = "dalat_poi_clean_fix.csv"      # thay vì "dalat_poi_clean.csv"
OUTPUT_MATCHED = "dalat_poi_gmaps_fix.csv" # nên đặt tên khác để tránh ghi đè
OUTPUT_UNMATCHED = "dalat_poi_unmatched_fix.csv"

# Bounding box Đà Lạt — lọc kết quả ngoài vùng
DALAT_LAT = 11.9404
DALAT_LNG = 108.4583
DALAT_RADIUS_KM = 25  # bán kính tìm kiếm tính từ trung tâm Đà Lạt

# Fuzzy match threshold — giảm xuống nếu match rate thấp
FUZZY_THRESHOLD = 65  # 0-100, càng cao càng chặt — hạ từ 65->60 để cứu ~31 POI score 60-64

# Delay giữa các request (tránh quota exceeded)
REQUEST_DELAY = 0.3  # giây

GMAPS_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GMAPS_DETAIL_URL = "https://maps.googleapis.com/maps/api/place/details/json"

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — TÌM KIẾM TRÊN GOOGLE MAPS
# ══════════════════════════════════════════════════════════════

def search_place(place_name):
    """Tìm địa điểm trên Google Maps, ưu tiên kết quả trong vùng Đà Lạt.
    Trả về (results, status) với status in {"success", "zero_results", "error"} —
    tách rõ zero_results (kết quả hợp lệ, không nên retry) khỏi error (nên retry)."""
    query = f"{place_name} Đà Lạt"
    params = {
        "query":    query,
        "location": f"{DALAT_LAT},{DALAT_LNG}",
        "radius":   DALAT_RADIUS_KM * 1000,
        "key":      GMAPS_API_KEY,
        "language": "vi",
    }
    try:
        r = requests.get(GMAPS_SEARCH_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "OK":
            return data.get("results", []), "success"
        elif data.get("status") == "ZERO_RESULTS":
            return [], "zero_results"
        else:
            print(f"    API error: {data.get('status')} - {data.get('error_message', '')}")
            return [], "error"
    except Exception as e:
        print(f"    Request error: {str(e)[:60]}")
        return [], "error"


def search_place_cached(place_name, stats):
    """Wrapper có cache quanh search_place — chỉ gọi API nếu chưa có trong cache
    (hoặc lần trước bị 'error' và đang chạy --retry-errors)."""
    key = cache.make_key(place_name)
    cached_candidates, cached_status = cache.get_search_cached(key)

    if cached_status is not None:
        stats["search_from_cache"] += 1
        return cached_candidates, cached_status

    candidates, status = search_place(place_name)
    stats["search_api_calls"] += 1
    cache.save_search_cache(key, place_name, candidates, status)
    return candidates, status


def get_place_details(place_id):
    """Lấy chi tiết địa điểm: giờ mở cửa, phone, website"""
    params = {
        "place_id": place_id,
        "fields":   "name,formatted_address,geometry,rating,user_ratings_total,opening_hours,formatted_phone_number,website,price_level,business_status",
        "key":      GMAPS_API_KEY,
        "language": "vi",
    }
    try:
        r = requests.get(GMAPS_DETAIL_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "OK":
            return data.get("result", {})
        return {}
    except Exception as e:
        print(f"    Detail error: {str(e)[:60]}")
        return {}


def get_place_details_cached(place_id, stats):
    """Wrapper có cache quanh get_place_details — key theo place_id nên nhiều POI
    trùng địa điểm (sau fuzzy match) chỉ tốn 1 lần gọi API detail."""
    detail, status = cache.get_detail_cached(place_id)

    if status is not None:
        stats["detail_from_cache"] += 1
        return detail

    detail = get_place_details(place_id)
    time.sleep(REQUEST_DELAY)
    stats["detail_api_calls"] += 1
    status = "success" if detail else "error"
    cache.save_detail_cache(place_id, detail, status)
    return detail

# ══════════════════════════════════════════════════════════════
# BƯỚC 2 — FUZZY MATCH
# ══════════════════════════════════════════════════════════════

def normalize(text):
    """Chuẩn hóa tên để so sánh fuzzy"""
    text = text.lower().strip()
    # Bỏ các suffix chung
    for suffix in ["đà lạt", "da lat", "dalat", "- đà lạt", "- da lat"]:
        text = text.replace(suffix, "").strip()
    # Bỏ dấu câu thừa
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def best_match(poi_name, candidates):
    """
    Chọn candidate tốt nhất từ kết quả Google Maps.
    Trả về (candidate, score) hoặc (None, 0) nếu không đạt threshold.
    """
    poi_norm = normalize(poi_name)
    best_score = 0
    best_cand  = None

    for cand in candidates:
        cand_name = cand.get("name", "")
        cand_norm = normalize(cand_name)

        # Token sort ratio — tốt cho tên có thứ tự từ khác nhau
        score = fuzz.token_sort_ratio(poi_norm, cand_norm)

        # Bonus nếu tên gốc là substring của tên Maps
        if poi_norm in cand_norm or cand_norm in poi_norm:
            score = min(100, score + 15)

        if score > best_score:
            best_score = score
            best_cand  = cand

    if best_score >= FUZZY_THRESHOLD:
        return best_cand, best_score
    return None, best_score

# ══════════════════════════════════════════════════════════════
# BƯỚC 3 — KIỂM TRA TRONG VÙNG ĐÀ LẠT
# ══════════════════════════════════════════════════════════════

def haversine_km(lat1, lng1, lat2, lng2):
    """Tính khoảng cách km giữa 2 tọa độ"""
    import math
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def in_dalat(candidate):
    """Kiểm tra địa điểm có trong vùng Đà Lạt không"""
    loc = candidate.get("geometry", {}).get("location", {})
    lat = loc.get("lat")
    lng = loc.get("lng")
    if lat is None or lng is None:
        return False
    dist = haversine_km(DALAT_LAT, DALAT_LNG, lat, lng)
    return dist <= DALAT_RADIUS_KM

# ══════════════════════════════════════════════════════════════
# BƯỚC 4 — PARSE GIỜ MỞ CỬA
# ══════════════════════════════════════════════════════════════

def parse_opening_hours(detail):
    """Trích xuất giờ mở cửa dạng text và structured"""
    hours = detail.get("opening_hours", {})
    periods = hours.get("periods", [])

    # Text hiển thị
    weekday_text = " | ".join(hours.get("weekday_text", []))

    # Giờ mở/đóng ngày thường (lấy ngày đầu tiên làm đại diện)
    open_time  = ""
    close_time = ""
    if periods:
        p = periods[0]
        open_time  = p.get("open",  {}).get("time", "")
        close_time = p.get("close", {}).get("time", "")
        # Format HHMM -> HH:MM
        if len(open_time)  == 4: open_time  = open_time[:2]  + ":" + open_time[2:]
        if len(close_time) == 4: close_time = close_time[:2] + ":" + close_time[2:]

    return weekday_text, open_time, close_time

# ══════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ══════════════════════════════════════════════════════════════

def load_csv(filepath):
    with open(filepath, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_csv(data, filepath):
    if not data:
        print(f"  Không có dữ liệu -> {filepath}")
        return
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    print(f"  Đã lưu {len(data)} dòng -> {filepath}")


def parse_args():
    parser = argparse.ArgumentParser(description="Giai đoạn 3 — Join POI với Google Maps")
    parser.add_argument("--retry-errors", action="store_true",
                         help="Xóa các entry 'error' trong cache rồi gọi lại API CHỈ cho các POI đó")
    parser.add_argument("--no-cache", action="store_true",
                         help="Bỏ qua cache hoàn toàn, gọi lại API cho mọi POI (tương đương --force cũ)")
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n" + "="*55)
    print("  GIAI ĐOẠN 3 — Join POI với Google Maps")
    print("="*55)

    if args.no_cache:
        # Xóa hẳn DB cache để đảm bảo mọi request đều gọi lại API
        if os.path.exists(cache.CACHE_DB):
            os.remove(cache.CACHE_DB)
        print("  (--no-cache) Đã xóa cache, sẽ gọi lại API cho toàn bộ POI")

    cache.init_cache()

    if args.retry_errors:
        n_search, n_detail = cache.clear_errors()
        print(f"  (--retry-errors) Đã xóa {n_search} search-cache lỗi, {n_detail} detail-cache lỗi")

    stats = {
        "search_from_cache": 0, "search_api_calls": 0,
        "detail_from_cache": 0, "detail_api_calls": 0,
    }

    pois = load_csv(INPUT_CSV)
    print(f"\nĐọc {len(pois)} POI từ {INPUT_CSV}")

    matched   = []
    unmatched = []
    total     = len(pois)

    for i, poi in enumerate(pois, 1):
        name = poi["place_name"]
        print(f"\n  [{i}/{total}] {name}")

        # Tìm trên Google Maps (có cache — chỉ gọi API nếu chưa có / đang retry lỗi)
        candidates, search_status = search_place_cached(name, stats)

        if search_status == "error":
            print(f"    -> Lỗi khi tìm kiếm (đã cache là error, dùng --retry-errors để thử lại)")
            unmatched.append({**poi, "reason": "search_error"})
            continue

        if not candidates:
            print(f"    -> Không tìm thấy")
            unmatched.append({**poi, "reason": "zero_results"})
            continue

        # Lọc trong vùng Đà Lạt trước
        dalat_candidates = [c for c in candidates if in_dalat(c)]
        search_pool = dalat_candidates if dalat_candidates else candidates

        # Fuzzy match
        best, score = best_match(name, search_pool)

        if best is None:
            print(f"    -> Fuzzy score thấp ({score}) — bỏ qua")
            unmatched.append({**poi, "reason": f"low_score_{score}"})
            continue

        # Kiểm tra trong vùng Đà Lạt
        if not in_dalat(best):
            print(f"    -> Ngoài vùng Đà Lạt — bỏ qua")
            unmatched.append({**poi, "reason": "out_of_dalat"})
            continue

        # Lấy chi tiết (có cache theo place_id)
        place_id = best.get("place_id", "")
        detail   = get_place_details_cached(place_id, stats) if place_id else {}

        loc = best.get("geometry", {}).get("location", {})
        weekday_text, open_time, close_time = parse_opening_hours(detail)

        row = {
            **poi,
            # Google Maps fields
            "gmaps_name":          best.get("name", ""),
            "gmaps_place_id":      place_id,
            "gmaps_address":       best.get("formatted_address", ""),
            "lat":                 loc.get("lat", ""),
            "lng":                 loc.get("lng", ""),
            "gmaps_rating":        best.get("rating", ""),
            "gmaps_reviews_count": best.get("user_ratings_total", ""),
            "gmaps_price_level":   detail.get("price_level", ""),
            "gmaps_phone":         detail.get("formatted_phone_number", ""),
            "gmaps_website":       detail.get("website", ""),
            "gmaps_status":        detail.get("business_status", ""),
            "opening_hours_text":  weekday_text,
            "open_time":           open_time,
            "close_time":          close_time,
            "fuzzy_score":         score,
        }
        matched.append(row)
        print(f"    -> MATCH '{best.get('name')}' | score={score} | rating={best.get('rating', 'N/A')}")

    # Lưu kết quả
    print("\n" + "="*55)
    print(f"  MATCH:   {len(matched)}/{total} ({round(len(matched)/total*100)}%)")
    print(f"  NO MATCH: {len(unmatched)}/{total}")

    print(f"\n  Cache — search: {stats['search_from_cache']} lấy từ cache, "
          f"{stats['search_api_calls']} gọi API mới")
    print(f"  Cache — detail: {stats['detail_from_cache']} lấy từ cache, "
          f"{stats['detail_api_calls']} gọi API mới")
    cs = cache.cache_stats()
    if cs["search_error"] or cs["detail_error"]:
        print(f"  Còn lỗi trong cache: search_error={cs['search_error']}, "
              f"detail_error={cs['detail_error']} -> chạy lại với --retry-errors")

    save_csv(matched,   OUTPUT_MATCHED)
    save_csv(unmatched, OUTPUT_UNMATCHED)

    # Thống kê
    if matched:
        has_hours = sum(1 for r in matched if r["open_time"])
        has_rating = sum(1 for r in matched if r["gmaps_rating"])
        print(f"\n  Có giờ mở cửa: {has_hours}/{len(matched)}")
        print(f"  Có rating:      {has_rating}/{len(matched)}")

        print("\n  Top 10 địa điểm sau join:")
        top10 = sorted(matched, key=lambda x: float(x["popularity_score"] or 0), reverse=True)[:10]
        for j, r in enumerate(top10, 1):
            print(f"    {j:2}. {r['place_name']:<30} | lat={r['lat']:.4f} | rating={r['gmaps_rating']} | {r['open_time']}-{r['close_time']}")

    print("="*55)


if __name__ == "__main__":
    main()