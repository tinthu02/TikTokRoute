"""
=============================================================
GIAI ĐOẠN 7 — Web App lộ trình Đà Lạt (bố cục thông minh)
=============================================================
Yêu cầu: pip install flask folium

Chạy: python webapp/app.py  (chạy từ thư mục gốc repo)
Mở trình duyệt: http://localhost:5000
=============================================================

feat: tích hợp form đánh giá hành trình (feedback) và thông báo popup cảm ơn

- Thêm form feedback chỉ hiển thị sau khi tạo lộ trình, gồm chọn sao (1-5) và nhập nhận xét
- Gửi đánh giá đến endpoint /submit-feedback, lưu vào database route_feedback
- Hiển thị popup cảm ơn sau khi gửi thành công, tự động ẩn form
- Cải thiện giao diện popup marker: hiển thị rating dạng sao ★★★☆☆
- Thêm hiệu ứng hover và chọn sao trong feedback"
"""

from flask import Flask, render_template, request, jsonify, make_response
import csv, math, random, json, os, sqlite3, sys
from uuid import uuid4

import requests

# Cho phép import package `common/` và `routing/` ở thư mục gốc repo, bất kể
# app.py được chạy bằng `python webapp/app.py` từ đâu (CWD nào) hay bị
# full_pipeline.py gọi bằng subprocess.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.weather import get_rainy_days
from common import osrm
from routing import core as rcore  # thuật toán/tiện ích lõi dùng chung với routing/route_optimizer.py

