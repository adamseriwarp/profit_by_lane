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
    # Include completed and canceled orders (canceled costs are filtered to XD legs in aggregation)
    conditions = [
        "shipmentStatus IN ('Complete', 'canceled')",
        f"({DATE_FIELD}) >= '{start_date}'",
        f"({DATE_FIELD}) <= '{end_date}'"
    ]
    if shipment_type != "All":
        conditions.append(f"shipmentType = '{shipment_type}'")
    if excluded_customers:
        customers_str = "', '".join(excluded_customers)
        conditions.append(f"clientName NOT IN ('{customers_str}')")
    return " AND ".join(conditions)


@st.cache_data(ttl=300)
def get_lane_profitability(start_date, end_date, shipment_type, min_orders, show_only_negative, excluded_customers=None):
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)
    having_clause = f"HAVING COUNT(DISTINCT orderCode) >= {min_orders}"
    if show_only_negative:
        having_clause += " AND SUM(smart_revenue) - SUM(smart_cost) < 0"

    query = f"""
    WITH order_metrics AS (
        SELECT
            orderCode,
            MAX(CASE WHEN mainShipment = 'YES' THEN shipmentStatus END) as order_status,
            -- Revenue/Cost for Complete orders
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
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
            COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN endMarket END), 'NA') as endMarket,
            MAX(CASE WHEN mainShipment = 'YES' THEN COALESCE(shipmentMiles, 0) END) as lane_miles
        FROM otp_reports
        WHERE {base_where}
        GROUP BY orderCode
    ),
    order_calculated AS (
        SELECT
            orderCode, startMarket, endMarket, lane_miles, order_status,
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
    )
    SELECT
        CONCAT(startMarket, ' → ', endMarket) as lane,
        startMarket, endMarket,
        COUNT(DISTINCT CASE WHEN order_status = 'Complete' THEN orderCode END) as completed_orders,
        COUNT(DISTINCT CASE WHEN order_status = 'canceled' THEN orderCode END) as canceled_orders,
        ROUND(AVG(lane_miles), 0) as avg_miles,
        SUM(smart_revenue) as total_revenue,
        SUM(smart_cost) as total_cost,
        SUM(smart_revenue) - SUM(smart_cost) as total_profit,
        CASE WHEN SUM(smart_revenue) > 0 THEN (SUM(smart_revenue) - SUM(smart_cost)) / SUM(smart_revenue) * 100 ELSE 0 END as margin_pct,
        SUM(crossdock_cost) as crossdock_cost,
        CASE WHEN SUM(smart_cost) > 0 THEN SUM(crossdock_cost) / SUM(smart_cost) * 100 ELSE 0 END as xd_cost_pct,
        SUM(tonu_revenue) as tonu_revenue,
        SUM(tonu_cost) as tonu_cost
    FROM order_calculated
    GROUP BY startMarket, endMarket
    {having_clause}
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_customer_analysis(start_date, end_date, shipment_type, start_market, end_market, excluded_customers=None):
    """Get customer breakdown for a specific lane, ordered by negative profit."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)
    query = f"""
    WITH order_metrics AS (
        SELECT
            orderCode,
            MAX(clientName) as customer,
            MAX(CASE WHEN mainShipment = 'YES' THEN shipmentStatus END) as order_status,
            -- Revenue/Cost for Complete orders
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
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
        HAVING startMarket = '{start_market}' AND endMarket = '{end_market}'
    ),
    order_calculated AS (
        SELECT
            orderCode, customer, order_status,
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
            tonu_revenue,
            tonu_cost
        FROM order_metrics
    )
    SELECT
        customer,
        COUNT(DISTINCT CASE WHEN order_status = 'Complete' THEN orderCode END) as completed_orders,
        COUNT(DISTINCT CASE WHEN order_status = 'canceled' THEN orderCode END) as canceled_orders,
        SUM(smart_revenue) as total_revenue,
        SUM(smart_cost) as total_cost,
        SUM(smart_revenue) - SUM(smart_cost) as total_profit,
        CASE WHEN SUM(smart_revenue) > 0 THEN (SUM(smart_revenue) - SUM(smart_cost)) / SUM(smart_revenue) * 100 ELSE 0 END as margin_pct,
        AVG(smart_revenue) - AVG(smart_cost) as avg_profit_per_order,
        SUM(tonu_revenue) as tonu_revenue,
        SUM(tonu_cost) as tonu_cost
    FROM order_calculated
    GROUP BY customer
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_carrier_analysis(start_date, end_date, shipment_type, start_market, end_market, excluded_customers=None):
    """Get carrier breakdown for a specific lane. Cost is allocated per shipment row with carrier."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)
    query = f"""
    WITH lane_orders AS (
        SELECT DISTINCT orderCode
        FROM otp_reports
        WHERE {base_where}
          AND mainShipment = 'YES'
          AND COALESCE(startMarket, 'NA') = '{start_market}'
          AND COALESCE(endMarket, 'NA') = '{end_market}'
    ),
    carrier_shipments AS (
        SELECT
            o.carrierName as carrier,
            o.orderCode,
            o.warpId,
            COALESCE(o.costAllocationNumber, 0) as cost,
            COALESCE(o.revenueAllocationNumber, 0) as revenue,
            o.mainShipment
        FROM otp_reports o
        INNER JOIN lane_orders lo ON o.orderCode = lo.orderCode
        WHERE o.shipmentStatus != 'removed'
          AND o.carrierName IS NOT NULL AND o.carrierName != ''
    )
    SELECT
        carrier,
        COUNT(DISTINCT orderCode) as orders_with_carrier,
        COUNT(DISTINCT warpId) as shipment_count,
        SUM(cost) as total_cost,
        SUM(revenue) as total_revenue,
        SUM(revenue) - SUM(cost) as total_profit,
        AVG(revenue - cost) as avg_profit_per_shipment
    FROM carrier_shipments
    GROUP BY carrier
    ORDER BY total_profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_similar_mileage_lanes(start_date, end_date, shipment_type, target_miles, tolerance_pct=0.2, excluded_customers=None):
    """Get lanes with similar mileage (within tolerance %). Uses shipmentMiles from YES row only."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)
    min_miles = target_miles * (1 - tolerance_pct)
    max_miles = target_miles * (1 + tolerance_pct)

    query = f"""
    WITH order_metrics AS (
        SELECT
            orderCode,
            MAX(CASE WHEN mainShipment = 'YES' THEN shipmentStatus END) as order_status,
            -- Revenue/Cost for Complete orders
            SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
            SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
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
            COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN endMarket END), 'NA') as endMarket,
            MAX(CASE WHEN mainShipment = 'YES' THEN COALESCE(shipmentMiles, 0) END) as lane_miles,
            COUNT(DISTINCT warpId) as leg_count
        FROM otp_reports
        WHERE {base_where}
        GROUP BY orderCode
        HAVING lane_miles BETWEEN {min_miles} AND {max_miles}
    ),
    order_calculated AS (
        SELECT
            orderCode, startMarket, endMarket, lane_miles, leg_count, order_status,
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
    )
    SELECT
        CONCAT(startMarket, ' → ', endMarket) as lane,
        startMarket, endMarket,
        COUNT(DISTINCT CASE WHEN order_status = 'Complete' THEN orderCode END) as completed_orders,
        COUNT(DISTINCT CASE WHEN order_status = 'canceled' THEN orderCode END) as canceled_orders,
        ROUND(AVG(lane_miles), 0) as avg_miles,
        SUM(smart_revenue) - SUM(smart_cost) as profit,
        CASE WHEN SUM(smart_revenue) > 0 THEN (SUM(smart_revenue) - SUM(smart_cost)) / SUM(smart_revenue) * 100 ELSE 0 END as margin_pct,
        CASE WHEN SUM(smart_cost) > 0 THEN SUM(crossdock_cost) / SUM(smart_cost) * 100 ELSE 0 END as xd_cost_pct,
        AVG(leg_count) as avg_legs,
        SUM(tonu_revenue) as tonu_revenue,
        SUM(tonu_cost) as tonu_cost
    FROM order_calculated
    GROUP BY startMarket, endMarket
    HAVING COUNT(DISTINCT orderCode) >= 3
    ORDER BY profit ASC
    """
    return execute_query(query)


