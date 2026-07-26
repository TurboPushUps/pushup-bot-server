import os
import re
import psycopg2
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

DATABASE_URL = os.environ.get("DATABASE_URL")

WEBAPP_URL = "https://turbopushups.github.io/pushup-camera/index.html"

bot = Bot(token=TOKEN)
dp = Dispatcher()

db = None


def connect_db():
    global db
    print("Подключаемся к базе данных...")
    db = psycopg2.connect(DATABASE_URL, connect_timeout=10)
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
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            print(f"Проблема с соединением (попытка {attempt + 1}): {e}")
            try:
                connect_db()
            except Exception as reconnect_error:
                print(f"Не удалось переподключиться: {reconnect_error}")
                raise
    raise psycopg2.OperationalError("Не удалось выполнить запрос после переподключения")


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


init_db()


def ensure_user_exists(user_id, username=None):
    """Одним запросом создаёт пользователя, если его нет (или обновляет имя, если оно передано)."""
    if username:
        run_query(
            "INSERT INTO users (user_id, username) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username",
            (user_id, username)
        )
    else:
        run_query(
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


@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id
    username = message.from_user.first_name or message.from_user.username or "Игрок"
    ensure_user_exists(user_id, username)

    personal_url = f"{WEBAPP_URL}?user_id={user_id}&v=3"

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text="💪 Открыть Pushup Tracker",
                web_app=WebAppInfo(url=personal_url)
            )]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "💪 Добро пожаловать!\n\n"
        "Здесь твои тренировки превращаются в награды.\n\n"
        "📹 Выполняй отжимания или планку перед камерой — бот автоматически всё засчитает.\n"
        "🏆 Получай внутриигровую валюту и ценные награды.\n"
        "📈 Прокачивай своего персонажа, открывай новые уровни и достижения.\n"
        "🥇 Соревнуйся с другими игроками и поднимайся в таблице лидеров.",
        reply_markup=keyboard
    )


async def api_user_status(request):
    user_id = request.query.get("user_id")
    if not user_id or not user_id.isdigit():
        return web.json_response({"error": "user_id обязателен"}, status=400)

    user_id = int(user_id)
    ensure_user_exists(user_id)

    row = run_query("SELECT nickname FROM users WHERE user_id = %s", (user_id,), fetchone=True)
    nickname = row[0] if row else None

    return web.json_response({
        "has_nickname": nickname is not None,
        "nickname": nickname
    })


