import dash
from dash import html, dcc, Input, Output, State, callback_context, no_update, ALL
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
import os
import io
import base64
import pandas as pd
import secrets

from alert_module import (
    ALERT_LEVELS,
    DEFAULT_ALERT_CONFIG,
    load_alert_config,
    save_alert_config,
    load_alert_logs,
    check_alerts,
    add_alert_log,
    detect_and_log_alerts
)

from sync_module import (
    DEFAULT_SYNC_CONFIG,
    load_sync_config,
    save_sync_config,
    load_sync_logs,
    add_sync_log,
    perform_sync,
    calculate_next_sync,
    get_countdown
)

from database import (
    register_user,
    login_user,
    get_user_by_id,
    create_target,
    update_target,
    delete_target,
    get_visible_targets,
    get_target_by_id,
    save_prediction,
    get_latest_prediction_by_target,
    get_latest_prediction_by_target_name,
    get_ranking_targets,
    get_targets_by_ids,
    get_targets_by_names,
    calculate_comparison_metrics
)

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "目标完成进度监控"
app.server.secret_key = secrets.token_hex(32)

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_snapshots.json")
EXPECTED_COLUMNS = ["目标名称", "当前值", "目标值", "完成率", "单位"]
EXPECTED_COLUMNS_EN = ["name", "current", "target", "completion", "unit"]


def export_data_to_file(data, file_format="csv"):
    export_list = []
    for item in data:
        export_list.append({
            "目标名称": item["name"],
            "当前值": item["current"],
            "目标值": item["target"],
            "完成率": item["completion"],
            "单位": item["unit"]
        })
    df = pd.DataFrame(export_list, columns=EXPECTED_COLUMNS)

    buffer = io.BytesIO()
    if file_format == "xlsx":
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="目标数据")
        buffer.seek(0)
        filename = f"目标数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        csv_str = df.to_csv(index=False, encoding="utf-8-sig")
        buffer.write(csv_str.encode("utf-8-sig"))
        buffer.seek(0)
        filename = f"目标数据_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        mime = "text/csv"

    b64 = base64.b64encode(buffer.read()).decode("utf-8")
    return dict(content=b64, filename=filename, mimetype=mime, base64=True)


def parse_uploaded_file(contents, filename):
    if contents is None:
        return None, "未选择文件"

    try:
        content_type, content_string = contents.split(",")
        decoded = base64.b64decode(content_string)
    except Exception:
        return None, "文件内容解析失败"

    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.StringIO(decoded.decode("utf-8-sig")))
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(decoded))
        else:
            return None, "不支持的文件格式，请上传 .csv 或 .xlsx 文件"
    except Exception as e:
        return None, f"文件读取失败：{str(e)}"

    cols = [c.strip() for c in df.columns.tolist()]
    has_zh = all(c in cols for c in EXPECTED_COLUMNS)
    has_en = all(c in cols for c in EXPECTED_COLUMNS_EN)

    if not has_zh and not has_en:
        return None, (f"数据格式错误！文件必须包含以下列：{', '.join(EXPECTED_COLUMNS)} "
                      f"或 {', '.join(EXPECTED_COLUMNS_EN)}。当前列：{', '.join(cols)}")

    col_map = dict(zip(EXPECTED_COLUMNS, EXPECTED_COLUMNS_EN)) if has_zh else dict(zip(EXPECTED_COLUMNS_EN, EXPECTED_COLUMNS_EN))
    df.columns = [col_map.get(c.strip(), c.strip()) for c in df.columns]

    parsed_data = []
    errors = []
    for idx, row in df.iterrows():
        row_num = idx + 2
        try:
            name = str(row["name"]).strip()
            if not name or name.lower() == "nan":
                errors.append(f"第 {row_num} 行：目标名称为空")
                continue

            try:
                current = float(row["current"])
            except (ValueError, TypeError):
                errors.append(f"第 {row_num} 行：当前值不是有效数字")
                continue

            try:
                target = float(row["target"])
            except (ValueError, TypeError):
                errors.append(f"第 {row_num} 行：目标值不是有效数字")
                continue

            try:
                completion = float(row["completion"])
                if completion < 0 or completion > 100:
                    errors.append(f"第 {row_num} 行：完成率应在 0-100 之间")
                    continue
            except (ValueError, TypeError):
                errors.append(f"第 {row_num} 行：完成率不是有效数字")
                continue

            unit = str(row["unit"]).strip() if pd.notna(row["unit"]) else ""
            if unit.lower() == "nan":
                unit = ""

            if target > 0:
                calc_completion = round(current / target * 100, 2)
            else:
                calc_completion = 0.0

            parsed_data.append({
                "name": name,
                "current": current,
                "target": target,
                "completion": calc_completion,
                "unit": unit
            })
        except Exception as e:
            errors.append(f"第 {row_num} 行：解析错误 - {str(e)}")

    if errors:
        return None, "\n".join(errors)

    if not parsed_data:
        return None, "文件中没有有效的数据行"

    return parsed_data, None


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_snapshot(data):
    history = load_history()
    snapshot = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "targets": [dict(item) for item in data]
    }
    history.append(snapshot)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    return history


def get_target_names(data=None):
    if data is None:
        data = get_visible_targets()
    return [item["name"] for item in data]


def simple_linear_regression(x_values, y_values):
    n = len(x_values)
    if n < 2:
        return None
    sum_x = sum(x_values)
    sum_y = sum(y_values)
    sum_xy = sum(x * y for x, y in zip(x_values, y_values))
    sum_x2 = sum(x * x for x in x_values)
    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return None
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_values, y_values))
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in y_values)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
    return {"slope": slope, "intercept": intercept, "r_squared": r_squared}


def calculate_completion_probability(r_squared, data_points, avg_growth_rate):
    if avg_growth_rate <= 0:
        return 0.0
    base_prob = min(0.95, max(0.1, r_squared)) if r_squared is not None else 0.5
    data_factor = min(1.0, data_points / 10.0)
    growth_factor = min(1.0, avg_growth_rate / 5.0) if avg_growth_rate > 0 else 0.0
    probability = base_prob * (0.3 + 0.35 * data_factor + 0.35 * growth_factor)
    return round(min(0.99, max(0.0, probability)) * 100, 2)


def predict_target_completion(target_name, history, targets_data=None):
    timestamps = []
    completions = []
    for snapshot in history:
        for target in snapshot["targets"]:
            if target["name"] == target_name:
                timestamps.append(snapshot["timestamp"])
                completions.append(target["completion"])
                break

    if len(timestamps) < 2:
        return None

    try:
        base_dt = datetime.strptime(timestamps[0], "%Y-%m-%d %H:%M:%S")
        x_hours = []
        for ts in timestamps:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            delta = (dt - base_dt).total_seconds() / 3600.0
            x_hours.append(delta)
    except ValueError:
        return None

    target_id = None
    if targets_data:
        for t in targets_data:
            if t["name"] == target_name:
                target_id = t.get("id")
                break

    if target_id:
        try:
            saved = get_latest_prediction_by_target(target_id)
            if saved and saved.get("data_points") == len(timestamps):
                return {
                    "target_name": target_name,
                    "slope": saved["slope"],
                    "intercept": saved["intercept"],
                    "r_squared": saved["r_squared"],
                    "avg_growth_rate": round(saved["avg_growth_rate"], 4),
                    "predicted_completion_date": saved["predicted_completion_date"],
                    "completion_probability": saved["completion_probability"],
                    "data_points": saved["data_points"],
                    "base_datetime": base_dt,
                    "timestamps": timestamps,
                    "completions": completions,
                    "x_hours": x_hours
                }
        except Exception:
            pass

    regression = simple_linear_regression(x_hours, completions)
    if regression is None:
        return None

    slope = regression["slope"]
    intercept = regression["intercept"]
    r_squared = regression["r_squared"]

    if len(x_hours) >= 2:
        total_hours = x_hours[-1] - x_hours[0]
        total_growth = completions[-1] - completions[0]
        avg_growth_rate = (total_growth / total_hours * 24) if total_hours > 0 else 0
    else:
        avg_growth_rate = slope * 24

    predicted_completion_date = None
    if slope > 0:
        hours_to_100 = (100 - intercept) / slope
        if hours_to_100 >= 0:
            from datetime import timedelta
            completion_dt = base_dt + timedelta(hours=hours_to_100)
            predicted_completion_date = completion_dt.strftime("%Y-%m-%d %H:%M:%S")

    probability = calculate_completion_probability(r_squared, len(timestamps), avg_growth_rate)

    if target_id and predicted_completion_date:
        try:
            save_prediction(
                target_id=target_id,
                target_name=target_name,
                slope=slope,
                intercept=intercept,
                avg_growth_rate=avg_growth_rate,
                predicted_completion_date=predicted_completion_date,
                completion_probability=probability,
                data_points=len(timestamps),
                r_squared=r_squared
            )
        except Exception:
            pass

    return {
        "target_name": target_name,
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "avg_growth_rate": round(avg_growth_rate, 4),
        "predicted_completion_date": predicted_completion_date,
        "completion_probability": probability,
        "data_points": len(timestamps),
        "base_datetime": base_dt,
        "timestamps": timestamps,
        "completions": completions,
        "x_hours": x_hours
    }


def generate_prediction_line(prediction, num_points=20):
    if prediction is None:
        return [], []
    base_dt = prediction["base_datetime"]
    x_hours = prediction["x_hours"]
    slope = prediction["slope"]
    intercept = prediction["intercept"]

    pred_x = []
    pred_y = []

    if slope > 0:
        hours_to_100 = (100 - intercept) / slope
        if hours_to_100 > 0:
            max_hours = max(max(x_hours) if x_hours else 0, hours_to_100)
        else:
            max_hours = max(x_hours) if x_hours else 0
    else:
        max_hours = max(x_hours) if x_hours else 0
        max_hours = max_hours * 1.5

    min_hours = min(x_hours) if x_hours else 0
    step = (max_hours - min_hours) / max(1, num_points - 1)

    from datetime import timedelta
    for i in range(num_points):
        h = min_hours + i * step
        y = slope * h + intercept
        y = max(0, min(100, y))
        dt = base_dt + timedelta(hours=h)
        pred_x.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
        pred_y.append(round(y, 2))

    return pred_x, pred_y


def create_prediction_info_panel(predictions):
    if not predictions:
        return html.Div("选择目标后将显示预测信息", className="prediction-info-empty")

    items = []
    for pred in predictions:
        if pred is None:
            continue
        target_name = pred["target_name"]
        prob = pred["completion_probability"]
        avg_growth = pred["avg_growth_rate"]
        pred_date = pred["predicted_completion_date"]
        data_pts = pred["data_points"]
        r2 = pred["r_squared"]

        no_growth = avg_growth <= 0
        if no_growth:
            prob_display = "暂无"
            prob_color = "#FF5252"
        else:
            prob_display = f"{prob}%"
            if prob >= 80:
                prob_color = "#00C853"
            elif prob >= 50:
                prob_color = "#FFB300"
            else:
                prob_color = "#FF5252"

        pred_date_text = pred_date if pred_date else "无法预测（增长趋势不明显）"
        r2_text = f"{r2:.4f}" if r2 is not None else "暂无"

        items.append(
            html.Div([
                html.Div([
                    html.Span(f"🎯 {target_name}", className="prediction-target-name"),
                    html.Span(
                        prob_display,
                        className="prediction-probability",
                        style={"color": prob_color}
                    )
                ], className="prediction-header-row"),
                html.Div([
                    html.Div([
                        html.Span("预计完成日期：", className="prediction-label"),
                        html.Span(pred_date_text, className="prediction-value prediction-date")
                    ], className="prediction-row"),
                    html.Div([
                        html.Span("日均增长速度：", className="prediction-label"),
                        html.Span(f"{avg_growth:.4f}%/天", className="prediction-value")
                    ], className="prediction-row"),
                    html.Div([
                        html.Span("数据点数：", className="prediction-label"),
                        html.Span(f"{data_pts} 个快照", className="prediction-value")
                    ], className="prediction-row"),
                    html.Div([
                        html.Span("拟合优度：", className="prediction-label"),
                        html.Span(r2_text, className="prediction-value")
                    ], className="prediction-row")
                ], className="prediction-details")
            ], className="prediction-item")
        )

    return html.Div(items, className="prediction-info-list")


