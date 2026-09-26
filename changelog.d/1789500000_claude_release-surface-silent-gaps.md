### Fixed

- ADR-075's "classified under different ownership rules" warning no longer
  fires on every sided `-H old=... -H new=...` comparison (once per release
  member). Without a project config each side's target roots are now
  recorded relative to that side's own header roots, not the shared VCS
  checkout, so two identical release trees fingerprint as one rule.
- The release-level public-surface acquisition now gets the include roots a
  member dump infers from `-H` (a `-H` directory is its own include root).
  Before, an umbrella header that wrote `#include <pkg/x.h>` relative to
  its `-H` directory failed to parse there alone, so no release export
  obligation was checked unless `-I old=/new=` was given too.
- A release side whose public surface could not be acquired now reports
  `coverage_complete: false`, and its warning says that no export
  obligation was checked. Under `assurance.require_complete: true` it also
  counts as an assurance shortfall (exit floor 1). Before, such a run
  exited 0 with coverage reported as complete.
- The Markdown "Release public surface" section lists at most 50 entries
  per list and says how many it left out. The JSON report still has every
  entry.
