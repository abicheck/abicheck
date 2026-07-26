### Fixed

- **F8 additive-only header-set carve-out now reaches the real production
  invocation, and comparability carve-outs compose** — the real
  `-H old=<dir> -H new=<dir>` CLI shape auto-adds the header-owning
  directory as a declared include (`resolve_inferred_header_roots`),
  changing `profile_fields["include_sequence"]` alongside
  `header_sequence`; added a fourth carve-out
  (`_include_sequence_is_additive_owned_growth`) covering it. Separately,
  the profile check required `differing` to match one carve-out's static
  field-set *in full*, so a release combining two independently-sanctioned
  deltas (e.g. a header addition and a corroborated C++-standard raise)
  still raised even though each half was individually fine. Restructured
  the check so each carve-out claims and verifies only the subset of
  `differing` it understands, composing correctly. A header added outside
  the old side's common ancestor directory remains a documented, safe
  (hard-failing, never silently wrong) limitation, out of scope for the
  real pvxs F8 case this carve-out targets.
