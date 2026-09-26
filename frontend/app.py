"""
前后端分离的 Streamlit 前端。
启动：streamlit run frontend/app.py
前提：后端 uvicorn backend.server:app --port 8000 已在运行。

界面方向：中性极简。颜色 / 字体 / 圆角等基础令牌在 .streamlit/config.toml
（Streamlit 主题层），主题层表达不了的排版与交互状态在这里的 CSS 里。
"""

import html
import sys
from pathlib import Path

# 让 frontend/app.py 也能 import 项目根目录的 config.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
import requests

import config

API = config.BACKEND_URL.rstrip("/")


def _api(path: str, method: str = "GET", json: dict | None = None) -> dict | list | None:
    """Helper：调后端 REST 接口，网络异常时返回 None。"""
    url = f"{API}{path}"
    try:
        if method == "GET":
            resp = requests.get(url, timeout=30)
        elif method == "POST":
            resp = requests.post(url, json=json, timeout=60)
        elif method == "DELETE":
            resp = requests.delete(url, timeout=30)
        else:
            raise ValueError(f"unsupported method: {method}")
    except requests.RequestException:
        return None
    if not resp.ok:
        return None
    return resp.json()


def _section_label(text: str) -> None:
    """分区标签（"对话列表" / "试试这样问"）。

    样式走内联而不是只用 class，原因是实测出来的两条：
    1. 存在一条特异性高于 .ds-label 的 Streamlit 段落规则会把 font-size 顶回
       16px（所以 CSS 里那份还补了 !important）；
    2. 标签与下面控件之间的间距不能靠 <p> 的 margin：它会被 Streamlit 的
       `[data-testid="stMarkdownContainer"] > :last-child { margin-bottom: 0 }`
       归零（那条规则特异性同样高于 .ds-label），margin 一归零，标签就贴住
       下面的"新建对话"按钮。
    内联声明的优先级高于任何非 !important 的选择器，两处都能压住。间距写成
    外层 div 的 padding：padding 不参与外边距折叠，会实实在在计进容器高度，
    把下面的控件推开，不依赖侧栏的 flex gap。CSS 里保留一份同值的 .ds-label
    规则作为退化兜底。
    """
    st.markdown(
        '<div style="padding:0 0 0.875rem">'
        '<p class="ds-label" style="'
        "font-family:var(--ds-font);font-size:0.75rem;font-weight:600;"
        "line-height:1.4;letter-spacing:0.08em;color:var(--ds-ink-2);"
        'margin:0;padding:0">'
        + html.escape(text)
        + "</p></div>",
        unsafe_allow_html=True,
    )


# ------------------------- 初始化 session -------------------------

st.set_page_config(page_title="DataSidekick", layout="wide")

# ------------------------- 全局 CSS -------------------------
# 只补主题层做不到的事：排版刻度、间距刻度、交互状态、消息与代码块的读法。
# 所有颜色都走 :root 上的令牌，方便整体调档；色板基准在 .streamlit/config.toml。

