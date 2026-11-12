"""Extractor status is a closed vocabulary: ``ok | partial | failed | skipped``.

It used to be a bare string, so a misspelled status was stored and later read
by consumers that compare against the four spellings and match none. Oracle:
the literal spellings written in this test, not the enum under test.
"""

from __future__ import annotations

import json

import pytest

from abicheck.buildsource.extractor import CollectionResult, NormalizationResult
from abicheck.buildsource.model import ExtractorRecord, ExtractorStatus

SPELLINGS = ("ok", "partial", "failed", "skipped")
WRONG = ("OK", "fail", "error", "partially", "", "complete")


def test_the_vocabulary_is_exactly_the_four_spellings() -> None:
    assert sorted(s.value for s in ExtractorStatus) == sorted(SPELLINGS)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_serialized_spelling_is_unchanged(spelling: str) -> None:
    rec = ExtractorRecord(name="x", status=spelling)
    text = json.dumps(rec.to_dict())
    assert f'"status": "{spelling}"' in text
    back = ExtractorRecord.from_dict(json.loads(text))
    assert back.status == spelling
    assert back.diagnostics == []


@pytest.mark.parametrize(
    "ctor", [ExtractorRecord, CollectionResult, NormalizationResult]
)
@pytest.mark.parametrize("wrong", WRONG)
def test_a_producer_cannot_record_an_unknown_status(ctor, wrong: str) -> None:  # type: ignore[no-untyped-def]
    kwargs = {"name": "x"} if ctor is ExtractorRecord else {}
    with pytest.raises(ValueError):
        ctor(status=wrong, **kwargs)


@pytest.mark.parametrize("wrong", WRONG)
def test_an_unknown_stored_status_reads_as_failed_not_ok(wrong: str) -> None:
    rec = ExtractorRecord.from_dict({"name": "x", "status": wrong})
    assert rec.status == "failed"
    assert any(repr(wrong) in d for d in rec.diagnostics)


def test_a_record_without_status_stays_ok() -> None:
    assert ExtractorRecord.from_dict({"name": "x"}).status == "ok"


def test_the_legacy_present_spelling_loads_as_ok() -> None:
    rec = ExtractorRecord.from_dict({"name": "bazel", "status": "present"})
    assert rec.status == "ok"
    assert rec.diagnostics == []


@pytest.mark.parametrize("raw", [[], {}, ["ok"], {"ok": 1}, 3, None, True])
def test_a_non_string_stored_status_reads_as_failed_and_loads(raw: object) -> None:
    rec = ExtractorRecord.from_dict({"name": "x", "status": raw})
    assert rec.status == "failed"
    assert any(repr(raw) in d for d in rec.diagnostics)
