# data/legacy/

Các file CSV trong thư mục này **không còn được script nào trong pipeline hiện tại
đọc hoặc ghi**. Chúng được giữ lại (không xoá) để không mất dữ liệu lịch sử, nhưng
đã được chuyển ra khỏi thư mục gốc để pipeline chính (`01_..06_*.csv`) không bị
lẫn với các bản chạy thử / bổ sung / lỗi thời trước đây.

| File | Lý do không còn dùng |
|---|---|
| `dalat_videos_raw.csv` | Bản raw gốc trước khi có `_fix`; đã thay bằng `01_videos_raw.csv` |
| `dalat_videos_raw_extra.csv`, `_extra2.csv`, `_extra3.csv` | Các lần scrape bổ sung, không nằm trong luồng `full_pipeline.py` |
| `dalat_poi_extracted.csv` | Bản trích xuất POI trước `_fix`; đã thay bằng `02_poi_extracted.csv` |
| `dalat_poi_extracted_extra.csv`, `_extra2.csv`, `_extra3.csv` | POI bổ sung từ các lần scrape extra ở trên |
| `dalat_poi_clean.csv` | Bản clean đầu tiên, trước khi có bản `_final`/`_fix`; không script nào đọc |
| `dalat_poi_clean_fix.csv` | **File gây bug**: `gmaps_join.py` từng trỏ input vào đây thay vì output thật của `clean_poi.py` (`dalat_poi_clean_final.csv`, nay là `03_poi_clean.csv`). Giữ lại để tham khảo, không dùng nữa |
| `dalat_route_2days.csv`, `dalat_route_4days.csv` | Kết quả route với `NUM_DAYS` khác 3, không phải output của cấu hình mặc định hiện tại |
| `dalat_route_greedy_4d_3days.csv` | Bản greedy từ một biến thể chạy thử, không khớp tên biến trong `route_optimizer.py` hiện tại |

Nếu một script trong tương lai cần đọc lại các file này, hãy copy ra thư mục gốc
(hoặc trỏ đường dẫn `data/legacy/...`) thay vì di chuyển ngược lại, để giữ thư mục
gốc sạch theo đúng chuẩn đặt tên trong `CSV_NAMING_STANDARD.md`.
