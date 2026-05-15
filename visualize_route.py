"""
=============================================================
GIAI ĐOẠN 6 — Trực quan hóa lộ trình trên bản đồ (cải tiến)
=============================================================
Input:  dalat_route_3days.csv  (output của giai đoạn 5)
Output: dalat_route_map.html   (bản đồ tương tác có panel thông tin)

Yêu cầu: pip install folium requests

Thêm panel thông tin tổng quan (km, thời gian di chuyển, 
số điểm feasible), đặt legend chính giữa phía dưới, 
chỉ hiển thị km (tắt dặm), cải thiện marker (hình tròn màu theo ngày), 
dùng AntPath cho hiệu ứng đường chạy, thêm MiniMap, MeasureControl, 
join tọa độ từ dalat_poi_scored_fix.csv
=============================================================
"""

import csv
import folium
import os
import requests
from folium import plugins
from folium.plugins import AntPath

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV  = "dalat_route_3days.csv"
OUTPUT_MAP = "dalat_route_map.html"

# Màu sắc đẹp cho từng ngày
DAY_COLORS = {
    1: "#FF6B6B",   # đỏ san hô
    2: "#4ECDC4",   # xanh ngọc
    3: "#FFE66D",   # vàng kem
}

# Tốc độ trung bình (km/h) để tính thời gian di chuyển
AVG_SPEED = 30

DALAT_CENTER = [11.9404, 108.4583]

# ══════════════════════════════════════════════════════════════
# HÀM TIỆN ÍCH
# ══════════════════════════════════════════════════════════════

def get_tiktok_thumbnail(video_url):
    """Lấy thumbnail từ TikTok qua oembed (tuỳ chọn)"""
    if not video_url:
        return ""
    try:
        oembed_url = f"https://www.tiktok.com/oembed?url={video_url}"
        resp = requests.get(oembed_url, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("thumbnail_url", "")
    except:
        pass
    return ""

def fmt_min(minutes):
    if minutes is None:
        return "?"
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h:02d}:{m:02d}"

# ══════════════════════════════════════════════════════════════
# ĐỌC DATA VÀ TÍNH TOÁN THÔNG TIN NGÀY
# ══════════════════════════════════════════════════════════════

