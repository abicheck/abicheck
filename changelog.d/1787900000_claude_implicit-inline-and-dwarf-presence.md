### Fixed

- **The clang header backend now models C++'s *implicit* `inline` rules, not
  just the explicit keyword.** `clang -ast-dump=json` emits its `inline` key
  only for a written `inline` specifier, so six everyday shapes reached the
  model as non-inline: a `constexpr`/`consteval` function; an ordinary member
  defined in its class body; an in-class `= default`; a **hidden friend**
  defined in its class (`friend bool operator==(const W&, const W&) { … }`,
  which clang emits as a plain `FunctionDecl` under a `FriendDecl` rather than
  a member node); a member of an **unnamed** record (`typedef struct { … } W;`,
  whose scope the parser spells `Anonymous(kind="struct")`, never `Record`);
  and a body clang wraps in a node of its own — a **function-try-block**
  (`void f() try { … } catch (...) { … }`, a `CXXTryStmt`) or a **coroutine**
  (`Task f() { co_return; }`, a `CoroutineBodyStmt`). The castxml backend reported every
  one of them as inline, so the two disagreed on the same declaration while
  `scripts/backend_capabilities.py` claimed full parity for the fact.

  The visible symptom was a `Confidence.HIGH` `public_not_exported` finding
  demanding that a header-defined `constexpr` constructor be exported, on a
  library whose consumers link and run correctly because the definition comes
  from the header. `Function.is_inline` now means inline linkage, explicit or
  implicit, on both backends (`extract/headers/clang/inline_semantics.py`).
  `= delete`d functions are included too, at any scope, per
  [dcl.fct.def.delete]/4 — excluding them made an `inline void f();` becoming
  `void f() = delete;` emit a spurious `func_lost_inline` beside the true
  `func_deleted`. A member merely *declared* in its class and an out-of-line
  definition at namespace scope keep their export obligation, so the false
  positive is not traded for a false negative.

  This also removes spurious `func_became_inline`/`func_lost_inline` findings
  from any comparison whose two sides were captured with different frontends,
  and correspondingly narrows `func_deleted_elf_fallback`. One transitional
  note: a baseline dumped with the opt-in clang backend *before* this fix
  carries the old value, so comparing it against a freshly-dumped candidate
  reports `func_became_inline` (RISK, never breaking) for each affected
  declaration until that baseline is re-dumped. castxml baselines are
  unaffected. See `docs/contribute/known-gaps.md`'s
  `clang_inline_facts_reliable` entry for why the durable fix is a separate,
  schema-versioned change.
- **`L1` evidence coverage no longer reports `present` for a binary with no
  debug info.** Both debug-metadata classes are plain dataclasses with no
  `__bool__`, and every ELF dump attaches one unconditionally — including the
  symbols-only fallback that logs "no DWARF debug info" while attaching it —
  so `bool(snap.dwarf or snap.dwarf_advanced)` asked whether a container
  existed, which is always true, rather than whether anything was collected. A
  stripped library was reported as `L1: present / confidence: high / detail:
  DWARF` in the same run whose check list said "no debug info". The new
  `model.debug_info_present` is the one supported way to ask the question, and
  the three sibling sites that read presence the same way are routed through
  it: `surface_graph`'s evidence tier (under which `elf_only` was unreachable
  on the normal dump path), `diff_helpers`' typedef key-shape trust, and the
  `advanced_dwarf` detector's support gate (which reported "ran, found
  nothing" where the truth was "never evaluated"). BTF and CTF still count as
  L1, since both reduce to the same `has_dwarf` flag.

  That last gate needed one further distinction:
  `AdvancedDwarfMetadata.has_dwarf` is itself overloaded, set from a bare
  section lookup on the presence-only paths (`--depth binary`,
  `symbols_only`) that deliberately parse no payload, so the new
  `model.advanced_facts_collected` answers from the fields the detector
  actually reads.
