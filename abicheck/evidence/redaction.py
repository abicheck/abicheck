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

"""Redaction of secrets and user-specific paths from build evidence (ADR-032 D7).

Source/build command lines routinely embed absolute home paths, environment
values, and occasionally secrets (tokens passed as ``-D``). Redaction is
mandatory before any command line or path is persisted in an evidence pack
(ADR-028 "Negative/risks"). This is a *minimal* policy for the ADR-029 MVP;
ADR-032 specifies the full capability/redaction model.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# Flags whose *value* is a likely secret (token/password). The value is
# replaced wholesale; the flag itself is kept so option-drift detection still
# sees that the option is present.
_SECRET_DEFINE_RE = re.compile(
    r"(?i)(TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|ACCESS[_-]?KEY|AUTH)",
)

_REDACTED = "<redacted>"


@dataclass
class RedactionPolicy:
    """Replace home directories and obvious secrets in argv/paths.

    ``home_replacements`` maps an absolute prefix to a stable placeholder so the
    same logical tree redacts identically across machines (stable content hash).
    """

    redact_home: bool = True
    redact_secrets: bool = True
    home_replacements: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.redact_home and not self.home_replacements:
            home = os.path.expanduser("~")
            if home and home != "~":
                self.home_replacements = {home: "~"}

    def path(self, value: str) -> str:
        """Redact home-prefix occurrences in a path-like or flag token.

        Replaces the home prefix wherever it appears, not only at the start, so
        combined compiler flags that embed a workspace path (``-I/home/u/inc``,
        ``-DROOT=/home/u/sdk``) are redacted in the persisted ``argv`` just like
        the standalone path fields.
        """
        if not value:
            return value
        out = value
        if self.redact_home:
            for prefix, placeholder in self.home_replacements.items():
                if prefix:
                    out = out.replace(prefix, placeholder)
        return out

    def arg(self, value: str) -> str:
        """Redact a single command-line argument."""
        if not value:
            return value
        if self.redact_secrets and value.startswith(("-D", "/D")):
            # -DKEY=VALUE / -DKEY — redact the value of secret-looking macros.
            body = value[2:]
            if "=" in body:
                key, _, _ = body.partition("=")
                if _SECRET_DEFINE_RE.search(key):
                    return value[:2] + key + "=" + _REDACTED
        return self.path(value)

    def define_value(self, key: str, value: str) -> str:
        """Redact a macro *value* stored under define name *key*.

        Mirrors :meth:`arg` for the structured ``defines`` dict (adapters store
        defines as ``{name: value}``, not ``-DNAME=value`` strings). A
        secret-looking macro name redacts the whole value; otherwise home-path
        prefixes in the value are still normalized.
        """
        if self.redact_secrets and value and _SECRET_DEFINE_RE.search(key):
            return _REDACTED
        return self.path(value)

    def argv(self, args: list[str]) -> list[str]:
        """Redact a full argument list, handling split ``-D KEY=VALUE`` form.

        A secret macro may be passed as a single ``-DKEY=secret`` token (handled
        by :meth:`arg`) or as two tokens ``['-D', 'KEY=secret']``; the latter
        needs lookahead so the value token is redacted before it is persisted in
        ``CompileUnit.argv``.
        """
        out: list[str] = []
        i = 0
        while i < len(args):
            a = args[i]
            if (
                self.redact_secrets
                and a in ("-D", "/D")
                and i + 1 < len(args)
                and "=" in args[i + 1]
            ):
                key, _, _ = args[i + 1].partition("=")
                if _SECRET_DEFINE_RE.search(key):
                    out.append(a)
                    out.append(key + "=" + _REDACTED)
                    i += 2
                    continue
            out.append(self.arg(a))
            i += 1
        return out


#: Default policy used when an adapter is given no explicit policy.
DEFAULT_REDACTION = RedactionPolicy()
