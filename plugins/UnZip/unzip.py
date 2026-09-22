import os
import re
import shutil
import asyncio
import logging
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyunpack import Archive

from plugins.config import Config
from plugins.database.database import db
from plugins.database.add import AddUser
from plugins.functions.forcesub import handle_force_subscribe
from plugins.functions.display_progress import progress_for_pyrogram
from plugins.functions.help_Nekmo_ffmpeg import take_screen_shot


logger = logging.getLogger(__name__)


# ============================================================
# CONFIG
# ============================================================

VIDEO_EXTENSIONS = {
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
}

NORMAL_ARCHIVE_EXTENSIONS = (
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

# Example:
# Movie.zip.001
# Movie.zip.002
# Movie.zip.003
#
# Your example:
# As.Beautiful.As.You....my.zip.zip.003
#
# The important part is the final .001/.002/.003...
MULTIPART_RE = re.compile(
    r"^(?P<base>.+?)(?:\.)(?P<part>\d{3,})$",
    re.IGNORECASE,
)


# Active extraction tasks
active_tasks = {}

# Multipart archive collections
#
# {
#   archive_key: {
#       "base_name": "...zip.zip",
#       "parts": {
#           1: "/path/file.001",
#           2: "/path/file.002",
#           3: "/path/file.003"
#       },
#       "captions": [...],
#       "user_id": 123,
#       "last_message": message,
#   }
# }
multipart_tasks = {}

multipart_locks = {}


# ============================================================
# SAFE EDIT
# ============================================================

async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.debug("safe_edit failed: %s", e)


# ============================================================
# FILENAME HELPERS
# ============================================================

def get_filename(message):
    """
    Telegram document filename.
    """
    try:
        return message.document.file_name or ""
    except Exception:
        return ""


def safe_filename(name):
    if not name:
        return "file"

    name = os.path.basename(name)

    # Remove dangerous characters
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

    return name.strip() or "file"


def get_multipart_info(filename):
    """
    Detect:
        movie.zip.001
        movie.zip.002
        movie.zip.003

    Returns:
        (base_name, part_number)

    Otherwise:
        (None, None)
    """

    if not filename:
        return None, None

    match = MULTIPART_RE.match(filename)

    if not match:
        return None, None

    base = match.group("base")
    part = int(match.group("part"))

    # Only treat it as multipart if the base looks like an archive.
    lower_base = base.lower()

    archive_like = (
        lower_base.endswith(".zip")
        or lower_base.endswith(".rar")
        or lower_base.endswith(".7z")
        or lower_base.endswith(".tar")
    )

    if not archive_like:
        return None, None

    return base, part


def is_multipart_archive(filename):
    base, part = get_multipart_info(filename)
    return base is not None and part is not None


def is_supported_archive(filename):
    if not filename:
        return False

    lower = filename.lower()

    # Multipart archive
    if is_multipart_archive(filename):
        return True

    return lower.endswith(NORMAL_ARCHIVE_EXTENSIONS)


def is_video_file(path):
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


# ============================================================
# METADATA
# ============================================================

def get_video_metadata(file_path):
    """
    Return:
        duration, width, height

    If metadata cannot be read, return safe defaults.
    """

    try:
        from hachoir.parser import createParser
        from hachoir.metadata import extractMetadata

        parser = createParser(file_path)

        if not parser:
            return 0, 0, 0

        metadata = extractMetadata(parser)

        if not metadata:
            return 0, 0, 0

        duration = 0
        width = 0
        height = 0

        try:
            duration_value = metadata.get("duration")

            if duration_value:
                try:
                    duration = int(duration_value.total_seconds())
                except Exception:
                    try:
                        duration = int(duration_value.seconds)
                    except Exception:
                        duration = 0
        except Exception:
            pass

        try:
            width = int(metadata.get("width") or 0)
        except Exception:
            width = 0

        try:
            height = int(metadata.get("height") or 0)
        except Exception:
            height = 0

        return duration, width, height

    except Exception as e:
        logger.warning("Metadata error: %s", e)
        return 0, 0, 0


# ============================================================
# THUMBNAIL
# ============================================================

async def get_custom_thumbnail(client, user_id):
    try:
        thumb_id = db.get_thumbnail(user_id)

        if not thumb_id:
            return None

        thumb_dir = os.path.join(
            Config.DOWNLOAD_LOCATION,
            "archives",
            "thumbs"
        )

        os.makedirs(thumb_dir, exist_ok=True)

        thumb_path = os.path.join(
            thumb_dir,
            f"{user_id}.jpg"
        )

        await client.download_media(
            thumb_id,
            file_name=thumb_path
        )

        if os.path.exists(thumb_path):
            return thumb_path

    except Exception as e:
        logger.warning(
            "Custom thumbnail error user=%s: %s",
            user_id,
            e
        )

    return None


async def prepare_thumbnail(client, user_id, video_path):
    """
    Custom thumbnail থাকলে সেটি ব্যবহার করবে।
    না থাকলে screenshot fallback করবে।
    """

    thumb = await get_custom_thumbnail(client, user_id)

    if thumb and os.path.exists(thumb):
        return thumb, True

    try:
        screenshot_dir = os.path.join(
            Config.DOWNLOAD_LOCATION,
            "archives",
            "screenshots"
        )

        os.makedirs(screenshot_dir, exist_ok=True)

        screenshot_path = os.path.join(
            screenshot_dir,
            f"{user_id}_{abs(hash(video_path))}.jpg"
        )

        result = await asyncio.to_thread(
            take_screen_shot,
            video_path,
            0,
            screenshot_path
        )

        if result and os.path.exists(screenshot_path):
            return screenshot_path, True

        if os.path.exists(screenshot_path):
            return screenshot_path, True

    except Exception as e:
        logger.warning(
            "Screenshot thumbnail error: %s",
            e
        )

    return None, False


# ============================================================
# UPLOAD MODE
# ============================================================

def get_upload_mode(user_id):
    """
    False = VIDEO
    True  = DOCUMENT
    """

    try:
        return bool(db.get_upload_as_doc(user_id))
    except Exception:
        return False


# ============================================================
# CANCEL
# ============================================================

def cancel_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ বাতিল করুন (Cancel)",
                    callback_data="cancel_unzip"
                )
            ]
        ]
    )


