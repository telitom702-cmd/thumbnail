# ============================================================
# Telegram Auto Cleaner
# File: plugins/auto_cleaner.py
# ============================================================

import re
import logging
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from plugins.database.database import db
from plugins.config import Config

LOGGER = logging.getLogger(__name__)

# ============================================================
# SETTINGS
# ============================================================

COLLECTION_NAME = "auto_cleaner"
CLEANER_STATE = {}
cleaner_col = db.db[COLLECTION_NAME]
OWNER_ID = Config.OWNER_ID

# ============================================================
# DEFAULT SETTINGS
# ============================================================

DEFAULT_SETTINGS = {
    "_id": "settings",
    "enabled": False,
    "caption_cleaner": True,
    "filename_cleaner": False,
    "remove_texts": [
        "[CineBari.com]",
        "[MovieBaaz.com]",
        "[MovieBaaz.com] -",
        "CineBari.com",
        "MovieBaaz.com",
    ],
    "channels": {},
}

# ============================================================
# DATABASE FUNCTIONS
# ============================================================

async def get_settings():
    data = await cleaner_col.find_one({"_id": "settings"})
    if not data:
        await cleaner_col.insert_one(DEFAULT_SETTINGS.copy())
        return DEFAULT_SETTINGS.copy()
    return data

async def update_settings(data):
    await cleaner_col.update_one(
        {"_id": "settings"},
        {"$set": data},
        upsert=True,
    )

async def is_enabled():
    settings = await get_settings()
    return bool(settings.get("enabled", False))

async def is_caption_cleaner_enabled():
    settings = await get_settings()
    return bool(settings.get("caption_cleaner", True))

async def get_remove_texts():
    settings = await get_settings()
    return settings.get("remove_texts", [])

async def add_remove_text(text):
    text = text.strip()
    if not text:
        return False
    await cleaner_col.update_one(
        {"_id": "settings"},
        {"$addToSet": {"remove_texts": text}},
        upsert=True,
    )
    return True

async def delete_remove_text(text):
    await cleaner_col.update_one(
        {"_id": "settings"},
        {"$pull": {"remove_texts": text}},
    )

async def is_channel_enabled(chat_id):
    settings = await get_settings()
    channels = settings.get("channels", {})
    return channels.get(str(chat_id), True)

async def set_channel_status(chat_id, status):
    await cleaner_col.update_one(
        {"_id": "settings"},
        {"$set": {f"channels.{chat_id}": bool(status)}},
        upsert=True,
    )

async def register_channel(message):
    if not message.chat:
        return
    chat_id = message.chat.id
    settings = await get_settings()
    channels = settings.get("channels", {})
    if str(chat_id) not in channels:
        await set_channel_status(chat_id, True)
        LOGGER.info("New channel registered: %s", chat_id)

# ============================================================
# CLEANING FUNCTIONS
# ============================================================

def clean_text(text, remove_texts):
    if not text:
        return ""
    result = text
    
    for remove_text in remove_texts:
        if not remove_text:
            continue
        result = re.sub(re.escape(remove_text), "", result, flags=re.IGNORECASE)
    
    result = re.sub(r"\[\s*\]", "", result)
    result = re.sub(r"^[\s\-_:|]+$", "", result, flags=re.MULTILINE)
    
    lines = []
    for line in result.splitlines():
        line = line.strip()
        lines.append(line)
    result = "\n".join(lines)
    
    result = re.sub(r"\n\s*\n\s*\n+", "\n\n", result)
    result = result.strip()
    return result

async def is_processed(chat_id, message_id):
    data = await cleaner_col.find_one({
        "type": "processed",
        "chat_id": int(chat_id),
        "message_id": int(message_id),
    })
    return bool(data)

async def mark_processed(chat_id, message_id):
    await cleaner_col.update_one(
        {
            "type": "processed",
            "chat_id": int(chat_id),
            "message_id": int(message_id),
        },
        {
            "$set": {
                "type": "processed",
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "processed_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )

async def process_caption(message):
    if not message.caption:
        return False
    remove_texts = await get_remove_texts()
    if not remove_texts:
        return False
    
    old_caption = message.caption
    new_caption = clean_text(old_caption, remove_texts)
    
    if new_caption == old_caption:
        return False
    
    try:
        await message.edit_caption(caption=new_caption)
        LOGGER.info("Caption cleaned | chat=%s | message=%s", message.chat.id, message.id)
        return True
    except Exception as e:
        LOGGER.error("Caption edit failed: %s", e)
        return False

async def process_message(message):
    if not await is_enabled():
        return
    await register_channel(message)
    if not await is_channel_enabled(message.chat.id):
        return
    if await is_processed(message.chat.id, message.id):
        return
    if not (message.video or message.document or message.audio):
        return
    
    caption_changed = False
    if await is_caption_cleaner_enabled():
        caption_changed = await process_caption(message)
        
    await mark_processed(message.chat.id, message.id)

# ============================================================
# HANDLERS
# ============================================================

@Client.on_message(
    filters.channel
    & (filters.video | filters.document | filters.audio)
)
async def auto_cleaner_channel_handler(client, message):
    try:
        await process_message(message)
    except Exception as e:
        LOGGER.exception("Auto Cleaner error: %s", e)

async def cleaner_menu():
    settings = await get_settings()
    enabled = settings.get("enabled", False)
    caption_enabled = settings.get("caption_cleaner", True)
    texts = settings.get("remove_texts", [])
    
    status = "🟢 ON" if enabled else "🔴 OFF"
    caption_status = "🟢 ON" if caption_enabled else "🔴 OFF"
    text_count = len(texts)
    
    return (
        "🧹 <b>Auto Cleaner</b>\n\n"
        f"Status: <b>{status}</b>\n"
        f"Caption Cleaner: <b>{caption_status}</b>\n"
        f"Remove Texts: <b>{text_count}</b>\n\n"
        "ℹ️ Bot নতুন Channel Post-এর caption automatically clean করবে।\n\n"
        "⚠️ Existing Telegram file-এর filename re-upload ছাড়া পরিবর্তন করা যায় না।"
    )

async def cleaner_keyboard():
    settings = await get_settings()
    enabled = settings.get("enabled", False)
    caption_enabled = settings.get("caption_cleaner", True)
    
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟢 Cleaner ON" if enabled else "🔴 Cleaner OFF", callback_data="ac_toggle")],
            [InlineKeyboardButton("🧹 Caption ON" if caption_enabled else "🚫 Caption OFF", callback_data="ac_caption")],
            [
                InlineKeyboardButton("➕ Add Remove Text", callback_data="ac_add"),
                InlineKeyboardButton("➖ Delete Text", callback_data="ac_delete"),
            ],
            [InlineKeyboardButton("📋 Remove Text List", callback_data="ac_list")],
            [InlineKeyboardButton("📢 Channel Settings", callback_data="ac_channels")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="ac_refresh")],
        ]
    )

