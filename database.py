import sqlite3
import os
import hashlib
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "monitor.db")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            current REAL NOT NULL,
            unit TEXT DEFAULT '',
            completion REAL NOT NULL,
            user_id INTEGER NOT NULL,
            is_public INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS target_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            target_name TEXT NOT NULL,
            slope REAL NOT NULL,
            intercept REAL NOT NULL,
            avg_growth_rate REAL NOT NULL,
            predicted_completion_date TEXT,
            completion_probability REAL NOT NULL,
            data_points INTEGER NOT NULL,
            r_squared REAL,
            predicted_at TEXT NOT NULL,
            FOREIGN KEY (target_id) REFERENCES targets (id)
        )
    """)

    conn.commit()
    conn.close()


def register_user(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return {"success": True, "user_id": user_id, "username": username}
    except sqlite3.IntegrityError:
        conn.close()
        return {"success": False, "error": "用户名已存在"}
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e)}


def login_user(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,)
    )
    user = cursor.fetchone()
    conn.close()

    if user is None:
        return {"success": False, "error": "用户不存在"}
    if user["password_hash"] != hash_password(password):
        return {"success": False, "error": "密码错误"}
    return {"success": True, "user_id": user["id"], "username": user["username"]}


def get_user_by_id(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return {"user_id": user["id"], "username": user["username"]}
    return None


def create_target(name, target, current, unit, user_id, is_public=0):
    if target > 0:
        completion = round(current / target * 100, 2)
    else:
        completion = 0.0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO targets (name, target, current, unit, completion, user_id, is_public, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, target, current, unit, completion, user_id, is_public, now, now)
    )
    conn.commit()
    target_id = cursor.lastrowid
    conn.close()
    return get_target_by_id(target_id)


def get_target_by_id(target_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM targets WHERE id = ?", (target_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row_to_target_dict(row)
    return None


def row_to_target_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "target": row["target"],
        "current": row["current"],
        "unit": row["unit"],
        "completion": row["completion"],
        "user_id": row["user_id"],
        "is_public": row["is_public"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }


def get_user_targets(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM targets WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [row_to_target_dict(r) for r in rows]


def get_public_targets():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM targets WHERE is_public = 1 ORDER BY created_at DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [row_to_target_dict(r) for r in rows]


def get_visible_targets(user_id=None):
    if user_id is None:
        return get_public_targets()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT * FROM targets WHERE user_id = ? OR is_public = 1
           ORDER BY CASE WHEN user_id = ? THEN 0 ELSE 1 END, created_at DESC""",
        (user_id, user_id)
    )
    rows = cursor.fetchall()
    conn.close()
    return [row_to_target_dict(r) for r in rows]


def update_target(target_id, user_id, name=None, target=None, current=None, unit=None, is_public=None):
    existing = get_target_by_id(target_id)
    if existing is None:
        return {"success": False, "error": "目标不存在"}
    if existing["user_id"] != user_id:
        return {"success": False, "error": "无权修改此目标"}

    updates = {}
    if name is not None:
        updates["name"] = name
    if target is not None:
        updates["target"] = target
    if current is not None:
        updates["current"] = current
    if unit is not None:
        updates["unit"] = unit
    if is_public is not None:
        updates["is_public"] = is_public

    if "target" in updates or "current" in updates:
        t = updates.get("target", existing["target"])
        c = updates.get("current", existing["current"])
        updates["completion"] = round(c / t * 100, 2) if t > 0 else 0.0

    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not updates:
        return {"success": True, "target": existing}

    set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
    values = list(updates.values()) + [target_id]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE targets SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()

    return {"success": True, "target": get_target_by_id(target_id)}


def delete_target(target_id, user_id):
    existing = get_target_by_id(target_id)
    if existing is None:
        return {"success": False, "error": "目标不存在"}
    if existing["user_id"] != user_id:
        return {"success": False, "error": "无权删除此目标"}

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM targets WHERE id = ?", (target_id,))
    conn.commit()
    conn.close()
    return {"success": True}


