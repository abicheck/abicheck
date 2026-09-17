### Fixed

- **`--exclude-header` now survives a directory or package operand** — the
  directory/package branch of `compare` forwarded every other header input
  to the per-library release fan-out and silently dropped the exclusion
  rules, so a release tree that cannot be parsed whole (Intel MKL's
  `include/`, whose FFTW2 and FFTW3 headers declare conflicting typedefs)
  failed every library under the exact arguments that made the identical
  single-library comparison exit 0. The canonical rules now reach every
  release path — matched pairs, one-sided/stranded snapshot capture, the
  `--dry-run` receipt — through the one existing implementation
  (`InputSpec.exclude_headers`), applied identically to both sides. They
  also feed `effective_config_fields["surface.exclude_headers"]`, so two
  runs differing only in their exclusion rules no longer share a
  configuration digest; equivalent rule sets (reordered or repeated)
  canonicalize to one identity. A rule that matches no header warns once
  for the whole run rather than once per library. New
  `.abicheck.yml` key `scope.exclude_headers` gives the flag a
  schema-consistent config spelling, deliberately separate from
  `sources.exclude`, which narrows source collection rather than header
  extraction. `dump` honors the key too, not only `compare`: the two produce
  operands for each other, so a config-only rule reaching one and not the
  other makes the same project config succeed on one command and fail on the
  other. (That wiring is load-bearing rather than convenient — the
  comparability gate cannot catch such a divergence today, because the
  native `dump` CLI stamps no `excluded_header_patterns` on the snapshot it
  writes at all. Measured and recorded in
  `docs/contribute/known-gaps.md`; pre-existing, and not changed here.)

- **A dependency's `-I` root is no longer treated as a public API root** —
  an include directory passed so the compiler can resolve a dependency's
  `#include` was promoted wholesale into the public-provenance set, making
  every declaration underneath it an export obligation of the library that
  merely needed to parse it. Intel MKL, which passes an MPI include
  directory solely so `mkl_cdft.h` can parse `#include <mpi.h>`, reported
  2,211 `public_not_exported` findings about an API it does not own. An
  `-I` root now widens public provenance only where the run's own declared
  public headers (`-H`, a declared public header directory,
  `sources.public_headers`) live underneath it — a structural containment
  rule that names no vendor, path or symbol prefix, and that the
  transitively-reached-header case this widening exists for already
  satisfied. Types a dependency declares remain available to type closure
  and leak analysis when an owned public declaration references them. A root
  the rule declines is classified `UNKNOWN`, never `PRIVATE_HEADER`: "not
  declared public" is an absence of evidence, and only a *confident* private
  origin licenses public-surface scoping to drop a finding — so the fix
  cannot cost a library whose own public headers are split across include
  roots a real breaking change (raised as a P1 by Codex's security review on
  this PR). This
  closes the dependency-tree half of `docs/contribute/known-gaps.md`'s
  "An `-I` include root makes another library's public headers this
  component's export obligations"; the sibling-libraries-sharing-one-include-
  tree half (the PVXS case) is unchanged and still recorded there.

- **A newly exported symbol is an addition again** — the seeding that
  demotes *property* churn on an undocumented export was also demoting the
  **appearance** of one, so a release that genuinely grew its exported
  surface reported `Additions: 0`, recommended `PATCH` instead of `MINOR`,
  and labelled an export-table-backed symbol `not-exported`. The rule is
  side-aware now: a removal is answered against the side that still had the
  symbol, an addition against the side that newly has it. Persistence and
  removal of an undocumented export keep their existing treatment.

- **A comparison's exclusion identity can no longer collide with a different
  one** — three separate ways two genuinely different compared surfaces
  shared one `surface.exclude_headers` value, and so one configuration
  digest. The patterns were joined on a comma, which is not injective over
  arbitrary glob text (`["a", "b,c"]` and `["a,b", "c"]` both rendered
  `glob:a,b,c`); they are now JSON-encoded. A comparison read only its
  non-empty side, collapsing "baseline narrowed", "candidate narrowed" and
  "both narrowed" onto one value — reachable because
  `compare(..., diagnostic_comparison=True)` skips the comparability check
  that otherwise refuses an asymmetric pair; both sides are now kept when
  they differ. And a directory/package release derived its identity from the
  rule set the command line *requested* rather than from what its member
  comparisons *observed*, which is not the same fact for a stored snapshot
  (never restamped with the current request); it is now read off each
  completed member, with disagreeing members staying distinguishable.

- **The header graph agrees with declaration provenance about a dependency
  root** — `buildsource.header_graph` called the shared include-root fold
  but kept only its first result, discarding the compile-only half, so a
  declaration reached solely through a dependency `-I` root was `UNKNOWN`
  through `apply_provenance` and `PRIVATE_HEADER` in the graph. That is the
  disagreement `extract.public_root_ownership` exists to prevent, and
  `PRIVATE_HEADER` is the confident demotion public-surface scoping acts on
  to drop findings — so graph consumers could act on evidence the run does
  not have. The classification context is now bound once for every consumer
  in that module rather than threaded as four separate values, which is what
  allowed one call site to drop one of them.
