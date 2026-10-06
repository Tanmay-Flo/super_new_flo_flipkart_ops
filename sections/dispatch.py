"""
sections/dispatch.py
--------------------
"Dispatch Self-Ship Orders" section.

Steps shown to the user:
  1. Upload Self-Ship order CSV files (one or many locations)
  2. Process: read + clean + concat -> fetch MongoDB -> merge -> WH columns
  3. Review data quality (shape before/after, duplicates, unmapped, missing)
  4. Choose Dispatch By Date(s) -> split SELF / SELF_NOT_DISPATCHED
  5. Confirm dispatch on Flipkart (Yes / No)

All results live in st.session_state so they survive Streamlit reruns.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st

import config
from utils import data_processing as dp
from utils import flipkart_api
from utils import mongo_utils
from utils.ui import badges, fmt_shape, metric_row, step_header

# --------------------------------------------------------------------------
# Session-state keys (kept in one place to avoid typos)
# --------------------------------------------------------------------------
K_UPLOADER_VERSION = "dispatch_uploader_version"
K_RUN_ID = "dispatch_run_id"
K_RESULT = "dispatch_result"
K_CONFIRM = "dispatch_confirmation"
K_EXCLUDE = "dispatch_exclude_incomplete"
K_DROP_DUPES = "dispatch_drop_duplicates"
K_SELF = "SELF"
K_SELF_NOT = "SELF_NOT_DISPATCHED"
K_OUTCOME = "dispatch_outcome"
K_DISPATCHED_IDS = "dispatched_order_item_ids"


def _init_state() -> None:
    defaults = {
        K_UPLOADER_VERSION: 0,
        K_RUN_ID: 0,
        K_RESULT: None,
        K_CONFIRM: None,
        K_EXCLUDE: True,
        K_DROP_DUPES: False,
        K_SELF: None,
        K_SELF_NOT: None,
        K_OUTCOME: None,
        K_DISPATCHED_IDS: set(),
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def reset_dispatch_state() -> None:
    """Clear everything for a fresh start (also clears the uploader)."""
    st.session_state[K_UPLOADER_VERSION] = st.session_state.get(K_UPLOADER_VERSION, 0) + 1
    for key in (K_RESULT, K_CONFIRM, K_SELF, K_SELF_NOT, K_OUTCOME):
        st.session_state[key] = None
    for key in [k for k in st.session_state.keys() if str(k).startswith("dbd_")]:
        del st.session_state[key]


def _files_signature(files) -> tuple:
    return tuple(sorted((f.name, f.size) for f in files))


def _show_df(df: pd.DataFrame, height: int | str = "auto", key: str | None = None) -> None:
    if df is None or df.empty:
        st.info("Nothing to show here.", icon="✅")
        return
    if len(df) <= 10:
        height = "auto"   # avoid empty filler rows on small tables
    st.dataframe(
        df,
        hide_index=True,
        height=height,
        key=key,
        column_config={
            "Quantity": st.column_config.NumberColumn(format="%d"),
            "WH Pincode": st.column_config.NumberColumn(format="%d"),
        },
    )


# ==========================================================================
# STEP 2 - processing pipeline
# ==========================================================================
def _run_pipeline(uploaded_files) -> None:
    files = [(f.name, f.getvalue()) for f in uploaded_files]

    with st.status("Processing Self-Ship order files…", expanded=True) as status:
        # ---- 1. read + clean + concat ------------------------------------
        st.write(f"📄 Reading **{len(files)}** file(s) (skipping first {config.CSV_SKIP_ROWS} rows)…")
        load = dp.load_self_ship_files(files)

        ok_files = [r for r in load.file_results if r.ok]
        bad_files = [r for r in load.file_results if not r.ok]

        if bad_files:
            for r in bad_files:
                st.write(f"⚠️ `{r.file_name}` skipped — {r.error}")
        if load.skipped_duplicate_files:
            st.write(
                "⚠️ Identical file(s) uploaded twice, ignored: "
                + ", ".join(f"`{n}`" for n in load.skipped_duplicate_files)
            )

        self_df = load.combined
        if self_df.empty:
            status.update(label="No usable data found in the uploaded files.", state="error")
            st.session_state[K_RESULT] = {"load": load, "failed": True}
            return

        st.write(f"✅ Combined **{len(ok_files)}** file(s) → {fmt_shape(self_df.shape)}")
        shape_before = self_df.shape

        # ---- 2. normalise Order Item ID ----------------------------------
        self_df["Order Item ID"] = dp.normalize_order_item_id(self_df["Order Item ID"])
        order_item_ids = dp.unique_order_item_ids(self_df)
        st.write(f"🔑 {len(order_item_ids):,} unique Order Item IDs to look up")

        # ---- 3. fetch MongoDB --------------------------------------------
        st.write("🍃 Fetching matching orders from MongoDB…")
        progress = st.progress(0.0, text="Querying MongoDB…")

        def _on_progress(done: int, total: int) -> None:
            progress.progress(done / total, text=f"Querying MongoDB… {done:,}/{total:,} IDs")

        try:
            records = mongo_utils.fetch_orders_by_item_ids(order_item_ids, _on_progress)
        except mongo_utils.MongoConfigError as exc:
            status.update(label="MongoDB is not configured.", state="error")
            st.error(str(exc), icon="🔐")
            st.session_state[K_RESULT] = None
            return
        except Exception as exc:  # noqa: BLE001
            status.update(label="MongoDB query failed.", state="error")
            st.error(
                f"Could not fetch data from MongoDB: {exc}\n\n"
                "Check your internet connection, the URI in secrets.toml and that your "
                "IP address is allowed in MongoDB Atlas → Network Access.",
                icon="🚫",
            )
            st.session_state[K_RESULT] = None
            return

        progress.progress(1.0, text="MongoDB query complete")
        mongo_df = dp.prepare_mongo_df(records)
        st.write(
            f"✅ MongoDB records found: **{len(records):,}** "
            f"({len(mongo_df):,} unique Order Item IDs after de-duplication)"
        )

        # ---- 4. merge ----------------------------------------------------
        rows_before_merge = len(self_df)
        self_df = dp.merge_mongo_data(self_df, mongo_df)
        st.write(f"🔗 Merged MongoDB data → {fmt_shape(self_df.shape)}")

        # ---- 5. warehouse columns ----------------------------------------
        self_df = dp.add_warehouse_columns(self_df)
        st.write("🏭 Added warehouse (Location ID / WH address) columns")

        # ---- 6. quality report -------------------------------------------
        report = dp.build_quality_report(shape_before, self_df, len(records))

        st.session_state[K_RUN_ID] += 1
        st.session_state[K_RESULT] = {
            "failed": False,
            "load": load,
            "report": report,
            "data": self_df,
            "rows_before_merge": rows_before_merge,
            "signature": _files_signature(uploaded_files),
            "processed_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
            "run_id": st.session_state[K_RUN_ID],
        }
        st.session_state[K_CONFIRM] = None
        status.update(label="Processing complete ✅", state="complete", expanded=False)


# ==========================================================================
# STEP 3 - review
# ==========================================================================
def _render_review(result: dict) -> None:
    load: dp.LoadSummary = result["load"]
    report: dp.QualityReport = result["report"]
    data: pd.DataFrame = result["data"]

    n_unmapped = len(report.unmapped_rows)
    n_missing = len(report.missing_ship_or_track_rows)
    n_dup_rows = len(report.duplicate_rows)
    ok_files = [r for r in load.file_results if r.ok]
    bad_files = [r for r in load.file_results if not r.ok]

    step_header(
        3, "Review data quality",
        f"Processed {result['processed_at']} · {len(ok_files)} file(s) loaded",
        state="done",
    )

    metric_row([
        {"label": "Shape before merge", "value": fmt_shape(report.shape_before),
         "hint": "After combining all files", "tone": "neutral"},
        {"label": "Shape after merge", "value": fmt_shape(report.shape_after),
         "hint": "With MongoDB + WH columns", "tone": "blue"},
        {"label": "MongoDB records found", "value": report.mongo_records_found,
         "hint": "Raw documents matched", "tone": "blue"},
    ])
    st.write("")
    metric_row([
        {"label": "Duplicate Order Item IDs", "value": report.duplicate_id_count,
         "hint": f"{n_dup_rows:,} rows involved" if n_dup_rows else "No duplicates",
         "tone": "amber" if n_dup_rows else "green"},
        {"label": "Orders not mapped", "value": n_unmapped,
         "hint": "No match in MongoDB", "tone": "red" if n_unmapped else "green"},
        {"label": "Missing Shipping / Tracking", "value": n_missing,
         "hint": "Shipping provider or Tracking Number blank (incl. unmapped)",
         "tone": "red" if n_missing else "green"},
        {"label": "Unknown facility", "value": len(report.unknown_facility_rows),
         "hint": "Facility not in warehouse master",
         "tone": "amber" if len(report.unknown_facility_rows) else "green"},
    ])

    # ---- written summary of the checks -----------------------------------
    st.write("")
    if report.shape_before[0] == report.shape_after[0]:
        st.success(
            f"Row count unchanged by the merge ({report.shape_before[0]:,} → "
            f"{report.shape_after[0]:,}). Columns went from {report.shape_before[1]} to "
            f"{report.shape_after[1]}.",
            icon="✅",
        )
    else:
        st.warning(
            f"Row count changed during the merge: {report.shape_before[0]:,} → "
            f"{report.shape_after[0]:,}. Please check the Duplicates tab.",
            icon="⚠️",
        )

    if n_dup_rows:
        st.warning(
            f"**Duplicates found:** {report.duplicate_id_count:,} Order Item ID(s) appear more "
            f"than once ({n_dup_rows:,} rows). See the *Duplicates* tab below.",
            icon="🔁",
        )
    else:
        st.success("**No duplicates** found on the Order Item ID column.", icon="✅")

    if n_unmapped or n_missing:
        st.error(
            f"**{n_unmapped:,}** order(s) did not map to MongoDB and **{n_missing:,}** order(s) "
            "have a missing Shipping provider or Tracking Number.",
            icon="🚩",
        )

    if load.unparsed_dates:
        details = ", ".join(f"{col}: {n}" for col, n in load.unparsed_dates.items())
        st.warning(f"Some date values could not be read and were left blank → {details}", icon="📅")

    if bad_files or load.skipped_duplicate_files:
        with st.expander("⚠️ Files that were skipped", expanded=True):
            for r in bad_files:
                st.markdown(f"- **{r.file_name}** — {r.error}")
            for name in load.skipped_duplicate_files:
                st.markdown(f"- **{name}** — identical to another uploaded file, ignored")

    # ---- detail tabs -----------------------------------------------------
    tabs = st.tabs([
        f"📋 All orders ({len(data):,})",
        f"🚫 Not mapped ({n_unmapped:,})",
        f"📭 Missing shipping/tracking ({n_missing:,})",
        f"🔁 Duplicates ({n_dup_rows:,})",
        f"🏭 Unknown facility ({len(report.unknown_facility_rows):,})",
        "🗂️ Files & facilities",
    ])
    with tabs[0]:
        _show_df(data, height=420, key="tbl_all")
    with tabs[1]:
        _show_df(report.unmapped_rows, key="tbl_unmapped")
    with tabs[2]:
        c1, c2 = st.columns(2)
        c1.caption(f"Mapped but Shipping provider blank: **{len(report.missing_shipping_rows):,}**")
        c2.caption(f"Mapped but Tracking Number blank: **{len(report.missing_tracking_rows):,}**")
        _show_df(report.missing_ship_or_track_rows, key="tbl_missing")
    with tabs[3]:
        _show_df(report.duplicate_rows, key="tbl_dupes")
    with tabs[4]:
        _show_df(report.unknown_facility_rows, key="tbl_unknown_fac")
    with tabs[5]:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Rows per file**")
            file_table = pd.DataFrame(
                [{"File": r.file_name, "Status": "Loaded" if r.ok else "Skipped",
                  "Rows": r.rows, "Note": r.error or ""} for r in load.file_results]
            )
            st.dataframe(file_table, hide_index=True)
        with c2:
            st.markdown("**Rows per facility**")
            fac = (data["Facility"].fillna("(not mapped)").value_counts()
                   .rename_axis("Facility").reset_index(name="Orders"))
            st.dataframe(fac, hide_index=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button(
        "⬇️ Download full data (CSV)", dp.to_csv_bytes(data),
        file_name=f"selfship_merged_{stamp}.csv", mime="text/csv", width="stretch",
    )
    d2.download_button(
        "⬇️ Download quality report (Excel)",
        dp.to_excel_bytes({
            "All Orders": data,
            "Not Mapped": report.unmapped_rows,
            "Missing Ship-Track": report.missing_ship_or_track_rows,
            "Duplicates": report.duplicate_rows,
            "Unknown Facility": report.unknown_facility_rows,
        }),
        file_name=f"selfship_quality_report_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )


# ==========================================================================
# STEP 4 - Dispatch By Date selection
# ==========================================================================
def _render_date_selection(result: dict) -> tuple[pd.DataFrame, pd.DataFrame, tuple] | None:
    data: pd.DataFrame = result["data"]
    run_id = result["run_id"]

    step_header(
        4, "Choose which Dispatch By Date orders to dispatch",
        "Tick one or more dates. Only those orders will be kept for dispatch.",
    )

    counts = dp.dispatch_date_counts(data)
    labels = counts["Dispatch By Date"].tolist()
    today = datetime.now().strftime("%Y-%m-%d")

    def _key(label: str) -> str:
        return f"dbd_{run_id}_{label}"

    def _set_all(value: bool) -> None:
        for lbl in labels:
            st.session_state[_key(lbl)] = value

    def _select_due() -> None:
        for lbl in labels:
            st.session_state[_key(lbl)] = lbl != config.NO_DATE_LABEL and lbl <= today

    b1, b2, b3, _ = st.columns([1, 1, 1.4, 2.6])
    b1.button("Select all", on_click=_set_all, args=(True,), width="stretch")
    b2.button("Clear all", on_click=_set_all, args=(False,), width="stretch")
    b3.button("Select today & overdue", on_click=_select_due, width="stretch",
              help=f"Selects every Dispatch By Date on or before today ({today})")

    with st.container(border=True):
        n_cols = 3
        cols = st.columns(n_cols)
        for i, row in counts.iterrows():
            label, n = row["Dispatch By Date"], int(row["Orders"])
            if label == config.NO_DATE_LABEL:
                pretty = f"{label}  ·  {n:,} order{'' if n == 1 else 's'}"
            else:
                dt = datetime.strptime(label, "%Y-%m-%d")
                tag = " · today" if label == today else (" · overdue" if label < today else "")
                pretty = f"{dt.strftime('%a %d %b %Y')}{tag} — {n:,} order{'' if n == 1 else 's'}"
            cols[i % n_cols].checkbox(pretty, key=_key(label))

    selected = [lbl for lbl in labels if st.session_state.get(_key(lbl), False)]

    with st.expander("⚙️ Dispatch options", expanded=False):
        st.toggle(
            "Exclude orders with missing Tracking Number / Shipping provider / warehouse "
            "(move them to SELF_NOT_DISPATCHED)",
            key=K_EXCLUDE,
            help="Recommended. Flipkart cannot dispatch an order without tracking details "
                 "and a pickup location.",
        )
        st.toggle(
            "Remove duplicate Order Item IDs (keep the first occurrence)",
            key=K_DROP_DUPES,
        )

    if not selected:
        st.info("Select at least one Dispatch By Date to continue.", icon="👆")
        return None

    working = data
    removed_dupes = 0
    if st.session_state[K_DROP_DUPES]:
        before = len(working)
        working = working.drop_duplicates(subset="Order Item ID", keep="first")
        removed_dupes = before - len(working)

    self_df, self_not = dp.split_by_dispatch_date(
        working, selected, exclude_incomplete=st.session_state[K_EXCLUDE]
    )

    # Keep the two dataframes in session_state under the names used in the notebook
    st.session_state[K_SELF] = self_df
    st.session_state[K_SELF_NOT] = self_not

    sel_text = [(s, "blue") for s in selected]
    badges(sel_text)

    n_excluded_incomplete = int(
        (self_not[config.NOT_DISPATCHED_REASON_COLUMN] != "Dispatch By Date not selected").sum()
    )
    metric_row([
        {"label": "Selected for dispatch (SELF)", "value": len(self_df),
         "hint": f"{self_df['Order ID'].nunique():,} unique Flipkart orders" if len(self_df) else "",
         "tone": "green" if len(self_df) else "red"},
        {"label": "Not dispatched", "value": len(self_not),
         "hint": "SELF_NOT_DISPATCHED", "tone": "neutral"},
        {"label": "Held back – incomplete data", "value": n_excluded_incomplete,
         "hint": "Selected date but missing details", "tone": "amber" if n_excluded_incomplete else "green"},
        {"label": "Duplicates removed", "value": removed_dupes,
         "hint": "Only if the option is on", "tone": "neutral"},
    ])
    st.write("")

    t1, t2 = st.tabs([f"✅ SELF – to dispatch ({len(self_df):,})",
                      f"⏸️ SELF_NOT_DISPATCHED ({len(self_not):,})"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    with t1:
        _show_df(self_df, height=360, key="tbl_self")
        if not self_df.empty:
            st.download_button("⬇️ Download SELF (CSV)", dp.to_csv_bytes(self_df),
                               file_name=f"SELF_to_dispatch_{stamp}.csv", mime="text/csv")
    with t2:
        if not self_not.empty:
            reason_counts = (self_not[config.NOT_DISPATCHED_REASON_COLUMN].value_counts()
                             .rename_axis("Reason").reset_index(name="Orders"))
            st.dataframe(reason_counts, hide_index=True)
        _show_df(self_not, height=360, key="tbl_self_not")
        if not self_not.empty:
            st.download_button("⬇️ Download SELF_NOT_DISPATCHED (CSV)", dp.to_csv_bytes(self_not),
                               file_name=f"SELF_NOT_DISPATCHED_{stamp}.csv", mime="text/csv")

    selection_sig = (
        run_id, tuple(selected), st.session_state[K_EXCLUDE], st.session_state[K_DROP_DUPES]
    )
    return self_df, self_not, selection_sig


# ==========================================================================
# STEP 5 - confirmation + Flipkart dispatch
# ==========================================================================
RESULT_COLUMNS = [
    config.RESULT_STATUS_COLUMN,
    config.RESULT_DISPATCHED_COLUMN,
    config.NOT_DISPATCHED_REASON_COLUMN,
    config.RESULT_RESPONSE_COLUMN,
    config.RESULT_PARTNER_COLUMN,
    config.RESULT_TIME_COLUMN,
]


def _remember_dispatched(results: pd.DataFrame) -> None:
    """Remember successfully dispatched Order Item IDs so they're never sent twice."""
    ok = results[results[config.RESULT_DISPATCHED_COLUMN] == "Yes"]["Order Item ID"].astype(str)
    st.session_state[K_DISPATCHED_IDS] = set(st.session_state[K_DISPATCHED_IDS]) | set(ok)


