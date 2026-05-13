"""
=============================================================
GIAI ĐOẠN 6 — Trực quan hóa lộ trình trên bản đồ
=============================================================
Input:  dalat_route_3days.csv  (output của giai đoạn 5)
Output: dalat_route_map.html   (bản đồ tương tác)

Yêu cầu: pip install folium
=============================================================
"""

import csv, folium, os
from folium import plugins

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV  = "dalat_route_3days.csv"
OUTPUT_MAP = "dalat_route_map.html"

# Màu cho từng ngày
DAY_COLORS = {
    1: "#E74C3C",   # đỏ
    2: "#2980B9",   # xanh dương
    3: "#27AE60",   # xanh lá
}

# Icon cho từng type
TYPE_ICONS = {
    "cafe":              ("coffee",  "white"),
    "nhà hàng":          ("cutlery", "white"),
    "chợ quán":          ("shopping-cart", "white"),
    "địa điểm checkin":  ("camera",  "white"),
    "thiên nhiên":       ("tree",    "white"),
    "quán ăn":           ("cutlery", "white"),
    "khác":              ("info-sign","white"),
}

DALAT_CENTER = [11.9404, 108.4583]

# ══════════════════════════════════════════════════════════════
# ĐỌC DATA
# ══════════════════════════════════════════════════════════════

def load_route(filepath):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # Gom theo ngày
    days = {}
    for row in rows:
        d = int(row["day"])
        if d not in days:
            days[d] = []
        days[d].append(row)
    return days

# ══════════════════════════════════════════════════════════════
# BUILD MAP
# ══════════════════════════════════════════════════════════════

def build_map(days):
    m = folium.Map(
        location=DALAT_CENTER,
        zoom_start=13,
        tiles="CartoDB positron",
    )

    # Layer control — bật/tắt từng ngày
    day_groups = {}
    for d in sorted(days.keys()):
        group = folium.FeatureGroup(name=f"📅 Ngày {d}", show=True)
        day_groups[d] = group
        m.add_child(group)

    for d, stops in sorted(days.items()):
        color    = DAY_COLORS.get(d, "#888888")
        group    = day_groups[d]
        coords   = []  # để vẽ polyline

        for idx, stop in enumerate(stops, 1):
            lat = float(stop.get("lat") or stop.get("latitude") or 0)
            lng = float(stop.get("lng") or stop.get("longitude") or 0)
            if lat == 0 or lng == 0:
                continue

            coords.append([lat, lng])

            poi_type  = stop.get("type", "khác").strip().lower()
            icon_name, icon_color = TYPE_ICONS.get(poi_type, ("info-sign", "white"))
            feasible  = str(stop.get("feasible", "True")).strip().lower() in ("true", "1")
            status    = "✅" if feasible else "⚠️ Ngoài giờ"

            # Popup HTML
            video_url = stop.get("video_url", "")
            video_html = f'<a href="{video_url}" target="_blank">🎬 Xem TikTok</a>' if video_url else ""
            address   = stop.get("address", "")
            rating    = stop.get("rating", "")
            score     = stop.get("attraction_score", "")

            popup_html = f"""
            <div style="font-family:Arial; min-width:220px; font-size:13px;">
                <b style="color:{color}; font-size:15px;">{idx}. {stop['name']}</b><br>
                <span style="color:#666;">{poi_type} &nbsp;|&nbsp; Ngày {d}</span><br>
                <hr style="margin:5px 0;">
                🕐 <b>{stop.get('start_visit','?')}</b> – <b>{stop.get('end_visit','?')}</b>
                &nbsp; {status}<br>
                ⭐ Rating: <b>{rating}</b>
                &nbsp; 📊 Score: <b>{score}</b><br>
                📍 {address}<br>
                {video_html}
            </div>
            """

            # Marker số thứ tự
            folium.Marker(
                location=[lat, lng],
                popup=folium.Popup(popup_html, max_width=280),
                tooltip=f"Ngày {d} #{idx}: {stop['name']}",
                icon=folium.DivIcon(
                    html=f"""
                    <div style="
                        background:{color};
                        color:white;
                        border-radius:50%;
                        width:28px; height:28px;
                        display:flex; align-items:center; justify-content:center;
                        font-weight:bold; font-size:12px;
                        border: 2px solid white;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.4);
                        opacity: {'1.0' if feasible else '0.5'};
                    ">{idx}</div>
                    """,
                    icon_size=(28, 28),
                    icon_anchor=(14, 14),
                )
            ).add_to(group)

        # Vẽ polyline nối các điểm trong ngày
        if len(coords) >= 2:
            folium.PolyLine(
                locations=coords,
                color=color,
                weight=3,
                opacity=0.7,
                dash_array="8 4",
                tooltip=f"Lộ trình Ngày {d}",
            ).add_to(group)

        # Mũi tên hướng di chuyển
        plugins.AntPath(
            locations=coords,
            color=color,
            weight=3,
            opacity=0.5,
            delay=800,
        ).add_to(group)

    # Layer control
    folium.LayerControl(collapsed=False).add_to(m)

    # Legend
    legend_html = """
    <div style="
        position: fixed; bottom: 30px; left: 30px; z-index: 1000;
        background: white; padding: 12px 16px; border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3); font-family: Arial; font-size: 13px;
    ">
        <b>🗺️ Lộ trình Đà Lạt</b><br>
        <span style="color:#E74C3C;">●</span> Ngày 1 &nbsp;
        <span style="color:#2980B9;">●</span> Ngày 2 &nbsp;
        <span style="color:#27AE60;">●</span> Ngày 3<br>
        <span style="opacity:0.5">○</span> Infeasible (ngoài giờ mở cửa)
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*55)
    print("  GIAI ĐOẠN 6 — Visualize lộ trình")
    print("="*55)

    # Kiểm tra file input có lat/lng không
    # (file route chỉ có từ scored nên cần join lại)
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        cols = f.readline()
    has_coords = "lat" in cols or "latitude" in cols

    if not has_coords:
        print("\n  ⚠️  File route chưa có tọa độ lat/lng.")
        print("  Đang join với dalat_poi_scored.csv...")
        join_coords()

    print(f"\n  Đọc lộ trình từ {INPUT_CSV}...")
    days = load_route(INPUT_CSV)

    total_stops = sum(len(v) for v in days.values())
    print(f"  {len(days)} ngày | {total_stops} điểm dừng")

    print(f"\n  Tạo bản đồ...")
    m = build_map(days)
    m.save(OUTPUT_MAP)

    print(f"\n  ✅ Đã lưu bản đồ -> {OUTPUT_MAP}")
    print(f"  Mở file trong trình duyệt để xem lộ trình tương tác.")
    print("="*55)


def join_coords():
    """Join tọa độ từ scored CSV vào route CSV nếu chưa có"""
    scored_path = "dalat_poi_scored.csv"
    if not os.path.exists(scored_path):
        print(f"  Không tìm thấy {scored_path}!")
        return

    # Đọc coords từ scored
    coords_map = {}
    with open(scored_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            coords_map[row["place_name"]] = {
                "lat": row.get("lat", ""),
                "lng": row.get("lng", ""),
            }

    # Đọc route
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # Join
    for row in rows:
        c = coords_map.get(row["name"], {})
        row["lat"] = c.get("lat", "")
        row["lng"] = c.get("lng", "")

    # Ghi lại
    with open(INPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Join xong: {len(rows)} dòng")


if __name__ == "__main__":
    main()
