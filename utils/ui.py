"""
utils/ui.py
-----------
Re-usable look-and-feel pieces: global CSS, page header, step headers,
metric cards, and small badges. Keeps the section files clean.
"""

from __future__ import annotations

import html

import streamlit as st

# Brand-ish palette (Flipkart blue + Flo navy)
PRIMARY = "#2874F0"
NAVY = "#172B4D"
YELLOW = "#FFC200"

TONES = {
    "neutral": ("#F4F6FA", "#172B4D", "#DCE3EE"),
    "blue":    ("#EEF4FF", "#1E5BC6", "#C9DAFB"),
    "green":   ("#ECF8F1", "#137A45", "#BFE5CF"),
    "amber":   ("#FFF7E6", "#9A6100", "#F6D99A"),
    "red":     ("#FDEEEE", "#B42318", "#F5C2BF"),
}


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        /* ---------- layout ---------- */
        .block-container {{ padding-top: 3rem; padding-bottom: 4rem; max-width: 1400px; }}
        [data-testid="stSidebar"] {{ background: #F7F9FC; border-right: 1px solid #E6EAF0; }}

        /* ---------- app header ---------- */
        .flo-hero {{
            background: linear-gradient(120deg, {NAVY} 0%, {PRIMARY} 100%);
            border-radius: 16px; padding: 22px 28px; color: #fff;
            margin-bottom: 1.4rem; position: relative; overflow: hidden;
        }}
        .flo-hero::after {{
            content: ""; position: absolute; right: -60px; top: -60px;
            width: 220px; height: 220px; border-radius: 50%;
            background: rgba(255,194,0,0.18);
        }}
        .flo-hero h1 {{ color: #fff; font-size: 1.75rem; margin: 0; padding: 0; font-weight: 700; }}
        .flo-hero .x {{ color: {YELLOW}; padding: 0 6px; }}
        .flo-hero p {{ margin: 6px 0 0 0; opacity: .88; font-size: .98rem; }}

        /* ---------- step header ---------- */
        .flo-step {{ display: flex; align-items: center; gap: 12px; margin: 1.6rem 0 .6rem 0; }}
        .flo-step .num {{
            min-width: 34px; height: 34px; border-radius: 50%;
            background: {PRIMARY}; color: #fff; font-weight: 700;
            display: flex; align-items: center; justify-content: center; font-size: .95rem;
        }}
        .flo-step .num.done {{ background: #137A45; }}
        .flo-step .num.locked {{ background: #B8C2D3; }}
        .flo-step .title {{ font-size: 1.18rem; font-weight: 650; color: {NAVY}; line-height: 1.2; }}
        .flo-step .sub {{ font-size: .88rem; color: #5E6C84; }}

        /* ---------- metric cards ---------- */
        .flo-card {{
            border-radius: 12px; padding: 14px 16px; border: 1px solid;
            height: 100%; min-height: 96px;
        }}
        .flo-card .label {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; opacity: .85; }}
        .flo-card .value {{ font-size: 1.65rem; font-weight: 700; margin-top: 2px; font-variant-numeric: tabular-nums; }}
        .flo-card .hint {{ font-size: .8rem; opacity: .8; margin-top: 2px; }}

        /* ---------- badges ---------- */
        .flo-badge {{
            display: inline-block; padding: 3px 10px; border-radius: 999px;
            font-size: .8rem; font-weight: 600; margin: 2px 4px 2px 0; border: 1px solid;
        }}

        /* ---------- buttons ---------- */
        .stButton > button, .stDownloadButton > button {{ border-radius: 10px; font-weight: 600; }}

        /* ---------- tabs ---------- */
        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
        .stTabs [data-baseweb="tab"] {{ padding: 8px 14px; }}

        /* ---------- confirm box ---------- */
        .flo-confirm {{
            border: 2px dashed {PRIMARY}; background: #F5F9FF; border-radius: 14px;
            padding: 18px 22px; margin-bottom: .8rem;
        }}
        .flo-confirm h3 {{ margin: 0 0 4px 0; color: {NAVY}; font-size: 1.15rem; }}
        .flo-confirm p {{ margin: 0; color: #42526E; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title_left: str, title_right: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="flo-hero">
          <h1>{html.escape(title_left)}<span class="x">×</span>{html.escape(title_right)}</h1>
          <p>{html.escape(subtitle)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def step_header(number: int, title: str, subtitle: str = "", state: str = "active") -> None:
    """state: 'active' | 'done' | 'locked'"""
    css = {"done": "done", "locked": "locked"}.get(state, "")
    label = "✓" if state == "done" else str(number)
    st.markdown(
        f"""
        <div class="flo-step">
          <div class="num {css}">{label}</div>
          <div>
            <div class="title">{html.escape(title)}</div>
            {'<div class="sub">' + html.escape(subtitle) + '</div>' if subtitle else ''}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_card(label: str, value, hint: str = "", tone: str = "neutral") -> None:
    bg, fg, border = TONES.get(tone, TONES["neutral"])
    if isinstance(value, int):
        value = f"{value:,}"
    st.markdown(
        f"""
        <div class="flo-card" style="background:{bg};color:{fg};border-color:{border};">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{html.escape(str(value))}</div>
          <div class="hint">{html.escape(hint)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_row(cards: list[dict]) -> None:
    """cards: list of kwargs for metric_card"""
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        with col:
            metric_card(**card)


def badges(items: list[tuple[str, str]]) -> None:
    """items: list of (text, tone)"""
    parts = []
    for text, tone in items:
        bg, fg, border = TONES.get(tone, TONES["neutral"])
        parts.append(
            f'<span class="flo-badge" style="background:{bg};color:{fg};border-color:{border};">'
            f"{html.escape(text)}</span>"
        )
    st.markdown("".join(parts), unsafe_allow_html=True)


def fmt_shape(shape: tuple[int, int]) -> str:
    return f"{shape[0]:,} rows × {shape[1]} cols"
