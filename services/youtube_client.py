# services/youtube_client.py
import os
from typing import List, Dict

from dotenv import load_dotenv
import requests

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAPIError(Exception):
    pass


def fetch_channel_videos(max_results: int = 5) -> List[Dict]:
    """
    Получаем список последних видео канала + статистику по ним.
    Возвращаем список словарей:
    {
        "platform": "YouTube",
        "external_id": "...",
        "url": "...",
        "title": "...",
        "views": int,
        "likes": int,
        "comments_count": int,
    }
    """
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        raise YouTubeAPIError("Не заданы YOUTUBE_API_KEY или YOUTUBE_CHANNEL_ID в .env")

    # 1. Получаем ID последних видео канала
    search_params = {
        "part": "id",
        "channelId": YOUTUBE_CHANNEL_ID,
        "maxResults": max_results,
        "order": "date",
        "type": "video",
        "key": YOUTUBE_API_KEY,
    }
    search_resp = requests.get(f"{YOUTUBE_API_BASE}/search", params=search_params)
    search_data = search_resp.json()

    if search_resp.status_code != 200:
        raise YouTubeAPIError(f"Ошибка YouTube search: {search_data}")

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