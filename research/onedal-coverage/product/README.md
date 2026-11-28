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

A standalone GCC replay against the installed 2025.9, 2025.11 and 2026.2
include packages reproduced the missing dispatcher diagnostic in all 18 cases.
Matching the six header families to demangled ELF export names identifies 40
distinct call-operator signatures in each release. The CPU and DPC parameters
libraries both export all 40; the other four providers export none. PCA
accounts for 18 signatures, decision forest training for eight, linear
regression for six, logistic regression for four, and covariance and decision
forest inference for two apiece. These name matches establish provider
coverage. They leave standalone compilation and runtime behavior distinct.

```sh
python research/onedal-coverage/product/parameter_header_exports.py "$OLD" "$MINOR" "$MAJOR" --out "$OUT/parameter-header-exports.json"
```

A frozen public covariance client exercises two of the 40 parameter selectors.
Compiled against 2025.9, it computes the unbiased sample covariance for rows
(1, 2), (2, 4), (3, 6) in float and double on both 2025.9 and 2025.11.
The client checks all four matrix entries against [[1, 2], [2, 4]]. For each
precision it also calls the disabled correlation-matrix and means getters.
The frozen executable catches `oneapi::dal::domain_error` from both getters
on both minor releases. A second client compiled against 2025.11 also passes
these checks on 2025.9. In both directions the provider-scope test compares
the client's required frontend symbol count with GNU nm and checks the
`abicheck compare --used-by` consumer verdict of COMPATIBLE. The frontend
comparison without headers returns UNCONFIRMED in both directions.
Rebuilding against 2026.2 reproduces the numerical and exception results.
Loader traces identify the CPU parameters DSO; replacing one selector at a
time with a function that exits 42 confirms each precision reaches its own
selector on both releases.

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --out "$OUT/covariance-parameters.json"
```

The covariance regression also compares both 2025 releases with 2026.2 in
both directions. For all four cross-major runs the loader exits 127 because
the candidate package lacks the parameters SONAME recorded by the client.
Comparing the explicitly selected frontend libraries with `--used-by` gives
a second observation. The 2026.2 frontend supplies all 31 symbols required
by each 2025 client. The 2026.2 client requires 32 frontend symbols, and
both 2025 frontends lack the complete-object destructor for
`covariance::compute_input<task::compute>`. GNU nm and the checker report
the same missing symbol.

The linear regression client exercises six more selectors across batch training,
partial training, and finalization in float and double. Both the batch model
and the model trained in two blocks predict −1 and 9 on held-out inputs.
The same binding and individual call checks pass on both releases, bringing
runtime coverage to 8 of the 40 inventoried Linux CPU parameter selectors.

Each 2025 linear regression client imports 49 symbols from the CPU frontend.
All three frontends export those symbols. The 2026.2 client imports 51, and
both 2025 frontends lack the complete-object destructors for `train_input`
and `partial_train_input`. Each directed release comparison passes all seven
CPU clients to one `abicheck compare` invocation with repeated `--used-by`
arguments. The test checks each client's missing-symbol list against GNU nm.

```sh
ABIFORGE_ONEDAL_OLD="$OLD" ABIFORGE_ONEDAL_NEW="$MINOR" ABIFORGE_ONEDAL_MAJOR="$MAJOR" python -m pytest -q tests/test_abiforge_onedal_product.py::test_frozen_cpu_client_provider_scope
```

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --algorithm linear_regression --out "$OUT/linear-regression-parameters.json"
```

The covariance-method PCA client trains on two perfectly correlated columns.
Batch and two-block incremental training both produce correlation eigenvalues
2 and 0 in float and double on both releases. Of six exported selectors for
this method, only the two batch overloads bind and execute in this client.
Interposing each partial-training and finalization overload leaves the
numerical check successful. This brings confirmed calls to 10 of 40.

The covariance-method PCA client imports 44 CPU frontend symbols when built
against either 2025 SDK. All three frontends export those names. The 2026.2
client imports 47. Both 2025 frontends lack the complete-object destructors
for PCA batch and partial training inputs and the noise-variance option ID
getter. The 2026.2 public header calls that getter to initialize the inline
`result_options::noise_variance` variable, so this client imports it even
though the fixture reads only eigenvalues. A control with just the PCA header
and an empty `main` reproduces the import on 2026.2 alone.

The batch SVD and precomputed PCA clients each import 35 frontend symbols
on the 2025 SDKs and 37 on 2026.2. When comparing either 2026.2 client to
either 2025 frontend, the two missing symbols are the batch training input
destructor and the noise-variance option ID getter. Only the covariance-method
PCA client performs partial training and requires the partial input destructor.

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --algorithm pca_cov --out "$OUT/pca-cov-parameters.json"
```

The SVD-method PCA batch client produces the same eigenvalues on both releases
and calls the float and double batch selectors, bringing the total to 12 of 40.
The SVD campaign covers batch training only. In a separate two-block run on
the same input, the leading eigenvalue was approximately 1.66667 in both
precisions on both releases. The cause of the difference from batch is open.

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --algorithm pca_svd --out "$OUT/pca-svd-parameters.json"
```

Precomputed PCA takes the two-by-two correlation matrix with every entry equal
to one as its batch input. Its eigenvalues are 2 and 0. Both releases pass
this check in float and double and reach the corresponding batch selectors.
Across the three PCA methods, six of 18 exported selectors now have observed
calls. Across all algorithm families, the running total is 14 of 40.

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --algorithm pca_precomputed --out "$OUT/pca-precomputed-parameters.json"
```

The decision forest client checks predictions on two held-out rows for
classification and regression with dense and histogram training, in float
and double. All eight combinations pass on both releases. Loader traces show
no binding from libonedal to the CPU parameters provider for the ten decision
forest selectors, and interposing each selector individually leaves the client
successful. Confirmed selector calls therefore remain at 14 of 40.

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --algorithm decision_forest --out "$OUT/decision-forest-parameters.json"
```

