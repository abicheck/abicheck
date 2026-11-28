# ABI Forge catalogue ablation and oneDAL witness delta

Compared the complete Forge trees at main `e2fc8dc2a` and PR implementation
`b6f6ef9d2`. The operator and model directories are identical. Run the same
audit against each checkout with its repository root on PYTHONPATH:

```sh
PYTHONPATH=. python research/onedal-coverage/forge_ablation.py > forge-ablation.json
```

The report lists every registered operator, the model classes lost if it is
removed, and alternatives for each class. Among 411 operators, 130 are the
only representative of at least one of the 194 represented construct/edit
classes. Removing any one of the other 281 preserves that class inventory.
No two operators with source changes produce identical role changes across
their entire applicable seed sets. Applicability, ABI direction, checker
evidence and the consumer may distinguish operators in the same class.
The catalogue counts therefore give no basis for deleting those 281 tests.

## A bounded minimum outside the oneDAL cases

For the existing 22 bit-field edits, the audit computes each edit's footprint
using the independent Forge layout model across both compilers and packing
settings. It retains each observed row shape per configuration and detection
of each of the four existing layout faults per configuration. Exhaustive
branching on the owners of each uncovered observation yields a minimum of
12 edits for the 48 observations. Eight edits have individually unique
observations. The JSON gives one minimum set. Numerical offsets, other model
faults and actual compiler diagnostics can distinguish additional edits.
This is a model-level reduction on both revisions.

## oneDAL mechanisms across the two revisions

The last column states what would disappear from the specific generic
experiment if its PR addition were removed. Main also contains SDK-specific
oneDAL replays. The comparison concerns reusable observations.

| Contract and witness | Main | PR contribution |
| --- | --- | --- |
| Field offset, stride, enum values and width | Layout experiments, allocation and enum consumers, append and reorder operators | Existing mechanisms linked to product observations |
| Virtual dispatch and callback ABI | Slot order, base moves and callback argument/return variants in L2 | Existing mechanisms linked |
| Binding to a missing or versioned provider | Provider campaign and duplicate-provider clients | Existing mechanisms linked |
| Template instantiation availability | Instantiation removal and template consumers | Two frozen clients isolate loss of the wrapper versus its implementation |
| By-value argument | Small aggregates and call triviality | One large argument moves from indirect to stack copy and displaces the following scalar |
| By-value member return | Return convention and callback lowering | Frozen member call identifies the displaced receiver address |
| Shared ownership | Early, missing and double release | Excess retention and release during self-assignment have separate mutations. The latter observes deletion immediately after assignment |
| Returned state | SDK status shims | SDK-independent client observes success, failure, copy, assignment and reset separately |
| Runtime type identity | Exception controls and inheritance edits | Cross-cast and catch after an access change, with a separate raw ELF base-edge reader for corroboration |
| Type-erased buffer | Product buffer workloads | Narrow control and wide transfer distinguish byte count from element count |
| Prerequisite propagation | Plugin export and package checks | Same exported call distinguishes dropped prerequisite from reversed order |
| Windows export identity | PE reader and product export inventory | Same name and ordinal across code, data and forwarder in one generated fixture. Runtime binding remains open |

The PR changes nine runner modules including one ELF evidence reader. It
adds no catalogue operator. A reader, control or alternate compiler run does
not itself add a consumer contract. A global minimum for standalone runners
and integration tests remains unknown: the registered operator model does
not encode their full consumer observations. Product algorithm names are
not inputs to either ablation.

## Frozen clients across the registered seeds

The executable audit builds each seed's baseline once, compiles its clients
against that baseline, and runs them against the edited libraries with eager
binding. It compares exit status, stdout and stderr. It repeats each baseline
and each edited run. For each seed it enumerates all client subsets and
reports the smallest ones that detect every edit detected by the full set.

On Linux x86-64 with GCC 14.2, C17/C++17 and default flags, 403 of 446
applicable seed/operator pairs ran across 13 seeds. These exercised 369
distinct operators. The clients detected a change in 102 pairs (87 distinct
operators). The other 301 ran without a client-visible difference in these
workloads. The 43 excluded pairs retain a configuration or build reason in
the matrix. A compatible control and an undetected break can both have an
empty detection set.

| Seed | Executed edits | Client-visible edits | Shapes | Minimum shapes for these detections |
| --- | ---: | ---: | ---: | ---: |
| c_core | 28 | 12 | 4 | 4 |
| cxx_classes | 42 | 19 | 6 | 6 |
| c_elf | 102 | 20 | 3 | 3 |
| c_elf_alt | 18 | 4 | 2 | 1 |
| cxx_more | 154 | 13 | 3 | 2 |
| cxx_alt | 7 | 4 | 1 | 1 |
| c_byval | 1 | 1 | 2 | 1 |
| c_opaque | 2 | 1 | 2 | 1 |
| c_layout | 41 | 23 | 1 | 1 |
| cxx_stdlib | 7 | 4 | 1 | 1 |
| cxx_dialect | 1 | 1 | 3 | 1 |
| cxx_src, cxx_tree | 0 | 0 | 2 | No eligible edit in this configuration |

For example, in c_core the enum client alone detects a changed enumerator
value, the field client alone detects a variable type change, the call
client alone detects an appended parameter, and the allocation client alone
detects an appended record field. Each contributes a distinct witness.
In c_opaque the handle client survives private-record growth. Its successful
run provides a compatibility control even though removing it loses no
positive detection.

The per-seed minima sum to 22 of 30 shapes for this detection objective.
Six shapes add no positive detection in seeds with executed edits. Two
belong to seeds without eligible edits. Stronger objectives such as retaining
negative controls, identifying which consumers are affected, testing source
compatibility or distinguishing different failure outputs can require more
clients. This experiment changes no suites or default test selections.

Run from the repository root:

```sh
PYTHONPATH=. python research/onedal-coverage/forge_client_ablation.py clients.json
```

The [recorded matrix](forge-client-matrix.json) includes all detected edits,
successful controls and exclusions. The operator names and build configuration
define the scope; this matrix does not measure product coverage.

Independent builds with the Forge implementation from main at `e2fc8dc2a`
and the PR at `164c1630d` produced byte-identical client matrices.
