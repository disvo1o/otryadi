import asyncio
import html
import os
import random

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Participant, Squad


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
CAPACITY_PER_SQUAD = 10
SQUAD_COUNT = 10

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

# Railway PostgreSQL URL -> SQLAlchemy async URL
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

SQUADS = [
    {
        "name": f"Отряд {number}",
        "invite_link": os.getenv(f"SQUAD_{number}_LINK", "").strip(),
    }
    for number in range(1, SQUAD_COUNT + 1)
]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

async def init_database():
    # create_all does not delete or reset existing tables/data.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        result = await session.execute(select(Squad).order_by(Squad.id))
        squads = result.scalars().all()

        # Create only missing squads. Existing participant assignments stay intact.
        for index, squad_data in enumerate(SQUADS):
            if index < len(squads):
                squad = squads[index]
                squad.name = squad_data["name"]

                # Empty Railway variables must not erase links already stored in DB.
                if squad_data["invite_link"]:
                    squad.invite_link = squad_data["invite_link"]
            else:
                session.add(
                    Squad(
                        name=squad_data["name"],
                        invite_link=squad_data["invite_link"],
                        members_count=0,
                    )
                )

        # Recalculate counters from actual registrations to repair any old drift.
        await session.flush()
        participant_result = await session.execute(select(Participant.squad_id))
        counts = {}
        for squad_id in participant_result.scalars().all():
            counts[squad_id] = counts.get(squad_id, 0) + 1

        for squad in squads:
            squad.members_count = counts.get(squad.id, 0)

        await session.commit()


# ============================================================
# KEYBOARDS
# ============================================================

def get_start_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, хочу выбрать", callback_data="choice:yes"),
                InlineKeyboardButton(text="Нет, случайно", callback_data="choice:no"),
            ]
        ]
    )


