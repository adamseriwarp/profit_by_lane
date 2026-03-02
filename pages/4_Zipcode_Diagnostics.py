import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_connection import execute_query
from auth import check_password

st.set_page_config(page_title="Zipcode Diagnostics", page_icon="📍", layout="wide")

if not check_password():
    st.stop()

st.title("📍 Zipcode Lane Diagnostics")
st.caption("Analyze profitability at the zip code level (pickZipCode → dropZipCode)")

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
    conditions = [
        "(shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')",
        f"({DATE_FIELD}) >= '{start_date}'",
        f"({DATE_FIELD}) <= '{end_date}'",
        "pickZipCode IS NOT NULL AND pickZipCode != ''",
        "dropZipCode IS NOT NULL AND dropZipCode != ''"
    ]
    if shipment_type != "All":
        conditions.append(f"shipmentType = '{shipment_type}'")
    if excluded_customers:
        customers_str = "', '".join(excluded_customers)
        conditions.append(f"clientName NOT IN ('{customers_str}')")
    return " AND ".join(conditions)


@st.cache_data(ttl=300)
def get_zipcode_profitability(start_date, end_date, shipment_type, min_orders, show_only_negative, excluded_customers=None):
    """Get zipcode lane profitability using row-level lane allocation."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

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

    query = f"""
    SELECT
        lane, pickZipCode, dropZipCode, pickCity, dropCity,
        startMarket, endMarket,
        completed_orders, canceled_orders, avg_miles,
        total_revenue, total_cost, crossdock_cost,
        (total_revenue - total_cost) as total_profit,
        CASE WHEN total_revenue > 0 THEN (total_revenue - total_cost) / total_revenue * 100 ELSE 0 END as margin_pct,
        CASE WHEN total_cost > 0 THEN crossdock_cost / total_cost * 100 ELSE 0 END as xd_cost_pct,
        tonu_revenue, tonu_cost
    FROM (
        SELECT
            CONCAT(pickZipCode, ' → ', dropZipCode) as lane,
            pickZipCode, dropZipCode,
            MAX(pickCity) as pickCity,
            MAX(dropCity) as dropCity,
            MAX(startMarket) as startMarket,
            MAX(endMarket) as endMarket,
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
        GROUP BY pickZipCode, dropZipCode
        {having_clause}
    ) as lane_agg
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_customer_analysis(start_date, end_date, shipment_type, pick_zip, drop_zip, excluded_customers=None):
    """Get customer breakdown for a specific zipcode lane."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

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
          AND pickZipCode = '{pick_zip}'
          AND dropZipCode = '{drop_zip}'
        GROUP BY clientName
    ) as cust_agg
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_carrier_analysis(start_date, end_date, shipment_type, pick_zip, drop_zip, excluded_customers=None):
    """Get carrier breakdown for a specific zipcode lane."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

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
          AND pickZipCode = '{pick_zip}'
          AND dropZipCode = '{drop_zip}'
          AND carrierName IS NOT NULL AND carrierName != ''
        GROUP BY carrierName
    ) as carrier_agg
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_lane_order_details(start_date, end_date, shipment_type, pick_zip, drop_zip, excluded_customers=None):
    """Get detailed row-level data for CSV export."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)

    query = f"""
    SELECT
        CAST(orderCode AS CHAR) as orderCode,
        CAST(warpId AS CHAR) as warpId,
        CAST(clientName AS CHAR) as customer,
        CAST(shipmentStatus AS CHAR) as shipmentStatus,
        CAST(mainShipment AS CHAR) as mainShipment,
        CAST(pickLocationName AS CHAR) as pickLocationName,
        CAST(dropLocationName AS CHAR) as dropLocationName,
        CAST(pickZipCode AS CHAR) as pickZipCode,
        CAST(dropZipCode AS CHAR) as dropZipCode,
        CAST(pickCity AS CHAR) as pickCity,
        CAST(dropCity AS CHAR) as dropCity,
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
      AND pickZipCode = '{pick_zip}'
      AND dropZipCode = '{drop_zip}'
    ORDER BY orderCode, mainShipment DESC, warpId
    """
    return execute_query(query)


# --- Load Data ---
with st.spinner("Analyzing zipcode lane profitability..."):
    df = get_zipcode_profitability(
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
    c1.metric("Zip Lanes Shown", f"{total_lanes:,}")
    c2.metric("Negative Margin Lanes", f"{negative_lanes:,}")
    c3.metric("Total Loss", f"${total_loss:,.0f}")
    c4.metric("Completed Affected", f"{completed_affected:,.0f}")
    c5.metric("Canceled Affected", f"{canceled_affected:,.0f}")

    if total_tonu_rev > 0 or total_tonu_cost > 0:
        col_t1, col_t2, col_t3 = st.columns(3)
        col_t1.metric("TONU Revenue", f"${total_tonu_rev:,.0f}")
        col_t2.metric("TONU Cost", f"${total_tonu_cost:,.0f}")
        total_cost_all = df['total_cost'].sum()
        tonu_pct = (total_tonu_cost / total_cost_all * 100) if total_cost_all > 0 else 0
        col_t3.metric("TONU Cost %", f"{tonu_pct:.1f}%")

    st.markdown("---")

    # Lane selector - show city names for context
    df['lane_display'] = df.apply(lambda r: f"{r['lane']} ({r['pickCity']} → {r['dropCity']})", axis=1)
    lane_options = df['lane_display'].tolist()
    selected_lane_display = st.selectbox("🎯 Select a Zipcode Lane to Analyze", options=lane_options, index=0)

    # Get selected lane data
    selected_row = df[df['lane_display'] == selected_lane_display].iloc[0]
    selected_pick_zip = selected_row['pickZipCode']
    selected_drop_zip = selected_row['dropZipCode']
    selected_lane = selected_row['lane']

    # Display selected lane summary
    st.markdown(f"### Selected: **{selected_lane}** ({selected_row['pickCity']} → {selected_row['dropCity']})")
    sel_c1, sel_c2, sel_c3, sel_c4, sel_c5, sel_c6 = st.columns(6)
    sel_c1.metric("Completed", f"{int(selected_row['completed_orders']):,}")
    sel_c2.metric("Canceled", f"{int(selected_row['canceled_orders']):,}")
    sel_c3.metric("Revenue", f"${selected_row['total_revenue']:,.0f}")
    sel_c4.metric("Cost", f"${selected_row['total_cost']:,.0f}")
    sel_c5.metric("Profit", f"${selected_row['total_profit']:,.0f}")
    sel_c6.metric("Margin", f"{selected_row['margin_pct']:.1f}%")

    st.markdown("---")

    # Tabs for analysis
    tab1, tab2, tab3 = st.tabs(["📋 All Lanes", "👥 Customers", "🚚 Carriers"])

    # --- TAB 1: All Lanes ---
    with tab1:
        st.subheader("🔴 Zipcode Lanes by Profitability (Worst First)")
        display_df = df.copy()
        display_df['margin_pct'] = display_df['margin_pct'].apply(lambda x: f"{x:.1f}%")
        display_df['xd_cost_pct'] = display_df['xd_cost_pct'].apply(lambda x: f"{x:.1f}%")
        display_df['total_revenue'] = display_df['total_revenue'].apply(lambda x: f"${x:,.0f}")
        display_df['total_cost'] = display_df['total_cost'].apply(lambda x: f"${x:,.0f}")
        display_df['total_profit'] = display_df['total_profit'].apply(lambda x: f"${x:,.0f}")
        display_df = display_df.rename(columns={
            'lane': 'Lane', 'pickCity': 'Origin City', 'dropCity': 'Dest City',
            'startMarket': 'Start Market', 'endMarket': 'End Market',
            'completed_orders': 'Completed', 'canceled_orders': 'Canceled',
            'avg_miles': 'Avg Miles', 'total_revenue': 'Revenue', 'total_cost': 'Cost',
            'total_profit': 'Profit', 'margin_pct': 'Margin %', 'xd_cost_pct': 'XD Cost %'
        })
        st.dataframe(
            display_df[['Lane', 'Origin City', 'Dest City', 'Start Market', 'End Market', 'Completed', 'Canceled', 'Avg Miles', 'Revenue', 'Cost', 'Profit', 'Margin %', 'XD Cost %']],
            use_container_width=True, height=400
        )
        st.download_button("📥 Download All Zipcode Lanes (CSV)", df.to_csv(index=False),
                           f"zipcode_lanes_{start_date}_{end_date}.csv", "text/csv", key="dl_lanes")

    # --- TAB 2: Customer Analysis ---
    with tab2:
        st.subheader(f"👥 Customers on {selected_lane}")
        with st.spinner("Loading customer data..."):
            cust_df = get_customer_analysis(
                start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                shipment_type, selected_pick_zip, selected_drop_zip, excluded_customers
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

            with st.spinner("Preparing order details for export..."):
                order_details = get_lane_order_details(
                    start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                    shipment_type, selected_pick_zip, selected_drop_zip, excluded_customers
                )
            if order_details is not None:
                st.download_button("📥 Download Order Details (CSV)", order_details.to_csv(index=False),
                                   f"zipcode_orders_{selected_lane.replace(' → ', '_')}_{start_date}_{end_date}.csv",
                                   "text/csv", key="dl_cust")
        else:
            st.info("No customer data found for this lane.")

    # --- TAB 3: Carrier Analysis ---
    with tab3:
        st.subheader(f"🚚 Carriers on {selected_lane}")
        with st.spinner("Loading carrier data..."):
            carrier_df = get_carrier_analysis(
                start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                shipment_type, selected_pick_zip, selected_drop_zip, excluded_customers
            )
        if carrier_df is not None and len(carrier_df) > 0:
            total_carriers = len(carrier_df)
            most_used = carrier_df.loc[carrier_df['orders_with_carrier'].idxmax()]
            worst_profit = carrier_df.loc[carrier_df['total_profit'].idxmin()]
            worst_avg = carrier_df.loc[carrier_df['avg_profit_per_shipment'].idxmin()]

            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Total Carriers", f"{total_carriers}")
            cc2.metric("Most Used", most_used['carrier'], f"{int(most_used['orders_with_carrier'])} orders")
            cc3.metric("Worst Total Profit", worst_profit['carrier'], f"${worst_profit['total_profit']:,.0f}")
            cc4.metric("Worst Avg Profit", worst_avg['carrier'], f"${worst_avg['avg_profit_per_shipment']:,.0f}/shipment")

            st.caption("Ordered by total profit (worst first)")
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

            with st.spinner("Preparing order details for export..."):
                order_details = get_lane_order_details(
                    start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'),
                    shipment_type, selected_pick_zip, selected_drop_zip, excluded_customers
                )
            if order_details is not None:
                st.download_button("📥 Download Order Details (CSV)", order_details.to_csv(index=False),
                                   f"carrier_orders_{selected_lane.replace(' → ', '_')}_{start_date}_{end_date}.csv",
                                   "text/csv", key="dl_carrier")
        else:
            st.info("No carrier data found for this lane.")
else:
    st.info("No zipcode lanes found. Try adjusting filters or disabling 'Show only negative margin lanes'.")
