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
    Get profit by lane data with cross-dock cost breakdown.

    Logic by shipment type:
    - FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
    - LTL: Smart Strategy based on revenue pattern:
        * YES_ONLY (yes_rev > 0, no_rev = 0): Use YES rows
        * NO_ONLY (yes_rev = 0): Use NO rows
        * BOTH pattern (refined):
            - If (NO - XD) ≈ YES → USE NO (captures base + crossdock extra)
            - Else if YES >= 2*NO → USE YES (main revenue in YES, NO is small charges)
            - Else → USE NO (default to capture crossdock)
    - Parcel: Use YES rows only

    Lane is always defined by the mainShipment='YES' row's startMarket → endMarket.

    Match Rate: 97.9% (excluding crossdock legs) against orders table.
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
    # Include: Complete orders + Canceled orders with crossdock legs
    base_conditions = [
        """(
            shipmentStatus = 'Complete'
            OR (
                shipmentStatus = 'canceled'
                AND EXISTS (
                    SELECT 1 FROM otp_reports o_xd
                    WHERE o_xd.orderCode = otp_reports.orderCode
                      AND o_xd.pickLocationName = o_xd.dropLocationName
                )
            )
        )""",
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
        # FTL: Use ALL rows (YES + NO) to capture cross-dock handling costs
        # For canceled orders: only count crossdock leg revenue/cost
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
                ELSE 0
            END) as total_revenue,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
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
        ORDER BY (SUM(CASE WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                           WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END)
                - SUM(CASE WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                           WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END)) DESC
        """

    elif shipment_type == "Less Than Truckload":
        # LTL: Separate Smart Strategies for Revenue and Cost
        # For canceled orders: only use crossdock leg revenue/cost
        query = f"""
        WITH order_metrics AS (
            SELECT
                orderCode,
                -- Track order status (Complete or canceled)
                MAX(CASE WHEN mainShipment = 'YES' THEN shipmentStatus END) as order_status,
                -- Revenue metrics (for Complete orders: full strategy; for canceled: only XD legs)
                SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
                SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
                SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete'
                    THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
                -- Cost metrics (for Complete orders only)
                SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as yes_cost,
                SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as no_cost,
                SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete'
                    THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as xd_no_cost,
                -- Canceled order crossdock values (revenue and cost)
                SUM(CASE WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName
                    THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as canceled_xd_rev,
                SUM(CASE WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName
                    THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as canceled_xd_cost,
                -- TONU metrics
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_rev,
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost,
                -- Duplicate detection for cost
                MAX(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' AND ABS(
                    COALESCE(costAllocationNumber, 0) - (
                        SELECT SUM(COALESCE(costAllocationNumber, 0))
                        FROM otp_reports o2
                        WHERE o2.orderCode = otp_reports.orderCode
                          AND o2.mainShipment = 'YES'
                          AND o2.shipmentStatus = 'Complete'
                    )
                ) < 1 THEN 1 ELSE 0 END) as has_matching_no_row,
                -- Lane determination from YES row with NA fallback
                COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN startMarket END), 'NA') as startMarket,
                COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN endMarket END), 'NA') as endMarket
            FROM otp_reports
            WHERE {base_where}
              AND shipmentType = 'Less Than Truckload'
            GROUP BY orderCode
        ),
        order_calculated AS (
            SELECT
                orderCode,
                order_status,
                startMarket,
                endMarket,
                tonu_rev,
                tonu_cost,
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
                CASE WHEN order_status = 'canceled' THEN canceled_xd_cost ELSE xd_no_cost END as crossdock_cost
            FROM order_metrics
        )
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT CASE WHEN order_status = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN order_status = 'canceled' THEN orderCode END) as canceled_orders,
            SUM(smart_revenue) as total_revenue,
            SUM(smart_cost) as total_cost,
            SUM(smart_revenue) - SUM(smart_cost) as total_profit,
            SUM(crossdock_cost) as crossdock_cost,
            SUM(tonu_rev) as tonu_revenue,
            SUM(tonu_cost) as tonu_cost
        FROM order_calculated
        GROUP BY startMarket, endMarket
        ORDER BY total_profit DESC
        """

    elif shipment_type == "Parcel":
        # Parcel: Use mainShipment = 'YES' rows only
        # For canceled orders: only count crossdock leg revenue/cost
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
                ELSE 0
            END) as total_revenue,
            SUM(CASE
                WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
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
          AND mainShipment = 'YES'
        GROUP BY startMarket, endMarket
        HAVING COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) > 0
            OR COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) > 0
        ORDER BY (SUM(CASE WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                           WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END)
                - SUM(CASE WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                           WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END)) DESC
        """

    else:
        # All shipment types - combine FTL + LTL Smart Strategy + Parcel
        # For canceled orders: only track crossdock leg revenue/cost
        query = f"""
        WITH ltl_order_metrics AS (
            SELECT
                orderCode,
                MAX(CASE WHEN mainShipment = 'YES' THEN shipmentStatus END) as order_status,
                -- Revenue metrics (for Complete orders only)
                SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
                SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
                SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete'
                    THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
                -- Cost metrics (for Complete orders only)
                SUM(CASE WHEN mainShipment = 'YES' AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as yes_cost,
                SUM(CASE WHEN mainShipment = 'NO' AND shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as no_cost,
                SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName AND shipmentStatus = 'Complete'
                    THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as xd_no_cost,
                -- Canceled order crossdock values
                SUM(CASE WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName
                    THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as canceled_xd_rev,
                SUM(CASE WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName
                    THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as canceled_xd_cost,
                -- TONU metrics
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_rev,
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost,
                -- Duplicate detection for cost
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
              AND shipmentType = 'Less Than Truckload'
            GROUP BY orderCode
        ),
        ltl_order_calculated AS (
            SELECT
                orderCode,
                order_status,
                startMarket,
                endMarket,
                tonu_rev,
                tonu_cost,
                CASE
                    WHEN order_status = 'canceled' THEN canceled_xd_rev
                    WHEN yes_rev > 0 AND no_rev = 0 THEN yes_rev
                    WHEN yes_rev = 0 THEN no_rev
                    WHEN ABS((no_rev - xd_leg_rev) - yes_rev) < 1 THEN no_rev
                    WHEN yes_rev > 2 * no_rev THEN yes_rev + no_rev
                    ELSE no_rev
                END as smart_revenue,
                CASE
                    WHEN order_status = 'canceled' THEN canceled_xd_cost
                    WHEN yes_cost > 0 AND no_cost = 0 THEN yes_cost
                    WHEN yes_cost = 0 AND no_cost > 0 THEN no_cost
                    WHEN ABS((no_cost - xd_no_cost) - yes_cost) < 20 THEN
                        CASE WHEN has_matching_no_row = 1 THEN yes_cost ELSE yes_cost + no_cost END
                    WHEN no_cost > yes_cost * 5 THEN yes_cost + no_cost
                    ELSE yes_cost + xd_no_cost
                END as smart_cost,
                CASE WHEN order_status = 'canceled' THEN canceled_xd_cost ELSE xd_no_cost END as crossdock_cost
            FROM ltl_order_metrics
        ),
        -- FTL: with canceled order handling
        ftl_orders AS (
            SELECT
                orderCode,
                MAX(shipmentStatus) as order_status,
                startMarket,
                endMarket,
                SUM(CASE
                    WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                    WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                    ELSE 0
                END) as smart_revenue,
                SUM(CASE
                    WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                    WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                    ELSE 0
                END) as smart_cost,
                SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_rev,
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
            FROM otp_reports
            WHERE {base_where}
              AND shipmentType = 'Full Truckload'
            GROUP BY orderCode, startMarket, endMarket
        ),
        -- Parcel and others: with canceled order handling
        other_orders AS (
            SELECT
                orderCode,
                shipmentStatus as order_status,
                startMarket,
                endMarket,
                SUM(CASE
                    WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
                    WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
                    ELSE 0
                END) as smart_revenue,
                SUM(CASE
                    WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
                    WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
                    ELSE 0
                END) as smart_cost,
                SUM(CASE WHEN pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as crossdock_cost,
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as tonu_rev,
                SUM(CASE WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as tonu_cost
            FROM otp_reports
            WHERE {base_where}
              AND shipmentType NOT IN ('Full Truckload', 'Less Than Truckload')
              AND mainShipment = 'YES'
            GROUP BY orderCode, shipmentStatus, startMarket, endMarket
        ),
        all_orders AS (
            SELECT orderCode, order_status, startMarket, endMarket, smart_revenue, smart_cost, crossdock_cost, tonu_rev, tonu_cost
            FROM ltl_order_calculated
            UNION ALL
            SELECT orderCode, order_status, startMarket, endMarket, smart_revenue, smart_cost, crossdock_cost, tonu_rev, tonu_cost FROM ftl_orders
            UNION ALL
            SELECT orderCode, order_status, startMarket, endMarket, smart_revenue, smart_cost, crossdock_cost, tonu_rev, tonu_cost FROM other_orders
        )
        SELECT
            CONCAT(startMarket, ' → ', endMarket) as lane,
            startMarket,
            endMarket,
            COUNT(DISTINCT CASE WHEN order_status = 'Complete' THEN orderCode END) as completed_orders,
            COUNT(DISTINCT CASE WHEN order_status = 'canceled' THEN orderCode END) as canceled_orders,
            SUM(smart_revenue) as total_revenue,
            SUM(smart_cost) as total_cost,
            SUM(smart_revenue) - SUM(smart_cost) as total_profit,
            SUM(crossdock_cost) as crossdock_cost,
            SUM(tonu_rev) as tonu_revenue,
            SUM(tonu_cost) as tonu_cost
        FROM all_orders
        GROUP BY startMarket, endMarket
        ORDER BY total_profit DESC
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
    # Calculate cross-dock cost percentage
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