def calculate_target_progress(target_name, history, current_completion, target_created_at=None):
    from datetime import datetime as dt
    created_dt = None
    if target_created_at:
        try:
            created_dt = dt.strptime(target_created_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            created_dt = None

    earliest_completion = None
    earliest_timestamp = None

    for snapshot in history:
        try:
            snap_dt = dt.strptime(snapshot["timestamp"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if created_dt is not None and snap_dt < created_dt:
            continue
        for target in snapshot["targets"]:
            if target["name"] == target_name:
                if earliest_timestamp is None or snap_dt < earliest_timestamp:
                    earliest_timestamp = snap_dt
                    earliest_completion = target["completion"]
                break

    if earliest_completion is not None:
        progress = round(current_completion - earliest_completion, 2)
        return progress, earliest_completion, current_completion
    return 0.0, current_completion, current_completion


def _get_ranking_time_range_start(time_range):
    from datetime import datetime as dt
    now = dt.now()
    if time_range == "week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start
    return None


def filter_targets_by_time_range(targets_data, history, time_range="all"):
    from datetime import datetime as dt
    if time_range == "all":
        return targets_data

    start_time = _get_ranking_time_range_start(time_range)
    if start_time is None:
        return targets_data

    target_names_in_range = set()
    for snapshot in history:
        try:
            snap_dt = dt.strptime(snapshot["timestamp"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if snap_dt >= start_time:
            for target in snapshot["targets"]:
                target_names_in_range.add(target["name"])

    filtered = []
    for item in targets_data:
        try:
            created_dt = dt.strptime(item["created_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError):
            created_dt = None
        created_in_range = created_dt is not None and created_dt >= start_time
        has_snapshot_in_range = item["name"] in target_names_in_range
        if created_in_range or has_snapshot_in_range:
            filtered.append(item)
    return filtered


def get_completion_top5(targets_data, history):
    ranked = sorted(targets_data, key=lambda x: x["completion"], reverse=True)
    top5 = ranked[:5]
    result = []
    for idx, item in enumerate(top5):
        progress, initial, current = calculate_target_progress(
            item["name"], history, item["completion"],
            target_created_at=item.get("created_at")
        )
        result.append({
            **item,
            "rank": idx + 1,
            "progress": progress,
            "initial_completion": initial if initial is not None else item["completion"]
        })
    return result


def get_progress_top5(targets_data, history):
    targets_with_progress = []
    for item in targets_data:
        progress, initial, current = calculate_target_progress(
            item["name"], history, item["completion"],
            target_created_at=item.get("created_at")
        )
        targets_with_progress.append({
            **item,
            "progress": progress,
            "initial_completion": initial if initial is not None else item["completion"],
            "current_completion": current if current is not None else item["completion"]
        })
    ranked = sorted(targets_with_progress, key=lambda x: x["progress"], reverse=True)
    top5 = ranked[:5]
    for idx, item in enumerate(top5):
        item["rank"] = idx + 1
    return top5


def _get_rank_badge(rank):
    colors = {
        1: ("#FFD700", "#FFF8DC"),
        2: ("#C0C0C0", "#F5F5F5"),
        3: ("#CD7F32", "#FFF5EE"),
    }
    if rank in colors:
        color, bg = colors[rank]
        return html.Span(
            str(rank),
            className="ranking-badge",
            style={"background": bg, "color": color, "border": f"2px solid {color}"}
        )
    return html.Span(str(rank), className="ranking-badge ranking-badge-normal")


def create_ranking_item(item, show_progress=False):
    progress_display = ""
    progress_class = ""
    if show_progress and "progress" in item:
        p = item["progress"]
        if p > 0:
            progress_display = f"+{p}%"
            progress_class = "ranking-progress-up"
        elif p < 0:
            progress_display = f"{p}%"
            progress_class = "ranking-progress-down"
        else:
            progress_display = "0%"
            progress_class = "ranking-progress-flat"

    completion = item.get("completion", 0)
    if completion >= 90:
        bar_color = "linear-gradient(90deg, #00C853, #64DD17)"
    elif completion >= 70:
        bar_color = "linear-gradient(90deg, #64DD17, #FFB300)"
    elif completion >= 50:
        bar_color = "linear-gradient(90deg, #FFB300, #FF9100)"
    else:
        bar_color = "linear-gradient(90deg, #FF5252, #FF1744)"

    display_name = item["name"]
    if item.get("username"):
        display_name = f"{item['name']} ({item['username']})"

    return html.Div([
        _get_rank_badge(item["rank"]),
        html.Div([
            html.Div([
                html.Span(display_name, className="ranking-target-name"),
                html.Span(progress_display, className=f"ranking-progress {progress_class}") if show_progress else None
            ], className="ranking-item-header"),
            html.Div([
                html.Div([
                    html.Div(
                        className="ranking-progress-bar",
                        style={"width": f"{min(completion, 100)}%", "background": bar_color}
                    )
                ], className="ranking-progress-container"),
                html.Span(f"{completion}%", className="ranking-completion-text")
            ], className="ranking-item-body")
        ], className="ranking-item-content")
    ], className="ranking-item")


def create_ranking_section(completion_top5, progress_top5):
    completion_items = [create_ranking_item(item, show_progress=False) for item in completion_top5] if completion_top5 else [
        html.Div("暂无数据", className="ranking-empty")
    ]
    progress_items = [create_ranking_item(item, show_progress=True) for item in progress_top5] if progress_top5 else [
        html.Div("暂无数据", className="ranking-empty")
    ]

    return html.Div([
        html.Div([
            html.Div([
                html.H3("🥇 完成率 TOP5", className="ranking-subtitle ranking-completion-title"),
                html.Div(completion_items, className="ranking-list")
            ], className="ranking-column ranking-completion-column"),
            html.Div([
                html.H3("🚀 进步最快 TOP5", className="ranking-subtitle ranking-progress-title"),
                html.Div(progress_items, className="ranking-list")
            ], className="ranking-column ranking-progress-column")
        ], className="ranking-columns")
    ], className="ranking-section-body")


def create_comparison_radar_chart(comparison_data):
    fig = go.Figure()
    colors = ["#667eea", "#764ba2", "#00C853", "#FFB300", "#FF5252", "#00BCD4"]

    if not comparison_data:
        fig.add_annotation(
            text="请选择至少两个目标进行对比",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=16, color="#999")
        )
        fig.update_layout(
            height=450,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        return fig

    categories = ["完成率得分", "增长速度", "效率得分", "时间得分", "综合得分"]

    for idx, data in enumerate(comparison_data):
        values = [
            data.get("completion_score", 0),
            data.get("growth_score", 0),
            data.get("efficiency_score", 0),
            data.get("time_score", 0),
            data.get("overall_score", 0)
        ]
        color = colors[idx % len(colors)]
        if len(color.lstrip('#')) == 6:
            r_val = int(color.lstrip('#')[0:2], 16)
            g_val = int(color.lstrip('#')[2:4], 16)
            b_val = int(color.lstrip('#')[4:6], 16)
            fillcolor = f"rgba({r_val}, {g_val}, {b_val}, 0.2)"
        else:
            fillcolor = color + "33"
        fig.add_trace(go.Scatterpolar(
            r=values,
            theta=categories,
            fill='toself',
            name=data["name"],
            line=dict(color=color, width=2),
            fillcolor=fillcolor,
            hovertemplate=f"<b>{data['name']}</b><br>%{{theta}}: %{{r}}<extra></extra>"
        ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100],
                tickfont=dict(size=10),
                gridcolor="#e0e0e0"
            ),
            angularaxis=dict(
                tickfont=dict(size=12, color="#333"),
                gridcolor="#e0e0e0"
            ),
            bgcolor="#fafafa"
        ),
        height=450,
        paper_bgcolor="white",
        font={"family": "Microsoft YaHei", "size": 12, "color": "#333"},
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font={"size": 12}
        ),
        margin={"l": 40, "r": 40, "t": 60, "b": 40},
        title={
            'text': '🎯 多维度能力雷达图',
            'y': 0.98,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top',
            'font': dict(size=16, color='#333')
        }
    )

    return fig


def create_comparison_bar_chart(comparison_data):
    fig = go.Figure()
    colors = ["#667eea", "#764ba2", "#00C853", "#FFB300", "#FF5252", "#00BCD4"]

    if not comparison_data:
        fig.add_annotation(
            text="请选择至少两个目标进行对比",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=16, color="#999")
        )
        fig.update_layout(
            height=450,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        return fig

    target_names = [d["name"] for d in comparison_data]

    fig.add_trace(go.Bar(
        name="完成率 (%)",
        x=target_names,
        y=[d["completion"] for d in comparison_data],
        marker_color="#667eea",
        text=[f"{d['completion']}%" for d in comparison_data],
        textposition='outside',
        hovertemplate="完成率: %{y}%<extra></extra>"
    ))

    fig.add_trace(go.Bar(
        name="日均增长 (%/天)",
        x=target_names,
        y=[d.get("growth_rate", 0) for d in comparison_data],
        marker_color="#00C853",
        text=[f"{d.get('growth_rate', 0):.2f}" for d in comparison_data],
        textposition='outside',
        hovertemplate="日均增长: %{y}%/天<extra></extra>"
    ))

    remaining_days = []
    remaining_text = []
    for d in comparison_data:
        if d.get("completion", 0) >= 100:
            remaining_days.append(0)
            remaining_text.append("已完成")
        elif d.get("estimated_days_remaining") is not None:
            remaining_days.append(d["estimated_days_remaining"])
            remaining_text.append(f"{d['estimated_days_remaining']}天")
        else:
            remaining_days.append(0)
            remaining_text.append("无法预测")

    fig.add_trace(go.Bar(
        name="剩余时间 (天)",
        x=target_names,
        y=remaining_days,
        marker_color="#FF5252",
        text=remaining_text,
        textposition='outside',
        hovertemplate="剩余时间: %{text}<extra></extra>"
    ))

    fig.add_trace(go.Bar(
        name="综合得分",
        x=target_names,
        y=[d["overall_score"] for d in comparison_data],
        marker_color="#FFB300",
        text=[f"{d['overall_score']}" for d in comparison_data],
        textposition='outside',
        hovertemplate="综合得分: %{y}<extra></extra>"
    ))

    fig.update_layout(
        barmode='group',
        height=450,
        paper_bgcolor="white",
        plot_bgcolor="#fafafa",
        font={"family": "Microsoft YaHei", "size": 12, "color": "#333"},
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font={"size": 12}
        ),
        xaxis=dict(
            title="目标名称",
            showgrid=True,
            gridcolor="#e0e0e0",
            title_font={"size": 14},
            tickfont={"size": 11}
        ),
        yaxis=dict(
            title="数值",
            showgrid=True,
            gridcolor="#e0e0e0",
            title_font={"size": 14}
        ),
        margin={"l": 60, "r": 30, "t": 80, "b": 80},
        title={
            'text': '📊 关键指标对比柱状图',
            'y': 0.98,
            'x': 0.5,
            'xanchor': 'center',
            'yanchor': 'top',
            'font': dict(size=16, color='#333')
        },
        hovermode="x unified"
    )

    return fig


def create_comparison_detail_card(data, idx):
    colors = ["#667eea", "#764ba2", "#00C853", "#FFB300", "#FF5252", "#00BCD4"]
    color = colors[idx % len(colors)]

    advantages = data.get("advantages", [])
    disadvantages = data.get("disadvantages", [])

    rank_badges = []
    if data.get("overall_score_rank"):
        rank_text = f"综合排名: 第{data['overall_score_rank']}"
        if data["overall_score_rank"] == 1:
            rank_badges.append(html.Span("🏆 " + rank_text, className="comparison-rank-badge comparison-rank-gold"))
        elif data["overall_score_rank"] == 2:
            rank_badges.append(html.Span("🥈 " + rank_text, className="comparison-rank-badge comparison-rank-silver"))
        elif data["overall_score_rank"] == 3:
            rank_badges.append(html.Span("🥉 " + rank_text, className="comparison-rank-badge comparison-rank-bronze"))
        else:
            rank_badges.append(html.Span(rank_text, className="comparison-rank-badge"))

    adv_items = [html.Span(f"✅ {a}", className="comparison-advantage") for a in advantages]
    disadv_items = [html.Span(f"⚠️ {d}", className="comparison-disadvantage") for d in disadvantages]

    est_days = data.get("estimated_days_remaining")
    est_date = data.get("estimated_completion_date", "")
    if data.get("completion", 0) >= 100:
        est_text = "已完成"
    elif est_days is not None and est_date:
        est_text = f"{est_days} 天 (预计 {est_date})"
    elif est_days is not None:
        est_text = f"{est_days} 天"
    elif est_date:
        est_text = est_date
    else:
        est_text = "无法预测"

    growth_rate = data.get("growth_rate", 0)
    growth_text = f"{growth_rate:.4f}%/天"

    return html.Div([
        html.Div([
            html.Div([
                html.Span(data["name"], className="comparison-card-title"),
                html.Div(rank_badges, className="comparison-rank-container")
            ], className="comparison-card-header", style={"border-left": f"4px solid {color}"}),
            html.Div([
                html.Div([
                    html.Div([
                        html.Span("完成率", className="comparison-metric-label"),
                        html.Span(f"{data['completion']}%", className="comparison-metric-value", style={"color": color})
                    ], className="comparison-metric"),
                    html.Div([
                        html.Span("综合得分", className="comparison-metric-label"),
                        html.Span(f"{data['overall_score']}", className="comparison-metric-value", style={"color": color})
                    ], className="comparison-metric")
                ], className="comparison-metrics-row"),
                html.Div([
                    html.Div([
                        html.Span("日均增速", className="comparison-metric-label"),
                        html.Span(growth_text, className="comparison-metric-value")
                    ], className="comparison-metric"),
                    html.Div([
                        html.Span("剩余时间", className="comparison-metric-label"),
                        html.Span(est_text, className="comparison-metric-value")
                    ], className="comparison-metric")
                ], className="comparison-metrics-row"),
                html.Div([
                    html.Div([
                        html.Span("已进行", className="comparison-metric-label"),
                        html.Span(f"{data.get('elapsed_days', 0)} 天", className="comparison-metric-value")
                    ], className="comparison-metric"),
                    html.Div([
                        html.Span("剩余量", className="comparison-metric-label"),
                        html.Span(f"{data['remaining_value']:,} {data.get('unit', '')}", className="comparison-metric-value")
                    ], className="comparison-metric")
                ], className="comparison-metrics-row")
            ], className="comparison-metrics-body")
        ], className="comparison-card-body"),
        html.Div([
            html.Div([
                html.Span("💪 优势", className="comparison-subtitle"),
                html.Div(adv_items if adv_items else [html.Span("暂无明显优势", className="comparison-empty")], className="comparison-tags")
            ], className="comparison-advantages-section"),
            html.Div([
                html.Span("🎯 待改进", className="comparison-subtitle"),
                html.Div(disadv_items if disadv_items else [html.Span("表现良好，继续保持！", className="comparison-empty")], className="comparison-tags")
            ], className="comparison-disadvantages-section")
        ], className="comparison-analysis-body")
    ], className="comparison-detail-card", style={"border-top": f"3px solid {color}"})


def create_comparison_section(comparison_data):
    if not comparison_data:
        return html.Div([
            html.Div([
                html.Div([
                    html.Label("请从上方下拉框中选择至少2个目标进行对比分析", className="comparison-empty-hint"),
                    html.Div("💡 支持同时选择多个目标，对比完成率、增速、剩余时间等关键指标", className="comparison-tip-text")
                ], className="comparison-empty-state")
            ])
        ], className="comparison-inner-empty")

    detail_cards = [
        create_comparison_detail_card(data, idx)
        for idx, data in enumerate(comparison_data)
    ]

    return html.Div([
        html.Div([
            html.Div([
                html.Span(f"共选择 {len(comparison_data)} 个目标进行对比", className="comparison-count"),
                html.Span("💡 提示：雷达图查看多维度能力，柱状图查看关键指标", className="comparison-tip")
            ], className="comparison-info-row"),
            html.Div([
                html.Div([
                    dcc.Graph(
                        id="comparison-radar-chart",
                        figure=create_comparison_radar_chart(comparison_data),
                        config={'displayModeBar': True, 'displaylogo': False},
                        className="comparison-chart"
                    )
                ], className="comparison-chart-container comparison-radar-container"),
                html.Div([
                    dcc.Graph(
                        id="comparison-bar-chart",
                        figure=create_comparison_bar_chart(comparison_data),
                        config={'displayModeBar': True, 'displaylogo': False},
                        className="comparison-chart"
                    )
                ], className="comparison-chart-container comparison-bar-container")
            ], className="comparison-charts-row"),
            html.Div([
                html.H3("📋 详细对比分析", className="comparison-detail-title"),
                html.Div(detail_cards, className="comparison-detail-grid")
            ], className="comparison-detail-section")
        ], className="comparison-section-body")
    ], className="comparison-section")


def create_trend_chart(selected_targets, history, targets_data=None):
    fig = go.Figure()
    colors = ["#667eea", "#764ba2", "#00C853", "#FFB300", "#FF5252", "#00BCD4"]
    pred_colors = ["#a8b3f5", "#b39dcc", "#7fefb5", "#ffd699", "#ffb3b3", "#9fe8ef"]

    if not selected_targets:
        fig.add_annotation(
            text="请从上方下拉框选择要查看的目标",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=16, color="#999")
        )
        fig.update_layout(
            height=450,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        return fig

    if not history:
        fig.add_annotation(
            text="暂无历史数据，请点击上方按钮保存快照",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=16, color="#999")
        )
        fig.update_layout(
            height=450,
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        return fig

    for idx, target_name in enumerate(selected_targets):
        timestamps = []
        completions = []
        for snapshot in history:
            for target in snapshot["targets"]:
                if target["name"] == target_name:
                    timestamps.append(snapshot["timestamp"])
                    completions.append(target["completion"])
                    break

        if timestamps:
            color = colors[idx % len(colors)]
            pred_color = pred_colors[idx % len(pred_colors)]
            fig.add_trace(go.Scatter(
                x=timestamps,
                y=completions,
                mode="lines+markers",
                name=target_name,
                line=dict(color=color, width=3, shape="spline"),
                marker=dict(size=8, color=color, line=dict(width=2, color="white")),
                hovertemplate=f"<b>{target_name}</b><br>" +
                              "时间：%{x}<br>" +
                              "完成率：%{y}%<extra></extra>"
            ))

            prediction = predict_target_completion(target_name, history, targets_data)
            if prediction is not None:
                pred_x, pred_y = generate_prediction_line(prediction)
                if pred_x and pred_y:
                    fig.add_trace(go.Scatter(
                        x=pred_x,
                        y=pred_y,
                        mode="lines",
                        name=f"{target_name} (预测)",
                        line=dict(color=pred_color, width=2, dash="dash"),
                        hovertemplate=f"<b>{target_name} (预测)</b><br>" +
                                      "时间：%{x}<br>" +
                                      "预测完成率：%{y}%<extra></extra>"
                    ))

                    if prediction["predicted_completion_date"]:
                        fig.add_annotation(
                            x=prediction["predicted_completion_date"],
                            y=100,
                            text=f"🎯 {target_name}<br>预计完成",
                            showarrow=True,
                            arrowhead=2,
                            arrowsize=1,
                            arrowwidth=2,
                            arrowcolor=pred_color,
                            ax=0,
                            ay=-40,
                            font=dict(size=10, color=color),
                            bgcolor="white",
                            bordercolor=pred_color,
                            borderwidth=1,
                            borderpad=4
                        )

    fig.add_shape(
        type="line",
        x0=0,
        x1=1,
        xref="paper",
        y0=100,
        y1=100,
        yref="y",
        line=dict(
            color="#333",
            width=1,
            dash="dot"
        )
    )

    fig.update_layout(
        height=450,
        margin={"l": 60, "r": 30, "t": 50, "b": 80},
        paper_bgcolor="white",
        plot_bgcolor="#fafafa",
        font={"family": "Microsoft YaHei", "size": 12, "color": "#333"},
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font={"size": 12}
        ),
        xaxis=dict(
            title="时间",
            showgrid=True,
            gridcolor="#e0e0e0",
            tickangle=45,
            title_font={"size": 14},
            tickfont={"size": 10}
        ),
        yaxis=dict(
            title="完成率 (%)",
            range=[0, 100],
            showgrid=True,
            gridcolor="#e0e0e0",
            title_font={"size": 14},
            zeroline=True,
            zerolinecolor="#999"
        ),
        hovermode="x unified"
    )

    return fig


def create_gauge_chart(data, alert_config=None):
    completion = data["completion"]
    is_alert = False
    alert_level = None

    if alert_config and data["name"] in alert_config:
        threshold = alert_config[data["name"]]["threshold"]
        if completion < threshold:
            is_alert = True
            alert_level = alert_config[data["name"]]["level"]

    if is_alert:
        color = "#FF5252"
    elif completion >= 90:
        color = "#00C853"
    elif completion >= 70:
        color = "#64DD17"
    elif completion >= 50:
        color = "#FFB300"
    else:
        color = "#FF5252"

    title_text = f"<b>{data['name']}</b>"
    if is_alert:
        title_text += f"<br><span style='color:#D50000;font-size:12px'>⚠ {ALERT_LEVELS[alert_level]['name']}</span>"

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=completion,
        domain={'x': [0, 1], 'y': [0, 1]},
        title={
            'text': title_text,
            'font': {'size': 16, 'color': '#333'}
        },
        delta={
            'reference': 100,
            'increasing': {'color': "#00C853"},
            'decreasing': {'color': "#FF5252"},
            'valueformat': '.0f',
            'prefix': '目标差：'
        },
        gauge={
            'axis': {
                'range': [0, 100],
                'tickwidth': 1,
                'tickcolor': "#666",
                'tickfont': {'size': 12}
            },
            'bar': {
                'color': color,
                'thickness': 0.75
            },
            'bgcolor': "white",
            'borderwidth': 2,
            'bordercolor': "#e0e0e0",
            'steps': [
                {'range': [0, 50], 'color': '#ffebee'},
                {'range': [50, 70], 'color': '#fff8e1'},
                {'range': [70, 90], 'color': '#e8f5e9'},
                {'range': [90, 100], 'color': '#c8e6c9'}
            ],
        },
        number={
            'font': {'size': 40, 'color': color},
            'suffix': '%'
        }
    ))

    if is_alert and alert_config and data["name"] in alert_config:
        threshold = alert_config[data["name"]]["threshold"]
        fig.add_shape(
            type="line",
            x0=threshold / 100,
            x1=threshold / 100,
            y0=0,
            y1=1,
            xref="paper",
            yref="paper",
            line=dict(
                color="#D50000",
                width=3,
                dash="dash"
            )
        )

    fig.update_layout(
        height=300,
        margin={'l': 20, 'r': 20, 't': 60, 'b': 20},
        paper_bgcolor='white',
        font={'family': 'Microsoft YaHei', 'size': 12},
    )

    return fig


