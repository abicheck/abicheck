# ADR-029: Build Graph and Toolchain Context Capture

**Date:** 2026-06-09
**Status:** Accepted — implemented (BuildEvidence model; compile DB, CMake
File API, Ninja, Bazel `cquery`/`aquery`, and Make dry-run adapters;
compiler-recorded metadata extractor; build-evidence diff and the six D9
change kinds). Only Phase 7 (CI rollout + baseline registry) remains, tracked
under ADR-033.
**Decision maker:** Nikolay Petrov

---

## Context

ADR-020a accepted `compile_commands.json` ingestion (`-p` / `--compile-db`)
to reduce header parse drift. That is the right first step, but it captures
only translation-unit compile commands. It does not capture:

- target-to-library mapping;
- link actions and output shared libraries;
- generated files and generator dependencies;
- build-system configuration variants;
- CMake target file sets and visibility;
- Bazel configured targets and action graph;
- implicit compiler include/link directories;
- compiler version and ABI-affecting toolchain options;
- build-option diffs between old and new baselines.

The evidence-pack architecture (ADR-028, layer L3) needs a normalized build
evidence model that can ingest build-system facts without parsing every
build language from scratch.

---

## Decision

### D1. Build evidence is its own normalized model

Create `BuildEvidence` as an abicheck-owned schema stored in
`build/build_evidence.json` inside the evidence pack:

```json
{
  "schema_version": 1,
  "source_root": "repo://root",
  "build_root": "build://root",
  "generators": [
    {"kind": "cmake", "version": "4.3.3", "generator": "Ninja"}
  ],
  "toolchains": [],
  "targets": [],
  "compile_units": [],
  "link_units": [],
  "generated_files": [],
  "build_options": [],
  "diagnostics": [],
  "raw_artifacts": []
}
```

The model is intentionally build-system neutral.

### D2. Normalize around targets, compile units, link units, and options

Core entities:

```json
{
  "targets": [
    {
      "id": "target://libfoo",
      "name": "foo",
      "kind": "shared_library|static_library|object_library|executable|interface|unknown",
      "build_system": "cmake|ninja|bazel|make|generic",
      "source_files": ["src/foo.cpp"],
      "public_headers": ["include/foo/foo.h"],
      "private_headers": ["src/foo_impl.h"],
      "outputs": ["build/libfoo.so"],
      "dependencies": ["target://bar"],
      "visibility": "public|private|interface|unknown",
      "confidence": "high|reduced|unknown"
    }
  ],
  "compile_units": [
    {
      "id": "cu://src/foo.cpp#cfg:abc123",
      "target_id": "target://libfoo",
      "source": "src/foo.cpp",
      "output": "build/CMakeFiles/foo.dir/src/foo.cpp.o",
      "directory": "build/",
      "compiler": "toolchain://gcc-14-cxx",
      "argv": ["/usr/bin/c++", "-std=c++20", "-DFOO=1", "-Iinclude", "-c", "src/foo.cpp"],
      "language": "CXX",
      "standard": "c++20",
      "defines": {"FOO": "1"},
      "undefines": [],
      "include_paths": ["include"],
      "system_include_paths": [],
      "sysroot": null,
      "target_triple": "x86_64-linux-gnu",
      "abi_relevant_flags": ["-std=c++20", "-DFOO=1"],
      "raw_ref": "raw/compile_commands/sha256...json"
    }
  ],
  "link_units": [
    {
      "id": "link://libfoo.so",
      "target_id": "target://libfoo",
      "output": "build/libfoo.so",
      "kind": "shared_library",
      "inputs": ["build/CMakeFiles/foo.dir/src/foo.cpp.o"],
      "linker_argv": [],
      "version_script": "exports.map",
      "soname": "libfoo.so.1"
    }
  ]
}
```

### D3. `compile_commands.json` is the universal low-friction input

Reuse and extend the ADR-020a ingestion path (`build_context.py`):

```bash
abicheck collect-evidence --compile-db build/compile_commands.json --output evidence/
abicheck collect-evidence -p build/ --headers include/ --output evidence/
```

Implementation rules:

- support both `arguments` and `command` fields;
- prefer `arguments`, because shell-parsing `command` is lossy;
- normalize relative paths against `directory`;
- preserve raw command lines for provenance, redacted per the configured
  `RedactionPolicy` (ADR-032 D7);
