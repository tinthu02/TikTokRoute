"""
=============================================================
GIAI ĐOẠN 2 — Clean & Dedup POI Đà Lạt (Hoàn chỉnh)
=============================================================
Input:  dalat_poi_extracted_fix.csv  (output của giai đoạn 1)
Output: dalat_poi_clean_final.csv    (đã lọc, gộp tên, viết hoa)

Yêu cầu: pip install requests
Ollama phải đang chạy với model qwen2.5:7b
=============================================================
"""

import csv, json, time, requests, re
from collections import defaultdict

# ══════════════════════════════════════════════════════════════
# CẤU HÌNH
# ══════════════════════════════════════════════════════════════

INPUT_CSV  = "dalat_poi_extracted_fix.csv"
OUTPUT_CSV = "dalat_poi_clean_final.csv"

OLLAMA_MODEL   = "qwen2.5:7b"
OLLAMA_URL     = "http://localhost:11434/api/chat"
OLLAMA_TIMEOUT = 180

# Lọc bỏ tên quá chung chung (không phải địa điểm cụ thể)
BLACKLIST = {
    "homestay", "cafe", "quán ăn", "nhà hàng", "khách sạn",
    "đà lạt", "da lat", "dalat", "địa điểm", "quán cafe",
    "coffee", "hotel", "resort", "villa", "bungalow",
    "phòng", "tour", "xe", "chợ", "view", "check-in", "checkin",
    "sương mù", "quán cafe mới toanh", "cafe ngắm hoàng hôn",
    "quán ăn vặt ngon", "quán cơm gia đình", "nhà hàng lẩu nướng",
    "cafe mới ở đà lạt", "cafe ngắm hoàng hôn", "cơm lam gà nướng",
    "suối bình yên", "giữa lòng thác", "nhà đẹp dalat",
    "cắm trại", "camping", "lều trại", "lều",
    "thác nước", "thác", "suối", "đồi", "núi", "rừng", "thung lũng",
    "ẩm thực", "đặc sản", "món ngon", "food tour", "foodtour",
    "local food", "local", "quán local",
    # Các tên chung chung sau dedup
    "nướng", "cà phê", "cơm trưa", "chợ đêm", "tiệm nướng", "quán nướng",
    "cafe", "cà phê", "cơm", "chợ",
}

# Gộp tay các tên bị viết sai hoặc cần chuẩn hoá
MANUAL_MERGE = {
    # Săn mây – gộp thành một POI cafe duy nhất
    "san may":       "Cà phê săn mây Đà Lạt",
    "san may dalat": "Cà phê săn mây Đà Lạt",
    "cafe may":      "Cà phê săn mây Đà Lạt",
    "cafe mây":      "Cà phê săn mây Đà Lạt",
    "san mai dalat": "Cà phê săn mây Đà Lạt",
    "săn mây":       "Cà phê săn mây Đà Lạt",
    "cà phê săn mây":"Cà phê săn mây Đà Lạt",
    "san may cau dalat": "Cà phê săn mây Đà Lạt",
    
    # Các merge khác
    "thác dambri":      "Thác Dambri",
    "thác liên khương": "Thác Liên Khương",
    "local food":       "Bánh Tráng Nướng Đà Lạt",
    "tiệm nướng local": "Bánh Tráng Nướng Đà Lạt",
    "quán nướng đà lạt":"Bánh Tráng Nướng Đà Lạt",
    "bánh tráng nướng ngon nhất đà lạt": "Bánh Tráng Nướng Đà Lạt",
    "cà tám đà lạt": "Cà Tám Đà Lạt",
    "datanla":        "Thác Datanla",
    "thác da tanla":  "Thác Datanla",
    "da tanla":       "Thác Datanla",
}

# Regex cho tên quá chung chung
GENERIC_NAME_PATTERNS = [
    r"^tiệm cafe$",
    r"^quán cafe$",
    r"^quán cafe \w+ tại đà lạt$",
    r"^cafe \w+ tại đà lạt$",
    r"^tiệm cà phê$",
    r"^quán cà phê$",
    r"^nhà hàng \w{1,5}$",
    r"^cafe ngắm hoàng hôn$",
    r"^quán cafe mới toanh$",
    r"^cơm lam gà nướng.*$",
    r"^nướng$",
    r"^cà phê$",
    r"^cơm trưa$",
    r"^chợ đêm$",
    r"^tiệm nướng$",
    r"^quán nướng$",
]

MIN_NAME_LENGTH = 4

# ══════════════════════════════════════════════════════════════
# BƯỚC 1 — ĐỌC & LỌC THÔ
# ══════════════════════════════════════════════════════════════

