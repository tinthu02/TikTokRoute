"""
=============================================================
routing.core — thuật toán & tiện ích lộ trình DÙNG CHUNG
=============================================================
Trước đây webapp/app.py và routing/route_optimizer.py mỗi nơi tự định
nghĩa một bản y hệt (hoặc gần y hệt) của: safe_float/safe_int,
haversine_km, infer_visit_duration, is_feasible, vòng lặp simulate_day,
vòng lặp greedy chọn POI tiếp theo... Mỗi lần sửa 1 chỗ (vd. công thức
haversine, cách suy luận giờ mở cửa mặc định) lại phải nhớ sửa cả 2 nơi
— rất dễ bị "trôi" logic (2 nơi tính ra kết quả khác nhau) như đã xảy ra
trong lịch sử repo. Module này gom phần LÕI dùng chung vào 1 chỗ.

Những gì KHÔNG gom vào đây (cố tình để riêng ở mỗi nơi):
- Vòng lặp Simulated Annealing của app.py (1 pha, cost = km + phạt
  infeasible, chia ngày bằng cách cắt lát list) và của route_optimizer.py
  (2 pha, cost có thêm balance/meal-bonus/type-diversity, chia ngày bằng
  K-Means 3D) là 2 công thức tối ưu THỰC SỰ khác nhau, không phải trùng
  lặp code — gộp cưỡng ép sẽ tạo ra 1 abstraction rò rỉ, khó hiểu hơn là
  giữ 2 bản riêng. Cả hai đều tái sử dụng is_feasible/simulate_day ở đây
  làm hàm đánh giá (evaluation) chung, đó là phần quan trọng cần dùng chung.
- Cách load & lọc dedup POI từ CSV (khác nhau: app.py dedup theo tọa độ,
  route_optimizer.py dedup theo gmaps_place_id trước, tọa độ sau).

Quy ước dict `poi` dùng chung: bắt buộc có "lat", "lng", "open_min",
"close_min", "visit_min", "score" (điểm hấp dẫn, trước đây route_optimizer
gọi là "attraction_score" — đã đổi tên cho khớp với app.py).
"""

import math

# ── Hằng số dùng chung ──────────────────────────────────────────
DALAT_LAT = 11.9404
DALAT_LNG = 108.4583
AVG_SPEED_KMH = 30

DEFAULT_HOURS_BY_TYPE = {
    "thiên nhiên":      (7 * 60, 18 * 60),
    "địa điểm checkin": (6 * 60, 20 * 60),
    "cafe":             (7 * 60, 22 * 60),
    "nhà hàng":         (10 * 60, 22 * 60),
    "chợ quán":         (6 * 60, 22 * 60),
    "khác":             (7 * 60, 21 * 60),
}

BASE_DURATION = {
    "cafe": 55, "nhà hàng": 65, "chợ quán": 40, "địa điểm checkin": 25,
    "thiên nhiên": 85, "quán ăn": 50, "khác": 40,
}
PRICE_ADJUST = {0: -5, 1: 0, 2: 5, 3: 15, 4: 20}


# ── Ép kiểu an toàn ─────────────────────────────────────────────
def safe_float(v, default=0.0):
    try:
        return float(v) if str(v) not in ("", "nan", "None") else default
    except (TypeError, ValueError):
        return default


def safe_int(v, default=0):
    try:
        return int(float(v)) if str(v) not in ("", "nan", "None") else default
    except (TypeError, ValueError):
        return default


# ── Khoảng cách & thời gian di chuyển ───────────────────────────
def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def travel_min(lat1, lng1, lat2, lng2, speed_kmh=AVG_SPEED_KMH):
    return haversine_km(lat1, lng1, lat2, lng2) / speed_kmh * 60


def fmt_min(minutes):
    if minutes is None:
        return "?"
    h = int(minutes) // 60 % 24
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"


# ── Suy luận thời lượng ghé thăm ────────────────────────────────
def infer_visit_duration(poi_type, price_level, reviews_count, csv_value):
    csv_int = safe_int(csv_value, 0)
    if csv_int > 0 and csv_int != 45:
        return csv_int
    base = BASE_DURATION.get(str(poi_type).strip().lower(), 40)
    base += PRICE_ADJUST.get(safe_int(price_level, 1), 0)
    reviews = safe_int(reviews_count, 0)
    if reviews > 5000:
        base += 15
    elif reviews > 1000:
        base += 8
    elif reviews > 500:
        base += 3
    elif reviews < 50:
        base -= 5
    return max(15, base)


