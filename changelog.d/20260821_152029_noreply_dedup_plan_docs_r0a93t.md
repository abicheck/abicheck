### Fixed

- **`evaluation_context_from_dict` now rejects a `schema_version >= 2`
  payload that is missing `gate.require_complete_analysis`/`gate.scope`,
  instead of silently defaulting them.** This build's own writer always
  emits both keys once it stamps `schema_version` 2, so a payload
  declaring that version with either key absent is truncated or
  hand-crafted, not a legitimate older-writer omission — the same
  distinction `resolved_config_from_dict` already drew for a present but
  wrong-typed or explicitly-null value, extended to cover absence itself
  once the enclosing schema version says the key should exist (Codex
  review, fresh evidence, third round). A genuinely older
  (`schema_version < 2`) payload still degrades to the documented
  defaults, per this module's "no lossy defaults on read" rule for
  legitimate forward compatibility.
