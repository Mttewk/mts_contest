from typing import List
import os

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
from services.llm_client import ask_llm, LLMClientError

# Загружаем переменные окружения из .env
load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

app = FastAPI(
    title="MTS Content Analytics",
    description="Сервис для сбора и аналитики контента под хакатон",
    version="0.5.0",
)


@app.get("/ping")
async def ping():
    """Проверка, что сервер жив."""
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
    synced: int          # сколько элементов использовали при /sync
    items: List[ContentItem]


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str


@app.post("/sync", response_model=SyncResult)
async def sync_content():
    """
    1) Берём последние видео с YouTube.
    2) Пишем НОВЫЕ записи в MWS (без дублей по external_id).
    3) Возвращаем список материалов, которые сейчас подтянули.
    """
    items: List[ContentItem] = []

    # 1. Пытаемся забрать данные с YouTube
    try:
        yt_data = fetch_channel_videos(max_results=5)

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
        print(f"[WARN] YouTube API error: {e}. Используем dummy данные.")
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

    # 2. Пишем в MWS только новые записи
    try:
        dict_items = [item.dict() for item in items]
        synced_new = upsert_content_items(dict_items)
        print(f"[INFO] В MWS добавлено новых записей: {synced_new}")
    except MWSClientError as e:
        print(f"[WARN] MWS sync error: {e}. Пока просто возвращаем данные без записи в MWS.")

    # 3. Отдаём данные наружу
    return SyncResult(
        synced=len(items),
        items=items,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Чат-бот: отвечает на вопросы по данным о контенте.

    Логика:
    1) Сначала берём последние N записей из MWS (таблица реестра).
    2) Если MWS недоступен или таблица пустая — используем YouTube/dummy.
    3) Отправляем вопрос + контекст в LLM.
    """
    dict_items: List[dict] = []

    # 1. Пробуем взять данные из MWS
    try:
        dict_items = fetch_content_items(limit=5)
    except MWSClientError as e:
        print(f"[WARN] MWS fetch error (для /chat): {e}. Пробуем YouTube.")

    # Если из MWS взять не получилось или там пусто — fallback на YouTube/dummy
    if not dict_items:
        try:
            yt_data = fetch_channel_videos(max_results=5)
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
            print(f"[WARN] YouTube API error (для /chat): {e}. Используем dummy данные.")
            dict_items = [
                {
                    "platform": "YouTube",
                    "external_id": "video_1",
                    "url": "https://youtube.com/watch?v=video_1",
                    "title": "Тестовое видео №1",
                    "views": 1234,
                    "likes": 150,
                    "comments_count": 12,
                },
                {
                    "platform": "YouTube",
                    "external_id": "video_2",
                    "url": "https://youtube.com/watch?v=video_2",
                    "title": "Тестовое видео №2",
                    "views": 5678,
                    "likes": 430,
                    "comments_count": 45,
                },
            ]

    # 2. Спрашиваем LLM
    try:
        answer = ask_llm(request.question, dict_items)
        return ChatResponse(answer=answer)
    except LLMClientError as e:
        print(f"[ERROR] LLM error: {e}")

        if dict_items:
            top = max(dict_items, key=lambda x: x.get("views", 0))
            fallback_answer = (
                "Сейчас LLM временно недоступна, поэтому отвечаю без неё. "
                f"По данным видно, что самое популярное видео: "
                f"«{top.get('title')}» с {top.get('views', 0)} просмотрами "
                f"и {top.get('likes', 0)} лайками."
            )
        else:
            fallback_answer = (
                "Сейчас LLM временно недоступна, и данных о контенте тоже нет."
            )

        return ChatResponse(answer=fallback_answer)


@app.get("/", response_class=HTMLResponse)
async def index():
    """
    Простая HTML-страница с чатиком.
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
                max-width: 800px;
                margin: 40px auto;
                padding: 0 16px;
            }
            h1 {
                font-size: 24px;
                margin-bottom: 8px;
            }
            #chat {
                border: 1px solid #ddd;
                border-radius: 8px;
                padding: 12px;
                height: 400px;
                overflow-y: auto;
                margin-bottom: 12px;
                background: #fafafa;
                font-size: 14px;
            }
            .msg-user {
                margin: 4px 0;
                font-weight: bold;
            }
            .msg-bot {
                margin: 4px 0 10px 0;
            }
            #question {
                width: 100%;
                padding: 8px;
                font-size: 14px;
                box-sizing: border-box;
                margin-bottom: 8px;
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
        <p>Задай вопрос по популярности и статистике контента (по данным MWS / YouTube).</p>
        <div id="chat"></div>
        <textarea id="question" rows="3" placeholder="Например: какoe самое популярное видео из последних пяти?"></textarea>
        <br/>
        <button onclick="sendQuestion()">Отправить</button>

        <p>Примеры вопросов:</p>
        <button onclick="setExample('какое самое популярное видео из последних пяти?')">
            Топ-1 из последних 5
        </button>
        <button onclick="setExample('топ-3 видео по просмотрам из последних пяти записей')">
            Топ-3 по просмотрам
        </button>
        <button onclick="setExample('у каких видео из последних пяти самая высокая вовлеченность?')">
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
                const chat = document.getElementById('chat');
                const q = textarea.value.trim();
                if (!q) return;

                const userMsg = document.createElement('div');
                userMsg.className = 'msg-user';
                userMsg.textContent = 'Вы: ' + q;
                chat.appendChild(userMsg);

                textarea.value = '';
                chat.scrollTop = chat.scrollHeight;

                try {
                    const resp = await fetch('/chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({question: q})
                    });

                    const data = await resp.json();
                    const botMsg = document.createElement('div');
                    botMsg.className = 'msg-bot';

                    if (!resp.ok) {
                        botMsg.textContent = 'Ошибка: ' + (data.detail || 'что-то пошло не так');
                    } else {
                        const safe = (data.answer || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        botMsg.innerHTML = 'Бот:<br>' + safe.replace(/\\n/g, '<br>');
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