def _combine_results(results_df: pd.DataFrame, self_not: pd.DataFrame) -> pd.DataFrame:
    """
    One table with EVERY order item from the upload:
      - rows that went through the dispatch call (results_df)
      - rows that were never sent (SELF_NOT_DISPATCHED)
    """
    nd = self_not.copy()
    if not nd.empty:
        not_selected = nd[config.NOT_DISPATCHED_REASON_COLUMN] == "Dispatch By Date not selected"
        nd[config.RESULT_STATUS_COLUMN] = "Held Back – Incomplete Data"
        nd.loc[not_selected, config.RESULT_STATUS_COLUMN] = "Not Selected for Dispatch"
        nd[config.RESULT_DISPATCHED_COLUMN] = "No"
        nd[config.RESULT_RESPONSE_COLUMN] = ""
        nd[config.RESULT_PARTNER_COLUMN] = ""
        nd[config.RESULT_TIME_COLUMN] = ""
    combined = pd.concat([results_df, nd], ignore_index=True)

    # Put the result columns at the end in a fixed, readable order
    result_cols = [
        config.RESULT_DISPATCHED_COLUMN,
        config.RESULT_STATUS_COLUMN,
        config.NOT_DISPATCHED_REASON_COLUMN,
        config.RESULT_PARTNER_COLUMN,
        config.RESULT_RESPONSE_COLUMN,
        config.RESULT_TIME_COLUMN,
    ]
    other = [c for c in combined.columns if c not in result_cols]
    return combined[other + result_cols]


