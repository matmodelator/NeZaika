# =========================================================
# КОНТЕНТ-ПАКЕТ В НАЧАЛЕ КАЖДОЙ ВЫДАЧИ, ДАЖЕ БЕЗ НОВОСТЕЙ; СТОРИ: UTF-8 / BOM / CP1251
# | 3.8.10
# =========================================================





from pathlib import Path
import os
import sys
import time
import threading
import math
import re
import json
import base64
import csv
import html
import xml.etree.ElementTree as ET
import requests

from html.parser import HTMLParser
from urllib.parse import urljoin

from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from pyluach.dates import HebrewDate


def print_initial_comment():
    """Печатает верхнюю шапку файла без дублирования текста/версии."""
    try:
        with open(__file__, "r", encoding="utf-8") as file:
            header = []
            started = False

            for raw_line in file:
                line = raw_line.rstrip("\n")

                if line.startswith("# ==="):
                    if not started:
                        started = True
                        header.append(line[2:])
                        continue

                    header.append(line[2:])
                    break

                if started:
                    if line.startswith("# "):
                        header.append(line[2:])
                    elif line == "#":
                        header.append("")
                    else:
                        break

        if header:
            print("\n".join(header))

    except Exception as error:
        print("Шапка файла не прочитана:", error)


print_initial_comment()


# =========================================================
# 0. ОБЩИЕ НАСТРОЙКИ
# =========================================================

BOT_TOKEN = "8843774698:AAGoaYTS4zask-N9HtesZ2v9pbx_1MCrbLY"
CHANNEL = "@ne_zaika"

TZ = ZoneInfo("Asia/Jerusalem")

# Глобальный безопасный лимит для любого Telegram-поста.
TELEGRAM_TEXT_LIMIT = 4000

# Все sendMessage проходят через одну очередь.
# Telegram рекомендует избегать >1 сообщения/сек. в один чат.
TELEGRAM_SEND_LOCK = threading.Lock()
TELEGRAM_LAST_SEND_AT = 0.0
TELEGRAM_MIN_SEND_INTERVAL = 1.10


def _telegram_send_post(data, timeout=30, max_retries=8):
    """
    Общий шлюз Telegram sendMessage для ВСЕХ модулей.
    Последовательно отправляет сообщения в канал/чат,
    выдерживает минимальный интервал и уважает retry_after при HTTP 429.
    """
    global TELEGRAM_LAST_SEND_AT

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    with TELEGRAM_SEND_LOCK:
        for attempt in range(max_retries + 1):
            elapsed = time.monotonic() - TELEGRAM_LAST_SEND_AT
            wait_before = TELEGRAM_MIN_SEND_INTERVAL - elapsed

            if wait_before > 0:
                time.sleep(wait_before)

            response = requests.post(
                url,
                data=data,
                timeout=timeout,
            )

            # Отмечаем сам факт попытки, чтобы следующий поток
            # не отправлял сообщение немедленно следом.
            TELEGRAM_LAST_SEND_AT = time.monotonic()

            if response.status_code != 429:
                return response

            retry_after = 1.0

            try:
                body = response.json()
                retry_after = float(
                    (
                        body.get("parameters")
                        or {}
                    ).get("retry_after")
                    or 1
                )
            except Exception:
                pass

            # Небольшой запас, чтобы не попасть в тот же лимит повторно.
            retry_after = max(retry_after, 1.0) + 0.25

            print(
                f"{log_time()} | TELEGRAM 429:",
                f"retry_after={retry_after:.2f} сек.",
                f"попытка {attempt + 1}/{max_retries + 1}"
            )

            time.sleep(retry_after)
            TELEGRAM_LAST_SEND_AT = time.monotonic()

        return response


# Общий цикл диспетчера: одна проверка в минуту.
SCHEDULER_INTERVAL = 60

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

def state_file(name):
    return os.path.join(
        BASE_DIR,
        name
    )


def log_time():
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M:%S")


def log_line(*parts):
    print(
        f"{log_time()} |",
        *parts
    )


# =========================================================
# 0.1. ОБЩИЕ HTTP-ФУНКЦИИ
# =========================================================

def get_json(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


# =========================================================
# 0.2. ОБЩИЕ TELEGRAM-ФУНКЦИИ
# =========================================================

def _truncate_telegram_text(
    text,
    limit=TELEGRAM_TEXT_LIMIT
):
    """
    Глобально ограничивает ЛЮБОЙ Telegram-текст.

    Стараемся не резать посередине строки.
    Если текст уже <= limit, возвращаем без изменений.
    """
    if text is None:
        text = ""

    text = str(text)

    if len(text) <= limit:
        return text, False

    cut = text[:limit]

    # Если возможно — заканчиваем на последней полной строке.
    last_newline = cut.rfind("\n")

    if last_newline >= int(limit * 0.75):
        cut = cut[:last_newline]

    cut = cut.rstrip()

    return cut, True


def send_message(text):

    original_length = len(
        str(text or "")
    )

    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    final_length = len(
        safe_text
    )

    print(
        f"{log_time()} | TELEGRAM SEND:",
        f"{final_length}/{TELEGRAM_TEXT_LIMIT} символов",
        (
            f"(исходно {original_length}, ОБРЕЗАНО)"
            if truncated
            else "(без обрезки)"
        )
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
        },
        timeout=30,
    )

    if not response.ok:
        print(
            "TELEGRAM SEND — HTTP ОШИБКА:",
            response.status_code,
            response.text
        )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        print(
            "TELEGRAM SEND — API ОШИБКА:",
            result
        )
        raise RuntimeError(result)

    message_id = (
        result["result"]["message_id"]
    )

    print(
        "TELEGRAM SEND: OK",
        f"message_id={message_id}"
    )

    return message_id


def edit_message(message_id, text):

    original_length = len(
        str(text or "")
    )

    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    final_length = len(
        safe_text
    )

    print(
        f"{log_time()} | TELEGRAM EDIT:",
        f"{final_length}/{TELEGRAM_TEXT_LIMIT} символов",
        (
            f"(исходно {original_length}, ОБРЕЗАНО)"
            if truncated
            else "(без обрезки)"
        )
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/editMessageText"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHANNEL,
            "message_id": message_id,
            "text": safe_text,
        },
        timeout=30,
    )

    if not response.ok:
        print(
            "TELEGRAM EDIT — HTTP ОШИБКА:",
            response.status_code,
            response.text
        )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        print(
            "TELEGRAM EDIT — API ОШИБКА:",
            result
        )
        raise RuntimeError(result)

    edited = result.get(
        "result",
        {}
    )

    print(
        "TELEGRAM EDIT: OK",
        f"message_id={edited.get('message_id', message_id)}"
    )

    return edited.get(
        "message_id",
        message_id
    )


def load_id_file(filename):
    if not os.path.exists(filename):
        return None

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:
            return int(file.read().strip())

    except Exception:
        return None


def save_id_file(filename, message_id):
    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(str(message_id))


def update_persistent_post(text, filename):
    message_id = load_id_file(filename)

    if message_id is None:
        message_id = send_message(text)
        save_id_file(filename, message_id)
        return message_id

    try:
        edit_message(message_id, text)
        return message_id

    except Exception as error:
        # Если постоянный пост был удалён вручную,
        # создаём новый и запоминаем новый message_id.
        description = str(error).lower()

        if (
            "message to edit not found" in description
            or "message can't be edited" in description
            or "message_id_invalid" in description
        ):
            message_id = send_message(text)
            save_id_file(filename, message_id)
            return message_id

        raise



# =========================================================
# 0.3. СЕРВИСЫ — ПОСТ-НАВИГАТОР
# | 3.8.0
# =========================================================
# Один постоянный ФОТО-пост со ссылками на последние сервисные
# сообщения. После обновления погоды / рейсов / валют /
# мирового времени навигатор редактируется автоматически.
# =========================================================

SERVICES_MESSAGE_ID_FILE = state_file("services_message_id.txt")
TIME_MESSAGE_ID_FILE = state_file("time_message_id.txt")

# Картинка закреплённого сервисного поста.
# Файл services_banner.png должен лежать рядом с ne_zaika_bot.py.
SERVICES_BANNER_FILE = Path(__file__).resolve().parent / "services_banner.png"


def telegram_channel_message_url(message_id):
    if not message_id:
        return None

    channel_name = str(CHANNEL or "").strip()
    if channel_name.startswith("@"):
        channel_name = channel_name[1:]

    if not channel_name:
        return None

    return f"https://t.me/{channel_name}/{int(message_id)}"


def _service_link(label, message_id):
    url = telegram_channel_message_url(message_id)

    if not url:
        return f"{label} — пока нет"

    return (
        f'<a href="{html.escape(url, quote=True)}">'
        f'{html.escape(label)}</a>'
    )


def make_services_text():
    weather_id = load_id_file(WEATHER_MESSAGE_ID_FILE)

    arrivals_actual_id = load_id_file(
        ARRIVALS_ACTUAL_MESSAGE_ID_FILE
    )
    arrivals_next_id = load_id_file(
        ARRIVALS_NEXT_MESSAGE_ID_FILE
    )
    departures_actual_id = load_id_file(
        DEPARTURES_ACTUAL_MESSAGE_ID_FILE
    )
    departures_next_id = load_id_file(
        DEPARTURES_NEXT_MESSAGE_ID_FILE
    )
    alerts_id = load_id_file(
        FLIGHT_ALERTS_MESSAGE_ID_FILE
    )

    rates_id = load_id_file(RATES_MESSAGE_ID_FILE)
    time_id = load_id_file(TIME_MESSAGE_ID_FILE)

    lines = [
        "📌 <b>СЕРВИСЫ СОЛНЕЧНОГО ГОРОДА</b>",
        "",
        _service_link("🌤 Погода в Хайфе", weather_id),
        "",
        "✈️ <b>БЕН-ГУРИОН</b>",
        _service_link(
            "🛬 Прилёты — фактические",
            arrivals_actual_id
        ),
        _service_link(
            "🛬 Прилёты — ближайшие",
            arrivals_next_id
        ),
        _service_link(
            "🛫 Вылеты — фактические",
            departures_actual_id
        ),
        _service_link(
            "🛫 Вылеты — ближайшие",
            departures_next_id
        ),
        _service_link(
            "⚠️ Изменения",
            alerts_id
        ),
        "",
        _service_link("💱 Курсы валют", rates_id),
        _service_link("🌍 Мировое время", time_id),
        "",
        (
            "🕒 Обновлено: "
            f"{datetime.now(TZ).strftime('%d.%m.%Y %H:%M:%S')}"
        ),
        "",
        "@ne_zaika",
    ]

    return "\n".join(lines)


def _send_services_post(text):
    """
    Создаёт сервисный пост как ФОТО + подпись со ссылками.
    """
    if not SERVICES_BANNER_FILE.exists():
        raise FileNotFoundError(
            f"Не найдена картинка сервисов: {SERVICES_BANNER_FILE}"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendPhoto"
    )

    with SERVICES_BANNER_FILE.open("rb") as photo_file:
        response = requests.post(
            url,
            data={
                "chat_id": CHANNEL,
                "caption": text,
                "parse_mode": "HTML",
            },
            files={
                "photo": (
                    SERVICES_BANNER_FILE.name,
                    photo_file,
                    "image/png",
                )
            },
            timeout=60,
        )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(data)

    return data["result"]["message_id"]


def _edit_services_post(message_id, text):
    """
    Обновляет подпись уже существующего сервисного фото-поста.
    Картинка и message_id остаются прежними.
    """
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/editMessageCaption"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHANNEL,
            "message_id": message_id,
            "caption": text,
            "parse_mode": "HTML",
        },
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(data)

    return data["result"]["message_id"]


def unpin_services_post(message_id):
    """
    Снимает закрепление со старого сервисного поста.
    Ошибка не останавливает сервер.
    """
    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/unpinChatMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": CHANNEL,
                "message_id": message_id,
            },
            timeout=20,
        )

        if response.ok:
            data = response.json()
            if data.get("ok"):
                log_line(
                    "СЕРВИСЫ: старый пост откреплён",
                    f"message_id={message_id}"
                )
                return True

    except Exception as exc:
        log_line(
            "СЕРВИСЫ: ошибка снятия старого закрепления:",
            exc
        )

    return False


def pin_services_post(message_id):
    """
    Пытаемся закрепить навигатор без уведомления.
    Если у бота нет права pin_messages, работа остальных
    модулей не прерывается.
    """
    try:
        url = (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/pinChatMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": CHANNEL,
                "message_id": message_id,
                "disable_notification": True,
            },
            timeout=20,
        )

        if response.ok:
            data = response.json()
            if data.get("ok"):
                log_line(
                    "СЕРВИСЫ: навигационный пост закреплён",
                    f"message_id={message_id}"
                )
                return True

        log_line(
            "СЕРВИСЫ: закрепление недоступно:",
            response.text[:300]
        )

    except Exception as exc:
        log_line(
            "СЕРВИСЫ: ошибка закрепления:",
            exc
        )

    return False


def update_services_post():
    """
    Создаёт навигатор один раз, затем только редактирует его.
    """
    text = make_services_text()
    message_id = load_id_file(
        SERVICES_MESSAGE_ID_FILE
    )

    if message_id is None:
        message_id = _send_services_post(text)
        save_id_file(
            SERVICES_MESSAGE_ID_FILE,
            message_id
        )

        log_line(
            "СЕРВИСЫ: создан навигационный пост",
            f"message_id={message_id}"
        )

        pin_services_post(message_id)
        return message_id

    try:
        _edit_services_post(
            message_id,
            text
        )

        log_line(
            "СЕРВИСЫ: навигационный пост обновлён",
            f"message_id={message_id}"
        )

        # Гарантируем, что сервисный навигатор остаётся
        # закреплённым сверху канала после любого обновления.
        pin_services_post(message_id)

        return message_id

    except Exception as exc:
        description = str(exc).lower()

        if (
            "message to edit not found" in description
            or "message can't be edited" in description
            or "message_id_invalid" in description
            or "400 client error" in description
        ):
            old_id = message_id

            new_id = _send_services_post(text)
            save_id_file(
                SERVICES_MESSAGE_ID_FILE,
                new_id
            )

            log_line(
                "СЕРВИСЫ: старый текстовый пост заменён фото-постом",
                f"old_message_id={old_id}",
                f"new_message_id={new_id}"
            )

            unpin_services_post(old_id)
            pin_services_post(new_id)
            return new_id

        raise


def safe_update_services_post():
    try:
        return update_services_post()
    except Exception as exc:
        log_line(
            "СЕРВИСЫ — ОШИБКА ОБНОВЛЕНИЯ:",
            exc
        )
        return None


# =========================================================
# 1. ПОГОДА — ХАЙФА
# =========================================================
# Независимый сервис.
#
# Публикация:
#   00:00 — новый погодный пост
#   01:00–11:00 — редактируется текущий пост
#   12:00 — новый погодный пост
#   13:00–23:00 — редактируется текущий пост
#
# Принудительное обновление:
#   Telegram: /weather
#   CMD:      python ne_zaika_bot.py now
# =========================================================

# Хайфа
LAT = 32.7940
LON = 34.9896

WEATHER_MESSAGE_ID_FILE = state_file("weather_message_id.txt")
WEATHER_SLOT_FILE = state_file("weather_slot.txt")


# ---------------------------------------------------------
# 1.1. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ПОГОДЫ
# ---------------------------------------------------------

def wind_direction(degrees):
    directions = [
        "северный",
        "северо-восточный",
        "восточный",
        "юго-восточный",
        "южный",
        "юго-западный",
        "западный",
        "северо-западный",
    ]

    index = round(degrees / 45) % 8
    return directions[index]


HEBREW_MONTHS = {
    "Nissan": "нисана",
    "Nisan": "нисана",
    "Iyar": "ияра",
    "Sivan": "сивана",
    "Tammuz": "тамуза",
    "Av": "ава",
    "Elul": "элула",
    "Tishrei": "тишрея",
    "Cheshvan": "хешвана",
    "Marcheshvan": "хешвана",
    "Kislev": "кислева",
    "Teves": "тевета",
    "Tevet": "тевета",
    "Shevat": "швата",
    "Adar": "адара",
    "Adar 1": "адара I",
    "Adar I": "адара I",
    "Adar 2": "адара II",
    "Adar II": "адара II",
}


def get_hebrew_date(now_local, sunset_today):
    greg_date = now_local.date()

    hebrew = HebrewDate.from_pydate(greg_date)

    # Еврейская дата меняется после заката
    if now_local >= sunset_today:
        hebrew = hebrew + 1

    month_eng = hebrew.month_name()

    month_ru = HEBREW_MONTHS.get(
        month_eng,
        month_eng
    )

    return f"{hebrew.day} {month_ru} {hebrew.year}"


MONTHS_RU = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def gregorian_date_ru(d):
    return (
        f"{d.day} "
        f"{MONTHS_RU[d.month]} "
        f"{d.year}"
    )


def format_delta(delta):
    seconds = int(delta.total_seconds())

    if seconds < 0:
        seconds = 0

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours} ч {minutes} мин"

    return f"{minutes} мин"


# ---------------------------------------------------------
# 1.2. ЛУНА / МАГНИТНОЕ ПОЛЕ / СОЛНЕЧНАЯ АКТИВНОСТЬ
# ---------------------------------------------------------

def moon_phase(now):
    epoch = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    synodic_month = 29.53058867
    now_utc = now.astimezone(timezone.utc)

    days = (now_utc - epoch).total_seconds() / 86400
    age = days % synodic_month
    fraction = age / synodic_month

    illumination = (
        1 - math.cos(2 * math.pi * fraction)
    ) / 2 * 100

    if fraction < 0.03 or fraction >= 0.97:
        phase = "🌑 новолуние"
        visibility = "практически не видна"
    elif fraction < 0.22:
        phase = "🌒 растущий серп"
        visibility = "лучше видна вечером"
    elif fraction < 0.28:
        phase = "🌓 первая четверть"
        visibility = "хорошо видна вечером"
    elif fraction < 0.47:
        phase = "🌔 растущая Луна"
        visibility = "хорошо видна вечером и ночью"
    elif fraction < 0.53:
        phase = "🌕 полнолуние"
        visibility = "видна почти всю ночь"
    elif fraction < 0.72:
        phase = "🌖 убывающая Луна"
        visibility = "лучше видна ночью и утром"
    elif fraction < 0.78:
        phase = "🌗 последняя четверть"
        visibility = "лучше видна после полуночи и утром"
    else:
        phase = "🌘 убывающий серп"
        visibility = "лучше виден перед рассветом"

    return (
        f"{phase}, освещено {illumination:.0f}% — "
        f"{visibility}"
    )


def kp_description(kp):
    if kp < 2:
        return "спокойное"
    elif kp < 3:
        return "слабо возмущённое"
    elif kp < 4:
        return "небольшие возмущения"
    elif kp < 5:
        return "возмущённое, но магнитной бури нет"
    elif kp < 6:
        return "G1 — слабая магнитная буря"
    elif kp < 7:
        return "G2 — умеренная магнитная буря"
    elif kp < 8:
        return "G3 — сильная магнитная буря"
    elif kp < 9:
        return "G4 — очень сильная магнитная буря"
    else:
        return "G5 — экстремальная магнитная буря"


def get_kp():

    # =====================================================
    # 1. NOAA
    # =====================================================

    try:
        url = (
            "https://services.swpc.noaa.gov/"
            "json/planetary_k_index_1m.json"
        )

        data = get_json(url)

        if data:
            last = data[-1]

            kp = last.get("kp_index")

            if kp is None:
                kp = last.get("estimated_kp")

            if kp is not None:
                print("Kp источник: NOAA")
                return float(kp)

    except Exception as error:
        print("NOAA Kp ошибка:", error)


    # =====================================================
    # 2. GFZ POTSDAM
    # =====================================================

    try:
        now_utc = datetime.now(timezone.utc)

        start_utc = now_utc - timedelta(hours=24)

        start = start_utc.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        end = now_utc.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        url = (
            "https://kp.gfz.de/app/json/"
            f"?start={start}"
            f"&end={end}"
            "&index=Kp"
        )

        data = get_json(url)

        # GFZ возвращает массив значений Kp
        kp_values = data.get("Kp", [])

        if kp_values:
            kp = float(kp_values[-1])

            print("Kp источник: GFZ")
            return kp

    except Exception as error:
        print("GFZ Kp ошибка:", error)


    print("Kp: данные не получены ни от NOAA, ни от GFZ")

    return None


def get_solar_activity():
    url = (
        "https://services.swpc.noaa.gov/"
        "json/solar-cycle/"
        "observed-solar-cycle-indices.json"
    )

    try:
        data = get_json(url)

        # ищем последнее значение F10.7
        for item in reversed(data):

            flux = item.get("f10.7")

            if flux is not None:
                return {
                    "flux": float(flux),
                    "month": item.get("time-tag", ""),
                    "sunspots": item.get(
                        "observed_swpc_ssn"
                    ),
                }

    except Exception:
        pass

    return None


