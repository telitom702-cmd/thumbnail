# ============================================================
# Telegram Auto Cleaner
# File: plugins/auto_cleaner.py
#
# Features:
# - New Channel Post Auto Caption Cleaner
# - Remove Text Management
# - Channel Add by ID
# - Auto Channel Detection
# - Per Channel ON/OFF
# - Channel Delete + Confirmation
# - Deleted Channel Protection
# - Existing / Old Post Cleaner
# - /oldclean command
# - Full Channel History Scan
# - Matching Caption Cleaner
# - Live Progress
# - Cancel Old Cleaner
# - Progress / Statistics
# - MongoDB Storage
# ============================================================

import re
import html
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

cleaner_col = db.db[COLLECTION_NAME]

OWNER_ID = Config.OWNER_ID


# ============================================================
# RUNTIME STATES
# ============================================================

# Add text / Add channel state
CLEANER_STATE = {}

# Old cleaner running state
OLD_CLEAN_RUNNING = {}


# ============================================================
# DEFAULT SETTINGS
# ============================================================

DEFAULT_SETTINGS = {
    "_id": "settings",

    # Main cleaner
    "enabled": False,

    # Caption cleaner
    "caption_cleaner": True,

    # Filename cleaner placeholder
    "filename_cleaner": False,

    # Texts to remove
    "remove_texts": [
        "[CineBari.com]",
        "[MovieBaaz.com]",
        "[MovieBaaz.com] -",
        "CineBari.com",
        "MovieBaaz.com",
    ],

    # Registered channels
    "channels": {},

    # Deleted channels
    "deleted_channels": [],
}


# ============================================================
# DATABASE
# ============================================================

async def get_settings():

    data = await cleaner_col.find_one(
        {"_id": "settings"}
    )

    if not data:

        new_data = {
            "_id": "settings",
            "enabled": DEFAULT_SETTINGS["enabled"],
            "caption_cleaner": DEFAULT_SETTINGS["caption_cleaner"],
            "filename_cleaner": DEFAULT_SETTINGS["filename_cleaner"],
            "remove_texts": DEFAULT_SETTINGS["remove_texts"].copy(),
            "channels": {},
            "deleted_channels": [],
        }

        await cleaner_col.insert_one(
            new_data
        )

        return new_data

    changed = False

    # --------------------------------------------------------
    # Old database compatibility
    # --------------------------------------------------------

    if "enabled" not in data:
        data["enabled"] = False
        changed = True

    if "caption_cleaner" not in data:
        data["caption_cleaner"] = True
        changed = True

    if "filename_cleaner" not in data:
        data["filename_cleaner"] = False
        changed = True

    if "remove_texts" not in data:
        data["remove_texts"] = DEFAULT_SETTINGS[
            "remove_texts"
        ].copy()
        changed = True

    if "channels" not in data:
        data["channels"] = {}
        changed = True

    if "deleted_channels" not in data:
        data["deleted_channels"] = []
        changed = True

    if changed:

        await cleaner_col.update_one(
            {"_id": "settings"},
            {
                "$set": {
                    "enabled": data["enabled"],
                    "caption_cleaner": data[
                        "caption_cleaner"
                    ],
                    "filename_cleaner": data[
                        "filename_cleaner"
                    ],
                    "remove_texts": data[
                        "remove_texts"
                    ],
                    "channels": data[
                        "channels"
                    ],
                    "deleted_channels": data[
                        "deleted_channels"
                    ],
                }
            },
            upsert=True,
        )

    return data


async def update_settings(data):

    await cleaner_col.update_one(
        {"_id": "settings"},
        {
            "$set": data
        },
        upsert=True,
    )


# ============================================================
# MAIN CLEANER STATUS
# ============================================================

async def is_enabled():

    settings = await get_settings()

    return bool(
        settings.get(
            "enabled",
            False
        )
    )


async def is_caption_cleaner_enabled():

    settings = await get_settings()

    return bool(
        settings.get(
            "caption_cleaner",
            True
        )
    )


# ============================================================
# REMOVE TEXT
# ============================================================

async def get_remove_texts():

    settings = await get_settings()

    return settings.get(
        "remove_texts",
        []
    )


async def add_remove_text(text):

    text = text.strip()

    if not text:
        return False

    await cleaner_col.update_one(
        {"_id": "settings"},
        {
            "$addToSet": {
                "remove_texts": text
            }
        },
        upsert=True,
    )

    return True


async def delete_remove_text(text):

    await cleaner_col.update_one(
        {"_id": "settings"},
        {
            "$pull": {
                "remove_texts": text
            }
        },
    )


# ============================================================
# CHANNEL FUNCTIONS
# ============================================================

async def get_channels():

    settings = await get_settings()

    return settings.get(
        "channels",
        {}
    )


async def get_deleted_channels():

    settings = await get_settings()

    return settings.get(
        "deleted_channels",
        []
    )


async def is_channel_deleted(chat_id):

    deleted = await get_deleted_channels()

    return str(chat_id) in [
        str(x)
        for x in deleted
    ]


async def is_channel_enabled(chat_id):

    channels = await get_channels()

    chat_id = str(chat_id)

    # Not registered = OFF
    if chat_id not in channels:
        return False

    return bool(
        channels.get(
            chat_id,
            False
        )
    )


async def set_channel_status(
    chat_id,
    status
):

    settings = await get_settings()

    channels = settings.get(
        "channels",
        {}
    )

    channels[str(chat_id)] = bool(
        status
    )

    await update_settings(
        {
            "channels": channels
        }
    )


