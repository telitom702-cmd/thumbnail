import os
import time
import shutil
import tempfile
import asyncio

from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified
from pyunpack import Archive

from plugins.config import Config
from plugins.UnZip.progress import progress_for_pyrogram

# ==========================================================
# DATABASE
# ==========================================================
# আপনার existing project-এর db
try:
    from database.users_chats_db import db
except Exception:
    db = None


# ==========================================================
# SUPPORTED ARCHIVE FORMATS
# ==========================================================

SUPPORTED_FORMATS = (
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".gz",
    ".bz2",
)


# ==========================================================
# SUPPORTED VIDEO FORMATS
# ==========================================================

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
)


# ==========================================================
# ACTIVE UNZIP TASKS
# ==========================================================

active_tasks = {}


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def is_supported_archive(file_name):
    """
    Check whether uploaded file is a supported archive.
    """

    if not file_name:
        return False

    return file_name.lower().endswith(
        SUPPORTED_FORMATS
    )


def is_video_file(file_path):
    """
    Check whether extracted file is a video.
    """

    if not file_path:
        return False

    return file_path.lower().endswith(
        VIDEO_EXTENSIONS
    )


def get_max_file_size():
    """
    Use Config.MAX_FILE_SIZE if available.

    Default:
        2 GB
    """

    return getattr(
        Config,
        "MAX_FILE_SIZE",
        2 * 1024 * 1024 * 1024
    )


async def safe_edit(
    message,
    text
):
    """
    Safely edit status message.
    """

    try:

        await message.edit(text)

    except MessageNotModified:

        pass

    except Exception:

        pass


def get_upload_as_doc(user_id):
    """
    Existing Video / Document setting.

    Existing system:

        False = VIDEO
        True  = DOCUMENT
    """

    if db is None:
        return False

    try:

        return bool(
            db.get_upload_as_doc(user_id)
        )

    except Exception:

        return False


def get_thumbnail(user_id):
    """
    Get user's saved thumbnail.

    Returns Telegram file_id/path depending
    on your existing database implementation.
    """

    if db is None:
        return None

    try:

        thumbnail = db.get_thumbnail(user_id)

        if thumbnail:
            return thumbnail

    except Exception:

        pass

    return None


def get_video_caption(
    file_path,
    index,
    total
):
    """
    Video caption.
    """

    file_name = os.path.basename(
        file_path
    )

    return (
        f"🎬 `{file_name}`\n\n"
        f"📦 File {index}/{total}"
    )


def get_document_caption(
    file_path,
    index,
    total
):
    """
    Document caption.
    """

    file_name = os.path.basename(
        file_path
    )

    return (
        f"📄 `{file_name}`\n\n"
        f"📦 File {index}/{total}"
    )


# ==========================================================
# MAIN ARCHIVE HANDLER
# ==========================================================

@Client.on_message(
    filters.private & filters.document
)
async def handle_unzip_file(
    client,
    message
):

    document = message.document

    if not document:
        return

    file_name = (
        document.file_name
        or "unknown_file"
    )

    # ------------------------------------------------------
    # Only handle supported archives
    # ------------------------------------------------------

    if not is_supported_archive(
        file_name
    ):
        return

    user_id = message.from_user.id

    # ------------------------------------------------------
    # Prevent multiple processes
    # ------------------------------------------------------

    if user_id in active_tasks:

        await message.reply_text(
            "⚠️ আপনার একটি UnZip process "
            "ইতিমধ্যে চলছে।\n\n"
            "আগের process শেষ হওয়ার পর "
            "আবার archive পাঠান।"
        )

        return

    # ------------------------------------------------------
    # FILE SIZE CHECK
    # ------------------------------------------------------

    max_file_size = get_max_file_size()

    if (
        document.file_size
        and document.file_size > max_file_size
    ):

        max_size_gb = (
            max_file_size / (1024 ** 3)
        )

        await message.reply_text(
            "⚠️ File too large.\n\n"
            f"Maximum allowed size: "
            f"{max_size_gb:.2f} GB"
        )

        return

    # ------------------------------------------------------
    # STATUS
    # ------------------------------------------------------

    status_message = await message.reply_text(
        "⏳ Preparing your archive..."
    )

    # ------------------------------------------------------
    # CREATE TASK
    # ------------------------------------------------------

    task = asyncio.create_task(
        process_archive(
            client,
            message,
            status_message
        )
    )

    active_tasks[user_id] = task

    try:

        await task

    except asyncio.CancelledError:

        await safe_edit(
            status_message,
            "⛔ UnZip process cancelled."
        )

    except Exception as e:

        await safe_edit(
            status_message,
            f"❌ Error:\n"
            f"`{str(e)[:3000]}`"
        )

    finally:

        active_tasks.pop(
            user_id,
            None
        )


