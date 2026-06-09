import dash
from dash import html, dcc, Input, Output, State, callback_context
import plotly.graph_objects as go
from datetime import datetime
import json
import os

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "目标完成进度监控"

mock_data = [
    {"name": "年度销售目标", "completion": 78, "target": 1000000, "current": 780000, "unit": "元"},
    {"name": "客户增长", "completion": 92, "target": 500, "current": 460, "unit": "个"},
    {"name": "产品上线", "completion": 65, "target": 12, "current": 8, "unit": "个"},
    {"name": "团队扩张", "completion": 45, "target": 50, "current": 23, "unit": "人"},
    {"name": "用户满意度", "completion": 88, "target": 95, "current": 83.6, "unit": "%"},
    {"name": "市场份额", "completion": 72, "target": 25, "current": 18, "unit": "%"},
]

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_snapshots.json")


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


def get_target_names():
    return [item["name"] for item in mock_data]


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

def create_gauge_chart(data):
    completion = data["completion"]
    
    if completion >= 90:
        color = "#00C853"
    elif completion >= 70:
        color = "#64DD17"
    elif completion >= 50:
        color = "#FFB300"
    else:
        color = "#FF5252"
    
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=completion,
        domain={'x': [0, 1], 'y': [0, 1]},
        title={
            'text': f"<b>{data['name']}</b>",
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
    
    fig.update_layout(
        height=300,
        margin={'l': 20, 'r': 20, 't': 50, 'b': 20},
        paper_bgcolor='white',
        font={'family': 'Microsoft YaHei', 'size': 12},
    )
    
    return fig


def create_stats_card(data):
    return html.Div([
        html.Div(data["name"], className="stat-name"),
        html.Div(
            f"{data['current']:,}{data['unit']} / {data['target']:,}{data['unit']}",
            className="stat-value"
        ),
        html.Div(
            f"完成率：{data['completion']}%",
            className="stat-completion"
        ),
        html.Div([
            html.Div(
                className="progress-bar",
                style={'width': f"{data['completion']}%"}
            )
        ], className="progress-container")
    ], className="stat-card")


app.layout = html.Div([
    dcc.Store(id="history-store", data=load_history()),
    
    html.Div([
        html.H1("🎯 目标完成进度监控", className="page-title"),
        html.Div(
            f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            className="update-time"
        ),
        html.Button(
            [html.Span("📸", className="btn-icon"), " 保存当前快照"],
            id="save-snapshot-btn",
            className="save-snapshot-btn",
            n_clicks=0
        ),
        html.Div(id="save-status", className="save-status")
    ], className="header"),
    
    html.Div([
        html.Div([
            dcc.Graph(
                figure=create_gauge_chart(data),
                config={'displayModeBar': False}
            )
        ], className="chart-container")
        for data in mock_data
    ], className="charts-grid"),
    
    html.Div([
        html.H2("详细数据统计", className="section-title"),
        html.Div([
            create_stats_card(data)
            for data in mock_data
        ], className="stats-grid")
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
    
    html.Footer([
        html.Div("目标完成进度监控系统 © 2026", className="footer-text")
    ], className="footer")
    
], className="main-container")


@app.callback(
    [Output("history-store", "data"),
     Output("save-status", "children"),
     Output("trend-chart", "figure"),
     Output("snapshot-count", "children")],
    [Input("save-snapshot-btn", "n_clicks"),
     Input("target-selector", "value")],
    [State("history-store", "data")],
    prevent_initial_call=False
)
def handle_trend_updates(n_clicks, selected_targets, current_history):
    ctx = callback_context
    
    if not ctx.triggered:
        history = current_history or []
        count_text = f"已保存 {len(history)} 个历史快照"
        return history, "", create_trend_chart(selected_targets or [], history), count_text
    
    trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    if trigger_id == "save-snapshot-btn" and n_clicks > 0:
        history = save_snapshot(mock_data)
        status = html.Div([
            html.Span("✅", className="status-icon"),
            f" 快照已保存成功！当前共 {len(history)} 个历史记录"
        ], className="save-success")
    else:
        history = current_history or []
        status = ""
    
    count_text = f"已保存 {len(history)} 个历史快照"
    fig = create_trend_chart(selected_targets or [], history)
    
    return history, status, fig, count_text


if __name__ == "__main__":
    print("正在启动目标完成进度页面...")
    print("访问地址：http://127.0.0.1:8050")
    app.run(debug=True, host="127.0.0.1", port=8050)
