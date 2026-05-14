"""
=============================================================
GIAI ĐOẠN 5 — Tối ưu lộ trình du lịch Đà Lạt (cải tiến)
=============================================================
SỬA LỖI:
  1. SA 2 phase: phase 1 tối ưu feasibility, phase 2 tối ưu km với feasibility làm hard constraint
  2. Dedup sau gmaps_join dựa trên gmaps_place_id (fallback tọa độ)
  3. K-Means 3D (lat, lng, open_min) để phân cụm theo cả thời gian mở cửa
=============================================================
"""

import csv, math, random, time, os, copy
import numpy as np

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH NGƯỜI DÙNG
# ══════════════════════════════════════════════════════════════

INPUT_CSV   = "dalat_poi_scored.csv"
NUM_DAYS    = 3
TOP_K       = 40

# ── ANCHOR POI ──────────────────────────────────────────────
ANCHOR_POIS: list[str] = []
# ────────────────────────────────────────────────────────────

USER_START  = 7 * 60    # 07:00
USER_END    = 21 * 60   # 21:00
AVG_SPEED_KMH = 30

# SA params (phase 1 và 2)
SA_T0        = 500.0
SA_ALPHA     = 0.9995
SA_MAX_ITER  = 100_000
SA_MIN_T     = 0.01

# Trọng số cho phase 1 (chỉ quan tâm feasibility)
INFEASIBLE_PENALTY      = 500.0
BALANCE_PENALTY_WEIGHT  = 200.0   # cân bằng số điểm khả thi giữa các ngày

# Các tham số cho phase 2 (km, meal, diversity)
MAX_KM_PER_DAY       = 70.0
OVER_KM_PENALTY      = 5.0
MEAL_WINDOWS = [(11*60+30, 13*60), (18*60, 19*60+30)]
MEAL_BONUS = 100.0
TYPE_DIVERSITY_WEIGHT = 150.0
FOOD_TYPES = {"chợ quán", "nhà hàng", "quán ăn", "ăn_uống"}

# Giờ mở cửa mặc định theo loại POI
DEFAULT_HOURS_BY_TYPE = {
    "thiên nhiên":      (7*60,  18*60),
    "địa điểm checkin": (6*60,  20*60),
    "cafe":             (7*60,  22*60),
    "nhà hàng":         (10*60, 22*60),
    "chợ quán":         (6*60,  22*60),
    "khác":             (7*60,  21*60),
}

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    try:
        return float(val) if str(val) not in ("", "nan", "None") else default
    except:
        return default

def safe_int(val, default=0):
    try:
        return int(float(val)) if str(val) not in ("", "nan", "None") else default
    except:
        return default

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def travel_minutes(lat1, lng1, lat2, lng2):
    km = haversine_km(lat1, lng1, lat2, lng2)
    return (km / AVG_SPEED_KMH) * 60

def fmt_min(minutes):
    if minutes is None:
        return "?"
    h = int(minutes) // 60 % 24
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"

DALAT_LAT = 11.9404
DALAT_LNG = 108.4583

# ══════════════════════════════════════════════════════════════
# VISIT DURATION — Infer thông minh
# ══════════════════════════════════════════════════════════════

_BASE_DURATION = {
    "cafe":              55,
    "nhà hàng":          65,
    "chợ quán":          40,
    "địa điểm checkin":  25,
    "thiên nhiên":       85,
    "quán ăn":           50,
    "khác":              40,
}
_PRICE_ADJUST = {0: -5, 1: 0, 2: 5, 3: 15, 4: 20}

def infer_visit_duration(poi_type: str, price_level, reviews_count, csv_value) -> int:
    csv_int = safe_int(csv_value, 0)
    if csv_int > 0 and csv_int != 45:
        return csv_int
    base = _BASE_DURATION.get(poi_type.strip().lower(), 40)
    pl = safe_int(price_level, 1)
    base += _PRICE_ADJUST.get(pl, 0)
    reviews = safe_int(reviews_count, 0)
    if reviews > 5000:   base += 15
    elif reviews > 1000: base += 8
    elif reviews > 500:  base += 3
    elif reviews < 50:   base -= 5
    return max(15, base)

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — ĐỌC & CHUẨN BỊ DATA (FIX DEDUP THEO GMAPS_PLACE_ID)
# ══════════════════════════════════════════════════════════════

