"""
=============================================================
GIAI ĐOẠN 4 — Tính Attraction Score
=============================================================
Input:  dalat_poi_gmaps.csv        (output của giai đoạn 3)
Output: dalat_poi_scored.csv       (có attraction_score)

Công thức:
  attraction_score = w1 * tiktok_score + w2 * gmaps_score

  tiktok_score = normalize(mention_count * 10
                           + total_digg   * 0.001
                           + total_plays  * 0.0001)

  gmaps_score  = normalize(gmaps_rating * log(gmaps_reviews_count + 1))

Trọng số mặc định: w1=0.6, w2=0.4
(TikTok = tín hiệu xu hướng viral; Maps = chất lượng thực tế)
=============================================================
"""

import csv, math, os

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV  = "dalat_poi_gmaps.csv"
OUTPUT_CSV = "dalat_poi_scored.csv"

# Trọng số — tổng = 1.0
W_TIKTOK = 0.6   # tín hiệu viral TikTok
W_GMAPS  = 0.4   # chất lượng Google Maps

# Thời gian tham quan mặc định theo type (phút)
# Dùng cho TSPTW khi địa điểm không có giờ mở cửa rõ ràng
DEFAULT_VISIT_DURATION = {
    "cafe":              60,
    "nhà hàng":          60,
    "chợ quán":          45,
    "địa điểm checkin":  30,
    "thiên nhiên":       90,
    "homestay":           0,   # không tính vào route
    "khách sạn":          0,
    "khác":              45,
}

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val) if val not in (None, "", "nan") else default
    except (ValueError, TypeError):
        return default

def safe_int(val, default=0):
    try:
        return int(float(val)) if val not in (None, "", "nan") else default
    except (ValueError, TypeError):
        return default

def normalize_minmax(values):
    """Min-max normalization -> [0, 1]"""
    valid = [v for v in values if v is not None]
    if not valid:
        return [0.0] * len(values)
    mn, mx = min(valid), max(valid)
    if mx == mn:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) if v is not None else 0.0 for v in values]

def parse_time(t):
    """'08:00' -> 480 (phút từ 00:00), '' -> None"""
    if not t or t.strip() == "":
        return None
    t = t.strip()
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — ĐỌC DATA
# ══════════════════════════════════════════════════════════════

