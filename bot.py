import os
import re
import hmac
import time
import json
import hashlib
import asyncio
import psycopg2
from datetime import date
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

DATABASE_URL = os.environ.get("DATABASE_URL")

WEBAPP_URL = "https://turbopushups.github.io/pushup-camera/index.html"
COMMUNITY_URL = "https://t.me/pushuphero"

bot = Bot(token=TOKEN)
dp = Dispatcher()

db = None


def connect_db():
    global db
    print("Подключаемся к базе данных...")
    dsn = DATABASE_URL
    if "sslmode" not in dsn:
        dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
    db = psycopg2.connect(
        dsn,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    db.autocommit = True


def run_query(sql, params=None, fetchone=False, fetchall=False):
    global db
    for attempt in range(2):
        try:
            if db is None or db.closed:
                connect_db()
            cursor = db.cursor()
            cursor.execute(sql, params or ())
            if fetchone:
                return cursor.fetchone()
            if fetchall:
                return cursor.fetchall()
            return None
        except psycopg2.Error as e:
            print(f"Проблема с соединением (попытка {attempt + 1}): {e}")
            try:
                connect_db()
            except Exception as reconnect_error:
                print(f"Не удалось переподключиться: {reconnect_error}")
                raise
    raise psycopg2.OperationalError("Не удалось выполнить запрос после переподключения")


async def db_query(sql, params=None, fetchone=False, fetchall=False):
    return await asyncio.to_thread(run_query, sql, params, fetchone, fetchall)


def init_db():
    run_query("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        nickname TEXT,
        total_points INTEGER DEFAULT 0,
        total_pushups INTEGER DEFAULT 0
    )
    """)
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_plank_seconds INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_pushups INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_pushup_date DATE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_plank_seconds INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_plank_date DATE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS plank_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_dungeon INTEGER DEFAULT 1")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS plank_dungeon INTEGER DEFAULT 1")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_best_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS plank_best_streak INTEGER DEFAULT 0")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_squats INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_squats INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_squat_date DATE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS squat_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS squat_best_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS squat_dungeon INTEGER DEFAULT 1")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_wallsit_seconds INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_wallsit_seconds INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_wallsit_date DATE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallsit_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallsit_best_streak INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallsit_dungeon INTEGER DEFAULT 1")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_achv_level INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS squat_achv_level INTEGER DEFAULT 0")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS stamina_remaining INTEGER DEFAULT 4")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS stamina_reset_date DATE")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS premium_until DATE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_purchased_pairs TEXT DEFAULT ''")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS plank_purchased_pairs TEXT DEFAULT ''")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS squat_purchased_pairs TEXT DEFAULT ''")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallsit_purchased_pairs TEXT DEFAULT ''")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS story_enabled BOOLEAN DEFAULT TRUE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_story_seen TEXT DEFAULT ''")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS spendable_points INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS potion_purchases_in_week INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS potion_week_anchor_date DATE")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_stamina_token TEXT")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_stamina_token_at TIMESTAMPTZ")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_date DATE")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_enemies INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_reps INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_hold_seconds INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_dungeons INTEGER DEFAULT 0")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_claimed TEXT DEFAULT ''")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_quest_chest_claimed BOOLEAN DEFAULT FALSE")

    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS pushup_arena_wave INTEGER DEFAULT 1")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS plank_arena_wave INTEGER DEFAULT 1")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS squat_arena_wave INTEGER DEFAULT 1")
    run_query("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallsit_arena_wave INTEGER DEFAULT 1")


init_db()


def verify_init_data(init_data: str, max_age_seconds: int = 86400):
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except Exception:
        return None

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = parsed.get("auth_date")
    if auth_date:
        try:
            if time.time() - int(auth_date) > max_age_seconds:
                return None
        except ValueError:
            return None

    user_json = parsed.get("user")
    if not user_json:
        return None
    try:
        user = json.loads(user_json)
    except Exception:
        return None

    return user


async def resolve_user_id(request, body=None):
    init_data = None
    if body is not None:
        init_data = body.get("init_data")
    if not init_data:
        init_data = request.query.get("init_data")

    user = verify_init_data(init_data)
    if not user:
        return None
    return user.get("id")


def unauthorized():
    return web.json_response({"error": "Не удалось подтвердить пользователя Telegram"}, status=401)


async def ensure_user_exists(user_id, username=None):
    if username:
        await db_query(
            "INSERT INTO users (user_id, username) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username",
            (user_id, username)
        )
    else:
        await db_query(
            "INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            (user_id,)
        )


BANNED_ROOTS = [
    "хуй", "хуе", "хуи", "пизд", "ебат", "ебал", "ёбан", "еба", "бляд",
    "сука ", "мудак", "долбоеб", "долбоёб", "пидор", "пидар", "залуп",
    "fuck", "shit", "bitch", "cunt", "asshole", "nigger", "faggot", "whore"
]


def is_nickname_valid(nickname: str):
    nickname = nickname.strip()
    if len(nickname) < 2 or len(nickname) > 20:
        return False, "Имя должно быть от 2 до 20 символов."
    if not re.match(r'^[a-zA-Zа-яА-ЯёЁ0-9 ]+$', nickname):
        return False, "Используй только буквы, цифры и пробелы, без спецсимволов."
    lowered = nickname.lower()
    for root in BANNED_ROOTS:
        if root in lowered:
            return False, "Это имя недопустимо. Придумай, пожалуйста, другое."
    return True, None


LEVELS = [
    (5000, "👑 Легенда зала"),
    (1500, "⚡ Терминатор"),
    (500, "🔥 Силач"),
    (100, "💪 Крепыш"),
    (0, "🐣 Новичок"),
]


def get_level(points):
    for threshold, title in LEVELS:
        if points >= threshold:
            return title
    return LEVELS[-1][1]


def get_next_level_info(points):
    for threshold, title in reversed(LEVELS):
        if points < threshold:
            return threshold, title
    return None, None


PUSHUP_ZONES = [
    (3, 15), (1, 25), (4, 30), (1, 40), (5, 45), (1, 55), (6, 60), (1, 70),
    (7, 85), (1, 100), (8, 110), (1, 125), (9, 140), (1, 160), (10, 175), (1, 200),
    (11, 215), (1, 245), (12, 260), (1, 300),
]

PLANK_ZONES = [
    (3, 12), (1, 20), (4, 24), (1, 32), (5, 36), (1, 44), (6, 48), (1, 56),
    (7, 64), (1, 76), (8, 84), (1, 96), (9, 108), (1, 124), (10, 140), (1, 160),
    (11, 172), (1, 196), (12, 208), (1, 240),
]

SQUAT_ZONES = [(count, total * 2) for count, total in PUSHUP_ZONES]
WALLSIT_ZONES = list(PLANK_ZONES)

ZONE_TABLES = {"pushup": PUSHUP_ZONES, "plank": PLANK_ZONES, "squat": SQUAT_ZONES, "wallsit": WALLSIT_ZONES}
MAX_DUNGEON = len(PUSHUP_ZONES)

PAID_PAIR_STARTS = [7, 9, 11, 13, 15, 17, 19]
PAIR_PRICE_STARS = 200
PREMIUM_PRICE_STARS = 1000

POTION_PRICE_ESSENCE = 300
MAX_POTIONS_PER_WEEK = 2

FREE_STORY_UNTIL = date(2026, 9, 10)

QUEST_TARGETS = {"enemies": 2, "reps": 30, "hold_seconds": 300, "dungeons": 1}
QUEST_TITLES = {
    "enemies": "⚔️ Победи 2 врагов",
    "reps": "💪 Сделай 30 повторений (отжимания/приседания)",
    "hold_seconds": "🔥 Продержись 5 минут (планка/стульчик)",
    "dungeons": "🏃 Заверши один поход",
}
QUEST_REWARD_XP = 50
QUEST_REWARD_ESSENCE = 20
CHEST_REWARD_XP = 100
CHEST_REWARD_ESSENCE = 50

QUEST_RESET_FIELDS_SQL = """
    daily_quest_enemies = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_enemies, 0) END,
    daily_quest_reps = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_reps, 0) END,
    daily_quest_hold_seconds = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_hold_seconds, 0) END,
    daily_quest_dungeons = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_dungeons, 0) END,
    daily_quest_claimed = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN '' ELSE COALESCE(users.daily_quest_claimed, '') END,
    daily_quest_chest_claimed = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN FALSE ELSE COALESCE(users.daily_quest_chest_claimed, FALSE) END,
    daily_quest_date = CURRENT_DATE
