# services/llm_client.py
import os
from typing import List, Dict, Tuple

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
    """Оставляем на будущее, сейчас наружу почти не бросаем."""
    pass


# ==== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ПО АНАЛИТИКЕ ====


def _normalize_items(items: List[Dict]) -> List[Dict]:
    """
    Приводим список items к нормальному виду и считаем вовлечённость.
    Ожидаем поля: platform, title, url, views, likes, comments_count.
    """
    normalized: List[Dict] = []
    for it in items:
        try:
            views = int(it.get("views", 0) or 0)
        except Exception:
            views = 0
        try:
            likes = int(it.get("likes", 0) or 0)
        except Exception:
            likes = 0
        try:
            comments = int(it.get("comments_count", 0) or 0)
        except Exception:
            comments = 0

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


def _detect_top_n(question: str, default: int = 3) -> int:
    """
    Пытаемся понять, сколько элементов хочет пользователь:
    - "топ-3", "топ 5", "три видео", "пять роликов" и т.п.
    Если ничего не нашли — возвращаем default.
    """
    q = question.lower()

    # Явные цифры
    import re
    nums = re.findall(r"\d+", q)
    if nums:
        try:
            n = int(nums[-1])
            if 1 <= n <= 10:
                return n
        except Exception:
            pass

    # Несколько базовых слов
    words_map = {
        "три": 3,
        "топ-3": 3,
        "топ 3": 3,
        "пять": 5,
        "топ-5": 5,
        "топ 5": 5,
    }
    for word, n in words_map.items():
        if word in q:
            return n

    return default


def _classify_question(question: str) -> Dict:
    """
    Классифицируем вопрос:
      - по какому критерию сортировать (views / engagement)
      - в каком направлении (лучшие / худшие)
      - нужно ли делать упор на рекомендации
    """
    q = question.lower()

    by_engagement = "вовлеч" in q or "engagement" in q
    by_likes = "лайк" in q or "реакц" in q
    by_comments = "коммент" in q

    worst = "худш" in q or "плох" in q or "низк" in q

    recommend = (
        "что делать" in q
        or "как улучшить" in q
        or "рекомендац" in q
        or "совет" in q
    )

    # если явно про популярность / просмотры
    if "популяр" in q or "больше всего просмотров" in q or "самое популярное" in q:
        metric = "views"
    # если явно про вовлечённость / лайки / комментарии
    elif by_engagement or by_likes or by_comments:
        metric = "engagement"
    else:
        # по умолчанию смотрим на просмотры, но в ответе учитываем и engagement
        metric = "views"

    return {
        "metric": metric,       # "views" или "engagement"
        "worst": worst,         # True -> ищем худшие
        "recommend": recommend, # True -> делать упор на рекомендации
    }


def _sort_items(normalized: List[Dict], metric: str, worst: bool) -> List[Dict]:
    """
    Сортируем элементы по нужной метрике.
    metric: "views" или "engagement"
    worst: True -> от худших к лучшим
    """
    if metric == "engagement":
        key_name = "engagement_rate"
    else:
        key_name = "views"

    reverse = not worst  # лучшие -> по убыванию; худшие -> по возрастанию
    return sorted(normalized, key=lambda x: x.get(key_name, 0), reverse=reverse)


def _summary_stats(normalized: List[Dict]) -> Tuple[float, float]:
    """
    Считаем средние значения:
      - по просмотрам
      - по вовлечённости
    """
    if not normalized:
        return 0.0, 0.0

    n = len(normalized)
    avg_views = sum(it["views"] for it in normalized) / n
    avg_eng = sum(it["engagement_rate"] for it in normalized) / n
    return avg_views, avg_eng


# ==== ЛОКАЛЬНЫЙ ОТВЕТ, ЕСЛИ LLM НЕДОСТУПНА ====


def _generate_local_answer(question: str, items: List[Dict]) -> str:
    """
    Локальная "мини-нейросетка" без OpenRouter.
    Пытается вести себя умно:
      - понимает, лучшее или худшее спрашивают;
      - по просмотрам или вовлечённости;
      - сколько элементов (топ-N);
      - даёт вывод и мини-рекомендации.
    """
    if not items:
        return "Нет данных о контенте, чтобы ответить на вопрос."

    normalized = _normalize_items(items)
    n_all = len(normalized)

    q_info = _classify_question(question)
    top_n = _detect_top_n(question, default=3)

    metric = q_info["metric"]
    worst = q_info["worst"]
    recommend = q_info["recommend"]

    if metric == "engagement":
        metric_title = "вовлечённости"
        metric_name = "engagement_rate"
    else:
        metric_title = "просмотрам"
        metric_name = "views"

    sorted_items = _sort_items(normalized, metric=metric, worst=worst)
    top_list = sorted_items[: top_n]

    avg_views, avg_eng = _summary_stats(normalized)

    # Заголовок
    direction = "лучших" if not worst else "худших"
    header = f"Топ-{len(top_list)} {direction} материалов по {metric_title} (из последних {n_all} видео):"

    lines: List[str] = [header, ""]

    for i, it in enumerate(top_list, start=1):
        lines.append(
            f"{i}. {it['title']}\n"
            f"   Просмотры: {it['views']}, лайки: {it['likes']}, "
            f"комментарии: {it['comments_count']}, "
            f"вовлечённость: {it['engagement_rate']:.3f}\n"
            f"   Ссылка: {it['url']}"
        )

    lines.append("")
    # Общий вывод
    lines.append(
        "Сводка по выборке:\n"
        f"  • Средние просмотры: {int(avg_views)}\n"
        f"  • Средняя вовлечённость: {avg_eng:.3f}"
    )

    # Мини-рекомендации
    if recommend or True:
        best_by_eng = max(normalized, key=lambda x: x["engagement_rate"])
        best_by_views = max(normalized, key=lambda x: x["views"])
        lines.append("")
        lines.append("Рекомендации:")
        lines.append(
            f"  • Форматы, похожие на «{best_by_eng['title']}», дают высокую вовлечённость — "
            "их можно использовать для укрепления лояльности аудитории."
        )
        lines.append(
            f"  • Ролики уровня «{best_by_views['title']}» по просмотрам хорошо подходят "
            "для охватных кампаний и интеграций."
        )
        lines.append(
            "  • Стоит избегать копирования самых слабых роликов (с вовлечённостью ниже среднего) "
            "и протестировать другие темы/длительность/креатив."
        )

    return "\n".join(lines)


