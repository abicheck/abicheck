# G37 pilot: `explain-abi-change`, 2026-09-30

This is the first A/B run of the second published skill. Its runs came from
the harness described in [the 2026-09-29 pilot](2026-09-29.md), with that
pilot's leak fixes in place. One model was used throughout,
`claude-sonnet-5-5`, recorded as the resolved model on every run. The runs
were headless, through `runners/claude_code.py`, on one Linux x86_64 host.
It is a pilot: two or three repetitions per cell.

## What is being measured

Each scenario is a small development environment that fails at runtime. Its
`setup.sh` builds a program against `sdk/`, and `env/run.sh` runs it the way
its users do. The answer is graded on two things, and both must be right:

- **the root cause**, from a closed vocabulary (`diagnosis.cause`):
  `symbol_removed`, `symbol_version_missing`, `layout_changed`,
  `stale_library_loaded`, `cxx_abi_mismatch` or `not_an_abi_problem`;
- **the verdict** of comparing the library the program was built against
  with the library the loader actually uses.

The cause alone would not be enough. The same missing export is either
`symbol_removed` or `stale_library_loaded`, and those need opposite fixes.

The eight scenarios:

| Scenario | Symptom | Cause |
|---|---|---|
| `explain-missing-symbol` | `undefined symbol` at startup | `symbol_removed` |
| `explain-stale-copy-loaded` | `undefined symbol`, correct copy installed elsewhere | `stale_library_loaded` |
| `explain-missing-version-node` | `version 'WIDGET_2.0' not found` | `symbol_version_missing` |
| `explain-layout-mismatch` | wrong result, no loader error | `layout_changed` |
| `explain-dual-cxx-abi` | missing `B5cxx11` symbol, identical source | `cxx_abi_mismatch` |
| `explain-not-an-abi-problem` | the program exits with an error; libraries identical | `not_an_abi_problem` |
| `explain-rpath-overrides-path` | `undefined symbol` although `LD_LIBRARY_PATH` is right; a `DT_RPATH` wins | `stale_library_loaded` |
| `explain-layout-binary-only` | wrong result; the loaded library is a vendor binary with no source | `layout_changed` |

The last two were written after the skill, as a check that it was not tuned
to the first six. Both remove the easy path.

Every fixture's symptom and ground truth are checked against the real
toolchain in `tests/test_skill_eval_diagnosis.py` (`integration` marker). The
loaded library is found by asking the dynamic loader itself
(`LD_TRACE_LOADED_OBJECTS`), not by reusing abicheck's own `deps tree`.

## Result

|                                   | skill     | baseline  |
|-----------------------------------|----------:|----------:|
| runs graded                       | 18        | 18        |
| correct answer (verdict + cause)  | 18 (100%) | 16 (89%)  |
| correct cause                     | 18 (100%) | 18 (100%) |
| ran a real comparison (dim 1/3)   | 18 (100%) | 9 (50%)   |
| zero-tolerance failures (dim 2/6) | 1 (6%)    | 11 (61%)  |
| mean cost / turns / wall time     | $0.142 / 8.3 / 30 s | $0.106 / 5.9 / 22 s |

**Read this plainly: the skill did not make the agent better at finding the
cause.** The baseline named the right cause in all 18 runs too. On
fixtures this small, with the loader's message in front of it, this model
reasons its way to the cause without help.

What the skill changed is the evidence behind the answer, and the severity
it reports.

- **Evidence.** The baseline answered from the loader message, `nm` and a
  reading of the sources in half of its runs. It stated a compatibility
  verdict with no comparison behind it, which is what dimension 6 counts.
  With the skill, every run established which library copy actually loads
  (`deps tree`) and compared it with the build-time library.
- **Severity.** Both baseline misses were in the stale-copy scenarios, and
  both were the same mistake: a missing export in an already-built program
  reported as `API_BREAK`, a source-level break, instead of `BREAKING`.

The one skill-arm failure (`explain-rpath-overrides-path`, repetition 2) is an
evidence citation slip, not a wrong diagnosis. The agent first tried a flag
that does not exist (`--old-header`), then ran the correct command, and cited
the failed call's number instead of the successful one.

## What this does and does not show

- It shows that the skill makes answers **checkable and correctly graded**:
  every diagnosis rests on a recorded loader resolution and comparison.
- It does not show a gain in diagnostic accuracy. The honest next step is
  harder scenarios, where reading the diff cannot find the cause:
  - no source for either library;
  - a symbol resolved from the wrong one of several loaded libraries
    (interposition);
  - a `dlopen`ed plugin;
  - a mismatch only a build flag explains, with no symptom in the source.
- One model, one agent, and small repetition counts. The cross-agent runs
  that `skills-src/CLAUDE.md` lists are still open.

## Reproducing

From a path containing neither `abicheck` nor a verdict word:

```bash
python scripts/gen_agent_skills.py
python skills-src/evaluation/agents/skills/runners/claude_code.py --out /tmp/sd/a --repetitions 2 \
    --scenarios explain-missing-symbol,explain-stale-copy-loaded   # and so on, per subset
python skills-src/evaluation/agents/skills/run_skill_eval.py --runs /tmp/sd/a --runs /tmp/sd/b ...
```
