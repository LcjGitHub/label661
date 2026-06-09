from datetime import datetime
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ALERT_LEVELS = {
    "low": {"name": "低预警", "color": "#FF5252", "border_color": "#D50000"},
    "medium": {"name": "中预警", "color": "#FF5252", "border_color": "#D50000"},
    "high": {"name": "高预警", "color": "#FF5252", "border_color": "#D50000"},
}

DEFAULT_ALERT_CONFIG = {
    "年度销售目标": {"threshold": 80, "level": "medium"},
    "客户增长": {"threshold": 85, "level": "low"},
    "产品上线": {"threshold": 70, "level": "medium"},
    "团队扩张": {"threshold": 60, "level": "high"},
    "用户满意度": {"threshold": 90, "level": "low"},
    "市场份额": {"threshold": 75, "level": "medium"},
}

ALERT_CONFIG_FILE = os.path.join(BASE_DIR, "alert_config.json")
ALERT_LOG_FILE = os.path.join(BASE_DIR, "alert_logs.json")


def load_alert_config():
    if os.path.exists(ALERT_CONFIG_FILE):
        try:
            with open(ALERT_CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                for key in DEFAULT_ALERT_CONFIG:
                    if key not in config:
                        config[key] = DEFAULT_ALERT_CONFIG[key]
                return config
        except (json.JSONDecodeError, IOError):
            return dict(DEFAULT_ALERT_CONFIG)
    return dict(DEFAULT_ALERT_CONFIG)


def save_alert_config(config):
    with open(ALERT_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_alert_logs():
    if os.path.exists(ALERT_LOG_FILE):
        try:
            with open(ALERT_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_alert_log(logs):
    with open(ALERT_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def check_alerts(data, config, is_initial_load=False):
    triggered = []
    for item in data:
        name = item["name"]
        if name in config:
            threshold = config[name]["threshold"]
            level = config[name]["level"]
            if item["completion"] < threshold:
                triggered.append({
                    "name": name,
                    "completion": item["completion"],
                    "threshold": threshold,
                    "level": level,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
    return triggered


def add_alert_log(triggered_alerts):
    logs = load_alert_logs()
    for alert in triggered_alerts:
        logs.insert(0, alert)
    logs = logs[:100]
    save_alert_log(logs)
    return logs


def detect_and_log_alerts(data, config, prev_triggered_names=None, is_initial_load=False):
    triggered = check_alerts(data, config)

    if is_initial_load:
        if triggered:
            updated_logs = add_alert_log(triggered)
        else:
            updated_logs = load_alert_logs()
    else:
        prev_names = set(prev_triggered_names or [])
        new_triggered = [a for a in triggered if a["name"] not in prev_names]
        if new_triggered:
            updated_logs = add_alert_log(new_triggered)
        else:
            updated_logs = load_alert_logs()

    return triggered, updated_logs