# ---------------------------------------------------------
# 1.3. ИСТОЧНИКИ ПОГОДНЫХ ДАННЫХ
# ---------------------------------------------------------

def get_weather():

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}"
        f"&longitude={LON}"

        "&current="
        "temperature_2m,"
        "apparent_temperature,"
        "relative_humidity_2m,"
        "dew_point_2m,"
        "wind_speed_10m,"
        "wind_direction_10m,"
        "pressure_msl,"
        "precipitation_probability,"
        "weather_code,"
        "visibility,"
        "wind_gusts_10m"

        "&daily="
        "sunrise,"
        "sunset"

        "&timezone=Asia%2FJerusalem"
        "&forecast_days=2"
    )

    return get_json(url)


def get_marine():

    url = (
        "https://marine-api.open-meteo.com/v1/marine"
        f"?latitude={LAT}"
        f"&longitude={LON}"

        "&current="
        "sea_surface_temperature"

        "&timezone=Asia%2FJerusalem"
    )

    return get_json(url)


def get_air():

    url = (
        "https://air-quality-api.open-meteo.com/"
        "v1/air-quality"

        f"?latitude={LAT}"
        f"&longitude={LON}"

        "&current="
        "dust,"
        "pm10,"
        "pm2_5,"
        "uv_index"

        "&timezone=Asia%2FJerusalem"
    )

    return get_json(url)


# ---------------------------------------------------------
# 1.4. РАНЖИРЫ И ПОЯСНЕНИЯ
# ---------------------------------------------------------

def humidity_description(humidity):
    if humidity < 10:
        return "экстремально сухо — пересыхают кожа и слизистые; пейте больше воды"
    elif humidity < 20:
        return "крайне сухо — быстрое испарение; желательно увлажнение"
    elif humidity < 30:
        return "очень сухо — возможна сухость кожи и слизистых"
    elif humidity < 40:
        return "сухо — быстрое испарение и высыхание"
    elif humidity < 50:
        return "умеренно — комфортная сухость"
    elif humidity < 60:
        return "умеренно — комфортная влажность"
    elif humidity < 70:
        return "влажно — испарение замедляется"
    elif humidity < 80:
        return "высокая — пот испаряется хуже"
    elif humidity < 90:
        return "очень высокая — при жаре повышается риск перегрева"
    else:
        return "почти насыщенный воздух — испарение минимально"


def pressure_description(pressure_hpa):
    if pressure_hpa < 980:
        return "очень низкое — циклон, погода обычно неустойчивая; при метеочуствительности и проблемах с АД снизить нагрузку"

    elif pressure_hpa < 990:
        return "низкое — вероятна неустойчивая погода; при метеочуствительности и проблемах с АД избегать перегрузок"

    elif pressure_hpa < 1000:
        return "пониженное — возможна смена погоды; при недомогании снизить нагрузку"

    elif pressure_hpa < 1010:
        return "слегка пониженное — без особых ограничений"

    elif pressure_hpa < 1020:
        return "нормальное — обычный режим"

    elif pressure_hpa < 1030:
        return "слегка повышенное — обычно устойчивая погода; без особых ограничений"

    elif pressure_hpa < 1040:
        return "повышенное — устойчивый антициклон; при проблемах с АД следить за самочувствием"

    elif pressure_hpa < 1050:
        return "высокое — сильный антициклон; при метеочуствительности и проблемах с АД избегать перегрузок"

    else:
        return "экстремально высокое — необычная ситуация; при метеочуствительности и проблемах с АД соблюдать максимальную осторожность"


def wind_description(speed):
    if speed < 1:
        return "штиль — ветер практически не ощущается"
    elif speed < 5:
        return "очень слабый — едва ощущается"
    elif speed < 12:
        return "слабый — ощущается лицом, шевелятся листья"
    elif speed < 20:
        return "умеренный — заметно движутся ветви"
    elif speed < 30:
        return "свежий — лёгкие предметы может сдувать"
    elif speed < 40:
        return "сильный — идти против ветра становится трудно"
    elif speed < 50:
        return "очень сильный — опасны незакреплённые предметы"
    elif speed < 65:
        return "штормовой — возможны повреждения ветвей; избегайте деревьев и незакреплённых предметов"
    elif speed < 80:
        return "сильный шторм — опасно находиться под деревьями и рядом с лёгкими конструкциями"
    else:
        return "экстремальный — опасность падения предметов и повреждений; лучше оставаться в защищённом месте"


def gust_description(speed):
    if speed < 10:
        return "слабые — практически не ощущаются"
    elif speed < 20:
        return "небольшие — без заметных последствий"
    elif speed < 30:
        return "умеренные — могут хлопать двери и раскачиваться лёгкие предметы"
    elif speed < 40:
        return "ощутимые — лучше закрепить лёгкие вещи на балконе"
    elif speed < 50:
        return "сильные — могут сдвигать лёгкие предметы; осторожно с зонтами"
    elif speed < 60:
        return "очень сильные — возможны падения веток и незакреплённых предметов; избегайте деревьев"
    elif speed < 75:
        return "опасные — возможны повреждения ветвей и лёгких конструкций; держитесь подальше от деревьев и вывесок"
    elif speed < 90:
        return "штормовые — возможны серьёзные повреждения; без необходимости не выходите на открытое место"
    elif speed < 110:
        return "очень опасные — возможны падения деревьев и повреждения конструкций; лучше оставаться в помещении"
    else:
        return "экстремальные — высокая опасность разрушений и летящих предметов; оставайтесь в защищённом помещении"


def uv_description(uv):
    if uv < 0.5:
        return "нет солнечного УФ — ночь или Солнце очень низко"
    elif uv < 3:
        return "низкий — обычная защита достаточна"
    elif uv < 6:
        return "умеренный — в полдень лучше тень, очки и защита кожи"
    elif uv < 8:
        return "высокий — сократите пребывание на открытом солнце, нужны головной убор и защита кожи"
    elif uv < 11:
        return "очень высокий — избегайте полуденного солнца, защита обязательна"
    else:
        return "экстремальный — по возможности не находиться на открытом солнце в полдень"


def dust_description(dust):
    if dust < 10:
        return "пустынной пыли практически нет"
    elif dust < 25:
        return "небольшая примесь пустынной пыли"
    elif dust < 50:
        return "заметная запылённость"
    elif dust < 100:
        return "сильная запылённость — чувствительным людям лучше сократить нагрузку на улице"
    elif dust < 300:
        return "очень сильная запылённость — возможен пыльный эпизод; окна лучше держать закрытыми"
    else:
        return "экстремальная пыль — возможна пыльная буря; лучше ограничить пребывание на улице"


def pm10_description(pm10):
    if pm10 < 20:
        return "низкая концентрация"
    elif pm10 < 40:
        return "невысокая концентрация"
    elif pm10 < 80:
        return "повышенная — чувствительным людям стоит учитывать качество воздуха"
    elif pm10 < 150:
        return "высокая — лучше сократить длительную нагрузку на улице"
    elif pm10 < 250:
        return "очень высокая — желательно ограничить пребывание на улице"
    else:
        return "экстремальная — лучше оставаться в помещении с закрытыми окнами"


def pm25_description(pm25):
    if pm25 < 10:
        return "низкая концентрация"
    elif pm25 < 20:
        return "умеренная концентрация"
    elif pm25 < 35:
        return "повышенная — чувствительным людям стоит учитывать качество воздуха"
    elif pm25 < 55:
        return "высокая — лучше сократить длительную нагрузку на улице"
    elif pm25 < 100:
        return "очень высокая — желательно ограничить пребывание на улице"
    else:
        return "экстремальная — лучше оставаться в помещении с закрытыми окнами"


def solar_activity_description(flux):
    if flux < 70:
        return "очень низкая"
    elif flux < 100:
        return "низкая"
    elif flux < 150:
        return "умеренная"
    elif flux < 200:
        return "высокая"
    elif flux < 250:
        return "очень высокая"
    else:
        return "экстремально высокая"


def weather_phenomenon(code):
    phenomena = {
        0: "☀️ Ясно",

        1: "🌤 Преимущественно ясно",
        2: "⛅ Переменная облачность",
        3: "☁️ Пасмурно",

        45: "🌫 Туман",
        48: "🌫 Туман с изморозью",

        51: "🌦 Слабая морось",
        53: "🌦 Умеренная морось",
        55: "🌧 Сильная морось",

        56: "🧊 Слабая переохлаждённая морось",
        57: "🧊 Сильная переохлаждённая морось",

        61: "🌧 Слабый дождь",
        63: "🌧 Умеренный дождь",
        65: "🌧 Сильный дождь",

        66: "🧊 Слабый ледяной дождь",
        67: "🧊 Сильный ледяной дождь",

        71: "🌨 Слабый снег",
        73: "🌨 Умеренный снег",
        75: "❄️ Сильный снег",
        77: "❄️ Снежная крупа",

        80: "🌦 Слабый ливень",
        81: "🌧 Умеренный ливень",
        82: "⛈ Сильный ливень",

        85: "🌨 Слабый снежный ливень",
        86: "❄️ Сильный снежный ливень",

        95: "⛈ Гроза",
        96: "⛈ Гроза со слабым градом",
        99: "⛈ Сильная гроза с градом",
    }

    return phenomena.get(
        code,
        "⚠️ Неизвестное погодное явление"
    )


# ---------------------------------------------------------
# 1.5. ФОРМИРОВАНИЕ ПОГОДНОГО ПОСТА
# ---------------------------------------------------------

def make_weather_text():

    weather_data = get_weather()
    marine_data = get_marine()
    air_data = get_air()

    current = weather_data["current"]
    daily = weather_data["daily"]
    marine = marine_data["current"]
    air = air_data["current"]

    now = datetime.now(TZ)

    # -----------------------------------------------------
    # ВОСХОД / ЗАКАТ
    # -----------------------------------------------------

    sunrise_today = datetime.fromisoformat(
        daily["sunrise"][0]
    ).replace(tzinfo=TZ)

    sunset_today = datetime.fromisoformat(
        daily["sunset"][0]
    ).replace(tzinfo=TZ)

    sunrise_tomorrow = datetime.fromisoformat(
        daily["sunrise"][1]
    ).replace(tzinfo=TZ)

    if now < sunrise_today:

        next_sun_text = (
            "🌅 До восхода: "
            f"{format_delta(sunrise_today - now)}"
        )

    elif now < sunset_today:

        next_sun_text = (
            "🌇 До заката: "
            f"{format_delta(sunset_today - now)}"
        )

    else:

        next_sun_text = (
            "🌅 До восхода: "
            f"{format_delta(sunrise_tomorrow - now)}"
        )

    # -----------------------------------------------------
    # ДАТЫ
    # -----------------------------------------------------

    gregorian = gregorian_date_ru(
        now.date()
    )

    hebrew = get_hebrew_date(
        now,
        sunset_today
    )

    # -----------------------------------------------------
    # ВЕТЕР
    # -----------------------------------------------------

    wind_dir = wind_direction(
        current["wind_direction_10m"]
    )

    # -----------------------------------------------------
    # KP
    # -----------------------------------------------------

    kp = get_kp()

    if kp is None:

        magnetic_text = (
            "🧲 Магнитное поле: данных нет"
        )

    else:

        magnetic_text = (
            f"🧲 Магнитное поле: "
            f"{kp_description(kp)} "
            f"(Kp {kp:.1f})"
        )

    # -----------------------------------------------------
    # СОЛНЦЕ
    # -----------------------------------------------------

    solar = get_solar_activity()

    if solar:

        solar_level = solar_activity_description(
            solar["flux"]
        )

        solar_text = (
            "☀️ Солнечная активность: "
            f"{solar_level} — "
            f"F10.7: {solar['flux']:.0f}"
        )

        if solar["sunspots"] is not None:
            solar_text += (
                f", пятен: "
                f"{float(solar['sunspots']):.0f}"
            )

    else:

        solar_text = (
            "☀️ Солнечная активность: "
            "данных нет"
        )

    # -----------------------------------------------------
    # ЛУНА
    # -----------------------------------------------------

    moon = moon_phase(now)

    # -----------------------------------------------------
    # ЧИСЛА
    # -----------------------------------------------------

    temperature = current["temperature_2m"]

    feels = current[
        "apparent_temperature"
    ]

    sea = marine[
        "sea_surface_temperature"
    ]

    humidity = current[
        "relative_humidity_2m"
    ]

    dew_point = current[
        "dew_point_2m"
    ]

    humidity_text = humidity_description(
        humidity
    )

    pressure = round(
    current["pressure_msl"]
    )

    pressure_text = pressure_description(
        pressure
    )

    wind_speed = current[
        "wind_speed_10m"
    ]

    wind_text = wind_description(wind_speed)

    weather_code = current["weather_code"]
    visibility = current["visibility"]
    wind_gusts = current["wind_gusts_10m"]

    special = []

    phenomenon = weather_phenomenon(weather_code)

    if phenomenon:
        special.append(phenomenon)

    if visibility < 1000:
        special.append(
            f"👁 Очень плохая видимость — {visibility / 1000:.1f} км"
        )
    elif visibility < 5000:
        special.append(
            f"👁 Плохая видимость — {visibility / 1000:.1f} км"
        )

    if special:
        special_text = (
            "🌦 Погодные явления:\n"
            + "\n".join(special)
        )
    else:
        special_text = "✅ Погодных явлений нет"

    precipitation = current[
        "precipitation_probability"
    ]

    uv = air["uv_index"]
    uv_text = uv_description(uv)

    dust = air["dust"]
    dust_text = dust_description(dust)

    pm10 = air["pm10"]
    pm10_text = pm10_description(pm10)

    pm25 = air["pm2_5"]
    pm25_text = pm25_description(pm25)

    # -----------------------------------------------------
    # ПОСТ
    # -----------------------------------------------------

    text = (
        "☀️ Погодка в Солнечном Городе\n"
        "📍 Хайфа\n\n"

        f"📅 {gregorian}\n"
        f"✡️ {hebrew}\n\n"

        f"🌡 Сейчас: {temperature:.1f}°C\n"
        f"🥵 Ощущается: {feels:.1f}°C\n"
        f"🌊 Море: {sea:.1f}°C\n\n"

        f"💧 Влажность: {humidity}% — {humidity_text}\n"
        f"💦 Точка росы: {dew_point:.1f}°C\n"
        
        f"🔵 Давление на уровне моря: {pressure} гПа — {pressure_text}\n"

        f"💨 Ветер: "
        f"{wind_speed:.1f} км/ч, "
        f"{wind_dir} — {wind_text}\n"
        f"🌪 Порывы: до {wind_gusts:.0f} км/ч — {gust_description(wind_gusts)}\n"

        f"🌧 Вероятность осадков: "
        f"{precipitation}%\n\n"

        f"{special_text}\n\n"

        f"🌫 Пустынная пыль: "
        f"{dust:.1f} мкг/м³ — {dust_text}\n"

        f"😷 Крупные взвешенные частицы (PM10): "
        f"{pm10:.1f} мкг/м³ — {pm10_text}\n"

        f"🫁 Мелкие взвешенные частицы (PM2.5): "
        f"{pm25:.1f} мкг/м³ — {pm25_text}\n\n"

        f"🌅 Восход: "
        f"{sunrise_today.strftime('%H:%M')}\n"

        f"🌇 Закат: "
        f"{sunset_today.strftime('%H:%M')}\n"

        f"{next_sun_text}\n\n"

        f"🌙 Луна: {moon}\n"
        f"{magnetic_text}\n"
        f"{solar_text}\n"
        f"☀️ УФ-индекс: {uv:.1f} — {uv_text}\n\n"

        f"🕒 Обновлено: "
        f"{now.strftime('%H:%M:%S')}\n\n"

        "@ne_zaika"
    )

    return text


# ---------------------------------------------------------
# 1.6. ОБНОВЛЕНИЕ ПОГОДНОГО ПОСТА
# ---------------------------------------------------------

def weather_slot(now=None):
    if now is None:
        now = datetime.now(TZ)

    half = "00" if now.hour < 12 else "12"
    return f"{now.strftime('%Y-%m-%d')}-{half}"


def load_weather_slot():
    if not os.path.exists(WEATHER_SLOT_FILE):
        return None

    try:
        with open(
            WEATHER_SLOT_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            return file.read().strip() or None

    except Exception:
        return None


def save_weather_slot(slot):
    with open(
        WEATHER_SLOT_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        file.write(slot)


def update_weather(force_new=False):
    # Один постоянный подробный пост погоды.
    # При последующих обновлениях только редактируется —
    # новых погодных простыней в ленте не создаём.
    text = make_weather_text()

    message_id = update_persistent_post(
        text,
        WEATHER_MESSAGE_ID_FILE
    )

    log_line(
        "ПОГОДА: постоянный пост обновлён",
        f"message_id={message_id}"
    )

    safe_update_services_post()

    return message_id


# =========================================================
# 2. БЕН-ГУРИОН — ТАБЛО РЕЙСОВ
# =========================================================
# Источник: официальный открытый набор IAA на data.gov.il
# resource_id: e83f763b-b7d7-479e-b172-ae981ddc6de5
#
# Поля IAA:
#   CHOPER   — код авиакомпании
#   CHFLTN   — номер рейса
#   CHOPERD  — авиакомпания
#   CHSTOL   — Scheduled
#   CHPTOL   — Actual / актуальное время
#   CHAORD   — A / D
#   CHLOC1   — IATA аэропорта
#   CHLOC1D  — полное название аэропорта
#   CHLOC1T  — город / короткое название
#   CHLOCCT  — страна
#   CHTERM   — Terminal
#   CHCINT   — Check-in counters
#   CHCKZN   — Check-in zone
#   CHRMINE  — Status
#
# Окно табло:
#   фактические: последний 1 час по CHPTOL
#   ближайшие:   следующие 3 часа по CHPTOL
#
# Завершённость:
#   A + LANDED   -> фактический прилёт
#   D + DEPARTED -> фактический вылет
#
# Codeshare / дубли склеиваются.
# =========================================================

DATA_GOV_FLIGHTS_API = (
    "https://data.gov.il/api/3/action/datastore_search"
)
DATA_GOV_FLIGHTS_RESOURCE_ID = (
    "e83f763b-b7d7-479e-b172-ae981ddc6de5"
)

ARRIVALS_ACTUAL_MESSAGE_ID_FILE = state_file("arrivals_actual_message_id.txt")
ARRIVALS_NEXT_MESSAGE_ID_FILE = state_file("arrivals_next_message_id.txt")
DEPARTURES_ACTUAL_MESSAGE_ID_FILE = state_file("departures_actual_message_id.txt")
DEPARTURES_NEXT_MESSAGE_ID_FILE = state_file("departures_next_message_id.txt")
FLIGHT_ALERTS_MESSAGE_ID_FILE = state_file("flight_alerts_message_id.txt")


def parse_flight_time(value):
    if not value:
        return None

    try:
        value = str(value).strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        # IAA timestamps in this dataset are local Israel time.
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ)

    except Exception:
        return None


def normalize_data_gov_flight(row):
    if not isinstance(row, dict):
        return None

    direction = str(
        row.get("CHAORD") or ""
    ).strip().upper()

    if direction not in ("A", "D"):
        return None

    scheduled = parse_flight_time(
        row.get("CHSTOL")
    )

    actual = parse_flight_time(
        row.get("CHPTOL")
    )

    if scheduled is None:
        return None

    operator = str(
        row.get("CHOPER") or ""
    ).strip().upper()

    flight_no = str(
        row.get("CHFLTN") or ""
    ).strip()

    return {
        "direction": direction,
        "number": f"{operator}{flight_no}".strip(),
        "airline": str(
            row.get("CHOPERD")
            or operator
            or "—"
        ).strip(),

        "city": str(
            row.get("CHLOC1T")
            or row.get("CHLOC1D")
            or row.get("CHLOC1")
            or "—"
        ).strip(),

        "airport_name": str(
            row.get("CHLOC1D")
            or row.get("CHLOC1T")
            or ""
        ).strip(),

        "iata": str(
            row.get("CHLOC1") or ""
        ).strip().upper(),

        "country": str(
            row.get("CHLOCCT") or ""
        ).strip(),

        "scheduled_time": scheduled,
        "updated_time": actual,

        "status": str(
            row.get("CHRMINE") or ""
        ).strip().upper(),

        "terminal": str(
            row.get("CHTERM") or ""
        ).strip(),

        # В data.gov.il это именно стойки регистрации и зона.
        "checkin": str(
            row.get("CHCINT") or ""
        ).strip(),

        "zone": str(
            row.get("CHCKZN") or ""
        ).strip(),
    }



# ---------------------------------------------------------
# 2.1.1. АЭРОПОРТЫ / РАССТОЯНИЕ / ETA / ПОГОДА
# ---------------------------------------------------------

TLV_COORDS = (32.0114, 34.8867)

# Полный мировой справочник аэропортов:
# OurAirports airports.csv, обновляется ежедневно.
OURAIRPORTS_CSV_URL = (
    "https://davidmegginson.github.io/"
    "ourairports-data/airports.csv"
)

OURAIRPORTS_CACHE_FILE = state_file(
    "ourairports_airports.csv"
)

OURAIRPORTS_CACHE_MAX_AGE = (
    24 * 60 * 60
)

_airport_coords_cache = None


def _ourairports_cache_is_fresh():
    if not os.path.exists(
        OURAIRPORTS_CACHE_FILE
    ):
        return False

    age = (
        time.time()
        - os.path.getmtime(
            OURAIRPORTS_CACHE_FILE
        )
    )

    return (
        age
        < OURAIRPORTS_CACHE_MAX_AGE
    )


def _download_ourairports_csv():
    print(
        "АЭРОПОРТЫ: обновляю "
        "полный справочник OurAirports..."
    )

    response = requests.get(
        OURAIRPORTS_CSV_URL,
        timeout=60,
    )
    response.raise_for_status()

    with open(
        OURAIRPORTS_CACHE_FILE,
        "wb"
    ) as file:
        file.write(
            response.content
        )

    print(
        "АЭРОПОРТЫ: справочник сохранён:",
        OURAIRPORTS_CACHE_FILE
    )


def load_airport_coords():
    """
    Возвращает:
        {
            "CDG": (49.0097, 2.5479),
            ...
        }

    Используем только записи с IATA-кодом.
    Если обновление не удалось, используем
    уже сохранённый локальный CSV.
    """
    global _airport_coords_cache

    if _airport_coords_cache is not None:
        return _airport_coords_cache

    if not _ourairports_cache_is_fresh():
        try:
            _download_ourairports_csv()
        except Exception as error:
            print(
                "АЭРОПОРТЫ — ОШИБКА ОБНОВЛЕНИЯ:",
                error
            )

    if not os.path.exists(
        OURAIRPORTS_CACHE_FILE
    ):
        print(
            "АЭРОПОРТЫ: локального "
            "справочника нет"
        )
        _airport_coords_cache = {}
        return _airport_coords_cache

    coords = {}

    try:
        with open(
            OURAIRPORTS_CACHE_FILE,
            "r",
            encoding="utf-8",
            newline=""
        ) as file:

            reader = csv.DictReader(
                file
            )

            for row in reader:
                iata = str(
                    row.get("iata_code")
                    or ""
                ).strip().upper()

                if not iata:
                    continue

                try:
                    lat = float(
                        row.get(
                            "latitude_deg"
                        )
                    )
                    lon = float(
                        row.get(
                            "longitude_deg"
                        )
                    )
                except (
                    TypeError,
                    ValueError
                ):
                    continue

                # При редких дублях IATA
                # предпочитаем large/medium airport.
                airport_type = str(
                    row.get("type")
                    or ""
                ).strip()

                priority = {
                    "large_airport": 3,
                    "medium_airport": 2,
                    "small_airport": 1,
                }.get(
                    airport_type,
                    0
                )

                previous = coords.get(
                    iata
                )

                if (
                    previous is None
                    or priority
                    > previous[2]
                ):
                    coords[iata] = (
                        lat,
                        lon,
                        priority,
                    )

        _airport_coords_cache = {
            iata: (
                value[0],
                value[1]
            )
            for iata, value
            in coords.items()
        }

        print(
            "АЭРОПОРТЫ:",
            len(
                _airport_coords_cache
            ),
            "IATA-кодов загружено"
        )

        return _airport_coords_cache

    except Exception as error:
        print(
            "АЭРОПОРТЫ — ОШИБКА ЧТЕНИЯ:",
            error
        )
        _airport_coords_cache = {}
        return _airport_coords_cache


_weather_cache = {}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1)
        * math.cos(p2)
        * math.sin(dlon / 2) ** 2
    )

    return 2 * r * math.asin(math.sqrt(a))


