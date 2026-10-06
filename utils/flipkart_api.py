"""
utils/flipkart_api.py
---------------------
Flipkart Seller API integration for Self-Ship dispatch.

Flow (runs when the user clicks "Yes, dispatch"):
    1. Validate every row in SELF - rows with any empty field that the API
       needs are NOT sent to Flipkart (they get a reason instead).
    2. Build one shipment per Sale Order Code (shipmentId). If several order
       items share a Sale Order Code they go in the same shipment's
       invoice.items list.
    3. STEP 1 - GET access token (Basic Auth, client_credentials).
    4. STEP 2 - POST shipments to /sellers/v3/shipments/selfShip/dispatch
       in batches, read Flipkart's response and map it back to every row.

Credentials are read from .streamlit/secrets.toml:
    [flipkart]
    username = "..."
    password = "..."
or from environment variables FLIPKART_USERNAME / FLIPKART_PASSWORD
(a .env file in the project folder is loaded automatically).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import pandas as pd
import requests
import streamlit as st
from requests.auth import HTTPBasicAuth

import config

try:  # optional: lets you keep credentials in a .env file
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


# ==========================================================================
# Errors
# ==========================================================================
class FlipkartConfigError(RuntimeError):
    """Credentials missing."""


class FlipkartAuthError(RuntimeError):
    """Access token could not be fetched."""


# ==========================================================================
# Settings / credentials
# ==========================================================================
@dataclass
class FlipkartSettings:
    username: str
    password: str
    dry_run: bool = False


def _secret_section() -> dict:
    try:
        return dict(st.secrets["flipkart"])
    except Exception:  # noqa: BLE001 - no secrets file / no [flipkart] section
        return {}


def is_dry_run() -> bool:
    """True if [flipkart] dry_run = true in secrets, or FLIPKART_DRY_RUN=true in env."""
    value = _secret_section().get("dry_run", os.getenv("FLIPKART_DRY_RUN", "false"))
    return str(value).strip().lower() in ("1", "true", "yes")


def get_settings() -> FlipkartSettings:
    sec = _secret_section()
    username = sec.get("username") or os.getenv("FLIPKART_USERNAME", "")
    password = sec.get("password") or os.getenv("FLIPKART_PASSWORD", "")
    dry_run = is_dry_run()

    if not dry_run and (not username or not password or "YOUR_" in username or "YOUR_" in password):
        raise FlipkartConfigError(
            "Flipkart API credentials not found. Add a [flipkart] section with username and "
            "password to .streamlit/secrets.toml (or set FLIPKART_USERNAME / FLIPKART_PASSWORD "
            "environment variables)."
        )
    return FlipkartSettings(username=username, password=password, dry_run=dry_run)


# ==========================================================================
# STEP 1 - access token
# ==========================================================================
def fetch_access_token(settings: FlipkartSettings) -> str:
    """
    GET https://seller.api.flipkart.net/oauth-service/oauth/token
        ?grant_type=client_credentials&scope=Seller_Api
    with Basic Auth (username / password).
    """
    try:
        resp = requests.get(
            config.FLIPKART_TOKEN_URL,
            params={"grant_type": "client_credentials", "scope": "Seller_Api"},
            auth=HTTPBasicAuth(settings.username, settings.password),
            timeout=config.FLIPKART_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise FlipkartAuthError(f"Could not reach Flipkart token service: {exc}") from exc

    if resp.status_code != 200:
        raise FlipkartAuthError(
            f"Token request failed (HTTP {resp.status_code}): {resp.text[:500]}"
        )

    try:
        token = resp.json().get("access_token")
    except ValueError as exc:
        raise FlipkartAuthError(f"Token response was not JSON: {resp.text[:500]}") from exc

    if not token:
        raise FlipkartAuthError(f"No access_token in token response: {resp.text[:500]}")
    return token


# ==========================================================================
# Payload helpers
# ==========================================================================
def delivery_partner(shipping_provider) -> str:
    """'DELHIVERY' if 'delhivery' appears anywhere (any case), else 'Other'."""
    if shipping_provider is not None and "delhivery" in str(shipping_provider).lower():
        return "DELHIVERY"
    return "Other"


def utc_now_iso() -> str:
    """Current UTC time as 2026-09-28T12:30:00.000Z"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def date_to_iso_datetime(date_str: str) -> str:
    """'2026-10-06' -> '2026-10-06T12:30:00.000Z' (time from config)."""
    d = datetime.strptime(str(date_str).strip()[:10], "%Y-%m-%d")
    return f"{d.strftime('%Y-%m-%d')}T{config.TENTATIVE_DELIVERY_TIME_UTC}Z"