def load_csv(filepath):
    with open(filepath, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def is_blacklisted(name):
    name_lower = name.lower().strip()
    if len(name_lower) < MIN_NAME_LENGTH:
        return True
    if name_lower in BLACKLIST:
        return True
    words = set(re.split(r"[\s\-_]+", name_lower))
    if words.issubset(BLACKLIST):
        return True
    for pattern in GENERIC_NAME_PATTERNS:
        if re.fullmatch(pattern, name_lower):
            return True
    return False

def filter_raw(pois):
    kept, removed = [], []
    for p in pois:
        if is_blacklisted(p["place_name"]):
            removed.append(p["place_name"])
        else:
            kept.append(p)
    print(f"  Lọc bỏ {len(removed)} tên chung chung:")
    for name in removed[:10]:
        print(f"    - {name}")
    if len(removed) > 10:
        print(f"    ... và {len(removed)-10} tên khác")
    return kept

# ══════════════════════════════════════════════════════════════
# BƯỚC 2 — DEDUP BẰNG OLLAMA
# ══════════════════════════════════════════════════════════════

DEDUP_SYSTEM = (
    "Bạn là chuyên gia địa lý Đà Lạt. Nhiệm vụ: gộp nhóm các tên địa điểm giống nhau. "
    "Quy tắc: "
    "'San May' = 'Cafe May' = 'San May Đà Lạt' = 'Quán San May' -> gộp thành 1 nhóm. "
    "Chỉ gộp nếu CHẮC CHẮN là cùng địa điểm. Nếu không chắc, để riêng. "
    "Chọn tên chuẩn: tên đầy đủ nhất, viết hoa đúng, không có 'đà lạt' ở cuối. "
    "Chỉ trả về JSON, không markdown, không text thêm. "
    "Format: {\"groups\": [{\"canonical\": \"Tên chuẩn\", \"members\": [\"ten1\", \"ten2\"]}]}"
)

def apply_manual_merge(pois):
    for p in pois:
        key = p["place_name"].lower().strip()
        if key in MANUAL_MERGE:
            print(f"  Manual merge: '{p['place_name']}' -> '{MANUAL_MERGE[key]}'")
            p["place_name"] = MANUAL_MERGE[key]
    return pois

def call_ollama_dedup(names_chunk):
    names_text = "\n".join(f"- {n}" for n in names_chunk)
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": DEDUP_SYSTEM},
            {"role": "user", "content": f"Danh sách tên địa điểm:\n{names_text}\n\nHãy gộp nhóm các tên giống nhau:"},
        ],
        "stream": False,
        "options": {"temperature": 0, "num_predict": 1000},
    }
    r = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    raw = r.json()["message"]["content"].strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return {}
    data = json.loads(raw[start:end])
    mapping = {}
    for group in data.get("groups", []):
        canonical = group["canonical"]
        for member in group.get("members", []):
            mapping[member.lower().strip()] = canonical
    return mapping

def build_dedup_map(pois, chunk_size=30):
    all_names = [p["place_name"] for p in pois]
    mapping = {}
    print(f"\n  Dedup {len(all_names)} tên theo batch {chunk_size}...")
    for i in range(0, len(all_names), chunk_size):
        chunk = all_names[i:i+chunk_size]
        print(f"  Batch {i//chunk_size + 1}/{(len(all_names)-1)//chunk_size + 1} ({len(chunk)} tên)...")
        try:
            m = call_ollama_dedup(chunk)
            mapping.update(m)
        except Exception as e:
            print(f"    Lỗi batch: {str(e)[:80]} — giữ nguyên")
        time.sleep(1)
    return mapping