async def add_channel(
    chat_id,
    status=True
):

    chat_id = str(chat_id)

    settings = await get_settings()

    channels = settings.get(
        "channels",
        {}
    )

    deleted = settings.get(
        "deleted_channels",
        []
    )

    channels[chat_id] = bool(
        status
    )

    # Re-add করলে deleted protection থেকে remove
    deleted = [
        str(x)
        for x in deleted
        if str(x) != chat_id
    ]

    await update_settings(
        {
            "channels": channels,
            "deleted_channels": deleted,
        }
    )


async def delete_channel(chat_id):

    chat_id = str(chat_id)

    settings = await get_settings()

    channels = settings.get(
        "channels",
        {}
    )

    deleted = settings.get(
        "deleted_channels",
        []
    )

    # Active list থেকে remove
    channels.pop(
        chat_id,
        None
    )

    # Deleted list-এ রাখি
    if chat_id not in [
        str(x)
        for x in deleted
    ]:
        deleted.append(
            chat_id
        )

    await update_settings(
        {
            "channels": channels,
            "deleted_channels": deleted,
        }
    )


# ============================================================
# AUTO REGISTER CHANNEL
# ============================================================

async def register_channel(message):

    if not message.chat:
        return False

    chat_id = str(
        message.chat.id
    )

    # Deleted channel হলে কখনো auto register নয়
    if await is_channel_deleted(
        chat_id
    ):
        return False

    settings = await get_settings()

    channels = settings.get(
        "channels",
        {}
    )

    if chat_id not in channels:

        channels[chat_id] = True

        await update_settings(
            {
                "channels": channels
            }
        )

        LOGGER.info(
            "New channel automatically registered: %s",
            chat_id
        )

    return True


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(
    text,
    remove_texts
):

    if not text:
        return ""

    result = text

    # --------------------------------------------------------
    # Remove configured texts
    # --------------------------------------------------------

    for remove_text in remove_texts:

        if not remove_text:
            continue

        result = re.sub(
            re.escape(remove_text),
            "",
            result,
            flags=re.IGNORECASE,
        )

    # --------------------------------------------------------
    # Remove empty brackets
    # --------------------------------------------------------

    result = re.sub(
        r"\[\s*\]",
        "",
        result
    )

    # --------------------------------------------------------
    # Remove lines containing only symbols
    # --------------------------------------------------------

    result = re.sub(
        r"^[\s\-_:|]+$",
        "",
        result,
        flags=re.MULTILINE
    )

    # --------------------------------------------------------
    # Trim each line
    # --------------------------------------------------------

    lines = []

    for line in result.splitlines():

        line = line.strip()

        lines.append(
            line
        )

    result = "\n".join(
        lines
    )

    # --------------------------------------------------------
    # Remove excessive blank lines
    # --------------------------------------------------------

    result = re.sub(
        r"\n\s*\n\s*\n+",
        "\n\n",
        result
    )

    return result.strip()


# ============================================================
# PROCESSED MESSAGE
# ============================================================

async def is_processed(
    chat_id,
    message_id
):

    data = await cleaner_col.find_one(
        {
            "type": "processed",
            "chat_id": int(chat_id),
            "message_id": int(message_id),
        }
    )

    return bool(data)


async def mark_processed(
    chat_id,
    message_id
):

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


# ============================================================
# CAPTION CLEANER
# ============================================================

async def process_caption(
    message
):

    if not message.caption:
        return False

    remove_texts = await get_remove_texts()

    if not remove_texts:
        return False

    old_caption = message.caption

    new_caption = clean_text(
        old_caption,
        remove_texts
    )

    if new_caption == old_caption:
        return False

    try:

        # Caption completely empty হয়ে গেলে
        # Telegram-এ caption remove করার জন্য None ব্যবহার
        if not new_caption:

            await message.edit_caption(
                caption=None
            )

        else:

            await message.edit_caption(
                caption=new_caption
            )

        LOGGER.info(
            "Caption cleaned | chat=%s | message=%s",
            message.chat.id,
            message.id
        )

        return True

    except Exception as e:

        LOGGER.error(
            "Caption edit failed | chat=%s | message=%s | %s",
            message.chat.id,
            message.id,
            e
        )

        return False


# ============================================================
# MESSAGE PROCESSOR
# ============================================================

async def process_message(
    message,
    mark=True
):

    if not message.chat:
        return False

    chat_id = message.chat.id

    # Deleted channel
    if await is_channel_deleted(
        chat_id
    ):
        return False

    # Main cleaner
    if not await is_enabled():
        return False

    # Auto register
    if not await register_channel(
        message
    ):
        return False

    # Channel ON/OFF
    if not await is_channel_enabled(
        chat_id
    ):
        return False

    # Already processed
    if await is_processed(
        chat_id,
        message.id
    ):
        return False

    # Only media
    if not (
        message.video
        or message.document
        or message.audio
    ):
        return False

    changed = False

    if await is_caption_cleaner_enabled():

        changed = await process_caption(
            message
        )

    if mark:

        await mark_processed(
            chat_id,
            message.id
        )

    return changed


# ============================================================
# NEW CHANNEL POST HANDLER
# ============================================================

@Client.on_message(
    filters.channel
    & (
        filters.video
        | filters.document
        | filters.audio
    )
)
async def auto_cleaner_channel_handler(
    client,
    message
):

    try:

        await process_message(
            message
        )

    except Exception:

        LOGGER.exception(
            "Auto Cleaner error"
        )


# ============================================================
# MAIN MENU
# ============================================================

