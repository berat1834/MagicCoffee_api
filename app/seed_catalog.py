"""MagicPay Kiosk platformu tohum verisi: 17 ana kategori, 17 kiosk ve kahve disindaki
16 ana kategorinin alt kategori/urun katalogu.

Kahve kategorisi mevcut Magic Coffee menusunden (app_state veya data/store.json) tasinir;
burada yalnizca diger 16 kategori tanimlidir. Ingilizce metinler `*_en` alanlarinda tutulur
ve migrasyon sirasinda mk_translations tablosuna hazir ceviri olarak yazilir.
"""
from __future__ import annotations

from typing import Any

# (id, ad, ad_en, kisa aciklama, kisa aciklama_en, lucide ikon, renk, siparis oneki)
SECTION_ROWS: list[tuple[str, str, str, str, str, str, str, str]] = [
    ("kahve", "Kahve", "Coffee", "Taze çekilmiş kahveler", "Freshly ground coffee", "Coffee", "#7a4a2a", "KHV"),
    ("burger", "Burger", "Burgers", "Izgara burger menüleri", "Grilled burger menus", "Beef", "#c2410c", "BRG"),
    ("pizza", "Pizza", "Pizza", "Taş fırın pizzalar", "Stone-oven pizzas", "Pizza", "#dc2626", "PZA"),
    ("doner", "Döner & Dürüm", "Doner & Wraps", "Döner, dürüm, porsiyon", "Doner, wraps and plates", "Sandwich", "#b45309", "DNR"),
    ("salata", "Salata & Bowl", "Salads & Bowls", "Hafif ve doyurucu", "Light and filling", "Salad", "#16a34a", "SLT"),
    ("tatli", "Tatlı & Pasta", "Desserts", "Pasta, cheesecake, tatlılar", "Cakes, cheesecakes and sweets", "CakeSlice", "#db2777", "TTL"),
    ("dondurma", "Dondurma", "Ice Cream", "Top dondurma, milkshake", "Scoops and milkshakes", "IceCreamCone", "#0ea5e9", "DND"),
    ("firin", "Fırın", "Bakery", "Simit, poğaça, kruvasan", "Simit, pastries and croissants", "Croissant", "#a16207", "FRN"),
    ("icecek", "Soğuk İçecek & Smoothie", "Drinks & Smoothies", "Meyve suyu, smoothie, limonata", "Juices, smoothies and lemonade", "CupSoda", "#f59e0b", "ICK"),
    ("atistirmalik", "Atıştırmalık & Market", "Snacks & Market", "Cips, çikolata, su", "Chips, chocolate and water", "Cookie", "#ea580c", "ATS"),
    ("giyim", "Giyim", "Clothing", "Tişört, sweatshirt, şapka", "T-shirts, sweatshirts and caps", "Shirt", "#4f46e5", "GYM"),
    ("ayakkabi", "Ayakkabı & Çanta", "Shoes & Bags", "Sneaker, terlik, sırt çantası", "Sneakers, slippers and backpacks", "Footprints", "#334155", "AYK"),
    ("elektronik", "Elektronik Aksesuar", "Electronics", "Şarj aleti, kulaklık, kablo", "Chargers, headphones and cables", "Headphones", "#0f766e", "ELK"),
    ("kozmetik", "Kozmetik & Bakım", "Beauty & Care", "Parfüm, krem, bakım", "Perfume, creams and care", "Sparkles", "#c026d3", "KZM"),
    ("kirtasiye", "Kitap & Kırtasiye", "Books & Stationery", "Kitap, defter, kalem", "Books, notebooks and pens", "BookOpen", "#2563eb", "KRT"),
    ("oyuncak", "Oyuncak & Hediyelik", "Toys & Gifts", "Oyuncak, hediyelik eşya", "Toys and gifts", "Gift", "#9333ea", "OYN"),
    ("cicek", "Çiçek", "Flowers", "Buket, saksı çiçeği", "Bouquets and potted plants", "Flower2", "#e11d48", "CCK"),
]

SECTIONS: list[dict[str, Any]] = [
    {
        "id": section_id,
        "name": name,
        "name_en": name_en,
        "tagline": tagline,
        "tagline_en": tagline_en,
        "icon": icon,
        "accent": accent,
        "order_prefix": prefix,
        "position": index,
        "active": True,
    }
    for index, (section_id, name, name_en, tagline, tagline_en, icon, accent, prefix) in enumerate(SECTION_ROWS)
]


def kiosk_rows() -> list[dict[str, Any]]:
    """k01..k17; kNN'nin vitrin kategorisi tablodaki NN. kategoridir."""
    rows = []
    for index, section in enumerate(SECTIONS, start=1):
        rows.append({
            "id": f"k{index:02d}",
            "name": f"Kiosk {index:02d} · {section['name']}",
            "featured_section_id": section["id"],
        })
    return rows


# ---- ozellestirme yardimcilari ------------------------------------------------
# Kiosk arayuzu ve panel `content`, `sides`, `drinks` adimlarini tanir; beden/numara
# secimi `sides` adiminda, ekstralar `drinks` adiminda tutulur.

def opt(option_id: str, name: str, name_en: str, price: float = 0, default: bool = False) -> dict[str, Any]:
    return {
        "id": option_id,
        "name": name,
        "name_en": name_en,
        "priceDelta": price,
        "defaultSelected": default,
        "enabled": True,
        "available": True,
    }


def step(title: str, title_en: str, options: list[dict[str, Any]], *, required: bool = False,
         max_select: int = 1, enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "title": title,
        "title_en": title_en,
        "required": required,
        "minSelect": 1 if required else 0,
        "maxSelect": max_select,
        "options": options,
    }


def base_steps() -> dict[str, Any]:
    return {
        "content": step("Ürün Notları", "Product Notes", [], enabled=False),
        "sides": step("Boyut Seçimi", "Size Selection", [], enabled=False),
        "drinks": step("Yan Ürün Seçimi", "Side Selection", [], enabled=False),
    }


def food_sizes(medium: float, large: float) -> dict[str, Any]:
    return step("Boyut Seçimi", "Size Selection", [
        opt("small", "Küçük", "Small", 0),
        opt("medium", "Orta", "Medium", medium),
        opt("large", "Büyük", "Large", large),
    ], required=True)


