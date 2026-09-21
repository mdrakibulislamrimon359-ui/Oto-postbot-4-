import os
import re
import sqlite3
import threading
from datetime import time
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

TARGET_CHAT_RAW = (
    os.getenv("TARGET_CHAT_ID")
    or os.getenv("CHANNEL_USERNAME")
    or "@RJteam123890"
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RJteam1").strip().lstrip("@")

TZ = ZoneInfo("Asia/Dhaka")
DB_NAME = "autopost.db"

PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")


# =========================================================
# TARGET CHAT
# =========================================================

def parse_target_chat(value):
    value = str(value).strip()

    if re.fullmatch(r"-?\d+", value):
        return int(value)

    return value


TARGET_CHAT = parse_target_chat(TARGET_CHAT_RAW)


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running.")

    def log_message(self, format, *args):
        return


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


threading.Thread(
    target=start_health_server,
    daemon=True
).start()


# =========================================================
# DATABASE
# =========================================================

def db_connect():
    return sqlite3.connect(DB_NAME)


def init_db():

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_time TEXT NOT NULL,
            photo_id TEXT,
            caption TEXT NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# TIME SYSTEM
# =========================================================

def normalize_time(value):
    """
    Supports:

    06:00 AM
    6:00 AM
    06:00 PM
    6:00 PM

    Also supports old 24-hour format:

    06:00
    18:00
    """

    value = value.strip().upper()

    # 12-hour AM/PM
    match = re.fullmatch(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        value
    )

    if match:

        hour = int(match.group(1))
        minute = int(match.group(2))
        ampm = match.group(3)

        if hour < 1 or hour > 12:
            raise ValueError("Hour must be between 1 and 12.")

        if minute < 0 or minute > 59:
            raise ValueError("Minute must be between 00 and 59.")

        if ampm == "AM":
            if hour == 12:
                hour = 0
        else:
            if hour != 12:
                hour += 12

        return f"{hour:02d}:{minute:02d}"

    # Old 24-hour format support
    match = re.fullmatch(
        r"(\d{1,2}):(\d{2})",
        value
    )

    if match:

        hour = int(match.group(1))
        minute = int(match.group(2))

        if hour < 0 or hour > 23:
            raise ValueError("Hour must be between 00 and 23.")

        if minute < 0 or minute > 59:
            raise ValueError("Minute must be between 00 and 59.")

        return f"{hour:02d}:{minute:02d}"

    raise ValueError(
        "Invalid time. Use example: 06:00 AM or 06:00 PM"
    )


def display_time(value):
    """
    Converts database 24-hour time to 12-hour AM/PM display.
    """

    hour, minute = map(int, value.split(":"))

    ampm = "AM"

    if hour >= 12:
        ampm = "PM"

    display_hour = hour % 12

    if display_hour == 0:
        display_hour = 12

    return f"{display_hour:02d}:{minute:02d} {ampm}"


# =========================================================
# ADMIN CHECK
# =========================================================

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user:
        return False

    # Group / Supergroup
    if update.effective_chat and update.effective_chat.type in (
        "group",
        "supergroup"
    ):

        try:

            member = await context.bot.get_chat_member(
                update.effective_chat.id,
                user.id
            )

            if member.status in ("administrator", "creator"):
                return True

        except Exception as e:
            print("Admin check error:", e)

        return False

    # Private chat
    username = (user.username or "").strip().lstrip("@")

    return username.lower() == ADMIN_USERNAME.lower()


# =========================================================
# SCHEDULE POST
# =========================================================

async def send_post(context: ContextTypes.DEFAULT_TYPE):

    post_id = context.job.data["id"]

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, post_time, photo_id, caption, enabled
        FROM posts
        WHERE id = ?
    """, (post_id,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return

    post_id, post_time, photo_id, caption, enabled = row

    if not enabled:
        return

    try:

        if photo_id:

            await context.bot.send_photo(
                chat_id=TARGET_CHAT,
                photo=photo_id,
                caption=caption
            )

        else:

            await context.bot.send_message(
                chat_id=TARGET_CHAT,
                text=caption
            )

        print(
            f"POST SUCCESS | ID={post_id} | TIME={display_time(post_time)}"
        )

    except Exception as e:

        print(
            f"POST ERROR | ID={post_id} | ERROR={e}"
        )


# =========================================================
# SCHEDULE ONE POST
# =========================================================

def schedule_post(application, post_id, post_time):

    hour, minute = map(int, post_time.split(":"))

    # Remove old job
    job_name = f"post_{post_id}"

    old_jobs = application.job_queue.get_jobs_by_name(job_name)

    for job in old_jobs:
        job.schedule_removal()

    # Daily job
    application.job_queue.run_daily(
        send_post,
        time=time(
            hour=hour,
            minute=minute,
            tzinfo=TZ
        ),
        days=tuple(range(7)),
        data={"id": post_id},
        name=job_name
    )

    print(
        f"SCHEDULED | ID={post_id} | TIME={display_time(post_time)}"
    )


# =========================================================
# LOAD ALL SAVED POSTS
# =========================================================

def schedule_all(application):

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, post_time
        FROM posts
        WHERE enabled = 1
    """)

    rows = cur.fetchall()

    conn.close()

    for post_id, post_time in rows:
        schedule_post(
            application,
            post_id,
            post_time
        )


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    await update.message.reply_text(
        "🤖 Auto Post Bot চালু আছে!\n\n"
        "🕐 Time format:\n"
        "06:00 AM = সকাল ৬টা\n"
        "12:00 PM = দুপুর ১২টা\n"
        "06:00 PM = সন্ধ্যা ৬টা\n"
        "11:00 PM = রাত ১১টা\n\n"
        "Text post example:\n"
        "06:00 PM|🌙 শুভ সন্ধ্যা 🌙\n\n"
        "Photo post করতে photo পাঠিয়ে caption-এ একই format ব্যবহার করুন।"
    )


