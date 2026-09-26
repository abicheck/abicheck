### Fixed

- Two byte-identical checkouts under different directory names no longer
  compare as changed at `--depth source`. The L5 body fingerprint and the
  `decl://…#sha256` signature key hashed clang's `(lambda at /abs/path:L:C)`
  spelling verbatim, producing `declaration_renamed` (e.g.
  `std::shared_ptr::make_shared#sha256:…`), `inline_body_changed` and
  reachability findings from the directory name alone. Both now hash the
  checkout-stable spelling (`model.graph_identity.checkout_stable_spelling`).
- A relative `-I`/`--include` root no longer makes one snapshot mix relative
  and absolute `source_header` spellings: `dumper.dump` absolutizes include
  roots the way the generated umbrella header already spells its headers.
- Without a project config, ownership rules are now recorded relative to the
  enclosing VCS checkout (`.git`/`.hg`/`.svn`), so two relocated checkouts no
  longer warn that they "were classified under different ownership rules".
  Roots outside a checkout, or spanning two, are still recorded absolute.
