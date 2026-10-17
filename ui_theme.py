"""Presentation-only layer: does not modify dashboard calculations."""
import streamlit as st
import plotly.express as px

CSS = r'''<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root {color-scheme:dark;}
html, body, [class*="css"], [data-testid="stApp"] {font-family:'Inter',sans-serif!important;}
[data-testid="stApp"] {background:radial-gradient(ellipse at 84% 0%,rgba(34,211,238,.10),transparent 38%),linear-gradient(155deg,#0b1220 0%,#101a2c 58%,#0c1525 100%);color:#e7edf7;}
[data-testid="stHeader"] {background:rgba(11,18,32,.75);backdrop-filter:blur(16px);}
[data-testid="stSidebar"] {background:linear-gradient(180deg,#111d32,#0b1424);border-right:1px solid #253653;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,[data-testid="stSidebar"] h3 {color:#a7e8f4;}
.block-container {max-width:1520px;padding-top:1.6rem;padding-bottom:3rem;}
h1,h2,h3,h4 {letter-spacing:-.025em;color:#f5f8ff!important;}
h2 {font-size:1.35rem!important;} h3 {font-size:1.08rem!important;}
[data-testid="stCaptionContainer"] {color:#96a9c3;}
.hero {padding:26px 30px;border:1px solid #304563;border-radius:22px;background:linear-gradient(115deg,rgba(35,73,117,.85),rgba(16,29,49,.96) 62%,rgba(18,71,85,.8));box-shadow:0 20px 60px rgba(0,0,0,.16);margin-bottom:1.4rem;}
.hero .eyebrow {font-size:.72rem;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:#66e0e3;margin-bottom:10px;}
.hero h1 {font-size:clamp(1.55rem,3vw,2.5rem);margin:0 0 8px!important;line-height:1.15;}
.hero p {color:#b7cbe0;font-size:.93rem;margin:0;max-width:850px;}
.hero .chips {display:flex;gap:8px;flex-wrap:wrap;margin-top:18px;}
.hero .chip {padding:5px 10px;border:1px solid #476481;border-radius:999px;color:#d2e9f7;font-size:.72rem;background:#1b324b;}
[data-testid="stTabs"] [data-baseweb="tab-list"] {gap:6px;overflow-x:auto;background:#111e32;padding:7px;border:1px solid #263a55;border-radius:14px;}
[data-testid="stTabs"] button[data-baseweb="tab"] {border-radius:9px;padding:10px 14px;color:#a8bbd2;font-weight:600;white-space:nowrap;}
[data-testid="stTabs"] button[aria-selected="true"] {background:#1d415b;color:#79e5ee!important;}
[data-testid="stTabs"] [data-baseweb="tab-highlight"], [data-testid="stTabs"] [data-baseweb="tab-border"] {display:none;}
[data-testid="stMetric"] {background:linear-gradient(145deg,#182940,#142137);border:1px solid #2b4260;border-radius:16px;padding:18px 20px;box-shadow:0 8px 28px rgba(0,0,0,.12);}
[data-testid="stMetricLabel"] {color:#a9bed4;} [data-testid="stMetricValue"] {color:#e9faff;font-weight:800;letter-spacing:-.035em;}
[data-testid="stDataFrame"] {border:1px solid #31455d;border-radius:12px;overflow:hidden;}
[data-testid="stFileUploader"] {background:#15263d;border:1px dashed #4d7796;border-radius:16px;padding:12px;}
.stButton button[kind="primary"],.stDownloadButton button {background:linear-gradient(110deg,#0d9ca8,#1476b9)!important;color:white!important;border:0!important;border-radius:10px!important;font-weight:700!important;box-shadow:0 6px 18px rgba(0,165,190,.14);}
.stButton button {border-radius:10px!important;border-color:#45617d!important;}
[data-testid="stAlert"] {border-radius:12px;}
[data-testid="stExpander"] {border:1px solid #31455d;border-radius:12px;background:rgba(23,40,62,.55);}
hr {border-color:#2d405b!important;}
@media(max-width:768px){.block-container{padding-left:1rem;padding-right:1rem}.hero{padding:20px}.hero h1{font-size:1.5rem}}
</style>'''

def apply_theme():
    st.markdown(CSS, unsafe_allow_html=True)
    px.defaults.template = 'plotly_dark'
    px.defaults.color_discrete_sequence = ['#36c7d7','#7c9af2','#f4bc70','#7cddb6','#c5a3f5','#f18c9d']

def hero():
    st.markdown('''<div class="hero"><div class="eyebrow">ANNOTATION INTELLIGENCE / LEADER WORKSPACE</div><h1>CVAT Quality Control <span style="color:#65e3eb">Dashboard</span></h1><p>Phân tích chất lượng ảnh, kiểm tra nhãn và đo độ bao phủ dữ liệu — không cần Ground Truth.</p><div class="chips"><span class="chip">◈ Image Quality</span><span class="chip">◇ Label Check A/B</span><span class="chip">◎ Coverage & Slice</span><span class="chip">↗ Review & Export</span></div></div>''',unsafe_allow_html=True)
