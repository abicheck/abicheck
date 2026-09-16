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

"""``scope.exclude_headers`` resolution, with no toolchain involved.

The precedence rule ("weaker than the flag, never unioned with it") is the
part worth stating directly: a union would silently make every stated rule
set wider than stated, and the rule set is the run's scope identity.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.buildsource.build_config import BuildConfig
from abicheck.frontends.cli.dump_debug_config import resolve_dump_scope_exclude_headers


def _cfg(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / ".abicheck.yml"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class TestTheConfigKeyParses:
    def test_a_list_is_taken_as_given(self) -> None:
        cfg = BuildConfig.from_dict({"scope": {"exclude_headers": ["a.h", "b.h"]}})
        assert cfg.exclude_headers == ["a.h", "b.h"]

    def test_a_bare_string_folds_to_one_entry(self) -> None:
        """``_strs`` accepts either shape; registering the key in
        ``LIST_SUBKEYS`` is what makes that true here too."""
        assert BuildConfig.from_dict(
            {"scope": {"exclude_headers": "a.h"}}
        ).exclude_headers == ["a.h"]

    def test_a_wrong_typed_value_is_rejected(self) -> None:
        """The reason the key has to be in ``LIST_SUBKEYS`` and not merely
        parsed: an unregistered key silently accepts nonsense."""
        with pytest.raises(ValueError):
            BuildConfig.from_dict({"scope": {"exclude_headers": 42}})

    def test_absent_is_empty_not_none(self) -> None:
        assert BuildConfig.from_dict({}).exclude_headers == []

    def test_it_round_trips(self) -> None:
        cfg = BuildConfig.from_dict({"scope": {"exclude_headers": ["a.h"]}})
        assert BuildConfig.from_dict(cfg.to_dict()).exclude_headers == ["a.h"]

    def test_it_is_not_sources_exclude(self) -> None:
        """Different inputs at different layers; neither implies the other."""
        cfg = BuildConfig.from_dict(
            {
                "scope": {"exclude_headers": ["h.h"]},
                "sources": {"exclude": ["vendor/**"]},
            }
        )
        assert cfg.exclude_headers == ["h.h"]
        assert cfg.exclude == ["vendor/**"]


class TestDumpResolution:
    def test_no_config_no_flag_is_empty(self, tmp_path: Path) -> None:
        assert resolve_dump_scope_exclude_headers(None, tmp_path, ()) == ()

    def test_the_config_key_is_used_when_no_flag_was_given(
        self, tmp_path: Path
    ) -> None:
        cfg = _cfg(tmp_path, {"scope": {"exclude_headers": ["a.h"]}})
        assert resolve_dump_scope_exclude_headers(cfg, None, ()) == ("a.h",)

    def test_an_explicit_flag_takes_the_whole_decision(self, tmp_path: Path) -> None:
        """Never unioned: the rule set is the run's scope identity, so
        widening a stated set would make the run narrower than the command
        line says it is."""
        cfg = _cfg(tmp_path, {"scope": {"exclude_headers": ["from-config.h"]}})
        assert resolve_dump_scope_exclude_headers(cfg, None, ("from-flag.h",)) == (
            "from-flag.h",
        )

    def test_a_malformed_config_is_lenient(self, tmp_path: Path) -> None:
        """Same deliberate leniency as its siblings in this module: a
        malformed auto-discovered config must not turn every ``dump`` into a
        hard error."""
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("{not: valid: yaml: at all", encoding="utf-8")
        assert resolve_dump_scope_exclude_headers(cfg, None, ()) == ()

    def test_a_config_without_the_key_is_empty(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, {"scope": {"public": True}})
        assert resolve_dump_scope_exclude_headers(cfg, None, ()) == ()
