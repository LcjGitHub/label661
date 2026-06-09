from datetime import datetime
from database import (
    get_user_subscriptions,
    has_triggered,
    record_triggered,
    clear_triggered,
    create_notification_log,
    get_visible_targets,
)


NOTIFICATION_TYPES = {
    "threshold_50": {
        "name": "完成度达到 50%",
        "threshold": 50,
        "icon": "📊",
        "message_template": "目标「{target_name}」完成度已达到 50%（当前：{completion}%）"
    },
    "threshold_80": {
        "name": "完成度达到 80%",
        "threshold": 80,
        "icon": "🚀",
        "message_template": "目标「{target_name}」完成度已达到 80%（当前：{completion}%），即将完成！"
    },
    "threshold_100": {
        "name": "完成度达到 100%",
        "threshold": 100,
        "icon": "🎉",
        "message_template": "恭喜！目标「{target_name}」已 100% 完成！"
    },
    "threshold_custom": {
        "name": "自定义阈值",
        "icon": "🎯",
        "message_template": "目标「{target_name}」完成度达到自定义阈值（当前：{completion}%，阈值：{threshold}%）"
    },
    "notify_on_drop": {
        "name": "进度下降提醒",
        "icon": "⚠️",
        "message_template": "注意：目标「{target_name}」完成度从 {prev_completion}% 下降到 {completion}%"
    }
}


def get_notification_display(subscription):
    types = []
    if subscription["threshold_50"]:
        types.append("50%")
    if subscription["threshold_80"]:
        types.append("80%")
    if subscription["threshold_100"]:
        types.append("100%")
    if subscription["threshold_custom"]:
        types.append(f"自定义({subscription['threshold_custom']}%)")
    if subscription["notify_on_drop"]:
        types.append("下降提醒")
    return "、".join(types) if types else "未选择"


def check_and_trigger_notifications(current_targets, prev_targets_map=None):
    triggered_notifications = []
    prev_targets_map = prev_targets_map or {}

    target_id_to_current = {}
    for t in current_targets:
        if isinstance(t, dict) and t.get("id"):
            target_id_to_current[t["id"]] = t

    all_subs = []
    checked_user_ids = set()

    for t in current_targets:
        if isinstance(t, dict) and t.get("user_id"):
            uid = t["user_id"]
            if uid not in checked_user_ids:
                checked_user_ids.add(uid)
                all_subs.extend(get_user_subscriptions(uid))

    import sqlite3
    from database import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT DISTINCT user_id FROM user_subscriptions")
        rows = cursor.fetchall()
        for row in rows:
            uid = row["user_id"]
            if uid not in checked_user_ids:
                checked_user_ids.add(uid)
                all_subs.extend(get_user_subscriptions(uid))
    finally:
        conn.close()

    for sub in all_subs:
        user_id = sub["user_id"]
        target_id = sub["target_id"]
        current = target_id_to_current.get(target_id)
        if not current:
            continue

        current_completion = float(current["completion"])
        target_name = current["name"]

        threshold_checks = [
            ("threshold_50", sub["threshold_50"], 50),
            ("threshold_80", sub["threshold_80"], 80),
            ("threshold_100", sub["threshold_100"], 100),
        ]

        for notif_type, enabled, threshold in threshold_checks:
            if enabled and current_completion >= threshold:
                if not has_triggered(user_id, target_id, notif_type):
                    type_info = NOTIFICATION_TYPES[notif_type]
                    message = type_info["message_template"].format(
                        target_name=target_name,
                        completion=current_completion
                    )
                    create_notification_log(
                        user_id=user_id,
                        target_id=target_id,
                        target_name=target_name,
                        notification_type=notif_type,
                        message=message,
                        completion=current_completion,
                        threshold=threshold
                    )
                    record_triggered(user_id, target_id, notif_type)
                    triggered_notifications.append({
                        "user_id": user_id,
                        "target_id": target_id,
                        "target_name": target_name,
                        "notification_type": notif_type,
                        "message": message,
                        "completion": current_completion,
                        "threshold": threshold
                    })

        if sub["threshold_custom"] and sub["threshold_custom"] > 0:
            custom_threshold = float(sub["threshold_custom"])
            if current_completion >= custom_threshold:
                notif_type = "threshold_custom"
                if not has_triggered(user_id, target_id, notif_type):
                    type_info = NOTIFICATION_TYPES[notif_type]
                    message = type_info["message_template"].format(
                        target_name=target_name,
                        completion=current_completion,
                        threshold=custom_threshold
                    )
                    create_notification_log(
                        user_id=user_id,
                        target_id=target_id,
                        target_name=target_name,
                        notification_type=notif_type,
                        message=message,
                        completion=current_completion,
                        threshold=custom_threshold
                    )
                    record_triggered(user_id, target_id, notif_type)
                    triggered_notifications.append({
                        "user_id": user_id,
                        "target_id": target_id,
                        "target_name": target_name,
                        "notification_type": notif_type,
                        "message": message,
                        "completion": current_completion,
                        "threshold": custom_threshold
                    })

        if sub["notify_on_drop"] and target_id in prev_targets_map:
            prev_t = prev_targets_map[target_id]
            if isinstance(prev_t, dict):
                prev_completion = float(prev_t["completion"])
                if current_completion < prev_completion:
                    notif_type = "notify_on_drop"
                    type_info = NOTIFICATION_TYPES[notif_type]
                    message = type_info["message_template"].format(
                        target_name=target_name,
                        completion=current_completion,
                        prev_completion=prev_completion
                    )
                    create_notification_log(
                        user_id=user_id,
                        target_id=target_id,
                        target_name=target_name,
                        notification_type=notif_type,
                        message=message,
                        completion=current_completion,
                        threshold=prev_completion
                    )
                    triggered_notifications.append({
                        "user_id": user_id,
                        "target_id": target_id,
                        "target_name": target_name,
                        "notification_type": notif_type,
                        "message": message,
                        "completion": current_completion,
                        "threshold": prev_completion
                    })

        if current_completion < 50:
            for notif_type in ["threshold_50", "threshold_80", "threshold_100"]:
                if has_triggered(user_id, target_id, notif_type):
                    threshold_val = NOTIFICATION_TYPES[notif_type]["threshold"]
                    if current_completion < threshold_val:
                        clear_triggered(user_id, target_id)
                        break

    return triggered_notifications
