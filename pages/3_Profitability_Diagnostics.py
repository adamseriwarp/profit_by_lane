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
st.caption("Identify and analyze lanes with negative profit margins using Smart Strategy V3")

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


@st.cache_data(ttl=300)
def get_lane_profitability(start_date, end_date, shipment_type, min_orders, show_only_negative):
    date_field = """CASE 
        WHEN pickLocationName = dropLocationName THEN STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s')
        WHEN dropTimeArrived IS NOT NULL AND dropTimeArrived != '' THEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s')
        WHEN dropDateArrived IS NOT NULL AND dropDateArrived != '' THEN STR_TO_DATE(dropDateArrived, '%m/%d/%Y')
        ELSE STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s') 
    END"""
    
    base_conditions = [
        "shipmentStatus = 'Complete'",
        f"({date_field}) >= '{start_date}'",
        f"({date_field}) <= '{end_date}'"
    ]
    if shipment_type != "All":
        base_conditions.append(f"shipmentType = '{shipment_type}'")
    base_where = " AND ".join(base_conditions)
    
    having_clause = f"HAVING COUNT(DISTINCT orderCode) >= {min_orders}"
    if show_only_negative:
        having_clause += " AND SUM(smart_revenue) - SUM(smart_cost) < 0"
    
    query = f"""
    WITH order_metrics AS (
        SELECT
            orderCode,
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus != 'removed' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus != 'removed' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus != 'removed' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus != 'removed' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as yes_cost,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus != 'removed' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as no_cost,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus != 'removed' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as xd_no_cost,
            -- Simplified: assume duplicate when (NO-XD) ≈ YES (skip expensive correlated subquery)
            0 as has_matching_no_row,
            COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN startMarket END), 'NA') as startMarket,
            COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN endMarket END), 'NA') as endMarket
        FROM otp_reports
        WHERE {base_where}
        GROUP BY orderCode
    ),
    order_calculated AS (
        SELECT 
            orderCode, startMarket, endMarket,
            CASE 
                WHEN yes_rev > 0 AND no_rev = 0 THEN yes_rev
                WHEN yes_rev = 0 THEN no_rev
                WHEN ABS((no_rev - xd_leg_rev) - yes_rev) < 1 THEN no_rev
                WHEN yes_rev > 2 * no_rev THEN yes_rev + no_rev
                ELSE no_rev
            END as smart_revenue,
            CASE
                WHEN yes_cost > 0 AND no_cost = 0 THEN yes_cost
                WHEN yes_cost = 0 AND no_cost > 0 THEN no_cost
                WHEN ABS((no_cost - xd_no_cost) - yes_cost) < 20 THEN yes_cost + no_cost
                WHEN no_cost > yes_cost * 5 THEN yes_cost + no_cost
                ELSE yes_cost + xd_no_cost
            END as smart_cost,
            xd_no_cost as crossdock_cost
        FROM order_metrics
    )
    SELECT 
        CONCAT(startMarket, ' → ', endMarket) as lane,
        startMarket, endMarket,
        COUNT(DISTINCT orderCode) as order_count,
        SUM(smart_revenue) as total_revenue,
        SUM(smart_cost) as total_cost,
        SUM(smart_revenue) - SUM(smart_cost) as total_profit,
        CASE WHEN SUM(smart_revenue) > 0 THEN (SUM(smart_revenue) - SUM(smart_cost)) / SUM(smart_revenue) * 100 ELSE 0 END as margin_pct,
        SUM(crossdock_cost) as crossdock_cost
    FROM order_calculated
    GROUP BY startMarket, endMarket
    {having_clause}
    ORDER BY total_profit ASC
    """
    return execute_query(query)


# --- Load Data ---
with st.spinner("Analyzing lane profitability..."):
    df = get_lane_profitability(
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
        shipment_type,
        min_orders,
        show_only_negative
    )

if df is not None and len(df) > 0:
    total_lanes = len(df)
    negative_lanes = len(df[df['total_profit'] < 0])
    total_loss = df[df['total_profit'] < 0]['total_profit'].sum()
    total_orders_affected = df[df['total_profit'] < 0]['order_count'].sum()
    
    st.markdown("### 📊 Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Lanes Shown", f"{total_lanes:,}")
    c2.metric("Negative Margin Lanes", f"{negative_lanes:,}")
    c3.metric("Total Loss", f"${total_loss:,.0f}")
    c4.metric("Orders Affected", f"{total_orders_affected:,.0f}")
    
    st.markdown("---")
    st.subheader("🔴 Lanes by Profitability (Worst First)")
    
    display_df = df.copy()
    display_df['margin_pct'] = display_df['margin_pct'].apply(lambda x: f"{x:.1f}%")
    display_df['total_revenue'] = display_df['total_revenue'].apply(lambda x: f"${x:,.0f}")
    display_df['total_cost'] = display_df['total_cost'].apply(lambda x: f"${x:,.0f}")
    display_df['total_profit'] = display_df['total_profit'].apply(lambda x: f"${x:,.0f}")
    display_df['crossdock_cost'] = display_df['crossdock_cost'].apply(lambda x: f"${x:,.0f}")
    display_df = display_df.rename(columns={
        'lane': 'Lane', 'order_count': 'Orders', 'total_revenue': 'Revenue',
        'total_cost': 'Cost', 'total_profit': 'Profit', 'margin_pct': 'Margin %',
        'crossdock_cost': 'Crossdock Cost'
    })
    
    st.dataframe(
        display_df[['Lane', 'Orders', 'Revenue', 'Cost', 'Profit', 'Margin %', 'Crossdock Cost']],
        use_container_width=True,
        height=600
    )
    
    csv = df.to_csv(index=False)
    st.download_button(
        label="📥 Download Full Data (CSV)",
        data=csv,
        file_name=f"lane_profitability_{start_date}_{end_date}.csv",
        mime="text/csv"
    )
    
    na_lanes = df[df['lane'].str.contains('NA')]
    if len(na_lanes) > 0:
        st.warning(f"⚠️ {len(na_lanes)} lane(s) have 'NA' markets - orders where mainShipment='YES' row lacks market data.")
else:
    st.info("No lanes found. Try adjusting filters or disabling 'Show only negative margin lanes'.")

