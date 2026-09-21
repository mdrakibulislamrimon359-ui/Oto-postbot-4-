import os
import sqlite3
from datetime import time
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

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

# You can use:
#   @channelusername
# OR a numeric Telegram chat ID such as:
#   -1001234567890
TARGET_CHAT_RAW = os.getenv("TARGET_CHAT_ID") or os.getenv("CHANNEL_USERNAME")

ADMIN_USERNAME = "RJteam1"

# Bangladesh Time
TZ = ZoneInfo("Asia/Dhaka")

# Database
DB_NAME = "autopost.db"

# Render Web Service port
PORT = int(os.getenv("PORT", "10000"))


# =========================================================
# CONFIG CHECK
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable পাওয়া যায়নি। "
        "Render Environment-এ BOT_TOKEN সেট করুন।"
    )

if not TARGET_CHAT_RAW:
    raise RuntimeError(
        "TARGET_CHAT_ID অথবা CHANNEL_USERNAME environment variable "
        "পাওয়া যায়নি। Render Environment-এ destination সেট করুন।"
    )


def get_target_chat():
    """Convert numeric chat IDs to int; keep @username as string."""
    value = TARGET_CHAT_RAW.strip()

    if value.lstrip("-").isdigit():
        return int(value)

    return value


TARGET_CHAT = get_target_chat()


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"RJ Auto Post Bot is running!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"🌐 Render health server running on port {PORT}")
    server.serve_forever()


def start_health_server():
    thread = Thread(
        target=run_health_server,
        daemon=True,
    )
    thread.start()


# =========================================================
# DATABASE
# =========================================================

def db():
    con = sqlite3.connect(DB_NAME)
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_time TEXT NOT NULL,
            photo_id TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    """)

    # If an older database already exists, photo_id is normally
    # already present. This keeps old saved posts compatible.
    con.commit()
    con.close()


# =========================================================
# ADMIN CHECK
# =========================================================

def is_admin(user):
    if not user:
        return False

    username = user.username

    if not username:
        return False

    return username.lower() == ADMIN_USERNAME.lower()


# =========================================================
# TIME VALIDATION
# =========================================================

def validate_time(post_time):
    try:
        hour, minute = map(int, post_time.strip().split(":"))

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None

        return time(hour=hour, minute=minute, tzinfo=TZ)

    except (ValueError, TypeError):
        return None


# =========================================================
# REMOVE SCHEDULED JOBS
# =========================================================

def remove_all_post_jobs(application):
    if not application.job_queue:
        return

    for job in application.job_queue.jobs():
        if job.name and job.name.startswith("post_"):
            job.schedule_removal()


# =========================================================
# SCHEDULE ONE POST
# =========================================================

def schedule_post(application, post_id, post_time):
    post_time_obj = validate_time(post_time)

    if post_time_obj is None:
        print(f"❌ Invalid time for post {post_id}: {post_time}")
        return False

    job_name = f"post_{post_id}"

    # Remove duplicate job if it already exists
    for job in application.job_queue.jobs():
        if job.name == job_name:
            job.schedule_removal()

    application.job_queue.run_daily(
        send_post,
        time=post_time_obj,
        days=tuple(range(7)),
        data={"id": post_id},
        name=job_name,
    )

    return True


# =========================================================
# SCHEDULE ALL POSTS
# =========================================================

def schedule_all(application):
    remove_all_post_jobs(application)

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT id, post_time
        FROM posts
        WHERE enabled=1
    """)

    rows = cur.fetchall()
    con.close()

    for post_id, post_time in rows:
        schedule_post(application, post_id, post_time)

    print(f"📅 Loaded {len(rows)} scheduled posts.")


