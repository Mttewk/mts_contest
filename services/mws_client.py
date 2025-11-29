# services/mws_client.py
import os
from typing import List, Dict, Optional

from dotenv import load_dotenv
import requests

# Подгружаем .env в этом модуле
load_dotenv()

MWS_API_TOKEN = os.getenv("MWS_API_TOKEN")
MWS_BASE_URL = os.getenv("MWS_BASE_URL")           # например: https://tables.mws.ru/fusion/v1
MWS_CONTENT_TABLE_ID = os.getenv("MWS_CONTENT_TABLE_ID")  # ID datasheet'а (dst...)


class MWSClientError(Exception):
    pass


def _get_headers() -> Dict[str, str]:
    if not MWS_API_TOKEN:
        raise MWSClientError("Не задан MWS_API_TOKEN в .env")
    return {
        "Authorization": f"Bearer {MWS_API_TOKEN}",
        "Content-Type": "application/json",
    }


def fetch_content_items(limit: Optional[int] = None) -> List[Dict]:
    """
    Считываем записи из таблицы MWS и возвращаем список словарей
    с полями platform, external_id, url, title, views, likes, comments_count, engagement_rate.

    Если передан limit, берём последние N записей (с конца списка).
    """
    if not MWS_BASE_URL or not MWS_CONTENT_TABLE_ID:
        raise MWSClientError("Не заданы MWS_BASE_URL или MWS_CONTENT_TABLE_ID в .env")

    headers = _get_headers()
    list_url = f"{MWS_BASE_URL}/datasheets/{MWS_CONTENT_TABLE_ID}/records"

    resp = requests.get(
        list_url,
        headers=headers,
        params={"fieldKey": "name"},
        timeout=30,
    )

    if resp.status_code != 200:
        raise MWSClientError(
            f"Ошибка получения записей из MWS: {resp.status_code} {resp.text}"
        )

    data = resp.json()
    records = data.get("records", [])

    # Берём последние N записей (на случай, если их больше)
    if limit is not None and len(records) > limit:
        records = records[-limit:]

    items: List[Dict] = []
    for rec in records:
        fields = rec.get("fields", {})
        try:
            items.append(
                {
                    "platform": fields.get("platform", "YouTube"),
                    "external_id": fields.get("external_id", ""),
                    "url": fields.get("url", ""),
                    "title": fields.get("title", ""),
                    "views": int(fields.get("views", 0) or 0),
                    "likes": int(fields.get("likes", 0) or 0),
                    "comments_count": int(fields.get("comments_count", 0) or 0),
                    "engagement_rate": float(fields.get("engagement_rate", 0) or 0),
                }
            )
        except Exception:
            # Если проблемы с типами в одной записи — пропускаем её
            continue

    return items


def upsert_content_items(items: List[Dict]) -> int:
    """
    Интеграция с MWS Fusion:

    - Сначала читаем текущие записи из datasheet'а и собираем set existing_external_ids.
    - Затем СОЗДАЁМ только те записи, которых ещё нет по external_id.
      Так мы не плодим дубликаты при каждом /sync.
    """
    if not MWS_BASE_URL or not MWS_CONTENT_TABLE_ID:
        raise MWSClientError("Не заданы MWS_BASE_URL или MWS_CONTENT_TABLE_ID в .env")

    headers = _get_headers()

    # 1. Получаем текущие записи
    list_url = f"{MWS_BASE_URL}/datasheets/{MWS_CONTENT_TABLE_ID}/records"
    list_resp = requests.get(
        list_url,
        headers=headers,
        params={"fieldKey": "name"},
        timeout=30,
    )

    if list_resp.status_code != 200:
        raise MWSClientError(
            f"Ошибка получения записей из MWS: {list_resp.status_code} {list_resp.text}"
        )

    list_data = list_resp.json()
    existing_external_ids = set()

    for rec in list_data.get("records", []):
        fields = rec.get("fields", {})
        ext_id = fields.get("external_id")
        if ext_id:
            existing_external_ids.add(ext_id)

    # 2. Оставляем только новые элементы (по external_id)
    new_items: List[Dict] = []
    for item in items:
        if item["external_id"] not in existing_external_ids:
            new_items.append(item)

    # Если новых нет — ничего не создаём
    if not new_items:
        return 0

    # 3. Строим payload только для новых записей
    records_payload = []
    for item in new_items:
        views = item.get("views", 0) or 0
        likes = item.get("likes", 0) or 0
        comments = item.get("comments_count", 0) or 0

        if views > 0:
            engagement_rate = (likes + comments) / views
        else:
            engagement_rate = 0

        fields = {
            "platform": item["platform"],
            "external_id": item["external_id"],
            "url": item["url"],
            "title": item["title"],
            "views": views,
            "likes": likes,
            "comments_count": comments,
            "engagement_rate": engagement_rate,
        }
        records_payload.append({"fields": fields})

    create_resp = requests.post(
        list_url,
        headers=headers,
        params={"fieldKey": "name"},
        json={"records": records_payload},
        timeout=30,
    )

    if create_resp.status_code not in (200, 201):
        raise MWSClientError(
            f"Ошибка создания записей в MWS: {create_resp.status_code} {create_resp.text}"
        )

    return len(new_items)