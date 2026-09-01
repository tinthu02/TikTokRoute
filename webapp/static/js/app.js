// USER_TOKEN được set bởi 1 đoạn <script> nhỏ inline trong templates/index.html
// (file .js tĩnh này không được Flask/Jinja xử lý nên không thể chèn
// {{ user_token }} trực tiếp vào đây).
const USER_TOKEN = window.USER_TOKEN;
const map = L.map('map', { zoomControl: false }).setView([11.9404, 108.4583], 13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { attribution: '© CartoDB', maxZoom: 19 }).addTo(map);
L.control.zoom({ position: 'bottomright' }).addTo(map);

let allLayers = [];
let selectedTypes = new Set();
let anchorPOIs = [];
let startLocation = null;
let pickingStartLocation = false;
let startMarker = null;
let selectedRating = 0;
let currentRouteId = null;

function toggleForm() {
  const formSection = document.getElementById('form-section');
  const btn = document.getElementById('btn-reopen-form');
  if (formSection.classList.contains('collapsed')) {
    formSection.classList.remove('collapsed');
    if (btn) btn.textContent = '⚙ Thu gọn';
  } else {
    formSection.classList.add('collapsed');
    if (btn) btn.textContent = '⚙ Cài đặt';
  }
}

function detectLocation() {
  if (!navigator.geolocation) { alert('Trình duyệt không hỗ trợ định vị.'); return; }
  const badge = document.getElementById('sl-badge');
  badge.textContent = '📡 Đang xác định vị trí...';
  navigator.geolocation.getCurrentPosition(
    pos => setStartLocation(pos.coords.latitude, pos.coords.longitude, '📡 Vị trí của tôi'),
    err => { badge.textContent = '🏔 Mặc định: Trung tâm Đà Lạt'; alert('Không lấy được vị trí: ' + err.message); },
    { timeout: 8000 }
  );
}

function pickOnMap() {
  pickingStartLocation = true;
  document.getElementById('sl-badge').textContent = '🖱 Bấm vào bản đồ để chọn điểm xuất phát...';
  map.getContainer().style.cursor = 'crosshair';
}

function setStartLocation(lat, lng, label) {
  startLocation = { lat, lng };
  document.getElementById('sl-badge').innerHTML = '📍 ' + label + ' (' + lat.toFixed(4) + ', ' + lng.toFixed(4) + ')';
  document.getElementById('sl-clear').style.display = 'block';
  if (startMarker) map.removeLayer(startMarker);
  const icon = L.divIcon({ className: '', html: '<div style="background:#d4b87a;color:#0a0c12;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-size:14px;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.5);">🏁</div>', iconSize:[28,28], iconAnchor:[14,14] });
  startMarker = L.marker([lat, lng], {icon}).bindPopup('📍 Điểm xuất phát').addTo(map);
  map.setView([lat, lng], 15);
}

function clearStartLocation() {
  startLocation = null;
  document.getElementById('sl-badge').textContent = '🏔 Mặc định: Trung tâm Đà Lạt';
  document.getElementById('sl-clear').style.display = 'none';
  if (startMarker) { map.removeLayer(startMarker); startMarker = null; }
}

map.on('click', function(e) {
  if (!pickingStartLocation) return;
  pickingStartLocation = false;
  map.getContainer().style.cursor = '';
  setStartLocation(e.latlng.lat, e.latlng.lng, 'Điểm chọn trên bản đồ');
});

fetch('/api/poi_types').then(r=>r.json()).then(types => {
  const container = document.getElementById('type-filters');
  types.forEach(t => {
    const pill = document.createElement('div');
    pill.className = 'type-pill';
    pill.dataset.type = t.value;
    pill.textContent = t.emoji + ' ' + t.label;
    pill.onclick = () => toggleType(t.value, pill);
    container.appendChild(pill);
  });
});

