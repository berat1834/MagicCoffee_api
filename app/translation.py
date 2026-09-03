from __future__ import annotations

import hashlib
import html
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


TARGET_LANGUAGE = "en"
RETRY_AFTER = timedelta(minutes=30)
GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"

EXACT_TRANSLATIONS = {
    "Espresso Bazli Kahveler": "Espresso-Based Coffees",
    "Filtre Kahveler": "Filter Coffees",
    "Soguk Kahveler": "Cold Coffees",
    "Frappeler": "Frappes",
    "Geleneksel Kahveler": "Traditional Coffees",
    "Caylar": "Teas",
    "Diger Icecekler": "Other Drinks",
    "Tatlilar": "Desserts",
    "Atistirmaliklar": "Snacks",
    "Boyut Secimi": "Size Selection",
    "Sicaklik": "Temperature",
    "Buz Miktari": "Ice Amount",
    "Sut Secimi": "Milk Selection",
    "Surup Secimi": "Syrup Selection",
    "Seker": "Sugar",
    "Ekstra Shot": "Extra Shot",
    "Krema": "Cream",
    "Yanina Tatli": "Add a Dessert",
    "Kucuk": "Small",
    "Orta": "Medium",
    "Buyuk": "Large",
    "Sicak": "Hot",
    "Ilik": "Warm",
    "Ekstra sicak": "Extra hot",
    "Buzsuz": "No ice",
    "Az buzlu": "Light ice",
    "Normal": "Regular",
    "Ekstra buzlu": "Extra ice",
    "Tam yagli sut": "Whole milk",
    "Yarim yagli sut": "Semi-skimmed milk",
    "Laktozsuz sut": "Lactose-free milk",
    "Badem sutu": "Almond milk",
    "Yulaf sutu": "Oat milk",
    "Hindistan cevizi sutu": "Coconut milk",
    "Vanilya": "Vanilla",
    "Karamel": "Caramel",
    "Findik": "Hazelnut",
    "Cikolata": "Chocolate",
    "Beyaz cikolata": "White chocolate",
    "Sekersiz": "No sugar",
    "Tatlandirici": "Sweetener",
    "Krema ekle": "Add cream",
}


@dataclass(frozen=True)
class TranslationSource:
    entity_type: str
    entity_id: str
    field: str
    text: str

    @property
    def key(self) -> str:
        return "|".join((self.entity_type, self.entity_id, self.field, TARGET_LANGUAGE))

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def provider_configured() -> bool:
    return bool(os.getenv("GOOGLE_TRANSLATE_API_KEY", "").strip())


def _ascii_key(value: str) -> str:
    return value.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU"))


def _exact_translation(value: str) -> str | None:
    return EXACT_TRANSLATIONS.get(value) or EXACT_TRANSLATIONS.get(_ascii_key(value))


def _customization_sources(product: dict[str, Any]) -> list[TranslationSource]:
    sources: list[TranslationSource] = []
    product_id = str(product.get("id") or "")
    for step_id, step in (product.get("customization") or {}).items():
        title = str(step.get("title") or "").strip()
        if title:
            sources.append(TranslationSource("product", product_id, f"customization.{step_id}.title", title))
        for option in step.get("options") or []:
            option_id = str(option.get("id") or "")
            name = str(option.get("name") or "").strip()
            if option_id and name:
                sources.append(TranslationSource(
                    "product", product_id, f"customization.{step_id}.options.{option_id}.name", name,
                ))
    return sources


