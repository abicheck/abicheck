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

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click

if TYPE_CHECKING:
    from .workflows.policy_file import PolicyFile
    from .workflows.suppression import SuppressionList


#: The built-in verdict-classification profiles ``--policy`` accepts by name,
#: as opposed to a policy *document* it resolves to a path.
BUILTIN_POLICY_PROFILES: tuple[str, ...] = ("strict_abi", "sdk_vendor", "plugin_abi")

#: The profile a run classifies under when ``--policy`` named a document
#: rather than a profile, and when it was not given at all.
DEFAULT_POLICY_PROFILE = "strict_abi"


class PolicyFileParam(click.ParamType):
    """Click type for a policy *document*: an existing file or a built-in name.

    Accepts a real path (which must exist) or a bare built-in policy name such
    as ``security`` that resolves to a packaged ``abicheck/policies/*.yaml``
    (see ``abicheck.policy_file.builtin_policy_path``).

    Not an option type of its own any more -- ``--policy`` takes both a
    profile name and a document, and resolves the document half through this
    converter (see ``cli_options.policy_options``).
    """

    name = "policy"

    def convert(self, value: Any, param: Any, ctx: Any) -> Path:
        from .policies import builtin_policy_names
        from .workflows.policy_file import builtin_policy_path

        value_str = str(value)
        builtin = builtin_policy_path(value_str)
        if builtin is not None:
            return builtin

        p = Path(value_str)
        if p.exists():
            return p
        names = ", ".join(builtin_policy_names())
        profiles = ", ".join(BUILTIN_POLICY_PROFILES)
        raise click.BadParameter(
            f"{value!r}: not a built-in profile ({profiles}), no such file, "
            f"and not a built-in policy document (available: {names})",
            ctx=ctx,
            param=param,
        )


#: Shared instance for resolving a ``--policy`` document operand.
POLICY_FILE_PARAM = PolicyFileParam()


class DepthParam(click.ParamType):
    """Click type for the unified ``--depth`` dial (ADR-043 D2).

    Accepts exactly the user-facing ladder ``{binary,headers,build,source}``.
    ``full``/``symbols``/``graph`` are hard errors, not aliases — the project
    predates a compatibility-stable release, so there is no translation shim.
    The L5 graph is built internally at ``--depth source`` (D6) and the old
    ``full`` rung collapsed into ``source`` (replay *scope*, not a deeper
    depth, distinguished them — see ``SourceScope``), so neither is a
    user-facing rung.
    """

    name = "depth"

    def convert(self, value: Any, param: Any, ctx: Any) -> str:
        from .buildsource.scan_levels import USER_DEPTHS

        v = str(value).lower()
        user_values = [d.value for d in USER_DEPTHS]
        if v in user_values:
            return v
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
#: Sided path for ``compare --dump-manifest`` — an existing YAML file (ADR-050
#: D3), mirroring ``dump --dump-manifest``'s own ``click.Path(exists=True,
#: dir_okay=False)`` constraint.
SIDED_DUMP_MANIFEST_PARAM = SidedPathParam(exists=True, dir_okay=False)


