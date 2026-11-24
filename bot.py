import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from dotenv import load_dotenv

from database import Database

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "ezdelkho").lstrip("@").lower()
DB_PATH = os.getenv("DB_PATH", "het_ckama.sqlite3")

if not TOKEN:
    raise RuntimeError("Укажи BOT_TOKEN в файле .env")

logging.basicConfig(level=logging.INFO)
db = Database(DB_PATH)
dp = Dispatcher()


class ReportFlow(StatesGroup):
    target = State()
    reason = State()
    evidence = State()


def is_admin(user_id: int) -> bool:
    return db.is_admin(user_id)


def clean_target(raw: str):
    value = raw.strip()
    if re.fullmatch(r"\d{5,20}", value):
        return "id", value
    value = value.removeprefix("https://t.me/").removeprefix("http://t.me/")
    value = value.removeprefix("@").strip("/")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", value):
        return "username", value.lower()
    return None, None


def main_menu(admin=False):
    rows = [
        [InlineKeyboardButton(text="🔎 Проверить пользователя", callback_data="check")],
        [InlineKeyboardButton(text="➕ Отправить жалобу", callback_data="report")],
        [InlineKeyboardButton(text="ℹ️ О проекте", callback_data="about")],
    ]
    if admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_keyboard(report_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{report_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{report_id}"),
        ]
    ])


@dp.message(CommandStart())
async def start(message: Message):
    user = message.from_user
    if user.username and user.username.lower() == ADMIN_USERNAME:
        db.add_admin(user.id)
    admin = is_admin(user.id)
    await message.answer(
        "🛡 <b>HET CKAMA</b>\n\n"
        "Проверка Telegram ID и username по базе модерации.\n"
        "Отправляй жалобы — публикация возможна только после проверки администратором.\n\n"
        "Выбери действие:",
        reply_markup=main_menu(admin),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "about")
async def about(call: CallbackQuery):
    await call.message.answer(
        "HET CKAMA — база пользовательских сообщений о возможном мошенничестве.\n"
        "Непроверенные жалобы не показываются как подтверждённые факты. "
        "Не отправляйте пароли, банковские данные, адреса или чужие личные документы."
    )
    await call.answer()


@dp.callback_query(F.data == "check")
async def check_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ReportFlow.target)
    await state.update_data(mode="check")
    await call.message.answer("Отправь @username или числовой Telegram ID для проверки.")
    await call.answer()


