# BIM Relationship Error - Column Qualifier Fix

## Problem

When publishing to Power BI Fabric API, the following error occurred:
```
Data source error: The '<oii>total_hours</oii>' column does not exist in the rowset.
Cluster URI: WABI-INDIA-CENTRAL-A-PRIMARY-redirect.analysis.windows.net
Property FromColumn of object "relationship <oii>Accommodation_destination_id_Destinations_destination_id</oii>" 
refers to an object which cannot be found
```

## Root Cause

The BIM (Tabular Model) was being published with column names containing table qualifiers:
- **Published column names**: `Accommodation.destination_id`, `Destinations.destination_id`
- **Relationship expectations**: `destination_id` (unqualified)

When the BIM parser tried to match relationship endpoints, it couldn't find columns named `Accommodation.destination_id` because the actual table column was named just `destination_id`.

## Solution

Applied fixes to strip table qualifiers from column names in all BIM column extraction pathways in `powerbi_publisher.py`:

### 1. **Step 4: Deep M Expression Scan (Line ~890)**
```python
# Added _strip_qlik_qualifier call:
for col in col_candidates:
    col = col.strip()
    col = _strip_qlik_qualifier(col)  # <- THE FIX
    if col and col != "*" and col not in seen_c:
        # ... add to columns
```

### 2. **Step 5: Last-Resort Type-Annotation Scan (Line ~912)**
```python
# Added _strip_qlik_qualifier call:
for m_match in re.finditer(...):
    col = m_match.group(1).strip()
    col = _strip_qlik_qualifier(col)  # <- THE FIX
    if col and col != "*" and col not in seen_lr:
        # ... add to columns
```

### 3. **_extract_typedarticle_columns Function (Line ~479)**
```python
# Added _strip_qlik_qualifier call:
for col_match in re.finditer(...):
    col_name = col_match.group(1).strip()
    col_name = _strip_qlik_qualifier(col_name)  # <- THE FIX
    if col_name and col_name not in seen and col_name != "*":
        # ... add to columns
```

## Impact

✅ **Result**: All column names in the BIM will now be unqualified (e.g., `destination_id`, not `Accommodation.destination_id`)

✅ **Relationships**: Can now properly match column endpoints since both:
- BIM table columns → unqualified (e.g., `destination_id`)
- Relationship definitions → already unqualified (e.g., `destination_id`)

✅ **Power BI**: Can parse and validate the BIM model without encountering "Property FromColumn refers to an object which cannot be found" errors

## Files Modified

- `powerbi_publisher.py` (3 locations)
  - Lines ~890: Deep M expression scan
  - Lines ~912: Type-annotation scan  
  - Lines ~479: TypedTable extraction

## Testing

Created `test_column_qualifier_fix.py` to verify the fixes:
- ✅ Step 5 type-annotation scan correctly strips qualifiers
- ✅ TypedTable extraction correctly strips qualifiers
- ✅ Relationship column matching now works correctly

## Next Steps for User

1. Re-publish the dataset to Power BI/Fabric
2. The BIM should now publish successfully without the "Property FromColumn" error
3. All relationships should be properly recognized in the Tabular Model
