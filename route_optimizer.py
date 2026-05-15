"""
=============================================================
GIAI ĐOẠN 5 — Tối ưu lộ trình du lịch Đà Lạt (phiên bản ổn định)
=============================================================
- Dedup theo gmaps_place_id
- K-Means 3D (lat, lng, open_min)
- SA 2-phase: phase1 chỉ feasibility, phase2 hard constraint trên feasibility

INPUT_CSV = 'dalat_poi_scored_fix.csv', sử dụng K-Means 3D (lat,lng,open_min), 
SA 2-phase (phase1 tối ưu feasibility, phase2 hard constraint giữ nguyên infeasible), 
xuất ra dalat_route_3days.csv
=============================================================
"""

import csv, math, random, time, copy
import numpy as np

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV   = "dalat_poi_scored_fix.csv"   # thay vì "dalat_poi_scored.csv"
NUM_DAYS    = 3
TOP_K       = 40
ANCHOR_POIS: list[str] = []

USER_START  = 7 * 60
USER_END    = 21 * 60
AVG_SPEED_KMH = 30

SA_T0        = 500.0
SA_ALPHA     = 0.9995
SA_MAX_ITER  = 100_000
SA_MIN_T     = 0.01

INFEASIBLE_PENALTY      = 500.0
BALANCE_PENALTY_WEIGHT  = 200.0

MAX_KM_PER_DAY       = 70.0
OVER_KM_PENALTY      = 5.0
MEAL_WINDOWS = [(11*60+30, 13*60), (18*60, 19*60+30)]
MEAL_BONUS = 100.0
TYPE_DIVERSITY_WEIGHT = 150.0
FOOD_TYPES = {"chợ quán", "nhà hàng", "quán ăn", "ăn_uống"}

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
# VISIT DURATION
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
# LOAD POI (DEDUP THEO GMAPS_PLACE_ID)
# ══════════════════════════════════════════════════════════════

def load_pois(filepath, top_k, num_days):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    pois = []
    seen_ids = set()
    seen_coords = set()

    for r in rows:
        if str(r.get("include_in_route", "")).strip().lower() not in ("true", "1"):
            continue

        lat = safe_float(r.get("lat"))
        lng = safe_float(r.get("lng"))
        if lat == 0 or lng == 0:
            continue

        place_id = r.get("gmaps_place_id", "").strip()
        if place_id:
            if place_id in seen_ids:
                continue
            seen_ids.add(place_id)
        else:
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
        print(f"  Anchor POI: {[a['name'] for a in anchors]}")
    else:
        print(f"  Không có anchor — chạy tự động")
    print(f"  Chọn {len(selected)} POI (đã dedup)")
    return selected

# ══════════════════════════════════════════════════════════════
# SIMULATE NGÀY
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

def count_infeasible(itinerary):
    total = 0
    for day_pois in itinerary:
        _, feas, _ = simulate_day(day_pois)
        total += len(day_pois) - feas
    return total

# ══════════════════════════════════════════════════════════════
# K-MEANS 3D (lat, lng, open_min)
# ══════════════════════════════════════════════════════════════

def kmeans_cluster_3d(pois, num_clusters, max_iter=50):
    n = len(pois)
    if n <= num_clusters:
        return [[i] for i in range(n)]

    lat_vals = np.array([p["lat"] for p in pois])
    lng_vals = np.array([p["lng"] for p in pois])
    open_vals = np.array([p["open_min"] for p in pois])

    lat_min, lat_max = lat_vals.min(), lat_vals.max()
    lng_min, lng_max = lng_vals.min(), lng_vals.max()
    open_min_all, open_max_all = 0.0, 24*60.0

    def norm_lat(l): return (l - lat_min) / (lat_max - lat_min + 1e-9)
    def norm_lng(l): return (l - lng_min) / (lng_max - lng_min + 1e-9)
    def norm_open(o): return o / (open_max_all + 1e-9)

    X = np.column_stack([norm_lat(lat_vals), norm_lng(lng_vals), norm_open(open_vals)])

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
# GREEDY NỘI BỘ
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
    clusters = kmeans_cluster_3d(pois, num_days)
    itinerary = []
    for idxs in clusters:
        day_pois = [pois[i] for i in idxs]
        itinerary.append(greedy_day(day_pois))
    return itinerary

# ══════════════════════════════════════════════════════════════
# COST FUNCTIONS
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

