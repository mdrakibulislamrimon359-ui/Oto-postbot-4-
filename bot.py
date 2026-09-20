import sqlite3
from datetime import time
from zoneinfo import ZoneInfo

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

BOT_TOKEN = "YOUR_BOT_TOKEN"

# যে Channel-এ পোস্ট হবে
CHANNEL_USERNAME = "@YOUR_CHANNEL"

# Admin Telegram Username
ADMIN_USERNAME = "RJteam1"

# Bangladesh Time
TZ = ZoneInfo("Asia/Dhaka")

# Database
DB_NAME = "autopost.db"


# =========================================================
# DATABASE
# =========================================================

def db():
    return sqlite3.connect(DB_NAME)


def init_db():

    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_time TEXT NOT NULL,
            photo_id TEXT NOT NULL,
            caption TEXT NOT NULL,
            enabled INTEGER DEFAULT 1
        )
    """)

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
# REMOVE SCHEDULED JOBS
# =========================================================

def remove_all_post_jobs(application):

    for job in application.job_queue.jobs():

        if job.name and job.name.startswith("post_"):
            job.schedule_removal()


# =========================================================
# SCHEDULE ONE POST
# =========================================================

def schedule_post(application, post_id, post_time):

    try:

        hour, minute = map(
            int,
            post_time.split(":")
        )

    except ValueError:

        print(
            f"Invalid time for post {post_id}: {post_time}"
        )

        return

    post_time_obj = time(
        hour=hour,
        minute=minute,
        tzinfo=TZ
    )

    job_name = f"post_{post_id}"

    # Duplicate job prevent
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


# =========================================================
# SCHEDULE ALL POSTS
# =========================================================

def schedule_all(application):

    # Remove old jobs first
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

        schedule_post(
            application,
            post_id,
            post_time
        )

    print(
        f"Loaded {len(rows)} scheduled posts."
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):

        await update.message.reply_text(
            "❌ আপনি এই Bot-এর Admin নন।"
        )

        return

    text = """
🤖 Telegram Auto Post Bot

👑 Admin: @RJteam1

📸 Auto Post তৈরি করতে ছবি পাঠান
এবং Caption এ লিখুন:

06:00|সুপ্রভাত 🌅

08:00|Good Morning ☀️

12:45|দুপুরের পোস্ট ❤️

17:00|বিকেলের শুভেচ্ছা 🌇

19:00|সন্ধ্যার পোস্ট 🌙


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


🇧🇩 Timezone: Bangladesh
"""

    await update.message.reply_text(text)


# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):
        return

    await update.message.reply_text(
        "📸 ছবি পাঠিয়ে Caption এ লিখুন:\n\n"
        "06:00|সুপ্রভাত 🌅\n\n"
        "⏰ Bangladesh Time অনুযায়ী "
        "প্রতিদিন পোস্ট হবে।"
    )


# =========================================================
# RECEIVE PHOTO
# =========================================================

async def receive_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):

        await update.message.reply_text(
            "❌ Admin only"
        )

        return

    photo = update.message.photo[-1]

    caption = update.message.caption

    # Caption নেই
    if not caption:

        await update.message.reply_text(
            "❌ Caption এ সময় দিতে হবে।\n\n"
            "উদাহরণ:\n"
            "06:00|সুপ্রভাত 🌅"
        )

        return

    # | নেই
    if "|" not in caption:

        await update.message.reply_text(
            "❌ Format ভুল।\n\n"
            "সঠিক Format:\n"
            "06:00|সুপ্রভাত 🌅"
        )

        return

    post_time, post_caption = caption.split(
        "|",
        1
    )

    post_time = post_time.strip()
    post_caption = post_caption.strip()

    # Time check
    try:

        hour, minute = map(
            int,
            post_time.split(":")
        )

        if not (
            0 <= hour <= 23
            and
            0 <= minute <= 59
        ):

            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ সময় ভুল।\n\n"
            "সঠিক Format:\n"
            "06:00|সুপ্রভাত"
        )

        return

    # Caption check
    if not post_caption:

        await update.message.reply_text(
            "❌ Caption খালি রাখা যাবে না।"
        )

        return

    # Save database
    con = db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO posts
        (
            post_time,
            photo_id,
            caption,
            enabled
        )
        VALUES (?, ?, ?, 1)
    """, (
        post_time,
        photo.file_id,
        post_caption
    ))

    post_id = cur.lastrowid

    con.commit()
    con.close()

    # Immediately schedule
    schedule_post(
        context.application,
        post_id,
        post_time
    )

    await update.message.reply_text(
        f"✅ Auto Post Save হয়েছে!\n\n"
        f"🆔 ID: {post_id}\n"
        f"⏰ সময়: {post_time}\n"
        f"📝 Caption: {post_caption}\n\n"
        f"🇧🇩 Bangladesh Time অনুযায়ী "
        f"প্রতিদিন পোস্ট হবে।"
    )


# =========================================================
# LIST
# =========================================================

async def list_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            id,
            post_time,
            caption,
            enabled
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

    for post_id, post_time, caption, enabled in rows:

        status = (
            "🟢 ON"
            if enabled
            else
            "🔴 OFF"
        )

        text += (
            f"🆔 ID: {post_id}\n"
            f"⏰ Time: {post_time}\n"
            f"📌 Status: {status}\n"
            f"📝 {caption}\n"
            f"──────────────\n"
        )

    await update.message.reply_text(text)


# =========================================================
# DELETE
# =========================================================

async def delete_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
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

    if deleted:

        # Remove scheduled job
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
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "DELETE FROM posts"
    )

    con.commit()
    con.close()

    # Remove all jobs
    remove_all_post_jobs(
        context.application
    )

    await update.message.reply_text(
        "🗑️ সব Auto Post Delete হয়েছে।"
    )


# =========================================================
# SEND POST
# =========================================================

async def send_post(
    context: ContextTypes.DEFAULT_TYPE
):

    data = context.job.data

    post_id = data["id"]

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            photo_id,
            caption,
            enabled
        FROM posts
        WHERE id=?
    """, (
        post_id,
    ))

    row = cur.fetchone()

    con.close()

    if not row:
        return

    photo_id, caption, enabled = row

    # Disabled
    if not enabled:
        return

    try:

        await context.bot.send_photo(
            chat_id=CHANNEL_USERNAME,
            photo=photo_id,
            caption=caption
        )

        print(
            f"✅ POST SENT: {post_id}"
        )

    except Exception as e:

        print(
            f"❌ POST ERROR: {e}"
        )


# =========================================================
# ON
# =========================================================

async def bot_on(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE posts SET enabled=1"
    )

    con.commit()
    con.close()

    # Re-create schedules
    schedule_all(
        context.application
    )

    await update.message.reply_text(
        "🟢 Auto Post চালু হয়েছে!\n\n"
        "⏰ সব Schedule আবার Active।"
    )


# =========================================================
# OFF
# =========================================================

async def bot_off(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update.effective_user):
        return

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE posts SET enabled=0"
    )

    con.commit()
    con.close()

    # Remove schedules
    remove_all_post_jobs(
        context.application
    )

    await update.message.reply_text(
        "🔴 Auto Post বন্ধ করা হয়েছে।"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    # Database তৈরি
    init_db()

    # Bot
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
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

    # Photo
    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            receive_photo
        )
    )

    # Load saved schedules
    schedule_all(
        application
    )

    print(
        "🤖 Auto Post Bot Started..."
    )

    application.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
