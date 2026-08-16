### Fixed

- **The L2 header parse now applies a matched compile unit's own forced
  pre-includes (`-include`/`-imacros`/`/FI`).** A build that forces a
  macro-controlling header in parses its own public headers with that
  header's macros already defined; abicheck's derived L2 `CompileContext`
  never carried it, so `dump`/`scan`/`compare`'s implicit-dump path could
  parse a materially different translation unit — different `#if` branches,
  different struct layouts — while still reporting a real compile-unit match
  and stamping `AbiSnapshot.parsed_with_build_context`. A relative operand is
  resolved against the compile unit's own directory when that names a real
  file, and left relative otherwise so a generated header the build finds
  through its `-I` chain still resolves; MSVC `/FI` renders as GNU
  `-include`, matching how every other derived field is rendered.
  `-include-pch` (locked to the compiler build that produced it) and `/FU`
  (managed C++/CLI `#using`, naming no C/C++ header) are deliberately not
  forwarded. L4 source-ABI replay is unchanged: it already carried forced
  includes from raw argv, and the two paths now share one recognizer rather
  than the L2 fix routing through `CompileUnit.abi_relevant_flags`, which
  would have made replay emit every forced include twice.
- **A CL-mode compile command spelled with GNU `-c` is now recognised as
  MSVC dialect on the build-evidence side.** `dpcpp-cl` (Intel's oneAPI
  CL-compatible driver) and version-suffixed drivers such as `clang-cl-20`
  were known to the L4 source-replay path but not to the build-evidence
  adapter, so unless the command used `/c` they read as GNU dialect there —
  dropping a `/FI` forced include from the derived header-parse context, and
  mis-detecting `/Tp`/`/Tc` source and forced-language markers. Both layers
  now share one driver vocabulary.
- **A forced pre-include is now part of the compile-context ambiguity check.**
  Two translation units that reference the same public header but force in
  *different* macro-controlling headers previously collapsed into one
  context, silently applying whichever grouped first; they now fail closed
  with `HeaderCompileContextAmbiguousError`, the same way a `-std=`/target/
  define disagreement already did, and the error lists each conflicting
  translation unit's forced includes so the differing dimension is visible
  rather than leaving two apparently-identical rows. Two spellings of the
  *same* forced header (separate vs. joined, relative vs. absolute) still
  agree.
- **A forced pre-include is now hashed into the header-AST cache key.**
  Editing the forced header — the one macro-controlling input a parse depends
  on most — previously reused a stale cached AST, because it reached the
  parse only as an opaque compiler-option string. The file itself is hashed,
  not just its directory, so a forced include whose suffix is not a
  recognised header suffix (`-imacros settings.def`, or an extensionless
  `-include generated/config`) is covered too — the directory walk is
  deliberately suffix-filtered and would skip those. A forced include found
  only through the `-I` chain is hashed at each candidate location, since the
  bare operand alone resolves against abicheck's working directory rather than
  the build's. All four header-parse
  cache keys — the primary dump parse, the header-graph second pass, the
  PE/Mach-O header-scoped parse, and the L2 seed's own derived paths — now
  share one definition of what affects staleness.
- **The L2 include-directory seed is restricted to the compile units that
  actually compile the headers being parsed.** When no explicit `-I` is
  given, abicheck seeds include directories from the build's compile
  database; it gathered them from every translation unit, so in a multi-TU
  build an unrelated TU's own generated-header directory could shadow the
  matched TU's own colliding header (a stray `config.h`) on a run that then
  reported build context as applied. It now seeds from the matched units,
  falling back to every unit only when no unit matches — the case the seed
  was built for, where there is no narrower set to prefer.
