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
    option("whole-milk", "Tam yaÄŸlÄ± sÃ¼t", 0, True),
    option("semi-skimmed", "YarÄ±m yaÄŸlÄ± sÃ¼t"),
    option("lactose-free", "Laktozsuz sÃ¼t", 8),
    option("almond", "Badem sÃ¼tÃ¼", 18),
    option("oat", "Yulaf sÃ¼tÃ¼", 18),
    option("coconut", "Hindistan cevizi sÃ¼tÃ¼", 18),
]

SYRUPS = [
    option("vanilla", "Vanilya", 12),
    option("caramel", "Karamel", 12),
    option("hazelnut", "FÄ±ndÄ±k", 12),
    option("chocolate", "Ã‡ikolata", 12),
    option("white-chocolate", "Beyaz Ã§ikolata", 14),
]

DESSERT_PAIRINGS = [
    option("brownie-bite", "Mini brownie", 55),
    option("cookie", "Ã‡ikolatalÄ± cookie", 45),
    option("croissant", "TereyaÄŸlÄ± kruvasan", 65),
]

CATEGORIES = [
    {"id": "espresso", "name": "Espresso BazlÄ± Kahveler", "eyebrow": "Barista klasikleri", "position": 0, "active": True},
    {"id": "filter", "name": "Filtre Kahveler", "eyebrow": "YavaÅŸ demlenmiÅŸ", "position": 1, "active": True},
    {"id": "cold", "name": "SoÄŸuk Kahveler", "eyebrow": "Serin kahve molasÄ±", "position": 2, "active": True},
    {"id": "frappe", "name": "Frappeler", "eyebrow": "TatlÄ± ve buzlu", "position": 3, "active": True},
    {"id": "tea", "name": "Ã‡aylar", "eyebrow": "Sade, sÄ±cak, ferah", "position": 4, "active": True},
    {"id": "other-drinks", "name": "DiÄŸer Ä°Ã§ecekler", "eyebrow": "Kahvesiz seÃ§enekler", "position": 5, "active": True},
    {"id": "desserts", "name": "TatlÄ±lar", "eyebrow": "Yan lezzetler", "position": 6, "active": True},
    {"id": "snacks", "name": "AtÄ±ÅŸtÄ±rmalÄ±klar", "eyebrow": "HÄ±zlÄ± eÅŸlikÃ§iler", "position": 7, "active": True},
    {"id": "deneme", "name": "Deneme Kategori", "eyebrow": "Test Ã¼rÃ¼nleri", "position": 8, "active": True},
]


def customization(*, cold: bool = False, milk_required: bool = False, pairings: bool = True):
    return {
        "size": {
            "enabled": True,
            "title": "Boyut SeÃ§imi",
            "required": True,
            "minSelect": 1,
            "maxSelect": 1,
            "options": [option("small", "KÃ¼Ã§Ã¼k", 0), option("medium", "Orta", 10, True), option("large", "BÃ¼yÃ¼k", 20)],
        },
        "temperature": {
            "enabled": not cold,
            "title": "SÄ±caklÄ±k",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("hot", "SÄ±cak", 0, True), option("warm", "IlÄ±k"), option("extra-hot", "Ekstra sÄ±cak")],
        },
        "ice": {
            "enabled": cold,
            "title": "Buz MiktarÄ±",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("no-ice", "Buzsuz"), option("light-ice", "Az buzlu"), option("normal-ice", "Normal", 0, True), option("extra-ice", "Ekstra buzlu")],
        },
        "milk": {
            "enabled": True,
            "title": "SÃ¼t SeÃ§imi",
            "required": milk_required,
            "minSelect": 1 if milk_required else 0,
            "maxSelect": 1,
            "options": deepcopy(MILKS),
        },
        "syrup": {"enabled": True, "title": "Åurup SeÃ§imi", "required": False, "minSelect": 0, "maxSelect": 2, "options": deepcopy(SYRUPS)},
        "sugar": {
            "enabled": True,
            "title": "Åeker",
            "required": False,
            "minSelect": 0,
            "maxSelect": 1,
            "options": [option("none", "Åekersiz", 0, True), option("one", "1 ÅŸeker"), option("two", "2 ÅŸeker"), option("sweetener", "TatlandÄ±rÄ±cÄ±")],
        },
        "shot": {"enabled": True, "title": "Ekstra Shot", "required": False, "minSelect": 0, "maxSelect": 2, "options": [option("single-shot", "1 ekstra shot", 22), option("double-shot", "2 ekstra shot", 40)]},
        "cream": {"enabled": cold, "title": "Krema", "required": False, "minSelect": 0, "maxSelect": 1, "options": [option("cream", "Krema ekle", 15)]},
        "pairing": {"enabled": pairings, "title": "YanÄ±na TatlÄ±", "required": False, "minSelect": 0, "maxSelect": 1, "options": deepcopy(DESSERT_PAIRINGS)},
    }