def extras(*options: dict[str, Any]) -> dict[str, Any]:
    return step("Ekstra Seçimi", "Extras", list(options), max_select=len(options))


def clothing_sizes() -> dict[str, Any]:
    return step("Beden Seçimi", "Size", [opt(size.lower(), size, size) for size in ("S", "M", "L", "XL")], required=True)


def shoe_sizes(start: int = 36, end: int = 44) -> dict[str, Any]:
    return step("Numara Seçimi", "Shoe Size", [opt(str(size), str(size), str(size)) for size in range(start, end + 1)], required=True)


BURGER_EXTRAS = extras(
    opt("extra-cheese", "Ekstra cheddar", "Extra cheddar", 30),
    opt("extra-patty", "Ekstra köfte", "Extra patty", 120),
    opt("caramelized-onion", "Karamelize soğan", "Caramelized onion", 25),
    opt("jalapeno", "Jalapeno biber", "Jalapeno pepper", 20),
)
PIZZA_EXTRAS = extras(
    opt("extra-mozzarella", "Ekstra mozzarella", "Extra mozzarella", 35),
    opt("mushroom", "Mantar", "Mushroom", 25),
    opt("olive", "Siyah zeytin", "Black olives", 20),
    opt("jalapeno", "Jalapeno biber", "Jalapeno pepper", 20),
)
DONER_EXTRAS = extras(
    opt("cheddar", "Kaşar peyniri", "Kashar cheese", 30),
    opt("fries", "Patates kızartması", "French fries", 25),
    opt("hot-sauce", "Acı sos", "Hot sauce", 0),
)
SALAD_EXTRAS = extras(
    opt("grilled-chicken", "Izgara tavuk", "Grilled chicken", 60),
    opt("avocado", "Avokado", "Avocado", 45),
    opt("parmesan", "Parmesan", "Parmesan", 30),
)
ICE_CREAM_EXTRAS = extras(
    opt("chocolate-sauce", "Çikolata sos", "Chocolate sauce", 15),
    opt("pistachio", "Antep fıstığı", "Pistachio", 35),
    opt("whipped-cream", "Krem şanti", "Whipped cream", 15),
)
FLOWER_EXTRAS = extras(
    opt("note-card", "Not kartı", "Note card", 25),
    opt("gift-wrap", "Hediye paketi", "Gift wrap", 50),
)


