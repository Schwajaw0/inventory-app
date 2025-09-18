import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from datetime import datetime, timezone
from dateutil import tz

def _full_height(n_rows: int, row_h: int = 38, header_h: int = 38, max_h: int = 2200) -> int:
    """Return a height tall enough to show all rows without a scroll bar."""
    return min(max_h, header_h + row_h * max(1, n_rows))

# =========================
# Page & constants
# =========================
st.set_page_config(page_title="Inventory Dashboard", layout="wide", page_icon="📦")
st.title("Inventory Dashboard")

LOCAL_TZ = tz.gettz("America/Chicago")  # change if needed

# ---- Secrets / Settings (set in Streamlit Cloud: Settings → Secrets) ----
# Required:
#   SHEET_ID
#   [gcp_service_account] {...}
# Optional:
#   WORKSHEET_NAME (default "Sheet1")
#   META_SHEET_NAME (default "Meta")
#   EDITOR_PIN (PIN for editor unlock)
#   ORDERS_SHEET_NAME (default "Orders")
#   MAP_SHEET_NAME (default "Map")
SHEET_ID = st.secrets["SHEET_ID"]
WORKSHEET_NAME = st.secrets.get("WORKSHEET_NAME", "Sheet1")
META_SHEET_NAME = st.secrets.get("META_SHEET_NAME", "Meta")
ORDERS_SHEET_NAME = st.secrets.get("ORDERS_SHEET_NAME", "Orders")
MAP_SHEET_NAME = st.secrets.get("MAP_SHEET_NAME", "Map")
EDITOR_PIN = st.secrets.get("EDITOR_PIN", None)

# =========================
# Google Sheets helpers
# =========================
def _gc():
    return gspread.service_account_from_dict(st.secrets["gcp_service_account"])

