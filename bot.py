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

BOT_TOKEN = "YOUR_BOT_TOKEN"
CHANNEL_USERNAME = "@YOUR_CHANNEL"
ADMIN_ID = 123456789

TZ = ZoneInfo("Asia/Dhaka")
DB_NAME = "autopost.db"


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


def is_admin(user_id):
    return user_id == ADMIN_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ আপনি এই Bot-এর Admin নন।")
        return

    await update.message.reply_text(
        "🤖 Telegram Auto Post Bot\n\n"
        "ছবি পাঠিয়ে Caption-এ লিখুন:\n"
        "06:00|সুপ্রভাত 🌅\n\n"
        "কমান্ড:\n"
        "/list - সব পোস্ট\n"
        "/delete ID - পোস্ট মুছুন\n"
        "/clear - সব পোস্ট মুছুন\n"
        "/on - Auto Post চালু\n"
        "/off - Auto Post বন্ধ\n"
        "/help - Help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "📸 Format:\n06:00|আপনার Caption\n\n"
        "🇧🇩 সময়: Bangladesh Time (UTC+6)"
    )


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only")
        return

    photo = update.message.photo[-1]
    caption = update.message.caption

    if not caption or "|" not in caption:
        await update.message.reply_text(
            "❌ Format ভুল।\n\n06:00|সুপ্রভাত 🌅"
        )
        return

    post_time, post_caption = caption.split("|", 1)
    post_time = post_time.strip()
    post_caption = post_caption.strip()

    try:
        hour, minute = map(int, post_time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ সময় ভুল। উদাহরণ: 06:00")
        return

    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO posts (post_time, photo_id, caption, enabled) VALUES (?, ?, ?, 1)",
        (post_time, photo.file_id, post_caption),
    )
    post_id = cur.lastrowid
    con.commit()
    con.close()

    # নতুন পোস্টের schedule সঙ্গে সঙ্গে যোগ করা
    hour, minute = map(int, post_time.split(":"))
    context.application.job_queue.run_daily(
        send_post,
        time=time(hour=hour, minute=minute, tzinfo=TZ),
        days=tuple(range(7)),
        data={"id": post_id},
        name=f"post_{post_id}",
    )

    await update.message.reply_text(
        f"✅ পোস্ট Save হয়েছে!\n\n"
        f"🆔 ID: {post_id}\n"
        f"⏰ সময়: {post_time}\n"
        f"📝 {post_caption}\n"
        f"🇧🇩 Bangladesh Time"
    )


async def list_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, post_time, caption, enabled FROM posts ORDER BY post_time")
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("📭 কোনো Auto Post নেই।")
        return

    text = "📋 Auto Post Schedule\n\n"
    for post_id, post_time, caption, enabled in rows:
        status = "🟢 ON" if enabled else "🔴 OFF"
        text += f"🆔 {post_id}\n⏰ {post_time}\n📌 {status}\n📝 {caption}\n────────────\n"

    await update.message.reply_text(text)


async def delete_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("ব্যবহার: /delete 3")
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID সঠিক নয়।")
        return

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM posts WHERE id=?", (post_id,))
    deleted = cur.rowcount
    con.commit()
    con.close()

    # সংশ্লিষ্ট scheduled job বন্ধ
    for job in context.application.job_queue.get_jobs_by_name(f"post_{post_id}"):
        job.schedule_removal()

    await update.message.reply_text(
        f"🗑️ Post {post_id} Delete হয়েছে।" if deleted
        else "❌ এই ID পাওয়া যায়নি।"
    )


async def clear_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM posts")
    con.commit()
    con.close()

    for job in context.application.job_queue.jobs():
        job.schedule_removal()

    await update.message.reply_text("🗑️ সব Auto Post Delete হয়েছে।")


async def send_post(context: ContextTypes.DEFAULT_TYPE):
    post_id = context.job.data["id"]

    con = db()
    cur = con.cursor()
    cur.execute(
        "SELECT photo_id, caption, enabled FROM posts WHERE id=?",
        (post_id,),
    )
    row = cur.fetchone()
    con.close()

    if not row:
        return

    photo_id, caption, enabled = row
    if not enabled:
        return

    try:
        await context.bot.send_photo(
            chat_id=CHANNEL_USERNAME,
            photo=photo_id,
            caption=caption,
        )
        print(f"POST SENT: {post_id}")
    except Exception as e:
        print(f"POST ERROR: {e}")


def schedule_all(application):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, post_time FROM posts WHERE enabled=1")
    rows = cur.fetchall()
    con.close()

    for post_id, post_time in rows:
        hour, minute = map(int, post_time.split(":"))
        application.job_queue.run_daily(
            send_post,
            time=time(hour=hour, minute=minute, tzinfo=TZ),
            days=tuple(range(7)),
            data={"id": post_id},
            name=f"post_{post_id}",
        )


async def bot_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE posts SET enabled=1")
    con.commit()
    con.close()

    schedule_all(context.application)
    await update.message.reply_text("🟢 Auto Post চালু হয়েছে।")


async def bot_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    con = db()
    cur = con.cursor()
    cur.execute("UPDATE posts SET enabled=0")
    con.commit()
    con.close()

    for job in context.application.job_queue.jobs():
        job.schedule_removal()

    await update.message.reply_text("🔴 Auto Post বন্ধ হয়েছে।")


def main():
    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_posts))
    application.add_handler(CommandHandler("delete", delete_post))
    application.add_handler(CommandHandler("clear", clear_posts))
    application.add_handler(CommandHandler("on", bot_on))
    application.add_handler(CommandHandler("off", bot_off))
    application.add_handler(
        MessageHandler(filters.PHOTO, receive_photo)
    )

    schedule_all(application)

    print("🤖 Auto Post Bot Started...")
    application.run_polling()


if __name__ == "__main__":
    main()
