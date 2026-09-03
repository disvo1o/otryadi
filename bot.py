import asyncio
import logging
import os
import random

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database import Base, Participant, Squad


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")


if not BOT_TOKEN:
    raise RuntimeError(
        "Не указана переменная BOT_TOKEN"
    )


if not DATABASE_URL:
    raise RuntimeError(
        "Не указана переменная DATABASE_URL"
    )


if not ADMIN_TELEGRAM_ID:
    raise RuntimeError(
        "Не указана переменная ADMIN_TELEGRAM_ID"
    )


ADMIN_TELEGRAM_ID = int(ADMIN_TELEGRAM_ID)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# DATABASE
# ============================================================

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


# ============================================================
# TELEGRAM
# ============================================================

dp = Dispatcher()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="🎯 Получить свой отряд"
            )
        ]
    ],
    resize_keyboard=True,
)


# ============================================================
# ПРОВЕРКА АДМИНИСТРАТОРА
# ============================================================

def is_admin(message: Message) -> bool:

    return (
        message.from_user.id
        == ADMIN_TELEGRAM_ID
    )


# ============================================================
# START
# ============================================================

@dp.message(Command("start"))
async def start_handler(
    message: Message,
):

    await message.answer(
        "Привет! 👋\n\n"
        "Здесь ты можешь получить свой отряд.\n\n"
        "Нажми кнопку ниже.\n\n"
        "⚠️ Отряд определяется случайным образом.\n"
        "После получения изменить его нельзя.",
        reply_markup=main_keyboard,
    )


# ============================================================
# ПОЛУЧЕНИЕ ОТРЯДА
# ============================================================

@dp.message(
    F.text == "🎯 Получить свой отряд"
)
async def get_squad_handler(
    message: Message,
):

    telegram_id = message.from_user.id

    async with SessionLocal() as session:

        # ====================================================
        # 1. ПРОВЕРЯЕМ, ЕСТЬ ЛИ УЖЕ РАСПРЕДЕЛЕНИЕ
        # ====================================================

        existing = await session.scalar(
            select(Participant)
            .where(
                Participant.telegram_id
                == telegram_id
            )
        )

        if existing:

            squad = await session.get(
                Squad,
                existing.squad_id,
            )

            if squad:

                await message.answer(
                    "⚠️ Ты уже получил свой отряд.\n\n"
                    f"🎯 <b>{squad.name}</b>\n\n"
                    f"🔗 {squad.invite_link}\n\n"
                    "Получить другой отряд невозможно.",
                    parse_mode="HTML",
                    reply_markup=main_keyboard,
                )

            return

        # ====================================================
        # 2. НАЧИНАЕМ TRANSACTION
        # ====================================================

        try:

            # ------------------------------------------------
            # Сначала блокируем ВСЕ строки отрядов.
            #
            # Это не позволяет нескольким параллельным
            # запросам одновременно изменить количество
            # участников.
            # ------------------------------------------------

            squads = list(
                (
                    await session.scalars(
                        select(Squad)
                        .order_by(Squad.id)
                        .with_for_update()
                    )
                ).all()
            )

            if not squads:

                await message.answer(
                    "❌ Отряды ещё не настроены.",
                    reply_markup=main_keyboard,
                )

                return

            # ------------------------------------------------
            # Проверяем свободные места
            # ------------------------------------------------

            free_slots = []

            for squad in squads:

                if squad.members_count >= 20:
                    continue

                free_places = (
                    20
                    - squad.members_count
                )

                for _ in range(
                    free_places
                ):

                    free_slots.append(
                        squad
                    )

            # ------------------------------------------------
            # Все 200 мест заняты
            # ------------------------------------------------

            if not free_slots:

                await message.answer(
                    "❌ Все 10 отрядов уже заполнены.\n\n"
                    "Всего доступно 200 мест.",
                    reply_markup=main_keyboard,
                )

                return

            # =================================================
            # 3. RANDOM
            # =================================================

            selected_squad = random.choice(
                free_slots
            )

            # =================================================
            # 4. СОЗДАЁМ УЧАСТНИКА
            # =================================================

            participant = Participant(
                telegram_id=telegram_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                squad_id=selected_squad.id,
            )

            session.add(
                participant
            )

            selected_squad.members_count += 1

            # =================================================
            # 5. COMMIT
            # =================================================

            await session.commit()

        except IntegrityError:

            await session.rollback()

            # Такая ситуация может возникнуть,
            # если пользователь нажал кнопку несколько раз
            # одновременно.

            existing = await session.scalar(
                select(Participant)
                .where(
                    Participant.telegram_id
                    == telegram_id
                )
            )

            if existing:

                squad = await session.get(
                    Squad,
                    existing.squad_id,
                )

                await message.answer(
                    "⚠️ Ты уже получил свой отряд.\n\n"
                    f"🎯 <b>{squad.name}</b>\n\n"
                    f"🔗 {squad.invite_link}",
                    parse_mode="HTML",
                    reply_markup=main_keyboard,
                )

            else:

                await message.answer(
                    "Произошла ошибка.\n"
                    "Попробуй нажать кнопку ещё раз.",
                    reply_markup=main_keyboard,
                )

            return

        except Exception:

            await session.rollback()

            logger.exception(
                "Ошибка распределения"
            )

            await message.answer(
                "Произошла техническая ошибка.\n"
                "Попробуй ещё раз.",
                reply_markup=main_keyboard,
            )

            return

        # ====================================================
        # 6. ОТПРАВЛЯЕМ РЕЗУЛЬТАТ
        # ====================================================

        await message.answer(
            "🎉 <b>Распределение завершено!</b>\n\n"
            f"🎯 Твой отряд:\n"
            f"<b>{selected_squad.name}</b>\n\n"
            f"🔗 Ссылка на отряд:\n"
            f"{selected_squad.invite_link}\n\n"
            "⚠️ Сохрани ссылку.\n"
            "Получить другой отряд повторно нельзя.",
            parse_mode="HTML",
            reply_markup=main_keyboard,
        )


