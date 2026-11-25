# CPU contract inventory and mutation receipts

Target: conda-forge dal 2025.9.0, Linux x86-64 SysV, C++17, GCC-built consumers, Clang 19 AST, four CPU DSOs. See ../README.md for the 23 release controls. This campaign uses stripped ELF and public headers. Old-client execution is independent of the checker.

## Inventory and current coverage

169 source entry headers include daal.h and installed modern headers outside detail, backend, gpu and spmd directories. Six parameter headers independently fail because an included backend header is absent. roots.txt and unparsed.csv preserve those cases. Successful translation units yield 16,528 candidate rows. Scope follows installed paths and C++ access, so undocumented or inline declarations can occur. Legacy declarations outside the daal.h include closure, protected interfaces, variable exports, alternate macro configurations and member-template specializations require additional inventory. Clang supplies 1,738 constructor ABI variants and 1,368 destructor ABI variants in addition to source declarations. Dependent instantiations remain incomplete. Some headers parse through a shared translation unit. A successful parse does not establish standalone includability.

ledger.csv.gz joins Clang declaration USRs and obligation kinds to header locations, mangled symbols, DSO providers, direct imports from the 23 clients, and registered operator-family witnesses. Empty evidence cells mean unknown. The 3,343 distinct matched exports include 1,118 record-returning names. 83/3,343 matched names occur in client imports. Of 16,528 candidate rows, 209 have an explicit mutation witness (10 fields, 2 virtual methods, 3 Status operations, 6 by-value arguments and 188 by-value returns). This measures linked evidence per inventory row. It is a lower bound and gives no estimate of detection probability. Multiple operations may apply to one declaration. DSO wrappers around range in contracts.py are separate mechanism probes. The denominator for real-product coverage excludes these synthetic functions.

For the next product-specific witnesses, the return spellings services::Status and data_management::NumericTablePtr account for 708 and 186 provider-matched return rows. Exercise nontrivial copy/destruction and old-client ownership across DSOs. Keep package-only and source-assisted root results separate. dependencies.txt lists actual dependency SONAMEs and version needs from readelf. A version-node probe checks old-loader behavior independently; the minimum deployable oneDAL environment has not been established.

## Reproduction

Install clang==19.1.7 Python bindings, libclang 19, GCC and the old CPU environment from ../conda. Pass all 23 retained consumer executables to inventory.py PREFIX OUTPUT CLIENT... . ledger.py OUTPUT writes the compressed ledger and coverage.json. LIBCLANG overrides the libclang library path. Set ABICHECK_AST_FRONTEND=clang and put clang++ on PATH. Run contracts.py PREFIX and bash provider_move.sh here. Run visitor.py PATH_TO_PILOT_OUT after the earlier pilot.

contracts.csv records nonzero old-client results and both levels of checker evidence for each broken variant and its compatible twin. visitor.csv repeats the inherited field and virtual-slot changes with stripped binaries and three controls. provider-move.csv tests a real DT_NEEDED edge between two synthetic DSOs: moving the explicit instantiation preserves the old client when its new owner is shipped, and prevents loading when that DSO is omitted. A scalar scan of the old provider against the forwarding DSO says BREAKING in both cases. This is a scope-dependent result for the single file. The directory and consumer-specific results are given below. These mechanism fixtures do not assert that oneDAL made either change.

## Follow-up on package resolution and object lifetime

Running provider_move.sh now scores the directory entry point too. With a DT_NEEDED edge to the shipped owner, the old client exits 0. Omitting the owner gives loader exit 127. The default directory comparison returns release BREAKING, bundle NO_CHANGE and zero bundle findings for both variants. The removed export in the original member explains the release exit. A consumer-impact diagnostic still needs to distinguish the two deployment outcomes. This experiment concerns one unversioned explicit instantiation on Linux. Other consumers could depend on the original provider directly, including through handle-specific lookup.

ownership.cpp adds a release control using oneDAL error Status, Status copy/assignment/clear, NumericTablePtr ownership transfer, and a data read after the original owner goes out of scope. Compile once and run both versions:

```bash
p=/tmp/dal-2025.9.0
g++ -std=c++17 -O1 -I"$p/include" -I"$p/include/dal" ownership.cpp "$p"/lib/libonedal{_core,_thread}.so.3 -o ownership
for v in 2025.9.0 2025.11.0; do LD_LIBRARY_PATH="/tmp/dal-$v/lib" ./ownership || exit; done
```

