#!/usr/bin/env python3
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

"""Pinned real-integration L2 profiles: oneDAL, SVS, PVXS.

These are the three live integrations abicheck is being exercised against. They
belong in a **periodic/manual lane only** -- an ordinary PR must never download
and build oneDAL -- so this module is deliberately *declarative*: it states each
profile's pinned inputs, the libraries and headers that are actually in L2 scope,
the toolchain identity a measurement must record, and the exact reproducible
commands to prepare and run it. Whether a given host can satisfy a profile is
answered at runtime, per profile, and never by substituting something else.

**The one rule this module exists to enforce:** an unavailable profile is
reported ``BLOCKED``/``NOT_RUN`` with a concrete reason. It is never quietly
replaced by a synthetic fixture, and a synthetic number is never published under
a real project's name. The small generated fixture in ``l2_cli_fixture.py`` is a
different thing measuring a different question; conflating the two would turn a
"we measured oneDAL" claim into a statement about a 40-line header.

Three further honesty constraints, each one a thing an earlier audit of this
area got wrong or was at risk of getting wrong:

* **No declarative L2 bundle capability exists today.** Where a profile has
  several header-bearing libraries, the honest measurement is the supported set
  of per-library L2 operations, measured as one multi-library workload --
  reporting the set's total, each library's own cost, how many distinct header
  contexts were involved, and how much work was genuinely repeated across them.
  That is **not** a bundle scan and this module never calls it one; introducing
  a bundle capability is product work, out of scope here.
* **A library with no public API of its own is not an L2 case.** oneDAL ships
  ``libonedal_thread``, which has no public headers to scope against; inflating
  the profile to a sixth "L2 library" by pointing it at someone else's headers
  would manufacture a measurement. It is listed with
  ``in_l2_scope=False`` and a stated reason instead.
* **A historical baseline must be historical.** Comparing an old binary against
  *today's* headers, or against a binary-only snapshot, is not an L2 temporal
  comparison -- it is a different (and easier) measurement wearing the same
  name. Each profile names where each side's headers come from, and
  :func:`validate_profile` rejects a plan that sources both sides' headers from
  one revision. It must also be the baseline the integration actually declares:
  SVS gates on its released v0.4.0 runtime distribution, so a PR-base comparison
  is a separate profile (``SVS_PR_BASE``) and not a cheaper stand-in for it.
* **Readiness is not a measurement.** :func:`resolve_status` answers only
  whether a host *could* measure a profile, and its positive answers are
  ``READY``/``PARTIAL``. It once returned ``MEASURED`` for any request whose
  prepared root merely existed -- an empty directory, no library or headers on
  either side, no comparison run. Declared operands are now checked
  (:func:`missing_inputs`), and ``MEASURED`` is reachable only through
  :func:`promote_to_measured`, which requires a completed timed run with
  validated output.
* **A scenario's findings mean what that scenario says they mean.** Every
  declared scenario carries an expectation (``SCENARIO_EXPECTATIONS``). "Any
  finding is a false positive by construction" is true of a literal
  self-comparison and of nothing else: two independent builds under one contract
  are to be investigated against the recorded toolchain, and two intentionally
  different build variants differ in contract on purpose. Treating all three the
  same risks reading correct detection of a build-induced ABI change as a
  scanner defect -- or suppressing it to satisfy the wrong expectation.

Pure stdlib; imported by the periodic harness and by its tests. Nothing here
downloads or builds anything on import.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Profile availability states. ``BLOCKED`` and ``NOT_RUN`` are deliberately
#: distinct: ``BLOCKED`` is "this host cannot satisfy the profile's stated
#: preconditions", ``NOT_RUN`` is "the preconditions hold but the caller did not
#: ask for it". Collapsing them would let a lane that skipped a profile for
#: convenience look like one that could not run it.
#: ``PARTIAL`` is in this vocabulary because `resolve_status` returns it: a host
#: that can build some of a profile's header/compile contexts and not others is a
#: fourth outcome, not a variety of one of the other three. It was omitted when
#: `PARTIAL` was introduced, so a caller validating or enumerating results through
#: this constant would have rejected or dropped a valid partial result -- and the
#: vocabulary test did not catch it, because its all-tools-missing setup reaches
#: ``BLOCKED`` before the per-context branch runs at all (Codex review).
#:
#: ``READY`` and ``MEASURED`` are likewise distinct, and the distinction is the
#: whole point of :func:`resolve_status` vs :func:`promote_to_measured`.
#: `resolve_status` answers a *readiness* question from preconditions alone --
#: tools, pinned revisions, and the concrete operands present on disk -- and
#: therefore can only ever return ``READY``. ``MEASURED`` is a claim that a timed
#: operation actually ran and produced validated output, which no precondition
#: check can establish. Collapsing the two let an EMPTY prepared directory report
#: ``MEASURED``: the resolver checked that the directory existed and returned
#: "measured" without a single artifact, header, or comparison behind it. That is
#: the same class of dishonesty as publishing a synthetic number under a real
#: project's name, which is what this module exists to prevent.
STATUSES = ("MEASURED", "READY", "PARTIAL", "BLOCKED", "NOT_RUN")

#: The scenario shapes a profile may declare, each with the expectation that
#: applies to its findings. The expectation is part of the vocabulary because
#: getting it wrong is how a harness comes to treat *correct* detection as a
#: scanner defect: the SVS profile previously declared that any finding between
#: two independent builds of one revision is "a false positive by construction",
#: which is true only of a literal self-comparison. Two independent builds are
#: not one artifact, and two deliberately different build variants are not even
#: the same contract -- SVS's own PR artifacts make the point, with
#: byte-identical runtime headers on the default and public-only builds and
#: materially different exported-symbol sets.
SCENARIO_EXPECTATIONS: dict[str, str] = {
    "temporal_release": (
        "released baseline vs candidate: the comparison the integration actually "
        "gates on. Findings are real until shown otherwise and are judged against "
        "the release's declared compatibility promise."
    ),
    "temporal_pr_base": (
        "PR merge-base vs PR head: a useful additional smoke test, NOT a "
        "substitute for the release-to-candidate comparison -- it cannot expose "
        "an ABI change that entered the branch before the merge base."
    ),
    "self_comparison": (
        "one artifact compared against itself: no introduced compatibility "
        "regression may be reported. Persistent audit observations (unknowns, "
        "assurance notes) are a separate, expected output and are not findings "
        "against the change."
    ),
    "rebuild_equivalence": (
        "two independent builds under a controlled, identical build contract: a "
        "difference is to be INVESTIGATED against the recorded compiler, flags, "
        "dependencies and artifact evidence -- not assumed to be a scanner "
        "defect. Identical header text does not imply identical binary evidence."
    ),
    "variant_comparison": (
        "two intentionally different build variants (e.g. default vs "
        "public-only): their real contract differences are the subject of the "
        "measurement. Findings are evaluated on their merits and are never "
        "automatically labelled false positives."
    ),
}

#: Scenarios that compare two different revisions/distributions, and therefore
#: require two different operands.
TEMPORAL_SCENARIOS = ("temporal_release", "temporal_pr_base")

#: How one side's operands are obtained.
SIDE_SOURCES = ("build_from_revision", "prebuilt_distribution")

#: Suffixes that make a file count as header evidence. Mirrors
#: ``abicheck.header_utils.HEADER_SUFFIXES``; restated rather than imported
#: because this module is deliberately dependency-free stdlib (it is read and
#: run by the periodic lane without installing abicheck). A divergence would be
#: conservative in the safe direction -- a suffix missing here makes a real
#: header root read as empty, i.e. BLOCKED, never READY.
HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc")


@dataclass(frozen=True)
class LibraryTarget:
    """One library within a profile, and whether it is an L2 case at all."""

    name: str
    #: Path, relative to the built tree, of the shared library.
    artifact: str
    #: The public headers (or header roots) that define this library's L2
    #: surface. Empty iff ``in_l2_scope`` is False.
    public_headers: tuple[str, ...] = ()
    #: Additional include roots the headers need to parse.
    include_roots: tuple[str, ...] = ()
    in_l2_scope: bool = True
    #: Required whenever ``in_l2_scope`` is False -- why it is not a case.
    out_of_scope_reason: str | None = None
    #: A free-text label for this library's header/compile context. Libraries
    #: sharing a label share a context; the count of distinct labels is what a
    #: multi-library measurement reports as its context count.
    context: str = "default"


@dataclass(frozen=True)
class RealProfile:
    """A pinned real-integration profile."""

    id: str
    project: str
    #: The upstream change this profile tracks, as a URL. Recorded so a
    #: measurement can be traced back to what it was taken for.
    reference: str
    repository: str
    #: The two revisions compared. ``old_revision`` is the historical side, and
    #: its headers must come from that revision -- see the module docstring.
    old_revision: str
    new_revision: str
    libraries: tuple[LibraryTarget, ...]
    #: Shell commands run ONCE, before either side (clone, dependency builds).
    #: Run outside every timed window.
    prepare_commands: tuple[str, ...] = ()
    #: Shell commands run once PER SIDE, with ``{side}`` (``old``/``new``),
    #: ``{revision}``, ``{root}`` (that side's own operand tree, in *distribution*
    #: layout) and ``{src_root}`` (that side's checkout, when it is built from
    #: source) substituted. This is what produces the two artifacts a temporal
    #: comparison needs -- a profile with an empty list here can only ever build
    #: one side, unless ``side_commands`` supplies the missing one.
    per_side_commands: tuple[str, ...] = ()
    #: Per-side override of ``per_side_commands``, keyed by ``"old"``/``"new"``.
    #: The two sides of a comparison are not always acquired the same way: a
    #: released baseline is a *published distribution*, not a rebuild of a tag,
    #: and an explicitly empty tuple here says "this side is supplied to the
    #: harness, not built by it" (see ``side_sources``).
    side_commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: How each side's operands are obtained, keyed by ``"old"``/``"new"``; see
    #: ``SIDE_SOURCES``. Defaults to ``build_from_revision`` for an unlisted
    #: side. A ``prebuilt_distribution`` side is the reason ``side_commands``
    #: exists: re-building a release from its tag measures a rebuild, not the
    #: artifact consumers actually got, and when the release and CI artifacts
    #: already exist there is no reason to rebuild at all.
    side_sources: dict[str, str] = field(default_factory=dict)
    #: Tools that must be present, by name on PATH, for ANY measurement of this
    #: profile. A tool only one header/compile context needs belongs in
    #: ``context_tools`` instead -- see that field for why the distinction is not
    #: cosmetic.
    required_tools: tuple[str, ...] = ()
    #: Extra tools a single ``LibraryTarget.context`` needs, keyed by that label.
    #:
    #: The distinction this field exists for: oneDAL's two DPC++ libraries need
    #: ``icpx`` and its three host libraries do not, so listing ``icpx`` among
    #: `required_tools` made a host-only machine report the WHOLE profile
    #: ``BLOCKED`` -- making the profile's own documented behaviour (measure the
    #: host libraries, report only the DPC++ ones blocked) unreachable, and hiding
    #: usable coverage from the periodic availability artifact (Codex review).
    context_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Approximate resource needs, so a lane can decline before starting.
    approx_build_minutes: int = 0
    approx_disk_gb: int = 0
    notes: tuple[str, ...] = ()
    #: Distribution packages needed beyond ``required_tools`` -- a header-only
    #: dependency has no binary on PATH, so tool presence cannot detect it.
    system_packages: tuple[str, ...] = ()
    #: Scenario shapes this profile is meant to exercise, from
    #: ``SCENARIO_EXPECTATIONS``. No default: a scenario carries the expectation
    #: that applies to this profile's findings, and a default would let a profile
    #: inherit an expectation nobody chose for it -- which is how SVS came to
    #: declare that any finding between two independent builds is a false
    #: positive by construction.
    scenarios: tuple[str, ...] = ()

    @property
    def l2_libraries(self) -> tuple[LibraryTarget, ...]:
        return tuple(lib for lib in self.libraries if lib.in_l2_scope)

    @property
    def header_contexts(self) -> int:
        return len({lib.context for lib in self.l2_libraries})

    def revision_for_side(self, side: str) -> str:
        return self.old_revision if side == "old" else self.new_revision

    def source_for_side(self, side: str) -> str:
        """How *side*'s operands are obtained -- see ``SIDE_SOURCES``."""
        return self.side_sources.get(side, "build_from_revision")

    def commands_for_side(self, side: str) -> tuple[str, ...]:
        """The commands that produce *side*'s operands.

        ``side_commands`` wins over ``per_side_commands`` when it names the
        side, *including* when it names it with an empty tuple -- that is the
        explicit "supplied, not built here" statement a prebuilt distribution
        side makes, and silently falling back to the shared build recipe would
        turn it back into the rebuild it is meant to replace.
        """
        if side in self.side_commands:
            return self.side_commands[side]
        return self.per_side_commands

    def side_root(self, prepared_root: Path, side: str) -> Path:
        """Where *side*'s operands live under a prepared root.

        One layout for both kinds of side, so a consumer never has to know which
        it is looking at: ``prepare_script`` builds and installs into it, and an
        operator supplying a published distribution extracts into the same place.
        """
        return prepared_root / f"{self.id}_{side}"