def create_stats_card(data, user_id=None, alert_config=None):
    is_alert = False
    alert_level = None
    card_class = "stat-card"

    is_owner = user_id is not None and data.get("user_id") == user_id and data.get("id") is not None

    if alert_config and data["name"] in alert_config:
        threshold = alert_config[data["name"]]["threshold"]
        if data["completion"] < threshold:
            is_alert = True
            alert_level = alert_config[data["name"]]["level"]
            card_class = "stat-card alert-card alert-active"

    children = [
        html.Div([
            html.Span(data["name"], className="stat-name"),
            html.Span(
                f"⚠ {ALERT_LEVELS[alert_level]['name']}",
                className="alert-badge alert-badge-active"
            )
        ], className="stat-name-row") if is_alert else html.Div(data["name"], className="stat-name"),
        html.Div(
            f"{data['current']:,}{data['unit']} / {data['target']:,}{data['unit']}",
            className="stat-value"
        ),
        html.Div([
            html.Span(f"完成率：{data['completion']}%", className="stat-completion"),
            html.Span(
                f" (阈值：{alert_config[data['name']]['threshold']}%)",
                className="alert-threshold-text"
            )
        ], className="stat-completion-row") if is_alert else html.Div(
            f"完成率：{data['completion']}%",
            className="stat-completion"
        ),
        html.Div([
            html.Div(
                className=f"progress-bar{' alert-progress-bar' if is_alert else ''}",
                style={
                    'width': f"{data['completion']}%",
                    'background': "linear-gradient(90deg, #FF5252 0%, #D50000 100%)" if is_alert else None
                }
            )
        ], className="progress-container")
    ]

    if is_owner:
        children.append(
            html.Div([
                html.Button(
                    "✏️ 编辑",
                    id={"type": "edit-target-btn", "index": data["id"]},
                    className="target-action-btn target-edit-btn",
                    n_clicks=0
                ),
                html.Button(
                    "🗑️ 删除",
                    id={"type": "delete-target-btn", "index": data["id"]},
                    className="target-action-btn target-delete-btn",
                    n_clicks=0
                )
            ], className="target-actions")
        )

    if not is_owner and data.get("is_public") == 1:
        children.append(
            html.Div("🌐 公开数据", className="target-public-badge")
        )

    return html.Div(children, className=card_class)


