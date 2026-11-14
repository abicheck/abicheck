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
