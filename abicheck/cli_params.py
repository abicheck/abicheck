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

"""Shared custom Click parameter types for the abicheck CLI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import click


class PolicyFileParam(click.ParamType):
    """Click type for ``--policy-file``: an existing file or a built-in name.

    Accepts a real path (which must exist) or a bare built-in policy name such
    as ``security`` that resolves to a packaged ``abicheck/policies/*.yaml``
    (see ``abicheck.policy_file.builtin_policy_path``).
    """

    name = "policy"

    def convert(self, value: Any, param: Any, ctx: Any) -> Path:
        from .policies import builtin_policy_names
        from .policy_file import builtin_policy_path

        value_str = str(value)
        builtin = builtin_policy_path(value_str)
        if builtin is not None:
            return builtin

        p = Path(value_str)
        if p.exists():
            return p
        names = ", ".join(builtin_policy_names())
        raise click.BadParameter(
            f"{value!r}: no such file, and not a built-in policy "
            f"(available built-ins: {names})",
            ctx=ctx,
            param=param,
        )


#: Shared instance for all ``--policy-file`` options.
POLICY_FILE_PARAM = PolicyFileParam()
