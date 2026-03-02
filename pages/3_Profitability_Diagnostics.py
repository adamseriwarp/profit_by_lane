import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_connection import execute_query
from auth import check_password

st.set_page_config(page_title="Profitability Diagnostics", page_icon="🔍", layout="wide")

if not check_password():
    st.stop()

st.title("🔍 Profitability Diagnostics")
st.caption("Identify and analyze lanes with negative profit margins using Hybrid Approach")

# --- Sidebar Filters ---
st.sidebar.header("Filters")
shipment_type = st.sidebar.selectbox("Shipment Type", options=["Less Than Truckload", "All", "Full Truckload", "Parcel"], index=0)
st.sidebar.caption("*Dates based on actual/scheduled delivery*")
col1, col2 = st.sidebar.columns(2)
default_start = datetime.now() - timedelta(days=90)
default_end = datetime.now()
start_date = col1.date_input("Start Date", default_start)
end_date = col2.date_input("End Date", default_end)
min_orders = st.sidebar.number_input("Minimum Orders per Lane", min_value=1, value=5)
show_only_negative = st.sidebar.checkbox("Show only negative margin lanes", value=True)

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
    # Use iloc to access first column by position (works regardless of column naming)
    return df.iloc[:, 0].tolist() if df is not None and len(df) > 0 else []

customers = get_customers()
excluded_customers = st.sidebar.multiselect("Exclude Customers", options=customers,
    help="Select customers to EXCLUDE from the analysis")

# Helper: Date field logic
DATE_FIELD = """CASE
    WHEN pickLocationName = dropLocationName THEN STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
    WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != '' THEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s')
    WHEN dropDateArrived IS NOT NULL AND dropDateArrived != '' THEN STR_TO_DATE(dropDateArrived, '%m/%d/%Y')
    ELSE STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
END"""


def get_base_conditions(start_date, end_date, shipment_type, excluded_customers=None):
    # Include: Complete orders + Canceled orders + TONU (regardless of status)
    conditions = [
        "(shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')",
        f"({DATE_FIELD}) >= '{start_date}'",
        f"({DATE_FIELD}) <= '{end_date}'",
        "startMarket IS NOT NULL AND startMarket != ''",
        "endMarket IS NOT NULL AND endMarket != ''"
    ]
    if shipment_type != "All":
        conditions.append(f"shipmentType = '{shipment_type}'")
    if excluded_customers:
        customers_str = "', '".join(excluded_customers)
        conditions.append(f"clientName NOT IN ('{customers_str}')")
    return " AND ".join(conditions)


