#!/usr/bin/env bash
# apply_csv_standardization.sh
# Chạy TỪ THƯ MỤC GỐC của repo TikTokRoute (nơi có full_pipeline.py).
# Không cần git. Đổi tên file bằng `mv`, sửa code bằng `sed -i`.
# An toàn để xem trước: chạy với --dry-run trước khi --apply.
#
# Cách dùng:
#   chmod +x apply_csv_standardization.sh
#   ./apply_csv_standardization.sh --dry-run
#   ./apply_csv_standardization.sh --apply

set -euo pipefail
MODE="${1:-}"
if [[ "$MODE" != "--apply" && "$MODE" != "--dry-run" ]]; then
  echo "Cách dùng: $0 --dry-run | --apply"
  exit 1
fi
APPLY=false
[[ "$MODE" == "--apply" ]] && APPLY=true

run() {
  if $APPLY; then eval "$1"; else echo "[dry-run] $1"; fi
}

if [[ ! -f "full_pipeline.py" ]]; then
  echo "❌ Không thấy full_pipeline.py — hãy chạy script này từ thư mục gốc repo."
  exit 1
fi

echo "== 1) Đổi tên các file CSV chính trong luồng pipeline =="
declare -A RENAMES=(
  ["dalat_videos_raw_fix.csv"]="01_videos_raw.csv"
  ["dalat_poi_extracted_fix.csv"]="02_poi_extracted.csv"
  ["dalat_poi_clean_final.csv"]="03_poi_clean.csv"
  ["dalat_poi_gmaps.csv"]="04_poi_gmaps_matched.csv"
  ["dalat_poi_unmatched.csv"]="04_poi_gmaps_unmatched.csv"
  ["dalat_poi_scored.csv"]="05_poi_scored.csv"
  ["optimized_weights.csv"]="05_optimized_weights.csv"
  ["dalat_route_greedy_3days.csv"]="06_route_greedy_init.csv"
  ["dalat_route_3days.csv"]="06_route_final_3days.csv"
  ["evaluation_output/metrics_summary.csv"]="evaluation_output/06_metrics_summary.csv"
)
for src in "${!RENAMES[@]}"; do
  dst="${RENAMES[$src]}"
  if [[ -f "$src" ]]; then
    run "mv '$src' '$dst'"
  else
    echo "SKIP (không tồn tại): $src"
  fi
done

echo ""
echo "== 2) Di dời file mồ côi (không script nào dùng) vào data/legacy/ =="
run "mkdir -p data/legacy"
for f in \
  dalat_poi_clean.csv \
  dalat_poi_clean_fix.csv \
  dalat_poi_extracted.csv \
  dalat_poi_extracted_extra.csv \
  dalat_poi_extracted_extra2.csv \
  dalat_poi_extracted_extra3.csv \
  dalat_route_2days.csv \
  dalat_route_4days.csv \
  dalat_route_greedy_4d_3days.csv \
  dalat_videos_raw.csv \
  dalat_videos_raw_extra.csv \
  dalat_videos_raw_extra2.csv \
  dalat_videos_raw_extra3.csv \
  ; do
  if [[ -f "$f" ]]; then
    run "mv '$f' 'data/legacy/$f'"
  else
    echo "SKIP (không tồn tại): $f"
  fi
done

echo ""
echo "== 3) Sửa các tham chiếu CSV/HTML cũ trong code (sed) =="

sed_replace() {
  local file="$1" old="$2" new="$3"
  if [[ -f "$file" ]]; then
    run "sed -i \"s|$old|$new|g\" '$file'"
  else
    echo "SKIP (không thấy file): $file"
  fi
}

sed_replace "scraping/dalat_scraper_fix.py" "dalat_videos_raw_fix.csv" "01_videos_raw.csv"
sed_replace "scraping/dalat_scraper_fix.py" "dalat_poi_extracted_fix.csv" "02_poi_extracted.csv"

sed_replace "processing/clean_poi.py" "dalat_poi_extracted_fix.csv" "02_poi_extracted.csv"
sed_replace "processing/clean_poi.py" "dalat_poi_clean_final.csv" "03_poi_clean.csv"

# BUGFIX thật: gmaps_join.py trước đọc "dalat_poi_clean_fix.csv" (sai / không tồn tại
# do script nào tạo ra) thay vì output thật "dalat_poi_clean_final.csv" của clean_poi.py.
sed_replace "processing/gmaps_join.py" "dalat_poi_clean_fix.csv" "03_poi_clean.csv"
sed_replace "processing/gmaps_join.py" "dalat_poi_clean.csv" "03_poi_clean.csv"
sed_replace "processing/gmaps_join.py" "dalat_poi_gmaps_fix.csv" "04_poi_gmaps_matched.csv"
sed_replace "processing/gmaps_join.py" "dalat_poi_gmaps.csv" "04_poi_gmaps_matched.csv"
sed_replace "processing/gmaps_join.py" "dalat_poi_unmatched_fix.csv" "04_poi_gmaps_unmatched.csv"
sed_replace "processing/gmaps_join.py" "dalat_poi_unmatched.csv" "04_poi_gmaps_unmatched.csv"