ONEDAL = RealProfile(
    id="onedal",
    project="uxlfoundation/oneDAL",
    reference="https://github.com/uxlfoundation/oneDAL/pull/3693",
    repository="https://github.com/uxlfoundation/oneDAL.git",
    # Real, verified revisions: PR #3693's head and its merge base with main,
    # resolved by fetching refs/pull/3693/head and deepening until the merge base
    # was reachable. Placeholders here were a silent unrunnability -- see
    # is_placeholder_revision.
    old_revision="a689f87d2f37873078598dfdbf069ee45de2c76e",
    new_revision="0c95622ed2d8a0c981d6a37a635657c44a4c3e1d",
    libraries=(
        LibraryTarget(
            "onedal_core",
            "lib/intel64/libonedal_core.so",
            public_headers=("cpp/daal/include/daal.h",),
            include_roots=("cpp/daal/include",),
            context="host",
        ),
        LibraryTarget(
            "onedal",
            "lib/intel64/libonedal.so",
            public_headers=("cpp/oneapi/dal.hpp",),
            include_roots=("cpp/oneapi",),
            context="host",
        ),
        LibraryTarget(
            "onedal_dpc",
            "lib/intel64/libonedal_dpc.so",
            public_headers=("cpp/oneapi/dal.hpp",),
            include_roots=("cpp/oneapi",),
            # The differing-context half of the profile: the same headers
            # parsed under a DPC++ compile context, which is a genuinely
            # different L2 input, not a duplicate of the host case.
            context="dpcpp",
        ),
        LibraryTarget(
            "onedal_parameters",
            "lib/intel64/libonedal_parameters.so",
            public_headers=("cpp/oneapi/dal/detail/parameters.hpp",),
            include_roots=("cpp/oneapi",),
            context="host",
        ),
        LibraryTarget(
            "onedal_parameters_dpc",
            "lib/intel64/libonedal_parameters_dpc.so",
            public_headers=("cpp/oneapi/dal/detail/parameters.hpp",),
            include_roots=("cpp/oneapi",),
            context="dpcpp",
        ),
        LibraryTarget(
            "onedal_thread",
            "lib/intel64/libonedal_thread.so",
            in_l2_scope=False,
            out_of_scope_reason=(
                "no public API of its own -- an internal threading-layer shim. "
                "Pointing it at another library's headers would manufacture a "
                "sixth L2 case rather than measure one, so it is a recorded "
                "non-case, not a gap."
            ),
        ),
    ),
    prepare_commands=("git clone --filter=blob:none {repository} onedal.git",),
    per_side_commands=(
        "git -C onedal.git worktree add --detach {root} {revision}",
        "cd {root} && ./dev/download_micromkl.sh",
        ". /opt/intel/oneapi/setvars.sh && cd {root} && "
        "make -f makefile daal oneapi_c PLAT=lnx32e -j$(nproc)",
    ),
    required_tools=("git", "make", "g++"),
    # `icpx` gates only the DPC++ context. A host-only machine can still measure
    # the three host libraries, which is what this profile's notes describe.
    context_tools={"dpcpp": ("icpx",)},
    approx_build_minutes=120,
    approx_disk_gb=25,
    scenarios=("temporal_pr_base",),
    notes=(
        "Five header-bearing libraries across two header/compile contexts (host "
        "and DPC++). With no declarative L2 bundle capability, measured as five "
        "per-library L2 comparisons reported as one multi-library workload.",
        "Needs the Intel oneAPI DPC++ compiler (icpx) for the two *_dpc "
        "libraries; without it those two are individually BLOCKED while the "
        "three host libraries can still be measured -- a partial profile, "
        "reported as partial.",
    ),
)

