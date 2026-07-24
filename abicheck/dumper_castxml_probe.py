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

"""``castxml --version`` probe: detect a too-old bundled Clang and fold an
upgrade recommendation into a castxml parse-failure diagnostic.

castxml drives an internal Clang frontend; when it predates the host
headers' requirements (glibc sized-float types, GCC 13+ ``__assume__``), the
parse fails with an opaque error. :func:`_castxml_version_note` probes
``castxml --version`` on that failure path and, when the bundled Clang is
below the recommended floor, returns a one-line note pointing at the real
fix (upgrade castxml) instead of a misleading generic remediation.

Split out of :mod:`abicheck.dumper` (at the file-size hard cap) and
re-exported there, so the public ``dumper._castxml_version_note`` /
``dumper._parse_castxml_version`` surface is unchanged.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import cast
from xml.etree.ElementTree import (
    Element,  # type annotation only; parsing uses defusedxml
)

from defusedxml import ElementTree as DefusedET

from . import deadline
from .dumper_ast_config import _CPP_ONLY_PATTERNS, _detect_cpp_headers
from .dumper_clang_errors import diagnose_header_compile_failure
from .errors import HeaderToolchainError, SnapshotError

# castxml drives an internal Clang frontend; it must be new enough to parse
# modern host headers. _Float32/_Float64/_Float128 land in Clang 16, and the
# [[assume]] / __assume__ attribute (GCC 13+ libstdc++) in Clang 18. We
# recommend a bundled Clang >= this so both are covered. This is the durable
# fix for the header-scoped toolchain aborts (plan G16) — abicheck cannot
# reliably work around a frontend that is simply older than the host headers,
# so it detects the version and tells the user to upgrade.
_RECOMMENDED_CLANG_MAJOR = 18

_CASTXML_VERSION_RE = re.compile(r"castxml version\s+(\S+)", re.IGNORECASE)
# `castxml --version` does not always print the bundled frontend version, and
# when it does the spelling varies ("clang version 18.1.8", "LLVM version 18.1.8").
# Accept either so the precise floor comparison can actually fire.
_CLANG_VERSION_RE = re.compile(
    r"(?:clang|LLVM) version\s+(\d+)(?:\.(\d+))?", re.IGNORECASE
)


def _parse_castxml_version(output: str) -> tuple[str | None, tuple[int, int] | None]:
    """Parse ``castxml --version`` text into (castxml_version, clang_major_minor).

    Either element is ``None`` when not found. Pure/string-only so it is fully
    unit-testable without castxml installed.
    """
    cx = _CASTXML_VERSION_RE.search(output or "")
    cl = _CLANG_VERSION_RE.search(output or "")
    cx_ver = cx.group(1) if cx else None
    clang = (int(cl.group(1)), int(cl.group(2) or 0)) if cl else None
    return cx_ver, clang


def _castxml_version_note(castxml_bin: str = "castxml") -> str:
    """Probe ``castxml --version`` and, when its bundled Clang predates the
    recommended floor, return a one-line upgrade note (else "").

    Best-effort: a probe failure against its OWN 15s cap yields "" so the
    base diagnostic still stands. A DeadlineExceeded from the tighter OUTER
    scan deadline instead propagates uncaught (authoritative L2 path, unlike
    an L3+ advisory pass) rather than masquerading as a parse failure —
    ``run_bounded()`` honors an active outer deadline verbatim, not
    ``min(timeout, left)``, so nesting a narrower scope is required to keep
    both caps meaningful (Codex review, PR #591, rounds 1-2). Only called on
    an actual parse failure, so the extra process is incurred rarely.
    """
    probe_timeout = 15.0
    scan_remaining = deadline.remaining()
    bound_by_scan_deadline = (
        scan_remaining is not None and scan_remaining < probe_timeout
    )
    if scan_remaining is not None:
        probe_timeout = min(probe_timeout, scan_remaining)
    try:
        with deadline.deadline_scope(probe_timeout):
            proc = deadline.run_bounded(
                [castxml_bin, "--version"],
                capture_output=True,
                text=True,
                timeout=probe_timeout,
            )
    except deadline.DeadlineExceeded:
        if bound_by_scan_deadline:
            raise
        # The entry-time snapshot said the local 15s cap was binding, not
        # the outer scan deadline — but run_bounded's own escalation
        # (SIGTERM -> grace -> SIGKILL, plus a fixed 5s pipe-drain) can push
        # real elapsed time past that snapshot, so the outer deadline can
        # still be exhausted by now even though it wasn't at entry. The
        # nested scope's exit already restored it, so re-check directly
        # instead of trusting the stale snapshot alone (Codex review,
        # PR #591, round 3).
        deadline.check()
        return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    raw, clang = _parse_castxml_version(f"{proc.stdout}\n{proc.stderr}")
    if clang is not None and clang[0] < _RECOMMENDED_CLANG_MAJOR:
        detected = (
            f"castxml {raw} (clang {clang[0]}.{clang[1]})"
            if raw
            else f"clang {clang[0]}.{clang[1]}"
        )
        return (
            f" Detected {detected}; these host headers need clang "
            f">= {_RECOMMENDED_CLANG_MAJOR} — upgrade castxml to a build with a "
            f"newer bundled Clang."
        )
    if raw and clang is None:
        return (
            f" Detected castxml {raw}; upgrade it if its bundled Clang predates "
            f"the host gcc (clang >= {_RECOMMENDED_CLANG_MAJOR} recommended)."
        )
    return ""


# clang's diagnostic for an unrecognised sized-float keyword, e.g.
#   error: unknown type name '_Float32'
_SIZED_FLOAT_RE = re.compile(r"_Float(?:16|32|64|128)(?:x)?\b")


def _is_toolchain_version_failure(stderr: str) -> bool:
    """True when a castxml failure is a bundled-Clang-too-old signature
    (sized-float keywords or the GCC ``__assume__`` attribute) — the only
    failures for which the ``castxml --version`` upgrade note is relevant."""
    return bool(stderr) and (
        bool(_SIZED_FLOAT_RE.search(stderr)) or "__assume__" in stderr
    )


def _castxml_failure_hint(
    stderr: str,
    *,
    force_cpp: bool,
    headers: list[Path],
    version_note: str = "",
) -> str:
    """Map a known castxml/host-toolchain failure to an actionable remediation.

    Returns the empty string when no known signature matches. These three
    signatures account for the header-scoped scan aborts seen across the
    real-world scan campaign (see plan G16); each previously surfaced only as an
    opaque clang stderr dump. The durable fix for the first two is a castxml
    built against a newer Clang (or the libclang extractor, G4) — abicheck cannot
    reliably work around a frontend that is simply older than the host headers,
    so it diagnoses precisely (optionally with the detected version via
    ``version_note``) instead of guessing.
    """
    # 1) glibc sized-float types (the dominant case): _Float32/64/128 keywords
    #    the bundled clang frontend rejects while emulating a newer host GCC.
    if stderr and _SIZED_FLOAT_RE.search(stderr):
        return (
            "\n\nHint: the host glibc declares sized-float types "
            "(_Float32/_Float64/_Float128) that this castxml/clang frontend "
            "cannot parse — the bundled clang is older than the host gcc/glibc. "
            "Install a newer castxml (newer bundled Clang), or point abicheck at "
            f"a clang-parsable toolchain via --gcc-path / --sysroot.{version_note}"
        )
    # 2) GCC 13+ libstdc++ uses the [[__assume__]] / __attribute__((__assume__))
    #    spelling the bundled clang frontend doesn't know.
    if "__assume__" in stderr:
        return (
            "\n\nHint: the host libstdc++ uses the GCC '__assume__' attribute "
            "that this castxml/clang frontend rejects. Install a newer castxml "
            "matching the host GCC, or scan against an older/clang-parsable "
            f"libstdc++ via --gcc-path / --sysroot.{version_note}"
        )
    # 3) Explicit --lang c on headers needing C++. _CPP_ONLY_PATTERNS (like the
    # retry gate below) excludes extern "C" so a valid guarded-C header's real
    # failure isn't misreported with this hint (Codex review).
    if not force_cpp and _detect_cpp_headers(headers, _CPP_ONLY_PATTERNS):
        return (
            "\n\nHint: The header files appear to contain C++ syntax "
            "(class, namespace, template) but --lang c was specified. "
            "Try removing --lang or using --lang c++."
        )
    # 4) Generic remediable signatures (missing dependency header, required
    #    config macro, undeclared type from a missing umbrella) — frontend-
    #    agnostic, so the castxml path benefits from the same guidance as clang.
    return diagnose_header_compile_failure(stderr) or ""


def _validate_castxml_output(
    result: subprocess.CompletedProcess[str],
    out_xml: Path,
    headers: list[Path],
    force_cpp: bool,
    castxml_bin: str = "castxml",
) -> Element:
    """Validate castxml output and return parsed XML root."""
    if result.returncode != 0:
        # Only probe `castxml --version` when the failure is a frontend-too-old
        # signature — otherwise the upgrade note is irrelevant (and unused).
        version_note = (
            _castxml_version_note(castxml_bin)
            if _is_toolchain_version_failure(result.stderr)
            else ""
        )
        hint = _castxml_failure_hint(
            result.stderr,
            force_cpp=force_cpp,
            headers=headers,
            version_note=version_note,
        )
        message = (
            f"castxml failed (exit {result.returncode}):\n{result.stderr[:2000]}{hint}"
        )
        # Must mirror _castxml_failure_hint's case-3 predicate exactly
        # (_CPP_ONLY_PATTERNS) or the class and hint text disagree.
        is_toolchain = _is_toolchain_version_failure(result.stderr) or (
            not force_cpp and _detect_cpp_headers(headers, _CPP_ONLY_PATTERNS)
        )
        raise (HeaderToolchainError if is_toolchain else SnapshotError)(message)
    if not out_xml.exists() or out_xml.stat().st_size == 0:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml exited 0 but produced no output file (or empty file).{detail}"
        )
    deadline.check()  # before parsing; outside the try below (Exception would swallow it)
    try:
        root = cast(Element, DefusedET.parse(str(out_xml)).getroot())
    except Exception as xml_exc:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml produced invalid XML: {xml_exc}{detail}"
        ) from xml_exc
    if len(root) == 0:
        stderr_snippet = result.stderr[:1000].strip()
        detail = f"\ncastxml stderr: {stderr_snippet}" if stderr_snippet else ""
        raise SnapshotError(
            f"castxml produced an empty XML document (no declarations found). "
            f"Check that the header paths are correct and the compiler can "
            f"parse them.{detail}"
        )
    # The parse itself can consume the rest of the budget on a huge fresh
    # XML tree; re-check before handing it off (Codex review, PR #591,
    # round 3, mirrors the cached-hit and clang-AST paths).
    deadline.check()
    return root
