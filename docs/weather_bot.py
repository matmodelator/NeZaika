# =========================================================
# GPT 1.0.0
# =========================================================

import os
import time
import math
import requests

from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from pyluach.dates import HebrewDate


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = "8843774698:AAFLs9BUlDFJBCDhCuET66p9bzt9qdZKdgM"
CHANNEL = "@ne_zaika"


# Хайфа
LAT = 32.7940
LON = 34.9896

TZ = ZoneInfo("Asia/Jerusalem")

# Раз в час
UPDATE_INTERVAL = 3600

# Здесь запоминаем Telegram message_id
MESSAGE_ID_FILE = "weather_message_id.txt"


# =========================================================
# HTTP
# =========================================================

def get_json(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


# =========================================================
# НАПРАВЛЕНИЕ ВЕТРА
# =========================================================

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


# =========================================================
# ЕВРЕЙСКАЯ ДАТА
# =========================================================

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


# =========================================================
# ГРИГОРИАНСКАЯ ДАТА
# =========================================================

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


# =========================================================
# ВРЕМЯ ДО СОБЫТИЯ
# =========================================================

def format_delta(delta):
    seconds = int(delta.total_seconds())

    if seconds < 0:
        seconds = 0

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours} ч {minutes} мин"

    return f"{minutes} мин"


# =========================================================
# ФАЗА ЛУНЫ
# =========================================================

def moon_phase(now):
    # известная эпоха новолуния
    epoch = datetime(
        2000, 1, 6, 18, 14,
        tzinfo=timezone.utc
    )

    synodic_month = 29.53058867

    now_utc = now.astimezone(timezone.utc)

    days = (
        now_utc - epoch
    ).total_seconds() / 86400

    age = days % synodic_month
    fraction = age / synodic_month

    if fraction < 0.03 or fraction >= 0.97:
        return "🌑 новолуние"

    elif fraction < 0.22:
        return "🌒 растущая Луна"

    elif fraction < 0.28:
        return "🌓 первая четверть"

    elif fraction < 0.47:
        return "🌔 растущая Луна"

    elif fraction < 0.53:
        return "🌕 полнолуние"

    elif fraction < 0.72:
        return "🌖 убывающая Луна"

    elif fraction < 0.78:
        return "🌗 последняя четверть"

    else:
        return "🌘 убывающая Луна"


# =========================================================
# МАГНИТНАЯ ОБСТАНОВКА
# =========================================================

def kp_description(kp):
    if kp < 2:
        return "спокойно"

    if kp < 4:
        return "небольшие возмущения"

    if kp < 5:
        return "возмущённое поле"

    if kp < 6:
        return "магнитная буря G1"

    if kp < 7:
        return "магнитная буря G2"

    if kp < 8:
        return "магнитная буря G3"

    if kp < 9:
        return "магнитная буря G4"

    return "магнитная буря G5"


def get_kp():
    url = (
        "https://services.swpc.noaa.gov/"
        "products/noaa-planetary-k-index.json"
    )

    try:
        data = get_json(url)

        # первая строка — заголовки
        rows = data[1:]

        last = rows[-1]

        kp = float(last[1])

        return kp

    except Exception:
        return None


# =========================================================
# СОЛНЕЧНАЯ АКТИВНОСТЬ
# =========================================================

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


# =========================================================
# ПОГОДА
# =========================================================

def get_weather():

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}"
        f"&longitude={LON}"

        "&current="
        "temperature_2m,"
        "apparent_temperature,"
        "relative_humidity_2m,"
        "wind_speed_10m,"
        "wind_direction_10m,"
        "surface_pressure,"
        "precipitation_probability"

        "&daily="
        "sunrise,"
        "sunset"

        "&timezone=Asia%2FJerusalem"
        "&forecast_days=2"
    )

    return get_json(url)


# =========================================================
# МОРЕ
# =========================================================

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


# =========================================================
# ВОЗДУХ
# =========================================================

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


# =========================================================
# ТЕКСТ
# =========================================================

def make_text():

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

        solar_text = (
            "☀️ Солнечная активность: "
            f"F10.7 = {solar['flux']:.0f}"
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

    pressure = round(
        current["surface_pressure"]
    )

    wind_speed = current[
        "wind_speed_10m"
    ]

    precipitation = current[
        "precipitation_probability"
    ]

    uv = air["uv_index"]

    dust = air["dust"]

    pm10 = air["pm10"]

    pm25 = air["pm2_5"]

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

        f"💧 Влажность: {humidity}%\n"
        f"🔵 Давление: {pressure} гПа\n"

        f"💨 Ветер: "
        f"{wind_speed:.1f} км/ч, "
        f"{wind_dir}\n"

        f"🌧 Вероятность осадков: "
        f"{precipitation}%\n\n"

        f"☀️ УФ-индекс: {uv:.1f}\n"

        f"🌫 Показатель пыльной були (хамсин или шарав): "
        f"{dust:.1f} мкг/м³\n"

        f"😷 Крупные взвешенные частицы (песок, сажа): "
        f"{pm10:.1f} мкг/м³\n"

        f"🫁 Мелкая взвешенные частицы (дым, выхлопы): "
        f"{pm25:.1f} мкг/м³\n\n"

        f"🌅 Восход: "
        f"{sunrise_today.strftime('%H:%M')}\n"

        f"🌇 Закат: "
        f"{sunset_today.strftime('%H:%M')}\n"

        f"{next_sun_text}\n\n"

        f"🌙 Луна: {moon}\n"
        f"{magnetic_text}\n"
        f"{solar_text}\n\n"

        f"🕒 Обновлено: "
        f"{now.strftime('%H:%M')}\n\n"

        "@ne_zaika"
    )

    return text


# =========================================================
# TELEGRAM
# =========================================================

def send_message(text):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHANNEL,
            "text": text,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    return result["result"]["message_id"]


def edit_message(message_id, text):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/editMessageText"
    )

    response = requests.post(
        url,
        data={
            "chat_id": CHANNEL,
            "message_id": message_id,
            "text": text,
        },
        timeout=30,
    )

    result = response.json()

    # Telegram возвращает ошибку, если текст
    # полностью совпадает с предыдущим.
    if not result.get("ok"):

        description = result.get(
            "description",
            ""
        )

        if (
            "message is not modified"
            not in description.lower()
        ):
            raise RuntimeError(result)


# =========================================================
# MESSAGE ID
# =========================================================

def load_message_id():

    if not os.path.exists(
        MESSAGE_ID_FILE
    ):
        return None

    try:

        with open(
            MESSAGE_ID_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return int(
                file.read().strip()
            )

    except Exception:
        return None


def save_message_id(message_id):

    with open(
        MESSAGE_ID_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            str(message_id)
        )


# =========================================================
# ОСНОВНОЙ ЦИКЛ
# =========================================================

def main():

    message_id = load_message_id()

    while True:

        try:

            text = make_text()

            if message_id is None:

                message_id = send_message(
                    text
                )

                save_message_id(
                    message_id
                )

                print(
                    "Создан погодный пост:",
                    message_id
                )

            else:

                edit_message(
                    message_id,
                    text
                )

                print(
                    "Погодный пост обновлён:",
                    datetime.now(TZ).strftime(
                        "%H:%M"
                    )
                )

        except Exception as error:

            print(
                "ОШИБКА:",
                error
            )

        time.sleep(
            UPDATE_INTERVAL
        )


if __name__ == "__main__":
    main()
