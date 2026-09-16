### Changed

- **`--depth` projection no longer deep-copies a snapshot it does not
  rewrite.** `policy/depth_projection.py` answered every rung with one
  unconditional `copy.deepcopy`. At or above the `headers` rung that
  duplicated an entire L2 surface in order to rebind two fields
  (`build_mode`, `build_source`), neither of which is read-modified-written
  there — measured on a real 327-type/4,802-function header snapshot at +34%
  resident (0.516 → 0.691 GiB) and +12.5 s per comparison, paid once per
  member of a release fan-out, concurrently. The copy is now scoped to what
  each rung actually writes to: below `headers` the whole snapshot is still
  deep-copied (`_strip_header_and_above_evidence` rewrites declarations in
  place), a surviving `build_source` pack is still owned (it is degraded in
  place), and everything else is shared. The same projection now costs
  0.000 s and +0.000 GiB with byte-identical findings. Projecting still never
  mutates its argument — the guarantee every caller relies on — but the
  result is explicitly *not* an independently mutable copy at or above
  `headers`; `tests/test_depth_projection_ownership.py` pins both halves.

- **Dependency-header classification prepares its root set once.**
  `provenance.is_dependency_header` re-resolved and re-`stat`ed its entire
  `-H` root set on every call, and `dumper_scoping.scope_snapshot_excluding_
  dependencies` asks it once per declaration — twice over, since it re-scans
  the same lists to collect dependency types. The new
  `provenance.prepare_dependency_header_roots` hoists that filesystem work to
  once per root set, and the scoping pass memoizes by distinct declaring
  header: on a real 5,769-declaration snapshot resolving to 15 distinct
  headers, 0.375 s → 0.0006 s per pass, with classification unchanged
  (`is_dependency_header` is now a thin wrapper over the prepared context).

- **The `types`/`graph` section codecs dispatch on exact builtin types
  first.** `_freeze`/`_unfreeze` paid a `Mapping` ABC `isinstance` for every
  scalar leaf before returning it unchanged. The general `Mapping`/`list`
  checks are kept underneath, so custom mappings and `list` subclasses are
  handled exactly as before: `types` 0.0357 s → 0.0216 s, `graph` 1.3515 s →
  1.0147 s on a real snapshot's own sections, outputs identical.