def get_squads_keyboard(squads):
    rows = []
    for squad in squads:
        rows.append([
            InlineKeyboardButton(
                text=f"🏕 {squad.name} · {squad.members_count}/{CAPACITY_PER_SQUAD}",
                callback_data=f"choose:{squad.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="choice:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# HELPERS
# ============================================================

def is_admin(user: User) -> bool:
    return bool(ADMIN_TELEGRAM_ID) and str(user.id) == str(ADMIN_TELEGRAM_ID)


async def get_existing_assignment(session, telegram_id: int):
    result = await session.execute(
        select(Participant, Squad)
        .join(Squad, Participant.squad_id == Squad.id)
        .where(Participant.telegram_id == telegram_id)
    )
    return result.first()


async def send_assignment(send, squad: Squad, already_assigned: bool = False):
    name = html.escape(squad.name)
    if squad.invite_link:
        link = html.escape(squad.invite_link, quote=True)
        link_line = f'🔗 <a href="{link}">Войти в чат отряда</a>'
    else:
        link_line = (
            "⚠️ Ссылка на чат пока не настроена. "
            "Обратись к организатору."
        )

    if already_assigned:
        heading = "✅ Ты уже закреплён за своим отрядом:"
    else:
        heading = "🎉 Твой отряд:"

    await send(
        f"{heading}\n\n"
        f"🏕 <b>{name}</b>\n\n"
        f"{link_line}\n\n"
        "Место закреплено за тобой. Повторное распределение не изменит отряд.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def show_existing_assignment(user: User, send) -> bool:
    async with SessionLocal() as session:
        existing = await get_existing_assignment(session, user.id)
        if not existing:
            return False
        participant, squad = existing
        # Keep the participant profile up to date without changing their squad.
        participant.first_name = user.first_name or ""
        participant.last_name = user.last_name or ""
        participant.username = user.username or ""
        await session.commit()
        await send_assignment(send, squad, already_assigned=True)
        return True


async def get_available_squads(session, lock: bool = False):
    query = select(Squad).order_by(Squad.id)
    if lock:
        query = query.with_for_update()
    result = await session.execute(query)
    squads = result.scalars().all()
    # A squad without a configured chat link cannot be assigned yet.
    return [
        squad for squad in squads
        if squad.members_count < CAPACITY_PER_SQUAD and squad.invite_link
    ]


async def assign_squad(user: User, send, squad_id: int | None = None):
    # Fast idempotency check. The same check is repeated after acquiring locks.
    if await show_existing_assignment(user, send):
        return

    async with SessionLocal() as session:
        try:
            # Lock all squad rows so concurrent users cannot take the last place
            # in the same squad at the same time.
            squads = await get_available_squads(session, lock=True)

            existing = await get_existing_assignment(session, user.id)
            if existing:
                await session.rollback()
                await send_assignment(send, existing[1], already_assigned=True)
                return

            if squad_id is not None:
                selected = next((s for s in squads if s.id == squad_id), None)
                if selected is None:
                    await session.rollback()
                    await send(
                        "Это место уже заняли или отряд заполнен. "
                        "Выбери другой свободный отряд.",
                    )
                    return
            else:
                if not squads:
                    await session.rollback()
                    await send("😔 Свободных мест больше нет. Все 10 отрядов заполнены.")
                    return
                selected = random.choice(squads)

            participant = Participant(
                telegram_id=user.id,
                first_name=user.first_name or "",
                last_name=user.last_name or "",
                username=user.username or "",
                squad_id=selected.id,
            )
            session.add(participant)
            selected.members_count += 1
            await session.commit()

            await send_assignment(send, selected)
        except IntegrityError:
            await session.rollback()
            # A duplicate Telegram account registration should never change teams.
            existing = await get_existing_assignment(session, user.id)
            if existing:
                await send_assignment(send, existing[1], already_assigned=True)
            else:
                await send("Не получилось закрепить отряд. Попробуй ещё раз через минуту.")


# ============================================================
# START / CHOICE
# ============================================================

@dp.message(Command("start"))
async def start_handler(message: Message):
    if await show_existing_assignment(message.from_user, message.answer):
        return

    await message.answer(
        "Привет! 👋\n\n"
        "Ты хочешь попасть в определённый отряд?",
        reply_markup=get_start_keyboard(),
    )


@dp.callback_query(F.data == "choice:yes")
async def choose_manually_callback(callback: CallbackQuery):
    await callback.answer()
    if await show_existing_assignment(callback.from_user, callback.message.answer):
        return

    async with SessionLocal() as session:
        squads = await get_available_squads(session)

    if not squads:
        await callback.message.edit_text("😔 Свободных мест больше нет. Все 10 отрядов заполнены.")
        return

    await callback.message.edit_text(
        "Выбери отряд. Здесь отображаются только те, где ещё есть места:",
        reply_markup=get_squads_keyboard(squads),
    )


@dp.callback_query(F.data == "choice:no")
async def choose_randomly_callback(callback: CallbackQuery):
    await callback.answer()
    await assign_squad(callback.from_user, callback.message.answer)


@dp.callback_query(F.data == "choice:back")
async def back_callback(callback: CallbackQuery):
    await callback.answer()
    if await show_existing_assignment(callback.from_user, callback.message.answer):
        return
    await callback.message.edit_text(
        "Ты хочешь попасть в определённый отряд?",
        reply_markup=get_start_keyboard(),
    )


@dp.callback_query(F.data.startswith("choose:"))
async def choose_squad_callback(callback: CallbackQuery):
    await callback.answer()
    try:
        squad_id = int(callback.data.split(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.message.answer("Не удалось определить отряд. Нажми /start.")
        return

    await assign_squad(
        callback.from_user,
        callback.message.answer,
        squad_id=squad_id,
    )


# ============================================================
# COMMANDS
# ============================================================

@dp.message(Command("team"))
async def team_handler(message: Message):
    if await show_existing_assignment(message.from_user, message.answer):
        return
    await message.answer(
        "Ты хочешь попасть в определённый отряд?",
        reply_markup=get_start_keyboard(),
    )


@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if not is_admin(message.from_user):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    async with SessionLocal() as session:
        result = await session.execute(select(Squad).order_by(Squad.id))
        squads = result.scalars().all()
        total = sum(s.members_count for s in squads)

        lines = ["📊 <b>Статистика отрядов</b>", ""]
        for squad in squads:
            status = "🔒" if squad.members_count >= CAPACITY_PER_SQUAD else "🟢"
            lines.append(
                f"{status} {html.escape(squad.name)}: "
                f"<b>{squad.members_count}/{CAPACITY_PER_SQUAD}</b>"
            )
        lines.extend(["", f"👥 Всего участников: <b>{total}/{SQUAD_COUNT * CAPACITY_PER_SQUAD}</b>"])
        await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("reset"))
async def reset_handler(message: Message):
    if not is_admin(message.from_user):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    async with SessionLocal() as session:
        await session.execute(delete(Participant))
        result = await session.execute(select(Squad))
        for squad in result.scalars().all():
            squad.members_count = 0
        await session.commit()

    await message.answer(
        "♻️ Распределение очищено. Все 100 мест снова доступны. "
        "Ссылки и настройки отрядов сохранены."
    )


@dp.message(Command("help"))
async def help_handler(message: Message):
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "/start — начать распределение\n"
        "/team — показать свой отряд\n"
    )
    if is_admin(message.from_user):
        text += (
            "\n<b>Команды администратора:</b>\n"
            "/admin — статистика отрядов\n"
            "/reset — очистить распределение\n"
        )
    await message.answer(text, parse_mode="HTML")


@dp.message()
async def fallback_handler(message: Message):
    if await show_existing_assignment(message.from_user, message.answer):
        return
    await message.answer(
        "Нажми /start, чтобы выбрать отряд или получить случайный.",
        reply_markup=get_start_keyboard(),
    )


# ============================================================
# MAIN
# ============================================================

async def main():
    print("Starting bot...")
    await init_database()
    print("Database initialized.")
    print("Bot is running.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