# SQLite database file
DB_NAME = "user_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS route_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        route_id TEXT,
        rating INTEGER,
        feedback TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS routes (
        route_id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        num_days INTEGER,
        total_km REAL,
        total_stops INTEGER,
        poi_names TEXT,
        user_token TEXT
    )
    """)
    # Migrate existing DBs: add user_token if missing (cần để join feedback → user_weights)
    try:
        cur.execute("ALTER TABLE routes ADD COLUMN user_token TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_weights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_token TEXT,
        cafe_weight REAL DEFAULT 1.0,
        nature_weight REAL DEFAULT 1.0,
        food_weight REAL DEFAULT 1.0,
        checkin_weight REAL DEFAULT 1.0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Migrate existing DBs: add checkin_weight if missing
    try:
        cur.execute("ALTER TABLE user_weights ADD COLUMN checkin_weight REAL DEFAULT 1.0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # NOTE: bảng `preferences` (personalization phiên bản cũ, trước khi chuyển
    # sang `user_weights`) đã bị loại bỏ khỏi init_db() vì không còn endpoint
    # nào ghi/đọc nó (xem git history: b458e3c tạo bảng, c695845 thay thế bằng
    # user_weights, /api/feedback bị bỏ lại mồ côi cho tới khi xóa ở đây).
    # Nếu máy bạn đã có bảng `preferences` trong user_data.db từ trước và muốn
    # dọn sạch luôn, chạy 1 lần (KHÔNG để trong code chạy mỗi lần khởi động app):
    #   sqlite3 user_data.db "DROP TABLE IF EXISTS preferences;"

    conn.commit()
    conn.close()

app = Flask(__name__)
init_db()

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

POI_CSV = "dalat_poi_scored_fix.csv"
DALAT_CENTER  = [11.9404, 108.4583]
AVG_SPEED_KMH = 30

DAY_COLORS = ["#FF6B6B", "#4ECDC4", "#FFE66D", "#A37CFF", "#FF8C42"]

TYPE_VI = {
    "cafe": "Cà phê", "nhà hàng": "Nhà hàng", "chợ quán": "Ăn uống",
    "địa điểm checkin": "Check-in", "thiên nhiên": "Thiên nhiên",
    "quán ăn": "Quán ăn", "homestay": "Homestay", "khách sạn": "Khách sạn", "khác": "Khác",
}
TYPE_EMOJI = {
    "cafe": "☕", "nhà hàng": "🍽️", "chợ quán": "🛒", "địa điểm checkin": "📸",
    "thiên nhiên": "🌿", "quán ăn": "🥢", "homestay": "🏠", "khách sạn": "🏨", "khác": "📍",
}

OUTDOOR_TYPES = {"thiên nhiên", "địa điểm checkin"}

# ══════════════════════════════════════════════════════════════
# HELPERS
# safe_float/safe_int, haversine_km, travel_min, infer_visit_duration và
# giờ mặc định theo loại POI giờ nằm ở routing/core.py (dùng chung với
# routing/route_optimizer.py) — chỉ còn lại phần đặc thù của webapp ở đây
# (đường chim bay "nhanh" cho lúc build lộ trình vs. OSRM "chính xác" cho
# lúc hiển thị kết quả cuối, video URL, tên hàm fmt/_DEFAULT_HOURS cũ).
# ══════════════════════════════════════════════════════════════

safe_float = rcore.safe_float
safe_int = rcore.safe_int
haversine_km = rcore.haversine_km
infer_visit_duration = rcore.infer_visit_duration
_DEFAULT_HOURS = rcore.DEFAULT_HOURS_BY_TYPE

def filter_relevant_video_url(place_name, video_urls_str):
    if not video_urls_str: return ""
    urls = [u.strip() for u in video_urls_str.split("|") if u.strip()]
    return urls[0] if urls else ""

def travel_min(lat1, lng1, lat2, lng2):
    return rcore.travel_min(lat1, lng1, lat2, lng2, speed_kmh=AVG_SPEED_KMH)

# Hàm mới — chỉ gọi sau khi SA xong
def travel_min_accurate(lat1, lng1, lat2, lng2):
    return osrm.travel_min(lat1, lng1, lat2, lng2)

def distance_km_accurate(lat1, lng1, lat2, lng2):
    return osrm.distance_km(lat1, lng1, lat2, lng2)

fmt = rcore.fmt_min

# ══════════════════════════════════════════════════════════════
# LOAD POI
# ══════════════════════════════════════════════════════════════

_poi_cache = None
def load_pois():
    global _poi_cache
    if _poi_cache is not None: return _poi_cache
    with open(POI_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    pois = []
    seen = set()
    for r in rows:
        if str(r.get("include_in_route","")).strip().lower() not in ("true","1"): continue
        lat = safe_float(r.get("lat")); lng = safe_float(r.get("lng"))
        if lat==0 or lng==0: continue
        coord_key = (round(lat,3), round(lng,3))
        if coord_key in seen: continue
        seen.add(coord_key)
        open_min_raw = r.get("open_min",""); close_min_raw = r.get("close_min","")
        open_min = safe_float(open_min_raw) if open_min_raw not in ("","nan","None") else 0
        close_min = safe_float(close_min_raw) if close_min_raw not in ("","nan","None") else 24*60
        if close_min_raw not in ("","nan","None") and close_min == 0 and open_min > 0:
            close_min = 24*60
        poi_type = r.get("type","khác").strip().lower()
        for k in TYPE_VI:
            if k in poi_type: poi_type = k; break
        if open_min_raw in ("","nan","None") or close_min_raw in ("","nan","None"):
            def_open, def_close = _DEFAULT_HOURS.get(poi_type, (7*60,21*60))
            if open_min_raw in ("","nan","None"): open_min = def_open
            if close_min_raw in ("","nan","None"): close_min = def_close
        pois.append({
            "name": r["place_name"], "type": poi_type, "lat": lat, "lng": lng,
            "score": safe_float(r.get("attraction_score")), "rating": safe_float(r.get("gmaps_rating")),
            "reviews": safe_int(r.get("gmaps_reviews_count")), "open_min": open_min, "close_min": close_min,
            "visit_min": infer_visit_duration(poi_type, r.get("gmaps_price_level",""), r.get("gmaps_reviews_count",""), r.get("visit_duration_min",45)),
            "address": r.get("gmaps_address",""), "video_url": filter_relevant_video_url(r["place_name"], r.get("video_urls","")),
            "price": r.get("price_mentions",""), "open_text": r.get("opening_hours_text",""), "anchor": False,
        })
    _poi_cache = pois
    osrm.warmup_cache(pois)   # tải toàn bộ ma trận khoảng cách 1 lần
    return pois

# ══════════════════════════════════════════════════════════════
# OPTIMIZER
# is_feasible/simulate_day dùng chung với routing/route_optimizer.py qua
# routing/core.py. Ở đây chỉ còn phần đặc thù webapp: chọn khoảng cách/thời
# gian "nhanh" (đường chim bay) hay "chính xác" (OSRM) tuỳ cờ `accurate`.
# ══════════════════════════════════════════════════════════════

def is_feasible(poi, arrive, user_end):
    return rcore.is_feasible(poi, arrive, user_end)

def simulate_day(poi_list, user_start, user_end, start_lat=None, start_lng=None, accurate=False):
    distance_fn, duration_fn = (distance_km_accurate, travel_min_accurate) if accurate \
        else (haversine_km, travel_min)
    return rcore.simulate_day(poi_list, user_start, user_end, start_lat, start_lng,
                               distance_fn=distance_fn, duration_fn=duration_fn)

def split_days(route, num_days):
    sorted_route = route
    n=len(sorted_route); days=[]; size=n//num_days; rem=n%num_days; idx=0
    for d in range(num_days):
        end=idx+size+(1 if d<rem else 0); days.append(sorted_route[idx:end]); idx=end
    return days

def route_cost(route, num_days, user_start, user_end, start_lat=None, start_lng=None):
    total_km=0; total_inf=0
    for day_pois in split_days(route, num_days):
        km, feas, _ = simulate_day(day_pois, user_start, user_end, start_lat, start_lng)
        total_km += km; total_inf += len(day_pois)-feas
    return total_km + total_inf*50

WEIGHT_BOUNDS = (0.5, 2.0)
WEIGHT_INCREASE = 0.1   # khi category được chọn
WEIGHT_DECAY    = 0.03  # khi category không được chọn
WEIGHT_FEEDBACK_DELTA = 0.15  # tín hiệu từ rating sao (route_feedback) — mạnh hơn decay, ngang sở thích

def _clamp_weight(w):
    return max(WEIGHT_BOUNDS[0], min(WEIGHT_BOUNDS[1], w))

def get_user_weights(user_token):
    """Trả về dict {cafe, nature, food, checkin} cho user_token."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT cafe_weight, nature_weight, food_weight, checkin_weight FROM user_weights WHERE user_token=?",
        (user_token,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"cafe": 1.0, "nature": 1.0, "food": 1.0, "checkin": 1.0}
    return {"cafe": row[0], "nature": row[1], "food": row[2], "checkin": row[3]}

