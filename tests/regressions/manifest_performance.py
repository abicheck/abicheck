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
                    "member); the NEW-side half of the old "
                    "`need_full_snapshots` amplification is closed too -- "
                    "JUnit and `--bundle-facts-out` read the OLD side only, "
                    "so `SnapshotRetention` keeps NEW compact for them, "
                    "halving six full L2 surfaces off a six-member bundle. "
                    "What remains is the OLD side those two consumers "
                    "genuinely do read: one full snapshot per member, held "
                    "until the release folds run. Spooling *those* through "
                    "the existing storage contracts is the structural fix "
                    "and is still not attempted."
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
        fixed_by=(),
        seed_tests=(
            "tests/test_release_snapshot_retention.py",
            "tests/test_compare_release_contract_coverage.py",
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
                    "for JUnit and `--bundle-facts-out`: those consumers "
                    "genuinely read it. Bounding *that* means spooling "
                    "completed members through the storage codec rather "
                    "than deciding retention, which is a separate change."
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
        fixed_by=(),
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
        fixed_by=(),
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
)
