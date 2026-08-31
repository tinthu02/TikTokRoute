## Cấu hình môi trường

Project này sử dụng một số API key (Apify, Google Maps, OpenWeatherMap).
Để chạy được, bạn cần tạo file `.env` dựa trên mẫu dưới đây:

```
APIFY_TOKEN=your_apify_token_here
GMAPS_API_KEY=your_google_maps_api_key_here
WEATHER_API_KEY=your_openweathermap_api_key_here
```

### Các bước thực hiện
1. Tạo file `.env`.
2. Điền API key của riêng bạn vào các biến tương ứng.
3. Lưu file và chạy project.

> Lưu ý: File `.env` đã được thêm vào `.gitignore` nên sẽ không được commit lên repository.

### Ghi chú
- `APIFY_TOKEN`: dùng cho `dalat_scraper_fix.py` (thu thập video TikTok qua Apify).
- `GMAPS_API_KEY`: dùng cho `gmaps_join.py` (đối sánh POI với Google Maps, cần bật Places API).
- `WEATHER_API_KEY`: dùng cho `weather.py` (lấy dự báo thời tiết từ OpenWeatherMap).
