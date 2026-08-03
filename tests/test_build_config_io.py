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

"""``load_build_config_with_digest`` — the digest half of reading a config.

An ADR-049 D6 receipt may claim a ``.abicheck.yml`` supplied a field only if
it can also say *which revision* of that file did. These cover the two claims
that makes: the digest describes the content the returned config was parsed
from, and it matches the bytes on disk rather than a normalized rendering of
them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from abicheck.buildsource.build_config_io import load_build_config_with_digest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestDigest:
    def test_digest_is_over_the_bytes_that_were_parsed(self, tmp_path: Path):
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  public: false\n", encoding="utf-8")

        config, digest = load_build_config_with_digest(cfg)
        assert config.scope_public is False
        assert digest == _sha(cfg)

    def test_a_crlf_file_hashes_as_what_is_on_disk(self, tmp_path: Path):
        """Text mode normalizes newlines, so hashing decoded text would give a
        digest matching nothing a consumer can re-derive from the file."""
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_bytes(b"scope:\r\n  public: false\r\n")

        config, digest = load_build_config_with_digest(cfg)
        assert config.scope_public is False
        assert digest == _sha(cfg)
        assert digest != hashlib.sha256(b"scope:\n  public: false\n").hexdigest()

    def test_no_file_is_all_defaults_and_no_digest(self, tmp_path: Path):
        """The same tolerance ``load_build_config`` has — ``None`` says there
        was nothing to digest, which is not the same as an empty file."""
        config, digest = load_build_config_with_digest(tmp_path / "absent.yml")
        assert config.scope_public is None
        assert digest is None

    def test_a_non_mapping_document_still_digests(self, tmp_path: Path):
        """An empty or scalar document configures nothing, but it is real
        content the run read — so it is reported with its digest, not as the
        absent case above."""
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("# only a comment\n", encoding="utf-8")

        config, digest = load_build_config_with_digest(cfg)
        assert config.scope_public is None
        assert digest == _sha(cfg)

    def test_malformed_yaml_raises_the_same_shape_as_the_values_only_loader(
        self, tmp_path: Path
    ):
        """A caller handling one loader's failure must handle this one's
        identically, so the two messages are compared — pinning only this
        one's text could not notice the pair drifting apart (CodeRabbit)."""
        from abicheck.buildsource.inline import load_build_config

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope: [unterminated\n", encoding="utf-8")

        with pytest.raises(ValueError, match="cannot read build config") as digested:
            load_build_config_with_digest(cfg)
        with pytest.raises(ValueError) as values_only:
            load_build_config(cfg)
        assert str(digested.value) == str(values_only.value)

    def test_values_match_the_values_only_loader(self, tmp_path: Path):
        """The digest is additive: the config itself must be what
        ``load_build_config`` would have returned."""
        from abicheck.buildsource.inline import load_build_config

        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text(
            "scope:\n  public: false\nsuppression:\n  strict: true\n",
            encoding="utf-8",
        )

        config, _ = load_build_config_with_digest(cfg)
        assert config == load_build_config(cfg)
