"""
=============================================================
GIAI ĐOẠN 7 — Web App lộ trình Đà Lạt (bố cục thông minh)
=============================================================
Yêu cầu: pip install flask folium

Chạy: python app.py
Mở trình duyệt: http://localhost:5000
=============================================================

feat: tích hợp form đánh giá hành trình (feedback) và thông báo popup cảm ơn

- Thêm form feedback chỉ hiển thị sau khi tạo lộ trình, gồm chọn sao (1-5) và nhập nhận xét
- Gửi đánh giá đến endpoint /submit-feedback, lưu vào database route_feedback
- Hiển thị popup cảm ơn sau khi gửi thành công, tự động ẩn form
- Cải thiện giao diện popup marker: hiển thị rating dạng sao ★★★☆☆
- Thêm hiệu ứng hover và chọn sao trong feedback"
"""

from flask import Flask, render_template_string, request, jsonify, make_response
import csv, math, random, json, os, sqlite3
from uuid import uuid4
from weather import get_rainy_days

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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS preferences (
        user_id TEXT,
        category TEXT,
        weight REAL,
        PRIMARY KEY (user_id, category)
    )
    """)

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
# ══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try: return float(v) if str(v) not in ("","nan","None") else d
    except: return d

def safe_int(v, d=0):
    try: return int(float(v)) if str(v) not in ("","nan","None") else d
    except: return d

def filter_relevant_video_url(place_name, video_urls_str):
    if not video_urls_str: return ""
    urls = [u.strip() for u in video_urls_str.split("|") if u.strip()]
    return urls[0] if urls else ""

def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2-lat1); dlng = math.radians(lng2-lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R*2*math.asin(math.sqrt(a))

def travel_min(lat1, lng1, lat2, lng2):
    return haversine_km(lat1,lng1,lat2,lng2)/AVG_SPEED_KMH*60

def fmt(minutes):
    if minutes is None: return "?"
    h=int(minutes)//60%24; m=int(minutes)%60
    return f"{h:02d}:{m:02d}"

_BASE_DURATION = {"cafe":55, "nhà hàng":65, "chợ quán":40, "địa điểm checkin":25, "thiên nhiên":85, "quán ăn":50, "khác":40}
_PRICE_ADJUST = {0:-5,1:0,2:5,3:15,4:20}

def infer_visit_duration(poi_type, price_level, reviews_count, csv_value):
    csv_int = safe_int(csv_value, 0)
    if csv_int > 0 and csv_int != 45: return csv_int
    base = _BASE_DURATION.get(poi_type.strip().lower(), 40)
    base += _PRICE_ADJUST.get(safe_int(price_level,1),0)
    reviews = safe_int(reviews_count,0)
    if reviews > 5000: base+=15
    elif reviews > 1000: base+=8
    elif reviews > 500: base+=3
    elif reviews < 50: base-=5
    return max(15, base)

_DEFAULT_HOURS = {
    "thiên nhiên": (7*60,18*60), "địa điểm checkin": (6*60,20*60),
    "cafe": (7*60,22*60), "nhà hàng": (10*60,22*60),
    "chợ quán": (6*60,22*60), "khác": (7*60,21*60),
}

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
    return pois

# ══════════════════════════════════════════════════════════════
# OPTIMIZER (giữ nguyên)
# ══════════════════════════════════════════════════════════════

def is_feasible(poi, arrive, user_end):
    start = max(arrive, poi["open_min"])
    end = start + poi["visit_min"]
    return (end <= poi["close_min"] and end <= user_end), start, end

def simulate_day(poi_list, user_start, user_end, start_lat=None, start_lng=None):
    cur_time = user_start
    cur_lat = start_lat if start_lat else DALAT_CENTER[0]
    cur_lng = start_lng if start_lng else DALAT_CENTER[1]
    total_km = 0.0
    feasible = 0
    timeline = []
    for poi in poi_list:
        tm = travel_min(cur_lat, cur_lng, poi["lat"], poi["lng"])
        arrive = cur_time + tm
        ok, start, end = is_feasible(poi, arrive, user_end)
        km = haversine_km(cur_lat, cur_lng, poi["lat"], poi["lng"])
        total_km += km
        timeline.append({**poi, "arrive":arrive, "start":start, "end":end, "feasible":ok, "km":round(km,2)})
        cur_time = end if ok else arrive
        cur_lat, cur_lng = poi["lat"], poi["lng"]
        if ok: feasible += 1
    return total_km, feasible, sorted(timeline, key=lambda x: x["start"])

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

def greedy(pois, user_start, user_end, start_lat=None, start_lng=None, user_token=None):
    unvisited = list(pois); route = []
    cur_lat = start_lat if start_lat else DALAT_CENTER[0]
    cur_lng = start_lng if start_lng else DALAT_CENTER[1]
    cur_time = user_start
    while unvisited:
        best=None; best_val=-1
        for p in unvisited:
            tm=travel_min(cur_lat,cur_lng,p["lat"],p["lng"])
            ok,start,end=is_feasible(p, cur_time+tm, user_end)
            if not ok: continue
            dist=haversine_km(cur_lat,cur_lng,p["lat"],p["lng"])
            weight = get_user_weight(user_token, p["type"]) if user_token else 1.0
            val = (p["score"] * weight) / (dist + 0.1)
            if val>best_val: best_val=val; best=p
        if best is None:
            best=min(unvisited, key=lambda p: haversine_km(cur_lat,cur_lng,p["lat"],p["lng"]))
        route.append(best)
        tm=travel_min(cur_lat,cur_lng,best["lat"],best["lng"])
        _,_,end=is_feasible(best, cur_time+tm, user_end)
        cur_time=end; cur_lat,cur_lng=best["lat"],best["lng"]
        unvisited.remove(best)
    return route

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
        resp = make_response(render_template_string(HTML_TEMPLATE, user_token=user_token))
        resp.set_cookie("user_token", user_token, max_age=60*60*24*365, httponly=True, samesite='Lax')
        return resp
    return render_template_string(HTML_TEMPLATE, user_token=user_token)

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
        km, feas, timeline = simulate_day(day_pois, user_start, user_end, start_lat if d==1 else None, start_lng if d==1 else None)
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

    return jsonify({
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

@app.route("/api/feedback", methods=["POST"])
def feedback():
    data = request.json
    user_id = data["user_id"]
    category = data["category"]
    rating = data["rating"]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT weight FROM preferences WHERE user_id=? AND category=?
    """, (user_id, category))
    row = cursor.fetchone()
    if row:
        old_weight = row[0]
        new_weight = min(1.0, max(0.0, old_weight + (rating / 5 - 0.5) * 0.1))
        cursor.execute("""
            UPDATE preferences SET weight=? WHERE user_id=? AND category=?
        """, (new_weight, user_id, category))
    else:
        new_weight = rating / 5
        cursor.execute("""
            INSERT INTO preferences (user_id, category, weight) VALUES (?, ?, ?)
        """, (user_id, category, new_weight))
    conn.commit()
    conn.close()
    return jsonify({"message": "feedback saved", "new_weight": new_weight})