# ── Khả thi & mô phỏng 1 ngày ───────────────────────────────────
def is_feasible(poi, arrive, user_end):
    """1 POI có ghé thăm được không nếu tới lúc `arrive` (phút)."""
    start = max(arrive, poi["open_min"])
    end = start + poi["visit_min"]
    return (end <= poi["close_min"] and end <= user_end), start, end


def default_score_fn(poi, dist_km):
    """Hàm chấm điểm mặc định cho bước chọn greedy: điểm hấp dẫn / khoảng cách."""
    return poi["score"] / (dist_km + 0.1)


def simulate_day(poi_list, user_start, user_end, start_lat=None, start_lng=None,
                  distance_fn=haversine_km, duration_fn=travel_min):
    """
    Mô phỏng việc di chuyển tuần tự qua `poi_list` bắt đầu từ (start_lat,
    start_lng) lúc `user_start`. Trả về (total_km, số điểm khả thi, timeline
    đã sắp theo giờ bắt đầu ghé thăm).

    `distance_fn`/`duration_fn` cho phép cắm khoảng cách/thời gian "chính
    xác" (vd. qua OSRM) thay vì đường chim bay mặc định — webapp/app.py
    dùng việc này cho chế độ `accurate=True`.
    """
    cur_time = user_start
    cur_lat = start_lat if start_lat else DALAT_LAT
    cur_lng = start_lng if start_lng else DALAT_LNG
    total_km = 0.0
    feasible = 0
    timeline = []
    for poi in poi_list:
        tm = duration_fn(cur_lat, cur_lng, poi["lat"], poi["lng"])
        arrive = cur_time + tm
        ok, start, end = is_feasible(poi, arrive, user_end)
        km = distance_fn(cur_lat, cur_lng, poi["lat"], poi["lng"])
        total_km += km
        timeline.append({**poi, "arrive": arrive, "start": start, "end": end,
                          "feasible": ok, "km": round(km, 2)})
        cur_time = end if ok else arrive
        cur_lat, cur_lng = poi["lat"], poi["lng"]
        if ok:
            feasible += 1
    return total_km, feasible, sorted(timeline, key=lambda x: x["start"])


def count_infeasible(itinerary, user_start, user_end):
    """itinerary: list các ngày, mỗi ngày là list POI."""
    total = 0
    for day_pois in itinerary:
        _, feas, _ = simulate_day(day_pois, user_start, user_end)
        total += len(day_pois) - feas
    return total


# ── Greedy: chọn POI kế tiếp tốt nhất ───────────────────────────
def greedy_day(poi_list, user_start, user_end, start_lat=None, start_lng=None,
               score_fn=default_score_fn):
    """
    Thuật toán nearest-best-value dùng chung cho cả 2 nơi: ở mỗi bước, chọn
    POI khả thi có `score_fn(poi, dist_km)` cao nhất; nếu không còn POI nào
    khả thi, chọn POI gần nhất (để không bỏ sót, dù sẽ bị đánh dấu
    infeasible). `score_fn` cho phép app.py cộng thêm trọng số cá nhân hóa
    theo user, còn route_optimizer.py dùng mặc định.
    """
    if not poi_list:
        return []
    unvisited = list(poi_list)
    route = []
    cur_lat = start_lat if start_lat else DALAT_LAT
    cur_lng = start_lng if start_lng else DALAT_LNG
    cur_time = user_start
    while unvisited:
        best, best_val = None, -1
        for p in unvisited:
            tm = travel_min(cur_lat, cur_lng, p["lat"], p["lng"])
            ok, _, _ = is_feasible(p, cur_time + tm, user_end)
            if not ok:
                continue
            dist = haversine_km(cur_lat, cur_lng, p["lat"], p["lng"])
            val = score_fn(p, dist)
            if val > best_val:
                best_val, best = val, p
        if best is None:
            best = min(unvisited, key=lambda p: haversine_km(cur_lat, cur_lng, p["lat"], p["lng"]))
        route.append(best)
        tm = travel_min(cur_lat, cur_lng, best["lat"], best["lng"])
        _, _, end = is_feasible(best, cur_time + tm, user_end)
        cur_time = end if end <= user_end else cur_time + tm
        cur_lat, cur_lng = best["lat"], best["lng"]
        unvisited.remove(best)
    return route
