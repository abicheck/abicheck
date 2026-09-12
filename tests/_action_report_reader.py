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

"""Loads ``action/report_query.py`` as a module for the tests that unit-test it.

The reader lives in ``action/`` rather than ``abicheck/`` because it must version
with the Action, not the installed package, so it is not importable by name.
Shared here so the modules that test it do not each keep a copy of the loader.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
READER = ACTION_DIR / "report_query.py"


def load_reader():
    """Import ``action/report_query.py`` under a private module name."""
    spec = importlib.util.spec_from_file_location("_abicheck_report_query", READER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rq = load_reader()