function toggleType(type, pill) {
  if (type === 'all') {
    selectedTypes.clear();
    document.querySelectorAll('.type-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    return;
  }
  document.querySelector('[data-type=all]').classList.remove('active');
  if (selectedTypes.has(type)) {
    selectedTypes.delete(type);
    pill.classList.remove('active');
    if (selectedTypes.size === 0) document.querySelector('[data-type=all]').classList.add('active');
  } else {
    selectedTypes.add(type);
    pill.classList.add('active');
  }
}

let anchorDebounce;
async function searchAnchor(q) {
  clearTimeout(anchorDebounce);
  const drop = document.getElementById('anchor-drop');
  if (!q.trim()) { drop.style.display='none'; return; }
  anchorDebounce = setTimeout(async () => {
    const res = await fetch('/api/search_poi?q='+encodeURIComponent(q));
    const items = await res.json();
    if (!items.length) { drop.style.display='none'; return; }
    drop.innerHTML = items.map(it => `<div class="anchor-drop-item" onclick="addAnchor('${esc(it.name)}','${it.emoji}','${esc(it.type)}')"><span>${it.emoji}</span><span style="flex:1">${it.name}</span><span style="color:var(--text-muted);font-size:11px">⭐${it.rating}</span></div>`).join('');
    drop.style.display='block';
  }, 220);
}
function addAnchor(name, emoji, type_vi) {
  if (anchorPOIs.find(a=>a.name===name)) { closeAnchorDrop(); return; }
  anchorPOIs.push({name, emoji, type_vi});
  renderAnchorTags();
  document.getElementById('anchor-input').value = '';
  closeAnchorDrop();
}
function removeAnchor(name) { anchorPOIs = anchorPOIs.filter(a=>a.name!==name); renderAnchorTags(); }
function renderAnchorTags() {
  const container = document.getElementById('anchor-tags');
  container.innerHTML = anchorPOIs.map(a => `<div class="anchor-tag">${a.emoji} ${a.name}<button onclick="removeAnchor('${esc(a.name)}')">×</button></div>`).join('');
}
function closeAnchorDrop() { document.getElementById('anchor-drop').style.display='none'; }
document.addEventListener('click', e => { if (!e.target.closest('#anchor-section')) closeAnchorDrop(); });
document.getElementById('anchor-input').addEventListener('input', e => searchAnchor(e.target.value));
document.getElementById('anchor-input').addEventListener('keydown', e => { if(e.key==='Escape') closeAnchorDrop(); });

async function optimize() {
  const btn = document.getElementById('btn-optimize');
  btn.disabled = true;
  document.getElementById('loading').classList.add('show');
  const preferences = {
    adventure: document.getElementById('pref_adventure').checked,
    relax: document.getElementById('pref_relax').checked,
    food: document.getElementById('pref_food').checked,
    checkin: document.getElementById('pref_checkin').checked,
  };
  const payload = {
    num_days: parseInt(document.getElementById('num_days').value),
    top_k: parseInt(document.getElementById('top_k').value),
    start_hour: parseInt(document.getElementById('start_hour').value),
    end_hour: parseInt(document.getElementById('end_hour').value),
    types: [...selectedTypes],
    anchor_pois: anchorPOIs.map(a=>a.name),
    preferences: preferences,
    start_location: startLocation,
  };
  try {
    const res = await fetch('/api/optimize', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
    const data = await res.json();
    renderResult(data);
    const formSection = document.getElementById('form-section');
    if (!formSection.classList.contains('collapsed')) {
      formSection.classList.add('collapsed');
    }
    // Hiện nút mở lại form
    document.getElementById('btn-reopen-form').style.display = 'inline-flex';
    document.getElementById('feedback-container').style.display = 'block';
  } catch(e) { alert('Lỗi: '+e.message); }
  finally { btn.disabled=false; document.getElementById('loading').classList.remove('show'); }
}

function renderResult(data) {
  currentRouteId = data.route_id || null;
  allLayers.forEach(l => map.removeLayer(l));
  allLayers = [];
  const s = data.summary;
  document.getElementById('s-km').innerHTML = s.total_km;
  document.getElementById('s-rate').innerHTML = s.rate;
  document.getElementById('s-stops').innerHTML = s.feasible + '/' + s.total_stops;
  document.getElementById('summary-bar').style.display = 'flex';

  let html = '';
  data.days.forEach(day => {
    const travelMins = Math.round(day.km / 30 * 60);
    html += `<div class="day-section">
      <div class="day-header" onclick="toggleDay(this)">
        <div class="day-dot" style="background:${day.color}"></div>
        <h3>Ngày ${day.day}</h3>
        <div class="day-stats">
          <span>🛣️ ${day.km} km</span>
          <span>🕒 ${travelMins} phút</span>
          <span>✅ ${day.feasible}/${day.total}</span>
        </div>
        <span style="font-size:12px;">▼</span>
      </div>
      <div class="day-stops" style="display:block;">`;
    day.stops.forEach(stop => {
      const cls = stop.feasible ? '' : ' infeasible';
      html += `<div class="stop-item${cls}" onclick="focusStop(${stop.lat},${stop.lng},'${esc(stop.name)}')">
        <div class="stop-num" style="background:${day.color}">${stop.idx}</div>
        <div class="stop-body">
          <div class="stop-name">${stop.emoji} ${stop.name} ${stop.anchor ? '<span class="badge-anchor">📌 bắt buộc</span>' : ''}</div>
          <div class="stop-meta">
            <span class="stop-time">🕐 ${stop.start} – ${stop.end}</span>
            <span>${stop.type_vi}</span>
            ${stop.rating ? `<span>⭐ ${stop.rating}</span>` : ''}
            ${stop.visit_min ? `<span>⏱ ${stop.visit_min} phút</span>` : ''}
            ${!stop.feasible ? '<span class="badge-infeasible">⚠️ ngoài giờ</span>' : ''}
          </div>
        </div>
      </div>`;
    });
    html += `</div></div>`;
  });
  document.getElementById('timeline').innerHTML = html;

  const bounds = [];
  data.days.forEach(day => {
    const coords = [];
    day.stops.forEach(stop => {
      const latlng = [stop.lat, stop.lng];
      coords.push(latlng);
      bounds.push(latlng);
      const icon = L.divIcon({ className: '', html: `<div style="background:${day.color};color:white;border-radius:50%;width:32px;height:32px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.4);opacity:${stop.feasible?1:0.45};">${stop.idx}</div>`, iconSize:[32,32], iconAnchor:[16,16] });
      const popupHtml = `<div class="map-popup"><b>${stop.name}</b><br><div class="meta">${stop.emoji} ${stop.type_vi} | Ngày ${day.day} #${stop.idx}</div>🕐 ${stop.start}–${stop.end} ${stop.feasible?'✅':'⚠️'}<br>⭐ <span class="rating-stars">${'★'.repeat(Math.round(stop.rating))}${'☆'.repeat(5-Math.round(stop.rating))}</span><br>${stop.address?`📍 ${stop.address.substring(0,60)}<br>`:''}${stop.video_url?`<a href="${stop.video_url}" target="_blank">🎬 TikTok</a>`:''}</div>`;
      const marker = L.marker(latlng, {icon}).bindPopup(popupHtml, {maxWidth:280}).addTo(map);
      allLayers.push(marker);
    });
    if (coords.length >= 2) {
      // Lấy polyline thực tế từ OSRM
      fetch("/api/route_polyline", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ coords: coords.map(c => ({ lat: c[0], lng: c[1] })) })
      })
      .then(r => r.json())
      .then(data => {
        const points = data.polyline && data.polyline.length > 0 ? data.polyline : coords;
        const line = L.polyline(points, { color: day.color, weight: 4, opacity: 0.8, dashArray: '8 6', lineCap: 'round', lineJoin: 'round' }).addTo(map);
        allLayers.push(line);
      });
    }
  });
  if (bounds.length) map.fitBounds(bounds, {padding:[40,40]});
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

  // Cập nhật trọng số nếu server trả về
  if (data.weights) {
    renderWeightPanel(data.weights);
    showWeightToast(data.weights);
  }
}

