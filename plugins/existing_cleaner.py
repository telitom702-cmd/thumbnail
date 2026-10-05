# ============================================================
# Telegram Existing / Old Post Cleaner
# File: plugins/existing_cleaner.py
# ============================================================

import logging
import re
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from plugins.database.database import db
from plugins.config import Config

LOGGER = logging.getLogger(__name__)

COLLECTION_NAME = "existing_cleaner"
OLD_CLEANER_STATE = {}
cleaner_col = db.db[COLLECTION_NAME]
OWNER_ID = Config.OWNER_ID

# ============================================================
# DATABASE HELPERS
# ============================================================

async def save_channel(chat_id, title=None, username=None):
    await cleaner_col.update_one(
        {"type": "channel", "chat_id": int(chat_id)},
        {"$set": {
            "type": "channel", "chat_id": int(chat_id),
            "title": title or "", "username": username or "",
            "updated_at": datetime.utcnow()
        }},
        upsert=True,
    )

# ============================================================
# CLEAN TEXT
# ============================================================

def remove_text_from_caption(caption, remove_text):
    if not caption or not remove_text:
        return caption

    result = re.sub(re.escape(remove_text), "", caption, flags=re.IGNORECASE)
    result = re.sub(r"\[\s*\]", "", result)
    result = re.sub(r"^[\s\-_:|]+$", "", result, flags=re.MULTILINE)
    
    lines = [line.strip() for line in result.splitlines()]
    result = "\n".join(lines)
    result = re.sub(r"\n\s*\n\s*\n+", "\n\n", result)
    return result.strip()

# ============================================================
# FORWARDED CHANNEL DETECTION
# ============================================================

def get_forwarded_channel(message):
    try:
        chat = message.forward_from_chat
        if not chat: return None
        if chat.type not in ("channel", "supergroup"): return None
        return chat
    except Exception:
        return None

# ============================================================
# MENU
# ============================================================

def existing_cleaner_menu():
    return (
        "📦 <b>Existing Post Cleaner</b>\n\n"
        "পুরোনো Channel Post-এর caption থেকে নির্দিষ্ট text কেটে দিতে পারবেন।\n\n"
        "⚠️ File download/upload হবে না।\n"
        "⚠️ শুধু matching caption edit হবে।"
    )

def existing_cleaner_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 Clean Existing Posts", callback_data="ec_start")],
            [InlineKeyboardButton("❌ Cancel", callback_data="ec_cancel")]
        ]
    )

# ============================================================
# COMMAND
# ============================================================