#: The build recipe for one SVS runtime side, built from a git revision. The
#: runtime is a **separate CMake project** under ``bindings/cpp`` -- the
#: repository-root project does not add it as a subdirectory and declares no
#: ``SVS_BUILD_SHARED`` option, so configuring the root with such an option
#: builds no runtime library at all. Installed into that side's own prefix so
#: both kinds of side present the same distribution layout (``lib/`` +
#: ``include/svs/runtime/``) to the comparison.
_SVS_BUILD_SIDE = (
    "git -C svs.git worktree add --detach {src_root} {revision}",
    "cmake -S {src_root}/bindings/cpp -B {src_root}/build "
    "-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX={root}",
    "cmake --build {src_root}/build -j$(nproc)",
    "cmake --install {src_root}/build",
)

#: The runtime library as a *distribution* consumer sees it. Paths are relative
#: to a side's own distribution root, which is what both an installed build and
#: an extracted published release produce.
_SVS_RUNTIME_LIBRARY = LibraryTarget(
    "svs_runtime",
    "lib/libsvs_runtime.so",
    # The installed runtime header directory -- deliberately not the whole
    # include/ tree. SVS is largely header-only internally; scoping L2 to
    # everything under include/ would measure the source tree rather than the
    # runtime library's public contract. It is also NOT
    # ``include/svs/lib/runtime.h``, which does not exist in any SVS
    # distribution: bindings/cpp installs its PUBLIC_HEADER set into
    # ``include/svs/runtime``.
    public_headers=("include/svs/runtime",),
    include_roots=("include",),
    context="runtime",
)

SVS = RealProfile(
    id="svs",
    project="intel/ScalableVectorSearch",
    reference="https://github.com/intel/ScalableVectorSearch/pull/387",
    repository="https://github.com/intel/ScalableVectorSearch.git",
    # The integration's OWN baseline: the released v0.4.0 runtime distribution,
    # compared against PR #387's head. This is the comparison that exposed the
    # real ABI change, and it is not interchangeable with the PR-base smoke test
    # -- which is why that one is a separate profile (SVS_PR_BASE) rather than a
    # second scenario on this one. ``old_revision`` names the release tag
    # (75e30f222cf06bcc0625ed021d783cae2d75cfc5) purely as the identity of the
    # baseline being consumed; this profile does not rebuild it.
    old_revision="v0.4.0",
    new_revision="7058e9605a54180aa64fbb7a81a82aa47f07eeff",
    libraries=(_SVS_RUNTIME_LIBRARY,),
    prepare_commands=(
        "git clone --filter=blob:none {repository} svs.git",
        # PR #387's head is not on the default branch, so fetch it explicitly.
        "git -C svs.git fetch origin 'refs/pull/387/head:pr387'",
    ),
    per_side_commands=_SVS_BUILD_SIDE,
    # The old side is CONSUMED, not rebuilt: the published v0.4.0 runtime
    # distribution is the artifact consumers actually received, and rebuilding
    # the tag would measure this host's toolchain instead. The operator extracts
    # it into the side root; `missing_inputs` then fails loudly if it is absent,
    # which is the whole reason an empty prepared tree can no longer read as a
    # completed measurement.
    side_commands={"old": ()},
    side_sources={"old": "prebuilt_distribution"},
    required_tools=("git", "cmake", "g++"),
    approx_build_minutes=45,
    approx_disk_gb=6,
    scenarios=("temporal_release",),
    notes=(
        "One runtime shared library (lib/libsvs_runtime.so) with its installed "
        "public headers (include/svs/runtime). Scope is the published runtime "
        "headers, not the whole source/include tree.",
        "The runtime is a separate CMake project under bindings/cpp: "
        "`project(svs_runtime VERSION 0.4.0)` defines the shared-library target "
        "and installs PUBLIC_HEADER into include/svs/runtime. The repository-root "
        "project does not add that subproject and declares no SVS_BUILD_SHARED "
        "option, so a root-level configure produces no runtime library -- the "
        "inputs this profile originally declared (lib/libsvs_shared.so, "
        "include/svs/lib/runtime.h, a root build with -DSVS_BUILD_SHARED=ON) are "
        "absent from every real distribution and could never have been measured.",
        "The baseline is the RELEASED v0.4.0 runtime distribution, not the PR's "
        "merge base. A PR-base comparison is a useful additional smoke test and "
        "cannot replace this one; it lives in the separate SVS_PR_BASE profile.",
        "The runtime is C++20 (CXX_STANDARD 20, CXX_EXTENSIONS OFF); an L2 "
        "header parse of it must use the same dialect.",
    ),
)

