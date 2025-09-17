import streamlit as st
import pandas as pd
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
from datetime import datetime, timezone
from dateutil import tz

# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(page_title="Inventory (Sheets)", layout="wide", page_icon="📦")
st.title("Inventory Dashboard")

# ----------------------------
# Secrets / Settings
# ----------------------------
# Set these in Streamlit Cloud → App → Settings → Secrets
# SHEET_ID: Google Sheet ID (the long string in the URL)
# WORKSHEET_NAME: Tab name that holds the inventory table (default "Sheet1")
# META_SHEET_NAME: Tab name used for last-updated timestamp (default "Meta")
# EDITOR_PIN: A short PIN to enable editing (e.g., "1234")

SHEET_ID = st.secrets["SHEET_ID"]
WORKSHEET_NAME = st.secrets.get("WORKSHEET_NAME", "Sheet1")
META_SHEET_NAME = st.secrets.get("META_SHEET_NAME", "Meta")
EDITOR_PIN = st.secrets.get("EDITOR_PIN", None)

LOCAL_TZ = tz.gettz("America/Chicago")  # change if you prefer another timezone

# ----------------------------
# Google Sheets helpers
# ----------------------------
@st.cache_data(ttl=30)
def read_sheet() -> pd.DataFrame:
    gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
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

@st.cache_data(ttl=20)
def read_last_updated_from_meta() -> str | None:
    """Reads Meta!B1 as the 'last updated' timestamp. Returns None if missing."""
    try:
        gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
        sh = gc.open_by_key(SHEET_ID)
        meta = sh.worksheet(META_SHEET_NAME)
        val = meta.acell("B1").value  # convention: B1 stores last-updated text
        return val.strip() if val else None
    except Exception:
        return None

def write_sheet(df: pd.DataFrame):
    gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)

    # Ensure header order
    out = df[["Item", "SKU", "OnHand", "MinLevel"]].copy()
    ws.clear()
    set_with_dataframe(ws, out, include_index=False, include_column_header=True)

    # Update meta timestamp (Meta!B1)
    try:
        stamp = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
        try:
            meta = sh.worksheet(META_SHEET_NAME)
        except gspread.WorksheetNotFound:
            meta = sh.add_worksheet(title=META_SHEET_NAME, rows=10, cols=3)
            meta.update("A1:B1", [["Key", "Value"]])
        meta.update("A2:B2", [["last_updated", stamp]])
    except Exception:
        # If meta update fails, don't crash the save
        pass

# ----------------------------
# Load data
# ----------------------------
df = read_sheet()
last_updated = read_last_updated_from_meta()  # None if not set
last_fetched = datetime.now(timezone.utc).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")

# ----------------------------
# Top bar: timestamps & mode
# ----------------------------
ts_col1, ts_col2, ts_col3 = st.columns([1.4, 1, 1])
with ts_col1:
    st.caption(
        f"**Last updated (from sheet)**: {last_updated if last_updated else '—'}"
    )
with ts_col2:
    st.caption(f"**Last fetched**: {last_fetched}")
with ts_col3:
    st.caption("Auto-refresh every ~30s")

# ----------------------------
# Sidebar: View / Edit controls
# ----------------------------
st.sidebar.header("View")
# Default Manager (read-only) mode
if "can_edit" not in st.session_state:
    st.session_state.can_edit = False

manager_view = not st.session_state.can_edit  # show as read-only unless unlocked

if not st.session_state.can_edit:
    st.sidebar.success("Manager mode: Read-only (default)")
else:
    st.sidebar.warning("Editor mode: Changes allowed")

# PIN unlock
with st.sidebar.expander("🔒 Editor unlock"):
    if EDITOR_PIN is None:
        st.info("Editor PIN is not set. Ask the app owner to add EDITOR_PIN in secrets.")
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

# Filters
with st.expander("Filters", expanded=True):
    show_only_low = st.checkbox("Show only low-stock items", value=False)
    q = st.text_input("Search by Item or SKU")

filtered = df.copy()
if show_only_low:
    filtered = filtered[filtered["LowStock"]]
if q:
    mask = (
        filtered["Item"].str.contains(q, case=False, na=False)
        | filtered["SKU"].str.contains(q, case=False, na=False)
    )
    filtered = filtered[mask]

# KPIs
left, mid, right = st.columns([1, 1, 1])
with mid:
    st.metric("Low Stock Items", int(df["LowStock"].sum()))

st.subheader("Inventory")

# ----------------------------
# Main table
# ----------------------------
cols = ["Item", "SKU", "OnHand", "MinLevel"]
if manager_view:
    view = filtered.copy()
    view["Status"] = view.apply(
        lambda r: "⚠️ Low" if r["OnHand"] <= r["MinLevel"] else "✅ OK",
        axis=1
    )
    # Rename columns just for display
    view = view.rename(columns={
        "Item": "Balance Size",
        "SKU": "Jamliner Length",
        "OnHand": "Current Stock",
        "MinLevel": "Reorder Level"
    })
    st.dataframe(
        view[["Balance Size", "Jamliner Length", "Current Stock", "Reorder Level", "Status"]],
        use_container_width=True,
        hide_index=True
    )

else:
    edited = st.data_editor(
        filtered[["Item","SKU","OnHand","MinLevel"]],
        num_rows="dynamic",
        use_container_width=True,
        key="inv_edit",
        column_config={
            "Item": "Product Name",
            "SKU": "Code",
            "OnHand": st.column_config.NumberColumn("Current Stock", format="%d"),
            "MinLevel": st.column_config.NumberColumn("Reorder Level", format="%d"),
        },
    )
    # Merge edits by SKU if unique, else by Item
    key = "SKU" if df["SKU"].is_unique else "Item"
    merged = df.set_index(key).copy()
    incoming = edited.set_index(key)[["OnHand", "MinLevel"]]
    merged.update(incoming)
    out_df = merged.reset_index()

    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button(" Save changes to Google Sheet"):
            try:
                write_sheet(out_df)
                st.success("Saved!")
                st.cache_data.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Save failed: {e}")
    with c2:
        if st.button(" Reload latest from Google Sheet"):
            st.cache_data.clear()
            st.rerun()

st.caption("Tip: Edit counts from your phone directly in Google Sheets. The app refreshes automatically.")

# git hub 

# git add app.py
# git commit -m "Show only Low Stock Items KPI"
# git push origin main