- extract ABI-relevant flags into structured fields (D9);
- derive compile-unit IDs from source path + normalized argv hash + output
  field;
- allow multiple entries for one source file when the same file is built
  under different configurations.

### D4. CMake adapter: File API plus compile DB

Do not parse `CMakeLists.txt` manually. The CMake adapter queries the CMake
File API reply directory and optionally reads `compile_commands.json`:

```bash
abicheck collect-evidence \
  --build-dir build \
  --cmake-file-api \
  --compile-db build/compile_commands.json \
  --output evidence/
```

Collected CMake facts:

| CMake source | abicheck use |
|---|---|
| `codemodel` object | target list, target kind, outputs, source list, dependencies |
| target `fileSets` | public/private/interface header sets when available |
| target `sources` with `compileGroupIndex` | map source files to compile groups |
| `compileGroups` | include dirs, defines, language, standard fragments |
| `toolchains` object | compiler path, ID, version, implicit include/link directories |
| `cmakeFiles` object | build-system input files for PR triggering and rebuild-drift detection |

When CMake File API data is present, it is the primary source for
target-level facts. The compile database remains primary for exact per-TU
command lines.

### D5. Ninja adapter: use `-t` tools, not `.ninja` parsing

Ninja is usually a generated backend; `.ninja` syntax is not a stable
high-level project model. Use Ninja's own tools:

```bash
ninja -C build -t compdb > compile_commands.json   # no rule args: all build statements
ninja -C build -t compdb-targets libfoo.so > libfoo.compile_commands.json  # Ninja >= 1.12
ninja -C build -t graph libfoo.so > libfoo.graph.dot
ninja -C build -t commands libfoo.so > libfoo.commands.txt
ninja -C build -t missingdeps libfoo.so > libfoo.missingdeps.txt
```

`-t compdb` takes Ninja **rule names**, which are generator-specific: GN
emits rules named `cxx`/`cc`, while CMake emits per-target rules such as
`CXX_COMPILER__foo_Release` — so hardcoding `compdb cxx cc` silently
yields an empty database on CMake/Ninja trees. The adapter must either run
`-t compdb` with no rule arguments (dumps every build statement) and
filter the entries to compiler invocations during normalization, or
discover the actual compiler rule names via `ninja -t rules` first. When
the tree was generated by CMake, prefer its exported
`compile_commands.json` (`CMAKE_EXPORT_COMPILE_COMMANDS`, D3/D4) over
reconstructing one through `compdb`.

`-t compdb-targets` (target-scoped compilation database) requires
Ninja ≥ 1.12. The adapter must probe `ninja -t list` and, on older Ninja,
fall back to whole-project `-t compdb` filtered to the target's inputs
(`ninja -t inputs libfoo.so`), recording the fallback in `diagnostics`.

Normalization:

- `compdb` / `compdb-targets` (or the filtered fallback) → `compile_units`;
- `graph` → approximate target/file dependency graph;
- `commands` → fallback link/compile command provenance;
- `missingdeps` → `diagnostics` feeding the
  `generated_file_dependency_unstable` finding (D9).

Ninja adapter confidence is high for compile commands and dependency edges,
reduced for public/private API intent unless paired with
CMake/Meson/GN/Bazel metadata.

### D6. Bazel adapter: `cquery`, `aquery`, and optional aspects

Do not parse BUILD files directly. Consume official query outputs:

```bash
# Configured target graph with build options/select() resolved.
bazel cquery 'deps(//foo:libfoo)' --output=jsonproto > bazel.cquery.json

# Actual actions with commands, inputs, outputs, mnemonics.
bazel aquery 'deps(//foo:libfoo)' --output=jsonproto > bazel.aquery.json

# Optional: project-specific aspect to emit public headers/source manifests.
bazel build //foo:libfoo --aspects=@abicheck//:abi_evidence.bzl%abi_evidence_aspect
```

`--output=jsonproto` is an accepted cquery value (alongside `proto`,
`streamed_proto`, and `textproto`) even though the cquery prose docs only
describe `proto`; the flag reference is authoritative.

**MVP scope (implemented):** the adapter ingests the textual **`jsonproto`**
form. A binary `--output=proto` blob is not decoded — it would require a
protobuf runtime plus vendored Bazel `.proto` bindings, which this
dependency-light tool deliberately avoids — so the adapter records a
diagnostic asking for `--output=jsonproto` rather than failing. Binary-proto
ingestion (for tooling that can only emit it) is a documented follow-up, not
an MVP requirement.

