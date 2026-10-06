"""
app.py
------
Entry point for the "Flo x Flipkart Operations" Streamlit app.

Run it from the project folder with:
    streamlit run app.py
"""

import streamlit as st

import config
from sections import dispatch, returns
from utils import auth, mongo_utils
from utils.ui import inject_css, page_header

# --------------------------------------------------------------------------
# Page setup (must be the first Streamlit call)
# --------------------------------------------------------------------------
st.set_page_config(
    page_title=config.APP_NAME,
    page_icon=config.APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()

# --------------------------------------------------------------------------
# Login gate - nothing below this line runs until the user signs in
# --------------------------------------------------------------------------
auth.require_login()

SECTIONS = {
    config.SECTION_DISPATCH: {
        "icon": "🚚",
        "subtitle": "Upload Self-Ship order files, verify them against FloBridge "
                    "(MongoDB), and prepare orders for dispatch on Flipkart.",
        "render": dispatch.render,
    },
    config.SECTION_RETURNS: {
        "icon": "🔄",
        "subtitle": "Mark Flipkart Self-Ship orders as returned (coming soon).",
        "render": returns.render,
    },
}

# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"## {config.APP_ICON} {config.APP_NAME}")
    st.caption("Flipkart Self-Ship operations for Flo Mattress")
    auth.sidebar_user_box()
    st.divider()

    section = st.radio(
        "Section",
        options=list(SECTIONS.keys()),
        format_func=lambda s: f"{SECTIONS[s]['icon']}  {s}",
        key="active_section",
    )

    st.divider()
    st.markdown("**Connections**")
    if st.button("🔌 Test MongoDB connection", width="stretch"):
        with st.spinner("Pinging MongoDB…"):
            ok, msg = mongo_utils.test_connection()
        st.session_state["mongo_status"] = (ok, msg)

    if "mongo_status" in st.session_state:
        ok, msg = st.session_state["mongo_status"]
        (st.success if ok else st.error)(msg, icon="🍃" if ok else "⚠️")

    st.divider()
    st.caption(
        "Tip: use **Start over** in a section to clear uploaded files and results."
    )

# --------------------------------------------------------------------------
# Main area
# --------------------------------------------------------------------------
page_header("Flo ", " Flipkart Operations", SECTIONS[section]["subtitle"])
st.markdown(f"### {SECTIONS[section]['icon']} {section}")
SECTIONS[section]["render"]()
