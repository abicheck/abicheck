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
  resolved through `service.run_compare`, which floor-checks it with the
  same `enforce_requested_depth` a single-pair compare runs and projects it
  with the same `project_pair_to_depth` — and a member may itself be a
  pre-dumped snapshot already carrying embedded L3/L4/L5 evidence, which
  satisfies `build`/`source` with no inline collection at all. Every rung is
  now forwarded verbatim to every member, and a member that falls short of
  the requested rung fails as *that member's* `ERROR` result
  (`operational: extraction_error`, `scope: incomplete`) naming the side and
  the depth it actually reached, instead of one whole-run usage error that
  named no member. The removed errors' guidance is not lost: the
  release-shaped alternatives (pre-dump members with `dump --sources`/
  `--build-info`, or compare the library on its own) are appended to that
  per-member failure, where they apply.

### Documentation

- **Corrected the standing claim that a directory/package `compare` does not
  build the L2 header-only semantic graph** — it has built it per member,
  with the same nodes a single-pair compare of that member produces, since
  the fan-out was migrated onto `service.run_compare`. The claim survived in
  `abicheck/cli_resolve.py` and in G31 Phase A's plan note because only
  comments stated it; it is now pinned by an executed parity test rather
  than by prose.