def create_alert_banner(triggered_alerts):
    if not triggered_alerts:
        return html.Div(className="alert-banner alert-banner-empty", children=[
            html.Span("✅ 当前无预警信息，所有目标状态正常", className="alert-banner-text")
        ])

    alert_texts = []
    for alert in triggered_alerts:
        level_info = ALERT_LEVELS.get(alert["level"], ALERT_LEVELS["medium"])
        alert_texts.append(
            f"【{level_info['name']}】{alert['name']} - 当前完成度: {alert['completion']}% (阈值: {alert['threshold']}%)"
        )

    scroll_content = "　　".join(alert_texts)
    return html.Div(className="alert-banner", children=[
        html.Span("🚨", className="alert-banner-icon"),
        html.Div([
            html.Div(scroll_content, className="alert-banner-scroll"),
            html.Div(scroll_content, className="alert-banner-scroll alert-banner-scroll-dup")
        ], className="alert-banner-track")
    ])


def create_alert_history_panel(alert_logs):
    recent_logs = alert_logs[:10] if alert_logs else []

    if not recent_logs:
        history_items = html.Div("暂无预警记录", className="alert-history-empty")
    else:
        history_items = []
        for idx, log in enumerate(recent_logs):
            history_items.append(
                html.Div([
                    html.Div([
                        html.Span("⚠", className="alert-history-icon alert-history-icon-active"),
                        html.Span(log["name"], className="alert-history-name"),
                    ], className="alert-history-header"),
                    html.Div([
                        html.Span(f"完成度: {log['completion']}%", className="alert-history-completion"),
                        html.Span(f" / 阈值: {log['threshold']}%", className="alert-history-threshold"),
                    ], className="alert-history-values"),
                    html.Div(log.get("timestamp", ""), className="alert-history-time")
                ], className="alert-history-item alert-history-item-active")
            )

    return html.Div([
        html.Div("🔔 预警历史记录", className="alert-history-title"),
        html.Div("最近 10 条预警", className="alert-history-subtitle"),
        html.Div(history_items, className="alert-history-list")
    ], className="alert-history-panel")


def create_alert_config_panel(alert_config, data=None):
    config_rows = []
    for target_name in get_target_names(data):
        config = alert_config.get(target_name, DEFAULT_ALERT_CONFIG.get(target_name, {"threshold": 70, "level": "medium"}))
        config_rows.append(
            html.Div([
                html.Div(target_name, className="alert-config-name"),
                html.Div([
                    html.Label("预警阈值 (%):", className="alert-config-label"),
                    dcc.Input(
                        id={"type": "threshold-input", "index": target_name},
                        type="number",
                        min=0,
                        max=100,
                        value=config["threshold"],
                        className="alert-config-threshold-input"
                    )
                ], className="alert-config-field"),
                html.Div([
                    html.Label("预警级别:", className="alert-config-label"),
                    dcc.Dropdown(
                        id={"type": "level-dropdown", "index": target_name},
                        options=[
                            {"label": "低预警", "value": "low"},
                            {"label": "中预警", "value": "medium"},
                            {"label": "高预警", "value": "high"}
                        ],
                        value=config["level"],
                        clearable=False,
                        className="alert-config-level-dropdown"
                    )
                ], className="alert-config-field")
            ], className="alert-config-row")
        )

    return html.Div([
        html.Div("⚙️ 预警配置", className="alert-config-title"),
        html.Div("为每个目标设置独立的预警阈值和级别", className="alert-config-subtitle"),
        html.Div(config_rows, className="alert-config-list"),
        html.Button(
            "💾 保存预警配置",
            id="save-alert-config-btn",
            className="save-alert-config-btn",
            n_clicks=0
        ),
        html.Div(id="alert-config-status", className="alert-config-status")
    ], className="alert-config-panel")


def create_auth_section(user_info):
    if user_info is not None:
        return html.Div([
            html.Div([
                html.Span("👤", className="user-status-icon"),
                html.Span(f"{user_info['username']}", className="user-status-name"),
                html.Span("已登录", className="user-status-tag")
            ], className="user-status-info"),
            html.Button(
                "🚪 退出登录",
                id="logout-btn",
                className="auth-btn logout-btn",
                n_clicks=0
            )
        ], className="user-status-section")
    else:
        return html.Div([
            html.Button(
                "🔐 登录",
                id="show-login-btn",
                className="auth-btn login-btn",
                n_clicks=0
            ),
            html.Button(
                "📝 注册",
                id="show-register-btn",
                className="auth-btn register-btn",
                n_clicks=0
            )
        ], className="user-status-section")


def create_login_modal():
    return html.Div(id="login-modal-container", className="auth-modal-overlay hidden", children=[
        html.Div(className="auth-modal", children=[
            html.Div(className="auth-modal-header", children=[
                html.Span("🔐 用户登录", className="auth-modal-title"),
                html.Button("×", id="login-modal-close", className="auth-modal-close-btn")
            ]),
            html.Div(className="auth-modal-body", children=[
                html.Div([
                    html.Label("用户名:", className="auth-form-label"),
                    dcc.Input(
                        id="login-username",
                        type="text",
                        placeholder="请输入用户名",
                        className="auth-form-input",
                        value=""
                    )
                ], className="auth-form-group"),
                html.Div([
                    html.Label("密码:", className="auth-form-label"),
                    dcc.Input(
                        id="login-password",
                        type="password",
                        placeholder="请输入密码",
                        className="auth-form-input",
                        value=""
                    )
                ], className="auth-form-group"),
                html.Button(
                    "登 录",
                    id="do-login-btn",
                    className="auth-submit-btn",
                    n_clicks=0
                ),
                html.Div(id="login-error-msg", className="auth-error-msg")
            ])
        ])
    ])


def create_register_modal():
    return html.Div(id="register-modal-container", className="auth-modal-overlay hidden", children=[
        html.Div(className="auth-modal", children=[
            html.Div(className="auth-modal-header", children=[
                html.Span("📝 用户注册", className="auth-modal-title"),
                html.Button("×", id="register-modal-close", className="auth-modal-close-btn")
            ]),
            html.Div(className="auth-modal-body", children=[
                html.Div([
                    html.Label("用户名:", className="auth-form-label"),
                    dcc.Input(
                        id="register-username",
                        type="text",
                        placeholder="请输入用户名（3-20个字符）",
                        className="auth-form-input",
                        value=""
                    )
                ], className="auth-form-group"),
                html.Div([
                    html.Label("密码:", className="auth-form-label"),
                    dcc.Input(
                        id="register-password",
                        type="password",
                        placeholder="请输入密码（至少6位）",
                        className="auth-form-input",
                        value=""
                    )
                ], className="auth-form-group"),
                html.Div([
                    html.Label("确认密码:", className="auth-form-label"),
                    dcc.Input(
                        id="register-password2",
                        type="password",
                        placeholder="请再次输入密码",
                        className="auth-form-input",
                        value=""
                    )
                ], className="auth-form-group"),
                html.Button(
                    "注 册",
                    id="do-register-btn",
                    className="auth-submit-btn",
                    n_clicks=0
                ),
                html.Div(id="register-error-msg", className="auth-error-msg")
            ])
        ])
    ])


def create_target_form_section(user_info):
    if user_info is None:
        return html.Div([
            html.Div("💡 登录后可创建自己的目标", className="create-target-hint")
        ], className="create-target-section")
    return html.Div([
        html.H2("➕ 创建新目标", className="section-title"),
        html.Div([
            html.Div([
                html.Label("目标名称:", className="target-form-label"),
                dcc.Input(
                    id="new-target-name",
                    type="text",
                    placeholder="例如：季度销售额",
                    className="target-form-input",
                    value=""
                )
            ], className="target-form-group"),
            html.Div([
                html.Label("目标值:", className="target-form-label"),
                dcc.Input(
                    id="new-target-target",
                    type="number",
                    placeholder="目标数值",
                    className="target-form-input",
                    value=None,
                    min=0
                )
            ], className="target-form-group"),
            html.Div([
                html.Label("当前值:", className="target-form-label"),
                dcc.Input(
                    id="new-target-current",
                    type="number",
                    placeholder="当前数值",
                    className="target-form-input",
                    value=None,
                    min=0
                )
            ], className="target-form-group"),
            html.Div([
                html.Label("单位:", className="target-form-label"),
                dcc.Input(
                    id="new-target-unit",
                    type="text",
                    placeholder="例如：元、个、人、%",
                    className="target-form-input",
                    value=""
                )
            ], className="target-form-group"),
            html.Div([
                html.Label([
                    dcc.Checklist(
                        id="new-target-public",
                        options=[{"label": "  设为公开（其他用户也能查看）", "value": "public"}],
                        value=[],
                        className="target-form-checkbox"
                    )
                ], className="target-form-label target-form-checkbox-label")
            ], className="target-form-group target-form-group-full"),
            html.Div([
                html.Button(
                    "✅ 创建目标",
                    id="create-target-btn",
                    className="create-target-submit-btn",
                    n_clicks=0
                )
            ], className="target-form-group target-form-group-full")
        ], className="target-form-grid"),
        html.Div(id="create-target-status", className="create-target-status")
    ], className="create-target-section")


def create_edit_target_modal():
    return html.Div(id="edit-target-modal-container", className="auth-modal-overlay hidden", children=[
        html.Div(className="auth-modal", children=[
            html.Div(className="auth-modal-header", children=[
                html.Span("✏️ 编辑目标", className="auth-modal-title"),
                html.Button("×", id="edit-target-modal-close", className="auth-modal-close-btn")
            ]),
            html.Div(className="auth-modal-body", children=[
                dcc.Store(id="editing-target-id", data=None),
                html.Div([
                    html.Label("目标名称:", className="auth-form-label"),
                    dcc.Input(id="edit-target-name", type="text", className="auth-form-input", value="")
                ], className="auth-form-group"),
                html.Div([
                    html.Label("目标值:", className="auth-form-label"),
                    dcc.Input(id="edit-target-target", type="number", className="auth-form-input", value=None, min=0)
                ], className="auth-form-group"),
                html.Div([
                    html.Label("当前值:", className="auth-form-label"),
                    dcc.Input(id="edit-target-current", type="number", className="auth-form-input", value=None, min=0)
                ], className="auth-form-group"),
                html.Div([
                    html.Label("单位:", className="auth-form-label"),
                    dcc.Input(id="edit-target-unit", type="text", className="auth-form-input", value="")
                ], className="auth-form-group"),
                html.Div([
                    html.Label([
                        dcc.Checklist(
                            id="edit-target-public",
                            options=[{"label": "  设为公开（其他用户也能查看）", "value": "public"}],
                            value=[],
                            className="target-form-checkbox"
                        )
                    ], className="auth-form-label target-form-checkbox-label")
                ], className="auth-form-group"),
                html.Button(
                    "💾 保存修改",
                    id="save-edit-target-btn",
                    className="auth-submit-btn",
                    n_clicks=0
                ),
                html.Div(id="edit-target-error-msg", className="auth-error-msg")
            ])
        ])
    ])


