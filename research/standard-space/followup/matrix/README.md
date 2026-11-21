# Checker outcome and entity attribution on paired ABI transitions

From the repository root use `PYTHONPATH=$PWD python research/standard-space/followup/matrix/run.py` with abicheck's dependencies, GCC/G++ 14.2.0, GNU binutils and CastXML 0.6.11 (Clang 19.1.7) on PATH. The run used the implementation shared by #2154 and main 1319f52. The script renders three existing operators, builds each provider once with DWARF, and strips a copy. Each lane with headers requires an observed L2 header AST for both inputs. results.json includes binary hashes, outcomes and diagnostic text.

Three real changes served as controls: reordering two method definitions in the translation unit; substituting a private integer initialized to the same multiplier in scale; and returning a private integer initialized to the same constant in B1::f1. Each control preserves the public declarations. The old binary client was compiled once per seed and rerun with each provider. Its observed outputs agree for controls and diverge on mutations. These controls measure three specific ABI-preserving edits. Their frequency cannot estimate production false-positive prevalence.

| Evidence | Observed breaks classified BREAKING | Controls classified NO_CHANGE | UNCONFIRMED (break / control) |
| --- | --- | --- | --- |
| DWARF | 3/3 | 3/3 | 0 / 0 |
| DWARF and headers | 3/3 | 3/3 | 0 / 0 |
| Stripped | 1/3 | 0/3 | 2 / 3 |
| Stripped and headers | 3/3 | 3/3 | 0 / 0 |

On virtual_reorder the unchanged client receives 70/7 before and 70/1 after (provider-mediated call / direct call). On the same unstripped bytes, DWARF-only findings name Shape and both changed methods; adding headers retains a Shape finding but loses the two individual method names. Headers improve the stripped result from UNCONFIRMED to BREAKING. Thus verdict recovery and diagnostic specificity need independent assertions. For the stripped primary_base_grew pair, the thunk finding names MI; the edited declaration is B1::pad. A correct break diagnosis can cite a different affected entity. The attribution fields score explicit owner/member mentions in relevant findings, including demangled symbol names; they do not grade transitive causes. Keep owner, member, causal mechanism and assurance separate in the shared runner.
