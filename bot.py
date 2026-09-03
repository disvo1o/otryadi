import os
import asyncio
import random

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import IntegrityError

from database import Base, Squad, Participant


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")


# Railway PostgreSQL обычно дает:
# postgresql://...
#
# Для SQLAlchemy Async нам нужен:
# postgresql+asyncpg://...
#
# Поэтому автоматически исправляем адрес.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1
    )


# ============================================================
# DATABASE
# ============================================================

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


# ============================================================
# SQUADS
# ============================================================

SQUADS = [
    {
        "name": "Squad 1",
        "link": os.getenv("SQUAD_1_LINK", ""),
    },
    {
        "name": "Squad 2",
        "link": os.getenv("SQUAD_2_LINK", ""),
    },
    {
        "name": "Squad 3",
        "link": os.getenv("SQUAD_3_LINK", ""),
    },
    {
        "name": "Squad 4",
        "link": os.getenv("SQUAD_4_LINK", ""),
    },
    {
        "name": "Squad 5",
        "link": os.getenv("SQUAD_5_LINK", ""),
    },
    {
        "name": "Squad 6",
        "link": os.getenv("SQUAD_6_LINK", ""),
    },
    {
        "name": "Squad 7",
        "link": os.getenv("SQUAD_7_LINK", ""),
    },
    {
        "name": "Squad 8",
        "link": os.getenv("SQUAD_8_LINK", ""),
    },
    {
        "name": "Squad 9",
        "link": os.getenv("SQUAD_9_LINK", ""),
    },
    {
        "name": "Squad 10",
        "link": os.getenv("SQUAD_10_LINK", ""),
    },
]


# ============================================================
# BOT
# ============================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

async def init_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        result = await session.execute(
            select(Squad).order_by(Squad.id)
        )

        squads = result.scalars().all()

        # Если squads еще нет — создаем 10 штук
        if not squads:
            for squad_data in SQUADS:
                squad = Squad(
                    name=squad_data["name"],
                    link=squad_data["link"],
                    members_count=0,
                )
                session.add(squad)

            await session.commit()

        else:
            # Обновляем названия и ссылки,
            # но НЕ трогаем количество участников.
            for index, squad in enumerate(squads[:10]):
                squad.name = SQUADS[index]["name"]
                squad.link = SQUADS[index]["link"]

            await session.commit()


# ============================================================
# КНОПКА
# ============================================================

def get_squad_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎲 Получить свою команду",
                    callback_data="get_squad"
                )
            ]
        ]
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer(
        "Привет! 👋\n\n"
        "Нажми кнопку ниже, чтобы получить свою команду.\n\n"
        "⚠️ После назначения изменить команду будет нельзя.",
        reply_markup=get_squad_keyboard(),
    )


# ============================================================
# ASSIGN SQUAD
# ============================================================

async def assign_squad(message: Message):
    telegram_id = message.from_user.id

    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""
    username = message.from_user.username or ""

    async with SessionLocal() as session:

        # ----------------------------------------------------
        # Проверяем, есть ли пользователь уже в базе
        # ----------------------------------------------------

        existing_result = await session.execute(
            select(Participant).where(
                Participant.telegram_id == telegram_id
            )
        )

        existing = existing_result.scalar_one_or_none()

        if existing:
            squad_result = await session.execute(
                select(Squad).where(
                    Squad.id == existing.squad_id
                )
            )

            squad = squad_result.scalar_one_or_none()

            if squad:
                text = (
                    f"✅ Ты уже получил команду:\n\n"
                    f"🏆 <b>{squad.name}</b>\n\n"
                )

                if squad.link:
                    text += f"🔗 <a href=\"{squad.link}\">Войти в команду</a>\n\n"

                text += "Повторное нажатие не изменит твою команду."

                await message.answer(
                    text,
                    parse_mode="HTML",
                )
                return

        # ----------------------------------------------------
        # Блокируем squads на время выбора
        #
        # Это защищает от ситуации, когда два человека
        # одновременно получают одно и то же последнее место.
        # ----------------------------------------------------

        result = await session.execute(
            select(Squad)
            .order_by(Squad.id)
            .with_for_update()
        )

        squads = result.scalars().all()

        # ----------------------------------------------------
        # Создаем список свободных "слотов".
        #
        # Например:
        #
        # Squad 1: 5 мест
        # Squad 2: 10 мест
        #
        # Тогда Squad 2 будет встречаться в списке
        # в 2 раза чаще.
        #
        # Таким образом выбор полностью случайный,
        # но команда никогда не превысит 20 человек.
        # ----------------------------------------------------

        free_slots = []

        for squad in squads:
            free_places = 20 - squad.members_count

            if free_places > 0:
                for _ in range(free_places):
                    free_slots.append(squad)

        # ----------------------------------------------------
        # Все места закончились
        # ----------------------------------------------------

        if not free_slots:
            await message.answer(
                "❌ Все команды уже заполнены."
            )
            return

        # ----------------------------------------------------
        # Случайно выбираем свободное место
        # ----------------------------------------------------

        selected_squad = random.choice(free_slots)

        # ----------------------------------------------------
        # Создаем участника
        # ----------------------------------------------------

        participant = Participant(
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
            squad_id=selected_squad.id,
        )

        session.add(participant)

        selected_squad.members_count += 1

        try:
            await session.commit()

        except IntegrityError:
            # На случай редкой гонки запросов:
            # если пользователь уже был записан,
            # просто откатываем текущую транзакцию.
            await session.rollback()

            existing_result = await session.execute(
                select(Participant).where(
                    Participant.telegram_id == telegram_id
                )
            )

            existing = existing_result.scalar_one_or_none()

            if existing:
                squad_result = await session.execute(
                    select(Squad).where(
                        Squad.id == existing.squad_id
                    )
                )

                squad = squad_result.scalar_one_or_none()

                if squad:
                    text = (
                        f"✅ Ты уже получил команду:\n\n"
                        f"🏆 <b>{squad.name}</b>\n\n"
                    )

                    if squad.link:
                        text += (
                            f"🔗 <a href=\"{squad.link}\">"
                            f"Войти в команду"
                            f"</a>\n\n"
                        )

                    text += "Повторное нажатие не изменит твою команду."

                    await message.answer(
                        text,
                        parse_mode="HTML",
                    )

            return

        # ----------------------------------------------------
        # Отправляем результат
        # ----------------------------------------------------

        text = (
            f"🎉 Твоя команда:\n\n"
            f"🏆 <b>{selected_squad.name}</b>\n\n"
        )

        if selected_squad.link:
            text += (
                f"🔗 <a href=\"{selected_squad.link}\">"
                f"Перейти в команду"
                f"</a>\n\n"
            )

        text += (
            "Твоя команда закреплена за тобой.\n"
            "Повторное нажатие не изменит её."
        )

        await message.answer(
            text,
            parse_mode="HTML",
        )