Logistic regression reaches all four CPU training selectors before throwing
`oneapi::dal::unimplemented`. The frozen client catches the exception across
the shared-library boundary and checks the method-specific diagnostic in float
and double, using dense and CSR inputs. Both releases behave identically.
This brings confirmed calls to 18 of 40. All 40 selectors have now been
individually probed across the campaigns. Calls to the other 22 were
unobserved with these clients (12 PCA and 10 decision forest).

```sh
python -m abiforge.run.onedal_covariance_parameters "$OLD" "$MINOR" --algorithm logistic_regression --out "$OUT/logistic-regression-parameters.json"
```

Across 2025.9, 2025.11 and 2026.2, the Linux CPU and DPC frontends each
contain named dynamic relocations for the same 18 selectors. All are
`R_X86_64_JUMP_SLOT` entries. Their names exactly match the selectors called
in the two 2025 CPU campaigns. The other 22 have no named dynamic relocation
in either frontend. Other consumers can still reference those exports.
DPC observations here come from ELF inspection alone.

```sh
python -m abiforge.run.onedal_selector_relocations "$OLD" "$MINOR" "$MAJOR" --out "$OUT/selector-relocations.json"
```

The DPC parameters provider also exports 42 GPU selector overloads on each
release. The DPC frontend references 30, including all 12 decision forest
selectors, six linear regression, six PCA, four logistic regression, and two
covariance selectors. The remaining 12 are PCA partial-training and
finalization overloads. The CPU frontend references none of the 42. The audit
checks abicheck's undefined symbols against the named GNU readelf relocations
in each frontend. GPU execution remains unverified by this audit.

```sh
python -m abiforge.run.onedal_selector_relocations "$OLD" "$MINOR" "$MAJOR" --device gpu --out "$OUT/gpu-selector-relocations.json"
```

A separate consumer-scope replay builds a fixture library with all 42 GPU
selector names. It removes each group of exports in turn and passes the
packaged DPC frontend to `abicheck compare --used-by`. On all three releases,
removing the 30 referenced names produces exactly 30 missing imports and a
breaking consumer verdict. Removing the 12 unreferenced names produces zero
missing imports and a compatible consumer verdict. Both edits are breaking
at library scope. Each comparison also scopes the same edit to the packaged
CPU frontend, which has zero GPU selector imports. All six CPU control rows
report `NO_CHANGE`, zero relevant changes and zero missing symbols.
These comparisons exercise ELF symbol availability only.

```sh
python -m abiforge.run.onedal_gpu_selector_scope "$OLD" "$MINOR" "$MAJOR" --out "$OUT/gpu-selector-scope.json"
```

The CPU-selector removal campaign compares both packaged frontends against
a fixture exporting the 40 CPU selectors. Removing the 18 referenced names
gives both consumers a breaking verdict with exactly 18 missing imports.
Removing the other 22 leaves both consumer verdicts compatible. The twelve
comparisons cover both edits and both frontends on each of the three releases.

```sh
python -m abiforge.run.onedal_gpu_selector_scope "$OLD" "$MINOR" "$MAJOR" --device cpu --out "$OUT/cpu-selector-scope.json"
```

For each frontend under test, a separate ELF executable links against the
baseline fixture and calls a stub for every referenced selector name. It
exits zero on the baseline. With eager binding, removing the referenced
group yields loader exit 127 and a diagnostic naming a removed symbol.
Removing only the unreferenced group preserves exit zero. The executable
tests loader resolution for these names and uses no device kernels.

The same frozen fixture executable also runs against a provider missing just
one export at a time. Across the three releases, this gives 240 CPU-selector
runs (40 names × two frontend import sets × three releases) and 126 GPU-selector
runs (42 names × one DPC import set × three releases). Each referenced removal
must report the exact missing name and exit 127. Each unreferenced removal
must allow the executable to finish successfully. The per-name loader exits
are included in the two selector-scope reports. For each individual provider
edit, abicheck also reads the candidate ELF exports and evaluates the packaged
frontend's requirements. The replay checks its predicted missing-symbol list
against the fixture loader result. The individual checks exercise the same
requirements evaluator used by consumer scoping; the grouped checks run the
full compare CLI.

Two additional frontend imports match exports of the corresponding parameters
provider on every release: the decision forest `propose_block_size` helper
and the `system_parameters` base-object constructor. They bring the CPU
frontend intersection to 20 names and the DPC frontend intersection to 50.
For each frontend, the helper campaign links a fixture executable against
both names and removes each export separately. Across the three releases
all 12 removals produce the exact missing name predicted by abicheck and
loader exit 127. The real frontends supply the import evidence; the stub
executable supplies the loader observation.

```sh
python -m abiforge.run.onedal_parameter_helpers "$OLD" "$MINOR" "$MAJOR" --out "$OUT/parameter-helper-scope.json"
```

The CPU decision forest training and prediction client reaches both helpers
on all three releases. With eager binding the trace names the parameters
provider for both imports. Interposing each helper individually terminates
the client at that helper, while the unchanged run passes the prediction
checks. Together with the 18 observed selector calls from the other clients,
this gives runtime call evidence for all 20 CPU frontend imports matched
to the CPU parameters provider. The helper audit records abicheck's required
names for each packaged frontend and checks them against GNU nm. Regression
tests compare the CPU names to the observed calls and the 50 DPC names to the
20 shared names plus the 30 GPU selector relocations. DPC helper calls still
lack runtime evidence.

For each release, the helper audit also checks its frontend requirements
against the other two packaged parameters providers of the same family.
Across all six directed release pairs, both candidate providers retain every
required name (20 for CPU and 50 for DPC). GNU nm independently confirms
the missing-symbol sets. This measures export availability; the transition
between SONAME majors still affects deployment of existing clients.

