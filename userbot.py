# -*- coding: utf-8 -*-
"""
Telegram Userbot + Admin Bot
- Kalit so'zlarni kuzatish
- Blacklist (qora ro'yxat)
- Formatlangan xabar
- Profil ulash (telefon + kod + 2FA)
- Admin panel
"""

import json
import os
import html
import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Set
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    TelegramObject, ErrorEvent, BufferedInputFile,
)
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from telethon import TelegramClient, events as tl_events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import PeerChannel, PeerChat, PeerUser, Channel, Chat, User


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ============================================================
# .env
# ============================================================

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN noto'g'ri! .env faylga BOT_TOKEN=... qo'shing")
if not ADMIN_IDS:
    raise SystemExit("❌ ADMIN_IDS bo'sh! .env faylga ADMIN_IDS=... qo'shing")


# ============================================================
# FAYL YORDAMCHILARI
# ============================================================

SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)


def load_json(filename, default=None):
    if default is None:
        default = {}
    if not os.path.exists(filename):
        return default
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.warning(f"⚠️ {filename} o'qishda xato: {e}")
        return default


def save_json(filename, data):
    tmp = filename + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, filename)
    except Exception as e:
        log.error(f"❌ {filename} yozishda xato: {e}")


def ensure_files():
    defaults = {
        'keywords.json': [],
        'channels.json': [],
        'blacklist.json': [],
        'stats.json': {"total_matches": 0, "keywords": {}, "daily": {}},
        'admins.json': {"superadmin": 0, "admins": []},
        'users.json': [],
        'config.json': {
            "api_id": 0,
            "api_hash": "",
            "search_interval": 60,
            "target_channel": "",
            "forward_to_channels": [],
        },
    }
    for filename, default in defaults.items():
        if not os.path.exists(filename):
            save_json(filename, default)
            log.info(f"📄 {filename} yaratildi")


def get_config():
    return load_json('config.json', {})


def update_config(key, value):
    config = get_config()
    config[key] = value
    save_json('config.json', config)
    return config


def esc(value):
    return html.escape(str(value))


def short(text, limit=30):
    s = str(text)
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ============================================================
# BLACKLIST
# ============================================================

def load_blacklist():
    bl = load_json('blacklist.json', [])
    if not isinstance(bl, list):
        bl = []
    return bl


def save_blacklist(bl):
    save_json('blacklist.json', bl)


def is_blacklisted(chat_id) -> bool:
    if chat_id is None:
        return False
    cid = str(chat_id)
    for item in load_blacklist():
        if str(item.get('id')) == cid:
            return True
    return False


def add_to_blacklist(chat_id, title):
    bl = load_blacklist()
    cid_str = str(chat_id)
    if any(str(x.get('id')) == cid_str for x in bl):
        return False
    bl.append({
        "id": chat_id,
        "title": title or str(chat_id),
        "added_at": datetime.now().isoformat(),
    })
    save_blacklist(bl)
    return True


def remove_from_blacklist(chat_id):
    bl = load_blacklist()
    cid_str = str(chat_id)
    new_bl = [x for x in bl if str(x.get('id')) != cid_str]
    if len(new_bl) == len(bl):
        return False
    save_blacklist(new_bl)
    return True


# ============================================================
# ADMIN TEKSHIRUV
# ============================================================

def get_admins():
    return load_json('admins.json', {"superadmin": 0, "admins": []})


def is_superadmin(user_id: int) -> bool:
    admins = get_admins()
    return bool(admins.get('superadmin')) and user_id == admins.get('superadmin')


def is_admin(user_id: int) -> bool:
    admins = get_admins()
    if admins.get('superadmin') and user_id == admins.get('superadmin'):
        return True
    return user_id in admins.get('admins', [])


def get_user_role(user_id: int) -> str:
    if is_superadmin(user_id):
        return "👑 Superadmin"
    if is_admin(user_id):
        return "👤 Admin"
    return "👤 Foydalanuvchi"


def register_user(user: types.User):
    try:
        users = load_json('users.json', [])
        user_ids = {u.get('id') for u in users}
        now = datetime.now().isoformat()
        if user.id not in user_ids:
            users.append({
                "id": user.id,
                "username": user.username or "",
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "first_seen": now,
                "last_seen": now,
            })
        else:
            for u in users:
                if u.get('id') == user.id:
                    u['last_seen'] = now
                    u['username'] = user.username or u.get('username', '')
                    u['first_name'] = user.first_name or u.get('first_name', '')
                    break
        save_json('users.json', users)
    except Exception as e:
        log.warning(f"register_user xatosi: {e}")


def bootstrap_superadmin():
    admins = get_admins()
    if admins.get('superadmin'):
        return
    first_admin = next(iter(ADMIN_IDS))
    admins['superadmin'] = first_admin
    save_json('admins.json', admins)
    log.info(f"👑 Superadmin .env dan o'rnatildi: {first_admin}")


# ============================================================
# API CREDENTIALS
# ============================================================

def get_api_credentials():
    cfg = get_config()
    api_id = cfg.get("api_id", 0)
    api_hash = str(cfg.get("api_hash", "")).strip().strip("'\"")
    try:
        api_id = int(api_id)
    except (ValueError, TypeError):
        api_id = 0
    return api_id, api_hash


def has_api_credentials() -> bool:
    api_id, api_hash = get_api_credentials()
    return bool(api_id) and len(api_hash) == 32


def is_session_exists(user_id: int) -> bool:
    return (SESSIONS_DIR / f"{user_id}.session").exists()


# ============================================================
# FILTRLAR
# ============================================================

class CancelFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return message.text in ("/cancel", "❌ Bekor qilish")


class AdminFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        return is_admin(message.from_user.id)


# ============================================================
# BOT
# ============================================================

ensure_files()
bootstrap_superadmin()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


@dp.errors()
async def global_error_handler(event: ErrorEvent):
    log.exception(f"❌ Kutilmagan xatolik: {event.exception}")
    return True


# ============================================================
# FSM
# ============================================================

class KeywordStates(StatesGroup):
    waiting_for_keyword = State()

class ChannelStates(StatesGroup):
    waiting_for_channel = State()

class BlacklistStates(StatesGroup):
    waiting_for_channel = State()

class AdminStates(StatesGroup):
    waiting_for_admin_id = State()

class SuperAdminTransferStates(StatesGroup):
    waiting_for_new_superadmin = State()

class EditKeywordStates(StatesGroup):
    waiting_for_new_keyword = State()

class BulkImportStates(StatesGroup):
    waiting_for_keywords = State()

class LoginStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_password = State()

class ApiStates(StatesGroup):
    waiting_api_id = State()
    waiting_api_hash = State()


temp_clients: Dict[int, TelegramClient] = {}
active_userbots: Dict[int, TelegramClient] = {}
background_tasks: Set[asyncio.Task] = set()


def spawn(coro):
    """Background task yaratish va reference saqlash."""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task


# ============================================================
# KLAVIATURALAR
# ============================================================