# =========================================================
# /START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        await update.message.reply_text(
            "❌ আপনি এই Bot-এর Admin নন।"
        )
        return

    text = """
🤖 RJ Team Auto Post Bot

👑 Admin: @RJteam1

📸 ছবি সহ Auto Post:
ছবি পাঠিয়ে Caption-এ লিখুন:

06:00|সুপ্রভাত 🌅

📝 শুধু Text Auto Post:
সরাসরি লিখুন:

01:50|🌙 শুভ রাত্রি 🌙

আরও উদাহরণ:

08:00|Good Morning ☀️
12:45|দুপুরের পোস্ট ❤️
17:00|বিকেলের শুভেচ্ছা 🌇
19:00|সন্ধ্যার পোস্ট 🌙

⏰ Bangladesh Time অনুযায়ী প্রতিদিন পোস্ট হবে।

📋 Commands:

/list
সব পোস্ট দেখুন

/delete ID
একটি পোস্ট Delete করুন

/clear
সব পোস্ট Delete করুন

/on
সব Auto Post চালু করুন

/off
সব Auto Post বন্ধ করুন

/help
Help দেখুন
"""

    await update.message.reply_text(text)


# =========================================================
# /HELP
# =========================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        return

    await update.message.reply_text(
        "📸 ছবি + Caption:\n"
        "06:00|সুপ্রভাত 🌅\n\n"
        "📝 শুধু Text:\n"
        "01:50|🌙 শুভ রাত্রি 🌙\n\n"
        "⏰ Bangladesh Time অনুযায়ী প্রতিদিন পোস্ট হবে।"
    )


# =========================================================
# SAVE POST
# =========================================================

async def save_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    photo_id="",
    raw_caption=None,
):
    if not is_admin(update.effective_user):
        await update.message.reply_text("❌ Admin only")
        return

    caption = raw_caption

    if caption is None:
        caption = update.message.caption or ""

    caption = caption.strip()

    if "|" not in caption:
        await update.message.reply_text(
            "❌ Format ভুল।\n\n"
            "সঠিক Format:\n"
            "06:00|সুপ্রভাত 🌅"
        )
        return

    post_time, post_caption = caption.split("|", 1)

    post_time = post_time.strip()
    post_caption = post_caption.strip()

    if validate_time(post_time) is None:
        await update.message.reply_text(
            "❌ সময় ভুল।\n\n"
            "সঠিক Format:\n"
            "06:00|সুপ্রভাত"
        )
        return

    if not post_caption:
        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO posts
        (post_time, photo_id, caption, enabled)
        VALUES (?, ?, ?, 1)
    """, (
        post_time,
        photo_id or "",
        post_caption,
    ))

    post_id = cur.lastrowid

    con.commit()
    con.close()

    schedule_post(
        context.application,
        post_id,
        post_time,
    )

    post_type = "📸 ছবি + Caption" if photo_id else "📝 Text"

    await update.message.reply_text(
        f"✅ Auto Post Save হয়েছে!\n\n"
        f"🆔 ID: {post_id}\n"
        f"📦 Type: {post_type}\n"
        f"⏰ সময়: {post_time}\n"
        f"📝 Caption: {post_caption}\n\n"
        f"🇧🇩 Bangladesh Time অনুযায়ী প্রতিদিন পোস্ট হবে।"
    )


# =========================================================
# RECEIVE PHOTO
# =========================================================

async def receive_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    photo = update.message.photo[-1]
    caption = update.message.caption

    if not caption:
        if not is_admin(update.effective_user):
            return

        await update.message.reply_text(
            "❌ Caption-এ সময় দিতে হবে।\n\n"
            "উদাহরণ:\n"
            "06:00|সুপ্রভাত 🌅"
        )
        return

    await save_post(
        update,
        context,
        photo_id=photo.file_id,
        raw_caption=caption,
    )


# =========================================================
# RECEIVE TEXT
# =========================================================

async def receive_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text or ""

    # Ignore normal messages that are not in schedule format.
    if "|" not in text:
        return

    await save_post(
        update,
        context,
        photo_id="",
        raw_caption=text,
    )


# =========================================================
# LIST
# =========================================================

async def list_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT id, post_time, caption, enabled, photo_id
        FROM posts
        ORDER BY post_time
    """)

    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text(
            "📭 এখন কোনো Auto Post নেই।"
        )
        return

    text = "📋 Auto Post Schedule\n\n"

    for post_id, post_time, caption, enabled, photo_id in rows:
        status = "🟢 ON" if enabled else "🔴 OFF"
        post_type = "📸 Photo" if photo_id else "📝 Text"

        text += (
            f"🆔 ID: {post_id}\n"
            f"📦 Type: {post_type}\n"
            f"⏰ Time: {post_time}\n"
            f"📌 Status: {status}\n"
            f"📝 {caption}\n"
            f"──────────────\n"
        )

    # Telegram message limit protection
    if len(text) <= 4000:
        await update.message.reply_text(text)
    else:
        chunk = ""
        for line in text.splitlines(True):
            if len(chunk) + len(line) > 4000:
                await update.message.reply_text(chunk)
                chunk = ""
            chunk += line

        if chunk:
            await update.message.reply_text(chunk)


