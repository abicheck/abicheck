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

"""Header-scoped PE/Mach-O dump binding shared by both adapters.

``service_header_scoped`` reaches ``dry_run_estimate``, which reaches back
through the already-baselined CLI-registration SCC, so it is bound through
``importlib.import_module`` rather than a static import -- the same
indirection the deleted ``service_dump_native_pe`` used, so these modules
stay out of that cycle.
"""

from __future__ import annotations

import importlib as _importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...model import AbiSnapshot

_service_header_scoped = _importlib.import_module(
    "abicheck.workflows.dump.header_scoped"
)
try_header_scoped_dump: Callable[..., tuple[AbiSnapshot | None, str | None]] = (
    _service_header_scoped._try_header_scoped_dump
)
del _service_header_scoped