# ==========================================================
# PROCESS ARCHIVE
# ==========================================================

async def process_archive(
    client,
    message,
    status_message
):

    user_id = message.from_user.id

    file_path = None
    extract_dir = None

    try:

        document = message.document

        file_name = (
            document.file_name
            or "archive"
        )

        # ==================================================
        # GET EXISTING USER MODE
        # ==================================================

        upload_as_doc = get_upload_as_doc(
            user_id
        )

        """
        False:
            VIDEO MODE

        True:
            DOCUMENT MODE
        """

        # ==================================================
        # DOWNLOAD ARCHIVE
        # ==================================================

        await safe_edit(
            status_message,
            "⬇️ Downloading archive..."
        )

        start_time = time.time()

        file_path = await message.download(
            file_name=file_name,
            progress=progress_for_pyrogram,
            progress_args=(
                "⬇️ Downloading...",
                status_message,
                start_time,
            ),
        )

        # ==================================================
        # CREATE EXTRACTION DIRECTORY
        # ==================================================

        extract_dir = tempfile.mkdtemp(
            prefix=f"unzip_{user_id}_"
        )

        await safe_edit(
            status_message,
            "📦 Extracting archive..."
        )

        # ==================================================
        # EXTRACT
        # ==================================================

        try:

            await asyncio.to_thread(
                Archive(file_path).extractall,
                extract_dir
            )

        except Exception as e:

            await safe_edit(
                status_message,
                "❌ Failed to extract archive:\n"
                f"`{str(e)[:3000]}`"
            )

            return

        # ==================================================
        # FIND EXTRACTED FILES
        # ==================================================

        extracted_files = []

        for root, dirs, files in os.walk(
            extract_dir
        ):

            for extracted_name in files:

                extracted_path = os.path.join(
                    root,
                    extracted_name
                )

                if os.path.isfile(
                    extracted_path
                ):

                    extracted_files.append(
                        extracted_path
                    )

        # Keep predictable order
        extracted_files.sort()

        # ==================================================
        # NO FILES
        # ==================================================

        if not extracted_files:

            await safe_edit(
                status_message,
                "⚠️ Archive extracted successfully, "
                "but no files were found."
            )

            return

        # ==================================================
        # FILE COUNTS
        # ==================================================

        total_files = len(
            extracted_files
        )

        video_count = 0
        document_count = 0

        for extracted_file in extracted_files:

            if is_video_file(
                extracted_file
            ):

                video_count += 1

            else:

                document_count += 1

        # ==================================================
        # MODE
        # ==================================================

        if upload_as_doc:

            mode_text = "📁 DOCUMENT"

        else:

            mode_text = "📹 VIDEO"

        # ==================================================
        # GET THUMBNAIL
        # ==================================================

        thumbnail = None

        if not upload_as_doc:

            thumbnail = get_thumbnail(
                user_id
            )

        # ==================================================
        # UPLOAD START
        # ==================================================

        await safe_edit(
            status_message,
            "📦 Extraction completed.\n\n"
            f"⚙️ Upload Mode: `{mode_text}`\n"
            f"📁 Total files: `{total_files}`\n"
            f"🎬 Videos: `{video_count}`\n"
            f"📄 Documents: `{document_count}`\n\n"
            "📤 Starting upload..."
        )

        # ==================================================
        # UPLOAD EACH FILE
        # ==================================================

        for index, extracted_file_path in enumerate(
            extracted_files,
            start=1
        ):

            # ------------------------------------------------
            # CANCEL CHECK
            # ------------------------------------------------

            current_task = asyncio.current_task()

            if (
                current_task
                and current_task.cancelled()
            ):

                raise asyncio.CancelledError

            # ------------------------------------------------
            # RELATIVE PATH
            # ------------------------------------------------

            relative_path = os.path.relpath(
                extracted_file_path,
                extract_dir
            )

            # =================================================
            # DOCUMENT MODE
            # =================================================

            if upload_as_doc:

                await send_as_document(
                    client,
                    message,
                    status_message,
                    extracted_file_path,
                    relative_path,
                    index,
                    total_files
                )

                continue

            # =================================================
            # VIDEO MODE
            # =================================================

            if is_video_file(
                extracted_file_path
            ):

                await send_as_video(
                    client,
                    message,
                    status_message,
                    extracted_file_path,
                    relative_path,
                    index,
                    total_files,
                    thumbnail
                )

            else:

                # Non-video files remain Document
                await send_as_document(
                    client,
                    message,
                    status_message,
                    extracted_file_path,
                    relative_path,
                    index,
                    total_files
                )

        # ==================================================
        # COMPLETE
        # ==================================================

        await safe_edit(
            status_message,
            "✅ UnZip completed successfully!\n\n"
            f"⚙️ Upload Mode: `{mode_text}`\n"
            f"📁 Total files: `{total_files}`\n"
            f"🎬 Videos: `{video_count}`\n"
            f"📄 Documents: `{document_count}`"
        )

    # ======================================================
    # CANCEL
    # ======================================================

    except asyncio.CancelledError:

        await safe_edit(
            status_message,
            "⛔ UnZip process cancelled."
        )

        raise

    # ======================================================
    # ERROR
    # ======================================================

    except Exception as e:

        await safe_edit(
            status_message,
            f"❌ Error:\n"
            f"`{str(e)[:3000]}`"
        )

    # ======================================================
    # CLEANUP
    # ======================================================

    finally:

        # --------------------------------------------------
        # DELETE ORIGINAL ARCHIVE
        # --------------------------------------------------

        if file_path:

            try:

                if os.path.exists(
                    file_path
                ):

                    os.remove(
                        file_path
                    )

            except Exception:

                pass

        # --------------------------------------------------
        # DELETE EXTRACTED FILES
        # --------------------------------------------------

        if extract_dir:

            try:

                if os.path.exists(
                    extract_dir
                ):

                    shutil.rmtree(
                        extract_dir,
                        ignore_errors=True
                    )

            except Exception:

                pass


