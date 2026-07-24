# Copyright 2026 Nikolay Petrov
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

"""Structured error hierarchy for abicheck.

All public exceptions inherit from AbicheckError, which itself extends
the built-in Exception class for easy catch-all error handling.

SuppressionError inherits both AbicheckError and ValueError so that
existing code catching ValueError continues to work without changes.
"""

from __future__ import annotations


class AbicheckError(Exception):
    """Base exception for all abicheck-specific errors."""


class ValidationError(AbicheckError, ValueError):
    """Raised when input data fails validation (schema, format, length limits).

    Inherits ValueError for backward compatibility with existing code that
    catches ValueError.
    """


class SnapshotError(AbicheckError, RuntimeError):
    """Raised when an ABI snapshot cannot be loaded or parsed.

    Inherits RuntimeError for backward compatibility with existing code that
    catches RuntimeError from snapshot extraction.
    """


class HeaderToolchainError(SnapshotError):
    """Raised when a header-scoped source-mode parse fails on a known,
    diagnosable host-toolchain mismatch (plan G16).

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling still catches it unchanged — but a dedicated class so a caller
    that wants to branch on "this failure carries an actionable, precise
    remediation" (e.g. a sized-float/``__assume__``/``--lang c`` signature
    :func:`abicheck.dumper._castxml_failure_hint` recognised) can do so,
    instead of treating every castxml failure as equally opaque. The
    remediation text is already folded into the exception message.
    """


class IncompatibleSnapshotSchemaError(SnapshotError):
    """Raised when a persisted snapshot's ``schema_version`` is both newer
    than this reader's ``serialization.SCHEMA_VERSION`` and at or above
    ``serialization._MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION`` (ADR-050
    D1).

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling still catches it unchanged, the same precedent
    :class:`HeaderToolchainError` already documents — rather than a bare
    sibling of :class:`AbicheckError` that would fall through
    ``cli_resolve.py``'s existing ``except ValidationError, SnapshotError``
    translation and surface as an unhandled internal failure instead of a
    clean usage error.

    Below that threshold, ``snapshot_from_dict`` keeps today's lenient
    warn-and-continue behavior for an ordinary additive schema bump — this
    error exists only for the specific class of field (starting with
    ``ExtractionContract``) where silently reading past an unrecognized,
    verdict-blocking field would let an old reader compare two
    possibly-incomparable snapshots and produce an ordinary, wrong verdict.
    """


class ProfileMismatchError(AbicheckError):
    """Raised by :func:`abicheck.comparability.check_contracts_comparable`
    when both sides of a compare carry a ``profile_fingerprint`` and it
    differs (ADR-050 D2) — the two snapshots' resolved compile context
    (compiler/macros/include-search inputs) is not comparable, so ``compare``
    must report ``not_comparable`` instead of producing any verdict.
    """


class ScopeMismatchError(AbicheckError):
    """Raised by :func:`abicheck.comparability.check_contracts_comparable`
    when both sides of a compare carry a ``scope_fingerprint`` and it
    differs (ADR-050 D2) — the two snapshots do not cover the same declared
    surface (a manifest/CLI-flag drift between two extraction runs), so
    ``compare`` must report ``not_comparable`` instead of producing any
    verdict.
    """


class SuppressionError(AbicheckError, ValueError):
    """Raised for invalid suppression rules or patterns.

    Inherits ValueError for backward compatibility with existing code that
    catches ValueError from SuppressionEngine.
    """


class PolicyError(AbicheckError, ValueError):
    """Invalid policy configuration.

    Inherits ValueError for backward compatibility with existing code that
    catches ValueError from policy validation.
    """


class ReportError(AbicheckError):
    """Error during report generation."""


class ExtractionSecurityError(AbicheckError):
    """Raised when archive extraction encounters a security violation.

    Triggered by path traversal attempts, symlinks escaping the extraction
    root, or other unsafe archive member paths.
    """

    def __init__(self, member_path: str, reason: str) -> None:
        self.member_path = member_path
        self.reason = reason
        super().__init__(f"Unsafe archive member '{member_path}': {reason}")
