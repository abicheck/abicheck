# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bug classes about *how often* correct work is performed.

A sibling of `manifest_evidence.py`/`manifest_guards.py`/
`manifest_report.py`/`manifest_tool_surface.py` (see `manifest.py` for what
this registry is and is not). The classes here produce the right answer:
what makes them defects is the cost of producing it, and -- the reason they
belong in a *regression* registry at all -- the fact that nothing anywhere
measured that cost, so the regression was invisible to a passing suite and
was found only by profiling a real corpus.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["PERFORMANCE_BUG_CLASSES"]


PERFORMANCE_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="perf.pure_content_digest_recomputed_per_consumer",
        invariant=(
            "A pure, expensive, content-derived value reached by several "
            "independent consumers within one run must be computed at "
            "most once per distinct input, not once per consumer. The "
            "guard is a count of the expensive work actually performed "
            "during a real run, bounded by the number of distinct inputs "
            "— never an assertion that one named consumer uses the cache, "
            "which forecloses exactly that call site and says nothing "
            "about the next one added."
        ),
        # v19 oneAPI scan: every graph-shaped compare regressed ~1.7-1.8x
        # against v18 (oneTBB pair 431s -> 740s, self-compare 406s -> 726s).
        # cProfile put 404s of a 620s run inside
        # `serialization.snapshot_content_digest` at n=6 over two snapshots;
        # v18 was n=2. A fourth `same_persisted_content` caller had been
        # added without anyone noticing the third, and nothing anywhere
        # measured the count.
        fixed_by=(1245,),
        seed_tests=(
            "tests/test_snapshot_digest_recomputation.py",
            # The same class in the clang header backend: three pure,
            # AST-derived template-parameter indexes rebuilt once per
            # constructed parser rather than once per distinct AST, so a
            # six-member shared-header fan-out ran each builder 14 times for
            # 2 distinct answers. Same guard shape, per this class's own
            # invariant -- a count of the builds a real run performs, bounded
            # by the number of distinct inputs, not an assertion that one
            # named call site consults the cache.
            "tests/test_clang_template_index_reuse.py",
            "tests/test_clang_template_index_reuse_e2e.py",
        ),
        public_surfaces=("cli", "python-api"),
        axes={
            "front_end": ("cli", "typed_api"),
            "pairing": ("content-identical", "genuinely-different"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The bound is enforced for the pairwise `compare` "
                    "paths that open a digest scope. Other snapshot-digest "
                    "consumers outside one — `workflows/"
                    "bundle_stored_pair_compare.py`'s stored-pair route — "
                    "are correct but unmemoized, and no gate fails if a "
                    "future front end forgets to open a scope."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="perf.shared_resource_gate_keyed_on_a_per_caller_value",
        invariant=(
            "A process-wide bound on a scarce resource must be one object "
            "whose accounting cannot be replaced while a holder is still "
            "using it, and must be sized from the *host* budget alone — "
            "never from a value that varies per caller (a unit count, a "
            "per-request worker request). The guard is a count of the "
            "resource actually held concurrently while two callers that "
            "resolve to *different* sizes overlap; a single-caller test, or "
            "one where both callers happen to resolve the same size, passes "
            "against a gate that is no gate at all. Two corollaries, each "
            "its own defect found on the same gate: every path that takes "
            "the resource goes through it -- a *serial* path is one holder, "
            "not none, and exempting it admits one over the cap -- and "
            "waiting for it is bounded by whatever deadlines bound the work "
            "itself, or one caller's long hold makes another overrun a "
            "budget it was supposed to be held to. And the bound must be "
            "re-read, not cached in the object: a limit baked in at "
            "construction cannot notice the budget it came from shrinking, "
            "so in a long-lived process every caller narrows itself "
            "correctly while the stale gate keeps admitting the old number."
        ),
        # Found in review on the PR that introduced the bounded-parallel
        # `clang -M` include-map pass. The gate was keyed on each pool's own
        # resolved worker count and rebuilt whenever that differed — and the
        # pool size is `min(host_limit, unit_count)`, so the ordinary case of
        # two sides with differing header counts (`service.compare` resolves
        # old and new concurrently) rebuilt it mid-flight: the first pool kept
        # an orphaned semaphore and both admitted their full quota at once, a
        # 2-slot plus a 4-slot gate against an intended cap of 4. Every test
        # written for the feature passed, because each drove one extractor, or
        # two whose unit counts matched.
        fixed_by=(1275,),
        seed_tests=("tests/test_include_graph_parallel.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "caller_count": ("one-pool", "two-concurrent-pools"),
            "resolved_size": ("equal-sizes", "differing-sizes"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The invariant is enforced for the `clang -M` "
                    "include-map gate only. The other concurrent pools in "
                    "this codebase (L4 source replay, the L5 call/type "
                    "graph passes, the per-TU manifest dump) each size "
                    "themselves from the same host probe but hold no shared "
                    "gate at all, so two of *those* running concurrently "
                    "still oversubscribe — the pre-existing hazard "
                    "`service_compare_pipeline.resolve_sides_sequentially` "
                    "documents rather than bounds."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md",
            ),
        ),
    ),
    BugClass(
        id="perf.cache_fast_path_bypasses_shared_coordination",
        invariant=(
            "When a cheap fast path (a warm cache hit) and an expensive slow "
            "path (running the real tool) produce the SAME logical value, the "
            "coordination that de-duplicates that value per request must sit "
            "*above both*, not between them. A fast path placed ahead of the "
            "coordinator still satisfies every equality assertion -- the two "
            "results compare equal -- while silently producing a second "
            "object: each consumer repeats the decode, identity-keyed work "
            "downstream (`id(root)`-scoped normalization) repeats with it, "
            "and every produced copy stays resident for the request. So the "
            "guard counts *productions and decodes* per (backend, key), never "
            "equality of results, and covers the shapes in which a second "
            "consumer appears at all: a later member, a later worker group, "
            "and a follow-up pass over the same artifact. The coordinated "
            "result must also carry everything the slow path resolved (the "
            "post-retry language mode, the selected compiler, the frontend "
            "context), or a waiter silently re-derives a stale answer; and "
            "sharing one parsed object retroactively constrains what the "
            "producer may do to it -- a lossy transformation that was private "
            "to the producer becomes visible to every later consumer."
        ),
        # PR #1303 added the request-local L2 AST singleflight, but both
        # backends read and decoded their disk cache *before* entering it: on
        # a six-DSO directory compare with a warm cache every member decoded
        # its own root (25 CastXML / 13 clang raw decodes, 12 neutral
        # `SemanticIR` normalizations for one effective context), and the
        # header-graph pass re-read the cache the primary pass had just
        # decoded. Nothing was wrong in the output, which is why no existing
        # test noticed: the defect is only visible as counts.
        fixed_by=(1305,),
        seed_tests=("tests/test_l2_ast_acquisition_singleflight.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "acquisition_source": ("cold-compiler-run", "warm-disk-hit"),
            "consumer_shape": (
                "sequential-member",
                "later-worker-group",
                "header-graph-pass",
            ),
            "backend": ("clang", "castxml"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Per-binary legacy parsing and symbol binding still run "
                    "once per member over the shared root -- deliberately, "
                    "since each member's export evidence drives it -- so the "
                    "counts this class guards are for the raw AST and its "
                    "member-independent normalization only, not for the "
                    "per-member passes above them."
                ),
                reference="docs/contribute/plans/vision-api-abi-evolution.md",
            ),
            KnownGap(
                description=(
                    "The directory-level guard is ELF-only. On the macOS and "
                    "Windows runners the fixture's header parse degrades "
                    "before either backend reaches the acquisition, so the "
                    "counts have nothing to observe and the test is skipped "
                    "there; the deterministic warm-path tests still run on "
                    "every platform, and the MSVC/PDB header path has no "
                    "coverage of this invariant at all."
                ),
                reference="tests/test_l2_ast_acquisition_singleflight.py",
            ),
        ),
    ),
    BugClass(
        id="perf.shared_projection_read_as_an_owned_copy",
        invariant=(
            "When a function drops an unconditional deep copy in favour of "
            "sharing -- because the work it does provably only *rebinds* "
            "fields rather than read-modify-writing anything beneath them -- "
            "what it owns must track, per code path, exactly what that path "
            "writes to. Two failures sit on opposite sides of that line and "
            "only opposite-direction assertions catch both. Share where the "
            "path mutates in place, and the 'copy' silently rewrites its "
            "caller's retained object; deep-copy where nothing mutates, and "
            "the cost the change existed to remove comes back invisibly, "
            "since every equality assertion still passes. So the guard "
            "asserts container and element *identity* per path -- shared "
            "where sharing is intended, distinct where ownership is required "
            "-- and never equality of contents, which cannot distinguish the "
            "two. The guarantee actually owed to callers ('this never "
            "mutates its argument') is separate from, and weaker than, the "
            "one a caller might assume ('the result is an independent "
            "mutable copy'); whichever is claimed must be stated and pinned, "
            "because a later consumer that starts writing through a shared "
            "result is the failure mode, and it corrupts a caller that is "
            "nowhere near the change. The mechanism used to make the shallow "
            "copy is part of the contract too: for a wide dataclass with a "
            "`__post_init__`, `copy.copy` carries every field without "
            "re-running initialisation, while `dataclasses.replace` -- the "
            "faster-looking substitution -- re-runs `__init__`/"
            "`__post_init__` and refuses `init=False` fields, so the "
            "no-re-normalisation property needs its own assertion rather "
            "than a comment."
        ),
        # `policy.depth_projection.project_snapshot_to_depth` answered every
        # `--depth` rung with one `copy.deepcopy`. At or above `headers` it
        # rebinds exactly two fields (`build_mode`, `build_source`) and
        # rewrites nothing, so on a real 327-type/4,802-function header
        # snapshot the copy cost +34% resident (0.516 -> 0.691 GiB) and
        # +12.5s per comparison -- paid once per member of a release
        # fan-out, concurrently. Nothing was wrong in the output, which is
        # why no existing test noticed: the defect is only visible as
        # resident bytes and object identity.
        fixed_by=(1317,),
        seed_tests=("tests/test_depth_projection_ownership.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "rung": ("binary", "headers", "build", "source"),
            "ownership": ("shared-surface", "owned-surface", "owned-pack"),
            "consumer_shape": (
                "rebind-on-result",
                "two-simultaneous-projections",
                "repeated-under-different-depths",
                "serialize-the-projection",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The sharing is sound because no consumer currently "
                    "writes through a projected snapshot -- established by "
                    "auditing the call sites and by digesting both projected "
                    "operands across a real full `compare --depth headers` "
                    "run, not by a structural guarantee. Nothing prevents a "
                    "future consumer from mutating one; the guard pins the "
                    "usage patterns that make sharing safe, so such a "
                    "consumer trips a test here, but a genuinely "
                    "unwriteable read-only view (over immutable data) is the "
                    "structural fix and is not attempted."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "Measured on a local compiled C++ fixture, never against "
                    "the real oneDAL bundle whose peak motivated it. The "
                    "change removes a *duplicate* of the L2 surface rather "
                    "than shrinking the surface, so each fan-out worker's "
                    "resident baseline is unchanged and the six-member peak "
                    "remains unverified; `release_job_mem_budget_gib`'s "
                    "`headers` constant was deliberately not re-derived on "
                    "the strength of it."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.admission_commits_the_whole_probed_budget",
        invariant=(
            "An automatic admission control that sizes a worker pool from a "
            "probed resource must budget for everything resident *beside* "
            "those workers, not only for the workers themselves. Dividing a "
            "whole probe by a per-worker budget commits 100% of what was "
            "probed and silently assumes the co-resident set is empty. It is "
            "not: a pool whose workers are threads shares one address space "
            "with the parent's own retained per-task results, with any "
            "context the tasks read through, and with whatever the process "
            "already held -- so the true peak is the pool's commitment plus "
            "that set, and the probe itself is a single sample taken before "
            "any of it exists. The failure is one-sided and invisible to "
            "every functional assertion: the run either fits or is "
            "OOM-killed, and the clamp reports having clamped either way. "
            "So the guard states the admission's properties over a swept "
            "domain of probe values rather than recomputing its arithmetic "
            "-- a test that re-derives `probe * factor - reserve` and "
            "compares it to the implementation compares the module with "
            "itself and passes against a factor of 1.0, which is the absent "
            "reserve the fix exists to add. The properties are directional: "
            "the new rule may never admit *more* than the rule it replaces "
            "(a reserve that could raise the count is a memory regression "
            "hiding inside a memory fix), must still admit at least one "
            "worker (a run that processes nothing is never a clean pass), "
            "must still skip the clamp entirely when the resource cannot be "
            "probed (unreadable is not zero), must stay monotonic in the "
            "probe, and must still scale up -- a reserve large enough to "
            "pin every host to a single worker passes every "
            "'uses less memory' assertion while destroying the "
            "parallelism the pool exists for. Each needs a non-vacuity "
            "guard, since 'never more than before' and 'monotonic' are both "
            "trivially true of a constant."
        ),
        # `workflows.release_jobs.release_jobs_mem_cap` clamped the
        # `compare-release` fan-out with `int(available / budget)`. The
        # fan-out is a `ThreadPoolExecutor`, so each completed member's
        # retained result, the shared header-depth context (AST/template
        # indexes, acquisition and metadata reuse) and the parent all live
        # in the same heap the workers allocate in, and none was charged.
        # Measured on a real 16 GB host: `--depth headers` probed 13.17 GiB
        # and admitted 3 workers at a 4.0 GiB budget -- a 12.0 GiB
        # commitment, 91% of available, with the parent's share still to
        # come out of the remaining 1.17 GiB.
        fixed_by=(1320,),
        seed_tests=("tests/test_cli_compare_release_jobs_memory.py",),
        # Deliberately empty: the seed test drives
        # `_compare_release_libraries` and the sizing helpers directly and
        # never builds a `CliRunner`, so it does not meet the schema's bar
        # for claiming the `cli` surface (CodeRabbit review).
        public_surfaces=(),
        axes={
            "depth": ("binary", "headers", "build", "source"),
            "probe": ("unreadable", "below-one-budget", "typical", "large"),
            "override": ("default", "utilization", "reserve", "unparsable"),
            "direction": (
                "never-admits-more",
                "floors-at-one",
                "monotonic",
                "scales-up",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Admission control bounds only the *concurrent* half of "
                    "a bundle's peak. The retention half is bounded on the "
                    "default path (a compact `BundleSignatureEvidence` per "
                    "member) but not under `need_full_snapshots`, which "
                    "JUnit output and `--bundle-facts-out` both set and "
                    "which holds every member's full old *and* new snapshot "
                    "simultaneously -- twelve full L2 surfaces for a "
                    "six-member bundle, a quantity no worker-count clamp "
                    "reduces. Spooling those through the existing storage "
                    "contracts is the structural fix and is not attempted."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "The utilization fraction and flat reserve are an "
                    "engineering target, not a measured expansion factor: "
                    "the co-resident set was established by reading the "
                    "fan-out's retention structure, not by sampling a real "
                    "six-member peak, because no oneDAL binary exists in "
                    "this workspace to sample. The admission is therefore "
                    "verified correct in its direction and its properties, "
                    "and unverified in its constant."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="test_double.narrower_than_the_real_signature",
        invariant=(
            "A test double installed over a production entry point must "
            "accept every parameter the real one accepts. When it does not, "
            "the failure is not a wrong answer but an *empty* one: each "
            "wrapped call raises `TypeError`, the counters and key sets the "
            "test was built to read come back empty, and only an assertion "
            "that demands a non-empty observation notices. So the guard is "
            "structural and reads the real signature with "
            "`inspect.signature` rather than restating it -- a list of "
            "parameter names written here would go stale the next time one "
            "is added, which is the original defect in a new place. Two "
            "non-vacuity conditions matter as much as the check: the "
            "discovery must find at least one double (a renamed helper or a "
            "changed patch spelling silently empties an AST scan), and the "
            "oracle must not degrade to an empty required-parameter list. "
            "The scan must also be keyed on the *object* patched and not "
            'the attribute name alone -- `"run"` matches '
            '`setattr(subprocess, "run", ...)` too, and the first '
            "version of this guard reported exactly those as violations."
        ),
        # Adding `group` to `dumper_cache.run_ast_acquisition` and
        # `AstAcquisitionScope.run` (the AST-root retention bound) broke the
        # doubles in `test_bundlefacts_l2_request_reuse.py` and
        # `test_l2_ast_acquisition_singleflight.py`. Every affected test is
        # `integration`-marked, so the default fast command -- which is what
        # the change was validated with -- said nothing, and the only
        # assertion that caught it was `assert len(cold_keys) >= 12`
        # reporting 0. The guard now runs in the fast lane.
        fixed_by=(1326,),
        seed_tests=("tests/test_ast_acquisition_double_signatures.py",),
        public_surfaces=(),
        axes={
            "entry_point": ("module-level function", "bound method"),
            "double_shape": (
                "named parameters",
                "*args",
                "**kwargs",
                "narrower",
            ),
            "owner_match": ("guarded owner", "unrelated same-named attribute"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The scan resolves a double only when the replacement "
                    "is a bare name whose `def` is in the same module. A "
                    "double passed as a lambda, an attribute, or imported "
                    "from a helper module is not inspected, so the guard "
                    "bounds the shape this suite actually uses rather than "
                    "every shape a double could take."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.optimization_wired_to_one_of_several_equivalent_paths",
        invariant=(
            "When an optimization replaces an expensive primitive, EVERY "
            "path in the tree that performs that same expensive work "
            "routes through it -- not merely the one path whose profile "
            "motivated the change. The failure is silent and specifically "
            "misleading: the helper exists, is tested, is documented as "
            "landed, and the reported hotspot still pays the original "
            "cost, so the optimization reads as complete while the "
            "measurement that prompted it does not move. A reviewer "
            "checking 'is the buffer implemented?' gets yes; the "
            "answerable question is 'does every caller that resolves a "
            "name use it?'. Establishing coverage means enumerating the "
            "callers of the underlying slow operation, not re-reading the "
            "optimized one."
        ),
        # #1331: `extract/elf_string_table.buffered_string_table` was wired
        # to `elf_metadata`'s dynamic-symbol walk only, while
        # `dumper_elf_symbols._pyelftools_exported_symbols` -- which builds
        # its own `ELFFile` and walks BOTH `.dynsym` and `.symtab` -- kept
        # resolving every `Symbol.name` with one seek-and-read per name.
        fixed_by=(1331,),
        seed_tests=("tests/test_dumper_elf_symbols_buffering.py",),
        public_surfaces=(),
        axes={
            "symbol_table": (".dynsym", ".symtab"),
            "symbol_shape": (
                "exported",
                "hidden-visibility",
                "static-only",
                "many",
                "long-named",
            ),
            "input_health": ("well-formed", "truncated"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "There is no gate enumerating the callers of a "
                    "just-optimized primitive, so the next instance of "
                    "this class is still found by reading, not by CI. "
                    "The seed test observes engagement for the two "
                    "sections THIS path walks; a third ELF-reading path "
                    "added later would not be noticed by it."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.reuse_key_normalizes_an_ordered_input",
        invariant=(
            "A cache/reuse key canonicalizes only inputs that are "
            "genuinely order-INsensitive. Sorting or de-duplicating an "
            "ordered input makes two requests with different meanings key "
            "identically, so one request is served the other's result -- a "
            "wrong answer produced quickly, which is strictly worse than "
            "the cache miss the normalization was meant to avoid. The "
            "test that isolates this must permute a fixed input set: "
            "substituting one member for another also changes the set, so "
            "it passes against the sorting implementation and proves "
            "nothing. Scoped both ways -- a membership-only input must "
            "still key order-independently, or the fix trades a "
            "correctness bug for lost reuse."
        ),
        # #1331: `workflows.release_public_surface.build_side_identity`
        # sorted `includes`, which becomes the compiler's `-I` search
        # order; `-I a -I b` and `-I b -I a` resolve a same-named header
        # to different declarations but produced one
        # `SurfaceAcquisitionIdentity.key()`.
        fixed_by=(1331,),
        seed_tests=("tests/test_surface_acquisition_include_order.py",),
        public_surfaces=(),
        axes={
            "input_kind": ("ordered", "membership-only"),
            "sequence_shape": ("permuted", "duplicate-bearing", "equal"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Asserted at the key level against a native-clang "
                    "counterexample reproduced by hand, not as a "
                    "completed end-to-end abicheck failure: no test "
                    "drives two differently-ordered include paths through "
                    "a real release comparison and observes the differing "
                    "contract. The other order-sensitive inputs folded "
                    "into this and neighbouring keys (compile option "
                    "tokens, `-D` define order) have no equivalent guard."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.bounded_cache_budget_omits_what_it_retains",
        invariant=(
            "A cache that publishes a retained-size budget counts every "
            "object it keeps alive, including ones it retains only "
            "indirectly -- a keepalive reference, an interned key, a "
            "closure. An omitted object is unbounded by construction: "
            "eviction is driven by the figure, so what the figure does "
            "not see is never the reason anything is evicted, and the "
            "omission is typically the LARGEST thing retained (a compiled "
            "pattern dwarfs the entry bookkeeping around it). The "
            "companion rule: a guarantee a cache advertises about shared "
            "results -- immutability above all -- is enforced, not "
            "documented, because a shared result mutated by one reader is "
            "observed changed by the next. `__slots__` bounds which "
            "attributes exist; it does not make them read-only. The "
            "accounting test needs a round-trip oracle (retain, then "
            "release everything, and return to the starting figure): a "
            "one-directional 'it grows' assertion passes against an "
            "implementation that adds cost and never subtracts it."
        ),
        # #1331: a `_MatchCache` entry keeps its compiled pattern alive via
        # `_keepalive`, outliving the 64-entry `_VocabularyCache` that
        # compiled it, while `retained_bytes` counted only entry
        # bookkeeping, subject strings and results. Separately,
        # `SpellingMatch` was slotted and documented immutable but
        # accepted `match._text = ...`.
        fixed_by=(1331,),
        seed_tests=("tests/test_spelling_match_cache_retention.py",),
        public_surfaces=(),
        axes={
            "pattern_population": ("single", "shared", "distinct", "oversized"),
            "budget_direction": ("retain", "release", "round-trip"),
            "mutation_operation": ("set", "delete", "new-attribute"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The per-pattern cost is an estimate "
                    "(`_PATTERN_BYTES_PER_CHAR`), on the same terms as "
                    "the existing per-entry/per-match constants, and is "
                    "not validated against measured RSS -- the budget "
                    "bounds growth rather than reporting real bytes. The "
                    "vocabulary cache is still bounded by entry COUNT (64) "
                    "rather than by vocabulary bytes, and its own keys "
                    "(the frozensets of spellings) are outside every "
                    "budget; the review's request for one coordinated "
                    "budget across vocabulary keys, compiled matchers and "
                    "results is only partly answered here."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.fixed_layout_fast_path_field_decoding",
        invariant=(
            "A fast path that replaces a third-party parser for a "
            "fixed-layout record must agree with that parser on EVERY "
            "field value and EVERY layout variant it claims to support, "
            "and must refuse -- returning a distinguishable 'unsupported', "
            "never an empty result -- for the ones it does not. Two "
            "properties make this class dangerous: the failures are "
            "silent (a mis-masked field yields a plausible wrong value, "
            "not a crash) and the tempting oracle is the fast path's own "
            "format string, which is a tautology. The oracle must be the "
            "displaced parser itself, exercised over the variants the "
            "development host does not produce -- a test limited to what "
            "the local compiler emits leaves other word sizes and "
            "endiannesses, whose record ORDER may differ, wholly "
            "unexercised. Refusal tests must also reach the condition "
            "they name: an earlier guard that rejects the input first "
            "makes the later guard's test pass with that guard deleted."
        ),
        # #1331: `extract/elf_symbol_fastpath.py` replaced pyelftools'
        # per-symbol `construct` parse (11.9s of a profiled 13.58s on
        # oneDAL's libonedal_core.so.3) with one bulk `struct.iter_unpack`.
        # Its first draft masked `st_other` with 0x3, per the generic
        # ABI's two-bit visibility field; pyelftools uses three bits, so
        # `STV_SINGLETON` (5) decoded as `STV_INTERNAL` (1) and the export
        # filter silently dropped the symbol. Caught before wiring by the
        # exhaustive binding-by-visibility grid. Two refusal tests were
        # separately found, by mutation, to be masked by the short-read
        # guard and to pass with their own check deleted.
        fixed_by=(1331,),
        seed_tests=("tests/test_elf_symbol_fastpath.py",),
        public_surfaces=(),
        axes={
            "elf_class": ("32", "64"),
            "endianness": ("little", "big"),
            "field": ("st_name", "binding", "visibility", "st_shndx"),
            "refusal": (
                "entry-size",
                "elf-class",
                "partial-record",
                "short-read",
                "oversize",
                "malformed-header",
                "stream-error",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The fast path yields raw integers for the four "
                    "fields a filtering caller needs; `st_value`/"
                    "`st_size`/`st_type` are decoded only far enough to "
                    "prove they do not shift the others, and no caller "
                    "consumes them through this route yet. Symbol "
                    "VERSION decoding (GNU default/non-default) is "
                    "untouched by this change and still goes through "
                    "`elf_metadata`'s ordinary reader, so the version "
                    "half of the original review's acceptance list is "
                    "not covered by these tests. Equivalence on real "
                    "binaries was established on x86-64 little-endian "
                    "objects only -- the big-endian and ELF32 evidence "
                    "is synthetic, built by pyelftools itself, because "
                    "no such toolchain was available."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.retention_decided_by_one_switch_not_by_consumers",
        invariant=(
            "A retention decision taken for several consumers at once must "
            "be resolved per consumer and per side, not by one boolean that "
            "any of them can flip for all of them. A shared switch is not "
            "merely coarse: it is unfalsifiable, because the side nobody "
            "reads looks exactly like the side everybody reads, and no test "
            "of the *output* can tell the difference -- the outputs are "
            "identical either way, which is precisely why the amplification "
            "survived. So the guard is a consumer inventory with an "
            "executable claim attached: the resolved retention says which "
            "side each requested output reads, and a test asserts the "
            "unread side is genuinely absent from the member entry rather "
            "than merely unused. `need_full_snapshots` pinned both sides' "
            "full `AbiSnapshot` for every member whenever JUnit or "
            "`--bundle-facts-out` was requested, while every consumer of "
            "either read only the OLD side; the NEW graph was retained for "
            "the whole release and never opened."
        ),
        fixed_by=(1332,),
        seed_tests=(
            "tests/test_release_snapshot_retention.py",
            "tests/test_compare_release_contract_coverage.py",
            # The same class, one consumer further: JUnit was resolved as
            # an OLD-side full-snapshot consumer without anyone checking
            # what it read, which was four attributes. See
            # `perf.a_whole_document_retained_for_a_narrow_projection`.
            "tests/test_junit_symbol_inventory.py",
        ),
        public_surfaces=(),
        axes={
            "output": ("json", "junit", "bundle-facts-out", "junit+baseline"),
            "side": ("old", "new"),
            "member_count": ("one", "several"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The OLD side is still retained in full, per member, "
                    "for `--bundle-facts-out` -- which genuinely reads the "
                    "whole document. JUnit no longer does (it takes the "
                    "compact `SymbolInventory`), so that half of this "
                    "gap is closed. Bounding the remaining one means "
                    "spooling completed members through the storage codec "
                    "rather than deciding retention, which is a separate "
                    "change."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "The consumer inventory is asserted against the call "
                    "sites as they are, not derived from them: a future "
                    "consumer that starts reading `_new_snapshot` would "
                    "fail the seed test (the key is absent) rather than be "
                    "prevented from being written, which is the intended "
                    "direction but is a test-time signal, not a type-level "
                    "one."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.a_whole_document_retained_for_a_narrow_projection",
        invariant=(
            "When a consumer is handed a large object and reads a small, "
            "fixed projection of it, the projection is what must be "
            "retained -- and *which* projection must be established by "
            "auditing every attribute the consumer actually reads, not by "
            "the parameter's declared type or by one function's docstring. "
            "The guard is therefore twofold, and neither half suffices "
            "alone: a differential assertion that rendering from the "
            "projection equals rendering from the whole object (so the "
            "audit was complete), and a reachability assertion that the "
            "large object is genuinely unreachable from the projection (so "
            "the saving is real rather than a wrapper around the same "
            "graph). An output-equality test alone passes against a "
            "projection that simply holds the original; a reachability "
            "test alone passes against a projection that has dropped "
            "something the consumer needed. JUnit retained every release "
            "member's full `AbiSnapshot` -- declarations, semantic IR, "
            "surface graph, build-source pack -- until the release-level "
            "fold ran, to read four attributes off it, and the module "
            "docstring recording that retention named a *fifth* consumer "
            "(declaration locations) that did not exist."
        ),
        fixed_by=(1334,),
        seed_tests=(
            "tests/test_junit_symbol_inventory.py",
            "tests/test_release_snapshot_retention.py",
        ),
        public_surfaces=("cli", "python-api"),
        axes={
            "output": ("junit", "bundle-facts-out", "junit+baseline"),
            "cardinality": ("single-pair", "release"),
            "filter": ("none", "show-only"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The audit is a point-in-time reading of "
                    "`junit_report.py`'s attribute accesses, re-asserted "
                    "only by the differential render test: a future JUnit "
                    "feature that starts reading a fifth snapshot "
                    "attribute fails that test (the inventory cannot "
                    "supply it) rather than being prevented from doing so."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.streaming_producer_joined_at_the_encoder",
        invariant=(
            "A pipeline built to bound memory must be bounded end to end: "
            "a producer that yields fragments buys nothing if any stage "
            "downstream joins them, and the joining stage is easy to miss "
            "precisely because the *output* is correct and the streaming "
            "half is visibly present upstream. So the guard observes the "
            "mechanism, not the result: the encoder must emit output "
            "before its input is exhausted, and no single buffer handed to "
            "the writer may approach the document's own size -- an "
            "assertion on the bytes written cannot distinguish a streaming "
            "encoder from a joining one. Paired with a differential "
            "byte-identity check against the pre-existing one-shot encoder "
            "per supported algorithm, since an incremental codec that "
            "silently changes the stored envelope is a different defect, "
            "and with destination-integrity checks on a mid-stream "
            "producer failure, since a streaming write has a failure "
            "window a one-shot write does not. "
            "`write_snapshot_text_stream` streamed an uncompressed write "
            'and did `"".join(chunks)` for a compressed one, so a '
            "compressed baseline still peaked at the whole document plus "
            "its whole encoded copy."
        ),
        fixed_by=(1334,),
        seed_tests=("tests/test_incremental_compression.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "compression": ("none", "gzip", "zstd"),
            "chunking": ("byte", "small", "whole-document"),
            "outcome": ("success", "producer-failure"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "A streamed zstd frame omits its declared content size "
                    "when the caller cannot state the decoded length up "
                    "front, which is the case for the bundle-facts "
                    "producer. The frame is legal and round-trips, and the "
                    "reader already handles `CONTENTSIZE_UNKNOWN`, but it "
                    "loses the declared-size cross-check that catches a "
                    "frame truncated mid-header, and its bytes differ from "
                    "the one-shot encoder's for the same content."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="cache.bookkeeping_describes_a_different_set_than_it_retains",
        invariant=(
            "A cache that reports how much it retains must report on the "
            "set that actually holds the memory. When a table is bounded "
            "along one axis and its counters describe only that axis, the "
            "counters are *correct* and the retention is unbounded, which "
            "is strictly worse than having no counters: an investigation "
            "reads them, sees the expected numbers, and looks elsewhere. "
            "`AstAcquisitionScope` bounded and counted its id-keyed groups "
            "while its content-keyed entries -- whose `Future` result *is* "
            "the parsed AST root -- were neither; a measured six-member "
            "release retained 24 raw roots while reporting eight retained "
            "groups and sixteen releases. The regression test for this "
            "class must therefore assert *reclamation of the object* "
            "through a `weakref`, never a counter, since a fix that only "
            "decrements a number satisfies every counter assertion and "
            "frees nothing; and it must assert the complement (a retained "
            "entry's result stays alive), or the reclamation assertion "
            "passes against a cache that retains nothing at all."
        ),
        fixed_by=(1332,),
        seed_tests=("tests/test_ast_acquisition_raw_entry_bound.py",),
        public_surfaces=(),
        axes={
            "key_kind": ("content-derived", "id-derived", "both for one object"),
            "entry_state": ("completed", "in-flight", "failed"),
            "traffic": ("one shared key", "distinct keys", "randomised mixed"),
            "payload_size": ("uniform small", "mixed", "uniform large"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The bound is a count, not a byte budget: the scope "
                    "cannot size a parsed AST cheaply enough to bound "
                    "bytes, so a run whose header sets differ wildly in "
                    "size still retains up to MAX_RETAINED_RAW_ENTRIES of "
                    "the largest ones."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "Eviction is exercised under real threads for the "
                    "single-flight and in-flight cases, but the released "
                    "byte total under a genuine concurrent release fan-out "
                    "is measured by the benchmark harness, not asserted by "
                    "a test -- a concurrent RSS assertion would be brittle."
                ),
                reference="scripts/bench_release_memory.py",
            ),
        ),
    ),
    BugClass(
        id="serialization.whole_document_materialised_to_write_it",
        invariant=(
            "Writing a multi-member document must not require every "
            "member, the whole encoded string and its byte encoding to "
            "exist at once. The three copies sit on top of the member "
            "graph itself, so the transient peak of *publishing* a "
            "baseline can exceed the peak of producing it -- and nothing "
            "in the output reveals it, since the bytes are the same either "
            "way. That is what makes this a regression class rather than a "
            "tuning question: the only observable is memory, so the guard "
            "has to be a reachability probe (how many member documents are "
            "alive when the next one is built) plus a byte-identity check "
            "against the eager spelling as the oracle. A golden file "
            "generated by the new writer would assert nothing about the "
            "change; the eager encoder is the oracle precisely because it "
            "is the thing being replaced."
        ),
        fixed_by=(1332,),
        seed_tests=(
            "tests/test_bundle_facts_streaming_write.py",
            "tests/test_json_stream_encoder.py",
        ),
        public_surfaces=(),
        axes={
            "members": ("zero", "one", "two", "six"),
            "envelope": ("uncompressed", "gzip"),
            "document": ("generated random", "real BundleFacts"),
            "indent": ("0", "1", "2", "4"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "A *compressed* write still joins the fragments and "
                    "delegates to the one-shot codec: both compressors here "
                    "take and return whole buffers, and their determinism "
                    "guarantees are stated for that form. The default "
                    "baseline path is uncompressed, which is the one "
                    "measured; a streaming compressor is a separate change "
                    "with its own determinism story."
                ),
                reference="abicheck/snapshot_io.py",
            ),
            KnownGap(
                description=(
                    "Only the *serialisation* transient is bounded. The "
                    "member snapshots themselves are still all resident, "
                    "because the release fan-out holds them until its folds "
                    "run -- bounding that is the completed-member spooling "
                    "this work did not attempt."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.per_symbol_dto_carries_a_per_instance_dict",
        invariant=(
            "A small frozen dataclass materialized once per row of a real "
            "input -- an export table, a symbol table, a relocation list -- "
            "carries no per-instance `__dict__`. The cost is invisible at "
            "the definition site and proportional to the input, so no test "
            "of behaviour can see it and no profile attributes it to a "
            "line: it shows up only as allocation pressure. The "
            "measurement that matters is the ratio, not the absolute "
            "number -- a dict six times the size of the data it wraps is "
            "the signal, and it is worth fixing only where the type is "
            "built per input row rather than once per run. The structural "
            "test must inspect a CONSTRUCTED instance for `__dict__` "
            "rather than read the decorator's keyword, since `slots=True` "
            "is one of several routes to the property, and must carry a "
            "vacuity guard, because a module sweep whose discovery "
            "predicate stops matching passes while asserting nothing."
        ),
        # #1333: `RawExportEntry` was frozen but unslotted -- 48 B of object
        # plus 296 B of `__dict__` per row, against 43,864 `.dynsym` entries
        # per side of a real oneDAL release (87,728 both sides, ~22 MiB at
        # peak). All three consumers project the index straight to a name
        # set and drop it, so the entries are a transient spike, not
        # retained state.
        #
        # Sibling to `perf.retention_decided_by_one_switch_not_by_consumers`
        # (#1332) above, and deliberately a separate class: that one is
        # about *which* objects are kept alive and for how long, this one
        # about what each object costs while it is. A release can be fixed
        # for one and still lose to the other.
        fixed_by=(1333,),
        seed_tests=("tests/test_export_index_allocation.py",),
        public_surfaces=(),
        axes={
            "dto": ("RawExportEntry", "RawExportIndex"),
            "operation": (
                "construct",
                "compare",
                "hash",
                "set-membership",
                "field-read",
                "default",
                "field-assign",
                "undeclared-assign",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Scoped to `model/export_index.py`; there is no "
                    "repo-wide sweep for other per-row DTOs, so the next "
                    "instance is found by reading rather than by CI. "
                    "Deliberately so: the same rule applied to a type "
                    "built once per run would be cargo-culting, and "
                    "`slots=True` on a frozen dataclass carries a real "
                    "CPython wart (an undeclared attribute assignment "
                    "raises `TypeError: super(type, obj)...` instead of "
                    "`FrozenInstanceError`, because slots builds a new "
                    "class while the frozen `__setattr__` closed over the "
                    "original) that is only acceptable where the "
                    "allocation saving is real."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="perf.enumerable_value_space_allocated_per_occurrence",
        invariant=(
            "A value type whose inhabitants are enumerable -- a frozen "
            "record of a few booleans or a small enum -- is materialized "
            "once per distinct VALUE, not once per occurrence, wherever it "
            "is produced per row of a real input. The bound is the point: "
            "the number of distinct objects a producer yields must not "
            "grow with its input size. Two things make the sharing sound "
            "rather than merely smaller, and both must be asserted, not "
            "argued: the type is frozen (a shared object no caller can "
            "write to), and no reader distinguishes two equal values by "
            "identity. Three test obligations follow, each closing a "
            "mutation the others miss. (1) The bound must be counted "
            "through the REAL producer, not the factory: a correct "
            "factory whose one call site still calls the constructor "
            "passes every factory-only test and saves nothing. (2) The "
            "fixture must span several distinct values, or a cache that "
            "ignores its key entirely -- collapsing every answer onto one "
            "-- satisfies the bound while corrupting results. (3) A "
            "normalizing factory must be tested with an input that can "
            "DETECT the normalization: `int` inputs are hash-equal to "
            "`bool` and resolve through a dict key either way, so they "
            "prove nothing about a `bool(...)` coercion."
        ),
        # #1333 follow-up: `SymbolSignatureStatus` is frozen, slotted, and
        # holds two booleans -- four inhabitants -- yet
        # `symbol_signature_statuses` allocated one per symbol. 48 B against
        # 87,728 symbols is 4.02 MiB for one real oneDAL library, and the
        # release fan-out retains a mapping per matched member (~24 MiB
        # across six). About 1% of a measured ~2.3 GiB peak: taken because
        # it is free and provably safe, NOT as a memory fix -- the
        # member-concurrency measurement owns that question.
        #
        # Obligation (3) is here because it was violated in this very PR:
        # the first version of the seed test exercised only `int(True)`,
        # and a mutation deleting the `bool(...)` coercion passed all 24
        # tests. The real coercion only bites on a truthy value that is not
        # hash-equal to `True` (a string, `2`, an unhashable list), which
        # the sweep now covers -- the same "an untested key is not an
        # unnecessary key" lesson AGENTS.md records for the memoization
        # cache key.
        fixed_by=(1333,),
        seed_tests=("tests/test_symbol_signature_status_interning.py",),
        # Internal-module construction and a direct `symbol_signature_
        # statuses` call -- no route through `abicheck.service`, no CLI, no
        # Action, so this stays `()` per the schema's own rule.
        public_surfaces=(),
        axes={
            "detection": ("value-equivalence", "allocation-bound"),
            "input_size": ("16", "256", "2048"),
        },
    ),
)
