import sqlite3
import time

DB_FILE = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            chat_id INTEGER PRIMARY KEY,
            snapshots INTEGER,
            social_snapshots INTEGER,
            quality TEXT,
            font_size TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_tasks (
            task_key TEXT PRIMARY KEY,
            url TEXT,
            task_type TEXT,
            created_at REAL
        )
    ''')
    conn.commit()
    conn.close()

def get_user_config(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT snapshots, social_snapshots, quality, font_size FROM user_settings WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "snapshots": bool(row[0]),
            "social_snapshots": bool(row[1]),
            "quality": row[2],
            "font_size": row[3]
        }
    else:
        default_config = {
            "snapshots": True,
            "social_snapshots": False,
            "quality": "720",
            "font_size": "large"
        }
        set_user_config(chat_id, default_config)
        return default_config

def set_user_config(chat_id, config):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_settings (chat_id, snapshots, social_snapshots, quality, font_size)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, int(config["snapshots"]), int(config["social_snapshots"]), config["quality"], config["font_size"]))
    conn.commit()
    conn.close()

def save_task(task_key, url, task_type):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO pending_tasks VALUES (?, ?, ?, ?)", (task_key, url, task_type, time.time()))
    conn.commit()
    conn.close()

def pop_task(task_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT url, task_type FROM pending_tasks WHERE task_key = ?", (task_key,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM pending_tasks WHERE task_key = ?", (task_key,))
        conn.commit()
        conn.close()
        return row[0], row[1]
    conn.close()
    return None, None    
    if row:
        return {
            "snapshots": bool(row[0]),
            "social_snapshots": bool(row[1]),
            "quality": row[2],
            "font_size": row[3]
        }
    else:
        default_config = {
            "snapshots": True,
            "social_snapshots": False,
            "quality": "720",
            "font_size": "large"
        }
        set_user_config(chat_id, default_config)
        return default_config

def set_user_config(chat_id, config):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_settings (chat_id, snapshots, social_snapshots, quality, font_size)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, int(config["snapshots"]), int(config["social_snapshots"]), config["quality"], config["font_size"]))
    conn.commit()
    conn.close()

def save_task(task_key, url, task_type):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO pending_tasks VALUES (?, ?, ?, ?)", (task_key, url, task_type, time.time()))
    conn.commit()
    conn.close()

def pop_task(task_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT url, task_type FROM pending_tasks WHERE task_key = ?", (task_key,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM pending_tasks WHERE task_key = ?", (task_key,))
        conn.commit()
        conn.close()
        return row[0], row[1]
    conn.close()
    return None, None        return {
            "snapshots": bool(row[0]),
            "social_snapshots": bool(row[1]),
            "quality": row[2],
            "font_size": row[3]
        }
    else:
        default_config = {
            "snapshots": True,
            "social_snapshots": True,  # تفعيل التقاط الصور افتراضياً لحل المشكلة
            "quality": "720",
            "font_size": "large"
        }
        set_user_config(chat_id, default_config)
        return default_config

def set_user_config(chat_id, config):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_settings (chat_id, snapshots, social_snapshots, quality, font_size)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, int(config["snapshots"]), int(config["social_snapshots"]), config["quality"], config["font_size"]))
    conn.commit()
    conn.close()

def save_task(task_key, url, task_type):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO pending_tasks VALUES (?, ?, ?, ?)", (task_key, url, task_type, time.time()))
    conn.commit()
    conn.close()

def pop_task(task_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT url, task_type FROM pending_tasks WHERE task_key = ?", (task_key,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM pending_tasks WHERE task_key = ?", (task_key,))
        conn.commit()
        conn.close()
        return row[0], row[1]
    conn.close()
    return None, None            "snapshots": bool(row[0]),
            "social_snapshots": bool(row[1]),
            "quality": row[2],
            "font_size": row[3]
        }
    else:
        default_config = {
            "snapshots": True,
            "social_snapshots": False,
            "quality": "720",
            "font_size": "large"
        }
        set_user_config(chat_id, default_config)
        return default_config

def set_user_config(chat_id, config):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_settings (chat_id, snapshots, social_snapshots, quality, font_size)
        VALUES (?, ?, ?, ?, ?)
    ''', (chat_id, int(config["snapshots"]), int(config["social_snapshots"]), config["quality"], config["font_size"]))
    conn.commit()
    conn.close()

def save_task(task_key, url, task_type):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO pending_tasks VALUES (?, ?, ?, ?)", (task_key, url, task_type, time.time()))
    conn.commit()
    conn.close()

def pop_task(task_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT url, task_type FROM pending_tasks WHERE task_key = ?", (task_key,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM pending_tasks WHERE task_key = ?", (task_key,))
        conn.commit()
        conn.close()
        return row[0], row[1]
    conn.close()
    return None, None