def estimate_flight_minutes(distance_km):
    """
    Грубая оценка:
    крейсер ~800 км/ч + 35 мин на набор/снижение/маршрут.
    """
    return max(
        45,
        int(round(distance_km / 800 * 60 + 35))
    )


def compass_16(degrees):
    if degrees is None:
        return ""

    names = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW",
        "W", "WNW", "NW", "NNW",
    ]

    index = int((float(degrees) + 11.25) // 22.5) % 16
    return names[index]


def destination_metrics(flight):
    if flight.get("direction") != "D":
        return None

    iata = str(
        flight.get("iata") or ""
    ).strip().upper()

    airport_coords = load_airport_coords()

    coords = airport_coords.get(
        iata
    )

    if not coords:
        print(
            "АЭРОПОРТЫ: нет координат для",
            iata
        )
        return None

    distance = haversine_km(
        TLV_COORDS[0],
        TLV_COORDS[1],
        coords[0],
        coords[1],
    )

    departure = (
        flight.get("updated_time")
        or flight.get("scheduled_time")
    )

    if departure is None:
        return None

    duration_min = estimate_flight_minutes(
        distance
    )

    eta = departure + timedelta(
        minutes=duration_min
    )

    return {
        "distance_km": int(round(distance)),
        "duration_min": duration_min,
        "eta": eta,
        "lat": coords[0],
        "lon": coords[1],
        "iata": iata,
    }


def get_arrival_weather(metrics):
    if not metrics:
        return None

    eta = metrics["eta"]
    cache_key = (
        metrics["iata"],
        eta.strftime("%Y-%m-%d %H")
    )

    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": metrics["lat"],
                "longitude": metrics["lon"],
                "hourly": (
                    "temperature_2m,"
                    "precipitation_probability,"
                    "wind_speed_10m,"
                    "wind_direction_10m"
                ),
                "timezone": "auto",
                "forecast_days": 4,
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()

        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []

        target = eta.strftime("%Y-%m-%dT%H:00")

        if target not in times:
            _weather_cache[cache_key] = None
            return None

        i = times.index(target)

        result = {
            "temperature": (hourly.get("temperature_2m") or [None])[i],
            "precipitation": (
                hourly.get("precipitation_probability") or [None]
            )[i],
            "wind_speed": (
                hourly.get("wind_speed_10m") or [None]
            )[i],
            "wind_direction": (
                hourly.get("wind_direction_10m") or [None]
            )[i],
        }

        _weather_cache[cache_key] = result
        return result

    except Exception as error:
        print(
            "ПОГОДА АЭРОПОРТА — ОШИБКА:",
            metrics.get("iata"),
            error
        )
        _weather_cache[cache_key] = None
        return None


def average_interval_minutes(selected):
    if len(selected) < 2:
        return None

    times = sorted(
        t for t, _ in selected
        if t is not None
    )

    if len(times) < 2:
        return None

    intervals = [
        (b - a).total_seconds() / 60
        for a, b in zip(
            times,
            times[1:]
        )
    ]

    if not intervals:
        return None

    return round(
        sum(intervals) / len(intervals)
    )


def average_delay_minutes(selected):
    delays = []

    for _, flight in selected:
        scheduled = flight.get(
            "scheduled_time"
        )
        actual = flight.get(
            "updated_time"
        )

        if (
            scheduled is None
            or actual is None
        ):
            continue

        minutes = (
            actual - scheduled
        ).total_seconds() / 60

        if minutes > 0:
            delays.append(minutes)

    if not delays:
        return None

    return round(
        sum(delays) / len(delays)
    )

def get_flights():
    """
    Получаем весь текущий набор IAA напрямую из CKAN DataStore.
    Никакого bengurion.co.il и HTML-парсинга.
    """
    response = requests.get(
        DATA_GOV_FLIGHTS_API,
        params={
            "resource_id": DATA_GOV_FLIGHTS_RESOURCE_ID,
            "limit": 5000,
        },
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/142 Safari/537.36"
            ),
            "Accept": "application/json",
        },
        timeout=30,
    )

    response.raise_for_status()
    payload = response.json()

    if not payload.get("success"):
        raise RuntimeError(
            "data.gov.il: API вернул success=false"
        )

    result = payload.get("result") or {}
    rows = result.get("records") or []

    if not rows:
        raise RuntimeError(
            "data.gov.il: список рейсов пуст"
        )

    flights = []

    for row in rows:
        flight = normalize_data_gov_flight(row)

        if flight is not None:
            flights.append(flight)

    arrivals_count = sum(
        1 for flight in flights
        if flight["direction"] == "A"
    )

    departures_count = sum(
        1 for flight in flights
        if flight["direction"] == "D"
    )

    if arrivals_count == 0:
        raise RuntimeError(
            "data.gov.il: прилёты не получены"
        )

    if departures_count == 0:
        raise RuntimeError(
            "data.gov.il: вылеты не получены"
        )

    print(
        "БЕН-ГУРИОН / DATA.GOV.IL:",
        f"всего {len(flights)}",
        f"🛬 {arrivals_count}",
        f"✈️ {departures_count}",
    )

    return flights


# ---------------------------------------------------------
# 2.2. НОРМАЛИЗАЦИЯ / СТАТУС / СВЕТОФОР
# ---------------------------------------------------------

def flight_number(flight):
    return str(
        flight.get("number") or "—"
    ).strip()


def flight_airline(flight):
    return str(
        flight.get("airline") or "—"
    ).strip()


def flight_city(flight):
    return str(
        flight.get("city") or "—"
    ).strip()


def flight_status_raw(flight):
    return str(
        flight.get("status") or ""
    ).strip()


def flight_direction_icon(flight):
    return (
        "🛬"
        if flight.get("direction") == "A"
        else "✈️"
    )


def flight_status_light(flight):
    status = flight_status_raw(
        flight
    ).upper()

    if "CANCEL" in status:
        return "🔴"

    scheduled = flight.get(
        "scheduled_time"
    )

    actual = flight.get(
        "updated_time"
    )

    # Для этих статусов цвет определяем
    # не по самому слову статуса, а по
    # фактическому расхождению Actual/Scheduled.
    if status in (
        "ON TIME",
        "ONTIME",
        "FINAL",
        "NOT FINAL",
    ):
        if (
            scheduled is not None
            and actual is not None
        ):
            minutes = int(
                (
                    actual - scheduled
                ).total_seconds()
                // 60
            )

            if flight.get("direction") == "D":
                # ВЫЛЕТЫ:
                # любое отличие = изменение.
                if minutes > 0:
                    return "🟡"

                if minutes < 0:
                    return "🟠"

                return "🟢"

            # ПРИЛЁТЫ:
            # отклонение до 15 минут включительно
            # считается нормальным.
            if minutes > 15:
                return "🟡"

            if minutes < -15:
                return "🟠"

            return "🟢"

        return "🟢"

    if "DELAY" in status:
        return "🟡"

    if status in (
        "LANDED",
        "LANDING",
    ):
        return "🔵"

    if status == "DEPARTED":
        return "⚪"

    return "🟢"


def flight_actual_light(flight):
    """
    Только для уже фактически завершённых рейсов:
      D + DEPARTED -> ⚪
      A + LANDED   -> 🔵
      CANCELED     -> 🔴
    """
    status = flight_status_raw(
        flight
    ).upper()

    if "CANCEL" in status:
        return "🔴"

    if flight.get("direction") == "D":
        return "⚪"

    return "🔵"


def flight_is_cancelled(flight):
    return (
        "CANCEL"
        in flight_status_raw(
            flight
        ).upper()
    )


def flight_is_completed(
    flight,
    direction
):
    status = flight_status_raw(
        flight
    ).upper()

    if direction == "A":
        return status == "LANDED"

    return status == "DEPARTED"


def flight_event_time(flight):
    return (
        flight.get("updated_time")
        or flight.get("scheduled_time")
    )


def delay_text(flight):
    scheduled = flight.get(
        "scheduled_time"
    )

    actual = flight.get(
        "updated_time"
    )

    if (
        scheduled is None
        or actual is None
    ):
        return None

    minutes = int(
        (
            actual - scheduled
        ).total_seconds()
        // 60
    )

    if minutes == 0:
        return None

    absolute_minutes = abs(minutes)
    hours, mins = divmod(
        absolute_minutes,
        60
    )

    if hours and mins:
        value = (
            f"{hours} h "
            f"{mins} min"
        )
    elif hours:
        value = f"{hours} h"
    else:
        value = f"{mins} min"

    if minutes > 0:
        return (
            "Delay",
            f"+{value}"
        )

    return (
        "Early",
        value
    )


# ---------------------------------------------------------
# 2.3. CODE-SHARE / ОТБОР
# ---------------------------------------------------------

def physical_flight_key(flight):
    """
    Склейка codeshare остаётся.

    Главные признаки одного физического рейса:
    направление + аэропорт + Scheduled + Actual +
    Terminal + Status.

    Номер авиакомпании намеренно НЕ входит в ключ.
    """
    return (
        flight.get("direction"),
        flight.get("iata"),
        flight.get("scheduled_time"),
        flight.get("updated_time"),
        flight.get("terminal"),
        flight_status_raw(flight),
    )


def merge_duplicate_flights(items):
    groups = {}

    for t, flight in items:
        key = physical_flight_key(
            flight
        )

        if key not in groups:
            groups[key] = {
                "time": t,
                "flight": dict(flight),
                "numbers": [],
                "airlines": [],
            }

        number = flight_number(
            flight
        )

        airline = flight_airline(
            flight
        )

        if (
            number
            and number != "—"
            and number
            not in groups[key]["numbers"]
        ):
            groups[key]["numbers"].append(
                number
            )

        if (
            airline
            and airline != "—"
            and airline
            not in groups[key]["airlines"]
        ):
            groups[key]["airlines"].append(
                airline
            )

    result = []

    for group in groups.values():
        merged = group["flight"]

        merged["_numbers"] = (
            group["numbers"]
        )

        merged["_airlines"] = (
            group["airlines"]
        )

        result.append(
            (
                group["time"],
                merged
            )
        )

    return result


def actual_flights(
    flights,
    direction
):
    """
    ФАКТИЧЕСКИЕ:
      окно CHPTOL: now - 1 hour .. now
      прилёты:     LANDED
      вылеты:      DEPARTED
    """
    now = datetime.now(TZ)
    start = now - timedelta(hours=1)

    result = []

    for flight in flights:

        if (
            flight.get("direction")
            != direction
        ):
            continue

        if not flight_is_completed(
            flight,
            direction
        ):
            continue

        actual = flight.get(
            "updated_time"
        )

        if actual is None:
            continue

        if start <= actual <= now:
            result.append(
                (actual, flight)
            )

    result = merge_duplicate_flights(
        result
    )

    result.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return result


def upcoming_flights(
    flights,
    direction,
    hours_forward=3
):
    """
    БЛИЖАЙШИЕ:
      окно CHPTOL: now .. now + 3 hours

    Отбор по Actual/CHPTOL.
    Status показывается как его отдаёт IAA.
    """
    now = datetime.now(TZ)
    end = now + timedelta(
        hours=hours_forward
    )

    result = []

    for flight in flights:

        if (
            flight.get("direction")
            != direction
        ):
            continue

        actual = flight.get(
            "updated_time"
        )

        if actual is None:
            continue

        if now < actual <= end:
            result.append(
                (actual, flight)
            )

    result = merge_duplicate_flights(
        result
    )

    result.sort(
        key=lambda item: item[0]
    )

    return result


# ---------------------------------------------------------
# 2.4. ФОРМАТ РЕЙСА
# ---------------------------------------------------------

def airport_display(flight):
    city = flight_city(flight)

    airport_name = str(
        flight.get("airport_name") or ""
    ).strip()

    iata = str(
        flight.get("iata") or ""
    ).strip()

    country = str(
        flight.get("country") or ""
    ).strip()

    # Если полное название совпадает с коротким, не дублируем.
    if (
        airport_name
        and airport_name.upper() != city.upper()
    ):
        place = f"{city} — {airport_name}"
    else:
        place = city

    if iata:
        place += f" ({iata})"

    if country:
        place += f", {country}"

    return place


def make_flight_line(
    t,
    flight,
    board_type=None
):
    numbers = (
        flight.get("_numbers")
        or [flight_number(flight)]
    )

    airlines = (
        flight.get("_airlines")
        or [flight_airline(flight)]
    )

    number = (
        " / ".join(
            x for x in numbers
            if x
        )
        or "—"
    )

    airline = (
        " / ".join(
            x for x in airlines
            if x
        )
        or "—"
    )

    place = airport_display(
        flight
    )

    status = flight_status_raw(
        flight
    )

    if board_type == "actual":
        light = flight_actual_light(
            flight
        )
    else:
        light = flight_status_light(
            flight
        )

    plane = flight_direction_icon(
        flight
    )

    scheduled = flight.get(
        "scheduled_time"
    )

    actual = flight.get(
        "updated_time"
    )

    terminal = str(
        flight.get("terminal") or ""
    ).strip()

    checkin = str(
        flight.get("checkin") or ""
    ).strip()

    zone = str(
        flight.get("zone") or ""
    ).strip()

    line = (
        f"{plane} "
        f"{number}  "
        f"{airline}\n"
    )

    if flight.get("direction") == "A":
        line += (
            f"{place} → Израиль TLV נתב\"ג\n"
        )
    else:
        line += (
            f"Израиль TLV נתב\"ג → {place}\n"
        )

    if status:
        line += (
            f"{light} "
            f"{status}\n"
        )

    if scheduled:
        line += (
            "Scheduled: "
            f"{scheduled.strftime('%d.%m %H:%M')}\n"
        )

    cancelled = flight_is_cancelled(
        flight
    )

    if actual and not cancelled:
        line += (
            "Actual: "
            f"{actual.strftime('%d.%m %H:%M')}\n"
        )

    timing = (
        None
        if cancelled
        else delay_text(flight)
    )

    if timing:
        timing_label, timing_value = timing
        line += (
            f"{timing_label}: "
            f"{timing_value}\n"
        )

    if terminal:
        line += (
            f"Terminal: T{terminal}\n"
        )

    show_departure_ops = (
        flight.get("direction") == "D"
        and board_type not in (
            "actual",
            "alerts",
        )
    )

    show_departure_extra = (
        flight.get("direction") == "D"
        and board_type != "alerts"
    )

    if (
        show_departure_ops
        and checkin
    ):
        line += (
            f"Check-in: {checkin}\n"
        )

    if (
        show_departure_ops
        and zone
    ):
        line += (
            f"Zone: {zone}\n"
        )

    if show_departure_extra:
        metrics = destination_metrics(
            flight
        )

        if metrics:
            line += (
                f"Distance: "
                f"{metrics['distance_km']:,}"
                .replace(",", " ")
                + " km\n"
            )

            duration = metrics[
                "duration_min"
            ]

            h, m = divmod(
                duration,
                60
            )

            if h and m:
                duration_text = (
                    f"{h} h {m} min"
                )
            elif h:
                duration_text = (
                    f"{h} h"
                )
            else:
                duration_text = (
                    f"{m} min"
                )

            line += (
                "Estimated flight time: "
                f"{duration_text}\n"
            )

            line += (
                "Estimated arrival: "
                f"{metrics['eta'].strftime('%d.%m %H:%M')}\n"
            )

            weather = get_arrival_weather(
                metrics
            )

            if weather:
                temp = weather.get(
                    "temperature"
                )
                wind_speed = weather.get(
                    "wind_speed"
                )
                wind_direction = weather.get(
                    "wind_direction"
                )
                precipitation = weather.get(
                    "precipitation"
                )

                if temp is not None:
                    line += (
                        "🌡 Temperature: "
                        f"{temp:+.0f}°C\n"
                    )

                if wind_speed is not None:
                    direction_text = (
                        compass_16(
                            wind_direction
                        )
                    )

                    line += (
                        "💨 Wind: "
                        f"{wind_speed:.0f} km/h"
                    )

                    if direction_text:
                        line += (
                            f", {direction_text}"
                        )

                    line += "\n"

                if precipitation is not None:
                    line += (
                        "🌧 Precipitation: "
                        f"{precipitation:.0f}%\n"
                    )

    return line.rstrip()


# ---------------------------------------------------------
# 2.5. ПОСТЫ
# ---------------------------------------------------------

def flight_interval_text(selected):
    times = sorted(
        t for t, _ in selected
        if t is not None
    )

    if not times:
        return None

    first = times[0]
    last = times[-1]

    if first.date() == last.date():
        return (
            f"{first.strftime('%H:%M')}"
            f"–{last.strftime('%H:%M')}"
        )

    return (
        f"{first.strftime('%d.%m %H:%M')}"
        f"–{last.strftime('%d.%m %H:%M')}"
    )