# ==== ОСНОВНАЯ ФУНКЦИЯ ДЛЯ /chat ====


def ask_llm(question: str, items: List[Dict]) -> str:
    """
    Главная функция, которую вызывает /chat.

    Логика:
      1) Нормализуем данные по видео.
      2) Классифицируем вопрос (по просмотрам / вовлечённости / худшие / рекомендации).
      3) Если есть ключ OpenRouter — пытаемся спросить внешнюю LLM.
      4) Если что-то пошло не так — возвращаем умный локальный ответ.
    """
    normalized = _normalize_items(items)
    if not normalized:
        return "Нет данных о контенте, чтобы ответить на вопрос."

    # Если ключа нет вообще — сразу локальный ответ
    if not OPENROUTER_API_KEY:
        return _generate_local_answer(question, normalized)

    q_info = _classify_question(question)
    top_n = _detect_top_n(question, default=3)
    avg_views, avg_eng = _summary_stats(normalized)

    # Строим текст контекста для LLM
    context_lines = []
    for i, it in enumerate(normalized, start=1):
        context_lines.append(
            f"{i}. [{it['platform']}] {it['title']}\n"
            f"   views={it['views']}, likes={it['likes']}, comments={it['comments_count']}, "
            f"engagement_rate={it['engagement_rate']:.4f}, url={it['url']}"
        )
    context_text = "\n".join(context_lines)

    system_prompt = (
        "Ты аналитик контента в крупной компании (например, МТС).\n"
        "У тебя есть список видео с просмотрами, лайками, комментариями и метрикой вовлечённости "
        "(engagement_rate = (likes + comments) / views).\n"
        "Отвечай структурировано, по делу, на русском, без воды. "
        "Не придумывай свои числа — опирайся только на переданные данные."
    )

    # Подсказка для модели, как мы уже предварительно классифицировали вопрос
    metric = q_info["metric"]
    worst = q_info["worst"]
    recommend = q_info["recommend"]

    if metric == "engagement":
        metric_desc = "вовлечённости (engagement_rate)"
    else:
        metric_desc = "просмотрам (views)"

    direction_desc = "лучшие" if not worst else "худшие"

    analysis_hint = (
        f"Предварительный разбор вопроса:\n"
        f"- Основной критерий: {metric_desc}\n"
        f"- Направление: {direction_desc}\n"
        f"- Запрошенное количество элементов (примерно): топ-{top_n}\n"
        f"- Нужны ли рекомендации: {'да' if recommend else 'желательно, но не обязательно'}\n\n"
        f"Также доступны агрегаты по всей выборке:\n"
        f"- Средние просмотры: {int(avg_views)}\n"
        f"- Средняя вовлечённость: {avg_eng:.4f}\n\n"
        f"Используй эти подсказки, но если они противоречат буквальной формулировке вопроса, "
        f"ориентируйся на сам вопрос."
    )

    user_prompt = (
        f"Вот данные о материалах (по одному видео на блок):\n"
        f"{context_text}\n\n"
        f"{analysis_hint}\n"
        f"Вопрос пользователя: {question}\n\n"
        "Как отвечать:\n"
        "1) Определи, по какому критерию уместнее сравнивать (просмотры, вовлечённость или комбинация), "
        "и явно объясни это в 1–2 предложениях.\n"
        f"2) Выведи список топ-{top_n} видео (или меньше, если данных мало) по выбранному критерию, "
        "в формате: название, просмотры, лайки, комментарии, engagement_rate и ссылка.\n"
        "3) Если вопрос про худшие видео — выбери соответствующие элементы (с самыми низкими значениями).\n"
        "4) В конце дай короткий вывод: что общего у успешных роликов, и что можно улучшить в контенте.\n"
        "5) Не используй markdown-таблицы, достаточно обычного текста с маркированным или нумерованным списком.\n"
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
        return _generate_local_answer(question, normalized)

    # Любой не-200 статус (401/403/500 и т.п.) → локальный ответ
    if resp.status_code != 200:
        # Можно тихо залогировать, но не шумим:
        # print(f"[WARN] OpenRouter error: {resp.status_code} {resp.text}")
        return _generate_local_answer(question, normalized)

    # Пытаемся распарсить ответ
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or not isinstance(content, str):
            return _generate_local_answer(question, normalized)
        return content
    except Exception:
        return _generate_local_answer(question, normalized)