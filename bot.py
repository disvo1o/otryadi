import asyncio
import html
import json
import os
import random

import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = os.getenv("ADMIN_TELEGRAM_ID")
GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID",
    "1o5tdrhdUfkDuugVi8FqnbNp_0eyzsUNIaOeE2nbiLeg",
)
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

CAPACITY_PER_SQUAD = 10
SQUAD_COUNT = 10
HEADERS = ["Команда", "Имя", "Фамилия", "Username", "Telegram ID"]

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not GOOGLE_SERVICE_ACCOUNT_JSON:
    raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")

SQUADS = [
    {
        "name": os.getenv(f"SQUAD_{number}_NAME", f"Отряд {number}"),
        "invite_link": os.getenv(f"SQUAD_{number}_LINK", "").strip(),
    }
    for number in range(1, SQUAD_COUNT + 1)
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
credentials = Credentials.from_service_account_info(
    credentials_info,
    scopes=SCOPES,
)
sheets_client = gspread.authorize(credentials)
spreadsheet = sheets_client.open_by_key(GOOGLE_SHEET_ID)
worksheet = spreadsheet.sheet1

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# One Railway replica should run this bot. The lock prevents two simultaneous
# registrations from taking the same final place inside this process.
registration_lock = asyncio.Lock()


# ============================================================
# GOOGLE SHEETS
# Columns: A squad, B first name, C last name, D username, E Telegram ID.
# ============================================================

def read_records():
    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(HEADERS, value_input_option="RAW")
        return []

    current_headers = values[0][:5]
    if current_headers != HEADERS:
        raise RuntimeError(
            "Google Sheet header mismatch. Expected columns A:E: "
            + ", ".join(HEADERS)
        )

    records = []
    for row_number, row in enumerate(values[1:], start=2):
        padded = row + [""] * (5 - len(row))
        if any(str(value).strip() for value in padded[:5]):
            records.append({
                "row": row_number,
                "squad": padded[0].strip(),
                "first_name": padded[1].strip(),
                "last_name": padded[2].strip(),
                "username": padded[3].strip(),
                "telegram_id": padded[4].strip(),
            })
    return records


def append_record(user: User, squad_name: str):
    worksheet.append_row(
        [
            squad_name,
            user.first_name or "",
            user.last_name or "",
            user.username or "",
            str(user.id),
        ],
        value_input_option="RAW",
    )


def clear_records():
    # Keep the header and formatting; remove participant data only.
    worksheet.batch_clear(["A2:E"])


def ensure_sheet_ready():
    values = worksheet.get_all_values()
    if not values:
        worksheet.append_row(HEADERS, value_input_option="RAW")
    elif values[0][:5] != HEADERS:
        raise RuntimeError(
            "Google Sheet header mismatch. Expected columns A:E: "
            + ", ".join(HEADERS)
        )


async def get_records():
    return await asyncio.to_thread(read_records)


async def find_existing(telegram_id: int):
    records = await get_records()
    return next(
        (record for record in records if record["telegram_id"] == str(telegram_id)),
        None,
    )


def get_counts(records):
    counts = {squad["name"]: 0 for squad in SQUADS}
    for record in records:
        if record["squad"] in counts:
            counts[record["squad"]] += 1
    return counts


def available_squads(records):
    counts = get_counts(records)
    return [
        squad for squad in SQUADS
        if squad["invite_link"]
        and counts[squad["name"]] < CAPACITY_PER_SQUAD
    ]


# ============================================================
# KEYBOARDS
# ============================================================

def get_start_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data="choice:yes"),
            InlineKeyboardButton(text="Нет", callback_data="choice:no"),
        ]
    ])


