"""
optimize_scoring_weights_advanced.py
============================================================
Thử nghiệm nhiều target khác nhau cho Linear Regression
So sánh R², tương quan, và đề xuất target tốt nhất.
============================================================
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import r2_score
import matplotlib.pyplot as plt

# Đọc dữ liệu
df = pd.read_csv("dalat_poi_scored_fix.csv", encoding="utf-8-sig")
df = df.dropna(subset=['gmaps_rating', 'gmaps_reviews_count']).copy()
print(f"Tổng số POI: {len(df)}")

# Features (TikTok metrics)
features = ['mention_count', 'total_digg', 'total_plays']
X = df[features].fillna(0).values
scaler = MinMaxScaler()
X_scaled = scaler.fit_transform(X)

# Định nghĩa các target cần thử nghiệm
targets = {
    'gmaps_rating': df['gmaps_rating'].values,
    'log_reviews_rating': np.log1p(df['gmaps_reviews_count']) * df['gmaps_rating'],
    'gmaps_reviews_count': np.log1p(df['gmaps_reviews_count']),  # log reviews
    'popularity_score_old': df['mention_count'] * 10 + df['total_digg'] * 0.001 + df['total_plays'] * 0.0001,
    'rating_filtered_50': df['gmaps_rating'].where(df['gmaps_reviews_count'] >= 50, np.nan),
    'rating_filtered_100': df['gmaps_rating'].where(df['gmaps_reviews_count'] >= 100, np.nan),
}

# Lọc bỏ NaN trong các target có điều kiện
for name in ['rating_filtered_50', 'rating_filtered_100']:
    targets[name] = targets[name].dropna().values
    # Cần đồng bộ X với các dòng không NaN
    mask = ~np.isnan(targets[name])
    # (Sẽ xử lý trong vòng lặp)

results = []

for name, y_raw in targets.items():
    if name in ['rating_filtered_50', 'rating_filtered_100']:
        # Lấy mask từ target gốc
        y_full = df['gmaps_rating'].where(df['gmaps_reviews_count'] >= (50 if '50' in name else 100), np.nan)
        mask = ~np.isnan(y_full)
        y = y_full[mask].values
        X_f = X_scaled[mask]
        n = len(y)
    else:
        y = y_raw
        X_f = X_scaled
        n = len(y)
    
    if n < 10:
        print(f"Bỏ qua {name}: chỉ có {n} POI")
        continue
    
    reg = LinearRegression()
    reg.fit(X_f, y)
    y_pred = reg.predict(X_f)
    r2 = r2_score(y, y_pred)
    # Tính tương quan Pearson giữa y_pred và y thực tế
    corr = np.corrcoef(y_pred, y)[0,1]
    # Tính trọng số chuẩn hóa
    coef = reg.coef_
    if coef.sum() != 0:
        weights = coef / coef.sum()
    else:
        weights = coef
    
    results.append({
        'target': name,
        'n_samples': n,
        'R2': r2,
        'correlation': corr,
        'intercept': reg.intercept_,
        'weights_mention': weights[0] if len(weights) > 0 else np.nan,
        'weights_digg': weights[1] if len(weights) > 1 else np.nan,
        'weights_plays': weights[2] if len(weights) > 2 else np.nan,
    })

# In kết quả so sánh
print("\n" + "="*70)
print("KẾT QUẢ THỬ NGHIỆM CÁC TARGET")
print("="*70)
results_df = pd.DataFrame(results).sort_values('correlation', ascending=False)
print(results_df.round(4).to_string())

# Vẽ biểu đồ so sánh tương quan
plt.figure(figsize=(10, 6))
bars = plt.bar(results_df['target'], results_df['correlation'], color='skyblue')
plt.axhline(y=0, color='red', linestyle='--')
plt.ylabel('Hệ số tương quan Pearson')
plt.title('Tương quan giữa dự đoán và target thực tế')
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.savefig("eda_output/target_comparison.png", dpi=150)
plt.close()
print("\nĐã lưu biểu đồ so sánh: eda_output/target_comparison.png")

# Xuất trọng số tốt nhất (dựa trên correlation cao nhất)
best = results_df.iloc[0]
print(f"\n=== Target tốt nhất: {best['target']} (corr={best['correlation']:.4f}) ===")
print(f"Công thức đề xuất: attraction_score = {best['weights_mention']:.4f} * mention_norm + {best['weights_digg']:.4f} * digg_norm + {best['weights_plays']:.4f} * plays_norm")
if best['intercept'] != 0:
    print(f"(cộng với intercept: {best['intercept']:.4f})")