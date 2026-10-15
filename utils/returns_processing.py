"""
utils/returns_processing.py
---------------------------
Map Flipkart returns to Unicommerce order items (MongoDB) and classify them.

    Channel Shipping = true   -> Flipkart-managed return  (removed from the list)
    Channel Shipping = false  -> Self-Ship return          (shown)
    no MongoDB match          -> Not found in Unicommerce  (shown separately)
    matched, value blank      -> Channel Shipping unknown  (shown separately)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config
from utils import data_processing as dp

CAT_SELF_SHIP = "Self-Ship"
CAT_FLIPKART = "Flipkart Managed"
CAT_NOT_FOUND = "Not found in Unicommerce"
CAT_UNKNOWN = "Channel Shipping unknown"

_LOCATION_TO_FACILITY: dict[str, str] = {}
for _fac, _info in config.FACILITY_DATA.items():
    _LOCATION_TO_FACILITY.setdefault(_info["Location ID"], _fac)


def parse_channel_shipping(value) -> bool | None:
    """true/false in any form (bool, 'true', 'TRUE', 'yes', 1 ...) -> bool; else None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip().lower()
    if text in ("true", "yes", "y", "1", "1.0"):
        return True
    if text in ("false", "no", "n", "0", "0.0"):
        return False
    return None


def merge_returns_with_mongo(returns_df: pd.DataFrame, mongo_records: list[dict]) -> pd.DataFrame:
    """
    Attach Unicommerce (MongoDB) columns to every return.

    Matching is on the base Order Item ID (Flipkart orderItemId == the part of
    'Sale Order Item Code' before '-'). When an Order Item ID has several
    MongoDB records (multi-quantity), returns are paired one-to-one in order
    (oldest return -> first record, next -> second ...). If there are MORE
    returns than records (e.g. an older rejected return plus a new one), the
    extra returns re-use the records from the start, so every return still gets
    the Channel Shipping value of its item.
    """
    merge_cols = config.RETURNS_MONGO_MERGE_COLUMNS
    df = returns_df.copy()
    df["Order Item ID"] = dp.normalize_order_item_id(df["orderItemId"])

    mongo_df = dp.prepare_mongo_df(mongo_records, merge_cols)

    # oldest return first, so pairing is stable
    order_cols = [c for c in ("createdDate", "returnId") if c in df.columns]
    df["_row"] = range(len(df))
    df = df.sort_values(order_cols or ["_row"], kind="stable")
    df["_occ"] = df.groupby("Order Item ID", sort=False).cumcount()

    if mongo_df.empty:
        df[config.MONGO_MATCH_FIELD] = None
        for col in merge_cols:
            df[col] = None
        df["_n"] = 0
    else:
        counts = mongo_df.groupby("Order Item ID").size()
        df["_n"] = df["Order Item ID"].map(counts).fillna(0).astype(int)
        df["_pair"] = df["_occ"] % df["_n"].where(df["_n"] > 0, 1)
        mongo_df = mongo_df.rename(columns={dp.OCCURRENCE_COLUMN: "_pair"})
        df = df.merge(mongo_df, on=["Order Item ID", "_pair"], how="left")

    df = df.sort_values("_row").reset_index(drop=True)

    shared = (df["_occ"] >= df["_n"]) & (df["_n"] > 0)
    status = pd.Series("Matched", index=df.index, dtype="object")
    status[df["_n"] == 0] = "Not found in MongoDB"
    status[shared] = "Matched (more returns than Unicommerce items - record shared)"
    df[dp.MONGO_MATCH_STATUS_COLUMN] = status

    return df.drop(columns=[c for c in ("_row", "_occ", "_n", "_pair") if c in df.columns])


def classify_returns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    flag = out[config.CHANNEL_SHIPPING_FIELD].map(parse_channel_shipping)
    category = pd.Series(CAT_UNKNOWN, index=out.index, dtype="object")
    category[flag == True] = CAT_FLIPKART   # noqa: E712 - flag holds True/False/None
    category[flag == False] = CAT_SELF_SHIP  # noqa: E712
    category[out[dp.MONGO_MATCH_STATUS_COLUMN] == "Not found in MongoDB"] = CAT_NOT_FOUND
    out[config.RETURN_CATEGORY_COLUMN] = category
    # show Channel Shipping consistently as "true" / "false" (blank if unknown)
    out[config.CHANNEL_SHIPPING_FIELD] = flag.map({True: "true", False: "false"})

    out[config.RETURN_FACILITY_COLUMN] = out.get("locationId", pd.Series(index=out.index)) \
        .map(lambda loc: _LOCATION_TO_FACILITY.get(str(loc).strip()) if loc else None)

    # Friendly label for the type filter
    if "type" in out.columns:
        out["Return Source"] = out["type"].map(lambda t: config.RETURN_SOURCES.get(t, t))
    return _order_columns(out)


_FRONT = [
    "returnId", "Return Source", "type", "status", "orderId", "orderItemId", "quantity",
    "reason", "subReason", "comments", "createdDate", "updatedDate", "orderDate",
    "locationId", config.RETURN_FACILITY_COLUMN, "courierName", "trackingId",
    "shipmentStatus", "shipmentId", "approvedOn", "expectedDate", "serviceProfile",
    "services_count", "serviceFulfilmentId", "serviceByDate", "serviceCreatedAt",
    "serviceDescription", "sellerId",
]
_UNICOMMERCE = [
    config.RETURN_CATEGORY_COLUMN, config.CHANNEL_SHIPPING_FIELD, "Order Item ID",
    config.MONGO_MATCH_FIELD, "Sale Order Code", "Item SKU Code", "Facility",
    "Shipping provider", "Tracking Number", dp.MONGO_MATCH_STATUS_COLUMN,
]


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    front = [c for c in _FRONT if c in df.columns]
    uc = [c for c in _UNICOMMERCE if c in df.columns]
    rest = [c for c in df.columns if c not in front and c not in uc]
    return df[front + rest + uc]


@dataclass
class ReturnsResult:
    all_returns: pd.DataFrame      # every return, classified
    self_ship: pd.DataFrame        # Channel Shipping = false
    flipkart_managed: pd.DataFrame # Channel Shipping = true (removed from the list)
    needs_check: pd.DataFrame      # not found in Unicommerce / Channel Shipping blank
    mongo_records: int


def build_returns_result(returns_df: pd.DataFrame, mongo_records: list[dict]) -> ReturnsResult:
    if returns_df.empty:
        empty = pd.DataFrame()
        return ReturnsResult(empty, empty, empty, empty, len(mongo_records))
    classified = classify_returns(merge_returns_with_mongo(returns_df, mongo_records))
    cat = classified[config.RETURN_CATEGORY_COLUMN]
    return ReturnsResult(
        all_returns=classified,
        self_ship=classified[cat == CAT_SELF_SHIP].reset_index(drop=True),
        flipkart_managed=classified[cat == CAT_FLIPKART].reset_index(drop=True),
        needs_check=classified[cat.isin([CAT_NOT_FOUND, CAT_UNKNOWN])].reset_index(drop=True),
        mongo_records=len(mongo_records),
    )