def collect_sources(
    categories: list[dict[str, Any]],
    products: list[dict[str, Any]],
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> list[TranslationSource]:
    sources: list[TranslationSource] = []
    if entity_type in (None, "category"):
        for category in categories:
            if entity_id and category.get("id") != entity_id:
                continue
            for field in ("name", "eyebrow"):
                text = str(category.get(field) or "").strip()
                if text:
                    sources.append(TranslationSource("category", str(category["id"]), field, text))
    if entity_type in (None, "product"):
        for product in products:
            if entity_id and product.get("id") != entity_id:
                continue
            for field in ("name", "description"):
                text = str(product.get(field) or "").strip()
                if text:
                    sources.append(TranslationSource("product", str(product["id"]), field, text))
            sources.extend(_customization_sources(product))
    return sources


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _translation_due(record: dict[str, Any] | None, source: TranslationSource, now: datetime) -> bool:
    if not record or record.get("sourceHash") != source.source_hash:
        return True
    if record.get("status") == "ready" and str(record.get("translatedText") or "").strip():
        return False
    attempted = _parse_timestamp(record.get("lastAttemptAt"))
    return attempted is None or now - attempted >= RETRY_AFTER


def _protect_brand_terms(text: str) -> str:
    return text.replace("Magic Coffee", "ZXQMCOFFEEZX").replace("Magic", "ZXQMAGICZX")


def _restore_brand_terms(text: str) -> str:
    return text.replace("ZXQMCOFFEEZX", "Magic Coffee").replace("ZXQMAGICZX", "Magic")


def _google_translate(texts: list[str]) -> list[str]:
    api_key = os.getenv("GOOGLE_TRANSLATE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_TRANSLATE_API_KEY tanimli degil")
    response = httpx.post(
        GOOGLE_TRANSLATE_URL,
        params={"key": api_key},
        json={
            "q": [_protect_brand_terms(text) for text in texts],
            "source": "tr",
            "target": TARGET_LANGUAGE,
            "format": "text",
        },
        timeout=httpx.Timeout(10.0, connect=5.0),
    )
    response.raise_for_status()
    items = response.json().get("data", {}).get("translations", [])
    if len(items) != len(texts):
        raise RuntimeError("Ceviri servisi eksik sonuc dondurdu")
    return [
        _restore_brand_terms(html.unescape(str(item.get("translatedText") or "")).strip())
        for item in items
    ]


def sync_catalog_translations(
    categories: list[dict[str, Any]],
    products: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> dict[str, int | bool]:
    sources = collect_sources(categories, products, entity_type, entity_id)
    now_datetime = datetime.now(timezone.utc)
    now = now_datetime.isoformat()
    due: list[TranslationSource] = []
    for source in sources:
        record = records.get(source.key)
        if _translation_due(record, source, now_datetime):
            due.append(source)
        if not record or record.get("sourceHash") != source.source_hash:
            records[source.key] = {
                "entityType": source.entity_type,
                "entityId": source.entity_id,
                "field": source.field,
                "language": TARGET_LANGUAGE,
                "sourceText": source.text,
                "sourceHash": source.source_hash,
                "translatedText": "",
                "status": "pending",
                "provider": None,
                "error": None,
                "lastAttemptAt": None,
                "updatedAt": now,
            }

    translated_count = 0
    remote: list[TranslationSource] = []
    for source in due:
        exact = _exact_translation(source.text)
        if exact:
            records[source.key].update({
                "translatedText": exact,
                "status": "ready",
                "provider": "magic-coffee-glossary",
                "error": None,
                "lastAttemptAt": now,
                "updatedAt": now,
            })
            translated_count += 1
        else:
            remote.append(source)

    if remote and not provider_configured():
        for source in remote:
            records[source.key].update({
                "status": "error",
                "provider": "google",
                "error": "GOOGLE_TRANSLATE_API_KEY tanimli degil",
                "lastAttemptAt": now,
                "updatedAt": now,
            })
        return {"configured": False, "translated": translated_count, "pending": len(remote)}

    for start in range(0, len(remote), 50):
        batch = remote[start:start + 50]
        try:
            translated = _google_translate([source.text for source in batch])
            if any(not value for value in translated):
                raise RuntimeError("Ceviri servisi bos sonuc dondurdu")
            for source, value in zip(batch, translated):
                records[source.key].update({
                    "translatedText": value,
                    "status": "ready",
                    "provider": "google",
                    "error": None,
                    "lastAttemptAt": now,
                    "updatedAt": now,
                })
                translated_count += 1
        except Exception as error:
            for source in batch:
                records[source.key].update({
                    "status": "error",
                    "provider": "google",
                    "error": str(error)[:500],
                    "lastAttemptAt": now,
                    "updatedAt": now,
                })

    return {
        "configured": provider_configured(),
        "translated": translated_count,
        "pending": max(0, len(due) - translated_count),
    }


def translation_status(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for record in records.values():
        if record.get("language") != TARGET_LANGUAGE:
            continue
        status = str(record.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    return {
        "language": TARGET_LANGUAGE,
        "provider": "google",
        "configured": provider_configured(),
        "ready": counts.get("ready", 0),
        "pending": counts.get("pending", 0),
        "errors": counts.get("error", 0),
    }


def localized_text(
    records: dict[str, dict[str, Any]],
    entity_type: str,
    entity_id: str,
    field: str,
    source: str,
    language: str,
) -> str:
    if language == "tr":
        return source
    key = "|".join((entity_type, entity_id, field, language))
    record = records.get(key)
    if not record or record.get("sourceText") != source or record.get("status") != "ready":
        return source
    return str(record.get("translatedText") or source)


def remove_entity_translations(records: dict[str, dict[str, Any]], entity_type: str, entity_id: str) -> None:
    prefix = f"{entity_type}|{entity_id}|"
    for key in [key for key in records if key.startswith(prefix)]:
        records.pop(key, None)
