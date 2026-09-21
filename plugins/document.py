# ©️ LISA-KOREA | @LISA_FAN_LK | NT_BOT_CHANNEL
# Document Handler - সব ধরনের ডকুমেন্টের জন্য

import logging
logger = logging.getLogger(__name__)

import os
import time
import random
import asyncio
from PIL import Image
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pyrogram.errors import MessageNotModified

from plugins.script import Translation
from plugins.database.add import AddUser
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot
from plugins.functions.forcesub import handle_force_subscribe
from plugins.database.database import db
from plugins.config import Config
from plugins.settings.settings import *
from plugins.functions.display_progress import progress_for_pyrogram, humanbytes


# ====================================================================
# ডকুমেন্ট হ্যান্ডলার - সব ধরনের ফাইল সাপোর্ট করবে
# ====================================================================
@Client.on_message(filters.document & filters.private)
async def document_handler(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
        fsub = await handle_force_subscribe(bot, update)
        if fsub == 400:
            return

    # ডকুমেন্টের মাইম টাইপ চেক করা হচ্ছে
    mime_type = update.document.mime_type or ""
    is_video_doc = mime_type.startswith("video/")

    # ইউজারের কাস্টম থাম্বনেইল পাথ
    custom_thumb_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    generated_thumb_path = None
    file = None
    thumb_image_path = None

    # ডাউনলোড লোকেশন তৈরি
    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id),
        f"{update.id}_{update.document.file_name or 'file'}"
    )

    m = await bot.send_message(
        chat_id=update.chat.id,
        text="**📄 ডকুমেন্ট রিসিভ করেছি ✅\nডাউনলোড শুরু হচ্ছে... ⏳**"
    )

    try:
        c_time = time.time()
        file = await bot.download_media(
            message=update,
            file_name=download_location,
            progress=progress_for_pyrogram,
            progress_args=(
                "**📥 ডাউনলোড হচ্ছে... ⏳**",
                m,
                c_time
            )
        )

        # যদি ফাইলটি ভিডিও হয়, তবে ভিডিও থেকে স্ক্রিনশট বা মেটাডেটা বের করা হবে
        if is_video_doc:
            width, height, duration = await Mdata01(file)
            thumb_image_path = await Gthumb02(bot, update, duration, file)
            
            # স্ক্রিনশট আলাদাভাবে ট্র্যাক করা (ক্লিনআপের জন্য)
            if thumb_image_path != custom_thumb_path:
                generated_thumb_path = thumb_image_path
        else:
            # ভিডিও না হলে থাম্বনেইল ব্যবহার করা হবে না (টেলিগ্রাম ডিফল্ট দেখাবে) 
            # অথবা ইউজারের কাস্টম থাম্বনেইল থাকলে সেটা ব্যবহার করবে
            db_thumb = await db.get_thumbnail(update.from_user.id)
            if db_thumb is not None and os.path.exists(custom_thumb_path):
                thumb_image_path = custom_thumb_path

        await m.edit_text("**✅ প্রসেস সম্পন্ন\nফাইল পাঠানো হচ্ছে... 🚀**")

        # --- ক্যাপশন ও ফাইল নাম ঠিক করা ---
        caption = update.caption if update.caption else "**📄 এখানে আপনার ফাইল 📁**"
        if len(caption) > 1024:
            caption = caption[:1024]

        file_name = update.document.file_name if update.document.file_name else f"{update.id}_file"
        # -------------------------------------

        u_time = time.time()
        
        # ভিডিও ডকুমেন্ট হলে send_video ব্যবহার করা হবে যাতে স্ট্রিমিং করা যায়
        if is_video_doc:
            await bot.send_video(
                chat_id=update.chat.id,
                video=file,
                duration=duration,
                width=width,
                height=height,
                supports_streaming=True,
                thumb=thumb_image_path,
                caption=caption,
                file_name=file_name,
                progress=progress_for_pyrogram,
                progress_args=(
                    "**📤 আপলোড হচ্ছে... 🚀**",
                    m,
                    u_time
                )
            )
        else:
            # অন্যান্য ফাইলের জন্য send_document ব্যবহার করা হবে
            await bot.send_document(
                chat_id=update.chat.id,
                document=file,
                thumb=thumb_image_path,
                caption=caption,
                file_name=file_name,
                progress=progress_for_pyrogram,
                progress_args=(
                    "**📤 আপলোড হচ্ছে... 🚀**",
                    m,
                    u_time
                )
            )
            
        await m.delete()

    except Exception as e:
        logger.error(f"Document handler error: {e}")
        await m.edit_text(f"**❌ এরর হয়েছে\nকারণ: {e}**")

    finally:
        # সার্ভার ক্লিন রাখা
        try:
            if file and os.path.lexists(file):
                os.remove(file)

            # শুধুমাত্র ভিডিও থেকে নেওয়া স্ক্রিনশট ডিলিট করা
            if generated_thumb_path and os.path.lexists(generated_thumb_path):
                os.remove(generated_thumb_path)

        except Exception as e:
            logger.warning(f"Error cleaning up document files: {e}")


# ====================================================================
# মেটাডেটা এক্সট্র্যাক্টর ফাংশনগুলো
# ====================================================================
async def Mdata01(download_directory):
    width = 0
    height = 0
    duration = 0
    metadata = extractMetadata(createParser(download_directory))
    if metadata is not None:
        if metadata.has("duration"):
            duration = metadata.get('duration').seconds
        if metadata.has("width"):
            width = metadata.get("width")
        if metadata.has("height"):
            height = metadata.get("height")
    return width, height, duration


# ====================================================================
# থাম্বনেইল জেনারেটর ফাংশন
# ====================================================================
async def Gthumb02(bot, update, duration, download_directory):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)

    # ইউজারের কাস্টম থাম্বনেইল থাকলে সেটাই ব্যবহার করবে
    if db_thumbnail is not None and os.path.exists(thumb_image_path):
        return thumb_image_path
    # না থাকলে ভিডিও থেকে স্ক্রিনশট নিবে
    elif duration > 1:
        return await take_screen_shot(
            download_directory,
            os.path.dirname(download_directory),
            random.randint(0, duration - 1)
        )
    else:
        return None
