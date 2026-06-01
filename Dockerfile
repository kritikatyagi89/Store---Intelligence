# syntax=docker/dockerfile:1

FROM python:3.11-slim

WORKDIR /app

ENV YOLO_CONFIG_DIR=/app
ENV ULTRALYTICS_HOME=/app/.ultralytics

# OpenCV / Ultralytics runtime dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Use local yolov8n.pt from build context when present; otherwise download at build time
RUN --mount=type=bind,source=.,target=/buildctx,readonly \
    if [ -f /buildctx/yolov8n.pt ]; then \
      cp /buildctx/yolov8n.pt /app/yolov8n.pt; \
    else \
      python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"; \
    fi

COPY . /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
