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

"""Output rendering for a :class:`~abicheck.checker_types.DiffResult`.

Extracted from :mod:`abicheck.service` so that module stays under the
AI-readiness size cap. This is a leaf module: it does not import
``abicheck.service`` and is re-exported there for backward compatibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ValidationError
from .model import AbiSnapshot
from .reporter import to_json, to_markdown, to_stat, to_stat_json

if TYPE_CHECKING:
    from .checker_types import DiffResult

    # ADR-061: this module is classified `frontends`, which may not import
    # `policy` (where `severity.py`/`SeverityConfig` now physically live,
    # `abicheck/policy/severity.py`) directly -- `workflows.gate` is the
    # existing re-export facade `frontends`-classified callers already
    # route policy-owned exit-decision types through (its own docstring:
    # "the one place a frontend gets its process response").
    from .workflows.gate import SeverityConfig

#: Internal-only ``fmt`` value for :func:`render_output` — a one-line human
#: summary, not exposed as a public ``--format`` choice (CLI cleanup phase
#: two, PR 1: ``--stat`` was removed as a public flag/boolean threaded through
#: every renderer; this is its sole surviving use, reached only via the
#: built-in ``quick`` --profile injecting ``fmt="oneline"`` — see
#: ``cli_profiles.COMPARE_PROFILES["quick"]``). Kept as a plain ``fmt`` value
#: rather than a revived boolean so it flows through the one existing
#: dispatch this function already has, instead of re-introducing a second,
#: orthogonal axis every caller down the stack has to thread separately.
ONELINE_FORMAT = "oneline"


def render_output(
    fmt: str,
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
    *,
    follow_deps: bool = False,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    severity_config: SeverityConfig | None = None,
    demangle: bool = False,
    contract_evaluation: bool = False,
    stat: bool = False,
    show_recommendation: bool = False,
    require_complete_analysis: bool = False,
) -> str:
    """Render comparison result in the requested output format.

    Supported formats: ``'json'``, ``'markdown'``, ``'sarif'``, ``'html'``,
    ``'junit'``, ``'review'``. Plus :data:`ONELINE_FORMAT`, an internal-only
    value not exposed on the public ``--format`` CLI choice.

    ``demangle`` only affects human-facing formats (markdown, review, html);
    machine formats (json/sarif/junit) always keep raw mangled symbols so
    downstream tooling can match on them. This function's own default
    (``False``) is for a direct Tier-2 caller with no CLI in front of it;
    the CLI itself resolves the per-format default via
    ``cli_compare_options._resolve_demangle`` before calling here.

    The release recommendation is unconditionally included in every
    human-facing format (markdown/review) and in JSON's own ``summary``
    block — there is no longer a flag suppressing it (CLI cleanup phase two,
    PR 1: ``--recommend`` removed as a no-op-by-default CLI opt-in).

    ``stat``/``show_recommendation`` are compatibility shims for existing
    Tier-2 Python API callers (this function is exported via
    ``abicheck.service.__all__``) — the CLI's own ``--stat``/``--recommend``
    flags are gone, but a signature change here is a separate, unannounced
    break this PR's own docs never claimed (Codex review). ``show_recommendation``
    stays a real, effective toggle for the markdown renderer, default
    ``False`` — the exact pre-removal default (Codex review, fresh evidence,
    second round: an earlier revision changed this default to ``True`` to
    match the CLI's new unconditional-inclusion behaviour, but that silently
    changed what an *existing* Tier-2 caller gets when it omits this keyword
    entirely, which is a public-API default change this PR's docs never
    announced either — only the CLI flag removal was). The CLI's own
    unconditional inclusion is instead achieved by its wrapper
    (``cli._render_output``) passing ``show_recommendation=True`` explicitly,
    not by changing this function's default. A yet-earlier revision also
    hard-coded ``True`` into the ``to_markdown`` call below regardless of
    what the caller passed, silently reintroducing the recommendation
    section for a caller that explicitly asked it be suppressed — fixed
    first, and unaffected by this default-value correction. Only
    ``review``'s own unconditional inclusion (above) and JSON's unconditional
    ``release_recommendation`` field are unaffected by this parameter — those
    never had a suppressing flag to restore. Prefer ``fmt=ONELINE_FORMAT``
    directly in new code.

    ``stat=True`` reproduces the old ``--stat`` boolean's own format-dependent
    dispatch, not a single fixed replacement — the pre-removal behaviour it
    stands in for was itself three different outcomes depending on *fmt*:
    ``to_stat_json`` (a summary-only JSON object, no ``changes`` array) for
    ``fmt="json"``; ``fmt="junit"`` was *never* short-circuited by ``stat`` at
    all (the pre-removal code's own guard was ``if stat and fmt != "junit":
    ...``) and always fell through to real JUnit XML, since a JUnit consumer
    needs the structured `<testsuite>` document regardless of ``--stat``; and
    ``to_stat`` (a human one-line string) for every other *fmt*, matching
    plain ``fmt=ONELINE_FORMAT``. A caller doing ``render_output("json", ...,
    stat=True)`` and feeding the result to ``json.loads()``, or
    ``render_output("junit", ..., stat=True)`` and feeding the result to an
    XML parser, must keep getting that shape back, not human text (Codex
    review, two rounds — an earlier revision of this shim collapsed every
    case but JSON onto ``to_stat``, silently breaking a JUnit caller too).

    Raises:
        ValidationError: For unrecognised output format.
    """
    if stat and fmt == "json":
        return to_stat_json(
            result,
            severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            show_only=show_only,
            contract_evaluation=contract_evaluation,
        )

    if (stat and fmt != "junit") or fmt == ONELINE_FORMAT:
        return to_stat(result, severity_config=severity_config)

    if fmt == "json":
        return _render_json_output(
            result,
            old,
            new,
            follow_deps=follow_deps,
            show_only=show_only,
            report_mode=report_mode,
            show_impact=show_impact,
            severity_config=severity_config,
            require_complete_analysis=require_complete_analysis,
            contract_evaluation=contract_evaluation,
        )

    if fmt == "sarif":
        from .sarif import to_sarif_str

        return to_sarif_str(
            result,
            show_only=show_only,
            report_mode=report_mode,
            severity_config=severity_config,
        )

    if fmt == "html":
        from .html_report import generate_html_report

        return generate_html_report(
            result,
            lib_name=old.library,
            old_version=old.version,
            new_version=new.version if new else "new",
            old_symbol_count=result.old_symbol_count,
            show_only=show_only,
            show_impact=show_impact,
            severity_config=severity_config,
            demangle=demangle,
        )

    if fmt == "junit":
        from .junit_report import to_junit_xml

        return to_junit_xml(
            result,
            old,
            show_only=show_only,
            severity_config=severity_config,
            report_mode=report_mode,
        )

    if fmt == "review":
        from .reporter import to_review_digest

        txt = to_review_digest(result, severity_config=severity_config)
        if demangle:
            from .demangle import demangle_text

            txt = demangle_text(txt)
        return txt

    _SUPPORTED_FORMATS = {"json", "sarif", "html", "junit", "markdown", "md", "review"}
    if fmt not in _SUPPORTED_FORMATS:
        raise ValidationError(
            f"Unsupported output format: {fmt!r} (expected one of {sorted(_SUPPORTED_FORMATS)})"
        )

    # Default: markdown. show_recommendation defaults to False here (CLI
    # cleanup phase two, PR 1: --recommend removed as a CLI flag -- this
    # function's own default stays the exact pre-removal Tier-2 Python API
    # value, per this docstring's own explanation above). The CLI's own
    # wrapper (cli._render_output) explicitly passes
    # show_recommendation=True, so its output stays unconditional, matching
    # review's own unconditional inclusion above and JSON's unconditional
    # release_recommendation field -- but that is an explicit override at
    # the CLI's own call site, not this function's default. A direct Tier-2
    # caller omitting the keyword (or passing show_recommendation=False
    # explicitly) still gets it suppressed here, same as before this PR
    # (Codex review, fresh evidence -- an earlier revision hard-coded True
    # regardless of the caller's own value).
    md = to_markdown(
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
        show_recommendation=show_recommendation,
        contract_evaluation=contract_evaluation,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        md += _render_deps_section_md(old, new)
    if demangle:
        from .demangle import demangle_text

        md = demangle_text(md)
    return md


def _render_json_output(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot | None,
    *,
    follow_deps: bool,
    show_only: str | None,
    report_mode: str,
    show_impact: bool,
    severity_config: SeverityConfig | None,
    require_complete_analysis: bool = False,
    contract_evaluation: bool = False,
) -> str:
    """Render comparison result as JSON, optionally including dependency info."""
    base = to_json(
        result,
        show_only=show_only,
        report_mode=report_mode,
        show_impact=show_impact,
        severity_config=severity_config,
        require_complete_analysis=require_complete_analysis,
        contract_evaluation=contract_evaluation,
    )
    if follow_deps and (old.dependency_info or (new and new.dependency_info)):
        import json
        from dataclasses import asdict

        d = json.loads(base)
        if old.dependency_info:
            d["old_dependency_info"] = asdict(old.dependency_info)
        if new and new.dependency_info:
            d["new_dependency_info"] = asdict(new.dependency_info)
        return json.dumps(d, indent=2)
    return base


def _render_deps_section_md(old: AbiSnapshot, new: AbiSnapshot | None) -> str:
    """Append dependency summary section to markdown output."""
    lines: list[str] = ["", "## Dependency Analysis", ""]

    for label, snap in [("Old", old), ("New", new)]:
        if snap is None or snap.dependency_info is None:
            continue
        info = snap.dependency_info
        lines.append(f"### {label} version (`{snap.version}`)")
        lines.append("")

        if info.nodes:
            lines.append(f"**Dependencies**: {len(info.nodes)} resolved DSOs")
            for node in info.nodes:
                raw_depth = node.get("depth", 0)
                depth = raw_depth if isinstance(raw_depth, int) else 0
                indent = "  " * depth
                reason = node.get("resolution_reason", "")
                lines.append(f"  {indent}- `{node.get('soname', '?')}` ({reason})")
            lines.append("")

        if info.bindings_summary:
            lines.append("**Bindings**:")
            for status, count in sorted(info.bindings_summary.items()):
                lines.append(f"  - `{status}`: {count}")
            lines.append("")

        if info.unresolved:
            lines.append("**Unresolved libraries**:")
            for u in info.unresolved:
                lines.append(
                    f"  - `{u.get('soname', '?')}` needed by `{u.get('consumer', '?')}`"
                )
            lines.append("")

        if info.missing_symbols:
            lines.append(f"**Missing symbols**: {len(info.missing_symbols)}")
            for ms in info.missing_symbols[:10]:
                ver = f"@{ms['version']}" if ms.get("version") else ""
                lines.append(f"  - `{ms['symbol']}{ver}`")
            if len(info.missing_symbols) > 10:
                lines.append(f"  - ... +{len(info.missing_symbols) - 10} more")
            lines.append("")

    return "\n".join(lines)
