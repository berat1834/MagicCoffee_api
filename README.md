# Magic Coffee API

FastAPI tabanlı kiosk, panel, katalog, sipariş, stok ve raporlama servisi.

## Çalıştırma

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload --port 8300
```

## Port

- API: `http://127.0.0.1:8300`
- Swagger: `http://127.0.0.1:8300/docs`

## Uçlar

- `GET /health`
- `GET /api/catalog`
- `POST /api/orders`
- `GET /api/admin/categories`
- `GET /api/admin/products`
- `GET /api/admin/stock`
- `GET /api/admin/orders`
- `GET /api/admin/reports`