class SidedIncludePathParam(click.ParamType):
    """``--include``'s own type (ADR-050 D1 ``project_include_labels``): layers
    an optional labeled form on top of :class:`SidedPathParam`'s
    ``[old=|new=|both=]PATH`` grammar (composed, not subclassed -- the two
    types' ``convert`` return shapes differ, a 2-tuple vs. this type's
    3-tuple, so subclassing would violate the Liskov substitution principle a
    type checker enforces on an overridden method), so a support ``-I`` root
    that owns no declared header can still be told apart, order-sensitively,
    from every other declared ``-I`` slot when
    ``comparability.compute_extraction_contract`` builds
    ``profile_fingerprint``'s per-slot token (see ``abicheck/comparability.py``'s
    module docstring and ``IncludeDir.label``).

    The labeled form requires the literal colon prefix -- ``old:``, ``new:``,
    or ``both:`` -- never a colon-less ``LABEL=PATH``: an ordinary external
    directory can legitimately contain a literal ``=`` past an
    ``old=``/``new=``/``both=`` prefix (``--include
    old=build/config=asan/include`` is a real, valid unlabeled value today),
    so only the already-reserved colon spelling may additionally carry a
    label without reinterpreting an existing, unrelated value. Convert
    returns ``(side, path, label)`` triples -- ``label`` is ``None`` for the
    ordinary unlabeled form, including every value :class:`SidedPathParam`
    already accepted::

        --include old:support=old/src -> ("old",  Path("old/src"), "support")
        --include new:support=new/src -> ("new",  Path("new/src"), "support")
        --include old=v1/inc          -> ("old",  Path("v1/inc"),  None)
        --include v1/inc              -> ("both", Path("v1/inc"), None)
    """

    name = "sided-include-path"

    def __init__(self) -> None:
        super().__init__()
        self._sided = SidedPathParam()

    def convert(self, value: Any, param: Any, ctx: Any) -> tuple[str, Path, str | None]:
        s = str(value)
        for side in _SIDES:
            prefix = f"{side}:"
            if s.startswith(prefix):
                rest = s[len(prefix):]
                label, sep, raw = rest.partition("=")
                if not sep or not label:
                    raise click.BadParameter(
                        f"{value!r}: a labeled --include requires "
                        f"'{prefix}LABEL=PATH' (e.g. '{prefix}support=path'), "
                        "with a non-empty LABEL before '='.",
                        ctx=ctx, param=param,
                    )
                path = cast("Path", self._sided._path.convert(raw, param, ctx))
                return (side, path, label)
        side, path = self._sided.convert(value, param, ctx)
        return (side, path, None)

    def get_metavar(self, param: Any, ctx: Any = None) -> str:
        return "[old=|new=|old:LABEL=|new:LABEL=]PATH"


#: The one ``--include`` registration shared by ``compare`` (via
#: ``two_sided_input_options``) that needs the labeled form; see
#: :class:`SidedIncludePathParam`.
SIDED_INCLUDE_PATH_PARAM = SidedIncludePathParam()


class LabeledIncludePathParam(click.ParamType):
    """The ``both:LABEL=PATH`` labeled-include grammar meant for ``dump``'s
    own ``--include`` (ADR-050 D1) -- built and unit-tested here, but **not
    yet wired into ``dump_cmd``'s actual ``--include`` option**, which still
    uses a plain ``click.Path`` (see ``comparability.py``'s module
    docstring for the tracked gap: a labeled entry passed to ``dump
    --include`` today is silently parsed as an ordinary unlabeled path, not
    rejected and not honored). This type recognizes **only** the
    colon-terminated ``both:LABEL=PATH`` labeled form, not
    :class:`SidedIncludePathParam`'s full ``old=``/``new=``/``both=`` side
    grammar -- ``dump`` has a single input, no old/new side concept at all.
    Designed so that, once wired in, recognizing ``both:LABEL=PATH`` is
    purely additive: every other value -- bare, or one that happens to
    literally start with ``old=``/``new=``/``both=`` -- stays an ordinary,
    unlabeled path exactly as before, with no equals-form side-prefix
    stripping at all (switching ``dump`` onto :class:`SidedIncludePathParam`
    wholesale would newly start stripping an ``old=`` prefix on ``dump`` too
    -- a real behavior change on a flag that never had that ambiguity).

    Returns ``(path, label)`` pairs -- ``label`` is ``None`` for the ordinary
    unlabeled form::

        --include both:support=path -> (Path("path"), "support")
        --include old=foo/          -> (Path("old=foo/"), None)  # literal dir
        --include path               -> (Path("path"), None)
    """

    name = "labeled-include-path"

    def __init__(self) -> None:
        super().__init__()
        self._path = click.Path(path_type=Path)

    def convert(self, value: Any, param: Any, ctx: Any) -> tuple[Path, str | None]:
        s = str(value)
        prefix = "both:"
        if s.startswith(prefix):
            rest = s[len(prefix):]
            label, sep, raw = rest.partition("=")
            if not sep or not label:
                raise click.BadParameter(
                    f"{value!r}: a labeled --include requires "
                    f"'{prefix}LABEL=PATH' (e.g. '{prefix}support=path'), "
                    "with a non-empty LABEL before '='.",
                    ctx=ctx, param=param,
                )
            path = cast("Path", self._path.convert(raw, param, ctx))
            return (path, label)
        return (cast("Path", self._path.convert(s, param, ctx)), None)

    def get_metavar(self, param: Any, ctx: Any = None) -> str:
        return "[both:LABEL=]PATH"


