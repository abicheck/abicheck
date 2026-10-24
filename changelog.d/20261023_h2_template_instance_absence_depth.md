### Fixed

- **A member template's vanished implicit instance no longer reads as a
  removal at `--depth headers` or when the template is overloaded.** When an
  old header's inline function instantiated `Table::setArrays<double>` and the
  new header stopped doing so, `compare` reported `inline_function_removed`
  although the template was still declared. `--depth headers` cleared the
  header graph as L5 evidence, and with it the L2 declarations the
  template-instance-absence rule reads; the projection now keeps those
  declaration ids (no edges, never L5 evidence). An overloaded primary is
  now accepted when every old overload is still declared with the same
  signature; deleting the overload still reports the removal at both depths.
