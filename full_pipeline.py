#!/usr/bin/env python3
"""
full_pipeline.py - Chạy pipeline, tự động bỏ qua các bước đã có file output.
Usage: python full_pipeline.py [--force] (--force sẽ chạy lại tất cả)
"""

import subprocess
import sys
import os

SCRIPTS = [
    ("dalat_scraper_fix.py", "dalat_poi_extracted_fix.csv"),
    ("clean_poi.py", "dalat_poi_clean_final.csv"),
    ("gmaps_join.py", "dalat_poi_gmaps_fix.csv"),
    ("scoring.py", "dalat_poi_scored_fix.csv"),
    ("route_optimizer.py", "dalat_route_3days.csv"),
    ("visualize_route.py", "dalat_route_map.html")
]

def run_script(script_name):
    print(f"\n--- Đang chạy {script_name} ---")
    result = subprocess.run([sys.executable, script_name])
    if result.returncode != 0:
        print(f"❌ Lỗi khi chạy {script_name}, dừng pipeline.")
        sys.exit(1)
    print(f"✅ Hoàn thành {script_name}")

def main():
    force = "--force" in sys.argv
    for script, outfile in SCRIPTS:
        if not force and os.path.exists(outfile):
            print(f"⏩ Bỏ qua {script} (đã có {outfile})")
            continue
        run_script(script)

if __name__ == "__main__":
    main()