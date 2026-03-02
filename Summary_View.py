import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from db_connection import execute_query
from auth import check_password

st.set_page_config(
    page_title="Profit by Lane Dashboard",
    page_icon="📊",
    layout="wide"
)

if not check_password():
    st.stop()

st.title("📊 Profit by Lane - Summary View")

# --- Sidebar Filters ---
st.sidebar.header("Filters")

# Shipment Type filter (important for logic)
shipment_type = st.sidebar.selectbox(
    "Shipment Type",
    options=["All", "Full Truckload", "Less Than Truckload", "Parcel"],
    index=0
)

# Date range filter (based on delivery date)
st.sidebar.caption("*Dates based on actual/scheduled delivery*")
col1, col2 = st.sidebar.columns(2)
default_start = datetime.now() - timedelta(days=30)
default_end = datetime.now()

start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Get filter options from database
@st.cache_data(ttl=3600)
def get_filter_options():
    """Get unique values for filter dropdowns"""
    customers_query = """
        SELECT DISTINCT clientName
        FROM otp_reports
        WHERE clientName IS NOT NULL AND clientName != ''
        ORDER BY clientName
        LIMIT 500
    """
    lanes_query = """
        SELECT DISTINCT CONCAT(startMarket, ' → ', endMarket) as lane
        FROM otp_reports
        WHERE startMarket IS NOT NULL AND startMarket != ''
          AND endMarket IS NOT NULL AND endMarket != ''
        ORDER BY lane
        LIMIT 2500
    """

    customers_df = execute_query(customers_query)
    lanes_df = execute_query(lanes_query)

    customers = customers_df['clientName'].tolist() if customers_df is not None else []
    lanes = lanes_df['lane'].tolist() if lanes_df is not None else []

    return customers, lanes

customers, lanes = get_filter_options()

# Filter selections
selected_customers = st.sidebar.multiselect("Customer", options=customers, default=[])
selected_lanes = st.sidebar.multiselect("Lane", options=lanes, default=[])


