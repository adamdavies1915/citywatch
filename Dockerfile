FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY citywatch ./citywatch
COPY config ./config
ENV PYTHONUNBUFFERED=1 CITYWATCH_STATE=/app/var CITYWATCH_INTERVAL_SECONDS=3600 CITYWATCH_SEND_EMAIL=false
VOLUME /app/var
CMD ["python", "-m", "citywatch.service"]