@dp.message(Command("check"))
async def check_command(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /check @username или /check 123456789")
        return
    await show_check_result(message, parts[1])


async def show_check_result(message: Message, raw: str):
    kind, value = clean_target(raw)
    if not kind:
        await message.answer("Не распознал данные. Пришли @username или числовой Telegram ID.")
        return
    entries = db.public_lookup(kind, value)
    if not entries:
        await message.answer("✅ В публичной части базы подтверждённых модератором записей не найдено.\n"
                             "Это не гарантирует безопасность пользователя.")
        return
    lines = [f"⚠️ <b>Найдены записи по {kind}:</b> <code>{value}</code>"]
    for item in entries:
        lines.append(
            f"\n<b>Запись #{item['id']}</b>\n"
            f"Статус: подтверждено модератором\n"
            f"Категория: {item['category']}\n"
            f"Описание: {item['reason']}\n"
            f"Дата: {item['created_at'][:10]}"
        )
    lines.append("\nЕсли запись ошибочная, напиши администратору для оспаривания.")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.callback_query(F.data == "report")
async def report_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(ReportFlow.target)
    await state.update_data(mode="report")
    await call.message.answer(
        "Кого касается жалоба? Пришли @username или числовой Telegram ID.\n"
        "Не присылай номер телефона, адрес или другие лишние персональные данные."
    )
    await call.answer()


@dp.message(Command("report"))
async def report_command(message: Message, state: FSMContext):
    await state.set_state(ReportFlow.target)
    await state.update_data(mode="report")
    await message.answer("Пришли @username или числовой Telegram ID, на который подаёшь жалобу.")


@dp.message(ReportFlow.target)
async def receive_target(message: Message, state: FSMContext):
    kind, value = clean_target(message.text or "")
    if not kind:
        await message.answer("Формат не распознан. Нужен @username или числовой Telegram ID.")
        return
    data = await state.get_data()
    if data.get("mode") == "check":
        await state.clear()
        await show_check_result(message, message.text)
        return
    await state.update_data(target_type=kind, target_value=value)
    await state.set_state(ReportFlow.reason)
    await message.answer("Кратко опиши, что произошло. Укажи факты, дату и контекст сделки. Без оскорблений.")


@dp.message(ReportFlow.reason)
async def receive_reason(message: Message, state: FSMContext):
    reason = (message.text or "").strip()
    if len(reason) < 15 or len(reason) > 1200:
        await message.answer("Описание должно быть от 15 до 1200 символов.")
        return
    await state.update_data(reason=reason)
    await state.set_state(ReportFlow.evidence)
    await message.answer(
        "Пришли ссылку на доказательство (например, публичный пост/облако) или напиши /skip.\n"
        "Не загружай документы с банковскими реквизитами, паролями или чужими чувствительными данными."
    )


@dp.message(ReportFlow.evidence, Command("skip"))
async def skip_evidence(message: Message, state: FSMContext):
    await save_report(message, state, None)


@dp.message(ReportFlow.evidence)
async def receive_evidence(message: Message, state: FSMContext):
    evidence = (message.text or "").strip()
    if not re.match(r"^https?://", evidence):
        await message.answer("Нужна ссылка, начинающаяся с http:// или https://, либо /skip.")
        return
    await save_report(message, state, evidence[:500])


async def save_report(message: Message, state: FSMContext, evidence):
    data = await state.get_data()
    report_id = db.create_report(
        reporter_id=message.from_user.id,
        target_type=data["target_type"],
        target_value=data["target_value"],
        reason=data["reason"],
        evidence=evidence,
    )
    await state.clear()
    await message.answer(
        f"✅ Жалоба #{report_id} отправлена на модерацию.\n"
        "Она не появится в публичной проверке, пока администратор её не рассмотрит."
    )
    for admin_id in db.admin_ids():
        try:
            await message.bot.send_message(
                admin_id,
                f"🆕 <b>Новая жалоба #{report_id}</b>\n"
                f"Цель: <code>{data['target_type']}:{data['target_value']}</code>\n"
                f"Описание: {data['reason']}\n"
                f"Доказательство: {evidence or 'не приложено'}",
                reply_markup=review_keyboard(report_id),
                parse_mode="HTML"
            )
        except Exception:
            logging.exception("Не удалось уведомить администратора %s", admin_id)


@dp.callback_query(F.data == "admin")
async def admin_panel(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    pending = db.pending_reports()
    await call.message.answer(
        f"🛠 <b>Админ-панель</b>\nОжидают проверки: {len(pending)}\n\n"
        "Команды:\n"
        "/pending — следующая жалоба\n"
        "/stats — статистика\n"
        "/pricing — состояние платного режима (пока настройка-заготовка)",
        parse_mode="HTML"
    )
    await call.answer()


@dp.message(Command("pending"))
async def pending_command(message: Message):
    if not is_admin(message.from_user.id):
        return
    item = db.next_pending()
    if not item:
        await message.answer("Новых жалоб нет.")
        return
    await message.answer(
        f"🆕 <b>Жалоба #{item['id']}</b>\n"
        f"Цель: <code>{item['target_type']}:{item['target_value']}</code>\n"
        f"Описание: {item['reason']}\n"
        f"Доказательство: {item['evidence'] or 'не приложено'}\n"
        f"Отправитель ID: <code>{item['reporter_id']}</code>",
        reply_markup=review_keyboard(item["id"]),
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith(("approve:", "reject:")))
async def moderate(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    action, raw_id = call.data.split(":")
    report_id = int(raw_id)
    new_status = "approved" if action == "approve" else "rejected"
    changed = db.moderate_report(report_id, new_status, call.from_user.id)
    if changed:
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"Жалоба #{report_id}: {'одобрена' if new_status == 'approved' else 'отклонена'}.")
        await call.answer("Сохранено")
    else:
        await call.answer("Жалоба уже обработана или не найдена", show_alert=True)


@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    s = db.stats()
    await message.answer(
        f"📊 Всего жалоб: {s['total']}\n"
        f"Ожидают: {s['pending']}\n"
        f"Одобрено: {s['approved']}\n"
        f"Отклонено: {s['rejected']}"
    )


@dp.message(Command("pricing"))
async def pricing(message: Message):
    if not is_admin(message.from_user.id):
        return
    enabled, price = db.get_pricing()
    await message.answer(
        f"Платный режим: {'ВКЛ' if enabled else 'ВЫКЛ'}\n"
        f"Цена: {price}\n\n"
        "Пока все проверки бесплатные. Интеграция оплаты подключается отдельно; "
        "не включай платный режим, пока не настроен платёжный провайдер."
    )


@dp.message()
async def fallback(message: Message):
    if message.text:
        await message.answer("Используй меню или команды /check и /report.")


async def main():
    db.initialize()
    bot = Bot(TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
