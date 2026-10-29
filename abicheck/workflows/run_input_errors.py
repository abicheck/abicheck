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

"""A run input that cannot be honored, stated without a front end's vocabulary.

Workflow code may not import ``click``, yet the CLI's exit code and message
depend on *how* an input was refused: a malformed invocation exits 64 (with
an option named, when the refusal is about one), an operational failure --
a well-formed command line whose input did not load -- exits 1. A workflow
states which with :class:`RunInputError`; ``frontends.cli.run_input_errors``
is the one translation into Click's own exceptions, so the wording a user
sees is decided here once, not re-derived by each adapter.
"""

from __future__ import annotations

from enum import Enum

from ..errors import AbicheckError

__all__ = ["InputRefusal", "RunInputError"]


class InputRefusal(Enum):
    """How a front end reports a :class:`RunInputError`."""

    #: The invocation is malformed (exit 64).
    USAGE = "usage"
    #: One option's value is invalid (exit 64, rendered against the option).
    PARAMETER = "parameter"
    #: The command line was well-formed; its input failed (exit 1).
    OPERATIONAL = "operational"


class RunInputError(AbicheckError):
    """*message* refused as *refusal*; *param_hint* names the option, if any."""

    def __init__(
        self,
        message: str,
        refusal: InputRefusal,
        *,
        param_hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.refusal = refusal
        self.param_hint = param_hint

    @classmethod
    def usage(cls, message: str) -> RunInputError:
        return cls(message, InputRefusal.USAGE)

    @classmethod
    def parameter(cls, message: str, param_hint: str | None = None) -> RunInputError:
        return cls(message, InputRefusal.PARAMETER, param_hint=param_hint)

    @classmethod
    def operational(cls, message: str) -> RunInputError:
        return cls(message, InputRefusal.OPERATIONAL)