st.markdown(
    """
<style>
:root {
    /* --- 中性色：统一带一丝冷色相 --- */
    --ds-paper:     #F9FAFB;   /* 主画布 */
    --ds-surface:   #F1F2F4;   /* 内嵌面：代码块、结果表、用户消息 */
    --ds-surface-2: #ECEDEF;   /* 侧栏底 */
    --ds-line:      #E3E4E7;   /* 发丝分隔线 */
    --ds-line-2:    #D3D5DA;   /* 悬停态边框 */

    /* --- 文字三级层次，均过 WCAG AA --- */
    --ds-ink:   #1C2024;       /* 正文（对纸白 15.6:1） */
    --ds-ink-2: #4A5057;       /* 次级（7.8:1） */
    --ds-ink-3: #6A6F76;       /* 元信息、占位符（4.8:1） */

    /* --- 唯一强调色 --- */
    --ds-accent:      #216A6A;
    --ds-accent-ink:  #174F4F;  /* 按下 / 选中文字 */
    --ds-accent-wash: #E4EEEE;  /* 8% 淡洗，只用于选中与焦点光环 */

    --ds-danger: #A3352F;       /* 危险色只在悬停时出现 */

    /* --- 间距：4pt 刻度，用 rem 表达（跟随根字号一起缩放） --- */
    --ds-gap-1: 0.25rem;  --ds-gap-2: 0.5rem;  --ds-gap-3: 0.75rem;
    --ds-gap-4: 1rem;     --ds-gap-6: 1.5rem;  --ds-gap-8: 2rem;

    --ds-radius: 0.5rem;
    --ds-radius-lg: 0.75rem;

    /* --- 版面尺寸：随视口浮动，不写死 px ---
       rem 跟随根字号缩放，vw / vh 跟随窗口缩放。固定 px 的麻烦在于它不参与
       这个缩放关系：窗口一放大（或系统字号调大），侧栏 276px 能吃掉小半屏，
       768px 正文列配上 40px 留白也会把内容挤窄。 */
    --ds-measure:   48rem;                        /* 正文列最大宽度（≈768px） */
    --ds-pad-y:     clamp(1.5rem, 3.5vw, 2.5rem); /* 画布上下留白 */
    --ds-pad-x:     clamp(1rem, 3vw, 1.5rem);     /* 画布左右留白 */
    --ds-sidebar-w: clamp(13rem, 26vw, 19rem);    /* 侧栏宽度 */
    --ds-list-h:    clamp(12rem, 38vh, 24rem);    /* 对话列表滚动区高度 */

    --ds-font: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI",
               system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
    --ds-mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo,
               Consolas, monospace;

    /* ease-out-quart：安静、不弹跳 */
    --ds-ease: cubic-bezier(0.25, 1, 0.5, 1);
    --ds-dur: 160ms;
}

/* ============ 画布：居中单列 ============ */
/* 定宽是为了中文正文的行长（约 44 字/行），不是为了好看 */
[data-testid="stHeader"] {
    background: transparent;
    box-shadow: none;
}
[data-testid="stMainBlockContainer"] {
    max-width: var(--ds-measure);
    margin: 0 auto;
    padding: var(--ds-pad-y) var(--ds-pad-x);
}
/* 底部输入条必须与正文列同宽，否则窄栏 + 宽输入框会错位 */
[data-testid="stBottom"] > div {
    background: var(--ds-paper);
}
[data-testid="stBottomBlockContainer"] {
    max-width: var(--ds-measure);
    margin: 0 auto;
    padding: 0.5rem var(--ds-pad-x) var(--ds-pad-x);
    background: var(--ds-paper);
}

/* ============ 焦点环：键盘用户必须看得见 ============ */
button:focus-visible,
a:focus-visible,
input:focus-visible,
textarea:focus-visible,
summary:focus-visible,
[tabindex]:focus-visible {
    outline: 0.125rem solid var(--ds-accent);
    outline-offset: 0.125rem;
    border-radius: var(--ds-gap-1);
}

/* ============ 侧栏 ============ */
[data-testid="stSidebar"] {
    /* 宽度走 --ds-sidebar-w（clamp + vw）：窄窗口自动收窄，宽窗口封顶在 19rem */
    min-width: var(--ds-sidebar-w) !important;
    max-width: var(--ds-sidebar-w) !important;
    background: var(--ds-surface-2);
}
[data-testid="stSidebar"] > div:first-child {
    padding: 1.25rem 0.875rem 0.5rem;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.625rem;
}

/* 分区标签（中文）：小号、600 字重、加大字距——用排版分区，不用色块和粗体。
   走 --ds-font 而不是 --ds-mono：等宽栈里没有中文字形，中文会落到系统
   等宽回退字体上，渲染又细又淡，这就是标签"看不清"的原因。

   同一份样式在内联里也写了一遍（见 _section_label），这里保留作为退化兜底：
   - font-size 必须 !important——有一条特异性高于 .ds-label 的 Streamlit
     段落规则会把字号顶回 16px；
   - <p> 的 margin 会被 Streamlit 的
     `[data-testid="stMarkdownContainer"] > :last-child { margin-bottom: 0 }`
     归零，所以标签与按钮之间的间距只能内联，不在这一条里。 */
.ds-label {
    font-family: var(--ds-font);
    font-size: 0.75rem !important;
    font-weight: 600;
    line-height: 1.4;
    letter-spacing: 0.08em;
    color: var(--ds-ink-2);
    margin: 0;
    padding: 0;
}
/* 兜底：_section_label 会把 <p> 包在带 padding 的 div 里；若哪天又写成
   <p> 直接挂在 markdown 容器下的形态，这条规则负责补上下间距
   （!important 用来压过 Streamlit 的 `> :last-child { margin-bottom: 0 }`）。 */
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] > p.ds-label {
    margin: 0 0 0.875rem !important;
}

/* ============ 按钮：默认不带边界，悬停才浮现 ============ */
/* 侧栏里六七个带边框的方块会把列表变成一堵墙。
   边界改由悬停态表达，列表更接近"文字清单"而不是"一叠卡片"。 */
[data-testid="stSidebar"] .stButton > button,
.stButton > button {
    font-family: var(--ds-font);
    font-size: 0.875rem;
    font-weight: 400;
    line-height: 1.45;
    min-height: 2.375rem;
    padding: var(--ds-gap-2) var(--ds-gap-3);
    border-radius: var(--ds-radius);
    border: 1px solid transparent;
    background: transparent;
    color: var(--ds-ink-2);
    box-shadow: none;
    transition: background-color var(--ds-dur) var(--ds-ease),
                border-color var(--ds-dur) var(--ds-ease),
                color var(--ds-dur) var(--ds-ease);
}
.stButton > button:hover {
    background: #FFFFFF;
    border-color: var(--ds-line);
    color: var(--ds-ink);
}
.stButton > button:active {
    background: var(--ds-surface);
    border-color: var(--ds-line-2);
    color: var(--ds-ink);
}
.stButton > button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
}

/* 新建对话：全局唯一的实心按钮，用墨黑。
   强调色留给焦点与选中态，实心动作面用墨黑。 */
[data-testid="stSidebar"] .st-key-new_chat button {
    background: var(--ds-ink);
    border-color: var(--ds-ink);
    color: #FFFFFF;
    font-weight: 500;
}
[data-testid="stSidebar"] .st-key-new_chat button:hover {
    background: #33383E;
    border-color: #33383E;
    color: #FFFFFF;
}
[data-testid="stSidebar"] .st-key-new_chat button:active {
    background: #0F1215;
    border-color: #0F1215;
}

/* 选中的对话：不做实心填充，用淡洗 + 左侧强调色短杠标记位置 */
[data-testid="stSidebar"] .stButton > button[kind="primary"],
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
    background: var(--ds-accent-wash);
    border-color: transparent;
    color: var(--ds-accent-ink);
    font-weight: 500;
    box-shadow: inset 0.125rem 0 0 0 var(--ds-accent);
}
[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover,
[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover {
    background: #DAE8E8;
    border-color: transparent;
    color: var(--ds-accent-ink);
}

/* 删除按钮：默认隐形，悬停才现身，避免与对话标题争注意力 */
[data-testid="stSidebar"] [class*="st-key-del_"] button {
    background: transparent;
    border-color: transparent;
    color: var(--ds-ink-3);
    font-size: 0.8125rem;
    padding: 0;
}
[data-testid="stSidebar"] [class*="st-key-del_"] button:hover {
    background: #FFFFFF;
    border-color: var(--ds-line-2);
    color: var(--ds-danger);
}

/* 对话列表滚动区：高度不写死 px，走 :root 的 --ds-list-h（clamp + vh），
   对话多了在内部滚动，"试试这样问" 不被推走。
   覆盖点见下面那条：Streamlit 把 st.container(height=N) 的高度写成一个
   生成类挂在 keyed 元素的父级 stLayoutWrapper 上（height + flex-basis
   两处都要改），所以只能从 DOM 上盖回去，Python 侧的 height=320 只是
   兜底占位（万一 :has() 不被支持还有 320px，不至于没有滚动区）。
   不画边框——侧栏靠"面"分层，再加一个盒子会把列表变成一堵墙。 */
[data-testid="stSidebar"] [data-testid="stLayoutWrapper"]:has(> .st-key-conv_list) {
    height: var(--ds-list-h) !important;
    flex: 0 0 auto !important;
    min-height: 0 !important;
}
[data-testid="stSidebar"] .st-key-conv_list {
    padding-right: 0.125rem;
}
/* 侧栏内的滚动条：窄一点，描边跟着侧栏的面走
   （全局那条描边配的是纸白色，放进侧栏会泛出一圈亮边） */
[data-testid="stSidebar"] ::-webkit-scrollbar { width: 0.5rem; }
[data-testid="stSidebar"] ::-webkit-scrollbar-thumb {
    border-width: 0.125rem;
    border-color: var(--ds-surface-2);
}

/* ============ 消息：助手消息是阅读面，用户消息收成一个安静的输入块 ============ */
[data-testid="stChatMessage"] {
    background: transparent;
    padding: 0;
    gap: var(--ds-gap-3);
    margin-bottom: var(--ds-gap-6);
}
[data-testid="stChatMessageContent"] {
    padding: 0 !important;
}
/* 用户消息：轻微内嵌面 + 发丝边框，一眼区分"我的提问"与"系统的回答" */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    margin-bottom: var(--ds-gap-4);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] {
    background: var(--ds-surface);
    border: 1px solid var(--ds-line);
    border-radius: var(--ds-radius-lg);
    padding: var(--ds-gap-3) var(--ds-gap-4) !important;
}
/* 头像：去掉彩色圆底，压成中性的角色标记 */
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
    width: 1.625rem !important;
    height: 1.625rem !important;
    min-width: 1.625rem !important;
    font-size: 0.8125rem !important;
    border-radius: var(--ds-radius) !important;
    border: 1px solid var(--ds-line) !important;
    background: var(--ds-surface) !important;
    color: var(--ds-ink-3) !important;
}
[data-testid="stChatMessageAvatarUser"] {
    background: var(--ds-accent-wash) !important;
    border-color: #D2E3E3 !important;
    color: var(--ds-accent-ink) !important;
}

/* ============ 正文排版：固定 rem 刻度 ============ */
/* 中文正文行距给到 1.75，并靠字重与留白建立层次，而不是把字号一路放大 */
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
    font-family: var(--ds-font);
    font-size: 1rem;
    line-height: 1.75;
    color: var(--ds-ink);
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
    margin: 0 0 var(--ds-gap-4);
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] > *:last-child {
    margin-bottom: 0;
}
[data-testid="stChatMessage"] h1,
[data-testid="stChatMessage"] h2,
[data-testid="stChatMessage"] h3,
[data-testid="stChatMessage"] h4 {
    font-family: var(--ds-font);
    font-weight: 600;
    line-height: 1.35;
    letter-spacing: -0.005em;
    color: var(--ds-ink);
    margin: var(--ds-gap-6) 0 var(--ds-gap-2);
}
[data-testid="stChatMessage"] h1 { font-size: 1.375rem; }
[data-testid="stChatMessage"] h2 { font-size: 1.125rem; }
[data-testid="stChatMessage"] h3 { font-size: 1rem; }
[data-testid="stChatMessage"] h4 {
    font-size: 0.875rem;
    letter-spacing: 0.02em;
}
[data-testid="stChatMessage"] :is(h1, h2, h3, h4):first-child {
    margin-top: 0;
}
[data-testid="stChatMessage"] strong { font-weight: 600; }

/* 分隔线：极淡的一根，只用来分组，不抢视线 */
[data-testid="stChatMessage"] hr {
    border: none;
    border-top: 1px solid var(--ds-line);
    margin: var(--ds-gap-6) 0;
}

/* 列表：收紧行距，标记弱化 */
[data-testid="stChatMessage"] ul,
[data-testid="stChatMessage"] ol {
    margin: 0 0 var(--ds-gap-4);
    padding-left: 1.4em;
}
[data-testid="stChatMessage"] li { margin-bottom: var(--ds-gap-1); }
[data-testid="stChatMessage"] li::marker { color: var(--ds-ink-3); }

/* 行内代码 */
[data-testid="stChatMessage"] :not(pre) > code {
    font-family: var(--ds-mono);
    font-size: 0.875em;
    font-variant-ligatures: none;
    background: var(--ds-surface);
    border: 1px solid var(--ds-line);
    border-radius: var(--ds-gap-1);
    padding: 0.1em 0.35em;
    color: var(--ds-ink);
}

/* ============ 代码块：SQL 是这个产品最需要被读清的东西 ============ */
/* 按 pre 定位，不依赖 st.code 的容器 testid（各版本命名不一致） */
[data-testid="stChatMessage"] pre,
[data-testid="stMarkdownContainer"] pre,
[data-testid="stExpander"] pre {
    font-family: var(--ds-mono) !important;
    font-variant-ligatures: none;
    font-size: 0.8125rem !important;
    line-height: 1.7 !important;
    border-radius: var(--ds-radius) !important;
    border: 1px solid var(--ds-line);
}
[data-testid="stChatMessage"] pre code,
[data-testid="stMarkdownContainer"] pre code,
[data-testid="stExpander"] pre code {
    font-family: var(--ds-mono) !important;
    font-size: 0.8125rem !important;
    line-height: 1.7 !important;
}

/* ============ expander：不画盒子，只用一条顶部分隔线 ============ */
/* 让"展开细节"明确处于次要地位 */
[data-testid="stExpander"] {
    border: none !important;
    border-top: 1px solid var(--ds-line) !important;
    border-radius: 0 !important;
    background: transparent !important;
    margin-top: var(--ds-gap-3);
}
[data-testid="stExpander"] details {
    border: none !important;
    background: transparent !important;
}
[data-testid="stExpander"] summary {
    padding: var(--ds-gap-3) 0 !important;
    font-size: 0.8125rem !important;
    font-weight: 500;
    letter-spacing: 0.01em;
    color: var(--ds-ink-3) !important;
}
[data-testid="stExpander"] summary:hover { color: var(--ds-ink) !important; }
[data-testid="stExpander"] summary p { font-size: 0.8125rem !important; }

/* ============ 元信息：作为答案的脚注 ============ */
[data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] .stCaption p,
.stCaption {
    font-size: 0.8125rem !important;
    line-height: 1.6;
    color: var(--ds-ink-3) !important;
}

/* ============ 结果表 ============ */
[data-testid="stDataFrame"] {
    font-family: var(--ds-mono) !important;
    font-size: 0.8125rem !important;
    font-variant-numeric: tabular-nums;
    border: 1px solid var(--ds-line);
    border-radius: var(--ds-radius);
    overflow: hidden;
}

/* ============ 提示条（后端未响应 / 错误） ============ */
[data-testid="stAlert"] {
    border-radius: var(--ds-radius);
    font-size: 0.9375rem;
    line-height: 1.7;
}

/* ============ 当前对话的上下文条：一条安静的抬头 ============ */
.ds-conv-head {
    display: flex;
    align-items: baseline;
    gap: var(--ds-gap-3);
    padding-bottom: var(--ds-gap-3);
    margin-bottom: var(--ds-gap-6);
    border-bottom: 1px solid var(--ds-line);
}
.ds-conv-head__label {
    font-family: var(--ds-font);
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--ds-ink-3);
    white-space: nowrap;
}
.ds-conv-head__title {
    font-size: 0.9375rem;
    font-weight: 500;
    color: var(--ds-ink-2);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* ============ 空状态：产品定位的第一句话，用排版而不是插图 ============ */
.ds-empty {
    padding: var(--ds-gap-8) 0 var(--ds-gap-6);
}
.ds-empty__wordmark {
    font-family: var(--ds-mono);
    font-size: 0.6875rem;
    font-weight: 500;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--ds-ink-3);
    margin: 0 0 var(--ds-gap-4);
}
.ds-empty__title {
    font-size: 1.75rem;
    font-weight: 600;
    line-height: 1.25;
    letter-spacing: -0.01em;
    color: var(--ds-ink);
    margin: 0 0 var(--ds-gap-3);
}
.ds-empty__lede {
    font-size: 1rem;
    line-height: 1.75;
    color: var(--ds-ink-2);
    max-width: 34em;
    margin: 0 0 var(--ds-gap-6);
}
.ds-empty__hint {
    font-size: 0.875rem;
    line-height: 1.7;
    color: var(--ds-ink-3);
    max-width: 34em;
    padding-top: var(--ds-gap-4);
    border-top: 1px solid var(--ds-line);
    margin: 0;
}

/* ============ 输入框：唯一的悬浮面 ============ */
[data-testid="stChatInput"] {
    border: 1px solid var(--ds-line) !important;
    border-radius: var(--ds-radius-lg) !important;
    background: #FFFFFF !important;
    box-shadow: 0 1px 2px rgba(28, 32, 36, 0.04);
    transition: border-color var(--ds-dur) var(--ds-ease),
                box-shadow var(--ds-dur) var(--ds-ease);
}
/* 焦点态用强调色描边 + 淡洗光环，替代被压掉的默认 outline */
[data-testid="stChatInput"]:focus-within {
    border-color: var(--ds-accent) !important;
    box-shadow: 0 0 0 0.1875rem var(--ds-accent-wash);
}
[data-testid="stChatInput"] textarea {
    font-family: var(--ds-font) !important;
    font-size: 1rem !important;
    line-height: 1.5 !important;
    color: var(--ds-ink) !important;
    min-height: 3.25rem !important;
    padding: var(--ds-gap-4) !important;
}
[data-testid="stChatInput"] textarea::placeholder { color: var(--ds-ink-3) !important; }
[data-testid="stChatInput"] textarea:focus-visible { outline: none; }
[data-testid="stChatInput"] button { color: var(--ds-accent) !important; }

/* ============ 滚动条 / 选中 ============ */
::-webkit-scrollbar { width: 0.625rem; height: 0.625rem; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: var(--ds-line-2);
    border: 0.1875rem solid var(--ds-paper);
    border-radius: 0.375rem;
}
::-webkit-scrollbar-thumb:hover { background: #B9BCC2; }
::selection { background: var(--ds-accent-wash); color: var(--ds-ink); }

/* ============ 降级：窄屏 ============ */
/* 断点用 rem 表达（媒体查询里的 1rem 固定等于浏览器默认字号 16px） */
@media (max-width: 64rem) {
    [data-testid="stMainBlockContainer"] { padding: 2rem 1.25rem; }
    [data-testid="stBottomBlockContainer"] { padding: 0.5rem 1.25rem 1.25rem; }
}
@media (max-width: 40rem) {
    [data-testid="stMainBlockContainer"] { padding: 1.5rem 1rem; }
    [data-testid="stBottomBlockContainer"] { padding: 0.375rem 1rem 1rem; }
    .ds-empty__title { font-size: 1.5rem; }
    .ds-empty { padding-top: var(--ds-gap-6); }
}

/* ============ 尊重系统的"减少动态效果" ============ */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
    }
}
</style>
""",
    unsafe_allow_html=True,
)

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "pending" not in st.session_state:
    st.session_state.pending = None