SVS_PR_BASE = RealProfile(
    id="svs_pr_base",
    project="intel/ScalableVectorSearch",
    reference="https://github.com/intel/ScalableVectorSearch/pull/387",
    repository="https://github.com/intel/ScalableVectorSearch.git",
    # PR #387's merge base with main, and its head. Both real, verified
    # revisions. Deliberately a SEPARATE profile from the release-to-candidate
    # comparison above: the two answer different questions, and a single result
    # cannot mean both.
    old_revision="8052bd9f0f78b759cad2bc5168ab37c4f66f0670",
    new_revision="7058e9605a54180aa64fbb7a81a82aa47f07eeff",
    libraries=(_SVS_RUNTIME_LIBRARY,),
    prepare_commands=(
        "git clone --filter=blob:none {repository} svs.git",
        "git -C svs.git fetch origin 'refs/pull/387/head:pr387'",
    ),
    per_side_commands=_SVS_BUILD_SIDE,
    required_tools=("git", "cmake", "g++"),
    approx_build_minutes=90,
    approx_disk_gb=10,
    # Both sides are built here, under one controlled build contract, so the
    # rebuild-equivalence question is answerable from this profile's own
    # artifacts -- and answering it means INVESTIGATING a difference against the
    # recorded toolchain, not declaring it a false positive. See
    # SCENARIO_EXPECTATIONS.
    scenarios=("temporal_pr_base", "rebuild_equivalence"),
    notes=(
        "A smoke test, not the integration's gate: it compares PR #387's merge "
        "base against its head and therefore cannot expose an ABI change that "
        "entered the branch before that base. The release-to-candidate "
        "comparison (profile `svs`) is the one that did.",
        "Both sides are built from source here, so each takes a full runtime "
        "build -- roughly twice the `svs` profile's cost.",
    ),
)

PVXS = RealProfile(
    id="pvxs",
    project="epics-base/pvxs",
    reference="https://github.com/epics-base/pvxs/pull/216",
    repository="https://github.com/epics-base/pvxs.git",
    # Real, verified revisions: PR #216's head and its merge base with master.
    # This is the one profile of the three that has actually been prepared and
    # measured end to end (see the notes below for the numbers and the one
    # caveat they carry), so its revisions are concrete rather than placeholder.
    old_revision="9371e12391794a66520fc5c4aba87c26a6c6b628",
    new_revision="b8a557d",
    libraries=(
        LibraryTarget(
            "pvxs",
            "lib/linux-x86_64/libpvxs.so",
            public_headers=(
                "src/pvxs/data.h",
                "src/pvxs/client.h",
                "src/pvxs/server.h",
            ),
            include_roots=("src", "../epics-base/include"),
            context="core",
        ),
        LibraryTarget(
            "pvxsIoc",
            "lib/linux-x86_64/libpvxsIoc.so",
            # A deliberately different header set, including generated ones --
            # the side-specific public/support/generated split this profile
            # exists to cover.
            public_headers=("ioc/pvxs/iochooks.h",),
            include_roots=("ioc", "src", "../epics-base/include"),
            context="ioc",
        ),
    ),
    # This sequence is the one actually executed locally to produce the measured
    # PVXS numbers, not a plausible-looking reconstruction -- including the
    # libevent headers, discovered the hard way when EPICS base built fine and
    # pvxs then failed partway through on a missing event2/event.h.
    prepare_commands=(
        "git clone --depth 50 --branch 7.0 "
        "https://github.com/epics-base/epics-base.git epics-base",
        "cd epics-base && make -j$(nproc)",
        "git clone --filter=blob:none {repository} pvxs.git",
        # PR #216's head is a branch ref, so fetch it explicitly: a plain clone
        # of the default branch does not contain it.
        "git -C pvxs.git fetch origin 'refs/pull/216/head:pr216'",
    ),
    per_side_commands=(
        "git -C pvxs.git worktree add --detach {root} {revision}",
        'printf "EPICS_BASE=%s/epics-base\\n" "$ROOT" > {root}/configure/RELEASE.local',
        "make -C {root} -j$(nproc)",
    ),
    # libevent's development headers are a real prerequisite, not an optional
    # extra: EPICS base builds fine without them and pvxs then fails partway
    # through its own build on a missing event2/event.h.
    system_packages=("libevent-dev",),
    required_tools=("git", "make", "g++", "perl"),
    approx_build_minutes=30,
    approx_disk_gb=4,
    scenarios=("temporal_pr_base",),
    notes=(
        "Two libraries with side-specific public/support/generated headers and "
        "two EPICS include roots -- the multi-context case at the smallest "
        "buildable scale of the three profiles.",
        "Must NOT be measured by running the project's own script with --depth "
        "source: that is an L4/L5 measurement and does not belong in an L2 "
        "profile's numbers.",
        "MEASURED once locally (2026-09-12, gcc 13.3.0 / castxml 0.7.0 / "
        "clang 18.1.3, 4 CPUs): libpvxs 166.5s wall / 160.1s user CPU / "
        "1949MB sampled peak concurrent process-tree RSS (4 concurrent "
        "processes) / 4 header extractions + 6 include passes; libpvxsIoc "
        "16.3s / 15.6s / 438MB / 4 extractions + 2 include passes. Both sides "
        "resolved to depth=headers with assurance complete and public scoping "
        "applied. The ~10x spread between the two libraries is the size of "
        "their public header surfaces (client.h+data.h+server.h vs. one "
        "iochooks.h), not a defect.",
        "CAVEAT on that measurement, and the reason it is not a temporal "
        "result: the two revisions' public header trees digest IDENTICALLY, "
        "because PR #216 is a CI-only change. So the run is in practice an "
        "equivalence comparison (same headers, two builds), not a "
        "header-changing temporal one -- which is exactly why "
        "validate_side_headers exists and why a digest is recorded. A temporal "
        "PVXS measurement needs a revision pair that actually changes a public "
        "header.",
        "Preparation needs libevent development headers (event2/event.h) in "
        "addition to the tools listed -- a missing system dependency, found the "
        "hard way: the first build failed on it after EPICS base had already "
        "built successfully. Recorded in system_packages so a reader does not "
        "rediscover it the same way.",
        "The prepare sequence above is the one actually executed to produce the "
        "measured numbers, re-expressed through per_side_commands so it builds "
        "BOTH revisions. The original version checked out only old_revision -- "
        "the two sides were built by hand, which is exactly the gap that made it "
        "possible to ship a temporal profile no script could prepare.",
    ),
)

PROFILES: dict[str, RealProfile] = {p.id: p for p in (ONEDAL, SVS, SVS_PR_BASE, PVXS)}


#: A revision that is a placeholder rather than an immutable SHA. Rendered into
#: ``git checkout <pinned-base-sha>``, the shell reads ``<`` as input redirection
#: and fails before git ever runs -- while the profile still validated as
#: "structurally valid", which is exactly the kind of silent unrunnability this
#: module exists to prevent. A placeholder is now both a validation finding and a
#: BLOCKED status.
_PLACEHOLDER_REVISION_MARKERS = ("<", ">", "pinned-", "TODO", "FIXME")


