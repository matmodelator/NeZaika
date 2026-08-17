# =========================================================
# Блочная структура: погода + Бен Гурион
# | 2.0.0
# =========================================================




import os
import sys
import time
import math
import requests

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

WEATHER_MESSAGE_ID_FILE = "weather_message_id.txt"
WEATHER_SLOT_FILE = "weather_slot.txt"


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
        f"{now.strftime('%H:%M')}\n\n"

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
    now = datetime.now(TZ)
    text = make_weather_text()

    current_slot = weather_slot(now)
    saved_slot = load_weather_slot()
    message_id = load_id_file(
        WEATHER_MESSAGE_ID_FILE
    )

    # Новый пост в каждом новом 12-часовом слоте.
    # Благодаря WEATHER_SLOT_FILE это работает и после перезапуска.
    need_new = (
        force_new
        or message_id is None
        or saved_slot != current_slot
    )

    if need_new:
        message_id = send_message(text)
        save_id_file(
            WEATHER_MESSAGE_ID_FILE,
            message_id
        )
        save_weather_slot(current_slot)

        print(
            "ПОГОДА: создан новый пост:",
            message_id,
            now.strftime("%H:%M")
        )
    else:
        edit_message(
            message_id,
            text
        )

        print(
            "ПОГОДА: обновлено:",
            now.strftime("%H:%M")
        )

    return message_id


# =========================================================
# 2. БЕН-ГУРИОН — ТАБЛО РЕЙСОВ
# =========================================================
# Независимый сервис.
#
# Источник:
#   официальный государственный набор рейсов data.gov.il / IAA
#
# Публикация:
#   1) прилёты
#   2) вылеты
#   3) изменения: задержки / отмены
#
# Обновление:
#   один раз в 10 минут
#
# Принудительное обновление:
#   /arrivals
#   /departures
#   /flights
#   CMD: python weather_bot.py flights
# =========================================================

FLIGHTS_RESOURCE_ID = (
    "e83f763b-b7d7-479e-b172-ae981ddc6de5"
)

FLIGHTS_UPDATE_INTERVAL = 600

ARRIVALS_MESSAGE_ID_FILE = "arrivals_message_id.txt"
DEPARTURES_MESSAGE_ID_FILE = "departures_message_id.txt"
FLIGHT_ALERTS_MESSAGE_ID_FILE = "flight_alerts_message_id.txt"


# ---------------------------------------------------------
# 2.1. ПОЛУЧЕНИЕ ДАННЫХ
# ---------------------------------------------------------

def get_flights():
    url = (
        "https://data.gov.il/api/3/action/"
        "datastore_search"
    )

    response = requests.get(
        url,
        params={
            "resource_id": FLIGHTS_RESOURCE_ID,
            "limit": 5000,
        },
        timeout=30,
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("success"):
        raise RuntimeError(
            "data.gov.il не вернул данные рейсов"
        )

    return data["result"]["records"]


# ---------------------------------------------------------
# 2.2. НОРМАЛИЗАЦИЯ ПОЛЕЙ РЕЙСА
# ---------------------------------------------------------

def parse_flight_time(value):
    if not value:
        return None

    try:
        value = str(value).strip()

        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ)

    except Exception:
        return None


def flight_number(flight):
    return (
        f"{flight.get('CHOPER', '')}"
        f"{flight.get('CHFLTN', '')}"
    ).strip()


def flight_city(flight):
    return (
        flight.get("CHLOC1T")
        or flight.get("CHLOC1D")
        or flight.get("CHLOC1")
        or "—"
    ).strip()


def flight_status(flight):
    raw = (
        flight.get("CHRMINE")
        or ""
    ).strip()

    status = raw.upper()

    translations = {
        "LANDED": "🟢 приземлился",
        "DEPARTED": "🟢 вылетел",
        "ON TIME": "🟢 по расписанию",
        "FINAL": "🟢 посадка заканчивается",
        "BOARDING": "🟢 посадка",
        "DELAYED": "🟡 задерживается",
        "CANCELLED": "🔴 отменён",
        "CANCELED": "🔴 отменён",
    }

    if status in translations:
        return translations[status]

    return raw if raw else "статус неизвестен"


