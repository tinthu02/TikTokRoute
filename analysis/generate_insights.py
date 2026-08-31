"""
generate_insights.py
============================================================
Phân tích và xuất các insight nâng cao cho báo cáo đồ án
- Loại POI phổ biến
- Hashtag phổ biến và so sánh hiệu quả
- Từ khóa trong mô tả
- Phân tích theo thời gian (xu hướng tháng)
- Top video theo lượt xem, like, comment
- Phân tích chủ đề (ăn uống, check-in, lưu trú, trải nghiệm)
- Xuất báo cáo text và biểu đồ trực quan.
============================================================
Yêu cầu: pip install pandas matplotlib seaborn
============================================================
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import re
from collections import Counter
from datetime import datetime
import os

# Thiết lập font và style
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['axes.unicode_minus'] = False
sns.set_style("darkgrid")

# Tạo thư mục output nếu chưa có
os.makedirs("eda_output", exist_ok=True)

# Đọc dữ liệu
poi_clean = pd.read_csv("dalat_poi_clean_final.csv", encoding="utf-8-sig")
poi_clean['type'] = poi_clean['type'].str.strip().str.split('|').str[0]

videos_raw = pd.read_csv("dalat_videos_raw_fix.csv", encoding="utf-8-sig")
# Chuẩn hóa cột text
for col in ['description', 'voice_to_text', 'in_video_text']:
    if col in videos_raw.columns:
        videos_raw[col] = videos_raw[col].fillna('').astype(str)

# === 1. PHÂN PHỐI LOẠI POI ===
type_counts = poi_clean['type'].value_counts()
type_pcts = poi_clean['type'].value_counts(normalize=True) * 100

# Biểu đồ tròn và cột
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

sns.barplot(x=type_counts.values, y=type_counts.index, ax=axes[1], palette='viridis')
axes[1].set_xlabel('Số lượng')
axes[1].set_title('Phân phối loại POI (cột)')
plt.tight_layout()
plt.savefig("eda_output/poi_type_distribution.png", dpi=150, bbox_inches='tight')
plt.close()

# === 2. HASHTAG ===
all_hashtags = []
for hs in videos_raw['hashtags'].dropna():
    tags = [h.strip().lower() for h in str(hs).split(',')]
    if len(tags) == 1 and ',' not in str(hs):
        tags = str(hs).split()
    all_hashtags.extend(tags)
hashtag_counts = Counter([h for h in all_hashtags if len(h)>=3])
top_hashtags = hashtag_counts.most_common(10)

# Biểu đồ top hashtag
if top_hashtags:
    tags, counts = zip(*top_hashtags)
    plt.figure(figsize=(10, 6))
    sns.barplot(x=list(counts), y=list(tags), palette='coolwarm')
    plt.xlabel('Số lần xuất hiện')
    plt.title('Top 10 hashtag xuất hiện nhiều nhất')
    plt.tight_layout()
    plt.savefig("eda_output/top_hashtags.png", dpi=150)
    plt.close()

# === 3. TỪ KHÓA TRONG MÔ TẢ ===
all_text = ' '.join(videos_raw['description'].dropna().astype(str))
words = re.findall(r'\b\w{4,}\b', all_text.lower())
word_counts = Counter(words)
stopwords = {'các', 'cho', 'có', 'không', 'những', 'một', 'với', 'này', 'đến', 'từ', 'bạn', 'mình', 'của', 'và', 'là', 'ở', 'đã', 'sẽ', 'khi'}
filtered_words = {w: cnt for w, cnt in word_counts.items() if w not in stopwords}
top_words = Counter(filtered_words).most_common(15)

if top_words:
    words_list, counts_list = zip(*top_words[:10])
    plt.figure(figsize=(10, 6))
    sns.barplot(x=list(counts_list), y=list(words_list), palette='viridis')
    plt.xlabel('Số lần xuất hiện')
    plt.title('Top 10 từ khóa xuất hiện trong mô tả video')
    plt.tight_layout()
    plt.savefig("eda_output/top_keywords.png", dpi=150)
    plt.close()

# === 4. PHÂN TÍCH CHỦ ĐỀ (dùng từ khóa) ===
food_keywords = ['quán', 'ngon', 'cafe', 'ăn', 'bánh', 'cơm', 'bún', 'món', 'nhà hàng', 'ẩm thực']
checkin_keywords = ['chụp', 'ảnh', 'view', 'checkin', 'cảnh', 'đẹp', 'thác', 'đồi', 'rừng', 'sống ảo']
stay_keywords = ['homestay', 'khách sạn', 'villa', 'resort', 'phòng', 'nghỉ']
exp_keywords = ['săn mây', 'cắm trại', 'trekking', 'trải nghiệm', 'khám phá']

text_lower = ' '.join(videos_raw['description'].dropna().astype(str)).lower()
topic_counts = {
    'Ẩm thực': sum(text_lower.count(k) for k in food_keywords),
    'Check-in / Cảnh đẹp': sum(text_lower.count(k) for k in checkin_keywords),
    'Lưu trú': sum(text_lower.count(k) for k in stay_keywords),
    'Trải nghiệm': sum(text_lower.count(k) for k in exp_keywords)
}
topics_df = pd.DataFrame(topic_counts.items(), columns=['Chủ đề', 'Số lượt nhắc'])
plt.figure(figsize=(8, 6))
sns.barplot(data=topics_df, x='Số lượt nhắc', y='Chủ đề', palette='Set2')
plt.title('Phân bố chủ đề nội dung TikTok Đà Lạt')
plt.tight_layout()
plt.savefig("eda_output/topic_distribution.png", dpi=150)
plt.close()

# === 5. XU HƯỚNG THEO THỜI GIAN ===
videos_raw['create_time'] = pd.to_datetime(videos_raw['create_time'], unit='s', errors='coerce')
videos_raw = videos_raw.dropna(subset=['create_time'])
videos_raw['year_month'] = videos_raw['create_time'].dt.to_period('M')
monthly = videos_raw.groupby('year_month').agg(video_count=('video_id', 'count'), total_likes=('digg_count', 'sum')).reset_index()
monthly['month_str'] = monthly['year_month'].astype(str)

if len(monthly) > 1:
    fig, axes = plt.subplots(2, 1, figsize=(12, 8))
    axes[0].plot(monthly['month_str'], monthly['video_count'], marker='o', color='steelblue')
    axes[0].set_title('Số lượng video theo tháng')
    axes[0].set_ylabel('Số video')
    axes[0].tick_params(axis='x', rotation=45)
    axes[1].plot(monthly['month_str'], monthly['total_likes'], marker='s', color='darkorange')
    axes[1].set_title('Tổng lượt thích theo tháng')
    axes[1].set_ylabel('Lượt thích')
    axes[1].tick_params(axis='x', rotation=45)
    plt.tight_layout()
    plt.savefig("eda_output/monthly_trend.png", dpi=150)
    plt.close()

# === 6. TOP VIDEO TƯƠNG TÁC ===
# Top 5 views
top5_views = videos_raw.nlargest(5, 'play_count')[['video_id', 'play_count', 'digg_count']]
# Top 5 likes
top5_likes = videos_raw.nlargest(5, 'digg_count')[['video_id', 'digg_count', 'play_count']]
# Top 5 comments
top5_comments = videos_raw.nlargest(5, 'comment_count')[['video_id', 'comment_count', 'play_count']]

# Lưu báo cáo text
with open("eda_output/insights_report_full.txt", "w", encoding="utf-8") as f:
    f.write("=============================================================\n")
    f.write("BÁO CÁO INSIGHTS DỮ LIỆU TIKTOK ĐÀ LẠT\n")
    f.write(f"Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write("=============================================================\n\n")
    
    f.write("1. PHÂN PHỐI LOẠI ĐỊA ĐIỂM\n")
    for t, cnt in type_counts.items():
        f.write(f"- {t}: {cnt} POI ({type_pcts[t]:.1f}%)\n")
    f.write(f"\n=> Cafe là loại phổ biến nhất ({type_pcts['cafe']:.1f}%), phản ánh nhu cầu thư giãn và check-in của du khách.\n\n")
    
    f.write("2. HASHTAG PHỔ BIẾN\n")
    for tag, cnt in top_hashtags[:5]:
        f.write(f"-#{tag}: {cnt} lần\n")
    f.write("\n3. TỪ KHÓA TRONG MÔ TẢ (TOP 10)\n")
    for w, cnt in top_words[:10]:
        f.write(f"- {w}: {cnt} lần\n")
    f.write("\n4. PHÂN TÍCH CHỦ ĐỀ\n")
    for topic, count in topic_counts.items():
        pct = count / sum(topic_counts.values()) * 100
        f.write(f"- {topic}: {count} lượt nhắc ({pct:.1f}%)\n")
    f.write("\n5. XU HƯỚNG THEO THỜI GIAN\n")
    if len(monthly) > 0:
        max_month = monthly.loc[monthly['video_count'].idxmax(), 'month_str']
        f.write(f"- Tháng có nhiều video nhất: {max_month} với {monthly['video_count'].max()} video\n")
        max_like_month = monthly.loc[monthly['total_likes'].idxmax(), 'month_str']
        f.write(f"- Tháng có tổng lượt thích cao nhất: {max_like_month} với {monthly['total_likes'].max():,} lượt\n")
    f.write("\n6. TOP VIDEO TƯƠNG TÁC\n")
    f.write("Top 5 video có lượt xem cao nhất:\n")
    for _, row in top5_views.iterrows():
        f.write(f"- Video {row['video_id']}: {row['play_count']:,} lượt xem (likes: {row['digg_count']:,})\n")
    f.write("\n7. TRẢI NGHIỆM SĂN MÂY\n")
    mask = videos_raw['description'].str.contains('săn mây', case=False, na=False) | \
           videos_raw['voice_to_text'].str.contains('săn mây', case=False, na=False) | \
           videos_raw['in_video_text'].str.contains('săn mây', case=False, na=False)
    sm_videos = videos_raw[mask]
    f.write(f"- Số video: {len(sm_videos)}\n")
    f.write(f"- Tổng lượt xem: {sm_videos['play_count'].sum():,}\n")
    f.write(f"- Tổng lượt thích: {sm_videos['digg_count'].sum():,}\n")

print("✅ Đã tạo các biểu đồ và báo cáo trong thư mục 'eda_output/'")