All seven CPU campaigns also run against the 2026.2 SDK. Each fixture is
rebuilt against the new major SONAME (`.so.4`). Their numerical and exception
controls pass, and individual probes record the same 18 called and 22
unobserved selectors. The 2025.9 binary is reused only for the two `.so.3`
releases. To reproduce the three-release receipts, add `--major "$MAJOR"` to
each selector campaign command above.

The reciprocal minor-release replay compiles all seven clients against
2025.11 and runs each unchanged on 2025.11 and 2025.9. Both directions
produce the same numerical and exception control exits, selector calls,
and decision forest helper calls.

```sh
python -m abiforge.run.onedal_covariance_parameters "$MINOR" "$OLD" --algorithm all --out "$OUT/cpu-parameter-downgrade.json"
```

The frozen-client load matrix builds each of the seven clients once per SDK,
checks abicheck’s complete ordered DT_NEEDED list against GNU readelf, and runs the binary
against each pinned package. All 35 same-major runs pass their controls.
All 28 cross-major runs exit 127 before main, with the loader naming the first
missing oneDAL dependency. These outcomes apply to the packages as shipped,
with the target package on the library path. Each client row records the full
direct dependency list and the oneDAL subset used for the package check.
The replay compares the full dynamic import table against GNU readelf,
including symbol binding, version and provider. Across the 21 executables
there are 973 versioned import entries in this build. They name libc,
libstdc++ and libgcc_s. Each row records its versioned import count and
the required version tags by provider. Comparing the clients with their four
direct oneDAL libraries and TBB identifies five distinct client imports with
additional version tags across the seven campaigns in each release. They
include `__libc_start_main@GLIBC_2.34` and the libstdc++ array-length
exception helper at `GLIBCXX_3.4.29`. The receipt identifies each symbol and
provider. These requirements describe executables built on this host.

```sh
python -m abiforge.run.onedal_parameter_load_matrix "$OLD" "$MINOR" "$MAJOR" --out "$OUT/parameter-load-matrix.json" --import-profiles "$OUT/parameter-import-profiles.json"
```

All 21 executables share 46 versioned imports on this build host. Fourteen
have exactly those 46. The three logistic regression clients also import
`memcmp@GLIBC_2.2.5`. The 2025 linear regression and covariance-method PCA
clients additionally reference the libstdc++ vtable for
`__cxxabiv1::__vmi_class_type_info` at `CXXABI_1.3`; their 2026.2 builds
use only the common 46. The optional import profile report records the
symbol, binding, version, and provider for every group.

Eager loader traces also identify the oneDAL provider that resolves each
executable's direct dynamic imports. Across the seven clients the two 2025
SDKs produce the same 201 names, all bound to libonedal.so.3. The 2026.2
builds bind those 201 plus six additional names to libonedal.so.4. The matrix
records each client’s provider/name pairs and checks them on every successful
same-major run. The consumer-requirements evaluator checks each client's imports against each
candidate CPU frontend. Both 2025 frontends lack the six additional 2026.2
names. This affects covariance, linear regression and all three PCA clients.
The decision forest and logistic regression clients retain full name coverage
on downgrade. All seven still encounter the major SONAME boundary first.
All 201 names required by the 2025 clients remain exported by the 2026.2
frontend. GNU nm and the baseline loader bindings independently confirm the
missing sets.

An independent native-linker replay takes one strong address reference per
observed frontend symbol and links it against each packaged libonedal with
GNU ld's `-z defs`. Of 621 links, 609 succeed. Each of the six extra names
in the 2026.2 clients fails individually against each 2025 frontend, with
the exact name in the undefined-reference diagnostic. These are link-time
checks and do not call the functions.

```sh
python -m abiforge.run.onedal_frontend_link "$OUT/parameter-load-matrix.json" "$OLD" "$MINOR" "$MAJOR" --out "$OUT/frontend-link.json"
```

All six additional 2026.2 names also have direct call witnesses. Each client
first runs unchanged. A separate build for each affected import uses GNU ld
`--wrap` to redirect that executable's reference to an exit probe. All ten
client/symbol combinations reach the selected probe. This covers five
input-object destructors and the PCA noise-variance option ID getter.
A separate main-entry marker shows the first getter call precedes main in
all three PCA clients. The 2026.2 SDK initializes the inline noise-variance
result option with this getter. All seven destructor probes first fire after
main entry. A marker-only build of each client passes its normal control.

```sh
python -m abiforge.run.onedal_frontend_calls "$OUT/parameter-load-matrix.json" "$MAJOR" dal-2025.11.0 --out "$OUT/frontend-calls.json"
```

Compiling an empty main with the public PCA header introduces six strong
references matched to the packaged CPU frontend in both 2025 SDKs and seven
in 2026.2. The additional reference is the noise-variance option ID getter.
An empty translation unit with the same main and no algorithm header has
zero matching references. The replay also covers the covariance, decision
forest, linear regression and logistic regression headers and records the
exact names for each. GCC 14.2.0 and Clang 19.1.7 produced identical
provider-matched reference sets at both `-O0` and `-O2` in all 18 header and
release combinations. To replay a configuration, pass `--compiler clang++`
or `--compiler g++` and `--optimization 0` or `--optimization 2` to the
command below and compare the output with the pinned report.

```sh
python -m abiforge.run.onedal_header_imports "$OLD" "$MINOR" "$MAJOR" --out "$OUT/header-imports.json"
```

With `--all-headers`, the replay enumerates every installed
`oneapi/dal/algo/*.hpp`. GCC at `-O0` compiled all 25 headers from each 2025
SDK and all 26 from 2026.2. Ten headers from each 2025 SDK and eleven from
2026.2 introduced strong references matched to the CPU frontend. PCA was the
only shared header whose set changed. The new HDBSCAN header introduced six
result-option getter references, all absent from both 2025 frontends.

```sh
python -m abiforge.run.onedal_header_imports "$OLD" "$MINOR" "$MAJOR" --all-headers --out "$OUT/all-algorithm-header-imports.json"
```

