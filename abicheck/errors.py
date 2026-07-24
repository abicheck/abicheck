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


class UnsupportedCastxmlVersionError(SnapshotError):
    """Raised when a CastXML build outside the supported version range would
    be used for an authoritative L2 scan, before any header is parsed.

    A subclass of :class:`SnapshotError` — existing ``except SnapshotError``
    handling still catches it unchanged. See :mod:`abicheck.castxml_policy`
    for the version range and the explicit ``allow_unsupported`` override
    that turns this hard failure into a degraded, clearly-flagged snapshot
    instead.
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
