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

"""The ``policy`` operand of a typed comparison request (ADR-091 R2).

``--policy NAME|PATH`` is one operand on the CLI: a built-in profile name, or a
policy document. :func:`resolve_policy_operand` applies the same rule to the
typed ``policy`` field so both front ends reach the same profile and document.
Split out of ``service_requests`` to keep that module under its line cap.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ValidationError
from ..model.change_catalog.registry import VALID_BASE_POLICIES

__all__ = ["resolve_policy_operand"]


def resolve_policy_operand(policy: str | Path) -> tuple[str, Path | None]:
    """Resolve a ``policy`` value to ``(profile, document)`` -- the CLI's rule.

    ADR-091 R2: ``--policy NAME|PATH`` is one operand. A built-in profile name
    (``strict_abi``, ``sdk_vendor``, ``plugin_abi``) selects that profile and
    no document. Anything else is a policy document -- a packaged built-in
    name such as ``security``, or an existing path -- and runs under the
    default profile (``frontends.cli.options.params.PolicyFileParam``, the CLI
    converter this mirrors; the profile set there is the same tuple).

    Raises:
        ValidationError: The value is empty, or is neither a profile nor a
            document that exists.
    """
    from ..policy_file import builtin_policy_path

    value = str(policy)
    if not value:
        raise ValidationError("policy profile name must not be empty")
    if value in VALID_BASE_POLICIES:
        return value, None
    builtin = builtin_policy_path(value)
    if builtin is not None:
        return "strict_abi", builtin
    if Path(value).exists():
        return "strict_abi", Path(value)
    raise ValidationError(
        f"unknown policy {value!r}: not a built-in profile "
        f"({', '.join(sorted(VALID_BASE_POLICIES))}), no such file, and not a "
        "built-in policy document"
    )