The 2026.2 HDBSCAN CPU fixture clusters eight points forming two separated
unit squares. It exercises the brute-force, k-d tree and ball tree methods with both
float and double inputs. Each run requests all six outputs with both center types enabled. It
checks the cluster count and membership of every row, the exact centroid
of each square and that each medoid is one of its four tied corners. Either
cluster label order passes. The 2026.2 CPU release returns 0-by-0 tables for
core flags, core indices and core observations in this case. The fixture
records that behavior. In particular the flags lack the documented eight
rows, so per-point core membership remains unverified.

The fixture then reuses each descriptor with only responses enabled. It checks
that the result reports the new option mask, returns eight response rows and
throws `oneapi::dal::domain_error` when the client calls any of the five
disabled getters. This exercises typed exception handling across the CPU
shared-library boundary for all six method and precision combinations.

A separate 2026.2 CPU client exercises ownership of all six HDBSCAN result
tables. It gives each setter a temporary table with a distinct value, copies
the result, destroys the source, and reads each value through the corresponding
getter. It also checks that the copied result preserves its cluster count and
option mask. The ownership client imports 23 HDBSCAN names from the CPU
frontend. Nine are additional to the clustering client's 29, bringing their
combined coverage to 38 of the 93 exported names containing `hdbscan` in
Linux 2026.2 `libonedal.so.4` (40.9%, up from 31.2%). This counts distinct
dynamic symbol names, including C++ ABI aliases. The downgrade tests compare
the ownership client's exact missing-name set against both 2025 frontends.

The descriptor client sets and reads back all 13 public HDBSCAN descriptor
properties, including the two constructor parameters. It checks distinct values
across the shared-library boundary for integer, double, Boolean, enum and
result-option types. Eight invalid values exercise the typed exception boundary.
For each rejection, the client compares the exception text with the corresponding
exported SDK diagnostic and verifies that the descriptor retains its prior settings.
Of its 41 HDBSCAN imports, 29 are unique to this client, including the eight
diagnostic helpers. The three CPU clients import 67/93 names in total (72.0%).

A fourth client replaces the table held by an HDBSCAN compute input. It reads
each value after the temporary table is destroyed and checks independent
deleter counts. The library releases the original buffer once at replacement
and the replacement buffer once at input destruction. This adds the
`set_data_impl` import, bringing the four-client total to 68/93 (73.1%).

Two additional builds use explicit instantiation declarations so the linker
resolves the public template setters in the shipped CPU library. They repeat
the input and result ownership checks and verify imports of one input setter
and eight result setters. Across the six builds, direct imports now cover
77/93 HDBSCAN names (82.8%) on Linux 2026.2.

A position-independent RTTI client imports the type information and vtables
for the compute input and descriptor base directly from the CPU library.
Through a common base reference it checks dynamic type identity, successful
downcasts and rejection of the other type. Its four RTTI/vtable imports and
one additional constructor bring the seven-client total to 82/93 (88.2%).
Client-defined subclasses also exercise both base-object constructor variants
for compute input and result. The client downcasts the derived input through
the common base and round-trips a cluster count through the derived result.
This raises direct imports to 84/93 (90.3%). The nine remaining names are six
distributed host dispatchers, two RTTI name strings and a GPU diagnostic.

With Open MPI development tools installed, set `ABIFORGE_ONEDAL_MPI=1` and
`ABIFORGE_ONEDAL_MAJOR` and run `test_hdbscan_distributed_dispatch`. Two ranks
call all six host dispatchers (three methods in float and double) via
`preview::compute`. Each invocation throws `oneapi::dal::unimplemented` with
the distributed-algorithm diagnostic. Direct import coverage reaches 90/93
(96.8%); these six calls verify the exception path. Distributed clustering
results remain unverified.

The RTTI client also compares the addresses and bytes of both exported Itanium
type-name strings with the names from live objects. The descriptor client
calls the CPU library's GPU k-d tree diagnostic accessor and checks its text.
With these three imports, the eight passing clients reference all 93 distinct
dynamic export names containing `hdbscan` in Linux 2026.2 `libonedal.so.4`.
GPU execution and successful distributed clustering remain unverified.

The two downgrade comparisons include the clustering executable alongside the
seven CPU parameter clients. It requires 42 symbols from the CPU frontend.
Both 2025 frontends retain 13 and lack all 29 HDBSCAN-specific imports.
The regression compares the complete missing-name list from `abicheck`
with GNU nm for each candidate. The loader stops earlier because the client
requires `libonedal_parameters.so.4`, which neither 2025 package provides.

A second executable contains only the 2026.2 HDBSCAN header and an empty
`main`. Its only six CPU frontend imports are the six result-option ID getters
used to initialize variables in the header. Both 2025 frontends lack all six.
The two downgrade reports check this executable alongside the clustering
client, which imports those same getters and 23 more HDBSCAN symbols.

With GNU ld's `--as-needed`, the original parameters-first link order
leaves unresolved references in the frontend. Placing the frontend first
links successfully. The executable's `DT_NEEDED` list retains the frontend,
parameters, core and thread libraries and drops TBB as a direct dependency.
On both 2025 runtimes the loader then reports the missing frontend SONAME. With the fixture's default `--no-as-needed` link it first
reports the missing parameters SONAME. Both executables import the same six
getters, and the checker attributes all six missing names to each executable.

