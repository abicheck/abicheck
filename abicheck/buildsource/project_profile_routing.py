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
"""Profile ``os:`` runner routing and ``dependency_source`` vocabulary (G34 Phase C).

Maps a ``profiles.<id>.os`` value to the GitHub-hosted runner label its check
cell is scheduled on, and lists the accepted ``dependency_source`` values.
Both are consumed by the profile parser in
:mod:`abicheck.buildsource.project_targets` and by the validator in
:mod:`abicheck.buildsource.project_targets_validation`.
"""

from __future__ import annotations

#: The GitHub-hosted runner label a profile's ``os:`` routes its check cell
#: to (G34 Phase C). Before this, every cell ran on ``ubuntu-latest``
#: regardless of what the profile declared, so a ``windows`` profile could
#: not be checked natively through ``check-project.yml`` at all — the
#: mechanical gap that blocked a genuine GCC/Clang/MSVC matrix.
#:
#: ``darwin`` is accepted alongside ``macos`` because both spellings occur in
#: the wild for one platform; nothing else is guessed at.
PROFILE_RUNNER_LABEL_BY_OS = {
    "linux": "ubuntu-latest",
    "windows": "windows-latest",
    "macos": "macos-latest",
    "darwin": "macos-latest",
}

#: What a profile with no ``os:`` at all routes to — today's behaviour for
#: *every* profile, so an existing project's cells are unmoved by Phase C.
DEFAULT_PROFILE_RUNNER_LABEL = "ubuntu-latest"

#: A runner label is accepted verbatim when it names a GitHub-hosted image
#: family, so a profile already written as ``os: ubuntu-24.04`` (pinning an
#: image rather than naming a platform) keeps working. ``os:`` was a free-form,
#: never-consulted string before this phase, so anything narrower would be a
#: breaking config change dressed up as a feature.
_RUNNER_LABEL_PREFIXES = ("ubuntu-", "windows-", "macos-")


def runner_label_for_os(os_value: str) -> str | None:
    """The runner label *os_value* routes to, or ``None`` if it routes nowhere.

    ``None`` is deliberately *not* folded into
    :data:`DEFAULT_PROFILE_RUNNER_LABEL`: silently sending an ``os: freebsd``
    profile to a Linux runner would produce a green cell that checked the
    wrong platform, which is the "a job reports success having gated the
    wrong thing" failure mode ``check-project.yml``'s own guards exist to
    close. An *unset* ``os:`` is a different question and does map to the
    default — that is every existing project, and it is what they get today.
    """
    if not os_value:
        return DEFAULT_PROFILE_RUNNER_LABEL
    mapped = PROFILE_RUNNER_LABEL_BY_OS.get(os_value.lower())
    if mapped is not None:
        return mapped
    if os_value.startswith(_RUNNER_LABEL_PREFIXES):
        return os_value
    return None


def unroutable_os_message(where: str, profile_id: str, os_value: str) -> str:
    """One shared wording for an ``os:`` no runner can be derived from, so
    ``project validate`` and ``project plan`` say the same thing."""
    known = ", ".join(sorted(PROFILE_RUNNER_LABEL_BY_OS))
    return (
        f"{where}: profiles entry {profile_id!r} has os: {os_value!r}, which "
        f"does not name a platform check-project.yml can schedule a runner "
        f"for — use one of {{{known}}}, or a GitHub-hosted runner label "
        f"({', '.join(p + '…' for p in _RUNNER_LABEL_PREFIXES)}) to pin an "
        "image directly."
    )


#: Accepted ``profiles.<id>.dependency_source`` values (G34 Phase C).
#:
#: **Not the fact owner** — the root ``action.yml``'s own ``Resolve
#: dependency-source`` step is, and it validates the identical set. This
#: mirror exists so a bad value fails at ``project validate`` time rather
#: than inside a matrix cell twenty minutes later;
#: ``tests/test_project_targets_dependency_source.py`` asserts the two agree
#: so the copy cannot drift silently.
PROFILE_DEPENDENCY_SOURCES = frozenset(
    {
        "conda-forge",
        "conda-forge-gcc14",
        "conda-forge-clang20",
        "system",
        "none",
    }
)
