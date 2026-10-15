"""
sections/returns.py
-------------------
"Self-Ship Returns" section (first draft - view only).

Steps:
  1. Fetch ALL customer + courier returns from the Flipkart Returns API
     (createdAfter = 1 Sep 2026; follows hasMore/nextUrl pagination)
  2. Map each return to its Unicommerce order item via MongoDB and read
     "Channel Shipping"
  3. Remove Flipkart-managed returns (Channel Shipping = true)
  4. Show the Self-Ship returns with filters (Customer / Courier, status, location)

Nothing in this section changes anything on Flipkart.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

import config
from utils import data_processing as dp
from utils import flipkart_api as fk
from utils import flipkart_returns as fr
from utils import gsheets as gs
from utils import mongo_utils
from utils import returns_processing as rp
from utils.ui import metric_row, step_header

K_STATE = "returns_state"


def _show_df(df: pd.DataFrame, key: str, height: int | str = 460) -> None:
    if df is None or df.empty:
        st.info("No returns to show here.", icon="✅")
        return
    st.dataframe(
        df,
        hide_index=True,
        height="auto" if len(df) <= 10 else height,
        key=key,
        column_config={
            "comments": st.column_config.TextColumn(width="large"),
            "quantity": st.column_config.NumberColumn(format="%d"),
        },
    )


# ==========================================================================
# STEP 1 + 2 - fetch from Flipkart, map with MongoDB
# ==========================================================================
def _run_fetch() -> None:
    with st.status("Fetching returns from Flipkart…", expanded=True) as status:
        labels = {s: st.empty() for s in config.RETURN_SOURCES}

        def _on_page(source: str, pages: int, items: int) -> None:
            labels[source].write(
                f"📦 **{config.RETURN_SOURCES[source]}s** — page {pages:,} · {items:,} returns so far"
            )

        st.write("🔑 Fetching Flipkart access token…")
        try:
            fetch = fr.fetch_all_returns(_on_page)
        except fk.FlipkartConfigError as exc:
            status.update(label="Flipkart credentials missing.", state="error")
            st.error(str(exc), icon="🔐")
            return
        except fk.FlipkartAuthError as exc:
            status.update(label="Could not get a Flipkart access token.", state="error")
            st.error(str(exc), icon="🚫")
            return
        except Exception as exc:  # noqa: BLE001
            status.update(label="Fetching returns failed.", state="error")
            st.error(f"Unexpected error: {exc}", icon="🚫")
            return

        for source, res in fetch.sources.items():
            name = config.RETURN_SOURCES[source]
            if res.error:
                labels[source].write(f"⚠️ **{name}s** — {len(res.items):,} returns from "
                                     f"{res.pages:,} page(s), then stopped: {res.error}")
            else:
                labels[source].write(f"✅ **{name}s** — {len(res.items):,} returns "
                                     f"({res.pages:,} page(s))")

        returns_df = fr.returns_to_dataframe(fetch.all_items)
        records: list[dict] = []
        if not returns_df.empty:
            ids = dp.unique_order_item_ids(
                returns_df.assign(**{"Order Item ID": dp.normalize_order_item_id(
                    returns_df["orderItemId"])})
            )
            st.write(f"🍃 Looking up {len(ids):,} order items in Unicommerce (MongoDB)…")
            bar = st.progress(0.0)
            try:
                records = mongo_utils.fetch_orders_by_item_ids(
                    ids,
                    lambda done, total: bar.progress(done / total,
                                                     text=f"{done:,}/{total:,} order items"),
                    projection=config.RETURNS_MONGO_PROJECTION,
                )
            except mongo_utils.MongoConfigError as exc:
                status.update(label="MongoDB is not configured.", state="error")
                st.error(str(exc), icon="🔐")
                return
            except Exception as exc:  # noqa: BLE001
                status.update(label="MongoDB lookup failed.", state="error")
                st.error(f"Could not fetch data from MongoDB: {exc}", icon="🚫")
                return
            bar.progress(1.0, text="MongoDB lookup complete")

        result = rp.build_returns_result(returns_df, records)
        st.session_state[K_STATE] = {
            "fetch": fetch,
            "result": result,
            "fetched_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        }
        # reset filters for the new data
        for key in [k for k in st.session_state if str(k).startswith("ret_f_")]:
            del st.session_state[key]

        # ---- update the Google Sheet ----------------------------------------
        if config.GSHEET_AUTO_SYNC and not returns_df.empty:
            st.write("📄 Updating the “Flipkart Returns” Google Sheet…")
            sync = gs.sync_returns(_sheet_groups(result))
            st.session_state[K_STATE]["sync"] = sync
            if sync.ok:
                st.write("✅ Google Sheet updated")
            else:
                st.write(f"⚠️ Google Sheet not updated: {sync.error}")

        partial = any(r.error for r in fetch.sources.values())
        status.update(
            label="Returns fetched with warnings ⚠️" if partial else "Returns fetched ✅",
            state="complete",
            expanded=partial,
        )


def _sheet_groups(result: rp.ReturnsResult) -> dict[str, pd.DataFrame]:
    """Full (unfiltered) data for each Google Sheet tab."""
    return {
        "self_ship": result.self_ship,
        "needs_check": result.needs_check,
        "flipkart_managed": result.flipkart_managed,
    }


def _render_sync(state: dict) -> None:
    """Google Sheet sync status + manual sync button."""
    sync: gs.SyncResult | None = state.get("sync")
    with st.container(border=True):
        c1, c2 = st.columns([4, 1.2])
        with c1:
            if sync is None:
                st.markdown(f"📄 **Google Sheet:** not synced yet for this fetch · "
                            f"[Open “Flipkart Returns”]({config.GSHEET_URL})")
            elif sync.ok:
                parts = []
                for t in sync.tabs:
                    bits = [f"{t.added:,} new", f"{t.updated:,} updated"]
                    if t.moved_out:
                        bits.append(f"{t.moved_out:,} moved to another tab")
                    parts.append(f"**{t.tab}:** " + ", ".join(bits) + f" ({t.total_rows:,} rows)")
                st.markdown(f"✅ **Google Sheet updated** at {sync.synced_at} IST · "
                            f"[Open “Flipkart Returns”]({config.GSHEET_URL})  \n" + "  \n".join(parts))
            else:
                st.markdown(f"⚠️ **Google Sheet not updated** — {sync.error}  \n"
                            f"[Open “Flipkart Returns”]({config.GSHEET_URL})")
        with c2:
            label = "🔁 Retry sync" if (sync is not None and not sync.ok) else "📄 Sync now"
            if st.button(label, width="stretch", key="ret_sync_btn"):
                with st.spinner("Updating Google Sheet…"):
                    state["sync"] = gs.sync_returns(_sheet_groups(state["result"]))
                st.rerun()


# ==========================================================================
# STEP 3 + 4 - summary, filters, tables
# ==========================================================================
def _apply_filters(df: pd.DataFrame, sources: list[str], statuses: list[str],
                   facilities: list[str], search: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df[df["type"].isin(sources)] if "type" in df.columns else df
    if statuses:
        out = out[out["status"].isin(statuses)]
    if facilities:
        out = out[out[config.RETURN_FACILITY_COLUMN].fillna("(unknown)").isin(facilities)]
    if search.strip():
        needle = search.strip().lower()
        cols = [c for c in ("returnId", "orderId", "orderItemId", "trackingId",
                            "Sale Order Code", "Tracking Number") if c in out.columns]
        mask = pd.Series(False, index=out.index)
        for c in cols:
            mask |= out[c].astype(str).str.lower().str.contains(needle, regex=False, na=False)
        out = out[mask]
    return out.reset_index(drop=True)


def _render_results(state: dict) -> None:
    fetch: fr.ReturnsFetch = state["fetch"]
    result: rp.ReturnsResult = state["result"]
    all_df = result.all_returns

    # ---------------- STEP 2 summary --------------------------------------
    step_header(2, "Map to Unicommerce & classify",
                f"Fetched {state['fetched_at']} · createdAfter = {fetch.created_after}",
                state="done")

    n_customer = len(fetch.sources.get("customer_return", fr.SourceResult("")).items)
    n_courier = len(fetch.sources.get("courier_return", fr.SourceResult("")).items)
    metric_row([
        {"label": "Customer returns fetched", "value": n_customer, "hint": "source=customer_return",
         "tone": "blue"},
        {"label": "Courier returns fetched", "value": n_courier, "hint": "source=courier_return",
         "tone": "blue"},
        {"label": "Flipkart managed (removed)", "value": len(result.flipkart_managed),
         "hint": "Channel Shipping = true", "tone": "neutral"},
        {"label": "Self-Ship returns", "value": len(result.self_ship),
         "hint": "Channel Shipping = false", "tone": "green" if len(result.self_ship) else "neutral"},
        {"label": "Needs check", "value": len(result.needs_check),
         "hint": "Not in Unicommerce / Channel Shipping blank",
         "tone": "amber" if len(result.needs_check) else "green"},
    ])
    st.write("")
    if not all_df.empty:
        _render_sync(state)

    for source, res in fetch.sources.items():
        if res.error:
            st.warning(
                f"**{config.RETURN_SOURCES[source]}s may be incomplete** — fetched "
                f"{len(res.items):,} returns from {res.pages:,} page(s) before stopping: {res.error}",
                icon="⚠️",
            )
    if len(result.needs_check):
        st.warning(
            f"**{len(result.needs_check):,} return(s) couldn't be classified** — either the order "
            "item isn't in Unicommerce (MongoDB) or its Channel Shipping value is blank. They're "
            "listed in the *Needs check* tab, not hidden.",
            icon="🔎",
        )

    if all_df.empty:
        st.info("Flipkart returned no returns for these filters.", icon="📭")
        return

    # ---------------- STEP 3 filters + tables ------------------------------
    step_header(3, "Self-Ship returns",
                "Flipkart-managed returns are removed. Use the filters to narrow the list.")

    with st.container(border=True):
        type_counts = result.self_ship["type"].value_counts() if not result.self_ship.empty \
            else pd.Series(dtype=int)
        source_keys = list(config.RETURN_SOURCES.keys())
        sources = st.pills(
            "Return type",
            options=source_keys,
            selection_mode="multi",
            default=source_keys,
            format_func=lambda s: f"{config.RETURN_SOURCES[s]} ({int(type_counts.get(s, 0)):,})",
            key="ret_f_type",
        ) or []

        c1, c2, c3 = st.columns([1.2, 1.2, 1.6])
        statuses = c1.multiselect(
            "Status", sorted(all_df["status"].dropna().unique()) if "status" in all_df else [],
            key="ret_f_status", placeholder="All statuses",
        )
        facilities = c2.multiselect(
            "Return location",
            sorted(all_df[config.RETURN_FACILITY_COLUMN].fillna("(unknown)").unique()),
            key="ret_f_fac", placeholder="All locations",
        )
        search = c3.text_input(
            "Search", key="ret_f_search",
            placeholder="Return ID, Order ID, Order Item ID, tracking, Sale Order Code…",
        )

    if not sources:
        st.info("Select at least one return type (Customer Return / Courier Return).", icon="👆")
        return

    self_ship = _apply_filters(result.self_ship, sources, statuses, facilities, search)
    needs = _apply_filters(result.needs_check, sources, statuses, facilities, search)
    removed = _apply_filters(result.flipkart_managed, sources, statuses, facilities, search)

    tabs = st.tabs([
        f"🔁 Self-Ship returns ({len(self_ship):,})",
        f"🔎 Needs check ({len(needs):,})",
        f"🚫 Removed – Flipkart managed ({len(removed):,})",
    ])
    with tabs[0]:
        if not self_ship.empty and "status" in self_ship.columns:
            summary = (self_ship.groupby(["Return Source", "status"]).size()
                       .reset_index(name="Returns"))
            with st.expander("Summary by type and status", expanded=False):
                st.dataframe(summary, hide_index=True)
        _show_df(self_ship, key="ret_tbl_self")
    with tabs[1]:
        st.caption("Returns that could not be mapped to Unicommerce, or whose Channel Shipping "
                   "value is blank. Check these manually.")
        _show_df(needs, key="ret_tbl_needs")
    with tabs[2]:
        st.caption("Channel Shipping = true. These are handled by Flipkart and are not part of "
                   "the Self-Ship list; shown here only for reference.")
        _show_df(removed, key="ret_tbl_removed")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button(
        "⬇️ Download Self-Ship returns (Excel)",
        dp.to_excel_bytes({"Self-Ship Returns": self_ship}),
        file_name=f"selfship_returns_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary", width="stretch", disabled=self_ship.empty,
    )
    d2.download_button(
        "⬇️ Download all (Excel)",
        dp.to_excel_bytes({
            "Self-Ship Returns": self_ship,
            "Needs Check": needs,
            "Flipkart Managed": removed,
        }),
        file_name=f"returns_{stamp}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    st.caption("Downloads follow the filters above.")


# ==========================================================================
# PUBLIC ENTRY POINT
# ==========================================================================
def render() -> None:
    state = st.session_state.get(K_STATE)

    step_header(
        1, "Fetch returns from Flipkart",
        f"Customer + courier returns created after {config.RETURNS_CREATED_AFTER[:10]}. "
        "All pages are fetched.",
        state="done" if state else "active",
    )
    c1, c2, _ = st.columns([1, 1, 3])
    clicked = c1.button("🔄 Fetch returns" if not state else "🔄 Refresh returns",
                        type="primary", width="stretch")
    if c2.button("🧹 Clear", width="stretch", disabled=not state):
        st.session_state.pop(K_STATE, None)
        st.rerun()

    if clicked:
        _run_fetch()
        if st.session_state.get(K_STATE) is not state:   # new data -> redraw buttons/headers
            st.rerun()
        state = st.session_state.get(K_STATE)

    if not state:
        st.caption("Nothing is changed on Flipkart — this section only reads returns.")
        return

    _render_results(state)