def apply_dedup(pois, mapping):
    """Gộp các POI có cùng tên chuẩn, cộng dồn metrics, giữ lại video_url tốt nhất và video_caption."""
    merged = {}
    for p in pois:
        original_name = p["place_name"]
        canonical = mapping.get(original_name.lower().strip(), original_name)
        key = canonical.lower().strip()

        if key not in merged:
            merged[key] = {
                "place_name":      canonical,
                "type":            p["type"],
                "mention_count":   0,
                "total_digg":      0,
                "total_plays":     0,
                "max_followers":   0,
                "price_mentions":  set(),
                "video_urls":      "",
                "video_caption":   "",
                "confidence_high": 0,
                "popularity_score": 0.0,
                "aliases":         set(),
            }

        e = merged[key]
        e["mention_count"]   += int(p.get("mention_count", 1) or 1)
        e["total_digg"]      += int(p.get("total_digg", 0) or 0)
        e["total_plays"]     += int(p.get("total_plays", 0) or 0)
        e["max_followers"]    = max(e["max_followers"], int(p.get("max_followers", 0) or 0))
        e["confidence_high"] += int(p.get("confidence_high", 0) or 0)

        if p.get("price_mentions"):
            e["price_mentions"].add(p["price_mentions"])

        p_url = p.get("video_urls", "")
        if p_url and not e["video_urls"]:
            e["video_urls"] = p_url

        p_cap = p.get("video_caption", "")
        if p_cap:
            if canonical.lower() in p_cap.lower():
                e["video_caption"] = p_cap
            elif not e["video_caption"]:
                e["video_caption"] = p_cap

        if original_name.lower() != canonical.lower():
            e["aliases"].add(original_name)

    result = []
    for e in merged.values():
        e["popularity_score"] = round(
            e["mention_count"] * 10 + e["total_digg"] * 0.001 + e["total_plays"] * 0.0001, 2
        )
        e["price_mentions"] = " | ".join(e["price_mentions"])
        e["aliases"]        = " | ".join(e["aliases"])
        if not e["video_urls"]:
            e["video_urls"] = ""
        result.append(e)

    return sorted(result, key=lambda x: x["popularity_score"], reverse=True)

# ══════════════════════════════════════════════════════════════
# BƯỚC 3 — LƯU & VIẾT HOA TÊN
# ══════════════════════════════════════════════════════════════

def capitalize_place_name(name):
    """Viết hoa chữ cái đầu mỗi từ, nhưng giữ nguyên các từ viết tắt hoặc từ đặc biệt nếu cần."""
    words = name.split()
    capitalized = []
    for w in words:
        if w.upper() == w:  # giữ nguyên nếu đang là viết tắt (VD: "OK", "VIP")
            capitalized.append(w)
        else:
            capitalized.append(w.capitalize())
    return " ".join(capitalized)

def save_csv(data, filepath):
    if not data:
        print("Không có dữ liệu!")
        return
    # Viết hoa tên place_name trước khi lưu
    for row in data:
        row["place_name"] = capitalize_place_name(row["place_name"])
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        writer.writerows(data)
    print(f"Đã lưu {len(data)} dòng -> {filepath}")

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    print("\n" + "="*55)
    print("  CLEAN & DEDUP POI Đà Lạt (Hoàn chỉnh)")
    print("="*55)

    print(f"\nDoc file: {INPUT_CSV}")
    pois = load_csv(INPUT_CSV)
    print(f"  {len(pois)} POI đầu vào")

    print("\nBước 1: Lọc tên chung chung...")
    pois = filter_raw(pois)
    print("\nBước 1b: Manual merge...")
    pois = apply_manual_merge(pois)
    print(f"  Còn lại: {len(pois)} POI")

    print("\nBước 2: Dedup bằng Ollama...")
    mapping = build_dedup_map(pois, chunk_size=30)
    print(f"  Tìm thấy {len(mapping)} tên cần gộp")

    # Lọc lại sau khi merge (phòng trường hợp merge tạo ra tên chung chung)
    print("\nBước 2b: Lọc lại sau dedup...")
    pois_final = [p for p in pois if not is_blacklisted(p["place_name"])]
    print(f"  Lọc bỏ thêm {len(pois) - len(pois_final)} tên")
    pois = pois_final

    merged_groups = defaultdict(list)
    for orig, canonical in mapping.items():
        if orig != canonical.lower():
            merged_groups[canonical].append(orig)
    if merged_groups:
        print("\n  Các nhóm được gộp:")
        for canonical, aliases in list(merged_groups.items())[:10]:
            print(f"    '{canonical}' <- {aliases}")

    pois = apply_dedup(pois, mapping)
    print(f"\n  Sau dedup: {len(pois)} POI unique")

    print("\nBước 3: Lưu kết quả...")
    save_csv(pois, OUTPUT_CSV)

    elapsed = round(time.time() - t0, 1)
    print("\n" + "="*55)
    print(f"  HOÀN THÀNH sau {elapsed}s")
    print(f"  POI ban đầu:  {len(load_csv(INPUT_CSV))}")
    print(f"  POI sau clean: {len(pois)}")
    print(f"\n  Top 10 địa điểm hot nhất:")
    for i, p in enumerate(pois[:10], 1):
        alias_str = f" (= {p['aliases']})" if p["aliases"] else ""
        print(f"    {i:2}. {p['place_name']}{alias_str}")
        print(f"        type={p['type']} | mentions={p['mention_count']} | score={p['popularity_score']}")
    print("="*55)

if __name__ == "__main__":
    main()