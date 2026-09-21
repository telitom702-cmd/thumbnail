# ©️ LISA-KOREA | @LISA_FAN_LK | NT_BOT_CHANNEL

import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import random
import numpy
import os
from PIL import Image
import time
from pyrogram import enums
# the Strings used for this "thing"
from plugins.script import Translation
from pyrogram import Client
from plugins.database.add import AddUser
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
logging.getLogger("pyrogram").setLevel(logging.WARNING)
from pyrogram import filters
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot
import psutil
import shutil
import string
import asyncio
from asyncio import TimeoutError
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery, ForceReply
from plugins.functions.forcesub import handle_force_subscribe
from plugins.database.database import db
from plugins.config import Config
from plugins.database.database import db
from plugins.settings.settings import *
from plugins.functions.display_progress import progress_for_pyrogram, humanbytes

# ইউজারের কাজ বন্ধ করার জন্য একটি ডিকশনারি (Cancel সিস্টেমের জন্য)
active_downloads = {}

@Client.on_message(filters.photo)
async def save_photo(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
      fsub = await handle_force_subscribe(bot, update)
      if fsub == 400:
        return
    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id) + ".jpg"
    )
    await bot.download_media(
        message=update,
        file_name=download_location
    )
    await bot.send_message(
        chat_id=update.chat.id,
        text=Translation.SAVED_CUSTOM_THUMB_NAIL,
    )
    await db.set_thumbnail(update.from_user.id, thumbnail=update.photo.file_id)


@Client.on_message(filters.command(["delthumb"]))
async def delete_thumbnail(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
      fsub = await handle_force_subscribe(bot, update)
      if fsub == 400:
        return
    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id)
    )
    try:
        os.remove(download_location + ".jpg")
    except:
        pass
    await bot.send_message(
        chat_id=update.chat.id,
        text=Translation.DEL_ETED_CUSTOM_THUMB_NAIL,
    )
    await db.set_thumbnail(update.from_user.id, thumbnail=None)

@Client.on_message(filters.command("showthumb"))
async def viewthumbnail(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
      fsub = await handle_force_subscribe(bot, update)
      if fsub == 400:
        return   
    thumbnail = await db.get_thumbnail(update.from_user.id)
    if thumbnail is not None:
        await bot.send_photo(
        chat_id=update.chat.id,
        photo=thumbnail,
        caption=f"YOUR THUMBNAIL 🏞",
        reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🗑️ 𝙳𝙴𝙻𝙴𝚃𝙴 𝚃𝙳𝚄𝙼𝙱𝙽𝙰𝙸𝙻", callback_data="deleteThumbnail", style=enums.ButtonStyle.DANGER)]]
                ),
         )
    else:
        await update.reply_text(text=f"𝙽𝙾 𝚃𝙳𝚄𝙼𝙱𝙽𝙰𝙸𝙻 😐")


async def Gthumb01(bot, update):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)
    if db_thumbnail is not None:
        thumbnail = await bot.download_media(message=db_thumbnail, file_name=thumb_image_path)
        Image.open(thumbnail).convert("RGB").save(thumbnail)
        img = Image.open(thumbnail)
        img.resize((100, 100))
        img.save(thumbnail, "JPEG")
    else:
        thumbnail = None
    return thumbnail

async def Gthumb02(bot, update, duration, download_directory):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)
    
    if db_thumbnail is not None:
        return await bot.download_media(message=db_thumbnail, file_name=thumb_image_path)
    elif duration > 1:
        return await take_screen_shot(download_directory, os.path.dirname(download_directory), random.randint(0, duration - 1))
    else:
        return None

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

async def Mdata02(download_directory):
    width = 0
    duration = 0
    metadata = extractMetadata(createParser(download_directory))
    if metadata is not None:
        if metadata.has("duration"):
            duration = metadata.get('duration').seconds
        if metadata.has("width"):
            width = metadata.get("width")
    return width, duration

async def Mdata03(download_directory):
    metadata = extractMetadata(createParser(download_directory))
    return (
        metadata.get('duration').seconds
        if metadata is not None and metadata.has("duration")
        else 0
    )


# ====== Cancel করার জন্য Callback Query Handler ======
@Client.on_callback_query(filters.regex(r"^cancel$"))
async def cancel_process(bot, query: CallbackQuery):
    user_id = query.from_user.id
    if user_id in active_downloads:
        active_downloads[user_id].set()
        await query.answer("❌ প্রক্রিয়া বাতিল করা হচ্ছে...", show_alert=True)
        try:
            await query.message.edit_text("**❌ প্রক্রিয়া সফলভাবে বাতিল করা হয়েছে!**")
        except:
            pass
    else:
        await query.answer("কোনো চলমান প্রক্রিয়া পাওয়া যায়নি!", show_alert=True)


