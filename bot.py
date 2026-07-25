import os
import psycopg2
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
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


def get_cursor():
    global db
    try:
        if db is None or db.closed:
            raise psycopg2.OperationalError("Соединение закрыто")
        cursor = db.cursor()
        cursor.execute("SELECT 1")
        return db.cursor()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        print("Переподключаемся к базе данных...")
        db = psycopg2.connect(DATABASE_URL)
        db.autocommit = True
        return db.cursor()


def init_db():
    cursor = get_cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        username TEXT,
        total_points INTEGER DEFAULT 0,
        total_pushups INTEGER DEFAULT 0
    )
    """)


init_db()


def ensure_user_exists(user_id, username=None):
    cursor = get_cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    if user is None:
        cursor.execute(
            "INSERT INTO users (user_id, username) VALUES (%s, %s)",
            (user_id, username)
        )
    elif username:
        cursor.execute(
            "UPDATE users SET username = %s WHERE user_id = %s",
            (username, user_id)
        )


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

    personal_url = f"{WEBAPP_URL}?user_id={user_id}"

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
        "Здесь твои отжимания превращаются в награды.\n\n"
        "📹 Выполняй отжимания перед камерой — бот автоматически засчитает повторения.\n"
        "🏆 Получай внутриигровую валюту и ценные награды.\n"
        "📈 Прокачивай своего персонажа, открывай новые уровни и достижения.\n"
        "🥇 Соревнуйся с другими игроками и поднимайся в таблице лидеров.",
        reply_markup=keyboard
    )


@dp.message(Command("profile"))
async def profile_handler(message: Message):
    user_id = message.from_user.id
    ensure_user_exists(user_id)

    cursor = get_cursor()
    cursor.execute("SELECT total_points, total_pushups FROM users WHERE user_id = %s", (user_id,))
    points, pushups = cursor.fetchone()

    level_title = get_level(points)
    next_threshold, next_title = get_next_level_info(points)

    text = (
        f"📊 Твой профиль\n\n"
        f"Звание: {level_title}\n"
        f"Очки: {points} ⭐\n"
        f"Всего отжиманий: {pushups} 💪\n"
    )

    if next_threshold is not None:
        remaining = next_threshold - points
        text += f"\nДо звания «{next_title}» осталось: {remaining} очков"
    else:
        text += "\nТы достиг максимального звания! 🎉"

    await message.answer(text)


async def api_profile(request):
    user_id = request.query.get("user_id")
    if not user_id or not user_id.isdigit():
        return web.json_response({"error": "user_id обязателен"}, status=400)

    user_id = int(user_id)

    try:
        ensure_user_exists(user_id)

        cursor = get_cursor()
        cursor.execute("SELECT total_points, total_pushups FROM users WHERE user_id = %s", (user_id,))
        points, pushups = cursor.fetchone()

        level_title = get_level(points)
        next_threshold, next_title = get_next_level_info(points)

        return web.json_response({
            "level": level_title,
            "points": points,
            "total_pushups": pushups,
            "next_level": next_title,
            "points_to_next_level": (next_threshold - points) if next_threshold else None
        })
    except Exception as e:
        print(f"Ошибка в api_profile: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_leaderboard(request):
    try:
        cursor = get_cursor()
        cursor.execute(
            "SELECT username, total_points FROM users ORDER BY total_points DESC LIMIT 10"
        )
        rows = cursor.fetchall()

        leaderboard = [
            {"name": name or "Игрок", "points": points}
            for name, points in rows
        ]

        return web.json_response({"leaderboard": leaderboard})
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

        cursor = get_cursor()
        cursor.execute("SELECT total_points FROM users WHERE user_id = %s", (user_id,))
        (points_before,) = cursor.fetchone()
        level_before = get_level(points_before)

        cursor = get_cursor()
        cursor.execute(
            "UPDATE users SET total_points = total_points + %s, total_pushups = total_pushups + %s WHERE user_id = %s",
            (points_earned, count, user_id)
        )

        points_after = points_before + points_earned
        level_after = get_level(points_after)

        level_up = level_after != level_before

        try:
            text = (
                f"Подход завершён! Засчитано: {count} отжиманий 💪\n"
                f"Получено очков: +{points_earned} ⭐"
            )
            if level_up:
                text += f"\n\n🎉 Новое звание: {level_after}!"
            await bot.send_message(user_id, text)
        except Exception as notify_error:
            print(f"Не удалось отправить уведомление: {notify_error}")

        return web.json_response({
            "success": True,
            "points_earned": points_earned,
            "level_up": level_up,
            "new_level": level_after if level_up else None
        })

    except Exception as e:
        print(f"Ошибка в api_save_pushups: {e}")
        return web.json_response({"error": "Внутренняя ошибка сервера"}, status=500)


async def api_health(request):
    return web.json_response({"status": "ok"})


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
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_post("/api/save_pushups", api_save_pushups)

    dp.startup.register(on_startup)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
