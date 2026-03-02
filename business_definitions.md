# Warp Data Analyst - Business Definitions

This document captures the business logic and definitions needed for the AI Data Analyst to correctly answer questions about Warp's data.

---

## 🚀 Quick Reference (Read First!)

### Decision Tree: Which Rows to Use?

```
1. What type of report?
   ├── CUSTOMER report → Use mainShipment = 'YES' only
   └── CARRIER report → Continue to step 2

2. What shipment type? (Check shipmentType column, or ASK USER if unclear)
   ├── LTL (Less Than Truckload)
   │   ├── Single row for orderCode? → Use that row
   │   └── Multiple rows? → Use mainShipment = 'NO' rows only
   │
   └── FTL (Full Truckload)
       ├── Single YES row? → Use that row (FTL Direct)
       ├── Multiple YES rows on same loadId? → Sum ALL YES rows (FTL Multidrop)
       └── 1 YES + NO rows? → Sum ALL rows (FTL Multistop)

3. For REVENUE/COST calculations:
   └── SAFEST: Always use orders.revenueAllocation (avoids double counting)
```

### Critical Warnings ⚠️

| Warning | Details |
|---------|---------|
| **LTL Double Counting** | For LTL multi-leg, YES row revenue DUPLICATES NO row revenue. Never sum both! |
| **profitNumber Column** | DO NOT USE - has data quality issues. Calculate as `revenueAllocationNumber - costAllocationNumber` |
| **Date Format** | Most dates are `MM/DD/YYYY HH:MM:SS`. Use `STR_TO_DATE(field, '%m/%d/%Y %H:%i:%s')` |

### Key Column Quick Reference

| Need | Column | Table |
|------|--------|-------|
| Shipment type (LTL/FTL) | `shipmentType` | otp_reports |
| Customer name | `clientName` | otp_reports |
| Carrier name | `carrierName` | otp_reports |
| Order ID | `orderCode` | otp_reports |
| Revenue | `revenueAllocationNumber` | otp_reports |
| Cost | `costAllocationNumber` | otp_reports |
| Order-level revenue (SAFE) | `revenueAllocation` | orders |
| Pickup time | `pickTimeArrived` | otp_reports |
| Delivery time | `dropTimeArrived` | otp_reports |
| Scheduled pickup window | `pickWindowFrom`, `pickWindowTo` | otp_reports |
| Scheduled delivery window | `dropWindowFrom`, `dropWindowTo` | otp_reports |

### When to Ask the User

If the user's question is ambiguous, ASK before proceeding:

| Ambiguity | Ask |
|-----------|-----|
| No shipment type specified | "Are you asking about LTL, FTL, or all shipment types?" |
| No perspective specified | "Is this a carrier performance report or customer-facing report?" |
| Date range unclear | "What date range would you like me to use?" |
| Lane granularity unclear | "Should I group by market (LAX → EWR) or city-state?" |

---

## Database Overview
- **Database**: `datahub` (MySQL)
- **Key Tables**: `otp_reports`, `shipments`, `orders`, `routes`, `quotes`, `carriers`, `clients`

---

## Table Relationships & Join Keys

### All Tables in datahub (17 total)

| Table | ~Rows | Purpose |
|-------|-------|---------|
| **quotes** | 8.7M | Quote history for pricing |
| **tasks** | 1.1M | Internal task tracking |
| **otp_reports** | 675k | **PRIMARY TABLE** - Denormalized shipment performance view |
| **shipments** | 628k | Core shipment data |
| **orders** | 304k | Order-level data with revenue/cost |
| **routes** | 262k | Route/load assignments to carriers |
| **warehouses** | 130k | Warehouse/cross-dock locations |
| **shipment_in_crossdocks** | 58k | Cross-dock handling records |
| **carriers** | 34k | Carrier master data |
| **users** | 26k | User accounts |
| **doordash_shipments** | 7k | DoorDash-specific shipments |
| **doordash_location_statistic** | 1.3k | DoorDash location stats |
| **clients** | 673 | Customer master data |
| **freight_quote_histories** | 409 | Historical quote records |
| **doordash_ontime_rate** | 64 | DoorDash OTD metrics |
| **doordash_blended_cost** | 58 | DoorDash cost metrics |
| **update_infos** | 4 | Sync/update tracking |

### Key Identifier Patterns

| ID Type | Format | Example | Description |
|---------|--------|---------|-------------|
| **orderCode** | `P-XXXXX-YYYY` or `O-XXXX-YYYY` | `P-02078-2452`, `O-0169-2408` | Parent order ID |
| **warpId** | `S-XXXXXX` | `S-385753` | Individual shipment ID |
| **loadId/routeId** | `XXXX-YYYY` | `1513-2445` | Route/load identifier |
| **carriers.id** | 26-char ULID | `01H22NK3ZB2Q...` | Carrier unique ID |
| **clients.id** | 26-char ULID | `01H22NK3JZ9E...` | Client unique ID |

### Verified Join Relationships

```
otp_reports (PRIMARY - use for most queries)
    ├── orderCode → orders.code ✓ (1:1 per order)
    ├── loadId → routes.routeId ✓ (1:1 per route)
    ├── carrierName → carriers.name ✓ (use name, not ID)
    └── clientName (text) → NO direct FK (clientId is internal)

orders (order-level data)
    ├── code = orderCode
    ├── warpId = numeric portion of S-XXXXXX
    └── customerName, revenueAllocation, costAllocation

routes (route/load data)
    ├── routeId = loadId
    ├── carrierName, carrierId
    ├── shipmentWarpIds (JSON array of numeric warpIds)
    └── shipmentCodes (JSON array of S-XXXXX codes)

shipments (individual legs)
    ├── warpId = S-XXXXXX
    ├── parent = links to another S-XXXXXX (NOT orderCode!)
    └── code = S-XXXXX-YYYY format

carriers
    ├── id = 26-char ULID (NOT used in joins)
    └── name = carrier name (USE THIS for joins)

clients
    ├── id = 26-char ULID (NOT used in joins)
    └── name = client/customer name
```

### ⚠️ Important Join Warnings

1. **otp_reports.warpId does NOT directly join to shipments.warpId**
   - Different formats: otp_reports has `S-10000`, shipments uses numeric warpId internally

2. **otp_reports.clientId does NOT join to clients.id**
   - `clientId` in otp_reports is an internal integer
   - `clients.id` is a 26-char ULID
   - Use `clientName` for text-based matching instead

3. **shipments.parent is NOT orders.code**
   - `parent` in shipments links to another shipment (S-XXXXX), not an order
   - To link shipments to orders, use the order code pattern in shipment codes

4. **carriers and clients use name-based joins**
   - Join via `carrierName = carriers.name` or `clientName = clients.name`
   - NOT via the ID fields (different data types)

### Recommended Table Usage

| Need | Use This Table | Join Key |
|------|---------------|----------|
| **Performance reports (OTP/OTD)** | `otp_reports` | N/A (denormalized) |
| **Order-level revenue/cost** | `orders` | `orders.code = otp_reports.orderCode` |
| **Route/carrier assignments** | `routes` | `routes.routeId = otp_reports.loadId` |
| **Carrier master data** | `carriers` | `carriers.name = otp_reports.carrierName` |
| **Client master data** | `clients` | `clients.name = otp_reports.clientName` |
| **Quote history/pricing** | `quotes` | `quotes.customer` (text match) |
| **Cross-dock details** | `shipment_in_crossdocks` | `shipmentId` column |

### otp_reports Column Categories (111 columns)

**Identifiers:**
- `id`, `warpId`, `orderCode`, `orderId`, `code`, `loadId`, `clientId`

**Customer/Carrier Info:**
- `clientName`, `carrierName`, `driverName`, `carrierEmail`, `carrierPhone`
- `accountOwner`, `assignedSeller`, `salesRep`, `clientSuccessRep`, `carrierSaleRep`

**Pickup Data:**
- `pickAddress`, `pickCity`, `pickState`, `pickZipcode`, `pickLocationName`, `pickLocationType`
- `pickWindowFrom`, `pickWindowTo` (scheduled window)
- `pickTimeArrived`, `pickTimeDeparted`, `pickDateArrived` (actual times)
- `pickStatus`, `pickupDelayCode`
- `pickRequiresAppointment`, `pickAppointmentFrom`, `pickAppointmentTo`