# ============================================================
# КНОПКА "ПОЛУЧИТЬ КОМАНДУ"
# ============================================================

@dp.callback_query(F.data == "get_squad")
async def get_squad_callback(callback):
    await callback.answer()

    await assign_squad(callback.message)


# ============================================================
# КОМАНДА /TEAM
# ============================================================

@dp.message(Command("team"))
async def team_handler(message: Message):
    await assign_squad(message)


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(message: Message):
    if not ADMIN_TELEGRAM_ID:
        return False

    return str(message.from_user.id) == str(ADMIN_TELEGRAM_ID)


# ============================================================
# ADMIN STATS
# ============================================================

@dp.message(Command("admin"))
async def admin_handler(message: Message):

    if not is_admin(message):
        await message.answer(
            "⛔ У тебя нет доступа к этой команде."
        )
        return

    async with SessionLocal() as session:

        result = await session.execute(
            select(Squad).order_by(Squad.id)
        )

        squads = result.scalars().all()

        total = sum(
            squad.members_count
            for squad in squads
        )

        text = "📊 <b>Статистика команд</b>\n\n"

        for squad in squads:
            text += (
                f"🏆 {squad.name}: "
                f"<b>{squad.members_count}/20</b>\n"
            )

        text += f"\n👥 Всего участников: <b>{total}/200</b>"

        await message.answer(
            text,
            parse_mode="HTML",
        )


# ============================================================
# ADMIN RESET
# ============================================================

@dp.message(Command("reset"))
async def reset_handler(message: Message):

    if not is_admin(message):
        await message.answer(
            "⛔ У тебя нет доступа к этой команде."
        )
        return

    async with SessionLocal() as session:

        # Удаляем всех участников
        await session.execute(
            delete(Participant)
        )

        # Обнуляем количество людей
        result = await session.execute(
            select(Squad)
        )

        squads = result.scalars().all()

        for squad in squads:
            squad.members_count = 0

        await session.commit()

    await message.answer(
        "♻️ Все команды очищены.\n\n"
        "Теперь можно проводить новое распределение."
    )


# ============================================================
# HELP
# ============================================================

@dp.message(Command("help"))
async def help_handler(message: Message):

    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "/start — получить кнопку распределения\n"
        "/team — получить команду\n"
    )

    if is_admin(message):
        text += (
            "\n<b>Команды администратора:</b>\n"
            "/admin — статистика\n"
            "/reset — полностью очистить распределение\n"
        )

    await message.answer(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ЛЮБОЕ ДРУГОЕ СООБЩЕНИЕ
# ============================================================

@dp.message()
async def fallback_handler(message: Message):

    await message.answer(
        "Нажми кнопку ниже, чтобы получить свою команду.",
        reply_markup=get_squad_keyboard(),
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    print("Starting bot...")

    await init_database()

    print("Database initialized.")
    print("Bot is running.")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
