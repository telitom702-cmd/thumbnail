# ©️ LISA-KOREA | @LISA_FAN_LK | NT_BOT_CHANNEL

import os
import time
import shutil
import tempfile
import asyncio
import logging
import random
import re

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
# HELPER: REMOVE EXTENSION
# ============================================================

def remove_extension(filename):
    if not filename:
        return ""

    lower_name = filename.lower()

    for ext in sorted(
        VIDEO_EXTENSIONS,
        key=len,
        reverse=True
    ):
        if lower_name.endswith(ext):
            return filename[:-len(ext)]

    return os.path.splitext(filename)[0]


# ============================================================
# HELPER: SAFE FILENAME
# ============================================================

def safe_filename(filename):
    """
    Makes a Telegram-safe filename.

    Does NOT intentionally change normal characters.
    Only removes characters that cannot safely be used
    as a filesystem filename.
    """

    if not filename:
        return ""

    filename = str(filename)

    # Remove null bytes
    filename = filename.replace("\x00", "")

    # Replace path separators
    filename = filename.replace("/", "_")
    filename = filename.replace("\\", "_")

    # Remove control characters
    filename = re.sub(
        r"[\x00-\x1f\x7f]",
        "",
        filename
    )

    # Remove characters that are problematic on filesystems
    filename = re.sub(
        r'[:*?"<>|]',
        "_",
        filename
    )

    # Collapse whitespace
    filename = re.sub(
        r"\s+",
        " ",
        filename
    ).strip()

    # Avoid empty filename
    if filename in (".", ".."):
        return ""

    return filename


# ============================================================
# HELPER: CAPTION -> FILENAME
# ============================================================