# =========================================================
# DELETE
# =========================================================

async def delete_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user):
        return

    if not context.args:
        await update.message.reply_text(
            "ব্যবহার:\n\n"
            "/delete 3"
        )
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID সঠিক নয়।")
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "DELETE FROM posts WHERE id=?",
        (post_id,),
    )

    deleted = cur.rowcount

    con.commit()
    con.close()

    if deleted:
        for job in context.application.job_queue.jobs():
            if job.name == f"post_{post_id}":
                job.schedule_removal()

        await update.message.reply_text(
            f"🗑️ Post {post_id} Delete হয়েছে।"
        )
    else:
        await update.message.reply_text(
            "❌ এই ID পাওয়া যায়নি।"
        )


# =========================================================
# CLEAR ALL
# =========================================================

async def clear_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute("DELETE FROM posts")

    con.commit()
    con.close()

    remove_all_post_jobs(context.application)

    await update.message.reply_text(
        "🗑️ সব Auto Post Delete হয়েছে।"
    )


# =========================================================
# SEND POST
# =========================================================

async def send_post(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    post_id = data["id"]

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT photo_id, caption, enabled
        FROM posts
        WHERE id=?
    """, (post_id,))

    row = cur.fetchone()
    con.close()

    if not row:
        return

    photo_id, caption, enabled = row

    if not enabled:
        return

    try:
        if photo_id:
            await context.bot.send_photo(
                chat_id=TARGET_CHAT,
                photo=photo_id,
                caption=caption,
            )
        else:
            await context.bot.send_message(
                chat_id=TARGET_CHAT,
                text=caption,
            )

        print(f"✅ POST SENT: {post_id}")

    except Exception as e:
        print(f"❌ POST ERROR {post_id}: {e}")


# =========================================================
# /ON
# =========================================================

async def bot_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute("UPDATE posts SET enabled=1")

    con.commit()
    con.close()

    schedule_all(context.application)

    await update.message.reply_text(
        "🟢 Auto Post চালু হয়েছে!\n\n"
        "⏰ সব Schedule আবার Active।"
    )


# =========================================================
# /OFF
# =========================================================

async def bot_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute("UPDATE posts SET enabled=0")

    con.commit()
    con.close()

    remove_all_post_jobs(context.application)

    await update.message.reply_text(
        "🔴 Auto Post বন্ধ করা হয়েছে।"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    print(f"❌ Telegram error: {context.error}")


# =========================================================
# MAIN
# =========================================================

def main():
    init_db()

    start_health_server()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_posts))
    application.add_handler(CommandHandler("delete", delete_post))
    application.add_handler(CommandHandler("clear", clear_posts))
    application.add_handler(CommandHandler("on", bot_on))
    application.add_handler(CommandHandler("off", bot_off))

    # Photo posts
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_photo,
        )
    )

    # Text posts: only schedule-format messages containing "|"
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_text,
        )
    )

    application.add_error_handler(error_handler)

    # Load saved schedules
    schedule_all(application)

    print("🤖 RJ Team Auto Post Bot Started...")
    print(f"🎯 Target: {TARGET_CHAT_RAW}")
    print("🇧🇩 Timezone: Asia/Dhaka")

    application.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