#: Shared instance intended for ``dump``'s own ``--include`` -- not yet
#: wired into ``dump_cmd`` (see the class docstring above).
LABELED_INCLUDE_PATH_PARAM = LabeledIncludePathParam()


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


class SidedChoiceParam(click.ParamType):
    """Side-aware option drawn from a fixed choice set -- e.g. ``--ast-frontend``.

    :class:`SidedStrParam` with the value half validated against *choices*, so
    a side-scoped option keeps the ``click.Choice`` error message it had
    before it grew an ``old=``/``new=`` prefix. A bare value is the base for
    both sides; ``old=``/``new=`` override one side.
    """

    name = "sided-choice"

    def __init__(self, choices: Sequence[str], *, case_sensitive: bool = False):
        self.choices = tuple(choices)
        self.case_sensitive = case_sensitive

    def convert(self, value: Any, param: Any, ctx: Any) -> tuple[str, str]:
        s = str(value)
        side = "both"
        for candidate in _SIDES:
            prefix = f"{candidate}="
            if s.startswith(prefix):
                side, s = candidate, s[len(prefix) :]
                break
        normalized = s if self.case_sensitive else s.lower()
        allowed = (
            self.choices
            if self.case_sensitive
            else tuple(c.lower() for c in self.choices)
        )
        if normalized not in allowed:
            self.fail(
                f"{s!r} is not one of {', '.join(repr(c) for c in self.choices)}.",
                param,
                ctx,
            )
        return (side, normalized)

    def get_metavar(self, param: Any, ctx: Any = None) -> str:
        return "[old=|new=][" + "|".join(self.choices) + "]"


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
    from .workflows.policy_file import PolicyFile, pending_validate_overrides_warnings
    from .workflows.suppression import SuppressionList

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
                        # Canonical (backend-independent) identity selector
                        # (Codex review, fresh evidence, PR #753): a
                        # finding_id-only rule with no other selector
                        # previously rendered as the bare "?" fallback
                        # here too, the same ambiguity already fixed in
                        # cli_compare_fold.py/post_processing.py's own
                        # selector-rendering chains.
                        or rule.finding_id and f'finding_id="{rule.finding_id}"'
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
            raise click.BadParameter(str(e), param_hint="--policy") from e
        # A policy override that downgrades a critical or otherwise-BREAKING
        # kind is exactly the kind of mistake `validate_overrides()` exists to
        # catch -- but the method was previously dead code, called from
        # nowhere in the CLI, so nobody ever saw its warnings. Surface them
        # here, the one place every policy-document consumer (`compare`,
        # `compare-release`, `scan --against`, `appcompat`) already funnels
        # through, so a risky override is flagged regardless of which command
        # loaded it. Routed through `pending_validate_overrides_warnings`
        # (not `pf.validate_overrides()` directly) so a
        # `dedup_validate_overrides_warnings()` scope -- e.g. `compare-
        # release` wrapping its whole run -- collapses a warning repeated
        # across several loads (here and in `service.
        # load_suppression_and_policy`) down to one (Codex review).
        for warning in pending_validate_overrides_warnings(pf):
            click.echo(f"Warning: {warning}", err=True)
    return suppression, pf