**Configured-graph fidelity (limitation):** when one label appears under
several configurations, the first configuration seen is the *canonical*
target and keeps the plain `target://<label>` id (so the label-based aquery
action graph still links to a collected target); additional configurations
are preserved under a `target://<label>#cfg:<id>` suffix. Dependency edges are
read from the label-only `deps` attribute, which does not carry each
dependency's configuration, so edges resolve to the canonical dependency
target. Full per-configuration dependency resolution would require richer
configured-edge output than the `jsonproto` rule attributes expose and is a
follow-up.

Collected Bazel facts:

| Bazel source | abicheck use |
|---|---|
| `cquery` | configured target graph, configuration IDs, dependency graph after `select()` and build options |
| `aquery` | compile and link actions, exact argv, inputs, outputs, mnemonics, generated artifacts |
| aspects | public headers, exported-symbol manifests, source ownership, rule-specific metadata |
| Build Event Protocol | optional CI metadata, parsed options, workspace status, action summaries |

Confidence rules: `aquery` actions are high-confidence for
commands/inputs/outputs; `cquery` is high-confidence for the configured
target graph; public/private header intent requires rule/aspect-specific
metadata and is reduced confidence without it. Aspect execution is opt-in
because it requires a Bazel build/evaluation flow (`run_build` action,
ADR-032 D5).

### D7. Make adapter: prefer generated compdb or compiler fragments

Make is too flexible to parse semantically with confidence. Support it as a
fallback tier:

1. Prefer an existing `compile_commands.json` generated by Bear, compiledb,
   intercept-build, or project tooling.
2. Prefer compiler-generated fragments such as Clang `-MJ` when projects
   can add the flag.
3. Use `make -n`, `make --trace`, or `make -p` only as diagnostic fallback,
   never as an authoritative target graph.
4. Compiler wrapper/interception is explicit opt-in (`wrap_build` action,
   ADR-032 D5) because it changes build invocation and can be fragile or
   sensitive.

```bash
abicheck collect-evidence --compile-db compile_commands.json --build-system make
abicheck collect-evidence --make-dry-run "make -n libfoo.so" --confidence reduced
```

### D8. Capture compiler-recorded metadata when available

Post-build extraction can often recover compiler provenance without
rebuilding:

| Toolchain feature | Use |
|---|---|
| Clang/GCC `-frecord-command-line` / `-frecord-gcc-switches` | read `.GCC.command.line` on ELF to recover compiler argv fragments |
| Clang CodeView command-line metadata | recover MSVC/clang-cl compiler command line from debug info when present |
| DWARF `DW_AT_producer` | compiler ID/version and sometimes codegen-affecting options |
| GCC `-fcallgraph-info` | optional per-object callgraph data in VCG format (feeds ADR-031) |
| Clang `-ftime-trace` | optional performance/provenance JSON; not ABI evidence by itself |
| Clang optimization records | optional implementation/provenance signal; not default ABI evidence |

These signals are advisory unless cross-checked against build-system
metadata.

### D9. ABI-relevant build options and the build-evidence diff

`BuildEvidenceDiff` compares build options between old and new evidence
packs and classifies the drift. High-priority ABI/API-affecting options:

- language mode: `-std=...`, `/std:...`;
- target/architecture/ABI: `--target=`, `-target`, `-mabi=`, `/arch:`,
  word-size-affecting flags;
- sysroot/SDK: `--sysroot`, `-isysroot`, SDK version;
- macro definitions: `-D`, `/D`, `-U`, `/U`, especially feature flags and
  `_GLIBCXX_USE_CXX11_ABI`;
- include path ordering: `-I`, `-isystem`, `/I`;
- visibility/export: `-fvisibility=`, `-fvisibility-inlines-hidden`,
  version scripts, `.def` files;
- layout: `-fpack-struct`, `/Zp`, `-fshort-enums`, `-fshort-wchar`;
- C++ ABI: `-fabi-version`, exceptions/RTTI toggles, MS extensions, ABI
  namespace toggles;
- LTO/thin-LTO and whole-program devirtualization toggles;
- toolchain version bumps: compiler, stdlib, sysroot, or SDK changes.

Proposed `ChangeKind` entries (each placed in exactly one partition set per
ADR-011 and the CLAUDE.md ChangeKind rules):

