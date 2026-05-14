"""
=============================================================
GIAI ĐOẠN 7 — Web App lộ trình Đà Lạt
=============================================================
Yêu cầu: pip install flask folium

Chạy: python app.py
Mở trình duyệt: http://localhost:5000
=============================================================
"""

from flask import Flask, render_template_string, request, jsonify
import csv, math, random, json, os

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════

POI_CSV       = "dalat_poi_scored.csv"
DALAT_CENTER  = [11.9404, 108.4583]
AVG_SPEED_KMH = 30

DAY_COLORS = ["#E8503A", "#2D7DD2", "#3BB273", "#F18F01", "#7B2FBE"]

TYPE_VI = {
    "cafe":             "Cà phê",
    "nhà hàng":         "Nhà hàng",
    "chợ quán":         "Ăn uống",
    "địa điểm checkin": "Check-in",
    "thiên nhiên":      "Thiên nhiên",
    "quán ăn":          "Quán ăn",
    "homestay":         "Homestay",
    "khách sạn":        "Khách sạn",
    "khác":             "Khác",
}

TYPE_EMOJI = {
    "cafe":             "☕",
    "nhà hàng":         "🍽️",
    "chợ quán":         "🛒",
    "địa điểm checkin": "📸",
    "thiên nhiên":      "🌿",
    "quán ăn":          "🥢",
    "homestay":         "🏠",
    "khách sạn":        "🏨",
    "khác":             "📍",
}

# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def safe_float(v, d=0.0):
    try: return float(v) if str(v) not in ("","nan","None") else d
    except: return d

def safe_int(v, d=0):
    try: return int(float(v)) if str(v) not in ("","nan","None") else d
    except: return d

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

# ── Visit Duration Inference ──────────────────────────────────
_BASE_DURATION = {
    "cafe": 55, "nhà hàng": 65, "chợ quán": 40,
    "địa điểm checkin": 25, "thiên nhiên": 85, "quán ăn": 50, "khác": 40,
}
_PRICE_ADJUST = {0: -5, 1: 0, 2: 5, 3: 15, 4: 20}

def infer_visit_duration(poi_type, price_level, reviews_count, csv_value):
    csv_int = safe_int(csv_value, 0)
    if csv_int > 0 and csv_int != 45:
        return csv_int
    base = _BASE_DURATION.get(poi_type.strip().lower(), 40)
    base += _PRICE_ADJUST.get(safe_int(price_level, 1), 0)
    reviews = safe_int(reviews_count, 0)
    if reviews > 5000:   base += 15
    elif reviews > 1000: base += 8
    elif reviews > 500:  base += 3
    elif reviews < 50:   base -= 5
    return max(15, base)

# Giờ mở cửa mặc định theo loại POI
_DEFAULT_HOURS = {
    "thiên nhiên":      (7*60, 18*60),
    "địa điểm checkin": (6*60, 20*60),
    "cafe":             (7*60, 22*60),
    "nhà hàng":         (10*60, 22*60),
    "chợ quán":         (6*60, 22*60),
    "khác":             (7*60, 21*60),
}

# ══════════════════════════════════════════════════════════════
# LOAD POI
# ══════════════════════════════════════════════════════════════

_poi_cache = None