def flight_delay_minutes(flight):
    scheduled = parse_flight_time(
        flight.get("CHSTOL")
    )

    expected = parse_flight_time(
        flight.get("CHPTOL")
    )

    if not scheduled or not expected:
        return None

    return int(
        (expected - scheduled).total_seconds()
        / 60
    )


def flight_delay_text(flight):
    minutes = flight_delay_minutes(flight)

    if minutes is None:
        return ""

    if minutes >= 10:
        return f" +{minutes} мин"

    if minutes <= -10:
        return f" {minutes} мин"

    return ""


# ---------------------------------------------------------
# 2.3. ОТБОР И ФОРМАТИРОВАНИЕ РЕЙСОВ
# ---------------------------------------------------------

def relevant_flights(
    flights,
    direction,
    hours_back=1,
    hours_forward=6
):
    now = datetime.now(TZ)

    start = now - timedelta(
        hours=hours_back
    )

    end = now + timedelta(
        hours=hours_forward
    )

    result = []

    for flight in flights:
        if flight.get("CHAORD") != direction:
            continue

        t = (
            parse_flight_time(
                flight.get("CHPTOL")
            )
            or
            parse_flight_time(
                flight.get("CHSTOL")
            )
        )

        if t is None:
            continue

        if start <= t <= end:
            result.append((t, flight))

    result.sort(
        key=lambda item: item[0]
    )

    return result


def make_flight_line(t, flight):
    number = flight_number(flight)
    city = flight_city(flight)
    terminal = flight.get("CHTERM")
    status = flight_status(flight)
    delay = flight_delay_text(flight)

    line = (
        f"{t.strftime('%H:%M')}  "
        f"{number}  "
        f"{city}"
    )

    if terminal:
        line += f"  T{terminal}"

    line += f"\n   {status}{delay}"

    return line


# ---------------------------------------------------------
# 2.4. ТЕКСТ: ПРИЛЁТЫ / ВЫЛЕТЫ / ИЗМЕНЕНИЯ
# ---------------------------------------------------------

