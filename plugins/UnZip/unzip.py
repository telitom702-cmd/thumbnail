# ©️ LISA-KOREA | @LISA_FAN_LK | NT_BOT_CHANNEL

import os
import time
import shutil
import tempfile
import asyncio
import logging
import random

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import MessageNotModified

from pyunpack import Archive

from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

from plugins.config import Config
from plugins.database.database import db
from plugins.database.add import AddUser
from plugins.functions.forcesub import handle_force_subscribe
from plugins.functions.display_progress import progress_for_pyrogram
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# SUPPORTED ARCHIVE FORMATS
# ============================================================

SUPPORTED_FORMATS = (
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".gz",
    ".bz2",
)


# ============================================================
# VIDEO FORMATS
# ============================================================

VIDEO_EXTENSIONS = (
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".mpeg",
    ".mpg",
    ".3gp",
    ".ts",
    ".m2ts",
)


# ============================================================
# ACTIVE TASKS
# ============================================================

active_tasks = {}


# ============================================================
# HELPER: SAFE EDIT
# ============================================================

async def safe_edit(message, text):
    try:
        if message and message.text != text:
            await message.edit_text(text)
    except MessageNotModified:
        pass
    except Exception:
        pass


# ============================================================
# HELPER: ARCHIVE CHECK
# ============================================================

def is_supported_archive(filename):
    if not filename:
        return False

    filename = filename.lower()

    return any(
        filename.endswith(ext)
        for ext in SUPPORTED_FORMATS
    )


# ============================================================
# HELPER: VIDEO CHECK
# ============================================================

def is_video_file(filename):
    if not filename:
        return False

    return filename.lower().endswith(VIDEO_EXTENSIONS)


# ============================================================
# HELPER: METADATA
# ============================================================

def get_video_metadata(file_path):
    """
    Returns:
        width,
        height,
        duration
    """

    width = 0
    height = 0
    duration = 0

    try:
        parser = createParser(file_path)

        if not parser:
            return width, height, duration

        metadata = extractMetadata(parser)

        if metadata:
            if metadata.has("duration"):
                duration_value = metadata.get("duration")

                if duration_value:
                    duration = int(duration_value.total_seconds())

            if metadata.has("width"):
                width = metadata.get("width") or 0

            if metadata.has("height"):
                height = metadata.get("height") or 0

    except Exception as e:
        logger.warning(
            f"Could not read video metadata: {e}"
        )

    return width, height, duration


# ============================================================
# HELPER: GET CUSTOM THUMBNAIL
# ============================================================

async def get_custom_thumbnail(client, user_id):
    """
    Uses the SAME thumbnail system as plugins/thumbnail.py.

    First checks:
        Config.DOWNLOAD_LOCATION/{user_id}.jpg

    If missing, downloads the saved Telegram photo
    using db.get_thumbnail().
    """

    try:
        download_dir = Config.DOWNLOAD_LOCATION

        os.makedirs(download_dir, exist_ok=True)

        thumb_path = os.path.join(
            download_dir,
            f"{user_id}.jpg"
        )

        # ----------------------------------------------------
        # If local thumbnail already exists
        # ----------------------------------------------------

        if os.path.exists(thumb_path):
            try:
                # Ensure it is a valid JPEG
                with Image.open(thumb_path) as img:
                    img.verify()

                return thumb_path

            except Exception:
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass

        # ----------------------------------------------------
        # Get Telegram file_id from database
        # ----------------------------------------------------

        thumbnail = await db.get_thumbnail(user_id)

        if not thumbnail:
            return None

        # ----------------------------------------------------
        # Download saved Telegram thumbnail
        # ----------------------------------------------------

        try:
            downloaded = await client.download_media(
                message=thumbnail,
                file_name=thumb_path
            )

            if downloaded and os.path.exists(thumb_path):
                return thumb_path

        except Exception as e:
            logger.warning(
                f"Could not download custom thumbnail: {e}"
            )

    except Exception as e:
        logger.warning(
            f"Custom thumbnail error: {e}"
        )

    return None


# ============================================================
# HELPER: PREPARE THUMBNAIL
# ============================================================