# =========================================================
# HELP
# =========================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    await update.message.reply_text(
        "📚 Commands:\n\n"
        "/start - Bot start\n"
        "/help - Help\n"
        "/list - Saved posts\n"
        "/test - Test post\n"
        "/status - Bot status\n"
        "/delete ID - Delete post\n"
        "/clear - Delete all posts\n"
        "/on - Enable all posts\n"
        "/off - Disable all posts\n\n"
        "🕐 Time examples:\n"
        "06:00 AM\n"
        "12:00 PM\n"
        "06:00 PM\n"
        "11:30 PM\n"
    )


# =========================================================
# SAVE TEXT POST
# =========================================================

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    if "|" not in text:
        return

    time_part, caption = text.split("|", 1)

    caption = caption.strip()

    if not caption:
        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )
        return

    try:
        post_time = normalize_time(time_part)

    except ValueError:

        await update.message.reply_text(
            "❌ Time ভুল।\n\n"
            "এভাবে লিখুন:\n"
            "06:00 AM|সুপ্রভাত 🌞\n"
            "06:00 PM|শুভ সন্ধ্যা 🌙"
        )

        return

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO posts (
            post_time,
            photo_id,
            caption,
            enabled
        )
        VALUES (?, ?, ?, 1)
    """, (
        post_time,
        None,
        caption
    ))

    post_id = cur.lastrowid

    conn.commit()
    conn.close()

    schedule_post(
        context.application,
        post_id,
        post_time
    )

    await update.message.reply_text(
        f"✅ Post saved!\n\n"
        f"🆔 ID: {post_id}\n"
        f"⏰ Time: {display_time(post_time)}\n"
        f"📅 প্রতিদিন এই সময়ে পোস্ট হবে।"
    )


# =========================================================
# SAVE PHOTO POST
# =========================================================

async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    if not update.message or not update.message.photo:
        return

    caption = update.message.caption or ""

    if "|" not in caption:

        await update.message.reply_text(
            "❌ Caption format ভুল।\n\n"
            "Example:\n"
            "06:00 PM|শুভ সন্ধ্যা 🌙"
        )

        return

    time_part, post_caption = caption.split("|", 1)

    post_caption = post_caption.strip()

    if not post_caption:

        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )

        return

    try:

        post_time = normalize_time(time_part)

    except ValueError:

        await update.message.reply_text(
            "❌ Time ভুল।\n\n"
            "Example:\n"
            "06:00 AM|সুপ্রভাত 🌞\n"
            "06:00 PM|শুভ সন্ধ্যা 🌙"
        )

        return

    photo_id = update.message.photo[-1].file_id

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO posts (
            post_time,
            photo_id,
            caption,
            enabled
        )
        VALUES (?, ?, ?, 1)
    """, (
        post_time,
        photo_id,
        post_caption
    ))

    post_id = cur.lastrowid

    conn.commit()
    conn.close()

    schedule_post(
        context.application,
        post_id,
        post_time
    )

    await update.message.reply_text(
        f"✅ Photo post saved!\n\n"
        f"🆔 ID: {post_id}\n"
        f"⏰ Time: {display_time(post_time)}\n"
        f"📅 প্রতিদিন এই সময়ে পোস্ট হবে।"
    )