def get_squads_keyboard(squads, counts):
    rows = []
    for index, squad in enumerate(squads, start=1):
        rows.append([InlineKeyboardButton(
            text=f"🏕 {squad['name']} · {counts[squad['name']]}/{CAPACITY_PER_SQUAD}",
            callback_data=f"choose:{SQUADS.index(squad) + 1}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="choice:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# HELPERS
# ============================================================

def is_admin(user: User) -> bool:
    return bool(ADMIN_TELEGRAM_ID) and str(user.id) == str(ADMIN_TELEGRAM_ID)


async def send_assignment(send, squad, already_assigned=False):
    name = html.escape(squad["name"])
    link = squad["invite_link"]
    if link:
        link_line = (
            f'🔗 <a href="{html.escape(link, quote=True)}">'
            "Войти в чат отряда</a>"
        )
    else:
        link_line = "⚠️ Ссылка на чат пока не настроена. Обратись к организатору."

    heading = (
        "✅ Ты уже закреплён за своим отрядом:"
        if already_assigned else "🎉 Твой отряд:"
    )
    await send(
        f"{heading}\n\n🏕 <b>{name}</b>\n\n{link_line}\n\n"
        "Место закреплено за тобой. Повторное распределение не изменит отряд.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def show_existing_assignment(user: User, send) -> bool:
    async with registration_lock:
        records = await get_records()
        record = next(
            (item for item in records if item["telegram_id"] == str(user.id)),
            None,
        )
        if not record:
            return False

        squad = next(
            (item for item in SQUADS if item["name"] == record["squad"]),
            None,
        )
        if not squad:
            await send(
                "Я нашёл твою запись в таблице, но не смог определить отряд. "
                "Обратись к организатору."
            )
            return True
        await send_assignment(send, squad, already_assigned=True)
        return True


async def assign_squad(user: User, send, squad_number=None):
    async with registration_lock:
        records = await get_records()

        existing = next(
            (item for item in records if item["telegram_id"] == str(user.id)),
            None,
        )
        if existing:
            squad = next(
                (item for item in SQUADS if item["name"] == existing["squad"]),
                None,
            )
            if squad:
                await send_assignment(send, squad, already_assigned=True)
            else:
                await send("Твоя запись найдена, но отряд не распознан. Обратись к организатору.")
            return

        counts = get_counts(records)
        available = available_squads(records)

        if squad_number is not None:
            if squad_number < 1 or squad_number > SQUAD_COUNT:
                await send("Не удалось определить отряд. Нажми /start.")
                return
            selected = SQUADS[squad_number - 1]
            if selected not in available:
                await send("Этот отряд уже заполнен или ссылка не настроена. Выбери другой.")
                return
        else:
            if not available:
                await send("😔 Свободных мест больше нет. Все 10 отрядов заполнены.")
                return
            selected = random.choice(available)

        # The row is the registration: capacity is reserved as soon as it is appended.
        await asyncio.to_thread(append_record, user, selected["name"])
        await send_assignment(send, selected)


# ============================================================
# START / CHOICE
# ============================================================

@dp.message(Command("start"))
async def start_handler(message: Message):
    if await show_existing_assignment(message.from_user, message.answer):
        return
    await message.answer(
        "Привет! 👋\n\nТы хочешь попасть в определённый отряд?",
        reply_markup=get_start_keyboard(),
    )


@dp.callback_query(F.data == "choice:yes")
async def choose_manually_callback(callback: CallbackQuery):
    await callback.answer()
    if await show_existing_assignment(callback.from_user, callback.message.answer):
        return

    records = await get_records()
    available = available_squads(records)
    if not available:
        await callback.message.edit_text("😔 Свободных мест больше нет. Все 10 отрядов заполнены.")
        return

    await callback.message.edit_text(
        "Выбери отряд. Здесь отображаются только те, где ещё есть места:",
        reply_markup=get_squads_keyboard(available, get_counts(records)),
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
        squad_number = int(callback.data.split(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.message.answer("Не удалось определить отряд. Нажми /start.")
        return
    await assign_squad(
        callback.from_user,
        callback.message.answer,
        squad_number=squad_number,
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

    records = await get_records()
    counts = get_counts(records)
    lines = ["📊 <b>Статистика отрядов</b>", ""]
    for squad in SQUADS:
        count = counts[squad["name"]]
        status = "🔒" if count >= CAPACITY_PER_SQUAD else "🟢"
        lines.append(
            f"{status} {html.escape(squad['name'])}: "
            f"<b>{count}/{CAPACITY_PER_SQUAD}</b>"
        )
    lines.extend(["", f"👥 Всего участников: <b>{len(records)}/{SQUAD_COUNT * CAPACITY_PER_SQUAD}</b>"])
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("reset"))
async def reset_handler(message: Message):
    if not is_admin(message.from_user):
        await message.answer("⛔ У тебя нет доступа к этой команде.")
        return

    async with registration_lock:
        await asyncio.to_thread(clear_records)
    await message.answer(
        "♻️ Распределение очищено. Все 100 мест снова доступны. "
        "Заголовки таблицы и настройки отрядов сохранены."
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
    print("Starting bot with Google Sheets storage...")
    await asyncio.to_thread(ensure_sheet_ready)
    print("Google Sheet connected.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
