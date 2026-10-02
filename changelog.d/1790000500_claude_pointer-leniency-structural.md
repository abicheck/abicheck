### Changed

- An internal-namespace type (`detail::`, `impl::`, `internal::`, ...) reached
  from the public API only through a pointer no longer has a layout-only
  change dropped as out-of-contract on the strength of its namespace name.
  The leniency now applies only when the layout is proven invisible to
  consumers: the type is opaque, or defined in a project header outside the
  public set. With unknown visibility the change is reported, exactly as for
  the same type in any other namespace.
