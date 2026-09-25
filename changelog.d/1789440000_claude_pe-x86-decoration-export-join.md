### Fixed

- The observed `exports` join now relates a 32-bit x86 PE export's C
  calling-convention decoration to its declaration: `_foo@8` (`__stdcall`),
  `@foo@8` (`__fastcall`), `foo@@8` (`__vectorcall`) and `_foo`
  (`__cdecl`) join the declaration spelled `foo` instead of staying
  `unmatched`. The alias applies only on the machine types that decorate,
  never to a C++-mangled name or on an unknown machine, is refused when
  another declaration owns the decorated spelling exactly, and leaves a
  collapse onto one export `ambiguous`.