async def cleaner_menu():

    settings = await get_settings()

    enabled = settings.get(
        "enabled",
        False
    )

    caption_enabled = settings.get(
        "caption_cleaner",
        True
    )

    texts = settings.get(
        "remove_texts",
        []
    )

    channels = settings.get(
        "channels",
        {}
    )

    status = (
        "🟢 ON"
        if enabled
        else
        "🔴 OFF"
    )

    caption_status = (
        "🟢 ON"
        if caption_enabled
        else
        "🔴 OFF"
    )

    return (
        "🧹 <b>Auto Cleaner</b>\n\n"

        f"Status: <b>{status}</b>\n"

        f"Caption Cleaner: "
        f"<b>{caption_status}</b>\n"

        f"Remove Texts: "
        f"<b>{len(texts)}</b>\n"

        f"Channels: "
        f"<b>{len(channels)}</b>\n\n"

        "ℹ️ নতুন Channel Post-এর "
        "caption automatically clean হবে।\n\n"

        "📂 পুরনো post-এর জন্য "
        "<code>/oldclean</code> ব্যবহার করুন।\n\n"

        "⚠️ Existing Telegram file-এর "
        "filename re-upload ছাড়া পরিবর্তন করা যায় না।"
    )


async def cleaner_keyboard():

    settings = await get_settings()

    enabled = settings.get(
        "enabled",
        False
    )

    caption_enabled = settings.get(
        "caption_cleaner",
        True
    )

    return InlineKeyboardMarkup(
        [

            [
                InlineKeyboardButton(
                    (
                        "🟢 Cleaner ON"
                        if enabled
                        else
                        "🔴 Cleaner OFF"
                    ),
                    callback_data="ac_toggle"
                )
            ],

            [
                InlineKeyboardButton(
                    (
                        "🧹 Caption ON"
                        if caption_enabled
                        else
                        "🚫 Caption OFF"
                    ),
                    callback_data="ac_caption"
                )
            ],

            [
                InlineKeyboardButton(
                    "➕ Add Remove Text",
                    callback_data="ac_add"
                ),

                InlineKeyboardButton(
                    "➖ Delete Text",
                    callback_data="ac_delete"
                ),
            ],

            [
                InlineKeyboardButton(
                    "📋 Remove Text List",
                    callback_data="ac_list"
                )
            ],

            [
                InlineKeyboardButton(
                    "📢 Channel Settings",
                    callback_data="ac_channels"
                )
            ],

            [
                InlineKeyboardButton(
                    "📂 Old Post Cleaner",
                    callback_data="ac_oldclean"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data="ac_refresh"
                )
            ],

        ]
    )


# ============================================================
# /cleaner
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("cleaner")
)
async def cleaner_command(
    client,
    message
):

    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        return

    await message.reply_text(
        await cleaner_menu(),
        reply_markup=await cleaner_keyboard(),
        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# CHANNEL SETTINGS TEXT
# ============================================================

async def channel_settings_text():

    channels = await get_channels()

    if not channels:

        return (
            "📢 <b>Channel Settings</b>\n\n"
            "এখনো কোনো channel add হয়নি।\n\n"
            "➕ Add Channel চাপুন।"
        )

    lines = []

    for chat_id, status in channels.items():

        status_text = (
            "🟢 ON"
            if status
            else
            "🔴 OFF"
        )

        lines.append(
            f"📢 <code>{html.escape(str(chat_id))}</code>"
            f" — <b>{status_text}</b>"
        )

    return (
        "📢 <b>Channel Settings</b>\n\n"
        + "\n".join(lines)
        + "\n\n"
        "প্রতিটি Channel আলাদাভাবে "
        "ON/OFF বা Delete করা যাবে।"
    )


# ============================================================
# CHANNEL SETTINGS KEYBOARD
# ============================================================

async def channel_settings_keyboard():

    channels = await get_channels()

    buttons = []

    for chat_id, status in channels.items():

        chat_id = str(chat_id)

        status_button = (
            "🔴 Turn OFF"
            if status
            else
            "🟢 Turn ON"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    status_button,
                    callback_data=(
                        f"ac_ch_toggle:{chat_id}"
                    )
                ),

                InlineKeyboardButton(
                    "🗑️ Delete",
                    callback_data=(
                        f"ac_ch_delete:{chat_id}"
                    )
                ),
            ]
        )

        # Old cleaner button
        buttons.append(
            [
                InlineKeyboardButton(
                    "🧹 Clean Old Posts",
                    callback_data=(
                        f"ac_old_channel:{chat_id}"
                    )
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "➕ Add Channel",
                callback_data="ac_ch_add"
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="ac_channels"
            ),

            InlineKeyboardButton(
                "🔙 Back",
                callback_data="ac_refresh"
            ),
        ]
    )

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# OLD CLEAN MENU
# ============================================================

async def oldclean_menu():

    channels = await get_channels()

    if not channels:

        return (
            "📂 <b>Old Post Cleaner</b>\n\n"
            "কোনো channel add করা নেই।"
        )

    lines = []

    for chat_id, status in channels.items():

        status_text = (
            "🟢 ON"
            if status
            else
            "🔴 OFF"
        )

        lines.append(
            f"📢 <code>{html.escape(str(chat_id))}</code>"
            f" — {status_text}"
        )

    return (
        "📂 <b>Old Post Cleaner</b>\n\n"

        "Channel নির্বাচন করলে তার "
        "<b>পুরো history</b> scan হবে।\n\n"

        "শুধু যেসব Video/Document/Audio post-এর "
        "caption-এ Remove Text List-এর কোনো "
        "text পাওয়া যাবে সেগুলো edit হবে।\n\n"

        + "\n".join(lines)
    )


