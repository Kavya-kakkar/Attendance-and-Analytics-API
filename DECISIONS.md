# DECISIONS.md

1. **Indexes.**
   I created a unique index on `employees.emp_code` for business-key uniqueness and fast lookups, compound index `{department: 1, emp_code: 1}` for filtered sorting, and `{joined_on: 1}` for headcount checks. For `attendance_logs`, I created a unique compound index on `{emp_code: 1, date: 1}` to enforce single-log-per-day natural uniqueness and prevent race conditions; `{date: -1, emp_code: 1}` for index-covered sorted pagination without in-memory sorting; and `{emp_code: 1, punch_in: -1}` for instant punch-out lookups across midnight.
   *Rejected*: A standalone index on `date`, because `{date: -1, emp_code: 1}` already covers date-range queries as its prefix while simultaneously satisfying the secondary sort.

2. **Punch-in race.**
   Two concurrent requests independently fetch the employee and compute the attendance date. Both attempt `insert_one`. MongoDB's unique index on `(emp_code, date)` guarantees atomic serialization at the storage engine level. Exactly one insert succeeds and returns HTTP 201 Created; the other raises PyMongo's `DuplicateKeyError`, which our handler intercepts and returns cleanly as HTTP 409 Conflict. No check-then-insert race is possible.

3. **Ties.**
   Our aggregation pipeline applies `$setWindowFields` with `$rank` (standard competition ranking: 1, 2, 2, 4). The `limit` filter (`rank <= limit`) is applied after ranking. Both tied employees receive the identical rank, and both are included in the output even if the total row count exceeds `limit`. Within tied ranks, rows are ordered by `emp_code` ascending.

4. **Headcount.**
   The department summary pipeline begins on the `employees` collection (filtering `joined_on <= end_of_period`) rather than on attendance logs. Employees are grouped by department to establish `headcount: {$sum: 1}`, then joined to `attendance_logs` via `$lookup`. Employees with zero logs produce an empty joined array; their existence increments headcount while contributing zero to present days.

5. **One thing you would change for 100x the data.**
   At 100x volume (10M+ records), I would shard `attendance_logs` by a hashed compound shard key on `{emp_code, date}` to distribute write throughput across replica set shards, and maintain a pre-aggregated monthly summary collection updated incrementally or via change streams instead of executing real-time cross-collection lookups during monthly reporting peaks.
