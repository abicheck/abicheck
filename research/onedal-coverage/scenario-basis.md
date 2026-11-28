# Which oneDAL observations have a reusable ABI scenario?

A scenario specifies an old client, a change to its provider, and an observation that distinguishes the broken provider from a compatible control. A release, algorithm, overload, precision, compiler or number of exports is a test input. Split a scenario when the client consumes a different contract or a different observation is needed to detect failure.

## Mechanisms and evidence

| Contract | Smallest distinguishing experiment | Reusable abiforge evidence | Limit of the evidence |
| --- | --- | --- | --- |
| Symbol binding | Hide one previously imported name. Run two frozen clients that reference different exports. Include template code instantiated in the client versus in the library. | `run/template_wrapper_consumers.py`, `operators/kinds.py` (`explicit_instantiation_dropped`), `operators/variables.py` | The symbol identity must include the ABI variant. A successful lookup alone does not exercise a constructor, destructor, or thunk. |
| Provider resolution | Keep the client frozen. Move the definition between libraries, change the required version, or make the provider unreachable. Observe which definition the loader actually binds. | `run/provider_campaign.py`, `run/duplicate_provider.py`, `operators/elf.py` | Same, minor and major releases are controls or mutations of the binding conditions. An unchanged symbol name can resolve to the wrong provider. |
| PE export identity | Compare an export by name and ordinal, including a nameless ordinal, a forwarder, and data in place of code. | `run/pe_export_kinds.py` and generic checker tests in `tests/test_pe_metadata_unit.py` and `tests/test_dump_pe_macho_extract.py` | Reader evidence only. A frozen Windows consumer exercising imports by ordinal and following a changed forwarder still needs a runtime witness. |
| Record representation | Exchange a record whose fields move, whose alignment changes or whose enum width changes. Inspect field values or offsets. | `run/l2_representatives.py` (layout), `operators/layout.py`, `operators/enums.py` | A successful load cannot establish layout compatibility. |
| Calling convention | Pass and return a small aggregate. Change register class, direct versus indirect passing, or hidden return storage. Exercise the same transition through a callback. | `run/l2_representatives.py` (calls and callback families), `operators/platform.py` | Input and output directions are distinct. One Linux calling convention does not certify the Windows ABI. |
| Virtual dispatch | Reorder slots or secondary bases. Have a frozen client and a derived object identify the override actually invoked. | `run/l2_representatives.py` (virtual), `operators/cxx.py`, `operators/vtable.py` | Checking a slot count alone misses a same-size swap or wrong this adjustment. |
| Runtime type identity | Change a public base to nonpublic while keeping export names fixed. Check a cross-cast and a catch through that base. | `run/base_access_consumers.py`, `run/itanium_base_edges.py` | Named ELF, local and packed relocation forms are evidence variants. Unknown type identities and other C++ runtimes need separate validation. |
| Resource ownership | Copy, retain and destroy handles across a library boundary. Observe the surviving value and count releases, including assignment through an alias. | `run/lifetime_campaign.py` | A runtime observation. Equal symbol names and layouts cannot prove correct reference counting. Device events and queue-dependent destruction require additional evidence. |
| Returned state | Preserve independent success and failure values through return, copy, assignment and reset. Mutate one operation at a time. | `run/value_state.py` | Behavioral contract. The portable scenario checks loss of state, not the numerical result of a clustering algorithm. |
| Type-erased buffer | Pass an element count and type tag through a shared library. A provider interpreting the count as bytes passes the one-byte control and fails for four-byte items. | `run/erased_buffer.py` | Covers typed and byte forms of a buffer transfer. The peer chosen and the reduction result are runtime semantics. A virtual communicator call additionally depends on slot identity. |
| Backend boundary | Withdraw a runtime/plugin entry point while keeping the consumer fixed. | `run/sycl_plugins.py` | Covers host binding. The portable `run/dependency_forwarding.py` distinguishes forwarding, dropping and reversing a prerequisite by its observed result. Real device scheduling and collective execution need their own runtime evidence. |

