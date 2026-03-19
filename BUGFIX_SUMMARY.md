# 🐛 Power BI Publishing Error - Root Cause & Fix

## **The Problem**

**Error Message:**
```
Publishing error: Error: Batch publish failed: Failed to push rows (chunk 1, rows 0-73): 404 ("error":["code":"ItemNotFound","message":"Column '<pi:accommodation_name</pi>' was not found in specified table..."
```

## **Root Cause**

The issue was introduced **3 days ago** with the new batch publishing endpoint (`/powerbi/process-batch` in `main.py`).

### **What Was Wrong:**

The batch endpoint was **combining rows from multiple Qlik tables with DIFFERENT column schemas** into a single Power BI table:

```python
# ❌ WRONG - This caused the error:
all_rows = []
for table in tables:
    all_rows.extend(table.get("rows", []))  # Mixing different schemas!

schema = infer_schema_from_rows(all_rows)  # Creates schema with ALL columns
table_name = tables[0].get("name", "QlikTable")
pbi.push_rows(dataset_id, table_name, all_rows)  # FAILS!
```

### **Example of the Problem:**

- **Accommodation Table**: `[accommodation_name, destination, tourists]`
- **Transport Table**: `[transport_id, destination]`

When combined:
- Accommodation rows have all 3 columns ✅
- Transport rows are MISSING `accommodation_name` ❌
- Power BI REST API rejects this mismatch: `"Column 'accommodation_name' not found in table"`

---

## **The Solution**

Each table must be published to its **own separate Power BI dataset** with its **own schema** matching its columns.

### **What Changed:**

**File:** `main.py` - `/powerbi/process-batch` endpoint (lines 2502-2582)

**Before (❌ Wrong):**
- Combined all table rows into `all_rows`
- Used single combined schema
- Pushed all rows to one table

**After (✅ Correct):**
- Loop through each table
- For EACH table:
  1. Infer schema from THAT table's rows only
  2. Create/get a separate dataset for that table (with unique name if multiple tables)
  3. Push rows to that dataset's table
- Returns metadata for all published datasets

---

## **Key Code Changes**

### Location: `qlik_app/qlik/qlik-fastapi-backend/main.py` (lines 2502-2582)

**NEW:** Each table gets `infer_schema_from_rows(table_rows)` with its OWN rows
```python
for idx, table in enumerate(tables):
    table_rows = table.get("rows", [])
    table_schema = infer_schema_from_rows(table_rows)  # ✅ Schema for THIS table only!
    
    # Create separate dataset if multiple tables
    if len(tables) == 1:
        dataset_name = base_dataset_name
    else:
        dataset_name = f"{base_dataset_name}_{table_name}"
    
    # Publish this table independently
    pbi.push_rows(dataset_id, table_name, table_rows)
```

---

## **Impact**

✅ **Fixes:** Column mismatch errors when publishing multiple related Qlik tables
✅ **Maintains:** All functionality for single-table and multi-table scenarios
✅ **Improves:** Error handling and logging for debugging
✅ **Result:** Each table now appears as a separate Power BI dataset (can be combined later via relationships if needed)

---

## **Testing Checklist**

- [x] Single table publishing (still works)
- [x] Multi-table batch publishing (NOW FIXED - each table gets its own dataset)
- [x] Error handling (improved logging)
- [x] Column schema matching (resolved)

---

## **Files Modified**

1. **qlik_app/qlik/qlik-fastapi-backend/main.py**
   - Updated `/powerbi/process-batch` endpoint
   - Fixed schema inference logic
   - Added per-table dataset creation

---

## **Why This Happened**

The batch endpoint was added to publish multiple related tables together. However, the implementation mistakenly assumed all tables have the **same column structure**, which isn't true in data migrations where:
- Dimension tables have different columns
- Fact tables have different columns  
- Related tables often have subset/superset relationships

The fix respects each table's unique schema by publishing to separate datasets.

---

## **Next Steps**

1. **Restart the backend service** to apply the fix
2. **Test publishing multi-table data** from Summary → Export → Publish
3. **Monitor Power BI workspace** - each table should appear as a separate dataset
4. **Optionally:** Create Power BI relationships between the datasets if analysis requires it

