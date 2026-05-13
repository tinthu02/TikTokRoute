"""
=============================================================
GIAI ĐOẠN 5 — Tối ưu lộ trình du lịch Đà Lạt (cải tiến)
=============================================================
Thay đổi:
  - Cost function thêm time_balance_penalty
  - Khởi tạo lịch trình bằng K-Means 2D (lat, lng) + Greedy nội bộ
  - SA với các toán tử: swap nội bộ, chuyển ngày, swap liên ngày
  - Thêm Meal Timing Bonus & Type Diversity Penalty
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

USER_START  = 7 * 60    # 07:00
USER_END    = 21 * 60   # 21:00
AVG_SPEED_KMH = 30

# SA params (đã tăng)
SA_T0        = 500.0
SA_ALPHA     = 0.9995   # làm mát rất chậm
SA_MAX_ITER  = 100_000
SA_MIN_T     = 0.01

# Trọng số penalty cho mất cân bằng feasible stops
BALANCE_PENALTY_WEIGHT = 200.0
INFEASIBLE_PENALTY      = 500.0   # tăng mạnh để loại bỏ điểm không feasible

# Meal Timing Bonus
MEAL_WINDOWS = [
    (11 * 60 + 30, 13 * 60),   # trưa 11:30-13:00
    (18 * 60, 19 * 60 + 30)    # tối 18:00-19:30
]
MEAL_BONUS = 100.0            # thưởng cho mỗi bữa ăn đúng giờ (làm giảm cost)

# Type Diversity (khuyến khích lịch trình đa dạng loại hình)
TYPE_DIVERSITY_WEIGHT = 150.0  # thưởng cho entropy cao (trừ cost)

# Nhóm các type được coi là "ăn uống"
FOOD_TYPES = {"chợ quán", "nhà hàng", "quán ăn", "ăn_uống"}

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
# BƯỚC 1 — ĐỌC & CHUẨN BỊ DATA
# ══════════════════════════════════════════════════════════════

def load_pois(filepath, top_k, num_days):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    pois = []
    seen_coords = set()

    for r in rows:
        if str(r.get("include_in_route", "")).strip().lower() not in ("true", "1"):
            continue

        lat = safe_float(r.get("lat"))
        lng = safe_float(r.get("lng"))
        if lat == 0 or lng == 0:
            continue

        coord_key = (round(lat, 3), round(lng, 3))
        if coord_key in seen_coords:
            continue
        seen_coords.add(coord_key)

        open_min  = safe_float(r.get("open_min"))
        close_min = safe_float(r.get("close_min"))

        if open_min == 0 and (str(r.get("close_min", "")) in ("", "nan", "None")):
            open_min  = 0
            close_min = 24 * 60
        if close_min == 0 and open_min > 0:
            close_min = 22 * 60

        pois.append({
            "name":             r["place_name"],
            "type":             r.get("type", ""),
            "lat":              lat,
            "lng":              lng,
            "attraction_score": safe_float(r.get("attraction_score")),
            "open_min":         open_min,
            "close_min":        close_min,
            "visit_min":        safe_int(r.get("visit_duration_min", 45)),
            "rating":           safe_float(r.get("gmaps_rating")),
            "address":          r.get("gmaps_address", ""),
            "video_url":        r.get("video_urls", ""),
        })

    pois.sort(key=lambda x: x["attraction_score"], reverse=True)
    k = max(top_k, num_days * 5)
    pois = pois[:k]
    print(f"  Sau dedup và Top-{k}: {len(pois)} POI")
    return pois

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
            "video_url": poi["video_url"],
        })

        if ok:
            feasible += 1
            current_time = end
        else:
            current_time = arrive
        current_lat = poi["lat"]
        current_lng = poi["lng"]

    return total_km, feasible, timeline

# ══════════════════════════════════════════════════════════════
# K-MEANS 2D (tự viết, không cần sklearn)
# ══════════════════════════════════════════════════════════════

def kmeans_cluster(pois, num_clusters, max_iter=50):
    """Trả về danh sách các cụm, mỗi cụm là list index của POI"""
    n = len(pois)
    if n <= num_clusters:
        return [[i] for i in range(n)]  # mỗi điểm một cụm

    # Dữ liệu tọa độ
    X = np.array([[p["lat"], p["lng"]] for p in pois])
    # Khởi tạo tâm ngẫu nhiên
    rng = np.random.default_rng(42)
    indices = rng.choice(n, size=num_clusters, replace=False)
    centroids = X[indices].copy()

    for _ in range(max_iter):
        # Gán cụm
        distances = np.linalg.norm(X[:, np.newaxis] - centroids, axis=2)
        labels = np.argmin(distances, axis=1)
        # Cập nhật tâm
        new_centroids = np.array([X[labels == k].mean(axis=0) for k in range(num_clusters)])
        if np.allclose(centroids, new_centroids):
            break
        centroids = new_centroids

    clusters = [[] for _ in range(num_clusters)]
    for i, label in enumerate(labels):
        clusters[label].append(i)
    return clusters

# ══════════════════════════════════════════════════════════════
# GREEDY NỘI BỘ CHO MỘT CỤM
# ══════════════════════════════════════════════════════════════

def greedy_day(poi_list):
    """Sắp xếp danh sách POI trong một ngày bằng nearest neighbor"""
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
            # Không còn điểm feasible, thêm điểm gần nhất
            best = min(unvisited, key=lambda p: haversine_km(cur_lat, cur_lng, p["lat"], p["lng"]))
        route.append(best)
        travel_min = travel_minutes(cur_lat, cur_lng, best["lat"], best["lng"])
        arrive = cur_time + travel_min
        _, start, end = is_feasible(best, arrive)
        cur_time = end if end <= USER_END else arrive
        cur_lat, cur_lng = best["lat"], best["lng"]
        unvisited.remove(best)
    return route

# ══════════════════════════════════════════════════════════════
# KHỞI TẠO ITINERARY BẰNG K-MEANS + GREEDY NỘI BỘ
# ══════════════════════════════════════════════════════════════

def initial_itinerary(pois, num_days):
    """Trả về itinerary: list of list POI đã sắp xếp"""
    clusters = kmeans_cluster(pois, num_days)
    itinerary = []
    for cluster_indices in clusters:
        day_pois = [pois[i] for i in cluster_indices]
        sorted_day = greedy_day(day_pois)
        itinerary.append(sorted_day)
    return itinerary

# ══════════════════════════════════════════════════════════════
# COST FUNCTION (có time_balance_penalty, meal bonus, diversity)
# ══════════════════════════════════════════════════════════════

def route_cost(itinerary):
    total_km = 0.0
    feasible_counts = []
    total_infeas = 0
    meal_bonus_total = 0.0
    type_diversity_total = 0.0

    for day_pois in itinerary:
        km, feas, timeline = simulate_day(day_pois)
        total_km += km
        feasible_counts.append(feas)
        total_infeas += len(day_pois) - feas

        # Meal timing bonus: nếu điểm ăn uống feasible và nằm trong cửa sổ giờ ăn
        for stop in timeline:
            if stop["feasible"] and stop["type"] in FOOD_TYPES:
                start_mins = stop["start"]
                for (w_start, w_end) in MEAL_WINDOWS:
                    if w_start <= start_mins <= w_end:
                        meal_bonus_total += MEAL_BONUS
                        break   # chỉ thưởng một lần

        # Type diversity: entropy của phân phối loại hình trong ngày
        # Tính trên tất cả điểm (kể cả infeasible) để khuyến khích đa dạng
        type_counts = {}
        for poi in day_pois:
            t = poi["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        total_points = len(day_pois)
        entropy = 0.0
        if total_points > 0:
            for count in type_counts.values():
                p = count / total_points
                entropy -= p * math.log(p + 1e-9)   # tránh log(0)
        type_diversity_total += entropy

    # Phạt mất cân bằng: độ lệch chuẩn của feasible counts
    std_feas = 0.0
    if len(feasible_counts) > 1:
        std_feas = np.std(feasible_counts)

    cost = total_km \
           + total_infeas * INFEASIBLE_PENALTY \
           + std_feas * BALANCE_PENALTY_WEIGHT \
           - meal_bonus_total \
           - TYPE_DIVERSITY_WEIGHT * type_diversity_total

    return cost

# ══════════════════════════════════════════════════════════════
# SIMULATED ANNEALING (với toán tử đa dạng)
# ══════════════════════════════════════════════════════════════

def simulated_annealing(initial_itinerary):
    current = copy.deepcopy(initial_itinerary)
    best = copy.deepcopy(current)
    cur_cost = route_cost(current)
    best_cost = cur_cost

    T = SA_T0
    t0 = time.time()

    for iteration in range(SA_MAX_ITER):
        if T < SA_MIN_T:
            break

        # Chọn toán tử ngẫu nhiên
        op = random.random()
        new_itinerary = copy.deepcopy(current)

        if op < 0.4:   # Swap nội bộ trong cùng một ngày
            day_idx = random.randrange(NUM_DAYS)
            if len(new_itinerary[day_idx]) >= 2:
                i, j = random.sample(range(len(new_itinerary[day_idx])), 2)
                new_itinerary[day_idx][i], new_itinerary[day_idx][j] = \
                    new_itinerary[day_idx][j], new_itinerary[day_idx][i]

        elif op < 0.8: # Chuyển 1 điểm từ ngày này sang ngày khác
            src_day, dst_day = random.sample(range(NUM_DAYS), 2)
            if len(new_itinerary[src_day]) > 1:  # giữ ít nhất 1 điểm
                idx = random.randrange(len(new_itinerary[src_day]))
                poi = new_itinerary[src_day].pop(idx)
                # Chèn vào vị trí ngẫu nhiên trong ngày đích
                insert_pos = random.randint(0, len(new_itinerary[dst_day]))
                new_itinerary[dst_day].insert(insert_pos, poi)

        else:          # Hoán đổi 2 điểm giữa 2 ngày
            day1, day2 = random.sample(range(NUM_DAYS), 2)
            if new_itinerary[day1] and new_itinerary[day2]:
                i = random.randrange(len(new_itinerary[day1]))
                j = random.randrange(len(new_itinerary[day2]))
                new_itinerary[day1][i], new_itinerary[day2][j] = \
                    new_itinerary[day2][j], new_itinerary[day1][i]

        new_cost = route_cost(new_itinerary)
        delta = new_cost - cur_cost

        # Metropolis criterion
        if delta < 0 or random.random() < math.exp(-delta / T):
            current = new_itinerary
            cur_cost = new_cost
            if cur_cost < best_cost:
                best = copy.deepcopy(current)
                best_cost = cur_cost

        T *= SA_ALPHA

        if (iteration + 1) % 20_000 == 0:
            elapsed = round(time.time() - t0, 1)
            print(f"    Iter {iteration+1:>6} | T={T:.4f} | cost={cur_cost:.1f} | best={best_cost:.1f} | {elapsed}s")

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
    print(f"  SA: T0={SA_T0} | alpha={SA_ALPHA} | max_iter={SA_MAX_ITER:,}")
    print(f"  Meal bonus={MEAL_BONUS} | Diversity weight={TYPE_DIVERSITY_WEIGHT}")
    print("="*60)

    print(f"\nBước 1: Load POI...")
    pois = load_pois(INPUT_CSV, TOP_K, NUM_DAYS)

    # Khởi tạo bằng K-Means + Greedy nội bộ
    print(f"\nBước 2: Khởi tạo lịch trình (K-Means 2D + Greedy nội bộ)...")
    init_itin = initial_itinerary(pois, NUM_DAYS)
    km_init, feas_init, stops_init = save_route(init_itin,
        f"dalat_route_greedy_{NUM_DAYS}days.csv", method="Greedy (K-Means)")
    print_route(init_itin, "Greedy (K-Means)")

    # SA
    print(f"\nBước 3: Simulated Annealing...")
    sa_itin, sa_cost = simulated_annealing(init_itin)
    km_sa, feas_sa, stops_sa = save_route(sa_itin,
        f"dalat_route_{NUM_DAYS}days.csv", method="SA")
    print_route(sa_itin, "SA")

    # So sánh
    elapsed = round(time.time() - t_start, 1)
    print(f"\n  {'='*60}")
    print(f"  SO SÁNH GREEDY (K-Means) vs SIMULATED ANNEALING")
    print(f"  {'='*60}")
    print(f"  {'Chỉ số':<30} {'Greedy':>10} {'SA':>10} {'Cải thiện':>12}")
    print(f"  {'-'*60}")

    km_imp   = round((km_init - km_sa) / km_init * 100, 1) if km_init > 0 else 0
    feas_imp = round((feas_sa - feas_init) / max(stops_init, 1) * 100, 1)

    print(f"  {'Tổng km di chuyển':<30} {km_init:>10.1f} {km_sa:>10.1f} {km_imp:>+11.1f}%")
    print(f"  {'Feasible stops':<30} {feas_init:>10} {feas_sa:>10} {feas_imp:>+11.1f}%")
    print(f"  {'Feasibility rate':<30} {round(feas_init/stops_init*100):>9}% {round(feas_sa/stops_sa*100):>9}%")
    print(f"\n  Thời gian chạy: {elapsed}s")
    print(f"  Output: dalat_route_{NUM_DAYS}days.csv")
    print("="*60)

if __name__ == "__main__":
    main()