def get_user_weight(user_token, category):
    """Lấy trọng số cho 1 category của user."""
    if not user_token:
        return 1.0
    weights = get_user_weights(user_token)
    if category == 'cafe':
        return weights["cafe"]
    if category == 'thiên nhiên':
        return weights["nature"]
    if category in ('nhà hàng', 'chợ quán', 'quán ăn'):
        return weights["food"]
    if category == 'địa điểm checkin':
        return weights["checkin"]
    return 1.0

def update_user_weights(user_token, selected_types, increase=None):
    """
    Cập nhật trọng số theo lượt sử dụng:
    - Category được chọn  → tăng `increase` (mặc định WEIGHT_INCREASE)
    - Category không chọn → giảm WEIGHT_DECAY (decay nhẹ)
    - Kết quả được clamp trong WEIGHT_BOUNDS
    - In log ra terminal
    increase=None  → dùng WEIGHT_INCREASE (type pill, 0.1)
    increase=0.15  → dùng cho checkbox sở thích (tín hiệu mạnh hơn)
    """
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT cafe_weight, nature_weight, food_weight, checkin_weight FROM user_weights WHERE user_token=?",
        (user_token,)
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO user_weights (user_token, cafe_weight, nature_weight, food_weight, checkin_weight) VALUES (?,1.0,1.0,1.0,1.0)",
            (user_token,)
        )
        conn.commit()
        cafe_w, nature_w, food_w, checkin_w = 1.0, 1.0, 1.0, 1.0
    else:
        cafe_w, nature_w, food_w, checkin_w = row

    cafe_sel    = "cafe" in selected_types
    nature_sel  = "thiên nhiên" in selected_types
    food_sel    = bool({"nhà hàng", "chợ quán", "quán ăn"} & set(selected_types))
    checkin_sel = "địa điểm checkin" in selected_types

    delta = increase if increase is not None else WEIGHT_INCREASE
    cafe_w    = _clamp_weight(cafe_w    + (delta if cafe_sel    else -WEIGHT_DECAY))
    nature_w  = _clamp_weight(nature_w  + (delta if nature_sel  else -WEIGHT_DECAY))
    food_w    = _clamp_weight(food_w    + (delta if food_sel    else -WEIGHT_DECAY))
    checkin_w = _clamp_weight(checkin_w + (delta if checkin_sel else -WEIGHT_DECAY))

    cur.execute(
        """UPDATE user_weights
           SET cafe_weight=?, nature_weight=?, food_weight=?, checkin_weight=?,
               updated_at=CURRENT_TIMESTAMP
           WHERE user_token=?""",
        (cafe_w, nature_w, food_w, checkin_w, user_token)
    )
    conn.commit()
    conn.close()

    token_short = user_token[:8]
    source = "sở thích" if (increase and increase > WEIGHT_INCREASE) else "loại đ/điểm"
    print(f"  [Weights/{source}] user={token_short}… "
          f"| ☕cafe={cafe_w:.2f}({'↑' if cafe_sel else '↓'}) "
          f"🌿nature={nature_w:.2f}({'↑' if nature_sel else '↓'}) "
          f"🍜food={food_w:.2f}({'↑' if food_sel else '↓'}) "
          f"📸checkin={checkin_w:.2f}({'↑' if checkin_sel else '↓'})")

    return {"cafe": cafe_w, "nature": nature_w, "food": food_w, "checkin": checkin_w}