def is_placeholder_revision(revision: str) -> bool:
    """True when *revision* is not a usable git revision.

    Deliberately a shape test rather than a git lookup: this module never touches
    the network, and a profile whose revision cannot even survive shell quoting is
    unrunnable regardless of what any remote holds.
    """
    return not revision.strip() or any(
        marker in revision for marker in _PLACEHOLDER_REVISION_MARKERS
    )


def validate_profile(profile: RealProfile) -> list[str]:
    """Structural problems with a profile definition, as messages.

    Checks the invariants that make a profile's *numbers* trustworthy rather
    than merely present -- each one corresponds to a way a real measurement can
    be silently wrong:

    * an out-of-scope library must state why (otherwise a genuine gap and a
      reviewed non-case are indistinguishable);
    * an in-scope library must name at least one public header (an L2 case with
      no headers is a binary-only case mislabelled);
    * the two revisions must differ for a temporal scenario (comparing a
      revision with itself is an equivalence check, not a temporal one);
    * a profile claiming more than one header context must really have more
      than one.
    """
    problems: list[str] = []
    for lib in profile.libraries:
        if lib.in_l2_scope and not lib.public_headers:
            problems.append(
                f"{profile.id}/{lib.name}: in L2 scope but names no public header "
                "-- that is a binary-only case mislabelled as an L2 one"
            )
        if not lib.in_l2_scope:
            if lib.out_of_scope_reason is None:
                problems.append(
                    f"{profile.id}/{lib.name}: excluded from L2 scope without a "
                    "stated reason -- an unreviewed exclusion is a gap, not a "
                    "recorded non-case"
                )
            if lib.public_headers:
                problems.append(
                    f"{profile.id}/{lib.name}: excluded from L2 scope yet names "
                    "public headers"
                )
    if (
        any(scenario in TEMPORAL_SCENARIOS for scenario in profile.scenarios)
        and profile.old_revision == profile.new_revision
    ):
        problems.append(
            f"{profile.id}: a temporal scenario needs two different revisions"
        )
    for scenario in profile.scenarios:
        if scenario not in SCENARIO_EXPECTATIONS:
            problems.append(
                f"{profile.id}: scenario {scenario!r} states no expectation -- a "
                "scenario whose findings have no declared meaning is how correct "
                "detection comes to be read as a scanner defect (see "
                "SCENARIO_EXPECTATIONS)"
            )
    for side in ("old", "new"):
        source = profile.source_for_side(side)
        if source not in SIDE_SOURCES:
            problems.append(
                f"{profile.id}: {side} side declares unknown source {source!r}"
            )
            continue
        commands = profile.commands_for_side(side)
        if source == "build_from_revision" and not commands:
            problems.append(
                f"{profile.id}: {side} side is built from a revision but no "
                "commands produce it, so that side can never exist"
            )
        if source == "prebuilt_distribution" and commands:
            problems.append(
                f"{profile.id}: {side} side is declared a prebuilt distribution "
                "yet carries build commands -- rebuilding a published artifact "
                "measures the rebuild, not the artifact consumers received"
            )
    for side in profile.side_commands:
        if side not in ("old", "new"):
            problems.append(
                f"{profile.id}: side_commands names {side!r}, which is not a side"
            )
    for side in profile.side_sources:
        if side not in ("old", "new"):
            problems.append(
                f"{profile.id}: side_sources names {side!r}, which is not a side"
            )
    for side, revision in (
        ("old", profile.old_revision),
        ("new", profile.new_revision),
    ):
        if is_placeholder_revision(revision):
            problems.append(
                f"{profile.id}: {side}_revision {revision!r} is a placeholder, not a "
                "usable revision -- prepare_script() would render it into a command "
                "the shell cannot even parse, while this profile still read as valid"
            )
    if not profile.scenarios:
        problems.append(
            f"{profile.id}: declares no scenario, so its findings carry no "
            "stated expectation (see SCENARIO_EXPECTATIONS)"
        )
    if not profile.l2_libraries:
        problems.append(f"{profile.id}: no library is in L2 scope")
    if not profile.required_tools:
        problems.append(
            f"{profile.id}: states no required tools, so availability "
            "cannot be checked and it could never report BLOCKED"
        )
    return problems


def validate_side_headers(*, old_header_root: Path, new_header_root: Path) -> list[str]:
    """Reject a plan that sources both sides' headers from one revision.

    The substitution this guards against is subtle and easy to make
    accidentally: checking out the new revision, building both binaries, and
    pointing both ``--header`` sets at the working tree. The run succeeds, is
    faster, and measures something real -- just not a temporal L2 comparison,
    because the historical side's declarations came from the present.
    """
    problems = []
    if old_header_root.resolve() == new_header_root.resolve():
        problems.append(
            "both sides' headers resolve to the same root: the historical side "
            "must come from its own revision, or this is not a temporal L2 "
            "comparison"
        )
    return problems