function toggleDay(header) {
  const stopsDiv = header.nextElementSibling;
  if (stopsDiv.style.display === 'none') stopsDiv.style.display = 'block';
  else stopsDiv.style.display = 'none';
}
function focusStop(lat, lng, name) {
  map.setView([lat, lng], 16);
  allLayers.forEach(l => { if (l.getLatLng && Math.abs(l.getLatLng().lat-lat)<0.0001) l.openPopup(); });
}
function esc(s) { return (s||'').replace(/'/g,"\\'"); }

// Khởi tạo sự kiện cho các sao
function initStars() {
  const stars = document.querySelectorAll('.star');
  stars.forEach(star => {
    star.addEventListener('click', () => {
      const value = parseInt(star.dataset.value);
      selectedRating = value;
      stars.forEach((s, idx) => {
        if (idx < value) s.classList.add('selected');
        else s.classList.remove('selected');
      });
    });
  });
}
initStars();

async function submitFeedback() {
  if (selectedRating === 0) {
    alert('Vui lòng chọn số sao đánh giá!');
    return;
  }
  const feedback = document.getElementById("feedbackText").value;
  const response = await fetch("/submit-feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rating: selectedRating, feedback: feedback, route_id: currentRouteId })
  });
  const result = await response.json();
  document.getElementById("feedbackMessage").innerText = result.message;
  dismissFeedback();
  selectedRating = 0;
  document.getElementById("feedbackText").value = '';
  document.querySelectorAll('.star').forEach(s => s.classList.remove('selected'));
  document.getElementById("thankPopup").style.display = "flex";
}
function dismissFeedback() {
  const fc = document.getElementById('feedback-container');
  fc.style.transition = 'opacity 0.25s, transform 0.25s';
  fc.style.opacity = '0';
  fc.style.transform = 'translateY(10px)';
  setTimeout(() => { fc.style.display = 'none'; fc.style.opacity = ''; fc.style.transform = ''; }, 260);
}
function closePopup() { document.getElementById("thankPopup").style.display = "none"; }

// ── Drag & drop cho feedback card ──────────────────────────────
(function initFeedbackDrag() {
  const fc = document.getElementById('feedback-container');
  const handle = document.getElementById('feedback-drag-handle');
  let dragging = false, ox = 0, oy = 0;

  handle.addEventListener('mousedown', e => {
    dragging = true;
    // Nếu đang dùng bottom/right, chuyển sang top/left tuyệt đối
    if (fc.style.top === '') {
      const rect = fc.getBoundingClientRect();
      fc.style.top  = rect.top  + 'px';
      fc.style.left = rect.left + 'px';
      fc.style.bottom = 'unset';
      fc.style.right  = 'unset';
    }
    ox = e.clientX - fc.getBoundingClientRect().left;
    oy = e.clientY - fc.getBoundingClientRect().top;
    fc.style.transition = 'none';
    fc.style.userSelect = 'none';
    e.preventDefault();
  });

  // Touch support
  handle.addEventListener('touchstart', e => {
    const t = e.touches[0];
    dragging = true;
    if (fc.style.top === '') {
      const rect = fc.getBoundingClientRect();
      fc.style.top  = rect.top  + 'px';
      fc.style.left = rect.left + 'px';
      fc.style.bottom = 'unset';
      fc.style.right  = 'unset';
    }
    ox = t.clientX - fc.getBoundingClientRect().left;
    oy = t.clientY - fc.getBoundingClientRect().top;
    fc.style.transition = 'none';
    e.preventDefault();
  }, { passive: false });

  function onMove(cx, cy) {
    if (!dragging) return;
    let nx = cx - ox;
    let ny = cy - oy;
    // Giữ trong viewport
    nx = Math.max(0, Math.min(window.innerWidth  - fc.offsetWidth,  nx));
    ny = Math.max(0, Math.min(window.innerHeight - fc.offsetHeight, ny));
    fc.style.left = nx + 'px';
    fc.style.top  = ny + 'px';
  }

  document.addEventListener('mousemove', e => onMove(e.clientX, e.clientY));
  document.addEventListener('touchmove', e => { if(dragging) onMove(e.touches[0].clientX, e.touches[0].clientY); }, { passive: false });

  function stopDrag() {
    dragging = false;
    fc.style.userSelect = '';
  }
  document.addEventListener('mouseup',  stopDrag);
  document.addEventListener('touchend', stopDrag);
})();

// ── Trọng số cá nhân ──────────────────────────────────────────
// Chuyển weight (0.5-2.0) → % thanh (0-100%)
function weightToPct(w) { return Math.round(((w - 0.5) / 1.5) * 100); }

let _prevWeights = null;
let _toastTimer = null;

function renderWeightPanel(w) {
  const panel = document.getElementById('weights-panel');
  panel.style.display = 'block';
  const cats = ['cafe','nature','food','checkin'];
  cats.forEach(c => {
    const pct = weightToPct(w[c]);
    document.getElementById(`wb-${c}`).style.width = pct + '%';
    document.getElementById(`wv-${c}`).textContent = w[c].toFixed(2);
  });
}

function showWeightToast(w) {
  const cats = ['cafe','nature','food','checkin'];
  cats.forEach(c => {
    const pct = weightToPct(w[c]);
    document.getElementById(`twb-${c}`).style.width = pct + '%';
    document.getElementById(`twv-${c}`).textContent = w[c].toFixed(2);
    let arrow = '';
    if (_prevWeights) {
      if (w[c] > _prevWeights[c]) arrow = '<span style="color:#4ade80">↑</span>';
      else if (w[c] < _prevWeights[c]) arrow = '<span style="color:#f87171">↓</span>';
    }
    document.getElementById(`twa-${c}`).innerHTML = arrow;
  });
  _prevWeights = {...w};

  const toast = document.getElementById('weight-toast');
  toast.classList.add('show');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('show'), 4000);
}

async function loadInitialWeights() {
  try {
    const res = await fetch('/api/user_weights');
    const w = await res.json();
    _prevWeights = {...w};
    renderWeightPanel(w);
  } catch(e) { console.warn('Không tải được trọng số:', e); }
}

async function resetWeights() {
  try {
    const res = await fetch('/api/user_weights/reset', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      renderWeightPanel(data.weights);
      showWeightToast(data.weights);
    }
  } catch(e) { console.warn('Reset thất bại:', e); }
}

// Tải weights khi khởi động
loadInitialWeights();