def custom(sides: dict[str, Any] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    steps = base_steps()
    if sides:
        steps["sides"] = sides
    if extra:
        steps["drinks"] = extra
    return steps


# (slug, ad, ad_en, aciklama, aciklama_en, fiyat, emoji, stok, ozellestirme)
P = tuple

# section -> [(alt kategori slug, ad, ad_en, ust baslik, ust baslik_en, [urunler])]
CATALOG: dict[str, list[tuple[str, str, str, str, str, list[P]]]] = {
    "burger": [
        ("klasik", "Klasik Burgerler", "Classic Burgers", "Izgara dana köfte", "Grilled beef patties", [
            ("klasik-burger", "Klasik Burger", "Classic Burger", "150 g dana köfte, marul, domates, turşu ve özel sos.", "150 g beef patty, lettuce, tomato, pickles and house sauce.", 289, "🍔", 60, custom(extra=BURGER_EXTRAS)),
            ("cheeseburger", "Cheeseburger", "Cheeseburger", "Dana köfte, eritilmiş cheddar, karamelize soğan.", "Beef patty, melted cheddar and caramelized onion.", 319, "🍔", 60, custom(extra=BURGER_EXTRAS)),
            ("double-burger", "Double Burger", "Double Burger", "İki kat dana köfte, çift cheddar ve füme et.", "Two beef patties, double cheddar and smoked beef.", 429, "🍔", 40, custom(extra=BURGER_EXTRAS)),
            ("mantarli-burger", "Mantarlı Burger", "Mushroom Burger", "Sote mantar, İsviçre peyniri ve trüf mayonez.", "Sautéed mushrooms, Swiss cheese and truffle mayo.", 349, "🍄", 35, custom(extra=BURGER_EXTRAS)),
        ]),
        ("tavuk", "Tavuk Burgerler", "Chicken Burgers", "Çıtır tavuk lezzetleri", "Crispy chicken favourites", [
            ("crispy-tavuk-burger", "Crispy Tavuk Burger", "Crispy Chicken Burger", "Çıtır tavuk göğsü, coleslaw ve ranch sos.", "Crispy chicken breast, coleslaw and ranch sauce.", 279, "🍗", 50, custom(extra=BURGER_EXTRAS)),
            ("acili-tavuk-burger", "Acılı Tavuk Burger", "Spicy Chicken Burger", "Acı marine tavuk, jalapeno ve chipotle sos.", "Spicy marinated chicken, jalapeno and chipotle sauce.", 289, "🌶️", 40, custom(extra=BURGER_EXTRAS)),
        ]),
        ("yan-urunler", "Yan Ürünler", "Sides", "Burgerin yanına", "Perfect with a burger", [
            ("patates-kizartmasi", "Patates Kızartması", "French Fries", "Çıtır elma dilim patates, deniz tuzu ile.", "Crispy fries with sea salt.", 99, "🍟", 120, custom(sides=food_sizes(20, 40))),
            ("sogan-halkasi", "Soğan Halkası", "Onion Rings", "8 adet çıtır soğan halkası, barbekü sos ile.", "8 crispy onion rings with barbecue sauce.", 119, "🧅", 80, None),
        ]),
    ],
    "pizza": [
        ("klasik", "Klasik Pizzalar", "Classic Pizzas", "Taş fırından", "Fresh from the stone oven", [
            ("margarita", "Margarita Pizza", "Margherita Pizza", "Domates sos, mozzarella ve taze fesleğen.", "Tomato sauce, mozzarella and fresh basil.", 249, "🍕", 50, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("karisik", "Karışık Pizza", "Mixed Pizza", "Sucuk, salam, mantar, biber ve zeytin.", "Turkish sausage, salami, mushrooms, peppers and olives.", 319, "🍕", 50, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("sucuklu", "Sucuklu Pizza", "Sucuk Pizza", "Bol sucuk ve mozzarella.", "Plenty of Turkish sausage and mozzarella.", 299, "🍕", 45, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
        ]),
        ("ozel", "Özel Pizzalar", "Signature Pizzas", "Şefin tarifleri", "Chef's recipes", [
            ("ton-balikli", "Ton Balıklı Pizza", "Tuna Pizza", "Ton balığı, mısır, kırmızı soğan ve mozzarella.", "Tuna, sweet corn, red onion and mozzarella.", 329, "🐟", 30, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("dort-peynirli", "Dört Peynirli Pizza", "Four Cheese Pizza", "Mozzarella, cheddar, parmesan ve rokfor.", "Mozzarella, cheddar, parmesan and blue cheese.", 339, "🧀", 30, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("vejetaryen", "Vejetaryen Pizza", "Vegetarian Pizza", "Közlenmiş sebzeler, mantar ve zeytin.", "Roasted vegetables, mushrooms and olives.", 289, "🥦", 30, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
        ]),
        ("yan-lezzetler", "Yan Lezzetler", "Sides", "Paylaşmalık", "To share", [
            ("sarimsakli-ekmek", "Sarımsaklı Ekmek", "Garlic Bread", "Tereyağlı sarımsaklı ekmek, kaşar ile.", "Buttery garlic bread with kashar cheese.", 89, "🥖", 60, None),
            ("tavuk-kanat", "Tavuk Kanat (6 Adet)", "Chicken Wings (6 pcs)", "Barbekü soslu fırın tavuk kanat.", "Oven-baked chicken wings with barbecue sauce.", 179, "🍗", 40, None),
        ]),
    ],
    "doner": [
        ("durumler", "Dürümler", "Wraps", "Lavaşta döner", "Doner in lavash", [
            ("tavuk-durum", "Tavuk Dürüm", "Chicken Wrap", "Lavaşta tavuk döner, domates, turşu ve patates.", "Chicken doner in lavash with tomato, pickles and fries.", 189, "🌯", 80, custom(extra=DONER_EXTRAS)),
            ("et-durum", "Et Dürüm", "Beef Wrap", "Lavaşta yaprak et döner, soğan ve domates.", "Sliced beef doner in lavash with onion and tomato.", 289, "🌯", 60, custom(extra=DONER_EXTRAS)),
        ]),
        ("porsiyonlar", "Porsiyonlar", "Plates", "Tabakta servis", "Served on a plate", [
            ("tavuk-porsiyon", "Tavuk Döner Porsiyon", "Chicken Doner Plate", "Pilav, patates ve közlenmiş biber ile.", "With rice, fries and roasted pepper.", 249, "🍽️", 40, None),
            ("et-porsiyon", "Et Döner Porsiyon", "Beef Doner Plate", "Pilav, salata ve közlenmiş domates ile.", "With rice, salad and roasted tomato.", 369, "🍽️", 35, None),
            ("iskender", "İskender", "Iskender Kebab", "Pide üzerinde et döner, tereyağı, domates sos ve yoğurt.", "Beef doner on pita with butter, tomato sauce and yoghurt.", 399, "🍖", 30, None),
        ]),
        ("ekmek-arasi", "Ekmek Arası", "Sandwiches", "Yarım ekmekte", "In half a loaf", [
            ("yarim-tavuk", "Yarım Ekmek Tavuk Döner", "Chicken Doner Sandwich", "Yarım ekmekte tavuk döner ve patates.", "Chicken doner and fries in half a loaf.", 169, "🥙", 60, custom(extra=DONER_EXTRAS)),
            ("yarim-et", "Yarım Ekmek Et Döner", "Beef Doner Sandwich", "Yarım ekmekte et döner, soğan ve domates.", "Beef doner with onion and tomato in half a loaf.", 259, "🥙", 50, custom(extra=DONER_EXTRAS)),
        ]),
        ("icecekler", "İçecekler", "Drinks", "Dönerin yanına", "Goes with doner", [
            ("ayran", "Ayran", "Ayran", "Köpüklü yayık ayranı, 300 ml.", "Frothy yoghurt drink, 300 ml.", 39, "🥛", 150, None),
            ("salgam", "Şalgam Suyu", "Turnip Juice", "Acılı veya acısız, 330 ml.", "Spicy or mild, 330 ml.", 45, "🥤", 100, None),
        ]),
    ],
    "salata": [
        ("salatalar", "Salatalar", "Salads", "Taze yeşillikler", "Fresh greens", [
            ("sezar", "Sezar Salata", "Caesar Salad", "Göbek marul, ızgara tavuk, kruton ve parmesan.", "Romaine, grilled chicken, croutons and parmesan.", 229, "🥗", 40, custom(extra=SALAD_EXTRAS)),
            ("ton-balikli", "Ton Balıklı Salata", "Tuna Salad", "Ton balığı, mısır, mevsim yeşillikleri ve limon sos.", "Tuna, sweet corn, seasonal greens and lemon dressing.", 259, "🐟", 30, custom(extra=SALAD_EXTRAS)),
            ("akdeniz", "Akdeniz Salatası", "Mediterranean Salad", "Domates, salatalık, zeytin, beyaz peynir ve zeytinyağı.", "Tomato, cucumber, olives, feta and olive oil.", 199, "🫒", 40, custom(extra=SALAD_EXTRAS)),
            ("hellim", "Hellim Salata", "Halloumi Salad", "Izgara hellim, roka ve nar ekşisi.", "Grilled halloumi, rocket and pomegranate molasses.", 239, "🧀", 30, custom(extra=SALAD_EXTRAS)),
        ]),
        ("bowllar", "Bowl'lar", "Bowls", "Doyurucu kaseler", "Hearty bowls", [
            ("tavuk-bowl", "Tavuk Bowl", "Chicken Bowl", "Esmer pirinç, ızgara tavuk, avokado ve edamame.", "Brown rice, grilled chicken, avocado and edamame.", 269, "🥣", 30, custom(extra=SALAD_EXTRAS)),
            ("somon-poke", "Somon Poke Bowl", "Salmon Poke Bowl", "Suşi pirinci, somon, mango ve susam sos.", "Sushi rice, salmon, mango and sesame dressing.", 389, "🍣", 20, None),
            ("falafel-bowl", "Falafel Bowl", "Falafel Bowl", "Falafel, humus, bulgur ve közlenmiş sebze.", "Falafel, hummus, bulgur and roasted vegetables.", 249, "🧆", 25, None),
            ("kinoa-bowl", "Kinoa Bowl", "Quinoa Bowl", "Kinoa, avokado, nohut ve nar.", "Quinoa, avocado, chickpeas and pomegranate.", 239, "🥑", 25, None),
        ]),
    ],
    "tatli": [
        ("pastalar", "Pastalar", "Cakes", "Dilim pastalar", "Cake slices", [
            ("cikolatali-pasta", "Çikolatalı Pasta", "Chocolate Cake", "Yoğun Belçika çikolatalı dilim pasta.", "Rich Belgian chocolate cake slice.", 179, "🍰", 25, None),
            ("frambuazli-pasta", "Frambuazlı Pasta", "Raspberry Cake", "Frambuaz ve vanilyalı krema ile hafif pasta.", "Light sponge with raspberries and vanilla cream.", 189, "🍰", 20, None),
            ("tiramisu", "Tiramisu", "Tiramisu", "Mascarpone, kahve ve kakao katmanları.", "Layers of mascarpone, coffee and cocoa.", 169, "🍮", 20, None),
        ]),
        ("cheesecakeler", "Cheesecake'ler", "Cheesecakes", "Kremamsı dilimler", "Creamy slices", [
            ("san-sebastian", "San Sebastian Cheesecake", "San Sebastian Cheesecake", "Yanık yüzeyli, akışkan Bask usulü cheesecake.", "Burnt Basque-style cheesecake with a soft centre.", 199, "🍰", 20, None),
            ("limonlu-cheesecake", "Limonlu Cheesecake", "Lemon Cheesecake", "Limon kremalı, bisküvi tabanlı cheesecake.", "Cheesecake with lemon curd on a biscuit base.", 179, "🍋", 20, None),
            ("frambuazli-cheesecake", "Frambuazlı Cheesecake", "Raspberry Cheesecake", "Frambuaz soslu klasik New York cheesecake.", "Classic New York cheesecake with raspberry sauce.", 189, "🍓", 20, None),
        ]),
        ("sutlu-tatlilar", "Sütlü Tatlılar", "Milk Desserts", "Geleneksel lezzetler", "Traditional favourites", [
            ("sutlac", "Fırın Sütlaç", "Baked Rice Pudding", "Fırında üstü kızarmış sütlaç.", "Oven-baked rice pudding with a golden top.", 99, "🍮", 30, None),
            ("kazandibi", "Kazandibi", "Kazandibi", "Karamelize tabanlı geleneksel muhallebi.", "Traditional milk pudding with a caramelized base.", 109, "🍮", 25, None),
            ("profiterol", "Profiterol", "Profiteroles", "Kremalı top hamurlar, bol çikolata sos.", "Cream-filled choux buns with chocolate sauce.", 139, "🍫", 25, None),
        ]),
    ],
    "dondurma": [
        ("top-dondurma", "Top Dondurma", "Scoops", "İki top, külah veya kase", "Two scoops, cone or cup", [
            ("maras", "Maraş Dondurması", "Maras Ice Cream", "Salep ve keçi sütü ile dövülmüş sakızlı dondurma.", "Stretchy mastic ice cream made with salep and goat milk.", 119, "🍨", 60, custom(extra=ICE_CREAM_EXTRAS)),
            ("cikolatali", "Çikolatalı Dondurma", "Chocolate Ice Cream", "İki top bitter çikolatalı dondurma.", "Two scoops of dark chocolate ice cream.", 109, "🍫", 60, custom(extra=ICE_CREAM_EXTRAS)),
            ("cilekli", "Çilekli Dondurma", "Strawberry Ice Cream", "Gerçek çilek parçacıklı iki top dondurma.", "Two scoops with real strawberry pieces.", 109, "🍓", 60, custom(extra=ICE_CREAM_EXTRAS)),
            ("limon-sorbe", "Limonlu Sorbe", "Lemon Sorbet", "Süt içermeyen ferahlatıcı limon sorbe.", "Refreshing dairy-free lemon sorbet.", 99, "🍋", 40, None),
        ]),
        ("milkshake", "Milkshake", "Milkshakes", "Buz gibi ve kremamsı", "Icy and creamy", [
            ("cikolatali-milkshake", "Çikolatalı Milkshake", "Chocolate Milkshake", "Çikolatalı dondurma ve soğuk süt.", "Chocolate ice cream blended with cold milk.", 149, "🥤", 50, custom(sides=food_sizes(20, 35))),
            ("cilekli-milkshake", "Çilekli Milkshake", "Strawberry Milkshake", "Çilekli dondurma ve taze çilek.", "Strawberry ice cream and fresh strawberries.", 149, "🍓", 50, custom(sides=food_sizes(20, 35))),
            ("muzlu-milkshake", "Muzlu Milkshake", "Banana Milkshake", "Muz, vanilyalı dondurma ve süt.", "Banana, vanilla ice cream and milk.", 149, "🍌", 50, custom(sides=food_sizes(20, 35))),
        ]),
        ("kup-sundae", "Kup & Sundae", "Cups & Sundaes", "Paylaşmalık kuplar", "Cups to share", [
            ("brownie-sundae", "Brownie Sundae", "Brownie Sundae", "Sıcak brownie, vanilyalı dondurma ve çikolata sos.", "Warm brownie, vanilla ice cream and chocolate sauce.", 189, "🍨", 30, None),
            ("meyveli-kup", "Meyveli Kup", "Fruit Cup", "Üç top dondurma, mevsim meyveleri ve krem şanti.", "Three scoops with seasonal fruit and whipped cream.", 169, "🍧", 30, None),
        ]),
    ],
    "firin": [
        ("simit-pogaca", "Simit & Poğaça", "Simit & Pastries", "Günlük taze", "Baked fresh daily", [
            ("simit", "Simit", "Simit", "Susamlı, çıtır İstanbul simidi.", "Crispy sesame-crusted Istanbul simit.", 25, "🥯", 150, None),
            ("peynirli-pogaca", "Peynirli Poğaça", "Cheese Pogaca", "Beyaz peynirli yumuşak poğaça.", "Soft pastry filled with white cheese.", 35, "🧀", 120, None),
            ("zeytinli-pogaca", "Zeytinli Poğaça", "Olive Pogaca", "Siyah zeytin ezmeli poğaça.", "Soft pastry with black olive paste.", 35, "🫒", 100, None),
        ]),
        ("kruvasan", "Kruvasan", "Croissants", "Tereyağlı katmanlar", "Buttery layers", [
            ("sade-kruvasan", "Sade Kruvasan", "Plain Croissant", "Fransız usulü tereyağlı kruvasan.", "French-style butter croissant.", 75, "🥐", 60, None),
            ("cikolatali-kruvasan", "Çikolatalı Kruvasan", "Chocolate Croissant", "Bitter çikolata dolgulu kruvasan.", "Croissant filled with dark chocolate.", 89, "🥐", 50, None),
            ("peynirli-kruvasan", "Peynirli Kruvasan", "Cheese Croissant", "Kaşar ve beyaz peynirli kruvasan.", "Croissant with kashar and white cheese.", 95, "🥐", 50, None),
        ]),
        ("borekler", "Börekler", "Borek", "Dilim börekler", "Borek by the slice", [
            ("su-boregi", "Su Böreği", "Water Borek", "Peynirli, ince yufkalı su böreği dilimi.", "Thin layered borek slice with cheese.", 119, "🥧", 40, None),
            ("ispanakli-borek", "Ispanaklı Börek", "Spinach Borek", "Ispanak ve beyaz peynirli kol böreği.", "Rolled borek with spinach and white cheese.", 99, "🥬", 40, None),
        ]),
    ],
    "icecek": [
        ("meyve-sulari", "Taze Meyve Suları", "Fresh Juices", "Anında sıkılır", "Squeezed to order", [
            ("portakal-suyu", "Taze Portakal Suyu", "Fresh Orange Juice", "Günlük sıkılmış portakal suyu.", "Freshly squeezed orange juice.", 99, "🍊", 80, custom(sides=food_sizes(20, 40))),
            ("nar-suyu", "Taze Nar Suyu", "Fresh Pomegranate Juice", "Mevsiminde sıkılmış nar suyu.", "Freshly squeezed pomegranate juice.", 119, "🧃", 50, custom(sides=food_sizes(25, 50))),
            ("havuc-suyu", "Havuç Suyu", "Carrot Juice", "Taze havuç ve bir dokunuş zencefil.", "Fresh carrot with a hint of ginger.", 89, "🥕", 50, custom(sides=food_sizes(20, 40))),
        ]),
        ("smoothie", "Smoothie'ler", "Smoothies", "Meyve ve yoğurt", "Fruit and yoghurt", [
            ("cilekli-smoothie", "Çilekli Smoothie", "Strawberry Smoothie", "Çilek, muz ve yoğurt.", "Strawberry, banana and yoghurt.", 139, "🍓", 50, custom(sides=food_sizes(20, 35))),
            ("muzlu-smoothie", "Muzlu Smoothie", "Banana Smoothie", "Muz, yulaf ve bal.", "Banana, oats and honey.", 139, "🍌", 50, custom(sides=food_sizes(20, 35))),
            ("mango-smoothie", "Mango Smoothie", "Mango Smoothie", "Mango, ananas ve hindistan cevizi sütü.", "Mango, pineapple and coconut milk.", 149, "🥭", 40, custom(sides=food_sizes(20, 35))),
        ]),
        ("limonatalar", "Limonatalar", "Lemonades", "Ev yapımı", "Homemade", [
            ("limonata", "Ev Yapımı Limonata", "Homemade Lemonade", "Taze limon ve hafif şeker.", "Fresh lemons, lightly sweetened.", 89, "🍋", 80, None),
            ("naneli-limonata", "Naneli Limonata", "Mint Lemonade", "Taze nane yapraklı limonata.", "Lemonade with fresh mint leaves.", 95, "🌿", 60, None),
        ]),
    ],
    "atistirmalik": [
        ("cipsler", "Cipsler", "Chips", "Çıtır paketler", "Crunchy packs", [
            ("patates-cipsi", "Patates Cipsi", "Potato Chips", "Klasik tuzlu patates cipsi, büyük boy.", "Classic salted potato chips, large pack.", 45, "🥔", 120, None),
            ("misir-cipsi", "Mısır Cipsi", "Corn Chips", "Baharatlı mısır cipsi, büyük boy.", "Spiced corn chips, large pack.", 49, "🌽", 100, None),
        ]),
        ("cikolata-biskuvi", "Çikolata & Bisküvi", "Chocolate & Biscuits", "Tatlı molası", "Sweet break", [
            ("sutlu-cikolata", "Sütlü Çikolata", "Milk Chocolate", "80 g sütlü tablet çikolata.", "80 g milk chocolate bar.", 45, "🍫", 100, None),
            ("bitter-cikolata", "Bitter Çikolata", "Dark Chocolate", "%70 kakaolu 80 g bitter çikolata.", "80 g dark chocolate, 70% cocoa.", 55, "🍫", 80, None),
            ("gofret", "Çikolatalı Gofret", "Chocolate Wafer", "Fındık kremalı çikolatalı gofret.", "Chocolate wafer with hazelnut cream.", 25, "🧇", 150, None),
            ("kurabiye", "Kurabiye Paketi", "Cookie Pack", "Damla çikolatalı kurabiye, 6'lı paket.", "Chocolate chip cookies, pack of 6.", 59, "🍪", 80, None),
        ]),
        ("su-icecek", "Su & Gazlı İçecek", "Water & Soft Drinks", "Serinleten seçenekler", "Cool refreshments", [
            ("su", "Su 0,5 L", "Water 0.5 L", "Doğal kaynak suyu.", "Natural spring water.", 15, "💧", 300, None),
            ("maden-suyu", "Maden Suyu", "Mineral Water", "Doğal mineralli maden suyu, 200 ml.", "Natural mineral water, 200 ml.", 25, "🫧", 200, None),
            ("kola", "Kola 330 ml", "Cola 330 ml", "Soğuk kutu kola.", "Chilled can of cola.", 55, "🥤", 200, None),
        ]),
    ],
    "giyim": [
        ("tisortler", "Tişörtler", "T-Shirts", "%100 pamuk", "100% cotton", [
            ("basic-tisort", "Basic Tişört", "Basic T-Shirt", "Bisiklet yaka, %100 pamuk basic tişört.", "Crew neck basic T-shirt, 100% cotton.", 399, "👕", 60, custom(sides=clothing_sizes())),
            ("oversize-tisort", "Oversize Tişört", "Oversize T-Shirt", "Düşük omuzlu, rahat kesim oversize tişört.", "Relaxed fit oversize T-shirt with dropped shoulders.", 499, "👕", 50, custom(sides=clothing_sizes())),
            ("baskili-tisort", "Baskılı Tişört", "Graphic T-Shirt", "MagicPay baskılı pamuklu tişört.", "Cotton T-shirt with a MagicPay print.", 549, "👕", 40, custom(sides=clothing_sizes())),
        ]),
        ("sweatshirt", "Sweatshirt & Hoodie", "Sweatshirts & Hoodies", "Yumuşak içi şardonlu", "Soft brushed fleece", [
            ("kapusonlu-sweatshirt", "Kapüşonlu Sweatshirt", "Hoodie", "Kanguru cepli, içi şardonlu kapüşonlu sweatshirt.", "Fleece-lined hoodie with kangaroo pocket.", 899, "🧥", 30, custom(sides=clothing_sizes())),
            ("bisiklet-yaka-sweatshirt", "Bisiklet Yaka Sweatshirt", "Crew Neck Sweatshirt", "Rahat kesim, ribanalı bisiklet yaka sweatshirt.", "Relaxed fit crew neck sweatshirt with ribbed cuffs.", 749, "🧥", 30, custom(sides=clothing_sizes())),
        ]),
        ("aksesuar", "Şapka & Aksesuar", "Caps & Accessories", "Tamamlayıcılar", "Finishing touches", [
            ("beyzbol-sapkasi", "Beyzbol Şapkası", "Baseball Cap", "Ayarlanabilir arka bantlı pamuklu şapka.", "Cotton cap with adjustable strap.", 299, "🧢", 40, None),
            ("bere", "Triko Bere", "Knit Beanie", "Yumuşak triko kışlık bere.", "Soft knit winter beanie.", 249, "🧶", 40, None),
            ("corap-seti", "Çorap Seti (3'lü)", "Sock Set (3 pairs)", "Pamuklu kolej çorap, 3 çift.", "Cotton crew socks, 3 pairs.", 149, "🧦", 60, None),
        ]),
    ],
    "ayakkabi": [
        ("sneaker", "Sneaker", "Sneakers", "Günlük konfor", "Everyday comfort", [
            ("beyaz-sneaker", "Beyaz Sneaker", "White Sneakers", "Deri görünümlü, kalın tabanlı beyaz sneaker.", "Leather-look white sneakers with a chunky sole.", 1899, "👟", 20, custom(sides=shoe_sizes())),
            ("kosu-ayakkabisi", "Koşu Ayakkabısı", "Running Shoes", "Hafif, nefes alan file koşu ayakkabısı.", "Lightweight breathable mesh running shoes.", 2299, "👟", 20, custom(sides=shoe_sizes())),
            ("kanvas-ayakkabi", "Kanvas Ayakkabı", "Canvas Shoes", "Bağcıklı klasik kanvas ayakkabı.", "Classic lace-up canvas shoes.", 1199, "👟", 25, custom(sides=shoe_sizes())),
        ]),
        ("terlik", "Terlik & Sandalet", "Slippers & Sandals", "Rahat adımlar", "Easy steps", [
            ("ev-terligi", "Ev Terliği", "House Slippers", "Yumuşak tabanlı peluş ev terliği.", "Plush house slippers with a soft sole.", 349, "🩴", 30, custom(sides=shoe_sizes())),
            ("plaj-terligi", "Plaj Terliği", "Flip-Flops", "Suya dayanıklı parmak arası terlik.", "Water-resistant flip-flops.", 299, "🩴", 30, custom(sides=shoe_sizes())),
        ]),
        ("cantalar", "Çantalar", "Bags", "Her güne bir çanta", "A bag for every day", [
            ("sirt-cantasi", "Sırt Çantası", "Backpack", "Laptop bölmeli, su itici sırt çantası.", "Water-repellent backpack with laptop sleeve.", 899, "🎒", 25, None),
            ("bel-cantasi", "Bel Çantası", "Belt Bag", "Ayarlanabilir kemerli çapraz bel çantası.", "Crossbody belt bag with adjustable strap.", 499, "👝", 25, None),
            ("bez-canta", "Bez Çanta", "Tote Bag", "Kalın kanvas, omuz askılı bez çanta.", "Heavy canvas tote bag with shoulder straps.", 199, "👜", 50, None),
        ]),
    ],
    "elektronik": [
        ("sarj-kablo", "Şarj & Kablo", "Chargers & Cables", "Hızlı şarj", "Fast charging", [
            ("20w-adaptor", "20W Hızlı Şarj Adaptörü", "20W Fast Charger", "USB-C çıkışlı, PD destekli 20W adaptör.", "20W USB-C power adapter with PD support.", 349, "🔌", 40, None),
            ("usb-c-kablo", "USB-C Kablo (1 m)", "USB-C Cable (1 m)", "Örgülü USB-C – USB-C hızlı şarj kablosu.", "Braided USB-C to USB-C fast charging cable.", 199, "🔌", 60, None),
            ("lightning-kablo", "Lightning Kablo (1 m)", "Lightning Cable (1 m)", "USB-C – Lightning şarj ve veri kablosu.", "USB-C to Lightning charge and sync cable.", 229, "🔌", 50, None),
            ("powerbank", "Powerbank 10.000 mAh", "Power Bank 10,000 mAh", "İnce kasa, çift çıkışlı taşınabilir şarj cihazı.", "Slim dual-output portable charger.", 699, "🔋", 30, None),
        ]),
        ("kulaklik-ses", "Kulaklık & Ses", "Headphones & Audio", "Müziğin keyfi", "Enjoy your music", [
            ("kablosuz-kulaklik", "Kablosuz Kulaklık", "Wireless Earbuds", "Şarj kutulu, dokunmatik kontrollü TWS kulaklık.", "True wireless earbuds with charging case and touch controls.", 1299, "🎧", 20, None),
            ("kablolu-kulaklik", "Kablolu Kulaklık", "Wired Earphones", "Mikrofonlu, USB-C girişli kulak içi kulaklık.", "In-ear earphones with mic and USB-C plug.", 299, "🎧", 40, None),
            ("bluetooth-hoparlor", "Bluetooth Hoparlör", "Bluetooth Speaker", "Suya dayanıklı, 12 saat pil ömürlü hoparlör.", "Water-resistant speaker with 12-hour battery.", 999, "🔊", 15, None),
        ]),
        ("telefon-aksesuar", "Telefon Aksesuarı", "Phone Accessories", "Koruma ve kullanım", "Protect and use", [
            ("telefon-tutucu", "Araç Telefon Tutucu", "Car Phone Holder", "Mıknatıslı, havalandırma ızgarasına takılan tutucu.", "Magnetic air-vent car phone holder.", 249, "📱", 30, None),
            ("ekran-koruyucu", "Ekran Koruyucu", "Screen Protector", "Temperli cam, çoğu modelle uyumlu.", "Tempered glass, fits most models.", 199, "📱", 40, None),
        ]),
    ],
    "kozmetik": [
        ("parfum", "Parfüm", "Perfume", "Kalıcı kokular", "Long-lasting scents", [
            ("eau-de-parfum", "Eau de Parfum 50 ml", "Eau de Parfum 50 ml", "Çiçeksi ve odunsu notalı kalıcı parfüm.", "Long-lasting perfume with floral and woody notes.", 1499, "🌸", 15, None),
            ("body-mist", "Body Mist 250 ml", "Body Mist 250 ml", "Hafif meyveli vücut spreyi.", "Light fruity body spray.", 399, "🌺", 25, None),
        ]),
        ("cilt-bakimi", "Cilt Bakımı", "Skin Care", "Günlük bakım", "Daily care", [
            ("nemlendirici", "Nemlendirici Krem", "Moisturizing Cream", "Hyaluronik asitli, 50 ml yüz kremi.", "50 ml face cream with hyaluronic acid.", 349, "🧴", 30, None),
            ("gunes-kremi", "Güneş Kremi SPF50", "Sunscreen SPF50", "Yüksek korumalı, yağsız güneş kremi.", "High protection, oil-free sunscreen.", 449, "☀️", 30, None),
            ("dudak-balmi", "Dudak Balmı", "Lip Balm", "Shea yağlı, nemlendirici dudak balmı.", "Moisturizing lip balm with shea butter.", 129, "💄", 50, None),
        ]),
        ("kisisel-bakim", "Kişisel Bakım", "Personal Care", "Yolda bakım", "Care on the go", [
            ("el-kremi", "El Kremi", "Hand Cream", "Hızlı emilen, 75 ml el kremi.", "Fast-absorbing hand cream, 75 ml.", 149, "🧴", 40, None),
            ("islak-mendil", "Islak Mendil", "Wet Wipes", "Alkolsüz, 72 yapraklı ıslak mendil.", "Alcohol-free wet wipes, 72 sheets.", 59, "🧻", 80, None),
            ("seyahat-sampuan", "Şampuan Seyahat Boy", "Travel Shampoo", "Tüm saç tipleri için 100 ml şampuan.", "100 ml shampoo for all hair types.", 99, "🧴", 50, None),
        ]),
    ],
    "kirtasiye": [
        ("kitaplar", "Kitaplar", "Books", "Okuma keyfi", "Reading pleasure", [
            ("roman", "Çok Satan Roman", "Bestselling Novel", "Haftanın çok satan Türkçe romanı.", "This week's bestselling Turkish novel.", 249, "📖", 20, None),
            ("cocuk-kitabi", "Resimli Çocuk Kitabı", "Picture Book", "3-7 yaş için resimli hikâye kitabı.", "Illustrated story book for ages 3-7.", 179, "📚", 25, None),
            ("bulmaca-kitabi", "Bulmaca Kitabı", "Puzzle Book", "Sudoku ve kare bulmaca derlemesi.", "A collection of sudoku and crosswords.", 99, "🧩", 30, None),
        ]),
        ("defter-ajanda", "Defter & Ajanda", "Notebooks & Planners", "Not almak için", "For your notes", [
            ("spiralli-defter", "Spiralli Defter A5", "A5 Spiral Notebook", "Çizgili, 100 yapraklı spiralli defter.", "Lined spiral notebook, 100 sheets.", 89, "📓", 50, None),
            ("ajanda", "Haftalık Ajanda", "Weekly Planner", "Ciltli kapak, tarihsiz haftalık ajanda.", "Hardcover undated weekly planner.", 299, "📔", 25, None),
        ]),
        ("kalemler", "Kalemler", "Pens & Pencils", "Yazmak ve çizmek için", "For writing and drawing", [
            ("tukenmez-kalem", "Tükenmez Kalem Seti", "Ballpoint Pen Set", "Mavi, siyah ve kırmızı, 3'lü set.", "Blue, black and red, set of 3.", 79, "🖊️", 60, None),
            ("kursun-kalem", "Kurşun Kalem Seti", "Pencil Set", "Silgili HB kurşun kalem, 6'lı set.", "HB pencils with erasers, set of 6.", 59, "✏️", 60, None),
            ("keceli-kalem", "Keçeli Kalem (12 Renk)", "Felt-Tip Pens (12 Colours)", "Yıkanabilir mürekkepli 12 renk keçeli kalem.", "12 washable felt-tip pens.", 129, "🖍️", 40, None),
        ]),
    ],
    "oyuncak": [
        ("oyuncaklar", "Oyuncaklar", "Toys", "Eğlenceli oyunlar", "Fun and games", [
            ("yapboz", "Yapboz (500 Parça)", "Jigsaw Puzzle (500 pcs)", "İstanbul manzaralı 500 parça yapboz.", "500-piece Istanbul landscape puzzle.", 299, "🧩", 20, None),
            ("oyuncak-araba", "Çek Bırak Araba", "Pull-Back Car", "Metal gövdeli çek bırak oyuncak araba.", "Die-cast pull-back toy car.", 249, "🚗", 30, None),
            ("zeka-kupu", "Zeka Küpü", "Puzzle Cube", "Klasik 3x3 hız küpü.", "Classic 3x3 speed cube.", 199, "🎲", 30, None),
        ]),
        ("peluslar", "Peluşlar", "Plush Toys", "Sarılmalık dostlar", "Cuddly friends", [
            ("pelus-ayi", "Peluş Ayı", "Teddy Bear", "35 cm yumuşak peluş ayıcık.", "35 cm soft teddy bear.", 349, "🧸", 20, None),
            ("pelus-tavsan", "Peluş Tavşan", "Plush Bunny", "Uzun kulaklı 30 cm peluş tavşan.", "30 cm plush bunny with long ears.", 299, "🐰", 20, None),
        ]),
        ("hediyelik", "Hediyelik Eşya", "Souvenirs", "Anı kalsın", "Keepsakes", [
            ("anahtarlik", "Nazar Boncuklu Anahtarlık", "Evil Eye Keychain", "El yapımı nazar boncuklu anahtarlık.", "Handmade evil eye keychain.", 99, "🔑", 60, None),
            ("magnet", "İstanbul Magneti", "Istanbul Magnet", "Galata Kulesi desenli buzdolabı magneti.", "Fridge magnet featuring Galata Tower.", 79, "🧲", 80, None),
            ("kupa", "Baskılı Kupa", "Printed Mug", "300 ml seramik hediye kupa.", "300 ml ceramic gift mug.", 199, "🎁", 30, None),
            ("kar-kuresi", "Kar Küresi", "Snow Globe", "Kız Kulesi figürlü kar küresi.", "Snow globe with a Maiden's Tower figure.", 249, "🔮", 20, None),
        ]),
    ],
    "cicek": [
        ("buketler", "Buketler", "Bouquets", "Taze kesme çiçekler", "Fresh cut flowers", [
            ("kirmizi-gul", "Kırmızı Gül Buketi (11 Adet)", "Red Rose Bouquet (11 stems)", "11 adet kırmızı gül, okaliptüs ile.", "11 red roses with eucalyptus.", 899, "🌹", 15, custom(extra=FLOWER_EXTRAS)),
            ("mevsim-buketi", "Mevsim Buketi", "Seasonal Bouquet", "Mevsimin renkli çiçeklerinden karışık buket.", "Mixed bouquet of colourful seasonal flowers.", 649, "💐", 15, custom(extra=FLOWER_EXTRAS)),
            ("lale-buketi", "Lale Buketi", "Tulip Bouquet", "10 adet renkli lale.", "10 colourful tulips.", 549, "🌷", 15, custom(extra=FLOWER_EXTRAS)),
            ("papatya-buketi", "Papatya Buketi", "Daisy Bouquet", "Kır papatyalarından sade buket.", "Simple bouquet of field daisies.", 399, "🌼", 15, custom(extra=FLOWER_EXTRAS)),
            ("aycicegi-buketi", "Ayçiçeği Buketi", "Sunflower Bouquet", "5 adet ayçiçeği, yeşilliklerle.", "5 sunflowers with greenery.", 499, "🌻", 15, custom(extra=FLOWER_EXTRAS)),
        ]),
        ("saksi", "Saksı Çiçekleri", "Potted Plants", "Uzun ömürlü", "Long-lasting", [
            ("orkide", "Beyaz Orkide", "White Orchid", "Seramik saksıda çift dallı beyaz orkide.", "Double-stem white orchid in a ceramic pot.", 1199, "🪴", 10, None),
            ("sukulent", "Sukulent", "Succulent", "Mini beton saksıda sukulent.", "Succulent in a mini concrete pot.", 199, "🌵", 25, None),
            ("baris-cicegi", "Barış Çiçeği", "Peace Lily", "Hava temizleyen barış çiçeği, 40 cm.", "Air-purifying peace lily, 40 cm.", 449, "🌿", 15, None),
        ]),
    ],
}


def _strip_en(customization: dict[str, Any]) -> dict[str, Any]:
    """Urun verisine yazilacak ozellestirme: *_en alanlari ceviri tablosuna gider."""
    result: dict[str, Any] = {}
    for step_id, step_data in customization.items():
        clean = {key: value for key, value in step_data.items() if key not in {"title_en", "options"}}
        clean["options"] = [
            {key: value for key, value in option.items() if key != "name_en"}
            for option in step_data.get("options", [])
        ]
        result[step_id] = clean
    return result


def build_section_catalog() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[str, str, str, str, str]]]:
    """(kategoriler, urunler, ingilizce ceviriler) dondurur.

    Kategori: {id, section_id, name, eyebrow, position, active}
    Urun: {id, section_id, category_id, position, active, data}
    Ceviri: (entity_type, entity_id, field, turkce kaynak, ingilizce metin).
    """
    categories: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    translations: list[tuple[str, str, str, str, str]] = []
    for section_id, groups in CATALOG.items():
        product_position = 0
        for category_position, (slug, name, name_en, eyebrow, eyebrow_en, items) in enumerate(groups):
            category_id = f"{section_id}-{slug}"
            categories.append({
                "id": category_id,
                "section_id": section_id,
                "name": name,
                "eyebrow": eyebrow,
                "position": category_position,
                "active": True,
            })
            translations.append(("category", category_id, "name", name, name_en))
            translations.append(("category", category_id, "eyebrow", eyebrow, eyebrow_en))
            for item_index, (product_slug, pname, pname_en, desc, desc_en, price, emoji, stock, customization) in enumerate(items):
                product_id = f"{section_id}-{product_slug}"
                steps = customization or base_steps()
                customizable = any(item.get("enabled") for item in steps.values())
                data = {
                    "id": product_id,
                    "sectionId": section_id,
                    "categoryId": category_id,
                    "name": pname,
                    "description": desc,
                    "price": float(price),
                    "kind": "simple",
                    "image": "",
                    "emoji": emoji,
                    "customizable": customizable,
                    "popular": item_index == 0,
                    "active": True,
                    "position": product_position,
                    "stockQuantity": int(stock),
                    "criticalStock": max(3, int(stock) // 10),
                    "stockTrackingEnabled": True,
                    "stockSellable": True,
                    "customization": _strip_en(steps),
                }
                products.append({
                    "id": product_id,
                    "section_id": section_id,
                    "category_id": category_id,
                    "position": product_position,
                    "active": True,
                    "data": data,
                })
                product_position += 1
                translations.append(("product", product_id, "name", pname, pname_en))
                translations.append(("product", product_id, "description", desc, desc_en))
                for step_id, step_data in steps.items():
                    if not step_data.get("enabled"):
                        continue
                    translations.append(("product", product_id, f"customization.{step_id}.title", step_data["title"], step_data["title_en"]))
                    for option in step_data.get("options", []):
                        translations.append((
                            "product", product_id, f"customization.{step_id}.options.{option['id']}.name", option["name"], option["name_en"],
                        ))
    return categories, products, translations
