import dash
from dash import html, dcc
import plotly.graph_objects as go
from datetime import datetime

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
    html.Div([
        html.H1("🎯 目标完成进度监控", className="page-title"),
        html.Div(
            f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            className="update-time"
        )
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
    
    html.Footer([
        html.Div("目标完成进度监控系统 © 2026", className="footer-text")
    ], className="footer")
    
], className="main-container")


if __name__ == "__main__":
    print("🚀 正在启动目标完成进度页面...")
    print("📊 访问地址：http://127.0.0.1:8050")
    app.run(debug=True, host="127.0.0.1", port=8050)
