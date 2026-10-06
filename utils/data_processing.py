"""
utils/data_processing.py
------------------------
Pure pandas logic (no Streamlit code in here) so it can be tested on its own.

Pipeline:
    1. load_single_self_ship_file()  -> read + clean ONE uploaded CSV
    2. load_self_ship_files()        -> do step 1 for every file and concat
    3. normalize_order_item_id()     -> "12345-1" -> "12345"
    4. merge_mongo_data()            -> left-join MongoDB columns onto SELF
    5. add_warehouse_columns()       -> Location ID + WH address from Facility
    6. build_quality_report()        -> duplicates / unmapped / missing values
    7. split_by_dispatch_date()      -> SELF  vs  SELF_NOT_DISPATCHED
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

import pandas as pd

import config


# ==========================================================================
# Small result containers
# ==========================================================================
@dataclass
class FileLoadResult:
    """Outcome of reading one uploaded file."""
    file_name: str
    ok: bool
    rows: int = 0
    error: str | None = None
    df: pd.DataFrame | None = None


@dataclass
class LoadSummary:
    """Outcome of reading ALL uploaded files."""
    combined: pd.DataFrame
    file_results: list[FileLoadResult] = field(default_factory=list)
    skipped_duplicate_files: list[str] = field(default_factory=list)
    unparsed_dates: dict[str, int] = field(default_factory=dict)


# ==========================================================================
# Helpers
# ==========================================================================
def file_fingerprint(file_bytes: bytes) -> str:
    """MD5 of the file content - used to detect the same file uploaded twice."""
    return hashlib.md5(file_bytes).hexdigest()


def _read_csv_bytes(file_bytes: bytes) -> pd.DataFrame:
    """
    Read raw CSV bytes with skiprows=3.
    Everything is read as TEXT (dtype=str) so long IDs never turn into
    scientific notation like 4.25E+17. Tries UTF-8 first, then falls back
    to other common encodings.
    """
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(
                io.BytesIO(file_bytes),
                skiprows=config.CSV_SKIP_ROWS,
                low_memory=False,
                dtype=str,
                encoding=encoding,
            )
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise ValueError(f"Could not decode the file: {last_error}")


def _to_yyyy_mm_dd(series: pd.Series) -> pd.Series:
    """
    Convert any date-like text to 'YYYY-MM-DD'. Unparseable values -> NaN.
    format='mixed' lets pandas handle rows that use different date formats.
    """
    cleaned = series.astype("string").str.strip()
    try:
        parsed = pd.to_datetime(cleaned, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(cleaned, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d")


def _clean_text_id(series: pd.Series) -> pd.Series:
    """Remove apostrophes and surrounding spaces from an ID column."""
    return (
        series.astype(str)
        .str.replace("'", "", regex=False)
        .str.strip()
    )


# ==========================================================================
# 1. Load ONE file
# ==========================================================================
def load_single_self_ship_file(file_name: str, file_bytes: bytes) -> FileLoadResult:
    """
    Equivalent of your Jupyter code for a single file:
        - skiprows=3
        - keep only the required columns
        - rename columns
        - strip apostrophes from both Order Item ID columns
        (date conversion happens after concat so it is done once)
    """
    try:
        raw = _read_csv_bytes(file_bytes)
    except Exception as exc:  # noqa: BLE001 - show any read error to user
        return FileLoadResult(file_name=file_name, ok=False, error=str(exc))

    # Header names sometimes come with stray spaces - normalise them
    raw.columns = [str(c).strip() for c in raw.columns]

    missing = [c for c in config.REQUIRED_RAW_COLUMNS if c not in raw.columns]
    if missing:
        return FileLoadResult(
            file_name=file_name,
            ok=False,
            error=(
                "Missing required column(s): " + ", ".join(missing)
                + ". Check that this is a Flipkart Self-Ship export and that the "
                  "header is on row 4."
            ),
        )

    df = raw[config.REQUIRED_RAW_COLUMNS].rename(columns=config.SELF_COLUMN_MAP)

    # Drop completely empty rows (trailing blank lines in the export)
    df = df.dropna(how="all")

    # Remove apostrophes from both Order Item ID columns
    for col in config.APOSTROPHE_COLUMNS:
        df[col] = _clean_text_id(df[col])

    # Other ID-type text columns: just trim spaces
    for col in ["Order ID", "Flipkart SKU", "Invoice Number"]:
        df[col] = df[col].astype("string").str.strip()

    # Quantity -> whole number
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").astype("Int64")

    # Remember which file every row came from
    df[config.SOURCE_FILE_COLUMN] = file_name

    return FileLoadResult(file_name=file_name, ok=True, rows=len(df), df=df)


# ==========================================================================
# 2. Load ALL files and concat
# ==========================================================================
def load_self_ship_files(files: list[tuple[str, bytes]]) -> LoadSummary:
    """
    files: list of (file_name, file_bytes)

    - Reads every file with load_single_self_ship_file()
    - Skips exact duplicate uploads (same content uploaded twice)
    - Concats all good files into ONE dataframe (SELF)
    - Converts all date columns to YYYY-MM-DD
    """
    results: list[FileLoadResult] = []
    skipped_dupes: list[str] = []
    seen_hashes: set[str] = set()

    for name, content in files:
        fp = file_fingerprint(content)
        if fp in seen_hashes:
            skipped_dupes.append(name)
            continue
        seen_hashes.add(fp)
        results.append(load_single_self_ship_file(name, content))

    good_frames = [r.df for r in results if r.ok and r.df is not None and not r.df.empty]

    if good_frames:
        combined = pd.concat(good_frames, ignore_index=True)
    else:
        combined = pd.DataFrame(
            columns=list(config.SELF_COLUMN_MAP.values()) + [config.SOURCE_FILE_COLUMN]
        )

    # Convert all date columns to YYYY-MM-DD and count values that failed
    unparsed: dict[str, int] = {}
    for col in config.DATE_COLUMNS:
        original_has_value = combined[col].notna() & (combined[col].astype(str).str.strip() != "")
        combined[col] = _to_yyyy_mm_dd(combined[col])
        failed = int((original_has_value & combined[col].isna()).sum())
        if failed:
            unparsed[col] = failed

    combined = combined.reset_index(drop=True)

    return LoadSummary(
        combined=combined,
        file_results=results,
        skipped_duplicate_files=skipped_dupes,
        unparsed_dates=unparsed,
    )


# ==========================================================================
# 3. Normalise Order Item ID  ("12345-1" -> "12345")
# ==========================================================================
def normalize_order_item_id(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.split("-")
        .str[0]
        .str.strip()
    )


def unique_order_item_ids(df: pd.DataFrame) -> list[str]:
    """Unique, non-empty Order Item IDs (ignores 'nan', 'None', '')."""
    ids = df["Order Item ID"].dropna().astype(str).str.strip()
    ids = ids[~ids.str.lower().isin(["", "nan", "none", "<na>"])]
    return ids.unique().tolist()


# ==========================================================================
# 4. Prepare MongoDB results and merge into SELF
# ==========================================================================
def prepare_mongo_df(mongo_records: list[dict]) -> pd.DataFrame:
    """
    Turn raw MongoDB documents into a clean dataframe:
      - create base 'Order Item ID' from 'Sale Order Item Code'
      - keep only required columns
      - if several docs share the same base ID, keep the first
    """
    mongo_df = pd.DataFrame(mongo_records)
    if mongo_df.empty:
        return pd.DataFrame(columns=["Order Item ID"] + config.MONGO_MERGE_COLUMNS)

    # Make sure every expected column exists even if Mongo didn't return it
    for col in [config.MONGO_MATCH_FIELD] + config.MONGO_MERGE_COLUMNS:
        if col not in mongo_df.columns:
            mongo_df[col] = None

    mongo_df["Order Item ID"] = normalize_order_item_id(mongo_df[config.MONGO_MATCH_FIELD])

    mongo_df = mongo_df[["Order Item ID"] + config.MONGO_MERGE_COLUMNS]

    mongo_df = mongo_df.drop_duplicates(subset="Order Item ID", keep="first")
    return mongo_df.reset_index(drop=True)


def merge_mongo_data(self_df: pd.DataFrame, mongo_df: pd.DataFrame) -> pd.DataFrame:
    """Left-merge MongoDB columns onto SELF on 'Order Item ID'."""
    if mongo_df.empty:
        out = self_df.copy()
        for col in config.MONGO_MERGE_COLUMNS:
            out[col] = None
        return out
    return self_df.merge(mongo_df, on="Order Item ID", how="left")


# ==========================================================================
# 5. Warehouse columns from Facility
# ==========================================================================
def _clean_facility(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().upper()
    if text in ("", "NAN", "NONE", "<NA>"):
        return None
    return text


def add_warehouse_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    - Clean Facility (strip + upper-case). Missing stays empty instead of
      becoming the text 'NAN'.
    - Add Location ID, WH Address Line 1/2, WH City, WH State, WH Pincode.
    """
    out = df.copy()
    out["Facility"] = out["Facility"].map(_clean_facility)

    for col in config.WH_COLUMNS:
        out[col] = out["Facility"].map(
            lambda facility, c=col: config.FACILITY_DATA.get(facility, {}).get(c)
            if facility
            else None
        )

    # Remove .0 from pincode
    out["WH Pincode"] = pd.to_numeric(out["WH Pincode"], errors="coerce").astype("Int64")
    return out


