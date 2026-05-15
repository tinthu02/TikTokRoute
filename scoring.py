"""
=============================================================
GIAI ĐOẠN 4 — Tính Attraction Score (Đã cải tiến)
=============================================================
Cải tiến:
  - Gộp các dòng trùng gmaps_place_id để tránh trùng lặp
  - Cộng dồn tín hiệu TikTok (mention, digg, plays, conf)
  - Xử lý giờ mở cửa qua đêm (close < open)
  - Giữ lại video_urls, aliases để hiển thị
  
Gộp POI trùng lặp theo gmaps_place_id, 
cộng dồn mention/digg/plays, chỉ nối video_urls khi có token chung với tên chuẩn, 
thêm ablation test 3 cấu hình trọng số, INPUT_CSV = 'dalat_poi_gmaps_fix.csv'
=============================================================
"""

import csv, math, os
from collections import defaultdict

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV  = "dalat_poi_gmaps_fix.csv"   # thay vì "dalat_poi_gmaps.csv"
OUTPUT_CSV = "dalat_poi_scored_fix.csv"  # nên đặt tên riêng

W_TIKTOK = 0.6   # tín hiệu viral TikTok
W_GMAPS  = 0.4   # chất lượng Google Maps

# Thời gian tham quan mặc định (phút)
DEFAULT_VISIT_DURATION = {
    "cafe":              60,
    "nhà hàng":          60,
    "chợ quán":          45,
    "địa điểm checkin":  30,
    "thiên nhiên":       90,
    "homestay":           0,
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

def fix_overnight_time(open_min, close_min):
    """
    Sửa giờ mở cửa qua đêm:
    - Nếu close_min == 0 (nửa đêm) và open_min > 0 -> close_min = 24*60
    - Nếu close_min < open_min (ví dụ 22:00 - 02:00) -> close_min += 24*60
    """
    if open_min is None or close_min is None:
        return open_min, close_min
    if close_min == 0 and open_min > 0:
        close_min = 24 * 60
    elif close_min < open_min:
        close_min += 24 * 60
    return open_min, close_min

def get_place_key(row):
    """Lấy khóa để gộp: ưu tiên gmaps_place_id, nếu không có thì dùng tọa độ làm tròn."""
    pid = row.get("gmaps_place_id", "").strip()
    if pid:
        return pid
    # fallback: tọa độ làm tròn 3 chữ số (~111m)
    lat = safe_float(row.get("lat", 0))
    lng = safe_float(row.get("lng", 0))
    if lat != 0 or lng != 0:
        return f"coord_{round(lat, 3)}_{round(lng, 3)}"
    return None

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — ĐỌC CSV & GỘP TRÙNG LẶP
# ══════════════════════════════════════════════════════════════

def load_and_merge(filepath):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    merged = {}  # key -> dict đại diện

    for r in rows:
        key = get_place_key(r)
        if key is None:
            continue

        if key not in merged:
            # Khởi tạo đại diện
            merged[key] = dict(r)
            # Chuyển các trường số về dạng số để sau này cộng dồn
            merged[key]["mention_count"] = safe_int(r.get("mention_count", 0))
            merged[key]["total_digg"] = safe_int(r.get("total_digg", 0))
            merged[key]["total_plays"] = safe_int(r.get("total_plays", 0))
            merged[key]["confidence_high"] = safe_int(r.get("confidence_high", 0))
            # Gộp video_urls và aliases
            if r.get("video_urls"):
                merged[key]["video_urls"] = r["video_urls"]
            if r.get("aliases"):
                merged[key]["aliases"] = r["aliases"]
        else:
            # Cộng dồn các chỉ số TikTok
            merged[key]["mention_count"] += safe_int(r.get("mention_count", 0))
            merged[key]["total_digg"] += safe_int(r.get("total_digg", 0))
            merged[key]["total_plays"] += safe_int(r.get("total_plays", 0))
            merged[key]["confidence_high"] += safe_int(r.get("confidence_high", 0))

            # Gộp video URLs: ưu tiên video của place_name chính xác
            # (không nối tất cả, tránh lấy video "quán cafe gần X" cho địa điểm X)
            new_urls = r.get("video_urls", "")
            if new_urls:
                existing = merged[key].get("video_urls", "")
                canonical_name = merged[key].get("place_name", "").lower()
                # Chỉ thêm URL nếu place_name của dòng mới khớp tên chuẩn
                row_name = r.get("place_name", "").lower()
                # Token match: tên dòng mới phải có ít nhất 1 token chính của tên chuẩn
                canonical_tokens = set(t for t in canonical_name.split() if len(t) >= 3)
                row_tokens = set(t for t in row_name.split() if len(t) >= 3)
                if canonical_tokens & row_tokens:  # có token chung
                    if new_urls not in existing:
                        merged[key]["video_urls"] = existing + " | " + new_urls if existing else new_urls

            # Nối aliases
            new_alias = r.get("aliases", "")
            if new_alias:
                existing_alias = merged[key].get("aliases", "")
                if new_alias not in existing_alias:
                    merged[key]["aliases"] = existing_alias + ", " + new_alias if existing_alias else new_alias

            # Nếu dòng mới có gmaps_rating cao hơn hoặc nhiều reviews hơn, ưu tiên cập nhật các trường Maps
            old_rating = safe_float(merged[key].get("gmaps_rating", 0))
            old_reviews = safe_int(merged[key].get("gmaps_reviews_count", 0))
            new_rating = safe_float(r.get("gmaps_rating", 0))
            new_reviews = safe_int(r.get("gmaps_reviews_count", 0))
            if new_rating > old_rating or (new_rating == old_rating and new_reviews > old_reviews):
                # Cập nhật các trường liên quan đến Google Maps
                for field in ["gmaps_name", "gmaps_address", "lat", "lng",
                              "gmaps_rating", "gmaps_reviews_count",
                              "gmaps_price_level", "gmaps_phone", "gmaps_website",
                              "gmaps_status", "opening_hours_text",
                              "open_time", "close_time", "fuzzy_score"]:
                    if field in r:
                        merged[key][field] = r[field]

    # Chuyển các trường số về string để đồng bộ với CSV (vẫn giữ nguyên giá trị số)
    for rec in merged.values():
        rec["mention_count"] = str(rec["mention_count"])
        rec["total_digg"] = str(rec["total_digg"])
        rec["total_plays"] = str(rec["total_plays"])
        rec["confidence_high"] = str(rec["confidence_high"])

    return list(merged.values())

# ══════════════════════════════════════════════════════════════
# BƯỚC 2 — TÍNH RAW SCORES
# ══════════════════════════════════════════════════════════════

def compute_tiktok_raw(row):
    mentions = safe_int(row.get("mention_count", 0))
    digg     = safe_int(row.get("total_digg", 0))
    plays    = safe_int(row.get("total_plays", 0))
    conf     = safe_int(row.get("confidence_high", 0))
    return (
        mentions * 10
        + digg   * 0.001
        + plays  * 0.0001
        + conf   * 5
    )

def compute_gmaps_raw(row):
    rating  = safe_float(row.get("gmaps_rating", 0))
    reviews = safe_int(row.get("gmaps_reviews_count", 0))
    if rating == 0:
        return 0.0
    return rating * math.log(reviews + 1)

# ══════════════════════════════════════════════════════════════
# BƯỚC 3 — TÍNH ATTRACTION SCORE & CHUẨN BỊ CHO ROUTE
# ══════════════════════════════════════════════════════════════

def compute_final_scores(pois):
    # Tính raw
    tiktok_raws = [compute_tiktok_raw(p) for p in pois]
    gmaps_raws  = [compute_gmaps_raw(p)  for p in pois]

    # Normalize
    tiktok_norm = normalize_minmax(tiktok_raws)
    gmaps_norm  = normalize_minmax(gmaps_raws)

    results = []
    for i, p in enumerate(pois):
        ts = tiktok_norm[i]
        gs = gmaps_norm[i]

        # Nếu không có Google rating, chỉ dùng TikTok
        if safe_float(p.get("gmaps_rating", 0)) == 0 or safe_int(p.get("gmaps_reviews_count", 0)) == 0:
            attraction_score = ts
        else:
            attraction_score = W_TIKTOK * ts + W_GMAPS * gs

        # Xử lý giờ mở cửa
        open_min = parse_time(p.get("open_time", ""))
        close_min = parse_time(p.get("close_time", ""))
        open_min, close_min = fix_overnight_time(open_min, close_min)

        # Thời gian tham quan
        poi_type = p.get("type", "khác").strip().lower()
        visit_min = DEFAULT_VISIT_DURATION.get(poi_type, 45)
        include_in_route = visit_min > 0

        row = {
            **p,
            "tiktok_raw":        round(tiktok_raws[i], 2),
            "gmaps_raw":         round(gmaps_raws[i],  2),
            "tiktok_score_norm": round(ts, 4),
            "gmaps_score_norm":  round(gs, 4),
            "attraction_score":  round(attraction_score, 4),
            "open_min":          open_min if open_min is not None else "",
            "close_min":         close_min if close_min is not None else "",
            "visit_duration_min": visit_min,
            "include_in_route":  include_in_route,
        }
        results.append(row)

    # Sắp xếp theo attraction_score giảm dần
    return sorted(results, key=lambda x: x["attraction_score"], reverse=True)

# ══════════════════════════════════════════════════════════════
# BƯỚC 4 — LƯU CSV & IN KẾT QUẢ
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
    print("\n" + "="*60)
    print("  GIAI ĐOẠN 4 — Tính Attraction Score (Đã gộp trùng lặp)")
    print(f"  Trọng số: TikTok={W_TIKTOK} | GMaps={W_GMAPS}")
    print("="*60)

    # Load & merge
    print("\n[1/4] Đọc và gộp POI trùng lặp...")
    pois = load_and_merge(INPUT_CSV)
    print(f"  Tổng POI sau gộp: {len(pois)}")

    # Tính điểm
    print("\n[2/4] Tính attraction score...")
    scored = compute_final_scores(pois)

    # ── ABLATION TEST trọng số ──────────────────────────────────
    print("\n[2b/4] Ablation test 3 cấu hình trọng số:")
    weight_configs = [
        (0.3, 0.7, "GMaps-heavy"),
        (0.5, 0.5, "Balanced"),
        (0.6, 0.4, "TikTok-heavy (current)"),
    ]
    for wt, wg, label in weight_configs:
        # Tạm thời override weights để tính
        tiktok_raws = [compute_tiktok_raw(p) for p in pois]
        gmaps_raws  = [compute_gmaps_raw(p)  for p in pois]
        tn = normalize_minmax(tiktok_raws)
        gn = normalize_minmax(gmaps_raws)
        test_scores = []
        for i, p in enumerate(pois):
            if safe_float(p.get("gmaps_rating", 0)) == 0:
                s = tn[i]
            else:
                s = wt * tn[i] + wg * gn[i]
            test_scores.append((p["place_name"], round(s, 4)))
        test_scores.sort(key=lambda x: -x[1])
        top5 = ", ".join(f"{n[:20]}({s})" for n, s in test_scores[:5])
        print(f"    [{label:<25}] Top-5: {top5}")
    print()
    # ────────────────────────────────────────────────────────────

    # Lọc POI đưa vào route
    route_pois = [p for p in scored if p["include_in_route"]]
    print(f"  POI đưa vào route: {len(route_pois)}/{len(scored)}")

    # Lưu
    print("\n[3/4] Lưu kết quả...")
    save_csv(scored, OUTPUT_CSV)

    # In top 20
    print("\n[4/4] Top 20 địa điểm theo Attraction Score:")
    print(f"  {'#':<3} {'Tên':<35} {'Type':<20} {'TikTok':>7} {'GMaps':>6} {'Score':>6} {'Giờ mở cửa'}")
    print("  " + "-"*95)
    for i, p in enumerate(scored[:20], 1):
        hours = ""
        if str(p["open_min"]) != "" and str(p["close_min"]) != "":
            oh = int(p["open_min"]) // 60
            om = int(p["open_min"]) % 60
            ch = int(p["close_min"]) // 60
            cm = int(p["close_min"]) % 60
            hours = f"{oh:02d}:{om:02d}-{ch:02d}:{cm:02d}"
        print(f"  {i:<3} {p['place_name'][:34]:<35} {p['type'][:19]:<20} "
              f"{p['tiktok_score_norm']:>7.3f} {p['gmaps_score_norm']:>6.3f} "
              f"{p['attraction_score']:>6.3f} {hours}")

    # Phân bố theo type
    print(f"\n  Phân bố POI trong route theo type:")
    type_counts = defaultdict(int)
    for p in route_pois:
        type_counts[p["type"]] += 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t:<25} {c} POI")

    print("\n" + "="*60)
    print("  HOÀN THÀNH")
    print(f"  File output: {OUTPUT_CSV}")
    print("  Bước tiếp theo: chạy route_optimizer.py")
    print("="*60)

if __name__ == "__main__":
    main()