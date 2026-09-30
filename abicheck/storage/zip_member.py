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

"""A zip member header whose bytes depend only on the member's name and
content -- never on when, where or by whom it was written. Shared by the two
zip containers in this package: G40's bundle archive (``bundle_archive.py``)
and the project-package transport (``project_package_archive.py``)."""

from __future__ import annotations

import zipfile

__all__ = ["deterministic_zipinfo"]

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def deterministic_zipinfo(name: str) -> zipfile.ZipInfo:
    """A ``ZipInfo`` for member *name* with every reproducibility-affecting
    field pinned, so the bytes this module writes depend only on the
    member's own name and content -- never on when or by whom it was
    written."""
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_STORED
    # A fixed, portable permission bit (rw-r--r--) rather than whatever
    # `ZipInfo`'s own platform-dependent default would otherwise stamp.
    info.external_attr = 0o644 << 16
    # `ZipInfo.__init__` defaults `create_system` to the host platform (0
    # Windows, 3 Unix); pinned to 3 unconditionally so identical facts on
    # Windows vs. Linux/macOS CI don't differ in bytes/`stored_sha256`.
    info.create_system = 3
    return info
