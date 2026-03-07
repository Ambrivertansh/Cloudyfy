import logging
import ssl
from urllib.parse import urlparse
import pg8000.dbapi
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, Application
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import os
import re
import random
import string

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- SECURE CREDENTIALS ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_URL = os.environ.get("DATABASE_URL")

FEEDBACK_SIG = "\n\n*(Suggestion are accepted send the suggestions to @Acchoro)*"
# -----------------------------------------------

MEDIA_GROUP_CAPTIONS = {}

def get_db_connection():
    url = urlparse(DB_URL)
    ssl_context = ssl.create_default_context()
    return pg8000.dbapi.connect(
        user=url.username, password=url.password,
        host=url.hostname, database=url.path[1:], ssl_context=ssl_context
    )

def upgrade_database_schema():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id SERIAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                download_count INTEGER DEFAULT 0
            );
        ''')
        cursor.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP DEFAULT NULL;")
        cursor.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS owner_id BIGINT DEFAULT 0;")
        cursor.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS short_code TEXT;")
        cursor.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS file_size BIGINT DEFAULT 0;")
        
        cursor.execute("UPDATE files SET short_code = substring(md5(random()::text) from 1 for 6) WHERE short_code IS NULL;")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                full_name TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        conn.commit()
    except Exception as e:
        print(f"Database setup error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

def auto_clean_trash():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM files WHERE deleted_at <= NOW() - INTERVAL '30 days'")
        conn.commit()
    except Exception: pass
    finally:
        cursor.close()
        conn.close()

def is_registered(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row is not None

def register_user(user_id, full_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, full_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, full_name))
    conn.commit()
    cursor.close()
    conn.close()

def save_to_cloud(file_id, file_name, file_type, owner_id, file_size):
    conn = get_db_connection()
    cursor = conn.cursor()
    new_code = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    
    cursor.execute('''
        INSERT INTO files (file_id, file_name, file_type, owner_id, short_code, file_size)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (file_id, file_name, file_type, owner_id, new_code, file_size))
    conn.commit()
    cursor.close()
    conn.close()

def format_bytes(size):
    if not size: return "0 B"
    try:
        size = float(size)
    except Exception:
        return "0 B"
        
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Family Cloud is awake!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

async def post_init(application: Application):
    upgrade_database_schema() 

# ==========================================
#         GUI MENU BUILDERS
# ==========================================
def get_main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("🕒 Recent Uploads", callback_data='p_rc_1_none')],
        [InlineKeyboardButton("📁 Categories ➡️", callback_data='menu_categories'),
         InlineKeyboardButton("🗂️ Folders ➡️", callback_data='menu_folders')],
        [InlineKeyboardButton("📅 Search by Time ➡️", callback_data='menu_dates')],
        [InlineKeyboardButton("🗑️ Recycle Bin (Trash) ➡️", callback_data='menu_trash')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_categories_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("🎥 Videos", callback_data='p_ct_1_Video'),
         InlineKeyboardButton("📸 Images", callback_data='p_ct_1_Image')],
        [InlineKeyboardButton("🎵 Audio", callback_data='p_ct_1_Audio'),
         InlineKeyboardButton("📦 ZIPs", callback_data='p_ct_1_ZIP')],
        [InlineKeyboardButton("📱 APKs", callback_data='p_ct_1_APK'),
         InlineKeyboardButton("📄 Others", callback_data='p_ct_1_Other')],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_dates_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("📆 Filter by Weeks ➡️", callback_data='menu_weeks')],
        [InlineKeyboardButton("📆 Filter by Months ➡️", callback_data='menu_months')],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_weeks_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("Past 1 Week", callback_data='p_tm_1_1w'),
         InlineKeyboardButton("Past 2 Weeks", callback_data='p_tm_1_2w'),
         InlineKeyboardButton("Past 3 Weeks", callback_data='p_tm_1_3w')],
        [InlineKeyboardButton("Range: 1 to 4 Weeks ago", callback_data='p_rn_1_1-4w')],
        [InlineKeyboardButton("Range: 3 to 4 Weeks ago", callback_data='p_rn_1_3-4w')],
        [InlineKeyboardButton("⬅️ Back to Dates", callback_data='menu_dates')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_months_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("Past 1 Month", callback_data='p_tm_1_1m'),
         InlineKeyboardButton("Past 2 Months", callback_data='p_tm_1_2m'),
         InlineKeyboardButton("Past 3 Months", callback_data='p_tm_1_3m')],
        [InlineKeyboardButton("Range: 1 to 4 Months ago", callback_data='p_rn_1_1-4m')],
        [InlineKeyboardButton("Range: 5 to 6 Months ago", callback_data='p_rn_1_5-6m')],
        [InlineKeyboardButton("⬅️ Back to Dates", callback_data='menu_dates')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_trash_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("🕒 Recently Trashed", callback_data='p_tr_1_none')],
        [InlineKeyboardButton("📁 Trashed Categories ➡️", callback_data='menu_trash_cat')],
        [InlineKeyboardButton("☢️ Empty Trash (Delete All)", callback_data='trash_empty')],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_trash_categories_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("🎥 Videos", callback_data='p_tc_1_Video'),
         InlineKeyboardButton("📸 Images", callback_data='p_tc_1_Image')],
        [InlineKeyboardButton("🎵 Audio", callback_data='p_tc_1_Audio'),
         InlineKeyboardButton("📦 ZIPs", callback_data='p_tc_1_ZIP')],
        [InlineKeyboardButton("⬅️ Back to Trash Menu", callback_data='menu_trash')]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==========================================
