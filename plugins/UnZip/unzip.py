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

    except Exception as e:
        logger.warning(
            f"[UNZIP] Status edit failed: {e}"
        )


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

    return filename.lower().endswith(
        VIDEO_EXTENSIONS
    )


# ============================================================
# HELPER: METADATA
# ============================================================

def get_video_metadata(file_path):

    width = 0
    height = 0
    duration = 0

    try:

        parser = createParser(
            file_path
        )

        if not parser:
            return width, height, duration

        metadata = extractMetadata(
            parser
        )

        if metadata:

            if metadata.has("duration"):

                duration_value = (
                    metadata.get("duration")
                )

                if duration_value:

                    try:
                        duration = int(
                            duration_value.total_seconds()
                        )
                    except Exception:
                        try:
                            duration = int(
                                duration_value.seconds
                            )
                        except Exception:
                            duration = 0

            if metadata.has("width"):
                width = (
                    metadata.get("width")
                    or 0
                )

            if metadata.has("height"):
                height = (
                    metadata.get("height")
                    or 0
                )

    except Exception as e:

        logger.warning(
            f"[UNZIP] Could not read video metadata: {e}"
        )

    return (
        width,
        height,
        duration
    )


# ============================================================
# HELPER: GET CUSTOM THUMBNAIL
# ============================================================