**Dropoff Data:**
- `dropAddress`, `dropCity`, `dropState`, `dropZipcode`, `dropLocationName`, `dropLocationType`
- `dropWindowFrom`, `dropWindowTo` (scheduled window)
- `dropTimeArrived`, `dropTimeDeparted`, `dropDateArrived` (actual times)
- `dropStatus`, `deliveryDelayCode`
- `dropRequiresAppointment`, `dropAppointmentFrom`, `dropAppointmentTo`

**Financial:**
- `revenueAllocationNumber`, `costAllocationNumber` - use these for profit calculations
- `profitNumber` - ⚠️ **DO NOT USE** - this column has data quality issues (often equals revenue instead of revenue-cost)
- `carrierRateNumber`, `customerCostNumber`, `datRate`
- `accessorialAmount`, `accessorialType`

**Shipment Classification:**
- `mainShipment` (YES/NO), `shipmentType`, `transitType`, `productType`
- `classification`, `classificationShipment`, `equipment`, `equipmentScrub`
- `loadType`, `shipmentStatus`, `loadStatus`

**Performance Metrics:**
- `isTracking`, `trackingMethod`, `trackingCoverage`
- `routeOTP`, `routeOTD`

**Time/Date:**
- `createdAt`, `updatedAt`, `createWhen`, `createWhenISOString`
- `revenueDate`, `revenueMonth`, `revenueWeekNumber`
- `bolSubmittedTime`, `loadBookedTime`

**Other:**
- `pieces`, `totalWeight`, `routeMiles`, `shipmentMiles`
- `refNums`, `cohort`, `startMarket`, `endMarket`
- `cancelReason`, `cancelNote`, `negativeMarginReason`
- `isPODConfirmed`, `isPODUploaded`, `isBarcodeRequired`

---

## Shipment Hierarchy (LTL vs FTL)

### `mainShipment` Column

The `mainShipment` column in `otp_reports` and `shipments` is a **YES/NO flag**:

| Value | Meaning | Count (approx) |
|-------|---------|----------------|
| `YES` | Main shipment - represents the customer's order | ~362k |
| `NO` | Sub-shipment - represents individual transport legs | ~361k |
| `NULL` | Unknown/legacy data | ~400 |

### Key Identifiers

| ID Type | Prefix | Example | Meaning |
|---------|--------|---------|---------|
| **orderCode** | `P-` | `P-02078-2452` | Parent/Order ID - groups all shipments for one customer order |
| **warpId** | `S-` | `S-385753` | Individual shipment/leg ID |

**CRITICAL**: One `orderCode` (P-) can have multiple `warpId` (S-) rows. The `orderCode` is the parent.

In other tables, this parent ID may be named: `parentId`, `orderId`, or `code`.

### LTL (Less Than Truckload) Structure

**Distribution**: ~157k YES rows, ~349k NO rows (ratio ~1:2)

#### ⚠️ CRITICAL: LTL Revenue Double Counting Issue

**CONFIRMED**: For LTL multi-leg orders, the `mainShipment = YES` row revenue **DUPLICATES** the revenue already on `mainShipment = NO` rows.

**Example P-0587-2409** (VERIFIED):
```
warpId       main     revenue       cost
-----------------------------------------
S-XXXXXX     YES      $85           $0      ← ORDER HEADER (revenue shown but is duplicate)
S-XXXXXX     NO       $42           $57     ← Leg 1
S-XXXXXX     NO       $43           $58     ← Leg 2
-----------------------------------------
If you SUM ALL rows:    $170 revenue   ← WRONG! Double counted!
Correct total:          $85 revenue    ← Use ONLY YES row OR ONLY NO rows, not both
```

**Example P-0906-2408** (VERIFIED):
- Summing all rows: $317 total
- Correct revenue: $158.65
- **We were double counting again!**

#### LTL Revenue Rules

| Scenario | Which Rows to Use | Why |
|----------|-------------------|-----|
| **LTL direct (single row)** | Use the single YES row | Only one row exists |
| **LTL multi-leg** | Use **ONLY NO rows** for revenue/cost | YES row duplicates revenue already on NO rows |

#### LTL Structure Example (O-0169-2408)

```
Customer View (mainShipment = YES):
  S-149126: Los Angeles → Pottsville (revenue: $0, cost: $0)  ← In this case YES = $0

Actual Transport Legs (mainShipment = NO):
  S-149128: Los Angeles → Vernon       (revenue: $133.14, cost: $65.13)  [pickup to cross-dock]
  S-149129: Vernon → Bound Brook       (revenue: $458.00, cost: $413.33) [line haul]
  S-149130: Bound Brook → Bound Brook  (revenue: $0, cost: $0)           [CROSS-DOCK HANDLING]
  S-149131: Bound Brook → Pottsville   (revenue: $58.00, cost: $24.83)   [final mile]

  TOTAL legs: $649.14 revenue, $503.29 cost
```

**Key Rules:**
- `mainShipment = YES` is the "order header" showing origin → final destination
- `mainShipment = NO` rows are the actual transport legs with revenue/cost
- **For LTL multi-leg**: Sum ONLY the NO rows for revenue/cost (YES row duplicates)

### Direct Shipments (Single Leg)

**IMPORTANT**: If a shipment goes direct (no cross-docks), there will be only ONE row with `mainShipment = YES`. In this case, that row represents BOTH the order AND the leg.

```
Direct Shipment Example:
  orderCode: P-XXXXX-XXXX
  Only row: S-XXXXXX, mainShipment = YES, Los Angeles → Chicago ($500 rev)

  This single row IS the order AND the transport leg.
```

### Cross-Dock Handling Legs

When `pickLocationName = dropLocationName` (same city/location), this is a **cross-dock handling operation**, NOT a transport leg.

- These have revenue attached (cross-dock handling fees)
- **Include in financial reports** (revenue/cost)
- **Exclude from shipment counts and OTP/OTD** (not a "real" leg)

### FTL (Full Truckload) Structure ✅ (VERIFIED)

**Distribution**: ~93k YES rows, ~6.5k NO rows (ratio ~14:1)

**Key Finding**: FTL is mostly direct shipments (single `mainShipment = YES` row), but some orders have multidrop or multistop structure.

#### FTL Multidrop vs Multistop

| Term | Pattern | mainShipment | Revenue/Cost | Example |
|------|---------|--------------|--------------|---------|
| **FTL Direct** | Single pickup → single drop | 1 YES row only | All on YES row | Most FTL orders |
| **FTL Multidrop** | Multiple separate shipments on same truck/load (truck makes multiple drops) | **Multiple YES rows** (each is separate shipment) | Each YES row has its own rev/cost | P-77056-2603 |
| **FTL Multistop** | Single order with additional services (freight stops at intermediate points) | 1 YES row + NO rows | YES = primary transport, NO = additional services | P-65893-2445 |

#### FTL Multidrop Example (P-77056-2603)
```
warpId       main   loadId       carrier          drop              revenue   cost
----------------------------------------------------------------------------------
S-1179502    YES    2571-2603    Frederick Kuri   Doordash DTX-1    $107      $90
S-1179503    YES    2571-2603    Frederick Kuri   Doordash DTX-1    $40       $33
S-1179504    YES    2571-2603    Frederick Kuri   Doordash DTX-1    $30       $25
S-1179505    YES    2571-2603    Frederick Kuri   Doordash DTX-1    $52       $44
S-1179506    YES    2571-2603    Frederick Kuri   Doordash DTX-1    $76       $63
----------------------------------------------------------------------------------
TOTAL (5 separate shipments on same load):                          $305      $255
```
**Key**: Each row is a **separate shipment** with its own warpId. These are NOT duplicates - they are 5 different drops on the same truck route. Sum ALL YES rows for total revenue/cost.

#### FTL Multistop Example (P-65893-2445) ✅ VERIFIED - SEPARATE SERVICES
```
warpId       main   carrier              pickup           drop          revenue   cost
---------------------------------------------------------------------------------------
S-339347     YES    BMX TRANSPORT LLC    GoPuff-DC1       WTCH-ATL-2    $1,529    $1,510  ← Long-haul (NJ→GA)
S-443507     NO     Best Warehousing     WTCH-ATL-2       WTCH-ATL-2    $0        $440    ← Cross-dock handling
S-339349     NO     FIRST TO FINAL LOG   WTCH-ATL-1       WTCH-ATL-2    $500      $400    ← Final mile (Marietta→Atlanta)
---------------------------------------------------------------------------------------
TOTAL (sum ALL rows):                                                   $2,029    $2,350
```
**✅ CONFIRMED**: These are **SEPARATE SERVICES**, not duplicates:
- **YES row**: Primary long-haul transport (Cherry Hill, NJ → Atlanta, GA)
- **NO row (Best Warehousing)**: Cross-dock handling at destination (same pickup/drop = handling fee)
- **NO row (FIRST TO FINAL)**: Final mile delivery from WTCH-ATL-1 to WTCH-ATL-2 (different pickup!)

