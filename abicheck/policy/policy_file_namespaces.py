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

"""Parsing for the two ``--policy`` namespace-convention list keys.

``internal_namespaces:`` names a project's private-implementation convention
(``detail``/``impl``/...); ``experimental_namespaces:`` names its
"declared, but not yet promised stable" convention (``experimental``/
``preview``/...). Unrelated conventions that happen to share a document
shape, so they share a parser shape too -- and they live here rather than in
``policy_file.py`` for the same reason ``policy_file_versioning.py`` and
``policy_file_acknowledgment.py`` do: only the irreducible dataclass-field
and ``load()`` call-site cost belongs in that (oversized, no-growth-tracked)
module.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..errors import PolicyError

__all__ = [
    "experimental_namespaces",
    "internal_namespaces",
    "parse_experimental_namespaces",
    "parse_internal_namespaces",
]


class _NamespaceConventions(Protocol):
    """The two ``PolicyFile`` fields these derivations read.

    A structural Protocol rather than an import of ``PolicyFile`` itself:
    ``policy_file`` imports this module for its parsers, so naming the class
    here -- even under ``TYPE_CHECKING`` -- forms an import cycle the
    AI-readiness gate rejects (and rightly: the dependency runs one way).
    """

    internal_namespaces: list[str]
    experimental_namespaces: list[str]


def internal_namespaces(policy_file: _NamespaceConventions | None) -> tuple[str, ...]:
    """The policy file's internal-namespace hints, or an empty tuple.

    One derivation shared by post-processing's own ``internal_namespaces``
    argument and the persisted evaluation context's ``surface`` hints (the two
    had independent copies, so a change to the resolution rule would have
    silently applied to only one). Post-processing wants ``None`` for "none
    configured" and converts at its own call site; this returns the empty
    tuple, which is what the typed config field takes.
    """
    if policy_file is None or not policy_file.internal_namespaces:
        return ()
    return tuple(policy_file.internal_namespaces)


def experimental_namespaces(
    policy_file: _NamespaceConventions | None,
) -> tuple[str, ...]:
    """The policy file's experimental-namespace segments, or an empty tuple.

    The counterpart of :func:`internal_namespaces` for the unrelated
    ``experimental::`` graduation convention.
    """
    if policy_file is None or not policy_file.experimental_namespaces:
        return ()
    return tuple(policy_file.experimental_namespaces)


def parse_experimental_namespaces(raw: Any) -> list[str]:
    """Validate and parse the ``experimental_namespaces`` list of segments.

    The counterpart of :func:`_parse_internal_namespaces` for the *other*
    namespace convention (see ``PolicyFile.experimental_namespaces``).
    """
    if not isinstance(raw, list):
        raise PolicyError(
            "'experimental_namespaces' must be a YAML list of namespace segments, got "
            + type(raw).__name__
        )
    result: list[str] = []
    for i, token in enumerate(raw):
        if not isinstance(token, str):
            raise PolicyError(
                f"experimental_namespaces[{i}]: expected string, got {type(token).__name__}"
            )
        result.append(token)
    return result


def parse_internal_namespaces(raw: Any) -> list[str]:
    """Validate and parse the ``internal_namespaces`` list of namespace tokens."""
    if not isinstance(raw, list):
        raise PolicyError(
            "'internal_namespaces' must be a YAML list of namespace tokens, got "
            + type(raw).__name__
        )
    result: list[str] = []
    for i, token in enumerate(raw):
        if not isinstance(token, str):
            raise PolicyError(
                f"internal_namespaces[{i}]: expected string, got {type(token).__name__}"
            )
        result.append(token)
    return result
