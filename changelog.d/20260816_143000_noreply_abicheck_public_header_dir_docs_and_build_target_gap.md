### Documentation

- Updated the `public-header-dir` Action input description (and regenerated
  `docs/reference/github-action-inputs.md`) to state that a scalar `scan
  --against` forwards it as a candidate-sided `-H new=...` root, not the
  bare `-H` root the earlier wording claimed — matching the sided-forwarding
  fix above. Audit-only and `--artifact-set` scans still use the bare form.
- Recorded a new "Known gaps" entry in `AGENTS.md`: `--build-target`
  currently has no effect when combined with a pre-captured Bazel
  `--build-info` (an `aquery`/`cquery` jsonproto) on either `dump` or
  `scan` — `BazelAdapter.collect()` only applies target scoping to a
  *live* `bazel query`, never to an already-captured file. Investigated in
  depth (both a real-filtering fix and a reject-the-combination fix), found
  to be a genuine multi-call-site feature either way, and deliberately not
  attempted as a same-session reactive patch — see the entry for the full
  analysis and the safe workaround in the meantime.
