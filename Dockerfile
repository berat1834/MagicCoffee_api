FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Kahve menüsü tohumu: app_state bulunamazsa ilk migrasyonda buradan taşınır.
COPY data/store.json ./data/store.json
COPY uploads ./uploads
# Repodaki ürün görselleri ilk kurulumda kalıcı diske kopyalanmak üzere ayrıca saklanır.
RUN cp -r uploads /opt/seed-uploads \
    && useradd --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app

USER 10001
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