def load_pois():
    global _poi_cache
    if _poi_cache is not None:
        return _poi_cache

    with open(POI_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    pois = []
    seen = set()
    for r in rows:
        if str(r.get("include_in_route","")).strip().lower() not in ("true","1"):
            continue
        lat = safe_float(r.get("lat"))
        lng = safe_float(r.get("lng"))
        if lat==0 or lng==0: continue

        coord_key = (round(lat,3), round(lng,3))
        if coord_key in seen: continue
        seen.add(coord_key)

        open_min_raw  = r.get("open_min", "")
        close_min_raw = r.get("close_min", "")
        open_min  = safe_float(open_min_raw)  if open_min_raw  not in ("","nan","None") else 0
        close_min = safe_float(close_min_raw) if close_min_raw not in ("","nan","None") else 24*60
        if close_min_raw not in ("","nan","None") and close_min == 0 and open_min > 0:
            close_min = 24*60

        # Default hours theo loại POI nếu không có giờ thực tế
        poi_type = r.get("type","khác").strip().lower()
        for k in TYPE_VI:
            if k in poi_type: poi_type = k; break
        if open_min_raw in ("","nan","None") or close_min_raw in ("","nan","None"):
            def_open, def_close = _DEFAULT_HOURS.get(poi_type, (7*60, 21*60))
            if open_min_raw  in ("","nan","None"): open_min  = def_open
            if close_min_raw in ("","nan","None"): close_min = def_close

        pois.append({
            "name":       r["place_name"],
            "type":       poi_type,
            "lat": lat, "lng": lng,
            "score":      safe_float(r.get("attraction_score")),
            "rating":     safe_float(r.get("gmaps_rating")),
            "reviews":    safe_int(r.get("gmaps_reviews_count")),
            "open_min":   open_min,
            "close_min":  close_min,
            "visit_min":  infer_visit_duration(
                              poi_type,
                              r.get("gmaps_price_level",""),
                              r.get("gmaps_reviews_count",""),
                              r.get("visit_duration_min", 45),
                          ),
            "address":    r.get("gmaps_address",""),
            "video_url":  r.get("video_urls",""),
            "price":      r.get("price_mentions",""),
            "open_text":  r.get("opening_hours_text",""),
            "anchor":     False,
        })

    _poi_cache = pois
    return pois

# ══════════════════════════════════════════════════════════════
# OPTIMIZER
# ══════════════════════════════════════════════════════════════

def is_feasible(poi, arrive, user_end):
    start = max(arrive, poi["open_min"])
    end   = start + poi["visit_min"]
    return (end <= poi["close_min"] and end <= user_end), start, end

def simulate_day(poi_list, user_start, user_end):
    cur_time = user_start
    cur_lat, cur_lng = DALAT_CENTER
    total_km = 0.0
    feasible = 0
    timeline = []
    for poi in poi_list:
        tm = travel_min(cur_lat, cur_lng, poi["lat"], poi["lng"])
        arrive = cur_time + tm
        ok, start, end = is_feasible(poi, arrive, user_end)
        km = haversine_km(cur_lat, cur_lng, poi["lat"], poi["lng"])
        total_km += km
        timeline.append({**poi, "arrive":arrive, "start":start, "end":end,
                         "feasible":ok, "km":round(km,2)})
        cur_time = end if ok else arrive
        cur_lat, cur_lng = poi["lat"], poi["lng"]
        if ok: feasible += 1
    return total_km, feasible, sorted(timeline, key=lambda x: x["start"])

def split_days(route, num_days):
    """
    Chia route thành num_days ngày theo clustering địa lý.
    Sort theo latitude để các điểm cùng ngày gần nhau về địa lý,
    tránh tình trạng ngày 1 toàn điểm phía bắc, ngày 2 toàn phía nam.
    """
    sorted_route = sorted(route, key=lambda p: p["lat"])
    n=len(sorted_route); days=[]; size=n//num_days; rem=n%num_days; idx=0
    for d in range(num_days):
        end=idx+size+(1 if d<rem else 0); days.append(sorted_route[idx:end]); idx=end
    return days

def route_cost(route, num_days, user_start, user_end):
    total_km=0; total_inf=0
    for day_pois in split_days(route, num_days):
        km, feas, _ = simulate_day(day_pois, user_start, user_end)
        total_km += km; total_inf += len(day_pois)-feas
    return total_km + total_inf*50

def greedy(pois, user_start, user_end):
    unvisited = list(pois); route = []
    cur_lat, cur_lng = DALAT_CENTER; cur_time = user_start
    while unvisited:
        best=None; best_val=-1
        for p in unvisited:
            tm=travel_min(cur_lat,cur_lng,p["lat"],p["lng"])
            ok,start,end=is_feasible(p, cur_time+tm, user_end)
            if not ok: continue
            dist=haversine_km(cur_lat,cur_lng,p["lat"],p["lng"])
            val=p["score"]/(dist+0.1)
            if val>best_val: best_val=val; best=p
        if best is None:
            best=min(unvisited, key=lambda p: haversine_km(cur_lat,cur_lng,p["lat"],p["lng"]))
        route.append(best)
        tm=travel_min(cur_lat,cur_lng,best["lat"],best["lng"])
        _,_,end=is_feasible(best, cur_time+tm, user_end)
        cur_time=end; cur_lat,cur_lng=best["lat"],best["lng"]
        unvisited.remove(best)
    return route

def simulated_annealing(initial, num_days, user_start, user_end,
                        T0=800, alpha=0.995, max_iter=30_000,
                        anchor_names=None):
    anchor_names = anchor_names or []
    random.seed(42)
    current=list(initial); best=list(current)
    cur_cost=route_cost(current,num_days,user_start,user_end)
    best_cost=cur_cost; T=T0
    for _ in range(max_iter):
        if T<0.01: break
        n=len(current)
        nb=list(current)
        op=random.randint(0,2)

        if op==0:                        # swap
            i,j=random.sample(range(n),2)
            nb[i],nb[j]=nb[j],nb[i]
        elif op==1:                      # 2-opt: reverse segment [i..j]
            i,j=sorted(random.sample(range(n),2))
            nb[i:j+1]=nb[i:j+1][::-1]
        else:                            # or-opt: move one element
            i=random.randrange(n); j=random.randrange(n-1)
            if j>=i: j+=1
            poi=nb[i]
            if anchor_names and any(a in poi["name"].lower() for a in anchor_names):
                T*=alpha; continue       # anchor không bị di chuyển
            nb.pop(i); nb.insert(j,poi)
        nc=route_cost(nb,num_days,user_start,user_end)
        delta=nc-cur_cost
        if delta<0 or random.random()<math.exp(-delta/T):
            current=nb; cur_cost=nc
            if cur_cost<best_cost: best=list(current); best_cost=cur_cost
        T*=alpha
    return best

# ══════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/optimize", methods=["POST"])
def optimize():
    data      = request.json
    num_days  = max(1, min(int(data.get("num_days",3)), 7))
    top_k     = max(num_days*5, min(int(data.get("top_k",40)), 80))
    user_start= int(data.get("start_hour",7))*60
    user_end  = int(data.get("end_hour",21))*60
    types_filter  = data.get("types", [])
    anchor_names  = [a.strip().lower() for a in data.get("anchor_pois", []) if a.strip()]

    all_pois = load_pois()

    # Đánh dấu anchor
    for p in all_pois:
        p["anchor"] = any(a in p["name"].lower() for a in anchor_names)

    # Lọc type
    if types_filter:
        filtered = [p for p in all_pois if p["type"] in types_filter]
        if len(filtered) < num_days*3:
            filtered = all_pois
    else:
        filtered = all_pois

    # Top-K: anchors luôn được giữ
    anchors  = [p for p in filtered if p["anchor"]]
    non_anch = [p for p in filtered if not p["anchor"]]
    non_anch.sort(key=lambda x: x["score"], reverse=True)
    pois = (anchors + non_anch)[:top_k]

    # Optimize
    g_route  = greedy(pois, user_start, user_end)
    sa_route = simulated_annealing(g_route, num_days, user_start, user_end,
                                   anchor_names=anchor_names)

    # Build result
    days_data = []
    total_km=0; total_feas=0; total_stops=0
    for d, day_pois in enumerate(split_days(sa_route, num_days), 1):
        km, feas, timeline = simulate_day(day_pois, user_start, user_end)
        total_km+=km; total_feas+=feas; total_stops+=len(day_pois)
        color = DAY_COLORS[(d-1) % len(DAY_COLORS)]
        stops = []
        for idx, s in enumerate(timeline, 1):
            stops.append({
                "idx":       idx,
                "name":      s["name"],
                "type":      s["type"],
                "type_vi":   TYPE_VI.get(s["type"], s["type"]),
                "emoji":     TYPE_EMOJI.get(s["type"], "📍"),
                "lat":       s["lat"],
                "lng":       s["lng"],
                "start":     fmt(s["start"]),
                "end":       fmt(s["end"]),
                "feasible":  s["feasible"],
                "km":        s["km"],
                "rating":    s["rating"],
                "score":     round(s["score"],3),
                "address":   s["address"],
                "video_url": s["video_url"],
                "price":     s["price"],
                "anchor":    s.get("anchor", False),
                "visit_min": s.get("visit_min", 45),
            })
        days_data.append({
            "day": d, "color": color,
            "km": round(km,1), "feasible": feas,
            "total": len(day_pois), "stops": stops,
        })

    return jsonify({
        "days": days_data,
        "summary": {
            "total_km":    round(total_km,1),
            "feasible":    total_feas,
            "total_stops": total_stops,
            "rate":        round(total_feas/total_stops*100) if total_stops else 0,
            "num_days":    num_days,
            "anchors":     [p["name"] for p in pois if p["anchor"]],
        }
    })

@app.route("/api/search_poi")
def search_poi():
    """Tìm kiếm POI theo tên — dùng cho autocomplete anchor picker"""
    q = request.args.get("q","").strip().lower()
    pois = load_pois()
    if not q:
        # Trả về top 20 theo score
        results = sorted(pois, key=lambda x: x["score"], reverse=True)[:20]
    else:
        results = [p for p in pois if q in p["name"].lower()][:15]
    return jsonify([{
        "name":   p["name"],
        "type":   TYPE_VI.get(p["type"], p["type"]),
        "emoji":  TYPE_EMOJI.get(p["type"], "📍"),
        "rating": p["rating"],
        "score":  round(p["score"],3),
    } for p in results])

@app.route("/api/poi_types")
def poi_types():
    pois = load_pois()
    types = sorted(set(p["type"] for p in pois))
    return jsonify([{"value": t, "label": TYPE_VI.get(t,t),
                     "emoji": TYPE_EMOJI.get(t,"📍")} for t in types])

# ══════════════════════════════════════════════════════════════
# HTML TEMPLATE
# ══════════════════════════════════════════════════════════════

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Đà Lạt Route Planner</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root {
  --bg:       #0f1117;
  --surface:  #1a1d27;
  --border:   #2a2d3a;
  --accent:   #c8a96e;
  --accent2:  #e8c98e;
  --text:     #e8e6e0;
  --muted:    #6b7280;
  --day1:     #E8503A;
  --day2:     #2D7DD2;
  --day3:     #3BB273;
  --day4:     #F18F01;
  --day5:     #7B2FBE;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: 'DM Sans', sans-serif;
  background: var(--bg);
  color: var(--text);
  height: 100vh;
  display: flex;
  overflow: hidden;
}

/* ── SIDEBAR ── */
#sidebar {
  width: 380px;
  min-width: 340px;
  background: var(--surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

#sidebar-header {
  padding: 24px 20px 16px;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(135deg, #1a1d27 0%, #22253a 100%);
}

#sidebar-header h1 {
  font-family: 'Playfair Display', serif;
  font-size: 22px;
  color: var(--accent);
  letter-spacing: 0.5px;
}
#sidebar-header p {
  font-size: 12px;
  color: var(--muted);
  margin-top: 3px;
}

