import logging
import asyncio
import json
import os
import shutil
import time
from datetime import datetime
from urllib.parse import urlparse
from pyrogram import enums
from pyrogram.types import InputMediaPhoto
from plugins.config import Config
from plugins.script import Translation
from plugins.thumbnail import Mdata01, Mdata02, Mdata03, Gthumb01, Gthumb02
from plugins.functions.display_progress import progress_for_pyrogram, humanbytes
from plugins.database.database import db
from plugins.functions.ran_text import random_char

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

cookies_file = 'cookies.txt'

async def youtube_dl_call_back(bot, update):
    cb_data = update.data
    try:
        tg_send_type, youtube_dl_format, youtube_dl_ext, ranom = cb_data.split("|")
    except ValueError:
        logger.error("Invalid callback data format")
        await update.message.edit_caption(caption="Invalid callback data. Please try again.")
        return False

    save_ytdl_json_path = os.path.join(Config.DOWNLOAD_LOCATION, f"{update.from_user.id}{ranom}.json")

    # Load JSON response
    try:
        with open(save_ytdl_json_path, "r", encoding="utf8") as f:
            response_json = json.load(f)
    except FileNotFoundError:
        logger.error(f"JSON file not found: {save_ytdl_json_path}")
        await update.message.edit_caption(caption="Download data not found. Please try again.")
        return False
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in file: {save_ytdl_json_path}")
        await update.message.edit_caption(caption="Invalid download data. Please try again.")
        return False

    # Parse URL and clean query parameters for YouTube Shorts
    youtube_dl_url = update.message.reply_to_message.text.strip()
    parsed_url = urlparse(youtube_dl_url)
    if "youtube.com" in parsed_url.netloc or "youtu.be" in parsed_url.netloc:
        youtube_dl_url = f"https://www.youtube.com{parsed_url.path}"

    custom_file_name = f"{response_json.get('title', 'video')}_{youtube_dl_format}.{youtube_dl_ext}".replace("|", "_")
    youtube_dl_username = None
    youtube_dl_password = None

    # Handle URL with additional parameters
    if "|" in youtube_dl_url:
        url_parts = youtube_dl_url.split("|")
        if len(url_parts) == 2:
            youtube_dl_url, custom_file_name = url_parts
        elif len(url_parts) == 4:
            youtube_dl_url, custom_file_name, youtube_dl_username, youtube_dl_password = url_parts
        else:
            for entity in update.message.reply_to_message.entities:
                if entity.type == enums.MessageEntityType.TEXT_LINK:
                    youtube_dl_url = entity.url
                elif entity.type == enums.MessageEntityType.URL:
                    o, l = entity.offset, entity.length
                    youtube_dl_url = youtube_dl_url[o:o + l]
    else:
        for entity in update.message.reply_to_message.entities:
            if entity.type == enums.MessageEntityType.TEXT_LINK:
                youtube_dl_url = entity.url
            elif entity.type == enums.MessageEntityType.URL:
                o, l = entity.offset, entity.length
                youtube_dl_url = youtube_dl_url[o:o + l]

    youtube_dl_url = youtube_dl_url.strip()
    custom_file_name = custom_file_name.strip().replace("|", "_")
    if youtube_dl_username:
        youtube_dl_username = youtube_dl_username.strip()
    if youtube_dl_password:
        youtube_dl_password = youtube_dl_password.strip()

    # Update message to indicate download start
    await update.message.edit_caption(caption=Translation.DOWNLOAD_START.format(custom_file_name))

    # Prepare description
    description = response_json.get("fulltitle", Translation.CUSTOM_CAPTION_UL_FILE)[:1021]

    # Create temporary directory
    tmp_directory_for_each_user = os.path.join(Config.DOWNLOAD_LOCATION, f"{update.from_user.id}{ranom}")
    os.makedirs(tmp_directory_for_each_user, exist_ok=True)
    download_directory = os.path.join(tmp_directory_for_each_user, custom_file_name)

    # Check if ffmpeg is available for video downloads
    ffmpeg_available = True
    if tg_send_type != "audio":
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
        except FileNotFoundError:
            logger.warning("ffmpeg not found. Falling back to single stream format.")
            ffmpeg_available = False

    # Build yt-dlp command
    command_to_exec = [
        "yt-dlp",
        "-c",
        "--max-filesize", str(Config.TG_MAX_FILE_SIZE),
        "--embed-subs",
        "-f", f"{youtube_dl_format}+bestaudio/best" if ffmpeg_available and tg_send_type != "audio" else "best",
        "--cookies", cookies_file,
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        youtube_dl_url,
        "-o", download_directory
    ]

    if tg_send_type == "audio":
        command_to_exec.extend([
            "--extract-audio",
            "--audio-format", youtube_dl_ext,
            "--audio-quality", youtube_dl_format
        ])
    elif ffmpeg_available:
        command_to_exec.extend(["--merge-output-format", "mp4"])

    if Config.HTTP_PROXY:
        command_to_exec.extend(["--proxy", Config.HTTP_PROXY])
    if youtube_dl_username:
        command_to_exec.extend(["--username", youtube_dl_username])
    if youtube_dl_password:
        command_to_exec.extend(["--password", youtube_dl_password])

    command_to_exec.append("--no-warnings")

    logger.info(f"Executing command: {' '.join(command_to_exec)}")
    start = datetime.now()

    # Execute yt-dlp command
    try:
        process = await asyncio.create_subprocess_exec(
            *command_to_exec,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        e_response = stderr.decode().strip()
        t_response = stdout.decode().strip()
        logger.info(f"yt-dlp stdout: {t_response}")
        logger.info(f"yt-dlp stderr: {e_response}")

        if process.returncode != 0:
            await update.message.edit_caption(caption=f"Download failed: {e_response or 'Unknown error'}")
            return False
    except Exception as e:
        logger.error(f"Subprocess error: {e}")
        await update.message.edit_caption(caption=f"Download failed: {str(e)}")
        return False

    # Clean up JSON file
    try:
        os.remove(save_ytdl_json_path)
    except FileNotFoundError:
        pass

    end_one = datetime.now()
    time_taken_for_download = (end_one - start).seconds

    # Check if file exists, try fallback extensions
    if not os.path.isfile(download_directory):
        for ext in [".mp4", ".mkv", ".webm"]:
            download_directory = os.path.splitext(download_directory)[0] + ext
            if os.path.isfile(download_directory):
                break
        else:
            await update.message.edit_caption(caption=Translation.DOWNLOAD_FAILED)
            return False

    # Check file size
    file_size = os.stat(download_directory).st_size
    if file_size > Config.TG_MAX_FILE_SIZE:
        await update.message.edit_caption(
            caption=Translation.RCHD_TG_API_LIMIT.format(time_taken_for_download, humanbytes(file_size))
        )
        return False

    # Update message to indicate upload start
    await update.message.edit_caption(caption=Translation.UPLOAD_START.format(custom_file_name))

    start_time = time.time()
    sent_message = None
    thumbnail = None

    # Upload file based on send type
    try:
        if tg_send_type == "audio":
            duration = await Mdata03(download_directory)
            thumbnail = await Gthumb01(bot, update)
            sent_message = await update.message.reply_audio(
                audio=download_directory,
                caption=description,
                duration=duration,
                thumb=thumbnail,
                progress=progress_for_pyrogram,
                progress_args=(Translation.UPLOAD_START, update.message, start_time)
            )
        elif tg_send_type == "vm":
            width, duration = await Mdata02(download_directory)
            height = 720  # Replace with actual height from Mdata02 if available
            thumbnail = await Gthumb02(bot, update, duration, download_directory)
            sent_message = await update.message.reply_video(
                video=download_directory,
                caption=description,
                duration=duration,
                width=width,
                height=height,
                supports_streaming=True,
                thumb=thumbnail,
                progress=progress_for_pyrogram,
                progress_args=(Translation.UPLOAD_START, update.message, start_time)
            )
        elif await db.get_upload_as_doc(update.from_user.id) is False:
            width, height, duration = await Mdata01(download_directory)
            thumbnail = await Gthumb02(bot, update, duration, download_directory)
            sent_message = await update.message.reply_video(
                video=download_directory,
                caption=description,
                duration=duration,
                width=width,
                height=height,
                supports_streaming=True,
                thumb=thumbnail,
                progress=progress_for_pyrogram,
                progress_args=(Translation.UPLOAD_START, update.message, start_time)
            )
        else:
            thumbnail = await Gthumb01(bot, update)
            sent_message = await update.message.reply_document(
                document=download_directory,
                thumb=thumbnail,
                caption=description,
                progress=progress_for_pyrogram,
                progress_args=(Translation.UPLOAD_START, update.message, start_time)
            )
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await update.message.edit_caption(caption=f"Upload failed: {str(e)}")
        return False

    # Forward to log channel
    if Config.LOG_CHANNEL and sent_message:
        try:
            await bot.copy_message(
                chat_id=int(Config.LOG_CHANNEL),
                from_chat_id=sent_message.chat.id,
                message_id=sent_message.id
            )
        except Exception as e:
            logger.error(f"Failed to log to channel: {e}")

    end_two = datetime.now()
    time_taken_for_upload = (end_two - end_one).seconds

    # Clean up
    try:
        shutil.rmtree(tmp_directory_for_each_user, ignore_errors=True)
        if thumbnail and os.path.exists(thumbnail):
            os.remove(thumbnail)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

    # Update message with success status
    await update.message.edit_caption(
        caption=Translation.AFTER_SUCCESSFUL_UPLOAD_MSG_WITH_TS.format(
            time_taken_for_download, time_taken_for_upload
        )
    )

    logger.info(f"Downloaded in: {time_taken_for_download} seconds")
    logger.info(f"Uploaded in: {time_taken_for_upload} seconds")
    return True