# ==========================================================================
# 6. Data-quality report
# ==========================================================================
def _is_blank(series: pd.Series) -> pd.Series:
    """True where the value is NaN/None/empty string/'nan'."""
    as_text = series.astype(str).str.strip().str.lower()
    return series.isna() | as_text.isin(["", "nan", "none", "<na>"])


@dataclass
class QualityReport:
    shape_before: tuple[int, int]
    shape_after: tuple[int, int]
    mongo_records_found: int
    duplicate_rows: pd.DataFrame          # every row whose Order Item ID repeats
    duplicate_id_count: int               # how many distinct IDs repeat
    unmapped_rows: pd.DataFrame           # no MongoDB match at all
    missing_shipping_rows: pd.DataFrame   # mapped but Shipping provider blank
    missing_tracking_rows: pd.DataFrame   # mapped but Tracking Number blank
    missing_ship_or_track_rows: pd.DataFrame  # any row missing either (incl. unmapped)
    unknown_facility_rows: pd.DataFrame   # Facility present but not in master


def build_quality_report(
    shape_before: tuple[int, int],
    final_df: pd.DataFrame,
    mongo_records_found: int,
) -> QualityReport:
    dup_mask = final_df.duplicated(subset="Order Item ID", keep=False)
    duplicate_rows = final_df[dup_mask].sort_values("Order Item ID")

    unmapped_mask = _is_blank(final_df["Sale Order Code"])
    ship_blank = _is_blank(final_df["Shipping provider"])
    track_blank = _is_blank(final_df["Tracking Number"])

    facility_present = final_df["Facility"].notna()
    unknown_facility_mask = facility_present & final_df["Location ID"].isna()

    return QualityReport(
        shape_before=shape_before,
        shape_after=final_df.shape,
        mongo_records_found=mongo_records_found,
        duplicate_rows=duplicate_rows,
        duplicate_id_count=int(duplicate_rows["Order Item ID"].nunique()),
        unmapped_rows=final_df[unmapped_mask],
        missing_shipping_rows=final_df[~unmapped_mask & ship_blank],
        missing_tracking_rows=final_df[~unmapped_mask & track_blank],
        missing_ship_or_track_rows=final_df[ship_blank | track_blank],
        unknown_facility_rows=final_df[unknown_facility_mask],
    )