#     PAGINATION ENGINE 
# ==========================================
def get_paginated_results(f_type, f_param, page, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    limit = 11 
    offset = (page - 1) * 10 
    
    auth_filter = f" AND owner_id = {user_id}"
    is_trash = False
    back_target = 'menu_main'
    header = ""
    
    if f_type == 'rc':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"🕒 *Recent Files (Page {page}):*\n\n"
    elif f_type == 'ct':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_type = %s AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (f_param, limit, offset))
        header = f"📂 *{f_param}s (Page {page}):*\n\n"
        back_target = 'menu_categories'
    elif f_type == 'hs':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_name ILIKE %s AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (f'%#{f_param}%', limit, offset))
        header = f"🗂️ *#{f_param} (Page {page}):*\n\n"
        back_target = 'menu_folders'
    elif f_type == 'tm':
        unit = "weeks" if 'w' in f_param else "months"
        num = f_param.replace('w', '').replace('m', '')
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE upload_date >= NOW() - INTERVAL '{num} {unit}' AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"📅 *Past {num} {unit} (Page {page}):*\n\n"
        back_target = 'menu_weeks' if 'w' in f_param else 'menu_months'
    elif f_type == 'rn':
        unit = "weeks" if 'w' in f_param else "months"
        nums = f_param.replace('w', '').replace('m', '').split('-')
        start_num, end_num = int(nums[0]), int(nums[1])
        if start_num > end_num: start_num, end_num = end_num, start_num
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE upload_date <= NOW() - INTERVAL '{start_num} {unit}' AND upload_date >= NOW() - INTERVAL '{end_num} {unit}' AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"📅 *{start_num} to {end_num} {unit} ago (Page {page}):*\n\n"
        back_target = 'menu_weeks' if 'w' in f_param else 'menu_months'
    elif f_type == 'sr':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_name ILIKE %s AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (f'%{f_param}%', limit, offset))
        header = f"🔎 *Search:* `{f_param}` (Page {page})\n\n"
    elif f_type == 'tr':
        is_trash = True
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE deleted_at IS NOT NULL {auth_filter} ORDER BY deleted_at DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"🕒 *Recently Trashed (Page {page}):*\n\n"
        back_target = 'menu_trash'
    elif f_type == 'tc':
        is_trash = True
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_type = %s AND deleted_at IS NOT NULL {auth_filter} ORDER BY deleted_at DESC LIMIT %s OFFSET %s", (f_param, limit, offset))
        header = f"📂 *Trashed {f_param}s (Page {page}):*\n\n"
        back_target = 'menu_trash_cat'
        
    results = list(cursor.fetchall())
    has_next = len(results) == limit
    if has_next: results.pop() 
        
    cursor.close()
    conn.close()
    return results, has_next, header, back_target, is_trash