# ==========================================================
# SEND VIDEO
# ==========================================================

async def send_as_video(
    client,
    message,
    status_message,
    file_path,
    relative_path,
    index,
    total,
    thumbnail
):

    caption = get_video_caption(
        file_path,
        index,
        total
    )

    await safe_edit(
        status_message,
        "🎬 Uploading Video...\n\n"
        f"📄 `{relative_path}`\n"
        f"📦 File {index}/{total}"
    )

    upload_start = time.time()

    # ======================================================
    # WITH THUMBNAIL
    # ======================================================

    if thumbnail:

        try:

            await client.send_video(
                chat_id=message.chat.id,
                video=file_path,
                thumb=thumbnail,
                caption=caption,
                supports_streaming=True,
                progress=progress_for_pyrogram,
                progress_args=(
                    f"⬆️ Uploading Video "
                    f"{index}/{total}...",
                    status_message,
                    upload_start,
                ),
            )

            return

        except Exception:

            # Thumbnail invalid/unavailable হলে
            # thumbnail ছাড়া retry করবে।
            pass

    # ======================================================
    # WITHOUT THUMBNAIL
    # ======================================================

    await client.send_video(
        chat_id=message.chat.id,
        video=file_path,
        caption=caption,
        supports_streaming=True,
        progress=progress_for_pyrogram,
        progress_args=(
            f"⬆️ Uploading Video "
            f"{index}/{total}...",
            status_message,
            upload_start,
        ),
    )


# ==========================================================
# SEND DOCUMENT
# ==========================================================

async def send_as_document(
    client,
    message,
    status_message,
    file_path,
    relative_path,
    index,
    total
):

    caption = get_document_caption(
        file_path,
        index,
        total
    )

    await safe_edit(
        status_message,
        "📄 Uploading Document...\n\n"
        f"📄 `{relative_path}`\n"
        f"📦 File {index}/{total}"
    )

    upload_start = time.time()

    await client.send_document(
        chat_id=message.chat.id,
        document=file_path,
        caption=caption,
        progress=progress_for_pyrogram,
        progress_args=(
            f"⬆️ Uploading Document "
            f"{index}/{total}...",
            status_message,
            upload_start,
        ),
    )


# ==========================================================
# CANCEL UNZIP
# ==========================================================

@Client.on_callback_query(
    filters.regex(r"^cancel_unzip$")
)
async def cancel_unzip_callback(
    client,
    callback_query
):

    user_id = callback_query.from_user.id

    task = active_tasks.get(
        user_id
    )

    # ======================================================
    # NO ACTIVE TASK
    # ======================================================

    if not task:

        await callback_query.answer(
            "⚠️ No ongoing UnZip operation.",
            show_alert=True
        )

        return

    # ======================================================
    # ALREADY FINISHED
    # ======================================================

    if task.done():

        active_tasks.pop(
            user_id,
            None
        )

        await callback_query.answer(
            "⚠️ This process is already finished.",
            show_alert=True
        )

        return

    # ======================================================
    # CANCEL
    # ======================================================

    await callback_query.answer(
        "⛔ Cancelling UnZip...",
        show_alert=True
    )

    task.cancel()

    try:

        await task

    except asyncio.CancelledError:

        pass

    except Exception:

        pass
