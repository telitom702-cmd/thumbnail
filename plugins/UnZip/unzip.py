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


# Supported archive formats
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


# Running unzip tasks
active_tasks = {}


def is_supported_archive(file_name):
    if not file_name:
        return False

    file_name = file_name.lower()

    return file_name.endswith(SUPPORTED_FORMATS)


def get_max_file_size():
    """
    Uses existing plugins/config.py.

    If MAX_FILE_SIZE is not present, default is 2GB.
    """
    return getattr(
        Config,
        "MAX_FILE_SIZE",
        2 * 1024 * 1024 * 1024
    )


async def safe_edit(message, text):
    try:
        await message.edit(text)
    except MessageNotModified:
        pass
    except Exception:
        pass


@Client.on_message(
    filters.private & filters.document
)
async def handle_unzip_file(client, message):

    document = message.document

    if not document:
        return

    file_name = document.file_name or "unknown_file"

    # IMPORTANT:
    # Only process supported archive files.
    # Normal documents will be handled by your existing bot.
    if not is_supported_archive(file_name):
        return

    user_id = message.from_user.id

    # Prevent multiple unzip jobs for the same user
    if user_id in active_tasks:
        await message.reply_text(
            "⚠️ আপনার একটি UnZip process ইতিমধ্যে চলছে।\n"
            "আগের process শেষ হওয়ার পর আবার file পাঠান।"
        )
        return

    max_file_size = get_max_file_size()

    if document.file_size and document.file_size > max_file_size:
        max_size_gb = max_file_size / (1024 ** 3)

        await message.reply_text(
            f"⚠️ File too large.\n\n"
            f"Maximum allowed size: {max_size_gb:.2f} GB"
        )
        return

    status_message = await message.reply_text(
        "⏳ Preparing your archive..."
    )

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
            f"❌ Error:\n`{str(e)[:3000]}`"
        )

    finally:
        active_tasks.pop(user_id, None)


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
        file_name = document.file_name or "archive"

        # --------------------------------------------------
        # DOWNLOAD
        # --------------------------------------------------

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

        # Check cancellation
        current_task = asyncio.current_task()

        if current_task.cancelled():
            raise asyncio.CancelledError

        # --------------------------------------------------
        # CREATE EXTRACTION DIRECTORY
        # --------------------------------------------------

        extract_dir = tempfile.mkdtemp(
            prefix=f"unzip_{user_id}_"
        )

        await safe_edit(
            status_message,
            "📦 Extracting archive..."
        )

        # --------------------------------------------------
        # EXTRACT
        # --------------------------------------------------

        try:

            # pyunpack extraction is synchronous.
            # Run it in a worker thread so Cancel can still
            # be processed by the bot.
            await asyncio.to_thread(
                Archive(file_path).extractall,
                extract_dir
            )

        except Exception as e:

            await safe_edit(
                status_message,
                f"❌ Failed to extract archive:\n"
                f"`{str(e)[:3000]}`"
            )

            return

        # --------------------------------------------------
        # FIND EXTRACTED FILES
        # --------------------------------------------------

        extracted_files = []

        for root, dirs, files in os.walk(extract_dir):

            for extracted_name in files:

                extracted_path = os.path.join(
                    root,
                    extracted_name
                )

                if os.path.isfile(extracted_path):
                    extracted_files.append(
                        extracted_path
                    )

        if not extracted_files:

            await safe_edit(
                status_message,
                "⚠️ Archive extracted successfully, "
                "but no files were found."
            )

            return

        # --------------------------------------------------
        # UPLOAD FILES
        # --------------------------------------------------

        total_files = len(extracted_files)

        await safe_edit(
            status_message,
            f"📤 Uploading extracted files...\n\n"
            f"Total files: {total_files}"
        )

        for index, extracted_file_path in enumerate(
            extracted_files,
            start=1
        ):

            # Check cancellation before every upload
            if asyncio.current_task().cancelled():
                raise asyncio.CancelledError

            relative_path = os.path.relpath(
                extracted_file_path,
                extract_dir
            )

            caption = (
                f"📄 `{relative_path}`\n\n"
                f"📦 File {index}/{total_files}"
            )

            upload_start = time.time()

            await client.send_document(
                chat_id=message.chat.id,
                document=extracted_file_path,
                caption=caption,
                progress=progress_for_pyrogram,
                progress_args=(
                    f"⬆️ Uploading {index}/{total_files}...",
                    status_message,
                    upload_start,
                ),
            )

        # --------------------------------------------------
        # COMPLETE
        # --------------------------------------------------

        await safe_edit(
            status_message,
            "✅ All files have been extracted "
            "and sent successfully."
        )

    except asyncio.CancelledError:

        await safe_edit(
            status_message,
            "⛔ UnZip process cancelled."
        )

        raise

    except Exception as e:

        await safe_edit(
            status_message,
            f"❌ Error:\n`{str(e)[:3000]}`"
        )

    finally:

        # --------------------------------------------------
        # CLEANUP
        # --------------------------------------------------

        if file_path:

            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass

        if extract_dir:

            try:
                if os.path.exists(extract_dir):
                    shutil.rmtree(
                        extract_dir,
                        ignore_errors=True
                    )
            except Exception:
                pass


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

    task = active_tasks.get(user_id)

    if not task:

        await callback_query.answer(
            "⚠️ No ongoing UnZip operation.",
            show_alert=True
        )

        return

    if task.done():

        active_tasks.pop(user_id, None)

        await callback_query.answer(
            "⚠️ This process is already finished.",
            show_alert=True
        )

        return

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
