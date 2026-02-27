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

# Customer filter
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
selected_customers = st.sidebar.multiselect("Customer", options=customers)

# --- Main Query ---
@st.cache_data(ttl=300)
def get_market_data(start_date, end_date, customers, shipment_type, include_crossdock):
    """
    Get profit by market (same start/end market).

    Logic by shipment type (same as Summary View):
    - FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
    - LTL Direct (single row): Use that row
    - LTL Multi-leg: Use ONLY NO rows (YES row duplicates revenue)
    - Parcel: Use ONLY mainShipment = 'YES' rows
    """

    # Date logic:
    # - Cross-dock leg (pickLocationName = dropLocationName): use dropWindowFrom
    # - Regular leg with actual delivery: use dropDateArrived or dropTimeArrived
    # - Regular leg without actual delivery: use dropWindowFrom
    date_field = """
        CASE
            WHEN pickLocationName = dropLocationName THEN STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
            WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != '' THEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s')
            WHEN dropDateArrived IS NOT NULL AND dropDateArrived != '' THEN STR_TO_DATE(dropDateArrived, '%m/%d/%Y')
            ELSE STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
        END
    """

    base_conditions = [
        "shipmentStatus = 'Complete'",
        "startMarket IS NOT NULL AND startMarket != ''",
        "startMarket = endMarket",  # Same market filter
        f"({date_field}) >= '{start_date}'",
        f"({date_field}) <= '{end_date}'"
    ]

    if customers:
        customers_str = "', '".join(customers)
        base_conditions.append(f"clientName IN ('{customers_str}')")

    crossdock_filter = ""
    if not include_crossdock:
        crossdock_filter = "AND pickLocationName != dropLocationName"

    base_where = " AND ".join(base_conditions)

    if shipment_type == "Full Truckload":
        # FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
        query = f"""
        SELECT
            startMarket as market,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as crossdock_revenue
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Full Truckload'
          {crossdock_filter}
        GROUP BY startMarket
        ORDER BY total_profit ASC
        """

    elif shipment_type == "Less Than Truckload":
        # LTL: Need to handle direct vs multi-leg differently
        query = f"""
        WITH order_row_counts AS (
            SELECT
                orderCode,
                COUNT(*) as total_rows
            FROM otp_reports
            WHERE {base_where}
              AND shipmentType = 'Less Than Truckload'
            GROUP BY orderCode
        ),
        filtered_rows AS (
            SELECT o.*
            FROM otp_reports o
            JOIN order_row_counts orc ON o.orderCode = orc.orderCode
            WHERE {base_where}
              AND o.shipmentType = 'Less Than Truckload'
              AND (
                (orc.total_rows > 1 AND o.mainShipment = 'NO')
                OR orc.total_rows = 1
              )
              {crossdock_filter}
        )
        SELECT
            startMarket as market,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as crossdock_revenue
        FROM filtered_rows
        GROUP BY startMarket
        ORDER BY total_profit ASC
        """

    elif shipment_type == "Parcel":
        # Parcel: Use mainShipment = 'YES' rows only
        query = f"""
        SELECT
            startMarket as market,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as crossdock_revenue
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Parcel'
          AND mainShipment = 'YES'
          {crossdock_filter}
        GROUP BY startMarket
        ORDER BY total_profit ASC
        """

    else:
        # All shipment types - combine FTL logic + LTL logic + Parcel logic
        query = f"""
        WITH order_row_counts AS (
            SELECT
                orderCode,
                shipmentType,
                COUNT(*) as total_rows
            FROM otp_reports
            WHERE {base_where}
            GROUP BY orderCode, shipmentType
        ),
        filtered_rows AS (
            SELECT o.*
            FROM otp_reports o
            JOIN order_row_counts orc ON o.orderCode = orc.orderCode
            WHERE {base_where}
              AND (
                o.shipmentType = 'Full Truckload'
                OR (o.shipmentType = 'Less Than Truckload' AND orc.total_rows > 1 AND o.mainShipment = 'NO')
                OR (o.shipmentType = 'Less Than Truckload' AND orc.total_rows = 1)
                OR (o.shipmentType NOT IN ('Full Truckload', 'Less Than Truckload') AND o.mainShipment = 'YES')
              )
              {crossdock_filter}
        )
        SELECT
            startMarket as market,
            COUNT(DISTINCT orderCode) as order_count,
            SUM(COALESCE(revenueAllocationNumber, 0)) as total_revenue,
            SUM(COALESCE(costAllocationNumber, 0)) as total_cost,
            SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as total_profit,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
            SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as crossdock_revenue
        FROM filtered_rows
        GROUP BY startMarket
        ORDER BY total_profit ASC
        """

    return execute_query(query)

# Execute query
df = get_market_data(
    start_date.strftime('%Y-%m-%d'),
    end_date.strftime('%Y-%m-%d'),
    selected_customers,
    shipment_type,
    include_crossdock
)

if df is not None and len(df) > 0:
    # Summary metrics
    total_revenue = df['total_revenue'].sum()
    total_cost = df['total_cost'].sum()
    total_profit = df['total_profit'].sum()
    total_orders = df['order_count'].sum()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Revenue", f"${total_revenue:,.0f}")
    col2.metric("Total Cost", f"${total_cost:,.0f}")
    col3.metric("Total Profit", f"${total_profit:,.0f}")
    col4.metric("Total Orders", f"{total_orders:,.0f}")
    
    st.markdown("---")
    st.subheader("Profit by Market")
    st.caption("Sorted by least profitable at top")
    
    # Format display
    display_df = df.copy()
    display_df['margin_pct'] = (display_df['total_profit'] / display_df['total_revenue'] * 100).fillna(0)
    display_df.columns = ['Market', 'Orders', 'Revenue', 'Cost', 'Profit', 'Cross-dock Cost', 'Cross-dock Revenue', 'Margin %']
    
    st.dataframe(
        display_df.style.format({
            'Revenue': '${:,.0f}',
            'Cost': '${:,.0f}',
            'Profit': '${:,.0f}',
            'Cross-dock Cost': '${:,.0f}',
            'Cross-dock Revenue': '${:,.0f}',
            'Margin %': '{:.1f}%'
        }),
        width='stretch',
        hide_index=True
    )
else:
    st.warning("No data found for the selected filters.")

