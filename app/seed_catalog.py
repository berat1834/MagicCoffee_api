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


def colors(*items: tuple[str, str, str]) -> dict[str, Any]:
    """Istege bagli renk secimi (`drinks` adiminda; kiosk bu adim icin kendi basligini gosterir)."""
    return step("Renk Seçimi", "Color", [opt(color_id, name, name_en) for color_id, name, name_en in items])


COLORS_BASIC = colors(("siyah", "Siyah", "Black"), ("beyaz", "Beyaz", "White"), ("lacivert", "Lacivert", "Navy"), ("gri", "Gri", "Grey"))
COLORS_TECH = colors(("siyah", "Siyah", "Black"), ("beyaz", "Beyaz", "White"), ("mavi", "Mavi", "Blue"))
COLORS_BAG = colors(("siyah", "Siyah", "Black"), ("kahverengi", "Kahverengi", "Brown"), ("haki", "Haki", "Khaki"))
COLORS_LIP = colors(("kirmizi", "Kırmızı", "Red"), ("nude", "Nude", "Nude"), ("pembe", "Pembe", "Pink"))
DRINK_SIZES = food_sizes(15, 30)

# Ikinci tohum dalgasi (migrasyon v2): mevcut alt kategorilere eklenen urunler ve yeni alt kategoriler.
# Ilk tohumdaki urun kimlikleri degismez; ayni slug'li alt kategoriye urunler eklenir.
EXTRA_CATALOG: dict[str, list[tuple[str, str, str, str, str, list[P]]]] = {
    "burger": [
        ("klasik", "Klasik Burgerler", "Classic Burgers", "Izgara dana köfte", "Grilled beef patties", [
            ("bbq-burger", "BBQ Burger", "BBQ Burger", "Füme barbekü sos, çıtır soğan ve cheddar.", "Smoky BBQ sauce, crispy onions and cheddar.", 369, "🥓", 40, custom(extra=BURGER_EXTRAS)),
            ("truflu-burger", "Trüflü Burger", "Truffle Burger", "Trüf mayonez, roka ve parmesan ile dana köfte.", "Beef patty with truffle mayo, rocket and parmesan.", 459, "🌰", 25, custom(extra=BURGER_EXTRAS)),
            ("smash-burger", "Smash Burger", "Smash Burger", "İnce ezilmiş iki köfte ve amerikan peyniri.", "Two thin smashed patties with American cheese.", 379, "🥩", 40, custom(extra=BURGER_EXTRAS)),
        ]),
        ("tavuk", "Tavuk Burgerler", "Chicken Burgers", "Çıtır tavuk lezzetleri", "Crispy chicken favourites", [
            ("schnitzel-burger", "Schnitzel Burger", "Schnitzel Burger", "Pane tavuk göğsü, turşu ve hardallı mayonez.", "Breaded chicken breast, pickles and mustard mayo.", 299, "🐔", 40, custom(extra=BURGER_EXTRAS)),
            ("balli-hardalli-tavuk", "Ballı Hardallı Tavuk Burger", "Honey Mustard Chicken Burger", "Izgara tavuk, ballı hardal sos ve marul.", "Grilled chicken, honey mustard sauce and lettuce.", 289, "🍯", 40, custom(extra=BURGER_EXTRAS)),
            ("tavuk-wrap", "Tavuk Wrap", "Chicken Wrap", "Lavaşta çıtır tavuk, coleslaw ve ranch sos.", "Crispy chicken, coleslaw and ranch in a wrap.", 249, "🌯", 40, None),
        ]),
        ("yan-urunler", "Yan Ürünler", "Sides", "Burgerin yanına", "Perfect with a burger", [
            ("cheddar-patates", "Cheddar Soslu Patates", "Cheddar Fries", "Patates kızartması, sıcak cheddar sos ile.", "French fries topped with warm cheddar sauce.", 139, "🧀", 80, custom(sides=food_sizes(20, 40))),
            ("tavuk-nugget", "Tavuk Nugget (8 Adet)", "Chicken Nuggets (8 pcs)", "Çıtır tavuk parçaları, ballı hardal sos ile.", "Crispy chicken bites with honey mustard dip.", 159, "🍤", 60, None),
            ("coleslaw", "Coleslaw Salata", "Coleslaw", "Lahana, havuç ve hafif mayonezli sos.", "Cabbage and carrot in a light mayo dressing.", 69, "🥬", 50, None),
            ("vanilyali-milkshake", "Vanilyalı Milkshake", "Vanilla Milkshake", "Vanilyalı dondurma ve soğuk süt.", "Vanilla ice cream blended with cold milk.", 129, "🥤", 50, custom(sides=DRINK_SIZES)),
        ]),
        ("vejetaryen", "Vejetaryen Burgerler", "Veggie Burgers", "Etsiz lezzetler", "Meat-free favourites", [
            ("vegan-burger", "Vegan Burger", "Vegan Burger", "Bitkisel köfte, avokado ve vegan mayonez.", "Plant-based patty, avocado and vegan mayo.", 329, "🌱", 30, None),
            ("hellim-burger", "Hellim Burger", "Halloumi Burger", "Izgara hellim, közlenmiş biber ve pesto.", "Grilled halloumi, roasted pepper and pesto.", 299, "🫑", 30, None),
            ("falafel-burger", "Falafel Burger", "Falafel Burger", "Falafel köfte, humus ve tahin sos.", "Falafel patty, hummus and tahini sauce.", 269, "🧆", 30, None),
        ]),
        ("menuler", "Menüler", "Meals", "Burger + patates + içecek", "Burger + fries + drink", [
            ("klasik-menu", "Klasik Burger Menü", "Classic Burger Meal", "Klasik burger, patates kızartması ve içecek.", "Classic burger with fries and a drink.", 389, "🍱", 50, custom(sides=food_sizes(40, 70))),
            ("tavuk-menu", "Tavuk Burger Menü", "Chicken Burger Meal", "Crispy tavuk burger, patates ve içecek.", "Crispy chicken burger with fries and a drink.", 369, "🥡", 50, custom(sides=food_sizes(40, 70))),
            ("cocuk-menu", "Çocuk Menü", "Kids Meal", "Mini burger, küçük patates ve meyve suyu.", "Mini burger, small fries and fruit juice.", 229, "🧒", 40, None),
        ]),
    ],
    "pizza": [
        ("klasik", "Klasik Pizzalar", "Classic Pizzas", "Taş fırından", "Fresh from the stone oven", [
            ("pepperoni", "Pepperoni Pizza", "Pepperoni Pizza", "Bol pepperoni ve mozzarella.", "Plenty of pepperoni and mozzarella.", 309, "🌶️", 45, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("mantarli", "Mantarlı Pizza", "Mushroom Pizza", "Kültür mantarı, mozzarella ve kekik.", "Button mushrooms, mozzarella and thyme.", 279, "🍄", 40, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("sebzeli-margarita", "Domatesli Burrata Pizza", "Tomato Burrata Pizza", "Çeri domates, burrata ve fesleğen.", "Cherry tomatoes, burrata and basil.", 349, "🍅", 25, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
        ]),
        ("ozel", "Özel Pizzalar", "Signature Pizzas", "Şefin tarifleri", "Chef's recipes", [
            ("barbeku-tavuk", "Barbekü Tavuklu Pizza", "BBQ Chicken Pizza", "Barbekü soslu tavuk, mısır ve kırmızı soğan.", "BBQ chicken, sweet corn and red onion.", 339, "🔥", 30, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("pastirmali", "Pastırmalı Pizza", "Pastirma Pizza", "Kayseri pastırması, kaşar ve közlenmiş biber.", "Cured beef pastirma, kashar and roasted pepper.", 359, "🥓", 25, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
            ("akdeniz", "Akdeniz Pizza", "Mediterranean Pizza", "Beyaz peynir, zeytin, roka ve kurutulmuş domates.", "Feta, olives, rocket and sun-dried tomatoes.", 319, "🫒", 25, custom(food_sizes(60, 120), PIZZA_EXTRAS)),
        ]),
        ("yan-lezzetler", "Yan Lezzetler", "Sides", "Paylaşmalık", "To share", [
            ("patates-wedges", "Baharatlı Patates", "Potato Wedges", "Fırında baharatlı elma dilim patates.", "Oven-baked spiced potato wedges.", 99, "🥔", 60, None),
            ("mozzarella-stick", "Mozzarella Stick (6 Adet)", "Mozzarella Sticks (6 pcs)", "Çıtır kaplamalı mozzarella, domates sos ile.", "Crispy mozzarella sticks with tomato dip.", 129, "🫕", 50, None),
            ("sezar-salata", "Sezar Salata", "Caesar Salad", "Göbek marul, kruton ve parmesan.", "Romaine, croutons and parmesan.", 159, "🥗", 30, None),
        ]),
        ("pideler", "Pideler", "Turkish Pide", "Odun ateşinde", "Wood-fired", [
            ("kiymali-pide", "Kıymalı Pide", "Minced Meat Pide", "Dana kıyma, domates ve biber.", "Minced beef, tomato and pepper.", 229, "🫓", 40, None),
            ("kasarli-pide", "Kaşarlı Pide", "Cheese Pide", "Bol kaşarlı, tereyağlı pide.", "Buttery pide with plenty of kashar cheese.", 199, "🧈", 40, None),
            ("kusbasili-pide", "Kuşbaşılı Pide", "Diced Beef Pide", "Kuşbaşı dana eti ve közlenmiş domates.", "Diced beef and roasted tomato.", 279, "🥩", 30, None),
            ("karisik-pide", "Karışık Pide", "Mixed Pide", "Kıyma, kaşar, sucuk ve yumurta.", "Minced beef, kashar, sucuk and egg.", 259, "🍳", 30, None),
        ]),
        ("tatli-icecek", "Tatlı & İçecek", "Desserts & Drinks", "Yemeğin tamamlayıcısı", "To finish your meal", [
            ("tiramisu", "Tiramisu", "Tiramisu", "Mascarpone ve kahveli İtalyan tatlısı.", "Italian dessert with mascarpone and coffee.", 149, "🍰", 30, None),
            ("kola-1l", "Kola 1 L", "Cola 1 L", "Paylaşmalık soğuk kola.", "Chilled cola to share.", 79, "🥤", 80, None),
            ("limonata", "Limonata", "Lemonade", "Ev yapımı taze limonata.", "Fresh homemade lemonade.", 69, "🍋", 60, None),
        ]),
    ],
    "doner": [
        ("durumler", "Dürümler", "Wraps", "Lavaşta döner", "Doner in lavash", [
            ("acili-tavuk-durum", "Acılı Tavuk Dürüm", "Spicy Chicken Wrap", "Acı soslu tavuk döner, közlenmiş biber.", "Spicy chicken doner with roasted pepper.", 199, "🌶️", 60, custom(extra=DONER_EXTRAS)),
            ("kasarli-et-durum", "Kaşarlı Et Dürüm", "Cheesy Beef Wrap", "Et döner ve eritilmiş kaşar peyniri.", "Beef doner with melted kashar cheese.", 309, "🧀", 50, custom(extra=DONER_EXTRAS)),
            ("falafel-durum", "Falafel Dürüm", "Falafel Wrap", "Falafel, humus, turşu ve yeşillik.", "Falafel, hummus, pickles and greens.", 179, "🧆", 40, None),
        ]),
        ("porsiyonlar", "Porsiyonlar", "Plates", "Tabakta servis", "Served on a plate", [
            ("pilav-ustu-tavuk", "Pilav Üstü Tavuk Döner", "Chicken Doner on Rice", "Tereyağlı pilav üzerinde tavuk döner.", "Chicken doner on buttery rice.", 269, "🍚", 40, None),
            ("beyti", "Beyti Sarma", "Beyti Kebab", "Lavaşa sarılı kebap, yoğurt ve domates sos.", "Kebab wrapped in lavash with yoghurt and tomato sauce.", 389, "🍢", 25, None),
            ("mega-et-porsiyon", "Mega Et Döner Porsiyon", "Mega Beef Doner Plate", "1,5 porsiyon et döner, pilav ve salata.", "1.5 portions of beef doner with rice and salad.", 489, "🥘", 20, None),
        ]),
        ("ekmek-arasi", "Ekmek Arası", "Sandwiches", "Yarım ekmekte", "In half a loaf", [
            ("tombik-tavuk", "Tombik Tavuk Döner", "Chicken Doner in Tombik", "Tombik ekmekte tavuk döner ve patates.", "Chicken doner and fries in tombik bread.", 189, "🍞", 50, custom(extra=DONER_EXTRAS)),
            ("tombik-et", "Tombik Et Döner", "Beef Doner in Tombik", "Tombik ekmekte et döner, domates ve soğan.", "Beef doner with tomato and onion in tombik bread.", 279, "🥖", 40, custom(extra=DONER_EXTRAS)),
            ("ekmek-arasi-kofte", "Ekmek Arası Köfte", "Meatball Sandwich", "Izgara köfte, piyaz ve közlenmiş biber.", "Grilled meatballs with bean salad and roasted pepper.", 219, "🍡", 40, None),
        ]),
        ("icecekler", "İçecekler", "Drinks", "Dönerin yanına", "Goes with doner", [
            ("kola", "Kola 330 ml", "Cola 330 ml", "Soğuk kutu kola.", "Chilled can of cola.", 55, "🧊", 120, None),
            ("meyve-suyu", "Meyve Suyu", "Fruit Juice", "Şeftali veya vişne, 200 ml.", "Peach or sour cherry, 200 ml.", 45, "🧃", 100, None),
        ]),
        ("yan-urunler", "Yan Ürünler", "Sides", "Yanında iyi gider", "Great on the side", [
            ("patates", "Patates Kızartması", "French Fries", "Çıtır patates kızartması.", "Crispy French fries.", 89, "🍟", 80, custom(sides=food_sizes(20, 40))),
            ("mercimek-corbasi", "Mercimek Çorbası", "Lentil Soup", "Limon ve kıtır ekmek ile.", "With lemon and croutons.", 99, "🥣", 40, None),
            ("kozlenmis-biber", "Közlenmiş Biber", "Roasted Peppers", "Sarımsaklı yoğurtlu közlenmiş biber.", "Roasted peppers with garlic yoghurt.", 49, "🫑", 40, None),
            ("cacik", "Cacık", "Cacik", "Yoğurt, salatalık ve nane.", "Yoghurt with cucumber and mint.", 59, "🥒", 40, None),
        ]),
    ],
    "salata": [
        ("salatalar", "Salatalar", "Salads", "Taze yeşillikler", "Fresh greens", [
            ("coban", "Çoban Salata", "Shepherd's Salad", "Domates, salatalık, biber ve maydanoz.", "Tomato, cucumber, pepper and parsley.", 139, "🍅", 40, custom(extra=SALAD_EXTRAS)),
            ("roka", "Roka Salata", "Rocket Salad", "Roka, parmesan ve balzamik sos.", "Rocket, parmesan and balsamic dressing.", 169, "🌿", 40, custom(extra=SALAD_EXTRAS)),
        ]),
        ("bowllar", "Bowl'lar", "Bowls", "Doyurucu kaseler", "Hearty bowls", [
            ("meksika-bowl", "Meksika Bowl", "Mexican Bowl", "Fasulye, mısır, avokado ve salsa.", "Beans, corn, avocado and salsa.", 279, "🌮", 25, custom(extra=SALAD_EXTRAS)),
            ("kofte-bowl", "Köfte Bowl", "Meatball Bowl", "Izgara köfte, bulgur pilavı ve közlenmiş sebze.", "Grilled meatballs, bulgur and roasted vegetables.", 289, "🍖", 25, None),
        ]),
        ("wrapler", "Wrap'ler", "Wraps", "Elde taşınır", "On the go", [
            ("tavuk-wrap", "Izgara Tavuk Wrap", "Grilled Chicken Wrap", "Izgara tavuk, yeşillik ve yoğurtlu sos.", "Grilled chicken, greens and yoghurt sauce.", 219, "🌯", 30, None),
            ("ton-wrap", "Ton Balıklı Wrap", "Tuna Wrap", "Ton balığı, mısır ve marul.", "Tuna, sweet corn and lettuce.", 229, "🐠", 25, None),
            ("vegan-wrap", "Vegan Wrap", "Vegan Wrap", "Humus, ızgara sebze ve roka.", "Hummus, grilled vegetables and rocket.", 199, "🥬", 25, None),
            ("hellim-wrap", "Hellim Wrap", "Halloumi Wrap", "Izgara hellim, domates ve pesto.", "Grilled halloumi, tomato and pesto.", 209, "🧈", 25, None),
        ]),
        ("corbalar", "Çorbalar", "Soups", "Günün çorbaları", "Soups of the day", [
            ("mercimek", "Mercimek Çorbası", "Lentil Soup", "Kırmızı mercimek, limon ile.", "Red lentil soup with lemon.", 99, "🫘", 40, None),
            ("domates", "Domates Çorbası", "Tomato Soup", "Kaşar rendeli domates çorbası.", "Tomato soup with grated kashar.", 109, "🥫", 30, None),
            ("brokoli", "Brokoli Çorbası", "Broccoli Soup", "Kremalı brokoli çorbası.", "Creamy broccoli soup.", 119, "🥦", 30, None),
            ("tavuk-suyu", "Tavuk Suyu Çorba", "Chicken Soup", "Şehriyeli tavuk suyu çorba.", "Chicken soup with vermicelli.", 109, "🍲", 30, None),
        ]),
        ("detoks-icecek", "Detoks & İçecek", "Detox & Drinks", "Hafif ve ferah", "Light and fresh", [
            ("yesil-detoks", "Yeşil Detoks", "Green Detox", "Ispanak, elma, salatalık ve limon.", "Spinach, apple, cucumber and lemon.", 129, "🍏", 30, custom(sides=DRINK_SIZES)),
            ("kirmizi-detoks", "Kırmızı Detoks", "Red Detox", "Pancar, havuç, elma ve zencefil.", "Beetroot, carrot, apple and ginger.", 129, "🫐", 30, custom(sides=DRINK_SIZES)),
            ("kombucha", "Kombucha", "Kombucha", "Fermente çay, 330 ml.", "Fermented tea, 330 ml.", 99, "🫖", 40, None),
            ("ayran", "Ayran", "Ayran", "Köpüklü ayran, 300 ml.", "Frothy yoghurt drink, 300 ml.", 39, "🥛", 80, None),
        ]),
    ],
    "tatli": [
        ("pastalar", "Pastalar", "Cakes", "Dilim pastalar", "Cake slices", [
            ("karamelli-pasta", "Karamelli Pasta", "Caramel Cake", "Tuzlu karamel kremalı dilim pasta.", "Cake slice with salted caramel cream.", 189, "🍯", 20, None),
            ("havuclu-kek", "Havuçlu Tarçınlı Kek", "Carrot Cake", "Cevizli havuç keki, krem peynirli.", "Walnut carrot cake with cream cheese frosting.", 149, "🥕", 20, None),
            ("red-velvet", "Red Velvet", "Red Velvet", "Kadife dokulu kırmızı pasta.", "Velvety red cake with cream cheese.", 199, "❤️", 20, None),
        ]),
        ("cheesecakeler", "Cheesecake'ler", "Cheesecakes", "Kremamsı dilimler", "Creamy slices", [
            ("oreo-cheesecake", "Oreo Cheesecake", "Oreo Cheesecake", "Kakaolu bisküvi parçacıklı cheesecake.", "Cheesecake with cocoa cookie crumbs.", 199, "🍪", 20, None),
            ("lotus-cheesecake", "Lotus Cheesecake", "Lotus Cheesecake", "Karamelize bisküvi kremalı cheesecake.", "Cheesecake with caramelized biscuit cream.", 209, "🧁", 20, None),
        ]),
        ("sutlu-tatlilar", "Sütlü Tatlılar", "Milk Desserts", "Geleneksel lezzetler", "Traditional favourites", [
            ("supangle", "Supangle", "Supangle", "Bol çikolatalı puding, kek tabanlı.", "Rich chocolate pudding on a sponge base.", 119, "🍩", 25, None),
            ("keskul", "Keşkül", "Keskul", "Bademli ve hindistan cevizli muhallebi.", "Almond and coconut milk pudding.", 109, "🥥", 25, None),
        ]),
        ("serbetli", "Şerbetli Tatlılar", "Syrup Desserts", "Usta işi", "Master-made", [
            ("baklava", "Fıstıklı Baklava (4 Dilim)", "Pistachio Baklava (4 pcs)", "Antep fıstıklı çıtır baklava.", "Crispy baklava with Antep pistachios.", 249, "🥮", 30, None),
            ("kunefe", "Künefe", "Kunefe", "Hatay peynirli, sıcak servis.", "Served hot with Hatay cheese.", 229, "🔥", 20, None),
            ("sobiyet", "Şöbiyet (4 Dilim)", "Sobiyet (4 pcs)", "Kaymak ve fıstık dolgulu şöbiyet.", "Filled with clotted cream and pistachio.", 239, "🥟", 25, None),
            ("kadayif", "Tel Kadayıf", "Kadayif", "Cevizli tel kadayıf.", "Shredded pastry with walnuts.", 199, "🪺", 25, None),
        ]),
        ("kurabiye-kek", "Kurabiye & Kek", "Cookies & Cakes", "Çayın yanına", "With your tea", [
            ("brownie", "Brownie", "Brownie", "Cevizli, yoğun çikolatalı brownie.", "Fudgy chocolate brownie with walnuts.", 119, "🟫", 30, None),
            ("cookie", "Damla Çikolatalı Cookie", "Chocolate Chip Cookie", "Büyük boy, yumuşak cookie.", "Large soft cookie.", 69, "🥠", 40, None),
            ("muffin", "Yaban Mersinli Muffin", "Blueberry Muffin", "Yaban mersinli yumuşak muffin.", "Soft muffin with blueberries.", 89, "🫐", 30, None),
            ("makaron", "Makaron (6'lı)", "Macarons (6 pcs)", "Karışık aromalı Fransız makaronu.", "Assorted French macarons.", 159, "🌈", 25, None),
        ]),
    ],
    "dondurma": [
        ("top-dondurma", "Top Dondurma", "Scoops", "İki top, külah veya kase", "Two scoops, cone or cup", [
            ("antep-fistikli", "Antep Fıstıklı Dondurma", "Pistachio Ice Cream", "İki top fıstıklı dondurma.", "Two scoops of pistachio ice cream.", 129, "🟢", 50, custom(extra=ICE_CREAM_EXTRAS)),
            ("vanilyali", "Vanilyalı Dondurma", "Vanilla Ice Cream", "İki top Madagaskar vanilyalı dondurma.", "Two scoops of Madagascar vanilla.", 99, "🍦", 60, custom(extra=ICE_CREAM_EXTRAS)),
            ("karamelli", "Tuzlu Karamelli Dondurma", "Salted Caramel Ice Cream", "İki top tuzlu karamel dondurma.", "Two scoops of salted caramel.", 109, "🍮", 50, custom(extra=ICE_CREAM_EXTRAS)),
        ]),
        ("milkshake", "Milkshake", "Milkshakes", "Buz gibi ve kremamsı", "Icy and creamy", [
            ("oreo-milkshake", "Oreo Milkshake", "Oreo Milkshake", "Kakaolu bisküvi ve vanilyalı dondurma.", "Cocoa cookies with vanilla ice cream.", 159, "🍪", 40, custom(sides=food_sizes(20, 35))),
            ("karamel-milkshake", "Karamelli Milkshake", "Caramel Milkshake", "Karamel sos ve vanilyalı dondurma.", "Caramel sauce with vanilla ice cream.", 155, "🍯", 40, custom(sides=food_sizes(20, 35))),
        ]),
        ("kup-sundae", "Kup & Sundae", "Cups & Sundaes", "Paylaşmalık kuplar", "Cups to share", [
            ("banana-split", "Banana Split", "Banana Split", "Muz, üç top dondurma ve çikolata sos.", "Banana, three scoops and chocolate sauce.", 199, "🍒", 25, None),
            ("profiterollu-kup", "Profiterollü Kup", "Profiterole Cup", "Profiterol, dondurma ve sıcak çikolata.", "Profiteroles, ice cream and hot chocolate.", 189, "🎂", 25, None),
        ]),
        ("cubuk-kulah", "Çubuk & Külah", "Sticks & Cones", "Hazır paket", "Ready to go", [
            ("cikolata-cubuk", "Çikolata Kaplı Çubuk", "Chocolate-Coated Stick", "Bademli çikolata kaplı çubuk dondurma.", "Ice cream stick coated in almond chocolate.", 69, "🍡", 80, None),
            ("meyveli-buz", "Meyveli Buz", "Fruit Ice Pop", "Çilek ve limonlu buz dondurma.", "Strawberry and lemon ice pop.", 49, "🧊", 80, None),
            ("kulah", "Külah Dondurma", "Ice Cream Cone", "Vanilya-çikolata külah dondurma.", "Vanilla and chocolate cone.", 59, "🍥", 80, None),
            ("sandvic-dondurma", "Sandviç Dondurma", "Ice Cream Sandwich", "İki bisküvi arasında vanilyalı dondurma.", "Vanilla ice cream between two biscuits.", 79, "🥪", 60, None),
        ]),
        ("frozen-yogurt", "Frozen Yoğurt", "Frozen Yoghurt", "Hafif ve ferah", "Light and fresh", [
            ("sade-frozen", "Sade Frozen Yoğurt", "Plain Frozen Yoghurt", "Ekşi aromalı sade frozen yoğurt.", "Tangy plain frozen yoghurt.", 119, "🥛", 40, custom(extra=ICE_CREAM_EXTRAS)),
            ("cilekli-frozen", "Çilekli Frozen Yoğurt", "Strawberry Frozen Yoghurt", "Taze çilekli frozen yoğurt.", "Frozen yoghurt with fresh strawberries.", 129, "❄️", 40, custom(extra=ICE_CREAM_EXTRAS)),
            ("yaban-mersinli-frozen", "Yaban Mersinli Frozen Yoğurt", "Blueberry Frozen Yoghurt", "Yaban mersini soslu frozen yoğurt.", "Frozen yoghurt with blueberry sauce.", 135, "🫐", 40, custom(extra=ICE_CREAM_EXTRAS)),
            ("granola-frozen", "Granolalı Frozen Yoğurt", "Granola Frozen Yoghurt", "Bal ve granola ile frozen yoğurt.", "Frozen yoghurt with honey and granola.", 139, "🥣", 40, custom(extra=ICE_CREAM_EXTRAS)),
        ]),
    ],
    "firin": [
        ("simit-pogaca", "Simit & Poğaça", "Simit & Pastries", "Günlük taze", "Baked fresh daily", [
            ("acma", "Açma", "Acma", "Yumuşak, tereyağlı açma.", "Soft buttery ring bread.", 25, "🥨", 100, None),
            ("sade-pogaca", "Sade Poğaça", "Plain Pogaca", "Yumuşacık sade poğaça.", "Soft plain pastry.", 30, "🍘", 100, None),
            ("dereotlu-pogaca", "Dereotlu Poğaça", "Dill Pogaca", "Dereotlu ve peynirli poğaça.", "Pastry with dill and cheese.", 35, "🌿", 80, None),
        ]),
        ("kruvasan", "Kruvasan", "Croissants", "Tereyağlı katmanlar", "Buttery layers", [
            ("bademli-kruvasan", "Bademli Kruvasan", "Almond Croissant", "Badem kremalı, dilim bademli kruvasan.", "Croissant with almond cream and flakes.", 99, "🌰", 40, None),
            ("fistikli-kruvasan", "Fıstıklı Kruvasan", "Pistachio Croissant", "Antep fıstığı kremalı kruvasan.", "Croissant with pistachio cream.", 109, "🥜", 40, None),
        ]),
        ("borekler", "Börekler", "Borek", "Dilim börekler", "Borek by the slice", [
            ("patatesli-borek", "Patatesli Börek", "Potato Borek", "Baharatlı patatesli kol böreği.", "Rolled borek with spiced potato.", 99, "🥔", 40, None),
            ("kiymali-borek", "Kıymalı Börek", "Minced Meat Borek", "Kıymalı tepsi böreği dilimi.", "Tray borek slice with minced meat.", 119, "🥩", 40, None),
            ("sigara-boregi", "Sigara Böreği (4 Adet)", "Cheese Rolls (4 pcs)", "Peynirli çıtır sigara böreği.", "Crispy cheese-filled rolls.", 89, "🌯", 50, None),
        ]),
        ("ekmekler", "Ekmekler", "Breads", "Taş fırın ekmekleri", "Stone-baked breads", [
            ("eksi-mayali", "Ekşi Mayalı Ekmek", "Sourdough Bread", "Uzun fermantasyonlu ekşi maya ekmeği.", "Long-fermented sourdough loaf.", 89, "🍞", 30, None),
            ("tam-bugday", "Tam Buğday Ekmeği", "Whole Wheat Bread", "Lif zengini tam buğday ekmeği.", "Fibre-rich whole wheat loaf.", 59, "🌾", 30, None),
            ("koy-ekmegi", "Köy Ekmeği", "Village Bread", "Odun fırınında köy ekmeği.", "Wood-fired village loaf.", 69, "🫓", 30, None),
            ("bagel", "Susamlı Bagel", "Sesame Bagel", "Susamlı New York usulü bagel.", "New York-style sesame bagel.", 55, "🍩", 40, None),
        ]),
        ("kahvaltilik", "Kahvaltılık & Tost", "Breakfast & Toasties", "Sıcak atıştırmalıklar", "Warm bites", [
            ("kasarli-tost", "Kaşarlı Tost", "Cheese Toastie", "Bol kaşarlı tost.", "Toastie with plenty of kashar.", 119, "🥪", 50, None),
            ("karisik-tost", "Karışık Tost", "Mixed Toastie", "Sucuk ve kaşarlı tost.", "Toastie with sucuk and kashar.", 149, "🌭", 50, None),
            ("kek-dilimi", "Mermer Kek Dilimi", "Marble Cake Slice", "Kakaolu mermer kek.", "Cocoa marble cake.", 49, "🍰", 40, None),
            ("tuzlu-kurabiye", "Tuzlu Kurabiye (250 g)", "Savoury Cookies (250 g)", "Susamlı tuzlu kurabiye.", "Savoury sesame cookies.", 89, "🍪", 40, None),
        ]),
    ],
    "icecek": [
        ("meyve-sulari", "Taze Meyve Suları", "Fresh Juices", "Anında sıkılır", "Squeezed to order", [
            ("elma-suyu", "Taze Elma Suyu", "Fresh Apple Juice", "Yeşil elmadan sıkılmış.", "Pressed from green apples.", 89, "🍏", 50, custom(sides=food_sizes(20, 40))),
            ("greyfurt-suyu", "Greyfurt Suyu", "Grapefruit Juice", "Taze sıkılmış greyfurt.", "Freshly squeezed grapefruit.", 99, "🍈", 40, custom(sides=food_sizes(20, 40))),
        ]),
        ("smoothie", "Smoothie'ler", "Smoothies", "Meyve ve yoğurt", "Fruit and yoghurt", [
            ("yaban-mersinli-smoothie", "Yaban Mersinli Smoothie", "Blueberry Smoothie", "Yaban mersini, muz ve yoğurt.", "Blueberry, banana and yoghurt.", 149, "🫐", 40, custom(sides=food_sizes(20, 35))),
            ("yesil-smoothie", "Yeşil Smoothie", "Green Smoothie", "Ispanak, elma, muz ve kivi.", "Spinach, apple, banana and kiwi.", 155, "🥝", 40, custom(sides=food_sizes(20, 35))),
            ("avokadolu-smoothie", "Avokadolu Smoothie", "Avocado Smoothie", "Avokado, bal ve badem sütü.", "Avocado, honey and almond milk.", 159, "🥑", 30, custom(sides=food_sizes(20, 35))),
        ]),
        ("limonatalar", "Limonatalar", "Lemonades", "Ev yapımı", "Homemade", [
            ("cilekli-limonata", "Çilekli Limonata", "Strawberry Lemonade", "Taze çilekli limonata.", "Lemonade with fresh strawberries.", 99, "🍹", 50, None),
            ("zencefilli-limonata", "Zencefilli Limonata", "Ginger Lemonade", "Taze zencefil ve bal.", "Fresh ginger and honey.", 99, "🫚", 50, None),
            ("lavantali-limonata", "Lavantalı Limonata", "Lavender Lemonade", "Lavanta şuruplu limonata.", "Lemonade with lavender syrup.", 105, "💜", 40, None),
        ]),
        ("soguk-cay-kahve", "Soğuk Çay & Kahve", "Iced Tea & Coffee", "Buz gibi", "Ice cold", [
            ("seftalili-soguk-cay", "Şeftalili Soğuk Çay", "Peach Iced Tea", "Demleme siyah çay ve şeftali.", "Brewed black tea with peach.", 79, "🍑", 60, custom(sides=DRINK_SIZES)),
            ("limonlu-soguk-cay", "Limonlu Soğuk Çay", "Lemon Iced Tea", "Demleme çay, limon ve nane.", "Brewed tea with lemon and mint.", 79, "🫖", 60, custom(sides=DRINK_SIZES)),
            ("cold-brew", "Cold Brew", "Cold Brew", "16 saat demlenmiş soğuk kahve.", "Coffee steeped for 16 hours.", 119, "☕", 40, custom(sides=DRINK_SIZES)),
            ("iced-latte", "Iced Latte", "Iced Latte", "Espresso, soğuk süt ve buz.", "Espresso, cold milk and ice.", 129, "🧋", 40, custom(sides=DRINK_SIZES)),
        ]),
        ("gazli", "Gazlı İçecekler", "Soft Drinks", "Kutu ve şişe", "Cans and bottles", [
            ("kola", "Kola 330 ml", "Cola 330 ml", "Soğuk kutu kola.", "Chilled can of cola.", 55, "🥤", 120, None),
            ("gazoz", "Gazoz", "Turkish Soda Pop", "Klasik limonlu gazoz, 250 ml.", "Classic lemon soda pop, 250 ml.", 45, "🫧", 100, None),
            ("enerji-icecegi", "Enerji İçeceği", "Energy Drink", "250 ml enerji içeceği.", "250 ml energy drink.", 79, "⚡", 80, None),
            ("meyveli-soda", "Meyveli Soda", "Flavoured Sparkling Water", "Vişneli maden suyu, 200 ml.", "Sour cherry sparkling water, 200 ml.", 35, "🍒", 100, None),
        ]),
    ],
    "atistirmalik": [
        ("cipsler", "Cipsler", "Chips", "Çıtır paketler", "Crunchy packs", [
            ("kraker", "Susamlı Kraker", "Sesame Crackers", "Çıtır susamlı kraker paketi.", "Crunchy sesame crackers.", 29, "🍘", 120, None),
            ("patlamis-misir", "Patlamış Mısır", "Popcorn", "Tereyağlı patlamış mısır, büyük boy.", "Buttered popcorn, large bag.", 39, "🍿", 100, None),
            ("kuruyemis", "Karışık Kuruyemiş", "Mixed Nuts", "Fındık, badem, kaju ve fıstık, 150 g.", "Hazelnuts, almonds, cashews and peanuts, 150 g.", 89, "🥜", 80, None),
        ]),
        ("cikolata-biskuvi", "Çikolata & Bisküvi", "Chocolate & Biscuits", "Tatlı molası", "Sweet break", [
            ("findikli-cikolata", "Fındıklı Çikolata", "Hazelnut Chocolate", "Bütün fındıklı sütlü çikolata, 80 g.", "Milk chocolate with whole hazelnuts, 80 g.", 59, "🌰", 80, None),
            ("protein-bar", "Protein Bar", "Protein Bar", "20 g protein içeren kakaolu bar.", "Cocoa bar with 20 g protein.", 69, "💪", 60, None),
        ]),
        ("su-icecek", "Su & Gazlı İçecek", "Water & Soft Drinks", "Serinleten seçenekler", "Cool refreshments", [
            ("limonlu-soda", "Limonlu Soda", "Lemon Soda", "Limon aromalı maden suyu.", "Lemon-flavoured mineral water.", 30, "🍋", 150, None),
            ("meyve-suyu-1l", "Meyve Suyu 1 L", "Fruit Juice 1 L", "Karışık meyve nektarı.", "Mixed fruit nectar.", 69, "🧃", 80, None),
        ]),
        ("sekerleme", "Şekerleme & Sakız", "Candy & Gum", "Küçük mutluluklar", "Little treats", [
            ("sakiz", "Nane Aromalı Sakız", "Mint Chewing Gum", "Şekersiz sakız paketi.", "Sugar-free gum pack.", 15, "🟩", 200, None),
            ("jelibon", "Jelibon", "Gummy Bears", "Meyve aromalı jelibon, 80 g.", "Fruit-flavoured gummies, 80 g.", 39, "🍬", 120, None),
            ("lolipop", "Lolipop", "Lollipop", "Meyve aromalı lolipop.", "Fruit-flavoured lollipop.", 20, "🍭", 150, None),
            ("naneli-seker", "Naneli Şeker", "Mint Candy", "Ferahlatıcı naneli şeker.", "Refreshing mint candy.", 25, "🌱", 150, None),
        ]),
        ("market", "Market Ürünleri", "Convenience", "İhtiyaç anında", "When you need it", [
            ("mendil", "Kağıt Mendil Paketi", "Tissue Pack", "10'lu cep mendili.", "Pocket tissues, 10 packs.", 15, "🤧", 200, None),
            ("islak-mendil", "Islak Mendil", "Wet Wipes", "Cep boy ıslak mendil.", "Pocket-size wet wipes.", 39, "🧻", 120, None),
            ("pil", "Alkalin Pil (AA, 4'lü)", "Alkaline Batteries (AA, 4)", "Uzun ömürlü AA pil.", "Long-lasting AA batteries.", 99, "🔋", 60, None),
            ("semsiye", "Katlanır Şemsiye", "Folding Umbrella", "Rüzgara dayanıklı mini şemsiye.", "Wind-resistant mini umbrella.", 249, "☂️", 30, None),
        ]),
    ],
    "giyim": [
        ("tisortler", "Tişörtler", "T-Shirts", "%100 pamuk", "100% cotton", [
            ("polo-tisort", "Polo Yaka Tişört", "Polo Shirt", "Pike kumaş polo yaka tişört.", "Piqué cotton polo shirt.", 649, "👔", 40, custom(clothing_sizes(), COLORS_BASIC)),
            ("uzun-kollu-tisort", "Uzun Kollu Tişört", "Long-Sleeve T-Shirt", "Yumuşak pamuk uzun kollu tişört.", "Soft cotton long-sleeve T-shirt.", 549, "🥼", 40, custom(clothing_sizes(), COLORS_BASIC)),
            ("cizgili-tisort", "Çizgili Tişört", "Striped T-Shirt", "Denizci çizgili pamuk tişört.", "Breton striped cotton T-shirt.", 499, "🎽", 40, custom(sides=clothing_sizes())),
        ]),
        ("sweatshirt", "Sweatshirt & Hoodie", "Sweatshirts & Hoodies", "Yumuşak içi şardonlu", "Soft brushed fleece", [
            ("fermuarli-hoodie", "Fermuarlı Hoodie", "Zip Hoodie", "Tam boy fermuarlı kapüşonlu sweatshirt.", "Full-zip hoodie.", 999, "🤐", 25, custom(clothing_sizes(), COLORS_BASIC)),
            ("polar-sweatshirt", "Polar Sweatshirt", "Fleece Sweatshirt", "Yarım fermuarlı polar sweatshirt.", "Half-zip fleece sweatshirt.", 849, "🐑", 25, custom(sides=clothing_sizes())),
            ("crop-sweatshirt", "Crop Sweatshirt", "Cropped Sweatshirt", "Kısa kesim rahat sweatshirt.", "Relaxed cropped sweatshirt.", 699, "✂️", 25, custom(sides=clothing_sizes())),
        ]),
        ("aksesuar", "Şapka & Aksesuar", "Caps & Accessories", "Tamamlayıcılar", "Finishing touches", [
            ("atki", "Triko Atkı", "Knit Scarf", "Yumuşak triko atkı.", "Soft knit scarf.", 349, "🧣", 30, custom(extra=COLORS_BASIC)),
            ("eldiven", "Dokunmatik Eldiven", "Touchscreen Gloves", "Telefon ekranıyla uyumlu eldiven.", "Gloves that work with touchscreens.", 249, "🧤", 30, custom(extra=COLORS_BASIC)),
        ]),
        ("altlar", "Eşofman & Şort", "Joggers & Shorts", "Rahat alt giyim", "Comfy bottoms", [
            ("esofman-alti", "Eşofman Altı", "Sweatpants", "Lastik paçalı pamuklu eşofman altı.", "Cotton sweatpants with cuffed hems.", 699, "👖", 30, custom(clothing_sizes(), COLORS_BASIC)),
            ("jogger", "Jogger Pantolon", "Jogger Pants", "Cepli, esnek jogger pantolon.", "Stretch jogger pants with pockets.", 749, "🏃", 30, custom(sides=clothing_sizes())),
            ("sort", "Spor Şort", "Sports Shorts", "Hızlı kuruyan spor şort.", "Quick-dry sports shorts.", 449, "🩳", 30, custom(sides=clothing_sizes())),
            ("tayt", "Spor Tayt", "Leggings", "Yüksek bel, esnek spor tayt.", "High-waist stretch leggings.", 499, "🧘", 30, custom(sides=clothing_sizes())),
        ]),
        ("dis-giyim", "Dış Giyim", "Outerwear", "Hava ne olursa olsun", "Whatever the weather", [
            ("yagmurluk", "Yağmurluk", "Rain Jacket", "Kapüşonlu, su geçirmez yağmurluk.", "Hooded waterproof rain jacket.", 1199, "🌧️", 20, custom(sides=clothing_sizes())),
            ("sisme-yelek", "Şişme Yelek", "Puffer Vest", "Hafif ve sıcak tutan şişme yelek.", "Light and warm puffer vest.", 1499, "🦺", 20, custom(sides=clothing_sizes())),
            ("ruzgarlik", "Rüzgarlık", "Windbreaker", "Katlanabilir hafif rüzgarlık.", "Packable lightweight windbreaker.", 999, "🌬️", 20, custom(sides=clothing_sizes())),
            ("kot-ceket", "Kot Ceket", "Denim Jacket", "Klasik kesim kot ceket.", "Classic fit denim jacket.", 1599, "🪡", 15, custom(sides=clothing_sizes())),
        ]),
    ],
    "ayakkabi": [
        ("sneaker", "Sneaker", "Sneakers", "Günlük konfor", "Everyday comfort", [
            ("basketbol-ayakkabisi", "Basketbol Ayakkabısı", "Basketball Shoes", "Bilek destekli basketbol ayakkabısı.", "High-top basketball shoes with ankle support.", 2599, "🏀", 15, custom(sides=shoe_sizes())),
            ("yuruyus-ayakkabisi", "Yürüyüş Ayakkabısı", "Walking Shoes", "Hafif, yastıklı yürüyüş ayakkabısı.", "Light cushioned walking shoes.", 1999, "🚶", 20, custom(sides=shoe_sizes())),
            ("slip-on", "Slip-on Sneaker", "Slip-on Sneakers", "Bağcıksız günlük sneaker.", "Laceless everyday sneakers.", 1299, "🛹", 20, custom(sides=shoe_sizes())),
        ]),
        ("terlik", "Terlik & Sandalet", "Slippers & Sandals", "Rahat adımlar", "Easy steps", [
            ("sandalet", "Deri Sandalet", "Leather Sandals", "Ayarlanabilir bantlı deri sandalet.", "Leather sandals with adjustable straps.", 699, "👡", 25, custom(sides=shoe_sizes())),
            ("spor-terlik", "Spor Terlik", "Sports Slides", "Yumuşak tabanlı spor terlik.", "Soft-soled sports slides.", 449, "🩴", 30, custom(sides=shoe_sizes())),
            ("ortopedik-terlik", "Ortopedik Terlik", "Orthopaedic Slippers", "Tabanı destekli ortopedik terlik.", "Supportive orthopaedic slippers.", 549, "🦶", 25, custom(sides=shoe_sizes())),
        ]),
        ("cantalar", "Çantalar", "Bags", "Her güne bir çanta", "A bag for every day", [
            ("laptop-cantasi", "Laptop Çantası", "Laptop Bag", "15,6 inç dizüstü bilgisayar çantası.", "Bag for 15.6-inch laptops.", 1099, "💼", 20, custom(extra=COLORS_BAG)),
            ("spor-canta", "Spor Çanta", "Gym Bag", "Ayakkabı bölmeli spor çantası.", "Gym bag with shoe compartment.", 799, "🏋️", 20, custom(extra=COLORS_BAG)),
        ]),
        ("bot-klasik", "Bot & Klasik", "Boots & Formal", "Şık ve dayanıklı", "Smart and durable", [
            ("chelsea-bot", "Chelsea Bot", "Chelsea Boots", "Lastik yanlı deri Chelsea bot.", "Leather Chelsea boots with elastic sides.", 2499, "👢", 15, custom(sides=shoe_sizes())),
            ("trekking-bot", "Trekking Botu", "Hiking Boots", "Su geçirmez trekking botu.", "Waterproof hiking boots.", 2799, "🥾", 15, custom(sides=shoe_sizes())),
            ("klasik-ayakkabi", "Klasik Deri Ayakkabı", "Leather Dress Shoes", "Bağcıklı klasik deri ayakkabı.", "Lace-up leather dress shoes.", 2199, "👞", 15, custom(sides=shoe_sizes())),
            ("loafer", "Loafer", "Loafers", "Süet görünümlü loafer.", "Suede-look loafers.", 1899, "🥿", 15, custom(sides=shoe_sizes())),
        ]),
        ("ayakkabi-aksesuar", "Ayakkabı Aksesuarı", "Shoe Accessories", "Bakım ve konfor", "Care and comfort", [
            ("spor-corap", "Spor Çorap (3'lü)", "Sports Socks (3 pairs)", "Topuk destekli spor çorap.", "Cushioned sports socks.", 129, "🧦", 60, None),
            ("tabanlik", "Jel Tabanlık", "Gel Insoles", "Kesilebilir jel tabanlık.", "Trim-to-fit gel insoles.", 199, "🩹", 40, None),
            ("bakim-seti", "Ayakkabı Bakım Seti", "Shoe Care Kit", "Fırça, cila ve temizleyici.", "Brush, polish and cleaner.", 249, "🧽", 30, None),
            ("bagcik", "Bağcık Seti", "Shoelace Set", "3 çift renkli bağcık.", "3 pairs of coloured laces.", 59, "🎀", 60, None),
        ]),
    ],
    "elektronik": [
        ("sarj-kablo", "Şarj & Kablo", "Chargers & Cables", "Hızlı şarj", "Fast charging", [
            ("kablosuz-sarj", "Kablosuz Şarj Pedi", "Wireless Charging Pad", "15W Qi uyumlu kablosuz şarj pedi.", "15W Qi-compatible wireless charger.", 449, "🪫", 30, custom(extra=COLORS_TECH)),
            ("arac-sarj", "Araç Şarj Cihazı", "Car Charger", "Çift USB-C çıkışlı araç şarjı.", "Dual USB-C car charger.", 299, "🚗", 30, None),
        ]),
        ("kulaklik-ses", "Kulaklık & Ses", "Headphones & Audio", "Müziğin keyfi", "Enjoy your music", [
            ("kulak-ustu", "Kulak Üstü Kulaklık", "Over-Ear Headphones", "Gürültü engelleyici kablosuz kulaklık.", "Noise-cancelling wireless headphones.", 1899, "🎼", 15, custom(extra=COLORS_TECH)),
            ("oyuncu-kulakligi", "Oyuncu Kulaklığı", "Gaming Headset", "Mikrofonlu, 7.1 sesli oyuncu kulaklığı.", "Gaming headset with mic and 7.1 sound.", 1499, "🎮", 15, None),
        ]),
        ("telefon-aksesuar", "Telefon Aksesuarı", "Phone Accessories", "Koruma ve kullanım", "Protect and use", [
            ("telefon-kilifi", "Silikon Telefon Kılıfı", "Silicone Phone Case", "Darbe emici silikon kılıf.", "Shock-absorbing silicone case.", 249, "📲", 40, custom(extra=COLORS_TECH)),
            ("selfie-cubugu", "Selfie Çubuğu", "Selfie Stick", "Bluetooth kumandalı tripod selfie çubuğu.", "Tripod selfie stick with Bluetooth remote.", 299, "🤳", 25, None),
            ("telefon-yuzugu", "Telefon Yüzüğü", "Phone Ring Holder", "360° dönen telefon tutma yüzüğü.", "360° rotating phone ring holder.", 129, "💍", 40, None),
        ]),
        ("bilgisayar", "Bilgisayar Aksesuarı", "Computer Accessories", "Masa başı", "For your desk", [
            ("kablosuz-mouse", "Kablosuz Mouse", "Wireless Mouse", "Sessiz tıklamalı kablosuz mouse.", "Silent-click wireless mouse.", 399, "🖱️", 30, custom(extra=COLORS_TECH)),
            ("bluetooth-klavye", "Bluetooth Klavye", "Bluetooth Keyboard", "İnce, Türkçe Q bluetooth klavye.", "Slim Turkish Q Bluetooth keyboard.", 899, "⌨️", 20, None),
            ("usb-bellek", "USB Bellek 64 GB", "USB Flash Drive 64 GB", "USB 3.2 hızlı flash bellek.", "Fast USB 3.2 flash drive.", 249, "💾", 40, None),
            ("usb-c-hub", "USB-C Çoklayıcı", "USB-C Hub", "HDMI ve 3 USB girişli çoklayıcı.", "Hub with HDMI and 3 USB ports.", 699, "🔗", 20, None),
        ]),
        ("akilli-cihaz", "Akıllı Cihazlar", "Smart Devices", "Hayatı kolaylaştıran", "Making life easier", [
            ("akilli-saat", "Akıllı Saat", "Smartwatch", "Nabız ve uyku takipli akıllı saat.", "Smartwatch with heart-rate and sleep tracking.", 2499, "⌚", 15, custom(extra=COLORS_TECH)),
            ("akilli-bileklik", "Akıllı Bileklik", "Fitness Band", "Adım ve kalori takip bilekliği.", "Step and calorie tracking band.", 999, "📟", 20, None),
            ("takip-cihazi", "Bluetooth Takip Cihazı", "Bluetooth Tracker", "Anahtar ve çanta için takip cihazı.", "Tracker for keys and bags.", 599, "📍", 25, None),
            ("akilli-priz", "Akıllı Priz", "Smart Plug", "Uygulama kontrollü Wi-Fi priz.", "App-controlled Wi-Fi smart plug.", 449, "💡", 25, None),
        ]),
    ],
    "kozmetik": [
        ("parfum", "Parfüm", "Perfume", "Kalıcı kokular", "Long-lasting scents", [
            ("erkek-parfum", "Erkek Parfüm 100 ml", "Men's Perfume 100 ml", "Odunsu ve baharatlı erkek parfümü.", "Woody and spicy men's fragrance.", 1599, "🌲", 15, None),
            ("deodorant", "Deodorant Sprey", "Deodorant Spray", "48 saat etkili deodorant.", "48-hour deodorant.", 199, "💨", 40, None),
            ("mini-parfum-seti", "Mini Parfüm Seti", "Mini Perfume Set", "4 farklı kokuda 10 ml parfüm.", "Four 10 ml fragrances.", 899, "🎁", 15, None),
        ]),
        ("cilt-bakimi", "Cilt Bakımı", "Skin Care", "Günlük bakım", "Daily care", [
            ("temizleme-jeli", "Yüz Temizleme Jeli", "Face Cleansing Gel", "Nazik köpüren temizleme jeli.", "Gentle foaming cleansing gel.", 279, "🫧", 30, None),
            ("c-vitamini-serum", "C Vitamini Serum", "Vitamin C Serum", "Aydınlatıcı C vitamini serum, 30 ml.", "Brightening vitamin C serum, 30 ml.", 549, "🍊", 20, None),
            ("kagit-maske", "Kağıt Maske (3'lü)", "Sheet Masks (3 pcs)", "Nemlendirici kağıt yüz maskesi.", "Hydrating sheet masks.", 199, "🎭", 30, None),
        ]),
        ("kisisel-bakim", "Kişisel Bakım", "Personal Care", "Yolda bakım", "Care on the go", [
            ("dis-fircasi", "Diş Fırçası Seti", "Toothbrush Set", "2'li yumuşak diş fırçası.", "Two soft toothbrushes.", 149, "🪥", 40, None),
            ("dis-macunu", "Seyahat Diş Macunu", "Travel Toothpaste", "Seyahat boy 20 ml diş macunu.", "Travel-size 20 ml toothpaste.", 59, "🦷", 60, None),
        ]),
        ("makyaj", "Makyaj", "Make-up", "Hızlı tazeleme", "Quick touch-up", [
            ("maskara", "Hacim Veren Maskara", "Volumising Mascara", "Topaklanmayan siyah maskara.", "Clump-free black mascara.", 349, "👁️", 25, None),
            ("ruj", "Mat Ruj", "Matte Lipstick", "Uzun süre kalıcı mat ruj.", "Long-lasting matte lipstick.", 299, "💋", 25, custom(extra=COLORS_LIP)),
            ("allik", "Allık", "Blush", "Doğal görünümlü toz allık.", "Natural-look powder blush.", 279, "🍑", 20, None),
            ("makyaj-mendili", "Makyaj Temizleme Mendili", "Make-up Remover Wipes", "25'li makyaj temizleme mendili.", "25 make-up remover wipes.", 129, "🧼", 30, None),
        ]),
        ("sac-bakimi", "Saç Bakımı", "Hair Care", "Saçlarınız için", "For your hair", [
            ("sac-kremi", "Saç Kremi", "Conditioner", "Argan yağlı saç kremi, 250 ml.", "Argan oil conditioner, 250 ml.", 179, "💆", 30, None),
            ("sac-fircasi", "Saç Fırçası", "Hair Brush", "Kolay tarayan detangle fırça.", "Easy detangling brush.", 199, "🪮", 25, None),
            ("toka-seti", "Saç Tokası Seti", "Hair Tie Set", "12 parça toka ve lastik seti.", "12-piece hair ties and clips.", 99, "🎗️", 40, None),
            ("kuru-sampuan", "Kuru Şampuan", "Dry Shampoo", "Anında tazeleyen kuru şampuan.", "Instantly refreshing dry shampoo.", 229, "🌬️", 25, None),
        ]),
    ],
    "kirtasiye": [
        ("kitaplar", "Kitaplar", "Books", "Okuma keyfi", "Reading pleasure", [
            ("polisiye", "Polisiye Roman", "Crime Novel", "Sürükleyici yerli polisiye roman.", "A gripping Turkish crime novel.", 229, "🔍", 20, None),
            ("kisisel-gelisim", "Kişisel Gelişim Kitabı", "Self-Help Book", "Alışkanlıklar üzerine çok satan kitap.", "Bestselling book on habits.", 199, "🌱", 20, None),
            ("boyama-kitabi", "Boyama Kitabı", "Colouring Book", "Yetişkinler için mandala boyama kitabı.", "Mandala colouring book for adults.", 129, "🎨", 25, None),
        ]),
        ("defter-ajanda", "Defter & Ajanda", "Notebooks & Planners", "Not almak için", "For your notes", [
            ("kareli-defter", "Kareli Defter A4", "A4 Squared Notebook", "Kareli, 80 yapraklı defter.", "Squared notebook, 80 sheets.", 79, "📒", 40, None),
            ("not-defteri-seti", "Not Defteri Seti (3'lü)", "Notebook Set (3 pcs)", "Cep boy not defterleri.", "Pocket-size notebooks.", 149, "🗒️", 30, None),
            ("yapiskanli-not", "Yapışkanlı Not Kağıdı", "Sticky Notes", "4 renk yapışkanlı not, 400 yaprak.", "Sticky notes in 4 colours, 400 sheets.", 49, "🟨", 60, None),
        ]),
        ("kalemler", "Kalemler", "Pens & Pencils", "Yazmak ve çizmek için", "For writing and drawing", [
            ("fosforlu-kalem", "Fosforlu Kalem Seti", "Highlighter Set", "6 renk fosforlu kalem.", "Six-colour highlighter set.", 99, "🖌️", 40, None),
            ("dolma-kalem", "Dolma Kalem", "Fountain Pen", "Metal gövdeli dolma kalem.", "Metal-body fountain pen.", 349, "🖋️", 15, None),
        ]),
        ("okul", "Okul Malzemeleri", "School Supplies", "Okula hazırlık", "Back to school", [
            ("kalem-kutusu", "Kalem Kutusu", "Pencil Case", "Çift bölmeli fermuarlı kalem kutusu.", "Double-compartment zip pencil case.", 199, "👝", 30, custom(extra=COLORS_TECH)),
            ("silgi-kalemtiras", "Silgi & Kalemtıraş Seti", "Eraser & Sharpener Set", "Hazneli kalemtıraş ve silgi.", "Sharpener with case and eraser.", 49, "🧽", 50, None),
            ("cetvel-seti", "Geometri Seti", "Geometry Set", "Cetvel, gönye ve iletki seti.", "Ruler, set squares and protractor.", 59, "📐", 40, None),
            ("makas", "Okul Makası", "School Scissors", "Güvenli uçlu okul makası.", "Safety-tip school scissors.", 69, "✂️", 40, None),
        ]),
        ("dergi-hediye", "Dergi & Hediyelik", "Magazines & Gifts", "Küçük hediyeler", "Small gifts", [
            ("haftalik-dergi", "Haftalık Dergi", "Weekly Magazine", "Güncel haber ve kültür dergisi.", "Current affairs and culture magazine.", 79, "📰", 30, None),
            ("bulmaca-dergisi", "Bulmaca Dergisi", "Puzzle Magazine", "Sudoku, kare bulmaca ve zeka oyunları.", "Sudoku, crosswords and brain teasers.", 49, "🧠", 30, None),
            ("hediye-karti", "Hediye Kartı", "Greeting Card", "Zarflı tebrik kartı.", "Greeting card with envelope.", 39, "💌", 50, None),
            ("ayrac-seti", "Kitap Ayracı Seti", "Bookmark Set", "4'lü metal kitap ayracı.", "Set of 4 metal bookmarks.", 69, "🔖", 40, None),
        ]),
    ],
    "oyuncak": [
        ("oyuncaklar", "Oyuncaklar", "Toys", "Eğlenceli oyunlar", "Fun and games", [
            ("yapi-seti", "Yapı Blokları Seti", "Building Blocks Set", "250 parça yapı blokları.", "250-piece building blocks.", 499, "🧱", 20, None),
            ("kumandali-araba", "Uzaktan Kumandalı Araba", "RC Car", "Şarjlı uzaktan kumandalı araba.", "Rechargeable remote-control car.", 899, "🏎️", 15, None),
            ("oyun-hamuru", "Oyun Hamuru Seti", "Play Dough Set", "8 renk oyun hamuru ve kalıplar.", "8 colours of play dough with moulds.", 199, "🎨", 30, None),
        ]),
        ("peluslar", "Peluşlar", "Plush Toys", "Sarılmalık dostlar", "Cuddly friends", [
            ("pelus-kedi", "Peluş Kedi", "Plush Cat", "25 cm peluş kedi.", "25 cm plush cat.", 279, "🐱", 20, None),
            ("pelus-panda", "Peluş Panda", "Plush Panda", "30 cm peluş panda.", "30 cm plush panda.", 329, "🐼", 20, None),
            ("pelus-dinozor", "Peluş Dinozor", "Plush Dinosaur", "35 cm peluş dinozor.", "35 cm plush dinosaur.", 349, "🦖", 20, None),
        ]),
        ("hediyelik", "Hediyelik Eşya", "Souvenirs", "Anı kalsın", "Keepsakes", [
            ("nazar-bileklik", "Nazar Boncuklu Bileklik", "Evil Eye Bracelet", "El yapımı nazar boncuklu bileklik.", "Handmade evil eye bracelet.", 129, "🧿", 40, None),
            ("kartpostal", "Kartpostal Seti", "Postcard Set", "8'li İstanbul kartpostalı.", "Set of 8 Istanbul postcards.", 59, "🖼️", 40, None),
        ]),
        ("kutu-oyunlari", "Kutu Oyunları", "Board Games", "Aile eğlencesi", "Family fun", [
            ("tavla", "Tavla", "Backgammon", "Ahşap kakmalı tavla takımı.", "Inlaid wooden backgammon set.", 599, "🎯", 15, None),
            ("satranc", "Satranç Takımı", "Chess Set", "Katlanır ahşap satranç takımı.", "Folding wooden chess set.", 449, "♟️", 15, None),
            ("kart-oyunu", "Kart Oyunu", "Card Game", "2-6 kişilik eğlenceli kart oyunu.", "Fun card game for 2-6 players.", 199, "🃏", 25, None),
            ("okey", "Okey Takımı", "Okey Set", "Istaka ve taşlarıyla okey takımı.", "Okey set with racks and tiles.", 699, "🀄", 10, None),
        ]),
        ("bebek", "Bebek & Okul Öncesi", "Baby & Toddler", "Minikler için", "For little ones", [
            ("dis-kasiyici", "Diş Kaşıyıcı", "Teether", "BPA içermeyen diş kaşıyıcı.", "BPA-free teether.", 149, "👶", 25, None),
            ("cingirak", "Çıngırak", "Rattle", "Renkli bebek çıngırağı.", "Colourful baby rattle.", 129, "🪇", 25, None),
            ("banyo-oyuncagi", "Banyo Oyuncağı", "Bath Toy", "Yüzen ördek banyo seti.", "Floating duck bath set.", 179, "🦆", 25, None),
            ("bultak", "Ahşap Bultak", "Wooden Shape Puzzle", "Şekil eşleştirmeli ahşap bultak.", "Wooden shape-matching puzzle.", 229, "🪵", 20, None),
        ]),
    ],
    "cicek": [
        ("buketler", "Buketler", "Bouquets", "Taze kesme çiçekler", "Fresh cut flowers", [
            ("beyaz-gul", "Beyaz Gül Buketi", "White Rose Bouquet", "11 adet beyaz gül.", "11 white roses.", 849, "🤍", 15, custom(extra=FLOWER_EXTRAS)),
            ("gerbera-buketi", "Gerbera Buketi", "Gerbera Bouquet", "Renkli gerbera buketi.", "Colourful gerbera bouquet.", 449, "🏵️", 15, custom(extra=FLOWER_EXTRAS)),
        ]),
        ("saksi", "Saksı Çiçekleri", "Potted Plants", "Uzun ömürlü", "Long-lasting", [
            ("bonsai", "Bonsai Ağacı", "Bonsai Tree", "Seramik saksıda ficus bonsai.", "Ficus bonsai in a ceramic pot.", 899, "🌳", 10, None),
            ("lavanta-saksi", "Saksıda Lavanta", "Potted Lavender", "Mis kokulu lavanta.", "Fragrant lavender.", 249, "🪻", 20, None),
            ("pasa-kilici", "Paşa Kılıcı", "Snake Plant", "Az bakım isteyen paşa kılıcı.", "Low-maintenance snake plant.", 399, "🗡️", 15, None),
        ]),
        ("aranjman", "Aranjmanlar", "Arrangements", "Özel günler için", "For special days", [
            ("kutuda-guller", "Kutuda Güller", "Roses in a Box", "Şık kutuda 15 kırmızı gül.", "15 red roses in a gift box.", 1299, "📦", 10, custom(extra=FLOWER_EXTRAS)),
            ("teraryum", "Teraryum", "Terrarium", "Cam fanusta minyatür bahçe.", "Miniature garden in a glass jar.", 699, "🫙", 10, None),
            ("masa-aranjmani", "Masa Aranjmanı", "Table Arrangement", "Mevsim çiçeklerinden masa aranjmanı.", "Table arrangement of seasonal flowers.", 749, "🏺", 10, custom(extra=FLOWER_EXTRAS)),
            ("sepette-orkide", "Sepette Orkide", "Orchid in a Basket", "Hasır sepette çift orkide.", "Two orchids in a wicker basket.", 1499, "🧺", 8, custom(extra=FLOWER_EXTRAS)),
        ]),
        ("kuru-cicek", "Kuru & Yapay Çiçek", "Dried & Faux Flowers", "Solmayan güzellik", "Beauty that lasts", [
            ("kuru-lavanta", "Kuru Lavanta Demeti", "Dried Lavender Bunch", "Doğal kurutulmuş lavanta.", "Naturally dried lavender.", 199, "💜", 20, None),
            ("pampas", "Pampas Otu", "Pampas Grass", "Dekoratif pampas otu demeti.", "Decorative pampas grass bunch.", 249, "🌾", 20, None),
            ("yapay-orkide", "Yapay Orkide", "Faux Orchid", "Gerçekçi yapay orkide, saksıda.", "Realistic faux orchid in a pot.", 549, "🌺", 15, None),
        ]),
        ("hediye", "Hediyelikler", "Gifts", "Çiçeğin yanına", "Alongside flowers", [
            ("cikolata-kutusu", "Çikolata Kutusu", "Chocolate Box", "16'lı spesiyal çikolata.", "16 speciality chocolates.", 299, "🍫", 20, None),
            ("balon", "Folyo Balon", "Foil Balloon", "Kalp şeklinde helyumlu balon.", "Heart-shaped helium balloon.", 99, "🎈", 30, None),
            ("pelus-ayicik", "Peluş Ayıcık", "Teddy Bear", "20 cm peluş ayıcık.", "20 cm teddy bear.", 249, "🧸", 20, None),
            ("tebrik-karti", "Tebrik Kartı", "Greeting Card", "Kişisel not yazılabilen kart.", "Card for a personal message.", 39, "💌", 50, None),
        ]),
    ],
}


def _merge_extra_catalog() -> None:
    for section_id, groups in EXTRA_CATALOG.items():
        target = CATALOG.setdefault(section_id, [])
        for group in groups:
            existing = next((item for item in target if item[0] == group[0]), None)
            if existing:
                known = {product[0] for product in existing[5]}
                existing[5].extend(product for product in group[5] if product[0] not in known)
            else:
                target.append((group[0], group[1], group[2], group[3], group[4], list(group[5])))


_merge_extra_catalog()


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
                    "popular": item_index == 0 and category_position < 3,
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