def load_pois(filepath, top_k, num_days):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    pois = []
    seen_ids = set()          # lưu gmaps_place_id đã gặp
    seen_coords = set()       # fallback nếu không có place_id

    for r in rows:
        if str(r.get("include_in_route", "")).strip().lower() not in ("true", "1"):
            continue

        lat = safe_float(r.get("lat"))
        lng = safe_float(r.get("lng"))
        if lat == 0 or lng == 0:
            continue

        # Ưu tiên dedup theo gmaps_place_id
        place_id = r.get("gmaps_place_id", "").strip()
        if place_id:
            if place_id in seen_ids:
                continue
            seen_ids.add(place_id)
        else:
            # fallback: dùng tọa độ (3 số thập phân)
            coord_key = (round(lat, 3), round(lng, 3))
            if coord_key in seen_coords:
                continue
            seen_coords.add(coord_key)

        open_min_raw  = r.get("open_min", "")
        close_min_raw = r.get("close_min", "")

        open_min  = safe_float(open_min_raw)  if open_min_raw  not in ("", "nan", "None") else 0
        close_min = safe_float(close_min_raw) if close_min_raw not in ("", "nan", "None") else 24 * 60

        if close_min_raw not in ("", "nan", "None") and close_min == 0 and open_min > 0:
            close_min = 24 * 60

        poi_type = r.get("type", "khác").strip().lower()
        if open_min_raw in ("", "nan", "None") or close_min_raw in ("", "nan", "None"):
            default_open, default_close = DEFAULT_HOURS_BY_TYPE.get(
                poi_type, DEFAULT_HOURS_BY_TYPE["khác"]
            )
            if open_min_raw  in ("", "nan", "None"): open_min  = default_open
            if close_min_raw in ("", "nan", "None"): close_min = default_close

        pois.append({
            "name":             r["place_name"],
            "type":             r.get("type", ""),
            "lat":              lat,
            "lng":              lng,
            "attraction_score": safe_float(r.get("attraction_score")),
            "open_min":         open_min,
            "close_min":        close_min,
            "visit_min":        infer_visit_duration(
                                    r.get("type", "khác"),
                                    r.get("gmaps_price_level", ""),
                                    r.get("gmaps_reviews_count", ""),
                                    r.get("visit_duration_min", 45),
                                ),
            "rating":           safe_float(r.get("gmaps_rating")),
            "reviews":          safe_int(r.get("gmaps_reviews_count", 0)),
            "address":          r.get("gmaps_address", ""),
            "video_url":        r.get("video_urls", ""),
            "anchor":           False,
        })

    pois.sort(key=lambda x: x["attraction_score"], reverse=True)
    k = max(top_k, num_days * 5)

    anchor_names_lower = [a.lower().strip() for a in ANCHOR_POIS]
    for p in pois:
        p["anchor"] = any(a in p["name"].lower() for a in anchor_names_lower)

    anchors   = [p for p in pois if p["anchor"]]
    non_anch  = [p for p in pois if not p["anchor"]]
    selected  = anchors + non_anch[:max(0, k - len(anchors))]
    selected  = selected[:k]

    if anchors:
        print(f"  Anchor POI đã chọn ({len(anchors)}): {[a['name'] for a in anchors]}")
    else:
        print(f"  Không có anchor POI — chạy lộ trình tự động")

    print(f"  Sau dedup (place_id) và Top-{k}: {len(selected)} POI")
    return selected

# ══════════════════════════════════════════════════════════════
# KIỂM TRA RÀNG BUỘC & SIMULATE MỘT NGÀY
# ══════════════════════════════════════════════════════════════

def is_feasible(poi, arrive_time):
    start = max(arrive_time, poi["open_min"])
    end = start + poi["visit_min"]
    if end > poi["close_min"] or end > USER_END:
        return False, start, end
    return True, start, end

def simulate_day(poi_list):
    current_time = USER_START
    current_lat  = poi_list[0]["lat"] if poi_list else DALAT_LAT
    current_lng  = poi_list[0]["lng"] if poi_list else DALAT_LNG
    total_km     = 0.0
    feasible     = 0
    timeline     = []

    for poi in poi_list:
        travel_min = travel_minutes(current_lat, current_lng, poi["lat"], poi["lng"])
        arrive     = current_time + travel_min
        ok, start, end = is_feasible(poi, arrive)

        km = haversine_km(current_lat, current_lng, poi["lat"], poi["lng"])
        total_km += km

        timeline.append({
            "name": poi["name"], "type": poi["type"],
            "arrive": arrive, "start": start, "end": end,
            "feasible": ok, "km": km, "rating": poi["rating"],
            "score": poi["attraction_score"], "address": poi["address"],
            "video_url": poi["video_url"], "close_min": poi["close_min"],
        })

        if ok:
            feasible += 1
            current_time = end
        else:
            current_time = arrive
        current_lat = poi["lat"]
        current_lng = poi["lng"]

    return total_km, feasible, timeline

