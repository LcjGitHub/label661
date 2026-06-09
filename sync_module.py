from datetime import datetime, timedelta
import json
import os
import random
import time
import traceback

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SYNC_CONFIG_FILE = os.path.join(BASE_DIR, "sync_config.json")
SYNC_LOG_FILE = os.path.join(BASE_DIR, "sync_logs.json")

DEFAULT_SYNC_CONFIG = {
    "interval_seconds": 60,
    "enabled": True,
    "api_url": "https://api.example.com/targets",
    "api_timeout": 10,
    "mock_mode": True
}

MOCK_TARGET_DATA = [
    {"name": "年度销售目标", "target": 1000000, "unit": "元", "base_current": 780000, "variance": 50000},
    {"name": "客户增长", "target": 500, "unit": "个", "base_current": 460, "variance": 30},
    {"name": "产品上线", "target": 12, "unit": "个", "base_current": 8, "variance": 2},
    {"name": "团队扩张", "target": 50, "unit": "人", "base_current": 23, "variance": 5},
    {"name": "用户满意度", "target": 95, "unit": "%", "base_current": 83.6, "variance": 5},
    {"name": "市场份额", "target": 25, "unit": "%", "base_current": 18, "variance": 3},
]


def load_sync_config():
    if os.path.exists(SYNC_CONFIG_FILE):
        try:
            with open(SYNC_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                for key, default_val in DEFAULT_SYNC_CONFIG.items():
                    if key not in config:
                        config[key] = default_val
                return config
        except (json.JSONDecodeError, IOError):
            return dict(DEFAULT_SYNC_CONFIG)
    return dict(DEFAULT_SYNC_CONFIG)


def save_sync_config(config):
    with open(SYNC_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_sync_logs():
    if os.path.exists(SYNC_LOG_FILE):
        try:
            with open(SYNC_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_sync_log(logs):
    with open(SYNC_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def add_sync_log(success, message="", details=None):
    logs = load_sync_logs()
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "success": success,
        "message": message,
        "details": details or {}
    }
    logs.insert(0, log_entry)
    logs = logs[:200]
    save_sync_log(logs)
    return logs


def fetch_mock_api_data():
    time.sleep(random.uniform(0.3, 1.2))
    if random.random() < 0.05:
        raise ConnectionError("模拟网络连接失败：无法连接到外部 API 服务器")

    data = []
    for item in MOCK_TARGET_DATA:
        variance = random.uniform(-item["variance"], item["variance"])
        current = max(0, item["base_current"] + variance)
        if item["target"] > 0:
            completion = round(current / item["target"] * 100, 2)
        else:
            completion = 0.0
        completion = min(100.0, max(0.0, completion))
        data.append({
            "name": item["name"],
            "target": item["target"],
            "current": round(current, 2),
            "unit": item["unit"],
            "completion": completion
        })
    return data


def fetch_external_api_data(config=None):
    if config is None:
        config = load_sync_config()

    if config.get("mock_mode", True):
        return fetch_mock_api_data()

    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            config.get("api_url", DEFAULT_SYNC_CONFIG["api_url"]),
            headers={"User-Agent": "TargetMonitor/1.0"}
        )
        timeout = config.get("api_timeout", DEFAULT_SYNC_CONFIG["api_timeout"])
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            api_data = json.loads(raw)
            parsed = []
            for item in api_data:
                name = item.get("name", "")
                target = float(item.get("target", 0))
                current = float(item.get("current", 0))
                unit = item.get("unit", "")
                completion = round(current / target * 100, 2) if target > 0 else 0.0
                parsed.append({
                    "name": name,
                    "target": target,
                    "current": current,
                    "unit": unit,
                    "completion": completion
                })
            return parsed
    except ImportError:
        return fetch_mock_api_data()
    except Exception as e:
        raise e


def calculate_next_sync(last_sync_time, interval_seconds):
    if last_sync_time is None:
        return None
    try:
        last_dt = datetime.strptime(last_sync_time, "%Y-%m-%d %H:%M:%S")
        next_dt = last_dt + timedelta(seconds=interval_seconds)
        return next_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def get_countdown(next_sync_str):
    if next_sync_str is None:
        return "--"
    try:
        next_dt = datetime.strptime(next_sync_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        delta = next_dt - now
        total_seconds = int(delta.total_seconds())
        if total_seconds <= 0:
            return "即将同步"
        mins, secs = divmod(total_seconds, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours}时{mins}分{secs}秒"
        elif mins > 0:
            return f"{mins}分{secs}秒"
        else:
            return f"{secs}秒"
    except Exception:
        return "--"


def perform_sync(config=None):
    if config is None:
        config = load_sync_config()

    start_time = datetime.now()
    try:
        data = fetch_external_api_data(config)
        elapsed = (datetime.now() - start_time).total_seconds()
        add_sync_log(
            success=True,
            message=f"同步成功，获取到 {len(data)} 条数据",
            details={"elapsed_seconds": round(elapsed, 2), "record_count": len(data)}
        )
        return {
            "success": True,
            "data": data,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": f"同步成功，获取到 {len(data)} 条数据",
            "elapsed": round(elapsed, 2)
        }
    except Exception as e:
        elapsed = (datetime.now() - start_time).total_seconds()
        error_detail = traceback.format_exc()
        add_sync_log(
            success=False,
            message=str(e),
            details={"elapsed_seconds": round(elapsed, 2), "traceback": error_detail}
        )
        return {
            "success": False,
            "data": None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": str(e),
            "elapsed": round(elapsed, 2),
            "error_detail": error_detail
        }
