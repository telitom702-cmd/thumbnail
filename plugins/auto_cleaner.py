# ============================================================
# Telegram Auto & Existing Post Cleaner (All-in-One)
# File: plugins/auto_cleaner.py
# ============================================================

import re
import logging
import asyncio
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
# SETTINGS & DATABASE
# ============================================================

COLLECTION_NAME = "auto_cleaner"
CLEANER_STATE = {}      # Auto Cleaner এর জন্য
OLD_CLEANER_STATE = {}   # Existing Cleaner এর জন্য
cleaner_col = db.db[COLLECTION_NAME]
OWNER_ID = Config.OWNER_ID

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
# TEXT CLEANING LOGIC
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

# ============================================================
# AUTO CLEANER (NEW POSTS)
# ============================================================

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
    
    if await is_caption_cleaner_enabled():
        await process_caption(message)
        
    await mark_processed(message.chat.id, message.id)

@Client.on_message(
    filters.channel
    & (filters.video | filters.document | filters.audio),
    group=-10
)
async def auto_cleaner_channel_handler(client, message):
    try:
        await process_message(message)
    except Exception as e:
        LOGGER.exception("Auto Cleaner error: %s", e)


# ============================================================
# EXISTING CLEANER (OLD POSTS)
# ============================================================

def get_forwarded_channel(message):
    try:
        chat = message.forward_from_chat
        if not chat: return None
        if chat.type not in ("channel", "supergroup"): return None
        return chat
    except Exception:
        return None

async def clean_existing_posts(client, query, state):
    user_id = query.from_user.id
    chat_id = state.get("chat_id")
    chat_title = state.get("chat_title", "Unknown Channel")
    remove_texts = await get_remove_texts()
    
    if not remove_texts:
        await query.message.edit_text("❌ কোনো Remove Text সেট করা নেই। অনুগ্রহ করে /cleaner থেকে টেক্সট অ্যাড করুন।")
        OLD_CLEANER_STATE.pop(user_id, None)
        return

    await query.message.edit_text(
        f"🔎 <b>Searching...</b>\n\n📢 Channel: <b>{chat_title}</b>\n\nপুরোনো পোস্টগুলো খুঁজছি দয়া করে অপেক্ষা করুন...",
        parse_mode=enums.ParseMode.HTML
    )

    matches = []
    try:
        async for post in client.get_chat_history(chat_id):
            if not post.caption: continue
            # চেক করবে পোস্টে কি লিস্টের কোনো টেক্সট আছে কিনা
            has_match = any(rt.lower() in post.caption.lower() for rt in remove_texts)
            if has_match:
                matches.append(post.id)
    except Exception as e:
        LOGGER.exception("Search error: %s", e)
        OLD_CLEANER_STATE.pop(user_id, None)
        await query.message.edit_text("❌ পুরোনো পোস্ট search করা যায়নি। Bot-এর Channel access/admin permission চেক করুন।")
        return

    if not matches:
        OLD_CLEANER_STATE.pop(user_id, None)
        await query.message.edit_text("🔎 <b>Search Complete</b>\n\nকোনো matching post পাওয়া যায়নি।", parse_mode=enums.ParseMode.HTML)
        return

    total = len(matches)
    cleaned = 0
    failed = 0
    unchanged = 0

    await query.message.edit_text(
        "🧹 <b>Cleaning Started...</b>\n\n"
        f"📦 Total: <b>{total}</b>\n✅ Cleaned: <b>0</b>\n❌ Failed: <b>0</b>\n\nদয়া করে অপেক্ষা করুন...",
        parse_mode=enums.ParseMode.HTML
    )

    for index, message_id in enumerate(matches, start=1):
        try:
            post = await client.get_messages(chat_id, message_id)
            if not post:
                failed += 1
                continue
            if not post.caption:
                unchanged += 1
                continue

            old_caption = post.caption
            new_caption = clean_text(old_caption, remove_texts)

            if new_caption == old_caption:
                unchanged += 1
                continue
            if not new_caption:
                new_caption = "\u200b"  # Zero width space if empty

            await client.edit_message_caption(chat_id, message_id, caption=new_caption)
            cleaned += 1
            await asyncio.sleep(1.5)  # Flood wait এড়ানোর জন্য
        except Exception as e:
            failed += 1
            LOGGER.error("Failed to clean chat=%s msg=%s err=%s", chat_id, message_id, e)

        if index % 10 == 0 or index == total:
            try:
                await query.message.edit_text(
                    "🧹 <b>Cleaning...</b>\n\n"
                    f"📦 Total: <b>{total}</b>\n🔄 Checked: <b>{index}</b>\n"
                    f"✅ Cleaned: <b>{cleaned}</b>\n⚪ Unchanged: <b>{unchanged}</b>\n❌ Failed: <b>{failed}</b>",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                pass

    OLD_CLEANER_STATE.pop(user_id, None)

    await query.message.edit_text(
        "✅ <b>Existing Cleaner Finished</b>\n\n"
        f"📦 Total Matching Posts: <b>{total}</b>\n🧹 Cleaned: <b>{cleaned}</b>\n"
        f"⚪ Unchanged: <b>{unchanged}</b>\n❌ Failed: <b>{failed}</b>",
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# COMMANDS & CALLBACKS
# ============================================================

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
        "ℹ️ Bot নতুন Channel Post-এর caption automatically clean করবে।"
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
            [InlineKeyboardButton("📦 Clean Old Posts", callback_data="ec_start")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="ac_refresh")],
        ]
    )

