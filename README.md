# Magic Coffee API

FastAPI tabanli kiosk, panel, katalog, siparis, stok ve raporlama servisi.

## Calistirma

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8300
```

## MagicCoffee PostgreSQL

API, `DATABASE_URL` olmadan local JSON dosyasina dusmez. Canli ortamda yalnizca MagicCoffee'ye ayrilmis veritabani ve ayri bir veritabani kullanicisi kullanin. Pavo odemeleri, asagidaki Pavo ayarlarinin tamami tanimlanana kadar HTTP 503 ile kapali kalir:

```env
DATABASE_URL=
DATABASE_ALLOWED_HOST=pg-39595717-beratbaylan123-9802.j.aivencloud.com
ALLOWED_ORIGINS=http://127.0.0.1:5370,http://localhost:5370,http://127.0.0.1:5371,http://localhost:5371
PAVO_GATEWAY_BASE_URL=
PAVO_GATEWAY_ALLOWED_HOST=
PAVO_BRANCH_ID=
PAVO_TERMINAL_SERIAL=
PAVO_SOURCE_FINGERPRINT=
PAVO_PROVIDER_TYPE=
PAVO_GATEWAY_SERVICE_EMAIL=
PAVO_GATEWAY_SERVICE_PASSWORD=
GOOGLE_TRANSLATE_API_KEY=
```

Gateway URL'si HTTPS olmali ve `PAVO_GATEWAY_ALLOWED_HOST` ile kod seviyesindeki Kebo allowlist'ine birebir uymalidir. Odeme akisi yalnizca `PAVO_CLOUD`, branch `2`, `PAV960000010` ve `TEST` kimligini kabul eder. MagicCoffee API, Kebo servis hesabi ile login olur; JWT yalnizca process belleginde tutulur ve 401 durumunda en fazla bir kez yenilenir. Odeme baslatma ve sorgulama isteklerinde `X-MagicCoffee-Kiosk-Fingerprint` basligi da tanimli source fingerprint ile birebir dogrulanir. Kebo'daki terminal kaydi MagicCoffee paneli ve API'si icin salt okunurdur; olusturma, degistirme, silme ve yeniden eslestirme kapatilmistir. Servis hesabi secret'lari repoya yazilmamalidir. Gecici offline gelistirme icin `ALLOW_LOCAL_FILE_STORE=true` verilebilir; kiosk/panel kullaniminda kapali kalmali.

## Port

- API: `http://127.0.0.1:8300`
- Swagger: `http://127.0.0.1:8300/docs`

## Uclar

- `GET /health`
- `GET /api/catalog`
- `GET /api/catalog?lang=tr|en`
- `GET|POST|PUT|DELETE /api/admin/pos/devices`
- `POST /api/admin/pos/devices/{id}/pair`
- `POST /api/admin/pos/devices/{id}/pair/check`
- `POST /api/pos/payments`
- `GET /api/pos/payments/{transaction_id}`
- `POST /api/orders`
- `POST /api/orders/{order_number}/receipt`
- `GET /api/admin/categories`
- `GET /api/admin/products`
- `GET /api/admin/stock`
- `GET /api/admin/orders`
- `GET /api/admin/reports`

Mock POS testleri gerçek Pavo servisine istek göndermez:

```powershell
.\.venv\Scripts\python.exe -m compileall app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