def build_pagination_keyboard(f_type, f_param, page, has_next, back_target):
    nav_row = []
    if page > 1: nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"p_{f_type}_{page-1}_{f_param}"))
    if has_next: nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"p_{f_type}_{page+1}_{f_param}"))
    keyboard = []
    if nav_row: keyboard.append(nav_row)
    
    if f_type == 'hs':
        keyboard.append([InlineKeyboardButton("☢️ Delete Entire Folder", callback_data=f"del_folder_{f_param}")])
        
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data=back_target)])
    return InlineKeyboardMarkup(keyboard)

def format_results_msg(results, header, is_trash):
    if not results: return header + "No files found."
    msg = header
    for row in results:
        safe_name = str(row[2]).replace('`', '') 
        if is_trash:
            msg += f"📁 `{safe_name}`\n♻️ /restore{row[0]} | ☢️ /pdel{row[0]}\n\n"
        else:
            msg += f"📁 `{safe_name}` ({row[3]})\n"
            msg += f"👁️ /view{row[0]} | 📥 /get{row[0]}\n"
            msg += f"🔗 /share{row[0]} | 🗑️ /del{row[0]}\n\n"
    return msg

def is_authorized_to_modify(short_code, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT owner_id FROM files WHERE short_code = %s", (short_code,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row and row[0] == user_id

# ==========================================

async def show_main_menu_msg(update: Update, user_id):
    welcome_text = (
        "🎉 *Welcome to your Private Vault.*\n\n"
        "📤 *1. HOW TO UPLOAD & ORGANIZE*\n"
        "• Send me any Photo, Video, Document, APK, or File.\n"
        "• *The Magic of Captions:* Type a name for your file before sending!\n"
        "• *Auto-Folders:* Use a `#hashtag` in the caption to create a folder!\n\n"
        "🔎 *2. HOW TO FIND YOUR FILES*\n"
        "• Use the buttons below or text me a keyword to search.\n\n"
        "⚙️ *3. COMMAND CHEAT SHEET*\n"
        "• `/viewCODE` - Preview the image/video.\n"
        "• `/getCODE` - Download the original file.\n"
        "• `/shareCODE` - Generate a shared link for others!\n"
        "• `/renameCODE New Name` - Change file name.\n"
        "• `/delCODE` - Move file to Recycle Bin.\n"
        "• `/main` - Open this menu again.\n\n"
        "🔒 _Note: Your vault is 100% private. Other users cannot see your files._\n\n"
        "👇 *Click a button below to explore!*"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_menu(user_id), parse_mode='Markdown')

async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_registered(user_id):
        await update.message.reply_text("✅ You are already registered in the vault!")
        return
    if not context.args:
        await update.message.reply_text("⚠️ *Registration Required*\nPlease reply with your name like this:\n`/register John Doe`", parse_mode='Markdown')
        return
        
    full_name = " ".join(context.args)
    register_user(user_id, full_name)
    await update.message.reply_text(f"✅ Vault secured! Registered as *{full_name}*.", parse_mode='Markdown')
    await show_main_menu_msg(update, user_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    auto_clean_trash()

    if context.args and context.args[0].startswith('get'):
        if not is_registered(user_id):
            await update.message.reply_text(
                "👋 *Registration Required!*\n\n"
                "To download this shared file, please *reply to this message with your Full Name*.\n\n"
                "_(Once registered, just click the shared link again!)_",
                parse_mode='Markdown'
            )
            return
            
        short_code = context.args[0].replace('get', '', 1)
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE short_code = %s RETURNING file_id", (short_code,))
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        
        if result: await update.message.reply_document(document=result[0], caption=FEEDBACK_SIG, parse_mode='Markdown')
        else: await update.message.reply_text("❌ This file no longer exists or was deleted.")
        return

    if not is_registered(user_id):
        await update.message.reply_text("👋 *Welcome to the Family Cloud!*\n\nTo get started, please register by typing:\n`/register Your Full Name`", parse_mode='Markdown')
        return
        
    await show_main_menu_msg(update, user_id)

async def main_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("⚠️ Please register first by typing:\n`/register Your Name`", parse_mode='Markdown')
        return
    await show_main_menu_msg(update, user_id)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id): return
    
    try:
        auth_filter = f" AND owner_id = {user_id}"
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"SELECT COUNT(*) FROM files WHERE deleted_at IS NULL {auth_filter}")
        total_files = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT COUNT(*) FROM files WHERE deleted_at IS NOT NULL {auth_filter}")
        trash_files = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT SUM(file_size) FROM files WHERE deleted_at IS NULL {auth_filter}")
        total_bytes = cursor.fetchone()[0] or 0
        formatted_size = format_bytes(total_bytes)
        
        cursor.execute(f"SELECT file_type, COUNT(*) FROM files WHERE deleted_at IS NULL {auth_filter} GROUP BY file_type")
        type_counts = cursor.fetchall()
        cursor.close()
        conn.close()
        
        stats_msg = f"📊 *Vault Analytics*\n\nTotal Accessible Files: {total_files}\nFiles in Trash: {trash_files}\n💾 *Storage Used: {formatted_size}*\n\n*File Breakdown:*\n"
        for t, c in type_counts: stats_msg += f"• {t}s: {c}\n"
        await update.message.reply_text(stats_msg, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}\n\nThe stats math engine threw an error.")

