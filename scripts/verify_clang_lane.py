#!/usr/bin/env python3
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

"""The clang-version-sensitive verification steps, split out of ``verify.py``
(its file-size record may not grow).

Why these exist: PR #1918 was developed and tested against clang 18 only.
Newer clang printed ``-fdump-record-layouts`` explicit-instantiation template
arguments as written rather than canonically, and that surfaced only on the
macOS/Windows integration legs after pushing. These steps run every test whose
outcome depends on *which clang* answers, so a CI lane can point them at a
different clang (``.github/workflows/clang-latest.yml`` puts a pinned, newest
released LLVM first on ``PATH``) without its own pytest line.

The clang under test is whatever ``clang``/``clang++`` resolves to on
``PATH``: that is how ``abicheck.extract.compiler_driver.resolve_clang_bin``
picks the driver when no ``--compiler``/``--compiler-prefix`` is given, and how
the tests themselves (``shutil.which("clang++")``) decide to run. Locally the
steps run against the host clang, which makes them a convenient focused loop
for clang-backend work too.

Pure data plus one factory; it imports nothing from ``verify.py`` (the
factory takes the ``Step`` class and command builders), so there is no cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from verify_preconditions import _need_all_bins

#: Test modules whose result depends on the clang that runs: the
#: ``-fdump-record-layouts`` layout pass, the clang header backend (unit +
#: live integration), the binary-ABI-target selection that decides which
#: triple clang parses with, and the castxml/clang backend parity cells
#: (castxml embeds its own libclang, so these compare two clang versions).
CLANG_SENSITIVE_TESTS: tuple[str, ...] = (
    "tests/test_clang_record_layout_dump.py",
    "tests/test_family_f8_target_parity.py",
    "tests/test_clang_header_backend_integration.py",
    "tests/test_header_backend_parity_probes_integration.py",
    "tests/test_castxml_clang_parity_gate.py",
    "tests/test_clang_castxml_origin_parity.py",
    "tests/test_clang_binary_abi_target.py",
    "tests/test_dumper_clang.py",
    "tests/test_dumper_clang_compiler_generated.py",
    "tests/test_dumper_clang_enum_value_properties.py",
    "tests/test_dumper_clang_extern_c_identity.py",
    "tests/test_dumper_clang_fact_construction.py",
    "tests/test_dumper_clang_streaming.py",
    "tests/test_dumper_clang_vtable.py",
    "tests/test_dumper_clang_vtable_redecl.py",
    # A base spelled relative to its scope: clang 22+ drops the desugared
    # spelling clang 18 gave (live probe + both JSON shapes).
    "tests/test_clang_relative_base_vtable.py",
)

#: A representative catalog subset for the clang toolchain lane: each case's
#: binaries are *built* with the clang under test, so its DWARF producer
#: changes too. One case per producer-sensitive area -- parameters, record
#: layout, enums, vtables (primary, secondary, thunks), symbol versioning,
#: class size, noexcept, templates (incl. explicit instantiation in a nested
#: namespace, #1918's case), _BitInt, [[no_unique_address]], EBO, the
#: ms_abi calling convention, pimpl, bitfields. Full names: the validator
#: filters by substring, and ``case17`` alone would also select case170-179.
CLANG_CATALOG_SUBSET: tuple[str, ...] = (
    "case02_param_type_change",
    "case07_struct_layout",
    "case08_enum_value_change",
    "case09_cpp_vtable",
    "case13_symbol_versioning",
    "case14_cpp_class_size",
    "case15_noexcept_change",
    "case17_template_abi",
    "case63_bitfield_changed",
    "case64_calling_convention_changed",
    "case77_detail_templated_base_changed",
    "case80_pimpl_shared_to_unique",
    "case115_bit_int_width_changed",
    "case117_no_unique_address",
    "case140_empty_base_optimization_lost",
    "case165_polymorphic_nonvirtual_dtor",
    "case172_vtable_thunk_offset_changed",
    "case174_secondary_vtable_group_changed",
)


def clang_lane_steps(
    step: Callable[..., Any],
    py: Callable[..., tuple[str, ...]],
    pyscript: Callable[..., tuple[str, ...]],
    profiles: frozenset[str],
) -> tuple[Any, ...]:
    """The two clang-sensitive steps, built with ``verify.py``'s own
    ``Step``/``_py``/``_pyscript`` so they behave like every other step."""
    return (
        step(
            "clang-sensitive",
            py(
                "pytest",
                *CLANG_SENSITIVE_TESTS,
                # `slow` is the Hypothesis/perf lane; everything else in these
                # files, integration-marked or not, is clang-sensitive.
                "-m",
                "not slow",
                "--tb=short",
            ),
            profiles,
            # "Did anything run" locally; the CI lane raises it to its known
            # count so a broken install cannot pass as a skip-only run.
            env_defaults={"ABICHECK_MIN_EXECUTED": "1"},
            precondition=_need_all_bins("clang", "clang++", "g++", "castxml"),
            description="clang-version-sensitive tests against the clang on PATH",
        ),
        step(
            "clang-catalog",
            pyscript(
                "tests/validate_examples.py",
                "--toolchain",
                "clang",
                "--jobs",
                "0",
                *CLANG_CATALOG_SUBSET,
            ),
            profiles,
            env={"CC": "clang", "CXX": "clang++", "PYTHONPATH": "."},
            precondition=_need_all_bins("clang", "clang++", "castxml"),
            description="Representative catalog subset built and checked with the clang on PATH",
        ),
    )
