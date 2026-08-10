<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`explain_use_case_impact()` walked only one of several declarations
  mapped to the same exported symbol** (G29 Phase 4, Codex review): when
  more than one `SOURCE_DECL_MAPS_TO_SYMBOL` edge targets the same export
  — an inline/weak definition captured once per TU is the common case —
  a plain `dict[str, str]` kept only the first declaration seen
  (edge-iteration-order dependent). A manifest entry naming that exact
  binary symbol could then walk from a declaration with no captured call
  edges while the sibling declaration carrying the real body — and its
  transitive calls — was silently never walked. Fixed by preserving every
  mapped declaration and walking each possible root.

- **`type_graph.py`'s `enum_underlying` role missed a field- or
  variable-declared anonymous enum** (G29 Phase 5 item 5, Codex review):
  `struct Public { enum : U { A } value; };` gives the enum no id-based
  linkage at all — unlike the typedef/alias idiom fixed earlier, clang's
  `FieldDecl`/`VarDecl` carries only a location-encoded type spelling
  (`"enum (unnamed enum at ...)"`), no `ownedTagDecl`/`decl` id — so the
  underlying-type dependency (`detail::Handle`) was silently dropped and
  only an unresolved noise edge to the anonymous spelling was emitted.
  Fixed by recognizing the marker spelling on an anonymous enum's
  immediately-following field/variable declarator sibling and attributing
  the dependency to the owning record (for a field) or the variable's own
  declaration (for a namespace/class-scope variable) — the only public
  identities available for an otherwise-anonymous enum. Verified against
  real Clang 18 output for both shapes.