def digest_tree(root: Path, patterns: tuple[str, ...] = ("*.h", "*.hpp")) -> str:
    """A content digest over a profile's pinned header inputs.

    Used to verify that a prepared profile really is the pinned one: a silent
    upstream change, a partially-applied checkout, or a stale cached tree all
    produce a different digest, and all three would otherwise publish a number
    against a revision it was not taken at.
    """
    h = hashlib.sha256()
    files = sorted(
        (p for pattern in patterns for p in root.rglob(pattern) if p.is_file()),
        key=lambda p: str(p.relative_to(root)),
    )
    for path in files:
        h.update(str(path.relative_to(root)).encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"


def header_evidence_missing(path: Path) -> str | None:
    """Why *path* is not usable header evidence, or ``None`` when it is.

    A declared public header is either a file or a header *root* directory, and
    a directory needs the same treatment this module's whole status vocabulary
    exists to enforce: its existence says nothing about its contents. An empty
    ``include/svs/runtime`` -- a partial extraction, an interrupted install, a
    distribution whose layout moved -- satisfied a bare ``exists()`` check, so a
    run with no header evidence at all could reach the point of appearing to
    have completed an L2 comparison. That is the same defect as an empty
    prepared tree reading as measured, one level down.
    """
    if not path.exists():
        return "absent"
    if path.is_dir():
        if not any(
            child.is_file() and child.suffix in HEADER_SUFFIXES
            for child in path.rglob("*")
        ):
            return "header root contains no header file"
        return None
    if not path.is_file():
        return "neither a file nor a directory"
    return None


def missing_inputs(
    profile: RealProfile,
    prepared_root: Path,
    *,
    libraries: tuple[LibraryTarget, ...] | None = None,
) -> list[str]:
    """Concrete operands this profile needs that are absent under *prepared_root*.

    The gap this closes: readiness used to be answered by ``prepared_root`` being
    a directory, full stop. An EMPTY directory therefore satisfied it -- no
    library on either side, no headers on either side, no comparison -- and the
    resolver reported the profile as measured. Existence of a directory is not
    evidence of anything; the operands are.

    Both sides are checked, and both halves of each library's L2 input: the
    binary artifact and every declared public header, which must be real header
    *evidence* rather than a path that merely exists (see
    :func:`header_evidence_missing`). A missing historical side is exactly as
    disqualifying as a missing candidate one -- a comparison needs both.

    *libraries* narrows the check to a subset, which is how a caller asks about
    the libraries this host can actually build. Defaulting to all of them keeps
    this function's own contract ("everything the profile declares") intact for
    a caller that has not resolved a context split.
    """
    targets = profile.l2_libraries if libraries is None else libraries
    missing: list[str] = []
    for side in ("old", "new"):
        root = profile.side_root(prepared_root, side)
        if not root.is_dir():
            missing.append(f"{side}: {root} (no operand tree for this side)")
            continue
        for lib in targets:
            artifact = root / lib.artifact
            if not artifact.is_file():
                missing.append(f"{side}/{lib.name}: {lib.artifact} (library)")
            for header in lib.public_headers:
                problem = header_evidence_missing(root / header)
                if problem is not None:
                    missing.append(
                        f"{side}/{lib.name}: {header} (public header: {problem})"
                    )
    return missing


def missing_tools(profile: RealProfile) -> list[str]:
    return [tool for tool in profile.required_tools if shutil.which(tool) is None]


def blocked_contexts(profile: RealProfile) -> dict[str, list[str]]:
    """Each header/compile context this host cannot build, and what it is missing.

    Per context, not per profile: a tool that only one context needs must not
    block the contexts that do not need it (see ``RealProfile.context_tools``).
    """
    blocked: dict[str, list[str]] = {}
    for context, tools in profile.context_tools.items():
        absent = [tool for tool in tools if shutil.which(tool) is None]
        if absent:
            blocked[context] = absent
    return blocked


def measurable_libraries(profile: RealProfile) -> tuple[LibraryTarget, ...]:
    """The in-scope libraries whose own context can actually be built here."""
    unavailable = set(blocked_contexts(profile))
    return tuple(lib for lib in profile.l2_libraries if lib.context not in unavailable)


def identity_tools(profile: RealProfile) -> tuple[str, ...]:
    """Every tool whose version is part of this profile's measurement identity.

    The union of profile-wide `required_tools` and every `context_tools` entry.
    Recording only the profile-wide set omitted `icpx` -- the compiler oneDAL's
    DPC++ libraries are built with -- so two measurements taken with different
    DPC++ compilers recorded identical toolchains, which defeats the one thing
    the field is for (Codex review). Order is stable and deduplicated so a
    receipt diff is readable.
    """
    seen: list[str] = list(profile.required_tools)
    for tools in profile.context_tools.values():
        seen.extend(tool for tool in tools if tool not in seen)
    return tuple(seen)


def toolchain_identity(profile: RealProfile) -> dict[str, str | None]:
    """Resolved version of every tool in this profile's measurement identity.

    Two measurements taken with different compilers are not comparable, and a
    profile that records only "g++" cannot tell a reader which. Captured outside
    every timed window. Covers `identity_tools`, not just `required_tools`: a
    context-specific compiler is exactly as identity-bearing as a profile-wide
    one.
    """
    identity: dict[str, str | None] = {}
    for tool in identity_tools(profile):
        path = shutil.which(tool)
        if path is None:
            identity[tool] = None
            continue
        try:
            proc = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=60,
                # A probe's non-zero exit is information, not an error.
                check=False,
            )
            text = (proc.stdout or proc.stderr).strip()
            identity[tool] = text.splitlines()[0] if text else None
        except (OSError, subprocess.SubprocessError):
            identity[tool] = None
    return identity


@dataclass
class ProfileStatus:
    """A profile's availability verdict, with a concrete reason when negative."""

    profile_id: str
    status: str
    reason: str | None = None
    missing_tools: list[str] = field(default_factory=list)
    toolchain: dict[str, str | None] = field(default_factory=dict)
    #: Under ``PARTIAL``: which in-scope libraries this host can and cannot
    #: measure, and what each blocked context is missing. Empty otherwise.
    measurable_libraries: list[str] = field(default_factory=list)
    blocked_libraries: dict[str, list[str]] = field(default_factory=dict)
    #: Operands the profile needs that are not present on disk -- see
    #: :func:`missing_inputs`. Non-empty only for a ``BLOCKED`` status.
    missing_inputs: list[str] = field(default_factory=list)
    #: Under ``MEASURED`` only: what was actually measured. A ``MEASURED``
    #: status with no measurement behind it is unconstructible -- see
    #: :func:`promote_to_measured`.
    measurement: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile_id,
            "status": self.status,
            "reason": self.reason,
            "missing_tools": self.missing_tools,
            "missing_inputs": self.missing_inputs,
            "toolchain": self.toolchain,
            "measurable_libraries": self.measurable_libraries,
            "blocked_libraries": self.blocked_libraries,
            "measurement": self.measurement,
        }


def _partial_status(profile: RealProfile) -> ProfileStatus | None:
    """``PARTIAL`` when some contexts are unbuildable but others still are.

    Returns ``None`` when every context is available (the ordinary case) and a
    ``BLOCKED`` status when *no* in-scope library survives -- a profile whose
    every context is missing a tool is blocked for a concrete reason, not
    partially measurable.

    This exists because a profile-wide tool list collapsed a real distinction: a
    host without ``icpx`` cannot build oneDAL's two DPC++ libraries and can build
    its three host ones, and reporting the whole profile ``BLOCKED`` both
    contradicted the profile's own documented behaviour and hid usable coverage
    from the availability artifact. The status vocabulary exists to tell apart
    "this host cannot", "nobody asked", and now "this host can do part of it" --
    collapsing the third into the first is the same class of dishonesty as
    substituting a synthetic number.
    """
    blocked = blocked_contexts(profile)
    if not blocked:
        return None
    measurable = measurable_libraries(profile)
    blocked_libs = {
        lib.name: blocked[lib.context]
        for lib in profile.l2_libraries
        if lib.context in blocked
    }
    if not measurable:
        return ProfileStatus(
            profile.id,
            "BLOCKED",
            reason=(
                f"every in-scope library's context is unbuildable here: {blocked} "
                "-- no part of this profile can be measured"
            ),
            missing_tools=sorted(
                {tool for tools in blocked.values() for tool in tools}
            ),
            blocked_libraries=blocked_libs,
        )
    return ProfileStatus(
        profile.id,
        "PARTIAL",
        reason=(
            f"{len(measurable)} of {len(profile.l2_libraries)} in-scope "
            f"librar(ies) are measurable here; context(s) {sorted(blocked)} are "
            f"missing {blocked} so their librar(ies) are not"
        ),
        missing_tools=sorted({tool for tools in blocked.values() for tool in tools}),
        toolchain=toolchain_identity(profile),
        measurable_libraries=[lib.name for lib in measurable],
        blocked_libraries=blocked_libs,
    )


