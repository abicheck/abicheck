### Fixed

- **`strip_anonymous_type_location`'s whitespace collapse could rewrite
  meaningful whitespace inside an ordinary type name.** Once wired into
  every castxml record/enum name at extraction time (not just anonymous
  ones), its unconditional multi-space-collapse/strip step also touched
  names with no anonymous-location marker at all — notably a C++20
  fixed-string NTTP template argument, where `Tag<"a  b">` and `Tag<"a
  b">` are genuinely distinct specializations that must not collapse onto
  one identity. The collapse now runs only when the anonymous-marker
  substitution actually fired.