A separate PCA-header control checks link order on 2025.9, 2025.11 and 2026.2.
For each release, the parameters-first link fails on exactly the 20 symbols
that GNU nm identifies as frontend imports supplied by the parameters library.
Swapping just those two libraries lets the same object link and run with eager
binding. Keeping parameters first and enclosing the two libraries in
`--start-group` and `--end-group` also links and runs on all three releases.
With LLVM LLD 19.1.7, all three `--as-needed` forms link successfully.
Each executable retains only the frontend among its direct oneDAL dependencies.
On all three SDKs eager loading then fails on
`CSRNumericTable::getSerializationTag() const`. An explicit
`--no-as-needed` link runs successfully with both linkers on each SDK.
For the parameters-first LLD executable, preloading the matching core library
supplies the missing serialization getter. Eager loading next reports a TBB
`spawn` import. Adding the thread library brings in TBB and exposes a missing
decision forest `propose_block_size` import. Adding the parameters library
then lets the same executable start on each release. GNU nm confirms the
respective definitions in core, TBB and parameters. Loading TBB directly
with core also reaches the missing `propose_block_size` import. With core,
parameters and TBB present, the frontend still lacks `_daal_unlock_mutex`.
The thread library exports that symbol, and adding it lets the client start.

Across all three Linux packages the CPU and DPC frontends import the same
23 thread-library symbols. Core imports 35 in both 2025 releases and 34 in
2026.2, where it no longer references `_daal_threader_for_blocked`. All three
thread providers still export that name. In all six directed thread-library
comparisons the CLI reports COMPATIBLE for each of these three consumers.
The overall provider verdict is BREAKING across the SONAME change and
COMPATIBLE between the two 2025 releases. These checks compare ELF symbols
and do not execute DPC workloads.

For the Linux core provider, GNU nm and the abicheck consumer-requirements
evaluator agree on the complete required and missing symbol sets. The CPU
frontend requires 688 core symbols in 2025.9, 689 in 2025.11 and 595 in
2026.2. The DPC frontend requires ten more in each release. The thread
library requires zero. Against 2025.9 core the 2025.11 frontends each lose
only `Environment::getStatus()`. The reverse comparison loses none.
Against 2026.2 core, each 2025 frontend loses 499 names. Each 2026.2
frontend loses 406 names against 2025.9 core and 405 against 2025.11 core.
These counts describe explicitly selected provider files.

The DPC frontend imports every core symbol required by the CPU frontend,
plus the same ten decision-tree symbols in all three releases. Four support
adding and copying classification and regression forest models. The other six
cover memory allocation, reset and destruction, model resize, the tree
destructor and tree RTTI. One x86-64 object with ten strong address references
links against each of the three core libraries with GNU ld's `-z defs`.
The loader regression runs each linked object against all three packages with
eager binding and the candidate thread library preloaded. In the five
same-SONAME combinations, glibc binding traces identify the candidate core
as the provider for each of the ten references. A C++ reader also resolves
the tree RTTI object, verifies its Itanium type name through
`std::type_info::name()` and reads its single-inheritance descriptor.
Its direct base pointer matches the exported RTTI for `daal::Base`, whose
name and base-free descriptor type the reader also verifies. The reader fails its lookup when run alone.
A static regression checks the same descriptors in each x86-64 core binary.
Abicheck reads a 24-byte Tree descriptor and a 16-byte Base descriptor,
classifying them as single-base and base-free respectively. GNU readelf
independently resolves all five pointer relocations, including the reference
at byte offset 16 in Tree that identifies the base as `daal::Base`.
All three cores also export a 40-byte `RefCounter` descriptor. Its vtable
relocation identifies the Itanium VMI descriptor family, and its sole base
entry points to `Atomic<int>`. The size classifier reports one base for
this case. A reader compiled against each SDK constructs a concrete
`RefCounter` subclass and measures the pointer adjustment from
`RefCounter*` to `Atomic<int>*`. All five same-SONAME header/runtime
combinations produce an 8-byte adjustment, matching the public, nonvirtual
base entry in the explicitly loaded core library's RTTI. Using both
descriptors from that library, libstdc++'s exception matcher adjusts the
object pointer to the same atomic subobject. The SDK downcast recovers the
original counter pointer, and increments and decrements through the two
views observe the same reference count.
The four cross-major runs
exit 127 at startup because the required core SONAME is absent. These
address relocations exercise loader resolution without calling the tree
functions or running a GPU workload.

The public CLI also checks all three consumers together on the 2025.11 to
2025.9 core downgrade. It classifies the removed `Environment::getStatus()`
export as `func_removed_elf_only`. Explicit consumer imports place the finding
in contract and give it a BREAKING decision. CPU and DPC each lose one symbol
among 689 and 699 requirements respectively. The thread library has no core
requirements and receives NO_CHANGE. The command exits 4. In the upgrade
direction the CLI reports the same symbol as an addition. All 688 CPU and
698 DPC requirements remain satisfied, and both consumer verdicts are
COMPATIBLE. The library-wide verdict is UNCONFIRMED (exit 1) with the
headerless comparison.

The same CLI regression covers the four cross-major directions. It compares
each consumer's full missing-symbol list and required-symbol count with GNU
nm, using the baseline frontend or thread library as the consumer in each
direction. Run all six release pairs with the three pinned SDK paths.

```sh
ABIFORGE_ONEDAL_OLD="$OLD" ABIFORGE_ONEDAL_NEW="$MINOR" ABIFORGE_ONEDAL_MAJOR="$MAJOR" python -m pytest -q tests/test_abiforge_onedal_product.py::test_core_provider_cli
```
Replay this check with the three SDK environment variables above and
`tests/test_abiforge_onedal_product.py::test_as_needed_parameter_link_order`.

```sh
ABIFORGE_ONEDAL_MAJOR="$MAJOR" python -m pytest -q tests/test_abiforge_onedal_product.py::test_hdbscan_cpu_clusters
```

A separate frozen Linux client exercises the installed SDK's
`detail::system_parameters` constructor, thread-count getter, CPU-extension
getter and string-returning `dump()`. It links against the 2025.9 CPU
parameters provider. On the same host the unchanged binary returned four
threads and extension value 32 on both 2025.9 and 2025.11. Both descriptions
reported AVX-512. CPU and thread results depend on the host.