# =========================================================
# LIST
# =========================================================

async def list_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, post_time, photo_id, caption, enabled
        FROM posts
        ORDER BY post_time
    """)

    rows = cur.fetchall()

    conn.close()

    if not rows:

        await update.message.reply_text(
            "📭 কোনো post save করা নেই।"
        )

        return

    lines = ["📋 Saved Posts:\n"]

    for post_id, post_time, photo_id, caption, enabled in rows:

        status = "ON" if enabled else "OFF"
        post_type = "📷 Photo" if photo_id else "📝 Text"

        lines.append(
            f"🆔 {post_id}\n"
            f"⏰ {display_time(post_time)}\n"
            f"{post_type}\n"
            f"🔘 {status}\n"
            f"📄 {caption[:80]}\n"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# =========================================================
# DELETE
# =========================================================

async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    if not context.args:

        await update.message.reply_text(
            "Example: /delete 3"
        )

        return

    try:
        post_id = int(context.args[0])

    except ValueError:

        await update.message.reply_text(
            "❌ ID অবশ্যই সংখ্যা হতে হবে।"
        )

        return

    job_name = f"post_{post_id}"

    for job in context.application.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM posts WHERE id = ?",
        (post_id,)
    )

    deleted = cur.rowcount

    conn.commit()
    conn.close()

    if deleted:

        await update.message.reply_text(
            f"✅ Post {post_id} deleted."
        )

    else:

        await update.message.reply_text(
            "❌ এই ID পাওয়া যায়নি।"
        )


# =========================================================
# CLEAR
# =========================================================

async def clear_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    for job in context.application.job_queue.jobs():
        job.schedule_removal()

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("DELETE FROM posts")

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "🗑️ সব scheduled post delete করা হয়েছে।"
    )


# =========================================================
# ON
# =========================================================

async def on_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "UPDATE posts SET enabled = 1"
    )

    conn.commit()

    cur.execute("""
        SELECT id, post_time
        FROM posts
        WHERE enabled = 1
    """)

    rows = cur.fetchall()

    conn.close()

    for post_id, post_time in rows:

        schedule_post(
            context.application,
            post_id,
            post_time
        )

    await update.message.reply_text(
        "✅ সব post ON করা হয়েছে।"
    )


# =========================================================
# OFF
# =========================================================

async def off_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    for job in context.application.job_queue.jobs():
        job.schedule_removal()

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "UPDATE posts SET enabled = 0"
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "⛔ সব automatic post OFF করা হয়েছে।"
    )


# =========================================================
# TEST
# =========================================================

async def test_post(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    try:

        await context.bot.send_message(
            chat_id=TARGET_CHAT,
            text="✅ Auto Post Bot Test Message"
        )

        await update.message.reply_text(
            "✅ Test message successfully sent."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Test failed:\n{e}"
        )


# =========================================================
# STATUS
# =========================================================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not await is_admin(update, context):
        return

    conn = db_connect()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM posts WHERE enabled = 1"
    )

    count = cur.fetchone()[0]

    conn.close()

    await update.message.reply_text(
        f"🤖 Bot: ONLINE\n"
        f"🌏 Timezone: Bangladesh (Asia/Dhaka)\n"
        f"🎯 Target: {TARGET_CHAT}\n"
        f"📅 Active posts: {count}\n"
        f"⏰ Time format: 12-hour AM/PM"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("list", list_posts)
    )

    application.add_handler(
        CommandHandler("delete", delete_post)
    )

    application.add_handler(
        CommandHandler("clear", clear_posts)
    )

    application.add_handler(
        CommandHandler("on", on_posts)
    )

    application.add_handler(
        CommandHandler("off", off_posts)
    )

    application.add_handler(
        CommandHandler("test", test_post)
    )

    application.add_handler(
        CommandHandler("status", status)
    )

    # Photo messages
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            photo_message
        )
    )

    # Text messages
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_message
        )
    )

    # Load saved schedules
    schedule_all(application)

    print("===================================")
    print("🤖 Auto Post Bot Started")
    print("🌏 Timezone: Asia/Dhaka")
    print("🎯 Target:", TARGET_CHAT)
    print("⏰ Time format: 12-hour AM/PM")
    print("===================================")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