# ------------------------- 侧栏：会话管理 + 提问示例 -------------------------

with st.sidebar:
    _section_label("对话列表")

    if st.button("新建对话", key="new_chat", use_container_width=True):
        data = _api("/api/conversations", method="POST")
        if data:
            st.session_state.conversation_id = data["id"]
            st.session_state.pending = None
            st.rerun()

    cs = _api("/api/conversations", method="GET") or []

    # 对话列表：可滚动的列表区。这里的 height 只是"必须给一个数"才能让
    # Streamlit 生成滚动容器，实际高度由 CSS 的 --ds-list-h（clamp + vh）
    # 按视口覆盖掉，见 .st-key-conv_list 那几条规则。
    # 对话变多时只在列表内部滚动，"试试这样问" 始终留在可视范围内。
    with st.container(height=320, border=False, key="conv_list"):
        if not cs:
            st.caption("还没有对话记录，点击上方新建。")

        for conv in cs:
            active = conv["id"] == st.session_state.conversation_id
            msg_count = conv.get("message_count", 0)

            open_col, del_col = st.columns([0.78, 0.22], gap="small")

            label = conv.get("title", "新对话")
            if msg_count:
                label += f"  ({msg_count})"

            if open_col.button(
                label,
                key=f"open_{conv['id']}",
                type="primary" if active else "secondary",
                use_container_width=True,
            ):
                st.session_state.conversation_id = conv["id"]
                st.rerun()

            if del_col.button(
                "✕",
                key=f"del_{conv['id']}",
                use_container_width=True,
                help="删除这个对话",
            ):
                _api(f"/api/conversations/{conv['id']}", method="DELETE")
                if active:
                    st.session_state.conversation_id = None
                st.rerun()

    st.divider()

    _section_label("试试这样问")
    examples = [
        "2024 年 6 月的 GMV 是多少？",
        "各城市的销售额排名",
        "复购率是多少？",
        "退款率是多少？",
    ]
    for example in examples:
        if st.button(example, key=f"example_{hash(example)}", use_container_width=True):
            st.session_state.pending = example