Both runs completed 256 iterations with exit 0. This tests the exercised lifetime behavior. It does not count all functions returning those types as covered.

## Consumer-specific binding and lifetime results

PR #2164 runs four real Linux loader cases under eager binding. See provider-binding.csv for post-fix results. The existing deps compare command computes the reachable graph. On main, a reachable V2-only owner gave a loadability warning even though the V1 client exited 127. With the fix, reachable V1 passes and absent, wrong-version and unreachable owners fail. All four retain an independent ABI risk warning. The directory release verdict still concerns the whole published interface.

PR #2165 exercises a nontrivial C++ return, copying, and destruction through a stripped DSO. lifetime.csv records the post-copy value and release count for an unchanged client. Baseline and compatible twin yielded (17, 1). Omitting the retain yielded (-1, 1), suppressing release yielded (17, 0), and duplicating the release event yielded (17, 2). All processes exited zero. Each mutant gave UNCONFIRMED on ELF alone and NO_CHANGE with headers. This is a synthetic lifetime contract. ownership-imports.csv joins the separate real oneDAL ownership client’s direct imports with 29 ledger obligations on 26 distinct names. Import presence alone establishes neither execution nor mutation coverage.

Run python root_context.py CONDA_PREFIX ONEDAL_SOURCE from this directory using the matching oneDAL 2025.9.0 source. root-context.csv records the first GCC diagnostic for each standalone root in each context. Six fail with installed headers alone (missing backend dispatcher), and six fail with the source include directory added (missing generated CPU dispatcher). All six compile with the source tree and its documented IDE preprocessing branch. Source-assisted success describes that configuration. The package-only inventory still has six unresolved roots.

Run bash status_clear.sh OLD_PREFIX NEW_PREFIX to reproduce the additional product-specific mutation. It compiles ownership.cpp once and interposes the exact Linux symbol for Status::clear. The baseline and forwarding twin pass on both packages. Replacing clear with a no-op gives exit 3 on both, the client's explicit post-clear state assertion. status-clear.csv registers this one operation in the ledger. It is an injected behavioral mutation, not an observed release regression.

The next product tests should mutate a specific oneDAL construction, copy, release or return obligation, compile the old client once, and exercise each broken/compatible pair. Register each independent observation against that declaration and operation. Extend the CPU inventory separately for dependent instantiations and configurations, and measure the SYCL surface with device-specific clients before making any whole-product claim.

Run `python quality.py` for per-family counts from the client experiments and a ranked list of remaining by-value return targets. Counts are per observed variant and scan lane. The lifetime oracle checks the surviving value and deleter count. Treat a risk on a passing control as a review item. The ranking prefers a return spelling already imported by a client, then the number of distinct unwitnessed symbols with an identified provider. Each new mutation still needs an independent client observation.

## By-value returns checked against the installed CPU packages

From the repository root, run
`python research/onedal-coverage/product/return_campaign.py OLD_PREFIX NEW_PREFIX OUTPUT`
with Linux x86-64 Conda installations of oneDAL, GCC and abicheck. The recorded run used GCC 14.2 and oneDAL 2025.9.0 and 2025.11.0. The generator compiles one old executable, checks its undefined symbols and builds ELF adapters for 67 exact C++ signatures (65 table getters and two status returns). Each adapter calls the package implementation. The positive control forwards its return unchanged. The old executable runs once per package, operation and variant. See `return-witnesses.csv` for observed process exits.

- For each getter in `return-getters.csv`, the client stores a one-cell table with a counting data deleter, retrieves it, drops the result and original owners, reads 17.25 through the surviving pointer, and releases it. Borrowing the table released its data too early (exit 11 before dereference). Holding an extra owner delayed release past reset (exit 12). The two controls observed zero releases after dropping the original owners and exactly one after reset.
- For `NumericTable::allocateBasicStatistics()`, the client invokes the base implementation on a three-column table, checks success and verifies four separate one-row, three-column statistics. Replacing success with an error gives exit 30. Removing the sum table while preserving the successful return gives exit 31. The latter checks a method postcondition and contributes no additional return-type coverage.
- For batch `AlgorithmImpl::computeNoThrow()`, the client checks an invalid input, a surviving copy of its error status, and valid input. Swallowing the error gave exit 20. Replacing a successful return with an error gave exit 23.

