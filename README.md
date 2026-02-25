

# 🚀 Telegram Private Cloud Vault

A robust, multi-tenant cloud storage solution built with **Python**, **PostgreSQL**, and the **Telegram API**. This bot provides users with a completely isolated and secure environment to store, organize, and share their media and documents.

### 🛡️ Privacy & Security Architecture

The core philosophy of this project is **Zero-Leakage Data Isolation**.

* **Mathematical Siloing:** Every database query is strictly locked to the user's unique `Telegram User ID`. This ensures that data is mathematically inaccessible to any other entity.
* **Alphanumeric Hash Codes:** Instead of predictable sequence numbers (1, 2, 3...), this system generates unique 6-character short-codes (e.g., `x8F2mA`) for every file. This prevents "ID-guessing" attacks and protects the total file count of the database.
* **Encrypted Connections:** All data transitions between the Telegram servers, the Python application, and the PostgreSQL database are handled via SSL-encrypted tunnels.

### ✨ Key Features

* 📤 **Bulk Uploading:** Sophisticated `MediaGroup` memory logic that applies hashtags and captions to entire photo/video albums automatically.
* 🗂️ **Dynamic Folder System:** Instant organization using `#hashtags` in captions. The bot dynamically generates folder menus based on your unique tags.
* 🕒 **Time-Travel Search:** Search your vault by timeframe (e.g., "Past 2 weeks") or specific date ranges.
* 🗑️ **Recycle Bin (30-Day Trash):** Deleted files are moved to a temporary trash bin and are automatically purged after 30 days of inactivity.
* 📊 **Vault Analytics:** Real-time breakdown of your storage usage and file categories.

### 🛠️ Tech Stack

* **Language:** Python 3.10+
* **Framework:** `python-telegram-bot` (Asynchronous)
* **Database:** PostgreSQL (Optimized with Indexing for fast retrieval)
* **Server:** Hosted on Render with an integrated HTTP keep-alive server.

### 🚀 Getting Started

1. **Clone the Repository:** `git clone https://github.com/YOUR_USERNAME/your-repo-name.git`
2. **Install Requirements:** `pip install -r requirements.txt`
3. **Set Environment Variables:**
* `BOT_TOKEN`: Your Telegram Bot API token.
* `DATABASE_URL`: Your PostgreSQL connection string.



---