PRODUCTS = [
    {"id": "latte", "categoryId": "espresso", "name": "Caffe Latte", "description": "Espresso, kadifemsi sÃ¼t ve ince kÃ¶pÃ¼k.", "price": 92, "kind": "coffee", "image": "/images/products/latte.png", "emoji": "â˜•", "customizable": True, "popular": True, "active": True, "position": 0, "stockQuantity": 80, "criticalStock": 10, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "americano", "categoryId": "espresso", "name": "Americano", "description": "Espresso ve sÄ±cak suyla dengeli klasik.", "price": 76, "kind": "coffee", "image": "/images/products/americano.png", "emoji": "â˜•", "customizable": True, "popular": False, "active": True, "position": 1, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization()},
    {"id": "flat-white", "categoryId": "espresso", "name": "Flat White", "description": "YoÄŸun espresso ve mikro kÃ¶pÃ¼klÃ¼ sÃ¼t.", "price": 98, "kind": "coffee", "image": "/images/products/flat-white.png", "emoji": "â˜•", "customizable": True, "popular": True, "active": True, "position": 2, "stockQuantity": 45, "criticalStock": 8, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(milk_required=True)},
    {"id": "batch-brew", "categoryId": "filter", "name": "GÃ¼nlÃ¼k Filtre Kahve", "description": "Taze Ã§ekilmiÅŸ Ã§ekirdeklerle gÃ¼nlÃ¼k demleme.", "price": 68, "kind": "coffee", "image": "/images/products/filter-coffee.png", "emoji": "â˜•", "customizable": True, "popular": False, "active": True, "position": 3, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": customization()},
    {"id": "cold-brew", "categoryId": "cold", "name": "Cold Brew", "description": "Uzun demlenmiÅŸ, yumuÅŸak iÃ§imli soÄŸuk kahve.", "price": 95, "kind": "cold-coffee", "image": "/images/products/cold-brew.png", "emoji": "CB", "customizable": True, "popular": True, "active": True, "position": 4, "stockQuantity": 36, "criticalStock": 6, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True)},
    {"id": "caramel-frappe", "categoryId": "frappe", "name": "Karamel Frappe", "description": "SÃ¼t, karamel ve buzla hazÄ±rlanan tatlÄ± iÃ§im.", "price": 118, "kind": "cold-coffee", "image": "/images/products/caramel-frappe.png", "emoji": "FR", "customizable": True, "popular": True, "active": True, "position": 5, "stockQuantity": 24, "criticalStock": 5, "stockTrackingEnabled": True, "stockSellable": True, "customization": customization(cold=True, milk_required=True)},
    {"id": "earl-grey", "categoryId": "tea", "name": "Earl Grey", "description": "Bergamot aromalÄ± siyah Ã§ay.", "price": 52, "kind": "simple", "image": "/images/products/earl-grey.png", "emoji": "TEA", "customizable": False, "popular": False, "active": True, "position": 6, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": {}},
    {"id": "lemonade", "categoryId": "other-drinks", "name": "Ev YapÄ±mÄ± Limonata", "description": "Taze limon, nane ve buz.", "price": 72, "kind": "simple", "image": "/images/products/lemonade.png", "emoji": "LIM", "customizable": False, "popular": False, "active": True, "position": 7, "stockQuantity": 18, "criticalStock": 4, "stockTrackingEnabled": True, "stockSellable": True, "customization": {}},
    {"id": "san-sebastian", "categoryId": "desserts", "name": "San Sebastian Cheesecake", "description": "KremamsÄ± dokulu cheesecake.", "price": 135, "kind": "simple", "image": "/images/products/san-sebastian.png", "emoji": "CAKE", "customizable": False, "popular": True, "active": True, "position": 8, "stockQuantity": 12, "criticalStock": 3, "stockTrackingEnabled": True, "stockSellable": True, "customization": {}},
    {"id": "croissant", "categoryId": "snacks", "name": "TereyaÄŸlÄ± Kruvasan", "description": "Kat kat hamur, sade ve sÄ±cak servis.", "price": 84, "kind": "simple", "image": "/images/products/croissant.png", "emoji": "CR", "customizable": False, "popular": False, "active": True, "position": 9, "stockQuantity": 20, "criticalStock": 4, "stockTrackingEnabled": True, "stockSellable": True, "customization": {}},
    {"id": "deneme-urun", "categoryId": "deneme", "name": "Deneme ÃœrÃ¼n", "description": "Panel ve kiosk gÃ¶rÃ¼nÃ¼rlÃ¼ÄŸÃ¼nÃ¼ test etmek iÃ§in Ã¶rnek Ã¼rÃ¼n.", "price": 10, "kind": "simple", "image": "/images/products/test-product.svg", "emoji": "TEST", "customizable": False, "popular": False, "active": True, "position": 10, "stockQuantity": None, "criticalStock": None, "stockTrackingEnabled": False, "stockSellable": True, "customization": {}},
]

