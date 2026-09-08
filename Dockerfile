FROM python:3.11-slim

WORKDIR /app

# Tesseract OCR engine (only needed by screen_ocr_monitor.py)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "telethon_collector.py"]
