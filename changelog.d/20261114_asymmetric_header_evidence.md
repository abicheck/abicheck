### Fixed

- **Headers given for one side only no longer manufacture a break** —
  comparing a library against itself with `--header old=…` (or `new=…`)
  alone reported removed types and functions, an added destructor pair and a
  vtable change, and exited 4: header-parsed declarations were compared
  against a header-less dump of the same binary. A pair whose operands reach
  different evidence rungs is now judged at the rung both reach (binary),
  through the same joint projection an explicit `--depth binary` uses, with a
  coverage warning naming the side that had headers
  (`policy/common_evidence_depth.py`). An explicit `--depth` still wins.
  Two latent defects in that projection are fixed with it: a projection made
  after a `compare()` of the same snapshot kept serving the stripped
  declarations from the snapshot's stale lookup indexes, and a binary-depth
  projection where one operand never had headers reported public-header
  scoping as "unresolved" instead of out of the requested scope.

  **This changes CI outcomes.** A pipeline that compares with headers on one
  side only (for example a stored header-less baseline against a fresh
  `-H` dump) used to fail with exit 4 (BREAKING) on findings that were
  artifacts of the evidence mismatch. It now gets NO_CHANGE (exit 0), or
  UNCONFIRMED (exit 1) when neither side has DWARF to confirm layouts at
  binary depth. Give headers to both sides to restore header-depth checking.
  Recorded as an ADR-063 amendment (2026-11-14).