**For FTL Multistop**: Sum ALL rows (YES + NO) for total revenue/cost.

**Contrast with LTL**: LTL multi-leg has revenue on NO rows, FTL typically has revenue on YES row.

---

## Deduplication and mainShipment Interaction

### VERIFIED: Does deduplication handle mainShipment = YES rows correctly?

**Answer: YES, effectively 100% when filtering for completed work.**

Analysis of 100 orders with both `mainShipment = YES` and `NO` rows:

| Scenario | % of orders | Pickup Dedup | Delivery Dedup |
|----------|-------------|--------------|----------------|
| Standard multi-leg | **97%** | ✅ | ✅ |
| Cross-dock only legs | **3%** | ❌ | ✅ |

**The 3% edge cases are NOT a problem** because:
- They are cross-dock-only orders (NO rows have same pickup/dropoff location)
- The NO rows have `shipmentStatus = 'Pending'` or `'Removed'` (incomplete work)
- These should be filtered out anyway - they're not completed carrier operations

**Solution**: Filter by `shipmentStatus = 'Complete'` OR `pickStatus = 'Succeeded'` to exclude incomplete legs.

**Why deduplication works:**
- The `mainShipment = YES` row has the **same pickup location** as the first leg (`mainShipment = NO`)
- The `mainShipment = YES` row has the **same dropoff location** as the last leg (`mainShipment = NO`)
- Therefore, when we deduplicate by `loadId + carrierName + pickLocationName + pickDate`, the YES row matches a NO row

**Example (Order O-0169-2408):**
```
YES row pickup: "Sak's Store 816 - Beverly Connection - Los Angeles"
First leg (NO) pickup: "Sak's Store 816 - Beverly Connection - Los Angeles"  ← MATCH!

YES row dropoff: "Sak's OFF 5TH - DC 593/789 ECOM (MDT1)"
Last leg (NO) dropoff: "Sak's OFF 5TH - DC 593/789 ECOM (MDT1)"  ← MATCH!
```

### The 3% Edge Case: Cross-dock Only Orders

Some orders only have cross-dock handling legs (same pickup/dropoff location):
```
YES row: GoPuff - DC1 → WTCH-MIA-2
NO rows: WTCH-MIA-2 → WTCH-MIA-2 (handling only)

The YES row pickup "GoPuff - DC1" doesn't match any NO row pickup.
```

**For carrier reports**: Filter out incomplete legs (`Pending`/`Removed` status) to achieve 100% effective deduplication.

**For customer reports**: The YES row represents the customer's view correctly, so this is fine.

---

## Carrier vs Customer Report Logic

### Summary Table

| Report Type | mainShipment Filter | Deduplication | Why |
|-------------|---------------------|---------------|-----|
| **Carrier** | None (use all rows) | Yes | Carriers accountable for every leg |
| **Customer** | `= 'YES'` only | No | Customers see order-level view |

### Carrier Reports ✅ (CONFIRMED)

**Purpose**: Measure carrier performance on every leg they operated.

#### LTL Carrier Shipment Counting (RECOMMENDED APPROACH)

For **counting shipments** (not revenue), use this logic for LTL:

| Scenario | Which Rows to Count | Why |
|----------|---------------------|-----|
| **LTL multi-leg** | Use `mainShipment = NO` only | Cleaner - avoids potential double counting |
| **LTL direct (single row)** | Use the single `mainShipment = YES` row | Only one row exists for the orderCode |
| **FTL** | Use all rows with deduplication | Standard approach works |

**Exception Rule**: If only one row exists for an orderCode AND it's `mainShipment = YES`, count it. This handles LTL direct shipments.

```sql
-- LTL Carrier Shipment Counting Logic
WITH order_row_counts AS (
    SELECT orderCode,
           COUNT(*) as total_rows,
           SUM(CASE WHEN mainShipment = 'YES' THEN 1 ELSE 0 END) as yes_count
    FROM otp_reports
    WHERE carrierName = 'YOUR_CARRIER'
    GROUP BY orderCode
)
SELECT o.*
FROM otp_reports o
JOIN order_row_counts orc ON o.orderCode = orc.orderCode
WHERE o.carrierName = 'YOUR_CARRIER'
  AND (
    -- Multi-leg: use NO rows only
    (orc.total_rows > 1 AND o.mainShipment = 'NO')
    -- Single row: use whatever exists (should be YES)
    OR orc.total_rows = 1
  )
```

#### General Carrier Logic (from `query_otp_clean.py`):
- **Include ALL rows** - both `mainShipment = YES` and `NO`
- **No mainShipment filter** - explicitly noted in code: `"# Apply deduplication for delivery (no mainShipment filter)"`
- **Use deduplication** to prevent double-counting:
  - `keep_for_pickup` flag: dedup by `loadId + carrierName + pickLocationName + pickDate`
  - `keep_for_delivery` flag: dedup by `loadId + carrierName + dropLocationName + dropDate`
- **Filter by status**: `shipmentStatus = 'Complete'` OR `pickStatus = 'Succeeded'` (excludes incomplete/cancelled legs)

**Why deduplication also works**:
- Deduplication handles the mainShipment=YES row overlap
- Filtering out `Pending`/`Removed` status legs achieves 100% effective deduplication
- But using `mainShipment = NO` for LTL upfront is **cleaner and safer**

### Customer Reports ✅ (CONFIRMED)

**Purpose**: Show customers their order-level performance (origin → final destination).

**Logic**:
- **Use `mainShipment = YES` rows only**
- **No deduplication needed** (each order has one YES row)
- **Filter by status**: `shipmentStatus = 'Complete'` (NOT `dropStatus` - often NULL on YES rows)

**Why this works for customers**:
- The YES row represents the customer's view: "My stuff was picked up at Store X and delivered to Warehouse Y"
- Customers don't need to see intermediate cross-dock hops
- For direct shipments, the single YES row IS both the order and the leg

### Time Data on YES Rows ✅ (VERIFIED)

**Answer**: YES, `mainShipment = YES` rows DO have time data populated!

**Aggregate Stats:**
| mainShipment | Total | Has PickTime | Has DropTime |
|--------------|-------|--------------|--------------|
| **YES** | 362,178 | 294,317 (81.3%) | 302,122 (83.4%) |
| **NO** | 361,200 | 245,037 (67.8%) | 230,751 (63.9%) |

**Example (Order O-0169-2408):**
```
S-149126 | YES | pick=02/20/2024 11:09:27 | drop=02/27/2024 10:09:40 | pStatus=NULL
S-149128 | NO  | pick=02/20/2024 11:09:27 | drop=02/20/2024 11:28:03 | pStatus=Succeeded
S-149131 | NO  | pick=02/27/2024 06:00:15 | drop=02/27/2024 10:09:40 | dStatus=Succeeded
```

**Key Observations:**
- YES row `pickTimeArrived` = first leg's pickup time
- YES row `dropTimeArrived` = last leg's delivery time
- ⚠️ YES rows often have `pickStatus = NULL` and `dropStatus = NULL`

**Implication for Customer Reports:**
- ✅ Can use YES row times directly for OTP/OTD calculations
- ✅ Filter by `shipmentStatus = 'Complete'` (NOT `dropStatus = 'Succeeded'`)

---

## Revenue & Cost Calculations

### Canceled Order Handling

**Rule**: Include canceled orders **only if** they have crossdock activity (incurred costs/revenue).

#### Identification

A **crossdock leg** is identified by: `pickLocationName = dropLocationName`

#### Logic

| Order Status | Has Crossdock Leg? | Tracking |
|--------------|-------------------|----------|
| `Complete` | N/A | Track ALL costs/revenue (Hybrid Approach) |
| `canceled` | YES | Track ONLY crossdock leg costs/revenue |
| `canceled` | NO | **Exclude** from all metrics |
| `removed` | N/A | **Exclude** from all metrics |

#### Implementation

