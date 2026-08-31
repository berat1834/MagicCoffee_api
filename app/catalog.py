from __future__ import annotations

from copy import deepcopy


def option(option_id: str, name: str, price: float = 0, default: bool = False):
    return {
        "id": option_id,
        "name": name,
        "priceDelta": price,
        "defaultSelected": default,
        "enabled": True,
        "available": True,
    }


MILKS = [
    option("whole-milk", "Tam yağlı süt", 0, True),
    option("semi-skimmed", "Yarım yağlı süt"),
    option("lactose-free", "Laktozsuz süt", 8),
    option("almond", "Badem sütü", 18),
    option("oat", "Yulaf sütü", 18),
    option("coconut", "Hindistan cevizi sütü", 18),
]

SYRUPS = [
    option("vanilla", "Vanilya", 12),
    option("caramel", "Karamel", 12),
    option("hazelnut", "Fındık", 12),
    option("chocolate", "Çikolata", 12),
    option("white-chocolate", "Beyaz çikolata", 14),
]

DESSERT_PAIRINGS = [
    option("brownie-bite", "Mini brownie", 55),
    option("cookie", "Çikolatalı cookie", 45),
    option("croissant", "Tereyağlı kruvasan", 65),
]

CATEGORIES = [
    {"id": "espresso", "name": "Espresso Bazlı Kahveler", "eyebrow": "Barista klasikleri", "position": 0, "active": True},
    {"id": "filter", "name": "Filtre Kahveler", "eyebrow": "Yavaş demlenmiş", "position": 1, "active": True},
    {"id": "cold", "name": "Soğuk Kahveler", "eyebrow": "Serin kahve molası", "position": 2, "active": True},
    {"id": "frappe", "name": "Frappeler", "eyebrow": "Tatlı ve buzlu", "position": 3, "active": True},
    {"id": "traditional", "name": "Geleneksel Kahveler", "eyebrow": "Klasik fincanlar", "position": 4, "active": True},
    {"id": "tea", "name": "Çaylar", "eyebrow": "Sade, sıcak, ferah", "position": 5, "active": True},
    {"id": "other-drinks", "name": "Diğer İçecekler", "eyebrow": "Kahvesiz seçenekler", "position": 6, "active": True},
    {"id": "desserts", "name": "Tatlılar", "eyebrow": "Yan lezzetler", "position": 7, "active": True},
    {"id": "snacks", "name": "Atıştırmalıklar", "eyebrow": "Hızlı eşlikçiler", "position": 8, "active": True},
    {"id": "deneme", "name": "Deneme Kategori", "eyebrow": "Test ürünleri", "position": 9, "active": True},
]


def customization(
    *,
    cold: bool = False,
    milk_required: bool = False,
    milk: bool = True,
    syrup: bool = True,
    cream: bool | None = None,
    pairings: bool = True,
):
    return {
        "size": {
            "enabled": True,
            "title": "Boyut Seçimi",
            "required": True,
            "minSelect": 1,
            "maxSelect": 1,
            "options": [option("small", "Küçük", 0), option("medium", "Orta", 10, True), option("large", "Büyük", 20)],
        },
        "temperature": {
            "enabled": not cold,
            "title": "Sıcaklık",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("hot", "Sıcak", 0, True), option("warm", "Ilık"), option("extra-hot", "Ekstra sıcak")],
        },
        "ice": {
            "enabled": cold,
            "title": "Buz Miktarı",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("no-ice", "Buzsuz"), option("light-ice", "Az buzlu"), option("normal-ice", "Normal", 0, True), option("extra-ice", "Ekstra buzlu")],
        },
        "milk": {
            "enabled": milk,
            "title": "Süt Seçimi",
            "required": milk_required,
            "minSelect": 1 if milk_required else 0,
            "maxSelect": 1,
            "options": deepcopy(MILKS),
        },
        "syrup": {"enabled": syrup, "title": "Şurup Seçimi", "required": False, "minSelect": 0, "maxSelect": 2, "options": deepcopy(SYRUPS)},
        "sugar": {
            "enabled": True,
            "title": "Şeker",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("none", "Şekersiz", 0, True), option("one", "1 şeker"), option("two", "2 şeker"), option("sweetener", "Tatlandırıcı")],
        },
        "shot": {"enabled": True, "title": "Ekstra Shot", "required": False, "minSelect": 0, "maxSelect": 2, "options": [option("single-shot", "1 ekstra shot", 22), option("double-shot", "2 ekstra shot", 40)]},
        "cream": {"enabled": cold if cream is None else cream, "title": "Krema", "required": False, "minSelect": 0, "maxSelect": 1, "options": [option("cream", "Krema ekle", 15)]},
        "pairing": {"enabled": pairings, "title": "Yanına Tatlı", "required": False, "minSelect": 0, "maxSelect": 1, "options": deepcopy(DESSERT_PAIRINGS)},
    }