# Hàm chỉ trả về số lượng infeasible (dùng cho phase 1)
def count_infeasible(itinerary):
    total_infeas = 0
    for day_pois in itinerary:
        _, feas, _ = simulate_day(day_pois)
        total_infeas += len(day_pois) - feas
    return total_infeas

# ══════════════════════════════════════════════════════════════
# K-MEANS 3D (lat, lng, open_min) — FIX LỖI #3
# ══════════════════════════════════════════════════════════════

def kmeans_cluster_3d(pois, num_clusters, max_iter=50):
    """Phân cụm dựa trên lat, lng, open_min (đã normalize)"""
    n = len(pois)
    if n <= num_clusters:
        return [[i] for i in range(n)]

    # Lấy dữ liệu
    lat_vals = np.array([p["lat"] for p in pois])
    lng_vals = np.array([p["lng"] for p in pois])
    open_vals = np.array([p["open_min"] for p in pois])

    # Normalize từng chiều về [0,1]
    lat_min, lat_max = lat_vals.min(), lat_vals.max()
    lng_min, lng_max = lng_vals.min(), lng_vals.max()
    open_min_all, open_max_all = 0.0, 24*60.0

    def norm_lat(l): return (l - lat_min) / (lat_max - lat_min + 1e-9)
    def norm_lng(l): return (l - lng_min) / (lng_max - lng_min + 1e-9)
    def norm_open(o): return o / (open_max_all + 1e-9)

    X = np.column_stack([
        norm_lat(lat_vals),
        norm_lng(lng_vals),
        norm_open(open_vals)
    ])

    # Khởi tạo tâm ngẫu nhiên
    rng = np.random.default_rng(42)
    indices = rng.choice(n, size=num_clusters, replace=False)
    centroids = X[indices].copy()

    for _ in range(max_iter):
        distances = np.linalg.norm(X[:, np.newaxis] - centroids, axis=2)
        labels = np.argmin(distances, axis=1)
        new_centroids = np.array([X[labels == k].mean(axis=0) for k in range(num_clusters)])
        if np.allclose(centroids, new_centroids):
            break
        centroids = new_centroids

    clusters = [[] for _ in range(num_clusters)]
    for i, label in enumerate(labels):
        clusters[label].append(i)
    return clusters

# ══════════════════════════════════════════════════════════════
# GREEDY NỘI BỘ CHO MỘT CỤM (giữ nguyên)
# ══════════════════════════════════════════════════════════════

def greedy_day(poi_list):
    if not poi_list:
        return []
    unvisited = list(poi_list)
    route = []
    cur_lat, cur_lng = DALAT_LAT, DALAT_LNG
    cur_time = USER_START

    while unvisited:
        best = None
        best_val = -1
        for p in unvisited:
            travel_min = travel_minutes(cur_lat, cur_lng, p["lat"], p["lng"])
            arrive = cur_time + travel_min
            ok, _, _ = is_feasible(p, arrive)
            if not ok:
                continue
            dist_km = haversine_km(cur_lat, cur_lng, p["lat"], p["lng"])
            val = p["attraction_score"] / (dist_km + 0.1)
            if val > best_val:
                best_val = val
                best = p
        if best is None:
            best = min(unvisited, key=lambda p: haversine_km(cur_lat, cur_lng, p["lat"], p["lng"]))
        route.append(best)
        travel_min = travel_minutes(cur_lat, cur_lng, best["lat"], best["lng"])
        arrive = cur_time + travel_min
        _, start, end = is_feasible(best, arrive)
        cur_time = end if end <= USER_END else arrive
        cur_lat, cur_lng = best["lat"], best["lng"]
        unvisited.remove(best)
    return route

def initial_itinerary(pois, num_days):
    clusters = kmeans_cluster_3d(pois, num_days)   # dùng K-Means 3D
    itinerary = []
    for cluster_indices in clusters:
        day_pois = [pois[i] for i in cluster_indices]
        sorted_day = greedy_day(day_pois)
        itinerary.append(sorted_day)
    return itinerary