```sh
g++ -std=c++17 -I"$OLD/include" -I"$OLD/include/dal" \
  abiforge/fixtures/onedal/system_parameters.cpp -L"$OLD/lib" \
  -Wl,--no-as-needed,-rpath-link,"$OLD/lib" -l:libonedal_parameters.so.3 \
  -l:libonedal.so.3 -l:libonedal_core.so.3 -l:libonedal_thread.so.3 \
  -l:libtbb.so.12 -o "$OUT/system_parameters"
for prefix in "$OLD" "$MINOR"; do
  LD_BIND_NOW=1 LD_LIBRARY_PATH="$prefix/lib" "$OUT/system_parameters"
done
```


The parameters-provider consumer comparison checks this frozen executable and
the 2025.9 CPU frontend in one abicheck invocation. On the minor update, the
four executable imports and 20 frontend imports all remain available. For
2026.2 the same names remain available, but the executable needs
`libonedal_parameters.so.3`. The loader exits 127 with only the new package on
its library path, and abicheck reports `COMPATIBLE_WITH_RISK` for the executable.
The frontend has no direct dependency on that SONAME and its scoped verdict
is `COMPATIBLE`. Run this check with the three pinned prefixes:

```sh
ABIFORGE_ONEDAL_OLD="$OLD" ABIFORGE_ONEDAL_NEW="$MINOR" ABIFORGE_ONEDAL_MAJOR="$MAJOR" \
  python -m pytest -q tests/test_abiforge_onedal_product.py::test_parameter_provider_consumer_scope
```

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

### Closing the remaining Linux ELF coverage gaps

The 2025.9 export inventory contains 22,428 SONAME/symbol pairs and 18,619
distinct names. The combined SDK inventories match 3,517 distinct names.
The independently witnessed lower bound is 1,144 names. Track these three
populations separately as coverage grows.

1. Inventory every provider in each candidate. Pair equal SONAMEs first and
   match a major-version successor only when the remaining family has one
   provider on each side. Count additions from candidate-only DSOs. These
   counts measure symbol presence across releases.
2. Resolve the six unparsed SDK roots and classify unmatched exports by
   provider and declaration or compiler-generated role. Keep unresolved
   entries visible with their reason and package version.
3. Group public obligations by calling convention, object layout, dispatch,
   ownership and device boundary. For each new group, compile a frozen
   consumer, record its provider binding and run it on an unchanged release,
   a breaking mutation and a compatible control.
4. Compare the same artifacts with Clang and CastXML, checking attribution,
   verdict and exit code independently. Publish per-provider totals for
   witnessed, unresolved and environment-dependent obligations on each replay.

Run the independent export extraction check for all three pinned packages:

```sh
python -m abiforge.run.onedal_elf_extraction "$OLD" "$MINOR" "$MAJOR" --verify-inventory research/onedal-coverage/product/elf-extraction.json --out "$OUT/elf-extraction.json"
```

The ELF extraction replay also compares each DSO's dependency-specific symbol
version requirements with GNU readelf. Across the six DSOs per release the
counts are 62 (2025.9), 62 (2025.11) and 57 (2026.2), counting each
consumer/provider/version tuple once. The 2026.2 CPU library adds requirements
for GLIBC_2.3 from ld-linux-x86-64.so.2 and GLIBC_2.3.4 from libc.so.6
relative to 2025.11. These are version-node requirements recorded in the ELF
files; the counts do not establish an overall minimum supported OS version.
The replay also checks each of the 7,808 named dynamic imports against
readelf, including binding and multiplicity. For the 1,278 versioned entries
it joins the dynamic symbol's version index to the provider and version name
in the version needs table. All tuples agree with abicheck.
The same dynamic-symbol pass checks all 60,612 export entries by versioned
name, ELF type, binding, visibility, byte size and multiplicity. The
per-library type counts are recorded in `elf-extraction.json`.
GNU objdump also agrees with the reader on all 18 SONAMEs, the order of all
164 DT_NEEDED entries, and the recorded RPATH and RUNPATH tags. Sixteen
libraries specify only `$ORIGIN/.`; the other two have three RPATH entries.
None of the 18 defines RUNPATH. These checks describe loader inputs on disk.


The checked-in receipt compares every name in GNU nm's defined dynamic
symbol list with the ELF reader, keyed by SONAME and symbol version.
There were zero missing or unexpected names in all six providers per release:
22,428 pairs in 2025.9, 22,442 in 2025.11 and 15,742 in 2026.2.

For link-time coverage, run the following on Linux x86-64 with GCC and GNU ld:

```sh
python -m abiforge.run.onedal_export_imports "$OLD" "$MINOR" "$MAJOR" --out "$OUT/export-imports.json"
```

One consumer DSO per provider contains address references to every defined
dynamic symbol. Link with undefined references forbidden in the consumer
and inspect its dynamic imports. Of 60,612 input pairs across three releases,
60,603 produced matching imports. GNU ld defined the three boundary symbols
`__bss_start`, `_edata` and `_end` in each thread-library consumer itself.
These probes exercise link-time address references. Runtime calls, object
layout and ownership require typed consumers.

Finally, verify live addresses with the Intel 2025.3.2 SYCL runtime for
the minor releases and 2026.2.0 for the major (including its pinned MKL
dependencies). This replay opens the target with eager binding in a fresh
process for each DSO. For every binutils export it checks that dlsym succeeds
and dladdr identifies the requested provider by filesystem identity.

```sh
python -m abiforge.run.onedal_export_lookup \
  --release "$OLD" "$RUNTIME_2025/lib" --release "$MINOR" "$RUNTIME_2025/lib" \
  --release "$MAJOR" "$RUNTIME_2026/lib" --out "$OUT/export-lookup.json"
```

All 60,612 lookups resolved to the requested provider, including the linker
boundary symbols under handle-specific lookup. A successful lookup establishes
address ownership. It does not invoke the exported function.

### Standalone adjustment thunk withdrawal

