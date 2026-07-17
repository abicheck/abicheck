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

"""S2 preprocessor pre-scan — macro values + private-header leaks (ADR-035 D2).

The **conditional** half of the D2 always-on tier (the compiler-free lexical part
is :mod:`pattern_scan`). It runs *only when a compile DB and a preprocessor
(``clang -E``) are available* and reports a coverage row naming what to enable
otherwise — it is never counted as clean when it could not run (ADR-035 D2
coverage honesty). Two signals:

- **per-TU ABI-macro-value capture** (``clang -E -dM``): the value each
  ABI-affecting macro (``_GLIBCXX_USE_CXX11_ABI``, ``NDEBUG``,
  ``_ITERATOR_DEBUG_LEVEL``, …) resolves to in each translation unit, and a
  **divergence** finding when the *same* ABI macro resolves to different values
  across TUs (a layout/ABI split inside one build);
- **public-header-includes-private/generated-header leak** detection (from the
  preprocessor's resolved include set): a public header that transitively pulls
  in a project-private or non-public generated header, so a consumer that
  includes only the public header needs an unshipped file to compile.

Per ADR-035 D1/D2 these are **advisory facts** that feed D3 escalation and D4
cross-checks; they are never a verdict on their own.

The whole *analysis* core (macro parsing, ABI-macro selection, divergence,
include classification, leak detection) is pure and unit-tested; only the live
``clang -E`` invocation (:class:`ClangPreprocessorExtractor`) shells out and is
integration-only, degrading to an empty result (reported as skipped) when clang
is absent — exactly like :mod:`include_graph` and the L4 extractors.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # noqa: S404 - preprocessor scan shells out to clang (never shell=True)
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from .. import deadline
from .model import CoverageStatus, LayerConfidence, LayerCoverage

if TYPE_CHECKING:
    from .build_evidence import BuildEvidence

#: Preprocessor-scan fact-schema version. Independent of every other buildsource
#: schema version (see ``buildsource/CLAUDE.md`` "Versioning").
PREPROCESSOR_SCAN_VERSION: int = 1


# ---------------------------------------------------------------------------
# ABI-affecting macro vocabulary
# ---------------------------------------------------------------------------

#: Curated macros whose *value* changes a library's binary layout / ABI. The
#: list is the actionable core (stdlib ABI toggles, debug-iterator levels,
#: hardening); it is illustrative and extensible, not exhaustive (ADR-035 D2).
_ABI_MACRO_NAMES: frozenset[str] = frozenset(
    {
        "NDEBUG",
        "_GLIBCXX_USE_CXX11_ABI",
        "_GLIBCXX_DEBUG",
        "_GLIBCXX_DEBUG_PEDANTIC",
        "_GLIBCXX_ASSERTIONS",
        "_ITERATOR_DEBUG_LEVEL",
        "_LIBCPP_ABI_VERSION",
        "_LIBCPP_ABI_NAMESPACE",
        "_LIBCPP_HARDENING_MODE",
        "_FORTIFY_SOURCE",
        "_HAS_ITERATOR_DEBUGGING",
        "_SECURE_SCL",
        "_HAS_EXCEPTIONS",
        "_CPPRTTI",
        "_CPPUNWIND",
    }
)

#: Prefixes flagging vendor/stdlib ABI-toggle families (e.g. ``_GLIBCXX_*`` ABI
#: knobs, ``_LIBCPP_ABI_*``). Matched in addition to :data:`_ABI_MACRO_NAMES`.
_ABI_MACRO_PREFIXES: tuple[str, ...] = ("_LIBCPP_ABI_", "_GLIBCXX_USE_")

#: ``#define NAME`` or ``#define NAME VALUE`` as emitted by ``clang -E -dM``.
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)(?:\(([^)]*)\))?(?:\s+(.*))?$")


def is_abi_macro(name: str) -> bool:
    """Whether *name* is an ABI-affecting macro worth capturing/diffing (D2)."""
    if name in _ABI_MACRO_NAMES:
        return True
    return name.startswith(_ABI_MACRO_PREFIXES)


def parse_defined_macros(text: str) -> dict[str, str]:
    """Parse ``clang -E -dM`` output into ``{macro_name: value}``.

    Object-like macros keep their (possibly empty) replacement text; a bare
    ``#define FOO`` maps to ``""``. Function-like macros (``#define F(x) …``) are
    **skipped** — they have no single ABI value to diff. Last definition wins
    (matches the preprocessor's final state under ``-dM``).
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _DEFINE_RE.match(line)
        if m is None:
            continue
        name, params, value = m.group(1), m.group(2), m.group(3)
        if params is not None:
            continue  # function-like macro — no scalar ABI value
        out[name] = (value or "").strip()
    return out


def select_abi_macros(defs: dict[str, str]) -> dict[str, str]:
    """Keep only the ABI-affecting macros from a full ``{name: value}`` map."""
    return {n: v for n, v in defs.items() if is_abi_macro(n)}


# ---------------------------------------------------------------------------
# include classification
# ---------------------------------------------------------------------------


class IncludeClass(str, Enum):
    """Provenance class of an included header, for leak detection (ADR-035 D2)."""

    PUBLIC = "public"  # an installed / public-API header
    PRIVATE = "private"  # a project-private (non-installed) header
    GENERATED = "generated"  # a non-public build-generated header
    SYSTEM = "system"  # a third-party / toolchain header (never a leak)
    UNKNOWN = "unknown"  # could not be classified


#: Path *segments* that mark a project-private header tree.
_PRIVATE_SEGMENTS: frozenset[str] = frozenset(
    {"detail", "details", "internal", "private", "impl", "_impl"}
)
#: Basename suffixes that mark a private header (``foo_p.h``, ``foo_impl.h`` …).
_PRIVATE_SUFFIXES: tuple[str, ...] = ("_p.h", "_impl.h", "_internal.h", "_priv.h")
#: Path segments that mark a build-generated tree.
_GENERATED_SEGMENTS: frozenset[str] = frozenset({"generated", "gen", "build", "_build"})
#: Basenames that are conventionally generated config headers.
_GENERATED_BASENAMES: frozenset[str] = frozenset(
    {"config.h", "version.h", "export.h", "abi_config.h"}
)
#: Path prefixes / segments that mark a system / toolchain header.
_SYSTEM_SEGMENTS: frozenset[str] = frozenset({"usr", "include-fixed"})


def classify_include(
    path: str, public_headers: frozenset[str] = frozenset()
) -> IncludeClass:
    """Classify an included header path for the leak check (pure, path-based).

    *public_headers* is the set of known-public header paths (basenames or tails);
    a match there always wins (it is installed surface). Otherwise heuristics on
    the path segments / basename decide. System headers are recognised so a
    public header pulling in ``<vector>`` is never reported as a leak.
    """
    norm = path.replace("\\", "/").lstrip("./")
    pp = PurePosixPath(norm)
    base = pp.name.lower()
    segments = {seg.lower() for seg in pp.parts}

    if _matches_public(norm, base, public_headers):
        return IncludeClass.PUBLIC
    # System first: a /usr/... or c++ stdlib path is third-party, never a leak,
    # even if it happens to contain a "detail" segment (libstdc++ does).
    if segments & _SYSTEM_SEGMENTS or "/include/c++/" in f"/{norm}/":
        return IncludeClass.SYSTEM
    if (
        base in _GENERATED_BASENAMES
        or base.endswith("_config.h")
        or (segments & _GENERATED_SEGMENTS)
    ):
        return IncludeClass.GENERATED
    if segments & _PRIVATE_SEGMENTS or base.endswith(_PRIVATE_SUFFIXES):
        return IncludeClass.PRIVATE
    return IncludeClass.UNKNOWN


def _matches_public(norm: str, base: str, public_headers: frozenset[str]) -> bool:
    """Whether *norm*/*base* is one of the known public headers (tail match).

    A **basename-only** match is used *only* when the public-header input is
    itself basename-only (no path component). Otherwise a public ``include/config.h``
    would shadow an unrelated ``build/config.h`` / ``src/detail/config.h`` by
    basename and hide exactly the generated/private-header leak this pass exists
    to report (Codex review) — so a public input with a path requires a path
    (tail) match.
    """
    if not public_headers:
        return False
    for ph in public_headers:
        p = ph.replace("\\", "/").lstrip("./")
        if not p:
            continue
        if norm == p or norm.endswith("/" + p) or p.endswith("/" + norm):
            return True
        if "/" not in p and p.lower() == base:
            return True
    return False


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroDivergence:
    """An ABI macro that resolves to different values across translation units."""

    macro: str
    values: dict[str, list[str]]  # value → the TU ids that define it that way

    def to_dict(self) -> dict[str, Any]:
        return {
            "macro": self.macro,
            "values": {v: list(tus) for v, tus in self.values.items()},
            "n_values": len(self.values),
        }


@dataclass(frozen=True)
class HeaderLeak:
    """A public header that transitively pulls in a private/generated header."""

    public_header: str
    leaked_header: str
    leak_class: IncludeClass

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_header": self.public_header,
            "leaked_header": self.leaked_header,
            "leak_class": self.leak_class.value,
        }


