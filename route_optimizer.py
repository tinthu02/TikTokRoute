"""
=============================================================
GIAI ĐOẠN 5 — Tối ưu lộ trình du lịch Đà Lạt
=============================================================
Input:  dalat_poi_scored.csv
Output: dalat_route_<N>days.csv   (lộ trình tối ưu)
        dalat_route_greedy_<N>days.csv (baseline để so sánh)

Thuật toán:
  - Greedy Nearest Neighbor (khởi tạo + baseline)
  - Simulated Annealing + Metropolis Criterion (tối ưu)

Ràng buộc TSPTW:
  - Giờ mở cửa địa điểm (time windows)
  - Thời gian di chuyển thực (Haversine, tốc độ trung bình 30km/h)
  - Thời gian tham quan mỗi địa điểm
  - Quỹ thời gian mỗi ngày (USER_START - USER_END)
=============================================================
"""

import csv, math, random, time, os, copy

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH NGƯỜI DÙNG — CHỈNH Ở ĐÂY
# ══════════════════════════════════════════════════════════════

INPUT_CSV   = "dalat_poi_scored.csv"

NUM_DAYS    = 3        # số ngày du lịch
TOP_K       = 40       # chọn Top-K POI theo attraction_score trước khi optimize
                       # tăng lên nếu muốn nhiều lựa chọn hơn

USER_START  = 7 * 60   # 07:00 — bắt đầu ngày (phút từ 00:00)
USER_END    = 21 * 60  # 21:00 — kết thúc ngày

AVG_SPEED_KMH = 30     # tốc độ di chuyển trung bình (xe máy nội thành)

# Simulated Annealing params
SA_T0        = 1000.0  # nhiệt độ ban đầu
SA_ALPHA     = 0.995   # tốc độ làm nguội
SA_MAX_ITER  = 50_000  # số vòng lặp tối đa
SA_MIN_T     = 0.01    # dừng khi nhiệt độ xuống đây

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
    """Thời gian di chuyển (phút)"""
    km = haversine_km(lat1, lng1, lat2, lng2)
    return (km / AVG_SPEED_KMH) * 60

def fmt_min(minutes):
    """480 -> '08:00'"""
    if minutes is None:
        return "?"
    h = int(minutes) // 60 % 24
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — ĐỌC & CHUẨN BỊ DATA
# ══════════════════════════════════════════════════════════════

def load_pois(filepath, top_k, num_days):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    pois = []
    seen_coords = set()  # dedup theo tọa độ gần giống nhau

    for r in rows:
        if str(r.get("include_in_route", "")).strip().lower() not in ("true", "1"):
            continue

        lat = safe_float(r.get("lat"))
        lng = safe_float(r.get("lng"))
        if lat == 0 or lng == 0:
            continue

        # Dedup: làm tròn 3 chữ số (~111m) để gộp địa điểm trùng tọa độ
        coord_key = (round(lat, 3), round(lng, 3))
        if coord_key in seen_coords:
            continue
        seen_coords.add(coord_key)

        # Giờ mở cửa
        open_min  = safe_float(r.get("open_min"))
        close_min = safe_float(r.get("close_min"))

        # open=0, close=NaN → mở cả ngày
        if open_min == 0 and (str(r.get("close_min", "")) in ("", "nan", "None")):
            open_min  = 0
            close_min = 24 * 60

        # close=NaN nhưng open có giá trị → giả định đóng lúc 22:00
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

    # Sắp xếp theo attraction_score, lấy Top-K
    pois.sort(key=lambda x: x["attraction_score"], reverse=True)
    # Lấy đủ POI cho số ngày (ít nhất 5/ngày)
    k = max(top_k, num_days * 5)
    pois = pois[:k]
    print(f"  Sau dedup và Top-{k}: {len(pois)} POI")
    return pois

# ══════════════════════════════════════════════════════════════
# BƯỚC 2 — CHIA NGÀY & KIỂM TRA RÀNG BUỘC
# ══════════════════════════════════════════════════════════════

def is_feasible(poi, arrive_time):
    """Kiểm tra có thể ghé POI lúc arrive_time không"""
    # Đến trước khi mở cửa → chờ
    start = max(arrive_time, poi["open_min"])
    # Kết thúc tham quan
    end = start + poi["visit_min"]
    # Phải xong trước khi đóng cửa và trước USER_END
    if end > poi["close_min"]:
        return False, start, end
    if end > USER_END:
        return False, start, end
    return True, start, end