@Client.on_callback_query(
    filters.regex("^cancel_unzip$")
)
async def cancel_unzip(client, callback_query):

    user_id = callback_query.from_user.id

    task = active_tasks.get(user_id)

    if task:
        task["cancel"] = True

    # Also cancel multipart collection
    multipart = multipart_tasks.get(user_id)

    if multipart:
        multipart["cancel"] = True

    try:
        await callback_query.answer(
            "Cancel করা হচ্ছে..."
        )
    except Exception:
        pass

    try:
        await callback_query.message.edit_text(
            "❌ কাজটি বাতিল করা হচ্ছে..."
        )
    except Exception:
        pass


# ============================================================
# MULTIPART DIRECTORY
# ============================================================

def get_multipart_dir(user_id, archive_key):
    root = os.path.join(
        Config.DOWNLOAD_LOCATION,
        "archives",
        "multipart"
    )

    os.makedirs(root, exist_ok=True)

    safe_key = safe_filename(archive_key)

    # Prevent path becoming too long
    safe_key = safe_key[:180]

    path = os.path.join(
        root,
        f"{user_id}_{safe_key}"
    )

    os.makedirs(path, exist_ok=True)

    return path


# ============================================================
# MERGE MULTIPART FILES
# ============================================================

async def merge_multipart_parts(
    part_paths,
    merged_path,
    cancel_event=None
):
    """
    Binary concatenate:

        .001
        .002
        .003
        ...

    into one archive file.
    """

    temp_path = merged_path + ".tmp"

    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)

        with open(temp_path, "wb") as output:

            for number, part_path in part_paths:

                if cancel_event and cancel_event.get("cancel"):
                    raise asyncio.CancelledError()

                if not os.path.exists(part_path):
                    raise FileNotFoundError(
                        f"Missing part {number}"
                    )

                with open(part_path, "rb") as source:

                    while True:

                        if cancel_event and cancel_event.get("cancel"):
                            raise asyncio.CancelledError()

                        chunk = source.read(8 * 1024 * 1024)

                        if not chunk:
                            break

                        output.write(chunk)

        os.replace(temp_path, merged_path)

        return True

    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass

        raise


# ============================================================
# CHECK IF PARTS ARE COMPLETE
# ============================================================

def get_available_part_numbers(parts):
    return sorted(parts.keys())


def get_missing_parts(parts):
    """
    We consider the archive ready only when parts start from
    .001 and there are no gaps.

    Example:

        001,002,003 -> contiguous
        001,002,004 -> missing 003
        002,003      -> missing 001
    """

    numbers = get_available_part_numbers(parts)

    if not numbers:
        return []

    if numbers[0] != 1:
        return list(
            range(1, numbers[0])
        )

    last = numbers[-1]

    expected = set(range(1, last + 1))
    actual = set(numbers)

    return sorted(expected - actual)


