---
name: phobos-performance
description: "Profile-driven performance and resource budgeting."
---

# phobos-performance

Define the workload, correctness constraints and measurable target. Profile CPU, allocations, I/O, query plans and p95 latency before changing code. Bound concurrency and caches. Prefer algorithmic and query improvements over micro-optimizations. Implement, then compare representative before/after measurements with the same data and environment. A Raspberry Pi control plane needs explicit memory budgets and optional heavy workers; do not claim hardware certification without running it.
