"""
utils/gsheets.py
----------------
Sync the returns tables to the "Flipkart Returns" Google Sheet.

For each tab ("Self-Ship Returns", "Needs Check", "Flipkart Managed"):
    * a row whose returnId is already in the tab is UPDATED in place
    * a row with a new returnId is APPENDED at the bottom
    * rows already in the tab that aren't in this fetch are KEPT as they are
    * (optional) a returnId that now belongs to a different tab is removed
      from its old tab, so each return lives in exactly one tab

Values are written RAW, so long IDs like 12360320720177001498923008 stay
exact text (no 1.23E+25 / rounding).

Credentials (service account) - one of:
  1. Streamlit secrets, recommended (works on Streamlit Cloud):
        [gcp_service_account]
        type = "service_account"
        project_id = "..."
        private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
        client_email = "...@...iam.gserviceaccount.com"
        ...every other field from the JSON key...
  2. Local only: path to the JSON key file
        [google_sheets]
        service_account_file = "logical-etching-422305-k8-6f0329c5456f.json"
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GSheetsConfigError(RuntimeError):
    """Service-account credentials missing or unreadable."""


# ==========================================================================
# Credentials / client
# ==========================================================================
def _service_account_info() -> dict:
    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:  # noqa: BLE001 - no secrets file
        pass

    path = ""
    try:
        path = st.secrets.get("google_sheets", {}).get("service_account_file", "")
    except Exception:  # noqa: BLE001
        pass
    path = path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except OSError as exc:
            raise GSheetsConfigError(f"Could not read service account file '{path}': {exc}") from exc

    raise GSheetsConfigError(
        "Google service account not configured. Add a [gcp_service_account] section to "
        "your secrets (see README → Google Sheet sync)."
    )


@st.cache_resource(show_spinner=False)
def _client(info_json: str):
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(json.loads(info_json), scopes=SCOPES)
    return gspread.authorize(creds)


def service_account_email() -> str:
    try:
        return _service_account_info().get("client_email", "")
    except GSheetsConfigError:
        return ""


def open_spreadsheet():
    info = _service_account_info()
    return _client(json.dumps(info, sort_keys=True)).open_by_key(config.GSHEET_SPREADSHEET_ID)


# ==========================================================================
# Pure merge logic (no Google calls - easy to test)
# ==========================================================================
def _cell(value):
    """Convert a pandas/numpy value into something Sheets accepts (RAW)."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return "true" if value else "false"
    if hasattr(value, "item"):          # numpy scalar -> python
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return int(value) if value.is_integer() else value
    if isinstance(value, int):
        return value
    return str(value)


def _key(value) -> str:
    return "" if value is None else str(value).strip()


@dataclass
class TabStats:
    tab: str
    updated: int = 0
    added: int = 0
    moved_out: int = 0
    kept: int = 0           # rows already in the tab and untouched by this fetch
    total_rows: int = 0


def merge_tab(
    existing: list[list],
    df: pd.DataFrame,
    key_col: str,
    synced_at: str,
    remove_keys: set[str] | None = None,
) -> tuple[list[list], TabStats]:
    """
    existing : current tab values (first row = header), as returned by get_all_values()
    df       : new rows for this tab
    Returns the full new grid (header + rows) and stats.
    """
    stats = TabStats(tab="")
    remove_keys = remove_keys or set()

    header = [str(h) for h in existing[0]] if existing and any(str(h).strip() for h in existing[0]) else []
    rows = [list(r) for r in existing[1:]] if header else []

    # header = existing columns (order kept) + any new columns at the end
    for col in list(df.columns) + [config.GSHEET_SYNC_COLUMN]:
        if col not in header:
            header.append(col)
    width = len(header)
    rows = [r + [""] * (width - len(r)) if len(r) < width else r[:width] for r in rows]
    pos = {c: i for i, c in enumerate(header)}
    kpos = pos[key_col]

    # drop completely empty rows, and rows that moved to another tab
    clean = []
    for r in rows:
        if not any(str(v).strip() for v in r):
            continue
        if _key(r[kpos]) in remove_keys:
            stats.moved_out += 1
            continue
        clean.append(r)
    rows = clean

    index: dict[str, int] = {}
    for i, r in enumerate(rows):
        k = _key(r[kpos])
        if k and k not in index:
            index[k] = i

    touched: set[int] = set()
    for rec in df.to_dict(orient="records"):
        k = _key(rec.get(key_col))
        if not k:
            continue
        if k in index:
            line = rows[index[k]]
            stats.updated += 1
            touched.add(index[k])
        else:
            line = [""] * width
            rows.append(line)
            index[k] = len(rows) - 1
            touched.add(index[k])
            stats.added += 1
        for col, val in rec.items():
            line[pos[col]] = _cell(val)
        line[pos[config.GSHEET_SYNC_COLUMN]] = synced_at

    stats.kept = len(rows) - len(touched)
    stats.total_rows = len(rows)
    return [header] + rows, stats