async def prepare_thumbnail(
    client,
    user_id,
    video_path,
    duration
):
    """
    Priority:

    1. User's saved custom thumbnail
    2. Screenshot from video
    3. None
    """

    # --------------------------------------------------------
    # CUSTOM THUMBNAIL
    # --------------------------------------------------------

    custom_thumb = await get_custom_thumbnail(
        client,
        user_id
    )

    if custom_thumb and os.path.exists(custom_thumb):
        return custom_thumb, False

    # --------------------------------------------------------
    # FALLBACK SCREENSHOT
    # --------------------------------------------------------

    if duration > 1:

        try:
            screenshot = await take_screen_shot(
                video_path,
                os.path.dirname(video_path),
                random.randint(
                    0,
                    max(1, duration - 1)
                )
            )

            if screenshot and os.path.exists(screenshot):
                return screenshot, True

        except Exception as e:
            logger.warning(
                f"Could not generate screenshot: {e}"
            )

    return None, False


# ============================================================
# HELPER: GET UPLOAD MODE
# ============================================================

async def get_upload_mode(user_id):
    """
    Existing database setting:

    False = VIDEO
    True  = DOCUMENT
    """

    try:
        return await db.get_upload_as_doc(user_id)

    except Exception as e:
        logger.warning(
            f"Could not read upload mode: {e}"
        )

        # Default = VIDEO
        return False


# ============================================================
# CANCEL BUTTON
# ============================================================

CANCEL_BUTTON = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data="cancel_unzip"
            )
        ]
    ]
)


# ============================================================
# CANCEL CALLBACK
# ============================================================

@Client.on_callback_query(
    filters.regex("^cancel_unzip$")
)
async def cancel_unzip(client, callback_query):

    user_id = callback_query.from_user.id

    task = active_tasks.get(user_id)

    if task:

        try:
            task.cancel()
        except Exception:
            pass

        active_tasks.pop(user_id, None)

        try:
            await callback_query.answer(
                "❌ Processing cancelled.",
                show_alert=True
            )
        except Exception:
            pass

        try:
            await callback_query.message.edit_text(
                "❌ **Archive processing cancelled.**"
            )
        except Exception:
            pass

    else:

        try:
            await callback_query.answer(
                "No active task found.",
                show_alert=True
            )
        except Exception:
            pass


# ============================================================
# SEND DOCUMENT
# ============================================================

async def send_document(
    client,
    chat_id,
    file_path,
    status_message,
    file_number,
    total_files
):

    filename = os.path.basename(file_path)

    text = (
        f"📦 **File {file_number}/{total_files}**\n\n"
        f"📄 `{filename}`\n\n"
        f"📤 **Uploading...**"
    )

    await safe_edit(
        status_message,
        text
    )

    upload_time = time.time()

    await client.send_document(
        chat_id=chat_id,

        # IMPORTANT:
        # Original extracted file path
        # No filename modification
        document=file_path,

        progress=progress_for_pyrogram,

        progress_args=(
            f"📤 **Uploading File {file_number}/{total_files}...**",
            status_message,
            upload_time
        )
    )


# ============================================================
# SEND VIDEO
# ============================================================