def _save_audit_log(all_results: pd.DataFrame, outcome: flipkart_api.DispatchOutcome) -> str | None:
    """Save every run to dispatch_logs/ so there is always a record of what was sent."""
    try:
        os.makedirs(config.DISPATCH_LOG_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "dryrun" if outcome.dry_run else "dispatch"
        base = os.path.join(config.DISPATCH_LOG_DIR, f"{prefix}_{stamp}")
        all_results.to_csv(base + "_results.csv", index=False, encoding="utf-8-sig")
        with open(base + "_api_log.json", "w", encoding="utf-8") as fh:
            json.dump({"dispatch_date": outcome.dispatch_date, "requests": outcome.payloads,
                       "responses": outcome.responses}, fh, indent=2, default=str)
        return base
    except OSError:
        return None


def _run_dispatch(rows: pd.DataFrame) -> flipkart_api.DispatchOutcome | None:
    """Call Flipkart with a progress bar. Returns None if the run was stopped."""
    progress = st.progress(0.0, text="Preparing shipments…")

    def _on_progress(done: int, total: int, text: str) -> None:
        progress.progress(done / total if total else 1.0, text=f"{text} {done:,}/{total:,} shipments")

    try:
        outcome = flipkart_api.dispatch_self_ship_orders(rows, _on_progress)
    except flipkart_api.FlipkartConfigError as exc:
        progress.empty()
        st.error(str(exc), icon="🔐")
        return None
    except flipkart_api.FlipkartAuthError as exc:
        progress.empty()
        st.error(f"**Step 1 failed – could not get a Flipkart access token.** Nothing was "
                 f"dispatched.\n\n{exc}", icon="🚫")
        return None
    except Exception as exc:  # noqa: BLE001
        progress.empty()
        st.error(f"Unexpected error during dispatch: {exc}", icon="🚫")
        return None
    progress.progress(1.0, text="Done")
    return outcome


def _render_precheck(self_df: pd.DataFrame) -> None:
    reasons = flipkart_api.validate_rows(self_df)
    n_bad = int((reasons != "").sum())
    if n_bad:
        st.warning(
            f"**{n_bad:,}** of {len(self_df):,} selected order item(s) have empty or invalid "
            "fields required by the Flipkart API and **will not be sent**. "
            "They'll be listed with the reason in the final download.",
            icon="⚠️",
        )
        with st.expander(f"See the {n_bad:,} item(s) that will be skipped"):
            show = self_df[reasons != ""][["Order ID", "Order Item ID", "Sale Order Code",
                                           "Facility", "Tracking Number"]].copy()
            show["Reason"] = reasons[reasons != ""]
            st.dataframe(show, hide_index=True)

    with st.expander("🔍 Preview the API request (first 3 shipments)"):
        st.caption("`dispatchDate` will be set to the exact time you click **Yes, dispatch**.")
        st.json(flipkart_api.preview_payload(self_df), expanded=2)


def _render_dispatch_results(state: dict, self_not: pd.DataFrame) -> None:
    outcome: flipkart_api.DispatchOutcome = state["outcome"]
    results = outcome.results
    all_results = _combine_results(results, self_not)
    status = all_results[config.RESULT_STATUS_COLUMN]

    n_dispatched = int((status == "Dispatched").sum())
    n_failed = int((status == "Failed").sum())
    n_missing = int(status.str.startswith("Not Sent").sum())
    n_held = int(status.isin(["Held Back – Incomplete Data", "Not Selected for Dispatch",
                              "Skipped – Already Dispatched"]).sum())
    n_dry = int((status == "Dry Run – Not Sent").sum())

    if outcome.dry_run:
        st.info("**Dry run** — requests were built but **nothing was sent to Flipkart**. "
                "Set `dry_run = false` in secrets.toml for a real dispatch.", icon="🧪")
    elif n_failed == 0 and n_dispatched:
        st.success(f"**{n_dispatched:,}** order item(s) marked as dispatched on Flipkart.", icon="🚚")
    elif not n_dispatched and int((status == "Skipped – Already Dispatched").sum()) and not n_failed:
        st.info("All selected order items were already dispatched earlier in this session — "
                "nothing new was sent.", icon="ℹ️")
    elif n_dispatched:
        st.warning(f"**{n_dispatched:,}** dispatched, **{n_failed:,}** failed. "
                   "Check the reasons below.", icon="⚠️")
    else:
        st.error("No order items were dispatched. Check the reasons below.", icon="🚫")

    metric_row([
        {"label": "Total order items", "value": len(all_results), "hint": "Everything uploaded",
         "tone": "neutral"},
        {"label": "Dispatched", "value": n_dispatched, "hint": "Accepted by Flipkart",
         "tone": "green" if n_dispatched else "neutral"},
        {"label": "Failed", "value": n_failed, "hint": "Rejected / API error",
         "tone": "red" if n_failed else "green"},
        {"label": "Not sent – data issues", "value": n_missing,
         "hint": "Missing / invalid / conflicting / duplicate", "tone": "amber" if n_missing else "green"},
        {"label": "Not selected / held back" if not n_dry else "Dry run – not sent",
         "value": n_held if not n_dry else n_dry,
         "hint": "SELF_NOT_DISPATCHED" if not n_dry else "Dry run mode", "tone": "neutral"},
    ])
    st.write("")

    dispatched_df = all_results[all_results[config.RESULT_DISPATCHED_COLUMN] == "Yes"]
    not_dispatched_df = all_results[all_results[config.RESULT_DISPATCHED_COLUMN] != "Yes"]

    tabs = st.tabs([
        f"📋 All order items ({len(all_results):,})",
        f"✅ Dispatched ({len(dispatched_df):,})",
        f"⛔ Not dispatched ({len(not_dispatched_df):,})",
        "🧾 API log",
    ])
    with tabs[0]:
        _show_df(all_results, height=420, key="tbl_res_all")
    with tabs[1]:
        _show_df(dispatched_df, height=360, key="tbl_res_ok")
    with tabs[2]:
        if not not_dispatched_df.empty:
            summary = (not_dispatched_df[config.RESULT_STATUS_COLUMN].value_counts()
                       .rename_axis("Status").reset_index(name="Order items"))
            st.dataframe(summary, hide_index=True)
        _show_df(not_dispatched_df, height=360, key="tbl_res_not")
    with tabs[3]:
        st.caption(f"dispatchDate sent: `{outcome.dispatch_date}` · "
                   f"{len(outcome.payloads)} request(s)")
        for i, req in enumerate(outcome.payloads):
            resp = outcome.responses[i] if i < len(outcome.responses) else None
            with st.expander(f"Request {i + 1} · {len(req['shipments'])} shipment(s)"
                             + (f" · HTTP {resp['http_status']}" if resp else "")):
                st.markdown("**Request body**")
                st.json(req, expanded=False)
                if resp:
                    st.markdown("**Response**")
                    st.json(resp["body"] if isinstance(resp["body"], (dict, list))
                            else {"text": str(resp["body"])}, expanded=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    d1, d2, d3 = st.columns(3)
    d1.download_button(
        "⬇️ Download all order items (CSV)", dp.to_csv_bytes(all_results),
        file_name=f"flipkart_dispatch_results_{stamp}.csv", mime="text/csv",
        type="primary", width="stretch",
    )
    d2.download_button(
        "⬇️ Download results (Excel)",
        dp.to_excel_bytes({
            "All Order Items": all_results,
            "Dispatched": dispatched_df,
            "Not Dispatched": not_dispatched_df,
        }),
        file_name=f"flipkart_dispatch_results_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    d3.download_button(
        "⬇️ Download API log (JSON)",
        json.dumps({"dispatch_date": outcome.dispatch_date, "requests": outcome.payloads,
                    "responses": outcome.responses}, indent=2, default=str).encode("utf-8"),
        file_name=f"flipkart_dispatch_api_log_{stamp}.json", mime="application/json",
        width="stretch",
    )
    if state.get("log_path"):
        st.caption(f"📁 A copy of this run was saved to `{state['log_path']}_results.csv`. "
                   "On Streamlit Cloud this folder is temporary — always download the results.")

    # ---- retry only the failed ones ---------------------------------------
    if n_failed and not outcome.dry_run:
        st.write("")
        if st.button(f"🔁 Retry the {n_failed:,} failed order item(s)"):
            failed_mask = results[config.RESULT_STATUS_COLUMN] == "Failed"
            base_cols = [c for c in results.columns if c not in RESULT_COLUMNS]
            retry_rows = results.loc[failed_mask, base_cols]
            retry = _run_dispatch(retry_rows)
            if retry is not None:
                merged = results.copy()
                retry_df = retry.results.set_axis(retry_rows.index)
                merged.loc[retry_rows.index, retry_df.columns] = retry_df
                _remember_dispatched(merged)
                outcome.results = merged
                outcome.payloads += retry.payloads
                outcome.responses += retry.responses
                state["log_path"] = _save_audit_log(_combine_results(merged, self_not), outcome)
                st.rerun()


def _render_confirmation(self_df: pd.DataFrame, self_not: pd.DataFrame, selection_sig: tuple) -> None:
    # If a dispatch already ran for this exact selection, only show its results.
    # This also prevents sending the same orders twice.
    dispatch_state = st.session_state.get(K_OUTCOME)
    already_ran = bool(dispatch_state and dispatch_state.get("sig") == selection_sig)
    step_header(5, "Confirm dispatch on Flipkart",
                "Dispatch complete – results below" if already_ran else
                "Step 1: access token → Step 2: mark as dispatched",
                state="done" if already_ran else "active")
    if already_ran:
        _render_dispatch_results(dispatch_state, self_not)
        return

    confirm = st.session_state.get(K_CONFIRM)
    if confirm and confirm.get("sig") != selection_sig:
        st.session_state[K_CONFIRM] = None
        confirm = None

    if self_df.empty:
        st.warning("No orders are eligible for dispatch with the current selection.", icon="🚫")
        return

    if flipkart_api.is_dry_run():
        st.info("**Dry run mode is ON** (`dry_run = true` in secrets.toml). Clicking Yes will "
                "build the API requests but **not send** them to Flipkart.", icon="🧪")

    _render_precheck(self_df)

    n_orders = self_df["Order ID"].nunique()
    facilities = self_df["Facility"].fillna("(not mapped)").value_counts()
    fac_text = " · ".join(f"{f}: {c}" for f, c in facilities.items())

    st.markdown(
        f"""
        <div class="flo-confirm">
          <h3>Would you like to dispatch these orders on Flipkart?</h3>
          <p><b>{len(self_df):,}</b> order item(s) across <b>{n_orders:,}</b> Flipkart order(s).<br>
          {fac_text}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if confirm and confirm["answer"] == "no":
        st.info("Dispatch cancelled. Nothing was sent to Flipkart. "
                "You can change the selection above or confirm again.", icon="🛑")
        if st.button("↩️ Change answer"):
            st.session_state[K_CONFIRM] = None
            st.rerun()
        return

    c1, c2, _ = st.columns([1, 1, 3])
    yes = c1.button("✅ Yes, dispatch", type="primary", width="stretch")
    no = c2.button("❌ No", width="stretch")

    if no:
        st.session_state[K_CONFIRM] = {"answer": "no", "sig": selection_sig}
        st.rerun()

    if yes:
        st.session_state[K_CONFIRM] = {"answer": "yes", "sig": selection_sig}
        already_mask = self_df["Order Item ID"].astype(str).isin(st.session_state[K_DISPATCHED_IDS])
        outcome = _run_dispatch(self_df[~already_mask])
        if outcome is None:
            st.session_state[K_CONFIRM] = None
            return
        if already_mask.any():
            skipped = self_df[already_mask].copy()
            skipped[config.RESULT_STATUS_COLUMN] = "Skipped – Already Dispatched"
            skipped[config.RESULT_DISPATCHED_COLUMN] = "No"
            skipped[config.NOT_DISPATCHED_REASON_COLUMN] = (
                "Already marked as dispatched earlier in this session")
            skipped[config.RESULT_RESPONSE_COLUMN] = ""
            skipped[config.RESULT_PARTNER_COLUMN] = ""
            skipped[config.RESULT_TIME_COLUMN] = ""
            outcome.results = pd.concat([outcome.results, skipped], ignore_index=True)
        _remember_dispatched(outcome.results)
        log_path = _save_audit_log(_combine_results(outcome.results, self_not), outcome)
        st.session_state[K_OUTCOME] = {"sig": selection_sig, "outcome": outcome,
                                       "log_path": log_path}
        st.rerun()


# ==========================================================================
# PUBLIC ENTRY POINT
# ==========================================================================
def render() -> None:
    _init_state()

    # ---------------- STEP 1: upload -------------------------------------
    result = st.session_state.get(K_RESULT)
    step_header(
        1, "Upload Self-Ship order files",
        "Please upload the Self-Ship Order files for all locations here.",
        state="done" if result and not result.get("failed") else "active",
    )

    uploaded = st.file_uploader(
        "Please upload the Self-Ship Order files for all locations here.",
        type=["csv"],
        accept_multiple_files=True,
        key=f"selfship_uploader_{st.session_state[K_UPLOADER_VERSION]}",
        help="Flipkart Seller Hub → Self-Ship orders export (.csv). "
             "The first 3 rows are skipped automatically; row 4 is used as the header.",
    )

    if uploaded:
        total_kb = sum(f.size for f in uploaded) / 1024
        badges([(f"📄 {f.name}", "neutral") for f in uploaded]
               + [(f"{len(uploaded)} file(s) · {total_kb:,.0f} KB", "blue")])

    # Invalidate old results if the uploaded files changed
    if result and not result.get("failed") and uploaded and \
            result.get("signature") != _files_signature(uploaded):
        st.warning("The uploaded files changed since the last run. Click **Process files** "
                   "to refresh the results.", icon="🔄")

    # ---------------- STEP 2: process ------------------------------------
    step_header(2, "Process & fetch order details",
                "Combine files → fetch MongoDB → add warehouse details",
                state="done" if result and not result.get("failed") else
                ("active" if uploaded else "locked"))

    c1, c2, _ = st.columns([1, 1, 3])
    process_clicked = c1.button("⚙️ Process files", type="primary",
                                disabled=not uploaded, width="stretch")
    c2.button("🧹 Start over", on_click=reset_dispatch_state, width="stretch")

    if process_clicked and uploaded:
        _run_pipeline(uploaded)
        result = st.session_state.get(K_RESULT)

    if not result:
        if not uploaded:
            st.caption("Upload at least one CSV file to begin.")
        return

    if result.get("failed"):
        load = result["load"]
        st.error("None of the uploaded files could be used.", icon="🚫")
        for r in load.file_results:
            if not r.ok:
                st.markdown(f"- **{r.file_name}** — {r.error}")
        return

    # ---------------- STEP 3: review -------------------------------------
    _render_review(result)

    # ---------------- STEP 4: date selection -----------------------------
    split = _render_date_selection(result)
    if split is None:
        return
    self_df, self_not, selection_sig = split

    # ---------------- STEP 5: confirm ------------------------------------
    _render_confirmation(self_df, self_not, selection_sig)
