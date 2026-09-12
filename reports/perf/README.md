# Performance receipts

Machine-readable receipts from the perf harnesses
(`scripts/perf_receipt.py`'s `abicheck-perf-receipt/2` envelope — schema 2 split
the measuring harness's own revision from the measured product's).

**What belongs here:** a small, reviewed receipt worth keeping as a reference
point — a real-integration profile measurement, or a reference run of the
synthetic suites. Receipts are JSON metadata and summary statistics only.

**What does not:** raw AST dumps, binaries, whole snapshots, or per-run CI
output. Those are CI artifacts (`actions/upload-artifact`) with a retention
window, not repository content — the perf lanes upload them under
`performance-l2-cli*` / `performance-header-graph`. A receipt here that grows
past a few tens of KB is almost certainly carrying something that should have
stayed an artifact.

A receipt records the product SHA, toolchain and host facts it was taken under.
Two receipts are comparable only when those agree; `identity` is there so a
reader can check rather than assume.
