# MagicPay Kiosk — Kahve otomatı MDB köprüsü

Kioskta ödemesi alınan kahvenin otomatta hazırlanmasını sağlayan küçük ajan. Raspberry Pi üzerinde çalışır,
Qibixx MDB kartı üzerinden kahve makinesine **Cashless Device (kartlı ödeme birimi)** olarak bağlanır ve
MagicPay Kiosk API'sinden (`/api/machine/agent/*`) işleri alır.

Akış: müşteri kioskta ürünü seçer → kartla öder (Castles/Pavo) → API bir "vend işi" oluşturur → köprü işi alır →
makineye ürünü verdirir → sonucu bildirir. Makine ürünü veremezse API tutarı **otomatik iade** eder.

## Donanım

| Parça | Not |
|---|---|
| Raspberry Pi 3B+/4/5 | Raspberry Pi OS (64-bit, Bookworm) |
| Qibixx **MDB Pi HAT Plus** (UART) veya **MDB-USB** | Makinenin MDB veri yoluna "cashless" olarak bağlanır |
| MDB kablosu (Y-kablo) | Makinedeki VMC'nin MDB soketine; mevcut kart okuyucu varsa yerine/araya |

> MDB veri yoluna bağlantı makine kapalıyken yapılır. Makinenin servis menüsünden **cashless (ödeme sistemi)**
> etkinleştirilmeli; bazı makinelerde "Cashless 1" adresi ve "Always idle / Single vend" ayarı da gerekir.

## 1. Raspberry Pi hazırlığı

```bash
sudo apt update && sudo apt install -y python3 python3-venv git
```

### UART'ı açma (yalnızca MDB Pi HAT için)

```bash
sudo raspi-config   # Interface Options > Serial Port
                    #  "login shell over serial?"  -> Hayır
                    #  "serial port hardware enabled?" -> Evet
```

Raspberry Pi 3/4/5'te Bluetooth birincil UART'ı kullanır; HAT için `/boot/firmware/config.txt` sonuna ekleyin:

```
enable_uart=1
dtoverlay=disable-bt
```

ve `sudo systemctl disable hciuart` ardından yeniden başlatın. Port `/dev/ttyAMA0` (veya `/dev/serial0`) olur.
**MDB-USB** kullanıyorsanız UART ayarı gerekmez; port genelde `/dev/ttyACM0`'dır (`ls /dev/ttyACM*`).

Bağlantıyı denemek için (115200 8N1, satır sonu `\n`):

```bash
sudo apt install -y picocom
picocom -b 115200 --omap crlf /dev/ttyAMA0
# V yazıp Enter: kartın sürüm bilgisi gelmeli. C,1 yazınca c,STATUS,... olayları görülür. Çıkış: Ctrl-A Ctrl-X
```

## 2. Kurulum

```bash
sudo useradd --system --create-home --home-dir /opt/vending-bridge --groups dialout vending
sudo -u vending git clone --depth 1 https://github.com/zbk-der/Magic-Kiosk-mix-API.git /tmp/mk-api
sudo cp -r /tmp/mk-api/vending-bridge/. /opt/vending-bridge/
sudo chown -R vending:vending /opt/vending-bridge
cd /opt/vending-bridge
sudo -u vending python3 -m venv .venv
sudo -u vending .venv/bin/pip install -r requirements.txt
```

## 3. Makine anahtarı (token) ve yapılandırma

1. Panel → **Otomatlar** → otomatı ekleyin ve kioska bağlayın.
2. **Ürün eşleme**: kahve ürünlerini (ve boyut/süt gibi her seçenek kombinasyonunu) makinedeki ürün numarasına eşleyin.
   Kiosk yalnızca eşlenmiş ürün/varyantları satar; müşteri ne seçerse makine birebir onu yapar.
3. **Token oluştur** → anahtar yalnızca bir kez gösterilir, kopyalayın.

```bash
sudo mkdir -p /etc/vending-bridge
sudo cp /opt/vending-bridge/config.example.toml /etc/vending-bridge/config.toml
sudo nano /etc/vending-bridge/config.toml      # machine_token ve serial_port alanlarını doldurun
sudo chown root:vending /etc/vending-bridge/config.toml && sudo chmod 0640 /etc/vending-bridge/config.toml
```