def parts_are_contiguous(parts):
    numbers = get_available_part_numbers(parts)

    if not numbers:
        return False

    if numbers[0] != 1:
        return False

    return numbers == list(
        range(1, numbers[-1] + 1)
    )


# ============================================================
# TRY EXTRACT MULTIPART
# ============================================================

async def extract_multipart(
    user_id,
    archive_key,
    multipart_data,
    status_message
):

    parts = multipart_data["parts"]

    if not parts_are_contiguous(parts):
        missing = get_missing_parts(parts)

        if missing:
            missing_text = ", ".join(
                f"{n:03d}"
                for n in missing[:20]
            )

            if len(missing) > 20:
                missing_text += "..."

            await safe_edit(
                status_message,
                "📦 Multipart archive পাওয়া যাচ্ছে...\n\n"
                f"✅ পাওয়া গেছে: {len(parts)} part\n"
                f"⏳ Missing: {missing_text}\n\n"
                "আরও part পাঠান।"
            )

        return False

    numbers = get_available_part_numbers(parts)

    await safe_edit(
        status_message,
        "📦 Multipart archive প্রস্তুত হচ্ছে...\n\n"
        f"🔢 মোট part: {len(numbers)}\n"
        "⏳ সব part একত্র করা হচ্ছে..."
    )

    archive_dir = get_multipart_dir(
        user_id,
        archive_key
    )

    merged_name = safe_filename(archive_key)

    # Ensure extension
    if not merged_name.lower().endswith(".zip"):
        merged_name += ".zip"

    merged_path = os.path.join(
        archive_dir,
        merged_name
    )

    try:

        await merge_multipart_parts(
            [
                (number, parts[number])
                for number in numbers
            ],
            merged_path,
            multipart_data
        )

    except asyncio.CancelledError:
        raise

    except Exception as e:

        logger.exception(
            "Multipart merge failed: %s",
            e
        )

        await safe_edit(
            status_message,
            "❌ Multipart archive merge করতে সমস্যা হয়েছে।\n\n"
            f"Error: {e}"
        )

        return False

    await safe_edit(
        status_message,
        "📦 Multipart archive একত্র হয়েছে।\n"
        "⏳ এখন extract করা হচ্ছে..."
    )

    return await process_archive(
        user_id=user_id,
        archive_path=merged_path,
        status_message=status_message,
        original_caption=multipart_data.get(
            "caption"
        ),
        multipart_data=multipart_data
    )


# ============================================================
# PROCESS EXTRACTED FILES
# ============================================================

