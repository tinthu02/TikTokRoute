"""
=============================================================
GIAI ĐOẠN 5 — Tối ưu lộ trình du lịch Đà Lạt (có tích hợp thời tiết)
=============================================================
- Dedup theo gmaps_place_id
- K-Means 3D (lat, lng, open_min)
- SA 2-phase: phase1 chỉ feasibility, phase2 hard constraint trên feasibility
- Tích hợp dự báo thời tiết: nếu ngày mưa, loại bỏ các POI ngoài trời
=============================================================
"""

import csv, math, random, time, copy, os, sys
import numpy as np

# Cho phép import package `common/` và `routing/` ở thư mục gốc repo, bất kể
# script này được chạy bằng `python routing/route_optimizer.py` từ đâu, hay
# bị full_pipeline.py gọi bằng subprocess.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.weather import get_rainy_days  # thêm dòng này
from routing import core as rcore  # thuật toán/tiện ích lõi dùng chung với webapp/app.py
import argparse

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV   = "05_poi_scored.csv"
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

# Các loại POI ngoài trời (sẽ bị loại nếu trời mưa)
OUTDOOR_TYPES = {"thiên nhiên", "địa điểm checkin"}  # check-in thường ngoài trời

# ══════════════════════════════════════════════════════════════
# HELPERS
# ── safe_float/safe_int, haversine_km, travel time, giờ mặc định theo
#    loại POI, và suy luận thời lượng ghé thăm giờ nằm ở routing/core.py
#    (dùng chung với webapp/app.py) — xem đó thay vì định nghĩa lại ở đây.
# ══════════════════════════════════════════════════════════════

safe_float = rcore.safe_float
safe_int = rcore.safe_int
haversine_km = rcore.haversine_km
infer_visit_duration = rcore.infer_visit_duration
fmt_min = rcore.fmt_min
DEFAULT_HOURS_BY_TYPE = rcore.DEFAULT_HOURS_BY_TYPE
DALAT_LAT = rcore.DALAT_LAT
DALAT_LNG = rcore.DALAT_LNG

def travel_minutes(lat1, lng1, lat2, lng2):
    return rcore.travel_min(lat1, lng1, lat2, lng2, speed_kmh=AVG_SPEED_KMH)

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
            "score":            safe_float(r.get("attraction_score")),
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

    # Lấy dự báo thời tiết
    rainy_days = get_rainy_days(num_days)
    print(f"  Dự báo thời tiết {num_days} ngày tới: {rainy_days}")

    # Lọc POI theo thời tiết (nếu ngày mưa, loại bỏ POI ngoài trời)
    # Việc lọc này sẽ được áp dụng khi phân cụm ngày (sau khi đã có clusters)
    # Ở đây ta chỉ lưu lại thông tin rainy_days để dùng sau.

    pois.sort(key=lambda x: x["score"], reverse=True)
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
    return selected, rainy_days

# ══════════════════════════════════════════════════════════════
# SIMULATE NGÀY
# is_feasible/simulate_day dùng chung với webapp/app.py (routing/core.py).
# Giữ nguyên chữ ký cũ (không cần truyền user_start/user_end) bằng cách bọc
# lại quanh hằng số module USER_START/USER_END, để không phải sửa mọi nơi
# gọi is_feasible(poi, arrive)/simulate_day(poi_list) bên dưới.
# ══════════════════════════════════════════════════════════════

def is_feasible(poi, arrive_time):
    return rcore.is_feasible(poi, arrive_time, USER_END)

def simulate_day(poi_list):
    # Lưu ý hành vi đặc thù của route_optimizer (khác app.py): mỗi ngày được
    # đánh giá độc lập, giả định người dùng đã ở sẵn tại POI đầu tiên của
    # ngày đó (không tính km/thời gian di chuyển tới POI đầu tiên) — vì các
    # ngày ở đây đến từ cụm K-Means, không có 1 điểm xuất phát cố định như
    # webapp/app.py. Giữ nguyên hành vi này khi tái sử dụng rcore.simulate_day
    # bằng cách truyền sẵn start_lat/start_lng = tọa độ POI đầu tiên.
    start_lat = poi_list[0]["lat"] if poi_list else None
    start_lng = poi_list[0]["lng"] if poi_list else None
    # rcore.simulate_day trả về timeline đã sắp theo giờ bắt đầu ghé thăm;
    # save_route/print_route bên dưới vốn đã tự sort lại theo "start" nên
    # không ảnh hưởng gì tới hành vi cũ.
    return rcore.simulate_day(
        poi_list, USER_START, USER_END, start_lat=start_lat, start_lng=start_lng,
        distance_fn=haversine_km, duration_fn=travel_minutes,
    )

