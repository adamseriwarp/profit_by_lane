import streamlit as st
import pandas as pd
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_connection import execute_query

# Check if user is authenticated (handled by Summary_View.py)
if not st.session_state.get("password_correct", False):
    st.warning("Please log in from the main page")
    st.stop()

st.title("🔍 Drill Down")
st.markdown("View individual order rows that contribute to profit/revenue/cost")

# --- Sidebar Filters ---
st.sidebar.header("Drill Down Filters")

# Get filter options
@st.cache_data(ttl=3600)
def get_customers():
    """Get unique customer names for filter dropdown"""
    customers_query = """
        SELECT DISTINCT clientName
        FROM otp_reports
        WHERE clientName IS NOT NULL AND clientName != ''
        ORDER BY clientName
        LIMIT 500
    """
    customers_df = execute_query(customers_query)
    return customers_df['clientName'].tolist() if customers_df is not None else []

@st.cache_data(ttl=3600)
def get_lanes_for_customer(customer_name=None):
    """Get unique lanes, optionally filtered by customer"""
    if customer_name:
        lanes_query = f"""
            SELECT DISTINCT CONCAT(startMarket, ' → ', endMarket) as lane
            FROM otp_reports
            WHERE startMarket IS NOT NULL AND startMarket != ''
              AND endMarket IS NOT NULL AND endMarket != ''
              AND clientName = '{customer_name}'
            ORDER BY lane
            LIMIT 2500
        """
    else:
        lanes_query = """
            SELECT DISTINCT CONCAT(startMarket, ' → ', endMarket) as lane
            FROM otp_reports
            WHERE startMarket IS NOT NULL AND startMarket != ''
              AND endMarket IS NOT NULL AND endMarket != ''
            ORDER BY lane
            LIMIT 2500
        """
    lanes_df = execute_query(lanes_query)
    return lanes_df['lane'].tolist() if lanes_df is not None else []

customers = get_customers()
lanes = get_lanes_for_customer()  # All lanes for initial load

# Use filters from main page if available
default_filters = st.session_state.get('filters', {})

# Date filters
from datetime import datetime, timedelta

# Shipment Type filter (matching main page)
default_shipment_type = default_filters.get('shipment_type', 'All')
shipment_type_options = ["All", "Full Truckload", "Less Than Truckload", "Parcel"]
default_idx = shipment_type_options.index(default_shipment_type) if default_shipment_type in shipment_type_options else 0
shipment_type = st.sidebar.selectbox(
    "Shipment Type",
    options=shipment_type_options,
    index=default_idx
)

col1, col2 = st.sidebar.columns(2)
default_start = default_filters.get('start_date', datetime.now() - timedelta(days=30))
default_end = default_filters.get('end_date', datetime.now())

start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)

# Drill-down selection - choose ONE customer OR lane
drill_type = st.sidebar.radio("Drill down by:", ["Customer", "Lane"])

if drill_type == "Customer":
    selected_value = st.sidebar.selectbox("Select Customer", options=customers)
    # Get lanes specific to this customer
    customer_lanes = get_lanes_for_customer(selected_value)
else:
    selected_value = st.sidebar.selectbox("Select Lane", options=lanes)
    customer_lanes = []

# Optional additional filters
st.sidebar.markdown("---")
st.sidebar.subheader("Additional Filters")
if drill_type == "Customer":
    selected_lane = st.sidebar.selectbox("Filter by Lane (optional)", options=["All"] + customer_lanes)
else:
    selected_lane = "All"

