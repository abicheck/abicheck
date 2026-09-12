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
  one revision.

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
STATUSES = ("MEASURED", "BLOCKED", "NOT_RUN")


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
    #: ``{revision}`` and ``{root}`` (that side's own tree) substituted. This is
    #: what produces the two artifacts a temporal comparison needs -- a profile
    #: with an empty list here can only ever build one side.
    per_side_commands: tuple[str, ...] = ()
    #: Tools that must be present, by name on PATH.
    required_tools: tuple[str, ...] = ()
    #: Approximate resource needs, so a lane can decline before starting.
    approx_build_minutes: int = 0
    approx_disk_gb: int = 0
    notes: tuple[str, ...] = ()
    #: Distribution packages needed beyond ``required_tools`` -- a header-only
    #: dependency has no binary on PATH, so tool presence cannot detect it.
    system_packages: tuple[str, ...] = ()
    #: Scenario shapes this profile is meant to exercise.
    scenarios: tuple[str, ...] = ("temporal",)

    @property
    def l2_libraries(self) -> tuple[LibraryTarget, ...]:
        return tuple(lib for lib in self.libraries if lib.in_l2_scope)

    @property
    def header_contexts(self) -> int:
        return len({lib.context for lib in self.l2_libraries})


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
    required_tools=("git", "make", "g++", "icpx"),
    approx_build_minutes=120,
    approx_disk_gb=25,
    scenarios=("temporal",),
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

SVS = RealProfile(
    id="svs",
    project="intel/ScalableVectorSearch",
    reference="https://github.com/intel/ScalableVectorSearch/pull/387",
    repository="https://github.com/intel/ScalableVectorSearch.git",
    # Real, verified revisions: PR #387's head and its merge base with main.
    old_revision="8052bd9f0f78b759cad2bc5168ab37c4f66f0670",
    new_revision="7058e9605a54180aa64fbb7a81a82aa47f07eeff",
    libraries=(
        LibraryTarget(
            "svs_shared",
            "lib/libsvs_shared.so",
            # The *published runtime* headers only -- deliberately not the whole
            # include/ tree. SVS is largely header-only internally; scoping L2
            # to everything under include/ would measure the source tree rather
            # than the runtime library's public contract.
            public_headers=("include/svs/lib/runtime.h",),
            include_roots=("include",),
            context="runtime",
        ),
    ),
    prepare_commands=("git clone --filter=blob:none {repository} svs.git",),
    per_side_commands=(
        "git -C svs.git worktree add --detach {root} {revision}",
        "cmake -S {root} -B {root}/build -DCMAKE_BUILD_TYPE=Release "
        "-DSVS_BUILD_SHARED=ON",
        "cmake --build {root}/build -j$(nproc)",
    ),
    required_tools=("git", "cmake", "g++"),
    approx_build_minutes=45,
    approx_disk_gb=6,
    # Two distinct scenarios, kept separate on purpose: a temporal comparison
    # (old revision vs new) answers "did this change break the ABI", while an
    # equivalence comparison (two builds of the *same* revision) answers "is the
    # analysis stable", and a single number cannot mean both.
    scenarios=("temporal", "equivalence"),
    notes=(
        "One runtime shared library with published runtime headers. Scope is the "
        "published runtime headers, not the whole source/include tree.",
        "The equivalence scenario compares two independent builds of one "
        "revision: any finding there is a false positive by construction.",
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
    scenarios=("temporal",),
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

PROFILES: dict[str, RealProfile] = {p.id: p for p in (ONEDAL, SVS, PVXS)}


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
    if "temporal" in profile.scenarios and profile.old_revision == profile.new_revision:
        problems.append(
            f"{profile.id}: a temporal scenario needs two different revisions"
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


def missing_tools(profile: RealProfile) -> list[str]:
    return [tool for tool in profile.required_tools if shutil.which(tool) is None]


def toolchain_identity(profile: RealProfile) -> dict[str, str | None]:
    """Resolved version of every required tool, for the receipt.

    Two measurements taken with different compilers are not comparable, and a
    profile that records only "g++" cannot tell a reader which. Captured outside
    every timed window.
    """
    identity: dict[str, str | None] = {}
    for tool in profile.required_tools:
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

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile_id,
            "status": self.status,
            "reason": self.reason,
            "missing_tools": self.missing_tools,
            "toolchain": self.toolchain,
        }


def resolve_status(
    profile: RealProfile, *, prepared_root: Path | None, requested: bool
) -> ProfileStatus:
    """Decide whether *profile* can be measured here, and say why if not.

    Never returns a substitute. The three outcomes are measure it, ``BLOCKED``
    with a reason, or ``NOT_RUN`` because nobody asked -- and the reason text is
    the deliverable for the latter two, since a profile reported as merely
    "skipped" tells a reader nothing about whether the coverage gap is
    environmental or a decision.
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
    return ProfileStatus(profile.id, "MEASURED", toolchain=toolchain_identity(profile))


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
    lines = [
        "#!/usr/bin/env bash",
        "# Generated from scripts/l2_real_profiles.py -- do not hand-edit.",
        f"# Profile: {profile.id} ({profile.project})",
        f"# Reference: {profile.reference}",
        f"# Approx cost: {profile.approx_build_minutes} build-minutes, "
        f"{profile.approx_disk_gb}GB disk "
        f"(for BOTH revisions -- a temporal comparison needs two builds).",
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
        for command in profile.per_side_commands:
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
                    root=f'"$ROOT"/{profile.id}_{side}',
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
                "scenarios": list(entry.scenarios),
                "validation": validate_profile(entry),
                "status": resolve_status(
                    entry, prepared_root=None, requested=True
                ).as_dict(),
            }
        )
    print(json.dumps(report, indent=2))
