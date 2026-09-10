<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`compare` no longer rejects `--depth headers`/`build`/`source` on a
  directory or package operand** — the release fan-out carried a per-rung
  allow-list that accepted only `--depth binary` and answered every other
  rung with exit `64`, on the stated grounds that it "does not enforce a
  per-library evidence floor" and "does not collect inline build/source
  evidence". Both grounds had stopped being true: every member pair is
  resolved through `service.run_compare`, and a member may itself be a
  pre-dumped snapshot already carrying embedded L3/L4/L5 evidence, which
  satisfies `build`/`source` with no inline collection at all. Every rung is
  now forwarded verbatim to every member.

- **A pinned `--depth` that the evidence does not reach now means the same
  thing however the operands are packaged.** `compare` had two depth-floor
  mechanisms and which one you got depended on the surface: the native
  single-pair CLI recorded ADR-064's evidence-contract axis (exit `7`, for
  `build`/`source` only, with a stored-snapshot side carved out), while
  `service_compare_pipeline.resolve_compare_request` raised a hard
  `ValidationError` for *every* rung including `headers` and for stored
  snapshots too — pre-empting the recording in exactly the cases it was
  written for. A directory `compare` routes through the second and a
  single-pair one through the first, so the identical comparison exited `7`
  or `4` depending only on whether the two binaries sat in a directory.
  `resolve_compare_request` no longer calls `enforce_requested_depth`, so
  the exit-7 axis governs every `compare` surface; the release fan-out
  aggregates each member's contribution with `max()` alongside the
  contract-coverage floor it already folds, and explains it on stderr rather
  than exiting `7` silently. Every document a release publishes carries the
  fact too — the JSON/`summary.json` `exit` block, the `run_outcome`
  `operational` axis, a Markdown section, and JUnit `<error>` entries — since
  a release document is rendered *before* the exit is taken, so a report-
  driven consumer would otherwise read a run that exited `7` as clean. A run
  without an explicit `--depth` is
  unaffected, as are `dump`'s own floors and the stored-bundle-facts pair
  comparison, which keep their separate, separately-tested contracts.

### Documentation

- **Corrected the standing claim that a directory/package `compare` does not
  build the L2 header-only semantic graph** — it has built it per member,
  with the same nodes a single-pair compare of that member produces, since
  the fan-out was migrated onto `service.run_compare`. The claim survived in
  `abicheck/cli_resolve.py` and in G31 Phase A's plan note because only
  comments stated it; it is now pinned by an executed parity test rather
  than by prose.