def resolve_status(
    profile: RealProfile, *, prepared_root: Path | None, requested: bool
) -> ProfileStatus:
    """Decide whether *profile* is READY to be measured here, and say why if not.

    This answers a **readiness** question only, and so its positive outcomes are
    ``READY`` and ``PARTIAL`` -- never ``MEASURED``. It returned ``MEASURED``
    once, for any request whose ``prepared_root`` merely *existed*: an empty
    directory, with neither side's library, neither side's headers, and no
    comparison ever run, resolved to "measured" with no reason recorded. A
    status asserting that a timed operation completed cannot be derived from
    preconditions; :func:`promote_to_measured` is the only way to reach it, and
    it requires the completed measurement itself.

    Never returns a substitute. The outcomes are ``READY``, ``PARTIAL`` (some
    header/compile contexts are unbuildable here and others are -- see
    :func:`_partial_status`), ``BLOCKED`` with a reason, or ``NOT_RUN`` because
    nobody asked; and the reason text is the deliverable for the negative ones,
    since a profile reported as merely "skipped" tells a reader nothing about
    whether the coverage gap is environmental or a decision.
    """
    if not requested:
        return ProfileStatus(
            profile.profile_id if hasattr(profile, "profile_id") else profile.id,
            "NOT_RUN",
            reason="not selected by this run (real profiles are periodic/manual only)",
        )
    unpinned = [
        f"{side}_revision={revision!r}"
        for side, revision in (
            ("old", profile.old_revision),
            ("new", profile.new_revision),
        )
        if is_placeholder_revision(revision)
    ]
    if unpinned:
        # Checked before the tool probe: a profile whose revisions are not pinned
        # cannot be measured however complete the toolchain is, and reporting
        # "missing icpx" for it would name the wrong blocker.
        return ProfileStatus(
            profile.id,
            "BLOCKED",
            reason=(
                f"revisions are not pinned ({', '.join(unpinned)}) -- a prepare "
                "script cannot be rendered from a placeholder, so nothing can be "
                "built or measured"
            ),
        )
    absent = missing_tools(profile)
    if absent:
        return ProfileStatus(
            profile.id,
            "BLOCKED",
            reason=(
                f"required tool(s) {absent} not on PATH; this profile needs roughly "
                f"{profile.approx_build_minutes} build-minutes and "
                f"{profile.approx_disk_gb}GB of disk"
            ),
            missing_tools=absent,
        )
    # The per-context check runs AFTER the prepared-tree check, by the same rule
    # the placeholder check follows before the tool probe: name the blocker the
    # caller actually hits first. A host with the right compilers but no prepared
    # tree is blocked on the tree, and reporting `PARTIAL` for it would describe a
    # coverage split that nothing could act on yet.
    if prepared_root is None or not prepared_root.is_dir():
        return ProfileStatus(
            profile.id,
            "BLOCKED",
            reason=(
                "no prepared build tree: run the profile's prepare_commands first "
                "(they are setup, excluded from every measured window)"
            ),
            toolchain=toolchain_identity(profile),
        )
    # The context split is resolved BEFORE the operand check, and the operand
    # check then asks only about the libraries this host can actually build.
    # The first version checked every declared library unconditionally, which
    # broke the documented partial path in the one case it exists for: a host
    # without `icpx` cannot build oneDAL's two DPC++ libraries, so a
    # legitimately prepared host-only tree does not contain their artifacts --
    # and reporting BLOCKED for that absence made PARTIAL reachable only when
    # the already-unmeasurable operands happened to be present anyway (Codex
    # review). A context this host cannot build is reported as a context
    # blocker, which is what it is; its operands are not also demanded.
    partial = _partial_status(profile)
    if partial is not None and partial.status == "BLOCKED":
        # No context is buildable: name that, not the operands it implies.
        return partial
    buildable = measurable_libraries(profile)
    absent_inputs = missing_inputs(profile, prepared_root, libraries=buildable)
    if absent_inputs:
        return ProfileStatus(
            profile.id,
            "BLOCKED",
            reason=(
                f"{len(absent_inputs)} declared operand(s) are absent under "
                f"{prepared_root}: a prepared directory is not evidence that "
                "either side's library or headers exist"
            ),
            toolchain=toolchain_identity(profile),
            missing_inputs=absent_inputs,
            measurable_libraries=[lib.name for lib in buildable],
            blocked_libraries=dict(partial.blocked_libraries) if partial else {},
        )
    if partial is not None:
        return partial
    return ProfileStatus(
        profile.id,
        "READY",
        reason=(
            "preconditions satisfied: tools, pinned revisions and both sides' "
            "operands are present. Nothing has been measured yet"
        ),
        toolchain=toolchain_identity(profile),
        measurable_libraries=[lib.name for lib in buildable],
    )


@dataclass(frozen=True)
class MeasurementResult:
    """The output of one completed, timed measurement of a profile."""

    profile_id: str
    #: The in-scope libraries this measurement actually covered.
    libraries: tuple[str, ...]
    #: Wall-clock duration of the timed window. Must be positive: a measurement
    #: that took no time did not happen.
    wall_seconds: float
    #: Files the measurement produced (reports, receipts). Each must exist.
    output_paths: tuple[Path, ...] = ()
    #: Measurable libraries this run deliberately did NOT cover, each with its
    #: reason. Every measurable library must appear in `libraries` or here --
    #: see :func:`promote_to_measured` for why an unaccounted omission is the
    #: same substitution this module exists to prevent.
    omitted_libraries: dict[str, str] = field(default_factory=dict)
    #: Free-text detail carried into the status for a reader.
    detail: str | None = None


def promote_to_measured(
    status: ProfileStatus, result: MeasurementResult
) -> ProfileStatus:
    """Turn a ``READY``/``PARTIAL`` status into ``MEASURED``, given the result.

    The one route to ``MEASURED``, and it is deliberately impossible to take
    without a completed measurement in hand. Every rejection here corresponds to
    a way the old resolver could publish the word without one:

    * a status that was never ready (``BLOCKED``/``NOT_RUN``) cannot become
      measured by assertion;
    * a result covering no library measured nothing;
    * a result that leaves a measurable library neither measured nor explicitly
      omitted would republish a subset as the profile's whole scope;
    * a non-positive duration is not a timed window;
    * an output path that does not exist is an unvalidated output.

    A ``PARTIAL`` status may be promoted, but only over the libraries it said
    were measurable -- promoting it over a blocked library would republish the
    coverage gap ``PARTIAL`` exists to expose.
    """
    if status.status not in ("READY", "PARTIAL"):
        raise ValueError(
            f"{status.profile_id}: cannot promote a {status.status} status to "
            "MEASURED -- only a ready profile can have been measured"
        )
    if result.profile_id != status.profile_id:
        raise ValueError(
            f"measurement for {result.profile_id!r} cannot promote the status of "
            f"{status.profile_id!r}"
        )
    if not result.libraries:
        raise ValueError(
            f"{status.profile_id}: a measurement covering no library measured "
            "nothing; MEASURED would be a claim about an empty run"
        )
    if status.measurable_libraries:
        measurable = set(status.measurable_libraries)
        unexpected = sorted(
            (set(result.libraries) | set(result.omitted_libraries)) - measurable
        )
        if unexpected:
            raise ValueError(
                f"{status.profile_id}: measurement claims librar(ies) "
                f"{unexpected} that this host reported unmeasurable"
            )
        # Every measurable library is measured or explicitly accounted for.
        # Checking only for *unknown* libraries let a five-library oneDAL
        # profile be promoted to MEASURED by a result naming one of them, and
        # the promoted status then silently republished that subset as the
        # profile's scope -- "we measured oneDAL" standing for a fifth of it
        # (Codex review). That is the same substitution as publishing a
        # synthetic number under a real project's name, so an omission is
        # allowed only when it is stated, with a reason, and stays visible in
        # the receipt.
        unaccounted = sorted(
            measurable - set(result.libraries) - set(result.omitted_libraries)
        )
        if unaccounted:
            raise ValueError(
                f"{status.profile_id}: measurable librar(ies) {unaccounted} were "
                "neither measured nor recorded as omitted; MEASURED would "
                "republish a subset as the profile's full scope"
            )
        overlap = sorted(set(result.libraries) & set(result.omitted_libraries))
        if overlap:
            raise ValueError(
                f"{status.profile_id}: librar(ies) {overlap} are recorded as both "
                "measured and omitted"
            )
    unexplained = sorted(
        name
        for name, reason in result.omitted_libraries.items()
        if not str(reason).strip()
    )
    if unexplained:
        raise ValueError(
            f"{status.profile_id}: omitted librar(ies) {unexplained} state no "
            "reason -- an unexplained omission is a coverage gap wearing a "
            "completed-measurement label"
        )
    if not result.wall_seconds > 0:
        raise ValueError(
            f"{status.profile_id}: wall_seconds={result.wall_seconds!r} is not a "
            "timed window, so nothing was measured"
        )
    absent = [str(path) for path in result.output_paths if not Path(path).exists()]
    if absent:
        raise ValueError(
            f"{status.profile_id}: measurement output(s) {absent} do not exist, "
            "so the result is unvalidated"
        )
    return ProfileStatus(
        status.profile_id,
        "MEASURED",
        reason=result.detail
        or (
            f"measured {len(result.libraries)} librar(ies) in "
            f"{result.wall_seconds:.1f}s"
        ),
        missing_tools=list(status.missing_tools),
        toolchain=dict(status.toolchain),
        measurable_libraries=list(result.libraries),
        blocked_libraries=dict(status.blocked_libraries),
        measurement={
            "libraries": list(result.libraries),
            "omitted_libraries": dict(result.omitted_libraries),
            "wall_seconds": result.wall_seconds,
            "outputs": [str(path) for path in result.output_paths],
            "promoted_from": status.status,
        },
    )