@Client.on_message(filters.private & filters.command("oldclean"))
async def existing_cleaner_command(client, message):
    if message.from_user.id != OWNER_ID: return
    await message.reply_text(
        existing_cleaner_menu(),
        reply_markup=existing_cleaner_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# CALLBACK
# ============================================================

@Client.on_callback_query(filters.regex(r"^ec_"))
async def existing_cleaner_callback(client, query):
    if query.from_user.id != OWNER_ID:
        return await query.answer("এটি শুধমাত্র এডমিনের জন্য!", show_alert=True)
    
    user_id = query.from_user.id
    data = query.data

    if data == "ec_start":
        OLD_CLEANER_STATE[user_id] = {"step": "waiting_forward"}
        await query.message.edit_text(
            "📦 <b>Existing Post Cleaner</b>\n\n"
            "প্রথমে যে Channel-এর পুরোনো Post clean করতে চান, সেই Channel থেকে <b>একটি Post Forward</b> করুন।\n\n"
            "❌ Cancel করতে /cancel পাঠান।",
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer()
        return

    if data == "ec_cancel":
        OLD_CLEANER_STATE.pop(user_id, None)
        await query.message.edit_text("❌ Existing Cleaner cancelled.")
        await query.answer()
        return

    if data == "ec_clean":
        state = OLD_CLEANER_STATE.get(user_id)
        if not state or state.get("step") != "ready":
            await query.answer("Session expired!", show_alert=True)
            return
        await query.answer("Cleaning started...")
        await clean_existing_posts(client, query, state)
        return

    if data == "ec_search_cancel":
        OLD_CLEANER_STATE.pop(user_id, None)
        await query.message.edit_text("❌ Cleaning cancelled.")
        await query.answer()
        return

# ============================================================
# TEXT INPUT
# ============================================================

@Client.on_message(filters.private & filters.text & ~filters.command(["cancel", "start", "cleaner", "oldclean"]))
async def existing_cleaner_text_input(client, message):
    if message.from_user.id != OWNER_ID: return
    
    user_id = message.from_user.id
    state = OLD_CLEANER_STATE.get(user_id)
    if not state or state.get("step") != "waiting_text": return

    remove_text = message.text.strip()
    if not remove_text:
        await message.reply_text("⚠️ Empty text দেওয়া যাবে না।")
        return

    chat_id = state.get("chat_id")
    chat_title = state.get("chat_title", "Unknown Channel")

    state["remove_text"] = remove_text
    state["step"] = "searching"

    await message.reply_text(
        "🔎 <b>Searching...</b>\n\n"
        f"📢 Channel: <b>{chat_title}</b>\n"
        f"✂️ Remove Text: <code>{remove_text}</code>\n\n"
        "পুরোনো পোস্টগুলো খুঁজছি...",
        parse_mode=enums.ParseMode.HTML
    )

    matches = []
    try:
        async for post in client.get_chat_history(chat_id):
            if not post.caption: continue
            if remove_text.lower() in post.caption.lower():
                matches.append(post.id)
    except Exception as e:
        LOGGER.exception("Search error: %s", e)
        OLD_CLEANER_STATE.pop(user_id, None)
        await message.reply_text("❌ পুরোনো পোস্ট search করা যায়নি। Bot-এর Channel access/admin permission চেক করুন।")
        return

    if not matches:
        OLD_CLEANER_STATE.pop(user_id, None)
        await message.reply_text("🔎 <b>Search Complete</b>\n\nকোনো matching post পাওয়া যায়নি।", parse_mode=enums.ParseMode.HTML)
        return

    state["matches"] = matches
    state["step"] = "ready"

    await message.reply_text(
        "🔎 <b>Search Complete</b>\n\n"
        f"📢 Channel: <b>{chat_title}</b>\n"
        f"✂️ Remove Text: <code>{remove_text}</code>\n\n"
        f"📦 Matching Posts: <b>{len(matches)}</b>\n\n"
        "শুধু এই matching posts-এর caption থেকেই text কাটা হবে।",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🧹 Clean {len(matches)} Posts", callback_data="ec_clean")],
            [InlineKeyboardButton("❌ Cancel", callback_data="ec_search_cancel")]
        ]),
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# FORWARDED POST HANDLER
# ============================================================

@Client.on_message(filters.private & filters.forwarded)
async def existing_cleaner_forward(client, message):
    if message.from_user.id != OWNER_ID: return
    
    user_id = message.from_user.id
    state = OLD_CLEANER_STATE.get(user_id)
    if not state or state.get("step") != "waiting_forward": return

    channel = get_forwarded_channel(message)
    if not channel:
        await message.reply_text("⚠️ এটি Channel-এর forwarded post নয়। যে Channel clean করতে চান, সেই Channel থেকে একটি Post Forward করুন।")
        return

    chat_id = channel.id
    title = channel.title or "Unknown Channel"
    username = channel.username or ""

    await save_channel(chat_id, title, username)
    OLD_CLEANER_STATE[user_id] = {"step": "waiting_text", "chat_id": chat_id, "chat_title": title, "username": username}

    username_text = f"\n🔗 @{username}" if username else ""

    await message.reply_text(
        "✅ <b>Channel Detected</b>\n\n"
        f"📢 <b>{title}</b>\n🆔 <code>{chat_id}</code>{username_text}\n\n"
        "এখন যে text পুরোনো পোস্টের caption থেকে কাটতে চান সেটা পাঠান।\n\n"
        "❌ Cancel করতে /cancel পাঠান।",
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# CLEAN EXISTING POSTS
# ============================================================

async def clean_existing_posts(client, query, state):
    user_id = query.from_user.id
    chat_id = state.get("chat_id")
    remove_text = state.get("remove_text")
    matches = state.get("matches", [])
    total = len(matches)

    if not chat_id or not remove_text:
        await query.message.edit_text("❌ Invalid cleaning session.")
        OLD_CLEANER_STATE.pop(user_id, None)
        return

    cleaned = 0
    failed = 0
    unchanged = 0

    await query.message.edit_text(
        "🧹 <b>Cleaning Started...</b>\n\n"
        f"📦 Total: <b>{total}</b>\n✅ Cleaned: <b>0</b>\n❌ Failed: <b>0</b>\n\nদয়া করে অপেক্ষা করুন...",
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
            new_caption = remove_text_from_caption(old_caption, remove_text)

            if new_caption == old_caption:
                unchanged += 1
                continue
            if not new_caption:
                new_caption = "\u200b"  # Zero width space if empty

            await client.edit_message_caption(chat_id, message_id, caption=new_caption)
            cleaned += 1
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
        f"⚪ Unchanged: <b>{unchanged}</b>\n❌ Failed: <b>{failed}</b>\n\n"
        f"✂️ Removed Text:\n<code>{remove_text}</code>",
        parse_mode=enums.ParseMode.HTML
    )

# ============================================================
# CANCEL COMMAND
# ============================================================

@Client.on_message(filters.private & filters.command("cancel"))
async def cancel_existing_cleaner(client, message):
    user_id = message.from_user.id
    if user_id in OLD_CLEANER_STATE:
        OLD_CLEANER_STATE.pop(user_id, None)
        await message.reply_text("❌ Existing Cleaner cancelled.")
