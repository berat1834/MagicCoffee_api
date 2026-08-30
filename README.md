# Magic Burger API

FastAPI tabanlı kiosk katalog ve sipariş servisi.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

- `GET /health`
- `GET /api/catalog`
- `POST /api/orders`
- `GET /api/orders/{order_number}`
- Swagger: `http://localhost:8000/docs`