async def send_video(
    client,
    chat_id,
    user_id,
    video_path,
    status_message,
    file_number,
    total_files
):

    # --------------------------------------------------------
    # ORIGINAL FILENAME
    # --------------------------------------------------------

    original_filename = os.path.basename(
        video_path
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Filename is NEVER modified.
    #
    # No:
    # 🎬
    # File 13/24
    # Bot name
    # Numbering
    # Extra text
    #
    # will be added to filename.
    # --------------------------------------------------------

    await safe_edit(
        status_message,
        (
            f"🎬 `{original_filename}`\n\n"
            f"📦 **File {file_number}/{total_files}**\n"
            f"📤 **Preparing video...**"
        )
    )

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    width, height, duration = get_video_metadata(
        video_path
    )

    # --------------------------------------------------------
    # THUMBNAIL
    # --------------------------------------------------------

    thumbnail_path = None
    generated_thumbnail = False

    try:

        thumbnail_path, generated_thumbnail = (
            await prepare_thumbnail(
                client,
                user_id,
                video_path,
                duration
            )
        )

        # ----------------------------------------------------
        # UPLOAD STATUS
        # ----------------------------------------------------

        await safe_edit(
            status_message,
            (
                f"🎬 `{original_filename}`\n\n"
                f"📦 **File {file_number}/{total_files}**\n"
                f"⏱️ Duration: `{duration}s`\n"
                f"📐 Resolution: `{width}x{height}`\n\n"
                f"📤 **Uploading... 🚀**"
            )
        )

        upload_time = time.time()

        # ----------------------------------------------------
        # SEND VIDEO
        # ----------------------------------------------------

        await client.send_video(

            chat_id=chat_id,

            video=video_path,

            # IMPORTANT:
            # Telegram video duration
            duration=duration,

            # IMPORTANT:
            # Telegram video resolution
            width=width,
            height=height,

            # IMPORTANT:
            # Existing custom/generated thumbnail
            thumb=thumbnail_path,

            # IMPORTANT:
            # Original filename ONLY
            file_name=original_filename,

            supports_streaming=True,

            # ------------------------------------------------
            # IMPORTANT:
            #
            # No artificial caption is added.
            #
            # Therefore:
            # filename remains filename
            # progress remains status message
            # ------------------------------------------------

            progress=progress_for_pyrogram,

            progress_args=(
                f"📤 **Uploading File {file_number}/{total_files}...**",
                status_message,
                upload_time
            )
        )

    finally:

        # ----------------------------------------------------
        # DELETE GENERATED SCREENSHOT ONLY
        # ----------------------------------------------------

        if generated_thumbnail:
            try:
                if (
                    thumbnail_path
                    and os.path.exists(thumbnail_path)
                ):
                    os.remove(thumbnail_path)
            except Exception as e:
                logger.warning(
                    f"Could not remove generated thumbnail: {e}"
                )


# ============================================================
# PROCESS EXTRACTED FILES
# ============================================================

async def process_extracted_files(
    client,
    message,
    user_id,
    extract_dir,
    status_message
):

    # --------------------------------------------------------
    # FIND ALL FILES
    # --------------------------------------------------------

    files = []

    for root, dirs, filenames in os.walk(
        extract_dir
    ):

        for filename in filenames:

            full_path = os.path.join(
                root,
                filename
            )

            if os.path.isfile(full_path):
                files.append(full_path)

    # --------------------------------------------------------
    # SORT FILES
    # --------------------------------------------------------

    files.sort(
        key=lambda x: x.lower()
    )

    total_files = len(files)

    if total_files == 0:

        await safe_edit(
            status_message,
            "❌ **Archive is empty. No files found.**"
        )

        return

    # --------------------------------------------------------
    # GET USER MODE
    # --------------------------------------------------------

    upload_as_doc = await get_upload_mode(
        user_id
    )

    # --------------------------------------------------------
    # PROCESS ONE BY ONE
    # --------------------------------------------------------

    for index, file_path in enumerate(
        files,
        start=1
    ):

        # ----------------------------------------------------
        # CANCEL CHECK
        # ----------------------------------------------------

        current_task = asyncio.current_task()

        if current_task.cancelled():
            raise asyncio.CancelledError

        # ----------------------------------------------------
        # DOCUMENT MODE
        # ----------------------------------------------------

        if upload_as_doc:

            await send_document(
                client=client,
                chat_id=message.chat.id,
                file_path=file_path,
                status_message=status_message,
                file_number=index,
                total_files=total_files
            )

        # ----------------------------------------------------
        # VIDEO MODE
        # ----------------------------------------------------

        else:

            if is_video_file(file_path):

                await send_video(
                    client=client,
                    chat_id=message.chat.id,
                    user_id=user_id,
                    video_path=file_path,
                    status_message=status_message,
                    file_number=index,
                    total_files=total_files
                )

            else:

                # Non-video files remain documents
                await send_document(
                    client=client,
                    chat_id=message.chat.id,
                    file_path=file_path,
                    status_message=status_message,
                    file_number=index,
                    total_files=total_files
                )

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    await safe_edit(
        status_message,
        (
            f"✅ **All files completed!**\n\n"
            f"📦 Total files: `{total_files}`"
        )
    )


# ============================================================
# MAIN ARCHIVE HANDLER
# ============================================================

@Client.on_message(
    filters.private & filters.document
)
async def unzip_handler(client, message):

    await AddUser(
        client,
        message
    )

    # --------------------------------------------------------
    # FORCE SUBSCRIBE
    # --------------------------------------------------------

    if Config.UPDATES_CHANNEL:

        fsub = await handle_force_subscribe(
            client,
            message
        )

        if fsub == 400:
            return

    # --------------------------------------------------------
    # GET FILE NAME
    # --------------------------------------------------------

    file_name = message.document.file_name

    if not file_name:
        return

    # --------------------------------------------------------
    # CHECK ARCHIVE
    # --------------------------------------------------------

    if not is_supported_archive(
        file_name
    ):
        return

    # --------------------------------------------------------
    # USER ID
    # --------------------------------------------------------

    user_id = message.from_user.id

    # --------------------------------------------------------
    # PREVENT MULTIPLE TASKS
    # --------------------------------------------------------

    if user_id in active_tasks:

        await message.reply_text(
            "⚠️ **আপনার একটি archive ইতিমধ্যে process হচ্ছে।**\n\n"
            "আগের কাজ শেষ হওয়ার পর আবার পাঠান।"
        )

        return

    # --------------------------------------------------------
    # STATUS MESSAGE
    # --------------------------------------------------------

    status_message = await message.reply_text(
        (
            f"📦 **Archive received**\n\n"
            f"📁 `{file_name}`\n\n"
            f"📥 **Downloading... ⏳**"
        ),
        reply_markup=CANCEL_BUTTON
    )

    archive_path = None
    extract_dir = None

    current_task = asyncio.current_task()

    active_tasks[user_id] = current_task

    try:

        # ====================================================
        # DOWNLOAD ARCHIVE
        # ====================================================

        download_dir = os.path.join(
            Config.DOWNLOAD_LOCATION,
            "archives"
        )

        os.makedirs(
            download_dir,
            exist_ok=True
        )

        # ----------------------------------------------------
        # Keep original archive filename
        # ----------------------------------------------------

        archive_path = os.path.join(
            download_dir,
            file_name
        )

        download_time = time.time()

        await client.download_media(

            message=message,

            file_name=archive_path,

            progress=progress_for_pyrogram,

            progress_args=(
                "📥 **Archive downloading... ⏳**",
                status_message,
                download_time
            )
        )

        # ====================================================
        # EXTRACTION DIRECTORY
        # ====================================================

        extract_dir = tempfile.mkdtemp(
            prefix=f"unzip_{user_id}_"
        )

        await safe_edit(
            status_message,
            (
                f"📦 **Archive downloaded**\n\n"
                f"📁 `{file_name}`\n\n"
                f"📂 **Extracting... ⏳**"
            )
        )

        # ====================================================
        # EXTRACT
        # ====================================================

        def extract_archive():

            Archive(
                archive_path
            ).extractall(
                extract_dir
            )

        # pyunpack is synchronous,
        # so run extraction in a worker thread.
        await asyncio.to_thread(
            extract_archive
        )

        # ====================================================
        # SEND EXTRACTED FILES
        # ====================================================

        await process_extracted_files(
            client=client,
            message=message,
            user_id=user_id,
            extract_dir=extract_dir,
            status_message=status_message
        )

    except asyncio.CancelledError:

        logger.info(
            f"Archive task cancelled for user {user_id}"
        )

        try:
            await safe_edit(
                status_message,
                "❌ **Archive processing cancelled.**"
            )
        except Exception:
            pass

    except Exception as e:

        logger.exception(
            "Archive processing failed"
        )

        try:
            await safe_edit(
                status_message,
                (
                    "❌ **Failed to process archive.**\n\n"
                    f"**Error:** `{str(e)[:1000]}`"
                )
            )

        except Exception:
            pass

    finally:

        # ====================================================
        # REMOVE ACTIVE TASK
        # ====================================================

        active_tasks.pop(
            user_id,
            None
        )

        # ====================================================
        # DELETE ORIGINAL ARCHIVE
        # ====================================================

        try:

            if (
                archive_path
                and os.path.exists(archive_path)
            ):
                os.remove(archive_path)

        except Exception as e:

            logger.warning(
                f"Could not remove archive: {e}"
            )

        # ====================================================
        # DELETE EXTRACTION DIRECTORY
        # ====================================================

        try:

            if (
                extract_dir
                and os.path.exists(extract_dir)
            ):
                shutil.rmtree(
                    extract_dir,
                    ignore_errors=True
                )

        except Exception as e:

            logger.warning(
                f"Could not remove extraction directory: {e}"
            )
