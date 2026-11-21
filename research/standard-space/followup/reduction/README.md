# Do the reduced suites retain fault detection?

Run `python research/standard-space/followup/reduction/run.py` on Linux x86-64 with GCC, Git and the project's Python dependencies installed. Fetch commit dac367b7 and its parent if using a shallow clone. The script executes the Python experiments embedded in the checked-in [array checker study](../../../../docs/contribute/plans/abiforge-array-checker-experiment.md) and [call study](../../../../docs/contribute/plans/abiforge-call-abi-experiment.md). It compiles temporary fixtures, archives the two historical source trees, runs the historical public dump command on identical binaries, applies faults in a temporary copy and writes [results.json](results.json). No changes are made to the checker under test in the checkout.

## Measurement units

The array cases are four profiles crossed with lengths 1–128. Before mutation, the runner verifies the existing public CLI comparisons against compiled size/offset probes for every projected direct offset. The original selected suite has 35 cases. The augmented suite adds grow8 at length 32 and a separate synthetic edit moving the first byte field from offset zero to one. The latter expands the original input domain. A failing array check is one missing expected member finding; the historical test instead checks the array bound in the old public snapshot of each fixture.

The call tests reuse the published seven profile pairs, prefixes 0–8 integer × 0–10 floating. They test whether old/new protocol equality differs from the reference capacity model, leaving machine-code checking to the existing qualification experiment. They do not invoke the public checker or measure C++ compilation time. The negative-capacity variant reconstructs an earlier mistake described in the study; its historical source revision is unavailable. The remaining three call mutations and both array detector mutations are deliberately injected faults.

## Results

| Fault and observation | Dense | Selected | Augmented |
| --- | ---: | ---: | ---: |
| Historical parent of #1962, erased DWARF bounds | 512/512 | 35/35 | 37/37 |
| After #1962, incorrect bounds | 0/512 | 0/35 | 0/37 |
| Detector skips offset moves originating at zero, missing findings | 0 | 0 | 1 |
| Detector compares only low eight bits of byte offsets, missing findings | 4 | 0 | 1 |
| Call model, exhausted unused bank, wrong protocol-equality bits | 34 | 0 | 2 |
| Call model, one fewer GP register | 23 | 4 | 4 |
| Call model, one fewer SSE register | 11 | 3 | 3 |
| Call model ignores SSE pressure | 37 | 7 | 8 |

The augmented call suite adds prefix (0,9) to the nine prefixes for each profile, for 70 versus 63 selected or 693 dense model evaluations. This recovers the failure at II→DD even though both protocols were already represented abstractly. For arrays, grow8 at N=32 changes the tail offset by exactly 256 bytes, exposing the byte truncation. The first-field fixture exposes a real blind spot shared by both original suites. Both added cases were chosen after seeing the faults; their performance on future regressions is unknown.

Use the augmented cases as targeted regression sentinels. Retain the dense controls until an independent holdout of additional historical defects establishes acceptable retention. Times in results.json are single local runs, including system load; array baseline times include compilation/dumping/comparison, while array mutant times reuse prepared snapshots. Do not compare them to pure call-model timings. Neither this small set of faults nor equality-vector coverage estimates a population-level defect detection rate.
