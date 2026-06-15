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

"""HTML report generator for application compatibility (appcompat) results.

Produces a self-contained HTML report showing whether a specific application
is affected by ABI changes in a library upgrade.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Shared page chrome (document frame, verdict palette, footer) + the change table.
from .html_report import _changes_table
from .html_template import _VERDICT_STYLE, render_document, render_footer


def appcompat_to_html(result: object) -> str:
    """Generate a self-contained HTML report for an AppCompatResult."""
    h = html.escape

    verdict = getattr(result, "verdict", None)
    v_label: str = verdict.value if verdict is not None and hasattr(verdict, "value") else str(verdict or "UNKNOWN")
    fg, bg = _VERDICT_STYLE.get(v_label, ("#212121", "#f5f5f5"))

    app_path = getattr(result, "app_path", "")
    old_lib = getattr(result, "old_lib_path", "")
    new_lib = getattr(result, "new_lib_path", "")
    required_count = getattr(result, "required_symbol_count", 0)
    coverage = getattr(result, "symbol_coverage", 0.0)
    missing = getattr(result, "missing_symbols", [])
    missing_ver = getattr(result, "missing_versions", [])
    breaking = getattr(result, "breaking_for_app", [])
    irrelevant = getattr(result, "irrelevant_for_app", [])
    total_changes = len(breaking) + len(irrelevant)
    full_diff = getattr(result, "full_diff", None)

    verdict_icon = {
        "BREAKING": "\U0001f534",
        "COMPATIBLE": "\U0001f7e2",
        "NO_CHANGE": "\U0001f535",
        "API_BREAK": "\U0001f7e0",
        "COMPATIBLE_WITH_RISK": "\U0001f7e0",
    }.get(v_label, "\u26aa")

    # File metadata
    file_info_html = ""
    old_meta = getattr(full_diff, "old_metadata", None) if full_diff else None
    new_meta = getattr(full_diff, "new_metadata", None) if full_diff else None
    if old_meta or new_meta:
        def _row(label: str, old_val: str, new_val: str) -> str:
            return f"<tr><th>{label}</th><td>{h(old_val)}</td><td>{h(new_val)}</td></tr>"

        old_path = getattr(old_meta, "path", "\u2014") if old_meta else "\u2014"
        new_path = getattr(new_meta, "path", "\u2014") if new_meta else "\u2014"
        old_sha_val = (getattr(old_meta, "sha256", None) or "\u2014") if old_meta else "\u2014"
        old_sha = old_sha_val[:16] + "\u2026"
        new_sha_val = (getattr(new_meta, "sha256", None) or "\u2014") if new_meta else "\u2014"
        new_sha = new_sha_val[:16] + "\u2026"
        old_size = str(getattr(old_meta, "size_bytes", 0)) if old_meta else "\u2014"
        new_size = str(getattr(new_meta, "size_bytes", 0)) if new_meta else "\u2014"
        file_info_html = f"""<div class='summary-section'>
  <h3>Library Files</h3>
  <table class='summary-table'>
    <thead><tr><th></th><th>Old</th><th>New</th></tr></thead>
    <tbody>
      {_row("Path", old_path, new_path)}
      {_row("SHA-256", old_sha, new_sha)}
      {_row("Size (bytes)", old_size, new_size)}
    </tbody>
  </table>
</div>"""

    # Confidence section
    confidence_html = ""
    conf = getattr(full_diff, "confidence", None) if full_diff else None
    if conf is not None:
        conf_val = conf.value if hasattr(conf, "value") else str(conf)
        tiers = getattr(full_diff, "evidence_tiers", []) or []
        conf_color = {"high": "#1b5e20", "medium": "#e65100", "low": "#b71c1c"}.get(
            conf_val, "#212121"
        )
        tier_badges = " ".join(
            f"<span class='kind-badge'>{h(t)}</span>" for t in tiers
        ) if tiers else "<em>none</em>"
        confidence_html = f"""<div class='summary-section'>
  <h3>\U0001f50d Analysis Confidence</h3>
  <table class='summary-table'>
    <tbody>
      <tr><th>Confidence</th><td><strong style='color:{conf_color}'>{h(conf_val.upper())}</strong></td></tr>
      <tr><th>Evidence tiers</th><td>{tier_badges}</td></tr>
    </tbody>
  </table>
