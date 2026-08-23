### Fixed

- **`strip_anonymous_type_location`'s location-stripping regex was
  unanchored, and its (already-narrowed) whitespace cleanup still touched
  unrelated parts of a composite name.** Two more real gaps from the same
  review round as the previous fragment:
  - The regex matched a bare `at <path>:<line>:<col>` anywhere in a name,
    so an ordinary specialization whose C++20 fixed-string NTTP argument
    merely *contained* location-shaped text (e.g. `Tag<"at
    /checkout:1:2)">`) was rewritten too, risking a collision with a
    genuinely distinct type. The regex now requires an actual `(lambda` or
    `(unnamed <kind>` marker immediately before `at`.
  - Gating the whitespace collapse on "did the substitution fire
    anywhere" (the previous fix) wasn't narrow enough: a *composite* name
    where the substitution legitimately fires in one template argument (a
    real lambda marker) could still have the collapse rewrite whitespace
    in an unrelated sibling argument (a fixed-string literal). The
    now-anchored regex captures its own marker and reconstructs it
    directly, so the substitution never introduces stray whitespace to
    clean up — the whitespace-collapse step is removed entirely rather
    than narrowed further.
