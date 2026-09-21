# ©️ LISA-KOREA | @LISA_FAN_LK | NT_BOT_CHANNEL

import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import random
import os
from PIL import Image
import time
from pyrogram import enums
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
from plugins.settings.settings import *
from plugins.functions.display_progress import progress_for_pyrogram, humanbytes


@Client.on_message(filters.photo)
async def save_photo(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
      fsub = await handle_force_subscribe(bot, update)
      if fsub == 400:
        return
    
    # থাম্বনেইল লোকাল স্টোরেজে সেভ করা
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
        # লোকাল থাম্বনেইল ফাইল ডিলিট করা
        if os.path.exists(download_location + ".jpg"):
            os.remove(download_location + ".jpg")
    except Exception as e:
        logger.error(f"Error deleting thumbnail: {e}")

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
                # style প্যারামিটার সরিয়ে দেওয়া হয়েছে যাতে এরর না আসে
                [[InlineKeyboardButton("🗑️ 𝙳𝙴𝙻𝙴𝚃𝙴 𝚃𝙷𝚄𝙼𝙱𝙽𝙰𝙸𝙻", callback_data="deleteThumbnail")]]
            ),
        )
    else:
        await update.reply_text(text=f"𝙽𝙾 𝚃𝙷𝚄𝙼𝙱𝙽𝙰𝙸𝙻 😐")


async def Gthumb01(bot, update):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)
    
    if db_thumbnail is not None:
        if not os.path.exists(thumb_image_path):
            await bot.download_media(message=db_thumbnail, file_name=thumb_image_path)
        
        # PIL দিয়ে ইমেজ রিসাইজ করার সঠিক নিয়ম
        img = Image.open(thumb_image_path)
        img = img.resize((100, 100)) # এখানে img এর ভেতরে রিসাইজ করে সেভ করতে হবে
        img.save(thumb_image_path, "JPEG")
        return thumb_image_path
    else:
        return None


async def Gthumb02(bot, update, duration, download_directory):
    thumb_image_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    db_thumbnail = await db.get_thumbnail(update.from_user.id)
    
    # ইউজারের কাস্টম থাম্বনেইল থাকলে সেটাই ব্যবহার করবে (আবার ডাউনলোড করার দরকার নেই)
    if db_thumbnail is not None and os.path.exists(thumb_image_path):
        return thumb_image_path
    # না থাকলে ভিডিও থেকে স্ক্রিনশট নিবে
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


# ====== প্রগ্রেস সহ ভিডিও হ্যান্ডলার কোড ======
@Client.on_message(filters.video & filters.private)
async def video_handler(bot, update):
    await AddUser(bot, update)
    if Config.UPDATES_CHANNEL:
        fsub = await handle_force_subscribe(bot, update)
        if fsub == 400:
            return

    m = await bot.send_message(
        chat_id=update.chat.id,
        text="**ভিডিও রিসিভ করেছি ✅\nডাউনলোড শুরু হচ্ছে... ⏳**"
    )

    download_location = os.path.join(
        Config.DOWNLOAD_LOCATION,
        str(update.from_user.id),
        f"{update.id}.mp4"
    )
    
    # কাস্টম থাম্বনেইলের পাথ আগেই বের করে রাখা, যাতে finally ব্লকে কনফিউশন না হয়
    custom_thumb_path = f"{Config.DOWNLOAD_LOCATION}/{str(update.from_user.id)}.jpg"
    generated_thumb_path = None # এটি পরে ভিডিও থেকে তোলা স্ক্রিনশট হিসেবে ব্যবহার করা হবে

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
        
        width, height, duration = await Mdata01(file)
        
        # কাস্টম থাম্বনেইল বের করা (যদি না থাকে তবে ভিডিও থেকে স্ক্রিনশট নেওয়া হবে)
        thumb_image_path = await Gthumb02(bot, update, duration, file)
        
        # যদি স্ক্রিনশট নেওয়া হয়, তবে সেটি আলাদা ভেরিয়েবলে রাখা
        if thumb_image_path != custom_thumb_path:
            generated_thumb_path = thumb_image_path
        
        await m.edit_text("**প্রসেস সম্পন্ন ✅\nথাম্বনেইল সহ ভিডিও পাঠানো হচ্ছে... 🚀**")
        
        u_time = time.time()
        await bot.send_video(
            chat_id=update.chat.id,
            video=file,
            duration=duration,
            width=width,
            height=height,
            supports_streaming=True,
            thumb=thumb_image_path,
            caption="**এখানে আপনার ভিডিও 🎬**",
            progress=progress_for_pyrogram,
            progress_args=(
                "**আপলোড হচ্ছে... 🚀**",
                m,
                u_time
            )
        )
        await m.delete()
        
    except Exception as e:
        await m.edit_text(f"**এরর হয়েছে ❌\nকারণ: {e}**")
        
    finally:
        # সার্ভার ক্লিন রাখা
        try:
            # ভিডিও ফাইল ডিলিট করা
            if 'file' in locals() and os.path.lexists(file):
                os.remove(file)
            
            # শুধুমাত্র ভিডিও থেকে নেওয়া স্ক্রিনশট ডিলিট করা, ইউজারের কাস্টম থাম্বনেইল নয়!
            if generated_thumb_path and os.path.lexists(generated_thumb_path):
                os.remove(generated_thumb_path)
                
        except Exception as e:
            logger.warning(f"Error cleaning up files: {e}")
# ====== কোড শেষ ======
