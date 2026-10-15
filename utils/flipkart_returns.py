"""
utils/flipkart_returns.py
-------------------------
Fetch ALL Flipkart returns (customer + courier) from the Returns API v2.

    GET https://api.flipkart.net/sellers/v2/returns
        ?source=customer_return|courier_return
        &createdAfter=2026-09-01T00:00:00         (config.RETURNS_CREATED_AFTER, no offset)
    Headers: Content-Type: application/json, Authorization: Bearer <token>

The response is paginated:
    {"hasMore": true, "nextUrl": "/returns/search/ewog...", "returnItems": [...]}
While hasMore is true, the next page is fetched from nextUrl (which already
contains the original filters, so no query parameters are re-sent).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import config
from utils import flipkart_api as fk


# ==========================================================================
# Helpers
# ==========================================================================
def now_ist() -> str:
    """Current IST time as 2026-10-14T12:02:00 (no timezone offset)."""
    return datetime.now(ZoneInfo(config.RETURNS_TIMEZONE)).strftime("%Y-%m-%dT%H:%M:%S")


def _next_page_url(next_url: str) -> str:
    """Turn the API's nextUrl into a full URL."""
    next_url = str(next_url).strip()
    if next_url.startswith("http"):
        return next_url
    if next_url.startswith("/sellers/"):
        return "https://api.flipkart.net" + next_url
    if not next_url.startswith("/"):
        next_url = "/" + next_url
    return config.FLIPKART_RETURNS_NEXT_BASE.rstrip("/") + next_url


def _get_with_retry(url: str, token: str, params: dict | None = None) -> requests.Response:
    """GET with retries on network errors, HTTP 429 and 5xx."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    last_error = ""
    for attempt in range(1, config.FLIPKART_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params,
                                timeout=config.FLIPKART_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = f"Network error: {exc}"
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            else:
                return resp
        if attempt < config.FLIPKART_MAX_RETRIES:
            time.sleep(2 ** attempt)
    raise RuntimeError(last_error)


# ==========================================================================
# Fetch
# ==========================================================================
@dataclass
class SourceResult:
    source: str
    items: list[dict] = field(default_factory=list)
    pages: int = 0
    error: str | None = None        # set if fetching stopped early


@dataclass
class ReturnsFetch:
    created_after: str
    sources: dict[str, SourceResult] = field(default_factory=dict)

    @property
    def all_items(self) -> list[dict]:
        out = []
        for res in self.sources.values():
            out.extend(res.items)
        return out


def fetch_source(
    token: str,
    source: str,
    created_after: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> SourceResult:
    """Fetch every page for one source. Stops early (with .error) on a failure."""
    result = SourceResult(source=source)
    url = config.FLIPKART_RETURNS_URL
    params: dict | None = {"source": source, "createdAfter": created_after}
    seen_urls: set[str] = set()

    while True:
        try:
            resp = _get_with_retry(url, token, params)
        except RuntimeError as exc:
            result.error = f"Page {result.pages + 1}: {exc}"
            break

        if resp.status_code != 200:
            result.error = f"Page {result.pages + 1}: HTTP {resp.status_code}: {resp.text[:300]}"
            break
        try:
            body = resp.json()
        except ValueError:
            result.error = f"Page {result.pages + 1}: response was not JSON: {resp.text[:300]}"
            break

        items = body.get("returnItems") or []
        result.items.extend(items)
        result.pages += 1
        if progress_callback:
            progress_callback(source, result.pages, len(result.items))

        next_url = body.get("nextUrl")
        if not body.get("hasMore") or not next_url:
            break
        if result.pages >= config.RETURNS_MAX_PAGES:
            result.error = f"Stopped after {config.RETURNS_MAX_PAGES} pages (safety limit)."
            break

        url = _next_page_url(next_url)
        if url in seen_urls:          # API returned the same page link again
            result.error = "Pagination returned a repeated nextUrl; stopped to avoid a loop."
            break
        seen_urls.add(url)
        params = None                  # nextUrl already contains the filters

    return result


def fetch_all_returns(
    progress_callback: Callable[[str, int, int], None] | None = None,
    created_after: str | None = None,
) -> ReturnsFetch:
    """
    Fetch customer AND courier returns created after `created_after`
    (defaults to config.RETURNS_CREATED_AFTER, i.e. 1 Sep 2026). Raises FlipkartConfigError / FlipkartAuthError if
    credentials are missing or the token can't be fetched.
    """
    settings = fk.get_settings()
    if not settings.username or not settings.password:
        raise fk.FlipkartConfigError(
            "Flipkart API credentials are needed to fetch returns. Add username and "
            "password to the [flipkart] section of your secrets."
        )
    token = fk.fetch_access_token(settings)

    fetch = ReturnsFetch(created_after=created_after or config.RETURNS_CREATED_AFTER)
    for source in config.RETURN_SOURCES:
        fetch.sources[source] = fetch_source(token, source, fetch.created_after,
                                             progress_callback)
    return fetch


# ==========================================================================
# Flatten into a table
# ==========================================================================
_DATE_FIELDS = ["orderDate", "createdDate", "updatedDate", "approvedOn", "expectedDate"]


def _to_ist(series: pd.Series) -> pd.Series:
    """ISO strings (UTC 'Z' or +05:30) -> 'YYYY-MM-DD HH:MM' in IST."""
    parsed = pd.to_datetime(series, errors="coerce", utc=True, format="ISO8601")
    return parsed.dt.tz_convert(config.RETURNS_TIMEZONE).dt.strftime("%Y-%m-%d %H:%M")


def _join(values) -> str | None:
    vals = [str(v) for v in values if v not in (None, "")]
    return ", ".join(vals) if vals else None


def returns_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """
    One row per return item. Every top-level API field is kept as its own
    column (same names as the API). The 'services' list is flattened into:
        services_count, serviceFulfilmentId, serviceByDate, serviceCreatedAt
    (comma-separated when there is more than one service).
    """
    rows = []
    for item in items:
        row = {k: v for k, v in item.items() if k != "services"}
        services = item.get("services") or []
        row["services_count"] = len(services)
        row["serviceFulfilmentId"] = _join(s.get("serviceFulfilmentId") for s in services)
        row["serviceByDate"] = _join(s.get("serviceByDate") for s in services)
        row["serviceCreatedAt"] = _join(s.get("createdAt") for s in services)
        row["serviceDescription"] = _join(s.get("description") for s in services)
        # keep any other top-level field that isn't a plain value as JSON text
        for k, v in list(row.items()):
            if isinstance(v, (dict, list)):
                row[k] = json.dumps(v, ensure_ascii=False)
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for col in ("orderItemId", "orderId", "returnId", "trackingId", "shipmentId",
                "locationId", "sellerId"):
        if col in df.columns:
            df[col] = df[col].map(lambda v: None if v is None else str(v).strip())

    for col in _DATE_FIELDS:
        if col in df.columns:
            df[col] = _to_ist(df[col])

    # The same return can show up twice (e.g. on two pages) - keep one
    key = [c for c in ("returnId", "orderItemId") if c in df.columns]
    if key:
        df = df.drop_duplicates(subset=key, keep="first")

    sort_cols = [c for c in ("createdDate",) if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=False, kind="stable")
    return df.reset_index(drop=True)
