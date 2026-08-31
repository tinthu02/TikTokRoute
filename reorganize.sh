#!/usr/bin/env bash
# Chạy script này TRONG thư mục repo TikTokRoute đã git clone thật (có .git),
# KHÔNG chạy trong bản zip tải rời — để git mv giữ lại lịch sử commit.
# Cách dùng:
#   cd /path/to/TikTokRoute
#   bash reorganize.sh
set -e

mkdir -p scraping processing scoring routing webapp analysis common

# --- module dùng chung nhiều giai đoạn (routing + webapp) ---
git mv weather.py common/weather.py
git mv osrm.py common/osrm.py
touch common/__init__.py
git add common/__init__.py

# --- scraping ---
git mv dalat_scraper_fix.py scraping/dalat_scraper_fix.py

# --- processing ---
git mv clean_poi.py processing/clean_poi.py
git mv gmaps_join.py processing/gmaps_join.py
git mv gmaps_cache.py processing/gmaps_cache.py
git mv seed_from_existing.py processing/seed_from_existing.py

# --- scoring ---
git mv scoring.py scoring/scoring.py
git mv optimize_scoring_weights.py scoring/optimize_scoring_weights.py
git mv optimize_scoring_weights_advanced.py scoring/optimize_scoring_weights_advanced.py

# --- routing ---
git mv route_optimizer.py routing/route_optimizer.py

# --- webapp ---
git mv app.py webapp/app.py

# --- analysis ---
git mv eda_analysis.py analysis/eda_analysis.py
git mv evaluate_routes.py analysis/evaluate_routes.py
git mv generate_insights.py analysis/generate_insights.py
git mv sentiment_analysis.py analysis/sentiment_analysis.py
git mv sentiment_cache.py analysis/sentiment_cache.py
git mv visualize_route.py analysis/visualize_route.py

# full_pipeline.py, requirements.txt, README.md, .github/, và toàn bộ file
# dữ liệu (.csv, .db, .png, thư mục eda_output/, evaluation_output/) GIỮ NGUYÊN
# ở root — xem giải thích trong phần chat.

echo "Đã di chuyển xong. Giờ áp patch sửa import:"
echo "  git apply fix_imports.patch"
echo "rồi tự kiểm tra lại và commit."