def simulate_day(poi_list):
    """
    Mô phỏng 1 ngày với danh sách POI theo thứ tự.
    Trả về: (total_km, feasible_count, timeline)
    """
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
            "name":       poi["name"],
            "type":       poi["type"],
            "arrive":     arrive,
            "start":      start,
            "end":        end,
            "feasible":   ok,
            "km":         round(km, 2),
            "rating":     poi["rating"],
            "score":      poi["attraction_score"],
            "address":    poi["address"],
            "video_url":  poi["video_url"],
        })

        if ok:
            feasible += 1
            current_time = end
        else:
            current_time = arrive  # vẫn di chuyển, dù không vào được

        current_lat = poi["lat"]
        current_lng = poi["lng"]

    return total_km, feasible, timeline


def split_into_days(route, num_days):
    """Chia route thành num_days ngày, phân bổ đều"""
    n = len(route)
    days = []
    size = n // num_days
    rem  = n % num_days
    idx  = 0
    for d in range(num_days):
        end = idx + size + (1 if d < rem else 0)
        days.append(route[idx:end])
        idx = end
    return days

# ══════════════════════════════════════════════════════════════
# BƯỚC 3 — GREEDY NEAREST NEIGHBOR
# ══════════════════════════════════════════════════════════════

DALAT_LAT = 11.9404  # Trung tâm Đà Lạt (điểm xuất phát)
DALAT_LNG = 108.4583

def greedy_route(pois):
    """Nearest Neighbor Heuristic — ưu tiên POI gần + score cao"""
    unvisited = list(pois)
    route     = []
    cur_lat, cur_lng = DALAT_LAT, DALAT_LNG
    cur_time  = USER_START

    while unvisited:
        best     = None
        best_val = -1

        for p in unvisited:
            travel_min = travel_minutes(cur_lat, cur_lng, p["lat"], p["lng"])
            arrive     = cur_time + travel_min
            ok, start, end = is_feasible(p, arrive)
            if not ok:
                continue
            # Heuristic: score cao, đi gần
            dist_km = haversine_km(cur_lat, cur_lng, p["lat"], p["lng"])
            val = p["attraction_score"] / (dist_km + 0.1)
            if val > best_val:
                best_val = val
                best     = p

        if best is None:
            # Không còn POI feasible — thêm POI gần nhất dù infeasible
            best = min(unvisited, key=lambda p: haversine_km(cur_lat, cur_lng, p["lat"], p["lng"]))

        route.append(best)
        travel_min = travel_minutes(cur_lat, cur_lng, best["lat"], best["lng"])
        arrive     = cur_time + travel_min
        _, start, end = is_feasible(best, arrive)
        cur_time   = end if end <= USER_END else arrive
        cur_lat    = best["lat"]
        cur_lng    = best["lng"]
        unvisited.remove(best)

    return route

# ══════════════════════════════════════════════════════════════
# BƯỚC 4 — SIMULATED ANNEALING
# ══════════════════════════════════════════════════════════════

def route_cost(route):
    """
    Hàm mục tiêu: minimize total_km - penalty_feasibility
    (giảm km + tối đa feasible stops)
    """
    days = split_into_days(route, NUM_DAYS)
    total_km    = 0.0
    total_infeas = 0

    for day_pois in days:
        km, feas, _ = simulate_day(day_pois)
        total_km    += km
        total_infeas += len(day_pois) - feas

    # Cost = tổng km + penalty lớn cho mỗi infeasible stop
    return total_km + total_infeas * 50.0


def simulated_annealing(initial_route):
    current  = list(initial_route)
    best     = list(current)
    cur_cost = route_cost(current)
    best_cost = cur_cost

    T = SA_T0
    t0 = time.time()

    for iteration in range(SA_MAX_ITER):
        if T < SA_MIN_T:
            break

        # Neighbor: random swap 2 điểm
        n = len(current)
        i, j = random.sample(range(n), 2)
        neighbor = list(current)
        neighbor[i], neighbor[j] = neighbor[j], neighbor[i]

        new_cost = route_cost(neighbor)
        delta    = new_cost - cur_cost

        # Metropolis criterion
        if delta < 0 or random.random() < math.exp(-delta / T):
            current  = neighbor
            cur_cost = new_cost
            if cur_cost < best_cost:
                best      = list(current)
                best_cost = cur_cost

        T *= SA_ALPHA

        # Progress mỗi 10k iterations
        if (iteration + 1) % 10_000 == 0:
            elapsed = round(time.time() - t0, 1)
            print(f"    Iter {iteration+1:>6} | T={T:.2f} | cost={cur_cost:.1f} | best={best_cost:.1f} | {elapsed}s")

    return best, best_cost

# ══════════════════════════════════════════════════════════════
# BƯỚC 5 — LƯU KẾT QUẢ
# ══════════════════════════════════════════════════════════════