async def process_extracted_files(
    client,
    user_id,
    extract_dir,
    status_message,
    original_caption=None,
    task=None
):

    files = []

    for root, dirs, filenames in os.walk(
        extract_dir
    ):

        for filename in filenames:

            path = os.path.join(
                root,
                filename
            )

            if os.path.isfile(path):
                files.append(path)

    files.sort()

    if not files:
        await safe_edit(
            status_message,
            "❌ Archive-এর ভিতরে কোনো file পাওয়া যায়নি।"
        )

        return False

    upload_as_doc = get_upload_mode(user_id)

    total = len(files)

    await safe_edit(
        status_message,
        f"📂 মোট {total}টি file পাওয়া গেছে।\n"
        "⏳ Upload শুরু হচ্ছে..."
    )

    for index, file_path in enumerate(
        files,
        start=1
    ):

        if task and task.get("cancel"):
            return False

        filename = os.path.basename(file_path)

        await safe_edit(
            status_message,
            f"📦 File {index}/{total}\n"
            f"📄 {filename}\n\n"
            "⏳ Upload হচ্ছে...",
            reply_markup=cancel_keyboard()
        )

        try:

            # =================================================
            # DOCUMENT MODE
            # =================================================

            if upload_as_doc:

                caption = original_caption or None

                await client.send_document(
                    user_id,
                    document=file_path,
                    caption=caption,
                    file_name=filename,
                    progress=progress_for_pyrogram,
                    progress_args=(
                        status_message,
                        f"📤 Uploading {index}/{total}"
                    )
                )

            # =================================================
            # VIDEO MODE
            # =================================================

            else:

                if not is_video_file(file_path):

                    # Non-video -> document
                    caption = original_caption or None

                    await client.send_document(
                        user_id,
                        document=file_path,
                        caption=caption,
                        file_name=filename,
                        progress=progress_for_pyrogram,
                        progress_args=(
                            status_message,
                            f"📤 Uploading {index}/{total}"
                        )
                    )

                else:

                    duration, width, height = (
                        get_video_metadata(file_path)
                    )

                    thumb_path, thumb_created = (
                        await prepare_thumbnail(
                            client,
                            user_id,
                            file_path
                        )
                    )

                    try:

                        await client.send_video(
                            user_id,
                            video=file_path,
                            caption=original_caption or None,
                            thumb=thumb_path,
                            duration=duration,
                            width=width,
                            height=height,
                            supports_streaming=True,
                            file_name=filename,
                            progress=progress_for_pyrogram,
                            progress_args=(
                                status_message,
                                f"📤 Uploading {index}/{total}"
                            )
                        )

                    finally:

                        # Screenshot/custom downloaded thumb cleanup
                        if thumb_created and thumb_path:

                            try:
                                if os.path.exists(
                                    thumb_path
                                ):
                                    os.remove(
                                        thumb_path
                                    )
                            except Exception:
                                pass

        except asyncio.CancelledError:
            raise

        except Exception as e:

            logger.exception(
                "Upload failed: %s",
                file_path
            )

            await safe_edit(
                status_message,
                f"❌ Upload failed:\n"
                f"{filename}\n\n"
                f"Error: {e}"
            )

            continue

    await safe_edit(
        status_message,
        f"✅ কাজ সম্পন্ন হয়েছে!\n\n"
        f"📦 মোট file: {total}"
    )

    return True


# ============================================================
# PROCESS NORMAL ARCHIVE
# ============================================================

async def process_archive(
    user_id,
    archive_path,
    status_message,
    original_caption=None,
    multipart_data=None
):

    extract_dir = archive_path + "_extracted"

    try:

        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)

        os.makedirs(
            extract_dir,
            exist_ok=True
        )

        await safe_edit(
            status_message,
            "📦 Archive পাওয়া গেছে।\n"
            "⏳ Extract করা হচ্ছে...",
            reply_markup=cancel_keyboard()
        )

        task = active_tasks.get(user_id)

        def extract_archive():
            Archive(
                archive_path
            ).extractall(
                extract_dir
            )

        await asyncio.to_thread(
            extract_archive
        )

        if task and task.get("cancel"):
            return False

        await safe_edit(
            status_message,
            "✅ Extraction complete.\n"
            "⏳ Files প্রস্তুত করা হচ্ছে..."
        )

        result = await process_extracted_files(
            client=task["client"] if task else None,
            user_id=user_id,
            extract_dir=extract_dir,
            status_message=status_message,
            original_caption=original_caption,
            task=task
        )

        return result

    except asyncio.CancelledError:
        raise

    except Exception as e:

        logger.exception(
            "Archive extraction failed: %s",
            e
        )

        await safe_edit(
            status_message,
            "❌ Archive extract করা যায়নি।\n\n"
            f"Error: {e}"
        )

        return False

    finally:

        # Normal archive cleanup
        try:
            if os.path.exists(archive_path):
                os.remove(archive_path)
        except Exception:
            pass

        try:
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)
        except Exception:
            pass


# ============================================================
# NORMAL ARCHIVE HANDLER
# ============================================================

async def handle_normal_archive(
    client,
    message,
    filename,
    status_message
):

    user_id = message.from_user.id

    archive_root = os.path.join(
        Config.DOWNLOAD_LOCATION,
        "archives"
    )

    os.makedirs(
        archive_root,
        exist_ok=True
    )

    filename = safe_filename(filename)

    archive_path = os.path.join(
        archive_root,
        f"{user_id}_{filename}"
    )

    try:

        await safe_edit(
            status_message,
            "📥 Archive download হচ্ছে...",
            reply_markup=cancel_keyboard()
        )

        await client.download_media(
            message,
            file_name=archive_path,
            progress=progress_for_pyrogram,
            progress_args=(
                status_message,
                "📥 Downloading"
            )
        )

        task = active_tasks.get(user_id)

        if task and task.get("cancel"):
            return

        await process_archive(
            user_id=user_id,
            archive_path=archive_path,
            status_message=status_message,
            original_caption=(
                message.caption
                if message.caption
                else None
            )
        )

    except asyncio.CancelledError:

        await safe_edit(
            status_message,
            "❌ কাজ বাতিল করা হয়েছে।"
        )

    except Exception as e:

        logger.exception(
            "Normal archive handler error: %s",
            e
        )

        await safe_edit(
            status_message,
            f"❌ Error:\n{e}"
        )

    finally:

        try:
            if os.path.exists(archive_path):
                os.remove(archive_path)
        except Exception:
            pass


