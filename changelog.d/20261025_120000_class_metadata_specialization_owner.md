### Fixed

- **A removed vtable/typeinfo of a template specialization or a dtor-only
  class is now confirmed by its header** — under `--contract public`, a
  `_ZTV`/`_ZTI`/`_ZTS`/`_ZTT` removal of, say, `api::Box<int>` or of a class in
  an inline namespace whose only virtual is its destructor used to read
  `NOT_EVALUATED` and not gate. It is now in contract when a public record's
  own mangled member (a virtual slot, or its implicit `operator=`) spells the
  same class. A primary template, another specialization or a user-declared
  `operator=` still proves nothing.
