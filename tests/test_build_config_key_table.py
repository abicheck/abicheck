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

"""``.abicheck.yml`` key round-trip and strictness, oracle = the reference doc.

The expected key set below is transcribed from ``docs/reference/config-file.md``
(each block's section and its bullet/example), not read back from
``BuildConfig``'s own registry, so these tests keep meaning the same thing
when the registry that drives ``from_dict``/``to_dict`` is restructured.

Every entry is ``(path, sample, wrong)``: ``path`` is the documented YAML
location, ``sample`` a value the doc shows as valid, and ``wrong`` a value of
the wrong YAML type for that key (the doc's own type column).
"""

from __future__ import annotations

import copy
import random
from typing import Any

import pytest

from abicheck.buildsource.build_config import BuildConfig

Path = tuple[str, ...]

DOCUMENTED: list[tuple[Path, Any, Any]] = [
    (("version",), 1, "1"),
    # build: (drives inline build/source collection)
    (("build", "system"), "cmake", 123),
    (("build", "query"), "make -j", 123),
    (("build", "compile_db"), "build/compile_commands.json", 123),
    (("build", "compile_db_filter"), "lib/**", 123),
    (("build", "targets"), ["//lib:foo"], 123),
    # sources: (public surface for source collection)
    (("sources", "public_headers"), ["include/**"], 123),
    (("sources", "exclude"), ["include/detail/**"], 123),
    (("sources", "graph"), "full", "nope"),
    # severity: (per-category levels + preset)
    (("severity", "preset"), "strict", "nope"),
    (("severity", "abi_breaking"), "error", "nope"),
    (("severity", "potential_breaking"), "warning", "nope"),
    (("severity", "quality_issues"), "info", "nope"),
    (("severity", "addition"), "info", "nope"),
    # scope: (public-surface scoping)
    (("scope", "public"), True, "yes"),
    (("scope", "collapse_versioned_symbols"), False, "yes"),
    (("scope", "public_symbols"), ["mylib_foo"], 123),
    (("scope", "show_redundant"), True, "yes"),
    (("scope", "public_header_dirs"), ["include/"], 123),
    (("scope", "exclude_headers"), ["*_internal.h"], 123),
    (("scope", "on_incomplete"), "block", "nope"),
    (
        ("scope", "dependencies"),
        [{"name": "fmt", "header_roots": ["include/fmt/"]}],
        "fmt",
    ),
    (("scope", "private_headers"), ["include/*/detail/**"], 123),
    (("scope", "private_namespaces"), ["detail"], 123),
    # contract: and acknowledgments: (overlay and gate documents)
    (("contract", "overlays", "post_manifest"), "python/abi/post.json", 123),
    (("acknowledgments", "file"), "abi/acknowledgments.yml", 123),
    (("acknowledgments", "unacknowledged_additions"), "block", "nope"),
    (("acknowledgments", "breaks"), "gate", "nope"),
    # suppression:
    (("suppression", "strict"), True, "yes"),
    (("suppression", "require_justification"), False, "yes"),
    # sources: (precise S-axis)
    (("sources", "method"), "s6", 123),
    # compile: (stable L2 header compile context)
    (("compile", "frontend"), "castxml", "nope"),
    (("compile", "std"), "c++17", 123),
    (("compile", "include_dirs"), ["include"], 123),
    (("compile", "defines"), ["MYLIB_STATIC=0"], 123),
    (("compile", "sysroot"), "/opt/sysroot", 123),
    (("compile", "nostdinc"), False, "yes"),
    (("compile", "compiler"), "gcc", 123),
    (("compile", "options"), ["-fno-exceptions"], 123),
    (("compile", "ast_frontend_fallback"), False, "yes"),
    (("compile", "allow_unsupported_castxml"), True, "yes"),
    (("compile", "frontend_context"), "device", "nope"),
    (("compile", "lang"), "c", "nope"),
    # debug: (separate-debug-file resolution)
    (("debug", "format"), "dwarf", "nope"),
    (("debug", "dwarf_only"), True, "yes"),
    (("debug", "debuginfod"), False, "yes"),
    (("debug", "debuginfod_url"), "https://debuginfod.example.org", 123),
    (("debug", "pdb_path"), "lib.pdb", 123),
    # bundle: / python: / gate: / release: (release and CI topology)
    (("bundle", "system_providers"), ["libc.so.6"], 123),
    (("bundle", "cohorts"), ["libfoo"], 123),
    (("python", "abi3_floor"), "3.9", 123),
    (("gate", "fail_on_removed_library"), True, "yes"),
    (("release", "dso_only"), True, "yes"),
    (("release", "include_private_dso"), True, "yes"),
    (("release", "support_promise"), "declared", "nope"),
    # resource_limits: / performance: / assurance: / policy:
    (("resource_limits", "max_bundle_facts_decode_nodes"), 5000, "5000"),
    (("performance", "profile"), "low-memory", "nope"),
    (("assurance", "require_complete"), True, "yes"),
    (("policy", "overrides"), {"abi_break_example": "warning"}, "warning"),
]

