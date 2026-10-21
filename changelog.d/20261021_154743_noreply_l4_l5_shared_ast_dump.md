### Performance

- **L4 source replay and the L5 call/type/override/template/macro/callback passes
  share one `clang -ast-dump=json` per translation unit** — a TU that both
  tiers read used to be dumped twice, once under each tier's own argv. The two
  now come from one argv builder (the L4 context, with ABI-relevant flags
  carried through) and one bounded runner. The L4 surface is unchanged: its
  declarations, types and source edges are byte-identical on the C++ catalog
  cases. The L5 passes see the same edges, checked against the old parse-only
  argv. Three L5 details move with the shared argv: the dump runs under the
  same driver as L4 (plain `clang` runs as `clang++` when that is on `PATH`),
  its working directory is the effective build directory (so an `env -C` build
  resolves its relative include flags as the real build did), and, when both
  tiers run, a TU's dump is bounded by the L4 per-TU timeout (180 s by default)
  instead of the L5 120 s cap. An L5 dump no longer stops after clang's default
  20 errors, as the L4 dump never did. Code-loading options (`-fplugin=`,
  `-Xclang -load`) are removed from the shared argv before clang runs, as the
  L5 allowlist did.
