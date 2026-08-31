"""
=============================================================
Seed cache từ kết quả gmaps_join.py đã chạy trước đó
=============================================================
Mục đích: bạn đã có `dalat_poi_gmaps_fix.csv` (matched) và
`dalat_poi_unmatched_fix.csv` (unmatched) từ lần chạy TRƯỚC khi có
cache. Script này "nạp" lại các kết quả đó vào gmaps_cache.db,
để khi chạy `python gmaps_join.py` lần tới:

  - Các POI đã match thành công -> lấy thẳng từ cache, KHÔNG gọi
    lại API (tiết kiệm phí).
  - Các POI unmatched do "zero_results" (search không ra kết quả)
    -> cũng seed là zero_results, không gọi lại (đỡ tốn quota vô ích).
  - Các POI unmatched do "low_score_*" hoặc "out_of_dalat"
    -> KHÔNG seed, vì CSV cũ không lưu lại danh sách candidates gốc.
    Những POI này sẽ được gọi lại API ở lần chạy tới -> đúng ý đồ:
    cho chúng cơ hội match lại (có thể threshold đã đổi, hoặc chỉ
    là muốn thử lại).

Chạy 1 lần trước khi chạy lại gmaps_join.py:
    python seed_from_existing.py
=============================================================
"""

import csv
import gmaps_cache as cache

MATCHED_CSV   = "dalat_poi_gmaps_fix.csv"
UNMATCHED_CSV = "dalat_poi_unmatched_fix.csv"


def load_csv(filepath):
    try:
        with open(filepath, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"  (Không tìm thấy {filepath}, bỏ qua)")
        return []


def hhmm_to_periods(open_time, close_time):
    """Dựng lại 'periods' tối thiểu từ open_time/close_time dạng HH:MM,
    đủ để parse_opening_hours() trong gmaps_join.py đọc lại đúng."""
    if not open_time or not close_time:
        return []
    try:
        o = open_time.replace(":", "")
        c = close_time.replace(":", "")
        return [{"open": {"time": o}, "close": {"time": c}}]
    except Exception:
        return []


def seed_matched():
    rows = load_csv(MATCHED_CSV)
    seeded = 0
    for row in rows:
        place_name = row.get("place_name", "")
        place_id   = row.get("gmaps_place_id", "")
        if not place_name or not place_id:
            continue

        # 1) Seed search_cache — candidate tối thiểu đủ để best_match() chọn lại đúng nó
        try:
            lat = float(row["lat"]) if row.get("lat") not in (None, "") else None
            lng = float(row["lng"]) if row.get("lng") not in (None, "") else None
        except ValueError:
            lat = lng = None

        candidate = {
            "name": row.get("gmaps_name", ""),
            "place_id": place_id,
            "formatted_address": row.get("gmaps_address", ""),
            "geometry": {"location": {"lat": lat, "lng": lng}},
            "rating": row.get("gmaps_rating", ""),
            "user_ratings_total": row.get("gmaps_reviews_count", ""),
        }
        search_key = cache.make_key(place_name)
        cache.save_search_cache(search_key, place_name, [candidate], "success")

        # 2) Seed detail_cache — dựng lại đủ field mà parse_opening_hours() và
        #    phần build "row" trong main() cần đọc
        weekday_text_list = row.get("opening_hours_text", "").split(" | ") if row.get("opening_hours_text") else []
        detail = {
            "opening_hours": {
                "periods": hhmm_to_periods(row.get("open_time", ""), row.get("close_time", "")),
                "weekday_text": weekday_text_list,
            },
            "business_status": row.get("gmaps_status", ""),
            "formatted_phone_number": row.get("gmaps_phone", ""),
            "website": row.get("gmaps_website", ""),
            "price_level": row.get("gmaps_price_level", ""),
        }
        cache.save_detail_cache(place_id, detail, "success")
        seeded += 1

    print(f"  Đã seed {seeded}/{len(rows)} POI matched từ {MATCHED_CSV}")
    return seeded


def seed_unmatched_zero_results():
    rows = load_csv(UNMATCHED_CSV)
    seeded = 0
    skipped = 0
    for row in rows:
        place_name = row.get("place_name", "")
        reason = row.get("reason", "")
        if not place_name:
            continue
        if reason == "zero_results":
            search_key = cache.make_key(place_name)
            cache.save_search_cache(search_key, place_name, [], "zero_results")
            seeded += 1
        else:
            # low_score_* hoặc out_of_dalat -> cố ý không seed, để gọi lại API
            skipped += 1

    print(f"  Đã seed {seeded} POI zero_results (không gọi lại API cho nhóm này)")
    print(f"  Bỏ qua {skipped} POI (low_score_*/out_of_dalat) -> sẽ gọi lại API khi chạy gmaps_join.py")
    return seeded, skipped


def main():
    print("\n" + "="*55)
    print("  SEED CACHE TỪ KẾT QUẢ GMAPS_JOIN.PY CŨ")
    print("="*55 + "\n")

    cache.init_cache()

    n_matched = seed_matched()
    n_zero, n_retry = seed_unmatched_zero_results()

    print("\n" + "-"*55)
    print(f"  Tổng seed vào cache : {n_matched + n_zero} POI (khỏi gọi lại API)")
    print(f"  Sẽ gọi lại API cho  : {n_retry} POI (low_score_*/out_of_dalat)")
    print("-"*55)
    print("\n  Xong. Giờ chạy: python gmaps_join.py")
    print("  (chỉ POI thuộc nhóm 'sẽ gọi lại API' ở trên mới tốn phí)\n")


if __name__ == "__main__":
    main()