Ortam değişkenleri dosyadakini ezer: `API_URL`, `MACHINE_TOKEN`, `SERIAL_PORT`, `MODE` (AUTO/REMOTE/CREDIT),
`SIMULATOR=1`, `POLL_WAIT`, `LOG_LEVEL`. Anahtar sızarsa panelden yeni token oluşturun (eskisi hemen geçersiz olur).

## 4. Mod tespiti (REMOTE / CREDIT)

```bash
sudo -u vending VENDING_BRIDGE_CONFIG=/etc/vending-bridge/config.toml .venv/bin/python -m vending_bridge --detect-only
```

- **REMOTE** (MDB 4.3 uzaktan seçim): VMC özellik seviyesi 3 ve opsiyon bit 6 açık. Köprü `C,SEL,<tutar>,<ödemeID>,<ürünNo>`
  ile ürünü kendisi seçer; makine yalnızca o ürün numarasını isterse onaylar (`C,VEND`), başka ürün isterse reddeder (`C,VEND,-1`) ve iade yapılır.
- **CREDIT** (eski makineler): köprü `C,START,<tutar>` ile kredi verir, müşteri makinede **kioskun gösterdiği numaralı
  düğmeye** basar. Yalnızca ürün numarası ve fiyatı eşleşen seçim onaylanır; diğer düğmeler reddedilir, kiosk "yanlış ürün"
  uyarısı gösterir. Süre (120 sn) dolarsa `C,STOP` ve iade.
  CREDIT modunda eşlemedeki fiyat makinedeki fiyatla **aynı** olmalıdır.

Köprü tespit sonucunu açılışta API'ye bildirir; panelden mod elle de seçilebilir (AUTO/REMOTE/CREDIT).

## 5. Deneme (simülatör) ve servis

```bash
# Gerçek makine olmadan uçtan uca deneme (API'ye bağlanır, sahte Qibixx kullanır):
sudo -u vending SIMULATOR=1 VENDING_BRIDGE_CONFIG=/etc/vending-bridge/config.toml .venv/bin/python -m vending_bridge

# systemd servisi
sudo cp /opt/vending-bridge/vending-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vending-bridge
journalctl -u vending-bridge -f
```

Panelde otomat **çevrimiçi** görünmeli (köprü sürekli uzun sorgu yapar, 30 sn'de bir heartbeat gönderir).

## Güvenlik ve hata durumları

- Ödeme her zaman kiosk POS'unda alınır; köprü para görmez. Makine ürünü veremezse (`c,ERR,VEND 3`), iptal ederse
  (`VEND 1`), veri yolu sıfırlanırsa (`VEND 5`), VMC reddederse (`ERR,-3`), süre dolarsa veya köprü yeniden başlarsa
  iş **FAILED/EXPIRED** olur ve API iadeyi otomatik yapar (Castles: aynı gün tam sipariş Void, aksi halde Refund;
  Pavo: iptal / kısmi iade). İade yapılamazsa panelde "Dikkat gerektiren otomat işleri" olarak görünür.
- Ağ kesintisinde köprü üstel bekleme (1→60 sn) ile yeniden bağlanır; sonuç bildirimi 5 dk boyunca tekrar denenir.

## Geliştirme

```bash
pip install -r requirements.txt pytest
python -m pytest -q          # simülatör + sahte API ile testler (donanım gerekmez)
```

Modüller: `qibixx.py` (seri port sürücüsü, olay ayrıştırma), `vend.py` (REMOTE/CREDIT durum makineleri),
`detect.py` (X,1 sniffer ile özellik seviyesi / bit 6), `agent.py` (API: hello / long-poll / olay / heartbeat),
`simulator.py` (sahte Qibixx + VMC), `config.py` (`config.toml` + ortam değişkenleri).

Kaynaklar: [Qibixx Cashless Slave API](https://docs.qibixx.com/released/mdb-products/api-cashless-slave),
[MDB 4.3 uzaktan ürün seçimi](https://docs.qibixx.com/released/mdb-products/cashless-device-remote-product-selection-with-mdb-4-3),
NAMA MDB/ICP 4.3 bölüm 7.
