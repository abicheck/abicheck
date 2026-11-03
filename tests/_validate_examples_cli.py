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

"""The argument parser of ``tests/validate_examples.py``, split out to keep
that module under its ``architecture/debt.yaml`` no-growth baseline."""

from __future__ import annotations

import argparse


def build_parser(
    description: str | None, variants: tuple[str, ...], default_variant: str
) -> argparse.ArgumentParser:
    """The ``validate_examples.py`` CLI."""
    ap = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "filters", nargs="*", help="Name substrings to filter cases (default: all)"
    )
    ap.add_argument("--fail-fast", action="store_true", help="Stop after first FAIL")
    ap.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        metavar="N",
        help="Parallel cases (0 = one per CPU; result order unchanged; ignored with --fail-fast)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Machine-readable JSON output",
    )
    ap.add_argument(
        "--category",
        metavar="CAT",
        help="Filter by category: breaking, compatible, bad_practice, api_break",
    )
    ap.add_argument(
        "--artifact-variant",
        choices=(*variants, "all"),
        default=default_variant,
        help=(
            "Example artifact profile to validate: debug-headers (current default), "
            "release-headers, stripped-headers, build-source, or all."
        ),
    )
    ap.add_argument(
        "--toolchain",
        choices=("auto", "gcc", "clang", "msvc"),
        default="auto",
        help=(
            "Compiler family to prefer for every compile/build (default: auto = "
            "platform-native order). Use 'clang' to exercise producer-sensitive "
            "cases such as case64 (DW_AT_calling_convention for ms_abi)."
        ),
    )
    ap.add_argument(
        "--contract-scope",
        choices=("legacy", "public"),
        default="legacy",
        help=(
            "legacy (default): each case's authored scope (unscoped unless "
            "scope_public_headers). public: the default scoped compare users "
            "get, for every case -- report-only, labels are authored unscoped."
        ),
    )
    return ap
