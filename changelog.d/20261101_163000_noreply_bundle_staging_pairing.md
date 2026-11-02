### Fixed

- **A `check-project.yml` bundle check now compares its members** — the
  candidate was staged under each member's build basename
  (`bundle-staging/libfoo.so.1`) while `actions/baseline` stages the baseline
  as `binaries/libfoo` (the target id), and `compare` pairs native members by
  file name, so no member paired: every one read one-sided and the bundle
  compared nothing. Each candidate member is now staged under its target id.
  Two members resolving to one file are still refused; two members whose
  files merely share a basename no longer collide.
