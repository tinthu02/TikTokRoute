"""
=============================================================
OSRM Routing Module — Thay thế haversine bằng đường thực tế
=============================================================
Sử dụng OSRM public API (miễn phí, không cần API key)
để lấy khoảng cách và thời gian di chuyển thực tế.

Tích hợp vào app.py:
  1. import osrm ở đầu file
  2. Thay haversine_km()  → osrm.distance_km()
  3. Thay travel_min()    → osrm.travel_min()
  4. Gọi osrm.warmup_cache(pois) sau khi load_pois()
     để tải trước toàn bộ ma trận khoảng cách 1 lần duy nhất
=============================================================
"""

import math
import time
import requests
from functools import lru_cache

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

OSRM_BASE_URL = "http://router.project-osrm.org"
OSRM_PROFILE  = "driving"          # driving | walking | cycling

REQUEST_TIMEOUT = 5                 # giây, timeout mỗi request
RETRY_LIMIT     = 2                 # số lần thử lại khi lỗi
RETRY_DELAY     = 1.0               # giây chờ giữa các lần retry

# Fallback: tốc độ mặc định khi OSRM không trả về kết quả
FALLBACK_SPEED_KMH = 30.0

# Cache nội bộ: (lat1,lng1,lat2,lng2) -> (km, minutes)
_route_cache: dict[tuple, tuple[float, float]] = {}


# ══════════════════════════════════════════════════════════════
# FALLBACK (haversine — dùng khi OSRM lỗi)
# ══════════════════════════════════════════════════════════════

def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _fallback(lat1, lng1, lat2, lng2) -> tuple[float, float]:
    """Trả về (km, minutes) theo haversine khi OSRM không khả dụng."""
    km = _haversine_km(lat1, lng1, lat2, lng2)
    minutes = km / FALLBACK_SPEED_KMH * 60
    return km, minutes


# ══════════════════════════════════════════════════════════════
# GỌI OSRM — ROUTE API (1 cặp điểm)
# ══════════════════════════════════════════════════════════════

def _fetch_route(lat1, lng1, lat2, lng2) -> tuple[float, float]:
    """
    Gọi OSRM Route API cho 1 cặp điểm.
    Trả về (km, minutes). Fallback về haversine nếu lỗi.
    """
    url = (
        f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/"
        f"{lng1},{lat1};{lng2},{lat2}"
        f"?overview=false&annotations=false"
    )
    for attempt in range(RETRY_LIMIT + 1):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                km      = route["distance"] / 1000.0
                minutes = route["duration"] / 60.0
                return km, minutes
        except Exception:
            if attempt < RETRY_LIMIT:
                time.sleep(RETRY_DELAY)
    # Fallback
    return _fallback(lat1, lng1, lat2, lng2)


# ══════════════════════════════════════════════════════════════
# GỌI OSRM — TABLE API (nhiều cặp điểm cùng lúc)
# ══════════════════════════════════════════════════════════════

def _fetch_table(coords: list[tuple[float, float]]) -> dict[tuple, tuple[float, float]]:
    """
    Gọi OSRM Table API để lấy ma trận thời gian/khoảng cách
    cho danh sách tọa độ [(lat, lng), ...].
    Trả về dict {(lat1,lng1,lat2,lng2): (km, minutes)}.

    OSRM Table API giới hạn ~100 điểm mỗi request.
    """
    result = {}
    n = len(coords)
    if n == 0:
        return result

    coord_str = ";".join(f"{lng},{lat}" for lat, lng in coords)
    url = (
        f"{OSRM_BASE_URL}/table/v1/{OSRM_PROFILE}/{coord_str}"
        f"?annotations=duration,distance"
    )

    try:
        resp = requests.get(url, timeout=max(REQUEST_TIMEOUT, n * 0.5))
        data = resp.json()
        if data.get("code") != "Ok":
            return result

        durations = data.get("durations", [])   # ma trận thời gian (giây)
        distances = data.get("distances", [])   # ma trận khoảng cách (mét)

        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                lat1, lng1 = coords[i]
                lat2, lng2 = coords[j]
                try:
                    minutes = durations[i][j] / 60.0
                    km      = distances[i][j] / 1000.0
                    result[(lat1, lng1, lat2, lng2)] = (km, minutes)
                except (IndexError, TypeError):
                    pass
    except Exception:
        pass  # Nếu lỗi, để caller fallback từng cặp

    return result


# ══════════════════════════════════════════════════════════════
# WARM-UP CACHE — gọi 1 lần sau khi load POI
# ══════════════════════════════════════════════════════════════

def warmup_cache(pois: list[dict], chunk_size: int = 80) -> None:
    """
    Tải trước toàn bộ ma trận khoảng cách cho danh sách POI.
    Gọi 1 lần duy nhất sau load_pois() để tránh gọi API lặp lại
    trong quá trình tối ưu (SA chạy hàng chục nghìn vòng lặp).

    pois: danh sách dict có key 'lat', 'lng'
    chunk_size: số điểm mỗi lần gọi Table API (tối đa ~100)
    """
    global _route_cache

    # Lấy tọa độ duy nhất
    coords = list({(p["lat"], p["lng"]) for p in pois})
    n = len(coords)
    if n == 0:
        return

    print(f"  [OSRM] Đang tải ma trận khoảng cách cho {n} POI...")
    t0 = time.time()
    loaded = 0

    # Chia thành các chunk để không vượt giới hạn OSRM
    for start in range(0, n, chunk_size):
        chunk = coords[start: start + chunk_size]
        batch = _fetch_table(chunk)
        _route_cache.update(batch)
        loaded += len(batch)

    elapsed = time.time() - t0
    print(f"  [OSRM] Cache xong: {loaded} cặp điểm trong {elapsed:.1f}s")


# ══════════════════════════════════════════════════════════════
# API CÔNG KHAI — dùng để thay thế trong app.py
# ══════════════════════════════════════════════════════════════

def get_route(lat1: float, lng1: float, lat2: float, lng2: float) -> tuple[float, float]:
    """
    Trả về (km, minutes) theo đường thực tế.
    Ưu tiên cache → OSRM API → fallback haversine.
    """
    if lat1 == lat2 and lng1 == lng2:
        return 0.0, 0.0

    key = (lat1, lng1, lat2, lng2)
    if key in _route_cache:
        return _route_cache[key]

    # Chưa có trong cache → gọi API trực tiếp
    km, minutes = _fetch_route(lat1, lng1, lat2, lng2)
    _route_cache[key] = (km, minutes)
    return km, minutes


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Trả về khoảng cách thực tế (km)."""
    km, _ = get_route(lat1, lng1, lat2, lng2)
    return km


def travel_min(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Trả về thời gian di chuyển thực tế (phút)."""
    _, minutes = get_route(lat1, lng1, lat2, lng2)
    return minutes

if __name__ == "__main__":
    # Test 2 địa điểm thực tế ở Đà Lạt
    # Hồ Xuân Hương -> Đồi Chè Cầu Đất
    lat1, lng1 = 11.9440, 108.4429
    lat2, lng2 = 11.8269, 108.5133

    km_osrm = distance_km(lat1, lng1, lat2, lng2)
    min_osrm = travel_min(lat1, lng1, lat2, lng2)

    # So sánh với haversine
    km_haversine = _haversine_km(lat1, lng1, lat2, lng2)
    min_haversine = km_haversine / 30 * 60

    print(f"Haversine : {km_haversine:.2f} km | {min_haversine:.1f} phút")
    print(f"OSRM      : {km_osrm:.2f} km | {min_osrm:.1f} phút")