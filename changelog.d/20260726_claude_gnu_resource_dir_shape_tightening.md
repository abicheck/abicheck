<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`_is_gnu_compiler_resource_dir` over-matched any `lib`/`gcc` adjacency,
  not just GCC's actual resource dir** (follow-up to the `realpath`
  symlink-awareness fix above, `dumper_sysinc.py`): the classifier scanned
  for a multilib segment (`lib`/`lib32`/`lib64`/`libx32`) immediately
  followed by `gcc`/`gcc-cross` *anywhere* in the path, so a real
  libstdc++/libc dir merely nested somewhere underneath a
  `lib/gcc/<triple>/<ver>/...` tree — not GCC's own intrinsics dir itself —
  was misclassified as the resource dir and dropped, starving clang of real
  standard-library headers (Codex review, PR #643, round 3; concretely
  demonstrated with a `-isystem` dir reached via a symlink whose target
  physically resolves under a `lib/gcc` tree, e.g.
  `/opt/lib/gcc/toolchain/13/include/c++/13`). Tightened to match the *full*
  documented shape at the end of the path —
  `lib{,32,64,x32}/gcc[-cross]/<triple>/<ver>/include[-fixed]`, exactly five
  trailing segments — instead of a bare adjacency scan, so only the literal
  resource dir (or `include-fixed`) matches, not anything merely living
  inside the same subtree.