sed_replace "processing/seed_from_existing.py" "dalat_poi_gmaps_fix.csv" "04_poi_gmaps_matched.csv"
sed_replace "processing/seed_from_existing.py" "dalat_poi_unmatched_fix.csv" "04_poi_gmaps_unmatched.csv"

sed_replace "scoring/scoring.py" "dalat_poi_gmaps_fix.csv" "04_poi_gmaps_matched.csv"
sed_replace "scoring/scoring.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"

sed_replace "scoring/optimize_scoring_weights.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"
sed_replace "scoring/optimize_scoring_weights.py" "optimized_weights.csv" "05_optimized_weights.csv"
sed_replace "scoring/optimize_scoring_weights_advanced.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"

sed_replace "routing/route_optimizer.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"
sed_replace "routing/route_optimizer.py" "dalat_route_greedy_3d.csv" "06_route_greedy_init.csv"
sed_replace "routing/route_optimizer.py" "dalat_route_phase1.csv" "06_route_phase1_feasible.csv"
sed_replace "routing/route_optimizer.py" "dalat_route_{NUM_DAYS}days.csv" "06_route_final_{NUM_DAYS}days.csv"

sed_replace "webapp/app.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"

sed_replace "analysis/visualize_route.py" "dalat_route_3days.csv" "06_route_final_3days.csv"
sed_replace "analysis/visualize_route.py" "dalat_route_map.html" "06_route_final_map.html"
sed_replace "analysis/visualize_route.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"

sed_replace "analysis/generate_insights.py" "dalat_poi_clean_final.csv" "03_poi_clean.csv"
sed_replace "analysis/generate_insights.py" "dalat_videos_raw_fix.csv" "01_videos_raw.csv"

sed_replace "analysis/eda_analysis.py" "dalat_poi_clean_final.csv" "03_poi_clean.csv"
sed_replace "analysis/eda_analysis.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"
sed_replace "analysis/eda_analysis.py" "dalat_videos_raw_fix.csv" "01_videos_raw.csv"

sed_replace "analysis/evaluate_routes.py" "dalat_route_greedy_3d.csv" "06_route_greedy_init.csv"
sed_replace "analysis/evaluate_routes.py" "dalat_route_phase1.csv" "06_route_phase1_feasible.csv"
sed_replace "analysis/evaluate_routes.py" "dalat_route_3days.csv" "06_route_final_3days.csv"
sed_replace "analysis/evaluate_routes.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"
sed_replace "analysis/evaluate_routes.py" "evaluation_output/metrics_summary.csv" "evaluation_output/06_metrics_summary.csv"

sed_replace "analysis/sentiment_analysis.py" "dalat_poi_gmaps_fix.csv" "04_poi_gmaps_matched.csv"
sed_replace "analysis/sentiment_analysis.py" "dalat_poi_sentiment.csv" "04_poi_sentiment.csv"

sed_replace "full_pipeline.py" "dalat_poi_extracted_fix.csv" "02_poi_extracted.csv"
sed_replace "full_pipeline.py" "dalat_poi_clean_final.csv" "03_poi_clean.csv"
sed_replace "full_pipeline.py" "dalat_poi_gmaps_fix.csv" "04_poi_gmaps_matched.csv"
sed_replace "full_pipeline.py" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"
sed_replace "full_pipeline.py" "dalat_route_3days.csv" "06_route_final_3days.csv"
sed_replace "full_pipeline.py" "dalat_route_map.html" "06_route_final_map.html"

echo ""
echo "== 4) Cập nhật .gitignore theo tên mới =="
if [[ -f ".gitignore" ]]; then
  sed_replace ".gitignore" "dalat_poi_gmaps_fix.csv" "04_poi_gmaps_matched.csv"
  sed_replace ".gitignore" "dalat_poi_scored_fix.csv" "05_poi_scored.csv"
  sed_replace ".gitignore" "dalat_poi_unmatched_fix.csv" "04_poi_gmaps_unmatched.csv"
  sed_replace ".gitignore" "dalat_poi_sentiment.csv" "04_poi_sentiment.csv"
  sed_replace ".gitignore" "dalat_route_3days.csv" "06_route_final_3days.csv"
  sed_replace ".gitignore" "dalat_route_greedy_3d.csv" "06_route_greedy_init.csv"
  sed_replace ".gitignore" "dalat_route_phase1.csv" "06_route_phase1_feasible.csv"
  sed_replace ".gitignore" "dalat_route_map.html" "06_route_final_map.html"
fi

echo ""
if $APPLY; then
  echo "✅ Xong. Kiểm tra: python3 -m py_compile <file>.py cho các script đã sửa,"
  echo "   rồi commit thay đổi (git add -A && git commit)."
else
  echo "Đây là DRY RUN — chưa có gì thay đổi. Chạy lại với --apply để thực thi."
fi