def seed_default_public_data():
    public_targets = get_public_targets()
    if public_targets:
        return

    default_data = [
        {"name": "年度销售目标", "completion": 78, "target": 1000000, "current": 780000, "unit": "元"},
        {"name": "客户增长", "completion": 92, "target": 500, "current": 460, "unit": "个"},
        {"name": "产品上线", "completion": 65, "target": 12, "current": 8, "unit": "个"},
        {"name": "团队扩张", "completion": 45, "target": 50, "current": 23, "unit": "人"},
        {"name": "用户满意度", "completion": 88, "target": 95, "current": 83.6, "unit": "%"},
        {"name": "市场份额", "completion": 72, "target": 25, "current": 18, "unit": "%"},
    ]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
        ("system", hash_password("system_default"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()

    cursor.execute("SELECT id FROM users WHERE username = ?", ("system",))
    system_user = cursor.fetchone()
    system_user_id = system_user["id"]

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for item in default_data:
        cursor.execute(
            """INSERT INTO targets (name, target, current, unit, completion, user_id, is_public, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (item["name"], item["target"], item["current"], item["unit"], item["completion"], system_user_id, now, now)
        )
    conn.commit()
    conn.close()


def save_prediction(target_id, target_name, slope, intercept, avg_growth_rate,
                    predicted_completion_date, completion_probability, data_points, r_squared=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM target_predictions WHERE target_id = ?", (target_id,))
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            """UPDATE target_predictions
               SET target_name = ?, slope = ?, intercept = ?, avg_growth_rate = ?,
                   predicted_completion_date = ?, completion_probability = ?,
                   data_points = ?, r_squared = ?, predicted_at = ?
               WHERE target_id = ?""",
            (target_name, slope, intercept, avg_growth_rate,
             predicted_completion_date, completion_probability, data_points, r_squared, now, target_id)
        )
        prediction_id = existing["id"]
    else:
        cursor.execute(
            """INSERT INTO target_predictions
               (target_id, target_name, slope, intercept, avg_growth_rate,
                predicted_completion_date, completion_probability, data_points, r_squared, predicted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (target_id, target_name, slope, intercept, avg_growth_rate,
             predicted_completion_date, completion_probability, data_points, r_squared, now)
        )
        prediction_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return get_prediction_by_id(prediction_id)


def get_prediction_by_id(prediction_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM target_predictions WHERE id = ?", (prediction_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row_to_prediction_dict(row)
    return None


def row_to_prediction_dict(row):
    return {
        "id": row["id"],
        "target_id": row["target_id"],
        "target_name": row["target_name"],
        "slope": row["slope"],
        "intercept": row["intercept"],
        "avg_growth_rate": row["avg_growth_rate"],
        "predicted_completion_date": row["predicted_completion_date"],
        "completion_probability": row["completion_probability"],
        "data_points": row["data_points"],
        "r_squared": row["r_squared"],
        "predicted_at": row["predicted_at"]
    }


def get_latest_prediction_by_target(target_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM target_predictions WHERE target_id = ? ORDER BY predicted_at DESC LIMIT 1",
        (target_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return row_to_prediction_dict(row)
    return None


def get_latest_prediction_by_target_name(target_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM target_predictions WHERE target_name = ? ORDER BY predicted_at DESC LIMIT 1",
        (target_name,)
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return row_to_prediction_dict(row)
    return None


def get_all_predictions_by_target(target_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM target_predictions WHERE target_id = ? ORDER BY predicted_at DESC",
        (target_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [row_to_prediction_dict(r) for r in rows]


def _get_time_range_start(time_range):
    now = datetime.now()
    if time_range == "week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    elif time_range == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start.strftime("%Y-%m-%d %H:%M:%S")
    else:
        return None


def _row_to_ranking_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "target": row["target"],
        "current": row["current"],
        "unit": row["unit"],
        "completion": row["completion"],
        "user_id": row["user_id"],
        "is_public": row["is_public"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "username": row["username"] if "username" in row.keys() else None
    }


def get_ranking_targets(user_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    base_query = """
        SELECT t.*, u.username
        FROM targets t
        LEFT JOIN users u ON t.user_id = u.id
        WHERE 1=1
    """
    params = []

    if user_id is not None:
        base_query += " AND (t.user_id = ? OR t.is_public = 1)"
        params.append(user_id)
    else:
        base_query += " AND t.is_public = 1"

    base_query += " ORDER BY t.completion DESC"

    cursor.execute(base_query, params)
    rows = cursor.fetchall()
    conn.close()
    return [_row_to_ranking_dict(r) for r in rows]


init_db()
seed_default_public_data()
