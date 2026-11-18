FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend /app/backend
COPY config /app/config
WORKDIR /app/backend
RUN useradd --uid 10001 --create-home commerceai && mkdir -p /app/storage && chown commerceai /app/storage
USER commerceai
ENV DATA_DIR=/app/storage
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
