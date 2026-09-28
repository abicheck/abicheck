### Fixed

- The header-AST choke point (`dumper_manifest.resolve_header_ast_result`)
  now absolutizes relative `-I` roots for every caller, not only the binary
  dumps that already did. The release public-surface parse
  (`header_only_dump`) passed them as given, so with relative `-H`/`-I`
  paths it keyed apart from the member dumps of the very same headers: a
  second full castxml parse per release side, a second resident DOM, and
  reached headers spelled differently in that one snapshot.

### Performance

- `parse_elf_metadata`'s memo stores pickled entries instead of deep
  copies. A miss (every miss on the scalar path, where the two sides are
  different files) no longer pays a `copy.deepcopy` of the symbol table
  (7.3 s of a `libmkl_rt` compare), a hit loads ~6x faster, and the
  retained entry is one `bytes` object rather than a second live graph.
- `collect_and_flag` (closure-identity renumbering) expands a nested
  dataclass of leaves (a `Fact`, ~42 per function) inline instead of a
  recursive call per node: ~21% less time for the walk, identical output.
- `buildsource.type_graph._base_type_name` is memoized (1.47M calls on one
  MKL side, about two per distinct spelling).
- Idiom recognition's callback check and `build_public_use_index` answer
  each distinct parameter spelling once per pass (MKL: 215 spellings over
  27.5k functions) instead of re-entering the lock-guarded `strip_ptr` memo
  per site.
- `DigestMemo` and the header-scan memo are single-flight: concurrent misses
  on one key compute it once and the others wait, rather than every release
  worker repeating the same scan until the first one finished.

- The identity table and the export join one comparison derived several
  times per snapshot are computed once per comparison
  (`model/comparison_memo.py`, opened by `compare_snapshots` and the
  `checker.compare` wrapper). Outside a comparison nothing is cached.
- `Fact.present(True/False/None/<enum member>)` and the argument-less
  `not_collected()`/`unsupported()`/`not_applicable()` return their shared
  instance without building the flyweight key: `Function()` construction
  5.3 µs to 2.9 µs.
- The C/C++ string and character literal patterns of the C++20 header scan
  are unrolled (same matches, 11x faster over MKL's headers), and
  `match_export` filters spellings in C.

A second round on the same pair: three-library release 190 s to 158 s,
single `libmkl_rt` 130 s to 118 s, reports identical.

MKL 2024.2.2 → 2025.2.0, 4 CPUs, cold castxml cache: three-library release
228 s → 192 s (max RSS 2.05 → 1.94 GiB), single `libmkl_rt` 139 s → 131 s
(1.39 → 1.33 GiB). Reports identical.