@dataclass
class PreprocessorScanResult:
    """Outcome of the S2 preprocessor pre-scan (ADR-035 D2).

    ``ran`` distinguishes "scanned and clean" from "could not run" (no compile DB
    / no clang) so the coverage row is honest. ``divergences`` and ``leaks`` are
    advisory facts feeding D3/D4; ``abi_macros`` is the per-TU captured value map.
    """

    ran: bool = False
    skipped_reason: str = ""
    tus_scanned: int = 0
    headers_scanned: int = 0
    attempted: int = 0  # total clang -E invocations attempted
    succeeded: int = 0  # invocations that returned usable output
    diagnostics: list[str] = field(default_factory=list)
    abi_macros: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # tu → {macro: value}
    divergences: list[MacroDivergence] = field(default_factory=list)
    leaks: list[HeaderLeak] = field(default_factory=list)
    version: int = PREPROCESSOR_SCAN_VERSION

    @property
    def all_failed(self) -> bool:
        """True when clang ran but **every** invocation failed (not clean)."""
        return self.ran and self.attempted > 0 and self.succeeded == 0

    def coverage(self) -> LayerCoverage:
        """The mandatory ADR-033 coverage row for the S2 tier (D2 honesty)."""
        if not self.ran:
            return LayerCoverage(
                layer="preprocessor_scan",
                status=CoverageStatus.NOT_COLLECTED,
                confidence=LayerConfidence.UNKNOWN,
                detail=self.skipped_reason or "S2 preprocessor pre-scan did not run",
            )
        # clang is present but every invocation failed (bad flags, missing
        # generated headers, …): nothing was actually inspected, so this is NOT
        # a clean scan — report not_collected with a diagnostic sample so a reader
        # is never misled by a PRESENT/0-findings row (Codex review).
        if self.all_failed:
            sample = (
                self.diagnostics[0] if self.diagnostics else "all clang -E runs failed"
            )
            return LayerCoverage(
                layer="preprocessor_scan",
                status=CoverageStatus.NOT_COLLECTED,
                confidence=LayerConfidence.UNKNOWN,
                detail=(
                    f"clang -E ran but every invocation failed "
                    f"({self.attempted} attempt(s)): {sample}"
                ),
            )
        status = CoverageStatus.PRESENT
        if self.attempted and self.succeeded < self.attempted:
            status = CoverageStatus.PARTIAL
        detail = (
            f"preprocessor scan (S2), {self.tus_scanned} TU(s), "
            f"{self.headers_scanned} public header(s), "
            f"{len(self.divergences)} macro divergence(s), {len(self.leaks)} leak(s)"
        )
        if status is CoverageStatus.PARTIAL:
            detail += f"; {self.attempted - self.succeeded} clang run(s) failed"
        return LayerCoverage(
            layer="preprocessor_scan",
            status=status,
            confidence=LayerConfidence.HIGH,
            detail=detail,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ran": self.ran,
            "skipped_reason": self.skipped_reason,
            "tus_scanned": self.tus_scanned,
            "headers_scanned": self.headers_scanned,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "all_failed": self.all_failed,
            "divergences": [d.to_dict() for d in self.divergences],
            "leaks": [leak.to_dict() for leak in self.leaks],
        }


