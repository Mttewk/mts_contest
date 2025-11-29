# services/youtube_client.py
import os
import time
from typing import List, Dict, Optional

from dotenv import load_dotenv
import requests

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")  # канал по умолчанию

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIError(Exception):
    pass


def _require_api_key():
    if not YOUTUBE_API_KEY:
        raise YouTubeAPIError("Не задан YOUTUBE_API_KEY в .env")


# ---- простой кэш, чтобы не долбить YouTube лишний раз ----

_CACHE: Dict[tuple, tuple] = {}
_CACHE_TTL_SECONDS = 60  # сек


def _cache_get(channel_id: str, max_results: int) -> Optional[List[Dict]]:
    key = (channel_id, max_results)
    entry = _CACHE.get(key)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return None
    return data


def _cache_set(channel_id: str, max_results: int, data: List[Dict]) -> None:
    key = (channel_id, max_results)
    _CACHE[key] = (time.time(), data)


# ---- разбор ссылок/хэндлов ----

def _extract_ids_from_url(raw: str) -> Dict[str, Optional[str]]:
    """
    Пытаемся вытащить channel_id / handle / video_id из разных форматов URL YouTube.
    """
    s = raw.strip()

    result = {
        "channel_id": None,
        "handle": None,
        "video_id": None,
    }

    # короткая ссылка на видео
    if "youtu.be/" in s:
        video_id = s.split("youtu.be/")[-1]
        video_id = video_id.split("?")[0].split("&")[0]
        result["video_id"] = video_id
        return result

    # длинная ссылка на видео
    if "youtube.com/watch" in s and "v=" in s:
        after_v = s.split("v=")[-1]
        video_id = after_v.split("&")[0].split("?")[0]
        result["video_id"] = video_id
        return result

    # ссылка на канал по channelId
    if "youtube.com/channel/" in s:
        after = s.split("youtube.com/channel/")[-1]
        channel_id = after.split("/")[0].split("?")[0].split("&")[0]
        result["channel_id"] = channel_id
        return result

    # ссылка с @handle, например /@MajorRainbow или /@MajorRainbow/videos
    if "youtube.com/@" in s:
        after = s.split("youtube.com/@")[-1]
        handle = after.split("/")[0].split("?")[0].split("&")[0]
        result["handle"] = "@" + handle
        return result

    # user/ или c/ — считаем, что это имя/часть URL → дальше будем искать через search
    return result


# ---- точные функции поиска канала ----

def _find_channel_by_search(query: str) -> str:
    """
    Ищем канал через search API по строке: имя, кусок URL и т.п.
    Это fallback, если по handle / video_id не нашли.
    """
    _require_api_key()

    params = {
        "part": "snippet",
        "type": "channel",
        "q": query,
        "maxResults": 1,
        "key": YOUTUBE_API_KEY,
    }
    resp = requests.get(f"{YOUTUBE_API_BASE}/search", params=params)
    data = resp.json()

    if resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube search (канал): {data}")

    items = data.get("items", [])
    if not items:
        raise YouTubeAPIError(f"Канал по запросу '{query}' не найден")

    channel_id = items[0]["snippet"]["channelId"]
    return channel_id


def _get_channel_id_from_handle(handle: str) -> str:
    """
    Для @handle используем точный метод YouTube:
    GET channels?forHandle=@handle
    """
    _require_api_key()

    # на всякий случай приводим к виду @xxxx
    handle = handle.strip()
    if not handle.startswith("@"):
        handle = "@" + handle

    params = {
        "part": "id",
        "forHandle": handle,
        "key": YOUTUBE_API_KEY,
    }
    resp = requests.get(f"{YOUTUBE_API_BASE}/channels", params=params)
    data = resp.json()

    if resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube channels(forHandle): {data}")

    items = data.get("items", [])
    if not items:
        raise YouTubeAPIError(f"Канал с handle '{handle}' не найден")

    return items[0]["id"]