# ══════════════════════════════════════════════════════════════
# COST FUNCTION — PHASE 1 (chỉ feasibility)
# ══════════════════════════════════════════════════════════════

def cost_phase1(itinerary):
    total_infeas = 0
    feasible_counts = []
    for day_pois in itinerary:
        _, feas, _ = simulate_day(day_pois)
        feasible_counts.append(feas)
        total_infeas += len(day_pois) - feas
    std_feas = np.std(feasible_counts) if len(feasible_counts) > 1 else 0.0
    return total_infeas * INFEASIBLE_PENALTY + std_feas * BALANCE_PENALTY_WEIGHT

# ══════════════════════════════════════════════════════════════
# COST FUNCTION — PHASE 2 (km, meal, diversity, nhưng chỉ áp dụng nếu feasibility không đổi)
# ══════════════════════════════════════════════════════════════

def cost_phase2(itinerary):
    total_km = 0.0
    meal_bonus_total = 0.0
    type_diversity_total = 0.0
    over_km_penalty = 0.0

    for day_pois in itinerary:
        km, feas, timeline = simulate_day(day_pois)
        total_km += km
        if km > MAX_KM_PER_DAY:
            over_km_penalty += (km - MAX_KM_PER_DAY) * OVER_KM_PENALTY

        for stop in timeline:
            if stop["feasible"] and stop["type"] in FOOD_TYPES:
                start_mins = stop["start"]
                for (w_start, w_end) in MEAL_WINDOWS:
                    if w_start <= start_mins <= w_end:
                        meal_bonus_total += MEAL_BONUS
                        break
        # Entropy cho diversity
        type_counts = {}
        for poi in day_pois:
            t = poi["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        total_points = len(day_pois)
        entropy = 0.0
        if total_points > 0:
            for count in type_counts.values():
                p = count / total_points
                entropy -= p * math.log(p + 1e-9)
        type_diversity_total += entropy

    cost = total_km * 2.5 + over_km_penalty - meal_bonus_total - TYPE_DIVERSITY_WEIGHT * type_diversity_total
    return cost

# ══════════════════════════════════════════════════════════════
# SIMULATED ANNEALING — PHASE 1 (chỉ feasibility)
# ══════════════════════════════════════════════════════════════

def sa_phase1(initial_itinerary):
    current = copy.deepcopy(initial_itinerary)
    best = copy.deepcopy(current)
    cur_cost = cost_phase1(current)
    best_cost = cur_cost
    T = SA_T0
    t0 = time.time()

    for iteration in range(SA_MAX_ITER):
        if T < SA_MIN_T:
            break
        op = random.random()
        new_itinerary = copy.deepcopy(current)

        if op < 0.4:
            day_idx = random.randrange(NUM_DAYS)
            if len(new_itinerary[day_idx]) >= 2:
                i, j = random.sample(range(len(new_itinerary[day_idx])), 2)
                new_itinerary[day_idx][i], new_itinerary[day_idx][j] = new_itinerary[day_idx][j], new_itinerary[day_idx][i]
        elif op < 0.8:
            src_day, dst_day = random.sample(range(NUM_DAYS), 2)
            if len(new_itinerary[src_day]) > 1:
                idx = random.randrange(len(new_itinerary[src_day]))
                poi = new_itinerary[src_day][idx]
                if poi.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new_itinerary[src_day].pop(idx)
                insert_pos = random.randint(0, len(new_itinerary[dst_day]))
                new_itinerary[dst_day].insert(insert_pos, poi)
        else:
            day1, day2 = random.sample(range(NUM_DAYS), 2)
            if new_itinerary[day1] and new_itinerary[day2]:
                i = random.randrange(len(new_itinerary[day1]))
                j = random.randrange(len(new_itinerary[day2]))
                p1 = new_itinerary[day1][i]
                p2 = new_itinerary[day2][j]
                if p1.get("anchor") or p2.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new_itinerary[day1][i], new_itinerary[day2][j] = new_itinerary[day2][j], new_itinerary[day1][i]

        new_cost = cost_phase1(new_itinerary)
        delta = new_cost - cur_cost
        if delta < 0 or random.random() < math.exp(-delta / T):
            current = new_itinerary
            cur_cost = new_cost
            if cur_cost < best_cost:
                best = copy.deepcopy(current)
                best_cost = cur_cost
        T *= SA_ALPHA

        if (iteration + 1) % 20000 == 0:
            elapsed = round(time.time() - t0, 1)
            infeas = count_infeasible(current)
            print(f"    Phase1 iter {iteration+1:>6} | T={T:.4f} | infeas={infeas} | best_infeas={count_infeasible(best)} | {elapsed}s")
    return best, best_cost

# ══════════════════════════════════════════════════════════════
# SIMULATED ANNEALING — PHASE 2 (tối ưu km, giữ nguyên feasibility)
# ══════════════════════════════════════════════════════════════

def sa_phase2(initial_itinerary, target_infeasible):
    """Chỉ chấp nhận move nếu số infeasible không tăng so với target"""
    current = copy.deepcopy(initial_itinerary)
    best = copy.deepcopy(current)
    cur_cost = cost_phase2(current)
    best_cost = cur_cost
    T = SA_T0
    t0 = time.time()

    for iteration in range(SA_MAX_ITER):
        if T < SA_MIN_T:
            break
        op = random.random()
        new_itinerary = copy.deepcopy(current)

        if op < 0.4:
            day_idx = random.randrange(NUM_DAYS)
            if len(new_itinerary[day_idx]) >= 2:
                i, j = random.sample(range(len(new_itinerary[day_idx])), 2)
                new_itinerary[day_idx][i], new_itinerary[day_idx][j] = new_itinerary[day_idx][j], new_itinerary[day_idx][i]
        elif op < 0.8:
            src_day, dst_day = random.sample(range(NUM_DAYS), 2)
            if len(new_itinerary[src_day]) > 1:
                idx = random.randrange(len(new_itinerary[src_day]))
                poi = new_itinerary[src_day][idx]
                if poi.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new_itinerary[src_day].pop(idx)
                insert_pos = random.randint(0, len(new_itinerary[dst_day]))
                new_itinerary[dst_day].insert(insert_pos, poi)
        else:
            day1, day2 = random.sample(range(NUM_DAYS), 2)
            if new_itinerary[day1] and new_itinerary[day2]:
                i = random.randrange(len(new_itinerary[day1]))
                j = random.randrange(len(new_itinerary[day2]))
                p1 = new_itinerary[day1][i]
                p2 = new_itinerary[day2][j]
                if p1.get("anchor") or p2.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new_itinerary[day1][i], new_itinerary[day2][j] = new_itinerary[day2][j], new_itinerary[day1][i]

        # Hard constraint: không được làm tăng số infeasible
        new_infeas = count_infeasible(new_itinerary)
        if new_infeas > target_infeasible:
            T *= SA_ALPHA
            continue

        new_cost = cost_phase2(new_itinerary)
        delta = new_cost - cur_cost
        if delta < 0 or random.random() < math.exp(-delta / T):
            current = new_itinerary
            cur_cost = new_cost
            if cur_cost < best_cost:
                best = copy.deepcopy(current)
                best_cost = cur_cost
        T *= SA_ALPHA

        if (iteration + 1) % 20000 == 0:
            elapsed = round(time.time() - t0, 1)
            print(f"    Phase2 iter {iteration+1:>6} | T={T:.4f} | cost={cur_cost:.1f} | best={best_cost:.1f} | {elapsed}s")
    return best, best_cost

# ══════════════════════════════════════════════════════════════
# LƯU KẾT QUẢ & IN
# ══════════════════════════════════════════════════════════════

def save_route(itinerary, filepath, method="SA"):
    rows = []
    total_km_all = 0.0
    total_feas_all = 0
    total_stop_all = 0

    for d, day_pois in enumerate(itinerary, 1):
        km, feas, timeline = simulate_day(day_pois)
        total_km_all += km
        total_feas_all += feas
        total_stop_all += len(day_pois)

        timeline_sorted = sorted(timeline, key=lambda x: x["start"])
        for stop_idx, stop in enumerate(timeline_sorted, 1):
            rows.append({
                "day": d, "stop": stop_idx, "method": method,
                "name": stop["name"], "type": stop["type"],
                "arrive": fmt_min(stop["arrive"]),
                "start_visit": fmt_min(stop["start"]),
                "end_visit": fmt_min(stop["end"]),
                "feasible": stop["feasible"],
                "dist_km": stop["km"],
                "rating": stop["rating"],
                "attraction_score": round(stop["score"], 4),
                "address": stop["address"],
                "video_url": stop["video_url"],
            })

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"  [{method}] Tổng km: {total_km_all:.1f} | "
          f"Feasible: {total_feas_all}/{total_stop_all} "
          f"({round(total_feas_all/total_stop_all*100)}%) -> {filepath}")
    return total_km_all, total_feas_all, total_stop_all