`return-scans.csv` compares each stripped adapter against the corresponding forwarding adapter once, independently of the two package runs. The client exit in each scan row is the checked expectation reproduced on both packages. Dependency and runtime-floor changes seen in an adapter do not identify the ownership or error-propagation contract. These are injected edits. Coverage is credited only to the 67 specific by-value return rows. Each getter was exercised with one named result ID. The original result-getter campaign left 121 further provider-matched return symbols spelled `data_management::NumericTablePtr`. Among their demangled names are 80 `Input::get` and five `PartialResult::get` methods. These counts identify candidates for additional clients. The table-accessor campaign below covers the other 121 sites.

All 65 provider-matched `Result::get` overloads returning `data_management::NumericTablePtr` in this inventory have an old-client witness. The 14 imported by the original example corpus and 51 invoked by the expanded client use the same ownership checks. Three overloads read element zero of an explicitly populated data collection. The linear model prediction result requires sized storage, the iterative solver optional result requires an optional-argument container, and the stump classification result requires storage for its variable-importance slot. The fixture constructs these through the old public headers before exercising the corresponding getter. The measurements concern one slot per overload and two package versions on Linux x86-64.

Run `bash ownership_campaign.sh OLD_PREFIX NEW_PREFIX > ownership-campaign.csv` here to reproduce ownership-campaign.csv.
The C1 copy mutant yields exit 11 and the assignment mutant yields 12. Exit 13 checks the survivor after scope exit. Each passing run also checks that the NumericTablePtr deleter is called zero times before the final owner resets and exactly once afterwards, with a successful row read in between. The deleter holds the backing table alive. This counts the ownership callback and adds no exported-symbol mutation credit for the inline smart pointer.

### ABIForge visitor permutations and released exports

On x86-64 Linux, the visitor campaign uses the tree declarations at oneDAL
commit `cb26da82f4672a7aad7e8d27be7d6b82fa1f300b`. A separately compiled
old classification or regression client consumes both callbacks. Each variant
permutes one declaration group while keeping all other declarations fixed.
The campaign requires agreement on client behavior, L2 availability, verdict,
and the exact moved field bit offsets or virtual slot positions.

```sh
ABICHECK_AST_FRONTEND=clang python -m abiforge.run.onedal_tree out/tree --source /path/to/oneDAL
```

The two identity controls and 28 permutation variants cover all single-group
field and callback reorderings for these two visitors. Six additional tests append an eight-byte field to a base, split or leaf descriptor. A frozen client indexes the second descriptor in an array, and L2 reports the exact size change in bits. The released-export
check has a separate denominator. It compares GNU nm definitions against
abicheck findings for each provider and symbol named by the product ledger.
One likely-rename finding identifies a missing old name in the major transition.
This check scores name availability only. Compare each of the four CPU DSOs
with the public CLI, retaining the JSON reports, then run

```sh
python -m abiforge.run.onedal_exports "$OLD/lib" "$NEW/lib" \
  research/onedal-coverage/product/ledger.csv.gz reports/*.json
```

Including the extended ledger with `--additional-ledger extended-obligations.csv.gz`, across the 2025.9.0 to 2025.11.0 transition all 3,517 provider-symbol pairs
remained defined. Across 2025.9.0 to 2026.2.0, 1,339 disappeared. Both comparisons
matched the independent symbol inventory for every pair. These measurements
complement the mutation-witness counts in the product ledger.

The per-variant outcomes and release binary SHA-256 digests are recorded in
[abiforge-cpu-receipt.json](abiforge-cpu-receipt.json).

### By-value arguments and table ownership

Run these commands from the repository root against the 2025.9.0 and 2025.11.0
CPU package prefixes on Linux x86-64. The full L2 comparison uses several
gigabytes of temporary space.

```sh
python -m abiforge.run.onedal_arguments "$OLD" out/arguments --check
python -m abiforge.run.onedal_table_ownership "$OLD" "$NEW" out/tables
python -m abiforge.run.onedal_loadability "$OLD" out/load
```

[argument-abi.json](argument-abi.json) records all six provider-matched
`by_value_arg0` signatures. Defaulting the copy constructor and destructor
of the SDK's three-pointer SharedPtr changes each argument from an invisible
reference to a stack copy. The exported names and object sizes survive.
Clang IR records the calling sequence. A frozen GCC client passes a tagged
pointer through each real SDK entry point to a stripped observation adapter.
It exits 0 for both controls and 41 for every mismatched argument.
The public checker independently compares libonedal_core with the respective
headers and identifies argument zero on each symbol. The merged ledgers contain
49 source argument obligations across positions zero through seven. Only the six
position-zero declarations match an exported provider.