def count_infeasible(itinerary):
    return rcore.count_infeasible(itinerary, USER_START, USER_END)

# ══════════════════════════════════════════════════════════════
# K-MEANS 3D (lat, lng, open_min) - có lọc outdoor khi mưa
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

def initial_itinerary(pois, num_days, rainy_days):
    # Lọc POI theo thời tiết: nếu ngày dự báo mưa, loại bỏ các POI outdoor
    # Ta sẽ tạo một bản sao pois_filtered cho từng ngày dựa trên rainy_days
    # Tuy nhiên, để đơn giản, ta sẽ lọc cứng trong chính hàm greedy_day
    # Ở đây ta sẽ gắn thêm thuộc tính "is_outdoor" cho mỗi POI
    for p in pois:
        p["is_outdoor"] = p["type"] in OUTDOOR_TYPES

    clusters = kmeans_cluster_3d(pois, num_days)
    itinerary = []
    for day_idx, idxs in enumerate(clusters):
        day_pois = [pois[i] for i in idxs]
        # Nếu ngày này mưa, loại bỏ các POI outdoor khỏi danh sách ngày
        if rainy_days[day_idx]:
            day_pois = [p for p in day_pois if not p["is_outdoor"]]
        itinerary.append(greedy_day(day_pois))
    return itinerary

def greedy_day(poi_list):
    return rcore.greedy_day(poi_list, USER_START, USER_END, start_lat=DALAT_LAT, start_lng=DALAT_LNG)

# ══════════════════════════════════════════════════════════════
# COST FUNCTIONS (giữ nguyên)
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
# LƯU VÀ IN KẾT QUẢ (giữ nguyên)
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


def parse_args():
    parser = argparse.ArgumentParser(description='Route Optimizer for Da Lat')
    parser.add_argument('--num_days', type=int, default=NUM_DAYS, help='Number of days')
    parser.add_argument('--top_k', type=int, default=TOP_K, help='Number of POIs')
    parser.add_argument('--anchor_pois', type=str, nargs='*', default=ANCHOR_POIS, help='Anchor POI names')
    return parser.parse_args()
# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    args = parse_args()
    global NUM_DAYS, TOP_K, ANCHOR_POIS
    NUM_DAYS = args.num_days
    TOP_K = args.top_k
    ANCHOR_POIS = args.anchor_pois
    random.seed(42)
    np.random.seed(42)
    t_start = time.time()

    print("\n" + "="*60)
    print(f"  ROUTE OPTIMIZER — {NUM_DAYS} ngày, Top {TOP_K} POI (có thời tiết)")
    print(f"  Giờ: {fmt_min(USER_START)} - {fmt_min(USER_END)}")
    print(f"  SA 2-phase (hard constraint)")
    print("="*60)

    print("\nBước 1: Load POI và dự báo thời tiết...")
    pois, rainy_days = load_pois(INPUT_CSV, TOP_K, NUM_DAYS)

    print("\nBước 2: Khởi tạo K-Means 3D + Greedy (có lọc outdoor khi mưa)...")
    init_itin = initial_itinerary(pois, NUM_DAYS, rainy_days)
    save_route(init_itin, "06_route_greedy_init.csv", "Greedy 3D")
    print_route(init_itin, "Greedy (K-Means 3D)")

    print("\nBước 3: SA Phase 1 (feasibility)...")
    feas_itin, _ = sa_phase1(init_itin)
    target = count_infeasible(feas_itin)
    print(f"  Phase 1 done: infeasible = {target}")
    save_route(feas_itin, "06_route_phase1_feasible.csv", "SA_Phase1")

    print("\nBước 4: SA Phase 2 (km, hard constraint)...")
    final_itin, _ = sa_phase2(feas_itin, target)
    km_final, feas_final, stops_final = save_route(final_itin, f"06_route_final_{NUM_DAYS}days.csv", "SA_Phase2")
    print_route(final_itin, "SA 2-phase")

    km_greedy, feas_greedy, _ = save_route(init_itin, "temp.csv", "temp")
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
    #print(f"  {'Feasibility rate':<30} {round(100*feas_greedy/len(init_itin[0]) if init_itin else 0):>11}% {round(100*feas_final/stops_final):>11}%")
    greedy_total_stops = sum(len(day) for day in init_itin) if init_itin else 0
    if greedy_total_stops == 0:
        greedy_rate = 0
    else:
        greedy_rate = round(100 * feas_greedy / greedy_total_stops)
    print(f"  {'Feasibility rate':<30} {greedy_rate:>11}% {round(100*feas_final/stops_final):>11}%")
    print(f"\n  Thời gian: {time.time()-t_start:.1f}s")
    print(f"  Output: 06_route_final_{NUM_DAYS}days.csv")
    print("="*60)

if __name__ == "__main__":
    main()