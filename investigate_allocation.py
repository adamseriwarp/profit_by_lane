"""
Investigation: How are revenueAllocationNumber and costAllocationNumber used?

Key questions:
1. Do mainShipment=No rows have allocation numbers?
2. Do allocation numbers sum to 1 across legs within a shipment?
3. Are they evenly distributed or specific?
"""
import pandas as pd
from db_connection import execute_query

# Query 1: Check NULL/empty allocation numbers by mainShipment status
print("=" * 80)
print("QUERY 1: Allocation number presence by mainShipment status")
print("=" * 80)

q1 = """
SELECT 
    mainShipment,
    COUNT(*) as total_rows,
    SUM(CASE WHEN revenueAllocationNumber IS NULL THEN 1 ELSE 0 END) as rev_null,
    SUM(CASE WHEN revenueAllocationNumber = 0 THEN 1 ELSE 0 END) as rev_zero,
    SUM(CASE WHEN revenueAllocationNumber > 0 THEN 1 ELSE 0 END) as rev_has_value,
    SUM(CASE WHEN costAllocationNumber IS NULL THEN 1 ELSE 0 END) as cost_null,
    SUM(CASE WHEN costAllocationNumber = 0 THEN 1 ELSE 0 END) as cost_zero,
    SUM(CASE WHEN costAllocationNumber > 0 THEN 1 ELSE 0 END) as cost_has_value
FROM otp_reports
GROUP BY mainShipment
"""
df1 = execute_query(q1)
print(df1.to_string())

# Query 2: For shipments with multiple legs, do allocation numbers sum to 1?
print("\n" + "=" * 80)
print("QUERY 2: Sum of allocation numbers per shipmentWarpId (sample of multi-leg shipments)")
print("=" * 80)

q2 = """
SELECT
    shipmentWarpId,
    COUNT(*) as num_legs,
    SUM(revenueAllocationNumber) as total_rev_alloc,
    SUM(costAllocationNumber) as total_cost_alloc
FROM otp_reports
WHERE shipmentWarpId IS NOT NULL
GROUP BY shipmentWarpId
HAVING COUNT(*) > 1
LIMIT 20
"""
df2 = execute_query(q2)
if df2 is not None:
    print(df2.to_string())
else:
    print("Query returned None")

# Query 3: Distribution of allocation number values
print("\n" + "=" * 80)
print("QUERY 3: Distribution of revenueAllocationNumber values")
print("=" * 80)

q3 = """
SELECT 
    revenueAllocationNumber,
    COUNT(*) as count
FROM otp_reports
WHERE revenueAllocationNumber IS NOT NULL
GROUP BY revenueAllocationNumber
ORDER BY count DESC
LIMIT 20
"""
df3 = execute_query(q3)
if df3 is not None:
    print(df3.to_string())
else:
    print("Query returned None")

# Query 4: Look at specific examples where allocation appears intentional
print("\n" + "=" * 80)
print("QUERY 4: Examples showing allocation breakdown within shipments")
print("=" * 80)

q4 = """
SELECT 
    shipmentWarpId,
    warpId,
    mainShipment,
    revenueAllocationNumber,
    costAllocationNumber,
    totalRevenue,
    totalCost,
    ROUND(totalRevenue * COALESCE(revenueAllocationNumber, 1), 2) as allocated_revenue,
    ROUND(totalCost * COALESCE(costAllocationNumber, 1), 2) as allocated_cost
FROM otp_reports
WHERE shipmentWarpId IN (
    SELECT shipmentWarpId 
    FROM otp_reports 
    WHERE revenueAllocationNumber IS NOT NULL 
      AND revenueAllocationNumber != 1 
      AND revenueAllocationNumber != 0
    LIMIT 5
)
ORDER BY shipmentWarpId, warpId
"""
df4 = execute_query(q4)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
if df4 is not None:
    print(df4.to_string())
else:
    print("Query returned None")

# Query 5: Check if mainShipment=Yes rows have different allocation patterns
print("\n" + "=" * 80)
print("QUERY 5: Allocation values for mainShipment=Yes vs No")
print("=" * 80)

q5 = """
SELECT 
    mainShipment,
    AVG(revenueAllocationNumber) as avg_rev_alloc,
    MIN(revenueAllocationNumber) as min_rev_alloc,
    MAX(revenueAllocationNumber) as max_rev_alloc,
    AVG(costAllocationNumber) as avg_cost_alloc,
    MIN(costAllocationNumber) as min_cost_alloc,
    MAX(costAllocationNumber) as max_cost_alloc
FROM otp_reports
WHERE revenueAllocationNumber IS NOT NULL
GROUP BY mainShipment
"""
df5 = execute_query(q5)
if df5 is not None:
    print(df5.to_string())
else:
    print("Query returned None")