async def api_register_nickname(request):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        nickname = data.get("nickname", "")

        if not user_id:
            return web.json_response({"error": "user_id обязателен"}, status=400)

        user_id = int(user_id)

        valid, error = is_nickname_valid(nickname)
        if not valid:
            return web.json_response({"success": False, "error": error})

        run_query(
            "INSERT INTO users (user_id, nickname) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET nickname = EXCLUDED.nickname",
            (user_id, nickname.strip())
        )

        return web.json_response({"success": True, "nickname": nickname.strip()})

    except Exception as e:
        print(f"Ошибка в api_register_nickname: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_profile(request):
    user_id = request.query.get("user_id")
    if not user_id or not user_id.isdigit():
        return web.json_response({"error": "user_id обязателен"}, status=400)

    user_id = int(user_id)

    try:
        ensure_user_exists(user_id)

        row = run_query("""
            SELECT total_points, total_pushups, total_plank_seconds,
                   daily_pushups, last_pushup_date, pushup_streak,
                   daily_plank_seconds, last_plank_date, plank_streak,
                   CURRENT_DATE
            FROM users WHERE user_id = %s
        """, (user_id,), fetchone=True)

        (points, total_pushups, total_plank_seconds,
         daily_pushups, last_pushup_date, pushup_streak,
         daily_plank_seconds, last_plank_date, plank_streak,
         today) = row

        pushups_today = daily_pushups if last_pushup_date == today else 0
        plank_today = daily_plank_seconds if last_plank_date == today else 0

        pushup_streak_display = pushup_streak if last_pushup_date and (today - last_pushup_date).days <= 1 else 0
        plank_streak_display = plank_streak if last_plank_date and (today - last_plank_date).days <= 1 else 0

        level_title = get_level(points)
        next_threshold, next_title = get_next_level_info(points)

        return web.json_response({
            "level": level_title,
            "points": points,
            "next_level": next_title,
            "points_to_next_level": (next_threshold - points) if next_threshold else None,
            "pushup": {
                "today": pushups_today,
                "streak": pushup_streak_display,
                "total": total_pushups
            },
            "plank": {
                "today_seconds": plank_today,
                "streak": plank_streak_display,
                "total_seconds": total_plank_seconds
            }
        })
    except Exception as e:
        print(f"Ошибка в api_profile: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_leaderboard(request):
    activity = request.query.get("activity", "pushup")
    period = request.query.get("period", "total")

    try:
        if activity == "pushup":
            if period == "today":
                rows = run_query("""
                    SELECT COALESCE(nickname, username, 'Игрок') AS name,
                           CASE WHEN last_pushup_date = CURRENT_DATE THEN daily_pushups ELSE 0 END AS value
                    FROM users
                    WHERE (last_pushup_date = CURRENT_DATE AND daily_pushups > 0)
                    ORDER BY value DESC LIMIT 10
                """, fetchall=True)
            else:
                rows = run_query("""
                    SELECT COALESCE(nickname, username, 'Игрок') AS name, total_pushups AS value
                    FROM users WHERE total_pushups > 0
                    ORDER BY value DESC LIMIT 10
                """, fetchall=True)
        else:
            if period == "today":
                rows = run_query("""
                    SELECT COALESCE(nickname, username, 'Игрок') AS name,
                           CASE WHEN last_plank_date = CURRENT_DATE THEN daily_plank_seconds ELSE 0 END AS value
                    FROM users
                    WHERE (last_plank_date = CURRENT_DATE AND daily_plank_seconds > 0)
                    ORDER BY value DESC LIMIT 10
                """, fetchall=True)
            else:
                rows = run_query("""
                    SELECT COALESCE(nickname, username, 'Игрок') AS name, total_plank_seconds AS value
                    FROM users WHERE total_plank_seconds > 0
                    ORDER BY value DESC LIMIT 10
                """, fetchall=True)

        leaderboard = [{"name": name, "value": value} for name, value in rows]

        return web.json_response({"leaderboard": leaderboard, "activity": activity, "period": period})
    except Exception as e:
        print(f"Ошибка в api_leaderboard: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_save_pushups(request):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        count = data.get("count")

        if not user_id or not count or count <= 0:
            return web.json_response({"error": "Некорректные данные"}, status=400)

        user_id = int(user_id)
        count = int(count)
        points_earned = count * 10

        ensure_user_exists(user_id)

        # ОДНИМ запросом: обновляем очки, отжимания, дневной счётчик и стрик,
        # и сразу получаем очки "до" и "после" через RETURNING
        row = run_query("""
            UPDATE users
            SET
                total_points = total_points + %(points)s,
                total_pushups = total_pushups + %(count)s,
                daily_pushups = CASE
                    WHEN last_pushup_date = CURRENT_DATE THEN daily_pushups + %(count)s
                    ELSE %(count)s
                END,
                pushup_streak = CASE
                    WHEN last_pushup_date = CURRENT_DATE THEN COALESCE(pushup_streak, 1)
                    WHEN last_pushup_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(pushup_streak, 0) + 1
                    ELSE 1
                END,
                last_pushup_date = CURRENT_DATE
            WHERE user_id = %(user_id)s
            RETURNING total_points - %(points)s AS points_before, total_points AS points_after
        """, {"points": points_earned, "count": count, "user_id": user_id}, fetchone=True)

        points_before, points_after = row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({
            "success": True,
            "points_earned": points_earned,
            "level_up": level_after != level_before,
            "new_level": level_after if level_after != level_before else None
        })

    except Exception as e:
        print(f"Ошибка в api_save_pushups: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_save_plank(request):
    try:
        data = await request.json()
        user_id = data.get("user_id")
        seconds = data.get("seconds")

        if not user_id or not seconds or seconds <= 0:
            return web.json_response({"error": "Некорректные данные"}, status=400)

        user_id = int(user_id)
        seconds = int(seconds)
        points_earned = seconds * 2

        ensure_user_exists(user_id)

        row = run_query("""
            UPDATE users
            SET
                total_points = total_points + %(points)s,
                total_plank_seconds = total_plank_seconds + %(seconds)s,
                daily_plank_seconds = CASE
                    WHEN last_plank_date = CURRENT_DATE THEN daily_plank_seconds + %(seconds)s
                    ELSE %(seconds)s
                END,
                plank_streak = CASE
                    WHEN last_plank_date = CURRENT_DATE THEN COALESCE(plank_streak, 1)
                    WHEN last_plank_date = CURRENT_DATE - INTERVAL '1 day' THEN COALESCE(plank_streak, 0) + 1
                    ELSE 1
                END,
                last_plank_date = CURRENT_DATE
            WHERE user_id = %(user_id)s
            RETURNING total_points - %(points)s AS points_before, total_points AS points_after
        """, {"points": points_earned, "seconds": seconds, "user_id": user_id}, fetchone=True)

        points_before, points_after = row
        level_before = get_level(points_before)
        level_after = get_level(points_after)

        return web.json_response({
            "success": True,
            "points_earned": points_earned,
            "level_up": level_after != level_before,
            "new_level": level_after if level_after != level_before else None
        })

    except Exception as e:
        print(f"Ошибка в api_save_plank: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_health(request):
    try:
        run_query("SELECT 1")
        db_status = "ok"
    except Exception as e:
        print(f"Health check: база не отвечает: {e}")
        db_status = "error"

    return web.json_response({"status": "ok", "db": db_status})


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
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_post("/api/save_pushups", api_save_pushups)
    app.router.add_post("/api/save_plank", api_save_plank)

    dp.startup.register(on_startup)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
