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

"""The descriptor include-path spelling a test should expect, per platform.

A compatibility descriptor is a text file that may be authored on Linux and
consumed on Windows, so ``abicheck.compat.descriptor`` documents that a
Unix-style absolute ``<include_paths>`` entry stays *absolute* on Windows by
taking the current drive -- ``/opt/inc`` becomes ``D:\\opt\\inc`` rather than
the drive-relative ``\\opt\\inc`` that bare ``Path("/opt/inc")`` would give.

Tests that assert a POSIX literal therefore pass on Linux and fail on the
Windows lanes for a reason that has nothing to do with what they test. This
helper states the *contract* once, in the tests' own terms, deliberately not
by calling ``descriptor._resolve`` -- an oracle that re-ran the code under
test would agree with it no matter what it did.
"""

from __future__ import annotations

import os
from pathlib import Path


def descriptor_absolute(posix_path: str) -> Path:
    """Where a descriptor's POSIX-absolute *posix_path* lands on this host."""
    if os.name == "nt":
        return Path(Path.cwd().drive + posix_path)
    return Path(posix_path)


def descriptor_include_flag(posix_path: str) -> str:
    """The ``-I`` token a descriptor's *posix_path* produces on this host."""
    return f"-I{descriptor_absolute(posix_path)}"
