# case165 — Runtime Floor Raised (glibc relink drift)

**Verdict:** 🟡 COMPATIBLE_WITH_RISK · **Category:** risk · **Evidence tier:** L0

## What it demonstrates

Environment drift, not an interface change: the exact same two exported
functions (`demo_init`, `demo_process`) in both versions — but v2 was
**relinked on a newer distro**, so its imports rebind to newer glibc version
nodes. `__libc_start_main` moves from `@GLIBC_2.28` to `@GLIBC_2.34`, which is
exactly what happens when you merely rebuild on glibc ≥ 2.34 with zero source
change. The binary is interface-identical for existing consumers but **no
longer loads** on distros older than glibc 2.34 (RHEL 8, Ubuntu 20.04, …).

abicheck reports both granularities:

- `symbol_version_required_added` — the per-node fact (`GLIBC_2.34` from
  `libc.so.6` is newer than the old maximum `GLIBC_2.28`);
- `runtime_floor_raised` — the roll-up headline:
  `GLIBC_2.28 → GLIBC_2.34 (required by: __libc_start_main@GLIBC_2.34)`.
  The evidence list is the actionable part — a floor pulled up only by
  `__libc_start_main` is a pure relink artifact; a real API symbol would mean
  the code genuinely uses the newer runtime.

Both findings are `COMPATIBLE_WITH_RISK` by default: whether they break
anyone depends on deployment targets the tool cannot see.

## Making it decidable: `--env-matrix`

Declare the oldest runtime you ship to and the risk becomes a verdict
(see [Environment & Toolchain Drift](../../docs/concepts/environment-drift.md)):

| Declared floor | Meaning | Verdict |
|---|---|---|
| `GLIBC: "2.36"` (ship to Ubuntu 24.04+) | every target already has 2.34 | 🟢 COMPATIBLE |
| `GLIBC: "2.28"` (ship to RHEL 8) | targets can't load a 2.34 binary | 🔴 BREAKING |
| *(none declared)* | unknown deployment envelope | 🟡 COMPATIBLE_WITH_RISK |

## Reproduce

This case ships committed snapshot fixtures (`old.abi.json` / `new.abi.json`)
instead of a compilable v1/v2 pair — producing the drift for real would
require two different glibc sysroots, not two sources.

```bash
abicheck compare old.abi.json new.abi.json                              # COMPATIBLE_WITH_RISK
abicheck compare old.abi.json new.abi.json --env-matrix env-newer.yaml  # COMPATIBLE
abicheck compare old.abi.json new.abi.json --env-matrix env-older.yaml  # BREAKING
```

Validated compiler-free by `tests/test_environment_drift.py`.