def _blank(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in ("", "nan", "none", "<na>", "nat")


def _valid_date(value) -> bool:
    try:
        datetime.strptime(str(value).strip()[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validate_rows(df: pd.DataFrame) -> pd.Series:
    """
    Returns a Series (same index as df) with "" for rows that are OK to send,
    otherwise the reason, e.g. "Missing: Tracking Number, Location ID".
    """
    reasons = []
    for _, row in df.iterrows():
        missing = [col for col in config.FLIPKART_REQUIRED_COLUMNS if _blank(row.get(col))]
        problems = []
        if missing:
            problems.append("Missing: " + ", ".join(missing))
        for col in ("Tentative Delivery Date", "Invoice Date"):
            if col not in missing and not _valid_date(row.get(col)):
                problems.append(f"Invalid date in {col}")
        if config.USE_QUANTITY_FROM_FILE and not _blank(row.get("Quantity")):
            try:
                if int(row.get("Quantity")) < 1:
                    problems.append("Quantity must be at least 1")
            except (TypeError, ValueError):
                problems.append("Invalid Quantity")
        reasons.append("; ".join(problems))
    return pd.Series(reasons, index=df.index, dtype="object")


def _pincode_text(value) -> str:
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value).strip()


def _item_quantity(row) -> int:
    if config.USE_QUANTITY_FROM_FILE:
        return int(row["Quantity"])
    return 1


def build_shipments(
    df: pd.DataFrame, dispatch_date: str
) -> tuple[list[dict], dict[str, list], dict]:
    """
    df must contain only rows that passed validate_rows().

    Returns:
        shipments       -> list of shipment dicts (API body format)
        rows_by_sid     -> shipmentId -> list of df index labels in that shipment
        conflicts       -> df index label -> reason (rows that share a
                           Sale Order Code but disagree on shipment-level data)
    """
    shipments: list[dict] = []
    rows_by_sid: dict[str, list] = {}
    conflicts: dict = {}

    for sid, group in df.groupby(df["Sale Order Code"].astype(str).str.strip(), sort=False):
        # Shipment-level fields must be identical for all items in a shipment
        conflicting = [
            col for col in config.FLIPKART_SHIPMENT_LEVEL_COLUMNS
            if group[col].astype(str).str.strip().nunique() > 1
        ]
        if conflicting:
            reason = ("Items with the same Sale Order Code have different "
                      + ", ".join(conflicting))
            for idx in group.index:
                conflicts[idx] = reason
            continue

        first = group.iloc[0]
        partner = delivery_partner(first["Shipping provider"])
        location_id = str(first["Location ID"]).strip()

        shipment = {
            "shipmentId": sid,
            "tentativeDeliveryDate": date_to_iso_datetime(first["Tentative Delivery Date"]),
            "dispatchDate": dispatch_date,
            "deliveryPartner": partner,
            "deliveryPartnerCode": partner,
            "trackingId": str(first["Tracking Number"]).strip(),
            "locationId": location_id,
            "invoice": {
                "invoiceDate": str(first["Invoice Date"]).strip()[:10],
                "items": [
                    {
                        "orderItemId": str(row["Order Item ID"]).strip(),
                        "quantity": _item_quantity(row),
                    }
                    for _, row in group.iterrows()
                ],
            },
            "dispatchLocation": {
                "locationId": location_id,
                "address": {
                    "address1": str(first["WH Address Line 1"]).strip(),
                    "address2": str(first["WH Address Line 2"]).strip(),
                    "city": str(first["WH City"]).strip(),
                    "state": str(first["WH State"]).strip(),
                    "pincode": _pincode_text(first["WH Pincode"]),
                },
            },
        }
        shipments.append(shipment)
        rows_by_sid[sid] = list(group.index)

    return shipments, rows_by_sid, conflicts


# ==========================================================================
# STEP 2 - dispatch call
# ==========================================================================
def _post_dispatch(token: str, shipments: list[dict]) -> tuple[int, object]:
    """
    POST one batch. Retries on network errors, 429 and 5xx.
    Returns (http_status, parsed_json_or_text). http_status 0 = no response.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    body = {"shipments": shipments}
    last_error = ""

    for attempt in range(1, config.FLIPKART_MAX_RETRIES + 1):
        try:
            resp = requests.post(
                config.FLIPKART_DISPATCH_URL,
                headers=headers,
                data=json.dumps(body),
                timeout=config.FLIPKART_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            last_error = f"Network error: {exc}"
        else:
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
            else:
                try:
                    return resp.status_code, resp.json()
                except ValueError:
                    return resp.status_code, resp.text

        if attempt < config.FLIPKART_MAX_RETRIES:
            time.sleep(2 ** attempt)  # 2s, 4s, ...

    return 0, last_error


_SUCCESS_WORDS = {"SUCCESS", "SUCCESSFUL", "OK", "DISPATCHED", "COMPLETED"}


def _compact(obj) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return text[:1000]


def _parse_response(
    status: int, payload: object, shipment_ids: list[str]
) -> dict[str, tuple[bool, str]]:
    """
    Map Flipkart's response to {shipmentId: (success, message)}.

    Handles a per-shipment list such as
        {"shipments": [{"shipmentId": "...", "status": "SUCCESS"}, ...]}
    and falls back to the HTTP status for anything not listed.
    """
    outcome: dict[str, tuple[bool, str]] = {}

    if not (200 <= status < 300):
        msg = f"HTTP {status}: {_compact(payload)}" if status else str(payload)
        return {sid: (False, msg) for sid in shipment_ids}

    entries = []
    if isinstance(payload, dict):
        for key in ("shipments", "results", "response", "shipmentResponses"):
            if isinstance(payload.get(key), list):
                entries = payload[key]
                break
    elif isinstance(payload, list):
        entries = payload

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("shipmentId", entry.get("shipment_id", ""))).strip()
        if sid not in shipment_ids:
            continue
        status_text = str(entry.get("status", "")).strip().upper()
        errors = entry.get("errors") or entry.get("errorMessage") or entry.get("message") or ""
        success = status_text in _SUCCESS_WORDS or (not status_text and not errors)
        msg = status_text or "OK"
        if errors:
            msg += f" — {_compact(errors)}"
        outcome[sid] = (success, msg)

    for sid in shipment_ids:
        if sid not in outcome:
            outcome[sid] = (True, f"HTTP {status} — accepted (no per-shipment status returned)")
    return outcome


# ==========================================================================
# Public entry point
# ==========================================================================
@dataclass
class DispatchOutcome:
    results: pd.DataFrame                    # SELF + result columns
    dry_run: bool
    dispatch_date: str
    payloads: list[dict] = field(default_factory=list)   # every batch body sent
    responses: list[dict] = field(default_factory=list)  # raw response per batch


def dispatch_self_ship_orders(
    self_df: pd.DataFrame,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> DispatchOutcome:
    """
    Validate -> build shipments -> fetch token -> POST in batches.
    Raises FlipkartConfigError / FlipkartAuthError for problems that stop
    the whole run (nothing is dispatched in that case).
    """
    settings = get_settings()
    df = self_df.copy()
    dispatch_date = utc_now_iso()

    df[config.RESULT_STATUS_COLUMN] = ""
    df[config.RESULT_DISPATCHED_COLUMN] = "No"
    df[config.NOT_DISPATCHED_REASON_COLUMN] = ""
    df[config.RESULT_RESPONSE_COLUMN] = ""
    df[config.RESULT_PARTNER_COLUMN] = df["Shipping provider"].map(delivery_partner)
    df[config.RESULT_TIME_COLUMN] = dispatch_date

    # ---- 1. validation ---------------------------------------------------
    reasons = validate_rows(df)
    invalid = reasons != ""
    df.loc[invalid, config.RESULT_STATUS_COLUMN] = "Not Sent – Missing Data"
    df.loc[invalid, config.NOT_DISPATCHED_REASON_COLUMN] = reasons[invalid]

    # ---- 1b. duplicate Order Item IDs: send each item only once ----------
    dup = ~invalid & df.duplicated(subset="Order Item ID", keep="first") & \
        df["Order Item ID"].isin(df.loc[~invalid, "Order Item ID"])
    df.loc[dup, config.RESULT_STATUS_COLUMN] = "Not Sent – Duplicate Row"
    df.loc[dup, config.NOT_DISPATCHED_REASON_COLUMN] = (
        "Duplicate row - this Order Item ID is sent once via its first row")
    invalid = invalid | dup

    # ---- 2. build shipments ---------------------------------------------
    shipments, rows_by_sid, conflicts = build_shipments(df[~invalid], dispatch_date)
    for idx, reason in conflicts.items():
        df.at[idx, config.RESULT_STATUS_COLUMN] = "Not Sent – Conflicting Data"
        df.at[idx, config.NOT_DISPATCHED_REASON_COLUMN] = reason

    outcome = DispatchOutcome(results=df, dry_run=settings.dry_run, dispatch_date=dispatch_date)
    if not shipments:
        return outcome

    batches = [
        shipments[i:i + config.FLIPKART_DISPATCH_BATCH_SIZE]
        for i in range(0, len(shipments), config.FLIPKART_DISPATCH_BATCH_SIZE)
    ]

    # ---- DRY RUN: build payloads only, send nothing ---------------------
    if settings.dry_run:
        for batch in batches:
            outcome.payloads.append({"shipments": batch})
            for s in batch:
                for idx in rows_by_sid[s["shipmentId"]]:
                    df.at[idx, config.RESULT_STATUS_COLUMN] = "Dry Run – Not Sent"
                    df.at[idx, config.NOT_DISPATCHED_REASON_COLUMN] = "Dry run mode is ON"
        return outcome

    # ---- 3. STEP 1: access token ------------------------------------------
    if progress_callback:
        progress_callback(0, len(shipments), "Fetching Flipkart access token…")
    token = fetch_access_token(settings)

    # ---- 4. STEP 2: dispatch in batches ---------------------------------
    done = 0

    def _apply(batch: list[dict], status: int, payload: object) -> None:
        ids = [s["shipmentId"] for s in batch]
        outcome.payloads.append({"shipments": batch})
        outcome.responses.append({"http_status": status, "body": payload})
        for sid, (ok, msg) in _parse_response(status, payload, ids).items():
            for idx in rows_by_sid[sid]:
                df.at[idx, config.RESULT_RESPONSE_COLUMN] = msg
                if ok:
                    df.at[idx, config.RESULT_STATUS_COLUMN] = "Dispatched"
                    df.at[idx, config.RESULT_DISPATCHED_COLUMN] = "Yes"
                else:
                    df.at[idx, config.RESULT_STATUS_COLUMN] = "Failed"
                    df.at[idx, config.NOT_DISPATCHED_REASON_COLUMN] = f"Flipkart rejected: {msg}"

    for batch in batches:
        status, payload = _post_dispatch(token, batch)

        # A 4xx on a multi-shipment batch may be caused by just one bad
        # shipment -> resend one-by-one so the good ones still go through.
        if 400 <= status < 500 and status not in (401, 403) and len(batch) > 1:
            for single in batch:
                s_status, s_payload = _post_dispatch(token, [single])
                _apply([single], s_status, s_payload)
                done += 1
                if progress_callback:
                    progress_callback(done, len(shipments), "Dispatching (one by one)…")
            continue

        _apply(batch, status, payload)
        done += len(batch)
        if progress_callback:
            progress_callback(done, len(shipments), "Dispatching on Flipkart…")

    return outcome


def preview_payload(self_df: pd.DataFrame, max_shipments: int = 3) -> dict:
    """Build (but don't send) the request body for the first few valid shipments."""
    df = self_df.copy()
    valid = validate_rows(df) == ""
    shipments, _, _ = build_shipments(df[valid], utc_now_iso())
    return {"shipments": shipments[:max_shipments]}