def filename_from_caption(
    caption,
    original_filename=None
):
    """
    If filename does not exist but caption exists,
    create filename from caption.

    Keeps original extension if available.
    """

    if not caption:
        return ""

    caption = str(caption).strip()

    if not caption:
        return ""

    # --------------------------------------------------------
    # Use first non-empty caption line
    # --------------------------------------------------------

    lines = [
        line.strip()
        for line in caption.splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    name = lines[0]

    # --------------------------------------------------------
    # Remove characters that cannot be in filename
    # --------------------------------------------------------

    name = safe_filename(name)

    if not name:
        return ""

    # --------------------------------------------------------
    # Get extension from original filename
    # --------------------------------------------------------

    extension = ""

    if original_filename:
        original_filename = str(
            original_filename
        ).strip()

        original_ext = os.path.splitext(
            original_filename
        )[1]

        if original_ext:
            extension = original_ext

    # --------------------------------------------------------
    # If caption already ends with an extension,
    # don't add another one.
    # --------------------------------------------------------

    if extension:

        if not name.lower().endswith(
            extension.lower()
        ):
            name += extension

    return name


# ============================================================
# HELPER: FILENAME -> CAPTION
# ============================================================

def caption_from_filename(filename):
    """
    Creates caption from filename.

    Extension is removed.
    """

    if not filename:
        return None

    filename = str(filename).strip()

    if not filename:
        return None

    caption = remove_extension(
        filename
    )

    caption = caption.strip()

    if not caption:
        return None

    return caption


# ============================================================
# HELPER: PREPARE FILENAME + CAPTION
# ============================================================

def prepare_filename_and_caption(
    filename,
    caption
):
    """
    Rules:

    1. Filename + Caption
       -> keep both unchanged

    2. Filename + no Caption
       -> filename stays
       -> caption from filename

    3. No Filename + Caption
       -> filename from caption
       -> caption stays

    4. No Filename + no Caption
       -> both remain None
       -> no artificial text is created
    """

    filename = (
        str(filename).strip()
        if filename
        else ""
    )

    caption = (
        str(caption).strip()
        if caption
        else ""
    )

    # --------------------------------------------------------
    # CASE 1
    # Filename + Caption
    # --------------------------------------------------------

    if filename and caption:

        return (
            filename,
            caption
        )

    # --------------------------------------------------------
    # CASE 2
    # Filename only
    # --------------------------------------------------------

    if filename and not caption:

        generated_caption = (
            caption_from_filename(
                filename
            )
        )

        return (
            filename,
            generated_caption
        )

    # --------------------------------------------------------
    # CASE 3
    # Caption only
    # --------------------------------------------------------

    if not filename and caption:

        generated_filename = (
            filename_from_caption(
                caption
            )
        )

        return (
            generated_filename,
            caption
        )

    # --------------------------------------------------------
    # CASE 4
    # Nothing
    # --------------------------------------------------------

    return (
        None,
        None
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
            return (
                width,
                height,
                duration
            )

        metadata = extractMetadata(
            parser
        )

        if metadata:

            if metadata.has("duration"):

                duration_value = (
                    metadata.get("duration")
                )

                if duration_value:
                    duration = int(
                        duration_value.total_seconds()
                    )

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
            f"Could not read video metadata: {e}"
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
        # Local thumbnail
        # ----------------------------------------------------

        if os.path.exists(
            thumb_path
        ):

            try:

                with Image.open(
                    thumb_path
                ) as img:

                    img.verify()

                return thumb_path

            except Exception:

                try:
                    os.remove(
                        thumb_path
                    )
                except Exception:
                    pass

        # ----------------------------------------------------
        # Database thumbnail
        # ----------------------------------------------------

        thumbnail = await db.get_thumbnail(
            user_id
        )

        if not thumbnail:
            return None

        # ----------------------------------------------------
        # Download thumbnail
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

                return (
                    screenshot,
                    True
                )

        except Exception as e:

            logger.warning(
                f"Could not generate screenshot: {e}"
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
            f"Could not read upload mode: {e}"
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
    filename=None,
    caption=None
):

    # --------------------------------------------------------
    # Get filename
    # --------------------------------------------------------

    if filename is None:

        filename = os.path.basename(
            file_path
        )

    # --------------------------------------------------------
    # Prepare filename + caption
    # --------------------------------------------------------

    final_filename, final_caption = (
        prepare_filename_and_caption(
            filename,
            caption
        )
    )

    # --------------------------------------------------------
    # Status text
    # --------------------------------------------------------

    display_filename = (
        final_filename
        or os.path.basename(
            file_path
        )
        or "File"
    )

    text = (
        f"📦 **File {file_number}/{total_files}**\n\n"
        f"📄 `{display_filename}`\n\n"
        f"📤 **Uploading...**"
    )

    await safe_edit(
        status_message,
        text
    )

    upload_time = time.time()

    # --------------------------------------------------------
    # Build upload arguments
    # --------------------------------------------------------

    upload_args = {
        "chat_id": chat_id,
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
    # Filename
    #
    # Only send file_name if we actually have one.
    # --------------------------------------------------------

    if final_filename:

        upload_args[
            "file_name"
        ] = final_filename

    # --------------------------------------------------------
    # Caption
    #
    # Only send caption if we actually have one.
    # --------------------------------------------------------

    if final_caption:

        upload_args[
            "caption"
        ] = final_caption

    # --------------------------------------------------------
    # SEND DOCUMENT
    # --------------------------------------------------------

    await client.send_document(
        **upload_args
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
    filename=None,
    caption=None
):

    # --------------------------------------------------------
    # Get original filename
    # --------------------------------------------------------

    if filename is None:

        filename = os.path.basename(
            video_path
        )

    # --------------------------------------------------------
    # Prepare filename + caption
    # --------------------------------------------------------

    final_filename, final_caption = (
        prepare_filename_and_caption(
            filename,
            caption
        )
    )

    # --------------------------------------------------------
    # Display filename only for status
    #
    # This does NOT create a Telegram filename.
    # --------------------------------------------------------

    display_filename = (
        final_filename
        or os.path.basename(
            video_path
        )
        or "Video"
    )

    await safe_edit(
        status_message,
        (
            f"🎬 `{display_filename}`\n\n"
            f"📦 **File {file_number}/{total_files}**\n"
            f"📤 **Preparing video...**"
        )
    )

    # --------------------------------------------------------
    # Metadata
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
        # Thumbnail
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
        # Upload status
        # ----------------------------------------------------

        await safe_edit(
            status_message,
            (
                f"🎬 `{display_filename}`\n\n"
                f"📦 **File {file_number}/{total_files}**\n"
                f"⏱️ Duration: `{duration}s`\n"
                f"📐 Resolution: `{width}x{height}`\n\n"
                f"📤 **Uploading... 🚀**"
            )
        )

        upload_time = time.time()

        # ----------------------------------------------------
        # Build upload arguments
        # ----------------------------------------------------

        upload_args = {

            "chat_id": chat_id,

            "video": video_path,

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
        # Filename
        #
        # Only add if filename exists.
        # ----------------------------------------------------

        if final_filename:

            upload_args[
                "file_name"
            ] = final_filename

        # ----------------------------------------------------
        # Caption
        #
        # Only add if caption exists.
        # ----------------------------------------------------

        if final_caption:

            upload_args[
                "caption"
            ] = final_caption

        # ----------------------------------------------------
        # SEND VIDEO
        # ----------------------------------------------------

        await client.send_video(
            **upload_args
        )

    finally:

        # ----------------------------------------------------
        # Delete generated screenshot
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
    # FIND FILES
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

    # ========================================================
    # IMPORTANT:
    #
    # Archive message caption is used as the source caption.
    #
    # If it does not exist, filename -> caption.
    # ========================================================

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

        # ----------------------------------------------------
        # Extracted filename
        # ----------------------------------------------------

        extracted_filename = (
            os.path.basename(
                file_path
            )
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Each extracted file has its own filename.
        #
        # Archive caption is used only as caption source.
        #
        # If caption exists:
        #   filename stays unchanged.
        #
        # If caption does not exist:
        #   filename -> caption.
        # ----------------------------------------------------

        file_caption = archive_caption

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

                filename=extracted_filename,

                caption=file_caption
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

                    filename=extracted_filename,

                    caption=file_caption
                )

            else:

                # ------------------------------------------------
                # Non-video files remain documents
                # ------------------------------------------------

                await send_document(

                    client=client,

                    chat_id=message.chat.id,

                    file_path=file_path,

                    status_message=status_message,

                    file_number=index,

                    total_files=total_files,

                    filename=extracted_filename,

                    caption=file_caption
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
async def unzip_handler(
    client,
    message
):

    await AddUser(
        client,
        message
    )

    # --------------------------------------------------------
    # FORCE SUBSCRIBE
    # --------------------------------------------------------

    if Config.UPDATES_CHANNEL:

        fsub = (
            await handle_force_subscribe(
                client,
                message
            )
        )

        if fsub == 400:
            return

    # --------------------------------------------------------
    # GET FILE NAME
    # --------------------------------------------------------

    file_name = (
        message.document.file_name
    )

    # --------------------------------------------------------
    # Archive itself must have an extension
    # for the archive detector.
    #
    # If Telegram has no filename, we cannot know
    # whether it is ZIP/RAR/7Z/etc reliably.
    # --------------------------------------------------------

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

    user_id = (
        message.from_user.id
    )

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

        extract_dir = (
            tempfile.mkdtemp(
                prefix=f"unzip_{user_id}_"
            )
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
                and os.path.exists(
                    archive_path
                )
            ):

                os.remove(
                    archive_path
                )

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
                and os.path.exists(
                    extract_dir
                )
            ):

                shutil.rmtree(
                    extract_dir,
                    ignore_errors=True
                )

        except Exception as e:

            logger.warning(
                f"Could not remove extraction directory: {e}"
            )
