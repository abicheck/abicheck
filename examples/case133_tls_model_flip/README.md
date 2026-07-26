# Case 133: TLS Model Flip

**Category:** Build mode | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

The public symbol and its source are byte-for-byte identical between v1 and
v2 — only the *thread-local-storage access model* changed. v1 is built with
`-ftls-model=global-dynamic` (safe for a library that may be `dlopen`ed
after program start); v2 is built with `-ftls-model=initial-exec`, which
emits a cheaper TLS access sequence but assumes the library is present at
program-load time. A consumer that later `dlopen`s v2 (rather than linking
it directly at startup) can crash or misbehave accessing thread-local data,
since `initial-exec` is not valid for TLS blocks that show up after
process start. Nothing observable changes for a normally-linked-at-startup
consumer, which is why this is a risk finding rather than a hard break.

## Old/new diff

| v1.cpp | v2.cpp |
|--------|--------|
| `int compute(int x) { return x + 1; }` (compiled `-ftls-model=global-dynamic`) | `int compute(int x) { return x + 1; }` (compiled `-ftls-model=initial-exec`) |

## abicheck command

```bash
g++ -shared -fPIC -g -std=gnu++17 -ftls-model=global-dynamic v1.cpp -o libv1.so
g++ -shared -fPIC -g -std=gnu++17 -ftls-model=initial-exec v2.cpp -o libv2.so

# abicheck needs the compile flags, not just the binaries, to see this
# change — a compile_commands.json per side supplies the L3 build context.
cat > v1_compile_commands.json <<EOF
[{"directory": "$PWD", "command": "g++ -std=gnu++17 -fPIC -g -ftls-model=global-dynamic -c v1.cpp -o v1.o", "file": "$PWD/v1.cpp"}]
EOF
cat > v2_compile_commands.json <<EOF
[{"directory": "$PWD", "command": "g++ -std=gnu++17 -fPIC -g -ftls-model=initial-exec -c v2.cpp -o v2.o", "file": "$PWD/v2.cpp"}]
EOF

abicheck dump libv1.so --build-info v1_compile_commands.json -o v1.abi.json
abicheck dump libv2.so --build-info v2_compile_commands.json -o v2.abi.json
abicheck compare v1.abi.json v2.abi.json
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

- tls_model_changed: Runtime-model option 'tls_model' changed:
  'global-dynamic' -> 'initial-exec'.
  > May not be link- or runtime-compatible across consumers; the artifact
    diff confirms any concrete break.
```

## Minimum evidence

`min_evidence: L3` — the symbols, DWARF, and binary layout are all
identical between v1 and v2; there is nothing to see at L0/L1/L2. Only the
build system's compile flags (captured in a `compile_commands.json` / build
context) reveal that the two sides used different TLS models.

## Why abicheck catches it

abicheck's L3 build-context diff reads each side's compiler invocation from
the supplied build info, normalizes `-ftls-model=...` to the canonical
`tls_model` runtime-model option, and reports the switch when the two
sides disagree.

## Runtime failure demonstration

**Severity: RISK (not a proven break)**

This is a build-mode signal, not an artifact-proven binary break (ADR-028
D3). `compute()` has no `thread_local` data, so nothing observably fails in
this minimal case either way; the real risk applies to a library exporting
`thread_local` state that gets `dlopen`ed after program start with
`initial-exec`. No swap-in-place crash demo is included for that reason.

## Safe redesign

Choose a TLS model compatible with how the library is actually loaded —
`global-dynamic` for anything that may be `dlopen`ed after program start —
and keep it stable across releases; don't let it silently vary between
build configurations.

## Cross-tool comparison

`abidiff`/ABICC compare pre-built binaries or headers; neither reads
compiler flags from a build system, so this build-mode-only change is
invisible to both — there's no symbol, type, or layout delta for them to
diff. Detecting it is specific to abicheck's L3 build-context evidence
layer.
