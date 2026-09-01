"""
optimize_scoring_weights.py
============================================================
Tối ưu trọng số cho công thức scoring dựa trên dữ liệu
- Sử dụng Linear Regression để tìm hệ số tốt nhất
- So sánh với trọng số thủ công hiện tại
- Xuất trọng số mới ra file CSV
============================================================
Yêu cầu: pip install pandas scikit-learn scipy
============================================================
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, r2_score
import matplotlib.pyplot as plt
import seaborn as sns
import os

# Đọc dữ liệu POI đã có Google rating
df = pd.read_csv("05_poi_scored.csv", encoding="utf-8-sig")
# Lọc các dòng có rating > 0
df = df[df['gmaps_rating'] > 0].copy()
print(f"Số POI có rating: {len(df)}")

# Các features từ TikTok
features = ['mention_count', 'total_digg', 'total_plays']
X = df[features].fillna(0).values

# Target: gmaps_rating (hoặc có thể dùng log(1+reviews) * rating)
y = df['gmaps_rating'].values

# Chuẩn hóa features về [0,1]
scaler = MinMaxScaler()
X_scaled = scaler.fit_transform(X)

# Hồi quy tuyến tính
reg = LinearRegression()
reg.fit(X_scaled, y)

# Hệ số học được
coef = reg.coef_
intercept = reg.intercept_

# Chuẩn hóa các hệ số về tổng = 1 (nếu muốn dùng dạng weighted sum)
weights = coef / coef.sum()
print("\n=== Hệ số hồi quy gốc ===")
for f, c in zip(features, coef):
    print(f"  {f}: {c:.4f}")
print(f"  Intercept: {intercept:.4f}")
print(f"  R² score: {reg.score(X_scaled, y):.4f}")

print("\n=== Trọng số chuẩn hóa (tổng = 1) ===")
for f, w in zip(features, weights):
    print(f"  {f}: {w:.4f}")

# Dự đoán rating từ mô hình
y_pred = reg.predict(X_scaled)

# Tính sai số
mse = mean_squared_error(y, y_pred)
print(f"\nMSE: {mse:.4f}")

# So sánh với công thức thủ công hiện tại
# popularity_score = mention_count * 10 + total_digg * 0.001 + total_plays * 0.0001
old_scores = df['mention_count'] * 10 + df['total_digg'] * 0.001 + df['total_plays'] * 0.0001
# Chuẩn hóa old_scores về [0,1] để so sánh correlation
old_scores_norm = (old_scores - old_scores.min()) / (old_scores.max() - old_scores.min())
new_scores_norm = (y_pred - y_pred.min()) / (y_pred.max() - y_pred.min())

corr_old = np.corrcoef(old_scores_norm, y)[0,1]
corr_new = np.corrcoef(new_scores_norm, y)[0,1]
print(f"\nTương quan với gmaps_rating:")
print(f"  Công thức thủ công: {corr_old:.4f}")
print(f"  Mô hình học: {corr_new:.4f}")

# Vẽ biểu đồ so sánh
fig, axes = plt.subplots(1, 2, figsize=(12, 5))
axes[0].scatter(old_scores_norm, y, alpha=0.5)
axes[0].set_xlabel('Normalized old score')
axes[0].set_ylabel('gmaps_rating')
axes[0].set_title(f'Thủ công (corr={corr_old:.2f})')

axes[1].scatter(new_scores_norm, y, alpha=0.5)
axes[1].set_xlabel('Normalized predicted score')
axes[1].set_ylabel('gmaps_rating')
axes[1].set_title(f'Linear Regression (corr={corr_new:.2f})')

plt.tight_layout()
plt.savefig("eda_output/scoring_comparison.png", dpi=150)
plt.close()
print("\nĐã lưu biểu đồ so sánh: eda_output/scoring_comparison.png")

# Lưu trọng số mới vào file
weights_dict = {f: w for f, w in zip(features, weights)}
weights_dict['intercept'] = intercept
weights_df = pd.DataFrame([weights_dict])
weights_df.to_csv("05_optimized_weights.csv", index=False)
print("\nĐã lưu trọng số tối ưu vào 05_optimized_weights.csv")

# In gợi ý công thức mới
print("\n=== Công thức scoring được đề xuất ===")
print(f"attraction_score = {weights[0]:.4f} * mention_norm + {weights[1]:.4f} * digg_norm + {weights[2]:.4f} * plays_norm")
print("Trong đó mention_norm, digg_norm, plays_norm được MinMaxScaler chuẩn hóa từ dữ liệu train.")