# ==========================================
#     ACTION COMMANDS 
# ==========================================

async def share_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        short_code = update.message.text.split()[0].replace('/share', '', 1)
        if not short_code: return
        if not is_authorized_to_modify(short_code, update.effective_user.id):
            await update.message.reply_text("🚫 Access Denied: You do not own this file.")
            return
        share_link = f"https://t.me/{context.bot.username}?start=get{short_code}"
        await update.message.reply_text(f"🔗 *Share Link Generated!*\n\n`{share_link}`", parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        short_code = update.message.text.split()[0].replace('/del', '', 1)
        if not short_code: return
        user_id = update.effective_user.id
        
        if not is_authorized_to_modify(short_code, user_id):
            await update.message.reply_text("🚫 Access Denied: You do not own this file.")
            return

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE files SET deleted_at = NOW() WHERE short_code = %s RETURNING file_name", (short_code,))
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if result: await update.message.reply_text(f"🗑️ `{result[0]}` moved to Trash.")
        else: await update.message.reply_text("❌ File not found in database.")
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

async def restore_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        short_code = update.message.text.split()[0].replace('/restore', '', 1)
        if not short_code: return
        user_id = update.effective_user.id
        
        if not is_authorized_to_modify(short_code, user_id):
            await update.message.reply_text("🚫 Access Denied: You do not own this file.")
            return

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE files SET deleted_at = NULL WHERE short_code = %s RETURNING file_name", (short_code,))
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if result: await update.message.reply_text(f"♻️ *Restored:* `{result[0]}`", parse_mode='Markdown')
        else: await update.message.reply_text("❌ File not found in database.")
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

async def perm_delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        short_code = update.message.text.split()[0].replace('/pdel', '', 1)
        if not short_code: return
        user_id = update.effective_user.id
        
        if not is_authorized_to_modify(short_code, user_id):
            await update.message.reply_text("🚫 Access Denied: You do not own this file.")
            return

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM files WHERE short_code = %s RETURNING file_name", (short_code,))
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if result: await update.message.reply_text(f"☢️ *Permanently Deleted:* `{result[0]}`", parse_mode='Markdown')
        else: await update.message.reply_text("❌ File not found in database.")
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

async def rename_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        match = re.match(r'^/rename(\w+)\s+(.+)', update.message.text)
        if match:
            short_code = match.group(1)
            user_id = update.effective_user.id
            if not is_authorized_to_modify(short_code, user_id):
                await update.message.reply_text("🚫 Access Denied: You do not own this file.")
                return

            new_name = match.group(2).strip()
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE files SET file_name = %s WHERE short_code = %s RETURNING file_name", (new_name, short_code))
            result = cursor.fetchone()
            conn.commit()
            cursor.close()
            conn.close()
            if result: await update.message.reply_text(f"✏️ *Renamed successfully!*\nNew name: `{result[0]}`", parse_mode='Markdown')
            else: await update.message.reply_text("❌ File not found in database.")
        else: await update.message.reply_text("⚠️ Please type the new name after the command. Example: `/renameABCD My Photo`", parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        short_code = update.message.text.split()[0].replace('/get', '', 1)
        if not short_code: return
        if not is_authorized_to_modify(short_code, update.effective_user.id): 
            await update.message.reply_text("🚫 Access Denied: You do not own this file.")
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE short_code = %s RETURNING file_id", (short_code,))
        result = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if result: await update.message.reply_document(document=result[0], caption=FEEDBACK_SIG, parse_mode='Markdown')
        else: await update.message.reply_text("❌ File not found in database.")
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

async def view_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        short_code = update.message.text.split()[0].replace('/view', '', 1)
        if not short_code: return
        if not is_authorized_to_modify(short_code, update.effective_user.id): 
            await update.message.reply_text("🚫 Access Denied: You do not own this file.")
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT file_id, file_type FROM files WHERE short_code = %s", (short_code,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            fid, ftype = result[0], result[1]
            if ftype == "Image": await update.message.reply_photo(photo=fid, caption=FEEDBACK_SIG, parse_mode='Markdown')
            elif ftype == "Video": await update.message.reply_video(video=fid, caption=FEEDBACK_SIG, parse_mode='Markdown')
            else: await update.message.reply_text("⚠️ Preview only available for Images and Videos.")
        else:
            await update.message.reply_text("❌ File not found in database.")
    except Exception as e: await update.message.reply_text(f"❌ Error: {str(e)}")

# ==========================================
#     INTERACTION HANDLERS 
# ==========================================

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    try:
        if data == 'menu_main':
            await query.edit_message_text("🔍 *Main Menu:*", reply_markup=get_main_menu(user_id), parse_mode='Markdown')
        elif data == 'menu_categories':
            await query.edit_message_text("📂 *Select a Category:*", reply_markup=get_categories_menu(user_id), parse_mode='Markdown')
        elif data == 'menu_dates':
            await query.edit_message_text("📅 *Select Timeframe Type:*", reply_markup=get_dates_menu(user_id), parse_mode='Markdown')
        elif data == 'menu_weeks':
            await query.edit_message_text("📆 *Select Weeks Filter:*", reply_markup=get_weeks_menu(user_id), parse_mode='Markdown')
        elif data == 'menu_months':
            await query.edit_message_text("📆 *Select Months Filter:*", reply_markup=get_months_menu(user_id), parse_mode='Markdown')
        elif data == 'menu_trash':
            await query.edit_message_text("🗑️ *Recycle Bin:*\nFiles stay here for 30 days.", reply_markup=get_trash_menu(user_id), parse_mode='Markdown')
        elif data == 'menu_trash_cat':
            await query.edit_message_text("📂 *Trashed Categories:*", reply_markup=get_trash_categories_menu(user_id), parse_mode='Markdown')
        
        elif data == 'trash_empty':
            auth_filter = f" AND owner_id = {user_id}"
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM files WHERE deleted_at IS NOT NULL {auth_filter} RETURNING short_code")
            deleted_count = len(cursor.fetchall())
            conn.commit()
            cursor.close()
            conn.close()
            await query.edit_message_text(f"☢️ *Trash Emptied!*\n{deleted_count} files were permanently deleted.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]]), parse_mode='Markdown')
            
        elif data.startswith('del_folder_'):
            tag = data.split('_', 2)[2]
            auth_filter = f" AND owner_id = {user_id}"
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"UPDATE files SET deleted_at = NOW() WHERE file_name ILIKE %s {auth_filter} RETURNING id", (f'%#{tag}%',))
            count = len(cursor.fetchall())
            conn.commit()
            cursor.close()
            conn.close()
            await query.edit_message_text(f"🗑️ Moved {count} files from `#{tag}` to Trash.", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Folders", callback_data='menu_folders')]]))

        elif data.startswith('p_'):
            parts = data.split('_', 3)
            res, has_next, head, target, trash = get_paginated_results(parts[1], parts[3], int(parts[2]), user_id)
            await query.edit_message_text(text=format_results_msg(res, head, trash), reply_markup=build_pagination_keyboard(parts[1], parts[3], int(parts[2]), has_next, target), parse_mode='Markdown')
            
        elif data == 'menu_folders':
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT file_name FROM files WHERE file_name LIKE '%%#%%' AND deleted_at IS NULL AND owner_id = %s", (user_id,))
            rows = cursor.fetchall()
            tags = set()
            for r in rows: tags.update(re.findall(r'#\w+', r[0]))
            cursor.close()
            conn.close()
            keyboard = [[InlineKeyboardButton(t, callback_data=f"p_hs_1_{t[1:]}")] for t in list(tags)[:14]]
            keyboard.append([InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')])
            await query.edit_message_text("🗂️ *Folder Hashtags:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            
    except Exception as e:
        await query.edit_message_text(f"❌ Developer Error: {str(e)}\n\n(Please try again or check format.)")

async def handle_text_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("⚠️ Please register first by typing:\n`/register Your Name`", parse_mode='Markdown')
        return
        
    search_query = update.message.text.strip()
    res, has_next, head, target, trash = get_paginated_results('sr', search_query[:30], 1, user_id)
    await update.message.reply_text(text=format_results_msg(res, head, trash), reply_markup=build_pagination_keyboard('sr', search_query[:30], 1, has_next, target), parse_mode='Markdown')

async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_registered(user_id):
        await update.message.reply_text("⚠️ Please register first by typing:\n`/register Your Name`", parse_mode='Markdown')
        return
        
    global MEDIA_GROUP_CAPTIONS
    custom_name = update.message.caption
    
    if update.message.media_group_id:
        mg_id = update.message.media_group_id
        if custom_name:
            MEDIA_GROUP_CAPTIONS[mg_id] = custom_name
        else:
            custom_name = MEDIA_GROUP_CAPTIONS.get(mg_id)
        if len(MEDIA_GROUP_CAPTIONS) > 100: MEDIA_GROUP_CAPTIONS.clear()
        
    fid, ftype, orig_name, fsize = None, "Other", None, 0
    
    if update.message.document:
        fid, ftype = update.message.document.file_id, "File"
        orig_name = update.message.document.file_name
        fsize = getattr(update.message.document, 'file_size', 0)
        ext = (orig_name or "").lower()
        if ext.endswith(('.zip', '.rar', '.7z')): ftype = "ZIP"
        elif ext.endswith('.apk'): ftype = "APK"
    elif update.message.video: 
        fid, ftype = update.message.video.file_id, "Video"
        orig_name = update.message.video.file_name
        fsize = getattr(update.message.video, 'file_size', 0)
    elif update.message.photo: 
        fid, ftype = update.message.photo[-1].file_id, "Image"
        fsize = getattr(update.message.photo[-1], 'file_size', 0)
    elif update.message.audio or update.message.voice:
        audio_obj = update.message.audio or update.message.voice
        fid, ftype = audio_obj.file_id, "Audio"
        orig_name = getattr(audio_obj, 'file_name', None)
        fsize = getattr(audio_obj, 'file_size', 0)
        
    if fid:
        if custom_name and orig_name:
            final_name = f"{custom_name} - {orig_name}"
        else:
            final_name = custom_name or orig_name or f"{ftype}_{fid[-5:]}"
            
        save_to_cloud(fid, final_name, ftype, user_id, fsize)
        await update.message.reply_text(f"✅ Saved: `{final_name}`", parse_mode='Markdown')

# ==========================================
#     MAIN APPLICATION BUILDER
# ==========================================

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('register', register_cmd))
    app.add_handler(CommandHandler('main', main_menu_command)) 
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CallbackQueryHandler(button_click))
    
    app.add_handler(MessageHandler(filters.Regex(r'^/share\w+'), share_file))
    app.add_handler(MessageHandler(filters.Regex(r'^/del\w+'), delete_file))
    app.add_handler(MessageHandler(filters.Regex(r'^/get\w+'), get_file))
    app.add_handler(MessageHandler(filters.Regex(r'^/view\w+'), view_file))
    app.add_handler(MessageHandler(filters.Regex(r'^/restore\w+'), restore_file))
    app.add_handler(MessageHandler(filters.Regex(r'^/pdel\w+'), perm_delete_file))
    app.add_handler(MessageHandler(filters.Regex(r'^/rename\w+'), rename_file))
    
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^[^/]'), handle_text_search))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.PHOTO | filters.AUDIO | filters.VOICE, handle_files))
    
    app.run_polling()