# ====== প্রগ্রেস সহ ভিডিও ও ডকুমেন্ট হ্যান্ডলার কোড ======
# আগে এখানে শুধু filters.video ছিল, এখন ডকুমেন্ট যোগ করা হয়েছে
@Client.on_message((filters.video | filters.document) & filters.private)
async def video_handler(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
        fsub = await handle_force_subscribe(bot, update)
        if fsub == 400:
            return

    cancel_button = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ বাতিল করুন (Cancel)", callback_data="cancel")]]
    )

    m = await bot.send_message(
        chat_id=update.chat.id,
        text="**ফাইল রিসিভ করেছি ✅\nডাউনলোড শুরু হচ্ছে... ⏳**",
        reply_markup=cancel_button
    )

    # ফাইলের এক্সটেনশন ও নাম বের করা
    if update.video:
        orig_name = update.video.file_name or f"{update.id}.mp4"
    elif update.document:
        orig_name = update.document.file_name or f"{update.id}.mp4"
    else:
        orig_name = f"{update.id}.mp4"
        
    file_ext = os.path.splitext(orig_name)[1] or ".mp4"

    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id),
        f"{update.id}{file_ext}"
    )
    
    active_downloads[update.from_user.id] = asyncio.Event()

    file = None
    thumb_image_path = None

    try:
        c_time = time.time()
        file = await bot.download_media(
            message=update,
            file_name=download_location,
            progress=progress_for_pyrogram,
            progress_args=(
                "**ডাউনলোড হচ্ছে... ⏳**",
                m,
                c_time
            )
        )

        if active_downloads[update.from_user.id].is_set():
            raise Exception("ইউজার দ্বারা প্রক্রিয়া বাতিল করা হয়েছে।")
        
        # ডকুমেন্ট ফাইলটি ভিডিও কিনা তা চেক করা হচ্ছে
        is_video_file = False
        if update.document:
            mime_type = update.document.mime_type or ""
            if "video" in mime_type:
                is_video_file = True
        elif update.video:
            is_video_file = True

        # ইউজারের সেটিংস চেক করা (সে ভিডিও নাকি ডকুমেন্ট চায়)
        user_data = await db.get_user_data(update.from_user.id)
        upload_as_doc = user_data.get("upload_as_doc", False) if user_data else False

        await m.edit_text(
            "**প্রসেস সম্পন্ন ✅\nথাম্বনেইল সহ ফাইল পাঠানো হচ্ছে... 🚀**",
            reply_markup=cancel_button
        )
        
        u_time = time.time()
        
        if update.media and update.caption:
            final_caption = update.caption
        else:
            final_caption = orig_name
        
        # যদি ইউজার সেটিংসে ভিডিও সিলেক্ট করে রাখে এবং ফাইলটি ভিডিও হয়
        if not upload_as_doc and is_video_file:
            width, height, duration = await Mdata01(file)
            thumb_image_path = await Gthumb02(bot, update, duration, file)
            
            await bot.send_video(
                chat_id=update.chat.id,
                video=file,
                duration=duration,
                width=width,
                height=height,
                supports_streaming=True,
                thumb=thumb_image_path,
                caption=final_caption,
                progress=progress_for_pyrogram,
                progress_args=(
                    "**আপলোড হচ্ছে... 🚀**",
                    m,
                    u_time
                )
            )
        # যদি ইউজার সেটিংসে ডকুমেন্ট সিলেক্ট করে রাখে বা ফাইলটি ভিডিও না হয়
        else:
            # ডকুমেন্ট হিসেবে আপলোডের সময় থাম্বনেইল যদি ডাটাবেসে থাকে
            thumb_image_path = await Gthumb01(bot, update)
            
            await bot.send_document(
                chat_id=update.chat.id,
                document=file,
                thumb=thumb_image_path,
                caption=final_caption,
                progress=progress_for_pyrogram,
                progress_args=(
                    "**আপলোড হচ্ছে... 🚀**",
                    m,
                    u_time
                )
            )

        if active_downloads[update.from_user.id].is_set():
            raise Exception("ইউজার দ্বারা প্রক্রিয়া বাতিল করা হয়েছে।")
        
        await m.delete()
        
    except Exception as e:
        error_text = str(e)
        if "বাতিল" in error_text or "Cancelled" in error_text:
            try:
                await m.edit_text(f"**❌ প্রক্রিয়া বাতিল করা হয়েছে!**")
            except:
                pass
        else:
            try:
                await m.edit_text(f"**এরর হয়েছে ❌\nকারণ: {e}**")
            except:
                pass
        
    finally:
        if update.from_user.id in active_downloads:
            del active_downloads[update.from_user.id]
            
        try:
            if file and os.path.lexists(file):
                os.remove(file)
            if thumb_image_path and os.path.lexists(thumb_image_path):
                os.remove(thumb_image_path)
        except Exception as e:
            logger.warning(f"Error cleaning up files: {e}")
# ====== কোড শেষ ======