def _poi_type_to_category(poi_type):
    """Map type POI thô (cafe, thiên nhiên, nhà hàng...) sang 1 trong 4 category
    trọng số cá nhân hóa (cafe/nature/food/checkin), dùng chung logic với get_user_weight."""
    if poi_type == "cafe":
        return "cafe"
    if poi_type == "thiên nhiên":
        return "nature"
    if poi_type in ("nhà hàng", "chợ quán", "quán ăn"):
        return "food"
    if poi_type == "địa điểm checkin":
        return "checkin"
    return None

def get_route_info(route_id):
    """Lấy lại (user_token, poi_names) đã lưu khi tạo route, để join với feedback."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_token, poi_names FROM routes WHERE route_id=?", (route_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None, []
    user_token, poi_names_str = row
    poi_names = poi_names_str.split("|") if poi_names_str else []
    return user_token, poi_names

def categories_in_route(poi_names):
    """Từ danh sách tên POI của 1 route, suy ra tập category (cafe/nature/food/checkin)
    xuất hiện trong route đó, bằng cách tra ngược type qua load_pois()."""
    if not poi_names:
        return set()
    name_to_type = {p["name"]: p["type"] for p in load_pois()}
    cats = set()
    for name in poi_names:
        cat = _poi_type_to_category(name_to_type.get(name))
        if cat:
            cats.add(cat)
    return cats

def apply_feedback_to_weights(route_id, rating):
    """
    ĐÂY LÀ PHẦN TRƯỚC ĐÂY BỊ THIẾU: đọc lại route_feedback và dùng rating (1-5)
    để cập nhật trọng số cá nhân hóa — thay vì trọng số chỉ dựa vào việc người
    dùng có tick chọn loại địa điểm hay không.

    rating >= 4 → người dùng hài lòng → tăng trọng số các category có trong route đó
    rating <= 2 → người dùng không hài lòng → giảm trọng số các category đó
    rating == 3 (hoặc thiếu/không hợp lệ) → trung lập, không đổi
    """
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return
    if rating not in (1, 2, 3, 4, 5) or rating == 3:
        return

    user_token, poi_names = get_route_info(route_id)
    if not user_token:
        return  # route cũ (trước migration) không có user_token → bỏ qua an toàn

    cats = categories_in_route(poi_names)
    if not cats:
        return

    delta = WEIGHT_FEEDBACK_DELTA if rating >= 4 else -WEIGHT_FEEDBACK_DELTA

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT cafe_weight, nature_weight, food_weight, checkin_weight FROM user_weights WHERE user_token=?",
        (user_token,)
    )
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO user_weights (user_token, cafe_weight, nature_weight, food_weight, checkin_weight) VALUES (?,1.0,1.0,1.0,1.0)",
            (user_token,)
        )
        conn.commit()
        cafe_w, nature_w, food_w, checkin_w = 1.0, 1.0, 1.0, 1.0
    else:
        cafe_w, nature_w, food_w, checkin_w = row

    if "cafe" in cats:    cafe_w    = _clamp_weight(cafe_w + delta)
    if "nature" in cats:  nature_w  = _clamp_weight(nature_w + delta)
    if "food" in cats:    food_w    = _clamp_weight(food_w + delta)
    if "checkin" in cats: checkin_w = _clamp_weight(checkin_w + delta)

    cur.execute(
        """UPDATE user_weights
           SET cafe_weight=?, nature_weight=?, food_weight=?, checkin_weight=?,
               updated_at=CURRENT_TIMESTAMP
           WHERE user_token=?""",
        (cafe_w, nature_w, food_w, checkin_w, user_token)
    )
    conn.commit()
    conn.close()

    token_short = user_token[:8]
    print(f"  [Weights/feedback★{rating}] user={token_short}… categories={cats} delta={delta:+.2f} "
          f"→ ☕{cafe_w:.2f} 🌿{nature_w:.2f} 🍜{food_w:.2f} 📸{checkin_w:.2f}")

def save_route_meta(route_id, num_days, total_km, total_stops, poi_names, user_token=None):
    """Lưu metadata của route vừa tạo, để feedback gửi sau đó join lại được
    với đúng nội dung route (thay vì route_id cố định 'dalat_route' như trước).
    Lưu kèm user_token để khi có feedback biết phải cập nhật trọng số cho ai."""
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO routes (route_id, num_days, total_km, total_stops, poi_names, user_token)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (route_id, num_days, total_km, total_stops, "|".join(poi_names), user_token)
    )
    conn.commit()
    conn.close()

# greedy: nearest-best-value dùng chung với routing/route_optimizer.py
# (routing.core.greedy_day) — phần đặc thù ở đây chỉ là score_fn có cộng
# thêm trọng số cá nhân hoá theo user_token.
def greedy(pois, user_start, user_end, start_lat=None, start_lng=None, user_token=None):
    def score_fn(p, dist_km):
        weight = get_user_weight(user_token, p["type"]) if user_token else 1.0
        return (p["score"] * weight) / (dist_km + 0.1)
    return rcore.greedy_day(pois, user_start, user_end, start_lat, start_lng, score_fn=score_fn)

def simulated_annealing(initial, num_days, user_start, user_end, T0=800, alpha=0.995, max_iter=30000, anchor_names=None, start_lat=11.9404, start_lng=108.4583):
    anchor_names = anchor_names or []
    if len(initial) <= 1:
        return initial  # không thể tối ưu khi chỉ có 1 hoặc 0 điểm
    random.seed(42)
    current = list(initial)
    best = list(current)
    anchor_names = anchor_names or []
    if len(initial) <= 1:
        return initial
    random.seed(42)
    current=list(initial); best=list(current)
    cur_cost=route_cost(current,num_days,user_start,user_end,start_lat,start_lng)
    best_cost=cur_cost; T=T0
    for _ in range(max_iter):
        if T<0.01: break
        n=len(current)
        nb=list(current)
        op=random.randint(0,2)
        if op==0:
            i,j=random.sample(range(n),2)
            nb[i],nb[j]=nb[j],nb[i]
        elif op==1:
            i,j=sorted(random.sample(range(n),2))
            nb[i:j+1]=nb[i:j+1][::-1]
        else:
            i=random.randrange(n); j=random.randrange(n-1)
            if j>=i: j+=1
            poi=nb[i]
            if anchor_names and any(a in poi["name"].lower() for a in anchor_names):
                T*=alpha; continue
            nb.pop(i); nb.insert(j,poi)
        nc=route_cost(nb,num_days,user_start,user_end,start_lat,start_lng)
        delta=nc-cur_cost
        if delta<0 or random.random()<math.exp(-delta/T):
            current=nb; cur_cost=nc
            if cur_cost<best_cost: best=list(current); best_cost=cur_cost
        T*=alpha
    return best

# ══════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    user_token = request.cookies.get("user_token")
    if not user_token:
        user_token = str(uuid4())
        resp = make_response(render_template("index.html", user_token=user_token))
        resp.set_cookie("user_token", user_token, max_age=60*60*24*365, httponly=True, samesite='Lax')
        return resp
    return render_template("index.html", user_token=user_token)

@app.route("/api/optimize", methods=["POST"])
def optimize():
    data = request.json
    num_days = max(1, min(int(data.get("num_days",3)), 7))
    top_k = max(num_days*5, min(int(data.get("top_k",40)), 80))
    user_start = int(data.get("start_hour",7))*60
    user_end = int(data.get("end_hour",21))*60
    types_filter = data.get("types", [])
    preferences = data.get("preferences", {})
    anchor_names = [a.strip().lower() for a in data.get("anchor_pois", []) if a.strip()]
    start_loc  = data.get("start_location", None)
    start_lat  = float(start_loc["lat"]) if start_loc else None
    start_lng  = float(start_loc["lng"]) if start_loc else None

    rainy_days = get_rainy_days(num_days)
    print(f"Dự báo thời tiết {num_days} ngày: {rainy_days}")

    all_pois = load_pois()
    for p in all_pois:
        p["anchor"] = any(a in p["name"].lower() for a in anchor_names)

    if types_filter:
        filtered = [p for p in all_pois if p["type"] in types_filter]
        if len(filtered) < num_days*3:
            filtered = all_pois
    else:
        filtered = all_pois

    pref_type_map = {
        'adventure': ['thiên nhiên', 'địa điểm checkin'],
        'relax': ['cafe', 'homestay'],
        'food': ['nhà hàng', 'chợ quán', 'quán ăn'],
        'checkin': ['địa điểm checkin', 'cafe']
    }
    if any(preferences.values()):
        for p in filtered:
            factor = 1.0
            for pref, active in preferences.items():
                if active and p['type'] in pref_type_map.get(pref, []):
                    factor = max(factor, 1.5)
            p['score'] = p['score'] * factor

    anchors = [p for p in filtered if p["anchor"]]
    non_anch = [p for p in filtered if not p["anchor"]]

    if start_lat and start_lng:
        for p in non_anch:
            dist = haversine_km(start_lat, start_lng, p["lat"], p["lng"])
            p["score"] = p["score"] / (1 + dist * 0.05)

    non_anch.sort(key=lambda x: x["score"], reverse=True)
    pois = (anchors + non_anch)[:top_k]

    if any(rainy_days):
        original_count = len(pois)
        pois = [p for p in pois if p['type'] not in OUTDOOR_TYPES]
        print(f"  Do dự báo mưa, đã loại {original_count - len(pois)} POI ngoài trời")

    user_token = request.cookies.get('user_token')
    new_weights = None
    if user_token:
        # Tín hiệu 1: Loại địa điểm (type pill) — increase mặc định 0.1
        if types_filter:
            new_weights = update_user_weights(user_token, types_filter)

        # Tín hiệu 2: Sở thích du lịch (checkbox) — increase mạnh hơn 0.15
        # Map mỗi checkbox → các types POI tương ứng
        PREF_TYPE_MAP = {
            'adventure': ['thiên nhiên', 'địa điểm checkin'],
            'relax':     ['cafe'],
            'food':      ['nhà hàng', 'chợ quán', 'quán ăn'],
            'checkin':   ['địa điểm checkin', 'cafe'],
        }
        pref_types = []
        for pref, active in preferences.items():
            if active:
                pref_types.extend(PREF_TYPE_MAP.get(pref, []))
        if pref_types:
            new_weights = update_user_weights(user_token, list(set(pref_types)), increase=0.15)
            print(f"  [Weights/sở thích] prefs={[k for k,v in preferences.items() if v]} → types={list(set(pref_types))}")

    g_route = greedy(pois, user_start, user_end, start_lat, start_lng, user_token)
    if not g_route:
        return jsonify({
            "route_id": None,
            "days": [],
            "summary": {
                "total_km": 0, "feasible": 0, "total_stops": 0,
                "rate": 0, "num_days": num_days, "anchors": []
            }
        })
    sa_route = simulated_annealing(g_route, num_days, user_start, user_end, anchor_names=anchor_names, start_lat=start_lat, start_lng=start_lng)

    days_data = []
    total_km=0; total_feas=0; total_stops=0
    for d, day_pois in enumerate(split_days(sa_route, num_days), 1):
        km, feas, timeline = simulate_day(day_pois, user_start, user_end, start_lat if d==1 else None, start_lng if d==1 else None, accurate=True)
        total_km+=km; total_feas+=feas; total_stops+=len(day_pois)
        color = DAY_COLORS[(d-1) % len(DAY_COLORS)]
        stops = []
        for idx, s in enumerate(timeline, 1):
            stops.append({
                "idx": idx, "name": s["name"], "type": s["type"], "type_vi": TYPE_VI.get(s["type"], s["type"]),
                "emoji": TYPE_EMOJI.get(s["type"], "📍"), "lat": s["lat"], "lng": s["lng"],
                "start": fmt(s["start"]), "end": fmt(s["end"]), "feasible": s["feasible"],
                "km": s["km"], "rating": s["rating"], "score": round(s["score"],3),
                "address": s["address"], "video_url": s["video_url"], "price": s["price"],
                "anchor": s.get("anchor", False), "visit_min": s.get("visit_min", 45),
            })
        days_data.append({"day": d, "color": color, "km": round(km,1), "feasible": feas, "total": len(day_pois), "stops": stops})

    route_id = str(uuid4())
    poi_names_flat = [s["name"] for d in days_data for s in d["stops"]]
    save_route_meta(route_id, num_days, round(total_km, 1), total_stops, poi_names_flat, user_token)

    return jsonify({
        "route_id": route_id,
        "days": days_data,
        "summary": {
            "total_km": round(total_km,1), "feasible": total_feas, "total_stops": total_stops,
            "rate": round(total_feas/total_stops*100) if total_stops else 0, "num_days": num_days,
            "anchors": [p["name"] for p in pois if p["anchor"]],
            "start_location": start_loc,
        },
        "weather": {
            "rainy_days": rainy_days,
            "outdoor_removed": any(rainy_days),
        },
        "weights": new_weights,
    })

@app.route("/api/user_weights", methods=["GET"])
def api_get_user_weights():
    user_token = request.cookies.get("user_token")
    if not user_token:
        return jsonify({"cafe": 1.0, "nature": 1.0, "food": 1.0, "checkin": 1.0})
    return jsonify(get_user_weights(user_token))

@app.route("/api/user_weights/reset", methods=["POST"])
def api_reset_user_weights():
    user_token = request.cookies.get("user_token")
    if not user_token:
        return jsonify({"success": False, "message": "Không tìm thấy user token"})
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        """UPDATE user_weights
           SET cafe_weight=1.0, nature_weight=1.0, food_weight=1.0, checkin_weight=1.0,
               updated_at=CURRENT_TIMESTAMP
           WHERE user_token=?""",
        (user_token,)
    )
    if cur.rowcount == 0:
        cur.execute(
            "INSERT INTO user_weights (user_token, cafe_weight, nature_weight, food_weight, checkin_weight) VALUES (?,1.0,1.0,1.0,1.0)",
            (user_token,)
        )
    conn.commit()
    conn.close()
    print(f"  [Weights] user={user_token[:8]}… → RESET về 1.0 tất cả")
    return jsonify({"success": True, "weights": {"cafe": 1.0, "nature": 1.0, "food": 1.0, "checkin": 1.0}})

@app.route("/api/search_poi")
def search_poi():
    q = request.args.get("q","").strip().lower()
    pois = load_pois()
    if not q:
        results = sorted(pois, key=lambda x: x["score"], reverse=True)[:20]
    else:
        results = [p for p in pois if q in p["name"].lower()][:15]
    return jsonify([{"name": p["name"], "type": TYPE_VI.get(p["type"], p["type"]), "emoji": TYPE_EMOJI.get(p["type"], "📍"), "rating": p["rating"], "score": round(p["score"],3)} for p in results])

@app.route("/api/poi_types")
def poi_types():
    pois = load_pois()
    types = sorted(set(p["type"] for p in pois))
    return jsonify([{"value": t, "label": TYPE_VI.get(t,t), "emoji": TYPE_EMOJI.get(t,"📍")} for t in types])
  
@app.route("/api/route_polyline", methods=["POST"])
def route_polyline():
    data = request.json
    coords = data.get("coords", [])
    if len(coords) < 2:
        return jsonify({"polyline": []})
    coord_str = ";".join(f"{c['lng']},{c['lat']}" for c in coords)
    url = f"http://router.project-osrm.org/route/v1/driving/{coord_str}?overview=full&geometries=geojson"
    try:
        resp = requests.get(url, timeout=10)
        result = resp.json()
        if result.get("code") == "Ok":
            geometry = result["routes"][0]["geometry"]["coordinates"]
            polyline = [[p[1], p[0]] for p in geometry]
            return jsonify({"polyline": polyline})
    except:
        pass
    return jsonify({"polyline": []})

@app.route("/submit-feedback", methods=["POST"])
def submit_feedback():
    data = request.json or {}
    rating = data.get("rating")
    feedback_text = data.get("feedback", "")
    route_id = data.get("route_id") or "unknown"
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO route_feedback (route_id, rating, feedback) VALUES (?, ?, ?)",
        (route_id, rating, feedback_text)
    )
    conn.commit()
    conn.close()

    # Đọc lại rating vừa lưu để cập nhật trọng số cá nhân hóa (trước đây bị bỏ sót)
    apply_feedback_to_weights(route_id, rating)

    return jsonify({"success": True, "message": "Cảm ơn bạn đã đánh giá hành trình!"})


if __name__ == "__main__":
    if not os.path.exists(POI_CSV):
        print(f"❌ Không tìm thấy {POI_CSV}")
        print("   Chạy scoring/scoring.py trước để tạo file này.")
    else:
        print("\n" + "="*50)
        print("  🏔  Đà Lạt Route Planner (bố cục thông minh)")
        print("="*50)
        print(f"  Mở trình duyệt: http://localhost:5000")
        print("  Ctrl+C để dừng server")
        print("="*50 + "\n")
        app.run(debug=False, port=5000)  