# ==========================================================================
# 7. Dispatch By Date helpers + split
# ==========================================================================
def dispatch_date_counts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Table of Dispatch By Date -> number of rows, sorted oldest first.
    Rows with no date are grouped under config.NO_DATE_LABEL (shown last).
    """
    labels = df["Dispatch By Date"].fillna(config.NO_DATE_LABEL)
    counts = labels.value_counts().rename_axis("Dispatch By Date").reset_index(name="Orders")
    counts["_sort"] = counts["Dispatch By Date"].eq(config.NO_DATE_LABEL)
    counts = counts.sort_values(["_sort", "Dispatch By Date"]).drop(columns="_sort")
    return counts.reset_index(drop=True)


def split_by_dispatch_date(
    df: pd.DataFrame,
    selected_dates: list[str],
    exclude_incomplete: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (SELF, SELF_NOT_DISPATCHED), both with a reset index.

    SELF                -> rows whose Dispatch By Date is in selected_dates
                           (and, if exclude_incomplete=True, that have a
                           Tracking Number, Shipping provider and a mapped
                           warehouse Location ID)
    SELF_NOT_DISPATCHED -> everything else, with a 'Not Dispatched Reason'
    """
    labels = df["Dispatch By Date"].fillna(config.NO_DATE_LABEL)
    date_selected = labels.isin(selected_dates)

    reasons = pd.Series("", index=df.index, dtype="object")
    reasons[~date_selected] = "Dispatch By Date not selected"

    keep = date_selected.copy()

    if exclude_incomplete:
        problems = []
        track_blank = _is_blank(df["Tracking Number"])
        ship_blank = _is_blank(df["Shipping provider"])
        loc_blank = df["Location ID"].isna()

        for mask, text in [
            (_is_blank(df["Sale Order Code"]), "Not found in MongoDB"),
            (track_blank, "Missing Tracking Number"),
            (ship_blank, "Missing Shipping provider"),
            (loc_blank, "Warehouse / Location ID not mapped"),
        ]:
            problems.append(mask.map(lambda m, t=text: t if m else ""))

        problem_text = pd.concat(problems, axis=1).apply(
            lambda row: "; ".join([p for p in row if p]), axis=1
        )
        incomplete = problem_text != ""
        reasons[date_selected & incomplete] = problem_text[date_selected & incomplete]
        keep = date_selected & ~incomplete

    self_df = df[keep].reset_index(drop=True)

    not_dispatched = df[~keep].copy()
    not_dispatched[config.NOT_DISPATCHED_REASON_COLUMN] = reasons[~keep]
    not_dispatched = not_dispatched.reset_index(drop=True)

    return self_df, not_dispatched


# ==========================================================================
# Export helpers
# ==========================================================================
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Write several dataframes to one .xlsx, one sheet each."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            ws = writer.sheets[sheet_name[:31]]
            # Auto-size columns (capped) so the file is readable on open
            for idx, col in enumerate(frame.columns, start=1):
                sample = [str(v) for v in frame[col].head(200).tolist()] + [str(col)]
                width = min(max(len(s) for s in sample) + 2, 60)
                ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    return buffer.getvalue()
