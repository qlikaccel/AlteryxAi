# Token Storage Fix - Race Condition Resolution

## Problem Identified
The `token_storage.json` file was **not being updated** with new tokens despite logs indicating successful persistence. This was caused by **thread safety issues** in the token manager.

### Root Causes
1. **Missing Thread Lock in `save_tokens()`**: The `save_tokens()` method didn't acquire `TOKEN_LOCK`, while `refresh_token()` did, creating a race condition
2. **No Disk Sync**: File writes weren't explicitly flushed to disk with `os.fsync()`
3. **Non-Reentrant Lock**: `threading.Lock()` cannot be acquired twice by the same thread, causing issues with nested method calls

## Solution Implemented

### 1. Changed Lock Type (Line 39)
**Before:**
```python
TOKEN_LOCK = threading.Lock()
```

**After:**
```python
TOKEN_LOCK = threading.RLock()  # ← Allows nested acquisitions
```

### 2. Added Thread Locking to `load_tokens()` (Lines 60-65)
```python
@staticmethod
def load_tokens() -> dict:
    with TOKEN_LOCK:  # ← CRITICAL: Prevent reading during writes
        return TokenManager._load_tokens_unlocked()
```

### 3. Added Internal Unlocked Method (Lines 61-70)
```python
@staticmethod
def _load_tokens_unlocked() -> dict:
    """Internal: Load tokens WITHOUT acquiring lock (caller must hold lock)."""
    # ... file read logic
```

### 4. Added Thread Locking to `save_tokens()` (Lines 72-107)
```python
@staticmethod
def save_tokens(...) -> None:
    with TOKEN_LOCK:  # ← CRITICAL: Prevent race conditions
        stored = TokenManager._load_tokens_unlocked()  # Use unlocked version
        # ... prepare data
        try:
            with open(TOKEN_STORAGE_PATH, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.flush()  # ← Force buffer flush
                os.fsync(file.fileno())  # ← Force OS to write to storage
            logger.info("[TokenManager] Stored latest Alteryx tokens...")
        except Exception as exc:
            logger.error("[TokenManager] CRITICAL: Could not write token storage: %s", exc)
```

### 5. Added Thread Locking to `clear_storage()` (Lines 129-135)
```python
@staticmethod
def clear_storage() -> None:
    with TOKEN_LOCK:  # ← CRITICAL: Prevent deletion during reads/writes
        # ... delete logic
```

## How This Fixes the Issue

### Previous Behavior (Race Condition)
1. Thread A: `persist_alteryx_tokens()` → `save_tokens()` (NO LOCK)
2. Thread B: `refresh_token()` acquires lock, reads stale tokens
3. Thread A: Writes new tokens to file (without lock protection)
4. Thread B: Completes refresh with stale token, writes stale data, overwriting Thread A's write
5. Result: File contains old tokens ❌

### New Behavior (Thread-Safe)
1. Thread A: `persist_alteryx_tokens()` → `save_tokens()` acquires LOCK
   - Reads current tokens
   - Updates with new tokens
   - **Flushes data to disk with `os.fsync()`**
   - Releases LOCK
2. Thread B: Waits for LOCK, then proceeds safely with fresh tokens
3. Result: File always contains the latest tokens ✅

## Testing the Fix

### Manual Test
```python
# Should now see token_storage.json update immediately
curl http://localhost:8000/api/alteryx/validate-auth
```

### Verification Steps
1. **Check file timestamp**: `token_storage.json` timestamp should update after each API call
2. **Monitor logs**: Look for `[TokenManager] Stored latest Alteryx tokens` at INFO level
3. **Critical errors**: Any write failures now log as `ERROR` (not `WARNING`)
4. **Multiple concurrent requests**: File should not have race conditions

## Affected Files
- `app/utils/token_manager.py` - All token persistence logic

## Performance Impact
- **Minimal**: Only adds lock acquisition (already happens in `refresh_token()`)
- **Synchronous disk writes** via `os.fsync()` ensure data durability
- **RLock overhead** is negligible for API request frequency

## Backwards Compatibility
✅ All existing method signatures unchanged
✅ All aliases (`_load_tokens_from_storage()`, `_save_tokens_to_storage()`) remain intact
✅ External callers need no changes

---

**Status**: ✅ Fixed and ready for deployment
