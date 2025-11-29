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
    """
    Оставляем на будущее, сейчас наружу не бросаем.
    """
    pass


def _build_context(items: List[Dict]) -> List[Dict]:
    """
    Нормализуем список items и считаем вовлечённость.
    """
    normalized: List[Dict] = []
    for it in items:
        views = int(it.get("views", 0) or 0)
        likes = int(it.get("likes", 0) or 0)
        comments = int(it.get("comments_count", 0) or 0)
        if views > 0:
            engagement_rate = (likes + comments) / views
        else:
            engagement_rate = 0.0

        normalized.append(
            {
                "platform": it.get("platform", "YouTube"),
                "title": it.get("title", "Без названия"),
                "url": it.get("url", ""),
                "views": views,
                "likes": likes,
                "comments_count": comments,
                "engagement_rate": engagement_rate,
            }
        )
    return normalized


def _generate_local_answer(question: str, items: List[Dict]) -> str:
    """
    Локальная "мини-LLM" без OpenRouter.
    Строит ответ по данным items:
      - считает вовлечённость
      - выбирает топ-3 по просмотрам или по вовлечённости
      - делает короткий вывод
    """
    if not items:
        return "Нет данных о контенте, чтобы ответить на вопрос."

    normalized = _build_context(items)
    n = len(normalized)

    q_lower = question.lower()
    sort_by_engagement = "вовлеч" in q_lower  # ловим 'вовлеченность', 'вовлечённость' и т.п.

    if sort_by_engagement:
        key_name = "engagement_rate"
        title_line = f"Топ-3 материалов по вовлечённости (из последних {n} видео):"
    else:
        key_name = "views"
        title_line = f"Топ-3 материалов по просмотрам (из последних {n} видео):"

    sorted_items = sorted(normalized, key=lambda x: x.get(key_name, 0), reverse=True)
    top3 = sorted_items[:3]

    lines = [title_line, ""]
    for i, it in enumerate(top3, start=1):
        lines.append(
            f"{i}. {it['title']}\n"
            f"   Просмотры: {it['views']}, лайки: {it['likes']}, "
            f"комментарии: {it['comments_count']}, "
            f"вовлечённость: {it['engagement_rate']:.3f}\n"
            f"   Ссылка: {it['url']}"
        )

    # Простейший вывод
    avg_views = sum(it["views"] for it in normalized) / n
    avg_eng = sum(it["engagement_rate"] for it in normalized) / n

    lines.append("")
    lines.append(
        "Вывод: топовые материалы набирают просмотров и вовлечённость выше среднего.\n"
        f"Средние значения по выборке — просмотры: {int(avg_views)}, "
        f"вовлечённость: {avg_eng:.3f}. "
        "Имеет смысл делать больше похожего контента: похожие форматы, темы и длину видео."
    )

    return "\n".join(lines)


def ask_llm(question: str, items: List[Dict]) -> str:
    """
    Главная функция для /chat.

    Логика:
    1) Пытаемся обратиться к OpenRouter (если задан OPENROUTER_API_KEY).
    2) Если не получилось (401, другая ошибка, странный ответ) —
       просто строим ответ локально (_generate_local_answer).
    """
    # Если ключа нет вообще — сразу локальный ответ
    if not OPENROUTER_API_KEY:
        return _generate_local_answer(question, items)

    normalized = _build_context(items)
    if not normalized:
        return "Нет данных о контенте, чтобы ответить на вопрос."

    context_lines = []
    for i, it in enumerate(normalized, start=1):
        line = (
            f"{i}. [{it['platform']}] {it['title']} | "
            f"views={it['views']}, likes={it['likes']}, comments={it['comments_count']}, "
            f"engagement_rate={it['engagement_rate']:.3f}, url={it['url']}"
        )
        context_lines.append(line)

    context_text = "\n".join(context_lines)

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

    # Пытаемся обратиться к OpenRouter
    try:
        resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=60)
    except Exception:
        # Любая сетевая ошибка → локальный ответ
        return _generate_local_answer(question, items)

    # Любой не-200 статус (включая 401) → локальный ответ
    if resp.status_code != 200:
        return _generate_local_answer(question, items)

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or not isinstance(content, str):
            return _generate_local_answer(question, items)
        return content
    except Exception:
        return _generate_local_answer(question, items)