# 🚀 Telegram Private Cloud Vault

A high-performance, multi-tenant cloud storage vault powered by **Hydrogram (MTProto)**, **PostgreSQL**, and a custom **Mini App Web UI**. This bot provides users with a completely isolated, secure environment to upload large files, preview media, and manage their cloud storage directly inside Telegram.

---

### 🛡️ Privacy & Security Architecture

The core philosophy of this project is **Zero-Leakage Data Isolation**.

* **Mathematical Siloing:** Every database query is strictly locked to the user's unique `Telegram User ID`. Data across tenants is mathematically isolated and inaccessible to any other user.
* **Alphanumeric Short-Codes:** Files are identified using randomized 6-character alphanumeric short-codes (e.g., `x8F2mA`) instead of sequential database IDs, eliminating enumeration and ID-guessing vulnerabilities.
* **Encrypted MTProto & Database Pipelines:** Uses direct MTProto binary protocols with native cryptographic acceleration via `tgcrypto`, alongside SSL-enforced connections to PostgreSQL.

---

### ✨ Key Features

* ⚡ **2 GB File Support via MTProto:** Native parallel upload and download engine bypassing the standard 50 MB HTTP Bot API upload and 20 MB download caps.
* 🖼️ **Visual Vault Mini App:** Integrated Telegram Web App frontend featuring an aesthetic 5×N grid, dynamic horizontal category filtering (`Images`, `Videos`, `Audio`, `ZIPs`, `APKs`), and instant client-side keyword search.
* 👁️ **In-App Media Previews:** Embedded modal previews for documents, photos, and high-resolution video thumbnails with direct in-app trash/delete actions.
* 🚀 **Deliver to Chat:** Instant retrieval pipeline utilizing cached media dispatch to send stored vault files directly into the active Telegram chat.
* 📤 **Album & MediaGroup Support:** Retains custom captions and folder tags across entire multi-file uploads simultaneously.
* 🗂️ **Dynamic Folder System:** Tag-based organization using `#hashtags` in captions, automatically generating clickable folder structures.
* 🕒 **Time-Based Filtering:** Search files across rolling weekly/monthly windows or specific historical date ranges.
* 🗑️ **30-Day Recycle Bin:** Soft-deletion workflow with automated cleanup jobs purging trashed items after 30 days.
* 📊 **Vault Analytics:** Storage metrics and category distributions computed directly from PostgreSQL.

---

### 🛠️ Tech Stack

* **Language:** Python 3.10+
* **Core Engine:** `hydrogram` (MTProto Client) + `tgcrypto` (C-accelerated cryptography)
* **Database Driver:** `pg8000` (Pure-Python PostgreSQL client with SSL support)
* **Web Server:** Custom Python `ThreadingHTTPServer` powering the Mini App API endpoints
* **Frontend:** Responsive HTML5 / CSS Grid / Vanilla JavaScript integrated with Telegram WebApp SDK

---

### 🚀 Getting Started

1. **Clone the Repository:**
```bash
git clone https://github.com/YOUR_USERNAME/your-repo-name.git
cd your-repo-name

```


2. **Install Dependencies:**
```bash
pip install -r requirements.txt

```


3. **Configure Environment Variables:**
* `BOT_TOKEN`: Telegram bot token from [@BotFather](https://t.me/BotFather).
* `API_ID`: Integer API ID obtained from [my.telegram.org](https://my.telegram.org).
* `API_HASH`: API hash string obtained from [my.telegram.org](https://my.telegram.org).
* `DATABASE_URL`: PostgreSQL connection URI (`postgresql://user:password@host:port/database`).
* `WEB_APP_URL`: Public HTTPS deployment URL serving the Mini App (e.g., `[https://your-service.onrender.com](https://your-service.onrender.com)`).
* `PORT`: Internal port for the web server (defaults to `8080`).


4. **Run the Bot:**
```bash
python bot.py

```
