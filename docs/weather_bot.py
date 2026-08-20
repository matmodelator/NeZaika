# =========================================================
# Avionio
# | 2.5.0
# =========================================================





import os
import sys
import time
import math
import re
import requests

from html.parser import HTMLParser

from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from pyluach.dates import HebrewDate


# =========================================================
# 0. ОБЩИЕ НАСТРОЙКИ
# =========================================================

BOT_TOKEN = "8843774698:AAGoaYTS4zask-N9HtesZ2v9pbx_1MCrbLY"
CHANNEL = "@ne_zaika"

TZ = ZoneInfo("Asia/Jerusalem")

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

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    edited = result.get("result", {})

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
#   CMD:      python weather_bot.py now
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
    # НИКАКОГО редактирования:
    # каждое обновление погоды — новый пост.
    now = datetime.now(TZ)
    text = make_weather_text()

    message_id = send_message(
        text
    )

    print(
        "ПОГОДА: создан новый пост:",
        message_id,
        now.strftime("%H:%M:%S")
    )

    return message_id


# =========================================================
# 2. БЕН-ГУРИОН — ТАБЛО РЕЙСОВ
# =========================================================
# Независимый сервис.
#
# Единственный источник данных:
#   Avionio — русское онлайн-табло TLV
#
# Никаких данных IAA / data.gov.il / Flightradar24.
# Никаких придуманных терминалов, гейтов или статусов.
# Статус выводится ровно в формулировке Avionio.
#
# Публикация:
#   1) прилёты — фактические
#   2) прилёты — ближайшие
#   3) вылеты — фактические
#   4) вылеты — ближайшие
#   5) изменения
#
# Принудительное обновление:
#   /arrivals
#   /departures
#   /flights
#   CMD: python weather_bot.py flights
# =========================================================

AVIONIO_ARRIVALS_URL = (
    "https://www.avionio.com/ru/airport/TLV/arrivals"
)

AVIONIO_DEPARTURES_URL = (
    "https://www.avionio.com/ru/airport/TLV/departures"
)

FLIGHTS_UPDATE_INTERVAL = 600

ARRIVALS_ACTUAL_MESSAGE_ID_FILE = state_file("arrivals_actual_message_id.txt")
ARRIVALS_NEXT_MESSAGE_ID_FILE = state_file("arrivals_next_message_id.txt")
DEPARTURES_ACTUAL_MESSAGE_ID_FILE = state_file("departures_actual_message_id.txt")
DEPARTURES_NEXT_MESSAGE_ID_FILE = state_file("departures_next_message_id.txt")
FLIGHT_ALERTS_MESSAGE_ID_FILE = state_file("flight_alerts_message_id.txt")


# ---------------------------------------------------------
# 2.1. HTML AVIONIO
# ---------------------------------------------------------