Of the 887 adjustment thunks exported by the 2025.9 DSOs, 282 have no
exported plain target in the same DSO (32 CPU, 218 core and 32 DPC).
For example, the CPU DSO exports `_ZThn8_N6oneapi3dal2v111range_errorD1Ev`.
A frozen PIE referencing its address links and starts against the old package.
After renaming only this symbol in the CPU DSO with patchelf, the same PIE
exits 127 and the loader identifies the exact missing name. ABIcheck previously
returned UNCONFIRMED (1). It now reports `func_removed_elf_only` for that
linker name and returns BREAKING (4). A change to an offset within a surviving
thunk family continues to use the existing vtable offset finding.

To replay on Linux x86-64, set OLD to the 2025.9 prefix and OUT to a fresh
writable directory. The client holds the address without invoking the destructor.

```sh
lib="$OLD/lib"
sym=_ZThn8_N6oneapi3dal2v111range_errorD1Ev
printf 'extern "C" char target[] asm("%s"); void *volatile ref=target; int main(){return ref==nullptr;}\n' "$sym" > "$OUT/client.cpp"
g++ -fPIC -pie "$OUT/client.cpp" -Wl,--no-as-needed "$lib"/libonedal{,_parameters,_core,_thread}.so.3 -Wl,--allow-shlib-undefined -o "$OUT/client"
LD_BIND_NOW=1 LD_LIBRARY_PATH="$lib" "$OUT/client"
printf '%s abiforge_withdrawn\n' "$sym" > "$OUT/renames"
patchelf --rename-dynamic-symbols "$OUT/renames" --output "$OUT/libonedal.so.3" "$lib/libonedal.so.3"
LD_BIND_NOW=1 LD_LIBRARY_PATH="$OUT:$lib" "$OUT/client"  # expected exit 127
abicheck compare "$lib/libonedal.so.3" "$OUT/libonedal.so.3" -o "json=$OUT/report.json"  # expected exit 4
```

### Exception handling across shared libraries

Build a thrower and a separate catcher once against 2025.9. The thrower
constructs eleven concrete SDK exception types. The catcher handles each
through both the oneDAL and standard exception bases, checks the cross-cast
and compares the messages. Both frozen artifacts run on 2025.9 and 2025.11.
For each class, the thrower imports two objects from the real CPU DSO,
the typeinfo and the vtable. The runner withdraws each object individually
in a fresh copy of that DSO and checks the missing spelling in both the
loader diagnostic and the CLI report. Run with GCC and a patchelf version
supporting dynamic symbol renaming.

```sh
python -m abiforge.run.onedal_exception_abi "$OLD" "$MINOR" --out "$OUT/exception-abi.json"
```

### Windows x86-64 export identities

The Windows receipt covers four DLLs per release (CPU, core, DPC and thread).
The binding, import-library and thread classification audits require all 12
release/DLL entries and reject any unresolved export mismatch before scoring.
The builds are conda-forge win-64 dal 2025.9.0 hbcdf7a0_51, 2025.11.0
hbcdf7a0_17, and 2026.2.0 h5461014_300 with its matching dal-gpu build.
The receipt pins each DLL's SHA-256. Place the DLLs under directories named
for their release and run:

```sh
python -m abiforge.run.onedal_pe_exports "$ROOT"/*/onedal*.dll --out "$OUT/pe-exports.json"
```

The audit uses GNU objdump to read the export address and name pointer tables independently. GNU objdump, LLVM 19 and the PE reader agree on all 92,503 decorated-name/ordinal pairs.
The forwarding-destination audit found zero forwarded exports across those 12 DLLs. A regression fixture with one named forwarder exercises the positive case.
The reader and objdump also agree on all 6,837 ordinary named imports across 154 DLL/provider pairs, including provider identity and symbol multiplicity.
In the 2025.9 core DLL alone, extraction recovers 20,260 exports (previously
11,741 entries, including misclassified ordinal-only entries). Names in the
CPU and DPC DLLs reach 2,905 bytes. The public CLI on the two minor DPC DLLs
reports four removed and four added exports and exits 4. In the core DLL, one added name shifts the ordinals of 3,780 retained names.
The default checker treats named exports as name-bound. This measurement
compares on-disk export identities. It does not execute Windows or GPU code.

### Windows import libraries

The three matching win-64 `dal-devel` packages (builds `h57928b3_51`,
`h57928b3_17`, and `h57928b3_300`) supply CPU, core and DPC import libraries.
Their nine versioned archives and nine unversioned aliases are pairwise
byte-identical. GNU nm finds 92,272 import-pointer definitions in the versioned
archives. Every decorated name and its multiplicity matches the corresponding
runtime DLL export inventory. GNU ar reports the expected versioned DLL name
for every archive member. GNU ld (i386pep) links one PE consumer per archive,
forcing a reference to every import pointer. GNU objdump independently reads
all 92,272 imports from the nine linked images and confirms their names,
multiplicities and provider filenames. These probes exercise link-time binding;
they contain no application entry point. The packages supply no thread import archive.

Extract the packages' `Library/lib` directories under the corresponding release
directories alongside the DLLs. From the product directory run:

```sh
python -m abiforge.run.onedal_pe_import_libraries "$ROOT" pe-exports.json --out pe-import-libraries.json
```

### Windows export classification

