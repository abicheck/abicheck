### Fixed

- **A C++20 fixed-string NTTP literal that merely spelled out a
  ``(lambda at ...)``/``(unnamed ... at ...)``-shaped value could be
  rewritten as though it were a real CastXML anonymous-type location.**
  `name_classification.strip_anonymous_type_location()` matched any text
  shaped like a marker, including inside a quoted string literal (e.g.
  ``Tag<"(lambda at /a/foo.hpp:1:2)">``) — so two specializations quoting
  different paths that happen to share a basename could collapse onto the
  same identity. Fixed by skipping any match that falls inside a ``"..."``
  quoted span, leaving quoted literal content untouched while still
  normalizing a genuine, unquoted marker elsewhere in the same name.