def create_sync_status_display(sync_status, sync_config):
    is_syncing = sync_status.get("is_syncing", False)
    last_sync = sync_status.get("last_sync_time", "从未同步")
    next_sync = sync_status.get("next_sync_time", None)
    countdown = sync_status.get("countdown", "--")
    last_message = sync_status.get("last_message", "")
    has_error = sync_status.get("has_error", False)
    enabled = sync_config.get("enabled", True)

    status_icon = "🔄" if is_syncing else ("✅" if enabled else "⏸️")
    status_class = "sync-status sync-status-syncing" if is_syncing else (
        "sync-status sync-status-error" if has_error else "sync-status sync-status-ready"
    )

    if is_syncing:
        status_text = "正在同步数据..."
    elif has_error:
        status_text = "同步失败"
    elif not enabled:
        status_text = "自动同步已暂停"
    else:
        status_text = "自动同步已启用"

    countdown_display = countdown
    if not next_sync:
        if not enabled:
            countdown_display = status_text
        elif last_sync and last_sync != "从未同步":
            countdown_display = "计算中..."
        else:
            countdown_display = countdown

    return html.Div([
        html.Div([
            html.Div([
                html.Span(status_icon, className=f"sync-status-icon{' sync-spin' if is_syncing else ''}"),
                html.Span(status_text, className="sync-status-text"),
            ], className=status_class),
        ], className="sync-status-header-left"),
        html.Div([
            html.Div([
                html.Div([
                    html.Span("上次同步：", className="sync-info-label"),
                    html.Span(last_sync, className="sync-info-value")
                ], className="sync-info-row"),
                html.Div([
                    html.Span("下次同步：", className="sync-info-label"),
                    html.Span(
                        countdown_display,
                        className="sync-info-value sync-countdown-value"
                    )
                ], className="sync-info-row"),
                html.Div([
                    html.Span("同步间隔：", className="sync-info-label"),
                    html.Span(
                        f"{sync_config.get('interval_seconds', 60)} 秒",
                        className="sync-info-value"
                    )
                ], className="sync-info-row"),
            ], className="sync-info-container"),
            html.Div(last_message, className="sync-message") if last_message else None,
        ], className="sync-status-body")
    ], className="sync-status-display")


def create_sync_config_panel(sync_config):
    interval = sync_config.get("interval_seconds", 60)
    enabled = sync_config.get("enabled", True)
    mock_mode = sync_config.get("mock_mode", True)
    api_url = sync_config.get("api_url", DEFAULT_SYNC_CONFIG["api_url"])
    api_timeout = sync_config.get("api_timeout", 10)

    interval_options = [
        {"label": "10 秒", "value": 10},
        {"label": "30 秒", "value": 30},
        {"label": "1 分钟", "value": 60},
        {"label": "2 分钟", "value": 120},
        {"label": "5 分钟", "value": 300},
        {"label": "10 分钟", "value": 600},
        {"label": "30 分钟", "value": 1800},
        {"label": "1 小时", "value": 3600},
    ]

    return html.Div([
        html.Div("🔄 数据同步配置", className="sync-config-title"),
        html.Div("配置自动数据同步的各项参数", className="sync-config-subtitle"),
        html.Div([
            html.Div([
                html.Label("启用自动同步：", className="sync-config-label"),
                dcc.Checklist(
                    id="sync-enabled-check",
                    options=[{"label": "  开启自动数据同步", "value": "enabled"}],
                    value=["enabled"] if enabled else [],
                    className="sync-config-checkbox"
                )
            ], className="sync-config-row"),
            html.Div([
                html.Label("同步间隔：", className="sync-config-label"),
                dcc.Dropdown(
                    id="sync-interval-dropdown",
                    options=interval_options,
                    value=interval,
                    clearable=False,
                    className="sync-config-interval-dropdown"
                )
            ], className="sync-config-row"),
            html.Div([
                html.Label("使用模拟 API：", className="sync-config-label"),
                dcc.Checklist(
                    id="sync-mock-mode-check",
                    options=[{"label": "  使用模拟数据（演示模式）", "value": "mock"}],
                    value=["mock"] if mock_mode else [],
                    className="sync-config-checkbox"
                )
            ], className="sync-config-row"),
            html.Div([
                html.Label("API 地址：", className="sync-config-label"),
                dcc.Input(
                    id="sync-api-url-input",
                    type="text",
                    value=api_url,
                    placeholder="例如：https://api.example.com/targets",
                    className="sync-config-text-input",
                    disabled=mock_mode
                )
            ], className="sync-config-row"),
            html.Div([
                html.Label("请求超时（秒）：", className="sync-config-label"),
                dcc.Input(
                    id="sync-api-timeout-input",
                    type="number",
                    min=1,
                    max=120,
                    value=api_timeout,
                    className="sync-config-number-input",
                    disabled=mock_mode
                )
            ], className="sync-config-row"),
        ], className="sync-config-list"),
        html.Div([
            html.Button(
                "💾 保存同步配置",
                id="save-sync-config-btn",
                className="save-sync-config-btn",
                n_clicks=0
            ),
        ], className="sync-config-actions"),
        html.Div(id="sync-config-status", className="sync-config-status"),
        html.Div([
            html.Div("📋 最近同步记录", className="sync-logs-title"),
            html.Div(id="sync-logs-container", className="sync-logs-list")
        ], className="sync-logs-section")
    ], className="sync-config-panel")


def create_sync_logs_list(logs):
    if not logs:
        return html.Div("暂无同步记录", className="sync-logs-empty")

    recent_logs = logs[:10]
    items = []
    for log in recent_logs:
        success = log.get("success", False)
        item_class = "sync-log-item sync-log-success" if success else "sync-log-item sync-log-error"
        icon = "✅" if success else "❌"
        elapsed = log.get("details", {}).get("elapsed_seconds", None)
        elapsed_text = f"（耗时 {elapsed}s）" if elapsed is not None else ""

        items.append(
            html.Div([
                html.Div([
                    html.Span(icon, className="sync-log-icon"),
                    html.Span(log.get("timestamp", ""), className="sync-log-time"),
                ], className="sync-log-header"),
                html.Div(
                    f"{log.get('message', '')}{elapsed_text}",
                    className="sync-log-message"
                )
            ], className=item_class)
        )
    return html.Div(items, className="sync-logs-items")


def create_sync_error_toast(error_message):
    if not error_message:
        return html.Div(className="sync-error-toast hidden", children="")
    return html.Div([
        html.Div([
            html.Span("⚠️", className="sync-error-icon"),
            html.Div([
                html.Div("同步失败", className="sync-error-title"),
                html.Div(error_message, className="sync-error-message")
            ], className="sync-error-content"),
            html.Button("×", id="sync-error-close-btn", className="sync-error-close-btn")
        ], className="sync-error-body")
    ], className="sync-error-toast")


initial_alert_config = load_alert_config()
initial_targets = get_visible_targets()
initial_triggered, initial_alert_logs = detect_and_log_alerts(
    initial_targets, initial_alert_config, prev_triggered_names=None, is_initial_load=True
)

initial_sync_config = load_sync_config()
initial_sync_logs = load_sync_logs()
initial_sync_status = {
    "is_syncing": False,
    "last_sync_time": "从未同步",
    "next_sync_time": None,
    "countdown": "--",
    "last_message": "",
    "has_error": False,
    "error_message": ""
}


app.layout = html.Div([
    dcc.Store(id="user-store", data=None),
    dcc.Store(id="targets-store", data=initial_targets),
    dcc.Store(id="history-store", data=load_history()),
    dcc.Store(id="alert-config-store", data=initial_alert_config),
    dcc.Store(id="alert-logs-store", data=initial_alert_logs),
    dcc.Store(id="triggered-alerts-store", data=initial_triggered),
    dcc.Store(id="is-first-load", data=True),
    dcc.Store(id="refresh-trigger", data=0),
    dcc.Store(id="sync-config-store", data=initial_sync_config),
    dcc.Store(id="sync-status-store", data=initial_sync_status),
    dcc.Store(id="sync-logs-store", data=initial_sync_logs),
    dcc.Store(id="sync-trigger-store", data=0),
    dcc.Store(id="ranking-time-range-store", data="all"),
    dcc.Download(id="download-data"),
    dcc.Interval(id="alert-interval", interval=30000, n_intervals=0),
    dcc.Interval(id="sync-interval", interval=initial_sync_config.get("interval_seconds", 60) * 1000, n_intervals=0, disabled=not initial_sync_config.get("enabled", True)),
    dcc.Interval(id="countdown-interval", interval=1000, n_intervals=0),

    html.Div([
        html.Div(id="auth-section", className="auth-section-container"),
        html.H1("🎯 目标完成进度监控", className="page-title"),
        html.Div(
            id="update-time-text",
            children=f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            className="update-time"
        ),
        html.Div([
            html.Div([
                html.Button(
                    [html.Span("📂", className="btn-icon"), " 导入数据"],
                    id="import-btn",
                    className="io-btn io-btn-import",
                    n_clicks=0
                ),
                html.Button(
                    [html.Span("📥", className="btn-icon"), " 导出 Excel"],
                    id="export-excel-btn",
                    className="io-btn io-btn-excel",
                    n_clicks=0
                ),
                html.Button(
                    [html.Span("📄", className="btn-icon"), " 导出 CSV"],
                    id="export-csv-btn",
                    className="io-btn io-btn-csv",
                    n_clicks=0
                ),
            ], className="io-buttons-group"),
        ], className="io-buttons-container"),
        html.Button(
            [html.Span("📸", className="btn-icon"), " 保存当前快照"],
            id="save-snapshot-btn",
            className="save-snapshot-btn",
            n_clicks=0
        ),
        html.Div(id="save-status", className="save-status"),
        html.Div(id="io-status", className="io-status"),
        html.Div([
            html.Div([
                html.Div(
                    id="sync-status-display-container",
                    children=create_sync_status_display(initial_sync_status, initial_sync_config),
                    className="sync-status-display-wrapper"
                ),
                html.Div([
                    html.Button(
                        [html.Span("🔄", className="btn-icon"), " 立即同步"],
                        id="manual-sync-btn",
                        className="manual-sync-btn",
                        n_clicks=0,
                        disabled=False
                    ),
                    html.Button(
                        [html.Span("⚙️", className="btn-icon"), " 同步设置"],
                        id="toggle-sync-config-btn",
                        className="toggle-sync-config-btn",
                        n_clicks=0
                    ),
                ], className="sync-buttons-group")
            ], className="sync-status-panel")
        ], className="sync-status-wrapper"),
    ], className="header"),

    html.Div(id="alert-banner-container", children=create_alert_banner(initial_triggered)),

    html.Div([
        html.Div([
            html.Div(id="create-target-container", children=create_target_form_section(None)),

            html.Div([
                html.Div(id="gauges-container", className="charts-grid"),

                html.Div([
                    html.H2("详细数据统计", className="section-title"),
                    html.Div(id="stats-container", className="stats-grid")
                ], className="stats-section"),

                html.Div([
                    html.H2("📈 历史趋势追踪", className="section-title"),
                    html.Div([
                        html.Div([
                            html.Label("选择目标（可多选对比）：", className="trend-label"),
                            dcc.Dropdown(
                                id="target-selector",
                                options=[{"label": name, "value": name} for name in get_target_names(initial_targets)],
                                value=[],
                                multi=True,
                                placeholder="请选择要查看的目标...",
                                className="target-dropdown"
                            )
                        ], className="trend-controls"),
                        html.Div([
                            html.Span(id="snapshot-count", className="snapshot-count")
                        ], className="trend-info")
                    ], className="trend-header"),
                    dcc.Graph(
                        id="trend-chart",
                        figure=create_trend_chart([], load_history()),
                        config={'displayModeBar': True, 'displaylogo': False},
                        className="trend-chart-container"
                    ),
                    html.Div([
                        html.H3("🔮 完成率预测", className="prediction-section-title"),
                        html.Div(
                            id="prediction-info-container",
                            children=create_prediction_info_panel([]),
                            className="prediction-info-panel"
                        )
                    ], className="prediction-section")
                ], className="trend-section"),

                html.Div(
                    id="comparison-container",
                    className="comparison-section-wrapper",
                    children=[
                        html.Div([
                            html.H2("📊 多目标进度对比", className="section-title"),
                            html.Div([
                                html.Div([
                                    html.Label("选择目标进行对比（至少选择2个）：", className="comparison-selector-label"),
                                    dcc.Dropdown(
                                        id="comparison-target-selector",
                                        options=[{"label": name, "value": name} for name in get_target_names(initial_targets)],
                                        value=[],
                                        multi=True,
                                        placeholder="请选择要对比的目标...",
                                        className="comparison-target-dropdown"
                                    )
                                ], className="comparison-selector-controls")
                            ], className="comparison-selector")
                        ], className="comparison-header-wrapper"),
                        html.Div(id="comparison-content", className="comparison-content", children=create_comparison_section([]))
                    ]
                ),

                html.Div(
                    id="ranking-container",
                    className="ranking-section-wrapper",
                    children=[
                        html.Div([
                            html.H2("🏆 目标排行榜", className="section-title ranking-section-title"),
                            html.Div([
                                html.Span("时间范围：", className="ranking-time-label"),
                                dcc.Dropdown(
                                    id="ranking-time-range",
                                    options=[
                                        {"label": "本周", "value": "week"},
                                        {"label": "本月", "value": "month"},
                                        {"label": "全部时间", "value": "all"}
                                    ],
                                    value="all",
                                    clearable=False,
                                    className="ranking-time-dropdown"
                                )
                            ], className="ranking-time-selector")
                        ], className="ranking-header"),
                        html.Div(id="ranking-content", className="ranking-content")
                    ]
                ),

                html.Div([
                    html.H2("⚙️ 预警阈值配置", className="section-title"),
                    html.Div(id="alert-config-container", children=create_alert_config_panel(initial_alert_config, initial_targets))
                ], className="alert-config-section")
            ], className="main-content"),

            html.Div([
                html.Div(id="sidebar-history", children=create_alert_history_panel(initial_alert_logs))
            ], className="sidebar")
        ], className="content-wrapper")
    ], className="content-area"),

    html.Footer([
        html.Div("目标完成进度监控系统 © 2026", className="footer-text")
    ], className="footer"),

    html.Div(id="import-modal-container", className="import-modal-overlay hidden", children=[
        html.Div(className="import-modal", children=[
            html.Div(className="import-modal-header", children=[
                html.Span("📂 选择数据文件导入", className="import-modal-title"),
                html.Button("×", id="import-modal-close", className="import-modal-close-btn")
            ]),
            html.Div(className="import-modal-body", children=[
                html.Div("支持 Excel (.xlsx, .xls) 和 CSV (.csv) 格式文件", className="import-modal-hint"),
                dcc.Upload(
                    id="upload-data",
                    children=html.Div(className="upload-zone", children=[
                        html.Div("📁", className="upload-zone-icon"),
                        html.Div([
                            html.Div("点击选择文件或拖拽文件到此处", className="upload-zone-text-main"),
                            html.Div("文件名应包含：目标名称、当前值、目标值、完成率、单位", className="upload-zone-text-sub")
                        ], className="upload-zone-texts")
                    ]),
                    multiple=False,
                    accept=".csv,.xlsx,.xls",
                    className="upload-component"
                )
            ])
        ])
    ]),
    html.Div(id="error-modal-container", className="error-modal-overlay hidden", children=[
        html.Div(className="error-modal", children=[
            html.Div(className="error-modal-header", children=[
                html.Span("⚠️ 数据导入错误", className="error-modal-title"),
                html.Button("×", id="error-modal-close", className="error-modal-close-btn")
            ]),
            html.Div(id="error-modal-body", className="error-modal-body", children="")
        ])
    ]),
    create_login_modal(),
    create_register_modal(),
    create_edit_target_modal(),

    html.Div(
        id="sync-config-container",
        children=create_sync_config_panel(initial_sync_config),
        style={"display": "none"}
    ),
    html.Div(
        id="sync-error-toast-container",
        children=create_sync_error_toast("")
    ),

], className="main-container")