@app.route("/submit-feedback", methods=["POST"])
def submit_feedback():
    data = request.json or {}
    rating = data.get("rating")
    feedback_text = data.get("feedback", "")
    route_id = data.get("route_id", "default")
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO route_feedback (route_id, rating, feedback) VALUES (?, ?, ?)",
        (route_id, rating, feedback_text)
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Cảm ơn bạn đã đánh giá hành trình!"})

# ══════════════════════════════════════════════════════════════
# HTML TEMPLATE (đã sửa CSS popup và sao)
# ══════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Đà Lạt Route Planner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,300;14..32,400;14..32,500;14..32,600;14..32,700&family=Playfair+Display:wght@600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root {
  --bg: #0a0c12;
  --surface: #141822;
  --surface-light: #1e2432;
  --border: #2a2f3f;
  --accent: #d4b87a;
  --accent-dark: #b89a5c;
  --accent-glow: rgba(212,184,122,0.2);
  --text: #f0f2f5;
  --text-muted: #9ca3af;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'Inter', sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  display: flex;
  overflow: hidden;
}
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
#sidebar {
  width: 420px;
  min-width: 360px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  height: 100vh;
  overflow-y: auto;
  overflow-x: hidden;
  z-index: 10;
}
.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(135deg, #0a0c12 0%, #141822 100%);
}
.sidebar-header h1 {
  font-family: 'Playfair Display', serif;
  font-size: 22px;
  background: linear-gradient(135deg, var(--accent), #f5e6c4);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.toggle-form-btn {
  background: rgba(212,184,122,0.2);
  border: 1px solid var(--accent);
  border-radius: 30px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  color: var(--accent);
  cursor: pointer;
  transition: 0.2s;
}
.toggle-form-btn:hover {
  background: var(--accent);
  color: #0a0c12;
}
#form-section {
  background: rgba(20,24,34,0.6);
  border-bottom: 1px solid var(--border);
  transition: max-height 0.4s ease, padding 0.3s;
  overflow-y: auto;
  padding: 0 20px;
}
#form-section.collapsed {
  max-height: 0 !important;
  padding: 0 20px !important;
  border-bottom: none;
}
#form-inner {
  padding: 20px 0;
}
.form-row-compact {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.form-group {
  flex: 1;
}
.form-group label {
  display: block;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}
.form-group input, .form-group select {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text);
  padding: 8px 10px;
  font-size: 13px;
  font-family: 'Inter', sans-serif;
}
#type-filters { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0; }
.type-pill {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 30px;
  padding: 4px 10px;
  font-size: 11px;
  cursor: pointer;
}
.type-pill.active { background: var(--accent); border-color: var(--accent); color: #0a0c12; }
.preference-group {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin: 8px 0 12px;
}
.preference-group label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 8px;
  cursor: pointer;
}
#anchor-section {
  margin: 8px 0 12px;
}
#anchor-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text);
  padding: 8px 10px 8px 32px;
  font-size: 13px;
  font-family: 'Inter', sans-serif;
  width: 100%;
  outline: none;
  transition: border-color 0.2s;
  background-image: url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="%23d4b87a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>');
  background-repeat: no-repeat;
  background-position: 10px center;
}
#anchor-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-glow);
}
#anchor-drop {
  background: var(--surface);
  border: 1px solid var(--accent);
  border-radius: 12px;
  box-shadow: 0 8px 20px rgba(0,0,0,0.5);
  overflow: hidden;
}
.anchor-drop-item {
  padding: 10px 14px;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  gap: 12px;
  align-items: center;
  transition: background 0.15s;
  border-bottom: 1px solid var(--border);
}
.anchor-drop-item:last-child { border-bottom: none; }
.anchor-drop-item:hover { background: var(--surface-light); }
.anchor-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(212,184,122,0.15);
  border: 1px solid var(--accent);
  border-radius: 40px;
  padding: 4px 10px 4px 12px;
  font-size: 12px;
  color: var(--accent);
}
.anchor-tag button {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 16px;
  cursor: pointer;
  margin-left: 4px;
  line-height: 1;
}
.anchor-tag button:hover {
  color: #ff5e5e;
}
#start-location-section {
  margin-bottom: 8px;
  padding: 8px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
}
#start-location-section .sl-row {
  display: flex; gap: 8px; align-items: center; margin-top: 6px;
}
#start-location-section .sl-badge {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--accent);
  background: rgba(212,184,122,0.1); border: 1px solid var(--accent);
  border-radius: 6px; padding: 3px 8px; flex: 1;
}
#start-location-section .sl-btn {
  background: var(--surface-light); border: 1px solid var(--border);
  color: var(--text-muted); border-radius: 6px; padding: 4px 8px;
  font-size: 11px; cursor: pointer; white-space: nowrap;
}
#start-location-section .sl-btn:hover { border-color: var(--accent); color: var(--accent); }
#btn-optimize {
  width: 100%;
  background: linear-gradient(135deg, var(--accent), var(--accent-dark));
  color: #0a0c12;
  border: none;
  border-radius: 12px;
  padding: 10px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  margin-top: 12px;
}
#summary-bar {
  display: none;
  padding: 12px 20px;
  background: rgba(212,184,122,0.08);
  border-bottom: 1px solid var(--border);
  justify-content: space-around;
  text-align: center;
}
.summary-item {
  flex: 1;
}
.summary-item .label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
}
.summary-item .value {
  font-size: 22px;
  font-weight: 700;
  color: var(--accent);
  line-height: 1.2;
}
.summary-item .unit {
  font-size: 12px;
  color: var(--text-muted);
}
#weather-banner {
  padding: 8px 16px;
  background: rgba(96,165,250,0.1);
  border-bottom: 1px solid rgba(96,165,250,0.3);
  font-size: 11px;
  color: #93c5fd;
  display: none;
}
#timeline {
  flex: 1;
  overflow-y: auto;
  padding: 0;
}
.day-section { border-bottom: 1px solid var(--border); }
.day-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  background: var(--surface-light);
  cursor: pointer;
}
.day-dot { width: 10px; height: 10px; border-radius: 50%; }
.day-header h3 { font-size: 14px; font-weight: 600; flex: 1; }
.day-stats { font-size: 10px; color: var(--text-muted); display: flex; gap: 12px; }
.stop-item {
  display: flex;
  gap: 12px;
  padding: 8px 20px 8px 36px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: 0.25s;
  border: 1px solid rgba(255,255,255,0.05);
}
.stop-item:hover {
  transform: translateX(8px);
  background: rgba(255,255,255,0.05);
  box-shadow: 0 8px 24px rgba(0,0,0,0.25);
}
.stop-num {
  width: 24px; height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 700;
  color: white;
}
.stop-body { flex: 1; min-width: 0; }
.stop-name { font-size: 14px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stop-meta { font-size: 11px; color: var(--text-muted); margin-top: 4px; display: flex; flex-wrap: wrap; gap: 10px; }
/* Popup marker - không viền trắng dày, bóng mờ nhẹ */
.map-popup {
  background: rgba(20,20,25,0.95);
  color: white;
  border-radius: 12px;
  padding: 6px 10px;
  backdrop-filter: blur(6px);
  border: none;
  box-shadow: 0 2px 6px rgba(0,0,0,0.3);
  font-size: 12px;
  line-height: 1.4;
}
.map-popup b {
  font-size: 14px;
  color: #f0d79a;
}
.map-popup .meta {
  opacity: 0.8;
  margin: 2px 0;
  font-size: 11px;
}
.map-popup .rating-stars {
  display: inline-block;
  font-size: 12px;
  letter-spacing: 2px;
  color: #ffcc00;
}
.stop-time { color: var(--accent); font-weight: 500; }
.badge-infeasible { background: rgba(255,94,94,0.15); color: #ff5e5e; padding: 2px 8px; border-radius: 20px; font-size: 10px; }
.badge-anchor { background: rgba(212,184,122,0.15); color: var(--accent); padding: 2px 8px; border-radius: 20px; font-size: 10px; }
#map-container { flex: 1; position: relative; }
#map { width: 100%; height: 100%; }
#loading {
  display: none;
  position: absolute;
  inset: 0;
  background: rgba(10,12,18,0.9);
  backdrop-filter: blur(4px);
  z-index: 1000;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 16px;
}
#loading.show { display: flex; }
.spinner { width: 40px; height: 40px; border: 3px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
/* Feedback container */
#feedback-container {
  display: none;
  padding: 16px;
  border-top: 1px solid var(--border);
}
.feedback-box {
  background: var(--surface-light);
  border-radius: 12px;
  padding: 12px;
}
.feedback-box h3 { font-size: 14px; margin-bottom: 8px; }
.stars {
  display: flex;
  gap: 4px;
  margin: 8px 0;
}
.star {
  cursor: pointer;
  font-size: 24px;
  transition: 0.1s;
  display: inline-block;
}
.star.selected {
  color: #ffcc00;
  filter: drop-shadow(0 0 2px gold);
}
.star:hover {
  transform: scale(1.1);
}
.popup {
  display: none;
  position: fixed;
  z-index: 999;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.5);
  justify-content: center;
  align-items: center;
}
.popup-content {
  background: white;
  padding: 30px;
  border-radius: 20px;
  text-align: center;
  width: 320px;
  animation: popupShow 0.3s ease;
  color: #222;
}
.popup-content h2 { color: #ff4d6d; font-size: 32px; margin-bottom: 10px; }
.popup-content p { color: #444; font-size: 18px; line-height: 1.5; }
.popup-content button { margin-top: 15px; background: #ff4d6d; color: white; border: none; padding: 10px 18px; border-radius: 10px; cursor: pointer; }
@keyframes popupShow { from { transform: scale(0.7); opacity: 0; } to { transform: scale(1); opacity: 1; } }

/* ── Toast thông báo trọng số ── */
#weight-toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: var(--surface-light);
  border: 1px solid var(--accent);
  border-radius: 12px;
  padding: 12px 16px;
  min-width: 260px;
  max-width: 340px;
  z-index: 9999;
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  transform: translateY(20px);
  opacity: 0;
  transition: opacity 0.3s, transform 0.3s;
  pointer-events: none;
}
#weight-toast.show {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}
#weight-toast .toast-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 8px;
}
.weight-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  margin: 4px 0;
  color: var(--text);
}
.weight-bar-bg {
  flex: 1;
  height: 5px;
  background: var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.weight-bar-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.4s ease;
}
.weight-val {
  font-size: 11px;
  color: var(--text-muted);
  width: 34px;
  text-align: right;
}
.weight-arrow { font-size: 12px; width: 14px; }

