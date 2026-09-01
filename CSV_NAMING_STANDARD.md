# Chuẩn hoá tên CSV — pipeline TikTokRoute

Nguyên tắc: tên file phải nói được **"đang ở giai đoạn nào"** và **"ai đọc/ghi nó"**,
không cần mở code mới hiểu. Số thứ tự = thứ tự chảy dữ liệu thật trong `full_pipeline.py`.

## Chuỗi chuẩn (đã verify khớp input/output giữa các script)

| # | File mới | Ghi bởi | Đọc bởi | Ý nghĩa |
|---|---|---|---|---|
| 01 | `01_videos_raw.csv` | `scraping/dalat_scraper_fix.py` | `processing/clean_poi.py` (bước trích xuất) | Video TikTok thô, chưa xử lý |
| 02 | `02_poi_extracted.csv` | scraper (bước trích POI) | `processing/clean_poi.py` | POI trích từ video, chưa clean, chưa toạ độ |
| 03 | `03_poi_clean.csv` | `processing/clean_poi.py` | `processing/gmaps_join.py` | POI đã dedup, chuẩn hoá tên/alias |
| 04 | `04_poi_gmaps_matched.csv` | `processing/gmaps_join.py` | `scoring/scoring.py`, `analysis/sentiment_analysis.py` | POI đã có lat/lng, rating, giờ mở |
| 04b | `04_poi_gmaps_unmatched.csv` | `processing/gmaps_join.py` | (seed thủ công / hồi quy) | POI chưa match được Google Maps |
| 05 | `05_poi_scored.csv` | `scoring/scoring.py` | `routing/route_optimizer.py`, `webapp/app.py` | Có `attraction_score`, `include_in_route` |
| 06a | `06_route_greedy_init.csv` | `routing/route_optimizer.py` | route optimizer (nội bộ) | Khởi tạo greedy |
| 06b | `06_route_phase1_feasible.csv` | `routing/route_optimizer.py` | route optimizer (nội bộ) | Sau bước feasibility / SA phase 1 |
| 06c | `06_route_final_{N}days.csv` | `routing/route_optimizer.py` | `webapp/app.py`, `analysis/*` | **Output cuối cùng**, N = số ngày (2/3/4) |
| — | `dalat_poi_sentiment.csv` | `analysis/sentiment_analysis.py` | (nhánh phụ, không đổi tên) | Sentiment từ comment, đứng riêng ngoài chuỗi chính |

File hỗ trợ khác (giữ nguyên hoặc đổi nếu muốn):
- `optimized_weights.csv` → `05_optimized_weights.csv` (trọng số scoring)
- `metrics_summary.csv` → `06_metrics_summary.csv`
- `dalat_route_map.html` → `06_route_final_map.html`
- `insights_report*.txt` → giữ nguyên, không nằm trong luồng CSV chính

## Bug đã phát hiện (đã sửa trong lần refactor trước)
`gmaps_join.py` từng đọc **`dalat_poi_clean_fix.csv`** (487 dòng) — một file không do
script nào tạo ra — thay vì output thật của `clean_poi.py` là **`dalat_poi_clean_final.csv`**
(505 dòng). Sau chuẩn hoá, input đúng là `03_poi_clean.csv`.

## File mồ côi (không script nào dùng) → chuyển vào `data/legacy/`, không xoá
```
dalat_videos_raw.csv
dalat_videos_raw_extra.csv
dalat_videos_raw_extra2.csv
dalat_videos_raw_extra3.csv
dalat_poi_extracted.csv
dalat_poi_extracted_extra.csv
dalat_poi_extracted_extra2.csv
dalat_poi_extracted_extra3.csv
dalat_poi_clean.csv
dalat_poi_clean_fix.csv          # file gây bug, KHÔNG dùng nữa
dalat_poi_unmatched.csv
dalat_poi_unmatched_fix.csv
dalat_poi_gmaps.csv
dalat_poi_scored.csv
dalat_route_phase1.csv
dalat_route_2days.csv
dalat_route_4days.csv
dalat_route_greedy_3d.csv
dalat_route_greedy_4d_3days.csv
```