@app.callback(
    Output("auth-section", "children"),
    [Input("user-store", "data")]
)
def render_auth_section(user_data):
    return create_auth_section(user_data)


@app.callback(
    Output("create-target-container", "children"),
    [Input("user-store", "data")]
)
def render_create_target_section(user_data):
    return create_target_form_section(user_data)


@app.callback(
    [Output("login-modal-container", "className", allow_duplicate=True),
     Output("register-modal-container", "className", allow_duplicate=True)],
    [Input("show-login-btn", "n_clicks"),
     Input("show-register-btn", "n_clicks"),
     Input("login-modal-close", "n_clicks"),
     Input("register-modal-close", "n_clicks")],
    prevent_initial_call=True
)
def toggle_auth_modals(n_login_show, n_reg_show, n_login_close, n_reg_close):
    ctx = callback_context
    if not ctx.triggered:
        return ["auth-modal-overlay hidden", "auth-modal-overlay hidden"]
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    login_cls = no_update
    register_cls = no_update
    if trigger_id == "show-login-btn":
        login_cls = "auth-modal-overlay"
        register_cls = "auth-modal-overlay hidden"
    elif trigger_id == "show-register-btn":
        login_cls = "auth-modal-overlay hidden"
        register_cls = "auth-modal-overlay"
    elif trigger_id == "login-modal-close":
        login_cls = "auth-modal-overlay hidden"
    elif trigger_id == "register-modal-close":
        register_cls = "auth-modal-overlay hidden"
    return [login_cls, register_cls]


@app.callback(
    [Output("user-store", "data"),
     Output("login-error-msg", "children"),
     Output("login-username", "value"),
     Output("login-password", "value"),
     Output("login-modal-container", "className", allow_duplicate=True)],
    [Input("do-login-btn", "n_clicks"),
     Input("logout-btn", "n_clicks")],
    [State("login-username", "value"),
     State("login-password", "value"),
     State("user-store", "data"),
     State("login-modal-container", "className")],
    prevent_initial_call='initial_duplicate'
)
def handle_login_logout(n_login, n_logout, username, password, current_user, current_login_cls):
    ctx = callback_context
    if not ctx.triggered:
        return no_update, "", "", "", no_update
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "logout-btn":
        return None, "", "", "", no_update

    if trigger_id == "do-login-btn":
        if not n_login or n_login == 0:
            return no_update, "", username, password, no_update
        if not username or not username.strip():
            return no_update, html.Span("⚠️ 请输入用户名", className="auth-error-text"), username, password, current_login_cls
        if not password:
            return no_update, html.Span("⚠️ 请输入密码", className="auth-error-text"), username, password, current_login_cls
        result = login_user(username.strip(), password)
        if result["success"]:
            user_data = {"user_id": result["user_id"], "username": result["username"]}
            return user_data, "", "", "", "auth-modal-overlay hidden"
        else:
            return no_update, html.Span(f"⚠️ {result['error']}", className="auth-error-text"), username, password, current_login_cls

    return no_update, "", username, password, no_update


@app.callback(
    [Output("register-error-msg", "children"),
     Output("register-username", "value"),
     Output("register-password", "value"),
     Output("register-password2", "value"),
     Output("login-modal-container", "className", allow_duplicate=True),
     Output("register-modal-container", "className", allow_duplicate=True)],
    [Input("do-register-btn", "n_clicks")],
    [State("register-username", "value"),
     State("register-password", "value"),
     State("register-password2", "value"),
     State("register-modal-container", "className")],
    prevent_initial_call=True
)
def handle_register(n_clicks, username, password, password2, current_register_cls):
    if not n_clicks or n_clicks == 0:
        return no_update, username, password, password2, no_update, no_update
    if not username or len(username.strip()) < 3 or len(username.strip()) > 20:
        return html.Span("⚠️ 用户名长度应为3-20个字符", className="auth-error-text"), username, password, password2, no_update, current_register_cls
    if not password or len(password) < 6:
        return html.Span("⚠️ 密码至少6位", className="auth-error-text"), username, password, password2, no_update, current_register_cls
    if password != password2:
        return html.Span("⚠️ 两次输入的密码不一致", className="auth-error-text"), username, password, password2, no_update, current_register_cls
    result = register_user(username.strip(), password)
    if result["success"]:
        return html.Span("✅ 注册成功！请登录", className="auth-success-text"), "", "", "", "auth-modal-overlay", "auth-modal-overlay hidden"
    else:
        return html.Span(f"⚠️ {result['error']}", className="auth-error-text"), username, password, password2, no_update, current_register_cls


@app.callback(
    [Output("targets-store", "data"),
     Output("refresh-trigger", "data"),
     Output("create-target-status", "children"),
     Output("new-target-name", "value"),
     Output("new-target-target", "value"),
     Output("new-target-current", "value"),
     Output("new-target-unit", "value"),
     Output("new-target-public", "value")],
    [Input("create-target-btn", "n_clicks"),
     Input("user-store", "data"),
     Input("refresh-trigger", "data")],
    [State("new-target-name", "value"),
     State("new-target-target", "value"),
     State("new-target-current", "value"),
     State("new-target-unit", "value"),
     State("new-target-public", "value"),
     State("refresh-trigger", "data")],
    prevent_initial_call=False
)
def handle_target_create_and_refresh(n_create, user_data, refresh, name, target, current, unit, public, current_refresh):
    ctx = callback_context
    user_id = user_data["user_id"] if user_data else None
    current_targets = get_visible_targets(user_id)

    if not ctx.triggered:
        return current_targets, 0, "", "", None, None, "", []

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "user-store" or trigger_id == "refresh-trigger":
        return current_targets, current_refresh or 0, no_update, no_update, no_update, no_update, no_update, no_update

    if trigger_id == "create-target-btn":
        if not n_create or n_create == 0:
            return no_update, no_update, "", no_update, no_update, no_update, no_update, no_update
        if not user_data:
            return no_update, no_update, html.Span("⚠️ 请先登录", className="create-target-error"), no_update, no_update, no_update, no_update, no_update
        if not name or not name.strip():
            return no_update, no_update, html.Span("⚠️ 请填写目标名称", className="create-target-error"), no_update, no_update, no_update, no_update, no_update
        if target is None or target <= 0:
            return no_update, no_update, html.Span("⚠️ 目标值必须大于0", className="create-target-error"), no_update, no_update, no_update, no_update, no_update
        if current is None or current < 0:
            return no_update, no_update, html.Span("⚠️ 当前值不能小于0", className="create-target-error"), no_update, no_update, no_update, no_update, no_update

        is_public = 1 if public and "public" in public else 0
        created = create_target(name.strip(), float(target), float(current), unit.strip() if unit else "", user_id, is_public)
        new_refresh = (current_refresh or 0) + 1
        updated_targets = get_visible_targets(user_id)
        success_msg = html.Div([
            html.Span("✅", className="status-icon"),
            f" 目标「{created['name']}」创建成功！完成率：{created['completion']}%"
        ], className="create-target-success")
        return updated_targets, new_refresh, success_msg, "", None, None, "", []

    return current_targets, current_refresh or 0, "", no_update, no_update, no_update, no_update, no_update