```sql
-- Base WHERE clause
WHERE (
    shipmentStatus = 'Complete'
    OR (
        shipmentStatus = 'canceled'
        AND EXISTS (
            SELECT 1 FROM otp_reports o2
            WHERE o2.orderCode = otp_reports.orderCode
              AND o2.pickLocationName = o2.dropLocationName
        )
    )
)
AND shipmentStatus != 'removed'
```

#### Revenue/Cost Extraction for Canceled Orders

For canceled orders with crossdock activity:
- **Revenue**: Sum revenue from crossdock legs only (`pickLocationName = dropLocationName`)
- **Cost**: Sum cost from crossdock legs only (`pickLocationName = dropLocationName`)
- **Lane**: Determined by `mainShipment = 'YES'` row (as usual)

```sql
-- For canceled orders, only count crossdock leg values
SUM(CASE
    WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
    WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName
        THEN COALESCE(revenueAllocationNumber, 0)
    ELSE 0
END) as revenue
```

#### Order Counting

Track completed and canceled orders separately per lane:

```sql
COUNT(DISTINCT CASE WHEN shipmentStatus = 'Complete' THEN orderCode END) as completed_orders,
COUNT(DISTINCT CASE WHEN shipmentStatus = 'canceled' THEN orderCode END) as canceled_orders
```

**Display**: Show as "X completed + Y canceled" or in separate columns.

---

### TONU (Truck Order Not Used) Handling ✅ (UPDATED)

**Definition**: TONU occurs when a carrier is dispatched but the load is canceled. The carrier gets paid (cost incurred) but typically no delivery revenue is generated.

#### Identification

A TONU is identified by: `accessorialType = 'TONU'` on any row for an order.

- TONU is **per order** (attached to an `orderCode`)
- The TONU row is typically on a `mainShipment = 'NO'` row
- An order can have both regular shipment activity AND a TONU charge

#### ⚠️ TONU Status Handling (CRITICAL)

**TONU rows must be included regardless of `shipmentStatus`.**

TONU charges may have a `shipmentStatus` that is NOT 'Complete' or 'canceled'. To capture all TONU charges, the WHERE clause must explicitly include them:

```sql
-- OLD (misses some TONU):
WHERE shipmentStatus IN ('Complete', 'canceled')

-- NEW (includes all TONU):
WHERE (shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')
```

#### Treatment in Profitability

- **TONU revenue/cost IS included** in total profit calculations
- TONU charges are allocated to the lane based on THAT row's `startMarket → endMarket`
- **TONU is tracked separately** as a summary statistic for visibility
- **Display**: Show "TONU Revenue", "TONU Cost", and "TONU Orders" as separate metrics

---

### Crossdock Leg Revenue ⚠️ (otp_reports is MORE correct)

**Discovery**: Crossdock legs (`pickLocationName = dropLocationName`) have revenue in `otp_reports` that the `orders` table is missing.

- **33,568 orders** have crossdock leg revenue
- **$1,513,356.86** total crossdock leg revenue
- This is **legitimate revenue** that should be included

**Example (P-43313-2548):**
```
warpId       main  pick→drop              revenue    Note
---------------------------------------------------------------------------
S-1039641    YES   CMH→LAX (DHL→ARITZIA)  $0.00      Header
S-1039781    NO    LAX→LAX (WTCH→ARITZIA) $300.00    Final delivery
S-1039783    NO    LAX→LAX (WTCH→WTCH)    $66.00     ← Crossdock leg (orders table missing this!)
---------------------------------------------------------------------------
otp_reports total: $366.00
orders table:      $300.00  ← Missing the $66 crossdock charge
```

### FTL (Full Truckload) ✅ (VERIFIED)

**Distribution**: ~115k YES rows, ~11k NO rows (mostly direct shipments)

**Pattern**: `mainShipment = YES` row has the **full revenue/cost**, NO rows typically have **$0**.

**Example (FTL Order P-0621-2408):**
```
warpId       main     revenue       cost   pick           drop
------------------------------------------------------------------------
S-150604     YES      1050.00     900.00   Moonachie      Wilkes-Barre   ← FULL AMOUNT
S-150606     NO          0.00       0.00   Moonachie      Bound Brook    ← $0
S-150607     NO          0.00       0.00   Bound Brook    Bound Brook    ← $0 (cross-dock)
S-150608     NO          0.00       0.00   Bound Brook    Wilkes-Barre   ← $0
```

### Source of Truth Summary

| Data | Source of Truth | Notes |
|------|-----------------|-------|
| **Revenue** | `otp_reports` (Row-Level) | Each row's `revenueAllocationNumber` → that row's lane |
| **Cost** | `otp_reports` (Row-Level) | Each row's `costAllocationNumber` → that row's lane |
| **Lane Definition** | Each row's own `startMarket → endMarket` | Row-level allocation, not order-level |
| **Per-leg detail** | `mainShipment = NO` rows | Individual leg metrics |

> **Note**: The `orders` table was previously considered the source of truth, but analysis shows that `otp_reports` contains more complete/accurate data for both revenue and cost, particularly for LTL shipments with crossdocks or multiple legs.

### Revenue/Cost Counting Rules ✅ (ROW-LEVEL ALLOCATION)

**IMPORTANT**: Each row's revenue and cost is allocated to THAT row's `startMarket → endMarket` lane.

| Scenario | Revenue | Cost |
|----------|---------|------|
| **All rows** | `revenueAllocationNumber` → row's lane | `costAllocationNumber` → row's lane |
| **Complete orders** | Full revenue counted | Full cost counted |
| **Canceled orders** | Only crossdock leg revenue | Only crossdock leg cost |
| **TONU charges** | Included regardless of status | Included regardless of status |

### Lane Definition for Multi-Leg Orders ✅ (ROW-LEVEL)

For LTL multi-leg orders, **each row** contributes to its OWN lane:
- Each row's revenue → that row's `startMarket → endMarket` lane
- Each row's cost → that row's `startMarket → endMarket` lane

**Example**: Dallas → Houston order with crossdock:
| Row | Type | startMarket | endMarket | Revenue | Cost | Lane Allocated |
|-----|------|-------------|-----------|---------|------|----------------|
| 1 | YES | Dallas | Houston | $1,200 | $0 | Dallas → Houston |
| 2 | NO (leg) | Dallas | Crossdock | $0 | $300 | Dallas → Crossdock |
| 3 | NO (leg) | Crossdock | Houston | $0 | $500 | Crossdock → Houston |

This gives visibility into which **specific legs** are profitable vs costly.

### Relevant Columns

| Column | Table | Description |
|--------|-------|-------------|
| `revenueAllocationNumber` | otp_reports | Revenue allocated to this shipment/leg |
| `costAllocationNumber` | otp_reports | Cost allocated to this shipment/leg |
| `revenueAllocation` | orders | Total order revenue (may miss crossdock) |
| `costAllocation` | orders | Total order cost |
| `mainShipment` | otp_reports | YES = order header/lane definition, NO = individual legs |
| `pickLocationName` | otp_reports | Used to identify crossdock legs (pick = drop) |
| `dropLocationName` | otp_reports | Used to identify crossdock legs (pick = drop) |

---

### Row-Level Lane Allocation ✅ (CURRENT APPROACH)

**Purpose**: Allocate each row's revenue and cost to THAT row's `startMarket → endMarket` lane.

This gives visibility into which **specific legs/segments** are profitable vs costly, rather than treating the entire order as a single lane.

#### Core Logic

```sql
-- Simple row-level aggregation by lane
SELECT
    COALESCE(startMarket, 'NA') as startMarket,
    COALESCE(endMarket, 'NA') as endMarket,
    -- Revenue: each row's revenueAllocationNumber to its own lane
    SUM(CASE
        WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
        WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
        WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
        ELSE 0
    END) as total_revenue,
    -- Cost: each row's costAllocationNumber to its own lane
    SUM(CASE
        WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
        WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
        WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
        ELSE 0
    END) as total_cost
FROM otp_reports
WHERE (shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')
  AND startMarket IS NOT NULL AND startMarket != ''
  AND endMarket IS NOT NULL AND endMarket != ''
GROUP BY startMarket, endMarket
```

#### Key Points

1. **Each row contributes to its OWN lane** based on that row's `startMarket → endMarket`
2. **TONU charges included** regardless of `shipmentStatus`
3. **Canceled orders**: Only count crossdock leg revenue/cost (where `pickLocationName = dropLocationName`)
4. **Multi-leg orders**: Costs get spread across multiple lanes (e.g., Dallas→Crossdock and Crossdock→Houston)