# ============================================================
# MULTIPART HANDLER
# ============================================================

async def handle_multipart_part(
    client,
    message,
    filename,
    base_name,
    part_number,
    status_message
):

    user_id = message.from_user.id

    # Unique key for one multipart archive
    archive_key = base_name.lower()

    lock = multipart_locks.setdefault(
        user_id,
        asyncio.Lock()
    )

    async with lock:

        data = multipart_tasks.get(user_id)

        # New multipart archive
        if (
            not data
            or data.get("archive_key") != archive_key
        ):

            data = {
                "archive_key": archive_key,
                "base_name": base_name,
                "parts": {},
                "caption": None,
                "client": client,
                "cancel": False,
            }

            multipart_tasks[user_id] = data

        # Keep caption if one exists.
        # Do NOT create caption from filename.
        if message.caption:
            data["caption"] = message.caption

        multipart_dir = get_multipart_dir(
            user_id,
            archive_key
        )

        part_filename = safe_filename(
            filename
        )

        part_path = os.path.join(
            multipart_dir,
            part_filename
        )

        # Duplicate part
        if part_number in data["parts"]:

            await safe_edit(
                status_message,
                f"⚠️ Part {part_number:03d} "
                "আগেই পাওয়া গেছে।\n\n"
                "Duplicate part ignore করা হয়েছে।"
            )

            return

        try:

            await safe_edit(
                status_message,
                f"📥 Part {part_number:03d} download হচ্ছে...",
                reply_markup=cancel_keyboard()
            )

            await client.download_media(
                message,
                file_name=part_path,
                progress=progress_for_pyrogram,
                progress_args=(
                    status_message,
                    f"📥 Part {part_number:03d}"
                )
            )

            if not os.path.exists(part_path):
                raise FileNotFoundError(
                    "Downloaded part not found"
                )

            data["parts"][part_number] = part_path

            numbers = get_available_part_numbers(
                data["parts"]
            )

            missing = get_missing_parts(
                data["parts"]
            )

            if not parts_are_contiguous(
                data["parts"]
            ):

                if missing:

                    missing_text = ", ".join(
                        f"{n:03d}"
                        for n in missing[:20]
                    )

                    if len(missing) > 20:
                        missing_text += "..."

                    await safe_edit(
                        status_message,
                        "📦 Multipart archive\n\n"
                        f"✅ পাওয়া গেছে: {len(numbers)} part\n"
                        f"📌 বর্তমান: {part_number:03d}\n"
                        f"⏳ Missing: {missing_text}\n\n"
                        "আরও part পাঠান।",
                        reply_markup=cancel_keyboard()
                    )

                return

            # ------------------------------------------------
            # We have 001 -> latest part without gaps.
            #
            # IMPORTANT:
            # We cannot always know whether 003 is the FINAL
            # part just because 001-003 exist.
            #
            # So try extraction.
            #
            # If archive is incomplete, extraction will fail
            # and user can send the next part.
            # ------------------------------------------------

            await safe_edit(
                status_message,
                "📦 সব পাওয়া part contiguous.\n"
                f"🔢 Part: {len(numbers)}\n"
                "⏳ Archive পরীক্ষা করা হচ্ছে..."
            )

            result = await extract_multipart(
                user_id=user_id,
                archive_key=archive_key,
                multipart_data=data,
                status_message=status_message
            )

            # Successful extraction/upload
            if result:

                # Cleanup multipart parts
                try:
                    shutil.rmtree(
                        multipart_dir,
                        ignore_errors=True
                    )
                except Exception:
                    pass

                multipart_tasks.pop(
                    user_id,
                    None
                )

                return

            # If not successful, keep parts.
            # User can send next part.
            #
            # We intentionally do NOT delete anything here.

        except asyncio.CancelledError:

            await safe_edit(
                status_message,
                "❌ Multipart কাজ বাতিল করা হয়েছে।"
            )

            # Cleanup collected parts
            try:
                shutil.rmtree(
                    multipart_dir,
                    ignore_errors=True
                )
            except Exception:
                pass

            multipart_tasks.pop(
                user_id,
                None
            )

        except Exception as e:

            logger.exception(
                "Multipart part error: %s",
                e
            )

            await safe_edit(
                status_message,
                f"❌ Part {part_number:03d} process error:\n\n"
                f"{e}"
            )