def make_flights_text(
    flights,
    direction,
    board_type
):
    if direction == "A":
        icon = "🛬"
        direction_title = "ПРИЛЁТЫ"
    else:
        icon = "✈️"
        direction_title = "ВЫЛЕТЫ"

    if board_type == "actual":
        selected = actual_flights(
            flights,
            direction
        )[:15]

        suffix = "ФАКТИЧЕСКИЕ"

        empty_text = (
            "Нет завершённых рейсов "
            "за последний час."
        )

    else:
        selected = upcoming_flights(
            flights,
            direction
        )[:25]

        suffix = "БЛИЖАЙШИЕ"

        empty_text = (
            "Нет ближайших рейсов "
            "на следующие 3 часа."
        )

    count = len(selected)

    if board_type == "actual":
        header = (
            f"{icon} БЕН-ГУРИОН — "
            f"{direction_title} — "
            f"{suffix} (last hour) "
            f"({count} рейсов)"
        )
    else:
        header = (
            f"{icon} БЕН-ГУРИОН — "
            f"{direction_title} — "
            f"{suffix} "
            f"({count} рейсов)"
        )

    stats = []

    if board_type != "actual":
        interval_text = flight_interval_text(
            selected
        )

        if interval_text:
            stats.append(
                f"Интервал: {interval_text}"
            )

    avg_interval = average_interval_minutes(
        selected
    )

    avg_delay = average_delay_minutes(
        selected
    )

    if avg_interval is not None:
        stats.append(
            f"Средний интервал: "
            f"{avg_interval} min"
        )

    if avg_delay is not None:
        stats.append(
            f"Средняя задержка: "
            f"{avg_delay} min"
        )

    lines = [
        header,
    ]

    if stats:
        lines.extend(
            stats
        )

    lines.append("")

    if not selected:
        lines.append(
            empty_text
        )

    else:
        for t, flight in selected:
            lines.append(
                make_flight_line(
                    t,
                    flight,
                    board_type
                )
            )
            lines.append("")

    now = datetime.now(TZ)

    lines.extend([
        (
            "🕒 Обновлено: "
            f"{now.strftime('%d.%m %H:%M:%S')}"
        ),
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


def make_flight_alerts_text(
    flights
):
    now = datetime.now(TZ)

    start = now - timedelta(
        hours=1
    )

    end = now + timedelta(
        hours=8
    )

    alerts = []

    for flight in flights:

        t = flight_event_time(
            flight
        )

        if (
            t is None
            or not (
                start <= t <= end
            )
        ):
            continue

        status = flight_status_raw(
            flight
        ).upper()

        scheduled = flight.get(
            "scheduled_time"
        )

        actual = flight.get(
            "updated_time"
        )

        timing_change = False

        if (
            scheduled is not None
            and actual is not None
        ):
            minutes = int(
                (
                    actual - scheduled
                ).total_seconds()
                // 60
            )

            if flight.get("direction") == "D":
                # ВЫЛЕТЫ:
                # любое отличие Actual/Scheduled
                # считается изменением,
                # даже если Status = ON TIME.
                if minutes != 0:
                    timing_change = True
            else:
                # ПРИЛЁТЫ:
                # только отклонение более 15 минут,
                # даже если Status = ON TIME.
                if abs(minutes) > 15:
                    timing_change = True

        if (
            "CANCEL" in status
            or "DELAY" in status
            or timing_change
        ):
            alerts.append(
                (t, flight)
            )

    alerts = merge_duplicate_flights(
        alerts
    )

    alerts.sort(
        key=lambda item: item[0]
    )


    selected = alerts[:25]

    arrivals_only = [
        item
        for item in selected
        if item[1].get("direction") == "A"
    ]

    departures_only = [
        item
        for item in selected
        if item[1].get("direction") == "D"
    ]

    avg_arrival_delay = average_delay_minutes(
        arrivals_only
    )

    avg_departure_delay = average_delay_minutes(
        departures_only
    )

    lines = [
        (
            "⚠️ БЕН-ГУРИОН — ИЗМЕНЕНИЯ "
            f"({len(selected)} рейсов)"
        ),
    ]

    interval_text = flight_interval_text(
        selected
    )

    if interval_text:
        lines.append(
            f"Интервал: {interval_text}"
        )

    if avg_arrival_delay is not None:
        lines.append(
            "Средняя задержка прилётов: "
            f"{avg_arrival_delay} min"
        )

    if avg_departure_delay is not None:
        lines.append(
            "Средняя задержка вылетов: "
            f"{avg_departure_delay} min"
        )

    lines.append("")

    if not selected:
        lines.append(
            "Задержек и отмен "
            "в выбранном интервале нет."
        )

    else:
        for t, flight in selected:
            lines.append(
                make_flight_line(
                    t,
                    flight,
                    "alerts"
                )
            )
            lines.append("")

    now = datetime.now(TZ)

    lines.extend([
        (
            "🕒 Обновлено: "
            f"{now.strftime('%d.%m %H:%M:%S')}"
        ),
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


def create_flight_board_posts():
    # Совместимость со старыми вызовами: новых постов НЕ создаём.
    return update_flight_board()


def update_flight_board():
    print(
        "БЕН-ГУРИОН / DATA.GOV.IL: "
        "обновляю 5 постоянных постов..."
    )

    flights = get_flights()

    posts = [
        (
            "ПРИЛЁТЫ — ФАКТИЧЕСКИЕ",
            make_flights_text(flights, "A", "actual"),
            ARRIVALS_ACTUAL_MESSAGE_ID_FILE
        ),
        (
            "ПРИЛЁТЫ — БЛИЖАЙШИЕ",
            make_flights_text(flights, "A", "next"),
            ARRIVALS_NEXT_MESSAGE_ID_FILE
        ),
        (
            "ВЫЛЕТЫ — ФАКТИЧЕСКИЕ",
            make_flights_text(flights, "D", "actual"),
            DEPARTURES_ACTUAL_MESSAGE_ID_FILE
        ),
        (
            "ВЫЛЕТЫ — БЛИЖАЙШИЕ",
            make_flights_text(flights, "D", "next"),
            DEPARTURES_NEXT_MESSAGE_ID_FILE
        ),
        (
            "ИЗМЕНЕНИЯ",
            make_flight_alerts_text(flights),
            FLIGHT_ALERTS_MESSAGE_ID_FILE
        ),
    ]

    message_ids = []

    for post_name, post_text, state_filename in posts:
        original_length = len(post_text)

        print()
        print("БЕН-ГУРИОН:", post_name)
        print(
            "Размер до глобального лимита:",
            f"{original_length}/{TELEGRAM_TEXT_LIMIT}"
        )

        try:
            message_id = update_persistent_post(
                post_text,
                state_filename
            )
            message_ids.append(message_id)
            print(
                "БЕН-ГУРИОН:",
                post_name,
                "— постоянный пост обновлён; message_id=",
                message_id
            )
        except Exception as error:
            print(
                "БЕН-ГУРИОН:",
                post_name,
                "— ОШИБКА:",
                error
            )
            continue

    safe_update_services_post()

    return message_ids


# =========================================================
# ВАЛЮТЫ
# =========================================================

RATES_MESSAGE_ID_FILE = state_file("rates_message_id.txt")

CURRENCY_SPECS = [
    ("USD", "🇺🇸", "США", "Доллар", "долларов", 1),
    ("EUR", "🇪🇺", "Еврозона", "Евро", "евро", 1),
    ("GBP", "🇬🇧", "Великобритания", "Фунт", "фунтов", 1),
    ("CHF", "🇨🇭", "Швейцария", "Франк", "франков", 1),
    ("CNY", "🇨🇳", "Китай", "Юань", "юаней", 1),
    ("RUB", "🇷🇺", "Россия", "Рубль", "рублей", 100),
    ("UAH", "🇺🇦", "Украина", "Гривна", "гривен", 100),
    ("BYN", "🇧🇾", "Беларусь", "Белорусский рубль", "рублей", 1),
    ("MDL", "🇲🇩", "Молдова", "Лей", "леев", 100),
    ("GEL", "🇬🇪", "Грузия", "Лари", "лари", 1),
    ("AMD", "🇦🇲", "Армения", "Драм", "драмов", 1000),
    ("AZN", "🇦🇿", "Азербайджан", "Манат", "манатов", 1),
    ("KZT", "🇰🇿", "Казахстан", "Тенге", "тенге", 1000),
    ("UZS", "🇺🇿", "Узбекистан", "Сум", "сумов", 10000),
    ("KGS", "🇰🇬", "Кыргызстан", "Сом", "сомов", 100),
    ("JOD", "🇯🇴", "Иордания", "Динар", "динаров", 1),
    ("EGP", "🇪🇬", "Египет", "Египетский фунт", "фунтов", 100),
]

CURRENCY_GROUPS = [
    ("ОСНОВНЫЕ", ["USD", "EUR", "GBP", "CHF", "CNY"]),
    ("БЫВШИЙ СССР", [
        "RUB", "UAH", "BYN", "MDL", "GEL",
        "AMD", "AZN", "KZT", "UZS", "KGS"
    ]),
    ("СОСЕДИ", ["JOD", "EGP"]),
]


def get_boi_rates():
    response = requests.get(
        "https://www.boi.org.il/PublicApi/GetExchangeRates",
        timeout=30
    )
    response.raise_for_status()
    data = response.json()

    result = {}

    for item in data.get("exchangeRates", []):
        code = str(item.get("key", "")).upper().strip()
        value = item.get("currentExchangeRate")

        if not code or value is None:
            continue

        # API Банка Израиля возвращает представительский курс.
        # Для текущего списка используем опубликованное значение
        # как ILS за единицу валюты.
        result[code] = {
            "ils_per_unit": float(value),
            "source": "Банк Израиля",
            "last_update": item.get("lastUpdate"),
        }

    return result


def get_frankfurter_rates():
    response = requests.get(
        "https://api.frankfurter.dev/v2/rates",
        params={"base": "ILS"},
        timeout=30
    )
    response.raise_for_status()

    result = {}

    for item in response.json():
        code = str(item.get("quote", "")).upper().strip()
        value = item.get("rate")

        if not code or value is None:
            continue

        value = float(value)

        if value > 0:
            result[code] = {
                "ils_per_unit": 1 / value,
                "source": "Frankfurter",
                "last_update": item.get("date"),
            }

    return result


def get_crypto_rates():
    response = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={
            "ids": "bitcoin,ethereum,tether",
            "vs_currencies": "ils",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        },
        headers={
            "accept": "application/json",
            "User-Agent": "ne-zaika-bot/1.0",
        },
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    mapping = {
        "bitcoin": ("BTC", "₿", "Bitcoin"),
        "ethereum": ("ETH", "Ξ", "Ethereum"),
        "tether": ("USDT", "₮", "Tether"),
    }

    result = {}

    for api_id, (code, symbol, name) in mapping.items():
        item = data.get(api_id, {})
        price = item.get("ils")

        if price is None:
            continue

        result[code] = {
            "symbol": symbol,
            "name": name,
            "price": float(price),
            "change": item.get("ils_24h_change"),
        }

    return result


def get_currency_rates():
    rates = {}

    try:
        rates.update(get_boi_rates())
    except Exception as error:
        print("ВАЛЮТЫ — БАНК ИЗРАИЛЯ:", error)

    try:
        extra = get_frankfurter_rates()

        for code, item in extra.items():
            if code not in rates:
                rates[code] = item

    except Exception as error:
        print("ВАЛЮТЫ — FRANKFURTER:", error)

    return rates


def format_rate_number(value, decimals=2):
    return f"{value:,.{decimals}f}".replace(",", " ")


def make_currency_pair(spec, info):
    code, flag, country, name, plural, units = spec
    rate = info["ils_per_unit"]

    direct = rate * units
    reverse = 1000 / rate

    if direct >= 100:
        direct_decimals = 0
    elif direct >= 1:
        direct_decimals = 2
    else:
        direct_decimals = 3

    if reverse >= 10000:
        reverse_decimals = 0
    elif reverse >= 1000:
        reverse_decimals = 1
    else:
        reverse_decimals = 2

    if units == 1:
        first = f"за 1 {name.lower()}"
    else:
        first = f"за {units} {plural}"

    return (
        f"{flag} {country} — {name}\n"
        f"{first} — "
        f"{format_rate_number(direct, direct_decimals)} ₪\n"
        f"за 1000 ₪ — "
        f"{format_rate_number(reverse, reverse_decimals)} {code}"
    )


def make_crypto_pair(code, item):
    price = item["price"]
    reverse = 1000 / price

    if price >= 1000:
        price_decimals = 0
    elif price >= 1:
        price_decimals = 2
    else:
        price_decimals = 4

    lines = [
        f"{item['symbol']} {item['name']} — шекель",
        (
            f"за 1 {code} — "
            f"{format_rate_number(price, price_decimals)} ₪"
        ),
        (
            "за 1000 ₪ — "
            f"{reverse:.6f} {code}"
        ),
    ]

    change = item.get("change")

    if change is not None:
        change = float(change)

        arrow = (
            "▲"
            if change > 0
            else "▼"
            if change < 0
            else "→"
        )

        lines.append(
            f"за 24 часа — {arrow} {abs(change):.2f}%"
        )

    return "\n".join(lines)


def make_rates_text():
    rates = get_currency_rates()
    specs = {spec[0]: spec for spec in CURRENCY_SPECS}

    lines = ["💱 КУРСЫ ВАЛЮТ", ""]

    for title, codes in CURRENCY_GROUPS:
        lines.extend([title, ""])

        for code in codes:
            info = rates.get(code)

            if info is None:
                spec = specs[code]
                lines.extend([
                    f"{spec[1]} {spec[2]} — {spec[3]}",
                    "данных нет",
                    "",
                ])
            else:
                lines.extend([
                    make_currency_pair(specs[code], info),
                    "",
                ])

    lines.extend(["КРИПТОВАЛЮТА", ""])

    try:
        crypto = get_crypto_rates()

        for code in ("BTC", "ETH", "USDT"):
            item = crypto.get(code)

            if item is None:
                lines.extend([
                    f"{code} — шекель",
                    "данных нет",
                    "",
                ])
                continue

            lines.extend([
                make_crypto_pair(code, item),
                "",
            ])

    except Exception as error:
        print("ВАЛЮТЫ — КРИПТО:", error)

        lines.extend([
            "₿ Bitcoin — шекель",
            "данных нет",
            "",
            "Ξ Ethereum — шекель",
            "данных нет",
            "",
            "₮ Tether — шекель",
            "данных нет",
        ])

    now = datetime.now(TZ)

    lines.extend([
        "",
        f"🕒 Обновлено: {now.strftime('%H:%M:%S')}",
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


def create_rates_post():
    # Совместимость со старыми вызовами: новых постов НЕ создаём.
    return update_rates()


def update_rates():
    # Один постоянный подробный пост валют.
    # Дальше только редактируем его.
    message_id = update_persistent_post(
        make_rates_text(),
        RATES_MESSAGE_ID_FILE
    )

    print(
        "ВАЛЮТЫ: постоянный пост обновлён:",
        datetime.now(TZ).strftime("%H:%M:%S"),
        "message_id:",
        message_id
    )

    safe_update_services_post()

    return message_id


# =========================================================
# МИРОВОЕ ВРЕМЯ
# | 2.4.1
# =========================================================

WORLD_CITIES = [
    ("Гонолулу", "Pacific/Honolulu"),
    ("Анкоридж", "America/Anchorage"),
    ("Лос-Анджелес", "America/Los_Angeles"),
    ("Ванкувер", "America/Vancouver"),
    ("Денвер", "America/Denver"),
    ("Чикаго", "America/Chicago"),
    ("Мехико", "America/Mexico_City"),
    ("Нью-Йорк", "America/New_York"),
    ("Торонто", "America/Toronto"),
    ("Каракас", "America/Caracas"),
    ("Галифакс", "America/Halifax"),
    ("Буэнос-Айрес", "America/Argentina/Buenos_Aires"),
    ("Сан-Паулу", "America/Sao_Paulo"),
    ("Лондон", "Europe/London"),
    ("Лиссабон", "Europe/Lisbon"),
    ("Париж", "Europe/Paris"),
    ("Берлин", "Europe/Berlin"),
    ("Рим", "Europe/Rome"),
    ("Афины", "Europe/Athens"),
    ("Бухарест", "Europe/Bucharest"),
    ("Каир", "Africa/Cairo"),
    ("Иерусалим", "Asia/Jerusalem"),
    ("Москва", "Europe/Moscow"),
    ("Эр-Рияд", "Asia/Riyadh"),
    ("Дубай", "Asia/Dubai"),
    ("Баку", "Asia/Baku"),
    ("Тбилиси", "Asia/Tbilisi"),
    ("Ташкент", "Asia/Tashkent"),
    ("Алматы", "Asia/Almaty"),
    ("Бишкек", "Asia/Bishkek"),
    ("Дели", "Asia/Kolkata"),
    ("Бангкок", "Asia/Bangkok"),
    ("Джакарта", "Asia/Jakarta"),
    ("Пекин", "Asia/Shanghai"),
    ("Гонконг", "Asia/Hong_Kong"),
    ("Сингапур", "Asia/Singapore"),
    ("Токио", "Asia/Tokyo"),
    ("Сеул", "Asia/Seoul"),
    ("Сидней", "Australia/Sydney"),
    ("Окленд", "Pacific/Auckland"),
]


def make_time_text():
    now_jerusalem = datetime.now(TZ)
    jerusalem_date = now_jerusalem.date()

    # Группируем сначала по календарной дате относительно Иерусалима,
    # затем по локальному времени.
    grouped = {}

    for city, zone_name in WORLD_CITIES:
        try:
            local_now = datetime.now(
                ZoneInfo(zone_name)
            )

            local_date = local_now.date()
            hhmm = local_now.strftime("%H:%M")

            grouped.setdefault(
                local_date,
                {}
            ).setdefault(
                hhmm,
                []
            ).append(city)

        except Exception:
            continue

    def time_key(value):
        h, m = value.split(":")
        return (int(h), int(m))

    lines = [
        "🌍 МИРОВОЕ ВРЕМЯ",
        "",
        (
            "📍 Иерусалим: "
            f"{gregorian_date_ru(jerusalem_date)}"
        ),
        "",
    ]

    for local_date in sorted(grouped.keys()):
        delta_days = (local_date - jerusalem_date).days

        if delta_days < 0:
            date_title = (
                f"◀ {gregorian_date_ru(local_date)} "
                f"({delta_days} день)"
            )
        elif delta_days > 0:
            date_title = (
                f"▶ {gregorian_date_ru(local_date)} "
                f"(+{delta_days} день)"
            )
        else:
            date_title = (
                f"● {gregorian_date_ru(local_date)} "
                "(дата Иерусалима)"
            )

        lines.append(date_title)

        for hhmm in sorted(
            grouped[local_date].keys(),
            key=time_key
        ):
            cities = ", ".join(
                grouped[local_date][hhmm]
            )

            lines.append(
                f"{hhmm} — {cities}"
            )

        lines.append("")

    lines.extend([
        f"🕒 Обновлено: {now_jerusalem.strftime('%H:%M:%S')}",
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)

def create_time_post():
    # Совместимость со старыми вызовами: новых постов НЕ создаём.
    return update_time_post()


def update_time_post():
    # Один постоянный подробный пост мирового времени.
    # Дальше только редактируем его.
    message_id = update_persistent_post(
        make_time_text(),
        TIME_MESSAGE_ID_FILE
    )

    safe_update_services_post()

    return message_id


# =========================================================
# 3. TELEGRAM-КОМАНДЫ
# =========================================================
# Здесь только маршрутизация команд.
# Логика погоды и аэропорта остаётся внутри своих блоков.
# =========================================================
# 3. НОВОСТИ ХАЙФЫ — МУНИЦИПАЛИТЕТ
# =========================================================

HAIFA_NEWS_URL = "https://www.haifa.muni.il/haifa-news/"
HAIFA_NEWS_INTERVAL = 60 * 60
HAIFA_NEWS_SEEN_FILE = state_file("haifa_news_seen_v3.json")
HAIFA_NEWS_TRANSLATION_CACHE_FILE = state_file("haifa_news_translation_cache.json")
HAIFA_NEWS_FIRST_RUN = True


class HaifaNewsLinksParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        url = urljoin(HAIFA_NEWS_URL, href)
        if "/article/" not in url:
            return
        if not url.startswith("https://www.haifa.muni.il/"):
            return
        url = url.split("#", 1)[0].split("?", 1)[0]
        if url not in self.links:
            self.links.append(url)


class HaifaArticleTitleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og_title = None
        self.in_title = False
        self.title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): v for k, v in attrs}
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "meta":
            prop = str(attrs.get("property") or attrs.get("name") or "").lower()
            content = str(attrs.get("content") or "").strip()
            if prop in ("og:title", "twitter:title") and content and not self.og_title:
                self.og_title = content

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title and str(data).strip():
            self.title_parts.append(str(data).strip())

    def get_title(self):
        title = self.og_title or " ".join(self.title_parts).strip()
        for suffix in (" - עיריית חיפה", " : עיריית חיפה", " | עיריית חיפה"):
            if title.endswith(suffix):
                title = title[:-len(suffix)].rstrip()
        return title or "Новая публикация муниципалитета Хайфы"


def load_seen_haifa_news():
    if not os.path.exists(HAIFA_NEWS_SEEN_FILE):
        return None
    try:
        with open(HAIFA_NEWS_SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(str(x) for x in data) if isinstance(data, list) else set()
    except Exception as error:
        print("ХАЙФА NEWS — ошибка state:", error)
        return set()


def save_seen_haifa_news(seen):
    with open(HAIFA_NEWS_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-1000:], f, ensure_ascii=False, indent=2)


def fetch_haifa_news_links():
    response = requests.get(
        HAIFA_NEWS_URL,
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "he,en;q=0.8"},
        timeout=30,
    )
    response.raise_for_status()
    parser = HaifaNewsLinksParser()
    parser.feed(response.text)
    return parser.links


def fetch_haifa_article_title(url):
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "he,en;q=0.8"},
        timeout=30,
    )
    response.raise_for_status()
    parser = HaifaArticleTitleParser()
    parser.feed(response.text)
    return parser.get_title()


def load_haifa_translation_cache():
    if not os.path.exists(
        HAIFA_NEWS_TRANSLATION_CACHE_FILE
    ):
        return {}

    try:
        with open(
            HAIFA_NEWS_TRANSLATION_CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, dict):
            return data

    except Exception as error:
        print(
            "ХАЙФА NEWS — ошибка кэша переводов:",
            error
        )

    return {}


def save_haifa_translation_cache(cache):
    try:
        with open(
            HAIFA_NEWS_TRANSLATION_CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                cache,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        print(
            "ХАЙФА NEWS — ошибка записи кэша переводов:",
            error
        )


def translate_hebrew_title_to_ru(title):
    """
    Перевод заголовка иврит -> русский.
    Используется MyMemory API + локальный кэш.
    """
    title = " ".join(
        str(title or "").split()
    ).strip()

    if not title:
        return ""

    cache = load_haifa_translation_cache()

    cached = cache.get(title)

    if cached:
        return cached

    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={
                "q": title,
                "langpair": "he|ru",
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64)"
                )
            },
            timeout=30,
        )

        response.raise_for_status()
        data = response.json()

        translated = str(
            (
                data.get("responseData")
                or {}
            ).get(
                "translatedText"
            )
            or ""
        ).strip()

        if translated:
            cache[title] = translated
            save_haifa_translation_cache(
                cache
            )
            return translated

        print(
            "ХАЙФА NEWS — перевод пустой:",
            title
        )

    except Exception as error:
        print(
            "ХАЙФА NEWS — перевод недоступен:",
            error
        )

    return title