#### Example: Multi-Leg Order

For a Dallas → Houston LTL order with crossdock:

| Row | mainShipment | startMarket | endMarket | Revenue | Cost | Lane |
|-----|--------------|-------------|-----------|---------|------|------|
| 1 | YES | Dallas | Houston | $1,200 | $0 | Dallas → Houston |
| 2 | NO | Dallas | XD_A | $0 | $300 | Dallas → XD_A |
| 3 | NO | XD_A | Houston | $0 | $500 | XD_A → Houston |

**Result**:
- Dallas → Houston lane gets: +$1,200 revenue, $0 cost
- Dallas → XD_A lane gets: $0 revenue, +$300 cost
- XD_A → Houston lane gets: $0 revenue, +$500 cost

This reveals which segments are cost centers vs profit centers.

#### Historical Note: Hybrid Approach (Deprecated)

Previously, we used a "Hybrid Approach" that:
- Grouped by `orderCode` first
- Used leg revenue when available, fell back to main row's `total`
- Assigned entire order to the YES row's lane

This was replaced with row-level allocation to give better visibility into segment profitability.

#### Implementation Notes

1. **For ALL dashboards**: Use row-level allocation (each row's revenue/cost → that row's lane)
2. **WHERE clause**: Always include `(shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')`
3. **Canceled orders**: Only count crossdock leg values (where `pickLocationName = dropLocationName`)

---

### Lane Definition ✅ (ROW-LEVEL)

**Use `startMarket → endMarket` for lane-level analysis**, NOT `pickCity → dropCity`.

**IMPORTANT**: Each row defines its OWN lane. A multi-leg order may contribute to MULTIPLE different lanes.

#### Why Markets Instead of Cities

| Field | Unique Lanes | Coverage |
|-------|--------------|----------|
| `pickCity → dropCity` | 39,118 | 99.9% |
| `startMarket → endMarket` | **1,882** | 99.0% |

**Markets are better because:**
- Manageable number of lane combinations (1,882 vs 39,118)
- Groups nearby cities into logical regions (e.g., "LAX" includes Los Angeles area)
- More actionable for business decisions
- Uses airport codes as region identifiers (LAX, EWR, SFO, ORD, etc.)

#### Field Definitions

| Field | Description | Example |
|-------|-------------|---------|
| `startMarket` | Origin market/region code | LAX, EWR, SFO |
| `endMarket` | Destination market/region code | LAX, ORD, DFW |
| `pickCity` | Origin city name | Los Angeles, Newark |
| `dropCity` | Destination city name | San Francisco, Chicago |

#### Lane Definition SQL (Row-Level)

```sql
-- Lane = each row's startMarket → endMarket
-- Includes TONU regardless of shipmentStatus
SELECT
    startMarket,
    endMarket,
    CONCAT(startMarket, ' → ', endMarket) as lane,
    SUM(CASE
        WHEN shipmentStatus = 'Complete' THEN COALESCE(revenueAllocationNumber, 0)
        WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(revenueAllocationNumber, 0)
        WHEN accessorialType = 'TONU' THEN COALESCE(revenueAllocationNumber, 0)
        ELSE 0
    END) as lane_revenue,
    SUM(CASE
        WHEN shipmentStatus = 'Complete' THEN COALESCE(costAllocationNumber, 0)
        WHEN shipmentStatus = 'canceled' AND pickLocationName = dropLocationName THEN COALESCE(costAllocationNumber, 0)
        WHEN accessorialType = 'TONU' THEN COALESCE(costAllocationNumber, 0)
        ELSE 0
    END) as lane_cost
FROM otp_reports
WHERE (shipmentStatus IN ('Complete', 'canceled') OR accessorialType = 'TONU')
  AND startMarket IS NOT NULL AND startMarket != ''
  AND endMarket IS NOT NULL AND endMarket != ''
GROUP BY startMarket, endMarket
ORDER BY (lane_revenue - lane_cost) DESC
```

---

### Cost Structure ⚠️ (DIFFERENT FROM REVENUE)

**Critical Finding**: Cost works **completely differently** from revenue. Cost is **additive** across rows, NOT duplicated like revenue.

#### Validation Results (vs Orders Table)

| Approach | Cost Total | vs Orders Table |
|----------|------------|-----------------|
| **Orders table (truth)** | $29,109,644.89 | — |
| Main rows only | $9,066,498.82 | **-68.9%** ❌ |
| NO rows only | $20,496,903.40 | **-29.6%** ❌ |
| **ALL rows (YES + NO)** | $29,563,482.22 | **+1.6%** ✅ |

**Best Match**: Sum ALL rows (only $453,837 difference from orders table)

#### Why Cost is Different from Revenue

| Metric | Structure | Correct Approach |
|--------|-----------|------------------|
| **Revenue** | Main row has full amount, legs are a breakdown (duplicates) | Use **hybrid** (legs when available, else main) |
| **Cost** | Main row has partial cost, legs have additional costs (additive) | **Sum ALL rows** (YES + NO) |

**Why this makes sense:**
- **Revenue** = One customer payment → duplicated on main row and split across legs
- **Cost** = Multiple carrier payments → each leg has its own carrier cost that ADDS UP

#### For Lane-Level Cost Analysis

Since costs are additive (not duplicated), **sum `costAllocationNumber` from ALL rows** (`mainShipment = 'YES'` + `mainShipment = 'NO'`).

For lane attribution:
- Each `mainShipment = 'NO'` row = one lane with its `costAllocationNumber`
- `mainShipment = 'YES'` row cost = attribute to the main lane (origin → destination)

```sql
-- Total cost for an order (should match orders.costAllocation)
SELECT orderId, SUM(COALESCE(costAllocationNumber, 0)) as total_cost
FROM otp_reports
WHERE shipmentType = 'Less Than Truckload' AND shipmentStatus = 'Complete'
GROUP BY orderId

-- Lane-level cost breakdown (use startMarket/endMarket for lanes)
SELECT startMarket, endMarket,
       SUM(COALESCE(costAllocationNumber, 0)) as lane_cost
FROM otp_reports
WHERE shipmentType = 'Less Than Truckload' AND shipmentStatus = 'Complete'
  AND startMarket IS NOT NULL AND startMarket != ''
  AND endMarket IS NOT NULL AND endMarket != ''
GROUP BY startMarket, endMarket
```

---

## OTP/OTD Definitions

### Standard Definition (DEFAULT)

```python
# On-Time Pickup
OTP = 'On Time' if pickTimeArrived < pickWindowTo else 'Late'

# On-Time Delivery
OTD = 'On Time' if dropTimeArrived < dropWindowTo else 'Late'
```

**This is the default logic** - use this unless the user specifically requests a different calculation.

### Custom OTP/OTD Logic (User-Requested Only)

The AI agent should be able to apply custom OTP/OTD logic if a user requests it. Example:

**"Calculate OTP where late = after 9 AM on the scheduled day"**:
```python
scheduled_pick_9am = pickWindowFrom.normalize() + 9 hours
OTP = 'Late' if pickTimeArrived > scheduled_pick_9am else 'On Time'
```

**Other possible custom definitions a user might request:**
- Late if X hours after window start
- Late if after a specific time of day
- Late if more than X minutes past window end (grace period)

⚠️ **Do NOT apply custom logic by default** - only when explicitly requested by the user.

### Customer vs Carrier Perspective ✅ (CONFIRMED)

| Perspective | What they care about | Which rows to use |
|-------------|---------------------|-------------------|
| **Customer** | First pickup → Final delivery | `mainShipment = YES` only |
| **Carrier** | Every leg they operated | ALL rows with deduplication |

---

## Counting Shipments ✅ (CONFIRMED)

### For Customer Reports
- **Filter**: `mainShipment = 'YES'`
- **Status filter**: `shipmentStatus = 'Complete'` (dropStatus often NULL on YES rows)
- **No deduplication needed** (one YES row per order)

### For Carrier Reports
- **Filter**: None (use all rows)
- **Status filter**: `shipmentStatus = 'Complete'` OR `pickStatus = 'Succeeded'` (excludes incomplete legs)
- **Use deduplication** to prevent counting YES row twice

### Deduplication Logic (from existing carrier reports)