/* ── FORM ── */
#form-section {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.form-row {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

.form-group {
  flex: 1;
}

label {
  display: block;
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-bottom: 5px;
}

input[type=number], select {
  width: 100%;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text);
  padding: 8px 12px;
  font-size: 14px;
  font-family: 'DM Sans', sans-serif;
  outline: none;
  transition: border-color 0.2s;
}
input[type=number]:focus, select:focus {
  border-color: var(--accent);
}

/* Type filter pills */
#type-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.type-pill {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
}
.type-pill.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #1a1d27;
  font-weight: 500;
}

#btn-optimize {
  width: 100%;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  color: #1a1d27;
  border: none;
  border-radius: 10px;
  padding: 11px;
  font-size: 14px;
  font-weight: 600;
  font-family: 'DM Sans', sans-serif;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.1s;
  letter-spacing: 0.3px;
}
#btn-optimize:hover { opacity: 0.9; }
#btn-optimize:active { transform: scale(0.98); }
#btn-optimize:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── SUMMARY BAR ── */
#summary-bar {
  display: none;
  padding: 10px 20px;
  background: rgba(200,169,110,0.08);
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  color: var(--muted);
  gap: 16px;
  flex-wrap: wrap;
}
#summary-bar span b { color: var(--accent); font-size: 14px; }