@st.cache_data(ttl=30)
def read_inventory_sheet() -> pd.DataFrame:
    gc = _gc()
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)
    df = get_as_dataframe(ws, evaluate_formulas=True, header=0)

    # Normalize expected columns
    for col in ["Item", "SKU", "OnHand", "MinLevel"]:
        if col not in df.columns:
            df[col] = "" if col in ["Item", "SKU"] else 0

    df = df[["Item", "SKU", "OnHand", "MinLevel"]].dropna(how="all")

    # Clean numerics
    for col in ["OnHand", "MinLevel"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["LowStock"] = df["OnHand"] <= df["MinLevel"]
    return df

def write_inventory_sheet(df: pd.DataFrame):
    """Writes inventory and updates Meta!B2 with timestamp."""
    gc = _gc()
    sh = gc.open_by_key(SHEET_ID)

    # Inventory
    ws = sh.worksheet(WORKSHEET_NAME)
    out = df[["Item", "SKU", "OnHand", "MinLevel"]].copy()
    ws.clear()
    set_with_dataframe(ws, out, include_index=False, include_column_header=True)

    # Meta timestamp
    stamp = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    try:
        try:
            meta = sh.worksheet(META_SHEET_NAME)
        except gspread.WorksheetNotFound:
            meta = sh.add_worksheet(title=META_SHEET_NAME, rows=10, cols=3)
            meta.update("A1:B1", [["Key", "Value"]])
            meta.update("A2", [["last_updated"]])
        meta.update("B2", [[stamp]])
    except Exception:
        pass  # don't fail saves if meta update fails

@st.cache_data(ttl=20)
def read_last_updated_from_meta() -> str | None:
    """Reads Meta!B2 as the 'last updated' timestamp. Returns None if missing."""
    try:
        gc = _gc()
        sh = gc.open_by_key(SHEET_ID)
        meta = sh.worksheet(META_SHEET_NAME)
        val = meta.acell("B2").value
        return val.strip() if val else None
    except Exception:
        return None

# ===== Orders sheet helpers =====
@st.cache_data(ttl=20)
def read_orders_sheet() -> pd.DataFrame:
    gc = _gc()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(ORDERS_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=ORDERS_SHEET_NAME, rows=1000, cols=9)
        ws.update(
            "A1:I1",
            [["OrderId","OrderName","LineId","SKU","Qty","Completed","CompletedAt","CreatedDate","Note"]]
        )
    df = get_as_dataframe(ws, evaluate_formulas=True, header=0)

    # Normalize columns
    cols = ["OrderId","OrderName","LineId","SKU","Qty","Completed","CompletedAt","CreatedDate","Note"]
    for col in cols:
        if col not in df.columns:
            df[col] = "" if col not in ["Qty","Completed"] else (0 if col == "Qty" else False)

    df = df[cols].dropna(how="all")
    df["Qty"] = pd.to_numeric(df["Qty"], errors="coerce").fillna(0).astype(int)
    df["Completed"] = df["Completed"].astype(str).str.lower().isin(["true","1","yes","y","t"])
    return df

def write_orders_sheet(df: pd.DataFrame):
    gc = _gc()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(ORDERS_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=ORDERS_SHEET_NAME, rows=1000, cols=9)
    out = df[["OrderId","OrderName","LineId","SKU","Qty","Completed","CompletedAt","CreatedDate","Note"]].copy()
    ws.clear()
    set_with_dataframe(ws, out, include_index=False, include_column_header=True)

# ===== Map sheet helpers (JamlinerLength -> BalanceSize) =====
@st.cache_data(ttl=60)
def read_map_sheet() -> pd.DataFrame:
    gc = _gc()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(MAP_SHEET_NAME)
    except gspread.WorksheetNotFound:
        # bootstrap an empty Map sheet with headers
        ws = sh.add_worksheet(title=MAP_SHEET_NAME, rows=100, cols=3)
        ws.update("A1:C1", [["JamlinerLength","BalanceSize","UnitsPerOrder"]])
    df = get_as_dataframe(ws, evaluate_formulas=True, header=0)
    for col in ["JamlinerLength","BalanceSize","UnitsPerOrder"]:
        if col not in df.columns:
            df[col] = "" if col != "UnitsPerOrder" else 1
    df = df[["JamlinerLength","BalanceSize","UnitsPerOrder"]].dropna(how="all")
    df["UnitsPerOrder"] = pd.to_numeric(df["UnitsPerOrder"], errors="coerce").fillna(1).astype(int)
    # Clean strings
    df["JamlinerLength"] = df["JamlinerLength"].astype(str).str.strip()
    df["BalanceSize"] = df["BalanceSize"].astype(str).str.strip()
    return df

def apply_completions_update_inventory(orders_before: pd.DataFrame,
                                       orders_after: pd.DataFrame,
                                       inventory_df: pd.DataFrame,
                                       map_df: pd.DataFrame) -> pd.DataFrame:
    """
    Find order lines that changed Completed: False -> True.
    For each line: use Map (JamlinerLength -> BalanceSize, UnitsPerOrder)
    and decrement inventory on that BalanceSize: OnHand -= Qty * UnitsPerOrder.
    """
    # Inventory index by BalanceSize (Item column)
    inv_key = "Item"  # Balance Size is tracked in 'Item'
    inv = inventory_df.set_index(inv_key).copy()

    # Build lookup from JamlinerLength (SKU) -> (BalanceSize, UnitsPerOrder)
    m = map_df.dropna(subset=["JamlinerLength","BalanceSize"]).copy()
    m["JamlinerLength"] = m["JamlinerLength"].astype(str).str.strip()
    m["BalanceSize"] = m["BalanceSize"].astype(str).str.strip()
    map_lookup = {
        row["JamlinerLength"]: (row["BalanceSize"], int(row["UnitsPerOrder"]))
        for _, row in m.iterrows()
    }

    # Detect newly-completed lines
    before = orders_before.set_index("LineId")
    after  = orders_after.set_index("LineId")

    changed = []
    for line_id in after.index:
        was = bool(before.loc[line_id, "Completed"]) if line_id in before.index else False
        now = bool(after.loc[line_id, "Completed"])
        if (not was) and now:
            changed.append(line_id)

    # Apply decrements
    for line_id in changed:
        jamliner = str(after.loc[line_id, "SKU"]).strip()  # SKU column is the Jamliner Length (product you make)
        qty      = int(after.loc[line_id, "Qty"] or 0)
        if qty <= 0:
            continue

        if jamliner not in map_lookup:
            st.warning(f"No Map entry for JamlinerLength '{jamliner}'. Skipping decrement for line {line_id}.")
            continue

        balance_size, units_per = map_lookup[jamliner]
        total_consume = qty * max(units_per, 1)

        if balance_size not in inv.index:
            st.warning(f"Balance Size '{balance_size}' not found in Inventory. Skipping decrement for line {line_id}.")
            continue

        cur = int(inv.loc[balance_size, "OnHand"])
        inv.loc[balance_size, "OnHand"] = cur - total_consume

    # Recompute LowStock flag
    out = inv.reset_index()
    out["LowStock"] = out["OnHand"] <= out["MinLevel"]
    return out

# =========================
# Load initial data
# =========================
df = read_inventory_sheet()
orders_df = read_orders_sheet()
map_df = read_map_sheet()
last_updated = read_last_updated_from_meta()
last_fetched = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

# =========================
# Top bar: timestamps
# =========================
c1, c2, c3 = st.columns([1.6, 1, 1])
with c1:
    st.caption(f"**Last updated (from sheet)**: {last_updated if last_updated else '—'}")
with c2:
    st.caption(f"**Last fetched**: {last_fetched}")
with c3:
    st.caption("Auto-refresh ~30s")

# =========================
# Sidebar
# =========================
st.sidebar.header("Section")
page = st.sidebar.radio("Go to", ["Inventory", "Orders"], index=0)

# Editor lock state
if "can_edit" not in st.session_state:
    st.session_state.can_edit = False

# Info banner
if st.session_state.can_edit:
    st.sidebar.warning("Editor mode: Changes allowed")
else:
    st.sidebar.success("Manager mode: Read-only (default)")

# PIN unlock control
with st.sidebar.expander(" Editor unlock"):
    if EDITOR_PIN is None:
        st.info("Editor PIN not set. Add EDITOR_PIN in Secrets to enable editing.")
    else:
        if not st.session_state.can_edit:
            pin_try = st.text_input("Enter Editor PIN", type="password", placeholder="••••")
            if st.button("Unlock editing"):
                if pin_try == str(EDITOR_PIN):
                    st.session_state.can_edit = True
                    st.rerun()
                else:
                    st.error("Incorrect PIN.")
        else:
            if st.button("Lock editing"):
                st.session_state.can_edit = False
                st.rerun()

# =========================
# KPI (only Low Stock Items)
# =========================
st.metric("Low Stock Items", int(df["LowStock"].sum()))

# =========================
# INVENTORY PAGE
# =========================
if page == "Inventory":
    st.subheader("Inventory")

    with st.expander(" Filters", expanded=True):
        show_only_low = st.checkbox("Show only low-stock items", value=False)
        q_inv = st.text_input("Search by Balance Size or Jamliner Length")

    filtered = df.copy()
    if show_only_low:
        filtered = filtered[filtered["LowStock"]]
    if q_inv:
        mask = (
            filtered["Item"].str.contains(q_inv, case=False, na=False)
            | filtered["SKU"].str.contains(q_inv, case=False, na=False)
        )
        filtered = filtered[mask]

    cols = ["Item", "SKU", "OnHand", "MinLevel"]

    if not st.session_state.can_edit:
        # Manager (read-only) view — rename headers for display
        view = filtered.copy()
        view["Status"] = view.apply(lambda r: "⚠️ Low" if r["OnHand"] <= r["MinLevel"] else "✅ OK", axis=1)
        view = view.rename(columns={
            "Item": "Balance Size",
            "SKU": "Jamliner Length",
            "OnHand": "Current Stock",
            "MinLevel": "Reorder Level",
        })
        st.dataframe(
            view[["Balance Size", "Jamliner Length", "Current Stock", "Reorder Level", "Status"]],
            use_container_width=True,
            hide_index=True,
            height=_full_height(len(view)) 
        )
    else:
        # Editor view — editable table with friendly labels
        edited = st.data_editor(
            filtered[cols],
            num_rows="dynamic",
            use_container_width=True,
            key="inv_edit",
            column_config={
                "Item": "Balance Size",
                "SKU": "Jamliner Length",
                "OnHand": st.column_config.NumberColumn("Current Stock", format="%d"),
                "MinLevel": st.column_config.NumberColumn("Reorder Level", format="%d"),
            },
        )
        # Merge edits back into df by SKU (or Item if not unique)
        key = "SKU" if df["SKU"].is_unique else "Item"
        merged = df.set_index(key).copy()
        incoming = edited.set_index(key)[["OnHand", "MinLevel"]]
        merged.update(incoming)
        out_df = merged.reset_index()

        cA, cB = st.columns([1, 1])
        with cA:
            if st.button("💾 Save changes to Google Sheet"):
                try:
                    write_inventory_sheet(out_df)
                    st.success("Inventory saved.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")
        with cB:
            if st.button("🔄 Reload"):
                st.cache_data.clear()
                st.rerun()

# =========================
# ORDERS PAGE
# =========================
if page == "Orders":
    st.subheader("Orders (complete to decrement inventory)")

    # Filters
    with st.expander(" Order filters", expanded=True):
        show_only_open = st.checkbox("Show only NOT completed", value=True)
        q_orders = st.text_input("Search Order Name / Id / Jamliner Length")
        simple_mode = st.toggle("Simple checkbox mode", value=True, help="Tick boxes like a to-do list. Turn off to edit rows in a table.")

    # Start from full orders df
    view_orders = orders_df.copy()
    if show_only_open:
        view_orders = view_orders[~view_orders["Completed"]]
    if q_orders:
        mask = (
            view_orders["OrderName"].astype(str).str.contains(q_orders, case=False, na=False)
            | view_orders["OrderId"].astype(str).str.contains(q_orders, case=False, na=False)
            | view_orders["SKU"].astype(str).str.contains(q_orders, case=False, na=False)
        )
        view_orders = view_orders[mask]

    # Merge mapping to show BalanceSize & UnitsPerOrder next to each line (display only)
    map_lookup_df = map_df.rename(columns={"JamlinerLength": "SKU"})
    view_orders = view_orders.merge(
        map_lookup_df[["SKU","BalanceSize","UnitsPerOrder"]],
        on="SKU", how="left"
    )

    # Sort by CreatedDate if present
    if "CreatedDate" in view_orders.columns:
        view_orders = view_orders.sort_values(by="CreatedDate", ascending=True, na_position="last")

    # ---------- SIMPLE CHECKBOX MODE ----------
    if simple_mode:
        st.caption("Tick ✅ for each line you completed. Then click the button below to update inventory.")
        # Keep only open lines in simple mode for clarity
        open_lines = view_orders[~view_orders["Completed"]].copy()

        if open_lines.empty:
            st.success("No open lines. ")
        else:
            # Build a checkbox list
            # Store checked states in session so re-renders don't lose choices
            if "complete_checks" not in st.session_state:
                st.session_state.complete_checks = {}

            # Render checkboxes
            for _, row in open_lines.iterrows():
                lid = str(row["LineId"])
                label = f"**{row['OrderName']}** ({row['OrderId']}) — Jamliner: **{row['SKU']}** × **{int(row['Qty'])}**"
                # Show mapped balance info if available
                if pd.notna(row.get("BalanceSize")):
                    label += f"  →  Balance: **{row['BalanceSize']}** (Units/Order: {int(row.get('UnitsPerOrder') or 1)})"
                st.session_state.complete_checks[lid] = st.checkbox(
                    label,
                    key=f"chk_{lid}",
                    value=st.session_state.complete_checks.get(lid, False)
                )

            # Apply selected completions
            if st.button("✅ Mark selected complete & update inventory"):
                # Build 'after' dataframe by flipping Completed for checked line IDs
                before_df = orders_df.copy()
                after_df = orders_df.copy()
                now_str = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

                any_checked = False
                for lid, checked in st.session_state.complete_checks.items():
                    if not checked:
                        continue
                    any_checked = True
                    # Set Completed True and stamp CompletedAt (if not already)
                    row_idx = after_df.index[after_df["LineId"].astype(str) == lid]
                    if len(row_idx):
                        idx = row_idx[0]
                        # Only flip if it wasn't completed before
                        if not bool(before_df.at[idx, "Completed"]):
                            after_df.at[idx, "Completed"] = True
                            if not str(after_df.at[idx, "CompletedAt"] or "").strip():
                                after_df.at[idx, "CompletedAt"] = now_str

                if not any_checked:
                    st.info("Select at least one line to complete.")
                else:
                    try:
                        # Update inventory using the mapped decrement logic
                        updated_inventory = apply_completions_update_inventory(before_df, after_df, df, map_df)
                        write_inventory_sheet(updated_inventory)
                        write_orders_sheet(after_df)
                        st.success("Saved. Inventory updated and selected lines marked completed.")
                        # Reset checkboxes for next session
                        st.session_state.complete_checks = {}
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Save failed: {e}")

        st.divider()
        st.caption("Need to edit rows (Qty/Note/etc.)? Turn off **Simple checkbox mode** to use the table editor.")

    # ---------- TABLE EDIT MODE (original) ----------
    else:
        st.caption("Edit rows directly. Toggle Completed in the table and click save.")
        edited_orders = st.data_editor(
            view_orders[["OrderId","OrderName","LineId","SKU","BalanceSize","UnitsPerOrder","Qty","Completed","CompletedAt","CreatedDate","Note"]],
            use_container_width=True,
            num_rows="dynamic",
            key="orders_editor",
            column_config={
                "Completed": st.column_config.CheckboxColumn("Completed"),
                "Qty": st.column_config.NumberColumn("Qty", format="%d"),
                "SKU": "Jamliner Length",
                "BalanceSize": "Balance Size (mapped)",
                "UnitsPerOrder": st.column_config.NumberColumn("Units/Order", format="%d"),
            },
        )

        # Merge edits back into full orders_df by LineId (to preserve rows not in the current view)
        base = orders_df.set_index("LineId").copy()
        incoming = edited_orders.set_index("LineId")[["Completed","CompletedAt","Qty","Note"]]
        base.update(incoming)
        merged_orders = base.reset_index()

        # Timestamp newly completed lines
        now_str = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        before_map = orders_df.set_index("LineId")["Completed"].to_dict()
        for idx, row in merged_orders.iterrows():
            lid = row["LineId"]
            prev = bool(before_map.get(lid, False))
            cur = bool(row["Completed"])
            if (not prev) and cur and (not str(row.get("CompletedAt","")).strip()):
                merged_orders.at[idx, "CompletedAt"] = now_str

        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button(" Save Orders & Update Inventory"):
                try:
                    updated_inventory = apply_completions_update_inventory(orders_df, merged_orders, df, map_df)
                    write_inventory_sheet(updated_inventory)
                    write_orders_sheet(merged_orders)
                    st.success("Saved. Inventory updated and Orders marked completed.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Save failed: {e}")
        with c2:
            if st.button(" Reload Orders"):
                st.cache_data.clear()
                st.rerun()