[table-accessors.csv](table-accessors.csv) selects one named table, index or
model member per additional access path. [table-ownership.csv](table-ownership.csv)
records the frozen client's four exits per release for all 186 symbols (1,488 runs).
Every one of the 186 provider-matched
`data_management::NumericTablePtr` returns has a lifetime witness. Controls
retain a readable table and release its data exactly once. Borrowing and
holding an extra reference produce distinct exits. These body-only edits
are measured by executing a client.

[cpu-loadability.json](cpu-loadability.json) checks the complete four-library
closure and each single missing SONAME with eager ELF binding. The OS loader
exited 0 for the control and 127 for each omission. The checker predicted the
same five outcomes through `abicheck deps compare`.

### Expanded CPU inventory

The original 16,528-row ledger remains the reference for the 209 mutation
witnesses. The source-assisted profile includes legacy headers outside daal.h,
protected declarations and provider-matched variables. It parses 541 selected
headers in translation-unit groups and yields 18,808 candidate rows and 3,516
distinct provider-matched names. [Extended inventory](extended-inventory.json)
records the optional header hashes. [Additional obligations](extended-obligations.csv.gz)
uses the same declaration key, kind and mangled symbol to join to the original
ledger. Standalone installed-header failures remain recorded in root-context.csv.

```sh
python research/onedal-coverage/product/inventory.py "$OLD" out/inventory \
  --extended --source "$ONEDAL_SOURCE" --legacy-umbrella \
  --include "$ARROW_INCLUDE" --include "$KDB_INCLUDE" \
  --include "$ODBC_INCLUDE" --include "$ODBC_PLATFORM_INCLUDE"
```

The source-assisted run used the pinned product checkout, Arrow 16.1 headers,
KX k.h, unixODBC 2.3.11 and libclang 19. This inventory covers the selected
Linux CPU preprocessing configuration. Each supported macro configuration,
target ABI and template instantiation needs its own evidence.

### ELF export coverage and next targets

The three Linux x86-64 packages each ship six oneDAL DSOs. In 2025.9 they
define 22,428 (SONAME, dynamic name) pairs and 18,619 distinct names. The two
SDK ledgers match 3,517 pairs (15.7%) and 3,516 distinct names (18.9%).
These denominators include backend internals, template instantiations, RTTI
and vtables. Inlined operations, record layouts, calling conventions and
kernel execution each need their own witnesses. A matched name alone measures
symbol identity. Combining these ledgers with the fresh all-provider scan yields
3,517 distinct names and 3,667 provider/name pairs (16.4%). The legacy ledger also records independent edit witnesses
for 197 provider/name pairs. Later campaigns record additional mutation
evidence separately. Joining the exact 947 L2 Status names, the 197 ledger
edit witnesses and four real minor-release withdrawals gives **1,144 distinct
exports with at least one named change witness** after deduplication. This is
a lower bound of 6.1% of names, or 5.1% of provider/name pairs. Each witness
covers a particular change. This bound excludes probes without an exact
symbol join.

| 2025.9 provider | Defined names | SDK ledger pairs | Fresh host SDK scan, all providers | Imports matched in 12 frozen clients |
| --- | ---: | ---: | ---: | ---: |
| Host | 3,670 | 148 | 148 | 57 |
| Core | 13,352 | 3,367 | 3,367 | 30 |
| DPC | 4,872 | 0 | 149 | 59 |
| Parameters (host) | 99 | 2 | 1 | 0 |
| Parameters (DPC) | 152 | 0 | 1 | 0 |
| Thread | 283 | 0 | 0 | 0 |

Import counts join undefined client symbols to matching exports in directly
needed DSOs. They measure candidate bindings and can include unused call paths.
The DPC library exports 148 names also recorded against the host provider.
The report keeps provider identities separate. All 3,516 distinct baseline SDK
names survive in 2025.11. Its own SDK scan matched 3,521 of 18,626 distinct
names. In 2026.2, 2,178 survive by spelling in a package
with new SONAMEs. A fresh host-header scan of 2026.2 matched 2,188 of its 12,082 distinct names
(18.1%) and 2,345 of 15,742 provider/name pairs. The installed-header scans
parsed 535 of 541 roots for 2025.9 and 426 of 432 for 2026.2. Both skipped six
CPU parameter headers requiring the source-tree backend dispatcher. [Machine-readable counts and missing client imports](export-surface.json)

