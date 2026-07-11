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

"""Shared custom Click parameter types for the abicheck CLI."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click

if TYPE_CHECKING:
    from .policy_file import PolicyFile
    from .suppression import SuppressionList


class PolicyFileParam(click.ParamType):
    """Click type for ``--policy-file``: an existing file or a built-in name.

    Accepts a real path (which must exist) or a bare built-in policy name such
    as ``security`` that resolves to a packaged ``abicheck/policies/*.yaml``
    (see ``abicheck.policy_file.builtin_policy_path``).
    """

    name = "policy"

    def convert(self, value: Any, param: Any, ctx: Any) -> Path:
        from .policies import builtin_policy_names
        from .policy_file import builtin_policy_path

        value_str = str(value)
        builtin = builtin_policy_path(value_str)
        if builtin is not None:
            return builtin

        p = Path(value_str)
        if p.exists():
            return p
        names = ", ".join(builtin_policy_names())
        raise click.BadParameter(
            f"{value!r}: no such file, and not a built-in policy "
            f"(available built-ins: {names})",
            ctx=ctx,
            param=param,
        )


#: Shared instance for all ``--policy-file`` options.
POLICY_FILE_PARAM = PolicyFileParam()


class DepthParam(click.ParamType):
    """Click type for the unified ``--depth`` dial (ADR-037 D5/D6).

    Accepts the user-facing ladder ``{binary,headers,build,source,full}`` and
    resolves the remaining deprecated spelling (``symbols`` → ``binary``) to its
    replacement, printing a one-line stderr deprecation note. The L5 graph is
    built internally at ``--depth source`` (D6), so ``graph`` is not a user-facing
    rung (it was removed outright as part of the pre-1.0 CLI clean-up).
    """

    name = "depth"

    def convert(self, value: Any, param: Any, ctx: Any) -> str:
        from .buildsource.scan_levels import DEPRECATED_DEPTHS, USER_DEPTHS

        v = str(value).lower()
        user_values = [d.value for d in USER_DEPTHS]
        if v in user_values:
            return v
        if v in DEPRECATED_DEPTHS:
            replacement = DEPRECATED_DEPTHS[v].value
            # Note covering the remaining deprecated rung (symbols→binary, the
            # evidence-named rung — G22 Phase 6).
            click.echo(
                f"warning: --depth {v} is deprecated (ADR-037 D5/D6); use "
                f"--depth {replacement}.",
                err=True,
            )
            return replacement
        choices = ", ".join(user_values)
        raise click.BadParameter(
            f"{value!r} is not one of {choices}.", ctx=ctx, param=param
        )

    def get_metavar(self, param: Any, ctx: Any = None) -> str:
        from .buildsource.scan_levels import USER_DEPTHS

        return "[" + "|".join(d.value for d in USER_DEPTHS) + "]"


#: Shared instance for every ``--depth`` option.
DEPTH_PARAM = DepthParam()


#: The side prefixes a sided option value may carry (ADR-040 Lever 1).
_SIDES: tuple[str, ...] = ("old", "new", "both")


class SidedPathParam(click.ParamType):
    """Click type for a side-aware path option (ADR-040 Lever 1).

    Collapses the old ``--X`` / ``--old-X`` / ``--new-X`` triple into one
    repeatable ``--X`` whose value optionally carries an ``old=`` / ``new=`` /
    ``both=`` prefix::

        --header v1/foo.h          -> ("both", Path("v1/foo.h"))
        --header old=v1/foo.h       -> ("old",  Path("v1/foo.h"))
        --header new=v2/foo.h       -> ("new",  Path("v2/foo.h"))

    A bare value (no recognised prefix) means both sides — the common case stays
    terminal-cheap. ``both=`` is the explicit escape hatch for the rare path that
    literally starts ``old=`` / ``new=``. Path validation (existence, file/dir
    constraints) is delegated to an internal :class:`click.Path` built from the
    constructor flags, applied to the *stripped* path — so ``--sources`` can
    require an existing directory while ``--header`` does not check existence
    (a header may be absent for a symbols-only fallback).
    """

    name = "sided-path"

    def __init__(
        self, *, exists: bool = False, file_okay: bool = True, dir_okay: bool = True
    ) -> None:
        super().__init__()
        self._path = click.Path(
            exists=exists, file_okay=file_okay, dir_okay=dir_okay, path_type=Path
        )

    def convert(self, value: Any, param: Any, ctx: Any) -> tuple[str, Path]:
        s = str(value)
        for side in _SIDES:
            prefix = f"{side}="
            if s.startswith(prefix):
                raw = s[len(prefix):]
                # ``click.Path.convert`` is typed ``str | bytes | PathLike`` even
                # with ``path_type=Path``; it returns a real ``Path`` at runtime.
                return (side, cast("Path", self._path.convert(raw, param, ctx)))
        return ("both", cast("Path", self._path.convert(s, param, ctx)))

    def get_metavar(self, param: Any, ctx: Any = None) -> str:
        return "[old=|new=]PATH"


#: Sided path for ``--header``/``--include`` — no existence check (a header may
#: be absent for a symbols-only fallback).
SIDED_PATH_PARAM = SidedPathParam()
#: Sided path for ``--sources`` — an existing directory (raw checkout / pack).
SIDED_SOURCES_PARAM = SidedPathParam(exists=True, file_okay=False)
#: Sided path for ``--build-info`` — an existing file (compile DB) or directory.
SIDED_BUILD_INFO_PARAM = SidedPathParam(exists=True)
#: Sided path requiring an existing file/dir (e.g. ``--probe-matrix`` snapshots).
SIDED_EXISTING_PATH_PARAM = SidedPathParam(exists=True)


class SidedStrParam(click.ParamType):
    """Side-aware *string* option (ADR-040 Lever 1) — e.g. ``--version``.

    Same ``old=`` / ``new=`` / ``both=`` prefix convention as
    :class:`SidedPathParam`, but the value stays a bare ``str`` (a version label,
    not a path). A bare value means both sides.
    """

    name = "sided-str"

    def convert(self, value: Any, param: Any, ctx: Any) -> tuple[str, str]:
        s = str(value)
        for side in _SIDES:
            prefix = f"{side}="
            if s.startswith(prefix):
                return (side, s[len(prefix):])
        return ("both", s)

    def get_metavar(self, param: Any, ctx: Any = None) -> str:
        return "[old=|new=]LABEL"


#: Shared instance for sided string options (``--version``).
SIDED_STR_PARAM = SidedStrParam()


def _load_suppression_and_policy(
    suppress: Path | None, policy: str, policy_file_path: Path | None,
    *,
    strict_suppressions: bool = False,
    require_justification: bool = False,
) -> tuple[SuppressionList | None, PolicyFile | None]:
    """Load suppression list and policy file from CLI arguments.

    Shared by ``compare`` (`cli`), ``compare-release``, ``appcompat`` and the
    plugin command — kept here, next to ``POLICY_FILE_PARAM``, rather than in the
    oversized ``cli.py`` so the cross-command resolution logic has one home.
    """
    from .policy_file import PolicyFile
    from .suppression import SuppressionList

    suppression: SuppressionList | None = None
    if suppress is not None:
        try:
            suppression = SuppressionList.load(
                suppress, require_justification=require_justification,
            )
        except OSError as e:
            raise click.BadParameter(str(e), param_hint="--suppress") from e
        except ValueError as e:
            msg = str(e)
            if "no 'reason' field" in msg:
                raise click.ClickException(msg) from e
            raise click.BadParameter(msg, param_hint="--suppress") from e
        if strict_suppressions:
            expired = suppression.check_expired_strict()
            if expired:
                parts = [
                    f"ERROR: {len(expired)} expired suppression rule(s) "
                    f"found in {suppress}:"
                ]
                for idx, rule in expired:
                    target = (
                        rule.symbol_pattern and f'symbol_pattern="{rule.symbol_pattern}"'
                        or rule.symbol and f'symbol="{rule.symbol}"'
                        or rule.type_pattern and f'type_pattern="{rule.type_pattern}"'
                        or rule.source_location and f'source_location="{rule.source_location}"'
                        or "?"
                    )
                    parts.append(
                        f"  Rule {idx + 1}: {target} expired on {rule.expires}"
                    )
                parts.append(
                    "Remove or renew expired rules before proceeding."
                )
                raise click.ClickException("\n".join(parts))

    pf: PolicyFile | None = None
    if policy_file_path is not None:
        try:
            pf = PolicyFile.load(policy_file_path)
        except ImportError as e:
            raise click.ClickException(str(e)) from e
        except (ValueError, OSError) as e:
            raise click.BadParameter(str(e), param_hint="--policy-file") from e
        if policy != "strict_abi":
            click.echo(
                f"Warning: --policy={policy!r} is ignored when --policy-file is given. "
                "Set base_policy in the YAML file to override the base policy.",
                err=True,
            )
    return suppression, pf
