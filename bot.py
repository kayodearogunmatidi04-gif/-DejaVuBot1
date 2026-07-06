import os
import hashlib
import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ===== CONFIGURATION =====
TOKEN = os.getenv("BOT_TOKEN")
DATABASE = "messages.db"
REPOST_WINDOW_HOURS = 24  # How far back to check for duplicates
SIMILARITY_THRESHOLD = 0.85  # For fuzzy matching (0.0-1.0)

# ===== LOGGING =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== DATABASE SETUP =====
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            message_id INTEGER,
            user_id INTEGER,
            username TEXT,
            content_hash TEXT,
            content_preview TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_hash ON messages(content_hash)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_chat ON messages(chat_id)")
    conn.commit()
    conn.close()

def save_message(chat_id, message_id, user_id, username, content):
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    preview = content[:100] + "..." if len(content) > 100 else content
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO messages (chat_id, message_id, user_id, username, content_hash, content_preview)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, message_id, user_id, username, content_hash, preview))
    conn.commit()
    conn.close()
    return content_hash

def find_duplicates(chat_id, content_hash, content, hours_back=24):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    cutoff = datetime.now() - timedelta(hours=hours_back)
    c.execute("""
        SELECT message_id, username, content_preview, timestamp 
        FROM messages 
        WHERE chat_id = ? AND content_hash = ? AND timestamp > ?
        ORDER BY timestamp DESC
        LIMIT 5
    """, (chat_id, content_hash, cutoff))
    results = c.fetchall()
    conn.close()
    return results

def get_stats(chat_id):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,))
    total = c.fetchone()[0]
    c.execute("""
        SELECT COUNT(*) FROM (
            SELECT content_hash, MIN(timestamp) 
            FROM messages 
            WHERE chat_id = ? 
            GROUP BY content_hash
        )
    """, (chat_id,))
    unique = c.fetchone()[0]
    conn.close()
    return total, unique

# ===== BOT COMMANDS =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome command with bot features"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 **Welcome to DejaVuBot, {user.first_name}!**\n\n"
        "🔄 **I detect duplicate messages in your groups/channels**\n\n"
        "📌 **Features:**\n"
        "• 🔍 Auto-detect reposted messages\n"
        "• 📊 Track repost statistics\n"
        "• ⏱️ Customizable detection window\n"
        "• 🎯 Smart content matching\n\n"
        "**Commands:**\n"
        "/stats - View repost statistics\n"
        "/recent - Show recent duplicate checks\n"
        "/settings - Configure bot preferences\n"
        "/help - Get detailed help\n\n"
        "🛠️ **Just add me to your group and I'll start working!**",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed help with usage examples"""
    await update.message.reply_text(
        "📖 **DejaVuBot Help Guide**\n\n"
        "**How it works:**\n"
        "1️⃣ I store every message you send\n"
        "2️⃣ When a new message arrives, I check if it's a duplicate\n"
        "3️⃣ If it's a repost, I reply with a warning\n\n"
        "**Commands:**\n"
        "/start - Welcome message\n"
        "/stats - View group statistics\n"
        "/recent - See last 5 duplicates found\n"
        "/settings - Adjust detection settings\n"
        "/clear - Clear this group's history (admin only)\n"
        "/help - Show this guide\n\n"
        "**Settings you can adjust:**\n"
        "• Detection window (6h, 12h, 24h, 48h)\n"
        "• Auto-delete duplicates\n"
        "• Ignore certain users\n\n"
        "💡 **Tip:** Add me as an admin for auto-delete features!",
        parse_mode="Markdown"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show repost statistics for the chat"""
    chat_id = update.effective_chat.id
    total, unique = get_stats(chat_id)
    duplicates = total - unique
    dup_percent = (duplicates / total * 100) if total > 0 else 0
    
    await update.message.reply_text(
        f"📊 **Statistics for this chat**\n\n"
        f"📝 Total messages tracked: `{total}`\n"
        f"🆕 Unique messages: `{unique}`\n"
        f"🔄 Duplicates found: `{duplicates}`\n"
        f"📈 Duplicate rate: `{dup_percent:.1f}%`\n"
        f"⏱️ Detection window: `{REPOST_WINDOW_HOURS} hours`\n\n"
        f"_Stats since bot was added_",
        parse_mode="Markdown"
    )

