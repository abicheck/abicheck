### Fixed

- **The opaque-type by-value scan's indirection check no longer mistakes a
  relational comparison for a template delimiter.** `_is_indirect_spelling`'s
  flat `<`/`>` depth counter treated every `>` as a closing bracket, so a
  by-value non-type template argument containing a parenthesized relational
  comparison (`ns::S<(N > 0), &handler>`) closed the outer template one `>`
  early on the comparison's own `>`, leaving the genuinely-nested `&handler`
  wrongly read as top-level indirection — masking a real by-value exposure
  and leaving the implementation type wrongly `opaque`, with its private
  layout change suppressed as a false negative. Since a relational operator
  used as a non-type template argument must be parenthesized to be valid
  C++, the check now also tracks parenthesis nesting and only lets a `<`/`>`
  affect the angle-bracket depth while parenthesis depth is zero — a
  relational operator's `<`/`>` always sits inside an open paren, a real
  template delimiter never does (Codex review on PR #1041).