Across the nine archives GNU nm shows code thunks for 85,138 import names
and import pointers without code thunks for 7,134. The COFF short-import
headers independently give the same per-archive counts for CODE and DATA.
In 2025.11 core, 2,085 of 20,261 named exports are DATA. Intersecting these
data names with the 2025.11 to 2026.2 header-free core CLI report finds 856
`var_removed` findings, including the AdaBoost prediction kernel vftable
`??_7?$AdaBoostPredictKernel@$00M$01@internal@prediction@adaboost@algorithms@daal@@6B@`.
Generation 98 reported these same 856 data removals as `func_removed`.
Generation 99 recognizes direct exports in non-executable PE data sections.
A generation-98 reader can reopen the generation-99 dump of 2025.11 core:
both readers recover 18,176 function declarations and 2,085 variables. The
older reader recognizes all 20,261 PE entries as direct exports, while the
current reader also recovers the per-export data marker on 2,085 entries.
Dumping the two core DLLs with `abicheck dump` and comparing their saved JSON
snapshots reproduces the live DLL comparison's 11,055 findings by kind, symbol,
severity and multiplicity, including all 856 variable removals. Both comparisons
return BREAKING (exit 4). Comparing generation-98 and generation-99 dumps
of the identical 2025.11 core DLL in either direction retains 6,255 findings
for the 2,085 reclassified exports and annotates them as extractor drift
(COMPATIBLE_WITH_RISK, exit 0). A generation-98 baseline against the distinct
2026.2 core DLL remains BREAKING (exit 4). Re-dump the baseline for a
same-generation comparison when the DLL bytes differ.

The replay checks each decorated name against the header-free dump. All 85,138
code imports appear as functions and all 7,134 data imports appear as variables
across the nine pinned archives. For the thread DLL, the separate audit uses the same-release
Linux thread DSO and LLVM's MSVC demangler. Each release has 55 undecorated
names with matching defined ELF FUNC symbols. LLVM decodes the other 22
Windows names into 21 function signatures and the daal::task virtual function
table. The PE dump agrees on all 77 names per release. The ELF matches are
cross-platform corroboration for the undecorated names.

Extract `lib/libonedal_thread.so.*` from the Linux packages listed in
`../conda/env-*.txt` under each corresponding release directory. Run:

```sh
python -m abiforge.run.onedal_thread_export_kinds "$ROOT" pe-exports.json --out thread-export-kinds.json
```

Use `--undname` to select the LLVM demangler if its executable has a version suffix.

### Windows intra-package imports

Replay the static binding check from the product directory with the same DLL tree used for the export audit.

```sh
python -m abiforge.run.onedal_pe_bindings "$ROOT" pe-exports.json --out pe-bindings.json
```

The CPU and DPC DLLs import from the core DLL in each release. All 4,133 same-release import entries have matching exports. The 2025.9 consumers also find all 1,437 required names in 2025.11. In the reverse direction each 2025.11 consumer requires the same additional symbol, `daal::services::interface1::Environment::getStatus()`, absent from 2025.9 core. Across the major boundary, all eight consumer/provider comparisons lack the requested DLL filename. These observations cover the ordinary named imports between the pinned oneDAL DLLs.

The public CLI corroborates the direction of the core export change. Run
`abicheck compare "$OLD/onedal_core.3.dll" "$NEW/onedal_core.3.dll" -o json=core.json`
with the release directories in each order. From 2025.11 to 2025.9 the CLI
reports `func_removed` for `?getStatus@Environment@interface1@services@daal@@SAHXZ`,
returns `BREAKING` and exits 4. From 2025.9 to 2025.11 it reports `func_added`
for the same symbol. The overall upgrade verdict is `UNCONFIRMED` (exit 1)
because both inputs lack type evidence.

Adding three `--used-by` arguments to the downgrade comparison checks which
2025.11 DLLs require the removed core export. The public CLI produced these
per-consumer results against the package bindings above.

| 2025.11 consumer | Required core symbols | Missing symbols | Consumer verdict |
|---|---:|---|---|
| `onedal.3.dll` | 715 | `Environment::getStatus()` | BREAKING |
| `onedal_dpc.3.dll` | 724 | `Environment::getStatus()` | BREAKING |
| `onedal_thread.3.dll` | 0 | None | NO_CHANGE |

The thread DLL serves as a control for consumer attribution. The library-wide
verdict remains BREAKING (exit 4). In the upgrade direction the 2025.9 CPU
and DPC consumers receive COMPATIBLE on 714 and 723 required core imports,
respectively. The thread consumer receives NO_CHANGE. Replay all six directed release pairs from the repository
root using the release directories prepared for the export audit.

```sh
ONEDAL_WINDOWS_RELEASES="$ROOT" python -m pytest tests/test_abiforge_onedal_exports.py -k pinned_pe_consumer_comparison
```

For the 2025.11 to 2026.2 comparison, select `onedal_core.3.dll` as the old
provider and `onedal_core.4.dll` as the candidate, with the same three 2025.11
consumers. The CLI reports BREAKING (exit 4). CPU and DPC each lose the same
174 decorated names among 715 and 724 required core imports, respectively.
Subtracting the candidate exports from each consumer’s core imports with GNU
objdump reproduces both complete missing-name lists. The thread control has
zero core imports and receives NO_CHANGE. This comparison explicitly selects
the candidate file. The package binding matrix separately records that 2026.2
lacks the `.3.dll` filename requested by both consumers.

The pinned regression uses GNU objdump to compute the exact missing names for
each CPU, DPC and thread consumer in every directed pair. With the 2025.9 CPU
and DPC DLLs and the explicitly selected 2026.2 core, each consumer loses 174
required names. With the 2026.2 consumers, each loses 88 names against 2025.11
core and 89 against 2025.9 core. The thread control imports zero core names
in all three releases.

### Frozen upstream CPU examples

Run against the upstream 2025.9.0 tag and the two installed minor SDKs.
The Jaccard application also requires the oneTBB development headers and
library. Use `--tbb-include` and `--tbb-library` for a non-default installation.

```sh
python research/onedal-coverage/product/cpu_examples.py "$SOURCE" "$OLD" "$MINOR" --out "$OUT/cpu-examples.csv"
```

All 60 oneAPI and 170 DAAL examples built against 2025.9 exited zero on
both releases with eager binding. Initial stdout matched byte-for-byte on
206. Of the other 24, six varied in repeat runs of at least one release.
For the remaining 18, rebuilding against 2025.11 reproduced the frozen
client's 2025.11 output in every case. The CSV records the observations on
this host; nondeterministic algorithms can produce different digests on replay.
