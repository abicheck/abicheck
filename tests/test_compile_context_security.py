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

"""CodeRabbit review, PR #1146, findings #1/#2/#5: ``.abicheck.yml``
``compile:`` block trust boundaries and toggle-application coverage.

Split out of ``test_compile_context_parity.py`` (that file sits at the
architecture no-growth line-count cap for legacy oversized test modules)
rather than grown inline -- these tests are thematically distinct anyway
(security/trust-tier behavior, not the compare/dump/scan flag-forwarding
parity that file's own docstring describes).

- ``compile.compiler`` (an auto-discovered ``.abicheck.yml`` selecting the
  executable used for header extraction) gets the identical trust gate
  ADR-032 D5 already draws for ``build.query`` -- see finding #1.
- ``compile.options`` may not smuggle a compiler-plugin-loading flag
  (``-Xclang -load``, ``-fplugin=``, ...), rejected unconditionally
  regardless of config trust tier -- see finding #2.
- The stored-OLD_FACTS + live-NEW_INPUT ``compare`` dispatch path applies
  ``compile.ast_frontend_fallback``/``compile.allow_unsupported_castxml``
  the same way the main ``compare``/``dump`` dispatch paths do -- see
  finding #5.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
import pytest

from abicheck.service_scan import CompileContext


def _merge_compile_config_autodiscover(
    cli: CompileContext, src: Path
) -> tuple[CompileContext, tuple[Path, ...]]:
    from abicheck.cli_scan import _merge_compile_config

    return _merge_compile_config(cli, (), None, sources=src)


def test_merge_compile_config_compiler_honored_from_explicit_config(
    tmp_path,
) -> None:
    """An *explicit* ``--config`` may select the compiler executable."""
    from abicheck.cli_scan import _merge_compile_config

    cfg = tmp_path / "trusted.yml"
    cfg.write_text(
        "compile:\n  compiler: /usr/bin/aarch64-linux-gnu-g++\n", encoding="utf-8"
    )
    merged, _ = _merge_compile_config(CompileContext(), (), cfg)
    assert merged.gcc_path == "/usr/bin/aarch64-linux-gnu-g++"


def test_merge_compile_config_compiler_ignored_from_autodiscovered_config(
    tmp_path, capsys
) -> None:
    """An arbitrary/attacker-supplied ``compile.compiler`` from a config found by
    directory search (never explicitly bound via ``--config``) must NOT select
    the executable used for header extraction -- the same trust boundary
    ADR-032 D5 already draws for ``build.query``. A fork-PR-controlled
    ``.abicheck.yml`` auto-discovered by a CI job must not be able to point
    header extraction at an attacker-supplied path (CodeRabbit review,
    PR #1146, finding #1)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / ".abicheck.yml").write_text(
        "compile:\n  compiler: /tmp/evil-compiler\n", encoding="utf-8"
    )
    cli = CompileContext()
    merged, _ = _merge_compile_config_autodiscover(cli, src)
    assert merged.gcc_path is None
    assert merged.gcc_prefix is None
    err = capsys.readouterr().err
    assert "compile.compiler" in err
    assert "auto-discovered" in err


def test_merge_compile_config_compiler_cli_still_wins_over_autodiscovered(
    tmp_path,
) -> None:
    """CLI > config precedence is unaffected by the trust gate: an explicit
    CLI compiler path is used regardless of where the (untouched) config's
    own compile.compiler would have pointed."""
    src = tmp_path / "src"
    src.mkdir()
    (src / ".abicheck.yml").write_text(
        "compile:\n  compiler: /tmp/evil-compiler\n", encoding="utf-8"
    )
    cli = CompileContext(gcc_path="/usr/bin/g++")
    merged, _ = _merge_compile_config_autodiscover(cli, src)
    assert merged.gcc_path == "/usr/bin/g++"


@pytest.mark.parametrize(
    "options_yaml",
    [
        "compile:\n  options:\n    - -Xclang\n    - -load\n    - ./evil.so\n",
        "compile:\n  options:\n    - -fplugin=./evil.so\n",
        "compile:\n  options:\n    - -Xclang\n    - -add-plugin\n    - evilpass\n",
        "compile:\n  options:\n    - -load\n",
        "compile:\n  options:\n    - -Xclang=-load\n    - -Xclang=./evil.so\n",
        "compile:\n  options:\n    - -Xclang=-add-plugin\n    - -Xclang=evilpass\n",
    ],
)
def test_compile_options_rejects_plugin_loading_sequences(
    tmp_path, options_yaml
) -> None:
    """``compile.options`` may not smuggle a compiler-plugin-loading flag,
    whether as a single ``-fplugin=`` token, the ``-Xclang``-prefixed
    two-token form, or Clang's own documented ``-Xclang=<arg>`` joined
    alias for it (``--help-hidden``) -- reassembled from otherwise
    individually whitespace-free YAML list items, this loads
    attacker-controlled native code into the compiler process (CodeRabbit
    review, PR #1146, finding #2; the joined-alias gap, PR #1154 follow-up).
    Rejected unconditionally, regardless of config trust tier: no
    header-ABI-extraction use case needs it."""
    from abicheck.buildsource.build_config import BuildConfig

    with pytest.raises(ValueError, match="plugin-loading flag"):
        BuildConfig.from_dict({"compile": _yaml_compile_block(options_yaml)})