/* ── Panel trọng số trong sidebar ── */
#weights-panel {
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  display: none;
}
#weights-panel .wp-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
#weights-panel .wp-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
#weights-panel .wp-reset {
  font-size: 11px;
  color: var(--accent);
  background: none;
  border: 1px solid var(--accent);
  border-radius: 20px;
  padding: 2px 10px;
  cursor: pointer;
  transition: 0.2s;
}
#weights-panel .wp-reset:hover {
  background: var(--accent);
  color: #0a0c12;
}
</style>
</head>
<body>

<div id="sidebar">
  <div class="sidebar-header">
    <h1>🏔 Đà Lạt Planner</h1>
    <div class="toggle-form-btn" onclick="toggleForm()" id="toggleBtn">▲ Thu gọn</div>
  </div>
  <div id="form-section" style="max-height: 500px;">
    <div id="form-inner">
      <div class="form-row-compact">
        <div class="form-group"><label>Số ngày</label><input type="number" id="num_days" value="3" min="1" max="5"></div>
        <div class="form-group"><label>Số POI</label><input type="number" id="top_k" value="40" min="10" max="80"></div>
        <div class="form-group"><label>Bắt đầu</label><select id="start_hour"><option value="6">06:00</option><option value="7" selected>07:00</option><option value="8">08:00</option><option value="9">09:00</option></select></div>
        <div class="form-group"><label>Kết thúc</label><select id="end_hour"><option value="19">19:00</option><option value="20">20:00</option><option value="21" selected>21:00</option><option value="22">22:00</option></select></div>
      </div>
      <label>🧭 Sở thích du lịch</label>
      <div class="preference-group">
        <label><input type="checkbox" id="pref_adventure"> 🏔 Mạo hiểm</label>
        <label><input type="checkbox" id="pref_relax"> 🌿 Thư giãn</label>
        <label><input type="checkbox" id="pref_food"> 🍜 Ăn uống</label>
        <label><input type="checkbox" id="pref_checkin"> 📸 Check-in</label>
      </div>
      <label>Loại địa điểm</label>
      <div id="type-filters"><div class="type-pill active" data-type="all">✨ Tất cả</div></div>
      <label>📌 Điểm bắt buộc</label>
      <div id="anchor-section">
        <div style="position:relative;"><input type="text" id="anchor-input" placeholder="Tìm địa điểm..." autocomplete="off"><div id="anchor-drop"></div></div>
        <div id="anchor-tags" style="display:flex; flex-wrap:wrap; gap:8px; margin-top:8px;"></div>
      </div>
      <div id="start-location-section">
        <label>📍 Điểm xuất phát</label>
        <div class="sl-row">
          <div class="sl-badge" id="sl-badge">🏔 Mặc định: Trung tâm Đà Lạt</div>
          <button class="sl-btn" onclick="detectLocation()">📡 Vị trí của tôi</button>
          <button class="sl-btn" onclick="pickOnMap()">🗺 Chọn trên bản đồ</button>
          <button class="sl-btn" id="sl-clear" onclick="clearStartLocation()" style="display:none;">✕</button>
        </div>
      </div>
      <button id="btn-optimize" onclick="optimize()">🗺 Tối ưu lộ trình</button>
    </div>
  </div>

  <!-- Panel trọng số cá nhân -->
  <div id="weights-panel">
    <div class="wp-header">
      <span class="wp-title">🧠 Trọng số sở thích của bạn</span>
      <button class="wp-reset" onclick="resetWeights()">↺ Reset</button>
    </div>
    <div class="weight-row"><span>☕ Cà phê</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="wb-cafe" style="width:50%"></div></div><span class="weight-val" id="wv-cafe">1.00</span></div>
    <div class="weight-row"><span>🌿 Thiên nhiên</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="wb-nature" style="width:50%"></div></div><span class="weight-val" id="wv-nature">1.00</span></div>
    <div class="weight-row"><span>🍜 Ăn uống</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="wb-food" style="width:50%"></div></div><span class="weight-val" id="wv-food">1.00</span></div>
    <div class="weight-row"><span>📸 Check-in</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="wb-checkin" style="width:50%"></div></div><span class="weight-val" id="wv-checkin">1.00</span></div>
  </div>

  <div id="summary-bar" style="display:none;">
    <div class="summary-item"><div class="label">Tổng km</div><div class="value" id="s-km">-</div><div class="unit">km</div></div>
    <div class="summary-item"><div class="label">Đúng giờ</div><div class="value" id="s-rate">-</div><div class="unit">%</div></div>
    <div class="summary-item"><div class="label">Địa điểm</div><div class="value" id="s-stops">-</div><div class="unit">/ -</div></div>
  </div>

  <div id="weather-banner"></div>
  <div id="timeline">
    <div style="padding:40px 20px; text-align:center; color:var(--text-muted);">⬅️ Nhập thông tin và bấm tạo lộ trình</div>
  </div>

  <div id="feedback-container">
    <div class="feedback-box">
      <h3>⭐ Đánh giá hành trình</h3>
      <div class="stars">
        <span class="star" data-value="1">⭐</span>
        <span class="star" data-value="2">⭐</span>
        <span class="star" data-value="3">⭐</span>
        <span class="star" data-value="4">⭐</span>
        <span class="star" data-value="5">⭐</span>
      </div>
      <textarea id="feedbackText" placeholder="Chia sẻ trải nghiệm của bạn..." style="width:100%;min-height:80px;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px;border-radius:8px;"></textarea>
      <button onclick="submitFeedback()" style="margin-top:8px;width:100%;background:linear-gradient(135deg,var(--accent),var(--accent-dark));color:#0a0c12;border:none;border-radius:8px;padding:10px;font-weight:700;cursor:pointer;">Gửi đánh giá</button>
      <p id="feedbackMessage" style="margin-top:8px;color:var(--text-muted);"></p>
    </div>
  </div>