def _get_channel_id_from_video(video_id: str) -> str:
    """
    По videoId получаем channelId (через videos API).
    """
    _require_api_key()

    params = {
        "part": "snippet",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
    }
    resp = requests.get(f"{YOUTUBE_API_BASE}/videos", params=params)
    data = resp.json()

    if resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube videos (по ссылке): {data}")

    items = data.get("items", [])
    if not items:
        raise YouTubeAPIError("Видео по ссылке не найдено, не могу определить канал.")

    snippet = items[0].get("snippet", {})
    channel_id = snippet.get("channelId")
    if not channel_id:
        raise YouTubeAPIError("Не удалось определить channelId из видео.")
    return channel_id


def _resolve_channel_id(channel: Optional[str]) -> str:
    """
    Определяем channelId для любых входных данных:

      - None/пусто              -> YOUTUBE_CHANNEL_ID из .env
      - 'UC...'                 -> используем как есть
      - '@handle'               -> точный запрос через channels?forHandle
      - URL (канал/видео)       -> парсим и берём либо handle, либо channelId, либо videoId
      - обычная строка (имя)    -> search по названию
    """
    _require_api_key()

    # нет входа → дефолтный канал
    if channel is None or channel.strip() == "":
        if not YOUTUBE_CHANNEL_ID:
            raise YouTubeAPIError(
                "Канал по умолчанию не задан: YOUTUBE_CHANNEL_ID в .env пустой."
            )
        return YOUTUBE_CHANNEL_ID

    channel = channel.strip()

    # готовый channelId
    if channel.startswith("UC") and len(channel) >= 20:
        return channel

    # @handle
    if channel.startswith("@"):
        return _get_channel_id_from_handle(channel)

    # URL
    if channel.startswith("http://") or channel.startswith("https://"):
        ids = _extract_ids_from_url(channel)

        if ids["video_id"]:
            return _get_channel_id_from_video(ids["video_id"])

        if ids["channel_id"]:
            return ids["channel_id"]

        if ids["handle"]:
            # здесь уже надёжно берём через forHandle
            return _get_channel_id_from_handle(ids["handle"])

        # если не смогли ничего распарсить — пробуем общий поиск
        return _find_channel_by_search(channel)

    # Просто текст: имя канала / кусок URL
    return _find_channel_by_search(channel)


# ---- основная функция получения видео ----

def fetch_channel_videos(
    max_results: int = 5,
    channel: Optional[str] = None,
) -> List[Dict]:
    """
    Получаем список последних видео канала + статистику по ним.
    Возвращаем список словарей с полями:
      platform, external_id, url, title, views, likes, comments_count
    """
    _require_api_key()

    channel_id = _resolve_channel_id(channel)

    cached = _cache_get(channel_id, max_results)
    if cached is not None:
        return cached

    # 1. Получаем ID последних видео
    search_params = {
        "part": "id",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": "date",
        "type": "video",
        "key": YOUTUBE_API_KEY,
    }
    search_resp = requests.get(f"{YOUTUBE_API_BASE}/search", params=search_params)
    search_data = search_resp.json()

    if search_resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube search (видео): {search_data}")

    video_ids = [item["id"]["videoId"] for item in search_data.get("items", [])]

    if not video_ids:
        # у канала нет видео или они скрыты
        return []

    # 2. Получаем статистику по этим видео
    videos_params = {
        "part": "snippet,statistics",
        "id": ",".join(video_ids),
        "key": YOUTUBE_API_KEY,
    }
    videos_resp = requests.get(f"{YOUTUBE_API_BASE}/videos", params=videos_params)
    videos_data = videos_resp.json()

    if videos_resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube videos: {videos_data}")

    result: List[Dict] = []

    for item in videos_data.get("items", []):
        vid = item["id"]
        snippet = item.get("snippet", {}) or {}
        stats = item.get("statistics", {}) or {}

        title = snippet.get("title", f"Video {vid}")
        url = f"https://www.youtube.com/watch?v={vid}"

        def _to_int(x):
            try:
                return int(x)
            except Exception:
                return 0

        views = _to_int(stats.get("viewCount", 0))
        likes = _to_int(stats.get("likeCount", 0))
        comments = _to_int(stats.get("commentCount", 0))

        result.append(
            {
                "platform": "YouTube",
                "external_id": vid,
                "url": url,
                "title": title,
                "views": views,
                "likes": likes,
                "comments_count": comments,
            }
        )

    _cache_set(channel_id, max_results, result)
    return result