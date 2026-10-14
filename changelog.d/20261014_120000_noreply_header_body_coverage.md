### Fixed

- **Header-only graphs now judge inline-body calls per body** — the header
  pass records which function bodies it parsed in full (`header_body` on the
  declaration's graph node), and `PUBLIC_API_INTERNAL_DEPENDENCY_ADDED` now
  reports a call or reference added inside an inline function whose body was
  seen on both sides, instead of declining every body-dependent edge. A body
  that is out of line on either side is still declined, never reported as
  clean or as a new dependency.