"""


def generate_dungeon(activity, n):
    n = min(max(n, 1), MAX_DUNGEON)
    zones = ZONE_TABLES[activity]
    enemy_count, total = zones[n - 1]
    is_boss = (enemy_count == 1)
    hp_each = max(1, round(total / enemy_count))
    enemies = [hp_each] * enemy_count
    diff = total - sum(enemies)
    enemies[-1] += diff
    return {"dungeon": n, "is_boss": is_boss, "enemies": enemies, "xp_reward": total}


def pair_start_for_zone(n: int):
    for start in PAID_PAIR_STARTS:
        if n in (start, start + 1):
            return start
    return None


DUNGEON_COL = {"pushup": "pushup_dungeon", "plank": "plank_dungeon", "squat": "squat_dungeon", "wallsit": "wallsit_dungeon"}
PURCHASED_COL = {"pushup": "pushup_purchased_pairs", "plank": "plank_purchased_pairs", "squat": "squat_purchased_pairs", "wallsit": "wallsit_purchased_pairs"}
ARENA_COL = {"pushup": "pushup_arena_wave", "plank": "plank_arena_wave", "squat": "squat_arena_wave", "wallsit": "wallsit_arena_wave"}

ZONES_META = [
    {"n": 1, "name": "Пещера летучих мышей", "chapter": 1}, {"n": 2, "name": "Гнездо ночного хищника", "chapter": 1},
    {"n": 3, "name": "Подземелье скелетов", "chapter": 2}, {"n": 4, "name": "Зал костяного командира", "chapter": 2},
    {"n": 5, "name": "Логово тролля", "chapter": 3}, {"n": 6, "name": "Двухголовый командир троллей", "chapter": 3},
    {"n": 7, "name": "Лес призраков", "chapter": 4}, {"n": 8, "name": "Рой погибших душ", "chapter": 4},
    {"n": 9, "name": "Топи гниения", "chapter": 5}, {"n": 10, "name": "Трон затонувшего короля", "chapter": 5},
    {"n": 11, "name": "Павшие рыцари", "chapter": 6}, {"n": 12, "name": "Чёрный рыцарь", "chapter": 6},
    {"n": 13, "name": "Огненные твари", "chapter": 7}, {"n": 14, "name": "Повелитель пламени", "chapter": 7},
    {"n": 15, "name": "Искажённые", "chapter": 8}, {"n": 16, "name": "Страж Бездны", "chapter": 8},
    {"n": 17, "name": "Зал испытаний", "chapter": 9}, {"n": 18, "name": "Варен", "chapter": 9},
    {"n": 19, "name": "Последняя печать", "chapter": 10}, {"n": 20, "name": "Морвен", "chapter": 10},
]

CHAPTERS = [
    {"chapter": 1, "title": "Крылья во тьме", "start": 1, "end": 2},
    {"chapter": 2, "title": "Кости не забывают", "start": 3, "end": 4},
    {"chapter": 3, "title": "Голод гор", "start": 5, "end": 6},
    {"chapter": 4, "title": "Лес, который помнит", "start": 7, "end": 8},
    {"chapter": 5, "title": "Гнилое сердце", "start": 9, "end": 10},
    {"chapter": 6, "title": "Крепость павших", "start": 11, "end": 12},
    {"chapter": 7, "title": "Земли пепла", "start": 13, "end": 14},
    {"chapter": 8, "title": "Бездна", "start": 15, "end": 16},
    {"chapter": 9, "title": "Цитадель", "start": 17, "end": 18},
    {"chapter": 10, "title": "Трон", "start": 19, "end": 20},
]

ACTIVITY_NAMES = {"pushup": "Отжимания", "plank": "Планка", "squat": "Приседания", "wallsit": "Стульчик"}

ARENA_WAVES_PER_CHAPTER = 5
ARENA_TOTAL_CHAPTERS = 10
ARENA_CYCLE_LEN = ARENA_WAVES_PER_CHAPTER * ARENA_TOTAL_CHAPTERS


def generate_arena_wave(activity, wave_n):
    wave_n = max(1, wave_n)
    zones = ZONE_TABLES[activity]

    cycle_index = (wave_n - 1) // ARENA_CYCLE_LEN
    pos_in_cycle = (wave_n - 1) % ARENA_CYCLE_LEN
    chapter = pos_in_cycle // ARENA_WAVES_PER_CHAPTER + 1
    local_wave = pos_in_cycle % ARENA_WAVES_PER_CHAPTER + 1
    scale = 1 + 0.5 * cycle_index

    n_normal = 2 * chapter - 1
    n_boss = 2 * chapter
    normal_count, normal_total = zones[n_normal - 1]
    boss_count, boss_total = zones[n_boss - 1]

    normal_hp_each = max(1, round((normal_total / normal_count) * scale))
    boss_hp_each = max(1, round(boss_total * scale))

    counts_by_local_wave = {1: 3, 2: 5, 3: 7, 4: 10}
    enemies = []
    if local_wave in counts_by_local_wave:
        cnt = counts_by_local_wave[local_wave]
        enemies = [{"hp": normal_hp_each, "zone": n_normal, "is_boss": False} for _ in range(cnt)]
    else:  # local_wave == 5
        enemies = [{"hp": normal_hp_each, "zone": n_normal, "is_boss": False} for _ in range(10)]
        enemies += [{"hp": boss_hp_each, "zone": n_boss, "is_boss": True} for _ in range(1)]

    xp_reward = sum(e["hp"] for e in enemies)
    return {
        "wave": wave_n, "chapter": chapter, "local_wave": local_wave, "cycle": cycle_index + 1,
        "enemies": enemies, "xp_reward": xp_reward,
    }


@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id
    username = message.from_user.first_name or message.from_user.username or "Игрок"
    await ensure_user_exists(user_id, username)

    personal_url = f"{WEBAPP_URL}?v=18"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать приключение", web_app=WebAppInfo(url=personal_url))]
    ])

    await message.answer(
        "⚔️ PushUp Hero\n\n"
        "Спорт ещё никогда не был таким затягивающим. Каждое отжимание — удар по врагу, каждая тренировка — шаг в истории, где ты главный герой.\n\n"
        "📹 Проходи сюжетную кампанию, испытай себя на бесконечной Арене или тренируйся свободно перед камерой — бот всё засчитает сам.\n"
        "🏆 Получай награды, качай персонажа, соревнуйся в таблице лидеров.\n"
        "📅 Каждый день — новые задания с наградами.\n\n"
        "🗝️ Первые главы истории — бесплатны. Начиная с IV главы — «Лес, который помнит» — каждая следующая пара подземелий открывается за 200 ⭐, либо сразу все — премиумом.\n\n"
        f"💬 Новости и пожелания — в группе: {COMMUNITY_URL}",
        reply_markup=keyboard
    )


async def api_user_status(request):
    user_id = await resolve_user_id(request)
    if not user_id:
        return unauthorized()

    row = await db_query("""
        INSERT INTO users (user_id) VALUES (%s)
        ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING nickname
    """, (user_id,), fetchone=True)
    nickname = row[0] if row else None
    return web.json_response({"has_nickname": nickname is not None, "nickname": nickname})


async def api_register_nickname(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        nickname = data.get("nickname", "")
        valid, error = is_nickname_valid(nickname)
        if not valid:
            return web.json_response({"success": False, "error": error})
        await db_query(
            "INSERT INTO users (user_id, nickname) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname",
            (user_id, nickname.strip())
        )
        return web.json_response({"success": True, "nickname": nickname.strip()})
    except Exception as e:
        print(f"Ошибка в api_register_nickname: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_profile(request):
    user_id = await resolve_user_id(request)
    if not user_id:
        return unauthorized()

    try:
        row = await db_query("""
            INSERT INTO users (user_id) VALUES (%(user_id)s)
            ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
            RETURNING total_points, spendable_points,
                      total_pushups, daily_pushups, last_pushup_date, pushup_streak, pushup_best_streak, pushup_dungeon,
                      total_plank_seconds, daily_plank_seconds, last_plank_date, plank_streak, plank_best_streak, plank_dungeon,
                      total_squats, daily_squats, last_squat_date, squat_streak, squat_best_streak, squat_dungeon,
                      total_wallsit_seconds, daily_wallsit_seconds, last_wallsit_date, wallsit_streak, wallsit_best_streak, wallsit_dungeon,
                      pushup_achv_level, squat_achv_level,
                      stamina_remaining, stamina_reset_date, premium_until, story_enabled,
                      potion_purchases_in_week, potion_week_anchor_date,
                      pushup_arena_wave, plank_arena_wave, squat_arena_wave, wallsit_arena_wave,
                      CURRENT_DATE
        """, {"user_id": user_id}, fetchone=True)

        (points, spendable,
         total_pushups, daily_pushups, last_pushup_date, pushup_streak, pushup_best_streak, pushup_dungeon,
         total_plank_seconds, daily_plank_seconds, last_plank_date, plank_streak, plank_best_streak, plank_dungeon,
         total_squats, daily_squats, last_squat_date, squat_streak, squat_best_streak, squat_dungeon,
         total_wallsit_seconds, daily_wallsit_seconds, last_wallsit_date, wallsit_streak, wallsit_best_streak, wallsit_dungeon,
         pushup_achv_level, squat_achv_level,
         stamina_remaining, stamina_reset_date, premium_until, story_enabled,
         potion_purchases_in_week, potion_week_anchor_date,
         pushup_arena_wave, plank_arena_wave, squat_arena_wave, wallsit_arena_wave,
         today) = row

        def today_or_zero(value, last_date):
            return value if last_date == today else 0

        def streak_or_zero(streak, last_date):
            return streak if last_date and (today - last_date).days <= 1 else 0

        pushups_today = today_or_zero(daily_pushups, last_pushup_date)
        plank_today = today_or_zero(daily_plank_seconds, last_plank_date)
        squats_today = today_or_zero(daily_squats, last_squat_date)
        wallsit_today = today_or_zero(daily_wallsit_seconds, last_wallsit_date)

        level_title = get_level(points)
        next_threshold, next_title = get_next_level_info(points)

        premium_active = premium_until is not None and premium_until >= today
        stamina_max = 8 if premium_active else 4
        stamina_display = stamina_remaining if stamina_reset_date == today else stamina_max

        pushup_next = 10 + 10 * ((pushup_achv_level or 0) + 1)
        squat_next = 10 + 10 * ((squat_achv_level or 0) + 1)

        potions_used_this_week = potion_purchases_in_week or 0
        if potion_week_anchor_date is None or (today - potion_week_anchor_date).days >= 7:
            potions_used_this_week = 0
        potions_left_this_week = max(0, MAX_POTIONS_PER_WEEK - potions_used_this_week)

        return web.json_response({
            "level": level_title, "points": points,
            "next_level": next_title,
            "points_to_next_level": (next_threshold - points) if next_threshold else None,
            "pushup": {"today": pushups_today, "streak": streak_or_zero(pushup_streak, last_pushup_date), "best_streak": pushup_best_streak or 0, "total": total_pushups, "dungeon": pushup_dungeon, "arena_wave": pushup_arena_wave or 1},
            "plank": {"today_seconds": plank_today, "streak": streak_or_zero(plank_streak, last_plank_date), "best_streak": plank_best_streak or 0, "total_seconds": total_plank_seconds, "dungeon": plank_dungeon, "arena_wave": plank_arena_wave or 1},
            "squat": {"today": squats_today, "streak": streak_or_zero(squat_streak, last_squat_date), "best_streak": squat_best_streak or 0, "total": total_squats, "dungeon": squat_dungeon, "arena_wave": squat_arena_wave or 1},
            "wallsit": {"today_seconds": wallsit_today, "streak": streak_or_zero(wallsit_streak, last_wallsit_date), "best_streak": wallsit_best_streak or 0, "total_seconds": total_wallsit_seconds, "dungeon": wallsit_dungeon, "arena_wave": wallsit_arena_wave or 1},
            "achievements": {"pushup_level": pushup_achv_level or 0, "pushup_today": pushups_today, "pushup_next_threshold": pushup_next, "squat_level": squat_achv_level or 0, "squat_today": squats_today, "squat_next_threshold": squat_next},
            "stamina": {"remaining": stamina_display, "max": stamina_max},
            "premium_active": premium_active,
            "premium_until": premium_until.isoformat() if premium_until else None,
            "story_enabled": story_enabled if story_enabled is not None else True,
            "wallet": {
                "spendable_points": spendable or 0,
                "potion_price": POTION_PRICE_ESSENCE,
                "potions_left_this_week": potions_left_this_week,
                "max_potions_per_week": MAX_POTIONS_PER_WEEK,
            },
            "story_free_until": FREE_STORY_UNTIL.isoformat(),
        })
    except Exception as e:
        print(f"Ошибка в api_profile: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_set_story_enabled(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        enabled = data.get("enabled")
        if enabled is None:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        await db_query("UPDATE users SET story_enabled = %s WHERE user_id = %s", (bool(enabled), user_id))
        return web.json_response({"success": True})
    except Exception as e:
        print(f"Ошибка в api_set_story_enabled: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_story_meta(request):
    return web.json_response({"chapters": CHAPTERS, "zones": ZONES_META})


async def api_leaderboard(request):
    activity = request.query.get("activity", "pushup")
    period = request.query.get("period", "total")
    arena = request.query.get("arena") == "true"

    if arena:
        if activity not in ARENA_COL:
            return web.json_response({"error": "Некорректная дисциплина"}, status=400)
        col = ARENA_COL[activity]
        try:
            rows = await db_query(f"""
                SELECT COALESCE(nickname, username, 'Игрок') AS name, {col} AS value
                FROM users WHERE {col} > 1 ORDER BY value DESC LIMIT 10
            """, fetchall=True)
            leaderboard = [{"name": name, "value": value} for name, value in rows]
            return web.json_response({"leaderboard": leaderboard, "activity": activity, "arena": True})
        except Exception as e:
            print(f"Ошибка в api_leaderboard (arena): {e}")
            return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)

    col_map = {
        "pushup": ("total_pushups", "daily_pushups", "last_pushup_date"),
        "plank": ("total_plank_seconds", "daily_plank_seconds", "last_plank_date"),
        "squat": ("total_squats", "daily_squats", "last_squat_date"),
        "wallsit": ("total_wallsit_seconds", "daily_wallsit_seconds", "last_wallsit_date"),
    }
    if activity not in col_map:
        return web.json_response({"error": "Некорректная дисциплина"}, status=400)
    total_col, daily_col, date_col = col_map[activity]

    try:
        if period == "today":
            rows = await db_query(f"""
                SELECT COALESCE(nickname, username, 'Игрок') AS name,
                       CASE WHEN {date_col} = CURRENT_DATE THEN {daily_col} ELSE 0 END AS value
                FROM users WHERE ({date_col} = CURRENT_DATE AND {daily_col} > 0)
                ORDER BY value DESC LIMIT 10
            """, fetchall=True)
        else:
            rows = await db_query(f"""
                SELECT COALESCE(nickname, username, 'Игрок') AS name, {total_col} AS value
                FROM users WHERE {total_col} > 0 ORDER BY value DESC LIMIT 10
            """, fetchall=True)
        leaderboard = [{"name": name, "value": value} for name, value in rows]
        return web.json_response({"leaderboard": leaderboard, "activity": activity, "period": period})
    except Exception as e:
        print(f"Ошибка в api_leaderboard: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_save_pushups(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        count = data.get("count")
        if not count or count <= 0:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        count = int(count)
        points_earned = count * 10

        row = await db_query(f"""
            INSERT INTO users (user_id, total_points, spendable_points, total_pushups, daily_pushups, last_pushup_date, pushup_streak, pushup_best_streak, pushup_achv_level,
                                daily_quest_date, daily_quest_reps)
            VALUES (%(user_id)s, %(points)s, %(points)s, %(count)s, %(count)s, CURRENT_DATE, 1, 1,
                    CASE WHEN %(count)s >= 20 THEN 1 ELSE 0 END,
                    CURRENT_DATE, %(count)s)
            ON CONFLICT (user_id) DO UPDATE SET
                total_points = users.total_points + %(points)s,
                spendable_points = COALESCE(users.spendable_points, 0) + %(points)s,
                total_pushups = users.total_pushups + %(count)s,
                daily_pushups = CASE WHEN users.last_pushup_date = CURRENT_DATE THEN users.daily_pushups + %(count)s ELSE %(count)s END,
                pushup_streak = CASE
                    WHEN users.last_pushup_date = CURRENT_DATE THEN COALESCE(users.pushup_streak, 1)
                    WHEN users.last_pushup_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.pushup_streak, 0) + 1
                    ELSE 1 END,
                pushup_best_streak = GREATEST(COALESCE(users.pushup_best_streak, 0),
                    CASE WHEN users.last_pushup_date = CURRENT_DATE THEN COALESCE(users.pushup_streak, 1)
                         WHEN users.last_pushup_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.pushup_streak, 0) + 1
                         ELSE 1 END),
                last_pushup_date = CURRENT_DATE,
                pushup_achv_level = CASE
                    WHEN (CASE WHEN users.last_pushup_date = CURRENT_DATE THEN users.daily_pushups + %(count)s ELSE %(count)s END)
                         >= (10 + 10 * (COALESCE(users.pushup_achv_level, 0) + 1))
                    THEN COALESCE(users.pushup_achv_level, 0) + 1 ELSE COALESCE(users.pushup_achv_level, 0) END,
                {QUEST_RESET_FIELDS_SQL.replace('daily_quest_reps = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_reps, 0) END,',
                                                 'daily_quest_reps = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN %(count)s ELSE COALESCE(users.daily_quest_reps, 0) + %(count)s END,')}
            RETURNING total_points - %(points)s AS points_before, total_points AS points_after
        """, {"points": points_earned, "count": count, "user_id": user_id}, fetchone=True)

        points_before, points_after = row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({"success": True, "points_earned": points_earned, "level_up": level_after != level_before, "new_level": level_after if level_after != level_before else None})
    except Exception as e:
        print(f"Ошибка в api_save_pushups: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_save_squats(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        count = data.get("count")
        if not count or count <= 0:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        count = int(count)
        points_earned = count * 10

        row = await db_query(f"""
            INSERT INTO users (user_id, total_points, spendable_points, total_squats, daily_squats, last_squat_date, squat_streak, squat_best_streak, squat_achv_level,
                                daily_quest_date, daily_quest_reps)
            VALUES (%(user_id)s, %(points)s, %(points)s, %(count)s, %(count)s, CURRENT_DATE, 1, 1,
                    CASE WHEN %(count)s >= 20 THEN 1 ELSE 0 END,
                    CURRENT_DATE, %(count)s)
            ON CONFLICT (user_id) DO UPDATE SET
                total_points = users.total_points + %(points)s,
                spendable_points = COALESCE(users.spendable_points, 0) + %(points)s,
                total_squats = users.total_squats + %(count)s,
                daily_squats = CASE WHEN users.last_squat_date = CURRENT_DATE THEN users.daily_squats + %(count)s ELSE %(count)s END,
                squat_streak = CASE
                    WHEN users.last_squat_date = CURRENT_DATE THEN COALESCE(users.squat_streak, 1)
                    WHEN users.last_squat_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.squat_streak, 0) + 1
                    ELSE 1 END,
                squat_best_streak = GREATEST(COALESCE(users.squat_best_streak, 0),
                    CASE WHEN users.last_squat_date = CURRENT_DATE THEN COALESCE(users.squat_streak, 1)
                         WHEN users.last_squat_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.squat_streak, 0) + 1
                         ELSE 1 END),
                last_squat_date = CURRENT_DATE,
                squat_achv_level = CASE
                    WHEN (CASE WHEN users.last_squat_date = CURRENT_DATE THEN users.daily_squats + %(count)s ELSE %(count)s END)
                         >= (10 + 10 * (COALESCE(users.squat_achv_level, 0) + 1))
                    THEN COALESCE(users.squat_achv_level, 0) + 1 ELSE COALESCE(users.squat_achv_level, 0) END,
                {QUEST_RESET_FIELDS_SQL.replace('daily_quest_reps = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_reps, 0) END,',
                                                 'daily_quest_reps = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN %(count)s ELSE COALESCE(users.daily_quest_reps, 0) + %(count)s END,')}
            RETURNING total_points - %(points)s AS points_before, total_points AS points_after
        """, {"points": points_earned, "count": count, "user_id": user_id}, fetchone=True)

        points_before, points_after = row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({"success": True, "points_earned": points_earned, "level_up": level_after != level_before, "new_level": level_after if level_after != level_before else None})
    except Exception as e:
        print(f"Ошибка в api_save_squats: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_save_plank(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        seconds = data.get("seconds")
        if not seconds or seconds <= 0:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        seconds = int(seconds)
        points_earned = seconds * 2

        row = await db_query("""
            INSERT INTO users (user_id, total_points, spendable_points, total_plank_seconds, daily_plank_seconds, last_plank_date, plank_streak, plank_best_streak,
                                daily_quest_date, daily_quest_hold_seconds)
            VALUES (%(user_id)s, %(points)s, %(points)s, %(seconds)s, %(seconds)s, CURRENT_DATE, 1, 1,
                    CURRENT_DATE, %(seconds)s)
            ON CONFLICT (user_id) DO UPDATE SET
                total_points = users.total_points + %(points)s,
                spendable_points = COALESCE(users.spendable_points, 0) + %(points)s,
                total_plank_seconds = users.total_plank_seconds + %(seconds)s,
                daily_plank_seconds = CASE WHEN users.last_plank_date = CURRENT_DATE THEN users.daily_plank_seconds + %(seconds)s ELSE %(seconds)s END,
                plank_streak = CASE
                    WHEN users.last_plank_date = CURRENT_DATE THEN COALESCE(users.plank_streak, 1)
                    WHEN users.last_plank_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.plank_streak, 0) + 1
                    ELSE 1 END,
                plank_best_streak = GREATEST(COALESCE(users.plank_best_streak, 0),
                    CASE WHEN users.last_plank_date = CURRENT_DATE THEN COALESCE(users.plank_streak, 1)
                         WHEN users.last_plank_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.plank_streak, 0) + 1
                         ELSE 1 END),
                last_plank_date = CURRENT_DATE,
                {QUEST_RESET_FIELDS_SQL.replace('daily_quest_hold_seconds = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_hold_seconds, 0) END,',
                                                 'daily_quest_hold_seconds = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN %(seconds)s ELSE COALESCE(users.daily_quest_hold_seconds, 0) + %(seconds)s END,')}
            RETURNING total_points - %(points)s AS points_before, total_points AS points_after
        """, {"points": points_earned, "seconds": seconds, "user_id": user_id}, fetchone=True)

        points_before, points_after = row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({"success": True, "points_earned": points_earned, "level_up": level_after != level_before, "new_level": level_after if level_after != level_before else None})
    except Exception as e:
        print(f"Ошибка в api_save_plank: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_save_wallsit(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        seconds = data.get("seconds")
        if not seconds or seconds <= 0:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        seconds = int(seconds)
        points_earned = seconds * 2

        row = await db_query("""
            INSERT INTO users (user_id, total_points, spendable_points, total_wallsit_seconds, daily_wallsit_seconds, last_wallsit_date, wallsit_streak, wallsit_best_streak,
                                daily_quest_date, daily_quest_hold_seconds)
            VALUES (%(user_id)s, %(points)s, %(points)s, %(seconds)s, %(seconds)s, CURRENT_DATE, 1, 1,
                    CURRENT_DATE, %(seconds)s)
            ON CONFLICT (user_id) DO UPDATE SET
                total_points = users.total_points + %(points)s,
                spendable_points = COALESCE(users.spendable_points, 0) + %(points)s,
                total_wallsit_seconds = users.total_wallsit_seconds + %(seconds)s,
                daily_wallsit_seconds = CASE WHEN users.last_wallsit_date = CURRENT_DATE THEN users.daily_wallsit_seconds + %(seconds)s ELSE %(seconds)s END,
                wallsit_streak = CASE
                    WHEN users.last_wallsit_date = CURRENT_DATE THEN COALESCE(users.wallsit_streak, 1)
                    WHEN users.last_wallsit_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.wallsit_streak, 0) + 1
                    ELSE 1 END,
                wallsit_best_streak = GREATEST(COALESCE(users.wallsit_best_streak, 0),
                    CASE WHEN users.last_wallsit_date = CURRENT_DATE THEN COALESCE(users.wallsit_streak, 1)
                         WHEN users.last_wallsit_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(users.wallsit_streak, 0) + 1
                         ELSE 1 END),
                last_wallsit_date = CURRENT_DATE,
                {QUEST_RESET_FIELDS_SQL.replace('daily_quest_hold_seconds = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_hold_seconds, 0) END,',
                                                 'daily_quest_hold_seconds = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN %(seconds)s ELSE COALESCE(users.daily_quest_hold_seconds, 0) + %(seconds)s END,')}
            RETURNING total_points - %(points)s AS points_before, total_points AS points_after
        """, {"points": points_earned, "seconds": seconds, "user_id": user_id}, fetchone=True)

        points_before, points_after = row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({"success": True, "points_earned": points_earned, "level_up": level_after != level_before, "new_level": level_after if level_after != level_before else None})
    except Exception as e:
        print(f"Ошибка в api_save_wallsit: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_get_stamina(request):
    user_id = await resolve_user_id(request)
    if not user_id:
        return unauthorized()
    row = await db_query("""
        INSERT INTO users (user_id) VALUES (%(user_id)s)
        ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING stamina_remaining, stamina_reset_date, premium_until, CURRENT_DATE
    """, {"user_id": user_id}, fetchone=True)
    stamina_remaining, stamina_reset_date, premium_until, today = row
    premium_active = premium_until is not None and premium_until >= today
    stamina_max = 8 if premium_active else 4
    remaining = stamina_remaining if stamina_reset_date == today else stamina_max
    return web.json_response({"remaining": remaining, "max": stamina_max})


async def api_consume_stamina(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        token = data.get("token")

        if token:
            existing = await db_query("""
                SELECT stamina_remaining, (CASE WHEN premium_until >= CURRENT_DATE THEN 8 ELSE 4 END),
                       last_stamina_token, last_stamina_token_at
                FROM users WHERE user_id = %s
            """, (user_id,), fetchone=True)
            if existing:
                remaining, max_stamina, last_token, last_token_at = existing
                if last_token == token and last_token_at is not None:
                    return web.json_response({"success": True, "remaining": remaining, "max": max_stamina, "replayed": True})

        row = await db_query("""
            UPDATE users SET
                stamina_remaining = CASE
                    WHEN stamina_reset_date IS DISTINCT FROM CURRENT_DATE
                        THEN (CASE WHEN premium_until >= CURRENT_DATE THEN 8 ELSE 4 END) - 1
                    ELSE stamina_remaining - 1
                END,
                stamina_reset_date = CURRENT_DATE,
                last_stamina_token = %(token)s,
                last_stamina_token_at = NOW()
            WHERE user_id = %(user_id)s
              AND (stamina_reset_date IS DISTINCT FROM CURRENT_DATE OR stamina_remaining > 0)
            RETURNING stamina_remaining, (CASE WHEN premium_until >= CURRENT_DATE THEN 8 ELSE 4 END) AS max_stamina
        """, {"user_id": user_id, "token": token}, fetchone=True)

        if row is None:
            fallback = await db_query("""
                SELECT stamina_remaining, (CASE WHEN premium_until >= CURRENT_DATE THEN 8 ELSE 4 END)
                FROM users WHERE user_id = %s
            """, (user_id,), fetchone=True)
            remaining, max_stamina = fallback if fallback else (0, 4)
            return web.json_response({"success": False, "remaining": remaining, "max": max_stamina})

        remaining, max_stamina = row
        return web.json_response({"success": True, "remaining": remaining, "max": max_stamina})
    except Exception as e:
        print(f"Ошибка в api_consume_stamina: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_buy_potion(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()

        row = await db_query("""
            SELECT spendable_points, potion_purchases_in_week, potion_week_anchor_date,
                   stamina_remaining, stamina_reset_date, premium_until, CURRENT_DATE
            FROM users WHERE user_id = %s
        """, (user_id,), fetchone=True)
        if row is None:
            return web.json_response({"error": "Пользователь не найден"}, status=404)

        spendable, purchases_in_week, anchor_date, stamina_remaining, stamina_reset_date, premium_until, today = row

        if anchor_date is None or (today - anchor_date).days >= 7:
            purchases_in_week = 0
            anchor_date = today

        if purchases_in_week >= MAX_POTIONS_PER_WEEK:
            return web.json_response({"success": False, "error": f"Лимит {MAX_POTIONS_PER_WEEK} зелья в неделю уже использован"}, status=400)

        if (spendable or 0) < POTION_PRICE_ESSENCE:
            return web.json_response({"success": False, "error": "Недостаточно очков"}, status=400)

        premium_active = premium_until is not None and premium_until >= today
        stamina_max = 8 if premium_active else 4
        current_stamina = stamina_remaining if stamina_reset_date == today else stamina_max
        new_stamina = current_stamina + 1

        await db_query("""
            UPDATE users SET
                spendable_points = spendable_points - %(price)s,
                potion_purchases_in_week = %(new_count)s,
                potion_week_anchor_date = %(anchor)s,
                stamina_remaining = %(new_stamina)s,
                stamina_reset_date = CURRENT_DATE
            WHERE user_id = %(user_id)s
        """, {
            "price": POTION_PRICE_ESSENCE, "new_count": purchases_in_week + 1, "anchor": anchor_date,
            "new_stamina": new_stamina, "user_id": user_id
        })

        return web.json_response({
            "success": True, "stamina_remaining": new_stamina, "stamina_max": stamina_max,
            "spendable_points": (spendable or 0) - POTION_PRICE_ESSENCE,
            "potions_left_this_week": max(0, MAX_POTIONS_PER_WEEK - (purchases_in_week + 1)),
        })
    except Exception as e:
        print(f"Ошибка в api_buy_potion: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_daily_quests(request):
    user_id = await resolve_user_id(request)
    if not user_id:
        return unauthorized()

    row = await db_query("""
        INSERT INTO users (user_id) VALUES (%(user_id)s)
        ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING daily_quest_date, daily_quest_enemies, daily_quest_reps, daily_quest_hold_seconds,
                  daily_quest_dungeons, daily_quest_claimed, daily_quest_chest_claimed, CURRENT_DATE
    """, {"user_id": user_id}, fetchone=True)

    (quest_date, enemies, reps, hold_seconds, dungeons, claimed_raw, chest_claimed, today) = row

    is_today = (quest_date == today)
    progress = {
        "enemies": enemies if is_today else 0,
        "reps": reps if is_today else 0,
        "hold_seconds": hold_seconds if is_today else 0,
        "dungeons": dungeons if is_today else 0,
    }
    claimed_list = (claimed_raw or "").split(",") if is_today else []
    chest_claimed = chest_claimed if is_today else False

    quests = []
    for qid in ["enemies", "reps", "hold_seconds", "dungeons"]:
        target = QUEST_TARGETS[qid]
        current = progress[qid]
        quests.append({
            "id": qid, "title": QUEST_TITLES[qid],
            "progress": min(current, target), "target": target,
            "completed": current >= target,
            "claimed": qid in claimed_list,
            "reward_xp": QUEST_REWARD_XP, "reward_essence": QUEST_REWARD_ESSENCE,
        })

    all_claimed = all(q["claimed"] for q in quests)
    return web.json_response({
        "quests": quests,
        "chest": {
            "available": all_claimed and not chest_claimed,
            "claimed": chest_claimed,
            "reward_xp": CHEST_REWARD_XP, "reward_essence": CHEST_REWARD_ESSENCE,
        }
    })


async def api_claim_quest(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        quest_id = data.get("quest_id")
        if quest_id not in list(QUEST_TARGETS.keys()) + ["chest"]:
            return web.json_response({"error": "Некорректное задание"}, status=400)

        row = await db_query("""
            SELECT daily_quest_date, daily_quest_enemies, daily_quest_reps, daily_quest_hold_seconds,
                   daily_quest_dungeons, daily_quest_claimed, daily_quest_chest_claimed, CURRENT_DATE
            FROM users WHERE user_id = %s
        """, (user_id,), fetchone=True)
        if row is None:
            return web.json_response({"error": "Пользователь не найден"}, status=404)

        (quest_date, enemies, reps, hold_seconds, dungeons, claimed_raw, chest_claimed, today) = row
        is_today = (quest_date == today)
        if not is_today:
            return web.json_response({"success": False, "error": "Сегодняшних заданий ещё нет прогресса"}, status=400)

        claimed_list = [x for x in (claimed_raw or "").split(",") if x]

        if quest_id == "chest":
            if len(claimed_list) < 4:
                return web.json_response({"success": False, "error": "Сначала забери награды за все 4 задания"}, status=400)
            if chest_claimed:
                return web.json_response({"success": False, "error": "Сундук уже открыт"}, status=400)
            await db_query("""
                UPDATE users SET
                    total_points = total_points + %(xp)s,
                    spendable_points = COALESCE(spendable_points, 0) + %(essence)s,
                    daily_quest_chest_claimed = TRUE
                WHERE user_id = %(user_id)s
            """, {"xp": CHEST_REWARD_XP, "essence": CHEST_REWARD_ESSENCE, "user_id": user_id})
            return web.json_response({"success": True, "reward_xp": CHEST_REWARD_XP, "reward_essence": CHEST_REWARD_ESSENCE})

        if quest_id in claimed_list:
            return web.json_response({"success": False, "error": "Уже получено"}, status=400)

        progress_map = {"enemies": enemies, "reps": reps, "hold_seconds": hold_seconds, "dungeons": dungeons}
        if progress_map[quest_id] < QUEST_TARGETS[quest_id]:
            return web.json_response({"success": False, "error": "Задание ещё не выполнено"}, status=400)

        claimed_list.append(quest_id)
        new_claimed = ",".join(claimed_list)

        await db_query("""
            UPDATE users SET
                total_points = total_points + %(xp)s,
                spendable_points = COALESCE(spendable_points, 0) + %(essence)s,
                daily_quest_claimed = %(claimed)s
            WHERE user_id = %(user_id)s
        """, {"xp": QUEST_REWARD_XP, "essence": QUEST_REWARD_ESSENCE, "claimed": new_claimed, "user_id": user_id})

        return web.json_response({"success": True, "reward_xp": QUEST_REWARD_XP, "reward_essence": QUEST_REWARD_ESSENCE})
    except Exception as e:
        print(f"Ошибка в api_claim_quest: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_dungeon_info(request):
    activity = request.query.get("activity")
    if activity not in DUNGEON_COL:
        return web.json_response({"error": "Некорректные параметры"}, status=400)
    user_id = await resolve_user_id(request)
    if not user_id:
        return unauthorized()

    dungeon_col = DUNGEON_COL[activity]
    purchased_col = PURCHASED_COL[activity]

    row = await db_query(f"""
        INSERT INTO users (user_id) VALUES (%(user_id)s)
        ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING {dungeon_col}, {purchased_col}, premium_until, CURRENT_DATE
    """, {"user_id": user_id}, fetchone=True)
    current_dungeon, purchased_raw, premium_until, today = row

    premium_active = premium_until is not None and premium_until >= today
    purchased_pairs = [int(x) for x in (purchased_raw or "").split(",") if x.strip().isdigit()]

    requested = request.query.get("dungeon")
    dungeon_n = int(requested) if requested and requested.isdigit() else current_dungeon
    dungeon_n = min(max(dungeon_n, 1), min(current_dungeon + 3, MAX_DUNGEON))

    dungeon_data = generate_dungeon(activity, dungeon_n)
    is_replay = dungeon_n < current_dungeon
    pair_start = pair_start_for_zone(dungeon_n)

    story_still_free = today <= FREE_STORY_UNTIL
    requires_payment = (
        pair_start is not None and not premium_active and pair_start not in purchased_pairs
        and not story_still_free
    )

    return web.json_response({
        "current_dungeon": current_dungeon, "max_dungeon": MAX_DUNGEON, "requested_dungeon": dungeon_n,
        "is_replay": is_replay, "is_boss": dungeon_data["is_boss"],
        "enemies": dungeon_data["enemies"] if not requires_payment else [],
        "xp_reward": dungeon_data["xp_reward"] if not is_replay else dungeon_data["xp_reward"] // 2,
        "purchased_pairs": purchased_pairs, "premium_active": premium_active,
        "paid_pair_starts": PAID_PAIR_STARTS, "pair_price_stars": PAIR_PRICE_STARS,
        "requires_payment": requires_payment, "pair_start": pair_start,
        "story_free_until": FREE_STORY_UNTIL.isoformat(),
    })


async def api_dungeon_complete(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        activity = data.get("activity")
        dungeon_n = data.get("dungeon")
        if activity not in DUNGEON_COL or not dungeon_n:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        dungeon_n = int(dungeon_n)
        dungeon_col = DUNGEON_COL[activity]

        row = await db_query(f"""
            INSERT INTO users (user_id) VALUES (%(user_id)s)
            ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
            RETURNING {dungeon_col}
        """, {"user_id": user_id}, fetchone=True)
        (current_dungeon,) = row

        dungeon_data = generate_dungeon(activity, dungeon_n)
        is_replay = dungeon_n < current_dungeon
        xp_reward = dungeon_data["xp_reward"] if not is_replay else dungeon_data["xp_reward"] // 2
        enemy_count = len(dungeon_data["enemies"])

        new_dungeon = current_dungeon
        if not is_replay and current_dungeon < MAX_DUNGEON:
            new_dungeon = current_dungeon + 1

        points_row = await db_query(f"""
            UPDATE users
            SET total_points = total_points + %(xp)s,
                spendable_points = COALESCE(spendable_points, 0) + %(xp)s,
                {dungeon_col} = %(new_dungeon)s,
                {QUEST_RESET_FIELDS_SQL.replace(
                    "daily_quest_enemies = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_enemies, 0) END,",
                    "daily_quest_enemies = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN %(enemies)s ELSE COALESCE(users.daily_quest_enemies, 0) + %(enemies)s END,"
                ).replace(
                    "daily_quest_dungeons = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_dungeons, 0) END,",
                    "daily_quest_dungeons = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 1 ELSE COALESCE(users.daily_quest_dungeons, 0) + 1 END,"
                )}
            WHERE user_id = %(user_id)s
            RETURNING total_points - %(xp)s AS points_before, total_points AS points_after
        """, {"xp": xp_reward, "new_dungeon": new_dungeon, "user_id": user_id, "enemies": enemy_count}, fetchone=True)

        points_before, points_after = points_row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({
            "success": True, "xp_earned": xp_reward, "was_replay": is_replay,
            "new_current_dungeon": new_dungeon,
            "level_up": level_after != level_before,
            "new_level": level_after if level_after != level_before else None
        })
    except Exception as e:
        print(f"Ошибка в api_dungeon_complete: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_arena_info(request):
    activity = request.query.get("activity")
    if activity not in ARENA_COL:
        return web.json_response({"error": "Некорректные параметры"}, status=400)
    user_id = await resolve_user_id(request)
    if not user_id:
        return unauthorized()

    col = ARENA_COL[activity]
    row = await db_query(f"""
        INSERT INTO users (user_id) VALUES (%(user_id)s)
        ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
        RETURNING {col}
    """, {"user_id": user_id}, fetchone=True)
    (current_wave,) = row
    current_wave = current_wave or 1

    wave_data = generate_arena_wave(activity, current_wave)
    return web.json_response(wave_data)


async def api_arena_wave_complete(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        activity = data.get("activity")
        wave_n = data.get("wave")
        if activity not in ARENA_COL or not wave_n:
            return web.json_response({"error": "Некорректные данные"}, status=400)
        wave_n = int(wave_n)
        col = ARENA_COL[activity]

        wave_data = generate_arena_wave(activity, wave_n)
        xp_reward = wave_data["xp_reward"]
        enemy_count = len(wave_data["enemies"])

        row = await db_query(f"""
            INSERT INTO users (user_id) VALUES (%(user_id)s)
            ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
            RETURNING {col}
        """, {"user_id": user_id}, fetchone=True)
        (current_wave,) = row
        current_wave = current_wave or 1

        new_wave = current_wave
        if wave_n == current_wave:
            new_wave = current_wave + 1

        points_row = await db_query(f"""
            UPDATE users
            SET total_points = total_points + %(xp)s,
                spendable_points = COALESCE(spendable_points, 0) + %(xp)s,
                {col} = %(new_wave)s,
                {QUEST_RESET_FIELDS_SQL.replace(
                    "daily_quest_enemies = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN 0 ELSE COALESCE(users.daily_quest_enemies, 0) END,",
                    "daily_quest_enemies = CASE WHEN users.daily_quest_date IS DISTINCT FROM CURRENT_DATE THEN %(enemies)s ELSE COALESCE(users.daily_quest_enemies, 0) + %(enemies)s END,"
                )}
            WHERE user_id = %(user_id)s
            RETURNING total_points - %(xp)s AS points_before, total_points AS points_after
        """, {"xp": xp_reward, "new_wave": new_wave, "user_id": user_id, "enemies": enemy_count}, fetchone=True)

        points_before, points_after = points_row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({
            "success": True, "xp_earned": xp_reward, "new_wave": new_wave,
            "level_up": level_after != level_before,
            "new_level": level_after if level_after != level_before else None
        })
    except Exception as e:
        print(f"Ошибка в api_arena_wave_complete: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_create_zone_invoice(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        activity = data.get("activity")
        pair_start = data.get("pair_start")
        if activity not in PURCHASED_COL or pair_start not in PAID_PAIR_STARTS:
            return web.json_response({"error": "Некорректные данные"}, status=400)

        if date.today() <= FREE_STORY_UNTIL:
            return web.json_response({"error": "Сейчас всё бесплатно — платный доступ откроется после 10 сентября"}, status=400)

        purchased_col = PURCHASED_COL[activity]

        row = await db_query(f"SELECT {purchased_col} FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        purchased_raw = row[0] if row else ""
        purchased_pairs = [int(x) for x in (purchased_raw or "").split(",") if x.strip().isdigit()]

        idx = PAID_PAIR_STARTS.index(pair_start)
        for earlier in PAID_PAIR_STARTS[:idx]:
            if earlier not in purchased_pairs:
                return web.json_response({"error": "Сначала купите предыдущее подземелье"}, status=400)
        if pair_start in purchased_pairs:
            return web.json_response({"error": "Уже куплено"}, status=400)

        zone = next(z for z in ZONES_META if z["n"] == pair_start)
        title = f"{zone['name']} — доступ"
        payload = f"zone:{activity}:{pair_start}:{user_id}"

        invoice_url = await bot.create_invoice_link(
            title=title,
            description=f"Открывает «{zone['name']}» и следующий уровень (босс) для дисциплины «{ACTIVITY_NAMES[activity]}»",
            payload=payload, provider_token="", currency="XTR",
            prices=[LabeledPrice(label=title, amount=PAIR_PRICE_STARS)],
        )
        return web.json_response({"url": invoice_url})
    except Exception as e:
        print(f"Ошибка в api_create_zone_invoice: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_create_premium_invoice(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        invoice_url = await bot.create_invoice_link(
            title="Премиум на 30 дней",
            description="8 подходов в день вместо 4 и открытый доступ ко всем платным подземельям на 30 дней.",
            payload=f"premium:{user_id}", provider_token="", currency="XTR",
            prices=[LabeledPrice(label="Премиум 30 дней", amount=PREMIUM_PRICE_STARS)],
        )
        return web.json_response({"url": invoice_url})
    except Exception as e:
        print(f"Ошибка в api_create_premium_invoice: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_create_support_invoice(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        amount = max(1, int(data.get("amount", 150)))
        invoice_url = await bot.create_invoice_link(
            title="Поддержать PushUp Hero",
            description="Спасибо, что помогаешь проекту развиваться! Это разовый добровольный донат, ни на что игровое не влияет.",
            payload=f"support:{user_id}", provider_token="", currency="XTR",
            prices=[LabeledPrice(label="Поддержка проекта", amount=amount)],
        )
        return web.json_response({"url": invoice_url})
    except Exception as e:
        print(f"Ошибка в api_create_support_invoice: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload
    user_id = message.from_user.id
    try:
        if payload.startswith("premium:"):
            await db_query("""
                UPDATE users SET premium_until = GREATEST(COALESCE(premium_until, CURRENT_DATE - 1), CURRENT_DATE) + INTERVAL '30 days'
                WHERE user_id = %s
            """, (user_id,))
            await message.answer("✅ Премиум активирован на 30 дней! Спасибо за поддержку 🙏")
        elif payload.startswith("support:"):
            await message.answer("🤍 Спасибо за поддержку! Это очень много значит для развития проекта.")
        elif payload.startswith("zone:"):
            parts = payload.split(":")
            activity, pair_start = parts[1], parts[2]
            purchased_col = PURCHASED_COL.get(activity)
            if purchased_col:
                await db_query(f"""
                    UPDATE users SET {purchased_col} = TRIM(BOTH ',' FROM (COALESCE({purchased_col}, '') || ',' || %s))
                    WHERE user_id = %s
                """, (pair_start, user_id))
                await message.answer("✅ Подземелье открыто! Возвращайся в приложение.")
    except Exception as e:
        print(f"Ошибка обработки платежа: {e}")


async def api_health(request):
    try:
        await db_query("SELECT 1")
        db_status = "ok"
    except Exception as e:
        print(f"Health check: база не отвечает: {e}")
        db_status = "error"
    return web.json_response({"status": "ok", "db": db_status})


async def api_reset_progress(request):
    try:
        data = await request.json()
        user_id = await resolve_user_id(request, data)
        if not user_id:
            return unauthorized()
        await db_query("""
            UPDATE users SET
                total_points = 0, spendable_points = 0, total_pushups = 0, total_plank_seconds = 0,
                total_squats = 0, total_wallsit_seconds = 0,
                daily_pushups = 0, daily_plank_seconds = 0, daily_squats = 0, daily_wallsit_seconds = 0,
                last_pushup_date = NULL, last_plank_date = NULL, last_squat_date = NULL, last_wallsit_date = NULL,
                pushup_streak = 0, plank_streak = 0, squat_streak = 0, wallsit_streak = 0,
                pushup_best_streak = 0, plank_best_streak = 0, squat_best_streak = 0, wallsit_best_streak = 0,
                pushup_dungeon = 1, plank_dungeon = 1, squat_dungeon = 1, wallsit_dungeon = 1,
                pushup_achv_level = 0, squat_achv_level = 0,
                pushup_purchased_pairs = '', plank_purchased_pairs = '', squat_purchased_pairs = '', wallsit_purchased_pairs = '',
                pushup_story_seen = '', potion_purchases_in_week = 0, potion_week_anchor_date = NULL,
                daily_quest_date = NULL, daily_quest_enemies = 0, daily_quest_reps = 0, daily_quest_hold_seconds = 0,
                daily_quest_dungeons = 0, daily_quest_claimed = '', daily_quest_chest_claimed = FALSE,
                pushup_arena_wave = 1, plank_arena_wave = 1, squat_arena_wave = 1, wallsit_arena_wave = 1
            WHERE user_id = %s
        """, (user_id,))
        return web.json_response({"success": True})
    except Exception as e:
        print(f"Ошибка в api_reset_progress: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)


def main():
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/", api_health)
    app.router.add_get("/api/user_status", api_user_status)
    app.router.add_post("/api/register_nickname", api_register_nickname)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_post("/api/set_story_enabled", api_set_story_enabled)
    app.router.add_get("/api/story_meta", api_story_meta)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_post("/api/save_pushups", api_save_pushups)
    app.router.add_post("/api/save_plank", api_save_plank)
    app.router.add_post("/api/save_squats", api_save_squats)
    app.router.add_post("/api/save_wallsit", api_save_wallsit)
    app.router.add_get("/api/dungeon_info", api_dungeon_info)
    app.router.add_post("/api/dungeon_complete", api_dungeon_complete)
    app.router.add_get("/api/arena_info", api_arena_info)
    app.router.add_post("/api/arena_wave_complete", api_arena_wave_complete)
    app.router.add_post("/api/reset_progress", api_reset_progress)
    app.router.add_get("/api/stamina", api_get_stamina)
    app.router.add_post("/api/consume_stamina", api_consume_stamina)
    app.router.add_post("/api/buy_potion", api_buy_potion)
    app.router.add_get("/api/daily_quests", api_daily_quests)
    app.router.add_post("/api/claim_quest", api_claim_quest)
    app.router.add_post("/api/create_zone_invoice", api_create_zone_invoice)
    app.router.add_post("/api/create_premium_invoice", api_create_premium_invoice)
    app.router.add_post("/api/create_support_invoice", api_create_support_invoice)

    dp.startup.register(on_startup)
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
