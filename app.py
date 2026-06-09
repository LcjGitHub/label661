import dash
from dash import html, dcc, Input, Output, State, callback_context, no_update, ALL
import plotly.graph_objects as go
from datetime import datetime
import json
import os
import io
import base64
import pandas as pd

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

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "目标完成进度监控"

initial_mock_data = [
    {"name": "年度销售目标", "completion": 78, "target": 1000000, "current": 780000, "unit": "元"},
    {"name": "客户增长", "completion": 92, "target": 500, "current": 460, "unit": "个"},
    {"name": "产品上线", "completion": 65, "target": 12, "current": 8, "unit": "个"},
    {"name": "团队扩张", "completion": 45, "target": 50, "current": 23, "unit": "人"},
    {"name": "用户满意度", "completion": 88, "target": 95, "current": 83.6, "unit": "%"},
    {"name": "市场份额", "completion": 72, "target": 25, "current": 18, "unit": "%"},
]

EXPECTED_COLUMNS = ["目标名称", "当前值", "目标值", "完成率", "单位"]
EXPECTED_COLUMNS_EN = ["name", "current", "target", "completion", "unit"]

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_snapshots.json")


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
        data = initial_mock_data
    return [item["name"] for item in data]


def create_trend_chart(selected_targets, history):
    fig = go.Figure()
    colors = ["#667eea", "#764ba2", "#00C853", "#FFB300", "#FF5252", "#00BCD4"]

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


def create_stats_card(data, alert_config=None):
    is_alert = False
    alert_level = None
    card_class = "stat-card"

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