def print_route(itinerary, method="SA"):
    print(f"\n  {'='*60}")
    print(f"  LỊCH TRÌNH {NUM_DAYS} NGÀY — {method}")
    print(f"  {'='*60}")
    for d, day_pois in enumerate(itinerary, 1):
        km, feas, timeline = simulate_day(day_pois)
        timeline_sorted = sorted(timeline, key=lambda x: x["start"])
        print(f"\n  📅 NGÀY {d} — {km:.1f}km | {feas}/{len(day_pois)} địa điểm đúng giờ")
        print(f"  {'-'*55}")
        for stop in timeline_sorted:
            status = "✅" if stop["feasible"] else "⚠️"
            print(f"    {status} {fmt_min(stop['start'])}-{fmt_min(stop['end'])}  "
                  f"{stop['name'][:30]:<30}  ({stop['type'][:15]})  ⭐{stop['rating']}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    random.seed(42)
    np.random.seed(42)
    t_start = time.time()

    print("\n" + "="*60)
    print(f"  GIAI ĐOẠN 5 — Route Optimizer ({NUM_DAYS} ngày, Top-{TOP_K})")
    print(f"  Khung giờ: {fmt_min(USER_START)} - {fmt_min(USER_END)}")
    print(f"  SA Phase1: chỉ tối ưu feasibility | Phase2: tối ưu km (feasibility hard)")
    print("="*60)

    print(f"\nBước 1: Load POI (dedup theo gmaps_place_id)...")
    pois = load_pois(INPUT_CSV, TOP_K, NUM_DAYS)

    print(f"\nBước 2: Khởi tạo lịch trình (K-Means 3D + Greedy nội bộ)...")
    init_itin = initial_itinerary(pois, NUM_DAYS)
    km_init, feas_init, stops_init = save_route(init_itin,
        f"dalat_route_greedy_{NUM_DAYS}days.csv", method="Greedy (K-Means 3D)")
    print_route(init_itin, "Greedy (K-Means 3D)")

    print(f"\nBước 3: SA Phase 1 — Tối ưu feasibility...")
    feas_itin, _ = sa_phase1(init_itin)
    target_infeas = count_infeasible(feas_itin)
    print(f"  Phase 1 kết thúc: số điểm không khả thi = {target_infeas}")
    save_route(feas_itin, "dalat_route_phase1.csv", method="SA_Phase1")

    print(f"\nBước 4: SA Phase 2 — Tối ưu km (giữ nguyên feasibility ≤ {target_infeas})...")
    sa_itin, _ = sa_phase2(feas_itin, target_infeas)
    km_sa, feas_sa, stops_sa = save_route(sa_itin,
        f"dalat_route_{NUM_DAYS}days.csv", method="SA_Phase2")
    print_route(sa_itin, "SA (2-phase)")

    # So sánh
    elapsed = round(time.time() - t_start, 1)
    print(f"\n  {'='*60}")
    print(f"  SO SÁNH GREEDY (K-Means 3D) vs SA 2-PHASE")
    print(f"  {'='*60}")
    print(f"  {'Chỉ số':<30} {'Greedy':>10} {'SA 2-phase':>12} {'Cải thiện':>12}")
    print(f"  {'-'*60}")

    km_imp   = round((km_init - km_sa) / km_init * 100, 1) if km_init > 0 else 0
    feas_imp = round((feas_sa - feas_init) / max(stops_init, 1) * 100, 1)

    print(f"  {'Tổng km di chuyển':<30} {km_init:>10.1f} {km_sa:>12.1f} {km_imp:>+11.1f}%")
    print(f"  {'Feasible stops':<30} {feas_init:>10} {feas_sa:>12} {feas_imp:>+11.1f}%")
    print(f"  {'Feasibility rate':<30} {round(feas_init/stops_init*100):>9}% {round(feas_sa/stops_sa*100):>11}%")
    print(f"\n  Thời gian chạy: {elapsed}s")
    print(f"  Output: dalat_route_{NUM_DAYS}days.csv")
    print("="*60)

if __name__ == "__main__":
    main()