## Mapping the source corpus

The companion inventory maps every tracked `abiforge/run/onedal_*.py` and every oneDAL C++ fixture to the contracts above. Inventory and attribution tools identify candidates. They cannot independently establish a runtime outcome. The pilot range edits reduce to representation and calling convention. The pilot template-provider and symbol-version edits reduce to symbol binding and provider resolution.

The legacy, extended and 2026.2 obligation ledgers contain 16,528, 2,286 and 14,223 rows respectively. The audit also checks the separate SYCL inventories (59 older and 61 newer signatures) and preserves each recorded package outcome. Rows can overlap across releases and ledgers. A declaration kind locates candidate contracts, not the edits that affect every concrete instantiation. Run `python research/onedal-coverage/check_scenario_basis.py` to verify that every source, ledger kind and recorded edit family has an explicit disposition. A new category fails this check until reviewed. Add `--rows coverage.csv` to export each declaration key, its candidate contract, the observed mutation, its scenario family and the package witness. Rows without an explicit edit link stay candidate-only.

The ten explicit mutation labels reduce to five contracts exercised by three
generic generators. `l2_representatives` supplies field swap, call triviality
and virtual slot reorder. `lifetime_campaign` supplies a lost reference and an
extra retained reference. `value_state` supplies incorrect success/failure
return, copy, assignment and reset. The crosswalk test resolves every label to
a concrete generated variant. This result applies to the mutations recorded
in the ledger. The remaining rows and the separate package receipts do not
inherit a pass from another declaration of the same kind.

The newer package experiments reduce in the same way. CPU/GPU selectors, eager loading, transitive header imports and helper exports exercise binding and provider resolution. Tree visitors exercise representation, dispatch and type identity. Returning tables exercises calling convention and ownership. Windows import libraries and export classifications exercise PE identity. Clustering labels, method choices and float/double repeat these mechanisms unless a changed binary contract can be demonstrated.

### Distributed operations

The two-rank package replay exercises four collective operations through typed
and byte-buffer calls. Their count/type boundary uses the erased-buffer
scenario. The five interface calls, including the queue getter, exercise virtual
dispatch. For the communicator factory and concrete queue getter, the evidence covers
returning a value and withdrawing the declaration. No compiler-lowering
mutation was recorded for these two returns. All 59 older SYCL signatures
have a separate exact declaration-withdrawal receipt with isolated, bulk and
unchanged controls. The generic operators are `header_inline_removed` and
`src_public_function_removed`. The receipt aggregates both finding kinds,
so it cannot establish which operator applies to each signature or whether
an already-built binary loses an export.

Peer routing is a separate behavioral observation. In a two-rank control, a
frozen client passed the destination and an integer to a shared library.
With inputs 10 and 11, the ranks received 11 and 10. A provider that used
the caller's rank as the destination returned 10 and 11. Both providers
exported the same `int exchange(int, int)` function and used the same calling
convention. An implementation-only routing error therefore contributes no
additional ABI edit to the basis. The package replay remains evidence for
actual collective and device behavior.

## Coverage decision

Every currently inventoried category has a disposition. End-to-end coverage of oneDAL remains **partial**. Three things require more evidence: execution on the Windows and device runtimes listed above, a concrete declaration-to-mutation witness for candidates with no independent edit, and SDK roots the existing inventory could not parse (see `product/unparsed.csv`). Image or export coverage cannot resolve those gaps. This is a reduced working basis; a proof of global minimality or completeness would require a complete inventory of obligations and a distinguishing experiment for each.

The crosswalk pairs the baseline and forwarding controls with each mutation in
`table-ownership.csv` and `return-witnesses.csv` by release and entry point.
Early destruction and delayed destruction reuse the same two lifetime mutations
across both receipts. The status checks reuse the returned-state scenario.
The statistics client's `drop_sum` check observes a missing or malformed
result after a successful call, an implementation-level payload obligation.
Repeated executions across accessors and releases do not add distinct contracts.
