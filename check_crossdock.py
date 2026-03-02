import mysql.connector
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    port=int(os.getenv("DB_PORT", 3306))
)

# Check if get_customers query returns results
query = """
SELECT DISTINCT clientName
FROM otp_reports
WHERE clientName IS NOT NULL AND clientName != ''
ORDER BY clientName
LIMIT 20
"""
cursor = conn.cursor()
cursor.execute(query)
results = cursor.fetchall()
print("Sample customers from database:")
for r in results:
    print(f"  - {r[0]}")
print(f"\nTotal returned: {len(results)}")

cursor.close()
conn.close()

