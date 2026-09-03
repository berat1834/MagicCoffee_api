# Magic Coffee API

FastAPI tabanli kiosk, panel, katalog, siparis, stok ve raporlama servisi.

## Calistirma

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8300
```

## Aiven PostgreSQL

API, `DATABASE_URL` olmadan local JSON dosyasina dusmez. Bu bilgisayarda calistirmadan once `magicCoffee_api\.env` icine Aiven baglantisini yaz:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DBNAME?sslmode=require
ALLOWED_ORIGINS=http://127.0.0.1:5370,http://localhost:5370,http://127.0.0.1:5371,http://localhost:5371
```

Gecici offline gelistirme icin ayrica `ALLOW_LOCAL_FILE_STORE=true` verilebilir; kiosk/panel kullaniminda kapali kalmali.

## Port

- API: `http://127.0.0.1:8300`
- Swagger: `http://127.0.0.1:8300/docs`

## Uclar

- `GET /health`
- `GET /api/catalog`
- `POST /api/orders`
- `GET /api/admin/categories`
- `GET /api/admin/products`
- `GET /api/admin/stock`
- `GET /api/admin/orders`
- `GET /api/admin/reports`