# ============================================================
# ADMIN
# ============================================================

@dp.message(Command("admin"))
async def admin_handler(
    message: Message,
):

    if not is_admin(message):

        await message.answer(
            "⛔ У тебя нет доступа."
        )

        return

    async with SessionLocal() as session:

        squads = list(
            (
                await session.scalars(
                    select(Squad)
                    .order_by(Squad.id)
                )
            ).all()
        )

        total = await session.scalar(
            select(
                func.count(
                    Participant.id
                )
            )
        )

    text = (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"Всего участников: "
        f"<b>{total}/200</b>\n\n"
    )

    for squad in squads:

        text += (
            f"🎯 {squad.name}: "
            f"<b>{squad.members_count}/20</b>\n"
        )

    await message.answer(
        text,
        parse_mode="HTML",
    )


# ============================================================
# RESET
# ============================================================

@dp.message(Command("reset"))
async def reset_handler(
    message: Message,
):

    if not is_admin(message):

        await message.answer(
            "⛔ У тебя нет доступа."
        )

        return

    async with SessionLocal() as session:

        await session.execute(
            delete(Participant)
        )

        squads = list(
            (
                await session.scalars(
                    select(Squad)
                    .order_by(Squad.id)
                )
            ).all()
        )

        for squad in squads:

            squad.members_count = 0

        await session.commit()

    await message.answer(
        "♻️ Распределение полностью сброшено.\n\n"
        "Теперь можно запускать жеребьёвку заново."
    )


# ============================================================
# ADMIN HELP
# ============================================================

@dp.message(Command("help"))
async def help_handler(
    message: Message,
):

    if is_admin(message):

        await message.answer(
            "🔐 <b>Команды администратора</b>\n\n"
            "/admin — статистика\n"
            "/reset — полностью сбросить распределение\n",
            parse_mode="HTML",
        )

    else:

        await message.answer(
            "Чтобы получить отряд, нажми:\n\n"
            "🎯 Получить свой отряд",
            reply_markup=main_keyboard,
        )


# ============================================================
# ЛЮБОЙ ДРУГОЙ ТЕКСТ
# ============================================================

@dp.message()
async def other_message_handler(
    message: Message,
):

    telegram_id = message.from_user.id

    async with SessionLocal() as session:

        participant = await session.scalar(
            select(Participant)
            .where(
                Participant.telegram_id
                == telegram_id
            )
        )

        if participant:

            squad = await session.get(
                Squad,
                participant.squad_id,
            )

            if squad:

                await message.answer(
                    "Твой отряд уже определён.\n\n"
                    f"🎯 <b>{squad.name}</b>\n\n"
                    f"🔗 {squad.invite_link}\n\n"
                    "Изменить отряд нельзя.",
                    parse_mode="HTML",
                    reply_markup=main_keyboard,
                )

            return

    await message.answer(
        "Нажми кнопку ниже, чтобы получить отряд.",
        reply_markup=main_keyboard,
    )


# ============================================================
# INITIALIZE DATABASE
# ============================================================

async def init_database():

    async with engine.begin() as connection:

        await connection.run_sync(
            Base.metadata.create_all
        )

    # --------------------------------------------------------
    # Получаем ссылки
    # --------------------------------------------------------

    links = []

    for i in range(1, 11):

        link = os.getenv(
            f"SQUAD_{i}_LINK"
        )

        if not link:

            raise RuntimeError(
                f"SQUAD_{i}_LINK не указан"
            )

        links.append(link)

    # --------------------------------------------------------
    # Названия
    # --------------------------------------------------------

    names = []

    for i in range(1, 11):

        name = os.getenv(
            f"SQUAD_{i}_NAME",
            f"Отряд {i}",
        )

        names.append(name)

    # --------------------------------------------------------
    # Создаём отряды
    # --------------------------------------------------------

    async with SessionLocal() as session:

        squads = list(
            (
                await session.scalars(
                    select(Squad)
                    .order_by(Squad.id)
                )
            ).all()
        )

        # Если база пустая
        if not squads:

            for i in range(10):

                session.add(
                    Squad(
                        name=names[i],
                        invite_link=links[i],
                        members_count=0,
                    )
                )

            await session.commit()

            logger.info(
                "Созданы 10 отрядов"
            )

        else:

            # Обновляем названия и ссылки.
            #
            # ВАЖНО:
            # members_count здесь НЕ трогаем.

            for i, squad in enumerate(
                squads[:10]
            ):

                squad.name = names[i]
                squad.invite_link = links[i]

            await session.commit()


# ============================================================
# MAIN
# ============================================================

async def main():

    await init_database()

    bot = Bot(
        token=BOT_TOKEN
    )

    logger.info(
        "Telegram bot started"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