# --- Main Query ---
@st.cache_data(ttl=300)
def get_profit_by_lane_data(start_date, end_date, customers, lanes, shipment_type):
    """
    Get profit by lane data using validated simple approach.

    VALIDATED APPROACH (March 2026):
    - Revenue: Hybrid approach
        * Use sum(revenueAllocationNumber) from legs when available
        * Fall back to main row's total when no legs or no allocation
        * Validation: 0.76% difference vs main-only (PASS)
    - Cost: Sum ALL rows (YES + NO)
        * costAllocationNumber is ADDITIVE, not duplicated
        * Validation: 1.6% difference vs orders table (PASS)
    - Lane: startMarket → endMarket (1,882 unique lanes vs 39,118 city lanes)

    Lane is defined by the mainShipment='YES' row's startMarket → endMarket.
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

    # Build base WHERE conditions
    # Include: Complete orders + Canceled orders + TONU (regardless of status)
    base_conditions = [
        "(shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')",
        "startMarket IS NOT NULL AND startMarket != ''",
        "endMarket IS NOT NULL AND endMarket != ''",
        f"({date_field}) >= '{start_date}'",
        f"({date_field}) <= '{end_date}'"
    ]

    if customers:
        customers_str = "', '".join(customers)
        base_conditions.append(f"clientName IN ('{customers_str}')")

    if lanes:
        lanes_str = "', '".join(lanes)
        base_conditions.append(f"CONCAT(startMarket, ' → ', endMarket) IN ('{lanes_str}')")

    base_where = " AND ".join(base_conditions)

    if shipment_type == "Full Truckload":
        # FTL: Row-level allocation - each row's revenue/cost to that row's lane
        # Includes TONU regardless of status, canceled orders only count crossdock
        query = f"""
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
                ELSE 0
            END) as total_revenue,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as total_cost,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Full Truckload'
        GROUP BY startMarket, endMarket
        HAVING COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) > 0
            OR COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) > 0
            OR SUM(CASE WHEN accessorialType = 'TONU' THEN 1 ELSE 0 END) > 0
        ORDER BY total_revenue - total_cost DESC
        """

    elif shipment_type == "Less Than Truckload":
        # LTL: Row-level allocation - each row's revenue/cost to that row's lane
        # Includes TONU regardless of status, canceled orders only count crossdock
        query = f"""
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
                ELSE 0
            END) as total_revenue,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as total_cost,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Less Than Truckload'
        GROUP BY startMarket, endMarket
        HAVING COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) > 0
            OR COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) > 0
            OR SUM(CASE WHEN accessorialType = 'TONU' THEN 1 ELSE 0 END) > 0
        ORDER BY total_revenue - total_cost DESC
        """

    elif shipment_type == "Parcel":
        # Parcel: Row-level allocation - each row's revenue/cost to that row's lane
        # Includes TONU regardless of status, canceled orders only count crossdock
        query = f"""
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
                ELSE 0
            END) as total_revenue,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as total_cost,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Parcel'
        GROUP BY startMarket, endMarket
        HAVING COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) > 0
            OR COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) > 0
            OR SUM(CASE WHEN accessorialType = 'TONU' THEN 1 ELSE 0 END) > 0
        ORDER BY total_revenue - total_cost DESC
        """

    else:
        # All shipment types - Row-level allocation for all types
        # Each row's revenue/cost goes to that row's lane
        query = f"""
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
                ELSE 0
            END) as total_revenue,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as total_cost,
            SUM(CASE
                WHEN pickLocationName = dropLocationName
                THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
        GROUP BY startMarket, endMarket
        HAVING COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) > 0
            OR COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) > 0
            OR SUM(CASE WHEN accessorialType = 'TONU' THEN 1 ELSE 0 END) > 0
        ORDER BY total_revenue - total_cost DESC
        """

    return execute_query(query)


# Load data
with st.spinner("Loading data..."):
    df = get_profit_by_lane_data(
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
        selected_customers,
        selected_lanes,
        shipment_type
    )

if df is not None and len(df) > 0:
    # Calculate profit and percentages
    df['total_profit'] = df['total_revenue'] - df['total_cost']
    df['crossdock_cost_pct'] = (df['crossdock_cost'] / df['total_cost'] * 100).fillna(0).round(1)
    df['margin_pct'] = (df['total_profit'] / df['total_revenue'] * 100).fillna(0).round(1)

    # Handle TONU columns (may not exist for all shipment types)
    if 'tonu_revenue' not in df.columns:
        df['tonu_revenue'] = 0
    if 'tonu_cost' not in df.columns:
        df['tonu_cost'] = 0

    # Total order count (completed + canceled)
    df['total_orders'] = df['completed_orders'] + df['canceled_orders']

    # Summary metrics - Row 1
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Revenue", f"${df['total_revenue'].sum():,.0f}")
    col2.metric("Total Cost", f"${df['total_cost'].sum():,.0f}")
    col3.metric("Total Profit", f"${df['total_profit'].sum():,.0f}")
    total_crossdock_pct = (df['crossdock_cost'].sum() / df['total_cost'].sum() * 100) if df['total_cost'].sum() > 0 else 0
    col4.metric("Cross-dock Cost %", f"{total_crossdock_pct:.1f}%")
    col5.metric("Orders", f"{int(df['completed_orders'].sum()):,} + {int(df['canceled_orders'].sum()):,} canceled")

    # Summary metrics - Row 2 (TONU)
    total_tonu_rev = df['tonu_revenue'].sum()
    total_tonu_cost = df['tonu_cost'].sum()
    if total_tonu_rev > 0 or total_tonu_cost > 0:
        col_t1, col_t2, col_t3, col_t4 = st.columns(4)
        col_t1.metric("TONU Revenue", f"${total_tonu_rev:,.0f}", help="Revenue from TONU (Truck Order Not Used) charges")
        col_t2.metric("TONU Cost", f"${total_tonu_cost:,.0f}", help="Cost from TONU charges")
        col_t3.metric("TONU Profit", f"${total_tonu_rev - total_tonu_cost:,.0f}")
        tonu_pct = (total_tonu_cost / df['total_cost'].sum() * 100) if df['total_cost'].sum() > 0 else 0
        col_t4.metric("TONU Cost %", f"{tonu_pct:.1f}%", help="TONU cost as % of total cost")

    st.markdown("---")

    # Display pivot table
    st.subheader("Profit by Lane")

    display_df = df[['lane', 'completed_orders', 'canceled_orders', 'total_revenue', 'total_cost', 'total_profit',
                     'crossdock_cost', 'crossdock_cost_pct', 'margin_pct']].copy()
    display_df.columns = ['Lane', 'Completed', 'Canceled', 'Revenue', 'Cost', 'Profit',
                          'Cross-dock Cost', 'XD Cost %', 'Margin %']

    # Format currency columns
    st.dataframe(
        display_df.style.format({
            'Completed': '{:,.0f}',
            'Canceled': '{:,.0f}',
            'Revenue': '${:,.0f}',
            'Cost': '${:,.0f}',
            'Profit': '${:,.0f}',
            'Cross-dock Cost': '${:,.0f}',
            'XD Cost %': '{:.1f}%',
            'Margin %': '{:.1f}%'
        }),
        width='stretch',
        height=600
    )

    # Store selected filters in session state for drill-down page
    st.session_state['filters'] = {
        'start_date': start_date,
        'end_date': end_date,
        'customers': selected_customers,
        'lanes': selected_lanes,
        'shipment_type': shipment_type
    }

    st.info("👉 Go to the **Drill Down** page in the sidebar to see individual order details.")

else:
    st.warning("No data found for the selected filters. Try adjusting your date range or filters.")