def find_macro_divergence(per_tu: dict[str, dict[str, str]]) -> list[MacroDivergence]:
    """Find ABI macros defined with **conflicting values** across TUs (pure).

    Only macros that appear in more than one distinct value are reported (a macro
    defined identically everywhere, or in only one TU, is not a divergence). A TU
    that does *not* define a macro is ignored for that macro — absence is not a
    conflict here (it is the macro-presence/build-context concern of D4). Returns
    a deterministically-ordered list.
    """
    by_macro: dict[str, dict[str, list[str]]] = {}
    for tu_id in sorted(per_tu):
        for macro, value in per_tu[tu_id].items():
            if not is_abi_macro(macro):
                continue
            by_macro.setdefault(macro, {}).setdefault(value, []).append(tu_id)
    out: list[MacroDivergence] = []
    for macro in sorted(by_macro):
        values = by_macro[macro]
        if len(values) > 1:
            out.append(MacroDivergence(macro=macro, values=values))
    return out


def find_private_header_leaks(
    header_includes: dict[str, list[str]],
    public_headers: frozenset[str] = frozenset(),
) -> list[HeaderLeak]:
    """Find public headers that include a private/generated header (pure).

    *header_includes* maps each **public** header to the set of headers the
    preprocessor resolved it to include (transitively). A target classified
    :data:`IncludeClass.PRIVATE` or :data:`IncludeClass.GENERATED` (and not
    itself public) is a leak. System/public/unknown includes are never leaks.
    Returns a deterministically-ordered, de-duplicated list.
    """
    seen: set[tuple[str, str]] = set()
    out: list[HeaderLeak] = []
    for public_header in sorted(header_includes):
        for inc in header_includes[public_header]:
            if not inc or inc == public_header:
                continue
            cls = classify_include(inc, public_headers)
            if cls not in (IncludeClass.PRIVATE, IncludeClass.GENERATED):
                continue
            key = (public_header, inc)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                HeaderLeak(
                    public_header=public_header, leaked_header=inc, leak_class=cls
                )
            )
    out.sort(key=lambda leak: (leak.public_header, leak.leaked_header))
    return out


