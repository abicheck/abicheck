### Removed

- Deleted four functions nothing in the package referenced:
  `internal_leak._typenode_is_indirection_wrapper`,
  `frontends.cli.runtime._validate_show_only`,
  `report.render_markdown.render_severity_sections`, and
  `post_processing._safe_index` — the last was never called in its own
  module and had one test as its only consumer, now pointed at the live
  `diff_filtering` implementation it duplicated.

### Changed

- Seven byte-identical helper implementations now have one owner each, with
  every caller importing it: the Mach-O Itanium underscore strip (five
  copies across `buildsource/{call,type,macro,override}_graph.py`, folded
  onto `model.mangled_name.strip_macho_itanium_decoration`, which already
  declared itself their canonical home), plus `layer_payload_empty`,
  `_change_covers_symbol`, `_freeze`, `_type_identifiers`,
  `_split_top_level_commas`, `_resolve_under_root` and `_frozen_tuple`.
  Each owner is the module the other already depended on, so no new import
  edge or cycle was introduced.
