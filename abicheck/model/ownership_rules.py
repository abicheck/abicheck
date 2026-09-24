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

"""Who owns a declaration, as the project states it.

The configured half of ``docs/contribute/plans/
target-ownership-and-extraction-scope.md``: target header roots, named
dependency roots, and the private (owned but not promised) narrowing. These
are *rules*, not decisions -- :mod:`abicheck.extract.ownership` applies them
to a declaration's file.

Roots are kept as the user spelled them. Resolving them against a project
root, and rejecting a root claimed twice, happens in ``extract`` where a
filesystem location is known.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CONTRACT_EXTERNAL",
    "CONTRACT_PRIVATE",
    "CONTRACT_PUBLIC",
    "CONTRACT_UNRESOLVED",
    "OWNER_TARGET",
    "OWNER_TOOLCHAIN",
    "OWNER_UNRESOLVED",
    "DependencyRoots",
    "OwnershipRules",
    "dependency_owner",
]

OWNER_TARGET = "target"
OWNER_TOOLCHAIN = "toolchain"
OWNER_UNRESOLVED = "unresolved"
_DEPENDENCY_PREFIX = "dependency:"

#: Promised by the target.
CONTRACT_PUBLIC = "public"
#: Owned by the target but not promised (``private_headers``/``_namespaces``).
CONTRACT_PRIVATE = "private"
#: Nobody's contract is known: the declaration's owner is unresolved.
CONTRACT_UNRESOLVED = "unresolved"
#: Owned by a dependency or the toolchain; the target promises nothing about it.
CONTRACT_EXTERNAL = "external"


def dependency_owner(name: str) -> str:
    """The owner spelling for declarations of the dependency *name*."""
    return f"{_DEPENDENCY_PREFIX}{name}"


@dataclass(frozen=True)
class DependencyRoots:
    """``scope.dependencies`` entry: a named dependency and its header roots."""

    name: str
    header_roots: tuple[str, ...]


@dataclass(frozen=True)
class OwnershipRules:
    """``scope:``'s ownership keys. ``target_roots`` is ``public_header_dirs``
    plus any ``-H`` directory, folded in by the caller."""

    target_roots: tuple[str, ...] = ()
    dependencies: tuple[DependencyRoots, ...] = ()
    private_headers: tuple[str, ...] = ()
    private_namespaces: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        """True when a key beyond ``public_header_dirs`` was stated -- the
        condition under which the ownership preview is shown."""
        return bool(
            self.dependencies or self.private_headers or self.private_namespaces
        )
