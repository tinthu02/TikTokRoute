"""
=============================================================
weather.py - Tích hợp dữ liệu thời tiết OpenWeatherMap
=============================================================
Yêu cầu: cài đặt requests (pip install requests)
.env cần có: WEATHER_API_KEY=your_key
=============================================================
"""

import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")
if not WEATHER_API_KEY:
    raise ValueError("Thiếu WEATHER_API_KEY trong file .env")

# Tọa độ Đà Lạt
DALAT_LAT = 11.9404
DALAT_LON = 108.4583

# URL API OpenWeatherMap (dự báo 5 ngày / 3 giờ)
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

def get_weather_forecast(lat=DALAT_LAT, lon=DALAT_LON, cnt=40):
    """
    Lấy dữ liệu dự báo thời tiết.
    cnt: số lượng khung thời gian (mỗi khung 3 giờ, tối đa 40 ~ 5 ngày)
    Trả về: dict hoặc None nếu lỗi
    """
    params = {
        'lat': lat,
        'lon': lon,
        'appid': WEATHER_API_KEY,
        'units': 'metric',    # độ C
        'cnt': cnt
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Lỗi khi gọi OpenWeatherMap: {e}")
        return None

def is_rainy(forecast, day_index):
    """
    Kiểm tra ngày thứ `day_index` (0 = hôm nay, 1 = ngày mai, ...) có mưa không.
    Dựa trên sự tồn tại của key 'rain' trong bất kỳ khung giờ nào của ngày đó.
    """
    if not forecast:
        return False
    # Mỗi ngày có 8 khung (3h x 8 = 24h)
    start = day_index * 8
    end = start + 8
    for item in forecast['list'][start:end]:
        if 'rain' in item or 'snow' in item:
            return True
    return False

def get_rainy_days(num_days=3):
    """
    Trả về danh sách boolean cho num_days ngày tới (True nếu có mưa).
    """
    forecast = get_weather_forecast(cnt=num_days*8)
    if not forecast:
        return [False] * num_days
    return [is_rainy(forecast, d) for d in range(num_days)]

# Ví dụ sử dụng (test)
if __name__ == "__main__":
    rainy_days = get_rainy_days(3)
    print("Dự báo 3 ngày tới có mưa:", rainy_days)