def make_flights_text(
    flights,
    direction
):
    if direction == "A":
        title = "🛬 БЕН-ГУРИОН — ПРИЛЁТЫ"
    else:
        title = "✈️ БЕН-ГУРИОН — ВЫЛЕТЫ"

    selected = relevant_flights(
        flights,
        direction
    )[:25]

    lines = [
        title,
        "",
    ]

    if not selected:
        lines.append(
            "Нет рейсов в выбранном интервале."
        )
    else:
        for t, flight in selected:
            lines.append(
                make_flight_line(
                    t,
                    flight
                )
            )

    now = datetime.now(TZ)

    lines.extend([
        "",
        f"🕒 Обновлено: {now.strftime('%H:%M')}",
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


def make_flight_alerts_text(flights):
    now = datetime.now(TZ)

    start = now - timedelta(hours=1)
    end = now + timedelta(hours=8)

    alerts = []

    for flight in flights:
        t = (
            parse_flight_time(
                flight.get("CHPTOL")
            )
            or
            parse_flight_time(
                flight.get("CHSTOL")
            )
        )

        if t is None:
            continue

        if not (start <= t <= end):
            continue

        delay = (
            flight_delay_minutes(flight)
            or 0
        )

        status = (
            flight.get("CHRMINE")
            or ""
        ).upper()

        problem = (
            delay >= 20
            or "DELAY" in status
            or "CANCEL" in status
        )

        if problem:
            alerts.append((t, flight))

    alerts.sort(
        key=lambda item: item[0]
    )

    lines = [
        "⚠️ БЕН-ГУРИОН — ИЗМЕНЕНИЯ",
        "",
    ]

    if not alerts:
        lines.append(
            "Существенных задержек и отмен нет."
        )
    else:
        for t, flight in alerts[:25]:
            direction = (
                "🛬"
                if flight.get("CHAORD") == "A"
                else "✈️"
            )

            lines.append(
                direction + " " +
                make_flight_line(
                    t,
                    flight
                )
            )

    lines.extend([
        "",
        f"🕒 Обновлено: {now.strftime('%H:%M')}",
        "",
        "@ne_zaika",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------
# 2.5. ОБНОВЛЕНИЕ ТРЁХ ПОСТОВ БЕН-ГУРИОНА
# ---------------------------------------------------------

def update_flight_board():
    print("БЕН-ГУРИОН: получаю табло...")

    flights = get_flights()

    arrivals_text = make_flights_text(
        flights,
        "A"
    )

    departures_text = make_flights_text(
        flights,
        "D"
    )

    alerts_text = make_flight_alerts_text(
        flights
    )

    update_persistent_post(
        arrivals_text,
        ARRIVALS_MESSAGE_ID_FILE
    )

    update_persistent_post(
        departures_text,
        DEPARTURES_MESSAGE_ID_FILE
    )

    update_persistent_post(
        alerts_text,
        FLIGHT_ALERTS_MESSAGE_ID_FILE
    )

    print(
        "БЕН-ГУРИОН: обновлено:",
        datetime.now(TZ).strftime("%H:%M")
    )


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
                update_weather()
                send_private_reply(
                    chat_id,
                    "Погода обновлена."
                )

            elif command == "/arrivals":
                flights = get_flights()
                update_persistent_post(
                    make_flights_text(
                        flights,
                        "A"
                    ),
                    ARRIVALS_MESSAGE_ID_FILE
                )
                send_private_reply(
                    chat_id,
                    "Табло прилётов обновлено."
                )

            elif command == "/departures":
                flights = get_flights()
                update_persistent_post(
                    make_flights_text(
                        flights,
                        "D"
                    ),
                    DEPARTURES_MESSAGE_ID_FILE
                )
                send_private_reply(
                    chat_id,
                    "Табло вылетов обновлено."
                )

            elif command == "/flights":
                update_flight_board()
                send_private_reply(
                    chat_id,
                    "Табло Бен-Гуриона обновлено."
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

def main():
    telegram_offset = None

    # ----- ПОГОДА: старт -----
    try:
        update_weather()
    except Exception as error:
        print(
            "ПОГОДА — ОШИБКА ПРИ ЗАПУСКЕ:",
            error
        )

    # ----- БЕН-ГУРИОН: старт -----
    try:
        update_flight_board()
        last_flights_update = time.time()
    except Exception as error:
        print(
            "БЕН-ГУРИОН — ОШИБКА ПРИ ЗАПУСКЕ:",
            error
        )
        last_flights_update = 0

    last_weather_hour = (
        datetime.now(TZ).strftime(
            "%Y-%m-%d %H"
        )
    )

    print(
        "ДИСПЕТЧЕР ЗАПУЩЕН:",
        "погода — каждый час;",
        "Бен-Гурион — каждые 10 минут."
    )

    while True:

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

        now = datetime.now(TZ)

        # ---------------------------------------------
        # ПОГОДА — НЕЗАВИСИМЫЙ ТАЙМЕР
        # ---------------------------------------------
        current_weather_hour = (
            now.strftime("%Y-%m-%d %H")
        )

        if (
            current_weather_hour
            != last_weather_hour
        ):
            try:
                update_weather()

                last_weather_hour = (
                    current_weather_hour
                )

            except Exception as error:
                print(
                    "ПОГОДА — ОШИБКА "
                    "АВТООБНОВЛЕНИЯ:",
                    error
                )

        # ---------------------------------------------
        # БЕН-ГУРИОН — НЕЗАВИСИМЫЙ ТАЙМЕР
        # ---------------------------------------------
        if (
            time.time() - last_flights_update
            >= FLIGHTS_UPDATE_INTERVAL
        ):
            try:
                update_flight_board()

                last_flights_update = (
                    time.time()
                )

            except Exception as error:
                print(
                    "БЕН-ГУРИОН — ОШИБКА "
                    "АВТООБНОВЛЕНИЯ:",
                    error
                )

        # ---------------------------------------------
        # ОБЩИЙ ТИК ДИСПЕТЧЕРА
        # ---------------------------------------------
        time.sleep(SCHEDULER_INTERVAL)


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

    else:
        main()