def save_route(route, filepath, method="SA"):
    days = split_into_days(route, NUM_DAYS)
    rows = []
    total_km_all   = 0.0
    total_feas_all = 0
    total_stop_all = 0

    for d, day_pois in enumerate(days, 1):
        km, feas, timeline = simulate_day(day_pois)
        total_km_all   += km
        total_feas_all += feas
        total_stop_all += len(day_pois)

        # Sort theo start để CSV đúng thứ tự thời gian
        timeline_sorted = sorted(timeline, key=lambda x: x["start"])
        for stop_idx, stop in enumerate(timeline_sorted, 1):
            rows.append({
                "day":          d,
                "stop":         stop_idx,
                "method":       method,
                "name":         stop["name"],
                "type":         stop["type"],
                "arrive":       fmt_min(stop["arrive"]),
                "start_visit":  fmt_min(stop["start"]),
                "end_visit":    fmt_min(stop["end"]),
                "feasible":     stop["feasible"],
                "dist_km":      stop["km"],
                "rating":       stop["rating"],
                "attraction_score": round(stop["score"], 4),
                "address":      stop["address"],
                "video_url":    stop["video_url"],
            })

    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"  [{method}] Tổng km: {total_km_all:.1f} | "
          f"Feasible: {total_feas_all}/{total_stop_all} "
          f"({round(total_feas_all/total_stop_all*100)}%) -> {filepath}")
    return total_km_all, total_feas_all, total_stop_all


def print_route(route, method="SA"):
    days = split_into_days(route, NUM_DAYS)
    print(f"\n  {'='*60}")
    print(f"  LỊCH TRÌNH {NUM_DAYS} NGÀY — {method}")
    print(f"  {'='*60}")
    for d, day_pois in enumerate(days, 1):
        km, feas, timeline = simulate_day(day_pois)
        # Sort theo start để hiển thị đúng thứ tự thời gian (có thể chờ mở cửa)
        timeline_sorted = sorted(timeline, key=lambda x: x["start"])
        print(f"\n  📅 NGÀY {d} — {km:.1f}km | {feas}/{len(day_pois)} địa điểm đúng giờ")
        print(f"  {'-'*55}")
        for stop in timeline_sorted:
            status = "✅" if stop["feasible"] else "⚠️"
            # Dùng fmt_min để hiển thị HH:MM thay vì số thập phân
            print(f"    {status} {fmt_min(stop['start'])}-{fmt_min(stop['end'])}  "
                  f"{stop['name'][:30]:<30}  "
                  f"({stop['type'][:15]})  ⭐{stop['rating']}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    random.seed(42)
    t_start = time.time()

    print("\n" + "="*60)
    print(f"  GIAI ĐOẠN 5 — Route Optimizer ({NUM_DAYS} ngày, Top-{TOP_K})")
    print(f"  Khung giờ: {fmt_min(USER_START)} - {fmt_min(USER_END)}")
    print(f"  SA: T0={SA_T0} | alpha={SA_ALPHA} | max_iter={SA_MAX_ITER:,}")
    print("="*60)

    # Load data
    print(f"\nBước 1: Load POI...")
    pois = load_pois(INPUT_CSV, TOP_K, NUM_DAYS)

    # Greedy baseline
    print(f"\nBước 2: Greedy Nearest Neighbor...")
    greedy = greedy_route(pois)
    km_g, feas_g, stops_g = save_route(
        greedy,
        f"dalat_route_greedy_{NUM_DAYS}days.csv",
        method="Greedy"
    )
    print_route(greedy, "Greedy")

    # Simulated Annealing
    print(f"\nBước 3: Simulated Annealing...")
    sa_route, sa_cost = simulated_annealing(greedy)
    km_sa, feas_sa, stops_sa = save_route(
        sa_route,
        f"dalat_route_{NUM_DAYS}days.csv",
        method="SA"
    )
    print_route(sa_route, "SA")

    # So sánh
    elapsed = round(time.time() - t_start, 1)
    print(f"\n  {'='*60}")
    print(f"  SO SÁNH GREEDY vs SIMULATED ANNEALING")
    print(f"  {'='*60}")
    print(f"  {'Chỉ số':<30} {'Greedy':>10} {'SA':>10} {'Cải thiện':>12}")
    print(f"  {'-'*60}")

    km_imp   = round((km_g   - km_sa)   / km_g   * 100, 1) if km_g > 0 else 0
    feas_imp = round((feas_sa - feas_g) / max(stops_g, 1) * 100, 1)

    print(f"  {'Tổng km di chuyển':<30} {km_g:>10.1f} {km_sa:>10.1f} {km_imp:>+11.1f}%")
    print(f"  {'Feasible stops':<30} {feas_g:>10} {feas_sa:>10} {feas_imp:>+11.1f}%")
    print(f"  {'Feasibility rate':<30} {round(feas_g/stops_g*100):>9}% {round(feas_sa/stops_sa*100):>9}%")
    print(f"\n  Thời gian chạy: {elapsed}s")
    print(f"  Output: dalat_route_{NUM_DAYS}days.csv")
    print("="*60)


if __name__ == "__main__":
    main()