async def oldclean_keyboard():

    channels = await get_channels()

    buttons = []

    for chat_id in channels:

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📢 {chat_id}",
                    callback_data=(
                        f"ac_old_channel:{chat_id}"
                    )
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data="ac_refresh"
            )
        ]
    )

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# OLD CLEAN CONFIRMATION KEYBOARD
# ============================================================

async def oldclean_start_keyboard(
    chat_id
):

    return InlineKeyboardMarkup(
        [

            [
                InlineKeyboardButton(
                    "▶️ Start Full History Scan",
                    callback_data=(
                        f"ac_old_start:{chat_id}"
                    )
                )
            ],

            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="ac_oldclean"
                )
            ],

        ]
    )


# ============================================================
# OLD CLEAN PROCESS
# ============================================================

async def clean_old_posts(
    client,
    chat_id,
    progress_message
):

    user_id = OWNER_ID

    # Already running
    if OLD_CLEAN_RUNNING.get(
        user_id
    ):

        return {
            "error": "already_running"
        }

    OLD_CLEAN_RUNNING[
        user_id
    ] = True

    scanned = 0
    media_posts = 0
    matched = 0
    cleaned = 0
    unchanged = 0
    failed = 0

    last_progress = 0

    try:

        # ----------------------------------------------------
        # Check channel exists
        # ----------------------------------------------------

        try:

            chat = await client.get_chat(
                int(chat_id)
            )

        except Exception as e:

            LOGGER.error(
                "Old cleaner get_chat failed: %s",
                e
            )

            return {
                "error": "channel_not_found"
            }

        channel_title = getattr(
            chat,
            "title",
            None
        ) or str(chat_id)

        # ----------------------------------------------------
        # Get remove texts
        # ----------------------------------------------------

        remove_texts = await get_remove_texts()

        if not remove_texts:

            return {
                "error": "remove_text_empty"
            }

        # ----------------------------------------------------
        # FULL CHANNEL HISTORY
        #
        # No limit is used here.
        #
        # Pyrogram will continue until the oldest
        # available message in the channel.
        # ----------------------------------------------------

        async for message in client.get_chat_history(
            int(chat_id)
        ):

            # ------------------------------------------------
            # Cancel check
            # ------------------------------------------------

            if not OLD_CLEAN_RUNNING.get(
                user_id,
                False
            ):

                return {
                    "error": "cancelled",
                    "scanned": scanned,
                    "media_posts": media_posts,
                    "matched": matched,
                    "cleaned": cleaned,
                    "unchanged": unchanged,
                    "failed": failed,
                }

            scanned += 1

            # ------------------------------------------------
            # Only Video / Document / Audio
            # ------------------------------------------------

            if not (
                message.video
                or message.document
                or message.audio
            ):
                continue

            media_posts += 1

            # ------------------------------------------------
            # Caption required
            # ------------------------------------------------

            if not message.caption:

                unchanged += 1

                continue

            # ------------------------------------------------
            # Current caption
            # ------------------------------------------------

            old_caption = message.caption

            # ------------------------------------------------
            # Check matching text
            #
            # This prevents counting every media post as
            # a matching post.
            # ------------------------------------------------

            has_match = False

            for remove_text in remove_texts:

                if not remove_text:
                    continue

                if re.search(
                    re.escape(remove_text),
                    old_caption,
                    flags=re.IGNORECASE
                ):

                    has_match = True
                    break

            if not has_match:

                unchanged += 1

                continue

            matched += 1

            # ------------------------------------------------
            # Clean caption
            # ------------------------------------------------

            new_caption = clean_text(
                old_caption,
                remove_texts
            )

            # Safety check
            if new_caption == old_caption:

                unchanged += 1

                continue

            # ------------------------------------------------
            # Edit caption
            # ------------------------------------------------

            try:

                if not new_caption:

                    await message.edit_caption(
                        caption=None
                    )

                else:

                    await message.edit_caption(
                        caption=new_caption
                    )

                cleaned += 1

                await mark_processed(
                    chat_id,
                    message.id
                )

            except Exception as e:

                failed += 1

                LOGGER.error(
                    "Old post edit failed | "
                    "chat=%s message=%s error=%s",
                    chat_id,
                    message.id,
                    e
                )

            # ------------------------------------------------
            # LIVE PROGRESS
            #
            # Every 25 scanned messages.
            # ------------------------------------------------

            if (
                scanned - last_progress >= 25
            ):

                last_progress = scanned

                if not OLD_CLEAN_RUNNING.get(
                    user_id,
                    False
                ):

                    return {
                        "error": "cancelled",
                        "scanned": scanned,
                        "media_posts": media_posts,
                        "matched": matched,
                        "cleaned": cleaned,
                        "unchanged": unchanged,
                        "failed": failed,
                    }

                try:

                    await progress_message.edit_text(

                        "🧹 <b>Old Post Cleaner Running...</b>\n\n"

                        f"📢 <b>{html.escape(str(channel_title))}</b>\n"
                        f"🆔 <code>{chat_id}</code>\n\n"

                        f"🔍 Scanned: <b>{scanned}</b>\n"
                        f"🎬 Media Posts: <b>{media_posts}</b>\n"
                        f"🎯 Matching: <b>{matched}</b>\n"
                        f"✅ Cleaned: <b>{cleaned}</b>\n"
                        f"⏭️ No Match: <b>{unchanged}</b>\n"
                        f"❌ Failed: <b>{failed}</b>\n\n"

                        "⏳ পুরো Channel History scan চলছে...\n"
                        "নতুন থেকে পুরনো পোস্টের দিকে যাচাই করা হচ্ছে।",

                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel Cleaner",
                                        callback_data="ac_old_cancel"
                                    )
                                ]
                            ]
                        ),

                        parse_mode=enums.ParseMode.HTML
                    )

                except Exception as e:

                    LOGGER.warning(
                        "Progress update failed: %s",
                        e
                    )

        # ----------------------------------------------------
        # Finished
        # ----------------------------------------------------

        return {
            "error": None,
            "scanned": scanned,
            "media_posts": media_posts,
            "matched": matched,
            "cleaned": cleaned,
            "unchanged": unchanged,
            "failed": failed,
        }

    except Exception as e:

        LOGGER.exception(
            "Old cleaner failed"
        )

        return {
            "error": str(e),
            "scanned": scanned,
            "media_posts": media_posts,
            "matched": matched,
            "cleaned": cleaned,
            "unchanged": unchanged,
            "failed": failed,
        }

    finally:

        OLD_CLEAN_RUNNING.pop(
            user_id,
            None
        )