@app.callback(
    [Output("edit-target-modal-container", "className", allow_duplicate=True),
     Output("editing-target-id", "data"),
     Output("edit-target-name", "value"),
     Output("edit-target-target", "value"),
     Output("edit-target-current", "value"),
     Output("edit-target-unit", "value"),
     Output("edit-target-public", "value"),
     Output("edit-target-error-msg", "children")],
    [Input({"type": "edit-target-btn", "index": ALL}, "n_clicks"),
     Input("edit-target-modal-close", "n_clicks"),
     Input("save-edit-target-btn", "n_clicks")],
    [State("editing-target-id", "data"),
     State("edit-target-name", "value"),
     State("edit-target-target", "value"),
     State("edit-target-current", "value"),
     State("edit-target-unit", "value"),
     State("edit-target-public", "value"),
     State("user-store", "data"),
     State("edit-target-modal-container", "className")],
    prevent_initial_call=True
)
def handle_edit_target_modal(edit_clicks_list, close_clicks, save_clicks,
                             editing_id, name, target, current, unit, public, user_data, current_edit_cls):
    ctx = callback_context
    if not ctx.triggered:
        return ["auth-modal-overlay hidden", None, "", None, None, "", [], ""]

    trigger = ctx.triggered[0]
    trigger_prop = trigger["prop_id"]
    trigger_value = trigger["value"]

    if trigger_prop == "edit-target-modal-close.n_clicks":
        return ["auth-modal-overlay hidden", None, "", None, None, "", [], ""]

    if trigger_prop == "save-edit-target-btn.n_clicks":
        if not save_clicks or save_clicks == 0:
            return no_update
        if not user_data:
            return [current_edit_cls, no_update, no_update, no_update, no_update, no_update, no_update,
                    html.Span("⚠️ 请先登录", className="auth-error-text")]
        if editing_id is None:
            return [current_edit_cls, no_update, no_update, no_update, no_update, no_update, no_update,
                    html.Span("⚠️ 未选择目标", className="auth-error-text")]
        if not name or not name.strip():
            return [current_edit_cls, no_update, no_update, no_update, no_update, no_update, no_update,
                    html.Span("⚠️ 请填写目标名称", className="auth-error-text")]
        if target is None or target <= 0:
            return [current_edit_cls, no_update, no_update, no_update, no_update, no_update, no_update,
                    html.Span("⚠️ 目标值必须大于0", className="auth-error-text")]
        if current is None or current < 0:
            return [current_edit_cls, no_update, no_update, no_update, no_update, no_update, no_update,
                    html.Span("⚠️ 当前值不能小于0", className="auth-error-text")]
        is_public = 1 if public and "public" in public else 0
        result = update_target(
            editing_id, user_data["user_id"],
            name=name.strip(),
            target=float(target),
            current=float(current),
            unit=unit.strip() if unit else "",
            is_public=is_public
        )
        if result["success"]:
            return ["auth-modal-overlay hidden", None, "", None, None, "", [], ""]
        else:
            return [current_edit_cls, no_update, no_update, no_update, no_update, no_update, no_update,
                    html.Span(f"⚠️ {result['error']}", className="auth-error-text")]

    if "type" in trigger_prop and "edit-target-btn" in trigger_prop:
        if not trigger_value or trigger_value == 0:
            return no_update
        try:
            prop_dict = json.loads(trigger_prop.split(".")[0])
            target_id_to_edit = prop_dict["index"]
        except Exception:
            return no_update
        target_data = get_target_by_id(target_id_to_edit)
        if target_data is None:
            return no_update
        if user_data and target_data["user_id"] == user_data["user_id"]:
            public_val = ["public"] if target_data.get("is_public") == 1 else []
            return [
                "auth-modal-overlay",
                target_id_to_edit,
                target_data["name"],
                target_data["target"],
                target_data["current"],
                target_data.get("unit", ""),
                public_val,
                ""
            ]
    return no_update


@app.callback(
    [Output("targets-store", "data", allow_duplicate=True),
     Output("refresh-trigger", "data", allow_duplicate=True)],
    [Input("save-edit-target-btn", "n_clicks"),
     Input({"type": "delete-target-btn", "index": ALL}, "n_clicks")],
    [State("user-store", "data"),
     State("editing-target-id", "data"),
     State("refresh-trigger", "data")],
    prevent_initial_call=True
)
def handle_edit_delete_refresh(save_clicks, delete_clicks_list, user_data, editing_id, current_refresh):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    user_id = user_data["user_id"] if user_data else None
    trigger = ctx.triggered[0]
    trigger_prop = trigger["prop_id"]
    trigger_value = trigger["value"]
    refresh = current_refresh or 0

    if trigger_prop == "save-edit-target-btn.n_clicks":
        if save_clicks and save_clicks > 0 and user_id is not None and editing_id is not None:
            return get_visible_targets(user_id), refresh + 1
        return no_update

    if "type" in trigger_prop and "delete-target-btn" in trigger_prop:
        if trigger_value and trigger_value > 0 and user_id is not None:
            try:
                prop_dict = json.loads(trigger_prop.split(".")[0])
                target_id = prop_dict["index"]
                delete_target(target_id, user_id)
                return get_visible_targets(user_id), refresh + 1
            except Exception:
                pass
    return no_update


@app.callback(
    Output("download-data", "data"),
    [Input("export-excel-btn", "n_clicks"),
     Input("export-csv-btn", "n_clicks")],
    [State("targets-store", "data")],
    prevent_initial_call=True
)
def handle_export(n_excel, n_csv, current_data):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    data = current_data or get_visible_targets()
    if trigger_id == "export-excel-btn":
        return export_data_to_file(data, "xlsx")
    elif trigger_id == "export-csv-btn":
        return export_data_to_file(data, "csv")
    return no_update


@app.callback(
    [Output("import-modal-container", "className")],
    [Input("import-btn", "n_clicks"),
     Input("import-modal-close", "n_clicks"),
     Input("upload-data", "contents")],
    prevent_initial_call=False
)
def toggle_import_modal(import_clicks, close_clicks, upload_contents):
    ctx = callback_context
    if not ctx.triggered:
        return ["import-modal-overlay hidden"]
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    if trigger_id == "import-btn" and import_clicks and import_clicks > 0:
        return ["import-modal-overlay"]
    if trigger_id == "import-modal-close" and close_clicks and close_clicks > 0:
        return ["import-modal-overlay hidden"]
    if trigger_id == "upload-data" and upload_contents is not None:
        return ["import-modal-overlay hidden"]
    return no_update


@app.callback(
    [Output("error-modal-container", "className"),
     Output("error-modal-body", "children"),
     Output("update-time-text", "children"),
     Output("alert-config-container", "children")],
    [Input("upload-data", "contents"),
     Input("error-modal-close", "n_clicks"),
     Input("targets-store", "data")],
    [State("upload-data", "filename"),
     State("alert-config-store", "data")],
    prevent_initial_call=False
)
def handle_data_updates(contents, close_clicks, targets_data, filename, alert_config):
    ctx = callback_context
    config = alert_config or load_alert_config()
    targets = targets_data or get_visible_targets()

    if not ctx.triggered:
        return (
            "error-modal-overlay hidden",
            "",
            f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            create_alert_config_panel(config, targets)
        )

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "error-modal-close":
        return "error-modal-overlay hidden", "", no_update, no_update

    if trigger_id == "targets-store":
        return no_update, "", f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", create_alert_config_panel(config, targets)

    if trigger_id == "upload-data" and contents is not None:
        parsed, error = parse_uploaded_file(contents, filename)
        if error:
            error_children = [
                html.Div("导入失败，具体错误如下：", className="error-modal-intro"),
                html.Pre(error, className="error-modal-detail")
            ]
            return "error-modal-overlay", error_children, no_update, no_update
        return no_update, "", f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", create_alert_config_panel(config, targets)

    return no_update, "", no_update, no_update


@app.callback(
    [Output("history-store", "data"),
     Output("save-status", "children"),
     Output("trend-chart", "figure"),
     Output("snapshot-count", "children"),
     Output("target-selector", "options"),
     Output("prediction-info-container", "children")],
    [Input("save-snapshot-btn", "n_clicks"),
     Input("target-selector", "value"),
     Input("targets-store", "data")],
    [State("history-store", "data")],
    prevent_initial_call=False
)
def handle_trend_updates(n_clicks, selected_targets, current_targets, current_history):
    ctx = callback_context
    targets = current_targets or get_visible_targets()
    options = [{"label": name, "value": name} for name in get_target_names(targets)]

    if not ctx.triggered:
        history = current_history or []
        count_text = f"已保存 {len(history)} 个历史快照"
        sel = selected_targets or []
        predictions = [predict_target_completion(t, history, targets) for t in sel]
        return history, "", create_trend_chart(sel, history, targets), count_text, options, create_prediction_info_panel(predictions)

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "targets-store":
        history = current_history or []
        count_text = f"已保存 {len(history)} 个历史快照"
        sel = selected_targets or []
        predictions = [predict_target_completion(t, history, targets) for t in sel]
        return history, "", create_trend_chart(sel, history, targets), count_text, options, create_prediction_info_panel(predictions)

    if trigger_id == "save-snapshot-btn" and n_clicks and n_clicks > 0:
        history = save_snapshot(targets)
        status = html.Div([
            html.Span("✅", className="status-icon"),
            f" 快照已保存成功！当前共 {len(history)} 个历史记录"
        ], className="save-success")
    else:
        history = current_history or []
        status = ""

    count_text = f"已保存 {len(history)} 个历史快照"
    sel = selected_targets or []
    fig = create_trend_chart(sel, history, targets)
    predictions = [predict_target_completion(t, history, targets) for t in sel]

    return history, status, fig, count_text, options, create_prediction_info_panel(predictions)


@app.callback(
    [Output("comparison-content", "children"),
     Output("comparison-target-selector", "options")],
    [Input("comparison-target-selector", "value"),
     Input("targets-store", "data"),
     Input("history-store", "data"),
     Input("user-store", "data")],
    prevent_initial_call=False
)
def render_comparison_section(selected_names, targets_data, history_data, user_data):
    ctx = callback_context
    user_id = user_data["user_id"] if user_data else None
    targets = targets_data or get_visible_targets(user_id)
    history = history_data or load_history()
    options = [{"label": name, "value": name} for name in get_target_names(targets)]

    if not ctx.triggered:
        return create_comparison_section([]), options

    if not selected_names or len(selected_names) < 2:
        return create_comparison_section([]), options

    targets_to_compare = []
    for name in selected_names:
        for t in targets:
            if t["name"] == name:
                targets_to_compare.append(t)
                break

    if len(targets_to_compare) < 2:
        return create_comparison_section([]), options

    comparison_metrics = calculate_comparison_metrics(targets_to_compare, history)
    return create_comparison_section(comparison_metrics), options


@app.callback(
    Output("ranking-content", "children"),
    [Input("ranking-time-range", "value"),
     Input("targets-store", "data"),
     Input("history-store", "data"),
     Input("user-store", "data")],
    prevent_initial_call=False
)
def render_ranking_section(time_range, targets_data, history_data, user_data):
    time_range = time_range or "all"
    user_id = user_data["user_id"] if user_data else None
    history = history_data or load_history()

    all_targets = get_ranking_targets(user_id)
    filtered_targets = filter_targets_by_time_range(all_targets, history, time_range)

    completion_top5 = get_completion_top5(filtered_targets, history)
    progress_top5 = get_progress_top5(filtered_targets, history)

    return create_ranking_section(completion_top5, progress_top5)


@app.callback(
    [Output("alert-banner-container", "children"),
     Output("alert-logs-store", "data"),
     Output("triggered-alerts-store", "data"),
     Output("stats-container", "children"),
     Output("gauges-container", "children"),
     Output("is-first-load", "data")],
    [Input("alert-config-store", "data"),
     Input("alert-interval", "n_intervals"),
     Input("targets-store", "data")],
    [State("alert-logs-store", "data"),
     State("triggered-alerts-store", "data"),
     State("is-first-load", "data"),
     State("user-store", "data")],
    prevent_initial_call=False
)
def handle_alert_detection(alert_config, n_intervals, current_targets, current_logs, prev_triggered, is_first_load, user_data):
    ctx = callback_context
    config = alert_config or load_alert_config()
    targets = current_targets or get_visible_targets()
    user_id = user_data["user_id"] if user_data else None

    if not ctx.triggered:
        triggered = check_alerts(targets, config)
        logs = current_logs or load_alert_logs()
        banner = create_alert_banner(triggered)
        stats_children = [create_stats_card(data, user_id, config) for data in targets]
        gauge_children = [
            html.Div([
                dcc.Graph(
                    id=f"gauge-{idx}",
                    figure=create_gauge_chart(data, config),
                    config={'displayModeBar': False},
                    className="gauge-chart"
                )
            ], className=f"chart-container{' alert-chart-container' if data['name'] in config and data['completion'] < config[data['name']]['threshold'] else ''}")
            for idx, data in enumerate(targets)
        ]
        return [banner, logs, triggered, stats_children, gauge_children, False]

    _is_initial = is_first_load if is_first_load is not None else False
    prev_names = set(a["name"] for a in (prev_triggered or []))

    triggered, updated_logs = detect_and_log_alerts(
        targets, config, prev_triggered_names=prev_names, is_initial_load=_is_initial
    )

    banner = create_alert_banner(triggered)
    stats_children = [create_stats_card(data, user_id, config) for data in targets]
    gauge_children = [
        html.Div([
            dcc.Graph(
                id=f"gauge-{idx}",
                figure=create_gauge_chart(data, config),
                config={'displayModeBar': False},
                className="gauge-chart"
            )
        ], className=f"chart-container{' alert-chart-container' if data['name'] in config and data['completion'] < config[data['name']]['threshold'] else ''}")
        for idx, data in enumerate(targets)
    ]

    return [banner, updated_logs, triggered, stats_children, gauge_children, False]


@app.callback(
    Output("sidebar-history", "children"),
    [Input("alert-logs-store", "data")],
    prevent_initial_call=False
)
def update_alert_history(alert_logs):
    logs = alert_logs or load_alert_logs()
    return create_alert_history_panel(logs)


