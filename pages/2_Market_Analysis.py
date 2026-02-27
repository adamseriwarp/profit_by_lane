import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_connection import execute_query
from auth import check_password

if not check_password():
    st.stop()

st.title("🏙️ Within-Market Analysis")
st.markdown("*Orders where start market = end market (e.g., LAX → LAX)*")

# --- Sidebar Filters ---
st.sidebar.header("Filters")

# Shipment Type filter
shipment_type = st.sidebar.selectbox(
    "Shipment Type",
    options=["All", "Full Truckload", "Less Than Truckload", "Parcel"],
    index=0
)

# Cross-dock filter
include_crossdock = st.sidebar.checkbox("Include Cross-dock Legs", value=True,
    help="Cross-dock legs are where pickup location = drop location")

# Date range filter
col1, col2 = st.sidebar.columns(2)
default_start = datetime.now() - timedelta(days=30)
default_end = datetime.now()
start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Customer exclusion filter
@st.cache_data(ttl=3600)
def get_customers():
    query = """
        SELECT DISTINCT clientName
        FROM otp_reports
        WHERE clientName IS NOT NULL AND clientName != ''
        ORDER BY clientName
        LIMIT 500
    """
    df = execute_query(query)
    return df['clientName'].tolist() if df is not None else []

customers = get_customers()
excluded_customers = st.sidebar.multiselect("Exclude Customers", options=customers,
    help="Select customers to EXCLUDE from the analysis")

# --- Smart Strategy V3 Date Field ---
DATE_FIELD = """CASE
    WHEN pickLocationName = dropLocationName THEN STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
    WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != '' THEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s')
    WHEN dropDateArrived IS NOT NULL AND dropDateArrived != '' THEN STR_TO_DATE(dropDateArrived, '%m/%d/%Y')
    ELSE STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
END"""


# --- Main Query using Smart Strategy V3 ---
@st.cache_data(ttl=300)
def get_market_data(start_date, end_date, excluded_customers, shipment_type, include_crossdock):
    """
    Get profit by market (same start/end market) using Smart Strategy V3.

    Smart Strategy V3 reconciles YES and NO rows to avoid double-counting:
    - Revenue: Use YES if present, else sum NO rows
    - Cost: Use YES + cross-dock NO costs to capture all legs
    """

    # Include canceled orders with crossdock legs
    base_conditions = [
        "(shipmentStatus = 'Complete' OR (shipmentStatus = 'canceled' AND pickLocationName = dropLocationName))",
        f"({DATE_FIELD}) >= '{start_date}'",
        f"({DATE_FIELD}) <= '{end_date}'"
    ]

    if shipment_type != "All":
        base_conditions.append(f"shipmentType = '{shipment_type}'")

    if excluded_customers:
        customers_str = "', '".join(excluded_customers)
        base_conditions.append(f"clientName NOT IN ('{customers_str}')")

    base_where = " AND ".join(base_conditions)

    # Smart Strategy V3 CTE with canceled order handling
    query = f"""
    WITH order_metrics AS (
        SELECT
            orderCode,
            MAX(CASE WHEN mainShipment = 'YES' THEN shipmentStatus END) as order_status,
            -- Revenue metrics (Complete orders only)
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
            -- Cost metrics (Complete orders only)
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as yes_cost,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as no_cost,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as xd_no_cost,
            -- Canceled order crossdock values
            SUM(CASE WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as canceled_xd_rev,
            SUM(CASE WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as canceled_xd_cost,
            -- TONU metrics (Truck Order Not Used)
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost,
            -- Duplicate detection
            MAX(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' AND ABS(
                COALESCE(costAllocationNumber, 0) - (
                    SELECT SUM(COALESCE(costAllocationNumber, 0))
                    FROM otp_reports o2
                    WHERE o2.orderCode = otp_reports.orderCode
                      AND o2.mainShipment = 'YES'
                      AND o2.shipmentStatus = 'Complete'
                )
            ) < 1 THEN 1 ELSE 0 END) as has_matching_no_row,
            COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN startMarket END), 'NA') as startMarket,
            COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN endMarket END), 'NA') as endMarket
        FROM otp_reports
        WHERE {base_where}
        GROUP BY orderCode
    ),
    order_calculated AS (
        SELECT
            orderCode, startMarket, endMarket, order_status,
            -- Revenue: Smart Strategy for Complete, XD only for canceled
            CASE
                WHEN order_status = 'canceled' THEN canceled_xd_rev
                WHEN yes_rev > 0 AND no_rev = 0 THEN yes_rev
                WHEN yes_rev = 0 THEN no_rev
                WHEN ABS((no_rev - xd_leg_rev) - yes_rev) < 1 THEN no_rev
                WHEN yes_rev > 2 * no_rev THEN yes_rev + no_rev
                ELSE no_rev
            END as smart_revenue,
            -- Cost: Smart Strategy for Complete, XD only for canceled
            CASE
                WHEN order_status = 'canceled' THEN canceled_xd_cost
                WHEN yes_cost > 0 AND no_cost = 0 THEN yes_cost
                WHEN yes_cost = 0 AND no_cost > 0 THEN no_cost
                WHEN ABS((no_cost - xd_no_cost) - yes_cost) < 20 THEN
                    CASE WHEN has_matching_no_row = 1 THEN yes_cost ELSE yes_cost + no_cost END
                WHEN no_cost > yes_cost * 5 THEN yes_cost + no_cost
                ELSE yes_cost + xd_no_cost
            END as smart_cost,
            CASE WHEN order_status = 'canceled' THEN canceled_xd_cost ELSE xd_no_cost END as crossdock_cost,
            tonu_revenue,
            tonu_cost
        FROM order_metrics
        WHERE startMarket = endMarket  -- Same market filter
          AND startMarket IS NOT NULL AND startMarket != '' AND startMarket != 'NA'
    )
    SELECT
        startMarket as market,
        COUNT(DISTINCT CASE WHEN order_status = 'Complete' THEN orderCode END) as completed_orders,
        COUNT(DISTINCT CASE WHEN order_status = 'canceled' THEN orderCode END) as canceled_orders,
        SUM(smart_revenue) as total_revenue,
        SUM(smart_cost) as total_cost,
        SUM(smart_revenue) - SUM(smart_cost) as total_profit,
        SUM(crossdock_cost) as crossdock_cost,
        SUM(tonu_revenue) as tonu_revenue,
        SUM(tonu_cost) as tonu_cost
    FROM order_calculated
    GROUP BY startMarket
    ORDER BY total_profit ASC
    """

    return execute_query(query)