def load_csv(filepath):
    with open(filepath, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

# ══════════════════════════════════════════════════════════════
# BƯỚC 2 — TÍNH RAW SCORES
# ══════════════════════════════════════════════════════════════

def compute_tiktok_raw(row):
    """Raw TikTok score — chưa normalize"""
    mentions = safe_int(row.get("mention_count", 0))
    digg     = safe_int(row.get("total_digg", 0))
    plays    = safe_int(row.get("total_plays", 0))
    conf     = safe_int(row.get("confidence_high", 0))

    return (
        mentions * 10
        + digg   * 0.001
        + plays  * 0.0001
        + conf   * 5       # thưởng thêm cho high-confidence mentions
    )


def compute_gmaps_raw(row):
    """
    Raw Google Maps score = rating * log(reviews + 1)
    Bayesian-style: rating cao mà ít review thì điểm thấp hơn rating cao + nhiều review.
    """
    rating  = safe_float(row.get("gmaps_rating", 0))
    reviews = safe_int(row.get("gmaps_reviews_count", 0))

    if rating == 0:
        return 0.0

    return rating * math.log(reviews + 1)

# ══════════════════════════════════════════════════════════════
# BƯỚC 3 — NORMALIZE & KẾT HỢP
# ══════════════════════════════════════════════════════════════

def compute_scores(pois):
    # Tính raw
    tiktok_raws = [compute_tiktok_raw(p) for p in pois]
    gmaps_raws  = [compute_gmaps_raw(p)  for p in pois]

    # Normalize [0, 1]
    tiktok_norm = normalize_minmax(tiktok_raws)
    gmaps_norm  = normalize_minmax(gmaps_raws)

    results = []
    for i, p in enumerate(pois):
        ts = tiktok_norm[i]
        gs = gmaps_norm[i]

        # Nếu gmaps_rating = 0 (không có rating) -> chỉ dùng TikTok
        if safe_float(p.get("gmaps_rating", 0)) == 0:
            attraction_score = ts
        else:
            attraction_score = W_TIKTOK * ts + W_GMAPS * gs

        # Giờ mở cửa -> phút
        open_min  = parse_time(p.get("open_time",  ""))
        close_min = parse_time(p.get("close_time", ""))

        # Thời gian tham quan mặc định
        poi_type = p.get("type", "khác").strip().lower()
        visit_min = DEFAULT_VISIT_DURATION.get(poi_type, 45)

        # Lọc homestay/khách sạn khỏi route (visit_duration=0)
        include_in_route = visit_min > 0

        row = {
            **p,
            # Scores
            "tiktok_raw":        round(tiktok_raws[i], 2),
            "gmaps_raw":         round(gmaps_raws[i],  2),
            "tiktok_score_norm": round(ts, 4),
            "gmaps_score_norm":  round(gs, 4),
            "attraction_score":  round(attraction_score, 4),
            # Cho TSPTW
            "open_min":          open_min  if open_min  is not None else "",
            "close_min":         close_min if close_min is not None else "",
            "visit_duration_min": visit_min,
            "include_in_route":  include_in_route,
        }
        results.append(row)

    # Sắp xếp theo attraction_score giảm dần
    return sorted(results, key=lambda x: x["attraction_score"], reverse=True)

# ══════════════════════════════════════════════════════════════
# BƯỚC 4 — LƯU
# ══════════════════════════════════════════════════════════════

def save_csv(data, filepath):
    if not data:
        print("Không có dữ liệu!")
        return
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    print(f"  Đã lưu {len(data)} dòng -> {filepath}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*55)
    print("  GIAI ĐOẠN 4 — Tính Attraction Score")
    print(f"  Trọng số: TikTok={W_TIKTOK} | GMaps={W_GMAPS}")
    print("="*55)

    pois = load_csv(INPUT_CSV)
    print(f"\n  Đọc {len(pois)} POI từ {INPUT_CSV}")

    scored = compute_scores(pois)

    # Lọc POI vào route (bỏ homestay/khách sạn)
    route_pois = [p for p in scored if p["include_in_route"]]
    print(f"  POI đưa vào route: {len(route_pois)}/{len(scored)}")

    save_csv(scored, OUTPUT_CSV)

    # In top 20
    print(f"\n  Top 20 địa điểm theo Attraction Score:")
    print(f"  {'#':<3} {'Tên':<35} {'Type':<20} {'TikTok':>7} {'GMaps':>6} {'Score':>6} {'Giờ mở cửa'}")
    print("  " + "-"*95)
    for i, p in enumerate(scored[:20], 1):
        hours = ""
        if p["open_min"] != "" and p["close_min"] != "":
            oh = int(p["open_min"]) // 60
            om = int(p["open_min"]) % 60
            ch = int(p["close_min"]) // 60
            cm = int(p["close_min"]) % 60
            hours = f"{oh:02d}:{om:02d}-{ch:02d}:{cm:02d}"
        print(f"  {i:<3} {p['place_name'][:34]:<35} {p['type'][:19]:<20} "
              f"{p['tiktok_score_norm']:>7.3f} {p['gmaps_score_norm']:>6.3f} "
              f"{p['attraction_score']:>6.3f} {hours}")

    # Thống kê theo type (chỉ POI trong route)
    print(f"\n  Phân bố POI trong route theo type:")
    type_counts = {}
    for p in route_pois:
        t = p["type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t:<25} {c} POI")

    print("\n" + "="*55)
    print("  HOÀN THÀNH")
    print(f"  File output: {OUTPUT_CSV}")
    print("  Bước tiếp theo: chạy route_optimizer.py")
    print("="*55)


if __name__ == "__main__":
    main()
