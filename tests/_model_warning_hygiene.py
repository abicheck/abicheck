"""Warning-hygiene gate: a WARNING from ``abicheck.model.*`` fails the test.

Field report H4 shipped a misleading "Duplicate type names skipped
(first-wins)" warning for two distinct ``algo_a::Parameter`` /
``algo_b::Parameter`` records. Nothing noticed, because a log line is not a
test failure. A test opts in with ``@pytest.mark.model_warning_hygiene``
(``conftest.py`` wires the marker to :func:`model_warning_gate`); every
``WARNING``-or-above record a logger under ``abicheck.model`` emits during the
test then fails it, unless its message matches an entry of
:data:`ALLOWED_MODEL_WARNINGS` -- each with the reason it is expected.

The handler sits on the ``abicheck.model`` logger itself rather than relying
on ``caplog``, so a CLI run in-process that reconfigures root logging cannot
hide a record from the gate.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager

MODEL_LOGGER = "abicheck.model"

#: ``{regex over the formatted message: reason it is an expected warning}``.
#: Empty by design: an entry needs a reason a reviewer accepts, not a
#: convenience for a red test.
ALLOWED_MODEL_WARNINGS: dict[str, str] = {}

#: ``{regex over one dropped key: reason}`` for the first-wins index warning
#: (``model.first_wins_index.warn_dropped``). That warning lists every
#: dropped key in one line, so it is allowed only when *each* key fullmatches
#: an entry here -- a new colliding name still fails. Found by the
#: ``catalog/cases`` depth-metamorphic run (case80, case126).
ALLOWED_FIRST_WINS_KEYS: dict[str, str] = {
    r"(wcschr|wcsrchr|wcspbrk|wcsstr|wcswcs|wmemchr|strchr|strrchr|strpbrk|strstr|memchr)": (
        "glibc's C++ <wchar.h>/<string.h> declare const and non-const overloads "
        "that share one asm name (one symbol, two declarations): a genuine "
        "duplicate key, and first-wins is the correct resolution"
    ),
    r"__abicheck_ctor__std::.*": (
        "castxml placeholder key for an implicitly-declared std:: template "
        "constructor, declared once per instantiation context; libstdc++ "
        "internals, never the library's own surface"
    ),
}


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


_FIRST_WINS = "Duplicate %s skipped (first-wins) in %s: %s"


def _first_wins_allowed(rec: logging.LogRecord, keys_allowed: dict[str, str]) -> bool:
    if rec.msg != _FIRST_WINS or not isinstance(rec.args, tuple) or len(rec.args) != 3:
        return False
    keys = [k for k in re.split(r" \(\u00d7\d+\)(?:, |$)", str(rec.args[2])) if k]
    patterns = [re.compile(p) for p in keys_allowed]
    return bool(keys) and all(any(p.fullmatch(k) for p in patterns) for k in keys)


def unexpected(
    records: list[logging.LogRecord],
    allowed: dict[str, str] | None = None,
    first_wins_keys: dict[str, str] | None = None,
) -> list[str]:
    """The ``logger: message`` of every record no allowlist entry covers."""
    patterns = [
        re.compile(p) for p in (ALLOWED_MODEL_WARNINGS if allowed is None else allowed)
    ]
    keys = ALLOWED_FIRST_WINS_KEYS if first_wins_keys is None else first_wins_keys
    out = []
    for rec in records:
        if rec.levelno < logging.WARNING:
            continue
        msg = rec.getMessage()
        if any(p.search(msg) for p in patterns) or _first_wins_allowed(rec, keys):
            continue
        out.append(f"{rec.name}: {msg}")
    return out


@contextmanager
def model_warning_gate() -> Iterator[_Collector]:
    """Collect ``abicheck.model.*`` warnings; raise on an unexpected one."""
    logger = logging.getLogger(MODEL_LOGGER)
    handler = _Collector()
    old_level = logger.level
    if logger.getEffectiveLevel() > logging.WARNING:
        logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    bad = unexpected(handler.records)
    if bad:
        raise AssertionError(
            "unexpected WARNING from abicheck.model.* (add a reasoned entry to "
            "tests/_model_warning_hygiene.py only if it is "
            "expected):\n  " + "\n  ".join(bad)
        )