class AvionioTableParser(HTMLParser):
    """Минимальный парсер строк таблицы Avionio без внешних библиотек."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None
        self._cell_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "tr":
            self._row = []

        elif tag in ("td", "th") and self._row is not None:
            self._cell = tag
            self._cell_parts = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("td", "th") and self._cell is not None:
            value = " ".join(
                " ".join(self._cell_parts).split()
            ).strip()
            self._row.append(value)
            self._cell = None
            self._cell_parts = []

        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


AVIONIO_MONTHS_RU = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "сент": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}


def parse_avionio_date(date_text, hhmm):
    """Дата и время берутся только из колонок Avionio."""
    text = str(date_text or "").lower().replace(".", " ")
    parts = text.split()

    if len(parts) < 2:
        return None

    try:
        day = int(parts[0])
        month = AVIONIO_MONTHS_RU.get(parts[1])

        if month is None:
            return None

        hour, minute = [int(x) for x in hhmm.split(":", 1)]
        now = datetime.now(TZ)

        # Если Avionio отдал год — используем его буквально.
        if len(parts) >= 3 and parts[2].isdigit():
            year = int(parts[2])
            return datetime(
                year, month, day, hour, minute,
                tzinfo=TZ
            )

        # В таблице год обычно не печатается. Выбираем календарную
        # дату, ближайшую к сегодняшней дате Иерусалима.
        candidates = []

        for year in (now.year - 1, now.year, now.year + 1):
            try:
                candidates.append(
                    datetime(
                        year, month, day, hour, minute,
                        tzinfo=TZ
                    )
                )
            except ValueError:
                pass

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda dt: abs((dt - now).total_seconds())
        )

    except Exception:
        return None


def status_clock_time(flight):
    """HH:MM из статуса Avionio, если Avionio его указал."""
    status = str(flight.get("status") or "").strip()
    match = re.search(r"(\d{1,2}:\d{2})\s*$", status)

    if not match:
        return None

    scheduled = flight.get("scheduled_time")

    if scheduled is None:
        return None

    try:
        hour, minute = [
            int(x) for x in match.group(1).split(":")
        ]
    except Exception:
        return None

    # Время в статусе может перескочить через полночь.
    # Берём ближайшее к плановому времени из трёх соседних дат.
    variants = []

    for day_shift in (-1, 0, 1):
        d = scheduled.date() + timedelta(days=day_shift)
        variants.append(
            datetime(
                d.year, d.month, d.day,
                hour, minute,
                tzinfo=TZ
            )
        )

    return min(
        variants,
        key=lambda dt: abs((dt - scheduled).total_seconds())
    )


def get_avionio_board(direction):
    if direction == "A":
        url = AVIONIO_ARRIVALS_URL
    elif direction == "D":
        url = AVIONIO_DEPARTURES_URL
    else:
        raise ValueError("Неизвестное направление рейса")

    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/142 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
        timeout=30,
    )

    response.raise_for_status()

    parser = AvionioTableParser()
    parser.feed(response.text)

    flights = []

    for row in parser.rows:
        if len(row) < 7:
            continue

        hhmm = row[0].strip()
        date_text = row[1].strip()

        if not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            continue

        scheduled = parse_avionio_date(
            date_text,
            hhmm
        )

        if scheduled is None:
            continue

        flight = {
            "direction": direction,
            "scheduled_time": scheduled,
            "date_text": date_text,
            "iata": row[2].strip(),
            "city": row[3].strip(),
            "number": row[4].strip(),
            "airline": row[5].strip(),
            "status": row[6].strip(),
        }

        flight["status_time"] = status_clock_time(flight)
        flights.append(flight)

    if not flights:
        raise RuntimeError(
            "Avionio: таблица TLV получена, но строки рейсов не распознаны"
        )

    return flights


def get_flights():
    """Единственный источник рейсов — Avionio."""
    arrivals = get_avionio_board("A")
    departures = get_avionio_board("D")
    return arrivals + departures


# ---------------------------------------------------------
# 2.2. ПОЛЯ / СТАТУС / СВЕТОФОР
# ---------------------------------------------------------

def flight_number(flight):
    return str(flight.get("number") or "—").strip()


def flight_airline(flight):
    return str(flight.get("airline") or "—").strip()


def flight_city(flight):
    return str(flight.get("city") or "—").strip()


def flight_status_raw(flight):
    return str(flight.get("status") or "").strip()


def flight_direction_icon(flight):
    return "🛬" if flight.get("direction") == "A" else "✈️"


def flight_status_light(flight):
    """
    Светофор оставляем.
    Цвет определяется только по буквальному статусу Avionio.
    """
    status = flight_status_raw(flight).lower()

    if status.startswith("прибыл"):
        return "🔵"

    if status.startswith("вылетел"):
        return "⚪"

    if "отмен" in status:
        return "🔴"

    if status.startswith("по расписанию"):
        return "🟢"

    # Ожидается / изменённое расчётное состояние Avionio.
    return "🟡"


def flight_is_completed(flight, direction):
    if flight.get("direction") != direction:
        return False

    status = flight_status_raw(flight).lower()

    if direction == "A":
        return status.startswith("прибыл")

    if direction == "D":
        return status.startswith("вылетел")

    return False


def flight_event_time(flight):
    """Фактическое/ожидаемое время Avionio, иначе плановое."""
    return (
        flight.get("status_time")
        or flight.get("scheduled_time")
    )


# ---------------------------------------------------------
# 2.3. ОТБОР И СКЛЕЙКА CODE-SHARE
# ---------------------------------------------------------

def flight_physical_key(flight):
    """
    Склеиваем строки Avionio только когда совпадают:
    направление, IATA, плановое время и буквальный статус.
    """
    return (
        str(flight.get("direction") or ""),
        str(flight.get("iata") or ""),
        str(flight.get("scheduled_time") or ""),
        str(flight.get("status") or ""),
    )


def merge_duplicate_flights(items):
    groups = {}

    for t, flight in items:
        key = flight_physical_key(flight)

        if key not in groups:
            groups[key] = {
                "time": t,
                "flight": dict(flight),
                "numbers": [],
                "airlines": [],
            }

        number = flight_number(flight)
        airline = flight_airline(flight)

        if number and number != "—" and number not in groups[key]["numbers"]:
            groups[key]["numbers"].append(number)

        if airline and airline != "—" and airline not in groups[key]["airlines"]:
            groups[key]["airlines"].append(airline)

    result = []

    for group in groups.values():
        merged = group["flight"]
        merged["_numbers"] = group["numbers"]
        merged["_airlines"] = group["airlines"]
        result.append((group["time"], merged))

    return result


def actual_flights(flights, direction):
    """Последние завершённые рейсы по буквальному статусу Avionio."""
    result = []

    for flight in flights:
        if not flight_is_completed(flight, direction):
            continue

        t = flight_event_time(flight)

        if t is not None:
            result.append((t, flight))

    result = merge_duplicate_flights(result)
    result.sort(key=lambda item: item[0], reverse=True)
    return result


def upcoming_flights(flights, direction, hours_forward=3):
    """Незавершённые рейсы от сейчас до +3 часов по времени Avionio."""
    now = datetime.now(TZ)
    end = now + timedelta(hours=hours_forward)
    result = []

    for flight in flights:
        if flight.get("direction") != direction:
            continue

        if flight_is_completed(flight, direction):
            continue

        if "отмен" in flight_status_raw(flight).lower():
            continue

        t = flight_event_time(flight)

        if t is None:
            continue

        if now <= t <= end:
            result.append((t, flight))

    result = merge_duplicate_flights(result)
    result.sort(key=lambda item: item[0])
    return result


# ---------------------------------------------------------
# 2.4. ФОРМАТИРОВАНИЕ РЕЙСА
# ---------------------------------------------------------

def make_flight_line(t, flight):
    numbers = flight.get("_numbers") or [flight_number(flight)]
    airlines = flight.get("_airlines") or [flight_airline(flight)]

    numbers = [x for x in numbers if x and x != "—"]
    airlines = [x for x in airlines if x and x != "—"]

    number = " / ".join(numbers) if numbers else "—"
    airline = " / ".join(airlines) if airlines else "—"

    city = flight_city(flight)
    iata = str(flight.get("iata") or "").strip()
    status = flight_status_raw(flight)
    light = flight_status_light(flight)
    plane = flight_direction_icon(flight)
    scheduled = flight.get("scheduled_time")

    # Первая строка — плановое время из колонки Time Avionio.
    if scheduled is not None:
        clock = scheduled.strftime("%H:%M")
    else:
        clock = t.strftime("%H:%M")

    line = (
        f"{plane} {clock}  {number}\n"
        f"{airline}\n"
        f"{city}"
    )

    if iata:
        line += f" ({iata})"

    if status:
        line += f"\n   {light} {status}"

    return line


# ---------------------------------------------------------
# 2.5. ТЕКСТ: ПРИЛЁТЫ / ВЫЛЕТЫ / ИЗМЕНЕНИЯ
# ---------------------------------------------------------

def make_flights_text(flights, direction, board_type):
    if direction == "A":
        icon = "🛬"
        direction_title = "ПРИЛЁТЫ"
    else:
        icon = "✈️"
        direction_title = "ВЫЛЕТЫ"

    if board_type == "actual":
        title = (
            f"{icon} БЕН-ГУРИОН — "
            f"{direction_title} — ФАКТИЧЕСКИЕ"
        )
        selected = actual_flights(flights, direction)[:15]
        empty_text = "Avionio не показывает завершённых рейсов в текущей таблице."

    else:
        title = (
            f"{icon} БЕН-ГУРИОН — "
            f"{direction_title} — БЛИЖАЙШИЕ"
        )
        selected = upcoming_flights(flights, direction)[:25]
        empty_text = "В Avionio нет ближайших рейсов в выбранном интервале."

    lines = [
        title,
        "Источник: Avionio",
        "",
    ]

    if not selected:
        lines.append(empty_text)
    else:
        for t, flight in selected:
            lines.append(make_flight_line(t, flight))

    now = datetime.now(TZ)

    lines.extend([
        "",
        f"🕒 Обновлено: {now.strftime('%H:%M:%S')}",
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


def flight_has_avionio_change(flight):
    """
    В изменения попадают только состояния, прямо видимые у Avionio:
    отмена или 'Ожидается HH:MM', отличающееся от планового HH:MM.
    Никакой собственной величины задержки не рассчитываем и не печатаем.
    """
    status = flight_status_raw(flight).lower()

    if "отмен" in status:
        return True

    if not status.startswith("ожидается"):
        return False

    status_time = flight.get("status_time")
    scheduled = flight.get("scheduled_time")

    if status_time is None or scheduled is None:
        return False

    return status_time != scheduled


def make_flight_alerts_text(flights):
    now = datetime.now(TZ)
    start = now - timedelta(hours=1)
    end = now + timedelta(hours=8)
    alerts = []

    for flight in flights:
        t = flight_event_time(flight)

        if t is None:
            continue

        if not (start <= t <= end):
            continue

        if flight_has_avionio_change(flight):
            alerts.append((t, flight))

    alerts = merge_duplicate_flights(alerts)
    alerts.sort(key=lambda item: item[0])

    lines = [
        "⚠️ БЕН-ГУРИОН — ИЗМЕНЕНИЯ",
        "Источник: Avionio",
        "",
    ]

    if not alerts:
        lines.append(
            "Avionio не показывает существенных изменений в выбранном интервале."
        )
    else:
        for t, flight in alerts[:25]:
            lines.append(make_flight_line(t, flight))

    lines.extend([
        "",
        f"🕒 Обновлено: {now.strftime('%H:%M:%S')}",
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------
# 2.6. СОЗДАНИЕ / ОБНОВЛЕНИЕ ПЯТИ ПОСТОВ
# ---------------------------------------------------------

def create_flight_board_posts():
    """При запуске создаём пять новых постов."""
    print("БЕН-ГУРИОН / AVIONIO: создаю 5 новых постов...")

    flights = get_flights()

    posts = [
        (
            make_flights_text(flights, "A", "actual"),
            ARRIVALS_ACTUAL_MESSAGE_ID_FILE
        ),
        (
            make_flights_text(flights, "A", "next"),
            ARRIVALS_NEXT_MESSAGE_ID_FILE
        ),
        (
            make_flights_text(flights, "D", "actual"),
            DEPARTURES_ACTUAL_MESSAGE_ID_FILE
        ),
        (
            make_flights_text(flights, "D", "next"),
            DEPARTURES_NEXT_MESSAGE_ID_FILE
        ),
        (
            make_flight_alerts_text(flights),
            FLIGHT_ALERTS_MESSAGE_ID_FILE
        ),
    ]

    for post_text, id_file in posts:
        message_id = send_message(post_text)
        save_id_file(id_file, message_id)

    print(
        "БЕН-ГУРИОН / AVIONIO: 5 новых постов созданы:",
        datetime.now(TZ).strftime("%H:%M:%S")
    )


def update_flight_board():
    # НИКАКОГО редактирования:
    # каждые 30 минут создаются 5 новых постов.
    print("БЕН-ГУРИОН / AVIONIO: создаю 5 новых постов...")

    flights = get_flights()

    posts = [
        make_flights_text(flights, "A", "actual"),
        make_flights_text(flights, "A", "next"),
        make_flights_text(flights, "D", "actual"),
        make_flights_text(flights, "D", "next"),
        make_flight_alerts_text(flights),
    ]

    message_ids = []

    for post_text in posts:
        message_ids.append(send_message(post_text))

    print(
        "БЕН-ГУРИОН / AVIONIO: созданы 5 новых постов:",
        datetime.now(TZ).strftime("%H:%M:%S")
    )

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
    """
    При запуске сервера создаём новый валютный пост.
    """
    message_id = send_message(
        make_rates_text()
    )

    save_id_file(
        RATES_MESSAGE_ID_FILE,
        message_id
    )

    print(
        "ВАЛЮТЫ: создан новый пост:",
        datetime.now(TZ).strftime("%H:%M:%S"),
        "message_id:",
        message_id
    )

    return message_id


def update_rates():
    # НИКАКОГО редактирования:
    # каждое обновление валют — новый пост.
    message_id = send_message(
        make_rates_text()
    )

    print(
        "ВАЛЮТЫ: создан новый пост:",
        datetime.now(TZ).strftime("%H:%M:%S"),
        "message_id:",
        message_id
    )

    return message_id


# =========================================================
# МИРОВОЕ ВРЕМЯ
# | 2.4.0
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
    return send_message(
        make_time_text()
    )


def update_time_post():
    # НИКАКОГО редактирования:
    # каждое обновление — новый пост.
    return create_time_post()


# =========================================================
# 3. TELEGRAM-КОМАНДЫ
# =========================================================
# Здесь только маршрутизация команд.
# Логика погоды и аэропорта остаётся внутри своих блоков.
# =========================================================

def send_private_reply(chat_id, text):
    response = requests.post(
        (
            f"https://api.telegram.org/"
            f"bot{BOT_TOKEN}/sendMessage"
        ),
        data={
            "chat_id": chat_id,
            "text": text,
        },
        timeout=10
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
                flights = get_flights()

                send_message(
                    make_flights_text(
                        flights,
                        "A",
                        "actual"
                    )
                )

                send_message(
                    make_flights_text(
                        flights,
                        "A",
                        "next"
                    )
                )

                send_private_reply(
                    chat_id,
                    "Созданы 2 новых поста прилётов."
                )

            elif command == "/departures":
                flights = get_flights()

                send_message(
                    make_flights_text(
                        flights,
                        "D",
                        "actual"
                    )
                )

                send_message(
                    make_flights_text(
                        flights,
                        "D",
                        "next"
                    )
                )

                send_private_reply(
                    chat_id,
                    "Созданы 2 новых поста вылетов."
                )

            elif command == "/time":
                update_time_post()

                send_private_reply(
                    chat_id,
                    "Создан новый пост мирового времени."
                )

            elif command == "/flights":
                update_flight_board()
                send_private_reply(
                    chat_id,
                    "Табло Бен-Гуриона обновлено."
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
#   обновляется независимо каждые 10 минут.
#
# Ошибка одного сервиса НЕ останавливает второй.
# =========================================================

def weather_schedule_key(now):
    return now.strftime(
        "%Y-%m-%d %H"
    )


def flights_schedule_key(now):
    # Бен-Гурион: ровно :00 и :30
    half_hour = (
        0 if now.minute < 30 else 30
    )

    return (
        now.strftime("%Y-%m-%d %H:")
        + f"{half_hour:02d}"
    )


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
    print("=" * 57)
    print("Версия")
    print("| 2.4.0")
    print("=" * 57)

    telegram_offset = None

    print(
        "ДИСПЕТЧЕР ЗАПУСКАЕТСЯ..."
    )

    # При каждом запуске — новые посты.
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

    last_flights_key = (
        flights_schedule_key(now)
    )

    last_rates_key = (
        rates_schedule_key(now)
    )

    last_time_key = (
        time_schedule_key(now)
    )

    print(
        "ДИСПЕТЧЕР ЗАПУЩЕН."
    )
    print(
        "Погода: новый пост каждый час."
    )
    print(
        "Бен-Гурион: 5 новых постов в :00 и :30."
    )
    print(
        "Валюты: новый пост в 08:00, 12:00, 16:00, 20:00."
    )
    print(
        "Мировое время: новый пост каждый час."
    )

    while True:

        now = datetime.now(TZ)

        # ---------------------------------------------
        # ПОГОДА — НОВЫЙ ПОСТ КАЖДЫЙ ЧАС
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
        # БЕН-ГУРИОН — 5 НОВЫХ ПОСТОВ :00 / :30
        # ---------------------------------------------
        current_flights_key = (
            flights_schedule_key(now)
        )

        if (
            current_flights_key
            != last_flights_key
        ):
            try:
                update_flight_board()
                last_flights_key = (
                    current_flights_key
                )
            except Exception as error:
                print(
                    "БЕН-ГУРИОН — ОШИБКА:",
                    error
                )

        # ---------------------------------------------
        # ВАЛЮТЫ — НОВЫЙ ПОСТ 08 / 12 / 16 / 20
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
        # МИРОВОЕ ВРЕМЯ — НОВЫЙ ПОСТ КАЖДЫЙ ЧАС
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

if __name__ == "__main__":

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

    else:
        main()