def make_haifa_news_text(title, url):
    title_ru = translate_hebrew_title_to_ru(title)
    title_ru = html.escape(str(title_ru))
    safe_url = html.escape(str(url), quote=True)

    return (
        "🏙 ХАЙФА — НОВОСТИ МЭРИИ\n\n"
        f"{title_ru}\n\n"
        "Source: Haifa Municipality\n"
        f'<a href="{safe_url}">Оригинал</a>\n\n'
        "@ne_zaika"
    )


def send_haifa_news_message(text):
    safe_text, truncated = _truncate_telegram_text(text)

    print(
        "ХАЙФА NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    return result["result"]["message_id"]


def check_haifa_news():
    links = fetch_haifa_news_links()
    print("ХАЙФА NEWS: найдено ссылок:", len(links))

    global HAIFA_NEWS_FIRST_RUN

    seen = load_seen_haifa_news()
    if seen is None:
        seen = set()

    # При КАЖДОМ новом запуске сервера публикуем 10 последних,
    # независимо от сохранённого state-файла.
    first_run = HAIFA_NEWS_FIRST_RUN

    if first_run:
        new_links = links[:10]
        print(
            "ХАЙФА NEWS: первый запуск — публикую последние:",
            len(new_links)
        )
    else:
        new_links = [
            url for url in links
            if url not in seen
        ]

    if not new_links:
        print("ХАЙФА NEWS: новых публикаций нет")
        return 0

    # Лента обычно идёт от новых к старым.
    # Публикуем от старых к новым, чтобы самая свежая
    # в итоге оказалась последней в Telegram-ленте.
    new_links = list(reversed(new_links))
    published = 0

    print(
        "ХАЙФА NEWS: к публикации:",
        len(new_links)
    )

    for index, url in enumerate(new_links, start=1):
        try:
            title = fetch_haifa_article_title(url)
            send_haifa_news_message(
                make_haifa_news_text(
                    title,
                    url
                )
            )

            seen.add(url)
            save_seen_haifa_news(seen)
            published += 1

            print(
                "ХАЙФА NEWS: опубликовано",
                f"{index}/{len(new_links)}:",
                url
            )

            # Не отправляем десятки постов одним мгновенным залпом.
            if index < len(new_links):
                time.sleep(1.1)

        except Exception as error:
            # Неуспешный URL НЕ добавляется в seen —
            # значит, бот попробует его снова через час.
            print(
                "ХАЙФА NEWS — ОШИБКА СТАТЬИ:",
                url,
                error
            )

    if first_run:
        # После стартовых 10 вся текущая лента считается просмотренной.
        seen.update(links)
        save_seen_haifa_news(seen)
        HAIFA_NEWS_FIRST_RUN = False

    log_line(
        "ХАЙФА NEWS: опубликовано всего:",
        published
    )

    return published


def haifa_news_scheduler_loop():
    first_cycle = True

    while True:
        if not first_cycle:
            now = datetime.now(TZ)

            next_run = (
                now.replace(
                    minute=0,
                    second=0,
                    microsecond=0
                )
                + timedelta(hours=1)
            )

            log_line(
                "ХАЙФА NEWS: следующее обновление",
                next_run.strftime("%d.%m.%Y %H:%M:%S")
            )

            time.sleep(
                max(
                    0,
                    (next_run - now).total_seconds()
                )
            )

        first_cycle = False

        try:
            log_line("ХАЙФА NEWS: запуск проверки")
            check_haifa_news()
            log_line("ХАЙФА NEWS: проверка завершена")

        except Exception as error:
            log_line(
                "ХАЙФА NEWS — ОШИБКА:",
                error
            )

# =========================================================


# =========================================================
# НОВОСТИ ИЗРАИЛЯ — JERUSALEM POST RSS
# =========================================================
# Используется только официальный RSS Jerusalem Post.
# При каждом запуске сервера: 10 последних.
# Далее: раз в час все новые.
# Заголовок RSS не изменяется и не переводится.
# =========================================================


# =========================================================
# ПЕРЕВОД НОВОСТЕЙ EN -> RU
# =========================================================

NEWS_TRANSLATION_CACHE_FILE = state_file(
    "news_translation_cache.json"
)

NEWS_TRANSLATION_CACHE_LOCK = threading.RLock()


def load_news_translation_cache():
    with NEWS_TRANSLATION_CACHE_LOCK:
        if not os.path.exists(NEWS_TRANSLATION_CACHE_FILE):
            return {}

        try:
            with open(
                NEWS_TRANSLATION_CACHE_FILE,
                "r",
                encoding="utf-8"
            ) as file:
                data = json.load(file)

            if isinstance(data, dict):
                return data

        except Exception as error:
            log_line(
                "NEWS TRANSLATE — ошибка чтения кэша:",
                error
            )

        return {}


def save_news_translation_cache(cache):
    with NEWS_TRANSLATION_CACHE_LOCK:
        try:
            temp_file = NEWS_TRANSLATION_CACHE_FILE + ".tmp"

            with open(
                temp_file,
                "w",
                encoding="utf-8"
            ) as file:
                json.dump(
                    cache,
                    file,
                    ensure_ascii=False,
                    indent=2
                )

            os.replace(
                temp_file,
                NEWS_TRANSLATION_CACHE_FILE
            )

        except Exception as error:
            log_line(
                "NEWS TRANSLATE — ошибка записи кэша:",
                error
            )


def clean_news_text(value):
    value = str(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)

    return " ".join(value.split()).strip()


def translate_news_to_ru(value, source_lang="en"):
    value = clean_news_text(value)

    if not value:
        return ""

    value = value[:480]
    key = f"{source_lang}|{value}"

    with NEWS_TRANSLATION_CACHE_LOCK:
        cache = load_news_translation_cache()

        if cache.get(key):
            return cache[key]

    try:
        response = requests.get(
            "https://api.mymemory.translated.net/get",
            params={
                "q": value,
                "langpair": f"{source_lang}|ru",
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64)"
                )
            },
            timeout=30,
        )

        response.raise_for_status()
        data = response.json()

        translated = clean_news_text(
            (
                data.get("responseData")
                or {}
            ).get("translatedText")
        )

        if translated:
            with NEWS_TRANSLATION_CACHE_LOCK:
                # Перечитываем свежую версию: другой поток мог
                # добавить переводы, пока этот запрос был в сети.
                cache = load_news_translation_cache()
                cache[key] = translated

                if len(cache) > 5000:
                    cache = dict(
                        list(cache.items())[-4000:]
                    )

                save_news_translation_cache(cache)

            return translated

    except Exception as error:
        log_line(
            "NEWS TRANSLATE — перевод недоступен:",
            error
        )

    return value


ISRAEL_NEWS_RSS_URL = "https://www.jpost.com/rss/rssfeedsisraelnews.aspx"
ISRAEL_NEWS_INTERVAL = 60 * 60
ISRAEL_NEWS_SEEN_FILE = state_file("israel_news_jpost_seen.json")
ISRAEL_NEWS_FIRST_RUN = True


def load_seen_israel_news():
    if not os.path.exists(ISRAEL_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            ISRAEL_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        print(
            "ИЗРАИЛЬ NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_israel_news(seen):
    try:
        with open(
            ISRAEL_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-3000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        print(
            "ИЗРАИЛЬ NEWS — ошибка записи state:",
            error
        )


def fetch_israel_news():
    response = requests.get(
        ISRAEL_NEWS_RSS_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64)"
            ),
            "Accept": (
                "application/rss+xml,"
                "application/xml,text/xml,*/*"
            ),
        },
        timeout=30,
    )

    response.raise_for_status()

    root = ET.fromstring(
        response.content
    )

    items = []
    urls = set()

    for item in root.findall(".//item"):
        title = (
            item.findtext("title")
            or ""
        ).strip()

        description = clean_news_text(
            item.findtext("description")
            or ""
        )

        url = (
            item.findtext("link")
            or item.findtext("guid")
            or ""
        ).strip()

        if (
            not title
            or not url.startswith("http")
            or url in urls
        ):
            continue

        urls.add(url)

        items.append(
            {
                "title": title,
                "description": description,
                "url": url,
            }
        )

    print(
        "ИЗРАИЛЬ NEWS: найдено:",
        len(items)
    )

    return items


def make_israel_news_text(
    title,
    description,
    url
):
    title_ru = translate_news_to_ru(title, "en")
    description_ru = translate_news_to_ru(
        description,
        "en"
    )

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(
        str(url),
        quote=True
    )

    parts = [
        "🇮🇱 НОВОСТИ ИЗРАИЛЯ",
        "",
        safe_title,
    ]

    if (
        safe_description
        and safe_description.lower()
        != safe_title.lower()
    ):
        parts += [
            "",
            safe_description,
        ]

    parts += [
        "",
        "Source: Jerusalem Post",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)

def send_israel_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "ИЗРАИЛЬ NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    return result["result"]["message_id"]


def check_israel_news():
    global ISRAEL_NEWS_FIRST_RUN

    items = fetch_israel_news()

    if not items:
        print(
            "ИЗРАИЛЬ NEWS: публикаций не найдено"
        )
        return 0

    seen = load_seen_israel_news()

    if ISRAEL_NEWS_FIRST_RUN:
        to_publish = items[:10]

        print(
            "ИЗРАИЛЬ NEWS: запуск сервера — "
            f"публикую последние {len(to_publish)}"
        )

    else:
        to_publish = [
            item
            for item in items
            if item["url"] not in seen
        ]

        print(
            "ИЗРАИЛЬ NEWS: новых публикаций:",
            len(to_publish)
        )

    if not to_publish:
        return 0

    published = 0

    # RSS обычно от новых к старым.
    # В Telegram отправляем от старых к новым.
    for index, item in enumerate(
        reversed(to_publish),
        start=1
    ):
        try:
            send_israel_news_message(
                make_israel_news_text(
                    item["title"],
                    item.get("description", ""),
                    item["url"]
                )
            )

            seen.add(
                item["url"]
            )

            save_seen_israel_news(
                seen
            )

            published += 1

            print(
                "ИЗРАИЛЬ NEWS: опубликовано",
                f"{index}/{len(to_publish)}:",
                item["url"]
            )

            if index < len(to_publish):
                time.sleep(1.1)

        except Exception as error:
            print(
                "ИЗРАИЛЬ NEWS — ОШИБКА СТАТЬИ:",
                item["url"],
                error
            )

    if ISRAEL_NEWS_FIRST_RUN:
        # После стартовых 10 вся текущая RSS-лента
        # считается просмотренной.
        seen.update(
            item["url"]
            for item in items
        )

        save_seen_israel_news(
            seen
        )

        ISRAEL_NEWS_FIRST_RUN = False

    log_line(
        "ИЗРАИЛЬ NEWS: опубликовано всего:",
        published
    )

    return published


def israel_news_scheduler_loop():
    first_cycle = True

    while True:
        if not first_cycle:
            now = datetime.now(TZ)

            next_run = (
                now.replace(
                    minute=0,
                    second=0,
                    microsecond=0
                )
                + timedelta(hours=1)
            )

            log_line(
                "ИЗРАИЛЬ NEWS: следующее обновление",
                next_run.strftime("%d.%m.%Y %H:%M:%S")
            )

            time.sleep(
                max(
                    0,
                    (next_run - now).total_seconds()
                )
            )

        first_cycle = False

        try:
            log_line("ИЗРАИЛЬ NEWS: запуск проверки")
            check_israel_news()
            log_line("ИЗРАИЛЬ NEWS: проверка завершена")

        except Exception as error:
            log_line(
                "ИЗРАИЛЬ NEWS — ОШИБКА:",
                error
            )


# =========================================================
# МИРОВЫЕ НОВОСТИ — GDELT DOC 2.0
# =========================================================

VOA_TOP_STORIES_URL = "https://www.voanews.com/"
VOA_MIDDLE_EAST_RSS_URL = "https://www.voanews.com/api/zrbopl-vomx-tpeovm_"
VOA_EUROPE_RSS_URL = "https://www.voanews.com/api/zjbovl-vomx-tpebvmr"
VOA_UKRAINE_RSS_URL = "https://www.voanews.com/api/zt_rqyl-vomx-tpekboq_"
VOA_USA_RSS_URL = "https://www.voanews.com/api/zqboml-vomx-tpeivmy"
VOA_IRAN_RSS_URL = "https://www.voanews.com/api/zvgmqil-vomx-tpeumvqm"
VOA_CHINA_RSS_URL = "https://www.voanews.com/api/zmjuqtl-vomx-tpey_jqq"
VOA_SCIENCE_RSS_URL = "https://www.voanews.com/api/ztbopl-vomx-tpekvmm"

VOA_TECHNOLOGY_RSS_URL = "https://www.voanews.com/api/zyritl-vomx-tpettmq"
VOA_ECONOMY_RSS_URL = "https://www.voanews.com/api/zyboql-vomx-tpetvmi"
VOA_ARTS_CULTURE_RSS_URL = "https://www.voanews.com/api/zpbovl-vomx-tpe_vmr"

TECHNOLOGY_NEWS_SEEN_FILE = state_file("technology_news_seen.json")
TECHNOLOGY_NEWS_FIRST_RUN = True

ECONOMY_NEWS_SEEN_FILE = state_file("economy_news_seen.json")
ECONOMY_NEWS_FIRST_RUN = True

ARTS_CULTURE_NEWS_SEEN_FILE = state_file("arts_culture_news_seen.json")
ARTS_CULTURE_NEWS_FIRST_RUN = True

US_ELECTIONS_NEWS_SEEN_FILE = state_file("us_elections_news_seen.json")
US_ELECTIONS_NEWS_FIRST_RUN = True

SCIENCE_NEWS_SEEN_FILE = state_file("science_news_seen.json")
SCIENCE_NEWS_FIRST_RUN = True


CLIMATE_RSS_URL = "https://www.jpost.com/rss/rssenvironment"
CLIMATE_NEWS_SEEN_FILE = state_file("climate_news_seen.json")
CLIMATE_NEWS_FIRST_RUN = True

SPORT_ISRAEL_RSS_URL = "https://www.jpost.com/rss/rssfeedssports.aspx"
ESPN_TOP_RSS_URL = "https://www.espn.com/espn/rss/news"
ESPN_NBA_RSS_URL = "https://www.espn.com/espn/rss/nba/news"
ESPN_SOCCER_RSS_URL = "https://www.espn.com/espn/rss/soccer/news"
ESPN_TENNIS_RSS_URL = "https://www.espn.com/espn/rss/tennis/news"
ESPN_OLYMPIC_RSS_URL = "https://www.espn.com/espn/rss/oly/news"
ESPN_COLLEGE_BASKETBALL_RSS_URL = "https://www.espn.com/espn/rss/ncb/news"

ESPN_SPORT_SEEN_FILE = state_file("espn_sport_seen.json")
ESPN_SPORT_FIRST_RUN = True
SPORT_NEWS_SEEN_FILE = state_file("sport_news_seen.json")
SPORT_NEWS_FIRST_RUN = True

CHINA_NEWS_SEEN_FILE = state_file(
    "china_news_seen.json"
)

CHINA_NEWS_FIRST_RUN = True

IRAN_NEWS_SEEN_FILE = state_file(
    "iran_news_seen.json"
)

IRAN_NEWS_FIRST_RUN = True

USA_NEWS_SEEN_FILE = state_file(
    "usa_news_seen.json"
)

USA_NEWS_FIRST_RUN = True

UKRAINE_NEWS_SEEN_FILE = state_file(
    "ukraine_news_seen.json"
)

UKRAINE_NEWS_FIRST_RUN = True

EUROPE_NEWS_SEEN_FILE = state_file(
    "europe_news_seen.json"
)

EUROPE_NEWS_FIRST_RUN = True

MIDDLE_EAST_NEWS_SEEN_FILE = state_file(
    "middle_east_news_seen.json"
)

MIDDLE_EAST_NEWS_FIRST_RUN = True

WORLD_NEWS_SEEN_FILE = state_file(
    "world_news_seen.json"
)

WORLD_NEWS_FIRST_RUN = True



class VOATopStoriesParser(HTMLParser):
    def __init__(self):
        super().__init__()

        self.in_heading = False
        self.heading_tag = ""
        self.heading_parts = []

        self.in_top_stories = False

        self.in_link = False
        self.link_href = ""
        self.link_parts = []

        self.items = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in ("h1", "h2", "h3"):
            self.in_heading = True
            self.heading_tag = tag
            self.heading_parts = []

        if self.in_top_stories and tag == "a":
            attrs_dict = dict(attrs)
            href = str(
                attrs_dict.get("href")
                or ""
            ).strip()

            if href:
                self.in_link = True
                self.link_href = href
                self.link_parts = []

    def handle_data(self, data):
        if self.in_heading:
            self.heading_parts.append(data)

        if self.in_link:
            self.link_parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if (
            self.in_heading
            and tag == self.heading_tag
        ):
            heading = clean_news_text(
                " ".join(self.heading_parts)
            )

            if heading.lower() == "top stories":
                self.in_top_stories = True

            elif self.in_top_stories:
                # Следующий крупный раздел означает,
                # что блок Top Stories закончился.
                self.in_top_stories = False

            self.in_heading = False
            self.heading_tag = ""
            self.heading_parts = []

        if self.in_link and tag == "a":
            title = clean_news_text(
                " ".join(self.link_parts)
            )

            href = self.link_href

            if title and href:
                self.items.append(
                    {
                        "title": title,
                        "url": urljoin(
                            VOA_TOP_STORIES_URL,
                            href
                        ),
                    }
                )

            self.in_link = False
            self.link_href = ""
            self.link_parts = []


def load_seen_world_news():
    if not os.path.exists(WORLD_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            WORLD_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "WORLD NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_world_news(seen):
    try:
        with open(
            WORLD_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "WORLD NEWS — ошибка записи state:",
            error
        )


def fetch_world_news():
    response = requests.get(
        VOA_TOP_STORIES_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/142.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        },
        timeout=30,
    )

    response.raise_for_status()

    parser = VOATopStoriesParser()
    parser.feed(response.text)

    items = []
    seen_urls = set()
    seen_titles = set()

    for item in parser.items:
        title = clean_news_text(
            item.get("title")
        )

        url = str(
            item.get("url")
            or ""
        ).strip()

        if (
            not title
            or not url.startswith(
                "https://www.voanews.com/"
            )
        ):
            continue

        # Отбрасываем навигационные ссылки.
        if title.lower() in {
            "top stories",
            "more",
            "more from voa",
            "breaking news",
        }:
            continue

        normalized_title = re.sub(
            r"[^a-z0-9]+",
            " ",
            title.lower()
        ).strip()

        if (
            url in seen_urls
            or (
                normalized_title
                and normalized_title in seen_titles
            )
        ):
            continue

        seen_urls.add(url)

        if normalized_title:
            seen_titles.add(
                normalized_title
            )

        items.append(
            {
                "title": title,
                "url": url,
                "domain": "Voice of America",
            }
        )

    log_line(
        "WORLD NEWS / VOA TOP STORIES: найдено:",
        len(items)
    )

    if not items:
        raise RuntimeError(
            "VOA Top Stories не найдены "
            "на главной странице"
        )

    return items