# ---------------------------------------------------------------------------
# live extractor (integration-only)
# ---------------------------------------------------------------------------


@dataclass
class ClangPreprocessorExtractor:
    """Run ``clang -E`` to capture macro values + header includes (integration).

    Compiler-dependent and side-effecting: a missing ``clang`` or a per-unit
    failure records a diagnostic and yields an empty result so collection never
    aborts (ADR-028 D3 authority rule — the pre-scan is advisory).
    """

    clang_bin: str = "clang++"
    diagnostics: list[str] = field(default_factory=list)
    runs_attempted: int = 0
    runs_ok: int = 0
    #: Set once an active scan --budget deadline is found already exhausted
    #: (P0 SVS follow-up). Read by capture_macros/capture_header_includes to
    #: stop iterating the remaining compile units/headers instead of calling
    #: _run() (and hitting the same DeadlineExceeded) for every one of them —
    #: cheap either way, but this keeps a large compile DB's degraded run from
    #: piling up a diagnostic per skipped unit.
    deadline_exhausted: bool = False

    def available(self) -> bool:
        return shutil.which(self.clang_bin) is not None

    def capture_macros(self, build: BuildEvidence) -> dict[str, dict[str, str]]:
        """Return ``{compile_unit_id: {abi_macro: value}}`` via ``clang -E -dM``."""
        from .include_graph import _lang_flag, depfile_args_from_argv
        from .source_extractors._argv import unredact_home

        out: dict[str, dict[str, str]] = {}
        for cu in build.compile_units:
            if self.deadline_exhausted:
                break
            if not cu.source:
                continue
            argv = depfile_args_from_argv(cu.argv) if cu.argv else [cu.source]
            if not argv:
                argv = [cu.source]
            cmd = [
                self.clang_bin,
                "-E",
                "-dM",
                *_lang_flag(cu.language),
                *(unredact_home(a) for a in argv),
            ]
            cwd = unredact_home(cu.directory) if cu.directory else None
            text = self._run(cmd, cwd, cu.id)
            if text is None:
                continue
            abi = select_abi_macros(parse_defined_macros(text))
            if abi:
                out[cu.id] = abi
        return out

    def _run(self, cmd: list[str], cwd: str | None, unit: str) -> str | None:
        # P0 follow-up: bound by the active scan --budget deadline (not just a
        # fixed 120s) and run in its own process group so a timeout kills the
        # whole tree (deadline.run_bounded), same fix as the L2 header-AST
        # subprocess. Unlike that path, a deadline overflow here degrades to a
        # diagnostic + skip rather than aborting the scan — this pre-scan is
        # advisory (ADR-028 D3): losing macro/leak coverage for the remaining
        # compile units on a tight budget is acceptable, silently hanging or
        # orphaning a clang process is not.
        self.runs_attempted += 1
        try:
            proc = deadline.run_bounded(  # noqa: S603 - fixed argv, never shell=True
                cmd,
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except deadline.DeadlineExceeded as exc:
            self.deadline_exhausted = True
            self.diagnostics.append(
                f"clang -E skipped for {unit}: scan --budget exceeded ({exc})"
            )
            return None
        except (OSError, subprocess.SubprocessError) as exc:
            self.diagnostics.append(f"clang -E failed for {unit}: {exc}")
            return None
        if proc.returncode != 0 and not proc.stdout.strip():
            self.diagnostics.append(
                f"clang -E nonzero exit for {unit}: "
                f"{proc.stderr.strip()[:200] or 'no output'}"
            )
            return None
        self.runs_ok += 1
        return cast("str", proc.stdout)

    def capture_header_includes(
        self,
        public_headers: list[str],
        context_argv: list[str],
        language: str = "c++",
        cwd: str | None = None,
    ) -> dict[str, list[str]]:
        """Return ``{public_header: [resolved include, ...]}`` via ``clang -M``.

        Each public header is preprocessed *on its own* with the build's include
        context (``context_argv`` — the ``-I``/``-isystem``/``-D``/``-std`` flags
        from a representative compile command) so the resolved transitive include
        set is what a consumer of that header actually pulls in.

        *cwd* is the representative compile unit's directory: compile DBs commonly
        store **relative** ``-I`` flags (CMake/Ninja), so the depfile pass must run
        from that directory to resolve them — running from the process cwd would
        fail to find project includes and report no leaks even with valid L3
        evidence (Codex review). The macro-capture path honours ``cu.directory``
        the same way per unit.
        """
        from .include_graph import _lang_flag, parse_depfile
        from .source_extractors._argv import unredact_home

        run_cwd = unredact_home(cwd) if cwd else None
        out: dict[str, list[str]] = {}
        for hdr in public_headers:
            if self.deadline_exhausted:
                break
            if not hdr:
                continue
            # The include context (-I flags) is relative to the build dir (run_cwd),
            # but the header path is relative to the process cwd the user invoked
            # from — so make it absolute before changing cwd, else clang looks for
            # it under the build dir and finds nothing (Codex review).
            header_arg = os.path.abspath(unredact_home(hdr))
            cmd = [
                self.clang_bin,
                "-M",
                *_lang_flag(language),
                *(unredact_home(a) for a in context_argv),
                header_arg,
            ]
            text = self._run(cmd, run_cwd, hdr)
            if text and text.strip():
                out[hdr] = parse_depfile(text)
        return out


#: Source-file extensions; a token ending in one is the compile command's TU,
#: not a flag, and is dropped when deriving the reusable include context.
_SOURCE_EXTS: tuple[str, ...] = (".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm")


def _context_flags(args: list[str]) -> list[str]:
    """Strip the source-file token(s) from a depfile argv, keeping only flags.

    The remaining ``-I``/``-isystem``/``-D``/``-std`` flags are the include
    context reused to preprocess each public header on its own.
    """
    return [a for a in args if not a.lower().endswith(_SOURCE_EXTS)]


def run_preprocessor_scan(
    build: BuildEvidence | None,
    public_headers: list[str] | None = None,
    *,
    clang_bin: str = "clang++",
) -> PreprocessorScanResult:
    """Run the S2 preprocessor pre-scan, honestly reporting when it cannot (D2).

    Needs **both** an L3 build (compile context) and a working preprocessor
    (``clang -E``); when either is missing the result is returned with
    ``ran=False`` and a ``skipped_reason`` naming what to enable — never silently
    counted as clean (ADR-035 D2 coverage honesty). With both present it captures
    per-TU ABI-macro values (→ divergence findings) and, when public headers are
    given, their resolved includes (→ private/generated-header leaks).
    """
    result = PreprocessorScanResult()
    if build is None or not build.compile_units:
        result.skipped_reason = (
            "no L3 build evidence (pass --sources/--compile-db so the "
            "preprocessor has compile context)"
        )
        return result
    extractor = ClangPreprocessorExtractor(clang_bin=clang_bin)
    if not extractor.available():
        result.skipped_reason = (
            f"{clang_bin} not found in PATH (S2 needs a preprocessor)"
        )
        return result

    per_tu = extractor.capture_macros(build)
    result.abi_macros = per_tu
    result.tus_scanned = len(per_tu)
    result.divergences = find_macro_divergence(per_tu)

    headers = [h for h in (public_headers or []) if h]
    if headers:
        representative = build.compile_units[0]
        context = _context_flags(_depfile_context(representative))
        language = representative.language or "c++"
        # Run from the representative CU's directory so relative -I flags from a
        # CMake/Ninja compile DB resolve (Codex review) — same dir the per-unit
        # macro capture uses.
        header_includes = extractor.capture_header_includes(
            headers, context, language, cwd=representative.directory or None
        )
        result.headers_scanned = len(header_includes)
        result.leaks = find_private_header_leaks(header_includes, frozenset(headers))

    # Surface the live clang invocation tally so coverage can downgrade to
    # not_collected/partial when runs failed — a present-but-empty row would
    # otherwise read as a clean scan (Codex review).
    result.attempted = extractor.runs_attempted
    result.succeeded = extractor.runs_ok
    result.diagnostics = list(extractor.diagnostics)
    result.ran = True
    return result


def _depfile_context(compile_unit: Any) -> list[str]:
    """The reusable depfile args of a representative compile unit (or empty)."""
    from .include_graph import depfile_args_from_argv

    argv = getattr(compile_unit, "argv", None)
    if not argv:
        return []
    return depfile_args_from_argv(list(argv))