</div>

<div id="map-container">
  <div id="loading"><div class="spinner"></div><p>Đang tính toán lộ trình...</p></div>
  <div id="map"></div>
</div>

<!-- Toast trọng số -->
<div id="weight-toast">
  <div class="toast-title">🧠 Hồ sơ sở thích đã cập nhật</div>
  <div class="weight-row"><span>☕ Cà phê</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="twb-cafe" style="width:50%"></div></div><span class="weight-arrow" id="twa-cafe"></span><span class="weight-val" id="twv-cafe">1.00</span></div>
  <div class="weight-row"><span>🌿 Thiên nhiên</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="twb-nature" style="width:50%"></div></div><span class="weight-arrow" id="twa-nature"></span><span class="weight-val" id="twv-nature">1.00</span></div>
  <div class="weight-row"><span>🍜 Ăn uống</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="twb-food" style="width:50%"></div></div><span class="weight-arrow" id="twa-food"></span><span class="weight-val" id="twv-food">1.00</span></div>
  <div class="weight-row"><span>📸 Check-in</span><div class="weight-bar-bg"><div class="weight-bar-fill" id="twb-checkin" style="width:50%"></div></div><span class="weight-arrow" id="twa-checkin"></span><span class="weight-val" id="twv-checkin">1.00</span></div>
