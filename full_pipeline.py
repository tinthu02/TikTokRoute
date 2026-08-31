#!/usr/bin/env python3
"""
full_pipeline.py - Chạy pipeline, tự động bỏ qua các bước đã có file output.
Usage: python full_pipeline.py [--force] (--force sẽ chạy lại tất cả)

Logging: mỗi lần chạy tạo 1 file log riêng trong logs/pipeline_<timestamp>.log,
đồng thời vẫn in ra console như trước. File log dùng để debug khi pipeline
chạy tự động qua GitHub Actions (console log của Actions bị mất sau một thời
gian) — xem .github/workflows/update_poi.yml, bước upload-artifact sẽ đính
kèm file log này vào mỗi lần chạy workflow.
"""

import subprocess
import sys
import os
import logging
from datetime import datetime

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("full_pipeline")

SCRIPTS = [
    ("dalat_scraper_fix.py", "dalat_poi_extracted_fix.csv"),
    ("clean_poi.py", "dalat_poi_clean_final.csv"),
    ("gmaps_join.py", "dalat_poi_gmaps_fix.csv"),
    ("scoring.py", "dalat_poi_scored_fix.csv"),
    ("route_optimizer.py", "dalat_route_3days.csv"),
    ("visualize_route.py", "dalat_route_map.html")
]

def run_script(script_name):
    """Chạy 1 script con, stream từng dòng stdout/stderr của nó qua logger
    (giữ nguyên output theo thời gian thực như subprocess.run cũ, nhưng có
    thêm timestamp + tên script + được ghi ra file)."""
    logger.info(f"--- Bắt đầu {script_name} ---")
    start = datetime.now()

    process = subprocess.Popen(
        [sys.executable, script_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in process.stdout:
        logger.info(f"[{script_name}] {line.rstrip()}")
    process.wait()

    duration = (datetime.now() - start).total_seconds()

    if process.returncode != 0:
        logger.error(
            f"❌ Lỗi khi chạy {script_name} "
            f"(exit code {process.returncode}, {duration:.1f}s), dừng pipeline."
        )
        sys.exit(1)
    logger.info(f"✅ Hoàn thành {script_name} ({duration:.1f}s)")

def main():
    force = "--force" in sys.argv
    logger.info(f"===== Bắt đầu pipeline (force={force}) | log: {LOG_FILE} =====")
    pipeline_start = datetime.now()

    for script, outfile in SCRIPTS:
        if not force and os.path.exists(outfile):
            logger.info(f"⏩ Bỏ qua {script} (đã có {outfile})")
            continue
        run_script(script)

    total = (datetime.now() - pipeline_start).total_seconds()
    logger.info(f"===== Pipeline hoàn thành trong {total:.1f}s =====")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise  # sys.exit(1) từ run_script đã log đủ, không cần log lại
    except Exception:
        logger.exception("💥 Pipeline dừng do lỗi không lường trước")
        sys.exit(1)