TURKISH_COFFEE_CUSTOMIZATION = {
    "size": {"enabled": False, "title": "Boyut Seçimi", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "temperature": {"enabled": False, "title": "Sıcaklık", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "ice": {"enabled": False, "title": "Buz Miktarı", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "milk": {"enabled": False, "title": "Süt Seçimi", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "syrup": {"enabled": False, "title": "Şurup Seçimi", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "sugar": {"enabled": True, "title": "Şeker", "required": True, "minSelect": 1, "maxSelect": 1, "options": [option("plain", "Sade", 0, True), option("medium-sugar", "Orta şekerli"), option("sweet", "Şekerli")]},
    "shot": {"enabled": False, "title": "Ekstra Shot", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "cream": {"enabled": False, "title": "Krema", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
    "pairing": {"enabled": True, "title": "Yanına Tatlı", "required": False, "minSelect": 0, "maxSelect": 1, "options": deepcopy(DESSERT_PAIRINGS)},
}


PRODUCTS = [
    {"id": "latte", "categoryId": "espresso", "name": "Caffe Latte", "description": "Espresso, kadifemsi süt ve ince köpük.", "price": 92, "kind": "coffee", "image": "/images/products/latte.png", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 0, "stockQuantity": 80, "criticalStock": 10, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "americano", "categoryId": "espresso", "name": "Americano", "description": "Espresso ve sıcak suyla dengeli klasik.", "price": 76, "kind": "coffee", "image": "/images/products/americano.png", "emoji": "☕", "customizable": True, "popular": False, "active": True, "position": 1, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization()},
    {"id": "flat-white", "categoryId": "espresso", "name": "Flat White", "description": "Yoğun espresso ve mikro köpüklü süt.", "price": 98, "kind": "coffee", "image": "/images/products/flat-white.png", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 2, "stockQuantity": 45, "criticalStock": 8, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "espresso", "categoryId": "espresso", "name": "Espresso", "description": "Yoğun aromalı, kısa ve klasik espresso.", "price": 60, "kind": "coffee", "image": "/images/products/espresso.png", "emoji": "☕", "customizable": True, "popular": False, "active": True, "position": 3, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization(milk=False, syrup=False)},
    {"id": "cappuccino", "categoryId": "espresso", "name": "Cappuccino", "description": "Espresso, sıcak süt ve bol süt köpüğü.", "price": 96, "kind": "coffee", "image": "/images/products/cappuccino.png", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 4, "stockQuantity": 55, "criticalStock": 8, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "mocha", "categoryId": "espresso", "name": "Mocha", "description": "Espresso, süt ve çikolata lezzeti.", "price": 112, "kind": "coffee", "image": "/images/products/mocha.png", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 5, "stockQuantity": 42, "criticalStock": 8, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True, cream=True)},
    {"id": "caramel-macchiato", "categoryId": "espresso", "name": "Caramel Macchiato", "description": "Süt köpüğü, espresso ve karamel dokunuşu.", "price": 118, "kind": "coffee", "image": "/images/products/caramel-macchiato.png", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 6, "stockQuantity": 40, "criticalStock": 7, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "batch-brew", "categoryId": "filter", "name": "Günlük Filtre Kahve", "description": "Taze çekilmiş çekirdeklerle günlük demleme.", "price": 68, "kind": "coffee", "image": "/images/products/filter-coffee.png", "emoji": "☕", "customizable": True, "popular": False, "active": True, "position": 7, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization()},
    {"id": "cold-brew", "categoryId": "cold", "name": "Cold Brew", "description": "Uzun demlenmiş, yumuşak içimli soğuk kahve.", "price": 95, "kind": "cold-coffee", "image": "/images/products/cold-brew.png", "emoji": "CB", "customizable": True, "popular": True, "active": True, "position": 8, "stockQuantity": 36, "criticalStock": 6, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True)},
    {"id": "iced-latte", "categoryId": "cold", "name": "Iced Latte", "description": "Soğuk süt, espresso ve buzla ferah latte.", "price": 104, "kind": "cold-coffee", "image": "/images/products/iced-latte.png", "emoji": "IC", "customizable": True, "popular": True, "active": True, "position": 9, "stockQuantity": 38, "criticalStock": 7, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True, milk_required=True)},
    {"id": "caramel-frappe", "categoryId": "frappe", "name": "Karamel Frappe", "description": "Süt, karamel ve buzla hazırlanan tatlı içim.", "price": 118, "kind": "cold-coffee", "image": "/images/products/caramel-frappe.png", "emoji": "FR", "customizable": True, "popular": True, "active": True, "position": 10, "stockQuantity": 24, "criticalStock": 5, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True, milk_required=True)},
    {"id": "turkish-coffee", "categoryId": "traditional", "name": "Türk Kahvesi", "description": "Cezvede pişen geleneksel bol köpüklü kahve.", "price": 70, "kind": "coffee", "image": "/images/products/turkish-coffee.png", "emoji": "TK", "customizable": True, "popular": False, "active": True, "position": 11, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": deepcopy(TURKISH_COFFEE_CUSTOMIZATION)},
    {"id": "earl-grey", "categoryId": "tea", "name": "Earl Grey", "description": "Bergamot aromalı siyah çay.", "price": 52, "kind": "simple", "image": "/images/products/earl-grey.png", "emoji": "TEA", "customizable": False, "popular": False, "active": True, "position": 12, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": {}},
    {"id": "lemonade", "categoryId": "other-drinks", "name": "Ev Yapımı Limonata", "description": "Taze limon, nane ve buz.", "price": 72, "kind": "simple", "image": "/images/products/lemonade.png", "emoji": "LIM", "customizable": False, "popular": False, "active": True, "position": 13, "stockQuantity": 18, "criticalStock": 4, "stockTrackingEnabled": True, "stockSellable": True, "customization": {}},
    {"id": "san-sebastian", "categoryId": "desserts", "name": "San Sebastian Cheesecake", "description": "Kremamsı dokulu cheesecake.", "price": 135, "kind": "simple", "image": "/images/products/san-sebastian.png", "emoji": "CAKE", "customizable": False, "popular": True, "active": True, "position": 14, "stockQuantity": 12, "criticalStock": 3, "stockTrackingEnabled": True, "stockSellable": True, "customization": {}},
    {"id": "croissant", "categoryId": "snacks", "name": "Tereyağlı Kruvasan", "description": "Kat kat hamur, sade ve sıcak servis.", "price": 84, "kind": "simple", "image": "/images/products/croissant.png", "emoji": "CR", "customizable": False, "popular": False, "active": True, "position": 15, "stockQuantity": 20, "criticalStock": 4, "stockTrackingEnabled": True, "stockSellable": True, "customization": {}},
    {"id": "deneme-urun", "categoryId": "deneme", "name": "Deneme Ürün", "description": "Panel ve kiosk görünürlüğünü test etmek için örnek ürün.", "price": 10, "kind": "simple", "image": "/images/products/test-product.svg", "emoji": "TEST", "customizable": False, "popular": False, "active": True, "position": 16, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": {}},
]
