# Audit: store/sqlite.py

**Date:** 2026-04-26  **Run ID:** 01jz3k7b  **Model:** google/gemma-4-26b-a4b  **Lens:** correctness
**Tokens in/out:** 8423 / 1276  **Latency:** 47s (thinking 32s, output 15s)
**Tools used:** 3 / 5 (`gitnexus_context`x1, `read_file`x2)  **Compactions:** 0 / 3
**GitNexus context:** cluster=retrieval/factory | callers d=1: 14 | processes: build_retrieval_pipeline, search_pipeline_init

The module implements a small SQLite-backed retrieval factory with reasonable shape but a few sharp edges around connection lifecycle and retry control. Core path is correct.

---

## Detailed Audit Findings

### A. Robustness & Error Handling (High Priority)
- **Connection leak on rollback**
  - **Issue:** rollback_transaction does not close the cursor before returning the connection to the pool.
  - **Why:** Open cursors keep the underlying file descriptor pinned; under load the pool exhausts and audits stall.
  - **Fix:** Close the cursor in a finally block before returning the connection.
  - **Confidence:** high
  - **Location:** L142-L156, `rollback_transaction`

### B. Concurrency (Medium Priority)
- **Race between writer and snapshot reader**
  - **Issue:** Writer commits a row that snapshot reader can observe mid-batch.
  - **Why:** Snapshot must be a consistent prefix; partial visibility breaks downstream invariants.
  - **Fix:** Wrap the batch in a transaction and let the reader use the same isolation snapshot.
  - **Confidence:** medium
  - **Location:** `_apply_batch`

### C. Style \| Naming (Low Priority)
- **Unbounded retry loop on stale \| index**
  - **Issue:** While loop has no max_iterations cap.
  - **Why:** Pensiv discipline requires every external loop to declare a bound.
  - **Fix:** Add max_iterations=10 and raise on overflow.
  - **Confidence:** low
  - **Location:** L312-L320, `retry_until_fresh`

### Healthy
- **Module docstring anchors back to spec** - The module docstring references the relevant spec section, matching project convention.

---

## Recommendations

#### Recommendation 1: Use a context manager for cursors
Eliminates the leak class entirely; cursors close deterministically on exit.

```python
with conn.cursor() as cur:
    cur.execute(sql)
    rows = cur.fetchall()
```

#### Recommendation 2: Document the snapshot isolation level
Readers need to know which isolation level the writer expects so partial-visibility regressions are caught in code review.

---

### Summary of Best Practices Applied
| Feature | Original Code | Recommended |
|---|---|---|
| Cursor lifecycle | manual close in finally | context manager (with conn.cursor() as cur) |
| Retry loop | while True: ... if cond: break | for _ in range(max_iterations): ... else: raise |
| Boolean flag \| toggle | x \| y as bitmask | explicit enum |
