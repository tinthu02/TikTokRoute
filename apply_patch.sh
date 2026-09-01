#!/usr/bin/env bash
# apply_patch.sh — áp patch "tách app.py + gom thuật toán routing dùng chung"
# vào repo TikTokRoute.
#
# Cách dùng: chạy từ THƯ MỤC GỐC repo (nơi có README.md, webapp/, routing/):
#   ./apply_patch.sh /duong/dan/toi/tach-app-routing.patch
# hoặc bỏ mặc định, script sẽ tìm file tach-app-routing.patch cùng thư mục.

set -e

PATCH_FILE="${1:-$(dirname "$0")/tach-app-routing.patch}"

if [ ! -f "$PATCH_FILE" ]; then
  echo "❌ Không tìm thấy file patch: $PATCH_FILE"
  echo "   Dùng: ./apply_patch.sh /duong/dan/toi/tach-app-routing.patch"
  exit 1
fi

if [ ! -d "webapp" ] || [ ! -d "routing" ]; then
  echo "❌ Có vẻ bạn không đứng ở thư mục gốc repo TikTokRoute"
  echo "   (không thấy thư mục webapp/ hoặc routing/ ở đây)."
  exit 1
fi

echo "🔍 Kiểm tra patch có áp được không (chưa sửa gì)..."
if ! git apply --check "$PATCH_FILE" 2>/tmp/apply_check.log; then
  echo "❌ Patch không áp được lên trạng thái hiện tại của repo. Lý do:"
  cat /tmp/apply_check.log
  echo ""
  echo "   Nguyên nhân thường gặp: bạn đã tự sửa webapp/app.py hoặc"
  echo "   routing/route_optimizer.py so với bản patch được tạo ra. Hãy"
  echo "   commit/stash các thay đổi hiện tại rồi thử lại, hoặc áp patch"
  echo "   lên 1 nhánh mới tạo từ đúng commit gốc."
  exit 1
fi

echo "✅ Patch áp được. Đang áp dụng..."
git apply "$PATCH_FILE"

echo ""
echo "🎉 Xong! Các file đã thay đổi/tạo mới:"
echo "   - routing/core.py            (MỚI — thuật toán/tiện ích routing dùng chung)"
echo "   - routing/__init__.py        (MỚI — cho phép 'from routing import core')"
echo "   - routing/route_optimizer.py (SỬA — dùng lại routing/core.py)"
echo "   - webapp/app.py              (SỬA — dùng lại routing/core.py + render_template)"
echo "   - webapp/templates/index.html (MỚI — HTML tách từ app.py)"
echo "   - webapp/static/css/style.css (MỚI — CSS tách từ app.py)"
echo "   - webapp/static/js/app.js     (MỚI — JS tách từ app.js)"
echo ""
echo "👉 Xem lại bằng: git status / git diff --staged"
echo "👉 Chạy thử: cd webapp && python app.py"
echo "   (cần có dalat_poi_scored_fix.csv ở thư mục gốc repo và biến môi"
echo "    trường WEATHER_API_KEY trong .env như trước giờ)."
