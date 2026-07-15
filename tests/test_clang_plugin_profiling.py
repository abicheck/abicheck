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

"""Regression guard for recommendation P1 #24/#25: the Clang facts plugin's
profiling telemetry must go to a channel separate from the emitted
``source_facts/*.jsonl`` output, and choosing that channel (stderr, the
default, vs. ``ABICHECK_PLUGIN_PROFILE_LOG``) must never perturb the facts a
build produces (execution-policy invariance).

Pure text scan of the plugin source; needs no compiler toolchain, so it runs
in the default fast lane rather than the `clang-plugin` workflow. The
compiled end-to-end behavior (profile line actually lands in the log file,
stderr stays clean) is exercised manually via the `clang-plugin` CI workflow
(no compiler available in the default unit-test lane); this guard keeps the
source-level invariant from silently regressing between those runs.
"""

from __future__ import annotations

from pathlib import Path

_PLUGIN_CPP = (
    Path(__file__).resolve().parent.parent
    / "contrib"
    / "abicheck-clang-plugin"
    / "AbicheckFactsPlugin.cpp"
)


def _read() -> str:
    return _PLUGIN_CPP.read_text(encoding="utf-8")


def test_plugin_source_present() -> None:
    assert _PLUGIN_CPP.is_file()


def test_profile_log_env_var_is_recognized() -> None:
    text = _read()
    assert "ABICHECK_PLUGIN_PROFILE_LOG" in text
    assert "ABICHECK_PLUGIN_PROFILE" in text


def test_profiling_sink_never_touches_facts_file() -> None:
    """``emitProfileLine`` — the function that writes the profiling summary
    line to whichever channel is configured — must never reference the facts
    output (``factsFile``/the ``out`` stream the TU's JSON is written to).
    A profiling sink is execution policy; it must not be able to perturb the
    emitted facts."""
    text = _read()
    start = text.index("inline void emitProfileLine(")
    assert start != -1
    end = text.index("\n}\n", start)
    body = text[start:end]
    assert "factsFile" not in body
    assert "out <<" not in body
    assert "out.write" not in body


def test_facts_file_is_written_before_profiling_summary() -> None:
    """Textual ordering guard: the per-TU facts file write (``out << tu``)
    happens strictly before the profiling summary line is emitted, so the
    profiling block can only run as a side effect *after* the facts a build
    depends on are already durable on disk."""
    text = _read()
    write_idx = text.index('out << tu << "\\n";')
    profile_idx = text.index("emitProfileLine(line);")
    assert write_idx < profile_idx


def test_only_one_profile_emission_call_site() -> None:
    """Exactly one call site emits the profiling line -- if a second one is
    ever added it must be reviewed for the same stderr/log-file/never-touch-
    facts discipline as this one."""
    text = _read()
    assert text.count("emitProfileLine(line);") == 1