def cost_phase2(itinerary):
    total_km = 0.0
    meal_bonus = 0.0
    type_div = 0.0
    over_km = 0.0

    for day_pois in itinerary:
        km, feas, timeline = simulate_day(day_pois)
        total_km += km
        if km > MAX_KM_PER_DAY:
            over_km += (km - MAX_KM_PER_DAY) * OVER_KM_PENALTY
        for stop in timeline:
            if stop["feasible"] and stop["type"] in FOOD_TYPES:
                start_mins = stop["start"]
                for ws, we in MEAL_WINDOWS:
                    if ws <= start_mins <= we:
                        meal_bonus += MEAL_BONUS
                        break
        type_counts = {}
        for poi in day_pois:
            t = poi["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        entropy = 0.0
        if day_pois:
            for cnt in type_counts.values():
                p = cnt / len(day_pois)
                entropy -= p * math.log(p + 1e-9)
        type_div += entropy

    return total_km * 2.5 + over_km - meal_bonus - TYPE_DIVERSITY_WEIGHT * type_div

# ══════════════════════════════════════════════════════════════
# SA PHASE 1 (chỉ feasibility)
# ══════════════════════════════════════════════════════════════

def sa_phase1(init_itin):
    current = copy.deepcopy(init_itin)
    best = copy.deepcopy(current)
    cur_cost = cost_phase1(current)
    best_cost = cur_cost
    T = SA_T0
    t0 = time.time()

    for it in range(SA_MAX_ITER):
        if T < SA_MIN_T:
            break
        op = random.random()
        new = copy.deepcopy(current)

        if op < 0.4:   # swap nội bộ
            d = random.randrange(NUM_DAYS)
            if len(new[d]) >= 2:
                i, j = random.sample(range(len(new[d])), 2)
                new[d][i], new[d][j] = new[d][j], new[d][i]
        elif op < 0.8: # move
            src, dst = random.sample(range(NUM_DAYS), 2)
            if len(new[src]) > 1:
                idx = random.randrange(len(new[src]))
                poi = new[src][idx]
                if poi.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new[src].pop(idx)
                new[dst].insert(random.randint(0, len(new[dst])), poi)
        else:          # cross swap
            d1, d2 = random.sample(range(NUM_DAYS), 2)
            if new[d1] and new[d2]:
                i = random.randrange(len(new[d1]))
                j = random.randrange(len(new[d2]))
                p1 = new[d1][i]
                p2 = new[d2][j]
                if p1.get("anchor") or p2.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new[d1][i], new[d2][j] = new[d2][j], new[d1][i]

        new_cost = cost_phase1(new)
        delta = new_cost - cur_cost
        if delta < 0 or random.random() < math.exp(-delta / T):
            current = new
            cur_cost = new_cost
            if cur_cost < best_cost:
                best = copy.deepcopy(current)
                best_cost = cur_cost
        T *= SA_ALPHA

        if (it+1) % 20000 == 0:
            print(f"    Phase1 {it+1:6d} | T={T:.4f} | infeas={count_infeasible(current)} | best={count_infeasible(best)} | {time.time()-t0:.1f}s")
    return best, best_cost

# ══════════════════════════════════════════════════════════════
# SA PHASE 2 (hard constraint: không tăng infeasible)
# ══════════════════════════════════════════════════════════════

def sa_phase2(init_itin, target_infeas):
    current = copy.deepcopy(init_itin)
    best = copy.deepcopy(current)
    cur_cost = cost_phase2(current)
    best_cost = cur_cost
    T = SA_T0
    t0 = time.time()

    for it in range(SA_MAX_ITER):
        if T < SA_MIN_T:
            break
        op = random.random()
        new = copy.deepcopy(current)

        if op < 0.4:
            d = random.randrange(NUM_DAYS)
            if len(new[d]) >= 2:
                i, j = random.sample(range(len(new[d])), 2)
                new[d][i], new[d][j] = new[d][j], new[d][i]
        elif op < 0.8:
            src, dst = random.sample(range(NUM_DAYS), 2)
            if len(new[src]) > 1:
                idx = random.randrange(len(new[src]))
                poi = new[src][idx]
                if poi.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new[src].pop(idx)
                new[dst].insert(random.randint(0, len(new[dst])), poi)
        else:
            d1, d2 = random.sample(range(NUM_DAYS), 2)
            if new[d1] and new[d2]:
                i = random.randrange(len(new[d1]))
                j = random.randrange(len(new[d2]))
                p1 = new[d1][i]
                p2 = new[d2][j]
                if p1.get("anchor") or p2.get("anchor"):
                    T *= SA_ALPHA
                    continue
                new[d1][i], new[d2][j] = new[d2][j], new[d1][i]

        if count_infeasible(new) > target_infeas:
            T *= SA_ALPHA
            continue

        new_cost = cost_phase2(new)
        delta = new_cost - cur_cost
        if delta < 0 or random.random() < math.exp(-delta / T):
            current = new
            cur_cost = new_cost
            if cur_cost < best_cost:
                best = copy.deepcopy(current)
                best_cost = cur_cost
        T *= SA_ALPHA

        if (it+1) % 20000 == 0:
            print(f"    Phase2 {it+1:6d} | T={T:.4f} | cost={cur_cost:.1f} | best={best_cost:.1f} | {time.time()-t0:.1f}s")
    return best, best_cost

# ══════════════════════════════════════════════════════════════
# LƯU VÀ IN KẾT QUẢ
# ══════════════════════════════════════════════════════════════

def save_route(itinerary, filepath, method):
    rows = []
    total_km = 0.0
    total_feas = 0
    total_stops = 0
    for d, day in enumerate(itinerary, 1):
        km, feas, timeline = simulate_day(day)
        total_km += km
        total_feas += feas
        total_stops += len(day)
        for s, stop in enumerate(sorted(timeline, key=lambda x: x["start"]), 1):
            rows.append({
                "day": d, "stop": s, "method": method,
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
    print(f"  [{method}] Km={total_km:.1f} | Feasible={total_feas}/{total_stops} ({round(100*total_feas/total_stops)}%) -> {filepath}")
    return total_km, total_feas, total_stops

def print_route(itinerary, title):
    print(f"\n  {'='*60}")
    print(f"  LỊCH TRÌNH {NUM_DAYS} NGÀY — {title}")
    print(f"  {'='*60}")
    for d, day in enumerate(itinerary, 1):
        km, feas, timeline = simulate_day(day)
        timeline_sorted = sorted(timeline, key=lambda x: x["start"])
        print(f"\n  📅 NGÀY {d} — {km:.1f}km | {feas}/{len(day)} feasible")
        print(f"  {'-'*55}")
        for stop in timeline_sorted:
            status = "✅" if stop["feasible"] else "⚠️"
            print(f"    {status} {fmt_min(stop['start'])}-{fmt_min(stop['end'])}  {stop['name'][:30]:<30} ({stop['type'][:12]}) ⭐{stop['rating']}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    random.seed(42)
    np.random.seed(42)
    t_start = time.time()

    print("\n" + "="*60)
    print(f"  ROUTE OPTIMIZER — {NUM_DAYS} ngày, Top {TOP_K} POI")
    print(f"  Giờ: {fmt_min(USER_START)} - {fmt_min(USER_END)}")
    print(f"  SA 2-phase (hard constraint)")
    print("="*60)

    print("\nBước 1: Load POI...")
    pois = load_pois(INPUT_CSV, TOP_K, NUM_DAYS)

    print("\nBước 2: Khởi tạo K-Means 3D + Greedy...")
    init_itin = initial_itinerary(pois, NUM_DAYS)
    save_route(init_itin, "dalat_route_greedy_3d.csv", "Greedy 3D")
    print_route(init_itin, "Greedy (K-Means 3D)")

    print("\nBước 3: SA Phase 1 (feasibility)...")
    feas_itin, _ = sa_phase1(init_itin)
    target = count_infeasible(feas_itin)
    print(f"  Phase 1 done: infeasible = {target}")
    save_route(feas_itin, "dalat_route_phase1.csv", "SA_Phase1")

    print("\nBước 4: SA Phase 2 (km, hard constraint)...")
    final_itin, _ = sa_phase2(feas_itin, target)
    km_final, feas_final, stops_final = save_route(final_itin, f"dalat_route_{NUM_DAYS}days.csv", "SA_Phase2")
    print_route(final_itin, "SA 2-phase")

    km_init, feas_init, stops_init = simulate_day(init_itin[0])[0], sum(1 for d in init_itin for _ in d), sum(len(d) for d in init_itin)  # quick
    # Actually get from saved data: we recompute
    km_greedy, feas_greedy, _ = save_route(init_itin, "temp.csv", "temp")  # dummy
    import os; os.remove("temp.csv")
    print(f"\n  {'='*60}")
    print(f"  SO SÁNH")
    print(f"  {'='*60}")
    print(f"  {'Chỉ số':<30} {'Greedy 3D':>12} {'SA 2-phase':>12} {'Cải thiện':>12}")
    print(f"  {'-'*60}")
    km_imp = (km_greedy - km_final)/km_greedy*100 if km_greedy>0 else 0
    feas_imp = (feas_final - feas_greedy)/stops_final*100
    print(f"  {'Tổng km':<30} {km_greedy:>12.1f} {km_final:>12.1f} {km_imp:>+11.1f}%")
    print(f"  {'Feasible stops':<30} {feas_greedy:>12} {feas_final:>12} {feas_imp:>+11.1f}%")
    print(f"  {'Feasibility rate':<30} {round(100*feas_greedy/stops_init):>11}% {round(100*feas_final/stops_final):>11}%")
    print(f"\n  Thời gian: {time.time()-t_start:.1f}s")
    print(f"  Output: dalat_route_{NUM_DAYS}days.csv")
    print("="*60)

if __name__ == "__main__":
    main()