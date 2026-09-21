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

# আপনার Target Group / Channel
# Render Environment-এ চাইলে পরিবর্তন করতে পারবেন
TARGET_CHAT_RAW = (
    os.getenv("TARGET_CHAT_ID")
    or os.getenv("CHANNEL_USERNAME")
    or "@RJteam123890"
)

# Private chat-এ Admin হিসেবে ব্যবহার করবেন
ADMIN_USERNAME = os.getenv(
    "ADMIN_USERNAME",
    "RJteam1"
).strip().lstrip("@")

# Bangladesh Time
TZ = ZoneInfo("Asia/Dhaka")

# Database
DB_NAME = "autopost.db"

# Render port
PORT = int(os.getenv("PORT", "10000"))


# =========================================================
# CONFIG CHECK
# =========================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "❌ BOT_TOKEN পাওয়া যায়নি। "
        "Render Environment-এ BOT_TOKEN দিন।"
    )

if not TARGET_CHAT_RAW:
    raise RuntimeError(
        "❌ TARGET_CHAT_ID অথবা CHANNEL_USERNAME পাওয়া যায়নি।"
    )


# =========================================================
# TARGET CHAT
# =========================================================

def get_target_chat():

    value = TARGET_CHAT_RAW.strip()

    # -100xxxxxxxxxx হলে Group/Channel ID
    if value.lstrip("-").isdigit():
        return int(value)

    # @username হলে username
    return value