# ============================================================
# OLD CLEAN START
# ============================================================

async def start_old_clean(
    client,
    query,
    chat_id
):

    user_id = query.from_user.id

    if OLD_CLEAN_RUNNING.get(
        user_id
    ):

        await query.answer(
            "Old Cleaner already running!",
            show_alert=True
        )

        return

    # --------------------------------------------------------
    # Start message
    # --------------------------------------------------------

    await query.message.edit_text(

        "⏳ <b>Old Post Cleaner Starting...</b>\n\n"

        f"📢 Channel:\n"
        f"<code>{html.escape(str(chat_id))}</code>\n\n"

        "🔍 <b>Scan Mode:</b> Full Channel History\n\n"

        "Channel-এর সবচেয়ে নতুন post থেকে "
        "সবচেয়ে পুরনো post পর্যন্ত scan হবে।\n\n"

        "শুধু Video/Document/Audio post-এর "
        "caption-এ Remove Text List-এর text "
        "পাওয়া গেলে caption edit করা হবে।\n\n"

        "⏳ Starting...",

        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel Cleaner",
                        callback_data="ac_old_cancel"
                    )
                ]
            ]
        ),

        parse_mode=enums.ParseMode.HTML
    )

    await query.answer()

    # --------------------------------------------------------
    # Run full-history cleaner
    # --------------------------------------------------------

    result = await clean_old_posts(
        client,
        chat_id,
        query.message
    )

    # --------------------------------------------------------
    # Already running
    # --------------------------------------------------------

    if result.get(
        "error"
    ) == "already_running":

        await query.message.edit_text(
            "⚠️ Old Cleaner ইতিমধ্যে চলছে।"
        )

        return

    # --------------------------------------------------------
    # Channel not found
    # --------------------------------------------------------

    if result.get(
        "error"
    ) == "channel_not_found":

        await query.message.edit_text(

            "❌ <b>Channel পাওয়া যায়নি</b>\n\n"

            "Channel ID এবং Bot permission "
            "চেক করুন।",

            parse_mode=enums.ParseMode.HTML
        )

        return

    # --------------------------------------------------------
    # Remove text empty
    # --------------------------------------------------------

    if result.get(
        "error"
    ) == "remove_text_empty":

        await query.message.edit_text(

            "⚠️ <b>Remove Text List Empty</b>\n\n"

            "প্রথমে Auto Cleaner → "
            "➕ Add Remove Text থেকে "
            "যে text remove করতে চান সেট করুন।",

            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Main Menu",
                            callback_data="ac_refresh"
                        )
                    ]
                ]
            ),

            parse_mode=enums.ParseMode.HTML
        )

        return

    # --------------------------------------------------------
    # Cancelled
    # --------------------------------------------------------

    if result.get(
        "error"
    ) == "cancelled":

        scanned = result.get(
            "scanned",
            0
        )

        media_posts = result.get(
            "media_posts",
            0
        )

        matched = result.get(
            "matched",
            0
        )

        cleaned = result.get(
            "cleaned",
            0
        )

        unchanged = result.get(
            "unchanged",
            0
        )

        failed = result.get(
            "failed",
            0
        )

        await query.message.edit_text(

            "🛑 <b>Old Post Cleaner Cancelled</b>\n\n"

            f"📢 Channel:\n"
            f"<code>{html.escape(str(chat_id))}</code>\n\n"

            f"🔍 Scanned: <b>{scanned}</b>\n"
            f"🎬 Media Posts: <b>{media_posts}</b>\n"
            f"🎯 Matching: <b>{matched}</b>\n"
            f"✅ Cleaned: <b>{cleaned}</b>\n"
            f"⏭️ No Match: <b>{unchanged}</b>\n"
            f"❌ Failed: <b>{failed}</b>\n\n"

            "⚠️ Cleaner মাঝপথে বন্ধ করা হয়েছে।",

            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📂 Old Post Cleaner",
                            callback_data="ac_oldclean"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔙 Main Menu",
                            callback_data="ac_refresh"
                        )
                    ],
                ]
            ),

            parse_mode=enums.ParseMode.HTML
        )

        return

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    scanned = result.get(
        "scanned",
        0
    )

    media_posts = result.get(
        "media_posts",
        0
    )

    matched = result.get(
        "matched",
        0
    )

    cleaned = result.get(
        "cleaned",
        0
    )

    unchanged = result.get(
        "unchanged",
        0
    )

    failed = result.get(
        "failed",
        0
    )

    error = result.get(
        "error"
    )

    # --------------------------------------------------------
    # Error
    # --------------------------------------------------------

    if error:

        error_text = str(error)

        if len(error_text) > 500:

            error_text = (
                error_text[:500]
                + "..."
            )

        await query.message.edit_text(

            "⚠️ <b>Old Cleaner Finished with Error</b>\n\n"

            f"📢 Channel:\n"
            f"<code>{html.escape(str(chat_id))}</code>\n\n"

            f"🔍 Scanned: <b>{scanned}</b>\n"
            f"🎬 Media Posts: <b>{media_posts}</b>\n"
            f"🎯 Matching: <b>{matched}</b>\n"
            f"✅ Cleaned: <b>{cleaned}</b>\n"
            f"⏭️ No Match: <b>{unchanged}</b>\n"
            f"❌ Failed: <b>{failed}</b>\n\n"

            f"<code>{html.escape(error_text)}</code>",

            parse_mode=enums.ParseMode.HTML
        )

        return

    # --------------------------------------------------------
    # Final result
    # --------------------------------------------------------

    await query.message.edit_text(

        "✅ <b>Old Post Cleaner Finished</b>\n\n"

        f"📢 Channel:\n"
        f"<code>{html.escape(str(chat_id))}</code>\n\n"

        f"🔍 Total Scanned: <b>{scanned}</b>\n"
        f"🎬 Media Posts: <b>{media_posts}</b>\n"
        f"🎯 Matching Posts: <b>{matched}</b>\n"
        f"✅ Cleaned: <b>{cleaned}</b>\n"
        f"⏭️ No Match: <b>{unchanged}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n\n"

        "🎉 <b>পুরো Channel History scan complete.</b>",

        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📂 Old Post Cleaner",
                        callback_data="ac_oldclean"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📢 Channel Settings",
                        callback_data="ac_channels"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Main Menu",
                        callback_data="ac_refresh"
                    )
                ],
            ]
        ),

        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# CALLBACK HANDLER