def load_route_with_stats(filepath):
    with open(filepath, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    days = {}
    day_stats = {}
    for row in rows:
        d = int(row["day"])
        if d not in days:
            days[d] = []
            day_stats[d] = {
                "total_km": 0.0,
                "total_travel_min": 0,
                "feasible": 0,
                "total_stops": 0,
                "start_time": None,
                "end_time": None,
            }
        days[d].append(row)
        km = float(row.get("dist_km", 0))
        day_stats[d]["total_km"] += km
        day_stats[d]["total_stops"] += 1
        if row.get("feasible", "").lower() == "true":
            day_stats[d]["feasible"] += 1
        start_str = row.get("start_visit", "")
        end_str = row.get("end_visit", "")
        if start_str and ":" in start_str:
            h, m = map(int, start_str.split(":"))
            start_min = h*60 + m
            if day_stats[d]["start_time"] is None or start_min < day_stats[d]["start_time"]:
                day_stats[d]["start_time"] = start_min
        if end_str and ":" in end_str:
            h, m = map(int, end_str.split(":"))
            end_min = h*60 + m
            if day_stats[d]["end_time"] is None or end_min > day_stats[d]["end_time"]:
                day_stats[d]["end_time"] = end_min

    for d in day_stats:
        km = day_stats[d]["total_km"]
        day_stats[d]["travel_time_min"] = round(km / AVG_SPEED * 60, 1)

    return days, day_stats

# ══════════════════════════════════════════════════════════════
# BUILD MAP VỚI PANEL THÔNG TIN
# ══════════════════════════════════════════════════════════════

def build_map(days, day_stats):
    m = folium.Map(
        location=DALAT_CENTER,
        zoom_start=13,
        tiles="CartoDB Voyager",
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> & CartoDB',
    )

    # Thêm control bổ trợ (chỉ hiển thị km)
    plugins.Fullscreen().add_to(m)
    plugins.MeasureControl(position='topright', primary_length_unit='kilometers', secondary_length_unit=None).add_to(m)
    plugins.MiniMap().add_to(m)

    # Layer groups
    day_groups = {}
    for d in sorted(days.keys()):
        group = folium.FeatureGroup(name=f"📅 Ngày {d}", show=True)
        day_groups[d] = group
        m.add_child(group)

    # Vẽ các điểm và đường
    for d, stops in sorted(days.items()):
        color = DAY_COLORS.get(d, "#888888")
        group = day_groups[d]
        coords = []
        for idx, stop in enumerate(stops, 1):
            lat = float(stop.get("lat") or stop.get("latitude") or 0)
            lng = float(stop.get("lng") or stop.get("longitude") or 0)
            if lat == 0 or lng == 0:
                continue
            coords.append([lat, lng])
            poi_type = stop.get("type", "khác").strip().lower()
            feasible = str(stop.get("feasible", "True")).strip().lower() in ("true", "1")
            status_icon = "✅" if feasible else "⚠️"
            video_url = stop.get("video_url", "")
            thumb_url = get_tiktok_thumbnail(video_url)
            thumb_html = f'<img src="{thumb_url}" width="120" style="border-radius:8px; margin-top:5px;"><br>' if thumb_url else ""
            popup_html = f"""
            <div style="font-family: 'Segoe UI', Arial; min-width: 240px; max-width: 300px;">
                <div style="background:{color}; padding:6px 10px; border-radius:8px 8px 0 0; color:white; font-weight:bold;">
                    {status_icon} <span style="font-size:1.1em;">{idx}. {stop['name']}</span>
                </div>
                <div style="padding:10px;">
                    <div style="margin-bottom:5px;">
                        <span style="color:#666;">{poi_type.upper()}</span> &nbsp;|&nbsp; Ngày {d}
                    </div>
                    <div>🕒 <b>{stop.get('start_visit','?')}</b> – <b>{stop.get('end_visit','?')}</b> &nbsp; {status_icon if feasible else '⚠️ Ngoài giờ'}</div>
                    <div>⭐ {stop.get('rating', 'N/A')} &nbsp; 📊 Score: {stop.get('attraction_score', 'N/A')}</div>
                    <div>📍 {stop.get('address', '')[:60]}</div>
                    {thumb_html}
                    <div style="margin-top:6px;"><a href="{video_url}" target="_blank" style="color:#4ECDC4;">🎬 Xem TikTok</a></div>
                </div>
            </div>
            """
            icon = folium.DivIcon(
                html=f"""
                <div style="
                    background:{color};
                    color:white;
                    border-radius:50%;
                    width:32px; height:32px;
                    display:flex; align-items:center; justify-content:center;
                    font-weight:bold; font-size:13px;
                    border:2px solid white;
                    box-shadow:0 2px 6px rgba(0,0,0,0.3);
                    opacity:{'1.0' if feasible else '0.6'};
                ">{idx}</div>
                """,
                icon_size=(32, 32),
                icon_anchor=(16, 16),
            )
            folium.Marker([lat, lng], popup=folium.Popup(popup_html, max_width=300),
                          tooltip=f"Ngày {d} #{idx}: {stop['name']}", icon=icon).add_to(group)
        if len(coords) >= 2:
            folium.PolyLine(coords, color=color, weight=4, opacity=0.8, dash_array="10, 5",
                            tooltip=f"Lộ trình Ngày {d}").add_to(group)
            AntPath(coords, color=color, weight=3, opacity=0.6, delay=1000).add_to(group)

    # === PANEL THÔNG TIN TỔNG QUAN (góc dưới bên trái, nâng lên để không đè lên legend) ===
    panel_html = """
    <div id="route-info-panel" style="
        position: fixed;
        bottom: 80px;
        left: 20px;
        background: rgba(0,0,0,0.75);
        backdrop-filter: blur(8px);
        color: white;
        padding: 10px 14px;
        border-radius: 12px;
        font-family: 'Segoe UI', Arial;
        font-size: 12px;
        z-index: 1000;
        max-width: 280px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        border-left: 4px solid #FF6B6B;
        pointer-events: none;
    ">
        <div style="font-weight:bold; margin-bottom:8px; font-size:14px;">🚀 THÔNG TIN LỘ TRÌNH</div>
    """
    for d in sorted(day_stats.keys()):
        s = day_stats[d]
        color = DAY_COLORS.get(d, "#888")
        start_str = fmt_min(s["start_time"]) if s["start_time"] else "?"
        end_str = fmt_min(s["end_time"]) if s["end_time"] else "?"
        panel_html += f"""
        <div style="margin-bottom:8px; border-left: 2px solid {color}; padding-left: 8px;">
            <div><span style="background:{color}; padding:2px 8px; border-radius:20px; font-size:10px;">📅 NGÀY {d}</span></div>
            <div>📍 {s['total_stops']} điểm • ✅ {s['feasible']} khả thi</div>
            <div>🛣️ {s['total_km']:.1f} km • 🕒 {s['travel_time_min']:.0f} phút di chuyển</div>
            <div>⏰ {start_str} – {end_str}</div>
        </div>
        """
    panel_html += """
        <div style="font-size:10px; color:#ccc; margin-top:5px;">💡 Nhấp vào marker để xem chi tiết</div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(panel_html))

    # === CHÚ THÍCH MÀU NGÀY (đặt dưới cùng, chính giữa) ===
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0,0,0,0.6);
        backdrop-filter: blur(4px);
        color: white;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 12px;
        z-index: 1000;
        font-family: 'Segoe UI', Arial;
        pointer-events: none;
        white-space: nowrap;
        display: flex;
        gap: 16px;
    ">
        <span><span style="color:#FF6B6B; font-weight:bold;">●</span> Ngày 1</span>
        <span><span style="color:#4ECDC4; font-weight:bold;">●</span> Ngày 2</span>
        <span><span style="color:#FFE66D; font-weight:bold;">●</span> Ngày 3</span>
        <span><span style="opacity:0.7; margin-left:8px;">○</span> Ngoài giờ</span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    return m

# ══════════════════════════════════════════════════════════════
# JOIN COORDINATES (giữ nguyên)
# ══════════════════════════════════════════════════════════════

def join_coords():
    scored_path = "dalat_poi_scored_fix.csv"
    if not os.path.exists(scored_path):
        print(f"  Không tìm thấy {scored_path}!")
        return
    coords_map = {}
    with open(scored_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            coords_map[row["place_name"]] = {"lat": row.get("lat", ""), "lng": row.get("lng", "")}
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        c = coords_map.get(row["name"], {})
        row["lat"] = c.get("lat", "")
        row["lng"] = c.get("lng", "")
    with open(INPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Đã cập nhật tọa độ cho {len(rows)} điểm.")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*55)
    print("  GIAI ĐOẠN 6 — Visualize lộ trình (có panel thông tin)")
    print("="*55)

    if not os.path.exists(INPUT_CSV):
        print(f"\n  ❌ Không tìm thấy file {INPUT_CSV}")
        print("  Hãy chạy route_optimizer.py trước.")
        return

    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        cols = f.readline()
    if "lat" not in cols and "latitude" not in cols:
        print("\n  ⚠️  File route chưa có tọa độ lat/lng. Đang join...")
        join_coords()

    print("\n  Đọc và tính toán thông tin lộ trình...")
    days, day_stats = load_route_with_stats(INPUT_CSV)
    total_stops = sum(len(v) for v in days.values())
    print(f"  {len(days)} ngày | {total_stops} điểm dừng")

    print("\n  Tạo bản đồ (có thể mất vài giây)...")
    m = build_map(days, day_stats)
    m.save(OUTPUT_MAP)

    print(f"\n  ✅ Đã lưu bản đồ -> {OUTPUT_MAP}")
    print("  Mở file trong trình duyệt để xem lộ trình và thông tin chi tiết.")
    print("="*55)

if __name__ == "__main__":
    main()