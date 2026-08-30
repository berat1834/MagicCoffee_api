from __future__ import annotations


INGREDIENTS = [
    {"id": "tomato", "name": "Domates"},
    {"id": "lettuce", "name": "Marul"},
    {"id": "cheddar", "name": "Cheddar Peyniri"},
    {"id": "onion", "name": "Soğan"},
    {"id": "pickle", "name": "Turşu"},
]

FRIES = [
    {"id": "small", "name": "Küçük", "priceDelta": 0},
    {"id": "medium", "name": "Orta", "priceDelta": 15},
    {"id": "large", "name": "Büyük", "priceDelta": 25},
]

DRINKS = [
    {"id": "cola", "name": "Kola"},
    {"id": "zero-cola", "name": "Şekersiz Kola"},
    {"id": "fanta", "name": "Portakallı Gazoz"},
    {"id": "ayran", "name": "Ayran"},
    {"id": "water", "name": "Su"},
]

CATEGORIES = [
    {"id": "burgers", "name": "Burgerler", "eyebrow": "Tek başına lezzet"},
    {"id": "burger-menus", "name": "Burger Menüler", "eyebrow": "Patates + içecek"},
    {"id": "two-person", "name": "2 Kişilik Menüler", "eyebrow": "Birlikte daha güzel"},
    {"id": "drinks", "name": "İçecekler", "eyebrow": "Buz gibi"},
    {"id": "desserts", "name": "Tatlılar", "eyebrow": "Mutlu son"},
    {"id": "sauces", "name": "Soslar", "eyebrow": "Son dokunuş"},
]


def burger(product_id: str, name: str, price: float, image: str, protein: str, patties: int):
    return {
        "id": product_id,
        "categoryId": "burgers",
        "name": name,
        "description": f"{patties} kat {protein.lower()}, taptaze malzemeler ve özel Magic sos.",
        "price": price,
        "image": image,
        "kind": "burger",
        "protein": protein,
        "patties": patties,
        "customizable": True,
        "popular": patties == 2,
    }


def menu(product_id: str, name: str, price: float, image: str, protein: str, patties: int):
    return {
        "id": product_id,
        "categoryId": "burger-menus",
        "name": name,
        "description": "Burger, çıtır patates ve seçeceğin içecek bir arada.",
        "price": price,
        "image": image,
        "kind": "menu",
        "protein": protein,
        "patties": patties,
        "customizable": True,
        "popular": patties == 2,
    }