# ============================================================

@Client.on_callback_query(
    filters.regex(r"^ac_")
)
async def cleaner_callback(
    client,
    query
):

    if query.from_user.id != OWNER_ID:

        return await query.answer(
            "এটি শুধুমাত্র এডমিনের জন্য!",
            show_alert=True
        )

    user_id = query.from_user.id
    data = query.data

    # ========================================================
    # MAIN TOGGLE
    # ========================================================

    if data == "ac_toggle":

        settings = await get_settings()

        current = settings.get(
            "enabled",
            False
        )

        await update_settings(
            {
                "enabled": not current
            }
        )

        await query.answer(
            (
                "Auto Cleaner Enabled"
                if not current
                else
                "Auto Cleaner Disabled"
            )
        )

    # ========================================================
    # CAPTION TOGGLE
    # ========================================================

    elif data == "ac_caption":

        settings = await get_settings()

        current = settings.get(
            "caption_cleaner",
            True
        )

        await update_settings(
            {
                "caption_cleaner": not current
            }
        )

        await query.answer(
            (
                "Caption Cleaner Enabled"
                if not current
                else
                "Caption Cleaner Disabled"
            )
        )

    # ========================================================
    # ADD REMOVE TEXT
    # ========================================================

    elif data == "ac_add":

        CLEANER_STATE[
            user_id
        ] = "add_text"

        await query.message.edit_text(

            "➕ <b>Add Remove Text</b>\n\n"

            "যে text/copyright/website "
            "caption থেকে remove করতে চান "
            "সেটা পাঠান।\n\n"

            "উদাহরণ:\n"
            "<code>[CineBari.com]</code>\n\n"

            "Cancel করতে /cancel পাঠান।",

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # DELETE REMOVE TEXT MENU
    # ========================================================

    elif data == "ac_delete":

        texts = await get_remove_texts()

        if not texts:

            await query.answer(
                "Remove text list empty!",
                show_alert=True
            )

            return

        buttons = []

        for index, value in enumerate(
            texts
        ):

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"❌ {value[:40]}",
                        callback_data=(
                            f"ac_del_{index}"
                        )
                    )
                ]
            )

        buttons.append(
            [
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="ac_refresh"
                )
            ]
        )

        await query.message.edit_text(

            "➖ <b>Delete Remove Text</b>\n\n"
            "যেটা delete করতে চান "
            "সেটাতে চাপুন:",

            reply_markup=InlineKeyboardMarkup(
                buttons
            ),

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # DELETE REMOVE TEXT
    # ========================================================

    elif data.startswith(
        "ac_del_"
    ):

        try:

            index = int(
                data.split("_")[-1]
            )

            texts = await get_remove_texts()

            if index >= len(texts):

                await query.answer(
                    "Text not found!",
                    show_alert=True
                )

                return

            await delete_remove_text(
                texts[index]
            )

            await query.answer(
                "Removed successfully!"
            )

        except Exception as e:

            LOGGER.error(
                "Delete text error: %s",
                e
            )

            await query.answer(
                "Delete failed!",
                show_alert=True
            )

    # ========================================================
    # REMOVE TEXT LIST
    # ========================================================

    elif data == "ac_list":

        texts = await get_remove_texts()

        if not texts:

            text = (
                "📋 <b>Remove Text List</b>\n\n"
                "কোনো text সেট করা নেই।"
            )

        else:

            lines = []

            for index, value in enumerate(
                texts,
                start=1
            ):

                lines.append(
                    f"{index}. "
                    f"<code>{html.escape(str(value))}</code>"
                )

            text = (
                "📋 <b>Remove Text List</b>\n\n"
                + "\n".join(lines)
            )

        await query.message.edit_text(

            text,

            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="ac_refresh"
                        )
                    ]
                ]
            ),

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # CHANNEL SETTINGS
    # ========================================================

    elif data == "ac_channels":

        await query.message.edit_text(

            await channel_settings_text(),

            reply_markup=await channel_settings_keyboard(),

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # ADD CHANNEL
    # ========================================================

    elif data == "ac_ch_add":

        CLEANER_STATE[
            user_id
        ] = "add_channel"

        await query.message.edit_text(

            "➕ <b>Add Channel</b>\n\n"

            "Channel ID পাঠান।\n\n"

            "উদাহরণ:\n"
            "<code>-1001234567890</code>\n\n"

            "⚠️ Bot-কে ওই Channel-এর "
            "Admin হতে হবে।\n\n"

            "Cancel করতে /cancel পাঠান।",

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # CHANNEL TOGGLE
    # ========================================================

    elif data.startswith(
        "ac_ch_toggle:"
    ):

        chat_id = data.split(
            ":",
            1
        )[1]

        channels = await get_channels()

        if chat_id not in channels:

            await query.answer(
                "Channel not found!",
                show_alert=True
            )

            return

        current = bool(
            channels[chat_id]
        )

        await set_channel_status(
            chat_id,
            not current
        )

        await query.answer(
            (
                "Channel Enabled"
                if not current
                else
                "Channel Disabled"
            )
        )

        await query.message.edit_text(

            await channel_settings_text(),

            reply_markup=await channel_settings_keyboard(),

            parse_mode=enums.ParseMode.HTML
        )

        return

    # ========================================================
    # CHANNEL DELETE
    # ========================================================

    elif data.startswith(
        "ac_ch_delete:"
    ):

        chat_id = data.split(
            ":",
            1
        )[1]

        channels = await get_channels()

        if chat_id not in channels:

            await query.answer(
                "Channel not found!",
                show_alert=True
            )

            return

        await query.message.edit_text(

            "⚠️ <b>Delete Channel?</b>\n\n"

            f"Channel ID:\n"
            f"<code>{html.escape(chat_id)}</code>\n\n"

            "Delete করলে Channel Settings "
            "থেকে channel মুছে যাবে।\n\n"

            "এবং নতুন post এলে "
            "automatically আবার add হবে না।",

            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Yes, Delete",
                            callback_data=(
                                f"ac_ch_confirm_delete:{chat_id}"
                            )
                        ),

                        InlineKeyboardButton(
                            "❌ Cancel",
                            callback_data="ac_channels"
                        ),
                    ]
                ]
            ),

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # CONFIRM DELETE
    # ========================================================

    elif data.startswith(
        "ac_ch_confirm_delete:"
    ):

        chat_id = data.split(
            ":",
            1
        )[1]

        await delete_channel(
            chat_id
        )

        await query.answer(
            "Channel deleted successfully!"
        )

        await query.message.edit_text(

            await channel_settings_text(),

            reply_markup=await channel_settings_keyboard(),

            parse_mode=enums.ParseMode.HTML
        )

        return

    # ========================================================
    # OLD CLEAN MAIN MENU
    # ========================================================

    elif data == "ac_oldclean":

        await query.message.edit_text(

            await oldclean_menu(),

            reply_markup=await oldclean_keyboard(),

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # SELECT CHANNEL FOR OLD CLEAN
    # ========================================================

    elif data.startswith(
        "ac_old_channel:"
    ):

        chat_id = data.split(
            ":",
            1
        )[1]

        channels = await get_channels()

        if chat_id not in channels:

            await query.answer(
                "Channel not found!",
                show_alert=True
            )

            return

        # ----------------------------------------------------
        # Full history confirmation
        # ----------------------------------------------------

        await query.message.edit_text(

            "📂 <b>Old Post Cleaner</b>\n\n"

            f"📢 Channel:\n"
            f"<code>{html.escape(chat_id)}</code>\n\n"

            "🔍 <b>Scan Mode:</b>\n"
            "পুরো Channel History\n\n"

            "নতুন থেকে সবচেয়ে পুরনো post পর্যন্ত "
            "সব message scan করা হবে।\n\n"

            "শুধু Video/Document/Audio post-এর "
            "caption-এ Remove Text List-এর "
            "text থাকলে সেটি clean করা হবে।\n\n"

            "⚠️ Channel বড় হলে সময় লাগতে পারে।",

            reply_markup=await oldclean_start_keyboard(
                chat_id
            ),

            parse_mode=enums.ParseMode.HTML
        )

        await query.answer()

        return

    # ========================================================
    # OLD CLEAN START
    # ========================================================

    elif data.startswith(
        "ac_old_start:"
    ):

        chat_id = data.split(
            ":",
            1
        )[1]

        channels = await get_channels()

        if chat_id not in channels:

            await query.answer(
                "Channel not found!",
                show_alert=True
            )

            return

        await start_old_clean(
            client,
            query,
            chat_id
        )

        return

    # ========================================================
    # OLD CLEAN CANCEL
    # ========================================================

    elif data == "ac_old_cancel":

        if OLD_CLEAN_RUNNING.get(
            user_id,
            False
        ):

            OLD_CLEAN_RUNNING[
                user_id
            ] = False

            await query.answer(
                "Stopping Old Cleaner..."
            )

        else:

            await query.answer(
                "Old Cleaner is not running.",
                show_alert=True
            )

        return

    # ========================================================
    # REFRESH
    # ========================================================

    elif data == "ac_refresh":

        await query.answer()

    else:

        await query.answer()

    # ========================================================
    # MAIN MENU
    # ========================================================

    await query.message.edit_text(

        await cleaner_menu(),

        reply_markup=await cleaner_keyboard(),

        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# PRIVATE TEXT INPUT
# ============================================================

@Client.on_message(
    filters.private
    & filters.text
    & ~filters.command(
        [
            "cancel",
            "start",
            "cleaner",
            "oldclean",
        ]
    )
)
async def cleaner_text_input(
    client,
    message
):

    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        return

    user_id = message.from_user.id

    state = CLEANER_STATE.get(
        user_id
    )

    # ========================================================
    # ADD REMOVE TEXT
    # ========================================================

    if state == "add_text":

        value = message.text.strip()

        if not value:

            await message.reply_text(
                "⚠️ Empty text দেওয়া যাবে না।"
            )

            return

        await add_remove_text(
            value
        )

        CLEANER_STATE.pop(
            user_id,
            None
        )

        await message.reply_text(

            "✅ <b>Remove Text Added</b>\n\n"
            f"<code>{html.escape(value)}</code>",

            parse_mode=enums.ParseMode.HTML
        )

        return

    # ========================================================
    # ADD CHANNEL
    # ========================================================

    if state == "add_channel":

        value = message.text.strip()

        # ----------------------------------------------------
        # Validate Channel ID
        # ----------------------------------------------------

        if not re.fullmatch(
            r"-100\d{5,}",
            value
        ):

            await message.reply_text(

                "⚠️ সঠিক Channel ID দিন।\n\n"

                "উদাহরণ:\n"
                "<code>-1001234567890</code>\n\n"

                "আবার চেষ্টা করুন অথবা /cancel দিন।",

                parse_mode=enums.ParseMode.HTML
            )

            return

        chat_id = int(
            value
        )

        # ----------------------------------------------------
        # Check Channel
        # ----------------------------------------------------

        try:

            chat = await client.get_chat(
                chat_id
            )

        except Exception as e:

            LOGGER.error(
                "Channel verification failed: %s",
                e
            )

            await message.reply_text(

                "❌ Channel পাওয়া যাচ্ছে না।\n\n"

                "চেক করুন:\n"
                "• Channel ID সঠিক কিনা\n"
                "• Bot ওই Channel-এ আছে কিনা\n"
                "• Bot-এর permission আছে কিনা\n\n"

                "আবার চেষ্টা করুন অথবা /cancel দিন।"
            )

            return

        # ----------------------------------------------------
        # Bot membership/admin
        # ----------------------------------------------------

        try:

            me = await client.get_me()

            member = await client.get_chat_member(
                chat_id,
                me.id
            )

            if member.status not in (
                enums.ChatMemberStatus.ADMINISTRATOR,
                enums.ChatMemberStatus.OWNER,
            ):

                await message.reply_text(

                    "⚠️ Bot এই Channel-এর Admin নয়।\n\n"

                    "প্রথমে Bot-কে Channel-এর "
                    "Admin করুন।"
                )

                return

        except Exception as e:

            LOGGER.warning(
                "Could not verify bot admin status: %s",
                e
            )

        # ----------------------------------------------------
        # Add
        # ----------------------------------------------------

        await add_channel(
            chat_id,
            True
        )

        CLEANER_STATE.pop(
            user_id,
            None
        )

        channel_title = getattr(
            chat,
            "title",
            None
        ) or "Unknown Channel"

        await message.reply_text(

            "✅ <b>Channel Added Successfully</b>\n\n"

            f"📢 Name: "
            f"<b>{html.escape(str(channel_title))}</b>\n"

            f"🆔 ID: "
            f"<code>{chat_id}</code>\n"

            "📊 Status: <b>🟢 ON</b>\n\n"

            "📂 এই Channel-এর আগের post clean করতে "
            "Channel Settings → "
            "🧹 Clean Old Posts ব্যবহার করুন।\n\n"

            "🆕 নতুন post এখন থেকে "
            "Auto Cleaner process করবে।",

            parse_mode=enums.ParseMode.HTML
        )

        return


# ============================================================
# /oldclean
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("oldclean")
)
async def oldclean_command(
    client,
    message
):

    if not message.from_user:
        return

    if message.from_user.id != OWNER_ID:
        return

    await message.reply_text(

        await oldclean_menu(),

        reply_markup=await oldclean_keyboard(),

        parse_mode=enums.ParseMode.HTML
    )


# ============================================================
# /cancel
# ============================================================

@Client.on_message(
    filters.private
    & filters.command("cancel")
)
async def cancel_cleaner_state(
    client,
    message
):

    if not message.from_user:
        return

    user_id = message.from_user.id

    if user_id != OWNER_ID:
        return

    cancelled = False

    if user_id in CLEANER_STATE:

        CLEANER_STATE.pop(
            user_id,
            None
        )

        cancelled = True

    # Old cleaner stop
    if OLD_CLEAN_RUNNING.get(
        user_id,
        False
    ):

        OLD_CLEAN_RUNNING[
            user_id
        ] = False

        cancelled = True

    if cancelled:

        await message.reply_text(
            "❌ Cancelled."
        )

    else:

        await message.reply_text(
            "ℹ️ কোনো active task নেই।"
        )
