# services/llm_client.py
import os
from typing import List, Dict

from dotenv import load_dotenv
import requests

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL",
    "meta-llama/llama-3.3-70b-instruct:free",
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMClientError(Exception):
    pass


def ask_llm(question: str, items: List[Dict]) -> str:
    """
    Отправляет вопрос + контекст по контенту в LLM
    и возвращает текстовый ответ.
    items — список словарей с полями:
      title, views, likes, comments_count, platform, url
    """
    if not OPENROUTER_API_KEY:
        raise LLMClientError("Не задан OPENROUTER_API_KEY в .env")

    # Сортируем по просмотрам по убыванию, чтобы LLM видела топ сверху
    sorted_items = sorted(items, key=lambda x: x.get("views", 0), reverse=True)

    # Собираем компактный контекст + считаем вовлечённость
    context_lines = []
    for i, it in enumerate(sorted_items, start=1):
        views = int(it.get("views", 0) or 0)
        likes = int(it.get("likes", 0) or 0)
        comments = int(it.get("comments_count", 0) or 0)
        if views > 0:
            engagement_rate = (likes + comments) / views
        else:
            engagement_rate = 0.0

        line = (
            f"{i}. [{it.get('platform', '?')}] {it.get('title', 'Без названия')} | "
            f"views={views}, likes={likes}, comments={comments}, "
            f"engagement_rate={engagement_rate:.3f}, url={it.get('url')}"
        )
        context_lines.append(line)

    context_text = "\n".join(context_lines) if context_lines else "Данных о контенте нет."

    system_prompt = (
        "Ты аналитик контента в крупной компании. "
        "У тебя есть список видео с просмотрами, лайками, комментариями и метрикой вовлеченности "
        "(engagement_rate = (likes + comments) / views). "
        "Отвечай структурировано, коротко, на русском, без воды."
    )

    user_prompt = (
        f"Вот данные о материалах (по одному на строку):\n"
        f"{context_text}\n\n"
        f"Вопрос пользователя: {question}\n\n"
        "Как отвечать:\n"
        "1) Если вопрос про самое популярное видео — выведи список топ-3 видео по просмотрам "
        "(views) с указанием views, likes, engagement_rate и коротким комментарием.\n"
        "2) Если вопрос про вовлеченность — ориентируйся на engagement_rate и тоже дай топ-3.\n"
        "3) Если вопрос общий — сам выбери разумный критерий (views + engagement_rate) и объясни выбор.\n"
        "4) В конце добавь короткий вывод (1–2 предложения), что можно улучшить в контенте.\n"
        "Отвечай в виде короткого текста с маркированным списком."
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=60)
    if resp.status_code != 200:
        raise LLMClientError(
            f"Ошибка OpenRouter: {resp.status_code} {resp.text}"
        )

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        raise LLMClientError(f"Неожиданный формат ответа от LLM: {e} | data={data}")