</div>

<div id="thankPopup" class="popup">
  <div class="popup-content">
    <h2>💖 Cảm ơn bạn!</h2>
    <p>Chúc bạn có trải nghiệm tuyệt vời cùng TikTokRoute!</p>
    <button onclick="closePopup()">Đóng</button>
  </div>
</div>

<script>
const USER_TOKEN = "{{ user_token }}";
const map = L.map('map', { zoomControl: false }).setView([11.9404, 108.4583], 13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { attribution: '© CartoDB', maxZoom: 19 }).addTo(map);
L.control.zoom({ position: 'bottomright' }).addTo(map);

let allLayers = [];
let selectedTypes = new Set();
let anchorPOIs = [];
let startLocation = null;
let pickingStartLocation = false;
let startMarker = null;
let selectedRating = 0;

function toggleForm() {
  const formSection = document.getElementById('form-section');
  const btn = document.getElementById('toggleBtn');
  if (formSection.classList.contains('collapsed')) {
    formSection.classList.remove('collapsed');
    btn.innerHTML = '▲ Thu gọn';
  } else {
    formSection.classList.add('collapsed');
    btn.innerHTML = '▼ Mở rộng';
  }
}

function detectLocation() {
  if (!navigator.geolocation) { alert('Trình duyệt không hỗ trợ định vị.'); return; }
  const badge = document.getElementById('sl-badge');
  badge.textContent = '📡 Đang xác định vị trí...';
  navigator.geolocation.getCurrentPosition(
    pos => setStartLocation(pos.coords.latitude, pos.coords.longitude, '📡 Vị trí của tôi'),
    err => { badge.textContent = '🏔 Mặc định: Trung tâm Đà Lạt'; alert('Không lấy được vị trí: ' + err.message); },
    { timeout: 8000 }
  );
}