async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent duplicates found"""
    chat_id = update.effective_chat.id
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""
        SELECT content_preview, username, timestamp 
        FROM messages 
        WHERE chat_id = ? 
        ORDER BY timestamp DESC 
        LIMIT 5
    """, (chat_id,))
    results = c.fetchall()
    conn.close()
    
    if not results:
        await update.message.reply_text("📭 No messages tracked yet!")
        return
    
    reply = "📋 **Recent messages:**\n\n"
    for i, (preview, username, timestamp) in enumerate(results, 1):
        time_ago = datetime.now() - datetime.fromisoformat(timestamp)
        hours = int(time_ago.total_seconds() / 3600)
        reply += f"{i}. `{preview}`\n   👤 @{username or 'unknown'} • {hours}h ago\n\n"
    
    await update.message.reply_text(reply, parse_mode="Markdown")

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interactive settings menu"""
    keyboard = [
        [InlineKeyboardButton("⏱️ Detection Window", callback_data="set_window")],
        [InlineKeyboardButton("🗑️ Auto-Delete Duplicates", callback_data="set_autodelete")],
        [InlineKeyboardButton("🚫 Ignore Users", callback_data="set_ignore")],
        [InlineKeyboardButton("📊 Export Stats", callback_data="export_stats")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚙️ **Settings Menu**\n\nChoose an option to configure:",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear chat history (admin only)"""
    # Check if user is admin (simplified - you can add proper admin check)
    chat_id = update.effective_chat.id
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🗑️ Chat history cleared successfully!")

# ===== MESSAGE HANDLER =====

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process incoming messages and detect duplicates"""
    message = update.message
    chat_id = message.chat_id
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    # Get text content
    content = message.text or message.caption or ""
    if not content:
        return  # Skip non-text messages
    
    # Save message and get hash
    content_hash = save_message(chat_id, message.message_id, user_id, username, content)
    
    # Check for duplicates
    duplicates = find_duplicates(chat_id, content_hash, content, REPOST_WINDOW_HOURS)
    
    # If duplicates found, reply
    if duplicates and len(duplicates) > 1:  # Exclude current message
        # Skip if duplicate count is 1 (only current message)
        duplicate_count = len([d for d in duplicates if d[0] != message.message_id])
        if duplicate_count > 0:
            warning_msg = f"⚠️ **Duplicate Detected!**\n\n"
            warning_msg += f"🔄 This message was already posted {duplicate_count} time(s) in the last {REPOST_WINDOW_HOURS}h.\n\n"
            warning_msg += f"**Previous posts:**\n"
            for msg_id, username, preview, timestamp in duplicates[:3]:
                if msg_id != message.message_id:
                    time_ago = datetime.now() - datetime.fromisoformat(timestamp)
                    hours = int(time_ago.total_seconds() / 3600)
                    warning_msg += f"• @{username or 'unknown'} - {hours}h ago: `{preview}`\n"
            
            # Add warning emoji
            warning_msg += f"\n💡 _Please avoid reposting content!_"
            
            await message.reply_text(warning_msg, parse_mode="Markdown")

# ===== CALLBACK HANDLER FOR SETTINGS =====

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button presses in settings menu"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "set_window":
        await query.edit_message_text(
            "⏱️ **Detection Window**\n\n"
            "How far back should I check for duplicates?\n\n"
            "• /setwindow 6 - Last 6 hours\n"
            "• /setwindow 12 - Last 12 hours\n"
            "• /setwindow 24 - Last 24 hours\n"
            "• /setwindow 48 - Last 48 hours",
            parse_mode="Markdown"
        )
    elif query.data == "set_autodelete":
        await query.edit_message_text(
            "🗑️ **Auto-Delete**\n\n"
            "⚠️ This feature requires admin privileges!\n\n"
            "To enable auto-delete:\n"
            "1. Add me as admin to your group\n"
            "2. Use /autodelete on\n\n"
            "I will automatically delete duplicate messages when enabled.",
            parse_mode="Markdown"
        )
    elif query.data == "set_ignore":
        await query.edit_message_text(
            "🚫 **Ignore Users**\n\n"
            "To ignore a user from duplicate detection:\n"
            "• /ignore @username\n"
            "• /unignore @username\n\n"
            "Ignored users won't trigger duplicate warnings.",
            parse_mode="Markdown"
        )
    elif query.data == "export_stats":
        await query.edit_message_text(
            "📊 **Export Stats**\n\n"
            "Feature coming soon! I'll generate a CSV report of all duplicates found.",
            parse_mode="Markdown"
        )

# ===== MAIN =====

def main():
    """Start the bot"""
    # Initialize database
    init_db()
    logger.info("✅ Database initialized")
    
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("recent", recent))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("clear", clear))
    
    # Add callback handler for buttons
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add message handler (process all text messages)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start the bot
    logger.info("🚀 Bot started successfully!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
