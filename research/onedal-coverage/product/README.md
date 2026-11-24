# CPU contract inventory and mutation receipts

Target: conda-forge dal 2025.9.0, Linux x86-64 SysV, C++17, GCC-built consumers, Clang 19 AST, four CPU DSOs. See ../README.md for the 23 release controls. This campaign uses stripped ELF and public headers. Old-client execution is independent of the checker.

## Inventory and current coverage

169 source entry headers include daal.h and installed modern headers outside detail, backend, gpu and spmd directories. Six parameter headers independently fail because an included backend header is absent. roots.txt and unparsed.csv preserve those cases. Successful translation units yield 16,528 candidate rows. Scope follows installed paths and C++ access, so undocumented or inline declarations can occur. Legacy declarations outside the daal.h include closure, protected interfaces, variable exports, alternate macro configurations and member-template specializations require additional inventory. Clang supplies 1,738 constructor ABI variants and 1,368 destructor ABI variants in addition to source declarations. Dependent instantiations remain incomplete. Some headers parse through a shared translation unit. A successful parse does not establish standalone includability.

ledger.csv.gz joins Clang declaration USRs and obligation kinds to header locations, mangled symbols, DSO providers, direct imports from the 23 clients, and registered operator-family witnesses. Empty evidence cells mean unknown. The 3,343 distinct matched exports include 1,118 record-returning names. 83/3,343 matched names occur in client imports. Of 16,528 candidate rows, 82 have an explicit mutation witness (10 fields, 2 virtual methods, 3 Status operations and 67 by-value returns). This measures linked evidence per inventory row. It is a lower bound and gives no estimate of detection probability. Multiple operations may apply to one declaration. DSO wrappers around range in contracts.py are separate mechanism probes. The denominator for real-product coverage excludes these synthetic functions.

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

`return-scans.csv` compares each stripped adapter against the corresponding forwarding adapter once, independently of the two package runs. The client exit in each scan row is the checked expectation reproduced on both packages. Dependency and runtime-floor changes seen in an adapter do not identify the ownership or error-propagation contract. These are injected edits. Coverage is credited only to the 67 specific by-value return rows. Each getter was exercised with one named result ID. The inventory contains 121 further provider-matched return symbols spelled `data_management::NumericTablePtr`. Among their demangled names are 80 `Input::get` and five `PartialResult::get` methods. These counts identify candidates for additional clients. The other return sites with the same type still need their own witnesses.

All 65 provider-matched `Result::get` overloads returning `data_management::NumericTablePtr` in this inventory have an old-client witness. The 14 imported by the original example corpus and 51 invoked by the expanded client use the same ownership checks. Three overloads read element zero of an explicitly populated data collection. The linear model prediction result requires sized storage, the iterative solver optional result requires an optional-argument container, and the stump classification result requires storage for its variable-importance slot. The fixture constructs these through the old public headers before exercising the corresponding getter. The measurements concern one slot per overload and two package versions on Linux x86-64.

Run `bash ownership_campaign.sh OLD_PREFIX NEW_PREFIX > ownership-campaign.csv` here to reproduce ownership-campaign.csv.
The C1 copy mutant yields exit 11 and the assignment mutant yields 12. Exit 13 checks the survivor after scope exit. Each passing run also checks that the NumericTablePtr deleter is called zero times before the final owner resets and exactly once afterwards, with a successful row read in between. The deleter holds the backing table alive. This counts the ownership callback and adds no exported-symbol mutation credit for the inline smart pointer.