def user_reply_menu(is_admin_user: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📞 Biz bilan bog'lanish")],
        [KeyboardButton(text="ℹ️ Bot haqida")],
    ]
    if is_admin_user:
        rows.insert(0, [KeyboardButton(text="🎛️ Admin panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_reply_menu(is_connected: bool = False, has_api: bool = True) -> ReplyKeyboardMarkup:
    rows = []
    if not has_api:
        rows.append([KeyboardButton(text="⚙️ API sozlash")])
    else:
        if is_connected:
            rows.append([KeyboardButton(text="🔗 Profilni uzish")])
        else:
            rows.append([KeyboardButton(text="🔗 Profilni ulash")])
        rows.append([KeyboardButton(text="👤 Profil ma'lumotlari")])
        rows.append([KeyboardButton(text="⚙️ API sozlamalari")])
    rows.append([KeyboardButton(text="🎛️ Admin panel")])
    rows.append([KeyboardButton(text="🏠 Asosiy menyu")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def phone_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamini yuborish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def cancel_reply_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Kalit so'zlar", callback_data="kw_menu")],
        [InlineKeyboardButton(text="📢 Kanallar", callback_data="ch_menu")],
        [InlineKeyboardButton(text="🚫 Qora ro'yxat", callback_data="bl_menu")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="users_menu")],
        [InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="settings")],
        [InlineKeyboardButton(text="👮 Adminlar", callback_data="admins_menu")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh")],
    ])


def keywords_menu():
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    buttons = []
    for i, kw in enumerate(keywords):
        label = short(kw, 25)
        buttons.append([
            InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"edit_kw:{i}"),
            InlineKeyboardButton(text="❌", callback_data=f"del_kw:{i}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Yangi qo'shish", callback_data="add_kw")])
    buttons.append([InlineKeyboardButton(text="📥 Ommaviy import", callback_data="bulk_import")])
    buttons.append([InlineKeyboardButton(text="📤 Eksport (JSON)", callback_data="bulk_export")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def channels_menu():
    channels = load_json('channels.json', [])
    buttons = []
    for i, ch in enumerate(channels):
        title = ch.get('title', ch.get('id', '?'))
        buttons.append([
            InlineKeyboardButton(text=f"❌ {short(title, 30)}", callback_data=f"del_ch:{i}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Kanal qo'shish", callback_data="add_ch")])
    buttons.append([InlineKeyboardButton(text="🗑 Hammasini o'chirish", callback_data="clear_ch")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def blacklist_menu():
    bl = load_blacklist()
    buttons = []
    for i, item in enumerate(bl):
        title = item.get('title', item.get('id', '?'))
        buttons.append([
            InlineKeyboardButton(text=f"❌ {short(title, 30)}", callback_data=f"del_bl:{i}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Qo'shish", callback_data="add_bl")])
    buttons.append([InlineKeyboardButton(text="🗑 Hammasini o'chirish", callback_data="clear_bl")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admins_menu():
    admins = get_admins()
    admin_list = admins.get('admins', [])
    buttons = []
    for admin_id in admin_list:
        buttons.append([
            InlineKeyboardButton(text=f"❌ {admin_id}", callback_data=f"del_admin:{admin_id}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="add_admin")])
    buttons.append([InlineKeyboardButton(text="👑 Superadmin o'tkazish", callback_data="transfer_superadmin")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def settings_menu():
    cfg = get_config()
    api_id, _ = get_api_credentials()
    api_status = f"✅ {api_id}" if has_api_credentials() else "❌ sozlanmagan"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⏱ Interval: {cfg.get('search_interval', 60)}s",
            callback_data="set_interval"
        )],
        [InlineKeyboardButton(text=f"🆔 API: {api_status}", callback_data="api_info")],
        [InlineKeyboardButton(text="📄 config.json yuklab olish", callback_data="download_config")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")],
    ])


def users_menu():
    users = load_json('users.json', [])
    buttons = []
    for u in users[-20:]:
        name = u.get('first_name') or u.get('username') or str(u.get('id'))
        buttons.append([
            InlineKeyboardButton(
                text=f"👤 {short(name, 25)} ({u.get('id')})",
                callback_data=f"user_info:{u.get('id')}"
            ),
        ])
    buttons.append([InlineKeyboardButton(text="📤 Eksport (JSON)", callback_data="export_users")])
    buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def back_kb(target="main_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=target)]
    ])


# ============================================================
# /start
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    register_user(user)

    if is_admin(user.id):
        connected = is_session_exists(user.id)
        has_api = has_api_credentials()
        status = []
        if not has_api:
            status.append("⚠️ <b>API sozlanmagan</b> — «⚙️ API sozlash» tugmasini bosing")
        else:
            status.append("✅ API sozlangan")
            status.append(f"📱 Profil: {'<b>ulangan</b>' if connected else '<i>ulanmagan</i>'}")
        await message.answer(
            f"👋 Salom, <b>{esc(user.full_name)}</b>!\n"
            f"Rol: {get_user_role(user.id)}\n"
            f"🆔 ID: <code>{user.id}</code>\n\n"
            + "\n".join(status)
            + "\n\nQuyidagi tugmalardan foydalaning:",
            reply_markup=admin_reply_menu(is_connected=connected, has_api=has_api),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"👋 Salom, <b>{esc(user.full_name)}</b>!\n\n"
            f"🤖 Bu bot orqali siz bizning xizmatlarimizdan foydalanishingiz mumkin.\n\n"
            f"Quyidagi tugmalardan birini tanlang:",
            reply_markup=user_reply_menu(is_admin_user=False),
            parse_mode="HTML",
        )


@dp.message(Command("myid"))
async def cmd_myid(message: types.Message):
    await message.answer(
        f"🆔 Sizning Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Rol: {get_user_role(message.from_user.id)}",
        parse_mode="HTML",
    )


# ============================================================
# FOYDALANUVCHI TUGMALARI
# ============================================================

@dp.message(F.text == "📞 Biz bilan bog'lanish")
async def user_contact(message: types.Message):
    register_user(message.from_user)
    await message.answer(
        "📞 <b>Biz bilan bog'lanish</b>\n\n"
        "👨‍💻Admin: @shaxsiy404\n\n"
        "📞Telefon: +998507714207\n\n"
        "🧩Support: @nwsxalfa\n\n"
        "📡Web: https://maqsudjon202.netlify.app\n\n",
        parse_mode="HTML",
    )


@dp.message(F.text == "ℹ️ Bot haqida")
async def user_about(message: types.Message):
    register_user(message.from_user)
    await message.answer(
        "ℹ️ <b>Bot haqida</b>\n\n"
        "🤖Bu bot ulangan profildagi kalit so'zlarni kuzatib boradi va tahlil qiladi.\n\n",
        parse_mode="HTML",
    )


@dp.message(F.text == "🏠 Asosiy menyu")
async def back_to_user_menu(message: types.Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id):
        connected = is_session_exists(message.from_user.id)
        has_api = has_api_credentials()
        await message.answer(
            "🏠 Asosiy menyu (admin)",
            reply_markup=admin_reply_menu(is_connected=connected, has_api=has_api),
        )
    else:
        await message.answer(
            "🏠 Asosiy menyu",
            reply_markup=user_reply_menu(is_admin_user=False),
        )


# ============================================================
# ADMIN PANEL
# ============================================================

@dp.message(Command("admin"))
@dp.message(F.text == "🎛️ Admin panel", AdminFilter())
async def cmd_admin_panel(message: types.Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    role = get_user_role(user.id)
    await message.answer(
        f"🎛️ <b>Admin Panel</b>\n\n"
        f"Rol: {role}\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"Quyidagi tugmalardan birini tanlang:",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user = callback.from_user
    role = get_user_role(user.id)
    try:
        await callback.message.edit_text(
            f"🎛️ <b>Admin Panel</b>\n\n"
            f"Rol: {role}\n"
            f"🆔 ID: <code>{user.id}</code>",
            reply_markup=main_menu(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "🎛️ <b>Admin Panel</b>",
            reply_markup=main_menu(),
            parse_mode="HTML"
        )
    await callback.answer()


@dp.callback_query(F.data == "refresh")
async def refresh(callback: types.CallbackQuery):
    await callback.answer("🔄 Yangilandi!", show_alert=False)


# ============================================================
# KALIT SO'ZLAR
# ============================================================

def _keywords_text_and_kb():
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    text = f"📝 <b>Kalit so'zlar</b> ({len(keywords)} ta):\n\n"
    if keywords:
        text += "\n".join([f"{i+1}. <code>{esc(kw)}</code>" for i, kw in enumerate(keywords)])
    else:
        text += "<i>Hozircha bo'sh.</i>"
    return text, keywords_menu()


@dp.callback_query(F.data == "kw_menu")
async def kw_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = _keywords_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "add_kw")
async def add_kw_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(KeywordStates.waiting_for_keyword)
    await callback.message.edit_text(
        "📝 Yangi kalit so'zni yozing:\n\n"
        "💡 Bir nechta so'zni <b>vergul</b> bilan ajratib yuborishingiz mumkin:\n"
        "<code>taksi, taxi, mashina</code>\n\n"
        "(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("kw_menu"),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(KeywordStates.waiting_for_keyword, CancelFilter())
async def cancel_add_kw(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _keywords_text_and_kb()
    await message.answer("❌ Bekor qilindi.")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(KeywordStates.waiting_for_keyword)
async def process_add_kw(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Bo'sh bo'lishi mumkin emas. Qayta yozing:")
        return
    new_kws = [k.strip() for k in raw.split(',') if k.strip()]
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    added, skipped = [], []
    for kw in new_kws:
        if kw in keywords:
            skipped.append(kw)
        else:
            keywords.append(kw)
            added.append(kw)
    save_json('keywords.json', keywords)
    parts = []
    if added:
        parts.append("✅ Qo'shildi:\n" + "\n".join(f"• <code>{esc(k)}</code>" for k in added))
    if skipped:
        parts.append("⚠️ Allaqachon mavjud:\n" + "\n".join(f"• <code>{esc(k)}</code>" for k in skipped))
    await state.clear()
    await message.answer("\n\n".join(parts), parse_mode="HTML")
    text, kb = _keywords_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("del_kw:"))
async def delete_kw(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    if 0 <= idx < len(keywords):
        removed = keywords.pop(idx)
        save_json('keywords.json', keywords)
        await callback.answer(f"✅ «{removed}» o'chirildi", show_alert=True)
    else:
        await callback.answer("⚠️ Topilmadi.", show_alert=True)
    text, kb = _keywords_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("edit_kw:"))
async def edit_kw_prompt(callback: types.CallbackQuery, state: FSMContext):
    try:
        idx = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    if not (0 <= idx < len(keywords)):
        await callback.answer("⚠️ Topilmadi.", show_alert=True)
        return
    await state.update_data(edit_idx=idx)
    await state.set_state(EditKeywordStates.waiting_for_new_keyword)
    await callback.message.edit_text(
        f"✏️ <b>Eski:</b> <code>{esc(keywords[idx])}</code>\n\n"
        f"Yangi kalit so'zni yozing:\n\n"
        f"(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("kw_menu"),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(EditKeywordStates.waiting_for_new_keyword, CancelFilter())
async def cancel_edit_kw(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _keywords_text_and_kb()
    await message.answer("❌ Bekor qilindi.")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(EditKeywordStates.waiting_for_new_keyword)
async def process_edit_kw(message: types.Message, state: FSMContext):
    data = await state.get_data()
    idx = data.get('edit_idx')
    new_kw = (message.text or "").strip()
    if not new_kw:
        await message.answer("❌ Bo'sh bo'lishi mumkin emas. Qayta yozing:")
        return
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    if idx is not None and 0 <= idx < len(keywords):
        old = keywords[idx]
        keywords[idx] = new_kw
        save_json('keywords.json', keywords)
        await message.answer(
            f"✅ <code>{esc(old)}</code> → <code>{esc(new_kw)}</code>",
            parse_mode="HTML"
        )
    else:
        await message.answer("⚠️ Topilmadi.")
    await state.clear()
    text, kb = _keywords_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# --- Bulk import/export ---

@dp.callback_query(F.data == "bulk_import")
async def bulk_import_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BulkImportStates.waiting_for_keywords)
    await callback.message.edit_text(
        "📥 <b>Ommaviy import</b>\n\n"
        "Kalit so'zlarni har biri <b>yangi qatorda</b> yoki <b>vergul bilan</b> yuboring:\n\n"
        "<code>taksi\ntaxi\nmashina</code>\n\n"
        "Yoki bitta JSON fayl yuboring.\n\n"
        "(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("kw_menu"),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(BulkImportStates.waiting_for_keywords, CancelFilter())
async def cancel_bulk(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _keywords_text_and_kb()
    await message.answer("❌ Bekor qilindi.")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(BulkImportStates.waiting_for_keywords, F.document)
async def bulk_import_document(message: types.Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith('.json'):
        await message.answer("❌ Faqat .json fayl qabul qilinadi.")
        return
    try:
        file = await bot.get_file(doc.file_id)
        content = await bot.download_file(file.file_path)
        data = json.loads(content.read().decode('utf-8'))
        if not isinstance(data, list):
            await message.answer("❌ JSON massiv bo'lishi kerak: [\"kw1\", \"kw2\"]")
            return
        new_kws = [str(x).strip() for x in data if str(x).strip()]
    except Exception as e:
        await message.answer(f"❌ Faylni o'qishda xato: {e}")
        return
    await _merge_keywords(message, state, new_kws)


@dp.message(BulkImportStates.waiting_for_keywords)
async def bulk_import_text(message: types.Message, state: FSMContext):
    raw = message.text or ""
    new_kws = [k.strip() for k in raw.replace(',', '\n').split('\n') if k.strip()]
    if not new_kws:
        await message.answer("❌ Hech qanday so'z topilmadi.")
        return
    await _merge_keywords(message, state, new_kws)


async def _merge_keywords(message, state, new_kws):
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    added, skipped = [], []
    for kw in new_kws:
        if kw in keywords:
            skipped.append(kw)
        else:
            keywords.append(kw)
            added.append(kw)
    save_json('keywords.json', keywords)
    await state.clear()
    await message.answer(
        f"✅ Qo'shildi: <b>{len(added)}</b>\n"
        f"⚠️ O'tkazib yuborildi: <b>{len(skipped)}</b>\n"
        f"📊 Jami: <b>{len(keywords)}</b>",
        parse_mode="HTML"
    )
    text, kb = _keywords_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "bulk_export")
async def bulk_export(callback: types.CallbackQuery):
    keywords = load_json('keywords.json', [])
    if isinstance(keywords, dict):
        keywords = keywords.get('list', [])
    if not keywords:
        await callback.answer("⚠️ Ro'yxat bo'sh.", show_alert=True)
        return
    content = json.dumps(keywords, ensure_ascii=False, indent=2).encode('utf-8')
    file = BufferedInputFile(content, filename="keywords.json")
    await callback.message.answer_document(file, caption=f"📤 {len(keywords)} ta kalit so'z")
    await callback.answer()


# ============================================================
# KANALLAR
# ============================================================

def _channels_text_and_kb():
    channels = load_json('channels.json', [])
    text = f"📢 <b>Kanallar</b> ({len(channels)} ta):\n\n"
    if channels:
        for i, ch in enumerate(channels):
            text += f"{i+1}. <b>{esc(ch.get('title', '?'))}</b>\n"
            text += f"   ID: <code>{esc(ch.get('id'))}</code>\n"
    else:
        text += "<i>Hozircha bo'sh.</i>"
    return text, channels_menu()


@dp.callback_query(F.data == "ch_menu")
async def ch_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = _channels_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "add_ch")
async def add_ch_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ChannelStates.waiting_for_channel)
    await callback.message.edit_text(
        "📢 <b>Kanal qo'shish</b>\n\n"
        "Kanal ID raqamini (masalan: <code>-1001234567890</code>) "
        "yoki @username ni yuboring:\n\n"
        "💡 Bir nechta kanalni vergul bilan ajratib yuborishingiz mumkin.\n\n"
        "(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("ch_menu"),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(ChannelStates.waiting_for_channel, CancelFilter())
async def cancel_ch(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _channels_text_and_kb()
    await message.answer("❌ Bekor qilindi.")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(ChannelStates.waiting_for_channel)
async def process_add_ch(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    if not parts:
        await message.answer("❌ Bo'sh bo'lishi mumkin emas.")
        return
    channels = load_json('channels.json', [])
    existing_ids = {str(c.get('id')) for c in channels}
    added = []
    errors = []
    for part in parts:
        ch_id = None
        ch_title = part
        try:
            ch_id = int(part)
            ch_title = str(ch_id)
        except ValueError:
            if part.startswith('@'):
                try:
                    chat = await bot.get_chat(part)
                    ch_id = chat.id
                    ch_title = chat.title or part
                except Exception as e:
                    errors.append(f"{part}: {e}")
                    continue
            else:
                errors.append(f"{part}: noto'g'ri format")
                continue
        if str(ch_id) in existing_ids:
            errors.append(f"{ch_title}: allaqachon mavjud")
            continue
        channels.append({"id": ch_id, "title": ch_title, "added_at": datetime.now().isoformat()})
        existing_ids.add(str(ch_id))
        added.append(ch_title)
    save_json('channels.json', channels)
    if channels:
        update_config('target_channel', channels[0]['id'])
        update_config('forward_to_channels', [c['id'] for c in channels])
    await state.clear()
    msg = ""
    if added:
        msg += "✅ Qo'shildi:\n" + "\n".join(f"• <b>{esc(a)}</b>" for a in added)
    if errors:
        msg += ("\n\n" if msg else "") + "⚠️ Xatolar:\n" + "\n".join(f"• {esc(e)}" for e in errors)
    await message.answer(msg or "❌ Hech narsa qo'shilmadi.", parse_mode="HTML")
    text, kb = _channels_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("del_ch:"))
async def delete_ch(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    channels = load_json('channels.json', [])
    if 0 <= idx < len(channels):
        removed = channels.pop(idx)
        save_json('channels.json', channels)
        update_config('forward_to_channels', [c['id'] for c in channels])
        if channels:
            update_config('target_channel', channels[0]['id'])
        else:
            update_config('target_channel', "")
        await callback.answer(f"✅ «{removed.get('title', '?')}» o'chirildi", show_alert=True)
    else:
        await callback.answer("⚠️ Topilmadi.", show_alert=True)
    text, kb = _channels_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "clear_ch")
async def clear_channels(callback: types.CallbackQuery):
    save_json('channels.json', [])
    update_config('forward_to_channels', [])
    update_config('target_channel', "")
    await callback.answer("🗑 Hammasi o'chirildi", show_alert=True)
    text, kb = _channels_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ============================================================
# BLACKLIST
# ============================================================

def _blacklist_text_and_kb():
    bl = load_blacklist()
    text = f"🚫 <b>Qora ro'yxat</b> ({len(bl)} ta):\n\n"
    if bl:
        for i, item in enumerate(bl):
            text += f"{i+1}. <b>{esc(item.get('title', '?'))}</b>\n"
            text += f"   ID: <code>{esc(item.get('id'))}</code>\n"
    else:
        text += "<i>Hozircha bo'sh.</i>\n\n"
        text += "💡 Bu yerga bot <b>kuzatmasligi kerak</b> bo'lgan chatlarni qo'shing.\n"
        text += "Masalan: bot o'zi xabar yuboradigan kanallar."
    return text, blacklist_menu()


@dp.callback_query(F.data == "bl_menu")
async def bl_menu_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = _blacklist_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "add_bl")
async def add_bl_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BlacklistStates.waiting_for_channel)
    await callback.message.edit_text(
        "🚫 <b>Qora ro'yxatga qo'shish</b>\n\n"
        "Chat ID raqamini (masalan: <code>-1001234567890</code>) "
        "yoki @username ni yuboring:\n\n"
        "💡 Bir nechta chatni vergul bilan ajratib yuborishingiz mumkin.\n\n"
        "(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("bl_menu"),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(BlacklistStates.waiting_for_channel, CancelFilter())
async def cancel_bl(message: types.Message, state: FSMContext):
    await state.clear()
    text, kb = _blacklist_text_and_kb()
    await message.answer("❌ Bekor qilindi.")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(BlacklistStates.waiting_for_channel)
async def process_add_bl(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    if not parts:
        await message.answer("❌ Bo'sh bo'lishi mumkin emas.")
        return
    added = []
    errors = []
    for part in parts:
        chat_id = None
        title = part
        try:
            chat_id = int(part)
            title = str(chat_id)
        except ValueError:
            if part.startswith('@'):
                try:
                    chat = await bot.get_chat(part)
                    chat_id = chat.id
                    title = chat.title or part
                except Exception as e:
                    errors.append(f"{part}: {e}")
                    continue
            else:
                errors.append(f"{part}: noto'g'ri format")
                continue
        if add_to_blacklist(chat_id, title):
            added.append(f"{title} (<code>{chat_id}</code>)")
        else:
            errors.append(f"{title}: allaqachon qora ro'yxatda")
    await state.clear()
    msg = ""
    if added:
        msg += "✅ Qo'shildi:\n" + "\n".join(f"• {a}" for a in added)
    if errors:
        msg += ("\n\n" if msg else "") + "⚠️ Xatolar:\n" + "\n".join(f"• {esc(e)}" for e in errors)
    await message.answer(msg or "❌ Hech narsa qo'shilmadi.", parse_mode="HTML")
    text, kb = _blacklist_text_and_kb()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("del_bl:"))
async def delete_bl(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    bl = load_blacklist()
    if 0 <= idx < len(bl):
        removed = bl.pop(idx)
        save_blacklist(bl)
        await callback.answer(f"✅ «{removed.get('title', '?')}» o'chirildi", show_alert=True)
    else:
        await callback.answer("⚠️ Topilmadi.", show_alert=True)
    text, kb = _blacklist_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "clear_bl")
async def clear_blacklist(callback: types.CallbackQuery):
    save_blacklist([])
    await callback.answer("🗑 Hammasi o'chirildi", show_alert=True)
    text, kb = _blacklist_text_and_kb()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


# ============================================================
# STATISTIKA
# ============================================================

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    stats = load_json('stats.json', {"total_matches": 0, "keywords": {}, "daily": {}})
    total = stats.get('total_matches', 0)
    keywords = stats.get('keywords', {})
    daily = stats.get('daily', {})
    users = load_json('users.json', [])

    text = "📊 <b>Statistika</b>\n\n"
    text += f"🔢 Jami topilgan: <code>{total}</code>\n"
    text += f"📝 Kalit so'zlar: <code>{len(load_json('keywords.json', []))}</code>\n"
    text += f"📢 Kanallar: <code>{len(load_json('channels.json', []))}</code>\n"
    text += f"🚫 Qora ro'yxat: <code>{len(load_blacklist())}</code>\n"
    text += f"👥 Foydalanuvchilar: <code>{len(users)}</code>\n\n"

    if keywords:
        text += "<b>Top 10 kalit so'z:</b>\n"
        sorted_kw = sorted(keywords.items(), key=lambda x: x[1], reverse=True)
        for kw, count in sorted_kw[:10]:
            text += f"• <code>{esc(kw)}</code>: {count}\n"

    if daily:
        text += "\n<b>Oxirgi 7 kun:</b>\n"
        sorted_daily = sorted(daily.items(), reverse=True)[:7]
        for day, count in sorted_daily:
            text += f"• {esc(day)}: {count}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="stats")],
        [InlineKeyboardButton(text="🗑 Tozalash", callback_data="clear_stats")],
        [InlineKeyboardButton(text="📤 Eksport (JSON)", callback_data="export_stats")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="main_menu")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "clear_stats")
async def clear_stats(callback: types.CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    save_json('stats.json', {"total_matches": 0, "keywords": {}, "daily": {}})
    await callback.answer("✅ Statistika tozalandi", show_alert=True)
    await show_stats(callback)


@dp.callback_query(F.data == "export_stats")
async def export_stats(callback: types.CallbackQuery):
    stats = load_json('stats.json', {})
    content = json.dumps(stats, ensure_ascii=False, indent=2).encode('utf-8')
    file = BufferedInputFile(content, filename="stats.json")
    await callback.message.answer_document(file, caption="📤 Statistika eksporti")
    await callback.answer()


# ============================================================
# FOYDALANUVCHILAR
# ============================================================

@dp.callback_query(F.data == "users_menu")
async def users_menu_handler(callback: types.CallbackQuery):
    users = load_json('users.json', [])
    text = f"👥 <b>Foydalanuvchilar</b> ({len(users)} ta):\n\n"
    if users:
        text += f"Oxirgi {min(len(users), 20)} ta:\n\n"
        for u in users[-20:][::-1]:
            name = u.get('first_name', '') or u.get('username', '') or '?'
            text += f"• <b>{esc(name)}</b> — <code>{u.get('id')}</code>\n"
    else:
        text += "<i>Hozircha foydalanuvchilar yo'q.</i>"
    try:
        await callback.message.edit_text(text, reply_markup=users_menu(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=users_menu(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("user_info:"))
async def user_info(callback: types.CallbackQuery):
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    users = load_json('users.json', [])
    user = next((u for u in users if u.get('id') == user_id), None)
    if not user:
        await callback.answer("⚠️ Topilmadi.", show_alert=True)
        return
    text = (
        f"👤 <b>Foydalanuvchi ma'lumotlari</b>\n\n"
        f"🆔 ID: <code>{user.get('id')}</code>\n"
        f"📛 Ism: <b>{esc(user.get('first_name', '') or '—')}</b>\n"
        f"📛 Familiya: <b>{esc(user.get('last_name', '') or '—')}</b>\n"
        f"🔗 Username: @{esc(user.get('username', '') or '—')}\n"
        f"📅 Birinchi marta: <code>{esc(user.get('first_seen', '—'))}</code>\n"
        f"📅 Oxirgi marta: <code>{esc(user.get('last_seen', '—'))}</code>\n"
        f"🎭 Rol: {get_user_role(user_id)}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="users_menu")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "export_users")
async def export_users(callback: types.CallbackQuery):
    users = load_json('users.json', [])
    content = json.dumps(users, ensure_ascii=False, indent=2).encode('utf-8')
    file = BufferedInputFile(content, filename="users.json")
    await callback.message.answer_document(file, caption=f"📤 {len(users)} ta foydalanuvchi")
    await callback.answer()


# ============================================================
# SOZLAMALAR
# ============================================================

@dp.callback_query(F.data == "settings")
async def settings_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    cfg = get_config()
    api_id, api_hash = get_api_credentials()
    if has_api_credentials():
        masked_hash = api_hash[:6] + "..." + api_hash[-4:]
        api_line = f"✅ <code>{api_id}</code> / <code>{masked_hash}</code>"
    else:
        api_line = "❌ <i>sozlanmagan</i>"
    text = (
        "⚙️ <b>Sozlamalar</b>\n\n"
        f"🆔 API: {api_line}\n"
        f"⏱ Interval: <code>{esc(cfg.get('search_interval', 60))}</code> sekund\n"
        f"📢 Asosiy kanal: <code>{esc(cfg.get('target_channel', '—'))}</code>\n"
    )
    try:
        await callback.message.edit_text(text, reply_markup=settings_menu(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=settings_menu(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "api_info")
async def api_info(callback: types.CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    api_id, api_hash = get_api_credentials()
    if has_api_credentials():
        await callback.answer(
            f"🆔 API_ID: {api_id}\n🔑 API_HASH: {api_hash[:6]}...{api_hash[-4:]}",
            show_alert=True
        )
    else:
        await callback.answer("❌ API sozlanmagan.", show_alert=True)


@dp.callback_query(F.data == "download_config")
async def download_config(callback: types.CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    cfg = get_config()
    safe = dict(cfg)
    if 'api_hash' in safe and safe['api_hash']:
        safe['api_hash'] = safe['api_hash'][:6] + '***HIDDEN***'
    content = json.dumps(safe, ensure_ascii=False, indent=2).encode('utf-8')
    file = BufferedInputFile(content, filename="config.json")
    await callback.message.answer_document(file, caption="📄 config.json")
    await callback.answer()


@dp.callback_query(F.data == "set_interval")
async def set_interval(callback: types.CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    cfg = get_config()
    current = cfg.get('search_interval', 60)
    options = [30, 60, 120, 300, 600]
    try:
        idx = options.index(current)
        new_val = options[(idx + 1) % len(options)]
    except ValueError:
        new_val = 60
    update_config('search_interval', new_val)
    await callback.answer(f"✅ Interval: {new_val}s", show_alert=True)
    await settings_handler(callback, state)


# ============================================================
# ADMINLAR
# ============================================================

@dp.callback_query(F.data == "admins_menu")
async def admins_menu_handler(callback: types.CallbackQuery):
    admins = get_admins()
    superadmin = admins.get('superadmin', 'N/A')
    admin_list = admins.get('admins', [])
    text = "👮 <b>Adminlar</b>\n\n"
    text += f"👑 Superadmin: <code>{esc(superadmin)}</code>\n\n"
    text += f"👤 Adminlar ({len(admin_list)} ta):\n"
    if admin_list:
        text += "\n".join([f"• <code>{esc(a)}</code>" for a in admin_list])
    else:
        text += "<i>Adminlar yo'q</i>"
    try:
        await callback.message.edit_text(text, reply_markup=admins_menu(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=admins_menu(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "add_admin")
async def add_admin_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_admin_id)
    await callback.message.edit_text(
        "👤 Yangi admin ID sini yuboring:\n\n(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("admins_menu")
    )
    await callback.answer()


@dp.message(AdminStates.waiting_for_admin_id, CancelFilter())
async def cancel_add_admin(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Bekor qilindi.")
    await admins_menu_handler_message(message)


async def admins_menu_handler_message(message: types.Message):
    admins = get_admins()
    superadmin = admins.get('superadmin', 'N/A')
    admin_list = admins.get('admins', [])
    text = f"👮 <b>Adminlar</b>\n\n👑 Superadmin: <code>{esc(superadmin)}</code>\n\n"
    text += f"👤 Adminlar ({len(admin_list)} ta):\n"
    text += "\n".join([f"• <code>{esc(a)}</code>" for a in admin_list]) if admin_list else "<i>Adminlar yo'q</i>"
    await message.answer(text, reply_markup=admins_menu(), parse_mode="HTML")


@dp.message(AdminStates.waiting_for_admin_id)
async def process_add_admin(message: types.Message, state: FSMContext):
    if not is_superadmin(message.from_user.id):
        await message.answer("⛔ Ruxsat yo'q.")
        await state.clear()
        return
    try:
        new_admin = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Iltimos, raqamli ID yuboring:")
        return
    admins = get_admins()
    admin_list = admins.get('admins', [])
    if new_admin in admin_list or new_admin == admins.get('superadmin'):
        await message.answer("⚠️ Bu foydalanuvchi allaqachon admin.")
    else:
        admin_list.append(new_admin)
        admins['admins'] = admin_list
        save_json('admins.json', admins)
        await message.answer(f"✅ <code>{new_admin}</code> admin qilib qo'shildi.", parse_mode="HTML")
    await state.clear()
    await admins_menu_handler_message(message)


@dp.callback_query(F.data.startswith("del_admin:"))
async def delete_admin(callback: types.CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    try:
        admin_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    admins = get_admins()
    admin_list = admins.get('admins', [])
    if admin_id in admin_list:
        admin_list.remove(admin_id)
        admins['admins'] = admin_list
        save_json('admins.json', admins)
        await callback.answer(f"✅ {admin_id} o'chirildi", show_alert=True)
    else:
        await callback.answer("⚠️ Topilmadi.", show_alert=True)
    await admins_menu_handler(callback)


# ============================================================
# SUPERADMIN O'TKAZISH
# ============================================================

@dp.callback_query(F.data == "transfer_superadmin")
async def transfer_superadmin_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("⛔ Faqat superadmin!", show_alert=True)
        return
    await state.set_state(SuperAdminTransferStates.waiting_for_new_superadmin)
    await callback.message.edit_text(
        "👑 <b>Superadmin o'tkazish</b>\n\n"
        "Yangi superadmin ID sini yuboring.\n\n"
        "⚠️ <b>Diqqat:</b> bu amal qaytarilmaydi!\n\n"
        "(Bekor qilish uchun /cancel)",
        reply_markup=back_kb("admins_menu"),
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(SuperAdminTransferStates.waiting_for_new_superadmin, CancelFilter())
async def cancel_transfer(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Bekor qilindi.")
    await admins_menu_handler_message(message)


@dp.message(SuperAdminTransferStates.waiting_for_new_superadmin)
async def process_transfer_superadmin(message: types.Message, state: FSMContext):
    if not is_superadmin(message.from_user.id):
        await message.answer("⛔ Ruxsat yo'q.")
        await state.clear()
        return
    try:
        new_superadmin = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Iltimos, raqamli ID yuboring:")
        return
    admins = get_admins()
    old_superadmin = admins.get('superadmin')
    admin_list = admins.get('admins', [])
    if new_superadmin == old_superadmin:
        await message.answer("⚠️ Bu allaqachon superadmin.")
        await state.clear()
        return
    if new_superadmin in admin_list:
        admin_list.remove(new_superadmin)
    if old_superadmin and old_superadmin not in admin_list:
        admin_list.append(old_superadmin)
    admins['superadmin'] = new_superadmin
    admins['admins'] = admin_list
    save_json('admins.json', admins)
    await state.clear()
    await message.answer(
        f"✅ Superadmin o'tkazildi:\n"
        f"👑 Yangi: <code>{new_superadmin}</code>\n"
        f"👤 Siz endi oddiy adminsiz.",
        parse_mode="HTML"
    )
    await admins_menu_handler_message(message)


# ============================================================
# API SOZLASH
# ============================================================

@dp.message(F.text == "⚙️ API sozlash", AdminFilter())
@dp.message(F.text == "⚙️ API sozlamalari", AdminFilter())
async def api_setup_start(message: types.Message, state: FSMContext):
    api_id, api_hash = get_api_credentials()
    if has_api_credentials():
        masked_hash = api_hash[:6] + "..." + api_hash[-4:]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Yangilash", callback_data="api_update")],
        ])
        await message.answer(
            "⚙️ <b>API sozlamalari</b>\n\n"
            f"🆔 API ID: <code>{api_id}</code>\n"
            f"🔑 API HASH: <code>{masked_hash}</code>\n\n"
            "Yangilash uchun «✏️ Yangilash» tugmasini bosing.",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "⚙️ <b>API sozlash</b>\n\n"
            "API_ID va API_HASH ni <b>https://my.telegram.org</b> saytidan oling.\n\n"
            "Endi <b>API_ID</b> ni yuboring (faqat raqam):",
            reply_markup=cancel_reply_kb(),
            parse_mode="HTML"
        )
        await state.set_state(ApiStates.waiting_api_id)


@dp.callback_query(F.data == "api_update")
async def api_update(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ApiStates.waiting_api_id)
    await callback.message.answer(
        "🆔 Yangi <b>API_ID</b> ni yuboring (faqat raqam):",
        reply_markup=cancel_reply_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(ApiStates.waiting_api_id, CancelFilter())
async def cancel_api_id(message: types.Message, state: FSMContext):
    await state.clear()
    connected = is_session_exists(message.from_user.id)
    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=admin_reply_menu(is_connected=connected),
    )


@dp.message(ApiStates.waiting_api_id)
async def process_api_id(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ API_ID faqat raqamlardan iborat. Qayta yuboring:")
        return
    api_id = int(text)
    if api_id < 1000:
        await message.answer("❌ API_ID juda kichik. Qayta tekshirib yuboring:")
        return
    await state.update_data(new_api_id=api_id)
    await state.set_state(ApiStates.waiting_api_hash)
    await message.answer(
        "✅ Qabul qilindi.\n\n"
        "🔑 Endi <b>API_HASH</b> ni yuboring (32 belgili hex string):",
        reply_markup=cancel_reply_kb(),
        parse_mode="HTML",
    )


@dp.message(ApiStates.waiting_api_hash, CancelFilter())
async def cancel_api_hash(message: types.Message, state: FSMContext):
    await state.clear()
    connected = is_session_exists(message.from_user.id)
    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=admin_reply_menu(is_connected=connected),
    )


@dp.message(ApiStates.waiting_api_hash)
async def process_api_hash(message: types.Message, state: FSMContext):
    api_hash = (message.text or "").strip().strip("'\"")
    if len(api_hash) != 32:
        await message.answer(
            f"❌ API_HASH 32 belgili bo'lishi kerak.\n"
            f"Sizda {len(api_hash)} belgi. Qayta yuboring:"
        )
        return
    try:
        int(api_hash, 16)
    except ValueError:
        await message.answer("❌ API_HASH faqat hex belgilardan iborat. Qayta yuboring:")
        return
    data = await state.get_data()
    new_api_id = data.get("new_api_id")
    if not new_api_id:
        await message.answer("❌ Xatolik: API_ID yo'qoldi. Qaytadan boshlang.")
        await state.clear()
        return
    cfg = get_config()
    cfg["api_id"] = new_api_id
    cfg["api_hash"] = api_hash
    save_json('config.json', cfg)
    await state.clear()
    connected = is_session_exists(message.from_user.id)
    await message.answer(
        f"✅ <b>API sozlamalari saqlandi!</b>\n\n"
        f"🆔 API ID: <code>{new_api_id}</code>\n"
        f"🔑 API HASH: <code>{api_hash[:6]}...{api_hash[-4:]}</code>\n\n"
        f"Endi profilingizni ulashingiz mumkin.",
        reply_markup=admin_reply_menu(is_connected=connected, has_api=True),
        parse_mode="HTML",
    )


# ============================================================
# PROFIL: ULASH
# ============================================================

@dp.message(F.text == "🔗 Profilni ulash", AdminFilter())
async def start_login(message: types.Message, state: FSMContext):
    if not has_api_credentials():
        await message.answer(
            "⚠️ Avval API sozlamalarini kiriting!\n"
            "«⚙️ API sozlash» tugmasini bosing.",
            reply_markup=admin_reply_menu(has_api=False),
        )
        return
    if is_session_exists(message.from_user.id):
        await message.answer(
            "⚠️ Sizda allaqachon ulangan sessiya bor.\n"
            "Avval «🔗 Profilni uzish» tugmasini bosing.",
            reply_markup=admin_reply_menu(is_connected=True),
        )
        return
    await message.answer(
        "📱 Iltimos, telefon raqamingizni yuboring.\n"
        "(Tugma orqali — Telegram kontaktni avtomatik oladi)",
        reply_markup=phone_reply_kb(),
    )
    await state.set_state(LoginStates.waiting_phone)


@dp.message(LoginStates.waiting_phone, CancelFilter())
async def cancel_login_phone(message: types.Message, state: FSMContext):
    await state.clear()
    connected = is_session_exists(message.from_user.id)
    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=admin_reply_menu(is_connected=connected),
    )


@dp.message(LoginStates.waiting_phone, F.contact)
async def handle_phone(message: types.Message, state: FSMContext):
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("❌ Faqat <b>o'zingizning</b> raqamingizni yuboring!", parse_mode="HTML")
        return
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    api_id, api_hash = get_api_credentials()
    if not api_id or not api_hash:
        await message.answer("❌ API sozlamalari topilmadi.")
        await state.clear()
        return
    status_msg = await message.answer("📨 Tasdiqlash kodi yuborilmoqda...")
    old_client = temp_clients.pop(message.from_user.id, None)
    if old_client:
        try:
            await old_client.disconnect()
        except Exception:
            pass
    session_path = str(SESSIONS_DIR / str(message.from_user.id))
    client = TelegramClient(session_path, api_id, api_hash)
    try:
        await client.connect()
        await client.send_code_request(phone)
        temp_clients[message.from_user.id] = client
        await state.update_data(phone=phone)
        await state.set_state(LoginStates.waiting_code)
        await status_msg.edit_text(
            "✅ Kod yuborildi!\n\n"
            "Iltimos, tasdiqlash kodini kiriting.\n"
            "Format: <code>1 2 3 4 5</code> yoki <code>12345</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        temp_clients.pop(message.from_user.id, None)
        await state.clear()
        err = str(e)
        extra = ""
        if "api_id/api_hash" in err:
            extra = "\n\n💡 API sozlamalarini «⚙️ API sozlamalari» orqali qayta kiriting."
        await status_msg.edit_text(
            f"❌ Yuborishda xatolik:\n<code>{err}</code>{extra}",
            parse_mode="HTML",
        )
        await message.answer(
            "Asosiy menyu:",
            reply_markup=admin_reply_menu(is_connected=is_session_exists(message.from_user.id)),
        )


# ============================================================
# PROFIL: KOD
# ============================================================

@dp.message(LoginStates.waiting_code, CancelFilter())
async def cancel_login_code(message: types.Message, state: FSMContext):
    client = temp_clients.pop(message.from_user.id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    await state.clear()
    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=admin_reply_menu(is_connected=is_session_exists(message.from_user.id)),
    )


@dp.message(LoginStates.waiting_code)
async def handle_code(message: types.Message, state: FSMContext):
    code = (message.text or "").replace(" ", "").replace(".", "").replace("-", "").strip()
    if not code.isdigit():
        await message.answer("❌ Kod faqat raqamlardan iborat. Qayta kiriting:")
        return
    data = await state.get_data()
    phone = data.get("phone")
    client = temp_clients.get(message.from_user.id)
    if not client:
        await message.answer(
            "❌ Sessiya muddati tugadi. /start dan qayta boshlang.",
            reply_markup=admin_reply_menu(is_connected=is_session_exists(message.from_user.id)),
        )
        await state.clear()
        return
    try:
        await client.sign_in(phone, code)
        await finish_login(message, state, client)
    except SessionPasswordNeededError:
        await state.set_state(LoginStates.waiting_password)
        await message.answer(
            "🔐 Ikki bosqichli tekshiruv (2FA) yoqilgan.\n"
            "Iltimos, parolingizni kiriting:",
            reply_markup=cancel_reply_kb(),
        )
    except Exception as e:
        err = str(e)
        if "PhoneCodeExpired" in err or "expired" in err.lower():
            try:
                await client.send_code_request(phone)
                await message.answer("⚠️ Kod eskirgan. Yangi kod yuborildi. Qayta kiriting:")
                return
            except Exception as e2:
                await message.answer(f"❌ Yangi kod yuborishda xato: {e2}")
        await message.answer(
            f"❌ Kod xato: <code>{err}</code>\n\nQayta kiriting:",
            parse_mode="HTML",
        )


# ============================================================
# PROFIL: 2FA
# ============================================================

@dp.message(LoginStates.waiting_password, CancelFilter())
async def cancel_login_password(message: types.Message, state: FSMContext):
    client = temp_clients.pop(message.from_user.id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    await state.clear()
    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=admin_reply_menu(is_connected=is_session_exists(message.from_user.id)),
    )


@dp.message(LoginStates.waiting_password)
async def handle_password(message: types.Message, state: FSMContext):
    client = temp_clients.get(message.from_user.id)
    if not client:
        await message.answer("❌ Sessiya yo'q. /start dan qayta boshlang.")
        await state.clear()
        return
    password = (message.text or "").strip()
    if not password:
        await message.answer("❌ Parol bo'sh bo'lishi mumkin emas.")
        return
    try:
        await client.sign_in(password=password)
        await finish_login(message, state, client)
    except Exception as e:
        await message.answer(
            f"❌ Parol xato: <code>{str(e)}</code>\n\nQayta kiriting:",
            parse_mode="HTML",
        )


# ============================================================
# PROFIL: TUGATISH
# ============================================================

async def finish_login(message: types.Message, state: FSMContext, client: TelegramClient):
    try:
        me = await client.get_me()
    except Exception as e:
        await message.answer(f"❌ Ma'lumot olishda xatolik: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        temp_clients.pop(message.from_user.id, None)
        await state.clear()
        return
    # Sessiyani saqlash
    try:
        await client.disconnect()
    except Exception:
        pass
    temp_clients.pop(message.from_user.id, None)
    await state.clear()
    username_line = f"@{me.username}" if me.username else "—"
    await message.answer(
        f"✅ <b>Muvaffaqiyatli ulandi!</b>\n\n"
        f"👤 Ism: <b>{me.first_name or ''}</b>\n"
        f"🆔 ID: <code>{me.id}</code>\n"
        f"📞 Telefon: <code>{me.phone or '—'}</code>\n"
        f"🔗 Username: {username_line}\n\n"
        f"🔄 Userbot ishga tushirilmoqda...",
        reply_markup=admin_reply_menu(is_connected=True),
        parse_mode="HTML",
    )
    # Userbotni ishga tushirish
    spawn(start_userbot_for_admin(message.from_user.id))


# ============================================================
# USERBOT: ASOSIY MANTIQ
# ============================================================

async def start_userbot_for_admin(admin_id: int):
    """Admin uchun userbotni ishga tushirish."""
    try:
        if not is_admin(admin_id):
            return
        if admin_id in active_userbots:
            log.info(f"Userbot allaqachon faol: {admin_id}")
            return
        if not is_session_exists(admin_id):
            log.warning(f"Sessiya topilmadi: {admin_id}")
            return
        api_id, api_hash = get_api_credentials()
        if not api_id or not api_hash:
            return
        session_path = str(SESSIONS_DIR / str(admin_id))
        client = TelegramClient(
            session_path, api_id, api_hash,
            device_model="Desktop",
            system_version="Windows 10",
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log.warning(f"Userbot avtorizatsiya qilinmagan: {admin_id}")
                await client.disconnect()
                return

            me = await client.get_me()
            log.info(f"✅ Userbot ishga tushdi: {me.first_name} (admin: {admin_id})")

            # Entity keshi to'ldirish (muhim!)
            try:
                async for _ in client.iter_dialogs(limit=None):
                    pass
                log.info(f"📚 Entity keshi yuklandi: {admin_id}")
            except Exception as e:
                log.warning(f"Dialoglarni yuklashda xato: {e}")

            active_userbots[admin_id] = client

            # Barcha yangi xabarlarni kuzatish
            @client.on(tl_events.NewMessage(incoming=True))
            async def on_new_message(event):
                try:
                    await process_incoming_message(admin_id, event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception(f"on_new_message xatosi: {e}")

            # Xabar tahrirlanishi
            @client.on(tl_events.MessageEdited(incoming=True))
            async def on_edit_message(event):
                try:
                    await process_incoming_message(admin_id, event)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.exception(f"on_edit_message xatosi: {e}")

        except Exception as e:
            log.exception(f"Userbot ishga tushirishda xato: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
    except asyncio.CancelledError:
        raise


def normalize_chat_id(chat_id) -> str:
    """Chat ID ni bir xil formatga keltirish."""
    if chat_id is None:
        return ""
    return str(chat_id)


async def process_incoming_message(admin_id: int, event):
    """Kelgan xabarni kalit so'zlar bo'yicha tekshirish."""
    try:
        # === FILTRLAR ===
        if event.out:
            return

        chat_id = event.chat_id

        # Qora ro'yxat
        if is_blacklisted(chat_id):
            return

        # Yuboriladigan kanallardan kelgan xabar
        channels = load_json('channels.json', [])
        channel_ids = {normalize_chat_id(c.get('id')) for c in channels}
        if normalize_chat_id(chat_id) in channel_ids:
            return

        # Xabar matni
        message_text = event.message.message or ""
        if not message_text:
            return

        # === KALIT SO'ZLAR ===
        keywords = load_json('keywords.json', [])
        if isinstance(keywords, dict):
            keywords = keywords.get('list', [])
        if not keywords:
            return

        text_lower = message_text.lower()
        matched_kws = [kw for kw in keywords if kw and kw.lower() in text_lower]
        if not matched_kws:
            return

        log.info(f"🎯 Kalit so'z topildi: {matched_kws} (chat: {chat_id})")

        # === STATISTIKA ===
        try:
            update_stats(matched_kws)
        except Exception as e:
            log.warning(f"Statistika xatosi: {e}")

        # === KANALLAR ===
        if not channels:
            log.warning("Kanallar ro'yxati bo'sh")
            return

        # === MA'LUMOTLAR ===
        try:
            chat = await event.get_chat()
            sender = await event.get_sender()
        except Exception as e:
            log.warning(f"Chat/sender olishda xato: {e}")
            chat = None
            sender = None

        chat_title = (
            getattr(chat, 'title', None)
            or getattr(chat, 'username', None)
            or str(chat_id)
        ) if chat else str(chat_id)

        sender_name = ""
        sender_username = ""
        sender_id_str = ""
        if sender:
            first = getattr(sender, 'first_name', '') or ''
            last = getattr(sender, 'last_name', '') or ''
            sender_name = f"{first} {last}".strip()
            sender_username = getattr(sender, 'username', '') or ''
            sender_id_str = str(getattr(sender, 'id', '') or '')

        msg_link = build_message_link(chat, event.message.id)

        # === FORMAT ===
        caption = format_message(
            matched_keywords=matched_kws,
            chat_title=chat_title,
            message_text=message_text,
        )

        # === INLINE TUGMALAR ===
        buttons = []

        # 1) Yuboruvchi tugmasi
        sender_btn = build_sender_button(sender_name, sender_username, sender_id_str)
        if sender_btn:
            buttons.append([sender_btn])

        # 2) Xabarni ko'rish tugmasi
        if msg_link:
            buttons.append([
                InlineKeyboardButton(text="👁 Xabarni ko'rish", url=msg_link)
            ])

        # 3) Chatga o'tish tugmasi (agar chat username bo'lsa)
        chat_username = getattr(chat, 'username', None) if chat else None
        if chat_username:
            buttons.append([
                InlineKeyboardButton(
                    text=f"📢 Kanalga o'tish",
                    url=f"https://t.me/{chat_username}"
                )
            ])

        kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

        # === YUBORISH ===
        for ch in channels:
            try:
                await bot.send_message(
                    chat_id=ch['id'],
                    text=caption,
                    reply_markup=kb,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                log.info(f"✅ Yuborildi: {ch.get('title', ch['id'])}")
            except Exception as e:
                log.error(f"❌ Kanalga yuborishda xato ({ch.get('title', ch['id'])}): {e}")

    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception(f"process_incoming_message xatosi: {e}")


def build_message_link(chat, message_id: int) -> Optional[str]:
    """Telegram xabariga havola qurish."""
    try:
        if chat is None:
            return None
        username = getattr(chat, 'username', None)
        if username:
            return f"https://t.me/{username}/{message_id}"
        chat_id = getattr(chat, 'id', None)
        if chat_id:
            cid = str(chat_id)
            if cid.startswith('-100'):
                cid = cid[4:]
            elif cid.startswith('-'):
                cid = cid[1:]
            return f"https://t.me/c/{cid}/{message_id}"
    except Exception:
        pass
    return None


def build_sender_button(sender_name: str, sender_username: str, sender_id: str):
    """
    Yuboruvchi uchun inline tugma qurish.
    - Agar username bo'lsa → https://t.me/username
    - Agar ID bo'lsa → tg://user?id=ID
    - Aks holda → None
    """
    display = (sender_name or "").strip()[:30]
    if not display:
        display = f"ID {sender_id}" if sender_id else "Yuboruvchi"

    # Username bo'lsa — to'g'ridan-to'g'ri havola
    if sender_username:
        return InlineKeyboardButton(
            text=f"👤 {display}",
            url=f"https://t.me/{sender_username}"
        )

    # ID bo'lsa — Telegram profiliga havola
    if sender_id and sender_id.isdigit():
        return InlineKeyboardButton(
            text=f"👤 {display}",
            url=f"tg://user?id={sender_id}"
        )

    return None


def format_message(
    matched_keywords,
    chat_title,
    message_text,
) -> str:
    """Xabarni chiroyli HTML formatda tayyorlash."""
    parts = []

    # ─── SARLAVHA ───
    parts.append("🔍 <b>KALIT SO'Z TOPILDI</b>")
    parts.append("━━━━━━━━━━━━━━━━━━━━")
    parts.append("")

    # ─── KALIT SO'ZLAR ───
    kw_line = "  ".join(f"<code>#{esc(kw)}</code>" for kw in matched_keywords)
    parts.append(f"⚡️ <b>So'zlar:</b>  {kw_line}")
    parts.append("")

    # ─── XABAR MATNI ───
    preview = message_text
    if len(preview) > 800:
        preview = preview[:800] + "…"
    safe_preview = esc(preview)

    parts.append("╭───────────────────")
    parts.append("│  💬 <b>XABAR MATNI</b>")
    parts.append("│")
    for line in safe_preview.split("\n"):
        parts.append(f"│  {line}")
    parts.append("╰───────────────────")
    parts.append("")

    # ─── CHAT MA'LUMOTI ───
    parts.append(f"📢 <b>Chat:</b>  <i>{esc(chat_title)}</i>")
    parts.append("")

    # ─── VAQT ───
    now = datetime.now().strftime("%d.%m.%Y | %H:%M:%S")
    parts.append(f"🕐 <b>Vaqt:</b>  <code>{now}</code>")

    return "\n".join(parts)


def update_stats(matched_keywords):
    """Statistikani yangilash."""
    stats = load_json('stats.json', {"total_matches": 0, "keywords": {}, "daily": {}})
    stats['total_matches'] = stats.get('total_matches', 0) + 1

    kw_stats = stats.setdefault('keywords', {})
    for kw in matched_keywords:
        kw_stats[kw] = kw_stats.get(kw, 0) + 1

    today = datetime.now().strftime("%Y-%m-%d")
    daily = stats.setdefault('daily', {})
    daily[today] = daily.get(today, 0) + 1

    save_json('stats.json', stats)

# ============================================================
# PROFIL: MA'LUMOTLAR
# ============================================================

@dp.message(F.text == "👤 Profil ma'lumotlari", AdminFilter())
async def profile_info(message: types.Message, state: FSMContext):
    if not has_api_credentials():
        await message.answer("⚠️ Avval API sozlamalarini kiriting.")
        return
    if not is_session_exists(message.from_user.id):
        await message.answer("⚠️ Profil ulanmagan.", reply_markup=admin_reply_menu())
        return
    api_id, api_hash = get_api_credentials()
    session_path = str(SESSIONS_DIR / str(message.from_user.id))
    client = TelegramClient(session_path, api_id, api_hash)
    try:
        await client.connect()
        me = await client.get_me()
        session_file = SESSIONS_DIR / f"{message.from_user.id}.session"
        size_kb = session_file.stat().st_size / 1024 if session_file.exists() else 0
        is_active = message.from_user.id in active_userbots
        username_line = f"@{me.username}" if me.username else "—"
        await message.answer(
            f"👤 <b>Profil ma'lumotlari</b>\n\n"
            f"👤 Ism: <b>{me.first_name or ''} {me.last_name or ''}</b>\n"
            f"🆔 ID: <code>{me.id}</code>\n"
            f"📞 Telefon: <code>{me.phone or '—'}</code>\n"
            f"🔗 Username: {username_line}\n"
            f"🌐 Til: <code>{me.lang_code or '—'}</code>\n"
            f"💾 Sessiya: <code>{size_kb:.1f} KB</code>\n"
            f"⚙️ Userbot: {'✅ faol' if is_active else '❌ faol emas'}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Xatolik:\n<code>{e}</code>", parse_mode="HTML")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ============================================================
# PROFIL: UZISH
# ============================================================

@dp.message(F.text == "🔗 Profilni uzish", AdminFilter())
async def disconnect_profile(message: types.Message, state: FSMContext):
    await state.clear()
    # Userbotni to'xtatish
    client = active_userbots.pop(message.from_user.id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    session_file = SESSIONS_DIR / f"{message.from_user.id}.session"
    journal_file = SESSIONS_DIR / f"{message.from_user.id}.session-journal"
    deleted = []
    for f in (session_file, journal_file):
        if f.exists():
            try:
                f.unlink()
                deleted.append(f.name)
            except Exception as e:
                await message.answer(f"❌ O'chirishda xatolik ({f.name}): {e}")
                return
    if deleted:
        await message.answer(
            "✅ Profil uzildi va userbot to'xtatildi.",
            reply_markup=admin_reply_menu(is_connected=False),
        )
    else:
        await message.answer(
            "⚠️ Sessiya topilmadi.",
            reply_markup=admin_reply_menu(is_connected=False),
        )


# ============================================================
# FALLBACK
# ============================================================

@dp.message()
async def fallback(message: types.Message):
    register_user(message.from_user)
    if is_admin(message.from_user.id):
        connected = is_session_exists(message.from_user.id)
        has_api = has_api_credentials()
        await message.answer(
            "🤔 Tushunmadim. Iltimos, menyudan foydalaning.\n\n"
            "Boshlash uchun /start yuboring.",
            reply_markup=admin_reply_menu(is_connected=connected, has_api=has_api),
        )
    else:
        await message.answer(
            "🤔 Tushunmadim. Iltimos, tugmalardan foydalaning.",
            reply_markup=user_reply_menu(is_admin_user=False),
        )


# ============================================================
# ISHGA TUSHIRISH
# ============================================================

async def startup_userbots():
    """Bot ishga tushganda barcha adminlarning sessiyalarini yuklash."""
    try:
        admins = get_admins()
        all_admin_ids = {admins.get('superadmin')} | set(admins.get('admins', []))
        all_admin_ids.discard(0)
        all_admin_ids.discard(None)
        for admin_id in all_admin_ids:
            if is_session_exists(admin_id):
                log.info(f"🔄 Admin {admin_id} uchun userbot ishga tushirilmoqda...")
                await start_userbot_for_admin(admin_id)
            else:
                log.info(f"ℹ️ Admin {admin_id} uchun sessiya yo'q")
    except Exception as e:
        log.exception(f"startup_userbots xatosi: {e}")


async def shutdown_all():
    """Barcha userbotlarni yopish."""
    for admin_id, client in list(active_userbots.items()):
        try:
            await client.disconnect()
            log.info(f"🔌 Userbot uzildi: {admin_id}")
        except Exception as e:
            log.warning(f"Userbot uzishda xato ({admin_id}): {e}")
    active_userbots.clear()
    # Background tasklarni bekor qilish
    for task in list(background_tasks):
        if not task.done():
            task.cancel()


async def main():
    log.info("🚀 Bot ishga tushmoqda...")
    admins = get_admins()
    log.info(f"👑 Superadmin: {admins.get('superadmin')}")
    log.info(f"👤 Adminlar: {admins.get('admins', [])}")
    api_id, api_hash = get_api_credentials()
    if not api_id or not api_hash:
        log.warning("⚠️ API sozlanmagan")
    else:
        log.info(f"✅ API sozlangan (ID: {api_id})")
    # Userbotlarni ishga tushirish
    spawn(startup_userbots())
    try:
        await dp.start_polling(bot)
    finally:
        await shutdown_all()
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot to'xtatildi.")
