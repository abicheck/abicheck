# G37 pilot: `explain-abi-change` (formerly `debug-abi-failure`), 2026-09-30

This is the first A/B run of the second published skill. Its runs came from
the harness described in [the 2026-09-29 pilot](2026-09-29.md), with that
pilot's leak fixes in place. One model was used throughout,
`claude-sonnet-5-5`, recorded as the resolved model on every run. The runs
were headless, through `runners/claude_code.py`, on one Linux x86_64 host.
It is a pilot: two or three repetitions per cell.

## What is being measured

Each scenario is a small development environment in which a developer
noticed something about a library; most fail at runtime. Its
`setup.sh` builds a program against `sdk/`, and `env/run.sh` runs it the way
its users do. The answer is graded on two things, and both must be right:

- **the root cause**, from a closed vocabulary (`diagnosis.cause`):
  `symbol_removed`, `library_older_than_build`, `symbol_version_missing`, `layout_changed`,
  `stale_library_loaded`, `cxx_abi_mismatch` or `not_an_abi_problem`;
- **the verdict** of comparing the library the program was built against
  with the library the loader actually uses.

The cause alone would not be enough. The same missing export is either
`symbol_removed` or `stale_library_loaded`, and those need opposite fixes.

The scenarios (the last two added in round 2):

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
| `explain-library-older-than-build` | `undefined symbol`; built against 1.2, runs on 1.1 | `library_older_than_build` |
| `explain-additions-only` | nothing fails; a newer release only adds exports | `not_an_abi_problem` (`COMPATIBLE`) |

The last two were written after the skill, as a check that it was not tuned
to the first six. Both remove the easy path.

Every fixture's symptom and ground truth are checked against the real
toolchain in `tests/test_skill_eval_diagnosis.py` (`integration` marker). The
loaded library is found by asking the dynamic loader itself
(`LD_TRACE_LOADED_OBJECTS`), not by reusing abicheck's own `deps tree`.

## Result

Two rounds, both on 2026-09-30.

**Round 2 (current skill, 10 scenarios, 2 repetitions per arm).** Run after
the skill was renamed from `debug-abi-failure` and widened from "a program
stopped working" to "a developer wants to understand an ABI change", which
added `library_older_than_build` and two scenarios
(`explain-library-older-than-build`, `explain-additions-only`).

|                                   | skill    | baseline  |
|-----------------------------------|---------:|----------:|
| runs graded                       | 20       | 20        |
| correct answer (verdict + cause)  | 19 (95%) | 20 (100%) |
| ran a real comparison (dim 1/3)   | 20 (100%)| 14 (70%)  |
| zero-tolerance failures (dim 2/6) | 1 (5%)   | 8 (40%)   |

The one skill-arm miss (`explain-library-older-than-build`, repetition 0)
named the right mechanism, but emitted two claim blocks: the first took its
verdict from the *reverse* comparison the skill had just told it to run, and
the second corrected it. Two blocks grade as ambiguous. The skill now says
that the reverse comparison is evidence for the mechanism only and the
reported change is still built-against to used. Re-run on that scenario
after the fix: 3/3 correct, 0 zero-tolerance failures. That re-run is on
the scenario that motivated the fix, so it confirms the fix, not a general
improvement.

**Round 1 (as `debug-abi-failure`, 8 scenarios, 2-3 repetitions per arm).**

|                                   | skill     | baseline  |
|-----------------------------------|----------:|----------:|
| runs graded                       | 18        | 18        |
| correct answer (verdict + cause)  | 18 (100%) | 16 (89%)  |
| correct cause                     | 18 (100%) | 18 (100%) |
| ran a real comparison (dim 1/3)   | 18 (100%) | 9 (50%)   |
| zero-tolerance failures (dim 2/6) | 1 (6%)    | 11 (61%)  |
| mean cost / turns / wall time     | $0.142 / 8.3 / 30 s | $0.106 / 5.9 / 22 s |

**Read this plainly: the skill did not make the agent better at finding the
cause.** Across both rounds the baseline named the right mechanism in every
run, including the new `library_older_than_build` and additions-only
scenarios. On fixtures this small, with the loader's message in front of it,
this model reasons its way to the cause without help.

What the skill changed is the evidence behind the answer, and the severity
it reports.

- **Evidence.** Without the skill, 6 of 20 (round 2) and 9 of 18 (round 1)
  runs answered from the loader message, `nm` and a reading of the sources,
  stating a compatibility verdict with no comparison behind it, which is
  what dimension 6 counts. With the skill, every run established which
  library copy is used and compared it with the build-time library.
- **Severity.** Round 1's two baseline misses were the same mistake: a
  missing export in an already-built program reported as `API_BREAK`, a
  source-level break, instead of `BREAKING`. Round 2 had no such miss.

Round 1's one skill-arm failure (`explain-rpath-overrides-path`) was an
evidence citation slip, not a wrong diagnosis: the agent first tried a flag
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