```python
# Pickup dedup key
pickup_dedup_key = loadId + '|' + carrierName + '|' + pickLocationName + '|' + pickDate

# Delivery dedup key
delivery_dedup_key = loadId + '|' + carrierName + '|' + dropLocationName + '|' + dropDate

# Keep first occurrence only
keep_for_pickup = ~duplicated(pickup_dedup_key, keep='first')
keep_for_delivery = ~duplicated(delivery_dedup_key, keep='first')
```

---

## Delay Codes

### Default Imputation
If a shipment is late but has no delay code, default to **"Carrier Failure"**:

```python
if OTP == 'Late' and pickupDelayCode is empty:
    pickupDelayCode = 'Carrier Failure'

if OTD == 'Late' and deliveryDelayCode is empty:
    deliveryDelayCode = 'Carrier Failure'
```

---

## Performance Targets

| Metric | Target |
|--------|--------|
| OTP | 98.5% |
| OTD | 99.9% (or 98.0% in some reports) |
| Tracking | 100% |

---

## Open Questions

### Answered ✅
1. ~~**LTL Revenue**: Confirm that we sum `mainShipment = NO` rows only~~ → ⚠️ UPDATED: For LTL multi-leg, YES row revenue often DUPLICATES NO row revenue. Use ONLY NO rows OR use `orders.revenueAllocation`.
2. ~~**Cross-dock legs**: What does a same-city leg represent?~~ → Cross-dock handling operation, include in financials but not shipment counts.
3. ~~**Carrier vs Customer perspective**~~ → Carrier = all rows with dedup. Customer = YES rows only.
4. ~~**FTL Structure**~~ → FTL has ~93k YES rows, ~6.5k NO rows. FTL multidrop has MULTIPLE YES rows (each is separate drop). FTL multistop has 1 YES + NO rows for additional services.
5. ~~**Orders table vs otp_reports**~~ → `orders.revenueAllocation` is the SAFEST source for order-level revenue. Summing otp_reports risks double counting for LTL.
6. ~~**Customer OTP/OTD time data**~~ → YES rows DO have time data (81% pickTime, 83% dropTime). Times represent first pickup → final delivery. BUT `pickStatus`/`dropStatus` may be NULL on YES rows.
7. ~~**Deduplication 3% edge cases**~~ → These are cross-dock-only orders with `Pending`/`Removed` status. Filter by `shipmentStatus = 'Complete'` to exclude incomplete work and achieve 100% effective deduplication.
8. ~~**Custom OTP/OTD logic**~~ → No default custom logic needed. AI agent should use standard logic by default, but be capable of applying custom logic (e.g., "9 AM cutoff") if user requests it.
9. ~~**LTL Carrier Shipment Counting**~~ → For counting shipments: Use `mainShipment = NO` for LTL multi-leg (cleaner), UNLESS only one row exists for an orderCode (then count that single YES row).

10. ~~**FTL Multistop Investigation (P-65893-2445)**~~ → ✅ VERIFIED: NO rows are SEPARATE SERVICES, not duplicates. YES row = long-haul (Cherry Hill→Atlanta), NO rows = cross-dock handling + final mile (different pickup locations). Sum ALL rows for FTL multistop.

### Still Open ❓

*None at this time.*

---

## Query Patterns & Examples

### Key Definitions for Queries

#### Lane Definition
A **lane** is an origin-destination pair. Can be defined at different granularities:

| Granularity | Columns | Example |
|-------------|---------|---------|
| **Market** | `startMarket → endMarket` | `LAX → EWR` |
| **City-State** | `pickCity, pickState → dropCity, dropState` | `Los Angeles, CA → Newark, NJ` |
| **Full Address** | `pickAddress → dropAddress` | (rarely used for aggregation) |

**Recommendation**: Use `startMarket → endMarket` for high-level analysis, City-State for detailed lane analysis.

#### Equipment Type
Use the `equipment` column. Common values:

**Dry (Non-Refrigerated) Equipment:**
- `53-ft Trailer` (31k) - **Dry 53-ft trailer**
- `26-ft Straight Truck` (50k)
- `Cargo Van` (25k)
- `Sprinter Van` (13k)

**Refrigerated (Reefer) Equipment:**
- `53-ft Reefer` (12k) - **Refrigerated 53-ft trailer**
- `Reefer Straight box truck` (5k)

**Variants with Add-ons:**
- `/w Lift Gate` - Has lift gate
- `/w Team` - Team drivers (for longer hauls)
- `/w Pallet Jack` - Includes pallet jack
- `/w Drop` - Drop trailer service

**Note**: When users ask about "dry 53-ft" vs "reefer 53-ft", use:
- `53-ft Trailer` = Dry
- `53-ft Reefer` = Refrigerated

#### Shipment Type (FTL vs LTL)
Use the `shipmentType` column:
- `Less Than Truckload` (508k) - LTL
- `Full Truckload` (126k) - FTL
- `Parcel` (89k)
- `LTL_CD` (1k) - LTL Cross-Dock

#### DoorDash Identification
Multiple ways to identify DoorDash shipments:
1. **Customer**: `clientName = 'DoorDash'` (~32k rows)
2. **Dropoff Location**: `dropLocationName LIKE '%DoorDash%'` or `dropLocationName LIKE '%Doordash%'`
3. **Dedicated Table**: `doordash_shipments` table (7k rows) with lane info

---

### Example Queries

#### Q1: OTP for a Given Carrier in Q4 2025

**Question**: "What was Carrier X's OTP in Q4 2025?"

**Considerations by Shipment Type**:
- **All types**: Use ALL rows (not just mainShipment=YES), apply deduplication
- **FTL direct**: Single row per shipment
- **FTL multi-stop**: Multiple stops on same route
- **LTL**: Multiple legs per order

```sql
-- Carrier OTP for Q4 2025 (Oct-Dec)
-- Uses deduplication for accurate pickup counts

WITH deduplicated AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY loadId, carrierName, pickLocationName, DATE(STR_TO_DATE(pickTimeArrived, '%m/%d/%Y %H:%i:%s'))
            ORDER BY STR_TO_DATE(pickTimeArrived, '%m/%d/%Y %H:%i:%s')
        ) as rn
    FROM otp_reports
    WHERE carrierName = 'YOUR_CARRIER_NAME'
      AND (shipmentStatus = 'Complete' OR pickStatus = 'Succeeded')
      AND pickTimeArrived IS NOT NULL
      AND pickWindowTo IS NOT NULL
      AND STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') >= '2025-10-01'
      AND STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') < '2026-01-01'
)
SELECT
    shipmentType,
    COUNT(*) as total_pickups,
    SUM(CASE WHEN STR_TO_DATE(pickTimeArrived, '%m/%d/%Y %H:%i:%s') < STR_TO_DATE(pickWindowTo, '%m/%d/%Y %H:%i:%s') THEN 1 ELSE 0 END) as on_time,
    ROUND(100.0 * SUM(CASE WHEN STR_TO_DATE(pickTimeArrived, '%m/%d/%Y %H:%i:%s') < STR_TO_DATE(pickWindowTo, '%m/%d/%Y %H:%i:%s') THEN 1 ELSE 0 END) / COUNT(*), 1) as otp_pct
FROM deduplicated
WHERE rn = 1
GROUP BY shipmentType;
```

**Key Logic**:
- Filter by `carrierName`
- Use `pickWindowFrom` for date range (scheduled pickup date)
- Deduplicate by `loadId + carrierName + pickLocationName + pickDate`
- OTP = `pickTimeArrived < pickWindowTo`

---

#### Q2: OTD for a Customer by Lane and Equipment Type

**Question**: "What was Customer X's OTD last week? Show pivot by lane and equipment."

