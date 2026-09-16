### Fixed

- A shared `--header`/`--include` no longer disappears when either side names
  one of its own. `split_sided_paths` has always documented the "both-sides
  value plus per-side additions" model, but every consumer implemented
  replacement, so `--include shared --include old=... --include new=...`
  resolved to `old`/`new` alone and the parse then failed on the dependency
  the shared root supplied. One rule now governs every front end —
  single-pair `compare`, the release/directory fan-out, the no-baseline and
  bundle-facts paths, and the `--dry-run` receipt — in
  `model/sided_inputs.py`: side-specific entries first (so they keep search
  precedence), shared entries after. Disjoint search paths stay expressible
  by naming nothing shared.
- A descriptor's `<skip_headers>`/`<skip_including>` are matched by ABICC's
  own three rule classes instead of an exact basename-or-path membership
  test: a bare name matches a file name, a value containing a separator
  matches a tree-relative path — or, with a trailing separator, a directory
  and its descendants — at component boundaries, and a value carrying `*`,
  `?` or `[` is compiled as a pattern, with an invalid one rejected by name.
  Every tree-relative rule previously matched nothing at all while the run
  still produced a confident verdict (Intel MKL's descriptor carries sixteen
  such rules). The two elements are also no longer merged: `<skip_headers>`
  is recorded as the snapshot's achieved narrowing, `<skip_including>` is
  not, and a rule that matched no header is reported rather than recorded.
  Descriptor-narrowed snapshots now record `excluded_header_matching`
  `"abicc"` rather than `"exact"`.
- A descriptor that states no `<include_paths>` now gets ABICC's automatic
  include-path mode: its own `<headers>` roots are resolved through the same
  `resolve_inferred_header_roots` every other entry point uses, so an
  umbrella header's `#include <sibling.h>` resolves without a hand-added
  element. `<include_paths>` and `<add_include_paths>` are kept as separate
  fields, since ABICC selects that mode by the absence of the former.
- Binary-level churn on an export no public header declares is now demoted to
  the filtered audit ledger with the `not-exported` reason instead of being
  reported. The export table proves the symbol exists and the resolved header
  surface proves nothing declares it, but `PublicSurface.all_symbols` held
  only modeled declarations, so the classifier read the symbol as *unknown*
  and the conservative-unknown rule kept every finding. `exported_not_public`
  itself is never filtered, so the evidence explaining the demotion stays in
  the active report.

### Changed

- A rolled-up finding kind in a multi-library run now carries a per-library
  breakdown (`KindRollup.counts_by_library`), rendered as "By library: …".
  A single-library run is unchanged.
