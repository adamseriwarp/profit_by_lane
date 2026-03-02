import sys
sys.path.insert(0, '.')
import os
os.environ['STREAMLIT_RUNNING'] = 'false'

import pymysql
from dotenv import load_dotenv
load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', 'datahub-mysql.wearewarp.link'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER', 'datahub-read'),
    password=os.getenv('DB_PASSWORD', 'warpdbhub2'),
    database=os.getenv('DB_NAME', 'datahub'),
    connect_timeout=30
)
import pandas as pd

# Check EWR -> EWR with Smart Strategy calculation
query = """
WITH order_metrics AS (
    SELECT
        orderCode,
        SUM(CASE WHEN mainShipment = 'YES' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as yes_rev,
        SUM(CASE WHEN mainShipment = 'NO' THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as no_rev,
        SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0) ELSE 0 END) as xd_leg_rev,
        SUM(CASE WHEN mainShipment = 'YES' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as yes_cost,
        SUM(CASE WHEN mainShipment = 'NO' THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as no_cost,
        SUM(CASE WHEN mainShipment = 'NO' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0) ELSE 0 END) as xd_no_cost,
        COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN startMarket END), 'NA') as startMarket,
        COALESCE(MAX(CASE WHEN mainShipment = 'YES' THEN endMarket END), 'NA') as endMarket
    FROM otp_reports
    WHERE shipmentStatus = 'Complete' AND shipmentType = 'Less Than Truckload'
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
        END as smart_cost
    FROM order_metrics
)
SELECT
    CONCAT(startMarket, ' -> ', endMarket) as lane,
    COUNT(*) as orders,
    SUM(smart_revenue) as revenue,
    SUM(smart_cost) as cost,
    SUM(smart_revenue) - SUM(smart_cost) as profit
FROM order_calculated
WHERE startMarket = endMarket AND startMarket IN ('EWR', 'LAX', 'SEA')
GROUP BY startMarket, endMarket
ORDER BY profit ASC
"""
df = pd.read_sql(query, conn)
print("Same-market lanes with Smart Strategy V3:")
print(df.to_string())
conn.close()