**Considerations**:
- **Customer reports**: Use `mainShipment = 'YES'` only (customer's view)
- **No deduplication needed** for customer reports
- Filter by `shipmentStatus = 'Complete'`

```sql
-- Customer OTD by Lane and Equipment (last week)
SELECT
    CONCAT(startMarket, ' → ', endMarket) as lane,
    equipment,
    shipmentType,
    COUNT(*) as total_deliveries,
    SUM(CASE WHEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s') < STR_TO_DATE(dropWindowTo, '%m/%d/%Y %H:%i:%s') THEN 1 ELSE 0 END) as on_time,
    ROUND(100.0 * SUM(CASE WHEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s') < STR_TO_DATE(dropWindowTo, '%m/%d/%Y %H:%i:%s') THEN 1 ELSE 0 END) / COUNT(*), 1) as otd_pct
FROM otp_reports
WHERE clientName = 'YOUR_CUSTOMER_NAME'
  AND mainShipment = 'YES'
  AND shipmentStatus = 'Complete'
  AND dropTimeArrived IS NOT NULL
  AND dropWindowTo IS NOT NULL
  AND YEARWEEK(STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s'), 1) = YEARWEEK(DATE_SUB(CURDATE(), INTERVAL 1 WEEK), 1)
GROUP BY startMarket, endMarket, equipment, shipmentType
ORDER BY total_deliveries DESC;
```

**Key Logic**:
- `mainShipment = 'YES'` for customer view
- Lane = `startMarket → endMarket`
- Group by `equipment` and `shipmentType` for breakdown
- OTD = `dropTimeArrived < dropWindowTo`

---

#### Q3: Profit from DoorDash Last Month

**Question**: "What was our profit from DoorDash last month?"

**Considerations**:
- DoorDash is a **customer** (`clientName = 'DoorDash'`)
- **⚠️ For accurate revenue/profit**, use `orders.revenueAllocation` to avoid LTL double counting
- **Profit = `revenueAllocationNumber - costAllocationNumber`** (do NOT use `profitNumber` - it has data issues)

**Recommended Approach (using orders table):**
```sql
-- DoorDash Profit Last Month (SAFE - uses orders table)
SELECT
    o.shipmentType,
    COUNT(DISTINCT o.code) as order_count,
    SUM(CAST(o.revenueAllocation AS DECIMAL(10,2))) as total_revenue,
    SUM(CAST(o.costAllocation AS DECIMAL(10,2))) as total_cost,
    SUM(CAST(o.revenueAllocation AS DECIMAL(10,2)) - CAST(o.costAllocation AS DECIMAL(10,2))) as total_profit
FROM orders o
WHERE o.customerName = 'DoorDash'
  AND o.status = 'Complete'
  AND STR_TO_DATE(o.createdAt, '%Y-%m-%d') >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
  AND STR_TO_DATE(o.createdAt, '%Y-%m-%d') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
GROUP BY o.shipmentType;
```

**Alternative (otp_reports - FTL only or with caution):**
```sql
-- DoorDash Profit Last Month (otp_reports - may double count LTL)
SELECT
    shipmentType,
    COUNT(DISTINCT orderCode) as order_count,
    SUM(revenueAllocationNumber) as total_revenue,
    SUM(costAllocationNumber) as total_cost,
    SUM(revenueAllocationNumber - costAllocationNumber) as total_profit,
    ROUND(100.0 * SUM(revenueAllocationNumber - costAllocationNumber) / NULLIF(SUM(revenueAllocationNumber), 0), 1) as margin_pct
FROM otp_reports
WHERE clientName = 'DoorDash'
  AND mainShipment = 'YES'
  AND shipmentStatus = 'Complete'
  AND STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s') >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
  AND STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
GROUP BY shipmentType;
```

**Key Logic**:
- `clientName = 'DoorDash'`
- **Best practice**: Use `orders` table for revenue (avoids LTL double counting)
- If using otp_reports: `mainShipment = 'YES'` works for FTL, may double count LTL
- Calculate profit as `revenue - cost`

---

#### Q4: OTD for Shipments Dropped at DoorDash Sites

**Question**: "What's the OTD for shipments delivered to DoorDash locations?"

**Considerations**:
- This is about **dropoff location**, not customer
- Use `dropLocationName LIKE '%DoorDash%'`
- This is a **carrier perspective** (all legs that deliver to DoorDash)

```sql
-- OTD for DoorDash Dropoff Locations
WITH deduplicated AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY loadId, carrierName, dropLocationName, DATE(STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s'))
            ORDER BY STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s')
        ) as rn
    FROM otp_reports
    WHERE (dropLocationName LIKE '%DoorDash%' OR dropLocationName LIKE '%Doordash%')
      AND (shipmentStatus = 'Complete' OR dropStatus = 'Succeeded')
      AND dropTimeArrived IS NOT NULL
      AND dropWindowTo IS NOT NULL
)
SELECT
    dropLocationName,
    shipmentType,
    COUNT(*) as total_deliveries,
    SUM(CASE WHEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s') < STR_TO_DATE(dropWindowTo, '%m/%d/%Y %H:%i:%s') THEN 1 ELSE 0 END) as on_time,
    ROUND(100.0 * SUM(CASE WHEN STR_TO_DATE(dropTimeArrived, '%m/%d/%Y %H:%i:%s') < STR_TO_DATE(dropWindowTo, '%m/%d/%Y %H:%i:%s') THEN 1 ELSE 0 END) / COUNT(*), 1) as otd_pct
FROM deduplicated
WHERE rn = 1
GROUP BY dropLocationName, shipmentType
ORDER BY total_deliveries DESC;
```

**Key Logic**:
- Filter by `dropLocationName` (not clientName)
- Use deduplication (carrier perspective)
- Common DoorDash locations: `DoorDash LAX-10`, `DoorDash LAX-12`, `Doordash DTX-1`, etc.

---

#### Q5: Profit by Lane Last Month

**Question**: "What was our profit by lane last month?"

**⚠️ WARNING**: This query using otp_reports may double count LTL revenue. For accurate profit, consider using the `orders` table.

**Considerations**:
- Lane = `startMarket → endMarket`
- Consider breaking down by `shipmentType`
- **Best practice**: Use `orders` table for accurate revenue

**Recommended Approach (using orders table):**
```sql
-- Profit by Lane Last Month (SAFE - uses orders table)
-- Note: orders table may not have startMarket/endMarket, may need join to otp_reports for lane info
SELECT
    CONCAT(otp.startMarket, ' → ', otp.endMarket) as lane,
    o.shipmentType,
    COUNT(DISTINCT o.code) as order_count,
    SUM(CAST(o.revenueAllocation AS DECIMAL(10,2))) as total_revenue,
    SUM(CAST(o.costAllocation AS DECIMAL(10,2))) as total_cost,
    SUM(CAST(o.revenueAllocation AS DECIMAL(10,2)) - CAST(o.costAllocation AS DECIMAL(10,2))) as total_profit
FROM orders o
JOIN otp_reports otp ON o.code = otp.orderCode AND otp.mainShipment = 'YES'
WHERE o.status = 'Complete'
  AND otp.startMarket IS NOT NULL AND otp.startMarket != ''
  AND otp.endMarket IS NOT NULL AND otp.endMarket != ''
  AND STR_TO_DATE(otp.dropWindowFrom, '%m/%d/%Y %H:%i:%s') >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
  AND STR_TO_DATE(otp.dropWindowFrom, '%m/%d/%Y %H:%i:%s') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
GROUP BY otp.startMarket, otp.endMarket, o.shipmentType
ORDER BY total_profit DESC
LIMIT 20;
```

**Alternative (otp_reports only - FTL safe, LTL may double count):**
```sql
-- Profit by Lane Last Month (otp_reports - may double count LTL)
SELECT
    CONCAT(startMarket, ' → ', endMarket) as lane,
    shipmentType,
    COUNT(DISTINCT orderCode) as order_count,
    SUM(revenueAllocationNumber) as total_revenue,
    SUM(costAllocationNumber) as total_cost,
    SUM(revenueAllocationNumber - costAllocationNumber) as total_profit,
    ROUND(100.0 * SUM(revenueAllocationNumber - costAllocationNumber) / NULLIF(SUM(revenueAllocationNumber), 0), 1) as margin_pct
FROM otp_reports
WHERE mainShipment = 'YES'
  AND shipmentStatus = 'Complete'
  AND startMarket IS NOT NULL AND startMarket != ''
  AND endMarket IS NOT NULL AND endMarket != ''
  AND STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s') >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
  AND STR_TO_DATE(dropWindowFrom, '%m/%d/%Y %H:%i:%s') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
GROUP BY startMarket, endMarket, shipmentType
ORDER BY total_profit DESC
LIMIT 20;
```

**Key Logic**:
- **Best**: Use `orders.revenueAllocation` joined to otp_reports for lane info
- Lane = `startMarket → endMarket`
- Filter out NULL/empty markets
- ⚠️ otp_reports-only approach may double count LTL revenue

---

### Q6: How many shipments were booked by carrier sales rep X on a given day?

**Purpose**: Track carrier sales team activity by showing shipments booked by a specific rep.

**Date Field**: Use `loadBookedTime` for "booked" questions (not `pickWindowFrom`/`pickWindowTo`)
- Format: `MM/DD/YYYY HH:MM:SS` (e.g., `10/04/2024 07:25:10`)
- Coverage: ~67% of rows have this field populated
- The `carrierSaleRep` column contains the sales rep name

**SQL Query**:
```sql
SELECT
    carrierSaleRep,
    DATE(STR_TO_DATE(loadBookedTime, '%m/%d/%Y %H:%i:%s')) as booked_date,
    shipmentType,
    COUNT(*) as shipment_count
FROM otp_reports
WHERE carrierSaleRep = 'YOUR_REP_NAME'
  AND loadBookedTime IS NOT NULL
  AND loadBookedTime != ''
  AND DATE(STR_TO_DATE(loadBookedTime, '%m/%d/%Y %H:%i:%s')) = '2025-01-15'
GROUP BY carrierSaleRep, booked_date, shipmentType
ORDER BY shipmentType;
```

**Key Logic**:
- Use `loadBookedTime` for when a shipment was booked (assigned to carrier)
- Use `carrierSaleRep` to filter by sales rep
- No need for `mainShipment` filter - count all shipment rows booked

**Note on carrierSaleRep**: Top reps include Holger Villegas, Vincent, Amy Ngo, Regina, Rachel Luong, etc.

---

### Date Field Defaults

**Default date range field**: Use `pickWindowFrom` and `pickWindowTo` for most date-based questions.

| Question Type | Date Field to Use |
|---------------|-------------------|
| OTP/OTD performance by date | `pickWindowFrom` / `pickWindowTo` (pickup window) |
| Revenue/profit by month | `pickWindowFrom` or `dropWindowFrom` |
| Shipments booked by rep | `loadBookedTime` |
| When order was created | `createdAt` or `createWhen` |

**Date format in otp_reports**: Most date fields use `MM/DD/YYYY HH:MM:SS` format.
To convert to MySQL datetime: `STR_TO_DATE(fieldName, '%m/%d/%Y %H:%i:%s')`

---

### Hybrid SQL + Python Approach

For complex calculations (averages, percentiles, correlations, trends), use a **hybrid approach**:

**Rule**:
- **SQL**: Filtering, date ranges, joins, WHERE clauses, and basic aggregations (COUNT, SUM)
- **Python**: ALL calculations and math (averages, percentiles, margins, trends, correlations)

**Pattern**:
```python
# Step 1: SQL gets the filtered data
df = pd.read_sql("""
    SELECT
        revenueAllocationNumber,
        costAllocationNumber,
        startMarket,
        endMarket,
        pickWindowFrom
    FROM otp_reports
    WHERE clientName = 'DoorDash'
      AND mainShipment = 'YES'
      AND shipmentStatus = 'Complete'
      AND STR_TO_DATE(pickWindowFrom, '%m/%d/%Y %H:%i:%s') >= '2025-01-01'
""", conn)

# Step 2: Python does all the math
df['profit'] = df['revenueAllocationNumber'] - df['costAllocationNumber']
df['margin'] = df['profit'] / df['revenueAllocationNumber']

# Complex calculations
avg_margin = df['margin'].mean()
median_margin = df['margin'].median()
p90_margin = df['margin'].quantile(0.90)
std_margin = df['margin'].std()
```

**Why this approach**:
1. SQL reduces 675k rows to relevant subset (e.g., 2k rows for DoorDash in January)
2. Python handles all math consistently with pandas/numpy
3. Easier for AI agent - clear pattern: "SQL filters, Python calculates"

**Examples of complex calculations in Python**:
- Weighted averages
- Percentiles (10th, 50th, 90th)
- Week-over-week/month-over-month trends
- Correlations between metrics
- Rolling averages
- Standard deviation / variance

---

### Query Pattern Summary

| Question Type | mainShipment Filter | Deduplication | Key Columns | Notes |
|---------------|---------------------|---------------|-------------|-------|
| **Carrier OTP (LTL)** | `= 'NO'` (or dedup) | Yes if all rows | `pickTimeArrived`, `pickWindowTo` | Use NO rows for LTL multi-leg |
| **Carrier OTP (FTL)** | None (all rows) | Yes | Same | Standard dedup works |
| **Carrier OTD** | Same as OTP | Yes | `dropTimeArrived`, `dropWindowTo` | |
| **Customer OTP/OTD** | `= 'YES'` | No | Same time columns | |
| **Revenue/Profit (Order-level)** | **Use `orders` table** | N/A | `orders.revenueAllocation` | ⚠️ Avoids LTL double counting |
| **Revenue/Profit (FTL only)** | `= 'YES'` | No | `revenueAllocationNumber` | Safe for FTL |
| **Shipments Booked** | None (all rows) | No | `loadBookedTime`, `carrierSaleRep` | |
| **By Lane** | Depends on perspective | Depends | `startMarket`, `endMarket` | |
| **By Equipment** | Depends on perspective | Depends | `equipment` | |
| **By Shipment Type** | Depends on perspective | Depends | `shipmentType` | |

### ⚠️ Key Warnings

1. **LTL Revenue**: For LTL multi-leg orders, `mainShipment = YES` row revenue may DUPLICATE NO row revenue. Use `orders.revenueAllocation` or sum ONLY NO rows.
2. **FTL Multidrop**: Multiple YES rows on same loadId are SEPARATE shipments (multiple drops) - sum ALL YES rows (not duplicates).
3. **profitNumber column**: DO NOT USE - has data quality issues. Calculate as `revenueAllocationNumber - costAllocationNumber`.

---

## Sanity Checks

Before returning results to the user, validate that the data makes sense:

### Margin/Profit Checks

| Check | Expected Range | If Violated |
|-------|----------------|-------------|
| Profit margin | -50% to +50% typical | Margins > 100% or < -100% likely indicate double counting or data issue |
| Revenue per shipment | $50 - $10,000 typical | Very high/low values may indicate aggregation error |
| Cost > 0 | Most shipments have cost | If cost = 0 for many rows, may be missing data |

### Count Checks

| Check | Expected | If Violated |
|-------|----------|-------------|
| Rows per order (LTL) | 1-10 typical | > 20 rows unusual, investigate |
| Rows per order (FTL) | 1-5 typical | > 10 rows unusual |
| OTP/OTD percentage | 80-100% typical | < 50% may indicate filter issue |

### Data Completeness Checks

```sql
-- Check for NULL values in critical fields
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN pickTimeArrived IS NULL THEN 1 ELSE 0 END) as missing_pick_time,
    SUM(CASE WHEN dropTimeArrived IS NULL THEN 1 ELSE 0 END) as missing_drop_time,
    SUM(CASE WHEN revenueAllocationNumber IS NULL THEN 1 ELSE 0 END) as missing_revenue
FROM otp_reports
WHERE <your_filters>
```

### Cross-Validation

When possible, validate totals against the `orders` table:
```sql
-- Compare otp_reports sum vs orders table
SELECT
    o.code,
    CAST(o.revenueAllocation AS DECIMAL(10,2)) as orders_revenue,
    SUM(otp.revenueAllocationNumber) as otp_sum
FROM orders o
JOIN otp_reports otp ON o.code = otp.orderCode
WHERE o.code = 'P-XXXXX-XXXX'
GROUP BY o.code, o.revenueAllocation
```

---

## To-Do / Future Improvements

### High Priority
- [ ] **Column value enumerations**: Document all possible values for key columns (`shipmentStatus`, `pickStatus`, `dropStatus`, `shipmentType`, `equipment`, etc.)
- [ ] **Edge case handling**: Define fallback behavior when an order has characteristics of multiple patterns
- [ ] **Validation queries**: Add more sanity check queries for common scenarios

### Medium Priority
- [ ] **Question → Query mapping**: Build comprehensive list of common questions and their corresponding query patterns
- [ ] **Performance optimization**: Add index hints and query optimization tips for large date ranges
- [ ] **Error handling**: Document common SQL errors and how to resolve them

### Low Priority
- [ ] **Historical data quirks**: Document any known data quality issues in older data
- [ ] **Seasonal patterns**: Note any seasonal variations in data patterns
- [ ] **Customer-specific logic**: Document any customer-specific business rules (e.g., DoorDash)

---

*Last updated: 2026-02-03*

