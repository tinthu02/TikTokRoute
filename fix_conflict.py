with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix conflict 1156-1173: giu flyToBounds cua HEAD + giu weather banner cua main
old = """<<<<<<< HEAD
  if (bounds.length) map.flyToBounds(bounds, {padding:[40,40], duration:1.5});
=======
  if (bounds.length) map.fitBounds(bounds, {padding:[40,40]});

  // Giữ lại marker điểm xuất phát nếu có
  if (startMarker) startMarker.addTo(map);

  const weatherBanner = document.getElementById('weather-banner');
  if (data.weather && data.weather.outdoor_removed) {
    const rainyCount = data.weather.rainy_days.filter(Boolean).length;
    const total = data.weather.rainy_days.length;
    weatherBanner.innerHTML = '🌧 Dự báo mưa ' + rainyCount + '/' + total + ' ngày — đã ẩn địa điểm ngoài trời (núi, thác, check-in). Bỏ tick sở thích Mạo hiểm để xem thêm.';
    weatherBanner.style.display = 'block';
  } else {
    weatherBanner.style.display = 'none';
  }
>>>>>>> origin/main"""

new = """  if (bounds.length) map.flyToBounds(bounds, {padding:[40,40], duration:1.5});

  // Giữ lại marker điểm xuất phát nếu có
  if (startMarker) startMarker.addTo(map);

  const weatherBanner = document.getElementById('weather-banner');
  if (data.weather && data.weather.outdoor_removed) {
    const rainyCount = data.weather.rainy_days.filter(Boolean).length;
    const total = data.weather.rainy_days.length;
    weatherBanner.innerHTML = '🌧 Dự báo mưa ' + rainyCount + '/' + total + ' ngày — đã ẩn địa điểm ngoài trời (núi, thác, check-in). Bỏ tick sở thích Mạo hiểm để xem thêm.';
    weatherBanner.style.display = 'block';
  } else {
    weatherBanner.style.display = 'none';
  }"""

if old in content:
    content = content.replace(old, new)
    print('Fixed!')
else:
    print('NOT FOUND')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