def prepare_script(profile: RealProfile) -> str:
    """The profile's prepare commands as one reproducible shell script.

    Two properties the first version of this got wrong, both found by review:

    * **Each command runs in its own subshell.** Appending ``cd svs && git
      checkout ...`` and then ``cd svs && cmake ...`` into one shell leaves the
      process inside ``svs``, so the second looks for ``svs/svs`` and fails. Every
      profile used that repeated-``cd`` shape, so no generated script could
      complete even its old-side build. A subshell per command means each one
      starts from the same root, which is also the only reading under which the
      command list is order-independent enough to be edited safely.
    * **Both revisions are prepared.** A temporal profile compares an old
      artifact against a new one, and a script that checks out only
      ``old_revision`` cannot produce the pair it advertises.
      ``{side}``/``{revision}`` are substituted per side over
      ``per_side_commands``, and ``{root}`` names that side's own tree.
    """
    if any(
        is_placeholder_revision(r) for r in (profile.old_revision, profile.new_revision)
    ):
        raise ValueError(
            f"{profile.id}: refusing to render a prepare script with placeholder "
            "revisions -- it would emit commands the shell cannot parse"
        )
    built_sides = ", ".join(
        side
        for side in ("old", "new")
        if profile.source_for_side(side) == "build_from_revision"
    )
    lines = [
        "#!/usr/bin/env bash",
        "# Generated from scripts/l2_real_profiles.py -- do not hand-edit.",
        f"# Profile: {profile.id} ({profile.project})",
        f"# Reference: {profile.reference}",
        f"# Approx cost: {profile.approx_build_minutes} build-minutes, "
        f"{profile.approx_disk_gb}GB disk (covering the "
        f"{built_sides if built_sides else 'no'} side(s) built here -- a temporal "
        "comparison needs two operands, whether built or supplied).",
        "# Everything here is SETUP: it is excluded from every measured window.",
        "set -euo pipefail",
        'ROOT="$(pwd)"',
    ]
    if profile.system_packages:
        lines.append(
            "# System packages this build needs beyond the tools on PATH: "
            + " ".join(profile.system_packages)
        )
    for command in profile.prepare_commands:
        # Subshell per command: see the docstring. Each starts at $ROOT, so a
        # `cd` inside one cannot leak into the next.
        lines.append(
            '( cd "$ROOT" && '
            + command.format(
                repository=profile.repository,
                old_revision=profile.old_revision,
                new_revision=profile.new_revision,
            )
            + " )"
        )
    for side, revision in (
        ("old", profile.old_revision),
        ("new", profile.new_revision),
    ):
        side_root = f'"$ROOT"/{profile.id}_{side}'
        commands = profile.commands_for_side(side)
        if profile.source_for_side(side) == "prebuilt_distribution":
            # A supplied side is not built, and the script must say so loudly
            # rather than silently produce nothing: an absent distribution is
            # the exact condition that used to read as a completed measurement.
            lines.extend(
                [
                    f"# {side} side: PREBUILT DISTRIBUTION ({revision}).",
                    f"# Extract the published distribution into {side_root} so "
                    "that its lib/ and include/ trees sit directly under it.",
                    "# It is NOT rebuilt here: rebuilding a release measures "
                    "this host's toolchain, not the artifact consumers received.",
                    f"if [ ! -d {side_root} ]; then echo 'missing {side} side: "
                    f"extract the {profile.project} {revision} distribution into "
                    f"{profile.id}_{side}' >&2; exit 1; fi",
                ]
            )
            continue
        for command in commands:
            lines.append(
                '( cd "$ROOT" && '
                + command.format(
                    repository=profile.repository,
                    side=side,
                    revision=revision,
                    # ABSOLUTE, via $ROOT. `git -C <repo> worktree add <path>`
                    # resolves a RELATIVE path against the repo directory, not
                    # the caller's cwd -- reproduced with the installed git:
                    # `git -C repo.git worktree add mytree` creates
                    # `repo.git/mytree`. Every following command then looks for
                    # `$ROOT/<root>` and fails. The subshell isolation added
                    # earlier is what exposed this: before it, the leaked cwd
                    # happened to mask it.
                    root=side_root,
                    # The side's own checkout, kept distinct from its
                    # distribution root: a profile that installs into the
                    # operand tree must not also check out over it.
                    src_root=f"{side_root}_src",
                )
                + " )"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover - a reporting convenience
    import json

    report = []
    for entry in PROFILES.values():
        report.append(
            {
                "id": entry.id,
                "project": entry.project,
                "reference": entry.reference,
                "l2_libraries": [lib.name for lib in entry.l2_libraries],
                "excluded": {
                    lib.name: lib.out_of_scope_reason
                    for lib in entry.libraries
                    if not lib.in_l2_scope
                },
                "header_contexts": entry.header_contexts,
                "scenarios": {
                    scenario: SCENARIO_EXPECTATIONS.get(scenario)
                    for scenario in entry.scenarios
                },
                "sides": {
                    side: {
                        "revision": entry.revision_for_side(side),
                        "source": entry.source_for_side(side),
                    }
                    for side in ("old", "new")
                },
                "validation": validate_profile(entry),
                "status": resolve_status(
                    entry, prepared_root=None, requested=True
                ).as_dict(),
            }
        )
    print(json.dumps(report, indent=2))