async def get_custom_thumbnail(
    client,
    user_id
):

    try:

        download_dir = (
            Config.DOWNLOAD_LOCATION
        )

        os.makedirs(
            download_dir,
            exist_ok=True
        )

        thumb_path = os.path.join(
            download_dir,
            f"{user_id}.jpg"
        )

        # ----------------------------------------------------
        # LOCAL THUMBNAIL
        # ----------------------------------------------------

        if os.path.exists(
            thumb_path
        ):

            try:

                with Image.open(
                    thumb_path
                ) as img:

                    img.verify()

                logger.info(
                    f"[UNZIP] Custom thumbnail found: {thumb_path}"
                )

                return thumb_path

            except Exception:

                try:
                    os.remove(
                        thumb_path
                    )
                except Exception:
                    pass

        # ----------------------------------------------------
        # DATABASE THUMBNAIL
        # ----------------------------------------------------

        thumbnail = await db.get_thumbnail(
            user_id
        )

        if not thumbnail:

            logger.info(
                f"[UNZIP] No custom thumbnail for user {user_id}"
            )

            return None

        # ----------------------------------------------------
        # DOWNLOAD TELEGRAM THUMBNAIL
        # ----------------------------------------------------

        try:

            downloaded = (
                await client.download_media(
                    message=thumbnail,
                    file_name=thumb_path
                )
            )

            if (
                downloaded
                and os.path.exists(
                    thumb_path
                )
            ):

                logger.info(
                    f"[UNZIP] Custom thumbnail downloaded"
                )

                return thumb_path

        except Exception as e:

            logger.warning(
                f"[UNZIP] Could not download custom thumbnail: {e}"
            )

    except Exception as e:

        logger.warning(
            f"[UNZIP] Custom thumbnail error: {e}"
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

    # --------------------------------------------------------
    # CUSTOM THUMBNAIL
    # --------------------------------------------------------

    custom_thumb = (
        await get_custom_thumbnail(
            client,
            user_id
        )
    )

    if (
        custom_thumb
        and os.path.exists(
            custom_thumb
        )
    ):

        return (
            custom_thumb,
            False
        )

    # --------------------------------------------------------
    # SCREENSHOT FALLBACK
    # --------------------------------------------------------

    if duration > 1:

        try:

            screenshot = (
                await take_screen_shot(
                    video_path,
                    os.path.dirname(
                        video_path
                    ),
                    random.randint(
                        0,
                        max(
                            1,
                            duration - 1
                        )
                    )
                )
            )

            if (
                screenshot
                and os.path.exists(
                    screenshot
                )
            ):

                logger.info(
                    f"[UNZIP] Screenshot thumbnail generated"
                )

                return (
                    screenshot,
                    True
                )

        except Exception as e:

            logger.warning(
                f"[UNZIP] Could not generate screenshot: {e}"
            )

    return (
        None,
        False
    )


# ============================================================
# HELPER: GET UPLOAD MODE
# ============================================================

async def get_upload_mode(user_id):

    """
    False = VIDEO
    True  = DOCUMENT
    """

    try:

        return await db.get_upload_as_doc(
            user_id
        )

    except Exception as e:

        logger.warning(
            f"[UNZIP] Could not read upload mode: {e}"
        )

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
async def cancel_unzip(
    client,
    callback_query
):

    user_id = (
        callback_query.from_user.id
    )

    task = active_tasks.get(
        user_id
    )

    if task:

        logger.info(
            f"[UNZIP] Cancel requested by user {user_id}"
        )

        try:
            task.cancel()
        except Exception:
            pass

        active_tasks.pop(
            user_id,
            None
        )

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
    total_files,
    caption=None
):

    # --------------------------------------------------------
    # ORIGINAL FILENAME
    # --------------------------------------------------------

    filename = os.path.basename(
        file_path
    )

    # --------------------------------------------------------
    # STATUS ONLY
    #
    # File number NEVER goes into filename.
    # --------------------------------------------------------

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

    upload_args = {
        "chat_id": chat_id,

        # Original extracted file
        "document": file_path,

        "progress": progress_for_pyrogram,

        "progress_args": (
            f"📤 **Uploading File "
            f"{file_number}/{total_files}...**",
            status_message,
            upload_time
        )
    }

    # --------------------------------------------------------
    # CAPTION
    #
    # No caption = no caption.
    # Filename is NOT converted into caption.
    # --------------------------------------------------------

    if caption:

        upload_args[
            "caption"
        ] = caption

    await client.send_document(
        **upload_args
    )

    logger.info(
        f"[UNZIP] Document uploaded "
        f"{file_number}/{total_files}: {filename}"
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
    total_files,
    caption=None
):

    # --------------------------------------------------------
    # ORIGINAL FILENAME
    # --------------------------------------------------------

    original_filename = os.path.basename(
        video_path
    )

    # --------------------------------------------------------
    # STATUS ONLY
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

    width, height, duration = (
        get_video_metadata(
            video_path
        )
    )

    thumbnail_path = None
    generated_thumbnail = False

    try:

        # ----------------------------------------------------
        # THUMBNAIL
        # ----------------------------------------------------

        (
            thumbnail_path,
            generated_thumbnail
        ) = await prepare_thumbnail(
            client,
            user_id,
            video_path,
            duration
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

        upload_args = {

            "chat_id": chat_id,

            "video": video_path,

            # Original filename
            "file_name": original_filename,

            "duration": duration,

            "width": width,

            "height": height,

            "thumb": thumbnail_path,

            "supports_streaming": True,

            "progress": progress_for_pyrogram,

            "progress_args": (
                f"📤 **Uploading File "
                f"{file_number}/{total_files}...**",
                status_message,
                upload_time
            )
        }

        # ----------------------------------------------------
        # CAPTION
        #
        # Only send caption if archive had one.
        # ----------------------------------------------------

        if caption:

            upload_args[
                "caption"
            ] = caption

        await client.send_video(
            **upload_args
        )

        logger.info(
            f"[UNZIP] Video uploaded "
            f"{file_number}/{total_files}: "
            f"{original_filename}"
        )

    finally:

        # ----------------------------------------------------
        # DELETE GENERATED SCREENSHOT ONLY
        # ----------------------------------------------------

        if generated_thumbnail:

            try:

                if (
                    thumbnail_path
                    and os.path.exists(
                        thumbnail_path
                    )
                ):

                    os.remove(
                        thumbnail_path
                    )

            except Exception as e:

                logger.warning(
                    f"[UNZIP] Could not remove generated thumbnail: {e}"
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

    logger.info(
        f"[UNZIP] Searching extracted files: {extract_dir}"
    )

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

            if os.path.isfile(
                full_path
            ):

                files.append(
                    full_path
                )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    files.sort(
        key=lambda x: x.lower()
    )

    total_files = len(files)

    logger.info(
        f"[UNZIP] Extracted files found: {total_files}"
    )

    if total_files == 0:

        await safe_edit(
            status_message,
            "❌ **Archive is empty. No files found.**"
        )

        return

    # --------------------------------------------------------
    # GET USER MODE
    # --------------------------------------------------------

    upload_as_doc = (
        await get_upload_mode(
            user_id
        )
    )

    if upload_as_doc:

        logger.info(
            f"[UNZIP] Upload mode: DOCUMENT"
        )

    else:

        logger.info(
            f"[UNZIP] Upload mode: VIDEO"
        )

    # --------------------------------------------------------
    # ARCHIVE CAPTION
    #
    # If archive itself has caption,
    # it is passed to extracted files.
    #
    # If archive has no caption:
    # extracted files have NO caption.
    # --------------------------------------------------------

    archive_caption = (
        message.caption
        if message.caption
        else None
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

        current_task = (
            asyncio.current_task()
        )

        if current_task.cancelled():

            raise asyncio.CancelledError

        extracted_filename = (
            os.path.basename(
                file_path
            )
        )

        logger.info(
            f"[UNZIP] Processing "
            f"{index}/{total_files}: "
            f"{extracted_filename}"
        )

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

                total_files=total_files,

                caption=archive_caption
            )

        # ----------------------------------------------------
        # VIDEO MODE
        # ----------------------------------------------------

        else:

            if is_video_file(
                file_path
            ):

                await send_video(

                    client=client,

                    chat_id=message.chat.id,

                    user_id=user_id,

                    video_path=file_path,

                    status_message=status_message,

                    file_number=index,

                    total_files=total_files,

                    caption=archive_caption
                )

            else:

                await send_document(

                    client=client,

                    chat_id=message.chat.id,

                    file_path=file_path,

                    status_message=status_message,

                    file_number=index,

                    total_files=total_files,

                    caption=archive_caption
                )

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    logger.info(
        f"[UNZIP] All files completed: {total_files}"
    )

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
async def unzip_handler(
    client,
    message
):

    user_id = (
        message.from_user.id
    )

    logger.info(
        f"[UNZIP] Handler triggered | user={user_id}"
    )

    # --------------------------------------------------------
    # ADD USER
    # --------------------------------------------------------

    await AddUser(
        client,
        message
    )

    # --------------------------------------------------------
    # FORCE SUBSCRIBE
    # --------------------------------------------------------

    if Config.UPDATES_CHANNEL:

        logger.info(
            f"[UNZIP] Checking force subscribe | user={user_id}"
        )

        fsub = (
            await handle_force_subscribe(
                client,
                message
            )
        )

        if fsub == 400:

            logger.info(
                f"[UNZIP] Force subscribe blocked user={user_id}"
            )

            return

    # --------------------------------------------------------
    # FILE NAME
    # --------------------------------------------------------

    file_name = (
        message.document.file_name
    )

    logger.info(
        f"[UNZIP] Filename received: {file_name}"
    )

    if not file_name:

        logger.info(
            "[UNZIP] No filename. Ignoring message."
        )

        return

    # --------------------------------------------------------
    # ARCHIVE CHECK
    # --------------------------------------------------------

    if not is_supported_archive(
        file_name
    ):

        logger.info(
            f"[UNZIP] Not a supported archive: {file_name}"
        )

        return

    logger.info(
        f"[UNZIP] Supported archive detected: {file_name}"
    )

    # --------------------------------------------------------
    # PREVENT MULTIPLE TASKS
    # --------------------------------------------------------

    if user_id in active_tasks:

        logger.info(
            f"[UNZIP] User already has active task: {user_id}"
        )

        await message.reply_text(
            "⚠️ **আপনার একটি archive ইতিমধ্যে process হচ্ছে।**\n\n"
            "আগের কাজ শেষ হওয়ার পর আবার পাঠান।"
        )

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    status_message = (
        await message.reply_text(

            (
                f"📦 **Archive received**\n\n"
                f"📁 `{file_name}`\n\n"
                f"📥 **Downloading... ⏳**"
            ),

            reply_markup=CANCEL_BUTTON
        )
    )

    archive_path = None
    extract_dir = None

    current_task = (
        asyncio.current_task()
    )

    active_tasks[
        user_id
    ] = current_task

    try:

        # ====================================================
        # DOWNLOAD
        # ====================================================

        download_dir = os.path.join(
            Config.DOWNLOAD_LOCATION,
            "archives"
        )

        os.makedirs(
            download_dir,
            exist_ok=True
        )

        archive_path = os.path.join(
            download_dir,
            file_name
        )

        logger.info(
            f"[UNZIP] Download started: {archive_path}"
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

        logger.info(
            f"[UNZIP] Download completed: {archive_path}"
        )

        # ====================================================
        # CHECK DOWNLOADED FILE
        # ====================================================

        if not os.path.exists(
            archive_path
        ):

            raise FileNotFoundError(
                "Downloaded archive file was not found."
            )

        archive_size = os.path.getsize(
            archive_path
        )

        logger.info(
            f"[UNZIP] Archive size: {archive_size} bytes"
        )

        # ====================================================
        # EXTRACTION DIRECTORY
        # ====================================================

        extract_dir = (
            tempfile.mkdtemp(
                prefix=f"unzip_{user_id}_"
            )
        )

        logger.info(
            f"[UNZIP] Extraction directory: {extract_dir}"
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

        logger.info(
            f"[UNZIP] Extraction started: {file_name}"
        )

        def extract_archive():

            Archive(
                archive_path
            ).extractall(
                extract_dir
            )

        await asyncio.to_thread(
            extract_archive
        )

        logger.info(
            f"[UNZIP] Extraction completed: {extract_dir}"
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
            f"[UNZIP] Archive task cancelled | user={user_id}"
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
            f"[UNZIP] Archive processing failed | user={user_id}"
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
                and os.path.exists(
                    archive_path
                )
            ):

                os.remove(
                    archive_path
                )

                logger.info(
                    f"[UNZIP] Archive deleted: {archive_path}"
                )

        except Exception as e:

            logger.warning(
                f"[UNZIP] Could not remove archive: {e}"
            )

        # ====================================================
        # DELETE EXTRACTION DIRECTORY
        # ====================================================

        try:

            if (
                extract_dir
                and os.path.exists(
                    extract_dir
                )
            ):

                shutil.rmtree(
                    extract_dir,
                    ignore_errors=True
                )

                logger.info(
                    f"[UNZIP] Extraction directory cleaned"
                )

        except Exception as e:

            logger.warning(
                f"[UNZIP] Could not remove extraction directory: {e}"
            )

        logger.info(
            f"[UNZIP] Task finished | user={user_id}"
        )