@st.cache_data(ttl=300)
def get_lane_order_details(start_date, end_date, shipment_type, start_market, end_market, excluded_customers=None):
    """Get detailed order-level data for CSV export."""
    base_where = get_base_conditions(start_date, end_date, shipment_type, excluded_customers)
    query = f"""
    WITH lane_orders AS (
        SELECT DISTINCT orderCode
        FROM otp_reports
        WHERE {base_where}
          AND mainShipment = 'YES'
          AND COALESCE(startMarket, 'NA') = '{start_market}'
          AND COALESCE(endMarket, 'NA') = '{end_market}'
    ),
    order_metrics AS (
        SELECT
            o.orderCode,
            MAX(o.clientName) as customer,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN o.shipmentStatus END) as order_status,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN o.pickLocationName END) as pickLocationName,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN o.dropLocationName END) as dropLocationName,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN o.startMarket END) as startMarket,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN o.endMarket END) as endMarket,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN o.dropWindowFrom END) as scheduled_delivery,
            MAX(CASE WHEN o.mainShipment = 'YES' THEN COALESCE(o.dropTimeArrived, o.dropDateArrived) END) as actual_delivery,
            GROUP_CONCAT(DISTINCT o.carrierName ORDER BY o.carrierName SEPARATOR ', ') as carriers,
            -- Complete order metrics
            SUM(CASE WHEN o.mainShipment = 'YES' AND o.shipmentStatus = 'Complete' THEN COALESCE(o.revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
            SUM(CASE WHEN o.mainShipment = 'NO' AND o.shipmentStatus = 'Complete' THEN COALESCE(o.revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
            SUM(CASE WHEN o.mainShipment = 'NO' AND o.pickLocationName = o.dropLocationName AND o.shipmentStatus = 'Complete' THEN COALESCE(o.revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
            SUM(CASE WHEN o.mainShipment = 'YES' AND o.shipmentStatus = 'Complete' THEN COALESCE(o.costAllocationNumber, 0) ELSE 0 END) as yes_cost,
            SUM(CASE WHEN o.mainShipment = 'NO' AND o.shipmentStatus = 'Complete' THEN COALESCE(o.costAllocationNumber, 0) ELSE 0 END) as no_cost,
            SUM(CASE WHEN o.mainShipment = 'NO' AND o.pickLocationName = o.dropLocationName AND o.shipmentStatus = 'Complete' THEN COALESCE(o.costAllocationNumber, 0) ELSE 0 END) as xd_no_cost,
            -- Canceled order crossdock values
            SUM(CASE WHEN o.shipmentStatus = 'canceled' AND o.pickLocationName = o.dropLocationName THEN COALESCE(o.revenueAllocationNumber, 0) ELSE 0 END) as canceled_xd_rev,
            SUM(CASE WHEN o.shipmentStatus = 'canceled' AND o.pickLocationName = o.dropLocationName THEN COALESCE(o.costAllocationNumber, 0) ELSE 0 END) as canceled_xd_cost,
            -- TONU metrics (Truck Order Not Used)
            SUM(CASE WHEN o.accessorialType = 'TONU' THEN COALESCE(o.revenueAllocationNumber, 0) ELSE 0 END) as tonu_revenue,
            SUM(CASE WHEN o.accessorialType = 'TONU' THEN COALESCE(o.costAllocationNumber, 0) ELSE 0 END) as tonu_cost,
            -- Duplicate detection
            MAX(CASE WHEN o.mainShipment = 'NO' AND o.shipmentStatus = 'Complete' AND ABS(
                COALESCE(o.costAllocationNumber, 0) - (
                    SELECT SUM(COALESCE(o3.costAllocationNumber, 0))
                    FROM otp_reports o3
                    WHERE o3.orderCode = o.orderCode
                      AND o3.mainShipment = 'YES'
                      AND o3.shipmentStatus = 'Complete'
                )
            ) < 1 THEN 1 ELSE 0 END) as has_matching_no_row
        FROM otp_reports o
        INNER JOIN lane_orders lo ON o.orderCode = lo.orderCode
        GROUP BY o.orderCode
    )
    SELECT
        orderCode,
        customer,
        order_status,
        pickLocationName,
        dropLocationName,
        startMarket,
        endMarket,
        scheduled_delivery,
        actual_delivery,
        carriers,
        -- Revenue: Smart Strategy for Complete, XD only for canceled
        CASE
            WHEN order_status = 'canceled' THEN canceled_xd_rev
            WHEN yes_rev > 0 AND no_rev = 0 THEN yes_rev
            WHEN yes_rev = 0 THEN no_rev
            WHEN ABS((no_rev - xd_leg_rev) - yes_rev) < 1 THEN no_rev
            WHEN yes_rev > 2 * no_rev THEN yes_rev + no_rev
            ELSE no_rev
        END as revenue,
        -- Cost: Smart Strategy for Complete, XD only for canceled
        CASE
            WHEN order_status = 'canceled' THEN canceled_xd_cost
            WHEN yes_cost > 0 AND no_cost = 0 THEN yes_cost
            WHEN yes_cost = 0 AND no_cost > 0 THEN no_cost
            WHEN ABS((no_cost - xd_no_cost) - yes_cost) < 20 THEN
                CASE WHEN has_matching_no_row = 1 THEN yes_cost ELSE yes_cost + no_cost END
            WHEN no_cost > yes_cost * 5 THEN yes_cost + no_cost
            ELSE yes_cost + xd_no_cost
        END as cost,
        tonu_revenue,
        tonu_cost
    FROM order_metrics
    ORDER BY (
        CASE
            WHEN order_status = 'canceled' THEN canceled_xd_rev
            WHEN yes_rev > 0 AND no_rev = 0 THEN yes_rev
            WHEN yes_rev = 0 THEN no_rev
            WHEN ABS((no_rev - xd_leg_rev) - yes_rev) < 1 THEN no_rev
            WHEN yes_rev > 2 * no_rev THEN yes_rev + no_rev
            ELSE no_rev
        END -
        CASE
            WHEN order_status = 'canceled' THEN canceled_xd_cost
            WHEN yes_cost > 0 AND no_cost = 0 THEN yes_cost
            WHEN yes_cost = 0 AND no_cost > 0 THEN no_cost
            WHEN ABS((no_cost - xd_no_cost) - yes_cost) < 20 THEN
                CASE WHEN has_matching_no_row = 1 THEN yes_cost ELSE yes_cost + no_cost END
            WHEN no_cost > yes_cost * 5 THEN yes_cost + no_cost
            ELSE yes_cost + xd_no_cost
        END
    ) ASC
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

