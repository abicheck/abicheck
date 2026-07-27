<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`_is_gnu_compiler_resource_dir` compared unresolved path segments**
  (castxml↔clang system-include parity, `dumper_sysinc.py`): the classifier
  walked `Path(path).parts` on the *raw, unresolved* string a host
  compiler's `-v` probe reports, without normalizing it first. GCC and
  Intel's `icpx`/`icx` both report real search dirs with a literal
  `../../../../` walk-back baked in (e.g.
  `/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13`, which is
  really `/usr/include/c++/13` — genuine libstdc++, not GCC's own resource
  dir). Because the string was never normalized, the `lib`/`gcc` segments
  from the walked-*through* prefix still matched, and a real libstdc++/libc
  dir was misclassified as GCC's compiler-internal resource dir and dropped
  from the probe results — silently narrowing the `-isystem` dirs injected
  into the clang L2 backend on any host using this reporting style. Fixed
  by lexically normalizing (`os.path.normpath`, no filesystem access or
  symlink resolution) before splitting into parts, so `..` segments collapse
  before the multilib/`gcc` match runs.
