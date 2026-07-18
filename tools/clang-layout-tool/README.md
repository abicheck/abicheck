# abicheck-clang-layout-tool

A small, **optional** [LibTooling](https://clang.llvm.org/docs/LibTooling.html)
companion program for abicheck's G28 Phase 4
(`docs/development/plans/g28-castxml-clang-l2-parity-hardening.md`).

## What it does

abicheck's direct-clang L2 backend (`--ast-frontend clang`, `dumper_clang.py`)
parses headers via `clang -ast-dump=json`, which is **syntactic only** — it
never computes a record's actual compiled layout (field offsets, base
offsets, vtable-pointer placement). CastXML, abicheck's other L2 backend,
runs its own bundled Clang internally and exports the layout it already
computed, which is why it remains the layout-authoritative backend today.

This tool closes that gap for the direct-clang backend: it walks every
complete, non-dependent `CXXRecordDecl` in a translation unit and calls
`clang::ASTContext::getASTRecordLayout()` — the same internal API Clang's own
Sema/CodeGen use to compute a class's real layout for the target ABI — and
serializes the result as JSON.

## Building

Requires LLVM/Clang development packages for **one specific LLVM version**
(this tool's output is verified against LLVM 18.1.3; there is no
cross-LLVM-release ABI stability guarantee for the internal APIs it links
against — see the G28 plan doc). On Debian/Ubuntu:

```bash
apt-get install llvm-18-dev libclang-18-dev
```

Then:

```bash
mkdir build && cd build
cmake -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang ..
cmake --build . -j"$(nproc)"
```

This produces `build/abicheck-clang-layout-tool`.

## Testing

```bash
python3 tests/run_tests.py build/abicheck-clang-layout-tool
```

Runs the tool against the fixtures in `tests/fixtures/` and asserts specific
offsets/sizes computed by hand against the real Itanium x86-64 C++ ABI —
including the subtle POD-vs-non-POD tail-padding-reuse rule, single/multiple/
virtual inheritance, and vtable-pointer placement. See `run_tests.py`'s
module docstring for the verification methodology (no castxml/libabigail
was available to cross-check against, so correctness was confirmed by
observing where a further-derived class's own field actually lands).

## Using it with abicheck

Point `ABICHECK_CLANG_LAYOUT_TOOL` at the compiled binary:

```bash
export ABICHECK_CLANG_LAYOUT_TOOL=/path/to/abicheck-clang-layout-tool
abicheck compare old.so new.so -H include/ --ast-frontend clang
```

When set, and the L2 backend actually resolved to `clang`, abicheck runs this
tool over the same headers and backfills the resulting snapshot's
`RecordType`s with the real layout it computed — see
`abicheck/clang_layout_tool.py`. Unset (the default), abicheck's behavior is
completely unchanged: this is never a hard dependency.

## Output format

```json
{
  "ok": true,
  "records": [
    {
      "qualified_name": "ns::Foo",
      "size_bits": 192,
      "alignment_bits": 64,
      "data_size_bits": 192,
      "is_standard_layout": true,
      "is_trivially_copyable": true,
      "vptr_offset_bits": null,
      "fields": [{"name": "a", "offset_bits": 0}, ...],
      "bases": [{"name": "ns::Base", "offset_bits": 0, "is_virtual": false}, ...]
    }
  ]
}
```

`"ok": false` means clang hit a parse error it couldn't fully recover from;
`"records"` may still contain partial results from declarations it did
manage to resolve. The process itself always exits 0 — callers should check
`"ok"` in the JSON, not the exit code.

## Explicitly out of scope

- Full vtable slot enumeration / thunk offsets (`clang::VTableContext` is a
  materially larger surface than record layout — see the G28 plan doc).
- Anonymous-aggregate-flattened fields (abicheck's own Python-side model
  already flattens these; this tool emits only direct `FieldDecl`s and the
  Python-side merge matches purely by name).