def _yaml_compile_block(options_yaml: str) -> dict:
    import yaml

    return yaml.safe_load(options_yaml)["compile"]


def test_compile_options_rejects_plugin_loading_even_from_explicit_config(
    tmp_path,
) -> None:
    """The plugin-loading rejection fires even for an *explicit* --config --
    it isn't a trust-tier question at all (finding #2's own scope note): no
    legitimate use case is lost by refusing it outright."""
    from abicheck.cli_scan import _merge_compile_config

    cfg = tmp_path / "trusted.yml"
    cfg.write_text(
        "compile:\n  options:\n    - -Xclang\n    - -load\n    - ./evil.so\n",
        encoding="utf-8",
    )
    with pytest.raises(click.UsageError, match="plugin-loading flag"):
        _merge_compile_config(CompileContext(), (), cfg)


def test_compile_options_benign_flags_still_accepted(tmp_path) -> None:
    """A normal compile.options list (no plugin-loading tokens) is unaffected."""
    from abicheck.buildsource.build_config import BuildConfig

    bc = BuildConfig.from_dict(
        {"compile": {"options": ["-march=armv8-a", "-DFOO=1"]}}
    )
    assert bc.compile_options == ["-march=armv8-a", "-DFOO=1"]


class TestBundleFactsDispatchCompileConfigEnvToggles:
    """CodeRabbit review, PR #1146, finding #5: the stored-OLD_FACTS +
    live-NEW_INPUT ``compare`` dispatch path
    (``frontends.cli.commands.compare_bundle_facts.
    resolve_dispatch_compile_context``) does real header-AST extraction, but
    never applied ``compile.ast_frontend_fallback``/
    ``compile.allow_unsupported_castxml`` the way the main ``compare``/
    ``dump`` dispatch paths do."""

    def test_ast_frontend_fallback_toggle_applied(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("compile:\n  ast_frontend_fallback: true\n", encoding="utf-8")
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)

        from abicheck import cli_options
        from abicheck.frontends.cli.commands.compare_bundle_facts import (
            resolve_dispatch_compile_context,
        )

        monkeypatch.setattr(
            cli_options, "resolve_compile_context", lambda ctx, **kw: (CompileContext(), ())
        )

        cli_ctx = click.Context(click.Command("compare"))
        kwargs: dict = {"config": cfg}
        resolve_dispatch_compile_context(cli_ctx, kwargs, new_is_stored=False)
        assert os.environ.get("ABICHECK_ALLOW_AST_FALLBACK") == "1"
        cli_ctx.close()  # runs call_on_close -> restores the prior (unset) value
        assert os.environ.get("ABICHECK_ALLOW_AST_FALLBACK") is None

    def test_allow_unsupported_castxml_toggle_applied(self, tmp_path, monkeypatch) -> None:
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("compile:\n  allow_unsupported_castxml: true\n", encoding="utf-8")
        monkeypatch.delenv("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", raising=False)

        from abicheck import cli_options
        from abicheck.frontends.cli.commands.compare_bundle_facts import (
            resolve_dispatch_compile_context,
        )

        monkeypatch.setattr(
            cli_options, "resolve_compile_context", lambda ctx, **kw: (CompileContext(), ())
        )

        cli_ctx = click.Context(click.Command("compare"))
        kwargs: dict = {"config": cfg}
        resolve_dispatch_compile_context(cli_ctx, kwargs, new_is_stored=False)
        assert os.environ.get("ABICHECK_ALLOW_UNSUPPORTED_CASTXML") == "1"
        cli_ctx.close()
        assert os.environ.get("ABICHECK_ALLOW_UNSUPPORTED_CASTXML") is None

    def test_no_config_no_toggle(self, tmp_path, monkeypatch) -> None:
        """No --config / no auto-discovered config -> nothing to toggle, and
        no crash from a None build config."""
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)

        from abicheck import cli_helpers_compare, cli_options
        from abicheck.frontends.cli.commands.compare_bundle_facts import (
            resolve_dispatch_compile_context,
        )

        monkeypatch.setattr(
            cli_options, "resolve_compile_context", lambda ctx, **kw: (CompileContext(), ())
        )
        monkeypatch.setattr(cli_helpers_compare, "discover_project_config", lambda: None)

        cli_ctx = click.Context(click.Command("compare"))
        kwargs: dict = {"config": None}
        resolve_dispatch_compile_context(cli_ctx, kwargs, new_is_stored=False)
        assert os.environ.get("ABICHECK_ALLOW_AST_FALLBACK") is None

    def test_new_is_stored_path_untouched(self, tmp_path, monkeypatch) -> None:
        """The stored/stored path (new_is_stored=True) does no extraction at
        all and returns early -- it must not be affected by this fix."""
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
        from abicheck.frontends.cli.commands.compare_bundle_facts import (
            resolve_dispatch_compile_context,
        )

        cli_ctx = click.Context(click.Command("compare"))
        kwargs: dict = {"config": None}
        result = resolve_dispatch_compile_context(cli_ctx, kwargs, new_is_stored=True)
        assert result is None
        assert os.environ.get("ABICHECK_ALLOW_AST_FALLBACK") is None