# Execute query
df = get_market_data(
    start_date.strftime('%Y-%m-%d'),
    end_date.strftime('%Y-%m-%d'),
    excluded_customers,
    shipment_type,
    include_crossdock
)

if df is not None and len(df) > 0:
    # Summary metrics
    total_revenue = df['total_revenue'].sum()
    total_cost = df['total_cost'].sum()
    total_profit = df['total_profit'].sum()
    completed_orders = df['completed_orders'].sum()
    canceled_orders = df['canceled_orders'].sum()
    tonu_revenue = df['tonu_revenue'].sum() if 'tonu_revenue' in df.columns else 0
    tonu_cost = df['tonu_cost'].sum() if 'tonu_cost' in df.columns else 0

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Completed Orders", f"{completed_orders:,.0f}")
    col2.metric("Canceled Orders", f"{canceled_orders:,.0f}", help="Canceled orders with cross-dock costs")
    col3.metric("Total Revenue", f"${total_revenue:,.0f}")
    col4.metric("Total Cost", f"${total_cost:,.0f}")
    col5.metric("Total Profit", f"${total_profit:,.0f}")

    # TONU summary (show only if there are TONU charges)
    if tonu_revenue > 0 or tonu_cost > 0:
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.metric("TONU Revenue", f"${tonu_revenue:,.0f}", help="Revenue from TONU (Truck Order Not Used)")
        col_t2.metric("TONU Cost", f"${tonu_cost:,.0f}", help="Cost from TONU charges")
        tonu_pct = (tonu_cost / total_cost * 100) if total_cost > 0 else 0
        col_t3.metric("TONU Cost %", f"{tonu_pct:.1f}%", help="TONU cost as % of total cost")

    st.markdown("---")
    st.subheader("Profit by Market")
    st.caption("Sorted by least profitable at top")

    # Format display - exclude TONU columns from main display (already shown in summary)
    display_df = df[['market', 'completed_orders', 'canceled_orders', 'total_revenue', 'total_cost', 'total_profit', 'crossdock_cost']].copy()
    display_df['margin_pct'] = (display_df['total_profit'] / display_df['total_revenue'] * 100).fillna(0)
    display_df.columns = ['Market', 'Completed', 'Canceled', 'Revenue', 'Cost', 'Profit', 'XD Cost', 'Margin %']

    st.dataframe(
        display_df.style.format({
            'Revenue': '${:,.0f}',
            'Cost': '${:,.0f}',
            'Profit': '${:,.0f}',
            'XD Cost': '${:,.0f}',
            'Margin %': '{:.1f}%'
        }),
        width='stretch',
        hide_index=True
    )
else:
    st.warning("No data found for the selected filters.")

