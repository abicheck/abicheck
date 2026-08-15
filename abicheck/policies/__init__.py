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

"""Built-in, shipped policy files.

These YAML policy files are packaged with abicheck so common gating profiles
are turnkey: ``--policy security`` resolves to ``security.yaml`` here
(see ``abicheck.policy_file.builtin_policy_path``).
"""
from __future__ import annotations

from pathlib import Path

#: Directory holding the shipped ``*.yaml`` policy files.
POLICIES_DIR = Path(__file__).parent


def builtin_policy_names() -> list[str]:
    """Return the stems of all shipped built-in policy files (sorted)."""
    return sorted(p.stem for p in POLICIES_DIR.glob("*.yaml"))
