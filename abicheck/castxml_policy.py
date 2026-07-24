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

"""CastXML version-gate policy: the single source of truth for what
"supported" means for an authoritative L2 CastXML scan.

Previously abicheck had no runtime floor on the CastXML version at all — the
only related check (``dumper_castxml_probe._castxml_version_note``) is a
best-effort, *advisory* note appended to a parse-failure message, never a
proactive gate run before headers are parsed, and it only inspects the
bundled Clang major version, never CastXML's own version. This module adds
that gate: a supported range for CastXML itself plus a minimum bundled/linked
Clang major, checked once before the L2 scan runs.

The floor targets the legacy PyPI ``castxml`` distribution specifically
(last released as 0.4.5 in 2018, with no bundled-Clang metadata at all) —
that is exactly what this gate is meant to catch and reject by default. The
floor is *not* pinned to conda-forge's current ``castxml`` feedstock line: the
CastXML Superbuild (github.com/CastXML/CastXMLSuperbuild, what
``action/install-castxml.sh`` installs and what most CI/CD consumers of this
project actually run) tracks its own, much slower-moving internal version
number — its latest active release as of this writing is still numbered
0.6.x despite bundling a current LLVM/Clang (21.x) — so a floor calibrated to
conda-forge's scale would reject every real-world Superbuild install
indefinitely, including this repo's own pinned CI build. ``MIN_CASTXML``
tracks the Superbuild's ``v0.6.11`` generation (Feb 2024), the first tag in
its current, actively-maintained release line; ``MIN_CASTXML_CLANG_MAJOR``
below carries the actual technical requirement (glibc sized-float types, the
GCC 13+ ``__assume__`` attribute) that a "modern enough" build must satisfy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from packaging.version import InvalidVersion, Version

# Supported CastXML version range for an authoritative L2 scan: >=0.6.11,<0.8.0.
MIN_CASTXML = "0.6.11"
MAX_CASTXML = "0.8.0"  # exclusive upper bound
# Minimum bundled/linked Clang major version a supported CastXML build must
# carry (glibc sized-float types and the GCC 13+ __assume__ attribute need
# this — see dumper_castxml_probe._RECOMMENDED_CLANG_MAJOR, which this
# constant is kept in sync with).
MIN_CASTXML_CLANG_MAJOR = 18

_CASTXML_VERSION_RE = re.compile(r"castxml version\s+(\S+)", re.IGNORECASE)
_CLANG_VERSION_RE = re.compile(
    r"(?:clang|LLVM) version\s+(\d+)(?:\.(\d+))?", re.IGNORECASE
)

REASON_VERSION_BELOW_MINIMUM = "castxml_version_below_minimum"
REASON_VERSION_AT_OR_ABOVE_MAXIMUM = "castxml_version_at_or_above_maximum"
REASON_VERSION_UNPARSEABLE = "castxml_version_unparseable"
REASON_CLANG_MAJOR_BELOW_MINIMUM = "castxml_bundled_clang_below_minimum"
REASON_CLANG_MAJOR_UNKNOWN = "castxml_bundled_clang_unknown"


@dataclass(frozen=True)
class CastxmlVersionCheck:
    """Outcome of checking a probed CastXML version against policy."""

    raw_output: str
    castxml_version: str | None
    clang_major_minor: tuple[int, int] | None
    supported: bool
    reasons: list[str] = field(default_factory=list)

    def message(self, *, found_at: str | None = None) -> str:
        """Render the standard user-facing unsupported-version message."""
        where = f" was found at {found_at}" if found_at else ""
        version_desc = self.castxml_version or "of unknown version"
        lines = [
            f"CastXML {version_desc}{where}.",
            f"abicheck supports CastXML >={MIN_CASTXML},<{MAX_CASTXML} for "
            "authoritative L2 scans.",
            "The PyPI `castxml` distribution is not a supported default scanner setup.",
            "Install the complete conda-forge environment or select an "
            "explicitly managed supported CastXML/direct-Clang profile.",
        ]
        return "\n".join(lines)


def parse_castxml_version_output(
    output: str,
) -> tuple[str | None, tuple[int, int] | None]:
    """Parse ``castxml --version`` text into (castxml_version, clang_major_minor).

    Either element is ``None`` when not found. Pure/string-only, so fully
    unit-testable without castxml installed. Mirrors
    ``dumper_castxml_probe._parse_castxml_version`` (kept as a separate,
    narrower helper there for the advisory note; this is the policy-facing
    copy so ``castxml_policy`` has no import-time dependency on the dumper).
    """
    cx = _CASTXML_VERSION_RE.search(output or "")
    cl = _CLANG_VERSION_RE.search(output or "")
    cx_ver = cx.group(1) if cx else None
    clang = (int(cl.group(1)), int(cl.group(2) or 0)) if cl else None
    return cx_ver, clang


def _parse_pep440(castxml_version: str) -> Version | None:
    """Parse *castxml_version* as PEP 440, tolerating the CastXML
    Superbuild's git-describe-style ``-g<hash>`` (or ``-<n>-g<hash>``)
    release suffix — e.g. ``0.7.0-g9864b1e``. PEP 440 only accepts that kind
    of build metadata after a ``+`` local-version separator, not a bare
    ``-``, so a straight ``Version(...)`` call on the Superbuild's own
    version string always raised ``InvalidVersion`` regardless of whether the
    numeric release itself was in range. Returns ``None`` when neither form
    parses.

    The fallback converts only the *last* hyphen to ``+``, not the first
    (Codex review): the documented CastXML version format also allows an
    optional ``-rc<n>`` pre-release id before the git suffix, e.g.
    ``0.7.0-rc1-gabc``, and PEP 440 already parses a hyphen-separated
    pre-release segment like ``-rc1`` directly (the first ``Version(...)``
    call below succeeds on ``0.7.0-rc1`` with no git suffix at all).
    Converting the *first* hyphen instead would fold that ``-rc1`` marker
    into the opaque local-version string, erasing its pre-release meaning
    and making the build compare as ``>=`` the final release it precedes.
    """
    try:
        return Version(castxml_version)
    except InvalidVersion:
        pass
    last_hyphen = castxml_version.rfind("-")
    if last_hyphen != -1:
        candidate = (
            castxml_version[:last_hyphen] + "+" + castxml_version[last_hyphen + 1 :]
        )
        try:
            return Version(candidate)
        except InvalidVersion:
            pass
    return None


def evaluate_castxml_version(raw_output: str) -> CastxmlVersionCheck:
    """Check a ``castxml --version`` transcript against the supported range.

    Never raises — an unparseable version is itself an unsupported-version
    reason (fail closed: an authoritative L2 scan must not silently proceed
    against a CastXML build whose version could not even be determined).
    """
    castxml_version, clang_major_minor = parse_castxml_version_output(raw_output)
    reasons: list[str] = []

    parsed: Version | None = None
    if castxml_version is not None:
        parsed = _parse_pep440(castxml_version)
        if parsed is None:
            reasons.append(REASON_VERSION_UNPARSEABLE)
    else:
        reasons.append(REASON_VERSION_UNPARSEABLE)

    if parsed is not None:
        if parsed < Version(MIN_CASTXML):
            reasons.append(REASON_VERSION_BELOW_MINIMUM)
        # Compare the *release* segment alone (parsed.base_version strips
        # any pre/post/dev/local qualifiers) against the upper bound —
        # Codex review: a plain ``Version`` comparison sorts a prerelease
        # of the next line (0.8.0-rc1, 0.8.0.dev1) *below* final 0.8.0,
        # so it slipped through this gate entirely (neither below the
        # minimum nor at/above the maximum) and would have run as an
        # unvalidated 0.8 build. The minimum-side check above is left
        # using the raw pre-release-aware comparison, since a prerelease
        # of the minimum itself (0.7.0-rc1) must still sort below it.
        elif Version(parsed.base_version) >= Version(MAX_CASTXML):
            reasons.append(REASON_VERSION_AT_OR_ABOVE_MAXIMUM)

    if clang_major_minor is None:
        reasons.append(REASON_CLANG_MAJOR_UNKNOWN)
    elif clang_major_minor[0] < MIN_CASTXML_CLANG_MAJOR:
        reasons.append(REASON_CLANG_MAJOR_BELOW_MINIMUM)

    return CastxmlVersionCheck(
        raw_output=raw_output,
        castxml_version=castxml_version,
        clang_major_minor=clang_major_minor,
        supported=not reasons,
        reasons=reasons,
    )