# ============================================================
# MAIN UNZIP HANDLER
# ============================================================

@Client.on_message(
    filters.private & filters.document
)
async def unzip_handler(client, message):

    user_id = message.from_user.id

    logger.info(
        "[UNZIP] Handler triggered | user=%s",
        user_id
    )

    try:

        logger.info(
            "[UNZIP] Checking force subscribe | user=%s",
            user_id
        )

        # ----------------------------------------------------
        # Add user
        # ----------------------------------------------------

        try:
            await AddUser(client, message)
        except Exception as e:
            logger.warning(
                "[UNZIP] AddUser failed: %s",
                e
            )

        # ----------------------------------------------------
        # Force subscribe
        # ----------------------------------------------------

        try:

            force_result = await handle_force_subscribe(
                client,
                message
            )

            if force_result:
                return

        except Exception as e:

            logger.warning(
                "[UNZIP] Force subscribe check error: %s",
                e
            )

        # ----------------------------------------------------
        # Filename
        # ----------------------------------------------------

        filename = get_filename(message)

        logger.info(
            "[UNZIP] Filename received: %s",
            filename
        )

        if not filename:

            logger.info(
                "[UNZIP] No filename found"
            )

            return

        # ----------------------------------------------------
        # Archive check
        # ----------------------------------------------------

        if not is_supported_archive(
            filename
        ):

            logger.info(
                "[UNZIP] Not a supported archive: %s",
                filename
            )

            return

        # ----------------------------------------------------
        # Prevent two processing tasks for same user
        # ----------------------------------------------------

        if user_id in active_tasks:

            await message.reply_text(
                "⚠️ আপনার আগের archive এখনও process হচ্ছে।\n"
                "আগের কাজ শেষ হওয়ার পর নতুন archive দিন।"
            )

            return

        # ----------------------------------------------------
        # Status message
        # ----------------------------------------------------

        status_message = await message.reply_text(
            "⏳ Archive চেক করা হচ্ছে...",
            reply_markup=cancel_keyboard()
        )

        # ----------------------------------------------------
        # Multipart
        # ----------------------------------------------------

        base_name, part_number = (
            get_multipart_info(filename)
        )

        if (
            base_name is not None
            and part_number is not None
        ):

            logger.info(
                "[UNZIP] Multipart archive detected | "
                "base=%s | part=%s",
                base_name,
                part_number
            )

            # Multipart collection itself remains active,
            # but extraction/upload is protected separately.
            #
            # Do not put it into active_tasks because the user
            # needs to send more parts.
            await handle_multipart_part(
                client=client,
                message=message,
                filename=filename,
                base_name=base_name,
                part_number=part_number,
                status_message=status_message
            )

            return

        # ----------------------------------------------------
        # Normal archive
        # ----------------------------------------------------

        task = {
            "cancel": False,
            "client": client,
            "message": message,
        }

        active_tasks[user_id] = task

        try:

            await handle_normal_archive(
                client=client,
                message=message,
                filename=filename,
                status_message=status_message
            )

        finally:

            active_tasks.pop(
                user_id,
                None
            )

    except Exception as e:

        logger.exception(
            "[UNZIP] Handler error user=%s: %s",
            user_id,
            e
        )

        try:
            await message.reply_text(
                f"❌ Error:\n{e}"
            )
        except Exception:
            pass

একটা গুরুত্বপূর্ণ বিষয়: উপরের logic ".001" থেকে contiguous parts পেলেই extract করার চেষ্টা করে। Split archive-এর ক্ষেত্রে 001–003 পেলেই যদি আসলে 004/005 লাগে, extraction ব্যর্থ হতে পারে। তখন parts রেখে দেবে এবং তুমি পরের part পাঠালে আবার চেষ্টা করবে।

তবে তোমার ".zip.zip.003" naming-এর জন্য আরও নির্ভরযোগ্য ব্যবস্থা করা যায়: ".001" আসার পর bot অপেক্ষা করবে, ".002", ".003"… আসবে, এবং শেষ part শনাক্ত/validation করে তারপর extract করবে। এটা করলে অসম্পূর্ণ archive নিয়ে বারবার extraction চালানোর দরকার হবে না।