@Client.on_message(filters.private & filters.command("cleaner"))
async def cleaner_command(client, message):
    if message.from_user.id != OWNER_ID:
        return
    await message.reply_text(
        await cleaner_menu(),
        reply_markup=await cleaner_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^ac_"))
async def cleaner_callback(client, query):
    if query.from_user.id != OWNER_ID:
        return await query.answer("এটি শুধুমাত্র এডমিনের জন্য!", show_alert=True)
    
    user_id = query.from_user.id
    data = query.data

    if data == "ac_toggle":
        settings = await get_settings()
        current = settings.get("enabled", False)
        await update_settings({"enabled": not current})
        await query.answer("Auto Cleaner " + ("Enabled" if not current else "Disabled"))
    
    elif data == "ac_caption":
        settings = await get_settings()
        current = settings.get("caption_cleaner", True)
        await update_settings({"caption_cleaner": not current})
        await query.answer("Caption Cleaner " + ("Enabled" if not current else "Disabled"))
    
    elif data == "ac_add":
        CLEANER_STATE[user_id] = "add"
        await query.message.edit_text(
            "➕ <b>Add Remove Text</b>\n\n"
            "যে text/copyright/website caption থেকে remove করতে চান সেটা এখন পাঠান।\n\n"
            "উদাহরণ:\n<code>[CineBari.com]</code>\n\n"
            "Cancel করতে /cancel পাঠান।",
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        return
    
    elif data == "ac_delete":
        texts = await get_remove_texts()
        if not texts:
            await query.answer("Remove text list empty!", show_alert=True)
            return
        
        buttons = []
        for index, text in enumerate(texts):
            buttons.append([InlineKeyboardButton(f"❌ {text[:40]}", callback_data=f"ac_del_{index}")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="ac_refresh")])
        
        await query.message.edit_text(
            "➖ <b>Delete Remove Text</b>\n\nযেটা delete করতে চান সেটাতে চাপুন:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        return
    
    elif data.startswith("ac_del_"):
        try:
            index = int(data.split("_")[-1])
            texts = await get_remove_texts()
            if index >= len(texts):
                await query.answer("Text not found!", show_alert=True)
                return
            text = texts[index]
            await delete_remove_text(text)
            await query.answer("Removed successfully!")
        except Exception as e:
            LOGGER.error("Delete text error: %s", e)
            await query.answer("Delete failed!", show_alert=True)
    
    elif data == "ac_list":
        texts = await get_remove_texts()
        if not texts:
            text = "📋 <b>Remove Text List</b>\n\nকোনো text সেট করা নেই।"
        else:
            lines = []
            for index, value in enumerate(texts, start=1):
                lines.append(f"{index}. <code>{value}</code>")
            text = "📋 <b>Remove Text List</b>\n\n" + "\n".join(lines)
        
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ac_refresh")]]),
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        return
    
    elif data == "ac_channels":
        settings = await get_settings()
        channels = settings.get("channels", {})
        if not channels:
            text = "📢 <b>Channel Settings</b>\n\nএখনো কোনো channel detect হয়নি।"
        else:
            lines = []
            for chat_id, status in channels.items():
                status_text = "🟢 ON" if status else "🔴 OFF"
                lines.append(f"<code>{chat_id}</code> — {status_text}")
            text = "📢 <b>Channel Settings</b>\n\n" + "\n".join(lines)
        
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ac_refresh")]]),
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        return
    
    elif data == "ac_refresh":
        await query.answer()
    else:
        await query.answer()
    
    await query.message.edit_text(
        await cleaner_menu(),
        reply_markup=await cleaner_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.private & filters.text & ~filters.command(["cancel", "start", "cleaner"]))
async def cleaner_text_input(client, message):
    if message.from_user.id != OWNER_ID:
        return
        
    user_id = message.from_user.id
    state = CLEANER_STATE.get(user_id)
    
    if state != "add":
        return
    
    value = message.text.strip()
    if not value:
        await message.reply_text("⚠️ Empty text দেওয়া যাবে না।")
        return
    
    await add_remove_text(value)
    CLEANER_STATE.pop(user_id, None)
    await message.reply_text(
        "✅ <b>Remove Text Added</b>\n\n"
        f"<code>{value}</code>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.private & filters.command("cancel"))
async def cancel_cleaner_state(client, message):
    user_id = message.from_user.id
    if user_id in CLEANER_STATE:
        CLEANER_STATE.pop(user_id, None)
        await message.reply_text("❌ Cancelled.")
