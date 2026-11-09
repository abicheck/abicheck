### Performance

- **Faster large comparisons** — hold the existing balanced-profile collector thresholds through the complete CLI comparison, including input loading and final report rendering. Low-memory keeps its existing collector scheduling; thresholds are restored after success or failure.

- **Faster CLI startup** — load command implementations only when selected; root help and version no longer import the comparison pipeline. Direct Click command introspection still returns complete commands.
- **Lower JSON report memory** — serialize immutable report objects through shallow projections instead of building a second mutable document. Report contents and formatting are unchanged.
- **Lower stored-snapshot memory** — the existing `low-memory` profile shares repeated short immutable JSON string values within each load, with a bounded pool; mutable containers retain independent ownership. The default profile keeps the fast JSON decoder.
