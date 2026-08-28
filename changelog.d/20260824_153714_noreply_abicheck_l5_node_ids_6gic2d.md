### Fixed

- **L5 source-graph decl/type node ids and labels no longer embed a
  checkout-dependent directory for an anonymous-tag or lambda-closure type.**
  `abicheck/buildsource/source_graph.py`'s `_decl_node_id`/`_type_node_id` —
  the one choke point every L5 producer (`type_graph.py`, `call_graph.py`,
  `override_graph.py`, `callback_graph.py`, `template_graph.py`,
  `header_graph.py`, `macro_graph.py`, `source_graph.py` itself) routes a
  decl/type identity through — now strip the absolute path out of a raw
  `"(unnamed struct at /a/foo.h:56:5)"`/`"raii_guard<(lambda at
  /a/foo.h:4:37)>"` spelling (as `dumper_castxml.py`'s L2 backend already
  does via `strip_anonymous_type_location`) *and* a bare, unparenthesized
  `"lambda at /a/foo.h:4:37"` spelling observed directly in real L5 graphs.
  Previously, two builds of the identical, unedited declaration under
  different checkout roots produced two different node ids/labels, which
  `graph_reconcile.py`/`diff_source_graph_findings.py` read as a real
  `declaration_renamed` risk finding purely from directory taint.