#: Paths whose documented value is itself a mapping, so the flattening used by
#: the random-combination test stops there rather than recursing into it.
_LEAF_MAPPINGS = {("policy", "overrides")}


def _nest(path: Path, value: Any) -> dict[str, Any]:
    out: Any = value
    for key in reversed(path):
        out = {key: out}
    return out


def _merge(into: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        else:
            into[key] = copy.deepcopy(value)
    return into


def _get(data: dict[str, Any], path: Path) -> Any:
    for key in path:
        data = data[key]
    return data


def _flatten(data: dict[str, Any], prefix: Path = ()) -> set[Path]:
    out: set[Path] = set()
    for key, value in data.items():
        here = (*prefix, key)
        if isinstance(value, dict) and here not in _LEAF_MAPPINGS:
            out |= _flatten(value, here)
        else:
            out.add(here)
    return out


@pytest.mark.parametrize(
    ("path", "sample", "wrong"),
    DOCUMENTED,
    ids=[".".join(p) for p, _, _ in DOCUMENTED],
)
def test_documented_key_round_trips(path: Path, sample: Any, wrong: Any) -> None:
    cfg = BuildConfig.from_dict(_nest(path, sample))
    emitted = cfg.to_dict()
    assert _get(emitted, path) == sample
    assert BuildConfig.from_dict(emitted) == cfg


@pytest.mark.parametrize(
    ("path", "sample", "wrong"),
    DOCUMENTED,
    ids=[".".join(p) for p, _, _ in DOCUMENTED],
)
def test_documented_key_rejects_wrong_type(path: Path, sample: Any, wrong: Any) -> None:
    with pytest.raises(ValueError):
        BuildConfig.from_dict(_nest(path, wrong))


@pytest.mark.parametrize(
    "raw",
    [
        {"bogus_top": 1},
        {"build": {"bogus_sub": "x"}},
        {"sources": {"graph": "full", "bogus_sub": True}},
        {"source": {"method": "s4", "graph": "full"}},
        {"scope": {"nonexistent": []}},
    ],
)
def test_unknown_key_is_rejected(raw: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="unknown .abicheck.yml key"):
        BuildConfig.from_dict(raw)


def test_non_mapping_block_is_rejected() -> None:
    with pytest.raises(ValueError, match="severity must be a mapping"):
        BuildConfig.from_dict({"severity": "strict"})


def test_documented_default_is_accepted_and_not_emitted() -> None:
    # `scope.dependency_evidence: full` is the documented default ("the only
    # accepted value today"): it loads, but a dumped config omits it.
    cfg = BuildConfig.from_dict({"scope": {"dependency_evidence": "full"}})
    assert cfg == BuildConfig()
    assert "dependency_evidence" not in cfg.to_dict().get("scope", {})


def test_null_top_level_scalar_and_block_are_accepted() -> None:
    # Documented as "absent": a bare `version:` / `severity:` with no value.
    cfg = BuildConfig.from_dict({"version": None, "severity": None})
    assert cfg == BuildConfig()


def test_empty_config_round_trips_to_empty_mapping() -> None:
    assert BuildConfig().to_dict() == {}
    assert BuildConfig.from_dict({}) == BuildConfig()


@pytest.mark.parametrize("seed", range(40))
def test_random_documented_combination_round_trips(seed: int) -> None:
    rng = random.Random(seed)
    chosen = rng.sample(DOCUMENTED, k=rng.randint(1, len(DOCUMENTED)))
    raw: dict[str, Any] = {}
    for path, sample, _ in chosen:
        _merge(raw, _nest(path, sample))
    cfg = BuildConfig.from_dict(raw)
    emitted = cfg.to_dict()
    # Every documented key we set comes back under its documented path...
    for path, sample, _ in chosen:
        assert _get(emitted, path) == sample, path
    # ...and nothing else is emitted (defaults stay out of a dumped config).
    assert _flatten(emitted) == {path for path, _, _ in chosen}
    assert BuildConfig.from_dict(emitted) == cfg
