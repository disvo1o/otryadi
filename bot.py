import asyncio
import logging
import os
import random

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from sqlalchemy import select
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не указан")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не указан")


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
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):

    await message.answer(
        "Привет! 👋\n\n"
        "Тебе нужно получить свой отряд.\n\n"
        "Нажми кнопку «🎯 Получить свой отряд».\n\n"
        "Важно: отряд определяется случайным образом "
        "и после получения изменить его нельзя.",
        reply_markup=main_keyboard,
    )


# ============================================================
# ПОЛУЧЕНИЕ ОТРЯДА
# ============================================================

@dp.message(F.text == "🎯 Получить свой отряд")
async def get_squad_handler(message: Message):

    telegram_id = message.from_user.id

    async with SessionLocal() as session:

        # ----------------------------------------------------
        # Проверяем, получал ли пользователь отряд раньше
        # ----------------------------------------------------

        existing_participant = await session.scalar(
            select(Participant)
            .where(
                Participant.telegram_id == telegram_id
            )
        )

        if existing_participant:

            squad = await session.get(
                Squad,
                existing_participant.squad_id,
            )

            if squad:

                await message.answer(
                    "⚠️ Ты уже получил свой отряд.\n\n"
                    f"🎯 Твой отряд: {squad.name}\n\n"
                    f"🔗 Ссылка:\n{squad.invite_link}\n\n"
                    "Получить другой отряд невозможно.",
                    reply_markup=main_keyboard,
                )

            return

        # ----------------------------------------------------
        # Получаем свободные отряды
        # ----------------------------------------------------

        squads = list(
            (
                await session.scalars(
                    select(Squad)
                    .where(Squad.members_count < 20)
                )
            ).all()
        )

        if not squads:

            await message.answer(
                "❌ Все отряды уже заполнены.\n\n"
                "Всего доступно 200 мест.",
                reply_markup=main_keyboard,
            )

            return

        # ----------------------------------------------------
        # Создаём список свободных мест
        #
        # Если:
        #
        # Отряд 1 = 20
        # Отряд 2 = 15
        # Отряд 3 = 10
        #
        # то случай выбирается среди свободных мест.
        #
        # Это сохраняет случайность, но гарантирует максимум 20.
        # ----------------------------------------------------

        free_slots = []

        for squad in squads:

            free_places = 20 - squad.members_count

            for _ in range(free_places):
                free_slots.append(squad.id)

        if not free_slots:

            await message.answer(
                "❌ Все места уже заняты.",
                reply_markup=main_keyboard,
            )

            return

        # ----------------------------------------------------
        # RANDOM
        # ----------------------------------------------------

        selected_squad_id = random.choice(
            free_slots
        )

        selected_squad = await session.get(
            Squad,
            selected_squad_id,
            with_for_update=True,
        )

        # ----------------------------------------------------
        # Повторная проверка после блокировки
        # ----------------------------------------------------

        if selected_squad.members_count >= 20:

            await session.rollback()

            await message.answer(
                "Произошла небольшая ошибка распределения.\n"
                "Нажми кнопку ещё раз.",
                reply_markup=main_keyboard,
            )

            return

        # ----------------------------------------------------
        # Создаём участника
        # ----------------------------------------------------

        participant = Participant(
            telegram_id=telegram_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            squad_id=selected_squad.id,
        )

        # Увеличиваем количество участников
        selected_squad.members_count += 1

        session.add(participant)

        await session.commit()

        logger.info(
            "User %s assigned to squad %s",
            telegram_id,
            selected_squad.name,
        )

        # ----------------------------------------------------
        # Отправляем результат
        # ----------------------------------------------------

        await message.answer(
            "🎉 Распределение завершено!\n\n"
            f"🎯 Твой отряд:\n"
            f"<b>{selected_squad.name}</b>\n\n"
            f"🔗 Ссылка на отряд:\n"
            f"{selected_squad.invite_link}\n\n"
            "⚠️ Сохрани ссылку.\n"
            "Получить другой отряд повторно нельзя.",
            reply_markup=main_keyboard,
            parse_mode="HTML",
        )


# ============================================================
# ОБРАБОТКА ЛЮБОГО ДРУГОГО ТЕКСТА
# ============================================================

@dp.message()
async def other_message_handler(message: Message):

    telegram_id = message.from_user.id

    async with SessionLocal() as session:

        participant = await session.scalar(
            select(Participant)
            .where(
                Participant.telegram_id == telegram_id
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
                    f"🎯 {squad.name}\n\n"
                    f"🔗 {squad.invite_link}\n\n"
                    "Изменить отряд нельзя.",
                    reply_markup=main_keyboard,
                )

            return

    await message.answer(
        "Чтобы получить отряд, нажми кнопку:\n\n"
        "🎯 Получить свой отряд",
        reply_markup=main_keyboard,
    )


# ============================================================
# СОЗДАНИЕ БАЗЫ
# ============================================================

async def init_database():

    async with engine.begin() as connection:

        await connection.run_sync(
            Base.metadata.create_all
        )

    # --------------------------------------------------------
    # Создаём 10 отрядов, если их ещё нет
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

    async with SessionLocal() as session:

        existing = list(
            (
                await session.scalars(
                    select(Squad)
                    .order_by(Squad.id)
                )
            ).all()
        )

        if not existing:

            for i in range(10):

                squad = Squad(
                    name=f"Отряд {i + 1}",
                    invite_link=links[i],
                    members_count=0,
                )

                session.add(squad)

            await session.commit()

            logger.info(
                "Созданы 10 отрядов"
            )

        else:

            # Обновляем ссылки из ENV.
            for i, squad in enumerate(existing[:10]):

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
        "Bot started"
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
