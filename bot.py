import logging
import asyncio
import threading
import ssl
import re
import random
import string
import json
import os
import urllib.request
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hydrogram import Client, filters
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
import pg8000.dbapi

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
#     ENVIRONMENT VARIABLES
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
WEB_APP_URL = os.environ.get("WEB_APP_URL", "").strip()

if not WEB_APP_URL or not WEB_APP_URL.startswith("http"):
    WEB_APP_URL = "https://cloudify-fallback-url.onrender.com"
if WEB_APP_URL.endswith('/'):
    WEB_APP_URL = WEB_APP_URL[:-1]

PORT = int(os.environ.get("PORT", 8080))
FEEDBACK_SIG = "\n\n*(Visual Vault by Cloudyfy)*"
MEDIA_GROUP_CAPTIONS = {}
THUMB_CACHE = {}
bot_app = None
bot_loop = None

# ==========================================
#           DATABASE ENGINE
# ==========================================
def safe_md(text):
    if not text: return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "")

def get_db_connection():
    if not DATABASE_URL: return None
    url = urlparse(DATABASE_URL)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return pg8000.dbapi.connect(
        user=url.username, password=url.password,
        host=url.hostname, port=url.port or 5432, 
        database=url.path.lstrip('/'), ssl_context=ssl_context
    )