/* ── TIMELINE ── */
#timeline {
  flex: 1;
  overflow-y: auto;
  padding: 0;
}
#timeline::-webkit-scrollbar { width: 4px; }
#timeline::-webkit-scrollbar-track { background: transparent; }
#timeline::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

.day-section { border-bottom: 1px solid var(--border); }
.day-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px 10px;
  position: sticky;
  top: 0;
  background: var(--surface);
  z-index: 10;
  cursor: pointer;
}
.day-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.day-header h3 {
  font-size: 13px;
  font-weight: 600;
  flex: 1;
}
.day-meta {
  font-size: 11px;
  color: var(--muted);
}

.stop-item {
  display: flex;
  gap: 12px;
  padding: 8px 20px 8px 20px;
  cursor: pointer;
  transition: background 0.15s;
  border-left: 3px solid transparent;
}
.stop-item:hover { background: rgba(255,255,255,0.03); }
.stop-item.active { background: rgba(200,169,110,0.08); }
.stop-item.infeasible { opacity: 0.45; }

.stop-num {
  width: 22px; height: 22px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700;
  flex-shrink: 0; margin-top: 2px;
  color: white;
}
.stop-body { flex: 1; min-width: 0; }
.stop-name {
  font-size: 13px; font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.stop-meta {
  font-size: 11px; color: var(--muted); margin-top: 2px;
  display: flex; gap: 8px; flex-wrap: wrap;
}
.stop-time { color: var(--accent); font-weight: 500; font-size: 12px; }
.badge-infeasible {
  font-size: 10px; color: #f59e0b;
  background: rgba(245,158,11,0.15);
  padding: 1px 6px; border-radius: 10px;
}

/* ── MAP ── */
#map-container {
  flex: 1;
  position: relative;
}
#map { width: 100%; height: 100%; }