The frozen-client compiler pass located the SDK declarations for the remaining
48 DPC, 11 host and four core imports in 2025.9. Together with the first-pass
ledger this binds all 59 observed DPC imports. For RTTI and vtable objects the
entry points to the defining class. These counts measure provider/name pairs.
The withdrawal runner uses one mutation rule across them. It checks the old
client's relocation, runs its normal workload, and compares the withdrawn ELF
exports with the public checker. The real minor-release losses have a separate
[four-relocation experiment](minor-dpc-withdrawals.json).
Normal execution failed in 44 withdrawals. The other 19 were visible during
eager relocation checks and the selected workload still exited successfully.
Grouping by Itanium entity, relocation kind and observed process exit yields
five distinct combinations on x86-64. One representative per combination
suffices to replay these observed outcomes. The selection does not measure
calling-convention or device coverage.
[Compiler locations](import-declarations.json) · [Withdrawal results](import-withdrawals.json) · [Five replay cases](withdrawal-scenarios.json)

The 2026.2 host inventory with the source dispatcher parsed all 432 roots and
matched 2,189 distinct exported names. Each of the six CPU parameter headers
contributed declarations. With the same roots, enabling
`ONEDAL_DATA_PARALLEL` added 61 public host-side callable signatures,
including `table::get_queue()` and the two-argument SYCL communicator factory.
Runtime evidence for the parameters/thread providers and replay on a physical
Level Zero GPU remain open. [Major-release measurements](2026.2/)

Reproduce the ELF survey with the prefixes, optional SDK include directories
and frozen executables used by the campaigns below. The first-pass inventory
parses the installed SDK in host C++ mode and joins its names to every DSO,
including both DPC providers.

```sh
for sdk in "$OLD" "$MINOR" "$MAJOR"; do
  python research/onedal-coverage/product/inventory.py "$sdk" "$OUT/inventory-$(basename "$sdk")" \
    --extended --legacy-umbrella --all-providers --include "$ARROW_INCLUDE" \
    --include "$KDB_INCLUDE" --include "$ODBC_INCLUDE" --include "$ODBC_PLATFORM_INCLUDE"
done
python -m abiforge.run.onedal_export_surface "$OLD" --candidate "$MINOR" --candidate "$MAJOR" \
  --baseline-inventory "$OUT/inventory-$(basename "$OLD")" \
  --candidate-inventory "$MINOR" "$OUT/inventory-$(basename "$MINOR")" \
  --candidate-inventory "$MAJOR" "$OUT/inventory-$(basename "$MAJOR")" \
  --client "$OUT/clients/release_client" --client "$OUT/options/client" \
  --client "$OUT/table-layout/host" --client "$OUT/table-layout/sycl" \
  --client "$OUT/sparse-indexing-clang/host" --client "$OUT/sparse-indexing-clang/sycl" \
  --client "$OUT/device/event" --client "$OUT/device/buffer" --client "$OUT/device/csr" \
  --client "$OUT/device/ownership" --client "$OUT/device/dependency" --client "$OUT/device/spmd" \
  --status-report "$OUT/original-castxml/old-mutant.json" --out "$OUT/export-surface.json"
```

Replay the compiler join and the release-client mutations with DPC++ 2025.2,
Open MPI, and patchelf with `--rename-dynamic-symbols` support. Supply the
ELF survey generated above so its executable labels match the output tree.

```sh
python -m abiforge.run.onedal_import_declarations "$OLD" "$ICPX" "$MPI_INCLUDE" "$OUT/import-declarations.json"
python -m abiforge.run.onedal_import_withdrawals "$OLD" "$MINOR" "$OUT" "$OUT/withdrawals" --surface "$OUT/export-surface.json"
python -m abiforge.run.onedal_withdrawal_scenarios "$OUT/withdrawals/receipt.json" "$OUT" "$OUT/scenarios.json"
python -m abiforge.run.onedal_import_withdrawals "$OLD" "$MINOR" "$OUT" "$OUT/representatives" --surface "$OUT/export-surface.json" --selection "$OUT/scenarios.json"
```

