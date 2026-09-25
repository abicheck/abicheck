### Changed

- **The `compare --contract` replay type graph now keys every declaration and
  type by its Phase 1 entity node id (report schema 5.6, `contract_evidence`
  schema 2).** `contract_context.contract_evidence`'s type graph, each
  provider's `declarations`, and the receipt's `evaluated_contract_roots`/
  `evaluated_type_closure` used their own `decl:`/`record:`/`enum:`/`typedef:`
  keys, a second identity scheme beside `model/graph_entity_identity.py` that
  merged what that module keeps apart (two ODR-distinct records sharing one
  name, identical unmangled overloads). They are now the same
  `decl://`/`type://`/`unresolved://` ids the L2 header and public-surface
  graphs use, with a `name:<spelling>` exact tier beside `alias:`. Replay and
  re-evaluation decisions are unchanged, and a report written before this
  change (`contract_evidence` schema 1) still replays and re-evaluates to the
  decisions it gave then: its graph is read as written and never remapped
  onto the new ids, since a schema-1 `decl:over` does not say which unmangled
  `over` it stood for. A consumer parsing the old node keys itself must check
  `contract_evidence.schema_version`.