| Proposed kind | Partition | Meaning |
|---|---|---|
| `build_context_changed` | `COMPATIBLE_KINDS` (quality) | Non-ABI-relevant build metadata changed |
| `abi_relevant_build_flag_changed` | `RISK_KINDS` | ABI-affecting option changed; the artifact diff decides whether anything actually broke |
| `header_parse_context_drift` | `RISK_KINDS` | Header AST was parsed under a different context than the real build |
| `toolchain_version_changed` | `RISK_KINDS` | Compiler/stdlib/sysroot changed |
| `generated_file_dependency_unstable` | `RISK_KINDS` | Build graph indicates generated-file dependency risk |
| `link_export_policy_changed` | `RISK_KINDS` | Version script/export map/`.def` file changed |

These map into the existing five-tier verdict (ADR-009) with
worst-verdict-wins; no new verdict values and no exit-code changes. Each
kind lives in exactly one partition set — the import-time assertion in
`checker_policy.py` forbids overlap. In particular,
`link_export_policy_changed` does **not** escalate itself: when an export
policy change actually removes or alters exported symbols, the artifact
diff (L0) emits the existing `BREAKING_KINDS` findings (e.g.
`func_removed`) as separate, artifact-backed results, and this kind serves
to explain and localize them.

### D10. No instrumented rebuild required for the MVP

MVP evidence collection must work with existing build outputs:

- existing `compile_commands.json`;
- CMake File API replies already in the build tree;
- Ninja `-t` tools on an existing build directory;
- Bazel `cquery`/`aquery` analysis commands;
- binary/debug metadata already present;
- compiler-recorded sections already present.

Instrumented rebuilds, compiler wrappers, Bazel aspects that require
building, and compiler plugin passes are optional higher tiers (ADR-033
D1).

---

## Consequences

### Positive

- Directly attacks one of the largest false-positive sources: mismatched
  build/header context (the ADR-020a problem, generalized).
- Enables build-options-to-build-options comparison immediately.
- Gives PR triage (ADR-025) a principled reason to run full ABI checks for
  build-file-only changes.
- Provides target-to-binary and source-to-library ownership for source ABI
  replay (ADR-030).
- Keeps the common path post-build and CI-friendly.

### Negative / risks

- Build-system adapters vary in confidence and portability.
- Make support is necessarily weaker than CMake/Ninja/Bazel support.
- Command lines may contain secrets or user-specific paths (redaction:
  ADR-032 D7).
- Some metadata requires running build-system query commands; hermetic CI
  may need dependency setup.
- Include-path change comparison is noisy unless normalized carefully.

---

## Implementation plan

| Phase | Scope | Effort |
|---|---|---|
| 1 | Extend the ADR-020a parser into the `BuildEvidence` compile-unit model | Medium |
| 2 | CMake File API adapter: codemodel, fileSets, toolchains, cmakeFiles | Medium |
| 3 | Ninja adapter: `compdb-targets`, `graph`, `commands`, `missingdeps` | Small/medium |
| 4 | Bazel adapter: `cquery`/`aquery` JSON/proto normalization | Medium/high |
| 5 | Compiler-recorded metadata extractor | Medium |
| 6 | Build evidence diff and findings (D9) | Medium |
| 7 | CI integration and baseline registry storage (ADR-033) | Medium |

---

## Validation

- Golden fixtures for flag drift: macro value, `-std`, `-fpack-struct`,
  `_GLIBCXX_USE_CXX11_ABI`, version-script change.
- CMake/Ninja fixture with a generated-file dependency and public/private
  file sets.
- Bazel fixture with `select()` changing the source/dependency graph.
- Make fixture with only `compile_commands.json` available and reduced
  confidence.
- Redaction tests for secrets in command lines and paths.

---

## References

- ADR-020a — Build-Context Aware Header Extraction
  ([020-build-context-capture.md](020-build-context-capture.md))
- ADR-028 — Evidence Pack Architecture
- [Clang JSON Compilation Database](https://clang.llvm.org/docs/JSONCompilationDatabase.html)
- [CMake File API](https://cmake.org/cmake/help/latest/manual/cmake-file-api.7.html) and `CMAKE_EXPORT_COMPILE_COMMANDS`
- [Ninja manual](https://ninja-build.org/manual.html): `-t compdb`, `-t compdb-targets`, `-t graph`, `-t missingdeps`
- Bazel `cquery`, `aquery`, aspects, and Build Event Protocol
- GCC and Clang command-line recording options
