# Experimental native GCC header extraction

Select native GCC when the public contract must reflect the GCC compiler
used for the library. This backend runs GCC itself, without CastXML/Clang
emulation. It is opt-in and reports partial assurance while coverage is
experimental. `auto` keeps its existing backend selection.

Install matching GCC plugin development headers (for example the
`gcc-14-plugin-dev` package alongside GCC 14). Build both frontend plugins
once for that compiler:

```sh
python -m abicheck.extract.headers.gcc.build --compiler g++-14
```

The build writes to the version/target-specific abicheck user cache. A scan
never compiles a plugin. For a custom location use `--output DIR`, then set
`ABICHECK_GCC_PLUGIN=DIR`; the environment variable also accepts a single
plugin shared object. C and C++ need different plugin files. A plugin built
against another GCC build is rejected, with no Clang fallback.

Configure the usual `.abicheck.yml` compilation context:

```yaml
compile:
  frontend: gcc
  compiler: /usr/bin/g++-14
  lang: c++
  std: c++20
  include_dirs: [include]
  defines: [API_LEVEL=7]
```

Then use the existing workflow:

```sh
abicheck dump old/libexample.so -H old/include --config .abicheck.yml -o old.json
abicheck dump new/libexample.so -H new/include --config .abicheck.yml -o new.json
abicheck compare old.json new.json -o json=report.json
```

Selecting a compiler executable requires an explicitly trusted `--config` file. An auto-discovered repository config cannot silently select an executable.

For C, select `lang: c`, an appropriate C standard and the actual GCC
compiler. Header-only dumps work by omitting the binary operand. The same
compiler context can be supplied through the typed API. An existing
`ABICHECK_AST_FRONTEND=gcc` environment pin also selects the native backend;
an explicit config/API selection takes precedence.

Native GCC provides computed layout, declaration identities, variables,
typedefs/constants, constructor/destructor variants, template defaults,
opaque concept/requires change evidence and vtables. Raw macro definitions,
TLS/volatile facts and full vtable slots are retained in provenance, with
known limitations. `analysis_assurance.status` stays `partial`, including
when comparing saved snapshots; a breaking verdict can still be conclusive.

Initial scope: Linux ELF and header-only C/C++, one translation unit built
from the selected headers, tested on GCC 13/14. Dump manifests, SYCL/device
contexts and source-ABI replay are rejected. A native header graph, full
macro comparison and complex template coverage remain future work. No
implicit Clang header-graph acquisition runs for this backend. The snapshot
cache includes the native compiler and plugin content identities.
