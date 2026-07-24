### Fixed

- **The C++20 dialect detector now recognizes a parenthesized
  requires-clause starting its own line** right after a `template<...>`
  header (`template<class T>` / `requires (sizeof(T) > 4)` / `void f(T);`
  on separate lines). Nothing precedes "requires" on its own line in this
  shape, which previously always meant a bare pre-C++20 call/declarator
  without considering the previous line's template context, the same
  cross-line check already used for concept declarations.
- **Hidden-friend surface classification now falls back to a bare-name
  public check when only one side has an exact qualified owner match.**
  When one snapshot has an exact `origin_by_qualified_key` entry
  (private/system) for a hidden friend's owner but the other snapshot never
  populated `qualified_name` for that record, the other side's bare-name
  origin can still prove the owner is public — the qualified path
  previously ignored that side entirely and could demote a friend whose
  owner is genuinely public.
