# =========================================================
# РАНЖИРЫ 
# | 1.2.0
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
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = "8843774698:AAGoaYTS4zask-N9HtesZ2v9pbx_1MCrbLY"
CHANNEL = "@ne_zaika"


# Хайфа
LAT = 32.7940
LON = 34.9896

TZ = ZoneInfo("Asia/Jerusalem")

# Раз в час
# UPDATE_INTERVAL = 3600

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
# ВЛАЖНОСТЬ РАНЖИР
# =========================================================

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

# =========================================================
# ДАВЛЕНИЕ РАНЖИР
# =========================================================

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

# =========================================================
# ВЕТЕР РАНЖИР
# =========================================================

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


# =========================================================
# УФ / ПЫЛЬ / ВЗВЕШЕННЫЕ ЧАСТИЦЫ / СОЛНЕЧНАЯ АКТИВНОСТЬ
# =========================================================

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


# =========================================================
# КОДЫ ЯВЛЕНИЙ
# =========================================================

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

        f"☀️ УФ-индекс: {uv:.1f} — {uv_text}\n"

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
# ПУШ через ТГ
# =========================================================

def check_telegram_commands(offset=None):
    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 0,
        "limit": 10
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
        update_id = update["update_id"]
        offset = update_id + 1

        message = update.get("message")

        if not message:
            continue

        text = message.get(
            "text",
            ""
        ).strip().lower()

        if text == "/weather":
            message_id = load_message_id()
            weather_text = make_text()

            if message_id is None:
                message_id = send_message(
                    weather_text
                )
                save_message_id(
                    message_id
                )
            else:
                edit_message(
                    message_id,
                    weather_text
                )

            chat_id = message["chat"]["id"]

            requests.post(
                f"https://api.telegram.org/"
                f"bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": "Погода обновлена."
                },
                timeout=10
            )

    return offset

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
# ОПРЕДЕЛЕНИЕ КРУГЛОГО ВРЕМЕНИ
# =========================================================

def wait_until_next_hour(telegram_offset):
    now = datetime.now(TZ)

    target = (
        now.replace(
          minute=0,
            second=0,
            microsecond=0
        )
        + timedelta(hours=1)
# + timedelta(minutes=1)
    )

    while True:
        now = datetime.now(TZ)
        seconds = (target - now).total_seconds()

        if seconds <= 0:
            return telegram_offset

        telegram_offset = check_telegram_commands(
            telegram_offset
        )

        time.sleep(min(5, seconds))

# =========================================================
# ОСНОВНОЙ ЦИКЛ
# =========================================================

def main():

    message_id = load_message_id()
    telegram_offset = None

    while True:

        try:
            telegram_offset = check_telegram_commands(
                telegram_offset
            )

            text = make_text()
            now = datetime.now(TZ)

            # Новый пост в ПОЛНОЧЬ
            if now.hour == 0 and now.minute == 0:

                message_id = send_message(text)
                save_message_id(message_id)

                print(
                    "Создан новый суточный погодный пост:",
                    message_id
                )

            elif message_id is None:

                message_id = send_message(text)
                save_message_id(message_id)

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
                    now.strftime("%H:%M")
                )

        except Exception as error:

            print(
                "ОШИБКА:",
                error
            )

        telegram_offset = wait_until_next_hour(
            telegram_offset
        )

# =========================================================
# ПУШ через cmd
# =========================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "now":
        message_id = load_message_id()
        text = make_text()

        if message_id is None:
            message_id = send_message(text)
            save_message_id(message_id)
            print("Создан новый погодный пост:", message_id)
        else:
            edit_message(message_id, text)
            print("Погодный пост принудительно обновлён.")

    else:
        main()