For the major SDK scan, use oneDAL source commit
`480788c54a09ddd3dbc7cc9bc76d2ee4ea350020` (tag `2026.2.0`).
The include view below selects the installed oneDAL headers and lets the
compiler supply its matching SYCL runtime headers.

```bash
python research/onedal-coverage/product/inventory.py "$MAJOR" "$OUT/major" \
  --source "$ONEDAL_SOURCE" --extended --legacy-umbrella --all-providers \
  --include "$ARROW_INCLUDE" --include "$KDB_INCLUDE" --include "$ODBC_INCLUDE" --include "$ODBC_PLATFORM_INCLUDE"
view="$OUT/major-headers"
mkdir -p "$view/oneapi"
ln -s "$MAJOR/include/dal" "$MAJOR/include/daal.h" "$view/"
ln -s "$MAJOR/include/oneapi/dal" "$MAJOR/include/oneapi/dal.hpp" "$view/oneapi/"
mapfile -t roots < "$OUT/major/roots.txt"
python research/onedal-sycl-usm/inventory.py "$ICPX" "$view" "${roots[@]}" \
  --extra-flag=-D__ONEDAL_IDE_MODE__ --extra-flag="-I$ONEDAL_SOURCE/cpp" \
  --extra-flag="-I$view/dal" --extra-flag="-I$ARROW_INCLUDE" \
  --extra-flag=-include --extra-flag=arrow/array.h > "$OUT/major/sycl.tsv"
```

### Device release replay

DPC++ 2025.2.0 and Intel OpenCL CPU on a Xeon Platinum 8573C ran the seven
queue overload withdrawal checks and four release clients. Run with the
matching compiler/runtime directories on PATH and LD_LIBRARY_PATH and the
CPU ICD configured.

```sh
python -m abiforge.run.onedal_sycl out/queue --prefix "$OLD" --compiler "$ICPX"
python -m abiforge.run.onedal_table_layout "$OLD" "$MINOR" "$CXX_SYCL" "$OUT/table-layout"
python -m abiforge.run.onedal_device_release "$OLD" "$NEW" "$ICPX" out/device
```

The frozen event client checked 256 values for each of host, shared and device
USM on both releases. The other clients check buffer values, CSR indices,
ownership and deleter calls. [Device release receipts](opencl-cpu-release.json)
and [queue withdrawal receipts](opencl-cpu-queue.json) record the observed
outcomes. GPU execution and distributed runtime coverage are separate profiles.

### Small C++ record returns

Run `python -m abiforge.run.onedal_status_return SDK_PREFIX OUTPUT` on Linux
x86-64 to replay the two Status callers in `status-return-abi.json`.
The frozen callers first run against the released library. Stripped adapters
then observe the receiver argument under the original and mutated SDK
declarations. Defaulting the copy constructor and destructor moves the
eight-byte result from a hidden pointer to an integer register. The L2
comparison checks this type-level change independently of the two client exits.

### Three-release L2 and frozen-client matrix

The baseline is Linux x86-64 oneDAL 2025.9.0. The minor and major candidates
are 2025.11.0 and 2026.2.0. The major package supplies SONAME 4. Lookups in
its successor provider measure export retention separately from whether an
old executable can load.