# ==========================================================================
# Write to Google
# ==========================================================================
def _write_grid(ws, grid: list[list], old_row_count: int) -> None:
    from gspread.utils import rowcol_to_a1

    n_rows, n_cols = len(grid), len(grid[0])
    if ws.row_count < n_rows or ws.col_count < n_cols:
        ws.resize(rows=max(ws.row_count, n_rows), cols=max(ws.col_count, n_cols))

    ws.update(grid, "A1", value_input_option="RAW")

    # if the tab got shorter (rows moved to another tab), blank the leftover rows
    if old_row_count > n_rows:
        start = rowcol_to_a1(n_rows + 1, 1)
        end = rowcol_to_a1(old_row_count, max(n_cols, ws.col_count))
        ws.batch_clear([f"{start}:{end}"])


@dataclass
class SyncResult:
    ok: bool
    synced_at: str
    tabs: list[TabStats] = field(default_factory=list)
    error: str | None = None


def sync_returns(groups: dict[str, pd.DataFrame]) -> SyncResult:
    """
    groups: {"self_ship": df, "needs_check": df, "flipkart_managed": df}
    (keys match config.GSHEET_TABS). Never raises - errors come back in .error.
    """
    synced_at = datetime.now(ZoneInfo(config.RETURNS_TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S")
    key_col = config.GSHEET_KEY_COLUMN
    try:
        sheet = open_spreadsheet()
        existing_tabs = {ws.title: ws for ws in sheet.worksheets()}

        # which tab each returnId belongs to in THIS fetch
        owner: dict[str, str] = {}
        for group, df in groups.items():
            if df is not None and not df.empty and key_col in df.columns:
                for k in df[key_col].map(_key):
                    if k:
                        owner[k] = group

        result = SyncResult(ok=True, synced_at=synced_at)
        for group, tab_name in config.GSHEET_TABS.items():
            df = groups.get(group)
            if df is None:
                df = pd.DataFrame(columns=[key_col])
            if key_col not in df.columns:
                df = df.assign(**{key_col: None})

            ws = existing_tabs.get(tab_name)
            if ws is None:
                ws = sheet.add_worksheet(title=tab_name, rows=max(len(df) + 10, 100),
                                         cols=max(len(df.columns) + 2, 26))
            existing = ws.get_all_values()

            remove = set()
            if config.GSHEET_MOVE_BETWEEN_TABS:
                remove = {k for k, g in owner.items() if g != group}

            grid, stats = merge_tab(existing, df, key_col, synced_at, remove)
            stats.tab = tab_name
            _write_grid(ws, grid, old_row_count=len(existing))
            result.tabs.append(stats)
        return result

    except GSheetsConfigError as exc:
        return SyncResult(ok=False, synced_at=synced_at, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        if exc.__class__.__name__ in ("SpreadsheetNotFound", "PermissionError") or "403" in msg \
                or "PERMISSION_DENIED" in msg or "404" in msg:
            email = service_account_email()
            msg = (f"The service account can't open the sheet. Share \"Flipkart Returns\" with "
                   f"{email or 'the service account email'} as Editor. ({msg})")
        return SyncResult(ok=False, synced_at=synced_at, error=msg)
