# ADR-074: Logical Macro Definitions on the CLI (`-D/--define`)

**Date:** 2026-09-18
**Status:** Accepted — implemented. Adds `-D/--define` to `dump` and
`compare`, backed by `abicheck/macro_definition.py`,
`abicheck/cli_options.py` (`define_option`, `merge_compile_config`) and
`abicheck/compile_context.py`'s `CompileContext.defines`. Amends — does not
reverse — [068](068-one-comparison-product-and-scan-retirement.md)'s CLI
reduction, which demoted the whole L2 compiler/frontend family to
`.abicheck.yml`'s `compile:` block. It changes no verdict, no gate and no
exit code for any invocation that does not pass `-D`.

## Context

A real integrator analysing [PVXS](https://github.com/mdavidsaver/pvxs),
whose opt-in public surface is gated behind `PVXS_ENABLE_EXPERT_API`,
expected this to work:

```bash
abicheck dump libpvxs.so -H include/ -DPVXS_ENABLE_EXPERT_API
```

It failed with `Error: No such option '-D'` (exit 64). The documented
fallback, `--gcc-options "-DPVXS_ENABLE_EXPERT_API"`, failed the same way:
the whole `--ast-frontend`/`--compiler`/`--compiler-prefix`/
`--compiler-option`/`--sysroot`/`--nostdinc`/`--frontend-context`/`--lang`
family was removed from `dump` and `compare` as one unit (the
one-comparison-product plan's phase 7b), leaving

```yaml
compile:
  defines:
    - PVXS_ENABLE_EXPERT_API
```

as the only route. Without it the run is not merely less detailed: every
declaration behind the macro is *absent*, so a signature break inside the
expert API reads as `compatible` (verified — see "Evidence" below).

The reduction's stated reason for each demoted flag was **toolchain
identity is stable per project/target** — a duplicated configuration
surface, not a security boundary. That reason holds for `--compiler`,
`--sysroot`, `-std=` and arbitrary `--compiler-option` pass-through. It does
not hold for a feature macro, and the asymmetry it produced is what the
integrator ran into: `-H` and `-I` kept their CLI spelling because they
*select which surface is being analysed*, and a macro that gates an opt-in
public API answers exactly the same question.

## Decision

Add a narrow, repeatable **`-D/--define NAME[=VALUE]`** to `dump` and
`compare`. It represents a *logical preprocessor definition*, never a raw
compiler argument. General compiler-option injection stays config-only;
`--gcc-options`/`--compiler-option` are **not** restored under any spelling.

### D1 — Availability and symmetry

- Present on `dump` and `compare`, the two commands that can run an L2
  header parse. (`scan` was retired by ADR-068; nothing was added to it.)
- On `compare` the definitions apply to **both sides identically**. There is
  deliberately no `old=`/`new=` form, unlike `-H`/`-I`/`--version`: two
  sides parsed under different macro contexts are two *different public
  surfaces*, and every finding between them would be an artifact of the
  flags rather than of the change. The existing both-sides threading gives
  this for free — `cli_resolve` hands one `CompileContext` to both
  `InputSpec`s.
- Inert, not an error, where no header parse happens: a stored-snapshot
  operand, `--depth binary`, a binary-only dump, or an invocation with no
  `-H`. Rejecting it there would turn a harmless both-sides flag into a
  usage error on the stored half of a mixed pair.
- With `--dump-manifest`, the manifest's own profiles keep owning their
  per-TU context; `-D` folds into the same pass-through token tail as
  today's `compile.defines`.
- **No matching Action input.** The root Action's `compile:` overlay already
  carries `defines`, and ADR-070 forbids the Action layer re-encoding CLI
  semantics. A CI workflow is exactly the stable-contract case
  `.abicheck.yml` exists for.

### D2 — Grammar

Accepted: `-DNAME`, `-D NAME`, `--define NAME`, `--define=NAME`, and each
with `=VALUE`. (Click gives the attached and separated forms for free; both
are covered by tests rather than asserted.)

- The split is on the **first** `=` only: `NAME=A=B` is the macro `NAME`
  with replacement list `A=B`, never three fields.
- `NAME=` (empty value) is valid and distinct from `NAME` (`#if NAME` is an
  error for the first and `1` for the second), so the two are preserved
  separately rather than normalised together.
- `NAME` must be a bare **ASCII** C identifier. A non-ASCII extended
  identifier is rejected: no portable spelling exists across `cl.exe`,
  clang-cl, CastXML and GCC, and shipping one that means four things on four
  frontends is worse than refusing it.
- **Rejected with a precise message, and documented as a limitation:**
  whitespace anywhere (a replacement list containing a space has no
  identical GNU-style and `cl`-style spelling, and would be re-splittable by
  any downstream `shlex`-style consumer), and function-like definitions
  (`F(x)=...`, same reason). Both belong in `compile.options`.
- Duplicates and conflicts are not errors: within one tier the last
  definition of a name wins (D3), which is both deterministic and what a
  compiler's own last-flag-wins rule would have done.
- There is no `-U`/`--undefine` counterpart. A `-U`-looking operand is
  rejected by the identifier rule with a hint naming `compile.defines`.

### D3 — Precedence and merging

Merging is **by macro name**, not list replacement and not append:

1. Every macro named on the CLI is removed from the config's own token
   position and re-emitted, once, at the end of the synthesized tail.
2. Config `compile.defines` entries for every *other* macro keep their exact
   position and value.
3. Within a tier, a repeated name resolves last-wins, so exactly one `-D`
   per macro reaches the frontend.

Re-emitting last (rather than substituting in place) is what makes the CLI
value win against a raw `-DNAME` smuggled through `compile.options`, which
is rendered after `defines`. Only CLI-named macros move, so an unrelated
`-U` in `compile.options` keeps its relative position — and a run with no
`-D` produces a byte-identical token tail to before this ADR.

The fold happens in exactly one place, `cli_options.merge_compile_config`,
which every front end already routes through (`dump`, `compare`, the
directory/package release fan-out, the stored-bundle dispatch). Compile
database and build-evidence defines are a *lower* tier still: they reach the
frontend through `header_compile_context`'s own resolution and are not
re-derived here.

### D4 — Safety model

The option cannot become raw-argument injection, structurally rather than by
blocklist:

- One `MacroDefinition` renders to exactly **one** argv token, always
  prefixed with the frontend's define switch. A user string is never placed
  in argv on its own.
- The *name* must be a bare C identifier, which is what turns
  `--define=-DFOO`, `--define=-Xclang`, `--define=@resp.txt` and
  `--define=--config=evil.cfg` into loud usage errors rather than a second
  compiler option. Each of the first three gets its own targeted hint.
- Whitespace rejection (D2) means no downstream re-split can manufacture a
  second token either.
- Validation lives in one module (`macro_definition.py`) and is re-applied
  at the fold, not duplicated between the Click callback and the frontend
  drivers.
- Macro values reach persisted provenance through the *same*
  `RedactionPolicy` every `ast_compile_args` token already passes through —
  no new secret-exposure surface relative to `compile.defines`.

### D5 — Reproducibility and positioning

`.abicheck.yml`'s `compile.defines` stays the recommendation for stable CI
and baseline generation; `-D` is for one-off runs, experiments and
integrations that construct the compile context at invocation time. Docs
show both without implying equal preference.

Because a macro set changes what was extracted, it participates in
extraction identity: the tokens land in `AbiSnapshot.ast_compile_args` and
in `compute_extraction_contract`'s `macro_ops` field, so comparing a
macro-off snapshot with a macro-on one is refused as `profile_mismatch`
rather than silently diffed. That behaviour is inherited, not added — see
the evidence below.

## Compiler/frontend compatibility matrix

| Environment | Spelling abicheck emits | Established how |
|---|---|---|
| GCC / G++ | `-DNAME[=VALUE]` | GCC docs; probed (`gcc -E -DFEATURE_API`) |
| Clang / Clang++ | `-DNAME[=VALUE]` | Clang docs; probed |
| CastXML, `--castxml-cc-gnu` | `-DNAME[=VALUE]` | probed (real AST) |
| CastXML, `--castxml-cc-msvc` | `-DNAME[=VALUE]` | CastXML passes user args to its bundled Clang in GNU driver mode regardless of the emulation id; this repo's own `buildsource/source_extractors/castxml.py` has emitted an unconditional `-D` there for its entire life, while spelling only `-std=`/`/std:` per id. **Not re-probed here** — no MSVC toolchain in this environment |
| abicheck direct-Clang L2 backend | `-DNAME[=VALUE]` | probed (real AST); this backend always drives a GNU-style driver (`-x c`, `--sysroot=`, `-isystem`) |
| MSVC `cl.exe` / clang-cl | `/DNAME[=VALUE]` | Microsoft/LLVM docs. Reachable only from L4 compile-DB replay (`source_extractors/clang.py`'s `--driver-mode=cl`), never from a CLI `-D` — **unverified boundary**, see "Limitations" |
| `compile_commands.json` ingestion | unchanged | pre-existing `-D`/`/D`, joined and separated, normalisation in `header_conditionals.defines_from_flags`; untouched by this ADR |

`MacroDefinition.token(style)` carries both spellings so the `cl` form is
exercised by unit tests even though no CLI path emits it today.

## Alternatives considered

- **Reject, and fix discoverability only** (a targeted error for `-D` plus a
  config example). Rejected: it leaves a one-off header experiment needing a
  file written into the project tree, and the asymmetry with `-H`/`-I`
  stands. The diagnostic improvements were done anyway.
- **Restore `--compiler-option`.** Rejected: that reopens arbitrary
  compiler-argument injection, which is the thing ADR-068's reduction and
  `compile_options_safety.py`'s plugin-loading rejection both exist to
  prevent.
- **Per-side `-D old=`/`new=`.** Rejected under D1 — it manufactures
  findings.
- **CLI replaces the whole config list.** Rejected under D3 — a project's
  other, unrelated feature macros would silently disappear the moment a user
  adds one experimental `-D`.

## Limitations (stated, not worked around)

- The CastXML+MSVC row is reasoned from this repository's own long-standing
  emission and CastXML's documented argument handling, not from a fresh
  Windows probe; no MSVC toolchain is available here. A Linux/GCC pass is
  not evidence for it.
- Whitespace-bearing and function-like definitions are unsupported by
  design. `compile.options` remains the escape hatch.
- `-U`/undefine has no CLI spelling.
