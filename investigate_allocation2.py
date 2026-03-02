"""
Check lane fields availability: pickCity/dropCity vs startMarket/endMarket
"""
import pymysql
import os
from dotenv import load_dotenv
import pandas as pd

load_dotenv()
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 400)

output = []

conn = pymysql.connect(
    host=os.getenv('DB_HOST'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME'),
    cursorclass=pymysql.cursors.DictCursor
)
cursor = conn.cursor()

FILTER = """shipmentType = 'Less Than Truckload'
AND shipmentStatus = 'Complete'
AND STR_TO_DATE(dropDateArrived, '%m/%d/%Y') >= '2025-01-01'"""

output.append("=" * 100)
output.append("CHECKING LANE FIELD AVAILABILITY")
output.append("=" * 100)

# Check which fields exist and have data
output.append("\n--- Field availability (all rows) ---")
cursor.execute(f"""
SELECT
    COUNT(*) as total_rows,
    SUM(CASE WHEN pickCity IS NOT NULL AND pickCity != '' THEN 1 ELSE 0 END) as has_pickCity,
    SUM(CASE WHEN dropCity IS NOT NULL AND dropCity != '' THEN 1 ELSE 0 END) as has_dropCity,
    SUM(CASE WHEN startMarket IS NOT NULL AND startMarket != '' THEN 1 ELSE 0 END) as has_startMarket,
    SUM(CASE WHEN endMarket IS NOT NULL AND endMarket != '' THEN 1 ELSE 0 END) as has_endMarket
FROM otp_reports
WHERE {FILTER}
""")
avail = cursor.fetchone()
total = avail['total_rows']
output.append(f"Total rows: {total:,}")
output.append(f"pickCity:    {avail['has_pickCity']:,} ({avail['has_pickCity']/total*100:.1f}%)")
output.append(f"dropCity:    {avail['has_dropCity']:,} ({avail['has_dropCity']/total*100:.1f}%)")
output.append(f"startMarket: {avail['has_startMarket']:,} ({avail['has_startMarket']/total*100:.1f}%)")
output.append(f"endMarket:   {avail['has_endMarket']:,} ({avail['has_endMarket']/total*100:.1f}%)")

# Check by mainShipment type
output.append("\n--- Field availability by mainShipment ---")
cursor.execute(f"""
SELECT
    mainShipment,
    COUNT(*) as total_rows,
    SUM(CASE WHEN pickCity IS NOT NULL AND pickCity != '' THEN 1 ELSE 0 END) as has_pickCity,
    SUM(CASE WHEN dropCity IS NOT NULL AND dropCity != '' THEN 1 ELSE 0 END) as has_dropCity,
    SUM(CASE WHEN startMarket IS NOT NULL AND startMarket != '' THEN 1 ELSE 0 END) as has_startMarket,
    SUM(CASE WHEN endMarket IS NOT NULL AND endMarket != '' THEN 1 ELSE 0 END) as has_endMarket
FROM otp_reports
WHERE {FILTER}
GROUP BY mainShipment
""")
for row in cursor.fetchall():
    t = row['total_rows']
    output.append(f"\nmainShipment = '{row['mainShipment']}':")
    output.append(f"  Total rows: {t:,}")
    output.append(f"  pickCity:    {row['has_pickCity']:,} ({row['has_pickCity']/t*100:.1f}%)")
    output.append(f"  dropCity:    {row['has_dropCity']:,} ({row['has_dropCity']/t*100:.1f}%)")
    output.append(f"  startMarket: {row['has_startMarket']:,} ({row['has_startMarket']/t*100:.1f}%)")
    output.append(f"  endMarket:   {row['has_endMarket']:,} ({row['has_endMarket']/t*100:.1f}%)")

# Show sample values
output.append("\n--- Sample values ---")
cursor.execute(f"""
SELECT orderId, mainShipment, pickCity, dropCity, startMarket, endMarket
FROM otp_reports
WHERE {FILTER}
LIMIT 10
""")
df = pd.DataFrame(cursor.fetchall())
output.append(df.to_string())

# Count unique values
output.append("\n--- Unique lane combinations ---")
cursor.execute(f"""
SELECT
    COUNT(DISTINCT CONCAT(pickCity, ' -> ', dropCity)) as city_lanes,
    COUNT(DISTINCT CONCAT(startMarket, ' -> ', endMarket)) as market_lanes
FROM otp_reports
WHERE {FILTER}
""")
uniq = cursor.fetchone()
output.append(f"City lanes (pickCity -> dropCity):     {uniq['city_lanes']:,}")
output.append(f"Market lanes (startMarket -> endMarket): {uniq['market_lanes']:,}")

# Show top market lanes by volume
output.append("\n--- Top 15 market lanes by volume ---")
cursor.execute(f"""
SELECT
    CONCAT(startMarket, ' -> ', endMarket) as lane,
    COUNT(*) as shipments,
    SUM(COALESCE(revenueAllocationNumber, 0)) as revenue,
    SUM(COALESCE(costAllocationNumber, 0)) as cost,
    SUM(COALESCE(revenueAllocationNumber, 0)) - SUM(COALESCE(costAllocationNumber, 0)) as profit
FROM otp_reports
WHERE {FILTER}
  AND startMarket IS NOT NULL AND startMarket != ''
  AND endMarket IS NOT NULL AND endMarket != ''
GROUP BY startMarket, endMarket
ORDER BY shipments DESC
LIMIT 15
""")
df = pd.DataFrame(cursor.fetchall())
df['revenue'] = df['revenue'].apply(lambda x: f"${float(x):,.0f}")
df['cost'] = df['cost'].apply(lambda x: f"${float(x):,.0f}")
df['profit'] = df['profit'].apply(lambda x: f"${float(x):,.0f}")
output.append(df.to_string())

cursor.close()
conn.close()

output_path = '/tmp/allocation_output.txt'
with open(output_path, 'w') as f:
    f.write('\n'.join(output))
print(f"Output written to {output_path}")