def make_world_news_text(item):
    title_ru = translate_news_to_ru(
        item.get("title", ""),
        "en"
    )

    safe_title = html.escape(title_ru)

    safe_url = html.escape(
        item.get("url", ""),
        quote=True
    )

    return (
        "🌍 МИРОВЫЕ НОВОСТИ\n\n"
        f"{safe_title}\n\n"
        "Source: Voice of America\n"
        f'<a href="{safe_url}">Оригинал</a>\n\n'
        "@ne_zaika"
    )

def send_world_news_message(text):
    safe_text, truncated = _truncate_telegram_text(text)

    log_line(
        "WORLD NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    return result["result"]["message_id"]


def check_world_news():
    global WORLD_NEWS_FIRST_RUN

    items = fetch_world_news()

    if not items:
        log_line(
            "WORLD NEWS: публикаций не найдено"
        )
        return 0

    seen = load_seen_world_news()

    if WORLD_NEWS_FIRST_RUN:
        to_publish = items[:10]

        log_line(
            "WORLD NEWS: запуск сервера — "
            f"публикую последние {len(to_publish)}"
        )

    else:
        to_publish = [
            item
            for item in items
            if item["url"] not in seen
        ]

        log_line(
            "WORLD NEWS: новых публикаций:",
            len(to_publish)
        )

    if not to_publish:
        return 0

    published = 0

    for index, item in enumerate(
        reversed(to_publish),
        start=1
    ):
        try:
            send_world_news_message(
                make_world_news_text(item)
            )

            seen.add(item["url"])
            save_seen_world_news(seen)
            published += 1

            log_line(
                "WORLD NEWS: опубликовано",
                f"{index}/{len(to_publish)}:",
                item["url"]
            )

            if index < len(to_publish):
                time.sleep(1.1)

        except Exception as error:
            log_line(
                "WORLD NEWS — ОШИБКА СТАТЬИ:",
                item.get("url", ""),
                error
            )

    if WORLD_NEWS_FIRST_RUN:
        seen.update(
            item["url"]
            for item in items
        )

        save_seen_world_news(seen)
        WORLD_NEWS_FIRST_RUN = False

    log_line(
        "WORLD NEWS: опубликовано всего:",
        published
    )

    return published



def load_seen_middle_east_news():
    if not os.path.exists(MIDDLE_EAST_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            MIDDLE_EAST_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "БЛИЖНИЙ ВОСТОК NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_middle_east_news(seen):
    try:
        with open(
            MIDDLE_EAST_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "БЛИЖНИЙ ВОСТОК NEWS — ошибка записи state:",
            error
        )


def fetch_middle_east_news():
    response = requests.get(
        VOA_MIDDLE_EAST_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("БЛИЖНИЙ ВОСТОК / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA Middle East RSS не вернул новости")

    return items


def make_middle_east_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 БЛИЖНИЙ ВОСТОК", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_middle_east_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "БЛИЖНИЙ ВОСТОК NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "БЛИЖНИЙ ВОСТОК NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_middle_east_news():
    global MIDDLE_EAST_NEWS_FIRST_RUN

    log_line("БЛИЖНИЙ ВОСТОК NEWS: запуск проверки")

    try:
        items = fetch_middle_east_news()
        seen = load_seen_middle_east_news()

        if MIDDLE_EAST_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("БЛИЖНИЙ ВОСТОК NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_middle_east_news_text(item)

            print(
                "БЛИЖНИЙ ВОСТОК NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_middle_east_news_message(
                message
            )

            published += 1
            print(
                f"БЛИЖНИЙ ВОСТОК NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_middle_east_news(seen)

        log_line(
            "БЛИЖНИЙ ВОСТОК NEWS: опубликовано всего:",
            published
        )
        log_line("БЛИЖНИЙ ВОСТОК NEWS: проверка завершена")

        MIDDLE_EAST_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("БЛИЖНИЙ ВОСТОК NEWS — ОШИБКА:", exc)
        return 0


def middle_east_news_scheduler_loop():
    check_middle_east_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "БЛИЖНИЙ ВОСТОК NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_middle_east_news()


def load_seen_europe_news():
    if not os.path.exists(EUROPE_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            EUROPE_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "ЕВРОПА NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_europe_news(seen):
    try:
        with open(
            EUROPE_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "ЕВРОПА NEWS — ошибка записи state:",
            error
        )


def fetch_europe_news():
    response = requests.get(
        VOA_EUROPE_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("ЕВРОПА / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA Europe RSS не вернул новости")

    return items


def make_europe_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 ЕВРОПА", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_europe_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "ЕВРОПА NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "ЕВРОПА NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_europe_news():
    global EUROPE_NEWS_FIRST_RUN

    log_line("ЕВРОПА NEWS: запуск проверки")

    try:
        items = fetch_europe_news()
        seen = load_seen_europe_news()

        if EUROPE_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("ЕВРОПА NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_europe_news_text(item)

            print(
                "ЕВРОПА NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_europe_news_message(
                message
            )

            published += 1
            print(
                f"ЕВРОПА NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_europe_news(seen)

        log_line(
            "ЕВРОПА NEWS: опубликовано всего:",
            published
        )
        log_line("ЕВРОПА NEWS: проверка завершена")

        EUROPE_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("ЕВРОПА NEWS — ОШИБКА:", exc)
        return 0


def europe_news_scheduler_loop():
    check_europe_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "ЕВРОПА NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_europe_news()


def load_seen_ukraine_news():
    if not os.path.exists(UKRAINE_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            UKRAINE_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "УКРАИНА NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_ukraine_news(seen):
    try:
        with open(
            UKRAINE_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "УКРАИНА NEWS — ошибка записи state:",
            error
        )


def fetch_ukraine_news():
    response = requests.get(
        VOA_UKRAINE_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("УКРАИНА / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA Ukraine RSS не вернул новости")

    return items


def make_ukraine_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 УКРАИНА", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_ukraine_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "УКРАИНА NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "УКРАИНА NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_ukraine_news():
    global UKRAINE_NEWS_FIRST_RUN

    log_line("УКРАИНА NEWS: запуск проверки")

    try:
        items = fetch_ukraine_news()
        seen = load_seen_ukraine_news()

        if UKRAINE_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("УКРАИНА NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_ukraine_news_text(item)

            print(
                "УКРАИНА NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_ukraine_news_message(
                message
            )

            published += 1
            print(
                f"УКРАИНА NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_ukraine_news(seen)

        log_line(
            "УКРАИНА NEWS: опубликовано всего:",
            published
        )
        log_line("УКРАИНА NEWS: проверка завершена")

        UKRAINE_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("УКРАИНА NEWS — ОШИБКА:", exc)
        return 0


def ukraine_news_scheduler_loop():
    check_ukraine_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "УКРАИНА NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_ukraine_news()


def load_seen_usa_news():
    if not os.path.exists(USA_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            USA_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "США NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_usa_news(seen):
    try:
        with open(
            USA_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "США NEWS — ошибка записи state:",
            error
        )


def fetch_usa_news():
    response = requests.get(
        VOA_USA_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("США / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA USA RSS не вернул новости")

    return items


def make_usa_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 США", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_usa_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "США NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "США NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_usa_news():
    global USA_NEWS_FIRST_RUN

    log_line("США NEWS: запуск проверки")

    try:
        items = fetch_usa_news()
        seen = load_seen_usa_news()

        if USA_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("США NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_usa_news_text(item)

            print(
                "США NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_usa_news_message(
                message
            )

            published += 1
            print(
                f"США NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_usa_news(seen)

        log_line(
            "США NEWS: опубликовано всего:",
            published
        )
        log_line("США NEWS: проверка завершена")

        USA_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("США NEWS — ОШИБКА:", exc)
        return 0


def usa_news_scheduler_loop():
    check_usa_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "США NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_usa_news()


def load_seen_iran_news():
    if not os.path.exists(IRAN_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            IRAN_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "ИРАН NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_iran_news(seen):
    try:
        with open(
            IRAN_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "ИРАН NEWS — ошибка записи state:",
            error
        )


def fetch_iran_news():
    response = requests.get(
        VOA_IRAN_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("ИРАН / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA Iran RSS не вернул новости")

    return items


def make_iran_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 ИРАН", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_iran_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "ИРАН NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "ИРАН NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_iran_news():
    global IRAN_NEWS_FIRST_RUN

    log_line("ИРАН NEWS: запуск проверки")

    try:
        items = fetch_iran_news()
        seen = load_seen_iran_news()

        if IRAN_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("ИРАН NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_iran_news_text(item)

            print(
                "ИРАН NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_iran_news_message(
                message
            )

            published += 1
            print(
                f"ИРАН NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_iran_news(seen)

        log_line(
            "ИРАН NEWS: опубликовано всего:",
            published
        )
        log_line("ИРАН NEWS: проверка завершена")

        IRAN_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("ИРАН NEWS — ОШИБКА:", exc)
        return 0


def iran_news_scheduler_loop():
    check_iran_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "ИРАН NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_iran_news()


def world_news_scheduler_loop():
    first_cycle = True

    while True:
        if not first_cycle:
            now = datetime.now(TZ)

            next_run = (
                now.replace(
                    minute=0,
                    second=0,
                    microsecond=0
                )
                + timedelta(hours=1)
            )

            log_line(
                "WORLD NEWS: следующее обновление",
                next_run.strftime("%d.%m.%Y %H:%M:%S")
            )

            time.sleep(
                max(
                    0,
                    (next_run - now).total_seconds()
                )
            )

        first_cycle = False

        try:
            log_line("WORLD NEWS: запуск проверки")
            check_world_news()
            log_line("WORLD NEWS: проверка завершена")

        except Exception as error:
            log_line(
                "WORLD NEWS — ОШИБКА:",
                error
            )


def send_private_reply(chat_id, text):
    response = _telegram_send_post(
        data={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=10,
    )

    response.raise_for_status()


def check_telegram_commands(offset=None):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 0,
        "limit": 20
    }

    if offset is not None:
        params["offset"] = offset

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        return offset

    for update in data.get("result", []):
        offset = update["update_id"] + 1

        message = update.get("message")

        if not message:
            continue

        command = (
            message.get("text", "")
            .strip()
            .lower()
            .split()[0]
            if message.get("text")
            else ""
        )

        chat_id = message["chat"]["id"]

        try:
            if command == "/services":
                message_id = safe_update_services_post()

                send_private_reply(
                    chat_id,
                    (
                        "Навигация сервисов обновлена. "
                        f"Пост #{message_id}."
                        if message_id
                        else "Не удалось обновить навигацию сервисов."
                    )
                )

            elif command == "/weather":
                weather_message_id = update_weather()

                send_private_reply(
                    chat_id,
                    (
                        "Погода реально обновлена. "
                        f"Пост #{weather_message_id}. "
                        f"{datetime.now(TZ).strftime('%H:%M:%S')}"
                    )
                )

            elif command == "/arrivals":
                update_flight_board()

                send_private_reply(
                    chat_id,
                    "Прилёты обновлены в постоянных постах. Новых постов не создано."
                )

            elif command == "/departures":
                update_flight_board()

                send_private_reply(
                    chat_id,
                    "Вылеты обновлены в постоянных постах. Новых постов не создано."
                )

            elif command == "/time":
                update_time_post()

                send_private_reply(
                    chat_id,
                    "Мировое время обновлено в постоянном посте. Новый пост не создан."
                )

            elif command == "/flights":
                update_flight_board()
                send_private_reply(
                    chat_id,
                    "Табло Бен-Гуриона обновлено."
                )

            elif command == "/news":
                published = check_haifa_news()
                send_private_reply(
                    chat_id,
                    f"Новости Хайфы проверены. Опубликовано новых: {published}."
                )

            elif command == "/middleeast":
                published = check_middle_east_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Ближнего Востока проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/europe":
                published = check_europe_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Европы проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/ukraine":
                published = check_ukraine_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Украины проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/usa":
                published = check_usa_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости США проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/iran":
                published = check_iran_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Ирана проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/china":
                published = check_china_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Китая проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/science":
                published = check_science_news()

                send_private_reply(
                    chat_id,
                    (
                        "Наука и здоровье проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/climate":
                published = check_climate_news()

                send_private_reply(
                    chat_id,
                    (
                        "Климат проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/espn":
                published = check_espn_sport()

                send_private_reply(
                    chat_id,
                    (
                        "ESPN Sport проверен. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/sport":
                published = check_sport_news()

                send_private_reply(
                    chat_id,
                    (
                        "Спорт проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/technology":
                published = check_technology_news()
                send_private_reply(
                    chat_id,
                    (
                        "Технологии VOA проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/economy":
                published = check_economy_news()
                send_private_reply(
                    chat_id,
                    (
                        "Экономика VOA проверена. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/uselections":
                published = check_us_elections_news()
                send_private_reply(
                    chat_id,
                    (
                        "Выборы в США проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/culture":
                published = check_arts_culture_news()

                send_private_reply(
                    chat_id,
                    (
                        "Arts & Culture VOA проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/worldnews":
                published = check_world_news()

                send_private_reply(
                    chat_id,
                    (
                        "Мировые новости проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/israelnews":
                published = check_israel_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Израиля проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/rates":
                rates_message_id = update_rates()
                send_private_reply(
                    chat_id,
                    (
                        "Курсы обновлены. "
                        f"Пост #{rates_message_id}. "
                        f"{datetime.now(TZ).strftime('%H:%M:%S')}"
                    )
                )

        except Exception as error:
            print(
                "ОШИБКА TELEGRAM-КОМАНДЫ:",
                command,
                error
            )

            try:
                send_private_reply(
                    chat_id,
                    f"Ошибка выполнения {command}"
                )
            except Exception:
                pass

    return offset


# =========================================================
# 4. ПЛАНИРОВЩИК
# =========================================================
# Оба сервиса независимы.
#
# Погода:
#   проверяется раз в минуту;
#   автоматически обновляется при смене часа.
#
# Бен-Гурион:
#   НЕ проверяется минутным циклом;
#   отдельный таймер запускает обновление только в :00 и :30.
#
# Ошибка одного сервиса НЕ останавливает второй.
# =========================================================

def weather_schedule_key(now):
    return now.strftime(
        "%Y-%m-%d %H"
    )


def next_flights_run(now=None):
    """
    Следующий запуск табло Бен-Гуриона:
    строго ближайшие :00 или :30.
    """
    if now is None:
        now = datetime.now(TZ)

    if now.minute < 30:
        target = now.replace(
            minute=30,
            second=0,
            microsecond=0
        )
    else:
        target = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

    return target



# =========================================================
def load_seen_china_news():
    if not os.path.exists(CHINA_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            CHINA_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "КИТАЙ NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_china_news(seen):
    try:
        with open(
            CHINA_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "КИТАЙ NEWS — ошибка записи state:",
            error
        )


def fetch_china_news():
    response = requests.get(
        VOA_CHINA_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("КИТАЙ / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA China RSS не вернул новости")

    return items


def make_china_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 КИТАЙ", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_china_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "КИТАЙ NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "КИТАЙ NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_china_news():
    global CHINA_NEWS_FIRST_RUN

    log_line("КИТАЙ NEWS: запуск проверки")

    try:
        items = fetch_china_news()
        seen = load_seen_china_news()

        if CHINA_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("КИТАЙ NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_china_news_text(item)

            print(
                "КИТАЙ NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_china_news_message(
                message
            )

            published += 1
            print(
                f"КИТАЙ NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_china_news(seen)

        log_line(
            "КИТАЙ NEWS: опубликовано всего:",
            published
        )
        log_line("КИТАЙ NEWS: проверка завершена")

        CHINA_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("КИТАЙ NEWS — ОШИБКА:", exc)
        return 0


def china_news_scheduler_loop():
    check_china_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "КИТАЙ NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_china_news()


def world_news_scheduler_loop():
    first_cycle = True

    while True:
        if not first_cycle:
            now = datetime.now(TZ)

            next_run = (
                now.replace(
                    minute=0,
                    second=0,
                    microsecond=0
                )
                + timedelta(hours=1)
            )

            log_line(
                "WORLD NEWS: следующее обновление",
                next_run.strftime("%d.%m.%Y %H:%M:%S")
            )

            time.sleep(
                max(
                    0,
                    (next_run - now).total_seconds()
                )
            )

        first_cycle = False

        try:
            log_line("WORLD NEWS: запуск проверки")
            check_world_news()
            log_line("WORLD NEWS: проверка завершена")

        except Exception as error:
            log_line(
                "WORLD NEWS — ОШИБКА:",
                error
            )


def send_private_reply(chat_id, text):
    response = _telegram_send_post(
        data={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=10,
    )

    response.raise_for_status()


def check_telegram_commands(offset=None):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 0,
        "limit": 20
    }

    if offset is not None:
        params["offset"] = offset

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        return offset

    for update in data.get("result", []):
        offset = update["update_id"] + 1

        message = update.get("message")

        if not message:
            continue

        command = (
            message.get("text", "")
            .strip()
            .lower()
            .split()[0]
            if message.get("text")
            else ""
        )

        chat_id = message["chat"]["id"]

        try:
            if command == "/weather":
                weather_message_id = update_weather()

                send_private_reply(
                    chat_id,
                    (
                        "Погода реально обновлена. "
                        f"Пост #{weather_message_id}. "
                        f"{datetime.now(TZ).strftime('%H:%M:%S')}"
                    )
                )

            elif command == "/arrivals":
                update_flight_board()

                send_private_reply(
                    chat_id,
                    "Прилёты обновлены в постоянных постах. Новых постов не создано."
                )

            elif command == "/departures":
                update_flight_board()

                send_private_reply(
                    chat_id,
                    "Вылеты обновлены в постоянных постах. Новых постов не создано."
                )

            elif command == "/time":
                update_time_post()

                send_private_reply(
                    chat_id,
                    "Мировое время обновлено в постоянном посте. Новый пост не создан."
                )

            elif command == "/flights":
                update_flight_board()
                send_private_reply(
                    chat_id,
                    "Табло Бен-Гуриона обновлено."
                )

            elif command == "/news":
                published = check_haifa_news()
                send_private_reply(
                    chat_id,
                    f"Новости Хайфы проверены. Опубликовано новых: {published}."
                )

            elif command == "/middleeast":
                published = check_middle_east_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Ближнего Востока проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/europe":
                published = check_europe_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Европы проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/ukraine":
                published = check_ukraine_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Украины проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/usa":
                published = check_usa_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости США проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/iran":
                published = check_china_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Ирана проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/worldnews":
                published = check_world_news()

                send_private_reply(
                    chat_id,
                    (
                        "Мировые новости проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/israelnews":
                published = check_israel_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Израиля проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/rates":
                rates_message_id = update_rates()
                send_private_reply(
                    chat_id,
                    (
                        "Курсы обновлены. "
                        f"Пост #{rates_message_id}. "
                        f"{datetime.now(TZ).strftime('%H:%M:%S')}"
                    )
                )

        except Exception as error:
            print(
                "ОШИБКА TELEGRAM-КОМАНДЫ:",
                command,
                error
            )

            try:
                send_private_reply(
                    chat_id,
                    f"Ошибка выполнения {command}"
                )
            except Exception:
                pass

    return offset


# =========================================================
# 4. ПЛАНИРОВЩИК
# =========================================================
# Оба сервиса независимы.
#
# Погода:
#   проверяется раз в минуту;
#   автоматически обновляется при смене часа.
#
# Бен-Гурион:
#   НЕ проверяется минутным циклом;
#   отдельный таймер запускает обновление только в :00 и :30.
#
# Ошибка одного сервиса НЕ останавливает второй.
# =========================================================

def weather_schedule_key(now):
    return now.strftime(
        "%Y-%m-%d %H"
    )


def next_flights_run(now=None):
    """
    Следующий запуск табло Бен-Гуриона:
    строго ближайшие :00 или :30.
    """
    if now is None:
        now = datetime.now(TZ)

    if now.minute < 30:
        target = now.replace(
            minute=30,
            second=0,
            microsecond=0
        )
    else:
        target = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

    return target



# =========================================================
def load_seen_science_news():
    if not os.path.exists(SCIENCE_NEWS_SEEN_FILE):
        return set()

    try:
        with open(
            SCIENCE_NEWS_SEEN_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return set(data)

    except Exception as error:
        log_line(
            "НАУКА И ЗДОРОВЬЕ NEWS — ошибка state:",
            error
        )

    return set()


def save_seen_science_news(seen):
    try:
        with open(
            SCIENCE_NEWS_SEEN_FILE,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                list(seen)[-5000:],
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:
        log_line(
            "НАУКА И ЗДОРОВЬЕ NEWS — ошибка записи state:",
            error
        )


def fetch_science_news():
    response = requests.get(
        VOA_SCIENCE_RSS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        url = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")

        if title and url:
            items.append({
                "title": title,
                "url": url,
                "description": description,
            })

    log_line("НАУКА И ЗДОРОВЬЕ / VOA: найдено:", len(items))

    if not items:
        raise RuntimeError("VOA Science & Health RSS не вернул новости")

    return items


def make_science_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = ["🌍 НАУКА И ЗДОРОВЬЕ", "", safe_title]

    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: Voice of America",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]

    return "\n".join(parts)



def send_science_news_message(text):
    safe_text, truncated = (
        _truncate_telegram_text(
            text
        )
    )

    print(
        "НАУКА И ЗДОРОВЬЕ NEWS TELEGRAM:",
        f"{len(safe_text)}/{TELEGRAM_TEXT_LIMIT} символов",
        "(ОБРЕЗАНО)" if truncated else ""
    )

    response = _telegram_send_post(
        data={
            "chat_id": CHANNEL,
            "text": safe_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    message_id = result["result"]["message_id"]

    print(
        "НАУКА И ЗДОРОВЬЕ NEWS TELEGRAM: OK",
        f"message_id={message_id}"
    )

    return message_id


def check_science_news():
    global SCIENCE_NEWS_FIRST_RUN

    log_line("НАУКА И ЗДОРОВЬЕ NEWS: запуск проверки")

    try:
        items = fetch_science_news()
        seen = load_seen_science_news()

        if SCIENCE_NEWS_FIRST_RUN:
            to_publish = items[:10]
            print("НАУКА И ЗДОРОВЬЕ NEWS: запуск сервера — публикую последние 10")
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_science_news_text(item)

            print(
                "НАУКА И ЗДОРОВЬЕ NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            send_science_news_message(
                message
            )

            published += 1
            print(
                f"НАУКА И ЗДОРОВЬЕ NEWS: опубликовано "
                f"{index}/{len(to_publish)}: {item.get('url', '')}"
            )

        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_science_news(seen)

        log_line(
            "НАУКА И ЗДОРОВЬЕ NEWS: опубликовано всего:",
            published
        )
        log_line("НАУКА И ЗДОРОВЬЕ NEWS: проверка завершена")

        SCIENCE_NEWS_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("НАУКА И ЗДОРОВЬЕ NEWS — ОШИБКА:", exc)
        return 0


def science_news_scheduler_loop():
    check_science_news()

    while True:
        now = datetime.now()
        next_run = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

        log_line(
            "НАУКА И ЗДОРОВЬЕ NEWS: следующее обновление",
            next_run.strftime("%d.%m.%Y %H:%M:%S")
        )

        seconds = max(
            1,
            (next_run - datetime.now()).total_seconds()
        )
        time.sleep(seconds)

        check_science_news()


def world_news_scheduler_loop():
    first_cycle = True

    while True:
        if not first_cycle:
            now = datetime.now(TZ)

            next_run = (
                now.replace(
                    minute=0,
                    second=0,
                    microsecond=0
                )
                + timedelta(hours=1)
            )

            log_line(
                "WORLD NEWS: следующее обновление",
                next_run.strftime("%d.%m.%Y %H:%M:%S")
            )

            time.sleep(
                max(
                    0,
                    (next_run - now).total_seconds()
                )
            )

        first_cycle = False

        try:
            log_line("WORLD NEWS: запуск проверки")
            check_world_news()
            log_line("WORLD NEWS: проверка завершена")

        except Exception as error:
            log_line(
                "WORLD NEWS — ОШИБКА:",
                error
            )


def send_private_reply(chat_id, text):
    response = _telegram_send_post(
        data={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=10,
    )

    response.raise_for_status()


def check_telegram_commands(offset=None):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 0,
        "limit": 20
    }

    if offset is not None:
        params["offset"] = offset

    response = requests.get(
        url,
        params=params,
        timeout=10
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        return offset

    for update in data.get("result", []):
        offset = update["update_id"] + 1

        message = update.get("message")

        if not message:
            continue

        command = (
            message.get("text", "")
            .strip()
            .lower()
            .split()[0]
            if message.get("text")
            else ""
        )

        chat_id = message["chat"]["id"]

        try:
            if command == "/weather":
                weather_message_id = update_weather()

                send_private_reply(
                    chat_id,
                    (
                        "Погода реально обновлена. "
                        f"Пост #{weather_message_id}. "
                        f"{datetime.now(TZ).strftime('%H:%M:%S')}"
                    )
                )

            elif command == "/arrivals":
                update_flight_board()

                send_private_reply(
                    chat_id,
                    "Прилёты обновлены в постоянных постах. Новых постов не создано."
                )

            elif command == "/departures":
                update_flight_board()

                send_private_reply(
                    chat_id,
                    "Вылеты обновлены в постоянных постах. Новых постов не создано."
                )

            elif command == "/time":
                update_time_post()

                send_private_reply(
                    chat_id,
                    "Мировое время обновлено в постоянном посте. Новый пост не создан."
                )

            elif command == "/flights":
                update_flight_board()
                send_private_reply(
                    chat_id,
                    "Табло Бен-Гуриона обновлено."
                )

            elif command == "/news":
                published = check_haifa_news()
                send_private_reply(
                    chat_id,
                    f"Новости Хайфы проверены. Опубликовано новых: {published}."
                )

            elif command == "/middleeast":
                published = check_middle_east_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Ближнего Востока проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/europe":
                published = check_europe_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Европы проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/ukraine":
                published = check_ukraine_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Украины проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/usa":
                published = check_usa_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости США проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/iran":
                published = check_science_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Ирана проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )
            elif command == "/worldnews":
                published = check_world_news()

                send_private_reply(
                    chat_id,
                    (
                        "Мировые новости проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/israelnews":
                published = check_israel_news()

                send_private_reply(
                    chat_id,
                    (
                        "Новости Израиля проверены. "
                        f"Опубликовано новых: {published}."
                    )
                )

            elif command == "/rates":
                rates_message_id = update_rates()
                send_private_reply(
                    chat_id,
                    (
                        "Курсы обновлены. "
                        f"Пост #{rates_message_id}. "
                        f"{datetime.now(TZ).strftime('%H:%M:%S')}"
                    )
                )

        except Exception as error:
            print(
                "ОШИБКА TELEGRAM-КОМАНДЫ:",
                command,
                error
            )

            try:
                send_private_reply(
                    chat_id,
                    f"Ошибка выполнения {command}"
                )
            except Exception:
                pass

    return offset


# =========================================================
# 4. ПЛАНИРОВЩИК
# =========================================================
# Оба сервиса независимы.
#
# Погода:
#   проверяется раз в минуту;
#   автоматически обновляется при смене часа.
#
# Бен-Гурион:
#   НЕ проверяется минутным циклом;
#   отдельный таймер запускает обновление только в :00 и :30.
#
# Ошибка одного сервиса НЕ останавливает второй.
# =========================================================

def weather_schedule_key(now):
    return now.strftime(
        "%Y-%m-%d %H"
    )


def next_flights_run(now=None):
    """
    Следующий запуск табло Бен-Гуриона:
    строго ближайшие :00 или :30.
    """
    if now is None:
        now = datetime.now(TZ)

    if now.minute < 30:
        target = now.replace(
            minute=30,
            second=0,
            microsecond=0
        )
    else:
        target = (
            now.replace(
                minute=0,
                second=0,
                microsecond=0
            )
            + timedelta(hours=1)
        )

    return target



# =========================================================

def load_seen_climate_news():
    path = Path(CLIMATE_NEWS_SEEN_FILE)

    if not path.exists():
        return set()

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
        return set(
            data
            if isinstance(data, list)
            else []
        )

    except Exception as exc:
        log_line(
            "КЛИМАТ NEWS: ошибка чтения state:",
            exc
        )
        return set()


def save_seen_climate_news(seen):
    path = Path(CLIMATE_NEWS_SEEN_FILE)

    path.write_text(
        json.dumps(
            sorted(seen),
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def _fetch_rss_items(url, source_name, region=""):
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    items = []
    for node in root.findall(".//item"):
        title = clean_news_text(node.findtext("title") or "")
        link = str(node.findtext("link") or "").strip()
        description = clean_news_text(node.findtext("description") or "")
        if title and link:
            items.append({
                "title": title,
                "url": link,
                "description": description,
                "source": source_name,
                "region": region,
            })
    return items


def fetch_climate_news():
    items = _fetch_rss_items(
        CLIMATE_RSS_URL,
        "Jerusalem Post",
        "Климат"
    )
    log_line("КЛИМАТ / Jerusalem Post: найдено:", len(items))
    if not items:
        raise RuntimeError("Environment & Climate Change RSS не вернул новости")
    return items


def make_climate_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")
    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)
    parts = ["🌱 КЛИМАТ", "", safe_title]
    if safe_description:
        parts += ["", safe_description]
    parts += [
        "",
        "Source: Jerusalem Post",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]
    return "\n".join(parts)


def send_climate_news_message(text):
    return _telegram_send_post({
        "chat_id": CHANNEL,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def check_climate_news():
    global CLIMATE_NEWS_FIRST_RUN
    log_line("КЛИМАТ NEWS: запуск проверки")
    try:
        items = fetch_climate_news()
        seen = load_seen_climate_news()
        if CLIMATE_NEWS_FIRST_RUN:
            to_publish = items[:10]
            log_line("КЛИМАТ NEWS: запуск сервера — публикую последние", len(to_publish))
        else:
            to_publish = [item for item in items if item.get("url") not in seen]
        published = 0
        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_climate_news_text(item)
            log_line("КЛИМАТ NEWS TELEGRAM:", f"{len(message)}/4000 символов")
            send_climate_news_message(message)
            published += 1
            log_line(f"КЛИМАТ NEWS: опубликовано {index}/{len(to_publish)}:", item.get("url",""))
        for item in items:
            if item.get("url"):
                seen.add(item["url"])
        save_seen_climate_news(seen)
        log_line("КЛИМАТ NEWS: опубликовано всего:", published)
        log_line("КЛИМАТ NEWS: проверка завершена")
        CLIMATE_NEWS_FIRST_RUN = False
        return published
    except Exception as exc:
        log_line("КЛИМАТ NEWS — ОШИБКА:", exc)
        return 0


def load_seen_sport_news():
    path = Path(SPORT_NEWS_SEEN_FILE)

    if not path.exists():
        return set()

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
        return set(
            data
            if isinstance(data, list)
            else []
        )

    except Exception as exc:
        log_line(
            "СПОРТ NEWS: ошибка чтения state:",
            exc
        )
        return set()


def save_seen_sport_news(seen):
    path = Path(SPORT_NEWS_SEEN_FILE)

    path.write_text(
        json.dumps(
            sorted(seen),
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def fetch_sport_news():
    items = _fetch_rss_items(
        SPORT_ISRAEL_RSS_URL,
        "Jerusalem Post",
        "ИЗРАИЛЬ"
    )
    log_line("СПОРТ / ИЗРАИЛЬ / Jerusalem Post: найдено:", len(items))
    if not items:
        raise RuntimeError("Jerusalem Post Israeli Sports RSS не вернул новости")
    return items


def make_sport_news_text(item):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")
    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)
    region = html.escape(item.get("region", "МИР"))
    source = html.escape(item.get("source", ""))
    parts = ["🏆 СПОРТ ИЗРАИЛЯ", "", safe_title]
    if safe_description:
        parts += ["", safe_description]
    parts += [
        "",
        f"Source: {source}",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]
    return "\n".join(parts)


def send_sport_news_message(text):
    return _telegram_send_post({
        "chat_id": CHANNEL,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def check_sport_news():
    global SPORT_NEWS_FIRST_RUN
    log_line("СПОРТ NEWS: запуск проверки")
    try:
        items = fetch_sport_news()
        seen = load_seen_sport_news()
        if SPORT_NEWS_FIRST_RUN:
            to_publish = items[:10]
            log_line(
                "СПОРТ ИЗРАИЛЯ NEWS: запуск сервера — публикую последние",
                len(to_publish)
            )
        else:
            to_publish = [item for item in items if item.get("url") not in seen]
        published = 0
        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_sport_news_text(item)
            log_line("СПОРТ NEWS TELEGRAM:", f"{len(message)}/4000 символов")
            send_sport_news_message(message)
            published += 1
            log_line(
                f"СПОРТ NEWS: опубликовано {index}/{len(to_publish)} "
                f"[{item.get('region','')}]:",
                item.get("url","")
            )
        for item in items:
            if item.get("url"):
                seen.add(item["url"])
        save_seen_sport_news(seen)
        log_line("СПОРТ NEWS: опубликовано всего:", published)
        log_line("СПОРТ NEWS: проверка завершена")
        SPORT_NEWS_FIRST_RUN = False
        return published
    except Exception as exc:
        log_line("СПОРТ NEWS — ОШИБКА:", exc)
        return 0



def load_seen_espn_sport():
    path = Path(ESPN_SPORT_SEEN_FILE)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except Exception as exc:
        log_line("ESPN SPORT: ошибка чтения state:", exc)
        return set()


def save_seen_espn_sport(seen):
    path = Path(ESPN_SPORT_SEEN_FILE)
    path.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def fetch_espn_sport():
    feeds = [
        ("TOP HEADLINES", ESPN_TOP_RSS_URL, 10),
        ("NBA", ESPN_NBA_RSS_URL, 1),
        ("SOCCER", ESPN_SOCCER_RSS_URL, 1),
        ("TENNIS", ESPN_TENNIS_RSS_URL, 1),
        ("OLYMPIC SPORTS", ESPN_OLYMPIC_RSS_URL, 1),
        ("COLLEGE BASKETBALL", ESPN_COLLEGE_BASKETBALL_RSS_URL, 1),
    ]

    all_items = []

    for category, url, startup_limit in feeds:
        try:
            items = _fetch_rss_items(url, "ESPN", category)
            log_line(f"ESPN / {category}: найдено:", len(items))
            for item in items:
                item["category"] = category
                item["startup_limit"] = startup_limit
            all_items.extend(items)
        except Exception as exc:
            log_line(f"ESPN / {category} — ОШИБКА:", exc)

    if not all_items:
        raise RuntimeError("ESPN RSS не вернули новости")

    return all_items


def make_espn_sport_text(item):
    # ВАЖНО: ESPN RSS headline публикуется без перевода и без изменения.
    safe_title = html.escape(item.get("title", ""))
    safe_url = html.escape(item.get("url", ""), quote=True)
    category = html.escape(item.get("category", ""))

    return "\n".join([
        f"🏆 ESPN • {category}",
        "",
        safe_title,
        "",
        "Source: ESPN",
        f'<a href="{safe_url}">Original</a>',
        "",
        "@ne_zaika",
    ])


def send_espn_sport_message(text):
    return _telegram_send_post({
        "chat_id": CHANNEL,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def check_espn_sport():
    global ESPN_SPORT_FIRST_RUN

    log_line("ESPN SPORT: запуск проверки")

    try:
        items = fetch_espn_sport()
        seen = load_seen_espn_sport()

        if ESPN_SPORT_FIRST_RUN:
            # Стартовая выдача:
            # Top Headlines — 10;
            # NBA, Soccer, Tennis, Olympic Sports, College Basketball — по 1.
            to_publish = []
            categories = [
                ("TOP HEADLINES", 10),
                ("NBA", 1),
                ("SOCCER", 1),
                ("TENNIS", 1),
                ("OLYMPIC SPORTS", 1),
                ("COLLEGE BASKETBALL", 1),
            ]
            for category, limit in categories:
                selected = [
                    item for item in items
                    if item.get("category") == category
                ][:limit]
                to_publish.extend(selected)

            log_line(
                "ESPN SPORT: запуск сервера — публикаций:",
                len(to_publish)
            )
        else:
            # После первого запуска в каждый час :00 публикуются ВСЕ новые
            # материалы из всех шести ESPN RSS.
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(reversed(to_publish), start=1):
            message = make_espn_sport_text(item)
            log_line(
                "ESPN SPORT TELEGRAM:",
                f"{len(message)}/4000 символов"
            )
            send_espn_sport_message(message)
            published += 1
            log_line(
                f"ESPN SPORT: опубликовано {index}/{len(to_publish)} "
                f"[{item.get('category','')}]:",
                item.get("url", "")
            )

        # После каждой успешной проверки запоминаем текущий снимок всех feeds.
        for item in items:
            url = item.get("url")
            if url:
                seen.add(url)

        save_seen_espn_sport(seen)

        log_line("ESPN SPORT: опубликовано всего:", published)
        log_line("ESPN SPORT: проверка завершена")
        ESPN_SPORT_FIRST_RUN = False
        return published

    except Exception as exc:
        log_line("ESPN SPORT — ОШИБКА:", exc)
        return 0



def _load_seen_simple_news(path_value, label):
    path = Path(path_value)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except Exception as exc:
        log_line(f"{label} NEWS: ошибка чтения state:", exc)
        return set()


def _save_seen_simple_news(path_value, seen, label):
    try:
        path = Path(path_value)
        path.write_text(
            json.dumps(sorted(seen)[-5000:], ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as exc:
        log_line(f"{label} NEWS: ошибка записи state:", exc)


def _fetch_voa_category_news(feed_url, label):
    items = _fetch_rss_items(feed_url, "VOA", label)
    log_line(f"{label} / VOA: найдено:", len(items))
    if not items:
        raise RuntimeError(f"VOA {label} RSS не вернул новости")
    return items


def _make_voa_category_text(item, header):
    title_ru = translate_news_to_ru(item.get("title", ""), "en")
    description_ru = translate_news_to_ru(item.get("description", ""), "en")

    safe_title = html.escape(title_ru)
    safe_description = html.escape(description_ru)
    safe_url = html.escape(item.get("url", ""), quote=True)

    parts = [header, "", safe_title]
    if safe_description:
        parts += ["", safe_description]

    parts += [
        "",
        "Source: VOA",
        f'<a href="{safe_url}">Оригинал</a>',
        "",
        "@ne_zaika",
    ]
    return "\n".join(parts)


def _send_voa_category_message(text):
    return _telegram_send_post({
        "chat_id": CHANNEL,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def check_technology_news():
    global TECHNOLOGY_NEWS_FIRST_RUN
    label = "ТЕХНОЛОГИИ"
    log_line(f"{label} NEWS: запуск проверки")

    try:
        items = _fetch_voa_category_news(VOA_TECHNOLOGY_RSS_URL, label)
        seen = _load_seen_simple_news(TECHNOLOGY_NEWS_SEEN_FILE, label)

        if TECHNOLOGY_NEWS_FIRST_RUN:
            to_publish = items[:10]
            log_line(f"{label} NEWS: запуск сервера — публикую последние", len(to_publish))
        else:
            to_publish = [item for item in items if item.get("url") not in seen]

        published = 0
        for index, item in enumerate(reversed(to_publish), start=1):
            message = _make_voa_category_text(item, "💻 ТЕХНОЛОГИИ")
            log_line(f"{label} NEWS TELEGRAM:", f"{len(message)}/4000 символов")
            _send_voa_category_message(message)
            published += 1
            log_line(
                f"{label} NEWS: опубликовано {index}/{len(to_publish)}:",
                item.get("url", "")
            )

        for item in items:
            if item.get("url"):
                seen.add(item["url"])

        _save_seen_simple_news(TECHNOLOGY_NEWS_SEEN_FILE, seen, label)
        TECHNOLOGY_NEWS_FIRST_RUN = False
        log_line(f"{label} NEWS: опубликовано всего:", published)
        log_line(f"{label} NEWS: проверка завершена")
        return published

    except Exception as exc:
        log_line(f"{label} NEWS — ОШИБКА:", exc)
        return 0


def check_economy_news():
    global ECONOMY_NEWS_FIRST_RUN
    label = "ЭКОНОМИКА"
    log_line(f"{label} NEWS: запуск проверки")

    try:
        items = _fetch_voa_category_news(VOA_ECONOMY_RSS_URL, label)
        seen = _load_seen_simple_news(ECONOMY_NEWS_SEEN_FILE, label)

        if ECONOMY_NEWS_FIRST_RUN:
            to_publish = items[:10]
            log_line(f"{label} NEWS: запуск сервера — публикую последние", len(to_publish))
        else:
            to_publish = [item for item in items if item.get("url") not in seen]

        published = 0
        for index, item in enumerate(reversed(to_publish), start=1):
            message = _make_voa_category_text(item, "💰 ЭКОНОМИКА")
            log_line(f"{label} NEWS TELEGRAM:", f"{len(message)}/4000 символов")
            _send_voa_category_message(message)
            published += 1
            log_line(
                f"{label} NEWS: опубликовано {index}/{len(to_publish)}:",
                item.get("url", "")
            )

        for item in items:
            if item.get("url"):
                seen.add(item["url"])

        _save_seen_simple_news(ECONOMY_NEWS_SEEN_FILE, seen, label)
        ECONOMY_NEWS_FIRST_RUN = False
        log_line(f"{label} NEWS: опубликовано всего:", published)
        log_line(f"{label} NEWS: проверка завершена")
        return published

    except Exception as exc:
        log_line(f"{label} NEWS — ОШИБКА:", exc)
        return 0



def check_arts_culture_news():
    global ARTS_CULTURE_NEWS_FIRST_RUN
    label = "КУЛЬТУРА"
    log_line(f"{label} NEWS: запуск проверки")

    try:
        items = _fetch_voa_category_news(
            VOA_ARTS_CULTURE_RSS_URL,
            "ARTS & CULTURE"
        )
        seen = _load_seen_simple_news(
            ARTS_CULTURE_NEWS_SEEN_FILE,
            label
        )

        if ARTS_CULTURE_NEWS_FIRST_RUN:
            to_publish = items[:10]
            log_line(
                f"{label} NEWS: запуск сервера — публикую последние",
                len(to_publish)
            )
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(
            reversed(to_publish),
            start=1
        ):
            message = _make_voa_category_text(
                item,
                "🎭 КУЛЬТУРА"
            )

            log_line(
                f"{label} NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            _send_voa_category_message(message)

            published += 1

            log_line(
                f"{label} NEWS: опубликовано "
                f"{index}/{len(to_publish)}:",
                item.get("url", "")
            )

        # Запоминаем весь текущий RSS, чтобы после стартовых
        # 10 не вывалить старый хвост на следующем :00.
        for item in items:
            if item.get("url"):
                seen.add(item["url"])

        _save_seen_simple_news(
            ARTS_CULTURE_NEWS_SEEN_FILE,
            seen,
            label
        )

        ARTS_CULTURE_NEWS_FIRST_RUN = False

        log_line(
            f"{label} NEWS: опубликовано всего:",
            published
        )
        log_line(
            f"{label} NEWS: проверка завершена"
        )

        return published

    except Exception as exc:
        log_line(
            f"{label} NEWS — ОШИБКА:",
            exc
        )
        return 0


US_ELECTION_KEYWORDS = (
    "election",
    "elections",
    "electoral",
    "vote",
    "votes",
    "voting",
    "voter",
    "voters",
    "ballot",
    "ballots",
    "campaign",
    "campaigns",
    "candidate",
    "candidates",
    "primary",
    "primaries",
    "midterm",
    "midterms",
    "polling",
    "polls",
    "republican",
    "republicans",
    "democrat",
    "democrats",
)


def _is_us_elections_item(item):
    haystack = " ".join([
        str(item.get("title", "")),
        str(item.get("description", "")),
    ]).lower()

    return any(
        keyword in haystack
        for keyword in US_ELECTION_KEYWORDS
    )


def check_us_elections_news():
    global US_ELECTIONS_NEWS_FIRST_RUN

    label = "ВЫБОРЫ В США"
    log_line(f"{label} NEWS: запуск проверки")

    try:
        items = _fetch_voa_category_news(
            VOA_USA_RSS_URL,
            label
        )

        items = [
            item for item in items
            if _is_us_elections_item(item)
        ]

        seen = _load_seen_simple_news(
            US_ELECTIONS_NEWS_SEEN_FILE,
            label
        )

        if US_ELECTIONS_NEWS_FIRST_RUN:
            to_publish = items[:10]
            log_line(
                f"{label} NEWS: запуск сервера — публикую последние",
                len(to_publish)
            )
        else:
            to_publish = [
                item for item in items
                if item.get("url") not in seen
            ]

        published = 0

        for index, item in enumerate(
            reversed(to_publish),
            start=1
        ):
            message = _make_voa_category_text(
                item,
                "🇺🇸 ВЫБОРЫ В США"
            )

            log_line(
                f"{label} NEWS TELEGRAM:",
                f"{len(message)}/4000 символов"
            )

            _send_voa_category_message(message)
            published += 1

            log_line(
                f"{label} NEWS: опубликовано "
                f"{index}/{len(to_publish)}:",
                item.get("url", "")
            )

        for item in items:
            if item.get("url"):
                seen.add(item["url"])

        _save_seen_simple_news(
            US_ELECTIONS_NEWS_SEEN_FILE,
            seen,
            label
        )

        US_ELECTIONS_NEWS_FIRST_RUN = False

        log_line(
            f"{label} NEWS: опубликовано всего:",
            published
        )
        log_line(
            f"{label} NEWS: проверка завершена"
        )

        return published

    except Exception as exc:
        log_line(
            f"{label} NEWS — ОШИБКА:",
            exc
        )
        return 0


# ЕДИНЫЙ ДИСПЕТЧЕР НОВОСТЕЙ
# =========================================================
# При запуске сервера выполняет стартовую проверку всех веток последовательно.
# Далее запускает полный цикл новостей строго в начале каждого часа (:00).
# Это исключает рассинхронизацию отдельных новостных потоков и гонку отправок.
# =========================================================

NEWS_DISPATCH_LOCK = threading.Lock()


def next_full_hour(now=None):
    if now is None:
        now = datetime.now(TZ)
    return (
        now.replace(
            minute=0,
            second=0,
            microsecond=0
        )
        + timedelta(hours=1)
    )


def log_next_news_update(now=None):
    next_run = next_full_hour(now)
    log_line(
        "NEWS DISPATCHER: следующее обновление",
        next_run.strftime("%d.%m.%Y %H:%M:%S")
    )
    return next_run


def news_schedule_key(now):
    """
    Ключ текущего часа.
    При переходе, например, 15:59 -> 16:00 значение меняется,
    и главный диспетчер запускает проверку новостей.
    """
    return (
        now.year,
        now.month,
        now.day,
        now.hour,
    )


def start_news_dispatch(reason="по расписанию"):
    """
    Запускает полный цикл новостей в отдельном потоке.
    Если предыдущий цикл ещё идёт, второй одновременно не запускается.
    """
    if NEWS_DISPATCH_LOCK.locked():
        log_line(
            f"NEWS DISPATCHER: пропуск запуска ({reason}) — предыдущий цикл ещё выполняется"
        )
        return False

    def worker():
        with NEWS_DISPATCH_LOCK:
            try:
                log_line(f"NEWS DISPATCHER: запуск ({reason})")
                run_all_news_checks()
                log_line(f"NEWS DISPATCHER: завершён ({reason})")
            except Exception as exc:
                log_line(f"NEWS DISPATCHER: ОШИБКА ({reason}):", exc)
            finally:
                log_next_news_update()

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name="news-dispatch-worker"
    )
    thread.start()
    return True



# =========================================================
# КОНТЕНТ-ПАКЕТЫ ДЛЯ ОБНОВЛЕНИЙ НОВОСТНЫХ ЛЕНТ
# =========================================================
#
# C:\ne_zaika_bot\
#   nezaika\1.<любое расширение картинки>
#   photos\1.<любое расширение картинки>
#   stories\1.<любое расширение текста>
#
# Имя файла = только номер.
# Расширение специально НЕ фиксируется.
# Один номер связывает все три элемента.
# =========================================================

NEZAIKA_DIR = Path(BASE_DIR) / "nezaika"
AI_PHOTOS_DIR = Path(BASE_DIR) / "photos"
MINISTORY_DIR = Path(BASE_DIR) / "stories"

CONTENT_PACK_STATE_FILE = state_file(
    "content_pack_next_number.txt"
)

CONTENT_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp"
}

CONTENT_TEXT_EXTENSIONS = {
    ".txt", ".md"
}

CONTENT_PACK_LOCK = threading.RLock()


def _numbered_files(folder, allowed_extensions):
    """
    Возвращает:
        {1: Path(...), 2: Path(...), ...}

    Учитываются только файлы, у которых имя ДО точки —
    целое положительное число: 1.png, 2.jpg, 17.webp и т.д.
    """
    result = {}

    if not folder.exists():
        return result

    for path in folder.iterdir():
        if not path.is_file():
            continue

        if path.suffix.lower() not in allowed_extensions:
            continue

        if not path.stem.isdigit():
            continue

        number = int(path.stem)

        if number <= 0:
            continue

        # Если случайно лежат 1.png и 1.jpg —
        # берём первый найденный, но номер остаётся один.
        result.setdefault(number, path)

    return result


def available_content_packs():
    """
    Возвращает только номера ПОЛНЫХ комплектов:
      nezaika/N.*
      photos/N.*
      stories/N.*
    """
    nezaika = _numbered_files(
        NEZAIKA_DIR,
        CONTENT_IMAGE_EXTENSIONS
    )
    photos = _numbered_files(
        AI_PHOTOS_DIR,
        CONTENT_IMAGE_EXTENSIONS
    )
    stories = _numbered_files(
        MINISTORY_DIR,
        CONTENT_TEXT_EXTENSIONS
    )

    numbers = sorted(
        set(nezaika)
        & set(photos)
        & set(stories)
    )

    return numbers, nezaika, photos, stories


def load_next_content_number():
    try:
        value = Path(
            CONTENT_PACK_STATE_FILE
        ).read_text(
            encoding="utf-8"
        ).strip()

        return int(value)

    except Exception:
        return None


def save_next_content_number(number):
    Path(
        CONTENT_PACK_STATE_FILE
    ).write_text(
        str(number),
        encoding="utf-8"
    )


def get_next_content_pack():
    """
    Берёт следующий полный комплект.
    После последнего номера начинает снова с первого.

    Если, например, есть 1, 2, 5 — порядок будет:
    1 -> 2 -> 5 -> 1 ...
    """
    with CONTENT_PACK_LOCK:
        numbers, nezaika, photos, stories = (
            available_content_packs()
        )

        if not numbers:
            return None

        wanted = load_next_content_number()

        if wanted in numbers:
            number = wanted
        else:
            # Если сохранённого номера нет — берём первый
            # существующий номер >= wanted, иначе первый вообще.
            number = None

            if wanted is not None:
                for candidate in numbers:
                    if candidate >= wanted:
                        number = candidate
                        break

            if number is None:
                number = numbers[0]

        position = numbers.index(number)
        next_number = numbers[
            (position + 1) % len(numbers)
        ]

        save_next_content_number(
            next_number
        )

        return {
            "number": number,
            "nezaika": nezaika[number],
            "photo": photos[number],
            "story": stories[number],
        }


def _telegram_send_photo_file(image_path, caption=None):
    """
    Отправляет локальную картинку в канал.
    """
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendPhoto"
    )

    data = {
        "chat_id": CHANNEL,
    }

    if caption:
        data["caption"] = caption[:1024]

    with image_path.open("rb") as image_file:
        response = requests.post(
            url,
            data=data,
            files={
                "photo": (
                    image_path.name,
                    image_file
                )
            },
            timeout=60,
        )

    response.raise_for_status()

    body = response.json()

    if not body.get("ok"):
        raise RuntimeError(body)

    return body["result"]["message_id"]


def publish_content_pack(section_name):
    """
    Публикует три элемента ОДНОГО номера:

      1. НеЗайка
      2. ИИ-фото
      3. МиниСТОРИ

    Вызывается только если соответствующая новостная
    лента действительно что-то опубликовала.
    """
    pack = get_next_content_pack()

    if pack is None:
        log_line(
            f"КОНТЕНТ / {section_name}: "
            "полных комплектов не найдено"
        )
        return False

    number = pack["number"]

    try:
        story_bytes = pack["story"].read_bytes()
        story_text = None
        for story_encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                story_text = story_bytes.decode(story_encoding).strip()
                break
            except UnicodeDecodeError:
                continue

        if story_text is None:
            story_text = story_bytes.decode("cp1251", errors="replace").strip()

        log_line(
            f"КОНТЕНТ / {section_name}: "
            f"комплект №{number} — публикация"
        )

        # 1. НеЗайка
        _telegram_send_photo_file(
            pack["nezaika"]
        )

        time.sleep(1.1)

        # 2. ИИ-фото
        _telegram_send_photo_file(
            pack["photo"]
        )

        time.sleep(1.1)

        # 3. МиниСТОРИ
        if story_text:
            send_message(
                story_text
            )

        log_line(
            f"КОНТЕНТ / {section_name}: "
            f"комплект №{number} опубликован"
        )

        return True

    except Exception as exc:
        log_line(
            f"КОНТЕНТ / {section_name}: "
            f"ОШИБКА комплекта №{number}:",
            exc
        )
        return False


def run_all_news_checks():
    sections = [
        ("ХАЙФА", check_haifa_news),
        ("ИЗРАИЛЬ", check_israel_news),
        ("WORLD", check_world_news),
        ("ТЕХНОЛОГИИ", check_technology_news),
        ("ЭКОНОМИКА", check_economy_news),
        ("КУЛЬТУРА", check_arts_culture_news),
        ("БЛИЖНИЙ ВОСТОК", check_middle_east_news),
        ("ЕВРОПА", check_europe_news),
        ("УКРАИНА", check_ukraine_news),
        ("США", check_usa_news),
        ("ВЫБОРЫ В США", check_us_elections_news),
        ("ИРАН", check_iran_news),
        ("КИТАЙ", check_china_news),
        ("НАУКА И ЗДОРОВЬЕ", check_science_news),
        ("КЛИМАТ", check_climate_news),
        ("СПОРТ ИЗРАИЛЯ", check_sport_news),
        ("ESPN SPORT", check_espn_sport),
    ]

    for section_name, checker in sections:
        try:
            log_line(
                f"NEWS DISPATCHER: {section_name} — запуск проверки"
            )

            # Контент-пакет выходит ВСЕГДА и ДО проверки/выдачи новостей.
            # Наличие новых публикаций на это больше не влияет.
            publish_content_pack(
                section_name
            )

            published = checker()

            log_line(
                f"NEWS DISPATCHER: {section_name} — проверка завершена"
            )

        except Exception as exc:
            log_line(
                f"NEWS DISPATCHER: {section_name} — ОШИБКА:",
                exc
            )


def all_news_scheduler_loop():
    """
    Оставлено для совместимости.
    Реальное часовое расписание новостей теперь контролирует main().
    """
    start_news_dispatch("старт сервера")
    log_next_news_update(now)
    while True:
        time.sleep(3600)


def flights_scheduler_loop():
    """
    Рейсы НЕ участвуют в минутном polling.

    После запуска отдельный поток:
      1) вычисляет ближайшие :00 / :30;
      2) спит до этого момента;
      3) обновляет 5 постов;
      4) снова вычисляет следующее обновление.
    """
    while True:
        now = datetime.now(TZ)
        target = next_flights_run(now)

        seconds = max(
            0,
            (target - now).total_seconds()
        )

        log_line(
            "БЕН-ГУРИОН: следующее обновление",
            target.strftime("%d.%m.%Y %H:%M:%S")
        )

        time.sleep(seconds)

        try:
            log_line("БЕН-ГУРИОН: запуск обновления")
            update_flight_board()
            log_line("БЕН-ГУРИОН: обновление завершено")
        except Exception as error:
            print(
                "БЕН-ГУРИОН — ОШИБКА:",
                error
            )

        # Не даём циклу повторно схватить ту же границу.
        time.sleep(1)


def rates_schedule_key(now):
    if now.hour in (
        8, 12, 16, 20
    ):
        return now.strftime(
            "%Y-%m-%d %H"
        )

    return None


def time_schedule_key(now):
    return now.strftime(
        "%Y-%m-%d %H"
    )


def main():
    telegram_offset = None

    log_line(
        "ДИСПЕТЧЕР ЗАПУСКАЕТСЯ..."
    )

    # При запуске обновляем постоянные сервисные посты; новых простыней не создаём.
    try:
        update_weather()
    except Exception as error:
        print(
            "ПОГОДА — ОШИБКА ПРИ ЗАПУСКЕ:",
            error
        )

    try:
        update_flight_board()
    except Exception as error:
        print(
            "БЕН-ГУРИОН — ОШИБКА ПРИ ЗАПУСКЕ:",
            error
        )

    flights_thread = threading.Thread(
        target=flights_scheduler_loop,
        daemon=True,
        name="ben-gurion-scheduler"
    )
    flights_thread.start()


    # Новости запускаются главным диспетчером.
    # Стартовая проверка выполняется сразу.
    start_news_dispatch("старт сервера")

    try:
        update_rates()
    except Exception as error:
        print(
            "ВАЛЮТЫ — ОШИБКА ПРИ ЗАПУСКЕ:",
            error
        )

    try:
        update_time_post()
    except Exception as error:
        print(
            "МИРОВОЕ ВРЕМЯ — ОШИБКА ПРИ ЗАПУСКЕ:",
            error
        )

    now = datetime.now(TZ)

    last_weather_key = (
        weather_schedule_key(now)
    )

    last_rates_key = (
        rates_schedule_key(now)
    )

    last_time_key = (
        time_schedule_key(now)
    )

    last_news_key = (
        news_schedule_key(now)
    )

    log_line(
        "ДИСПЕТЧЕР ЗАПУЩЕН."
    )
    print(
        "Погода: редактируется постоянный пост каждый час."
    )
    print(
        "Бен-Гурион: редактируются 5 постоянных постов в :00 и :30."
    )
    print(
        "Новости Хайфы: проверка раз в час."
    )
    print(
        "Новости: главный диспетчер; старт сразу, затем проверка при каждом переходе на новый час (:00)."
    )
    print(
        "Валюты: редактируется постоянный пост в 08:00, 12:00, 16:00, 20:00."
    )
    print(
        "Мировое время: редактируется постоянный пост каждый час."
    )

    while True:

        now = datetime.now(TZ)

        # ---------------------------------------------
        # ПОГОДА — ОБНОВЛЕНИЕ ПОСТОЯННОГО ПОСТА КАЖДЫЙ ЧАС
        # ---------------------------------------------
        current_weather_key = (
            weather_schedule_key(now)
        )

        if (
            current_weather_key
            != last_weather_key
        ):
            try:
                update_weather()
                last_weather_key = (
                    current_weather_key
                )
            except Exception as error:
                print(
                    "ПОГОДА — ОШИБКА:",
                    error
                )

        # ---------------------------------------------
        # ВАЛЮТЫ — ОБНОВЛЕНИЕ ПОСТОЯННОГО ПОСТА 08 / 12 / 16 / 20
        # ---------------------------------------------
        current_rates_key = (
            rates_schedule_key(now)
        )

        if (
            current_rates_key is not None
            and current_rates_key
            != last_rates_key
        ):
            try:
                update_rates()
                last_rates_key = (
                    current_rates_key
                )
            except Exception as error:
                print(
                    "ВАЛЮТЫ — ОШИБКА:",
                    error
                )

        # ---------------------------------------------
        # МИРОВОЕ ВРЕМЯ — ОБНОВЛЕНИЕ ПОСТОЯННОГО ПОСТА КАЖДЫЙ ЧАС
        # ---------------------------------------------
        current_time_key = (
            time_schedule_key(now)
        )

        if (
            current_time_key
            != last_time_key
        ):
            try:
                update_time_post()
                last_time_key = (
                    current_time_key
                )
            except Exception as error:
                print(
                    "МИРОВОЕ ВРЕМЯ — ОШИБКА:",
                    error
                )

        # ---------------------------------------------
        # НОВОСТИ — ПРОВЕРКА КАЖДЫЙ НОВЫЙ ЧАС
        # Главный диспетчер, без отдельного часового sleep-потока.
        # ---------------------------------------------
        current_news_key = (
            news_schedule_key(now)
        )

        if (
            current_news_key
            != last_news_key
        ):
            log_line(
                "NEWS DISPATCHER: ЧАСОВОЙ ТРИГГЕР",
                now.strftime("%d.%m.%Y %H:%M:%S")
            )

            if start_news_dispatch(
                "автообновление :00"
            ):
                last_news_key = (
                    current_news_key
                )
                log_next_news_update(now)

        # ---------------------------------------------
        # TELEGRAM-КОМАНДЫ
        # ---------------------------------------------
        try:
            telegram_offset = (
                check_telegram_commands(
                    telegram_offset
                )
            )
        except Exception as error:
            print(
                "TELEGRAM — ОШИБКА:",
                error
            )

        time.sleep(
            SCHEDULER_INTERVAL
        )


# =========================================================
# 5. ЗАПУСК И ПРИНУДИТЕЛЬНЫЕ КОМАНДЫ ИЗ CMD
# =========================================================

def print_restart_command(reason="СЕРВЕР ОСТАНОВЛЕН"):
    print()
    print("=" * 60)
    print(reason)
    print()
    print("ПОВТОРНЫЙ ЗАПУСК:")
    print("python ne_zaika_bot.py")
    print("=" * 60)


if __name__ == "__main__":

    try:
        if (
            len(sys.argv) > 1
            and sys.argv[1].lower() == "now"
        ):
            update_weather()

        elif (
            len(sys.argv) > 1
            and sys.argv[1].lower() == "flights"
        ):
            update_flight_board()

        elif (
            len(sys.argv) > 1
            and sys.argv[1].lower() == "rates"
        ):
            update_rates()

        elif (
            len(sys.argv) > 1
            and sys.argv[1].lower() == "news"
        ):
            check_haifa_news()

        else:
            main()

    except KeyboardInterrupt:
        print_restart_command("СЕРВЕР ОСТАНОВЛЕН: Ctrl+C")

    except Exception as error:
        print()
        print("КРИТИЧЕСКАЯ ОШИБКА:", repr(error))
        print_restart_command("СЕРВЕР ОСТАНОВЛЕН ИЗ-ЗА ОШИБКИ")

    else:
        print_restart_command("СЕРВЕР ЗАВЕРШИЛ РАБОТУ")