# ------------------------- 主区：渲染当前会话 -------------------------

conversation_id = st.session_state.conversation_id

if not conversation_id:
    st.markdown(
        """
<div class="ds-empty">
  <p class="ds-empty__wordmark">DataSidekick</p>
  <h1 class="ds-empty__title">问你的数据</h1>
  <p class="ds-empty__lede">用自然语言提问。系统会先检索业务口径，再生成只读 SQL 取数，最后把结果和口径一起讲清楚。</p>
  <p class="ds-empty__hint">在左侧新建或选择一个对话，或直接在下方输入框提问 —— 系统会自动为你创建一个新对话。左侧还备了几个示例问题。</p>
</div>
""",
        unsafe_allow_html=True,
    )
else:
    detail = _api(f"/api/conversations/{conversation_id}", method="GET")
    if detail:
        st.markdown(
            '<div class="ds-conv-head">'
            '<span class="ds-conv-head__label">当前对话</span>'
            f'<span class="ds-conv-head__title">{html.escape(detail.get("title", "新对话"))}</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        for msg in detail.get("messages", []):
            with st.chat_message(msg.get("role", "assistant")):
                st.markdown(msg.get("content", ""))
    else:
        st.caption("该会话已被删除。")

# 输入：手动打优先，其次 sidebar 示例按钮
question = st.chat_input("输入你的数据问题，按 Enter 发送…")
if st.session_state.pending and not question:
    question = st.session_state.pending
    st.session_state.pending = None

if question:
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("判断意图并检索口径..."):
            data = _api(
                "/api/query",
                method="POST",
                json={"question": question, "conversation_id": conversation_id},
            )

        if not data:
            st.error("后端未响应，请确认 uvicorn backend.server:app 已启动。")
        else:
            st.markdown(data.get("answer", ""))

            if data.get("matched_metrics"):
                st.caption("命中指标模板：" + "、".join(data["matched_metrics"]))
            if data.get("schema_mode") == "retrieved":
                st.caption("schema 注入：检索式子集（库较大，仅注入相关表）")

            if data.get("sql"):
                with st.expander("查看执行的 SQL"):
                    st.code(data["sql"], language="sql")

            rows = data.get("rows") or []
            columns = data.get("columns") or []
            if rows:
                table = [
                    {columns[i]: row[i] for i in range(len(columns))}
                    for row in rows
                ]
                with st.expander("查看原始结果"):
                    st.dataframe(table, use_container_width=True)

    if data:
        st.session_state.conversation_id = data.get("conversation_id")