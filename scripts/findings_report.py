#!/usr/bin/env python3
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

"""The shared error/warning collector every structural gate script reports through.

A **leaf** module, pure stdlib, importable before ``pip install -e .`` — the same
constraint ``check_ai_readiness.py`` itself runs under (it is the first CI step).

``check_ai_readiness.py`` and ``check_docs_contract.py`` each carried a
byte-identical copy of this class, differing only in the label on the summary
line; the second copy even said so in its docstring ("mirrors
scripts/check_ai_readiness.py's Findings class for consistent output"). Stated
once here so "consistent output" is a property of the code rather than of
someone remembering to patch both (CodeFactor: duplicate code).

Each gate subclasses it to pin its own summary label, which keeps the zero-arg
``Findings()`` construction its checks and tests already use.
"""

from __future__ import annotations

from collections import defaultdict


class Findings:
    """Collects errors and warnings, grouped by check name for readable output."""

    #: Prefix on the closing summary line ("<label>: N error(s), M warning(s)").
    SUMMARY_LABEL = "findings"

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def err(self, check: str, msg: str) -> None:
        self.errors.append((check, msg))

    def warn(self, check: str, msg: str) -> None:
        self.warnings.append((check, msg))

    def report(self) -> int:
        """Print every finding grouped by check; return the process exit code."""
        by_check: dict[str, dict[str, list[str]]] = defaultdict(
            lambda: {"errors": [], "warnings": []}
        )
        for check, msg in self.errors:
            by_check[check]["errors"].append(msg)
        for check, msg in self.warnings:
            by_check[check]["warnings"].append(msg)

        for check, buckets in sorted(by_check.items()):
            print(f"\n=== {check} ===")
            for m in buckets["errors"]:
                print(f"  ERROR: {m}")
            for m in buckets["warnings"]:
                print(f"  WARN:  {m}")

        n_err, n_warn = len(self.errors), len(self.warnings)
        print(f"\n{self.SUMMARY_LABEL}: {n_err} error(s), {n_warn} warning(s)")
        return 1 if n_err else 0