| Artifact | Measurement |
| --- | --- |
| `release-matrix.csv.gz` | 3,521 declaration/provider obligations checked against the dynamic exports of each released image. |
| `status-impact.csv.gz` | Each SDK Status return's provider and virtual/nonvirtual declaration, joined to the L2 affected-symbol lists from both mutation directions and the unchanged control. The public CLI consumes a return-slot projection of the SDK ledger and the real core DSO. The three executable clients separately exercise actual method signatures. |
| `status-original-headers.json` | Original SDK headers and stripped core ELF. Both frontends cover all 795 ledger exports in both directions. Explicit SDK instantiation declarations materialize the 38 exported member specializations that Clang previously omitted. A separate compiler witness checks all 38 actual calls for the hidden return pointer transition, using access-control relaxation to exercise internal member templates. Both frontends attribute the same 947 names. |
| `virtual-slots.csv.gz`, `header-slots.csv` | Relocation targets in exported ELF vtables and byte offsets from the table symbol. Three unmatched methods have separate SDK pointer-to-member measurements relative to the vtable address point. Empty ELF cells convey lack of a unique observed relocation. |
| `serialization-slots.json` | Frozen calls to both serialization methods through a secondary base in a derived fixture. Inserting a method before the first dispatch changes the two client exits from 21/22 to 23/21. The checker also detects reordering and removal. |
| `return-lowering.csv`, `shared-return-types.csv` | Clang IR for 225 remaining SDK return declarations and 52 concrete SharedPtr types exercised across a compiled return boundary with deleter counters. These type-level probes represent 185 exports. |
| `option-returns.csv`, `release-clients.json` | Frozen clients import the 38 option getters and verify mask values on both minor releases. Additional real clients exercise heterogen table and exception returns, two collection getters with ownership mutants, virtual destruction and the exported string limit. |
| `variant-loss.json` | Six individual export withdrawals compared against six independent ELF relocations and an unchanged fixture. |
| `sparse-indexing-clang.json`, `sparse-indexing-castxml.json` | Frozen host and SYCL CSR accessors pull two rows, including an empty row. The fixture provider writes the requested indexing into a shared C++ record. Swapping the enum values or narrowing its storage changes both column indices and row offsets. |
| `table-layout.json` | Frozen host and SYCL clients read a converted row slice from a column-major table on both minor releases. A separate fixture DSO compiled against reordered SDK enum members supplies the layout value. Both clients observe the corresponding row-major sequence. Appending an enumerator preserves the original observations. |
| `device-release-matrix.json` | Frozen clients on the OpenCL CPU, including a cross-queue host-task dependency, and a standalone major-release loader control. The optional two-rank Open MPI run checks typed and byte-buffer collectives through the SYCL communicator. GPU replay accepts `--selector level_zero:gpu` or `--selector opencl:gpu`. |

The minor release adds exported vtables for optimization_solver BatchIface and kmeans init BatchBase in libonedal_core. Both tables are 88 bytes in 2025.11. Their observed relocations at offsets 32 and 40 target checkComputeParams and checkResult respectively. All previously observed cells retain their targets.

Run from the repository root with the installed prefixes in `OLD`, `MINOR`,
and `MAJOR` and a writable output directory `OUT`. The lowering campaign
requires matching Clang 19 Python bindings and libclang (`LIBCLANG` can be
set to the shared library). The device campaign requires a SYCL compiler and
runtime.

```sh
python -m abiforge.run.onedal_release_matrix "$OLD" "$MINOR" "$MAJOR" "$OUT/matrix.csv"
python -m abiforge.run.onedal_virtual_slots "$OLD" "$MINOR" "$MAJOR" "$OUT/slots.csv"
python -m abiforge.run.onedal_status_return "$OLD" "$OUT/status"
python -m abiforge.run.onedal_status_member_templates "$OLD" "$MINOR" "$OUT/status" "$OUT/member-templates"
python -m abiforge.run.onedal_minor_withdrawals "$OLD" "$MINOR" "$OUT/minor-withdrawals"
python -m abiforge.run.onedal_status_exports "$OLD" "$OUT/status" "$OUT/impact"
for backend in clang castxml; do
  python -m abiforge.run.onedal_status_exports "$OLD" "$OUT/status" "$OUT/original-$backend" --original-headers --backend "$backend"
done
python -m abiforge.run.onedal_serialization_slots "$OLD" "$OUT/serialization"
python -m abiforge.run.onedal_return_lowering "$OLD" "$OUT/lowering"
python -m abiforge.run.onedal_shared_return_types "$OLD" "$MINOR" "$OUT/shared"
python -m abiforge.run.onedal_option_returns "$OLD" "$MINOR" "$MAJOR" "$OUT/options"
python -m abiforge.run.onedal_release_clients "$OLD" "$MINOR" "$MAJOR" "$OUT/clients"
python -m abiforge.run.onedal_variant_loss "$OLD" "$OUT/variants"
python -m abiforge.run.onedal_table_layout "$OLD" "$MINOR" "$CXX_SYCL" "$OUT/table-layout"
for frontend in clang castxml; do
  python -m abiforge.run.onedal_table_layout "$OLD" "$MINOR" "$CXX_SYCL" "$OUT/sparse-indexing-$frontend" --enum sparse_indexing --frontend "$frontend"
done
python -m abiforge.run.onedal_device_release "$OLD" "$MINOR" "$CXX_SYCL" "$OUT/device" --major "$MAJOR" --mpi
```