def upgrade_database_schema():
    conn = get_db_connection()
    if not conn: return
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
        cursor.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS thumb_id TEXT DEFAULT NULL;")
        cursor.execute("UPDATE files SET short_code = substring(md5(random()::text) from 1 for 6) WHERE short_code IS NULL;")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                full_name TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_username TEXT;")
        cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_name TEXT;")
        conn.commit()
    except Exception as e:
        logging.error(f"DB setup error: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

def auto_clean_trash():
    conn = get_db_connection()
    if not conn: return
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

def register_user(user_id, full_name, tg_username=None, tg_name=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, full_name, tg_username, tg_name) 
        VALUES (%s, %s, %s, %s) 
        ON CONFLICT (user_id) DO UPDATE SET tg_username = EXCLUDED.tg_username, tg_name = EXCLUDED.tg_name
    ''', (user_id, full_name, tg_username, tg_name))
    conn.commit()
    cursor.close()
    conn.close()

def save_to_cloud(file_id, file_name, file_type, owner_id, file_size, thumb_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    new_code = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    cursor.execute('''
        INSERT INTO files (file_id, file_name, file_type, owner_id, short_code, file_size, thumb_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (file_id, file_name, file_type, owner_id, new_code, file_size, thumb_id))
    conn.commit()
    cursor.close()
    conn.close()

def format_bytes(size):
    if not size: return "0 B"
    try: size = float(size)
    except Exception: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0: return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

# ==========================================
#     MINI APP FRONTEND (AESTHETIC UI)
# ==========================================
MINI_APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Visual Vault</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root { --bg: #000000; --surface: #111111; --border: #222222; --text: #ffffff; --text-muted: #888888; --accent: #ffffff; --danger: #ff3b30;}
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
        body { background: var(--bg); color: var(--text); padding-bottom: 40px; min-height: 100vh; }
        header { position: sticky; top: 0; z-index: 50; background: rgba(0, 0, 0, 0.9); backdrop-filter: blur(10px); border-bottom: 1px solid var(--border); padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; }
        .brand { font-size: 1.1rem; font-weight: 700; cursor: pointer; }
        .search-container { padding: 12px 16px; border-bottom: 1px solid var(--border); }
        .search-input { width: 100%; background: var(--surface); border: 1px solid var(--border); color: var(--text); padding: 12px 16px; border-radius: 16px; font-size: 0.9rem; outline: none; transition: border 0.2s; }
        .search-input:focus { border-color: var(--text-muted); }
        .categories { display: flex; overflow-x: auto; gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--border); scrollbar-width: none; }
        .categories::-webkit-scrollbar { display: none; }
        .cat-btn { background: var(--surface); padding: 6px 16px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; cursor: pointer; white-space: nowrap; border: 1px solid var(--border); color: var(--text-muted); transition: all 0.2s ease; }
        .cat-btn.active { background: var(--accent); color: #000000; }
        .gallery-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; padding: 10px; }
        .file-card { position: relative; aspect-ratio: 1; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); display: flex; flex-direction: column; justify-content: center; align-items: center; cursor: pointer; }
        .file-card img { width: 100%; height: 100%; object-fit: cover; background-color: #1a1a1a; }
        .file-icon { font-size: 1.8rem; color: #555; }
        .video-badge { position: absolute; top: 6px; right: 6px; background: rgba(0,0,0,0.8); border-radius: 100px; padding: 3px 6px; font-size: 0.5rem; font-weight: 600; border: 1px solid #333; z-index: 5; }
        .file-name-overlay { position: absolute; bottom: 0; left: 0; right: 0; background: linear-gradient(to top, rgba(0,0,0,1) 0%, rgba(0,0,0,0) 100%); font-size: 0.6rem; font-weight: 500; padding: 20px 8px 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: center; }
        .modal-overlay { display: flex; position: fixed; inset: 0; z-index: 100; background: rgba(0,0,0,0.8); backdrop-filter: blur(8px); opacity: 0; pointer-events: none; transition: opacity 0.2s ease; justify-content: center; align-items: flex-end; padding-bottom: 24px; }
        .modal-overlay.active { opacity: 1; pointer-events: auto; }
        .modal-content { width: 92%; max-width: 400px; background: var(--surface); border: 1px solid var(--border); border-radius: 28px; overflow: hidden; transform: translateY(20px); transition: transform 0.3s; }
        .modal-overlay.active .modal-content { transform: translateY(0); }
        .modal-header { display: flex; justify-content: space-between; padding: 20px 24px 12px; }
        .modal-title { font-weight: 600; font-size: 0.95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .close-btn { background: #222; width: 28px; height: 28px; border-radius: 50%; display: flex; justify-content: center; align-items: center; cursor: pointer; color: #aaa; }
        .modal-body { padding: 0 24px 20px; display: flex; justify-content: center; flex-direction: column; align-items: center;}
        .modal-body img { max-width: 100%; max-height: 45vh; border-radius: 16px; object-fit: contain; background: #000;}
        .vid-warning { font-size: 0.75rem; color: var(--text-muted); text-align: center; margin-top: 12px; }
        .modal-footer { padding: 0 24px 24px; display: flex; gap: 8px;}
        .btn { flex: 2; padding: 14px; border-radius: 100px; border: none; font-weight: 600; font-size: 0.9rem; background: #fff; color: #000; cursor: pointer;}
        .btn-del { flex: 1; background: #1a1a1a; color: var(--danger); border: 1px solid #333;}
        .toast { position: fixed; top: -60px; left: 50%; transform: translateX(-50%); background: #fff; color: #000; padding: 12px 24px; border-radius: 100px; font-size: 0.85rem; font-weight: 600; z-index: 200; transition: top 0.3s; box-shadow: 0 4px 12px rgba(0,0,0,0.5);}
        .toast.show { top: 40px; }
    </style>
</head>
<body>
    <header>
        <div class="brand" id="headerTitle">Visual Vault</div>
        <div style="font-size:0.75rem; font-weight:600; color:var(--text-muted); background:var(--surface); padding:4px 10px; border-radius:20px;">Personal</div>
    </header>

    <div class="search-container">
        <input type="text" id="searchInput" class="search-input" placeholder="Search keywords or #hashtags..." oninput="applyFilters()">
    </div>

    <div class="categories" id="cat-container">
        <div class="cat-btn active" onclick="setCategoryFilter('All', this)">All</div>
        <div class="cat-btn" onclick="setCategoryFilter('Image', this)">Images</div>
        <div class="cat-btn" onclick="setCategoryFilter('Video', this)">Videos</div>
        <div class="cat-btn" onclick="setCategoryFilter('Audio', this)">Audio</div>
        <div class="cat-btn" onclick="setCategoryFilter('ZIP', this)">ZIPs</div>
        <div class="cat-btn" onclick="setCategoryFilter('APK', this)">APKs</div>
    </div>

    <div class="gallery-grid" id="gallery"></div>

    <div class="modal-overlay" id="previewModal" onclick="closeModal(event)">
        <div class="modal-content" onclick="event.stopPropagation()">
            <div class="modal-header">
                <div class="modal-title" id="modalTitle">Preview</div>
                <div class="close-btn" onclick="closeModal()">✕</div>
            </div>
            <div class="modal-body" id="modalMedia"></div>
            <div class="modal-footer">
                <button class="btn btn-del" id="modalDelBtn">🗑️ Delete</button>
                <button class="btn" id="modalSendBtn">Deliver to Chat</button>
            </div>
        </div>
    </div>
    <div class="toast" id="toast">Action Successful</div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        const user = tg.initDataUnsafe?.user || { id: 0 };
        
        let allFiles = []; 
        let currentCategory = 'All';

        function showToast(msg) {
            const t = document.getElementById('toast');
            t.innerText = msg; t.classList.add('show');
            setTimeout(() => { t.classList.remove('show'); }, 2500);
        }

        async function initApp() {
            const res = await fetch(`/api/gallery?user_id=${user.id}`);
            allFiles = await res.json();
            applyFilters(); 
        }

        function setCategoryFilter(catType, element) {
            document.querySelectorAll('.cat-btn').forEach(btn => btn.classList.remove('active'));
            element.classList.add('active');
            currentCategory = catType;
            applyFilters();
        }

        function applyFilters() {
            const searchQuery = document.getElementById('searchInput').value.toLowerCase();
            const container = document.getElementById('gallery');
            container.innerHTML = '';

            const filteredFiles = allFiles.filter(f => {
                const matchesCat = (currentCategory === 'All' || f.file_type === currentCategory);
                const matchesSearch = f.name.toLowerCase().includes(searchQuery);
                return matchesCat && matchesSearch;
            });

            if (filteredFiles.length === 0) {
                container.innerHTML = '<div style="grid-column: 1/-1; text-align:center; padding: 60px 20px; color:var(--text-muted); font-size: 0.9rem;">No files found.</div>';
                return;
            }

            filteredFiles.forEach(f => {
                const card = document.createElement('div');
                card.className = 'file-card';
                card.id = `card_${f.short_code}`;
                card.onclick = () => handleItemClick(f);

                let innerContent = '';
                if (f.thumb_id && f.thumb_id !== "NULL") {
                    innerContent += `<img src="/api/thumb?file_id=${f.thumb_id}" loading="lazy">`;
                } else if (f.file_type === 'Image') {
                    innerContent += `<img src="/api/thumb?file_id=${f.file_id}" loading="lazy">`;
                } else {
                    let icons = {'Video':'🎥', 'Audio':'🎵', 'ZIP':'📦', 'APK':'📱', 'File':'📄'};
                    innerContent += `<div class="file-icon">${icons[f.file_type] || '📄'}</div>`;
                }
                if (f.file_type === 'Video') innerContent += `<div class="video-badge">▶</div>`;
                innerContent += `<div class="file-name-overlay">${f.name}</div>`;
                card.innerHTML = innerContent;
                container.appendChild(card);
            });
        }

        function handleItemClick(file) {
            let mediaHtml = `<div style="padding: 20px; font-size:3rem;">📄</div>`; 
            if (file.file_type === 'Video') {
                const src = (file.thumb_id && file.thumb_id !== "NULL") ? file.thumb_id : file.file_id;
                mediaHtml = `<img src="/api/thumb?file_id=${src}"><div class="vid-warning">Tap 'Deliver to Chat' to stream full Video.</div>`;
            } else if (file.thumb_id && file.thumb_id !== "NULL") {
                mediaHtml = `<img src="/api/thumb?file_id=${file.thumb_id}">`;
            } else if (file.file_type === 'Image') {
                mediaHtml = `<img src="/api/thumb?file_id=${file.file_id}">`;
            }

            document.getElementById('modalTitle').innerText = file.name;
            document.getElementById('modalMedia').innerHTML = mediaHtml;
            document.getElementById('modalDelBtn').onclick = () => { deleteFile(file.short_code); };
            document.getElementById('modalSendBtn').onclick = () => { sendToTelegram(file.short_code); closeModal(); };
            document.getElementById('previewModal').classList.add('active');
        }

        function closeModal(e) {
            if (e && e.target !== document.getElementById('previewModal')) return;
            document.getElementById('previewModal').classList.remove('active');
            setTimeout(() => { document.getElementById('modalMedia').innerHTML = ''; }, 200);
        }

        async function sendToTelegram(shortCode) {
            showToast("Dispatching to Chat...");
            await fetch(`/api/send_to_chat?short_code=${shortCode}&target_chat=${user.id}`);
            showToast("Delivered! ✅");
        }

        async function deleteFile(shortCode) {
            if(!confirm("Move this file to trash?")) return;
            closeModal();
            showToast("Deleting...");
            const res = await fetch(`/api/delete?short_code=${shortCode}&user_id=${user.id}`);
            if (res.ok) {
                document.getElementById(`card_${shortCode}`).style.display = 'none';
                allFiles = allFiles.filter(f => f.short_code !== shortCode);
                showToast("Moved to Trash 🗑️");
            } else {
                showToast("Failed to delete ❌");
            }
        }

        initApp();
    </script>
</body>
</html>
"""

# ==========================================
#     MINI APP BACKEND (HTTP SERVER)
# ==========================================
class WebAppServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(MINI_APP_HTML.encode('utf-8'))

        elif path == "/api/gallery":
            user_id = qs.get("user_id", ["0"])[0]
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT file_id, short_code, file_name, file_type, owner_id, thumb_id FROM files WHERE deleted_at IS NULL AND owner_id = %s ORDER BY upload_date DESC LIMIT 150", (user_id,))
            results = cursor.fetchall()
            cursor.close()
            conn.close()
            
            data = [{"file_id": r[0], "short_code": r[1], "name": r[2], "file_type": r[3], "owner_id": r[4], "thumb_id": r[5]} for r in results]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode('utf-8'))
            
        elif path == "/api/delete":
            short_code = qs.get("short_code", [""])[0]
            user_id = int(qs.get("user_id", ["0"])[0])
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT owner_id FROM files WHERE short_code = %s", (short_code,))
            row = cursor.fetchone()
            if row and row[0] == user_id:
                cursor.execute("UPDATE files SET deleted_at = NOW() WHERE short_code = %s", (short_code,))
                conn.commit()
                self.send_response(200)
            else:
                self.send_response(403)
            cursor.close()
            conn.close()
            self.end_headers()
            
        elif path == "/api/thumb":
            file_id = qs.get("file_id", [None])[0]
            if not file_id: 
                self.send_response(404); self.end_headers(); return
            if file_id in THUMB_CACHE:
                self.send_response(200); self.send_header("Content-type", "image/jpeg"); self.send_header("Cache-Control", "public, max-age=86400"); self.end_headers()
                self.wfile.write(THUMB_CACHE[file_id]); return
            try:
                req = urllib.request.urlopen(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={file_id}", timeout=10)
                res = json.loads(req.read().decode())
                if res.get("ok"):
                    img_data = urllib.request.urlopen(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{res['result']['file_path']}", timeout=10).read()
                    if len(THUMB_CACHE) > 500: THUMB_CACHE.clear()
                    THUMB_CACHE[file_id] = img_data
                    self.send_response(200); self.send_header("Content-type", "image/jpeg"); self.end_headers()
                    self.wfile.write(img_data); return
            except Exception: pass
            self.send_response(404); self.end_headers()
            
        elif path == "/api/send_to_chat":
            short_code = qs.get("short_code", [""])[0]
            target_chat = int(qs.get("target_chat", ["0"])[0])
            
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT file_id, file_name, file_type, owner_id FROM files WHERE short_code = %s", (short_code,))
            file_meta = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if file_meta and bot_app and bot_loop:
                fid, fname, ftype, owner_id = file_meta
                if target_chat != owner_id:
                    self.send_response(403); self.end_headers(); return
                
                asyncio.run_coroutine_threadsafe(
                    bot_app.send_cached_media(chat_id=target_chat, file_id=fid, caption=f"⚡ Delivered: `{fname}`"), bot_loop
                )
                self.send_response(200); self.send_header('Content-type', 'application/json'); self.end_headers()
                self.wfile.write(b'{"status":"ok"}'); return
            self.send_response(400); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

def run_web_server():
    server = ThreadingHTTPServer(('0.0.0.0', PORT), WebAppServer)
    server.serve_forever()

# ==========================================
#     HYDROGRAM MTPROTO BOT ENGINE
# ==========================================
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

app = Client("cloudyfy_engine", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)

def get_main_menu(user_id):
    keyboard = [
        [InlineKeyboardButton("🖼️ Open Visual Vault", web_app=WebAppInfo(url=WEB_APP_URL))],
        [InlineKeyboardButton("🕒 Recent Uploads", callback_data='p_rc_1_none')],
        [InlineKeyboardButton("📁 Categories ➡️", callback_data='menu_categories'),
         InlineKeyboardButton("🗂️ Folders ➡️", callback_data='menu_folders')],
        [InlineKeyboardButton("📅 Search by Time ➡️", callback_data='menu_dates')],
        [InlineKeyboardButton("🗑️ Recycle Bin (Trash) ➡️", callback_data='menu_trash')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_categories_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Videos", callback_data='p_ct_1_Video'), InlineKeyboardButton("📸 Images", callback_data='p_ct_1_Image')],
        [InlineKeyboardButton("🎵 Audio", callback_data='p_ct_1_Audio'), InlineKeyboardButton("📦 ZIPs", callback_data='p_ct_1_ZIP')],
        [InlineKeyboardButton("📱 APKs", callback_data='p_ct_1_APK'), InlineKeyboardButton("📄 Others", callback_data='p_ct_1_Other')],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]
    ])

def get_dates_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📆 Filter by Weeks ➡️", callback_data='menu_weeks')],
        [InlineKeyboardButton("📆 Filter by Months ➡️", callback_data='menu_months')],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]
    ])

def get_weeks_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Past 1 Week", callback_data='p_tm_1_1w'), InlineKeyboardButton("Past 2 Weeks", callback_data='p_tm_1_2w'), InlineKeyboardButton("Past 3 Weeks", callback_data='p_tm_1_3w')],
        [InlineKeyboardButton("Range: 1 to 4 Weeks ago", callback_data='p_rn_1_1-4w')],
        [InlineKeyboardButton("Range: 3 to 4 Weeks ago", callback_data='p_rn_1_3-4w')],
        [InlineKeyboardButton("⬅️ Back to Dates", callback_data='menu_dates')]
    ])

def get_months_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Past 1 Month", callback_data='p_tm_1_1m'), InlineKeyboardButton("Past 2 Months", callback_data='p_tm_1_2m'), InlineKeyboardButton("Past 3 Months", callback_data='p_tm_1_3m')],
        [InlineKeyboardButton("Range: 1 to 4 Months ago", callback_data='p_rn_1_1-4m')],
        [InlineKeyboardButton("Range: 5 to 6 Months ago", callback_data='p_rn_1_5-6m')],
        [InlineKeyboardButton("⬅️ Back to Dates", callback_data='menu_dates')]
    ])

def get_trash_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 Recently Trashed", callback_data='p_tr_1_none')],
        [InlineKeyboardButton("📁 Trashed Categories ➡️", callback_data='menu_trash_cat')],
        [InlineKeyboardButton("☢️ Empty Trash (Delete All)", callback_data='trash_empty')],
        [InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')]
    ])

def get_trash_categories_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎥 Videos", callback_data='p_tc_1_Video'), InlineKeyboardButton("📸 Images", callback_data='p_tc_1_Image')],
        [InlineKeyboardButton("🎵 Audio", callback_data='p_tc_1_Audio'), InlineKeyboardButton("📦 ZIPs", callback_data='p_tc_1_ZIP')],
        [InlineKeyboardButton("⬅️ Back to Trash Menu", callback_data='menu_trash')]
    ])

def get_paginated_results(f_type, f_param, page, user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    limit, offset = 11, (page - 1) * 10 
    auth_filter = f" AND owner_id = {user_id}"
    is_trash, back_target, header = False, 'menu_main', ""
    
    if f_type == 'rc':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"🕒 *Recent Files (Page {page}):*\n\n"
    elif f_type == 'ct':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_type = %s AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (f_param, limit, offset))
        header = f"📂 *{f_param}s (Page {page}):*\n\n"; back_target = 'menu_categories'
    elif f_type == 'hs':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_name ILIKE %s AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (f'%#{f_param}%', limit, offset))
        header = f"🗂️ *#{safe_md(f_param)} (Page {page}):*\n\n"; back_target = 'menu_folders'
    elif f_type == 'tm':
        unit = "weeks" if 'w' in f_param else "months"
        num = f_param.replace('w', '').replace('m', '')
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE upload_date >= NOW() - INTERVAL '{num} {unit}' AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"📅 *Past {num} {unit} (Page {page}):*\n\n"; back_target = 'menu_weeks' if 'w' in f_param else 'menu_months'
    elif f_type == 'rn':
        unit = "weeks" if 'w' in f_param else "months"
        nums = f_param.replace('w', '').replace('m', '').split('-')
        start_num, end_num = int(nums[0]), int(nums[1])
        if start_num > end_num: start_num, end_num = end_num, start_num
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE upload_date <= NOW() - INTERVAL '{start_num} {unit}' AND upload_date >= NOW() - INTERVAL '{end_num} {unit}' AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"📅 *{start_num} to {end_num} {unit} ago (Page {page}):*\n\n"; back_target = 'menu_weeks' if 'w' in f_param else 'menu_months'
    elif f_type == 'sr':
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_name ILIKE %s AND deleted_at IS NULL {auth_filter} ORDER BY upload_date DESC LIMIT %s OFFSET %s", (f'%{f_param}%', limit, offset))
        header = f"🔎 *Search:* `{safe_md(f_param)}` (Page {page})\n\n"
    elif f_type == 'tr':
        is_trash = True
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE deleted_at IS NOT NULL {auth_filter} ORDER BY deleted_at DESC LIMIT %s OFFSET %s", (limit, offset))
        header = f"🕒 *Recently Trashed (Page {page}):*\n\n"; back_target = 'menu_trash'
    elif f_type == 'tc':
        is_trash = True
        cursor.execute(f"SELECT short_code, file_id, file_name, file_type FROM files WHERE file_type = %s AND deleted_at IS NOT NULL {auth_filter} ORDER BY deleted_at DESC LIMIT %s OFFSET %s", (f_param, limit, offset))
        header = f"📂 *Trashed {f_param}s (Page {page}):*\n\n"; back_target = 'menu_trash_cat'
        
    results = list(cursor.fetchall())
    has_next = len(results) == limit
    if has_next: results.pop() 
    cursor.close(); conn.close()
    return results, has_next, header, back_target, is_trash

def build_pagination_keyboard(f_type, f_param, page, has_next, back_target):
    nav_row = []
    if page > 1: nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"p_{f_type}_{page-1}_{f_param}"))
    if has_next: nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"p_{f_type}_{page+1}_{f_param}"))
    keyboard = [nav_row] if nav_row else []
    if f_type == 'hs': keyboard.append([InlineKeyboardButton("☢️ Delete Entire Folder", callback_data=f"del_folder_{f_param}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data=back_target)])
    return InlineKeyboardMarkup(keyboard)

def format_results_msg(results, header, is_trash):
    if not results: return header + "No files found."
    msg = header
    for row in results:
        safe_name = str(row[2]).replace('`', '') 
        if is_trash: msg += f"📁 `{safe_name}`\n♻️ /restore{row[0]} | ☢️ /pdel{row[0]}\n\n"
        else: msg += f"📁 `{safe_name}` ({row[3]})\n👁️ /view{row[0]} | 📥 /get{row[0]}\n🔗 /share{row[0]} | 🗑️ /del{row[0]}\n\n"
    return msg

@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    user_id = message.from_user.id
    auto_clean_trash()
    if is_registered(user_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE users SET tg_username = %s, tg_name = %s WHERE user_id = %s", (message.from_user.username, message.from_user.first_name, user_id))
        conn.commit(); cursor.close(); conn.close()

    if len(message.command) > 1 and message.command[1].startswith('get'):
        if not is_registered(user_id):
            await message.reply_text("👋 *Registration Required!*\n\nPlease register using `/register Your Name`.")
            return
        short_code = message.command[1].replace('get', '', 1)
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE short_code = %s RETURNING file_id", (short_code,))
        res = cursor.fetchone()
        conn.commit(); cursor.close(); conn.close()
        if res: await app.send_cached_media(chat_id=message.chat.id, file_id=res[0], caption=FEEDBACK_SIG)
        else: await message.reply_text("❌ File not found.")
        return

    if not is_registered(user_id):
        await message.reply_text("👋 *Welcome!*\n\nPlease register by typing:\n`/register Your Full Name`")
        return
    await message.reply_text("🚀 **Welcome to your Private Vault.**", reply_markup=get_main_menu(user_id))

@app.on_message(filters.command("register"))
async def register_cmd(client, message):
    user_id = message.from_user.id
    if is_registered(user_id):
        await message.reply_text("✅ You are already registered!")
        return
    if len(message.command) < 2:
        await message.reply_text("⚠️ Use: `/register John Doe`")
        return
    full_name = " ".join(message.command[1:])
    register_user(user_id, full_name, message.from_user.username, message.from_user.first_name)
    await message.reply_text(f"✅ Registered as *{safe_md(full_name)}*.")
    await message.reply_text("Vault Ready.", reply_markup=get_main_menu(user_id))

@app.on_message(filters.command("main"))
async def main_menu_cmd(client, message):
    if not is_registered(message.from_user.id): return
    await message.reply_text("🔍 Main Menu:", reply_markup=get_main_menu(message.from_user.id))

@app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    user_id = message.from_user.id
    if not is_registered(user_id): return
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM files WHERE deleted_at IS NULL AND owner_id = {user_id}")
    tot = cursor.fetchone()[0]
    cursor.execute(f"SELECT COUNT(*) FROM files WHERE deleted_at IS NOT NULL AND owner_id = {user_id}")
    trsh = cursor.fetchone()[0]
    cursor.execute(f"SELECT SUM(file_size) FROM files WHERE deleted_at IS NULL AND owner_id = {user_id}")
    sz = format_bytes(cursor.fetchone()[0] or 0)
    cursor.close(); conn.close()
    await message.reply_text(f"📊 *Stats*\nTotal Files: {tot}\nTrash: {trsh}\nStorage: {sz}")

@app.on_callback_query()
async def button_click(client, query):
    data, user_id = query.data, query.from_user.id
    try:
        if data == 'menu_main': await query.edit_message_text("🔍 *Main Menu:*", reply_markup=get_main_menu(user_id))
        elif data == 'menu_categories': await query.edit_message_text("📂 *Categories:*", reply_markup=get_categories_menu(user_id))
        elif data == 'menu_dates': await query.edit_message_text("📅 *Timeframe:*", reply_markup=get_dates_menu(user_id))
        elif data == 'menu_weeks': await query.edit_message_text("📆 *Weeks:*", reply_markup=get_weeks_menu(user_id))
        elif data == 'menu_months': await query.edit_message_text("📆 *Months:*", reply_markup=get_months_menu(user_id))
        elif data == 'menu_trash': await query.edit_message_text("🗑️ *Recycle Bin:*", reply_markup=get_trash_menu(user_id))
        elif data == 'menu_trash_cat': await query.edit_message_text("📂 *Trashed Categories:*", reply_markup=get_trash_categories_menu(user_id))
        elif data == 'trash_empty':
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute(f"DELETE FROM files WHERE deleted_at IS NOT NULL AND owner_id = {user_id}")
            conn.commit(); cursor.close(); conn.close()
            await query.edit_message_text("☢️ *Trash Emptied!*", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='menu_main')]]))
        elif data.startswith('del_folder_'):
            tag = data.split('_', 2)[2]
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute(f"UPDATE files SET deleted_at = NOW() WHERE file_name ILIKE %s AND owner_id = {user_id}", (f'%#{tag}%',))
            conn.commit(); cursor.close(); conn.close()
            await query.edit_message_text(f"🗑️ Deleted `#{tag}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='menu_folders')]]))
        elif data.startswith('p_'):
            parts = data.split('_', 3)
            res, has_next, head, target, trash = get_paginated_results(parts[1], parts[3], int(parts[2]), user_id)
            await query.edit_message_text(text=format_results_msg(res, head, trash), reply_markup=build_pagination_keyboard(parts[1], parts[3], int(parts[2]), has_next, target))
        elif data == 'menu_folders':
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("SELECT file_name FROM files WHERE file_name LIKE '%%#%%' AND deleted_at IS NULL AND owner_id = %s", (user_id,))
            tags = set(); [tags.update(re.findall(r'#\w+', r[0])) for r in cursor.fetchall()]
            cursor.close(); conn.close()
            keyboard = [[InlineKeyboardButton(t, callback_data=f"p_hs_1_{t[1:]}")] for t in list(tags)[:14]]
            keyboard.append([InlineKeyboardButton("⬅️ Back to Main", callback_data='menu_main')])
            await query.edit_message_text("🗂️ *Folders:*", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e: await query.edit_message_text(f"❌ Error: {str(e)}")

def check_auth(short_code, user_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT owner_id FROM files WHERE short_code = %s", (short_code,))
    row = cursor.fetchone(); cursor.close(); conn.close()
    return row and row[0] == user_id

@app.on_message(filters.regex(r'^/share(\w+)'))
async def share_file(client, message):
    if not check_auth(message.matches[0].group(1), message.from_user.id): return
    await message.reply_text(f"🔗 `https://t.me/{app.me.username}?start=get{message.matches[0].group(1)}`")

@app.on_message(filters.regex(r'^/(del|restore|pdel)(\w+)'))
async def modify_file(client, message):
    action, short_code = message.matches[0].group(1), message.matches[0].group(2)
    if not check_auth(short_code, message.from_user.id): return
    conn = get_db_connection(); cursor = conn.cursor()
    if action == 'del': cursor.execute("UPDATE files SET deleted_at = NOW() WHERE short_code = %s RETURNING file_name", (short_code,))
    elif action == 'restore': cursor.execute("UPDATE files SET deleted_at = NULL WHERE short_code = %s RETURNING file_name", (short_code,))
    elif action == 'pdel': cursor.execute("DELETE FROM files WHERE short_code = %s RETURNING file_name", (short_code,))
    row = cursor.fetchone(); conn.commit(); cursor.close(); conn.close()
    if row: await message.reply_text(f"✅ Success: `{row[0]}`")

@app.on_message(filters.regex(r'^/rename(\w+)\s+(.+)'))
async def rename_file(client, message):
    short_code, new_name = message.matches[0].group(1), message.matches[0].group(2).strip()
    if not check_auth(short_code, message.from_user.id): return
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE files SET file_name = %s WHERE short_code = %s RETURNING file_name", (new_name, short_code))
    row = cursor.fetchone(); conn.commit(); cursor.close(); conn.close()
    if row: await message.reply_text(f"✅ Renamed: `{row[0]}`")

@app.on_message(filters.regex(r'^/(get|view)(\w+)'))
async def get_or_view(client, message):
    action, short_code = message.matches[0].group(1), message.matches[0].group(2)
    if not check_auth(short_code, message.from_user.id): return
    conn = get_db_connection(); cursor = conn.cursor()
    if action == 'get':
        cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE short_code = %s RETURNING file_id", (short_code,))
        row = cursor.fetchone()
        if row: await app.send_cached_media(chat_id=message.chat.id, file_id=row[0], caption=FEEDBACK_SIG)
    elif action == 'view':
        cursor.execute("SELECT file_id, file_type FROM files WHERE short_code = %s", (short_code,))
        row = cursor.fetchone()
        if row:
            if row[1] == "Image": await app.send_photo(chat_id=message.chat.id, photo=row[0], caption=FEEDBACK_SIG)
            elif row[1] == "Video": await app.send_video(chat_id=message.chat.id, video=row[0], caption=FEEDBACK_SIG)
    cursor.commit() if action == 'get' else None; cursor.close(); conn.close()

@app.on_message(filters.text & ~filters.command(["start", "register", "main", "stats"]))
async def handle_text_search(client, message):
    if not is_registered(message.from_user.id) or message.text.startswith('/'): return
    res, has_next, head, target, trash = get_paginated_results('sr', message.text.strip()[:30], 1, message.from_user.id)
    await message.reply_text(text=format_results_msg(res, head, trash), reply_markup=build_pagination_keyboard('sr', message.text.strip()[:30], 1, has_next, target))

@app.on_message(filters.document | filters.video | filters.photo | filters.audio | filters.voice)
async def handle_files(client, message):
    user_id = message.from_user.id
    if not is_registered(user_id): return
    
    custom_name = message.caption
    if message.media_group_id:
        mg_id = message.media_group_id
        if custom_name: MEDIA_GROUP_CAPTIONS[mg_id] = custom_name
        else: custom_name = MEDIA_GROUP_CAPTIONS.get(mg_id)
        if len(MEDIA_GROUP_CAPTIONS) > 100:
            oldest_key = list(MEDIA_GROUP_CAPTIONS.keys())[0]
            del MEDIA_GROUP_CAPTIONS[oldest_key]
        
    fid, ftype, orig_name, fsize, thumb_id = None, "Other", None, 0, None
    if message.document:
        fid, ftype, orig_name = message.document.file_id, "File", message.document.file_name
        fsize = getattr(message.document, 'file_size', 0)
        if getattr(message.document, 'thumbs', None): thumb_id = message.document.thumbs[0].file_id
        ext = (orig_name or "").lower()
        if ext.endswith(('.zip', '.rar', '.7z')): ftype = "ZIP"
        elif ext.endswith('.apk'): ftype = "APK"
    elif message.video: 
        fid, ftype, orig_name = message.video.file_id, "Video", message.video.file_name
        fsize = getattr(message.video, 'file_size', 0)
        if getattr(message.video, 'thumbs', None): thumb_id = message.video.thumbs[0].file_id
    elif message.photo: 
        fid, ftype, thumb_id = message.photo[-1].file_id, "Image", message.photo[-1].file_id
        fsize = getattr(message.photo[-1], 'file_size', 0)
    elif message.audio or message.voice:
        audio_obj = message.audio or message.voice
        fid, ftype = audio_obj.file_id, "Audio"
        orig_name = getattr(audio_obj, 'file_name', None)
        fsize = getattr(audio_obj, 'file_size', 0)
        
    if fid:
        final_name = f"{custom_name} - {orig_name}" if custom_name and orig_name else custom_name or orig_name or f"{ftype}_{fid[-5:]}"
        save_to_cloud(fid, final_name, ftype, user_id, fsize, thumb_id)
        await message.reply_text(f"✅ Saved: `{final_name}`")

if __name__ == '__main__':
    upgrade_database_schema()
    global bot_app, bot_loop
    bot_app = app
    bot_loop = asyncio.get_event_loop_policy().get_event_loop()
    threading.Thread(target=lambda: ThreadingHTTPServer(('0.0.0.0', PORT), WebAppServer).serve_forever(), daemon=True).start()
    app.run()
