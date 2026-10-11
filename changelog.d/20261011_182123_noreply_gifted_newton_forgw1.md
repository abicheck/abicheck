### Fixed

- **Snapshots, baselines and caches written on Python 3.15 load again** —
  every string-valued enum is now an `enum.StrEnum`, so CPython 3.15's
  `json` (which serializes `str` subclasses through `str()`) writes
  `"global"` instead of `"SymbolBinding.GLOBAL"`; such files previously
  failed to load on any interpreter. Side effect: an enum interpolated into
  text now renders its value (`public`), not `Visibility.PUBLIC`.
- **Free-threaded Python (3.15t): the per-thread header-AST handoff slot is
  per-thread again** — it is now a `threading.local`, since free-threaded
  threads inherit their parent's `ContextVar` values and both compare sides
  could consume the same parsed AST.
- **Docs: the contract-coverage exit axis applies to any stated domain** —
  `.abicheck.yml` `scope.public: true` states `public` just as
  `--contract public` does, so incomplete identity coverage can turn a clean
  `0` into `1`.
- **Compiled-pattern memory estimate no longer undercounts on Python 3.11**
  — the spelling-pattern cache budget now charges a fixed per-pattern
  overhead, so small vocabularies are never accounted below their measured
  size.