function pickOnMap() {
  pickingStartLocation = true;
  document.getElementById('sl-badge').textContent = '🖱 Bấm vào bản đồ để chọn điểm xuất phát...';
  map.getContainer().style.cursor = 'crosshair';
}

function setStartLocation(lat, lng, label) {
  startLocation = { lat, lng };
  document.getElementById('sl-badge').innerHTML = '📍 ' + label + ' (' + lat.toFixed(4) + ', ' + lng.toFixed(4) + ')';
  document.getElementById('sl-clear').style.display = 'block';
  if (startMarker) map.removeLayer(startMarker);
  const icon = L.divIcon({ className: '', html: '<div style="background:#d4b87a;color:#0a0c12;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:14px;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.5);">🏁</div>', iconSize:[28,28], iconAnchor:[14,14] });
  startMarker = L.marker([lat, lng], {icon}).bindPopup('📍 Điểm xuất phát').addTo(map);
  map.setView([lat, lng], 15);
}

function clearStartLocation() {
  startLocation = null;
  document.getElementById('sl-badge').textContent = '🏔 Mặc định: Trung tâm Đà Lạt';
  document.getElementById('sl-clear').style.display = 'none';
  if (startMarker) { map.removeLayer(startMarker); startMarker = null; }
}

map.on('click', function(e) {
  if (!pickingStartLocation) return;
  pickingStartLocation = false;
  map.getContainer().style.cursor = '';
  setStartLocation(e.latlng.lat, e.latlng.lng, 'Điểm chọn trên bản đồ');
});

fetch('/api/poi_types').then(r=>r.json()).then(types => {
  const container = document.getElementById('type-filters');
  types.forEach(t => {
    const pill = document.createElement('div');
    pill.className = 'type-pill';
    pill.dataset.type = t.value;
    pill.textContent = t.emoji + ' ' + t.label;
    pill.onclick = () => toggleType(t.value, pill);
    container.appendChild(pill);
  });
});

