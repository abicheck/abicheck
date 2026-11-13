### Fixed

- **A failed release member reads as exit 4 through `abicheck aggregate` and in the report's verdict** (ADR-065 D8). `aggregate` folded a degraded member's `run_outcome.operational` to `1` while `compare` exited `4`; the aggregate now folds the release's own persisted operational-error contribution, so the two agree. A release report with a `FAILED` member also reports `ERROR` as its worst verdict instead of `NO_CHANGE` beside exit `4`, for live and stored (`--bundle-facts`) runs. `compare` exit codes are unchanged; `aggregate` now reads `4` where it read `1`.
