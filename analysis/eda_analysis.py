"""
=============================================================
EDA (Exploratory Data Analysis) cho dữ liệu Đà Lạt
=============================================================
Phân tích:
- Phân phối loại POI
- Tương quan giữa TikTok engagement và Google rating
- Heatmap mật độ địa điểm
- Trend theo thời gian
- Thống kế từ khóa, hashtag, engagement
=============================================================
Yêu cầu: pip install pandas matplotlib seaborn numpy
=============================================================
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
import os
import re
from collections import Counter

# Thiết lập font tiếng Việt cho matplotlib (nếu có)
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['axes.unicode_minus'] = False

# Tạo thư mục lưu ảnh nếu chưa có
os.makedirs("eda_output", exist_ok=True)

# =============================================================
# 1. ĐỌC DỮ LIỆU
# =============================================================
print("="*60)
print("ĐANG ĐỌC DỮ LIỆU...")
print("="*60)

# POI clean (sau khi gộp tên)
poi_clean = pd.read_csv("03_poi_clean.csv", encoding="utf-8-sig")
# Chuẩn hóa type: bỏ khoảng trắng, lấy phần đầu trước '|'
poi_clean['type'] = poi_clean['type'].str.strip().str.split('|').str[0]
print(f"Đọc {len(poi_clean)} POI từ 03_poi_clean.csv")

# POI scored (có điểm attraction, Google rating)
poi_scored = pd.read_csv("05_poi_scored.csv", encoding="utf-8-sig")
poi_scored['type'] = poi_scored['type'].str.strip().str.split('|').str[0]
print(f"Đọc {len(poi_scored)} POI từ 05_poi_scored.csv")

# Video raw (có mention_count, digg, play, timestamp...)
videos_raw = pd.read_csv("01_videos_raw.csv", encoding="utf-8-sig")
print(f"Đọc {len(videos_raw)} video từ 01_videos_raw.csv")

# =============================================================
# 2. PHÂN PHỐI LOẠI POI
# =============================================================
print("\n" + "="*60)
print("1. PHÂN PHỐI LOẠI ĐỊA ĐIỂM")
print("="*60)

type_counts = poi_clean['type'].value_counts()
type_pcts = poi_clean['type'].value_counts(normalize=True) * 100

print("\nSố lượng POI theo từng loại:")
for t, cnt in type_counts.items():
    print(f"  {t}: {cnt} POI ({type_pcts[t]:.1f}%)")

# Vẽ biểu đồ tròn (ẩn nhãn phần nhỏ, dùng legend)
fig, axes = plt.subplots(1, 2, figsize=(14, 5))

labels = type_counts.index
sizes = type_counts.values

def make_autopct(values):
    def my_autopct(pct):
        return f'{pct:.1f}%' if pct >= 3 else ''
    return my_autopct

wedges, texts, autotexts = axes[0].pie(sizes, labels=None, autopct=make_autopct(sizes), 
                                        startangle=90, colors=sns.color_palette("Set2"))
axes[0].legend(wedges, labels, title="Loại POI", loc="center left", bbox_to_anchor=(1, 0, 0.5, 1))
axes[0].set_title('Phân phối loại POI (tròn)')

# Bar chart
sns.barplot(x=type_counts.values, y=type_counts.index, ax=axes[1], palette='viridis')
axes[1].set_xlabel('Số lượng')
axes[1].set_title('Phân phối loại POI (cột)')

plt.tight_layout()
plt.savefig("eda_output/poi_type_distribution.png", dpi=150, bbox_inches='tight')
plt.close()
print("  Đã lưu biểu đồ: eda_output/poi_type_distribution.png")

# =============================================================
# 3. TƯƠNG QUAN GIỮA TIKTOK ENGAGEMENT VÀ GOOGLE RATING
# =============================================================
print("\n" + "="*60)
print("2. TƯƠNG QUAN TIKTOK vs GOOGLE")
print("="*60)

# Lấy các cột cần thiết
df_corr = poi_scored[['mention_count', 'total_digg', 'total_plays', 'gmaps_rating', 'gmaps_reviews_count']].dropna()
df_corr = df_corr[df_corr['gmaps_rating'] > 0]

print(f"Số POI có đủ cả TikTok và Google data: {len(df_corr)}")

# Ma trận tương quan
corr_matrix = df_corr.corr(method='pearson')
print("\nMa trận tương quan (Pearson):")
print(corr_matrix.round(3))

# Vẽ heatmap
plt.figure(figsize=(8, 6))
sns.heatmap(corr_matrix, annot=True, cmap='coolwarm', center=0, fmt='.2f')
plt.title('Tương quan giữa TikTok Engagement và Google Maps Rating')
plt.tight_layout()
plt.savefig("eda_output/correlation_heatmap.png", dpi=150)
plt.close()
print("  Đã lưu heatmap: eda_output/correlation_heatmap.png")

# Scatter plot giữa total_digg và gmaps_rating
plt.figure(figsize=(8, 5))
sns.scatterplot(data=df_corr, x='total_digg', y='gmaps_rating', alpha=0.6, color='teal')
plt.xlabel('Tổng lượt thích TikTok')
plt.ylabel('Google Rating')
plt.title('Mối quan hệ giữa Viral TikTok và Đánh giá Google')
plt.xscale('log')  # log scale vì dữ liệu phân tán
plt.tight_layout()
plt.savefig("eda_output/scatter_digg_vs_rating.png", dpi=150)
plt.close()
print("  Đã lưu scatter plot: eda_output/scatter_digg_vs_rating.png")

# =============================================================
# 4. HEATMAP MẬT ĐỘ ĐỊA ĐIỂM THEO KHU VỰC
# =============================================================
print("\n" + "="*60)
print("3. HEATMAP MẬT ĐỘ POI")
print("="*60)

# Dùng poi_scored (đã có lat, lng từ Google Maps)
poi_coords = poi_scored.dropna(subset=['lat', 'lng'])
poi_coords = poi_coords[(poi_coords['lat'] != 0) & (poi_coords['lng'] != 0)]
print(f"Số POI có tọa độ: {len(poi_coords)}")

if len(poi_coords) > 0:
    plt.figure(figsize=(10, 8))
    sns.kdeplot(data=poi_coords, x='lng', y='lat', fill=True, cmap='Reds', thresh=0.05, levels=20)
    plt.scatter(poi_coords['lng'], poi_coords['lat'], s=10, alpha=0.5, color='blue')
    plt.xlabel('Kinh độ')
    plt.ylabel('Vĩ độ')
    plt.title('Mật độ phân bố địa điểm du lịch Đà Lạt')
    plt.tight_layout()
    plt.savefig("eda_output/poi_density_heatmap.png", dpi=150)
    plt.close()
    print("  Đã lưu heatmap: eda_output/poi_density_heatmap.png")
else:
    print("  Không có dữ liệu tọa độ để vẽ heatmap.")

# =============================================================
# 5. PHÂN TÍCH HASHTAG VÀ TỪ KHÓA
# =============================================================
print("\n" + "="*60)
print("5. PHÂN TÍCH HASHTAG VÀ TỪ KHÓA")
print("="*60)

# Hashtags (cột 'hashtags' có thể là chuỗi phân cách bằng dấu phẩy hoặc khoảng trắng)
all_hashtags = []
for hs in videos_raw['hashtags'].dropna():
    # Thử tách bằng dấu phẩy trước
    tags = [h.strip().lower() for h in str(hs).split(',')]
    # Nếu chỉ có một phần tử và không có dấu phẩy, tách bằng khoảng trắng
    if len(tags) == 1 and ',' not in hs:
        tags = hs.split()
    all_hashtags.extend(tags)
hashtag_counts = Counter(all_hashtags)
# Lọc bỏ hashtag rỗng hoặc quá ngắn (<3 ký tự)
hashtag_counts = {k:v for k,v in hashtag_counts.items() if k and len(k)>=3}
print("\nTop 10 hashtag phổ biến nhất:")
if hashtag_counts:
    for tag, cnt in Counter(hashtag_counts).most_common(10):
        print(f"  #{tag}: {cnt} lần xuất hiện")
else:
    print("  Không tìm thấy hashtag nào (có thể cột 'hashtags' rỗng).")

# Phần từ khóa giữ nguyên
all_text = ' '.join(videos_raw['description'].dropna().astype(str))
words = re.findall(r'\b\w{4,}\b', all_text.lower())
word_counts = Counter(words)
print("\nTop 10 từ khóa xuất hiện nhiều nhất trong mô tả:")
stopwords = {'các', 'cho', 'có', 'không', 'những', 'một', 'với', 'này', 'đến', 'từ', 'bạn', 'mình', 'của', 'và', 'là', 'ở', 'đã', 'sẽ', 'khi'}
filtered_words = {w: cnt for w, cnt in word_counts.items() if w not in stopwords and len(w) > 2}
for w, cnt in sorted(filtered_words.items(), key=lambda x: -x[1])[:15]:
    print(f"  {w}: {cnt} lần")

# =============================================================
# 6. PHÂN TÍCH TỪ KHÓA / HASHTAG
# =============================================================
print("\n" + "="*60)
print("5. PHÂN TÍCH HASHTAG VÀ TỪ KHÓA")
print("="*60)

# Hashtags (trong cột 'hashtags' - đã được nối bằng ", ")
all_hashtags = []
for hs in videos_raw['hashtags'].dropna():
    tags = [h.strip().lower() for h in hs.split(',')]
    all_hashtags.extend(tags)
hashtag_counts = Counter(all_hashtags)
print("\nTop 10 hashtag phổ biến nhất:")
for tag, cnt in hashtag_counts.most_common(10):
    print(f"  #{tag}: {cnt} lần xuất hiện")

# Trích xuất các từ khóa từ description
all_text = ' '.join(videos_raw['description'].dropna().astype(str))
words = re.findall(r'\b\w{4,}\b', all_text.lower())
word_counts = Counter(words)
print("\nTop 10 từ khóa xuất hiện nhiều nhất trong mô tả (bỏ stopwords đơn giản):")
stopwords = {'các', 'cho', 'có', 'không', 'những', 'một', 'với', 'này', 'đến', 'từ', 'bạn', 'mình', 'của', 'và', 'là', 'ở', 'đã', 'sẽ', 'khi'}
filtered_words = {w: cnt for w, cnt in word_counts.items() if w not in stopwords and len(w) > 2}
for w, cnt in sorted(filtered_words.items(), key=lambda x: -x[1])[:15]:
    print(f"  {w}: {cnt} lần")

# =============================================================
# 7. INSIGHTS NỔI BẬT VỀ ĐỊA ĐIỂM
# =============================================================
print("\n" + "="*60)
print("6. INSIGHTS NỔI BẬT VỀ ĐỊA ĐIỂM")
print("="*60)

# Top POI theo TikTok mention (trong file clean)
top_mentions = poi_clean.nlargest(5, 'mention_count')[['place_name', 'mention_count', 'total_digg', 'total_plays', 'type']]
print("\nTop 5 POI có mention nhiều nhất trên TikTok:")
for i, row in top_mentions.iterrows():
    print(f"  {row['place_name']} ({row['type']}) – {row['mention_count']} video, {int(row['total_digg'])} likes, {int(row['total_plays'])} views")

# Top POI theo Google rating
top_rating = poi_scored[poi_scored['gmaps_rating'] > 0].nlargest(5, 'gmaps_rating')[['place_name', 'gmaps_rating', 'gmaps_reviews_count', 'type']]
print("\nTop 5 POI có Google rating cao nhất:")
for i, row in top_rating.iterrows():
    print(f"  {row['place_name']} ({row['type']}) – {row['gmaps_rating']} ⭐ ({int(row['gmaps_reviews_count'])} reviews)")

# POI hot nhất tổng hợp (attraction_score cao)
top_score = poi_scored.nlargest(5, 'attraction_score')[['place_name', 'attraction_score', 'mention_count', 'gmaps_rating']]
print("\nTop 5 POI có Attraction Score tổng hợp cao nhất:")
for i, row in top_score.iterrows():
    print(f"  {row['place_name']} – score={row['attraction_score']:.3f} (mentions={int(row['mention_count'])}, rating={row['gmaps_rating']})")

# =============================================================
# 8. THỐNG KÊ SO SÁNH CÁC LOẠI POI
# =============================================================
print("\n" + "="*60)
print("7. THỐNG KÊ THEO LOẠI ĐỊA ĐIỂM")
print("="*60)

grouped = poi_clean.groupby('type').agg(
    avg_mentions=('mention_count', 'mean'),
    avg_digg=('total_digg', 'mean'),
    avg_plays=('total_plays', 'mean'),
    count=('place_name', 'count')
).round(0).sort_values('avg_mentions', ascending=False)
print("\nTrung bình mỗi loại POI:")
print(grouped)

# =============================================================
# 9. XUẤT BÁO CÁO TỔNG HỢP
# =============================================================
print("\n" + "="*60)
print("8. XUẤT BÁO CÁO TEXT")
print("="*60)

with open("eda_output/insights_report.txt", "w", encoding="utf-8") as f:
    f.write("=============================================================\n")
    f.write("BÁO CÁO PHÂN TÍCH DỮ LIỆU ĐÀ LẠT (EDA)\n")
    f.write(f"Thời gian chạy: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write("=============================================================\n\n")
    
    f.write("1. TỔNG QUAN DỮ LIỆU\n")
    f.write(f"- Số POI sau clean: {len(poi_clean)}\n")
    f.write(f"- Số video TikTok thu thập: {len(videos_raw)}\n")
    f.write(f"- Số POI có Google Maps rating: {len(poi_scored[poi_scored['gmaps_rating']>0])}\n\n")
    
    f.write("2. PHÂN PHỐI LOẠI POI\n")
    for t, cnt in type_counts.items():
        f.write(f"- {t}: {cnt} POI ({type_pcts[t]:.1f}%)\n")
    
    f.write("\n3. TƯƠNG QUAN NỔI BẬT\n")
    f.write(f"- Hệ số tương quan giữa total_digg và gmaps_rating: {corr_matrix.loc['total_digg', 'gmaps_rating']:.3f}\n")
    f.write("- Nhận xét: TikTok viral và Google rating có tương quan yếu (không nhất thiết quán nổi trên TikTok được đánh giá cao trên Maps).\n")
    
    f.write("\n4. TOP HASHTAG\n")
    if hashtag_counts:
        for tag, cnt in Counter(hashtag_counts).most_common(5):
            f.write(f"-#{tag}: {cnt} lần\n")
    else:
        f.write("Không có dữ liệu hashtag.\n")
    
    f.write("\n5. INSIGHTS CHÍNH\n")
    f.write(f"- {top_mentions.iloc[0]['place_name']} là địa điểm được nhắc nhiều nhất TikTok ({int(top_mentions.iloc[0]['mention_count'])} video).\n")
    f.write(f"- {top_rating.iloc[0]['place_name']} có Google rating cao nhất ({top_rating.iloc[0]['gmaps_rating']}⭐).\n")
    f.write(f"- Loại hình phổ biến nhất: {type_counts.index[0]} chiếm {type_pcts.iloc[0]:.1f}%.\n")

print("  Đã lưu báo cáo text: eda_output/insights_report.txt")
print("\n" + "="*60)
print("PHÂN TÍCH EDA HOÀN TẤT!")
print("Các biểu đồ và báo cáo được lưu trong thư mục 'eda_output/'")
print("="*60)