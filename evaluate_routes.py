"""
evaluate_routes.py
============================================================
Đánh giá và so sánh các phương pháp tối ưu lộ trình
- Đọc các file CSV có sẵn: Greedy, SA Phase1, SA Phase2
- Tạo random baseline (xáo trộn ngẫu nhiên)
- Metrics: tổng km, feasible rate, balance (std feasible giữa các ngày)
- Xuất bảng so sánh và biểu đồ
============================================================
Yêu cầu: pip install pandas numpy matplotlib seaborn
============================================================
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os
import random
from collections import defaultdict
from math import radians, sin, cos, sqrt, asin

def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng/2)**2
    return R * 2 * asin(sqrt(a))

def load_route_metrics(filepath):
    """Đọc file CSV lộ trình và tính metrics."""
    if not os.path.exists(filepath):
        print(f"File {filepath} không tồn tại.")
        return None
    df = pd.read_csv(filepath, encoding="utf-8-sig")
    required = ['day', 'dist_km', 'feasible']
    if not all(c in df.columns for c in required):
        print(f"File {filepath} thiếu cột {required}")
        return None
    total_km = df['dist_km'].sum()
    total_stops = len(df)
    feasible = df['feasible'].astype(bool).sum()
    feasible_rate = feasible / total_stops if total_stops > 0 else 0
    daily_feasible = df.groupby('day')['feasible'].apply(lambda x: x.astype(bool).sum())
    balance_std = daily_feasible.std() if len(daily_feasible) > 1 else 0
    return {
        'total_km': total_km,
        'feasible_rate': feasible_rate,
        'balance_std': balance_std,
        'total_stops': total_stops,
        'feasible': feasible,
        'daily_feasible': daily_feasible.to_dict()
    }

def random_baseline(pois_file, num_days=3, user_start=7*60, user_end=21*60):
    df_poi = pd.read_csv(pois_file, encoding="utf-8-sig")
    df_poi = df_poi[df_poi['include_in_route'].astype(str).str.lower().isin(['true', '1'])]
    df_poi = df_poi.dropna(subset=['lat', 'lng'])
    df_poi = df_poi[(df_poi['lat'] != 0) & (df_poi['lng'] != 0)]
    if len(df_poi) == 0:
        print("Không có POI nào để tạo random baseline.")
        return None
    pois = df_poi.to_dict('records')
    random.shuffle(pois)
    n = len(pois)
    days = []
    size = n // num_days
    rem = n % num_days
    idx = 0
    for d in range(num_days):
        end = idx + size + (1 if d < rem else 0)
        days.append(pois[idx:end])
        idx = end
    total_km = 0
    total_feas = 0
    total_stops = 0
    daily_feasible = []
    for day_pois in days:
        cur_time = user_start
        cur_lat, cur_lng = 11.9404, 108.4583
        feas_count = 0
        for p in day_pois:
            dist = haversine(cur_lat, cur_lng, p['lat'], p['lng'])
            travel = (dist / 30) * 60
            arrive = cur_time + travel
            open_min = p.get('open_min', 0)
            close_min = p.get('close_min', 24*60)
            visit_min = p.get('visit_min', 45)
            start = max(arrive, open_min)
            end = start + visit_min
            feasible = (end <= close_min and end <= user_end)
            if feasible:
                feas_count += 1
                cur_time = end
            else:
                cur_time = arrive
            total_km += dist
            total_stops += 1
            cur_lat, cur_lng = p['lat'], p['lng']
        daily_feasible.append(feas_count)
        total_feas += feas_count
    feasible_rate = total_feas / total_stops if total_stops > 0 else 0
    balance_std = np.std(daily_feasible) if len(daily_feasible) > 1 else 0
    return {
        'total_km': total_km,
        'feasible_rate': feasible_rate,
        'balance_std': balance_std,
        'total_stops': total_stops,
        'feasible': total_feas,
        'daily_feasible': {i+1: f for i, f in enumerate(daily_feasible)}
    }

def main():
    files = {
        'Greedy 3D': 'dalat_route_greedy_3d.csv',
        'SA Phase1': 'dalat_route_phase1.csv',
        'SA Phase2': 'dalat_route_3days.csv'
    }
    results = {}
    for name, f in files.items():
        metrics = load_route_metrics(f)
        if metrics:
            results[name] = metrics
            print(f"{name:12} | km={metrics['total_km']:.1f} | feasible_rate={metrics['feasible_rate']:.1%} | balance_std={metrics['balance_std']:.1f}")
    
    random_metrics = random_baseline('dalat_poi_scored_fix.csv', num_days=3)
    if random_metrics:
        results['Random'] = random_metrics
        print(f"{'Random':12} | km={random_metrics['total_km']:.1f} | feasible_rate={random_metrics['feasible_rate']:.1%} | balance_std={random_metrics['balance_std']:.1f}")
    
    df_comp = pd.DataFrame(results).T[['total_km', 'feasible_rate', 'balance_std']]
    os.makedirs("evaluation_output", exist_ok=True)
    df_comp.to_csv("evaluation_output/metrics_summary.csv", encoding="utf-8-sig")
    print("\nĐã lưu bảng metrics: evaluation_output/metrics_summary.csv")
    
    # Vẽ biểu đồ
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    colors = ['#FF6B6B', '#4ECDC4', '#FFE66D', '#A37CFF']
    for i, (metric, title) in enumerate(zip(['total_km', 'feasible_rate', 'balance_std'],
                                            ['Tổng quãng đường (km)', 'Tỷ lệ đúng giờ (%)', 'Độ cân bằng (std)'])):
        values = df_comp[metric]
        axes[i].bar(values.index, values, color=colors[:len(values)])
        axes[i].set_title(title)
        axes[i].set_ylabel(metric if i==0 else '')
        axes[i].tick_params(axis='x', rotation=45)
        for j, v in enumerate(values):
            axes[i].text(j, v + 0.02 * values.max(), f'{v:.2f}', ha='center', fontsize=9)
    plt.tight_layout()
    plt.savefig("evaluation_output/methods_comparison.png", dpi=150)
    plt.show()
    print("Đã lưu biểu đồ: evaluation_output/methods_comparison.png")

if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    main()