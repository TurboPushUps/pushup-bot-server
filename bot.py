import os
import psycopg2
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ===== НАСТРОЙКИ =====

TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

DATABASE_URL = os.environ.get("DATABASE_URL")

WEBAPP_URL = "https://turbopushups.github.io/pushup-camera/index.html"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== БАЗА ДАННЫХ (PostgreSQL) =====

db = psycopg2.connect(DATABASE_URL)
db.autocommit = True
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    total_points INTEGER DEFAULT 0,
    total_pushups INTEGER DEFAULT 0
)
""")


def ensure_user_exists(user_id, username=None):
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


# ===== УРОВНИ =====

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


# ===== ОБРАБОТЧИКИ БОТА =====

@dp.message(CommandStart())
async def start_handler(message: Message):
    user_id = message.from_user.id
    username = message.from_user.first_name or message.from_user.username or "Игрок"
    ensure_user_exists(user_id, username)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💪 Открыть Pushup Tracker",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]
    ])

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


@dp.message(F.web_app_data)
async def webapp_data_handler(message: Message):
    user_id = message.from_user.id
    raw_data = message.web_app_data.data

    if not raw_data.isdigit():
        await message.answer("Не удалось распознать результат подхода 😕")
        return

    count = int(raw_data)

    if count == 0:
        await message.answer("Подход завершён, но отжиманий не засчитано 🤔")
        return

    points_earned = count * 10

    username = message.from_user.first_name or message.from_user.username or "Игрок"
    ensure_user_exists(user_id, username)

    cursor.execute("SELECT total_points FROM users WHERE user_id = %s", (user_id,))
    (points_before,) = cursor.fetchone()
    level_before = get_level(points_before)

    cursor.execute(
        "UPDATE users SET total_points = total_points + %s, total_pushups = total_pushups + %s WHERE user_id = %s",
        (points_earned, count, user_id)
    )

    points_after = points_before + points_earned
    level_after = get_level(points_after)

    text = (
        f"Подход завершён! Засчитано: {count} отжиманий 💪\n"
        f"Получено очков: +{points_earned} ⭐"
    )

    if level_after != level_before:
        text += f"\n\n🎉 Новое звание: {level_after}!"

    await message.answer(text)


# ===== API ДЛЯ САЙТА =====

async def api_profile(request):
    user_id = request.query.get("user_id")
    if not user_id or not user_id.isdigit():
        return web.json_response({"error": "user_id обязателен"}, status=400)

    user_id = int(user_id)
    ensure_user_exists(user_id)

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


async def api_leaderboard(request):
    cursor.execute(
        "SELECT username, total_points FROM users ORDER BY total_points DESC LIMIT 10"
    )
    rows = cursor.fetchall()

    leaderboard = [
        {"name": name or "Игрок", "points": points}
        for name, points in rows
    ]

    return web.json_response({"leaderboard": leaderboard})


async def api_health(request):
    return web.json_response({"status": "ok"})


# ===== CORS =====

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


# ===== ЗАПУСК СЕРВЕРА =====

async def on_startup(bot: Bot):
    await bot.set_webhook(WEBHOOK_URL)


def main():
    app = web.Application(middlewares=[cors_middleware])

    app.router.add_get("/", api_health)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/leaderboard", api_leaderboard)

    dp.startup.register(on_startup)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