</div>"""

    # Missing symbols section
    missing_html = ""
    if missing:
        rows = "\n".join(
            f"<tr><td><code>{h(s)}</code></td></tr>" for s in missing
        )
        missing_html = f"""<div class='section section-removed'>
  <h3>\u26d4 Missing Symbols ({len(missing)})</h3>
  <table class='changes'><thead><tr><th>Symbol</th></tr></thead>
  <tbody>{rows}</tbody></table>
</div>"""

    # Missing versions section
    missing_ver_html = ""
    if missing_ver:
        rows = "\n".join(
            f"<tr><td><code>{h(v)}</code></td></tr>" for v in missing_ver
        )
        missing_ver_html = f"""<div class='section section-changed'>
  <h3>\u26a0\ufe0f Missing Symbol Versions ({len(missing_ver)})</h3>
  <table class='changes'><thead><tr><th>Version</th></tr></thead>
  <tbody>{rows}</tbody></table>
</div>"""

    # Relevant changes
    relevant_html = ""
    if breaking:
        relevant_html = f"""<div class='section section-removed'>
  <h3>\u274c Relevant Changes ({len(breaking)} of {total_changes} total)</h3>
  <p style='padding:0 16px; font-size:0.88em; color:#666;'>
    These library changes affect symbols your application uses.
  </p>
  {_changes_table(list(breaking))}
</div>"""
    elif total_changes > 0:
        relevant_html = f"""<div class='section section-added'>
  <h3>\u2705 No Relevant Changes (0 of {total_changes} total)</h3>
  <p class='empty'>None of the library's ABI changes affect your application.</p>
</div>"""

    # Irrelevant changes
    irrelevant_html = ""
    if irrelevant:
        irrelevant_html = f"""<div class='section' style='opacity:0.7'>
  <h3>\u2139\ufe0f Irrelevant Changes ({len(irrelevant)})</h3>
  <p style='padding:0 16px; font-size:0.85em; color:#999;'>
    These library changes do NOT affect your application.
  </p>
  {_changes_table(list(irrelevant))}
</div>"""

    body = f"""
<div class="header">
  <h1>Application Compatibility Report</h1>
  <div class="meta">
    App: <strong>{h(app_path)}</strong> &nbsp;|&nbsp;
    Library: {h(old_lib)} \u2192 {h(new_lib)} &nbsp;|&nbsp;
    Generated by <strong>abicheck</strong>
  </div>
</div>

<div class="verdict-box" style="background:{bg}; color:{fg}; border-left:6px solid {fg};">
  <h2>{verdict_icon} Verdict: {h(v_label)}</h2>
  <div class="bc-metric">
    Symbol Coverage: <strong>{coverage:.0f}%</strong>
    <span style="font-size:0.82em; opacity:0.75">
      ({required_count} required symbols)
    </span>
    &nbsp;&nbsp;
    <span style="font-size:0.85em;">
      Relevant: <strong>{len(breaking)}</strong>
      &nbsp;|&nbsp; Irrelevant: <strong>{len(irrelevant)}</strong>
      &nbsp;|&nbsp; Missing: <strong>{len(missing)}</strong>
    </span>
  </div>
</div>

{file_info_html}
{confidence_html}
{missing_html}
{missing_ver_html}
{relevant_html}
{irrelevant_html}

{render_footer("Application Compatibility Report")}
"""
    return render_document(title=f"AppCompat Report: {h(app_path)}", body=body)


def write_appcompat_html(result: object, path: Path) -> None:
    """Write an AppCompat HTML report to *path*."""
    path.write_text(appcompat_to_html(result), encoding="utf-8")
