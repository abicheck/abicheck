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
        """Redact a single path-like string."""
        if not value:
            return value
        out = value
        if self.redact_home:
            for prefix, placeholder in self.home_replacements.items():
                if prefix and out.startswith(prefix):
                    out = placeholder + out[len(prefix):]
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

    def argv(self, args: list[str]) -> list[str]:
        return [self.arg(a) for a in args]


#: Default policy used when an adapter is given no explicit policy.
DEFAULT_REDACTION = RedactionPolicy()
