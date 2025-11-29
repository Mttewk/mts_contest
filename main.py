from typing import List, Optional
import os
import re

from fastapi import FastAPI
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from services.youtube_client import fetch_channel_videos, YouTubeAPIError
from services.mws_client import (
    upsert_content_items,
    fetch_content_items,
    MWSClientError,
)
from services.llm_client import ask_llm

# Загружаем переменные окружения из .env
load_dotenv()

app = FastAPI(
    title="MTS Content Analytics",
    description="Сервис для сбора и аналитики контента под хакатон",
    version="0.7.0",
)


@app.get("/ping")
async def ping():
    return JSONResponse({"status": "ok", "message": "pong"})


class ContentItem(BaseModel):
    platform: str
    external_id: str
    url: str
    title: str
    views: int
    likes: int
    comments_count: int


class SyncResult(BaseModel):
    synced: int
    items: List[ContentItem]


class SyncRequest(BaseModel):
    channel: Optional[str] = None  # можно передать другой канал, иначе .env
    max_results: int = 5


class ChatRequest(BaseModel):
    question: str
    channel: Optional[str] = None  # канал для анализа (id/handle/имя/ссылка)


class ChatResponse(BaseModel):
    answer: str


def extract_limit_from_question(question: str, default: int = 5) -> int:
    """
    Пытаемся вытащить из вопроса, сколько последних видео надо анализировать.
    Примеры:
      - "из последних пяти" -> 5
      - "из последних 10"   -> 10
      - "последних десяти"  -> 10
    Если ничего не нашли — используем default.
    Ограничиваем диапазон 3..20.
    """
    q = question.lower()

    # 1. Явные цифры
    nums = re.findall(r"\d+", q)
    if nums:
        try:
            n = int(nums[-1])
            n = max(3, min(20, n))
            return n
        except ValueError:
            pass

    # 2. Простейшее сопоставление по словам
    words_map = {
        "пяти": 5,
        "пятерых": 5,
        "пятерки": 5,
        "десяти": 10,
        "десять": 10,
        "десятка": 10,
    }
    for word, n in words_map.items():
        if word in q:
            return n

    # 3. По умолчанию
    return default


