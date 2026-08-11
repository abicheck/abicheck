<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Loading an older-schema snapshot that degraded a `*_facts_reliable` flag
  was silent** (`serialization.snapshot_from_dict`): the hard-rejection and
  version-mismatch warning at the top of the function only ever covered a
  snapshot *newer* than the reader. A snapshot *older* than the reader — the
  direction every CI baseline actually hits, since a baseline is committed
  once and outlives however many abicheck pin bumps happen before it's next
  regenerated — loaded with no signal at all, even when the version gap
  meant a real, known fact (e.g. clang-backend deprecation/vtable/restrict/
  va_list facts, or CastXML CV/variable-access facts) was marked unreliable
  and silently excluded from detection. `snapshot_from_dict` now emits a
  `UserWarning` at load time naming exactly which `*_facts_reliable` flags
  got degraded, so a stale committed baseline's reduced detection coverage
  is visible instead of discovered later. Stays silent for a version gap
  that doesn't actually degrade anything, so an ordinary one-version-behind
  baseline isn't warned about for no reason — including for
  `clang_va_list_facts_reliable`/`castxml_var_access_facts_reliable` on a
  `"hybrid"`-producer snapshot, where the flag's own value is conservatively
  `False` but no detector actually consults it for that producer. The
  warning also survives a re-save: `snapshot_to_dict` always re-stamps
  `schema_version` to the current value, so a degraded flag preserved
  through an explicit marker on a reserialized legacy snapshot now still
  triggers the warning instead of silently disappearing once the version
  number itself reads as current.
