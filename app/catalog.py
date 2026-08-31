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
    {"id": "tea", "name": "Çaylar", "eyebrow": "Sade, sıcak, ferah", "position": 4, "active": True},
    {"id": "other-drinks", "name": "Diğer İçecekler", "eyebrow": "Kahvesiz seçenekler", "position": 5, "active": True},
    {"id": "desserts", "name": "Tatlılar", "eyebrow": "Kahvenin yanına", "position": 6, "active": True},
    {"id": "snacks", "name": "Atıştırmalıklar", "eyebrow": "Hızlı eşlikçiler", "position": 7, "active": True},
]


def customization(*, cold: bool = False, milk_required: bool = False, pairings: bool = True):
    steps = {
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
            "enabled": True,
            "title": "Süt Seçimi",
            "required": milk_required,
            "minSelect": 1 if milk_required else 0,
            "maxSelect": 1,
            "options": deepcopy(MILKS),
        },
        "syrup": {"enabled": True, "title": "Şurup Seçimi", "required": False, "minSelect": 0, "maxSelect": 2, "options": deepcopy(SYRUPS)},
        "sugar": {
            "enabled": True,
            "title": "Şeker",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("none", "Şekersiz", 0, True), option("one", "1 şeker"), option("two", "2 şeker"), option("sweetener", "Tatlandırıcı")],
        },
        "shot": {"enabled": True, "title": "Ekstra Shot", "required": False, "minSelect": 0, "maxSelect": 2, "options": [option("single-shot", "1 ekstra shot", 22), option("double-shot", "2 ekstra shot", 40)]},
        "cream": {"enabled": cold, "title": "Krema", "required": False, "minSelect": 0, "maxSelect": 1, "options": [option("cream", "Krema ekle", 15)]},
        "pairing": {"enabled": pairings, "title": "Yanına Tatlı", "required": False, "minSelect": 0, "maxSelect": 1, "options": deepcopy(DESSERT_PAIRINGS)},
    }
    return steps


PRODUCTS = [
    {"id": "latte", "categoryId": "espresso", "name": "Caffe Latte", "description": "Espresso, kadifemsi süt ve ince köpük.", "price": 92, "kind": "coffee", "image": "", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 0, "stockQuantity": 80, "criticalStock": 10, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "americano", "categoryId": "espresso", "name": "Americano", "description": "Espresso ve sıcak suyla dengeli klasik.", "price": 76, "kind": "coffee", "image": "", "emoji": "☕", "customizable": True, "popular": False, "active": True, "position": 1, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization()},
    {"id": "flat-white", "categoryId": "espresso", "name": "Flat White", "description": "Yoğun espresso ve mikro köpüklü süt.", "price": 98, "kind": "coffee", "image": "", "emoji": "☕", "customizable": True, "popular": True, "active": True, "position": 2, "stockQuantity": 45, "criticalStock": 8, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "batch-brew", "categoryId": "filter", "name": "Günlük Filtre Kahve", "description": "Taze çekilmiş çekirdeklerle günlük demleme.", "price": 68, "kind": "coffee", "image": "", "emoji": "☕", "customizable": True, "popular": False, "active": True, "position": 3, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization()},
    {"id": "cold-brew", "categoryId": "cold", "name": "Cold Brew", "description": "Uzun demlenmiş, yumuşak içimli soğuk kahve.", "price": 95, "kind": "cold-coffee", "image": "", "emoji": "🧊", "customizable": True, "popular": True, "active": True, "position": 4, "stockQuantity": 36, "criticalStock": 6, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True)},
    {"id": "caramel-frappe", "categoryId": "frappe", "name": "Karamel Frappe", "description": "Kahve, süt, karamel ve buzla hazırlanan tatlı içim.", "price": 118, "kind": "cold-coffee", "image": "", "emoji": "🥤", "customizable": True, "popular": True, "active": True, "position": 5, "stockQuantity": 24, "criticalStock": 5, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True, milk_required=True)},
    {"id": "earl-grey", "categoryId": "tea", "name": "Earl Grey", "description": "Bergamot aromalı siyah çay.", "price": 52, "kind": "simple", "image": "", "emoji": "🍵", "customizable": False, "popular": False, "active": True, "position": 6, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization(pairings=False)},
    {"id": "lemonade", "categoryId": "other-drinks", "name": "Ev Yapımı Limonata", "description": "Taze limon, nane ve buz.", "price": 72, "kind": "simple", "image": "", "emoji": "🍋", "customizable": False, "popular": False, "active": True, "position": 7, "stockQuantity": 18, "criticalStock": 4, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True, pairings=False)},
    {"id": "san-sebastian", "categoryId": "desserts", "name": "San Sebastian Cheesecake", "description": "Kremamsı dokulu, kahveyle uyumlu cheesecake.", "price": 135, "kind": "simple", "image": "", "emoji": "🍰", "customizable": False, "popular": True, "active": True, "position": 8, "stockQuantity": 12, "criticalStock": 3, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(pairings=False)},
    {"id": "croissant", "categoryId": "snacks", "name": "Tereyağlı Kruvasan", "description": "Kat kat hamur, sade ve sıcak servis.", "price": 84, "kind": "simple", "image": "", "emoji": "🥐", "customizable": False, "popular": False, "active": True, "position": 9, "stockQuantity": 0, "criticalStock": 4, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(pairings=False)},
]
