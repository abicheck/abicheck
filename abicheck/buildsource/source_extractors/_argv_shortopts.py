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

"""Round 29 Codex review helpers for ``env``-prefix parsing, split into
their own leaf module purely to keep ``_argv.py`` (both helpers' one
caller) under this repo's 2000-line AI-readiness hard cap. Pure/stdlib-only
-- no other dependency, so neither carries any of ``_argv.py``'s own
documented import-cycle risk with ``header_compile_context.py``.
"""

from __future__ import annotations

import os
import shutil

#: Single-CHARACTER short flags that may be GROUPED into one clustered
#: token (GNU clustering only ever combines no-operand flags -- excludes
#: every operand-taking short flag: ``-C``/``-u``/``-S``).
CLUSTERABLE_NO_OPERAND_SHORT_FLAGS = frozenset({"i", "v", "0"})


def is_short_flag_cluster(arg: str) -> bool:
    """True when *arg* is a GNU-clustered no-operand short-flag token,
    e.g. ``-iv`` == ``-i -v``."""
    return (
        len(arg) > 1
        and arg[0] == "-"
        and not arg.startswith("--")
        and all(c in CLUSTERABLE_NO_OPERAND_SHORT_FLAGS for c in arg[1:])
    )


def resolve_bare_token_with_default_path(token: str) -> str | None:
    """Resolve against :data:`os.defpath` only, never inherited ``PATH``
    (a real PATH-less ``execvp`` still searches it)."""
    return shutil.which(token, path=os.defpath)