@app.callback(
    [Output("alert-config-store", "data"),
     Output("alert-config-status", "children")],
    [Input("save-alert-config-btn", "n_clicks")],
    [State({"type": "threshold-input", "index": ALL}, "value"),
     State({"type": "threshold-input", "index": ALL}, "id"),
     State({"type": "level-dropdown", "index": ALL}, "value"),
     State({"type": "level-dropdown", "index": ALL}, "id")],
    prevent_initial_call=False
)
def handle_alert_config_save(n_clicks, thresholds, threshold_ids, levels, level_ids):
    if not n_clicks or n_clicks == 0:
        return load_alert_config(), ""

    new_config = {}
    if thresholds and threshold_ids:
        for i, tid in enumerate(threshold_ids):
            name = tid["index"]
            threshold = thresholds[i] if thresholds[i] is not None else 70
            new_config[name] = {"threshold": int(threshold), "level": "medium"}

    if levels and level_ids:
        for i, lid in enumerate(level_ids):
            name = lid["index"]
            level = levels[i] if levels[i] is not None else "medium"
            if name in new_config:
                new_config[name]["level"] = level
            else:
                new_config[name] = {"threshold": 70, "level": level}

    existing = load_alert_config()
    for k, v in existing.items():
        if k not in new_config:
            new_config[k] = v

    save_alert_config(new_config)
    status = html.Div([
        html.Span("✅", className="status-icon"),
        " 预警配置已保存成功！"
    ], className="config-save-success")

    return new_config, status


@app.callback(
    [Output("io-status", "children"),
     Output("targets-store", "data", allow_duplicate=True),
     Output("refresh-trigger", "data", allow_duplicate=True)],
    [Input("upload-data", "contents")],
    [State("upload-data", "filename"),
     State("user-store", "data"),
     State("refresh-trigger", "data")],
    prevent_initial_call=True
)
def handle_import_status(contents, filename, user_data, current_refresh):
    if contents is None:
        return no_update, no_update, no_update
    parsed, error = parse_uploaded_file(contents, filename)
    if error:
        return no_update, no_update, no_update
    if user_data:
        for item in parsed:
            create_target(
                item["name"], item["target"], item["current"], item.get("unit", ""),
                user_data["user_id"], is_public=0
            )
        success = html.Div([
            html.Span("✅", className="status-icon"),
            f" 成功导入并创建 {len(parsed)} 条目标数据！"
        ], className="io-success")
        new_refresh = (current_refresh or 0) + 1
        updated_targets = get_visible_targets(user_data["user_id"])
        return success, updated_targets, new_refresh
    else:
        success = html.Div([
            html.Span("✅", className="status-icon"),
            f" 成功解析 {len(parsed)} 条目标数据！（登录后可导入创建为自己的目标）"
        ], className="io-success")
        return success, no_update, no_update


@app.callback(
    [Output("sync-status-display-container", "children"),
     Output("sync-error-toast-container", "children"),
     Output("manual-sync-btn", "disabled")],
    [Input("sync-status-store", "data"),
     Input("sync-config-store", "data"),
     Input("sync-error-close-btn", "n_clicks")],
    prevent_initial_call=False
)
def render_sync_display(sync_status, sync_config, close_clicks):
    ctx = callback_context
    status = sync_status or initial_sync_status
    config = sync_config or initial_sync_config

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else ""

    if trigger_id == "sync-error-close-btn":
        return create_sync_status_display(status, config), create_sync_error_toast(""), status.get("is_syncing", False)

    has_error = status.get("has_error", False)
    error_msg = status.get("error_message", "") if has_error else ""
    btn_disabled = status.get("is_syncing", False)
    return create_sync_status_display(status, config), create_sync_error_toast(error_msg), btn_disabled


@app.callback(
    [Output("sync-status-store", "data"),
     Output("sync-trigger-store", "data", allow_duplicate=True)],
    [Input("manual-sync-btn", "n_clicks"),
     Input("sync-interval", "n_intervals")],
    [State("sync-config-store", "data"),
     State("sync-status-store", "data"),
     State("sync-trigger-store", "data")],
    prevent_initial_call='initial_duplicate'
)
def start_sync_process(manual_clicks, sync_n_intervals, sync_config, current_status, current_trigger):
    ctx = callback_context
    config = sync_config or initial_sync_config
    status = dict(current_status or initial_sync_status)

    if not ctx.triggered:
        return status, no_update

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "sync-interval":
        if not config.get("enabled", True):
            return status, no_update
        if sync_n_intervals is None or sync_n_intervals == 0:
            return status, no_update

    if trigger_id == "manual-sync-btn":
        if not manual_clicks or manual_clicks == 0:
            return status, no_update

    if status.get("is_syncing", False):
        return status, no_update

    status["is_syncing"] = True
    status["has_error"] = False
    status["error_message"] = ""
    status["last_message"] = "正在从外部 API 获取数据..."

    new_trigger = (current_trigger or 0) + 1
    return status, new_trigger


@app.callback(
    [Output("sync-status-store", "data", allow_duplicate=True),
     Output("targets-store", "data", allow_duplicate=True),
     Output("refresh-trigger", "data", allow_duplicate=True),
     Output("sync-logs-store", "data", allow_duplicate=True)],
    [Input("sync-trigger-store", "data")],
    [State("sync-config-store", "data"),
     State("sync-status-store", "data"),
     State("refresh-trigger", "data"),
     State("user-store", "data")],
    prevent_initial_call='initial_duplicate'
)
def execute_sync(trigger_val, sync_config, current_status, current_refresh, user_data):
    if trigger_val is None or trigger_val == 0:
        return no_update, no_update, no_update, no_update

    config = sync_config or initial_sync_config
    status = dict(current_status or initial_sync_status)
    user_id = user_data["user_id"] if user_data else None

    sync_result = perform_sync(config)

    status["is_syncing"] = False

    if sync_result["success"]:
        status["last_sync_time"] = sync_result["timestamp"]
        status["last_message"] = sync_result["message"]
        status["has_error"] = False
        status["error_message"] = ""

        next_sync = calculate_next_sync(
            sync_result["timestamp"],
            config.get("interval_seconds", 60)
        )
        status["next_sync_time"] = next_sync
        status["countdown"] = get_countdown(next_sync)

        synced_data = sync_result["data"]
        all_visible_targets = get_visible_targets(None)
        existing_by_name = {}
        for t in all_visible_targets:
            existing_by_name.setdefault(t["name"], []).append(t)

        for item in synced_data:
            if item["name"] in existing_by_name:
                for existing in existing_by_name[item["name"]]:
                    tgt_user_id = existing.get("user_id")
                    update_target(
                        existing["id"], tgt_user_id,
                        current=item["current"],
                        target=item["target"],
                        unit=item.get("unit", existing.get("unit", ""))
                    )

        new_refresh = (current_refresh or 0) + 1
        updated_targets = get_visible_targets(user_id)
        sync_logs = load_sync_logs()
        return status, updated_targets, new_refresh, sync_logs
    else:
        status["last_sync_time"] = sync_result["timestamp"]
        status["last_message"] = f"同步失败：{sync_result['message']}"
        status["has_error"] = True
        status["error_message"] = sync_result["message"]
        sync_logs = load_sync_logs()
        return status, no_update, no_update, sync_logs


@app.callback(
    Output("sync-status-store", "data", allow_duplicate=True),
    [Input("countdown-interval", "n_intervals")],
    [State("sync-status-store", "data"),
     State("sync-config-store", "data")],
    prevent_initial_call=True
)
def update_countdown_cb(n_intervals, current_status, sync_config):
    status = dict(current_status or initial_sync_status)
    config = sync_config or initial_sync_config

    if status.get("is_syncing", False):
        return status

    if not config.get("enabled", True):
        status["countdown"] = "--"
        status["next_sync_time"] = None
        return status

    next_sync = status.get("next_sync_time", None)
    if next_sync is None:
        last_sync = status.get("last_sync_time")
        interval = config.get("interval_seconds", 60)
        if last_sync and last_sync != "从未同步":
            next_sync = calculate_next_sync(last_sync, interval)
        else:
            from datetime import datetime, timedelta
            next_dt = datetime.now() + timedelta(seconds=interval)
            next_sync = next_dt.strftime("%Y-%m-%d %H:%M:%S")
        status["next_sync_time"] = next_sync

    status["countdown"] = get_countdown(next_sync)
    return status


@app.callback(
    Output("sync-interval", "interval"),
    Output("sync-interval", "disabled"),
    [Input("sync-config-store", "data")],
    prevent_initial_call=False
)
def update_sync_interval(sync_config):
    config = sync_config or initial_sync_config
    interval_ms = config.get("interval_seconds", 60) * 1000
    disabled = not config.get("enabled", True)
    return interval_ms, disabled


@app.callback(
    Output("sync-config-container", "style"),
    [Input("toggle-sync-config-btn", "n_clicks")],
    [State("sync-config-container", "style")],
    prevent_initial_call=True
)
def toggle_sync_config_panel(toggle_clicks, current_style):
    if not toggle_clicks or toggle_clicks == 0:
        return no_update

    style = dict(current_style or {})
    if style.get("display", "none") == "none":
        style["display"] = "block"
    else:
        style["display"] = "none"
    return style


@app.callback(
    Output("sync-api-url-input", "disabled"),
    Output("sync-api-timeout-input", "disabled"),
    [Input("sync-mock-mode-check", "value")],
    prevent_initial_call=False
)
def update_api_inputs_disabled(mock_value):
    is_mock = bool(mock_value and "mock" in mock_value)
    return is_mock, is_mock


@app.callback(
    [Output("sync-config-store", "data"),
     Output("sync-config-status", "children"),
     Output("sync-logs-container", "children"),
     Output("sync-status-store", "data", allow_duplicate=True)],
    [Input("save-sync-config-btn", "n_clicks"),
     Input("sync-logs-store", "data")],
    [State("sync-enabled-check", "value"),
     State("sync-interval-dropdown", "value"),
     State("sync-mock-mode-check", "value"),
     State("sync-api-url-input", "value"),
     State("sync-api-timeout-input", "value"),
     State("sync-config-store", "data"),
     State("sync-status-store", "data")],
    prevent_initial_call='initial_duplicate'
)
def handle_sync_config_and_logs(save_clicks, sync_logs, enabled_val, interval_val, mock_val,
                                api_url, api_timeout, current_config, current_status):
    ctx = callback_context
    logs = sync_logs or load_sync_logs()
    status = dict(current_status or initial_sync_status)

    if not ctx.triggered:
        return current_config or initial_sync_config, "", create_sync_logs_list(logs), status

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "sync-logs-store":
        return no_update, no_update, create_sync_logs_list(logs), no_update

    if trigger_id == "save-sync-config-btn":
        if not save_clicks or save_clicks == 0:
            return no_update, "", create_sync_logs_list(logs), no_update

        config = dict(current_config or initial_sync_config)
        new_enabled = bool(enabled_val and "enabled" in enabled_val)
        new_interval = int(interval_val) if interval_val else 60
        config["enabled"] = new_enabled
        config["interval_seconds"] = new_interval
        config["mock_mode"] = bool(mock_val and "mock" in mock_val)
        config["api_url"] = api_url or DEFAULT_SYNC_CONFIG["api_url"]
        config["api_timeout"] = int(api_timeout) if api_timeout else 10

        save_sync_config(config)

        if new_enabled:
            from datetime import datetime, timedelta
            last_sync = status.get("last_sync_time")
            if last_sync and last_sync != "从未同步":
                next_sync = calculate_next_sync(last_sync, new_interval)
            else:
                next_dt = datetime.now() + timedelta(seconds=new_interval)
                next_sync = next_dt.strftime("%Y-%m-%d %H:%M:%S")
            status["next_sync_time"] = next_sync
            status["countdown"] = get_countdown(next_sync)
        else:
            status["next_sync_time"] = None
            status["countdown"] = "--"

        status_msg = html.Div([
            html.Span("✅", className="status-icon"),
            " 同步配置已保存成功！"
        ], className="config-save-success")

        return config, status_msg, create_sync_logs_list(logs), status

    return no_update, no_update, create_sync_logs_list(logs), no_update


if __name__ == "__main__":
    print("正在启动目标完成进度页面...")
    print("访问地址：http://127.0.0.1:8051")
    app.run(debug=False, host="127.0.0.1", port=8051)
