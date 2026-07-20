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

from . import deadline

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