TARGET_CHAT = get_target_chat()


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"RJ Auto Post Bot is running!"
        )

    def do_HEAD(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

    def log_message(self, format, *args):
        return


def run_health_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(
        f"🌐 Health Server running on port {PORT}"
    )

    server.serve_forever()


def start_health_server():

    thread = Thread(
        target=run_health_server,
        daemon=True
    )

    thread.start()


# =========================================================
# DATABASE
# =========================================================

def db():

    con = sqlite3.connect(
        DB_NAME
    )

    con.execute(
        "PRAGMA busy_timeout = 5000"
    )

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

    con.commit()

    con.close()


# =========================================================
# ADMIN CHECK
# =========================================================

async def is_admin(
    update,
    context
):

    user = update.effective_user
    chat = update.effective_chat

    if not user or not chat:
        return False

    # -----------------------------------------
    # GROUP / SUPERGROUP
    # -----------------------------------------

    if chat.type in (
        "group",
        "supergroup"
    ):

        try:

            member = await context.bot.get_chat_member(
                chat_id=chat.id,
                user_id=user.id
            )

            return member.status in (
                "administrator",
                "creator"
            )

        except Exception as e:

            print(
                "❌ ADMIN CHECK ERROR:",
                repr(e)
            )

            return False

    # -----------------------------------------
    # PRIVATE CHAT
    # -----------------------------------------

    username = user.username

    if not username:
        return False

    return (
        username.lower()
        ==
        ADMIN_USERNAME.lower()
    )


# =========================================================
# TIME VALIDATION
# =========================================================

def validate_time(
    post_time
):

    try:

        hour, minute = map(
            int,
            post_time.strip().split(":")
        )

        if not (
            0 <= hour <= 23
            and
            0 <= minute <= 59
        ):

            return None

        return time(
            hour=hour,
            minute=minute,
            tzinfo=TZ
        )

    except (
        ValueError,
        TypeError
    ):

        return None


# =========================================================
# REMOVE ALL JOBS
# =========================================================

def remove_all_post_jobs(
    application
):

    if not application.job_queue:
        return

    for job in application.job_queue.jobs():

        if (
            job.name
            and
            job.name.startswith("post_")
        ):

            job.schedule_removal()


# =========================================================
# SCHEDULE ONE POST
# =========================================================

def schedule_post(
    application,
    post_id,
    post_time
):

    if not application.job_queue:

        print(
            "❌ JobQueue পাওয়া যায়নি!"
        )

        return False

    post_time_obj = validate_time(
        post_time
    )

    if post_time_obj is None:

        print(
            f"❌ Invalid time: {post_time}"
        )

        return False

    job_name = f"post_{post_id}"

    # Duplicate job remove
    for job in application.job_queue.jobs():

        if job.name == job_name:

            job.schedule_removal()

    application.job_queue.run_daily(

        send_post,

        time=post_time_obj,

        days=tuple(range(7)),

        data={
            "id": post_id
        },

        name=job_name
    )

    print(
        f"✅ SCHEDULED | ID={post_id} "
        f"| TIME={post_time} "
        f"| BD TIME"
    )

    return True


# =========================================================
# LOAD ALL SCHEDULES
# =========================================================

def schedule_all(
    application
):

    remove_all_post_jobs(
        application
    )

    con = db()

    cur = con.cursor()

    cur.execute("""
        SELECT
            id,
            post_time
        FROM posts
        WHERE enabled=1
    """)

    rows = cur.fetchall()

    con.close()

    for post_id, post_time in rows:

        schedule_post(
            application,
            post_id,
            post_time
        )

    print(
        f"📅 Loaded {len(rows)} schedules"
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await is_admin(
        update,
        context
    ):

        await update.message.reply_text(
            "❌ শুধু Admin এই Bot ব্যবহার করতে পারবেন।"
        )

        return

    await update.message.reply_text(

        "🤖 RJ Team Auto Post Bot\n\n"

        "📸 Photo Auto Post:\n"
        "06:00|সুপ্রভাত 🌅\n\n"

        "📝 Text Auto Post:\n"
        "19:00|শুভ সন্ধ্যা 🌙\n\n"

        "📋 Commands:\n\n"

        "/list - Schedule দেখুন\n"
        "/test - এখনই Test Post\n"
        "/on - Auto Post চালু\n"
        "/off - Auto Post বন্ধ\n"
        "/delete ID - Post Delete\n"
        "/clear - সব Post Delete\n"
        "/help - Help\n\n"

        "🇧🇩 Bangladesh Time অনুযায়ী Post হবে।"
    )


# =========================================================
# HELP
# =========================================================

async def help_command(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    await update.message.reply_text(

        "📸 Photo:\n\n"
        "06:00|সুপ্রভাত 🌅\n\n"

        "📝 Text:\n\n"
        "19:00|শুভ সন্ধ্যা 🌙\n\n"

        "🧪 /test\n"
        "Target Group/Channel-এ এখনই Test Post পাঠাবে।\n\n"

        "📋 /list\n"
        "সব Schedule দেখাবে।"
    )


# =========================================================
# SAVE POST
# =========================================================

async def save_post(
    update,
    context,
    photo_id="",
    raw_caption=None
):

    if not await is_admin(
        update,
        context
    ):

        await update.message.reply_text(
            "❌ শুধু Admin এই Bot ব্যবহার করতে পারবেন।"
        )

        return

    if raw_caption is None:

        raw_caption = (
            update.message.caption
            or ""
        )

    text = raw_caption.strip()

    # Format check
    if "|" not in text:

        await update.message.reply_text(

            "❌ Format ভুল!\n\n"

            "সঠিক Format:\n"

            "06:00|সুপ্রভাত 🌅"
        )

        return

    post_time, post_caption = text.split(
        "|",
        1
    )

    post_time = post_time.strip()

    post_caption = post_caption.strip()

    # Time check
    if validate_time(
        post_time
    ) is None:

        await update.message.reply_text(

            "❌ সময় ভুল!\n\n"

            "উদাহরণ:\n"

            "06:00|সুপ্রভাত"
        )

        return

    # Caption check
    if not post_caption:

        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )

        return

    # Save DB
    con = db()

    cur = con.cursor()

    cur.execute(
        """
        INSERT INTO posts
        (
            post_time,
            photo_id,
            caption,
            enabled
        )
        VALUES (?, ?, ?, 1)
        """,
        (
            post_time,
            photo_id or "",
            post_caption
        )
    )

    post_id = cur.lastrowid

    con.commit()

    con.close()

    # Schedule
    scheduled = schedule_post(
        context.application,
        post_id,
        post_time
    )

    post_type = (
        "📸 Photo"
        if photo_id
        else
        "📝 Text"
    )

    status = (
        "🟢 Schedule Active"
        if scheduled
        else
        "🔴 Schedule হয়নি"
    )

    await update.message.reply_text(

        "✅ Auto Post Save হয়েছে!\n\n"

        f"🆔 ID: {post_id}\n"

        f"📦 Type: {post_type}\n"

        f"⏰ Time: {post_time}\n"

        f"📌 Status: {status}\n\n"

        f"📝 {post_caption}\n\n"

        "🇧🇩 Bangladesh Time অনুযায়ী প্রতিদিন Post হবে।"
    )


# =========================================================
# PHOTO MESSAGE
# =========================================================

async def receive_photo(
    update,
    context
):

    caption = (
        update.message.caption
        or ""
    )

    if not caption:

        if await is_admin(
            update,
            context
        ):

            await update.message.reply_text(

                "❌ ছবির Caption-এ সময় দিতে হবে।\n\n"

                "উদাহরণ:\n"

                "06:00|সুপ্রভাত 🌅"
            )

        return

    photo = (
        update.message.photo[-1]
    )

    await save_post(

        update,

        context,

        photo_id=photo.file_id,

        raw_caption=caption
    )


# =========================================================
# TEXT MESSAGE
# =========================================================

async def receive_text(
    update,
    context
):

    text = (
        update.message.text
        or ""
    )

    if "|" not in text:
        return

    await save_post(

        update,

        context,

        photo_id="",

        raw_caption=text
    )


# =========================================================
# LIST
# =========================================================

async def list_posts(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    con = db()

    cur = con.cursor()

    cur.execute("""
        SELECT
            id,
            post_time,
            caption,
            enabled,
            photo_id
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

    text = (
        "📋 Auto Post Schedule\n\n"
    )

    for (
        post_id,
        post_time,
        caption,
        enabled,
        photo_id
    ) in rows:

        status = (
            "🟢 ON"
            if enabled
            else
            "🔴 OFF"
        )

        post_type = (
            "📸 Photo"
            if photo_id
            else
            "📝 Text"
        )

        text += (

            f"🆔 ID: {post_id}\n"

            f"📦 Type: {post_type}\n"

            f"⏰ Time: {post_time}\n"

            f"📌 Status: {status}\n"

            f"📝 {caption}\n"

            "──────────────\n"
        )

    # Telegram limit
    if len(text) <= 4000:

        await update.message.reply_text(
            text
        )

    else:

        chunk = ""

        for line in text.splitlines(
            keepends=True
        ):

            if (
                len(chunk)
                +
                len(line)
                >
                4000
            ):

                await update.message.reply_text(
                    chunk
                )

                chunk = ""

            chunk += line

        if chunk:

            await update.message.reply_text(
                chunk
            )


# =========================================================
# TEST POST
# =========================================================

async def test_post(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        await update.message.reply_text(
            "❌ শুধু Admin Test করতে পারবেন।"
        )

        return

    try:

        bot_info = (
            await context.bot.get_me()
        )

        print(
            f"🤖 BOT: @{bot_info.username}"
        )

        print(
            f"🎯 TARGET: {TARGET_CHAT_RAW}"
        )

        # Test message
        await context.bot.send_message(

            chat_id=TARGET_CHAT,

            text=(
                "🧪 TEST POST\n\n"

                "✅ RJ Team Auto Post Bot কাজ করছে!\n\n"

                "🇧🇩 Bangladesh Time"
            )
        )

        print(
            "✅ TEST POST SENT"
        )

        await update.message.reply_text(

            "✅ Test Post পাঠানো হয়েছে!\n\n"

            f"🎯 Target: {TARGET_CHAT_RAW}"
        )

    except Exception as e:

        print(
            "❌ TEST POST ERROR:",
            repr(e)
        )

        await update.message.reply_text(

            "❌ Test Post পাঠানো যায়নি।\n\n"

            f"Error:\n{e}"
        )


# =========================================================
# DELETE
# =========================================================

async def delete_post(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    if not context.args:

        await update.message.reply_text(
            "ব্যবহার:\n\n/delete 1"
        )

        return

    try:

        post_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ ID সঠিক নয়।"
        )

        return

    con = db()

    cur = con.cursor()

    cur.execute(
        "DELETE FROM posts WHERE id=?",
        (post_id,)
    )

    deleted = cur.rowcount

    con.commit()

    con.close()

    # Remove scheduled job
    if context.application.job_queue:

        for job in (
            context.application
            .job_queue
            .jobs()
        ):

            if job.name == (
                f"post_{post_id}"
            ):

                job.schedule_removal()

    if deleted:

        await update.message.reply_text(

            f"🗑️ Post {post_id} Delete হয়েছে।"
        )

    else:

        await update.message.reply_text(
            "❌ এই ID পাওয়া যায়নি।"
        )


# =========================================================
# CLEAR
# =========================================================

async def clear_posts(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    con = db()

    con.execute(
        "DELETE FROM posts"
    )

    con.commit()

    con.close()

    remove_all_post_jobs(
        context.application
    )

    await update.message.reply_text(
        "🗑️ সব Auto Post Delete হয়েছে।"
    )


# =========================================================
# SEND SCHEDULED POST
# =========================================================

async def send_post(
    context
):

    data = context.job.data

    post_id = data["id"]

    print(
        f"⏰ RUNNING POST ID: {post_id}"
    )

    con = db()

    cur = con.cursor()

    cur.execute(
        """
        SELECT
            photo_id,
            caption,
            enabled
        FROM posts
        WHERE id=?
        """,
        (post_id,)
    )

    row = cur.fetchone()

    con.close()

    if not row:

        print(
            f"❌ POST {post_id} NOT FOUND"
        )

        return

    photo_id, caption, enabled = row

    if not enabled:

        print(
            f"🔴 POST {post_id} OFF"
        )

        return

    try:

        # Photo post
        if photo_id:

            await context.bot.send_photo(

                chat_id=TARGET_CHAT,

                photo=photo_id,

                caption=caption
            )

        # Text post
        else:

            await context.bot.send_message(

                chat_id=TARGET_CHAT,

                text=caption
            )

        print(
            f"✅ POST SENT SUCCESSFULLY: {post_id}"
        )

    except Exception as e:

        print(
            f"❌ POST ERROR {post_id}:",
            repr(e)
        )


# =========================================================
# ON
# =========================================================

async def bot_on(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    con = db()

    con.execute(
        "UPDATE posts SET enabled=1"
    )

    con.commit()

    con.close()

    schedule_all(
        context.application
    )

    await update.message.reply_text(

        "🟢 Auto Post চালু হয়েছে!\n\n"

        "⏰ সব Schedule Active।"
    )


# =========================================================
# OFF
# =========================================================

async def bot_off(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    con = db()

    con.execute(
        "UPDATE posts SET enabled=0"
    )

    con.commit()

    con.close()

    remove_all_post_jobs(
        context.application
    )

    await update.message.reply_text(
        "🔴 Auto Post বন্ধ হয়েছে।"
    )


# =========================================================
# STATUS
# =========================================================

async def status(
    update,
    context
):

    if not await is_admin(
        update,
        context
    ):

        return

    con = db()

    count = con.execute(
        "SELECT COUNT(*) FROM posts WHERE enabled=1"
    ).fetchone()[0]

    con.close()

    job_count = 0

    if context.application.job_queue:

        job_count = len([
            j
            for j in
            context.application.job_queue.jobs()
            if (
                j.name
                and
                j.name.startswith("post_")
            )
        ])

    await update.message.reply_text(

        "🤖 Bot Status\n\n"

        "🟢 Bot: Running\n"

        f"📋 Active Posts: {count}\n"

        f"⏰ Scheduled Jobs: {job_count}\n"

        f"🎯 Target: {TARGET_CHAT_RAW}\n"

        "🇧🇩 Timezone: Asia/Dhaka"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update,
    context
):

    print(
        "❌ TELEGRAM ERROR:",
        repr(context.error)
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "================================="
    )

    print(
        "🤖 RJ TEAM AUTO POST BOT"
    )

    print(
        "================================="
    )

    print(
        f"🎯 TARGET: {TARGET_CHAT_RAW}"
    )

    print(
        f"👑 ADMIN: @{ADMIN_USERNAME}"
    )

    print(
        "🇧🇩 TIMEZONE: Asia/Dhaka"
    )

    # Database
    init_db()

    # Render server
    start_health_server()

    # Telegram Application
    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # -------------------------------
    # COMMANDS
    # -------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "list",
            list_posts
        )
    )

    application.add_handler(
        CommandHandler(
            "test",
            test_post
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    application.add_handler(
        CommandHandler(
            "delete",
            delete_post
        )
    )

    application.add_handler(
        CommandHandler(
            "clear",
            clear_posts
        )
    )

    application.add_handler(
        CommandHandler(
            "on",
            bot_on
        )
    )

    application.add_handler(
        CommandHandler(
            "off",
            bot_off
        )
    )

    # -------------------------------
    # PHOTO
    # -------------------------------

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_photo
        )
    )

    # -------------------------------
    # TEXT
    # -------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT
            &
            ~filters.COMMAND,
            receive_text
        )
    )

    # Error handler
    application.add_error_handler(
        error_handler
    )

    # Load schedules
    schedule_all(
        application
    )

    print(
        "================================="
    )

    if application.job_queue:

        print(
            "✅ JOB QUEUE: READY"
        )

    else:

        print(
            "❌ JOB QUEUE: NOT AVAILABLE"
        )

    print(
        "🤖 BOT STARTING..."
    )

    print(
        "================================="
    )

    # Start bot
    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
