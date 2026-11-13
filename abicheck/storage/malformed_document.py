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

"""The one translation of a malformed stored document into a typed error.

The snapshot decoders index the document directly (``fn["mangled"]``,
``t["fields"]``), so a document missing a required key, or holding a node of
the wrong JSON type, raised ``KeyError``/``TypeError``/``AttributeError`` --
eagerly from ``load_snapshot`` or lazily on first ``declarations`` access --
where the documented contract is a typed error.
Each decode entry point (the eager ``decode_snapshot`` and the lazy
declaration-store build) runs inside :func:`malformed_document_errors`, so
every decoder below them inherits the contract instead of each validating its
own keys.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..errors import AbicheckError, MalformedSnapshotError

__all__ = ["malformed_document_errors"]

#: What a decoder raises when the document's shape is not the one it reads.
_SHAPE_ERRORS = (KeyError, TypeError, AttributeError, IndexError)


@contextmanager
def malformed_document_errors(what: str = "snapshot document") -> Iterator[None]:
    """Re-raise a shape error from the block as
    :class:`~abicheck.errors.MalformedSnapshotError`.

    That error is a ``SnapshotError`` and still a ``TypeError``/``ValueError``,
    so no existing handler stops catching it. A plain ``ValueError`` (the
    storage layer's own refusal, already a "malformed package" signal) and an
    :class:`~abicheck.errors.AbicheckError` (already typed) pass through.
    """
    try:
        yield
    except AbicheckError:
        raise
    except _SHAPE_ERRORS as exc:
        detail = f"missing key {exc}" if isinstance(exc, KeyError) else str(exc)
        raise MalformedSnapshotError(
            f"malformed {what}: {type(exc).__name__}: {detail}"
        ) from exc
