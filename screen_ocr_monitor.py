"""Optional fallback ingestion source: periodically screenshots a screen
region (e.g. your browser window showing hood.vantis.sh) and OCRs it for
wallet addresses. Only runs when OCR_ENABLED=true in .env.
"""
import json
import os
import time
from datetime import datetime, timezone

import cv2
import mss
import numpy as np
import pytesseract
from PIL import Image

import config
from google_sheets import append_row, append_rows, get_existing_addresses
from regex_utils import ADDRESS_RE

if config.TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


def preprocess_image(img):
    """Pre-process image for better OCR accuracy."""
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=50)
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    return Image.fromarray(gray)


def capture_and_ocr():
    """Capture the configured screen region and run OCR on it."""
    with mss.mss() as sct:
        screenshot = sct.grab(config.OCR_MONITOR_REGION)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

    text = pytesseract.image_to_string(preprocess_image(img), lang="eng", config="--psm 6")
    addresses = sorted(set(ADDRESS_RE.findall(text)))

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    screenshot_path = os.path.join(SCREENSHOT_DIR, f"ocr_{timestamp.strftime('%Y%m%d_%H%M%S')}.png")
    img.save(screenshot_path)

    append_row(
        config.GOOGLE_SHEET_ID,
        "ocr_results",
        [timestamp.isoformat(), screenshot_path, text[:5000], json.dumps(addresses), 1.0],
    )

    if addresses:
        existing = get_existing_addresses(config.GOOGLE_SHEET_ID, "candidates")
        new_addresses = [a for a in addresses if a not in existing]
        if new_addresses:
            now = timestamp.isoformat()
            append_rows(
                config.GOOGLE_SHEET_ID,
                "candidates",
                [[a, now, "screen_ocr", screenshot_path, "candidate", now] for a in new_addresses],
            )

    print(f"OCR complete: found {len(addresses)} addresses")
    return addresses


if __name__ == "__main__":
    if not config.OCR_ENABLED:
        print("Screen OCR is disabled. Set OCR_ENABLED=true in .env to enable.")
        raise SystemExit(0)

    print(f"Starting screen OCR monitor (interval: {config.OCR_CAPTURE_INTERVAL}s)...")

    while True:
        try:
            capture_and_ocr()
        except Exception as e:
            print(f"OCR error: {e}")

        time.sleep(config.OCR_CAPTURE_INTERVAL)
