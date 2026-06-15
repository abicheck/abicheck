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

"""Shared HTML page chrome for abicheck's native report renderers.

This is the one seam every abicheck-native HTML report renders through: the
document wrapper (DOCTYPE / head / embedded stylesheet / body frame), the
verdict colour palette, and the footer. The three renderers —
``html_report.generate_html_report``, ``appcompat_html.appcompat_to_html`` and
``stack_html.stack_to_html`` — supply only their domain content as the document
*body*; the chrome lives here once.

Owning the chrome in one module is the point: a stylesheet, layout or
accessibility fix is edited here instead of being hunted across three renderers
that previously each re-emitted the same ``<!DOCTYPE html> … </html>`` skeleton
and footer by hand (architecture-deepening candidate N-A).

This module is for the abicheck-native palette only. The ABICC-clone report
format (``_COMPAT_CSS`` in ``html_report``) is a deliberately distinct chrome
that mirrors abi-compliance-checker's own markup and stays separate.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Verdict styling — matches ABICC's visual palette
# ---------------------------------------------------------------------------

_VERDICT_STYLE: dict[str, tuple[str, str]] = {
    "BREAKING": ("#b71c1c", "#ffcdd2"),
    "COMPATIBLE_WITH_RISK": ("#e65100", "#fff3e0"),  # orange — deployment caution
    "COMPATIBLE": ("#1b5e20", "#c8e6c9"),
    "NO_CHANGE": ("#0d47a1", "#bbdefb"),
    "API_BREAK": ("#e65100", "#ffe0b2"),
}

# ---------------------------------------------------------------------------
# CSS — ABICC visual style, no external deps
# ---------------------------------------------------------------------------

_CSS = """\
*, *::before, *::after { box-sizing: border-box; }
body { font-family: Arial, sans-serif; margin: 0; padding: 0; background: #f5f5f5; color: #212121; }

/* ---- header ---- */
.header { padding: 20px 32px; background: #263238; color: #fff; }
.header h1 { margin: 0 0 4px; font-size: 1.4em; letter-spacing: .02em; }
.header .meta { font-size: 0.88em; color: #b0bec5; }

/* ---- verdict banner ---- */
.verdict-box { margin: 20px 32px 0; padding: 14px 22px; border-radius: 6px; }
.verdict-box h2 { margin: 0 0 6px; font-size: 1.2em; }
.bc-metric { font-size: 1em; margin-top: 4px; }
.bc-metric strong { font-size: 1.1em; }

/* ---- nav bar ---- */
.nav { margin: 14px 32px 0; display: flex; gap: 8px; flex-wrap: wrap; }
.nav a { display: inline-block; padding: 5px 12px; border-radius: 4px;
          background: #eceff1; color: #37474f; font-size: 0.85em;
          text-decoration: none; border: 1px solid #cfd8dc; }
.nav a:hover { background: #cfd8dc; }
.nav a.breaking { background: #ffcdd2; border-color: #e57373; color: #b71c1c; }
.nav a.added    { background: #c8e6c9; border-color: #81c784; color: #1b5e20; }

/* ---- summary table ---- */
.summary-section { margin: 20px 32px 0; background: #fff; border-radius: 6px;
                   box-shadow: 0 1px 3px rgba(0,0,0,.1); overflow: hidden; }
.summary-section h3 { margin: 0; padding: 10px 16px; background: #eceff1;
                      font-size: .95em; border-bottom: 1px solid #cfd8dc; }
.summary-table { width: 100%; border-collapse: collapse; font-size: 0.88em; }
.summary-table th { background: #f5f5f5; padding: 7px 12px; text-align: left;
                    border-bottom: 2px solid #e0e0e0; }
.summary-table td { padding: 6px 12px; border-bottom: 1px solid #eeeeee; }
.summary-table tr:last-child td { border-bottom: none; }
.num { font-weight: bold; font-family: monospace; }
.num-red  { color: #b71c1c; }
.num-green { color: #1b5e20; }
.num-blue  { color: #1565c0; }

/* ---- change sections ---- */
.section { margin: 16px 32px 0; background: #fff; border-radius: 6px;
           box-shadow: 0 1px 3px rgba(0,0,0,.1); overflow: hidden; }
.section h3 { margin: 0; padding: 10px 16px; font-size: .95em;
              border-bottom: 1px solid #cfd8dc; }
.section-removed h3 { background: #ffebee; color: #b71c1c; }
.section-changed h3 { background: #fff8e1; color: #e65100; }
.section-added   h3 { background: #e8f5e9; color: #1b5e20; }
.section-suppressed h3 { background: #f3e5f5; color: #6a1b9a; }

/* ---- changes table ---- */
table.changes { width: 100%; border-collapse: collapse; font-size: 0.87em; }
table.changes th { background: #fafafa; padding: 7px 12px; text-align: left;
                   border-bottom: 2px solid #e0e0e0; white-space: nowrap; }
table.changes td { padding: 7px 12px; border-bottom: 1px solid #eeeeee; vertical-align: top; }
table.changes tr:last-child td { border-bottom: none; }
.kind-badge { font-family: monospace; font-size: 0.82em; color: #37474f;
              background: #eceff1; padding: 2px 6px; border-radius: 3px;
              white-space: nowrap; }
.sym { font-family: monospace; font-size: 0.85em; }
.sym abbr { text-decoration: underline dotted #9e9e9e; cursor: help; }
.empty { padding: 14px 16px; color: #9e9e9e; font-style: italic; font-size: 0.88em; }
.cat-badge { font-size: 0.78em; background: #e3f2fd; color: #1565c0;
             padding: 1px 5px; border-radius: 3px; white-space: nowrap; }

/* ---- footer ---- */
footer { margin: 20px 32px 32px; padding: 12px 16px; font-size: 0.8em;
         color: #9e9e9e; border-top: 1px solid #e0e0e0; }
"""


def render_document(*, title: str, body: str, css: str = _CSS) -> str:
    """Wrap a report *body* in the shared abicheck-native page skeleton.

    *title* and *body* must already be HTML-escaped/assembled by the caller;
    this only supplies the surrounding document chrome. *body* is the exact
    markup that sits between ``<body>`` and ``</body>`` (the renderers build it
    so it begins with a blank line and ends after the footer, preserving the
    historical whitespace).
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_footer(subtitle: str) -> str:
    """Render the shared report footer with a per-report *subtitle*.

    Returns the ``<footer>…</footer>`` block with no trailing newline, matching
    how the renderers previously inlined it.
    """
    return (
        "<footer>\n"
        f"  Generated by <strong>abicheck</strong> · {subtitle} ·\n"
        '  <a href="https://github.com/napetrov/abicheck" style="color:#9e9e9e;">napetrov/abicheck</a>\n'
        "</footer>"
    )
