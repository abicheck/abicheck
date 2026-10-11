---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Toolchain-profile compiler-family rendering — audited, `args` trust boundary hardened; the `-stdlib=`/`--target=` "fix" itself was wrong and has been reverted

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** Audit record: the wrong fix was reverted and the args trust boundary was hardened. _compose_gcc_options still exists (buildsource/run_plan_profile_fields.py:21-25).

An external audit found `run_plan.py`'s
`_compose_gcc_options()` composing `-stdlib=`/`--target=` unconditionally
for any `profiles.<id>.compile` overlay, even when
`compile.compiler_family: gcc` — both are Clang-driver-only spellings a
real GCC binary rejects (confirmed against GCC 14.2), so an early pass
dropped both whenever `compiler_family` resolved to a GCC family name. A
later review round found that fix backwards: the composed string this
function returns is **never actually fed to a literal GCC binary
anywhere in this pipeline** — `--ast-frontend` only has
`auto`/`castxml`/`clang`/`hybrid` (no `gcc`); castxml's own frontend is
always its internal bundled Clang (`--castxml-cc-<id>` selects an
*emulation* mode, not a literal execution path); and the direct-clang
backend's `_resolve_clang_bin` (`dumper_clang.py`) explicitly rejects a
`gcc-path` that isn't clang-family and falls back to host
`clang`/`clang++`. Since the real consumer is always Clang, dropping
`--target=` actively broke cross-compilation-target correctness for the
direct-clang backend — it was the *only* signal available there to steer
parsing away from the host architecture (no "probe the real compiler"
auto-discovery step exists on that path the way castxml has one), so a
GCC-family profile with an explicit `target:` would silently have its
headers parsed for the runner's architecture instead. Reverted:
`_compose_gcc_options()` emits `-stdlib=`/`--target=` unconditionally
again, same as before the original audit, with both the change and the
reasoning for reverting it recorded in the function's own docstring so a
future reader doesn't rediscover and re-"fix" the same false positive.
The same original audit flagged a real trust-boundary gap in
`profiles.<id>.compile.args`, which is unaffected by this revert and
stays fixed: the existing whitespace-
smuggling check (`_safe_profile_atom`) rejected one YAML scalar expanding
into multiple argv tokens, but not a single, whitespace-free dangerous
atom. `_DANGEROUS_ARG_PREFIXES` (`project_targets.py`) now blocks four
families of these: direct code-loading flags (`-Xclang`, `-load`,
`-fplugin=`, `-fpass-plugin=`), file/argv re-expansion (`@response-file`,
Clang's `--config`/`--config=`), driver command-line substitution
(`-specs=`/`--specs=`, `-wrapper`), and — added across two follow-up
review rounds on the same PR, since each is the same underlying
"opaque subprocess-forwarding" mechanism as the others — GCC's
`-Wa,`/`-Wp,`/`-Wl,` (comma-joined payload passed straight to the
assembler/preprocessor/linker; `-Wp,-fplugin=./evil.so` reaches cc1 the
same as a bare `-fplugin=`, `-Wl,-plugin=./evil.dso` loads an LTO linker
plugin) and Clang's `-Xpreprocessor`/`-Xassembler`/`-Xlinker`
(separate-argument equivalent of `-Xclang`). A third review round found a
deeper issue than another missing flag spelling: every `compile.*` atom
(not just `args`) now also rejects quote (`'`/`"`) and backslash (`\`)
characters, since `_compose_gcc_options` space-joins every field into one
string that `dumper.py`'s `--gcc-options` handling later re-splits with
`shlex.split()` — an atom like `"'-fplugin=./evil.so'"` starts with a
quote, not `-fplugin=`, so the prefix denylist alone accepted it, but
POSIX shlex quote-removal reconstitutes the exact blocked flag on
re-split (confirmed with an actual `shlex.split()` round-trip). Two more
review rounds each found a flag real for the mechanism it names but
empirically NOT exploitable through abicheck's actual pipeline —
verified rather than taken on faith, and blocked anyway since doing so
is free: `--castxml-cc-` (a second occurrence naively looks like it
could replace the trusted `--castxml-cc-<id> <path>` pair
`dumper_ast_config.py` composes ahead of `args`, but real castxml
0.6.3 hard-rejects any repeated `--castxml-cc-*` occurrence at
argv-parse time instead of silently substituting the compiler); and
`-B<dir>`/`-B <dir>` (GCC's compiler-component search path override
really does let a planted `cc1`/`cc1plus` run instead of the real one,
confirmed against real GCC — but every consumer of this composed
string is Clang, not GCC, and Clang re-execs itself via `-cc1` rather
than spawning a separate, `-B`-discoverable one; confirmed neither
castxml's internal bundled Clang nor the direct `--ast-frontend clang`
backend ran a planted `cc1` with `-B` set). A fifth review round found a
flag family that IS actually exploitable through this pipeline, unlike
the two immediately above: clang-cl's (Clang's MSVC-compatible driver
mode — reachable via a `compile.binding` whose path stem contains
"clang", e.g. `clang-cl`/`clang-cl.exe`, which
`dumper_clang._is_clang_family_binary` recognizes as clang-family)
`/clang:<arg>` escape hatch forwards an argument straight to the
underlying clang driver, bypassing clang-cl's MSVC-shaped option parsing
entirely — empirically confirmed exploitable: `clang
--driver-mode=cl "/clang:-fplugin=./evil.so" -c t.h` really does load and
run the planted plugin. `/link <options>` (clang-cl's documented
"forward options to the linker") is blocked alongside it on the same
LTO-linker-plugin grounds as the already-blocked `-Wl,`, without a
from-scratch empirical repro of that specific sub-case. A sixth review
round found a different shape of finding again: `-cc1`/`-cc1as`, Clang's
internal frontend mode, only activates when `-cc1`/`-cc1as` is literally
the *first* argument after the program name (confirmed empirically:
`-cc1` anywhere else is rejected as "unknown argument", including right
after a leading `-I`) — but `dumper.py`'s `_build_clang_header_command`
builds argv as `[cc_bin, *-I dirs, --sysroot, -nostdinc, *gcc_options
tokens, ...]`, so a scan with no `extra_includes`/`sysroot`/`nostdinc`
lets a leading `-cc1` in `compile.args` genuinely land in that
first-argument slot. Once in cc1 mode, `-load`/`-fpass-plugin=` were
already blocked, but cc1 mode exposes an entirely different, much larger
argument namespace this denylist was never designed to enumerate — Codex
found `-fcas-plugin-path` (a cc1-only flag not present in every Clang
build) doing the identical thing. Rejected the mode switch itself rather
than chasing individual cc1-only flags, the same reasoning as `--config`.
This denylist is necessarily reactive to the delivery *mechanism*, not exhaustive over
every dangerous flag a mechanism could carry — a real fix for the
whack-a-mole shape of this (an allowlist of known-safe ABI flags instead
of a denylist of known-dangerous ones) was suggested during review but
deliberately not done here: `args` is documented as a general escape
hatch for ABI-relevant flags this codebase cannot enumerate a priori
(GCC/Clang/MSVC each have their own vocabulary), and a strict allowlist
would need that vocabulary built out first — its own scoped project, not
a reactive expansion of this fix. (A fourth review round briefly caught a
correctness gap in a since-reverted sentinel the family-aware
`_compose_gcc_options()` fix needed — moot now that the fix itself is
reverted, see above; not detailed here since it no longer applies to any
code that ships.) Still **not** implemented, and out of
scope for that fix (each needs its own
scoped design, not a drive-by extension of the same narrow correction):
a real toolchain-identity probe that validates a resolved `binding`'s
actual compiler family/version/target against the profile's declared
constraints (`compiler_version` is still parsed but never checked against
anything); a profile-specific AST frontend (there is still only one
global `--ast-frontend`); and a genuine family-specific argv resolver —
in particular MSVC `/std:`/`/D` spellings, which this fix does not
attempt (no `compiler_family: msvc` caller/test exists yet to validate
against, and a wrong guess here is worse than the pre-existing gap).
