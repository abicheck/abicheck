### Fixed

- **`resolved_config_from_dict` now distinguishes a genuinely absent
  `gate.require_complete_analysis` key from an explicit JSON `null`.**
  The previous strict-boolean decoder took a pre-fetched
  `gate.get("require_complete_analysis")` value, which collapses both
  cases to Python `None` and silently defaulted an explicit `null` to
  `False` instead of rejecting it as malformed — the same bypass of
  `GateConfig.__post_init__`'s own strict check the first fix closed for
  a truthy non-boolean value, just for the null case (Codex review, PR
  #817). A present `null` now raises; a genuinely absent key still
  degrades to the documented default.