@st.cache_data(ttl=300)
def get_lane_profitability(start_date, end_date, shipment_type, min_orders, show_only_negative, excluded_customers=None):
    """Get lane profitability using row-level lane allocation."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

    # Build HAVING clause - note: we use the revenue/cost expression directly
    having_parts = [f"COUNT(DISTINCT orderCode) >= {min_orders}"]
    if show_only_negative:
        having_parts.append("""(SUM(CASE
            WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
            WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
            WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
            ELSE 0
        END) - SUM(CASE
            WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
            WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
            WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
            ELSE 0
        END)) < 0""")
    having_clause = "HAVING " + " AND ".join(having_parts)

    # Row-level allocation: each row's revenue/cost goes to that row's lane
    # Use a subquery to compute total_profit, margin_pct, xd_cost_pct from aggregates
    query = f"""
    SELECT
        lane, startMarket, endMarket,
        completed_orders, canceled_orders, avg_miles,
        total_revenue, total_cost, crossdock_cost,
        (total_revenue - total_cost) as total_profit,
        CASE WHEN total_revenue > 0 THEN (total_revenue - total_cost) / total_revenue * 100 ELSE 0 END as margin_pct,
        CASE WHEN total_cost > 0 THEN crossdock_cost / total_cost * 100 ELSE 0 END as xd_cost_pct,
        tonu_revenue, tonu_cost
    FROM (
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket, endMarket,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders,
            ROUND(AVG(CASE WHEN mainShipment = 'YES' THEN COALESCE(shipmentMiles, 0) ELSE NULL END), 0) as avg_miles,
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
                WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
        GROUP BY startMarket, endMarket
        {having_clause}
    ) as lane_agg
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_customer_analysis(start_date, end_date, shipment_type, start_market, end_market, excluded_customers=None):
    """Get customer breakdown for a specific lane using row-level lane allocation."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

    # Row-level: filter rows where startMarket/endMarket match the selected lane
    query = f"""
    SELECT
        customer, completed_orders, canceled_orders,
        total_revenue, total_cost,
        (total_revenue - total_cost) as total_profit,
        CASE WHEN total_revenue > 0 THEN (total_revenue - total_cost) / total_revenue * 100 ELSE 0 END as margin_pct,
        CASE WHEN (completed_orders + canceled_orders) > 0
            THEN (total_revenue - total_cost) / (completed_orders + canceled_orders)
            ELSE 0 END as avg_profit_per_order,
        tonu_revenue, tonu_cost
    FROM (
        SELECT
            clientName as customer,
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
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
          AND startMarket = '{start_market}'
          AND endMarket = '{end_market}'
        GROUP BY clientName
    ) as cust_agg
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_carrier_analysis(start_date, end_date, shipment_type, start_market, end_market, excluded_customers=None):
    """Get carrier breakdown for a specific lane using row-level lane allocation."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

    # Row-level: filter rows where startMarket/endMarket match the selected lane
    query = f"""
    SELECT
        carrier, orders_with_carrier, shipment_count,
        total_cost, total_revenue,
        (total_revenue - total_cost) as total_profit,
        CASE WHEN shipment_count > 0
            THEN (total_revenue - total_cost) / shipment_count
            ELSE 0 END as avg_profit_per_shipment
    FROM (
        SELECT
            carrierName as carrier,
            COUNT(DISTINCT orderCode) as orders_with_carrier,
            COUNT(DISTINCT warpId) as shipment_count,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as total_cost,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
                ELSE 0
            END) as total_revenue
        FROM otp_reports
        WHERE {base_where}
          AND startMarket = '{start_market}'
          AND endMarket = '{end_market}'
          AND carrierName IS NOT NULL AND carrierName != ''
        GROUP BY carrierName
    ) as carrier_agg
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_similar_mileage_lanes(start_date, end_date, shipment_type, target_miles, tolerance_pct=0.2, excluded_customers=None):
    """Get lanes with similar mileage (within tolerance %) using row-level lane allocation."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)
    min_miles = target_miles * (1 - tolerance_pct)
    max_miles = target_miles * (1 + tolerance_pct)

    # Row-level allocation with mileage filter on mainShipment rows
    # Use subquery to compute profit, margin_pct, xd_cost_pct
    query = f"""
    SELECT
        lane, startMarket, endMarket,
        completed_orders, canceled_orders, avg_miles,
        total_revenue, total_cost, crossdock_cost,
        (total_revenue - total_cost) as profit,
        CASE WHEN total_revenue > 0 THEN (total_revenue - total_cost) / total_revenue * 100 ELSE 0 END as margin_pct,
        CASE WHEN total_cost > 0 THEN crossdock_cost / total_cost * 100 ELSE 0 END as xd_cost_pct,
        avg_legs, tonu_revenue, tonu_cost
    FROM (
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket, endMarket,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders,
            ROUND(AVG(CASE WHEN mainShipment = 'YES' THEN COALESCE(shipmentMiles, 0) ELSE NULL END), 0) as avg_miles,
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
                WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                ELSE 0
            END) as crossdock_cost,
            COUNT(DISTINCT warpId) / COUNT(DISTINCT orderCode) as avg_legs,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
        FROM otp_reports
        WHERE {base_where}
        GROUP BY startMarket, endMarket
        HAVING AVG(CASE WHEN mainShipment = 'YES' THEN COALESCE(shipmentMiles, 0) ELSE NULL END) BETWEEN {min_miles} AND {max_miles}
           AND COUNT(DISTINCT orderCode) >= 3
    ) as mileage_agg
    ORDER BY profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_lane_order_details(start_date, end_date, shipment_type, start_market, end_market, excluded_customers=None):
    """Get detailed row-level data for CSV export using row-level lane allocation."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

    # Row-level: show all rows that belong to this lane
    # Use CAST to avoid MySQL connector binary unpacking errors
    query = f"""
    SELECT
        CAST(orderCode AS CHAR) as orderCode,
        CAST(warpId AS CHAR) as warpId,
        CAST(clientName AS CHAR) as customer,
        CAST(shipmentStatus AS CHAR) as shipmentStatus,
        CAST(mainShipment AS CHAR) as mainShipment,
        CAST(pickLocationName AS CHAR) as pickLocationName,
        CAST(dropLocationName AS CHAR) as dropLocationName,
        CAST(startMarket AS CHAR) as startMarket,
        CAST(endMarket AS CHAR) as endMarket,
        CAST(dropWindowFrom AS CHAR) as scheduled_delivery,
        CAST(COALESCE(dropTimeArrived, dropDateArrived) AS CHAR) as actual_delivery,
        CAST(carrierName AS CHAR) as carrier,
        CAST(accessorialType AS CHAR) as accessorialType,
        CASE
            WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
            WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
            WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
            ELSE 0
        END as revenue,
        CASE
            WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
            WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
            WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
            ELSE 0
        END as cost,
        CASE WHEN accessorialType = 'TONU' THEN 'Yes' ELSE 'No' END as is_tonu,
        CASE WHEN pickLocationName = dropLocationName THEN 'Yes' ELSE 'No' END as is_crossdock
    FROM otp_reports
    WHERE {base_where}
      AND startMarket = '{start_market}'
      AND endMarket = '{end_market}'
    ORDER BY orderCode, mainShipment DESC, warpId
    """
    return execute_query(query)


# --- Load Data ---
with st.spinner("Analyzing lane profitability..."):
    df = get_lane_profitability(
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
        shipment_type,
        min_orders,
        show_only_negative,
        excluded_customers
    )

if df is not None and len(df) > 0:
    total_lanes = len(df)
    negative_lanes = len(df[df['total_profit'] < 0])
    total_loss = df[df['total_profit'] < 0]['total_profit'].sum()
    neg_df = df[df['total_profit'] < 0]
    completed_affected = neg_df['completed_orders'].sum()
    canceled_affected = neg_df['canceled_orders'].sum()
    total_tonu_rev = df['tonu_revenue'].sum() if 'tonu_revenue' in df.columns else 0
    total_tonu_cost = df['tonu_cost'].sum() if 'tonu_cost' in df.columns else 0

    st.markdown("### 📊 Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Lanes Shown", f"{total_lanes:,}")
    c2.metric("Negative Margin Lanes", f"{negative_lanes:,}")
    c3.metric("Total Loss", f"${total_loss:,.0f}")
    c4.metric("Completed Affected", f"{completed_affected:,.0f}")
    c5.metric("Canceled Affected", f"{canceled_affected:,.0f}", help="Canceled orders with cross-dock costs")

    # TONU summary (show only if there are TONU charges)
    if total_tonu_rev > 0 or total_tonu_cost > 0:
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.metric("TONU Revenue", f"${total_tonu_rev:,.0f}", help="Revenue from TONU (Truck Order Not Used)")
        col_t2.metric("TONU Cost", f"${total_tonu_cost:,.0f}", help="Cost from TONU charges")
        total_cost_all = df['total_cost'].sum()
        tonu_pct = (total_tonu_cost / total_cost_all * 100) if total_cost_all > 0 else 0
        col_t3.metric("TONU Cost %", f"{tonu_pct:.1f}%", help="TONU cost as % of total cost")

    st.markdown("---")

    # Lane selector
    lane_options = df['lane'].tolist()
    selected_lane = st.selectbox("🎯 Select a Lane to Analyze", options=lane_options, index=0)

    # Get selected lane data
    selected_row = df[df['lane'] == selected_lane].iloc[0]
    selected_start = selected_row['startMarket']
    selected_end = selected_row['endMarket']
    selected_miles = selected_row.get('avg_miles', 0)

    # Display selected lane summary
    st.markdown(f"### Selected: **{selected_lane}**")
    sel_c1, sel_c2, sel_c3, sel_c4, sel_c5, sel_c6 = st.columns(6)
    sel_c1.metric("Completed", f"{int(selected_row['completed_orders']):,}")
    sel_c2.metric("Canceled", f"{int(selected_row['canceled_orders']):,}")
    sel_c3.metric("Revenue", f"${selected_row['total_revenue']:,.0f}")
    sel_c4.metric("Cost", f"${selected_row['total_cost']:,.0f}")
    sel_c5.metric("Profit", f"${selected_row['total_profit']:,.0f}")
    sel_c6.metric("Margin", f"{selected_row['margin_pct']:.1f}%")

    st.markdown("---")

    # Tabs for analysis
    tab1, tab2, tab3, tab4 = st.tabs(["📋 All Lanes", "👥 Customers", "🚚 Carriers", "📏 Similar Mileage"])

    # --- TAB 1: All Lanes ---
    with tab1:
        st.subheader("🔴 Lanes by Profitability (Worst First)")
        display_df = df.copy()
        display_df['margin_pct'] = display_df['margin_pct'].apply(lambda x: f"{x:.1f}%")
        display_df['xd_cost_pct'] = display_df['xd_cost_pct'].apply(lambda x: f"{x:.1f}%")
        display_df['total_revenue'] = display_df['total_revenue'].apply(lambda x: f"${x:,.0f}")
        display_df['total_cost'] = display_df['total_cost'].apply(lambda x: f"${x:,.0f}")
        display_df['total_profit'] = display_df['total_profit'].apply(lambda x: f"${x:,.0f}")
        display_df = display_df.rename(columns={
            'lane': 'Lane', 'completed_orders': 'Completed', 'canceled_orders': 'Canceled',
            'avg_miles': 'Avg Miles', 'total_revenue': 'Revenue', 'total_cost': 'Cost',
            'total_profit': 'Profit', 'margin_pct': 'Margin %', 'xd_cost_pct': 'XD Cost %'
        })
        st.dataframe(
            display_df[['Lane', 'Completed', 'Canceled', 'Avg Miles', 'Revenue', 'Cost', 'Profit', 'Margin %', 'XD Cost %']],
            use_container_width=True, height=400
        )
        st.download_button("📥 Download All Lanes (CSV)", df.to_csv(index=False),
                           f"all_lanes_{start_date}_{end_date}.csv", "text/csv", key="dl_lanes")

    # --- TAB 2: Customer Analysis ---
    with tab2:
        st.subheader(f"👥 Customers on {selected_lane}")
        with st.spinner("Loading customer data..."):
            cust_df = get_customer_analysis(
                start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                shipment_type, selected_start, selected_end, excluded_customers
            )
        if cust_df is not None and len(cust_df) > 0:
            st.caption("Ordered by total profit (worst first)")
            cust_display = cust_df.copy()
            cust_display['margin_pct'] = cust_display['margin_pct'].apply(lambda x: f"{x:.1f}%")
            cust_display['total_revenue'] = cust_display['total_revenue'].apply(lambda x: f"${x:,.0f}")
            cust_display['total_cost'] = cust_display['total_cost'].apply(lambda x: f"${x:,.0f}")
            cust_display['total_profit'] = cust_display['total_profit'].apply(lambda x: f"${x:,.0f}")
            cust_display['avg_profit_per_order'] = cust_display['avg_profit_per_order'].apply(lambda x: f"${x:,.0f}")
            cust_display = cust_display.rename(columns={
                'customer': 'Customer', 'completed_orders': 'Completed', 'canceled_orders': 'Canceled',
                'total_revenue': 'Revenue', 'total_cost': 'Cost', 'total_profit': 'Total Profit',
                'margin_pct': 'Margin %', 'avg_profit_per_order': 'Avg Profit/Order'
            })
            st.dataframe(cust_display, use_container_width=True, height=400)

            # CSV export with order details
            with st.spinner("Preparing order details for export..."):
                order_details = get_lane_order_details(
                    start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                    shipment_type, selected_start, selected_end, excluded_customers
                )
            if order_details is not None:
                st.download_button("📥 Download Order Details (CSV)", order_details.to_csv(index=False),
                                   f"customer_orders_{selected_lane.replace(' → ', '_')}_{start_date}_{end_date}.csv",
                                   "text/csv", key="dl_cust")
        else:
            st.info("No customer data found for this lane.")

    # --- TAB 3: Carrier Analysis ---
    with tab3:
        st.subheader(f"🚚 Carriers on {selected_lane}")
        with st.spinner("Loading carrier data..."):
            carrier_df = get_carrier_analysis(
                start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                shipment_type, selected_start, selected_end, excluded_customers
            )
        if carrier_df is not None and len(carrier_df) > 0:
            # Summary metrics
            total_carriers = len(carrier_df)
            most_used = carrier_df.loc[carrier_df['orders_with_carrier'].idxmax()]
            worst_profit = carrier_df.loc[carrier_df['total_profit'].idxmin()]
            worst_avg = carrier_df.loc[carrier_df['avg_profit_per_shipment'].idxmin()]

            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Total Carriers", f"{total_carriers}")
            cc2.metric("Most Used", most_used['carrier'], f"{int(most_used['orders_with_carrier'])} orders")
            cc3.metric("Worst Total Profit", worst_profit['carrier'], f"${worst_profit['total_profit']:,.0f}")
            cc4.metric("Worst Avg Profit", worst_avg['carrier'], f"${worst_avg['avg_profit_per_shipment']:,.0f}/shipment")

            st.caption("Ordered by total profit (worst first). Cost/revenue allocated per shipment row.")
            carrier_display = carrier_df.copy()
            carrier_display['total_revenue'] = carrier_display['total_revenue'].apply(lambda x: f"${x:,.0f}")
            carrier_display['total_cost'] = carrier_display['total_cost'].apply(lambda x: f"${x:,.0f}")
            carrier_display['total_profit'] = carrier_display['total_profit'].apply(lambda x: f"${x:,.0f}")
            carrier_display['avg_profit_per_shipment'] = carrier_display['avg_profit_per_shipment'].apply(lambda x: f"${x:,.0f}")
            carrier_display = carrier_display.rename(columns={
                'carrier': 'Carrier', 'orders_with_carrier': 'Orders', 'shipment_count': 'Shipments',
                'total_revenue': 'Revenue', 'total_cost': 'Cost', 'total_profit': 'Total Profit',
                'avg_profit_per_shipment': 'Avg Profit/Shipment'
            })
            st.dataframe(carrier_display, use_container_width=True, height=400)

            # CSV export
            with st.spinner("Preparing order details for export..."):
                order_details = get_lane_order_details(
                    start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                    shipment_type, selected_start, selected_end, excluded_customers
                )
            if order_details is not None:
                st.download_button("📥 Download Order Details (CSV)", order_details.to_csv(index=False),
                                   f"carrier_orders_{selected_lane.replace(' → ', '_')}_{start_date}_{end_date}.csv",
                                   "text/csv", key="dl_carrier")
        else:
            st.info("No carrier data found for this lane.")

    # --- TAB 4: Similar Mileage Lanes ---
    with tab4:
        st.subheader(f"📏 Lanes with Similar Mileage to {selected_lane}")
        if selected_miles and selected_miles > 0:
            st.caption(f"Selected lane avg: {int(selected_miles):,} miles. Showing lanes within ±20%.")
            with st.spinner("Loading similar mileage lanes..."):
                similar_df = get_similar_mileage_lanes(
                    start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                    shipment_type, selected_miles, 0.2, excluded_customers
                )
            if similar_df is not None and len(similar_df) > 0:
                # Add comparison type column
                similar_df['comparison_type'] = similar_df['lane'].apply(
                    lambda x: 'Selected' if x == selected_lane else 'Similar Mileage'
                )
                # Sort so selected lane is first
                similar_df = similar_df.sort_values(
                    by=['comparison_type', 'profit'],
                    key=lambda x: x.map({'Selected': 0, 'Similar Mileage': 1}) if x.name == 'comparison_type' else x
                )

                # Compare selected vs others
                selected_in_similar = similar_df[similar_df['lane'] == selected_lane]
                others = similar_df[similar_df['lane'] != selected_lane]
                if len(selected_in_similar) > 0 and len(others) > 0:
                    sel_profit = selected_in_similar.iloc[0]['profit']
                    others_avg_profit = others['profit'].mean()
                    diff_pct = ((sel_profit - others_avg_profit) / abs(others_avg_profit) * 100) if others_avg_profit != 0 else 0
                    diff_abs = sel_profit - others_avg_profit

                    cmp_c1, cmp_c2 = st.columns(2)
                    cmp_c1.metric("Selected Lane Profit", f"${sel_profit:,.0f}")
                    cmp_c2.metric("Similar Lanes Avg Profit", f"${others_avg_profit:,.0f}",
                                  f"{'↑' if diff_abs < 0 else '↓'} ${abs(diff_abs):,.0f} vs selected")

                similar_display = similar_df.copy()
                similar_display['margin_pct'] = similar_display['margin_pct'].apply(lambda x: f"{x:.1f}%")
                similar_display['xd_cost_pct'] = similar_display['xd_cost_pct'].apply(lambda x: f"{x:.1f}%")
                similar_display['profit'] = similar_display['profit'].apply(lambda x: f"${x:,.0f}")
                similar_display['avg_legs'] = similar_display['avg_legs'].apply(lambda x: f"{x:.1f}")
                similar_display = similar_display.rename(columns={
                    'lane': 'Lane', 'comparison_type': 'Type', 'completed_orders': 'Completed',
                    'canceled_orders': 'Canceled', 'avg_miles': 'Avg Miles', 'profit': 'Profit',
                    'margin_pct': 'Margin %', 'xd_cost_pct': 'XD Cost %', 'avg_legs': 'Avg Legs'
                })
                st.dataframe(
                    similar_display[['Lane', 'Type', 'Completed', 'Canceled', 'Avg Miles', 'Profit', 'Margin %', 'XD Cost %', 'Avg Legs']],
                    use_container_width=True, height=400
                )
                st.download_button("📥 Download Similar Lanes (CSV)", similar_df.to_csv(index=False),
                                   f"similar_lanes_{selected_lane.replace(' → ', '_')}_{start_date}_{end_date}.csv",
                                   "text/csv", key="dl_similar")
            else:
                st.info("No similar mileage lanes found.")
        else:
            st.warning("Mileage data not available for this lane.")

    # NA warning
    na_lanes = df[df['lane'].str.contains('NA')]
    if len(na_lanes) > 0:
        st.warning(f"⚠️ {len(na_lanes)} lane(s) have 'NA' markets - orders where mainShipment='YES' row lacks market data.")
else:
    st.info("No lanes found. Try adjusting filters or disabling 'Show only negative margin lanes'.")