@Client.on_message(filters.private & filters.command("cleaner"), group=1)
async def cleaner_command(client, message):
    if message.from_user.id != OWNER_ID:
        return
    await message.reply_text(
        await cleaner_menu(),
        reply_markup=await cleaner_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^(ac_|ec_)"))
async def cleaner_callback(client, query):
    if query.from_user.id != OWNER_ID:
        return await query.answer("এটি শুধুমাত্র এডমিনের জন্য!", show_alert=True)
    
    user_id = query.from_user.id
    data = query.data

    # --- AUTO CLEANER CALLBACKS ---
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

    # --- EXISTING CLEANER CALLBACKS ---
    elif data == "ec_start":
        OLD_CLEANER_STATE[user_id] = {"step": "waiting_forward"}
        await query.message.edit_text(
            "📦 <b>Existing Post Cleaner</b>\n\n"
            "প্রথমে যে Channel-এর পুরোনো Post clean করতে চান, সেই Channel থেকে <b>একটি Post Forward</b> করুন।\n\n"
            "❌ Cancel করতে /cancel পাঠান।",
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        return

    elif data == "ec_cancel":
        OLD_CLEANER_STATE.pop(user_id, None)
        await query.message.edit_text("❌ Existing Cleaner cancelled.")
        await query.answer()
        return

    elif data == "ec_clean":
        state = OLD_CLEANER_STATE.get(user_id)
        if not state or state.get("step") != "ready":
            await query.answer("Session expired! Forward a channel post again.", show_alert=True)
            OLD_CLEANER_STATE.pop(user_id, None)
            return
        await query.answer("Cleaning started...")
        await clean_existing_posts(client, query, state)
        return

    elif data == "ec_search_cancel":
        OLD_CLEANER_STATE.pop(user_id, None)
        await query.message.edit_text("❌ Cleaning cancelled.")
        await query.answer()
        return

    # --- REFRESH ---
    elif data == "ac_refresh":
        await query.answer()
    else:
        await query.answer()
    
    await query.message.edit_text(
        await cleaner_menu(),
        reply_markup=await cleaner_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# TEXT & FORWARD INPUT HANDLERS
# ============================================================

@Client.on_message(filters.private & filters.text & ~filters.command(["cancel", "start", "cleaner", "oldclean"]) & ~filters.forwarded, group=1)
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
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="ac_refresh")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.private & filters.forwarded, group=1)
async def existing_cleaner_forward(client, message):
    if message.from_user.id != OWNER_ID: return
    
    user_id = message.from_user.id
    state = OLD_CLEANER_STATE.get(user_id)
    if not state or state.get("step") != "waiting_forward": return

    channel = get_forwarded_channel(message)
    if not channel:
        await message.reply_text("⚠️ এটি Channel-এর forwarded post নয়। যে Channel clean করতে চান, সেই Channel থেকে একটি Post Forward করুন।")
        return

    chat_id = channel.id
    title = channel.title or "Unknown Channel"
    username = channel.username or ""

    OLD_CLEANER_STATE[user_id] = {
        "step": "ready", 
        "chat_id": chat_id, 
        "chat_title": title, 
        "username": username
    }

    username_text = f"\n🔗 @{username}" if username else ""

    await message.reply_text(
        "✅ <b>Channel Detected</b>\n\n"
        f"📢 <b>{title}</b>\n🆔 <code>{chat_id}</code>{username_text}\n\n"
        "বট এই চ্যানেলের সব পুরোনো পোস্ট স্ক্যান করে সেভ করা Remove Texts গুলো কেটে দেবে।\n\n",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🧹 Start Cleaning Now", callback_data="ec_clean")],
            [InlineKeyboardButton("❌ Cancel", callback_data="ec_search_cancel")]
        ]),
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# CANCEL COMMAND
# ============================================================

@Client.on_message(filters.private & filters.command("cancel"), group=1)
async def cancel_cleaner_state(client, message):
    user_id = message.from_user.id
    if user_id in CLEANER_STATE or user_id in OLD_CLEANER_STATE:
        CLEANER_STATE.pop(user_id, None)
        OLD_CLEANER_STATE.pop(user_id, None)
        await message.reply_text("❌ Cancelled.")
