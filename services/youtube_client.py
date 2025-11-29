# services/youtube_client.py
import os
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


def _resolve_channel_id(channel: Optional[str]) -> str:
    """
    Определяем channelId:
    - если channel=None -> берём YOUTUBE_CHANNEL_ID из .env
    - если начинается с 'UC' и длиной ~24 символа -> считаем, что это уже channelId
    - иначе -> делаем search по строке и берём первый найденный канал
      (подходит для 'kuplinovplay', '@kuplinovplay', URL и пр.)
    """
    _require_api_key()

    if channel is None or channel.strip() == "":
        if not YOUTUBE_CHANNEL_ID:
            raise YouTubeAPIError(
                "Канал по умолчанию не задан: YOUTUBE_CHANNEL_ID в .env пустой."
            )
        return YOUTUBE_CHANNEL_ID

    channel = channel.strip()

    # Похоже на готовый channelId
    if channel.startswith("UC") and len(channel) >= 20:
        return channel

    # Иначе ищем по строке
    params = {
        "part": "snippet",
        "type": "channel",
        "q": channel,
        "maxResults": 1,
        "key": YOUTUBE_API_KEY,
    }
    resp = requests.get(f"{YOUTUBE_API_BASE}/search", params=params)
    data = resp.json()

    if resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube search (канал): {data}")

    items = data.get("items", [])
    if not items:
        raise YouTubeAPIError(f"Канал по запросу '{channel}' не найден")

    channel_id = items[0]["snippet"]["channelId"]
    return channel_id


def fetch_channel_videos(
    max_results: int = 5,
    channel: Optional[str] = None,
) -> List[Dict]:
    """
    Получаем список последних видео канала + статистику по ним.

    channel:
        - None  -> берём канал из YOUTUBE_CHANNEL_ID
        - "UC..." (channelId) -> используем как есть
        - любое другое (имя, @handle, часть URL) -> пытаемся найти канал через search.
    """
    _require_api_key()

    channel_id = _resolve_channel_id(channel)

    # 1. Получаем ID последних видео канала
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
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})

        title = snippet.get("title", f"Video {vid}")
        url = f"https://www.youtube.com/watch?v={vid}"

        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0)) if "likeCount" in stats else 0
        comments = int(stats.get("commentCount", 0)) if "commentCount" in stats else 0

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

    return result