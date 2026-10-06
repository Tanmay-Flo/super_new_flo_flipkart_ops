"""
utils/auth.py
-------------
Simple login gate for the whole app.

Credentials are NEVER stored in the code. They live in .streamlit/secrets.toml
(locally) or in the app's Secrets box (Streamlit Community Cloud):

    [auth]
    username = "FKFlo"
    password_salt = "<hex>"
    password_hash = "<hex>"

The password itself is not stored anywhere - only a salted PBKDF2-SHA256 hash.
Generate a new salt + hash with:   python generate_password_hash.py

Protection included:
  * constant-time comparison of username and password hash
  * app-wide lockout after too many wrong attempts (slows down guessing)
  * automatic sign-out after a session timeout
  * Logout button
"""

from __future__ import annotations

import hashlib
import hmac
import html
import threading
import time

import streamlit as st

import config

PBKDF2_ITERATIONS = 310_000

# Lockout: if MAX_FAILED_ATTEMPTS wrong logins happen within FAILED_WINDOW_SECONDS
# (from anyone), the login form is paused for LOCKOUT_SECONDS.
MAX_FAILED_ATTEMPTS = 8
FAILED_WINDOW_SECONDS = 10 * 60
LOCKOUT_SECONDS = 10 * 60

# Signed-in users are logged out after this many hours
SESSION_TIMEOUT_HOURS = 12

_K_OK = "auth_ok"
_K_USER = "auth_user"
_K_AT = "auth_logged_in_at"


# ==========================================================================
# Password hashing
# ==========================================================================
def hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
    ).hex()


def _auth_settings() -> dict | None:
    try:
        sec = dict(st.secrets["auth"])
    except Exception:  # noqa: BLE001 - no secrets file / no [auth] section
        return None
    if not all(sec.get(k) for k in ("username", "password_salt", "password_hash")):
        return None
    return sec


def _credentials_valid(username: str, password: str, sec: dict) -> bool:
    user_ok = hmac.compare_digest(username.strip().encode(), str(sec["username"]).encode())
    try:
        candidate = hash_password(password, str(sec["password_salt"]))
    except ValueError:  # bad salt in secrets
        return False
    pass_ok = hmac.compare_digest(candidate, str(sec["password_hash"]).lower())
    return user_ok and pass_ok


# ==========================================================================
# App-wide failed-attempt tracker (shared by all visitors of this server)
# ==========================================================================
@st.cache_resource
def _failure_log() -> dict:
    return {"times": [], "locked_until": 0.0, "lock": threading.Lock()}


def _lockout_remaining() -> int:
    log = _failure_log()
    return max(0, int(log["locked_until"] - time.time()))


def _record_failure() -> None:
    log = _failure_log()
    now = time.time()
    with log["lock"]:
        log["times"] = [t for t in log["times"] if now - t < FAILED_WINDOW_SECONDS] + [now]
        if len(log["times"]) >= MAX_FAILED_ATTEMPTS:
            log["locked_until"] = now + LOCKOUT_SECONDS
            log["times"] = []


# ==========================================================================
# Session helpers
# ==========================================================================
def is_logged_in() -> bool:
    if not st.session_state.get(_K_OK):
        return False
    if time.time() - st.session_state.get(_K_AT, 0) > SESSION_TIMEOUT_HOURS * 3600:
        logout(rerun=False)
        st.session_state["auth_expired"] = True
        return False
    return True


def current_user() -> str:
    return st.session_state.get(_K_USER, "")


def logout(rerun: bool = True) -> None:
    # Clear EVERYTHING in the session (uploaded data, results, etc.)
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if rerun:
        st.rerun()


# ==========================================================================
# Login page
# ==========================================================================
_LOGIN_CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] { display: none; }
.block-container { max-width: 480px !important; padding-top: 8vh !important; }
.flo-login-hero {
    background: linear-gradient(120deg, #172B4D 0%, #2874F0 100%);
    border-radius: 16px; padding: 26px 26px 22px 26px; color: #fff; text-align: center;
    margin-bottom: 18px;
}
.flo-login-hero h1 { color:#fff; font-size: 1.55rem; margin:0; padding:0; font-weight:700; }
.flo-login-hero .x { color:#FFC200; padding: 0 6px; }
.flo-login-hero p { margin: 8px 0 0 0; opacity: .88; font-size: .92rem; }
.flo-login-foot { text-align:center; color:#7A869A; font-size:.8rem; margin-top: 14px; }
</style>
"""


def require_login() -> None:
    """
    Call right after st.set_page_config(). If the visitor isn't signed in,
    shows the login page and stops the script so nothing else is rendered.
    """
    if is_logged_in():
        return

    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="flo-login-hero">
          <div style="font-size:2rem;line-height:1;margin-bottom:8px;">{config.APP_ICON}</div>
          <h1>Flo<span class="x">×</span>Flipkart Operations</h1>
          <p>Restricted to the Flo Mattress team. Please sign in to continue.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sec = _auth_settings()
    if sec is None:
        st.error(
            "Login is not configured. Add an **[auth]** section with `username`, "
            "`password_salt` and `password_hash` to the app's secrets "
            "(see README → *Login*).",
            icon="🔐",
        )
        st.stop()

    if st.session_state.pop("auth_expired", False):
        st.info("Your session expired. Please sign in again.", icon="⏱️")

    remaining = _lockout_remaining()
    if remaining:
        st.error(
            f"Too many failed sign-in attempts. Please try again in "
            f"{remaining // 60 + 1} minute(s).",
            icon="🚫",
        )
        st.stop()

    with st.form("login_form", clear_on_submit=False, border=True):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

    if submitted:
        if not username or not password:
            st.warning("Please enter both username and password.", icon="✍️")
        elif _credentials_valid(username, password, sec):
            st.session_state[_K_OK] = True
            st.session_state[_K_USER] = username.strip()
            st.session_state[_K_AT] = time.time()
            st.rerun()
        else:
            _record_failure()
            time.sleep(1.0)  # slows down automated guessing
            st.error("Incorrect username or password.", icon="❌")

    st.markdown(
        '<div class="flo-login-foot">Trouble signing in? Contact the Flo data team.</div>',
        unsafe_allow_html=True,
    )
    st.stop()


def sidebar_user_box() -> None:
    """Signed-in user + Logout button, for the sidebar."""
    st.markdown(
        f"👤 Signed in as **{html.escape(current_user())}**",
    )
    if st.button("🚪 Log out", width="stretch"):
        logout()
