### Fixed

- **The clang header backend now models C++'s *implicit* `inline` rules, not
  just the explicit keyword.** `clang -ast-dump=json` emits its `inline` key
  only for a written `inline` specifier, so a `constexpr`/`consteval`
  function, an ordinary member defined in its class body, and an in-class
  `= default` all reached the model as non-inline — while the castxml backend
  reported all three as inline, despite `scripts/backend_capabilities.py`
  claiming full parity for the fact. The visible symptom was a
  `Confidence.HIGH` `public_not_exported` finding demanding that a
  header-defined `constexpr` constructor be exported, on a library whose
  consumers link and run correctly because the definition comes from the
  header. `Function.is_inline` now means inline linkage, explicit or implicit,
  on both backends (`extract/headers/clang/inline_semantics.py`); a member
  merely *declared* in its class, an out-of-line definition at namespace
  scope, and a `= delete`d member keep their export obligation. This also
  removes spurious `func_became_inline`/`func_lost_inline` findings from any
  comparison whose two sides were captured with different frontends, and
  correspondingly narrows `func_deleted_elf_fallback`. One transitional
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