PRODUCTS = [
    burger("beef-jr", "Magic Jr Burger Et (60gr)", 165, "/images/products/beef-jr.webp", "Et", 1),
    burger("beef-magic", "Magic Burger Et (120gr)", 220, "/images/products/beef-magic.webp", "Et", 2),
    burger("beef-big", "Big Magic Burger Et (180gr)", 275, "/images/products/beef-big.webp", "Et", 3),
    burger("chicken-jr", "Magic Jr Burger Tavuk (60gr)", 145, "/images/products/chicken-jr.webp", "Tavuk", 1),
    burger("chicken-magic", "Magic Burger Tavuk (120gr)", 190, "/images/products/chicken-magic.webp", "Tavuk", 2),
    burger("chicken-big", "Big Magic Burger Tavuk (180gr)", 235, "/images/products/chicken-big.webp", "Tavuk", 3),
    menu("menu-beef-jr", "Magic Jr Et Menü", 225, "/images/products/menu-beef-jr.webp", "Et", 1),
    menu("menu-beef-magic", "Magic Et Menü", 280, "/images/products/menu-beef-magic.webp", "Et", 2),
    menu("menu-beef-big", "Big Magic Et Menü", 335, "/images/products/menu-beef-big.webp", "Et", 3),
    menu("menu-chicken-jr", "Magic Jr Tavuk Menü", 205, "/images/products/menu-chicken-jr.webp", "Tavuk", 1),
    menu("menu-chicken-magic", "Magic Tavuk Menü", 250, "/images/products/menu-chicken-magic.webp", "Tavuk", 2),
    menu("menu-chicken-big", "Big Magic Tavuk Menü", 295, "/images/products/menu-chicken-big.webp", "Tavuk", 3),
    {
        "id": "couple-jr",
        "categoryId": "two-person",
        "name": "2 Kişilik Jr Magic Menü",
        "description": "2 Magic Jr burger, 2 küçük patates ve 2 içecek.",
        "price": 410,
        "image": "/images/products/menu-beef-jr.webp",
        "kind": "bundle",
        "protein": "Et",
        "patties": 1,
        "serves": 2,
        "customizable": True,
        "popular": False,
    },
    {
        "id": "couple-mix",
        "categoryId": "two-person",
        "name": "2 Kişilik Magic Mix Menü",
        "description": "1 Magic Et, 1 Magic Tavuk, 2 küçük patates ve 2 içecek.",
        "price": 495,
        "image": "/images/products/menu-chicken-magic.webp",
        "kind": "bundle",
        "protein": "Mix",
        "patties": 2,
        "serves": 2,
        "customizable": True,
        "popular": True,
    },
    {
        "id": "couple-big",
        "categoryId": "two-person",
        "name": "2 Kişilik Big Magic Menü",
        "description": "2 Big Magic burger, 2 küçük patates ve 2 içecek.",
        "price": 625,
        "image": "/images/products/menu-beef-big.webp",
        "kind": "bundle",
        "protein": "Et",
        "patties": 3,
        "serves": 2,
        "customizable": True,
        "popular": False,
    },
    {"id": "cola", "categoryId": "drinks", "name": "Kola", "description": "Buz gibi, 330 ml.", "price": 55, "kind": "simple", "emoji": "🥤"},
    {"id": "zero-cola", "categoryId": "drinks", "name": "Şekersiz Kola", "description": "Şekersiz, buz gibi, 330 ml.", "price": 55, "kind": "simple", "emoji": "🥤"},
    {"id": "ayran", "categoryId": "drinks", "name": "Ayran", "description": "Soğuk ve ferah, 300 ml.", "price": 45, "kind": "simple", "emoji": "🥛"},
    {"id": "water", "categoryId": "drinks", "name": "Su", "description": "Doğal kaynak suyu, 500 ml.", "price": 25, "kind": "simple", "emoji": "💧"},
    {"id": "brownie", "categoryId": "desserts", "name": "Sıcak Brownie", "description": "Yoğun çikolatalı, sıcacık.", "price": 95, "kind": "simple", "emoji": "🍫"},
    {"id": "sundae", "categoryId": "desserts", "name": "Karamelli Sundae", "description": "Vanilyalı dondurma ve karamel.", "price": 85, "kind": "simple", "emoji": "🍨"},
    {"id": "cookie", "categoryId": "desserts", "name": "Magic Cookie", "description": "Bol çikolata parçalı kurabiye.", "price": 65, "kind": "simple", "emoji": "🍪"},
    {"id": "magic-sauce", "categoryId": "sauces", "name": "Magic Sos", "description": "İmza burger sosumuz.", "price": 20, "kind": "simple", "emoji": "✨"},
    {"id": "bbq", "categoryId": "sauces", "name": "Barbekü Sos", "description": "İsli ve tatlı.", "price": 20, "kind": "simple", "emoji": "🔥"},
    {"id": "ranch", "categoryId": "sauces", "name": "Ranch Sos", "description": "Kremamsı ve ferah.", "price": 20, "kind": "simple", "emoji": "🥣"},
    {"id": "hot", "categoryId": "sauces", "name": "Acı Sos", "description": "Cesurlar için ekstra acı.", "price": 20, "kind": "simple", "emoji": "🌶️"},
]


CATALOG = {
    "brand": {"name": "Magic Burger", "currency": "TL", "version": "1.0.0"},
    "categories": CATEGORIES,
    "products": PRODUCTS,
    "modifiers": {"ingredients": INGREDIENTS, "fries": FRIES, "drinks": DRINKS},
}
