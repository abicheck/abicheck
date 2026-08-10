### Fixed

- **Direct-clang vtable reconstruction now picks up a template default
  legally added on a later redeclaration.** `_register_template_param_metadata`
  previously kept a class template's tracked kinds/defaults/names frozen at
  the FIRST declaration's value on every confirmed redeclaration — correct
  when the later declaration only renames parameters, but wrong when it
  legally adds a default the first declaration never had (e.g.
  `template<class T, class U> struct A;` followed by `template<class T,
  class U=T> struct A {...};`, one C++ entity whose effective default for
  `U` only becomes visible on the second declaration). Dropping the added
  default left dependent-default substitution unable to trim a trailing
  template argument, mis-indexing a specialization and leaving an inherited
  vtable invisible — hiding a real virtual-method addition as `NO_CHANGE`.
  Fixed by positionally merging a confirmed redeclaration's value into the
  tracked one instead of keeping it frozen: a position the tracked value
  already has data for still wins (preserving the original declaration's
  spelling), only an empty position adopts the new declaration's value.