def create_alert_config_panel(alert_config):
    config_rows = []
    for target_name in get_target_names():
        config = alert_config.get(target_name, DEFAULT_ALERT_CONFIG.get(target_name, {"threshold": 70, "level": "medium"}))
        config_rows.append(
            html.Div([
                html.Div(target_name, className="alert-config-name"),
                html.Div([
                    html.Label("预警阈值 (%):", className="alert-config-label"),
                    dcc.Input(
                        id=f"threshold-{target_name}",
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
                        id=f"level-{target_name}",
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


initial_alert_config = load_alert_config()
initial_triggered, initial_alert_logs = detect_and_log_alerts(
    initial_mock_data, initial_alert_config, prev_triggered_names=None, is_initial_load=True
)


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


app.layout = html.Div([
    dcc.Store(id="targets-store", data=initial_mock_data),
    dcc.Store(id="history-store", data=load_history()),
    dcc.Store(id="alert-config-store", data=initial_alert_config),
    dcc.Store(id="alert-logs-store", data=initial_alert_logs),
    dcc.Store(id="triggered-alerts-store", data=initial_triggered),
    dcc.Store(id="is-first-load", data=True),
    dcc.Download(id="download-data"),
    dcc.Interval(id="alert-interval", interval=30000, n_intervals=0),

    html.Div([
        html.H1("🎯 目标完成进度监控", className="page-title"),
        html.Div(
            id="update-time-text",
            children=f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            className="update-time"
        ),
        html.Div([
            html.Div([
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
                html.Button(
                    [html.Span("📤", className="btn-icon"), " 导入数据"],
                    id="import-btn",
                    className="io-btn io-btn-import",
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
    ], className="header"),

    html.Div(id="alert-banner-container", children=create_alert_banner(initial_triggered)),

    html.Div([
        html.Div([
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
                                options=[{"label": name, "value": name} for name in get_target_names()],
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
                        figure=create_trend_chart(
                            [],
                            load_history()
                        ),
                        config={'displayModeBar': True, 'displaylogo': False},
                        className="trend-chart-container"
                    )
                ], className="trend-section"),

                html.Div([
                    html.H2("⚙️ 预警阈值配置", className="section-title"),
                    html.Div(id="alert-config-container", children=create_alert_config_panel(initial_alert_config, initial_mock_data))
                ], className="alert-config-section")
            ], className="main-content"),

            html.Div([
                html.Div(id="sidebar-history", children=create_alert_history_panel(initial_alert_logs))
            ], className="sidebar")
        ], className="content-wrapper")
    ], className="content-area"),

    html.Footer([
        html.Div("目标完成进度监控系统 © 2026", className="footer-text")
    ], className="footer")

], className="main-container")


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
    data = current_data or initial_mock_data
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
    [Output("targets-store", "data"),
     Output("io-status", "children"),
     Output("error-modal-container", "className"),
     Output("error-modal-body", "children"),
     Output("update-time-text", "children"),
     Output("alert-config-container", "children")],
    [Input("upload-data", "contents"),
     Input("error-modal-close", "n_clicks")],
    [State("upload-data", "filename"),
     State("targets-store", "data"),
     State("alert-config-store", "data")],
    prevent_initial_call=False
)
def handle_import(contents, close_clicks, filename, current_data, alert_config):
    ctx = callback_context

    if not ctx.triggered:
        config = alert_config or load_alert_config()
        return (
            initial_mock_data,
            "",
            "error-modal-overlay hidden",
            "",
            f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            create_alert_config_panel(config, initial_mock_data)
        )

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "error-modal-close":
        return no_update, "", "error-modal-overlay hidden", "", no_update, no_update

    if trigger_id == "upload-data" and contents is not None:
        parsed, error = parse_uploaded_file(contents, filename)
        if error:
            error_children = [
                html.Div("导入失败，具体错误如下：", className="error-modal-intro"),
                html.Pre(error, className="error-modal-detail")
            ]
            return no_update, "", "error-modal-overlay", error_children, no_update, no_update

        config = alert_config or load_alert_config()
        success = html.Div([
            html.Span("✅", className="status-icon"),
            f" 成功导入 {len(parsed)} 条目标数据！"
        ], className="io-success")
        return (
            parsed,
            success,
            "error-modal-overlay hidden",
            "",
            f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            create_alert_config_panel(config, parsed)
        )

    return no_update, "", "error-modal-overlay hidden", "", no_update, no_update


@app.callback(
    [Output("history-store", "data"),
     Output("save-status", "children"),
     Output("trend-chart", "figure"),
     Output("snapshot-count", "children"),
     Output("target-selector", "options")],
    [Input("save-snapshot-btn", "n_clicks"),
     Input("target-selector", "value"),
     Input("targets-store", "data")],
    [State("history-store", "data")],
    prevent_initial_call=False
)
def handle_trend_updates(n_clicks, selected_targets, current_targets, current_history):
    ctx = callback_context
    targets = current_targets or initial_mock_data
    options = [{"label": name, "value": name} for name in get_target_names(targets)]

    if not ctx.triggered:
        history = current_history or []
        count_text = f"已保存 {len(history)} 个历史快照"
        return history, "", create_trend_chart(selected_targets or [], history), count_text, options

    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

    if trigger_id == "targets-store":
        history = current_history or []
        count_text = f"已保存 {len(history)} 个历史快照"
        return history, "", create_trend_chart(selected_targets or [], history), count_text, options

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
    fig = create_trend_chart(selected_targets or [], history)

    return history, status, fig, count_text, options


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
     State("is-first-load", "data")],
    prevent_initial_call=False
)
def handle_alert_detection(alert_config, n_intervals, current_targets, current_logs, prev_triggered, is_first_load):
    ctx = callback_context
    config = alert_config or load_alert_config()
    targets = current_targets or initial_mock_data

    if not ctx.triggered:
        triggered = check_alerts(targets, config)
        logs = current_logs or load_alert_logs()
        banner = create_alert_banner(triggered)
        stats_children = [create_stats_card(data, config) for data in targets]
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
    stats_children = [create_stats_card(data, config) for data in targets]
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


if __name__ == "__main__":
    print("正在启动目标完成进度页面...")
    print("访问地址：http://127.0.0.1:8050")
    app.run(debug=True, host="127.0.0.1", port=8050)
