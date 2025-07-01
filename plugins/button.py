import logging
import asyncio
import json
import os
import shutil
import time
from datetime import datetime
from pyrogram import enums
from pyrogram.types import InputMediaPhoto
from plugins.config import Config
from plugins.script import Translation
from plugins.thumbnail import *
from plugins.functions.display_progress import progress_for_pyrogram, humanbytes
from plugins.functions.ran_text import random_char
from plugins.database.database import db
from PIL import Image

cookies_file = 'cookies.txt'

# Set up logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

async def forward_to_log_channel(bot, update, message, tg_send_type):
    try:
        await message.forward(
            chat_id=Config.LOG_CHANNEL,
            disable_notification=True
        )
        logger.info(f"Message forwarded to log channel: {message.id}")
    except Exception as e:
        logger.error(f"Error forwarding to log channel: {e}")

async def youtube_dl_call_back(bot, update):
    cb_data = update.data
    tg_send_type, youtube_dl_format, youtube_dl_ext, ranom = cb_data.split("|")
    random1 = random_char(5)

    save_ytdl_json_path = os.path.join(Config.DOWNLOAD_LOCATION, f"{update.from_user.id}{ranom}.json")
    try:
        with open(save_ytdl_json_path, "r", encoding="utf8") as f:
            response_json = json.load(f)
    except FileNotFoundError as e:
        logger.error(f"JSON file not found: {e}")
        await update.message.delete()
        return False

    youtube_dl_url = update.message.reply_to_message.text
    custom_file_name = f"{response_json.get('title')}_{youtube_dl_format}.{youtube_dl_ext}"
    youtube_dl_username = youtube_dl_password = None

    if "|" in youtube_dl_url:
        parts = youtube_dl_url.split("|")
        if len(parts) == 2:
            youtube_dl_url, custom_file_name = parts
        elif len(parts) == 4:
            youtube_dl_url, custom_file_name, youtube_dl_username, youtube_dl_password = parts
        else:
            for ent in update.message.reply_to_message.entities:
                if ent.type == "text_link":
                    youtube_dl_url = ent.url
                elif ent.type == "url":
                    youtube_dl_url = youtube_dl_url[ent.offset:ent.offset + ent.length]
        youtube_dl_url = youtube_dl_url.strip()
        custom_file_name = custom_file_name.strip()
        if youtube_dl_username:
            youtube_dl_username = youtube_dl_username.strip()
        if youtube_dl_password:
            youtube_dl_password = youtube_dl_password.strip()
    else:
        for ent in update.message.reply_to_message.entities:
            if ent.type == "text_link":
                youtube_dl_url = ent.url
            elif ent.type == "url":
                youtube_dl_url = youtube_dl_url[ent.offset:ent.offset + ent.length]

    await update.message.edit_caption(caption=Translation.DOWNLOAD_START.format(custom_file_name))
    description = response_json.get("fulltitle", "")[:1021] or Translation.CUSTOM_CAPTION_UL_FILE

    tmp_dir = os.path.join(Config.DOWNLOAD_LOCATION, f"{update.from_user.id}{random1}")
    os.makedirs(tmp_dir, exist_ok=True)
    download_path = os.path.join(tmp_dir, custom_file_name)

    cmd = [
        "yt-dlp", "-c", "--max-filesize", str(Config.TG_MAX_FILE_SIZE),
        "--embed-subs", "-f", "bv*+ba/best",
        "--hls-prefer-ffmpeg", "--cookies", cookies_file,
        "--user-agent", "Mozilla/5.0 ... Safari/537.36",
        youtube_dl_url, "-o", download_path
    ]
    if tg_send_type == "audio":
        cmd = [
            "yt-dlp", "-c", "--max-filesize", str(Config.TG_MAX_FILE_SIZE),
            "--bidi-workaround", "--extract-audio", "--cookies", cookies_file,
            "--audio-format", youtube_dl_ext, "--audio-quality", youtube_dl_format,
            "--user-agent", "Mozilla/5.0 ... Safari/537.36",
            youtube_dl_url, "-o", download_path
        ]
    if Config.HTTP_PROXY:
        cmd += ["--proxy", Config.HTTP_PROXY]
    if youtube_dl_username:
        cmd += ["--username", youtube_dl_username]
    if youtube_dl_password:
        cmd += ["--password", youtube_dl_password]
    cmd.append("--no-warnings")

    logger.info(cmd)
    start = datetime.now()
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    e_resp = stderr.decode().strip()
    t_resp = stdout.decode().strip()
    logger.info(e_resp); logger.info(t_resp)

    if process.returncode != 0:
        await update.message.edit_caption(caption=f"Error: {e_resp}")
        return False
    if "**Invalid link !**" in e_resp:
        await update.message.edit_caption(caption=e_resp.replace("**Invalid link !**", "").strip())
        return False

    try:
        os.remove(save_ytdl_json_path)
    except:
        pass
    end_dl = datetime.now()
    dl_time = (end_dl - start).seconds

    if os.path.isfile(download_path):
        file_size = os.stat(download_path).st_size
    else:
        download_path = os.path.splitext(download_path)[0] + ".mkv"
        if os.path.isfile(download_path):
            file_size = os.stat(download_path).st_size
        else:
            await update.message.edit_caption(caption=Translation.DOWNLOAD_FAILED)
            return False

    if file_size > Config.TG_MAX_FILE_SIZE:
        await update.message.edit_caption(caption=Translation.RCHD_TG_API_LIMIT.format(dl_time, humanbytes(file_size)))
        return False

    await update.message.edit_caption(caption=Translation.UPLOAD_START.format(custom_file_name))
    upload_start = time.time()

    # Choose upload type
    if not await db.get_upload_as_doc(update.from_user.id):
        thumbnail = await Gthumb01(bot, update)
        sent = await update.message.reply_document(
            document=download_path, thumb=thumbnail, caption=description,
            progress=progress_for_pyrogram, progress_args=(Translation.UPLOAD_START, update.message, upload_start)
        )
        await forward_to_log_channel(bot, update, sent, "document")
    elif tg_send_type == "vm":
        w, duration = await Mdata02(download_path)
        thumb = await Gthumb02(bot, update, duration, download_path)
        sent = await update.message.reply_video_note(
            video_note=download_path, duration=duration, length=w, thumb=thumb,
            progress=progress_for_pyrogram, progress_args=(Translation.UPLOAD_START, update.message, upload_start)
        )
        await forward_to_log_channel(bot, update, sent, "vm")
    elif tg_send_type == "audio":
        dur = await Mdata03(download_path)
        thumb = await Gthumb01(bot, update)
        sent = await update.message.reply_audio(
            audio=download_path, caption=description, duration=dur, thumb=thumb,
            progress=progress_for_pyrogram, progress_args=(Translation.UPLOAD_START, update.message, upload_start)
        )
        await forward_to_log_channel(bot, update, sent, "audio")
    else:
        w, h, dur = await Mdata01(download_path)
        thumb = await Gthumb02(bot, update, dur, download_path)
        sent = await update.message.reply_video(
            video=download_path, caption=description, duration=dur,
            width=w, height=h, supports_streaming=True, thumb=thumb,
            progress=progress_for_pyrogram, progress_args=(Translation.UPLOAD_START, update.message, upload_start)
        )
        await forward_to_log_channel(bot, update, sent, "video")

    end_up = datetime.now()
    up_time = (end_up - end_dl).seconds

    try:
        shutil.rmtree(tmp_dir)
        os.remove(thumb)
    except Exception as cleanup_err:
        logger.error(f"Cleanup error: {cleanup_err}")

    await update.message.edit_caption(
        caption=Translation.AFTER_SUCCESSFUL_UPLOAD_MSG_WITH_TS.format(dl_time, up_time)
    )
    logger.info(f"✅ Downloaded in: {dl_time}s, Uploaded in: {up_time}s")