# --- Query for detailed rows ---
@st.cache_data(ttl=300)
def get_order_details(start_date, end_date, drill_type, selected_value, selected_lane, shipment_type):
    """
    Get individual order rows for drill-down analysis.

    NOTE: This returns RAW rows for display/investigation. The summary metrics
    at the top of the page use get_order_summary_metrics() which applies the
    Hybrid Approach for revenue and sum-all for cost.
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
    base_conditions = [
        "shipmentStatus != 'removed'",
        f"({date_field}) >= '{start_date}'",
        f"({date_field}) <= '{end_date}'"
    ]

    if drill_type == "Customer":
        base_conditions.append(f"clientName = '{selected_value}'")
    else:  # Lane
        base_conditions.append(f"CONCAT(startMarket, ' → ', endMarket) = '{selected_value}'")

    if selected_lane != "All":
        base_conditions.append(f"CONCAT(startMarket, ' → ', endMarket) = '{selected_lane}'")

    base_where = " AND ".join(base_conditions)

    # Column selection for output (use 'o.' prefix for JOINs)
    select_cols_simple = """
        orderCode as `Order ID`,
        warpId as `Warp ID`,
        mainShipment as `Main Shipment`,
        CONCAT(startMarket, ' → ', endMarket) as `Lane`,
        clientName as `Customer`,
        carrierName as `Carrier`,
        pickLocationName as `Pickup Location`,
        dropLocationName as `Drop Location`,
        COALESCE(revenueAllocationNumber, 0) as `Revenue`,
        COALESCE(costAllocationNumber, 0) as `Cost`,
        COALESCE(revenueAllocationNumber, 0) - COALESCE(costAllocationNumber, 0) as `Profit`,
        CASE WHEN pickLocationName = dropLocationName THEN 'Yes' ELSE 'No' END as `Cross-dock`,
        shipmentType as `Shipment Type`,
        pickWindowFrom as `Pickup Window`
    """

    select_cols_aliased = """
        o.orderCode as `Order ID`,
        o.warpId as `Warp ID`,
        o.mainShipment as `Main Shipment`,
        CONCAT(o.startMarket, ' → ', o.endMarket) as `Lane`,
        o.clientName as `Customer`,
        o.carrierName as `Carrier`,
        o.pickLocationName as `Pickup Location`,
        o.dropLocationName as `Drop Location`,
        COALESCE(o.revenueAllocationNumber, 0) as `Revenue`,
        COALESCE(o.costAllocationNumber, 0) as `Cost`,
        COALESCE(o.revenueAllocationNumber, 0) - COALESCE(o.costAllocationNumber, 0) as `Profit`,
        CASE WHEN o.pickLocationName = o.dropLocationName THEN 'Yes' ELSE 'No' END as `Cross-dock`,
        o.shipmentType as `Shipment Type`,
        o.pickWindowFrom as `Pickup Window`
    """

    if shipment_type == "Full Truckload":
        # FTL: Use ALL rows (YES + NO) - no JOIN needed
        query = f"""
        SELECT {select_cols_simple}
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Full Truckload'
        ORDER BY orderCode, mainShipment DESC, warpId
        LIMIT 5000
        """

    elif shipment_type == "Less Than Truckload":
        # LTL: Show all rows (YES + NO) for drill-down investigation
        query = f"""
        SELECT {select_cols_simple}
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Less Than Truckload'
        ORDER BY orderCode, mainShipment DESC, warpId
        LIMIT 5000
        """

    elif shipment_type == "Parcel":
        # Parcel: Use mainShipment = 'YES' rows only - no JOIN needed
        query = f"""
        SELECT {select_cols_simple}
        FROM otp_reports
        WHERE {base_where}
          AND shipmentType = 'Parcel'
          AND mainShipment = 'YES'
        ORDER BY orderCode, mainShipment DESC, warpId
        LIMIT 5000
        """

    else:
        # All: Show all rows for FTL and LTL, YES only for Parcel
        query = f"""
        SELECT {select_cols_simple}
        FROM otp_reports
        WHERE {base_where}
          AND (
            -- FTL and LTL: show all rows
            shipmentType IN ('Full Truckload', 'Less Than Truckload')
            -- Parcel: use YES rows only
            OR (shipmentType = 'Parcel' AND mainShipment = 'YES')
          )
        ORDER BY orderCode, mainShipment DESC, warpId
        LIMIT 5000
        """

    return execute_query(query)


@st.cache_data(ttl=300)
def get_order_summary_metrics(start_date, end_date, drill_type, selected_value, selected_lane, shipment_type):
    """
    Calculate summary metrics using row-level lane allocation.

    Each row's revenue/cost is allocated to THAT row's lane.
    Includes TONU regardless of shipmentStatus.
    """
    # Build base WHERE conditions
    date_field = """
        CASE
            WHEN pickLocationName = dropLocationName THEN STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
            WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != '' THEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s')
            WHEN dropDateArrived IS NOT NULL AND dropDateArrived != '' THEN STR_TO_DATE(dropDateArrived, '%m/%d/%Y')
            ELSE STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
        END
    """

    # Include: Complete orders + Canceled orders + TONU (regardless of status)
    base_conditions = [
        "(shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')",
        f"({date_field}) >= '{start_date}'",
        f"({date_field}) <= '{end_date}'"
    ]

    if drill_type == "Customer":
        base_conditions.append(f"clientName = '{selected_value}'")
        if selected_lane and selected_lane != "All":
            parts = selected_lane.split(' → ')
            if len(parts) == 2:
                base_conditions.append(f"startMarket = '{parts[0]}'")
                base_conditions.append(f"endMarket = '{parts[1]}'")
    else:
        parts = selected_value.split(' → ')
        if len(parts) == 2:
            base_conditions.append(f"startMarket = '{parts[0]}'")
            base_conditions.append(f"endMarket = '{parts[1]}'")

    if shipment_type and shipment_type != "All":
        base_conditions.append(f"shipmentType = '{shipment_type}'")

    base_where = " AND ".join(base_conditions)

    # Row-level allocation: each row's revenue/cost goes to that row's lane
    query = f"""
    SELECT
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
    """

    return execute_query(query)


if selected_value:
    with st.spinner("Loading order details..."):
        df = get_order_details(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            drill_type,
            selected_value,
            selected_lane,
            shipment_type
        )
        # Get accurate summary metrics using Hybrid Approach
        summary_df = get_order_summary_metrics(
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            drill_type,
            selected_value,
            selected_lane,
            shipment_type
        )

    if df is not None and len(df) > 0:
        # Summary metrics using Hybrid Approach calculations
        st.subheader(f"Summary for {drill_type}: {selected_value}")

        # Extract summary values (use Hybrid Approach totals, but row count from df)
        if summary_df is not None and len(summary_df) > 0:
            completed_orders = int(summary_df['completed_orders'].iloc[0])
            canceled_orders = int(summary_df['canceled_orders'].iloc[0])
            total_revenue = float(summary_df['total_revenue'].iloc[0])
            total_cost = float(summary_df['total_cost'].iloc[0])
            total_profit = float(summary_df['total_profit'].iloc[0])
            crossdock_cost = float(summary_df['crossdock_cost'].iloc[0])
            tonu_revenue = float(summary_df['tonu_revenue'].iloc[0]) if 'tonu_revenue' in summary_df.columns else 0
            tonu_cost = float(summary_df['tonu_cost'].iloc[0]) if 'tonu_cost' in summary_df.columns else 0
        else:
            # Fallback to raw row sums if summary query fails
            completed_orders = df['Order ID'].nunique()
            canceled_orders = 0
            total_revenue = df['Revenue'].sum()
            total_cost = df['Cost'].sum()
            total_profit = df['Profit'].sum()
            crossdock_df = df[df['Cross-dock'] == 'Yes']
            crossdock_cost = crossdock_df['Cost'].sum()
            tonu_revenue = 0
            tonu_cost = 0

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        col1.metric("Completed Orders", f"{completed_orders:,}")
        col2.metric("Canceled Orders", f"{canceled_orders:,}", help="Canceled orders with cross-dock costs")
        col3.metric("Total Rows", f"{len(df):,}")
        col4.metric("Total Revenue", f"${total_revenue:,.0f}")
        col5.metric("Total Cost", f"${total_cost:,.0f}")
        col6.metric("Total Profit", f"${total_profit:,.0f}")

        # Cross-dock breakdown
        crossdock_pct = (crossdock_cost / total_cost * 100) if total_cost > 0 else 0

        # TONU breakdown (show only if there are TONU charges)
        if tonu_revenue > 0 or tonu_cost > 0:
            col_t1, col_t2, col_t3 = st.columns(3)
            col_t1.metric("TONU Revenue", f"${tonu_revenue:,.0f}", help="Revenue from TONU (Truck Order Not Used)")
            col_t2.metric("TONU Cost", f"${tonu_cost:,.0f}", help="Cost from TONU charges")
            tonu_pct = (tonu_cost / total_cost * 100) if total_cost > 0 else 0
            col_t3.metric("TONU Cost %", f"{tonu_pct:.1f}%", help="TONU cost as % of total cost")

        st.info(f"💡 Cross-dock handling costs: ${crossdock_cost:,.0f} ({crossdock_pct:.1f}% of total cost)")
        
        st.markdown("---")
        
        # Detailed table
        st.subheader("Order Details")
        
        st.dataframe(
            df.style.format({
                'Revenue': '${:,.2f}',
                'Cost': '${:,.2f}',
                'Profit': '${:,.2f}'
            }),
            width='stretch',
            height=600
        )
        
        # Download button
        csv = df.to_csv(index=False)
        st.download_button(
            label="📥 Download as CSV",
            data=csv,
            file_name=f"drill_down_{drill_type}_{selected_value}_{start_date}_{end_date}.csv",
            mime="text/csv"
        )
    else:
        st.warning("No data found for the selected filters.")
else:
    st.info("Please select a customer or lane from the sidebar to view order details.")