@app.post("/sync", response_model=SyncResult)
async def sync_content(body: SyncRequest):
    """
    1) Берём последние видео с YouTube (по указанному каналу или .env).
    2) Пишем НОВЫЕ записи в MWS (без дублей по external_id).
    3) Возвращаем список материалов, которые сейчас подтянули.
    """
    items: List[ContentItem] = []

    try:
        yt_data = fetch_channel_videos(
            max_results=body.max_results,
            channel=body.channel,
        )

        for it in yt_data:
            items.append(
                ContentItem(
                    platform=it["platform"],
                    external_id=it["external_id"],
                    url=it["url"],
                    title=it["title"],
                    views=it["views"],
                    likes=it["likes"],
                    comments_count=it["comments_count"],
                )
            )
    except YouTubeAPIError as e:
        print(f"[WARN] YouTube API error в /sync: {e}. Используем dummy данные.")
        items = [
            ContentItem(
                platform="YouTube",
                external_id="video_1",
                url="https://youtube.com/watch?v=video_1",
                title="Тестовое видео №1",
                views=1234,
                likes=150,
                comments_count=12,
            ),
            ContentItem(
                platform="YouTube",
                external_id="video_2",
                url="https://youtube.com/watch?v=video_2",
                title="Тестовое видео №2",
                views=5678,
                likes=430,
                comments_count=45,
            ),
        ]

    # Пишем в MWS только новые записи (если MWS настроен)
    try:
        dict_items = [item.dict() for item in items]
        synced_new = upsert_content_items(dict_items)
        print(f"[INFO] В MWS добавлено новых записей: {synced_new}")
    except MWSClientError as e:
        print(f"[WARN] MWS sync error: {e}. Пока просто возвращаем данные без записи в MWS.")

    return SyncResult(synced=len(items), items=items)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Чат-бот: отвечает на вопросы по данным о контенте.
    Канал можно задать прямо в запросе. Если не задан:
    1) Пытаемся взять последние записи из MWS.
    2) Если там пусто или ошибка — берём YouTube по каналу из .env.

    Количество последних видео, которые анализируем, вытаскиваем из текста вопроса
    (из последних пяти/десяти и т.п.).
    """
    dict_items: List[dict] = []

    limit = extract_limit_from_question(request.question, default=5)

    # Если канал явно указан — сразу идём в YouTube
    if request.channel:
        try:
            yt_data = fetch_channel_videos(max_results=limit, channel=request.channel)
            for it in yt_data:
                dict_items.append(
                    {
                        "platform": it["platform"],
                        "external_id": it["external_id"],
                        "url": it["url"],
                        "title": it["title"],
                        "views": it["views"],
                        "likes": it["likes"],
                        "comments_count": it["comments_count"],
                    }
                )
        except YouTubeAPIError as e:
            print(f"[WARN] YouTube API error (для /chat, канал={request.channel}): {e}")
            return ChatResponse(
                answer=(
                    "Не удалось получить данные по этому YouTube-каналу. "
                    "Проверь ID/название/ссылку канала или попробуй другой."
                )
            )
    else:
        # Канал не указан: сначала пробуем MWS, потом YouTube по умолчанию
        try:
            dict_items = fetch_content_items(limit=limit)
        except MWSClientError as e:
            print(f"[WARN] MWS fetch error (для /chat): {e}. Пробуем YouTube.")

        if not dict_items:
            try:
                yt_data = fetch_channel_videos(max_results=limit, channel=None)
                for it in yt_data:
                    dict_items.append(
                        {
                            "platform": it["platform"],
                            "external_id": it["external_id"],
                            "url": it["url"],
                            "title": it["title"],
                            "views": it["views"],
                            "likes": it["likes"],
                            "comments_count": it["comments_count"],
                        }
                    )
            except YouTubeAPIError as e:
                print(f"[WARN] YouTube API error (для /chat, канал по умолчанию): {e}")
                return ChatResponse(
                    answer="Не удалось получить данные ни из MWS, ни из YouTube."
                )

    # 2. Строим ответ (через LLM или локально)
    answer = ask_llm(request.question, dict_items)
    return ChatResponse(answer=answer)


@app.get("/", response_class=HTMLResponse)
async def index():
    """
    Простой фронт с выбором канала и чатиком.
    """
    html = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8" />
        <title>MTS Content Chat</title>
        <style>
            body {
                font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
                max-width: 900px;
                margin: 40px auto;
                padding: 0 16px;
            }
            h1 {
                font-size: 24px;
                margin-bottom: 8px;
            }
            #chat {
                border: 1px solid #333;
                border-radius: 8px;
                padding: 12px;
                height: 400px;
                overflow-y: auto;
                margin-bottom: 12px;
                background: #111;
                color: #eee;
                font-size: 14px;
                white-space: pre-wrap;
            }
            .msg-user {
                margin: 4px 0;
                font-weight: bold;
                color: #9cdcfe;
            }
            .msg-bot {
                margin: 4px 0 10px 0;
                color: #d4d4d4;
            }
            #question, #channel {
                width: 100%;
                padding: 8px;
                font-size: 14px;
                box-sizing: border-box;
                margin-bottom: 8px;
            }
            #channel {
                margin-top: 8px;
            }
            button {
                padding: 8px 14px;
                font-size: 14px;
                cursor: pointer;
                margin-right: 6px;
                margin-top: 4px;
            }
        </style>
    </head>
    <body>
        <h1>MTS Content Chat</h1>
        <p>Задай вопрос по популярности и статистике контента. Можно указать YouTube-канал для анализа.</p>

        <label for="channel">Канал YouTube (ID, @handle, название или ссылка, можно оставить пустым для канала по умолчанию):</label>
        <input id="channel" placeholder="например: https://www.youtube.com/@TheBrianMaps" />

        <div id="chat"></div>
        <textarea id="question" rows="3" placeholder="Например: какое самое популярное видео из последних пяти?"></textarea>
        <br/>
        <button onclick="sendQuestion()">Отправить</button>

        <p>Примеры вопросов:</p>
        <button onclick="setExample('какое самое популярное видео из последних пяти?')">
            Топ-1 из последних 5
        </button>
        <button onclick="setExample('топ-3 видео по просмотрам из последних десяти записей')">
            Топ-3 по просмотрам
        </button>
        <button onclick="setExample('у каких видео из последних десяти самая высокая вовлеченность?')">
            Топ-3 по вовлеченности
        </button>

        <script>
            function setExample(text) {
                const textarea = document.getElementById('question');
                textarea.value = text;
                textarea.focus();
            }

            async function sendQuestion() {
                const textarea = document.getElementById('question');
                const channelInput = document.getElementById('channel');
                const chat = document.getElementById('chat');

                const q = textarea.value.trim();
                const channel = channelInput.value.trim();
                if (!q) return;

                const userMsg = document.createElement('div');
                userMsg.className = 'msg-user';
                userMsg.textContent = 'Вы: ' + q + (channel ? ' (канал: ' + channel + ')' : '');
                chat.appendChild(userMsg);

                textarea.value = '';
                chat.scrollTop = chat.scrollHeight;

                try {
                    const resp = await fetch('/chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            question: q,
                            channel: channel || null
                        })
                    });

                    const data = await resp.json();
                    const botMsg = document.createElement('div');
                    botMsg.className = 'msg-bot';

                    if (!resp.ok) {
                        botMsg.textContent = 'Ошибка: ' + (data.detail || 'что-то пошло не так');
                    } else {
                        const safe = (data.answer || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        botMsg.innerHTML = 'Бот: ' + safe.replace(/\\n/g, '<br>');
                    }

                    chat.appendChild(botMsg);
                    chat.scrollTop = chat.scrollHeight;
                } catch (e) {
                    const botMsg = document.createElement('div');
                    botMsg.className = 'msg-bot';
                    botMsg.textContent = 'Ошибка запроса: ' + e;
                    chat.appendChild(botMsg);
                    chat.scrollTop = chat.scrollHeight;
                }
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)