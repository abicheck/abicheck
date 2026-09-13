### Fixed

- **A cache-sequence scenario never reset its cache between repetitions.**
  `check_l2_cli_perf.py` reset only for `cache_mode="cold"`, so the
  `cold_then_warm` and `invalidation_control` sequences carried one repetition's
  cache into the next: at `--repeat 2` the second repetition's step *named*
  `cold` was served by the first's cache, observed zero header extractions, and
  failed its own contract. The extended CI lane runs `--repeat 3`, so it would
  have failed deterministically; every local run that missed it used
  `--repeat 1`, where the bug cannot appear. The two lifecycles are now
  distinguished explicitly — a `cold` scenario resets before every step, a
  sequence resets once before its first.

- **A base measurement that failed its own validation could still gate a PR.**
  The receipt is written before the exit code is decided (deliberately — a failed
  run's numbers are diagnostic), and a base that fell back to binary-only
  evidence is *faster* than a correct one, so gating against it compared the head
  to a number no correct run produces. `gated_points()` now takes points only
  from scenarios whose `status` is `ok`, on both sides, and `load_baseline()`
  reports the scenarios it refused. The CI job also records the base run's exit
  status instead of discarding it.

- **Three defects in the real-integration profile definitions**, all of which
  made a profile unrunnable while it still validated as correct: placeholder
  revisions rendered into `git checkout <pinned-base-sha>`, where the shell reads
  `<` as input redirection (now pinned to real verified SHAs for all three
  profiles, and a placeholder is both a validation finding and a `BLOCKED`
  status naming that as the blocker); every command was appended into one shell,
  so a `cd svs` in one leaked into the next and made it look for `svs/svs` (each
  command now runs in its own subshell rooted at the script's start directory);
  and preparation checked out only `old_revision`, so no script could produce
  the old-vs-new pair a temporal comparison needs (per-side commands now build
  both). The generated scripts are `bash -n` parse-checked in tests.
