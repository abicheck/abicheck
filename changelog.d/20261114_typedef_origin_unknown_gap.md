### Fixed

- **A typedef change demoted for lack of recorded header provenance now says
  so** — a header-backed snapshot classified against a public-header set but
  carrying no `typedef_origins` entry for a typedef (a pre-generation-62
  snapshot, or an unattributed spelling) used to drop a change to an unused
  public typedef out of the public surface as `no-provenance` with no stated
  gap, so a BREAKING run read as `NO_CHANGE`. The demotion is now labelled
  `header-origin-unknown`, which lowers scope confidence and adds the
  coverage warning every report format shows. `source_level_kind_changed` is
  also no longer bucketed as a binary-gated API break (it is COMPATIBLE).