function toggleType(type, pill) {
  if (type === 'all') {
    selectedTypes.clear();
    document.querySelectorAll('.type-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    return;
  }
  document.querySelector('[data-type=all]').classList.remove('active');
  if (selectedTypes.has(type)) {
    selectedTypes.delete(type);
    pill.classList.remove('active');
    if (selectedTypes.size === 0) document.querySelector('[data-type=all]').classList.add('active');
  } else {
    selectedTypes.add(type);
    pill.classList.add('active');
  }
}

let anchorDebounce;
async function searchAnchor(q) {
  clearTimeout(anchorDebounce);
  const drop = document.getElementById('anchor-drop');
  if (!q.trim()) { drop.style.display='none'; return; }
  anchorDebounce = setTimeout(async () => {
    const res = await fetch('/api/search_poi?q='+encodeURIComponent(q));
    const items = await res.json();
    if (!items.length) { drop.style.display='none'; return; }
    drop.innerHTML = items.map(it => `<div class="anchor-drop-item" onclick="addAnchor('${esc(it.name)}','${it.emoji}','${esc(it.type)}')"><span>${it.emoji}</span><span style="flex:1">${it.name}</span><span style="color:var(--text-muted);font-size:11px">⭐${it.rating}</span></div>`).join('');
    drop.style.display='block';
  }, 220);
}
function addAnchor(name, emoji, type_vi) {
  if (anchorPOIs.find(a=>a.name===name)) { closeAnchorDrop(); return; }
  anchorPOIs.push({name, emoji, type_vi});
  renderAnchorTags();
  document.getElementById('anchor-input').value = '';
  closeAnchorDrop();
}
function removeAnchor(name) { anchorPOIs = anchorPOIs.filter(a=>a.name!==name); renderAnchorTags(); }
function renderAnchorTags() {
  const container = document.getElementById('anchor-tags');
  container.innerHTML = anchorPOIs.map(a => `<div class="anchor-tag">${a.emoji} ${a.name}<button onclick="removeAnchor('${esc(a.name)}')">×</button></div>`).join('');
}
function closeAnchorDrop() { document.getElementById('anchor-drop').style.display='none'; }
document.addEventListener('click', e => { if (!e.target.closest('#anchor-section')) closeAnchorDrop(); });
document.getElementById('anchor-input').addEventListener('input', e => searchAnchor(e.target.value));
document.getElementById('anchor-input').addEventListener('keydown', e => { if(e.key==='Escape') closeAnchorDrop(); });

async function optimize() {
  const btn = document.getElementById('btn-optimize');
  btn.disabled = true;
  document.getElementById('loading').classList.add('show');
  const preferences = {
    adventure: document.getElementById('pref_adventure').checked,
    relax: document.getElementById('pref_relax').checked,
    food: document.getElementById('pref_food').checked,
    checkin: document.getElementById('pref_checkin').checked,
  };
  const payload = {
    num_days: parseInt(document.getElementById('num_days').value),
    top_k: parseInt(document.getElementById('top_k').value),
    start_hour: parseInt(document.getElementById('start_hour').value),
    end_hour: parseInt(document.getElementById('end_hour').value),
    types: [...selectedTypes],
    anchor_pois: anchorPOIs.map(a=>a.name),
    preferences: preferences,
    start_location: startLocation,
  };
  try {
    const res = await fetch('/api/optimize', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
    const data = await res.json();
    renderResult(data);
    const formSection = document.getElementById('form-section');
    if (!formSection.classList.contains('collapsed')) {
      formSection.classList.add('collapsed');
      document.getElementById('toggleBtn').innerHTML = '▼ Mở rộng';
    }
    document.getElementById('feedback-container').style.display = 'block';
  } catch(e) { alert('Lỗi: '+e.message); }
  finally { btn.disabled=false; document.getElementById('loading').classList.remove('show'); }
}

function renderResult(data) {
  allLayers.forEach(l => map.removeLayer(l));
  allLayers = [];
  const s = data.summary;
  document.getElementById('s-km').innerHTML = s.total_km;
  document.getElementById('s-rate').innerHTML = s.rate;
  document.getElementById('s-stops').innerHTML = s.feasible + '/' + s.total_stops;
  document.getElementById('summary-bar').style.display = 'flex';

  let html = '';
  data.days.forEach(day => {
    const travelMins = Math.round(day.km / 30 * 60);
    html += `<div class="day-section">
      <div class="day-header" onclick="toggleDay(this)">
        <div class="day-dot" style="background:${day.color}"></div>
        <h3>Ngày ${day.day}</h3>
        <div class="day-stats">
          <span>🛣️ ${day.km} km</span>
          <span>🕒 ${travelMins} phút</span>
          <span>✅ ${day.feasible}/${day.total}</span>
        </div>
        <span style="font-size:12px;">▼</span>
      </div>
      <div class="day-stops" style="display:block;">`;
    day.stops.forEach(stop => {
      const cls = stop.feasible ? '' : ' infeasible';
      html += `<div class="stop-item${cls}" onclick="focusStop(${stop.lat},${stop.lng},'${esc(stop.name)}')">
        <div class="stop-num" style="background:${day.color}">${stop.idx}</div>
        <div class="stop-body">
          <div class="stop-name">${stop.emoji} ${stop.name} ${stop.anchor ? '<span class="badge-anchor">📌 bắt buộc</span>' : ''}</div>
          <div class="stop-meta">
            <span class="stop-time">🕐 ${stop.start} – ${stop.end}</span>
            <span>${stop.type_vi}</span>
            ${stop.rating ? `<span>⭐ ${stop.rating}</span>` : ''}
            ${stop.visit_min ? `<span>⏱ ${stop.visit_min} phút</span>` : ''}
            ${!stop.feasible ? '<span class="badge-infeasible">⚠️ ngoài giờ</span>' : ''}
          </div>
        </div>
      </div>`;
    });
    html += `</div></div>`;
  });
  document.getElementById('timeline').innerHTML = html;

  const bounds = [];
  data.days.forEach(day => {
    const coords = [];
    day.stops.forEach(stop => {
      const latlng = [stop.lat, stop.lng];
      coords.push(latlng);
      bounds.push(latlng);
      const icon = L.divIcon({ className: '', html: `<div style="background:${day.color};color:white;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.4);opacity:${stop.feasible?1:0.45};">${stop.idx}</div>`, iconSize:[32,32], iconAnchor:[16,16] });
      const popupHtml = `<div class="map-popup"><b>${stop.name}</b><br><div class="meta">${stop.emoji} ${stop.type_vi} | Ngày ${day.day} #${stop.idx}</div>🕐 ${stop.start}–${stop.end} ${stop.feasible?'✅':'⚠️'}<br>⭐ <span class="rating-stars">${'★'.repeat(Math.round(stop.rating))}${'☆'.repeat(5-Math.round(stop.rating))}</span><br>${stop.address?`📍 ${stop.address.substring(0,60)}<br>`:''}${stop.video_url?`<a href="${stop.video_url}" target="_blank">🎬 TikTok</a>`:''}</div>`;
      const marker = L.marker(latlng, {icon}).bindPopup(popupHtml, {maxWidth:280}).addTo(map);
      allLayers.push(marker);
    });
    if (coords.length >= 2) {
      const line = L.polyline(coords, { color: day.color, weight: 4, opacity: 0.8, dashArray: '8 6', lineCap: 'round', lineJoin: 'round' }).addTo(map);
      allLayers.push(line);
    }
  });
  if (bounds.length) map.fitBounds(bounds, {padding:[40,40]});
  if (startMarker) startMarker.addTo(map);

  const weatherBanner = document.getElementById('weather-banner');
  if (data.weather && data.weather.outdoor_removed) {
    const rainyCount = data.weather.rainy_days.filter(Boolean).length;
    const total = data.weather.rainy_days.length;
    weatherBanner.innerHTML = '🌧 Dự báo mưa ' + rainyCount + '/' + total + ' ngày — đã ẩn địa điểm ngoài trời (núi, thác, check-in). Bỏ tick sở thích Mạo hiểm để xem thêm.';
    weatherBanner.style.display = 'block';
  } else {
    weatherBanner.style.display = 'none';
  }

  // Cập nhật trọng số nếu server trả về
  if (data.weights) {
    renderWeightPanel(data.weights);
    showWeightToast(data.weights);
  }
}

function toggleDay(header) {
  const stopsDiv = header.nextElementSibling;
  if (stopsDiv.style.display === 'none') stopsDiv.style.display = 'block';
  else stopsDiv.style.display = 'none';
}
function focusStop(lat, lng, name) {
  map.setView([lat, lng], 16);
  allLayers.forEach(l => { if (l.getLatLng && Math.abs(l.getLatLng().lat-lat)<0.0001) l.openPopup(); });
}
function esc(s) { return (s||'').replace(/'/g,"\\'"); }

// Khởi tạo sự kiện cho các sao
function initStars() {
  const stars = document.querySelectorAll('.star');
  stars.forEach(star => {
    star.addEventListener('click', () => {
      const value = parseInt(star.dataset.value);
      selectedRating = value;
      stars.forEach((s, idx) => {
        if (idx < value) s.classList.add('selected');
        else s.classList.remove('selected');
      });
    });
  });
}
initStars();

async function submitFeedback() {
  if (selectedRating === 0) {
    alert('Vui lòng chọn số sao đánh giá!');
    return;
  }
  const feedback = document.getElementById("feedbackText").value;
  const response = await fetch("/submit-feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating: selectedRating, feedback: feedback, route_id: "dalat_route" })
  });
  const result = await response.json();
  document.getElementById("feedbackMessage").innerText = result.message;
  document.getElementById('feedback-container').style.display = 'none';
  selectedRating = 0;
  document.getElementById("feedbackText").value = '';
  document.querySelectorAll('.star').forEach(s => s.classList.remove('selected'));
  document.getElementById("thankPopup").style.display = "flex";
}
function closePopup() { document.getElementById("thankPopup").style.display = "none"; }

// ── Trọng số cá nhân ──────────────────────────────────────────
// Chuyển weight (0.5-2.0) → % thanh (0-100%)
function weightToPct(w) { return Math.round(((w - 0.5) / 1.5) * 100); }

let _prevWeights = null;
let _toastTimer = null;

function renderWeightPanel(w) {
  const panel = document.getElementById('weights-panel');
  panel.style.display = 'block';
  const cats = ['cafe','nature','food','checkin'];
  cats.forEach(c => {
    const pct = weightToPct(w[c]);
    document.getElementById(`wb-${c}`).style.width = pct + '%';
    document.getElementById(`wv-${c}`).textContent = w[c].toFixed(2);
  });
}

function showWeightToast(w) {
  const cats = ['cafe','nature','food','checkin'];
  cats.forEach(c => {
    const pct = weightToPct(w[c]);
    document.getElementById(`twb-${c}`).style.width = pct + '%';
    document.getElementById(`twv-${c}`).textContent = w[c].toFixed(2);
    let arrow = '';
    if (_prevWeights) {
      if (w[c] > _prevWeights[c]) arrow = '<span style="color:#4ade80">↑</span>';
      else if (w[c] < _prevWeights[c]) arrow = '<span style="color:#f87171">↓</span>';
    }
    document.getElementById(`twa-${c}`).innerHTML = arrow;
  });
  _prevWeights = {...w};

  const toast = document.getElementById('weight-toast');
  toast.classList.add('show');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('show'), 4000);
}

async function loadInitialWeights() {
  try {
    const res = await fetch('/api/user_weights');
    const w = await res.json();
    _prevWeights = {...w};
    renderWeightPanel(w);
  } catch(e) { console.warn('Không tải được trọng số:', e); }
}

async function resetWeights() {
  try {
    const res = await fetch('/api/user_weights/reset', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      renderWeightPanel(data.weights);
      showWeightToast(data.weights);
    }
  } catch(e) { console.warn('Reset thất bại:', e); }
}

// Tải weights khi khởi động
loadInitialWeights();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if not os.path.exists(POI_CSV):
        print(f"❌ Không tìm thấy {POI_CSV}")
        print("   Chạy scoring.py trước để tạo file này.")
    else:
        print("\n" + "="*50)
        print("  🏔  Đà Lạt Route Planner (bố cục thông minh)")
        print("="*50)
        print(f"  Mở trình duyệt: http://localhost:5000")
        print("  Ctrl+C để dừng server")
        print("="*50 + "\n")
        app.run(debug=False, port=5000)