/* Loading overlay */
#loading {
  display: none;
  position: absolute;
  inset: 0;
  background: rgba(15,17,23,0.85);
  z-index: 1000;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 14px;
}
#loading.show { display: flex; }
.spinner {
  width: 40px; height: 40px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
#loading p { color: var(--muted); font-size: 13px; }

/* Popup on map */
.map-popup { font-family: 'DM Sans', sans-serif; font-size: 13px; }
.map-popup b { font-size: 14px; }
.map-popup .meta { color: #666; margin: 4px 0; }
.map-popup a { color: #2D7DD2; text-decoration: none; }

/* Anchor tags */
.anchor-tag {
  display: inline-flex; align-items: center; gap: 5px;
  background: rgba(200,169,110,0.15); border: 1px solid var(--accent);
  border-radius: 20px; padding: 3px 10px; font-size: 12px; color: var(--accent);
}
.anchor-tag button {
  background: none; border: none; color: var(--accent);
  cursor: pointer; padding: 0; font-size: 14px; line-height: 1;
}
.anchor-drop-item {
  padding: 8px 12px; font-size: 13px; cursor: pointer;
  display: flex; align-items: center; gap: 8px;
  transition: background 0.15s;
}
.anchor-drop-item:hover { background: rgba(255,255,255,0.05); }
.badge-anchor {
  font-size: 10px; color: var(--accent);
  background: rgba(200,169,110,0.15);
  padding: 1px 6px; border-radius: 10px;
}
</style>
</head>
<body>

<!-- SIDEBAR -->
<div id="sidebar">
  <div id="sidebar-header">
    <h1>🏔 Đà Lạt Planner</h1>
    <p>Lộ trình tối ưu dựa trên TikTok & Google Maps</p>
  </div>

  <div id="form-section">
    <div class="form-row">
      <div class="form-group">
        <label>Số ngày</label>
        <input type="number" id="num_days" value="3" min="1" max="7">
      </div>
      <div class="form-group">
        <label>Số POI tối đa</label>
        <input type="number" id="top_k" value="40" min="10" max="80">
      </div>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label>Bắt đầu lúc</label>
        <select id="start_hour">
          <option value="6">06:00</option>
          <option value="7" selected>07:00</option>
          <option value="8">08:00</option>
          <option value="9">09:00</option>
        </select>
      </div>
      <div class="form-group">
        <label>Kết thúc lúc</label>
        <select id="end_hour">
          <option value="19">19:00</option>
          <option value="20">20:00</option>
          <option value="21" selected>21:00</option>
          <option value="22">22:00</option>
        </select>
      </div>
    </div>

    <label style="margin-bottom:8px;">Loại địa điểm</label>
    <div id="type-filters">
      <div class="type-pill active" data-type="all">✨ Tất cả</div>
    </div>

    <label style="margin-bottom:8px; margin-top:4px;">📌 Điểm bắt buộc ghé (tùy chọn)</label>
    <div id="anchor-section">
      <div style="position:relative;">
        <input type="text" id="anchor-input" placeholder="Tìm địa điểm..."
               autocomplete="off"
               style="padding-right:32px;"
               oninput="searchAnchor(this.value)"
               onkeydown="if(event.key==='Escape') closeAnchorDrop()">
        <div id="anchor-drop" style="
          display:none; position:absolute; top:100%; left:0; right:0;
          background:var(--surface); border:1px solid var(--border);
          border-radius:8px; max-height:180px; overflow-y:auto; z-index:100;
          margin-top:4px; box-shadow:0 8px 24px rgba(0,0,0,0.4);
        "></div>
      </div>
      <div id="anchor-tags" style="display:flex; flex-wrap:wrap; gap:5px; margin-top:7px;"></div>
    </div>

    <button id="btn-optimize" onclick="optimize()">🗺 Tạo lộ trình</button>
  </div>

  <div id="summary-bar">
    <span>🗺 <b id="s-km">-</b> km</span>
    <span>✅ <b id="s-rate">-</b>% đúng giờ</span>
    <span>📍 <b id="s-stops">-</b> điểm</span>
  </div>

  <div id="timeline">
    <div style="padding:40px 20px; text-align:center; color:var(--muted); font-size:13px;">
      Nhập thông tin và bấm<br><b style="color:var(--accent)">Tạo lộ trình</b> để bắt đầu
    </div>
  </div>
</div>

<!-- MAP -->
<div id="map-container">
  <div id="loading">
    <div class="spinner"></div>
    <p>Đang tính toán lộ trình tối ưu...</p>
  </div>
  <div id="map"></div>
</div>

<script>
// ── Init map ──
const map = L.map('map', { zoomControl: false }).setView([11.9404, 108.4583], 13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  attribution: '© CartoDB', maxZoom: 19
}).addTo(map);
L.control.zoom({ position: 'bottomright' }).addTo(map);

let allLayers = [];
let activeStop = null;
let selectedTypes = new Set(); // empty = tất cả

// ── Load type filters ──
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
  // Deactivate "all"
  document.querySelector('[data-type=all]').classList.remove('active');
  if (selectedTypes.has(type)) {
    selectedTypes.delete(type);
    pill.classList.remove('active');
    if (selectedTypes.size === 0)
      document.querySelector('[data-type=all]').classList.add('active');
  } else {
    selectedTypes.add(type);
    pill.classList.add('active');
  }
}

// ── Anchor POI picker ──
let anchorPOIs = [];  // [{name, emoji, type_vi}]
let anchorDebounce = null;

async function searchAnchor(q) {
  clearTimeout(anchorDebounce);
  const drop = document.getElementById('anchor-drop');
  if (!q.trim()) { drop.style.display='none'; return; }
  anchorDebounce = setTimeout(async () => {
    const res = await fetch('/api/search_poi?q='+encodeURIComponent(q));
    const items = await res.json();
    if (!items.length) { drop.style.display='none'; return; }
    drop.innerHTML = items.map(it => `
      <div class="anchor-drop-item" onclick="addAnchor('${esc(it.name)}','${it.emoji}','${esc(it.type_vi)}')">
        <span>${it.emoji}</span>
        <span style="flex:1">${it.name}</span>
        <span style="color:var(--muted);font-size:11px">⭐${it.rating}</span>
      </div>`).join('');
    drop.style.display='block';
  }, 220);
}

function addAnchor(name, emoji, type_vi) {
  if (anchorPOIs.find(a => a.name===name)) { closeAnchorDrop(); return; }
  anchorPOIs.push({name, emoji, type_vi});
  renderAnchorTags();
  document.getElementById('anchor-input').value = '';
  closeAnchorDrop();
}

function removeAnchor(name) {
  anchorPOIs = anchorPOIs.filter(a => a.name !== name);
  renderAnchorTags();
}

function renderAnchorTags() {
  const container = document.getElementById('anchor-tags');
  container.innerHTML = anchorPOIs.map(a => `
    <div class="anchor-tag">
      ${a.emoji} ${a.name}
      <button onclick="removeAnchor('${esc(a.name)}')" title="Xoá">×</button>
    </div>`).join('');
}

function closeAnchorDrop() {
  document.getElementById('anchor-drop').style.display='none';
}
document.addEventListener('click', e => {
  if (!e.target.closest('#anchor-section')) closeAnchorDrop();
});

// ── Optimize ──
async function optimize() {
  const btn = document.getElementById('btn-optimize');
  btn.disabled = true;
  document.getElementById('loading').classList.add('show');

  const payload = {
    num_days:    parseInt(document.getElementById('num_days').value),
    top_k:       parseInt(document.getElementById('top_k').value),
    start_hour:  parseInt(document.getElementById('start_hour').value),
    end_hour:    parseInt(document.getElementById('end_hour').value),
    types:       [...selectedTypes],
    anchor_pois: anchorPOIs.map(a => a.name),
  };

  try {
    const res  = await fetch('/api/optimize', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    renderResult(data);
  } catch(e) {
    alert('Lỗi: ' + e.message);
  } finally {
    btn.disabled = false;
    document.getElementById('loading').classList.remove('show');
  }
}

function renderResult(data) {
  // Clear old layers
  allLayers.forEach(l => map.removeLayer(l));
  allLayers = [];

  // Summary
  const s = data.summary;
  document.getElementById('s-km').textContent    = s.total_km;
  document.getElementById('s-rate').textContent  = s.rate;
  document.getElementById('s-stops').textContent = s.feasible + '/' + s.total_stops;
  const bar = document.getElementById('summary-bar');
  bar.style.display = 'flex';

  // Timeline HTML
  let html = '';
  data.days.forEach(day => {
    html += `<div class="day-section">
      <div class="day-header">
        <div class="day-dot" style="background:${day.color}"></div>
        <h3>Ngày ${day.day}</h3>
        <div class="day-meta">${day.km}km &nbsp;·&nbsp; ${day.feasible}/${day.total} đúng giờ</div>
      </div>`;
    day.stops.forEach(stop => {
      const cls = stop.feasible ? '' : ' infeasible';
      html += `<div class="stop-item${cls}" id="stop-${day.day}-${stop.idx}"
                    onclick="focusStop(${stop.lat},${stop.lng},'${esc(stop.name)}')">
        <div class="stop-num" style="background:${day.color}">${stop.idx}</div>
        <div class="stop-body">
          <div class="stop-name">${stop.emoji} ${stop.name}</div>
          <div class="stop-meta">
            <span class="stop-time">🕐 ${stop.start}–${stop.end}</span>
            <span>${stop.type_vi}</span>
            ${stop.rating ? `<span>⭐ ${stop.rating}</span>` : ''}
            ${stop.visit_min ? `<span style="color:var(--muted)">⏱ ${stop.visit_min}ph</span>` : ''}
            ${stop.anchor ? '<span class="badge-anchor">📌 bắt buộc</span>' : ''}
            ${!stop.feasible ? '<span class="badge-infeasible">⚠ ngoài giờ</span>' : ''}
          </div>
        </div>
      </div>`;
    });
    html += '</div>';
  });
  document.getElementById('timeline').innerHTML = html;

  // Map markers & polylines
  const bounds = [];
  data.days.forEach(day => {
    const coords = [];
    day.stops.forEach(stop => {
      const latlng = [stop.lat, stop.lng];
      coords.push(latlng);
      bounds.push(latlng);

      // Marker
      const icon = L.divIcon({
        html: `<div style="
          background:${day.color};color:white;border-radius:50%;
          width:28px;height:28px;display:flex;align-items:center;
          justify-content:center;font-weight:700;font-size:11px;
          border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.5);
          opacity:${stop.feasible?1:0.4};">${stop.idx}</div>`,
        iconSize:[28,28], iconAnchor:[14,14],
      });

      const video = stop.video_url
        ? `<a href="${stop.video_url}" target="_blank">🎬 TikTok</a>` : '';
      const popup = `<div class="map-popup">
        <b>${stop.name}</b><br>
        <div class="meta">${stop.emoji} ${stop.type_vi} &nbsp;|&nbsp; Ngày ${day.day} #${stop.idx}</div>
        🕐 ${stop.start} – ${stop.end} ${stop.feasible?'✅':'⚠️'}<br>
        ${stop.rating ? `⭐ ${stop.rating}` : ''} ${stop.address ? `<br>📍 ${stop.address}` : ''}
        ${video ? `<br>${video}` : ''}
      </div>`;

      const marker = L.marker(latlng, {icon})
        .bindPopup(popup, {maxWidth:280})
        .addTo(map);
      allLayers.push(marker);
    });

    // Polyline
    if (coords.length >= 2) {
      const line = L.polyline(coords, {
        color: day.color, weight: 3, opacity: 0.7,
        dashArray: '8 5',
      }).addTo(map);
      allLayers.push(line);
    }
  });

  if (bounds.length) map.fitBounds(bounds, {padding:[40,40]});
}

function focusStop(lat, lng, name) {
  map.setView([lat, lng], 16, {animate:true});
  // Highlight marker
  document.querySelectorAll('.stop-item').forEach(el => el.classList.remove('active'));
  // Find and open popup
  allLayers.forEach(l => {
    if (l.getLatLng && Math.abs(l.getLatLng().lat-lat)<0.0001) {
      l.openPopup();
    }
  });
}

function esc(s) { return (s||'').replace(/'/g,"\\'"); }
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
        print("  🏔  Đà Lạt Route Planner")
        print("="*50)
        print(f"  Mở trình duyệt: http://localhost:5000")
        print("  Ctrl+C để dừng server")
        print("="*50 + "\n")
        app.run(debug=False, port=5000)