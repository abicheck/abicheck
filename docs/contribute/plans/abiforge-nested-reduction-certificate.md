# Finite certificate for independent inner and outer packing

The certificate maps all 256 recorded configurations to 30 canonical
layout representatives. Each configuration and its representative agree
on all 29 observed byte quantities. The projection includes enum size and
alignment, each record's size and alignment, every member's offset and
extent, and the array element stride.

## Reproduce

Run from the checkout root with Python 3.11 or later. Verification uses
the saved `campaign result`
and requires only the Python standard library.

```sh
python -B research/nested-reduction-cert/certificate.py build
python -B research/nested-reduction-cert/certificate.py verify
python -B research/nested-reduction-cert/certificate.py challenge
python -B -m unittest discover -s research/nested-reduction-cert -v
```

The build is deterministic for the same input bytes. The test suite
rebuilds in a temporary directory and checks byte equality with the
`checked certificate`. Use `--results` and
`--certificate` to select different input and output paths.

## Verification contract

The verifier requires one matched probe for each combination of O0/O2,
upper enumerator 255/256/65535/65536, inner and outer cap 1/2/4/8, and
default/short enum mode. It checks every observed vector against the
required fields and the recorded oracle result. It reconstructs classes
directly from the probe rows. The canonical representative is the first
configuration in matrix order, sorted by optimization, numeric upper
enumerator, numeric inner cap, numeric outer cap, then enum mode.
Every representative maps to itself.

Verification checks exact domain coverage, representative membership,
complete vector equality, canonical selection, and class counts. It
binds the certificate to the input file's raw SHA-256 and recorded
environment. The digest checks identity against the supplied campaign
file. Compiler and environment identity come from that file's recorded
metadata.

The recorded run used Debian GCC 14.2.0-19 targeting
`x86_64-linux-gnu`. The reduction covers storage layout for this finite
fixture and compiler run. Enum values and compiler flags are
configuration coordinates outside the equality projection. The
`campaign's checker sample`
records cases where those differences affect comparison results within
a layout class.

## Shared-pack-only challenge

The challenge tries all four shared caps at the same optimization, enum
range, and enum mode for each configuration. When none matches, it
falls back to the diagonal at the original inner cap. The verifier
rejects the resulting mapping with **96 full-layout mismatches**,
exactly the configurations whose outer cap is lower than their inner
cap. For example, `O0/u255/i2-o1/default` fails against
`O0/u255/i2-o2/default`. Across all enum settings and both optimization
levels, shared caps cover 12 of the 30 classes, leaving the same 96
configurations across 18 classes without a diagonal equivalent.

## Projection omission search

The search counts unordered pairs that agree after the omission and
differ in the full projection.

| Omitted field | Colliding pairs |
| --- | ---: |
| `Inner.align` | 80 |
| `RecordArray.items.stride` | 0 |
| Both fields | 80 |

`O0/u255/i2-o1/short` and `O0/u255/i4-o1/short` differ only in inner
alignment, at 2 and 4 bytes respectively. Array stride equals
`Inner.size` in all 256 observations, so omitting stride alone merges
no classes in this matrix. The certificate includes stride to preserve
the complete observed projection.

## certificate.py

Save under `research/nested-reduction-cert/`.

```python
#!/usr/bin/env python3
"""Build, verify, or challenge a finite independent-pack layout certificate."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from hashlib import sha256
from itertools import combinations, product
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "independent-pack-campaign" / "results.json"
CERTIFICATE = HERE / "certificate.json"
CAPS = (1, 2, 4, 8)
CONFIGS = {
    f"{opt}/u{limit}/i{inner}-o{outer}/{mode}": (opt, limit, inner, outer, mode)
    for opt, limit, inner, outer, mode in product(
        ("O0", "O2"), (255, 256, 65535, 65536), CAPS, CAPS, ("default", "short")
    )
}
PROJECTION = ["E.size", "E.align"]
for record, fields in (
    ("Inner", ("anchor", "lead", "value", "tail")),
    ("Nested", ("lead", "item", "tail")),
    ("RecordArray", ("lead", "items", "tail")),
):
    PROJECTION.extend(f"{record}.{metric}" for metric in ("size", "align"))
    PROJECTION.extend(
        f"{record}.{field}.{metric}" for field in fields for metric in ("offset", "size")
    )
PROJECTION.append("RecordArray.items.stride")
PROVENANCE_KEYS = {"compiler", "compiler_command", "target", "checker", "python", "git_head"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_campaign(path):
    raw = path.read_bytes()
    data = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError("campaign must be a JSON object")
    environment = data.get("environment")
    if (
        not isinstance(environment, dict)
        or environment.keys() != PROVENANCE_KEYS
        or any(not isinstance(value, str) or not value.strip() for value in environment.values())
    ):
        raise ValueError("invalid campaign provenance")
    rows = data.get("probes")
    if not isinstance(rows, list):
        raise ValueError("campaign probes must be a list")

    observations = {}
    for row in rows:
        case = row.get("case") if isinstance(row, dict) else None
        if not isinstance(case, str) or case not in CONFIGS or case in observations:
            raise ValueError(f"unexpected or duplicate probe: {case!r}")
        facts = row.get("observed")
        if row.get("status") != "matched" or row.get("differences") != {}:
            raise ValueError(f"probe did not match: {case}")
        if (
            not isinstance(facts, dict)
            or facts.keys() != set(PROJECTION)
            or any(type(value) is not int or value < 0 for value in facts.values())
        ):
            raise ValueError(f"invalid observed projection: {case}")
        if row.get("expected") != facts:
            raise ValueError(f"probe differs from recorded expectation: {case}")
        observations[case] = tuple(facts[key] for key in PROJECTION)
    if missing := CONFIGS.keys() - observations.keys():
        raise ValueError(f"probe coverage: missing {len(missing)} configurations, first {min(missing)}")
    provenance = {
        "campaign": "independent-pack-campaign/results.json",
        "results_sha256": sha256(raw).hexdigest(),
        "environment": environment,
    }
    return provenance, observations


def canonical_mapping(observations):
    first = {}
    return {
        case: first.setdefault(observations[case], case)
        for case in CONFIGS
    }


def summary(mapping):
    return {"configurations": len(mapping), "layout_classes": len(set(mapping.values()))}


def build(provenance, observations):
    mapping = canonical_mapping(observations)
    return {
        "schema_version": 1,
        "provenance": provenance,
        "projection_bytes": PROJECTION.copy(),
        "summary": summary(mapping),
        "mapping": mapping,
    }


def layout_mismatches(mapping, observations):
    return [
        case for case in CONFIGS
        if observations[case] != observations[mapping[case]]
    ]


def verify(certificate, provenance, observations):
    if (
        not isinstance(certificate, dict)
        or certificate.keys() != {"schema_version", "provenance", "projection_bytes", "summary", "mapping"}
        or type(certificate["schema_version"]) is not int
        or certificate["schema_version"] != 1
    ):
        raise ValueError("unsupported certificate schema")
    declared = certificate["provenance"]
    if not isinstance(declared, dict) or declared.get("results_sha256") != provenance["results_sha256"]:
        raise ValueError("campaign SHA-256 digest mismatch")
    if declared != provenance:
        raise ValueError("campaign provenance mismatch")
    if certificate["projection_bytes"] != PROJECTION:
        raise ValueError("certificate projection must contain all 29 layout observations")
    mapping = certificate["mapping"]
    if not isinstance(mapping, dict) or mapping.keys() != CONFIGS.keys():
        raise ValueError("mapping coverage must match the full configuration domain")
    for case, rep in mapping.items():
        if not isinstance(rep, str) or rep not in CONFIGS:
            raise ValueError(f"invalid representative for {case}: {rep!r}")
    if mismatches := layout_mismatches(mapping, observations):
        first = mismatches[0]
        raise ValueError(
            f"full layout mismatch for {len(mismatches)} configurations, first {first} -> {mapping[first]}"
        )
    canonical = canonical_mapping(observations)
    for case, rep in canonical.items():
        if mapping[case] != rep:
            raise ValueError(f"noncanonical representative for {case}; expected {rep}")
    if certificate["summary"] != summary(canonical):
        raise ValueError("certificate summary mismatch")


def shared_pack_mapping(observations):
    """Try every same-setting diagonal before falling back to the inner-cap diagonal."""
    mapping = {}
    for case, (opt, limit, inner, _, mode) in CONFIGS.items():
        diagonals = [f"{opt}/u{limit}/i{cap}-o{cap}/{mode}" for cap in CAPS]
        mapping[case] = next(
            (rep for rep in diagonals if observations[rep] == observations[case]),
            f"{opt}/u{limit}/i{inner}-o{inner}/{mode}",
        )
    return mapping


def omission_search(observations, omitted):
    kept = [index for index, key in enumerate(PROJECTION) if key not in omitted]
    groups = defaultdict(list)
    for case in CONFIGS:
        groups[tuple(observations[case][index] for index in kept)].append(case)

    count = 0
    witness = None
    for group in groups.values():
        for left, right in combinations(group, 2):
            if observations[left] == observations[right]:
                continue
            count += 1
            if witness is None:
                witness = {
                    "cases": [left, right],
                    "differences_bytes": {
                        key: [a, b]
                        for key, a, b in zip(PROJECTION, observations[left], observations[right])
                        if a != b
                    },
                }
    return {"colliding_pairs": count, "first_witness": witness}


def challenge(observations):
    mapping = shared_pack_mapping(observations)
    mismatches = layout_mismatches(mapping, observations)
    full_classes = set(observations.values())
    diagonal_classes = {observations[rep] for rep in mapping.values()}
    return {
        "shared_pack_only": {
            "same_settings_unrepresented_configurations": len(mismatches),
            "globally_unrepresented_layout_classes": len(full_classes - diagonal_classes),
            "globally_unrepresented_configurations": sum(
                layout not in diagonal_classes for layout in observations.values()
            ),
            "first_mismatch": [mismatches[0], mapping[mismatches[0]]] if mismatches else None,
        },
        "omissions": {
            "inner_alignment": omission_search(observations, {"Inner.align"}),
            "array_stride": omission_search(observations, {"RecordArray.items.stride"}),
            "both": omission_search(observations, {"Inner.align", "RecordArray.items.stride"}),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify", "challenge"))
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--certificate", type=Path, default=CERTIFICATE)
    args = parser.parse_args()
    try:
        provenance, observations = read_campaign(args.results)
        if args.command == "challenge":
            result = challenge(observations)
        else:
            if args.command == "build":
                certificate = build(provenance, observations)
            else:
                certificate = json.loads(
                    args.certificate.read_bytes(), object_pairs_hook=unique_object
                )
            verify(certificate, provenance, observations)
            if args.command == "build":
                args.certificate.write_text(json.dumps(certificate, indent=2) + "\n")
            result = certificate["summary"]
    except (OSError, ValueError) as error:
        parser.exit(1, f"certificate error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

```

## test_certificate.py

Save under `research/nested-reduction-cert/`.

```python
"""Regression checks against the recorded independent-pack campaign."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import certificate as cert


class CertificateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provenance, cls.observations = cert.read_campaign(cert.RESULTS)

    def setUp(self):
        self.certificate = cert.build(self.provenance, self.observations)

    def verify(self):
        cert.verify(self.certificate, self.provenance, self.observations)

    def test_complete_canonical_mapping(self):
        self.verify()
        mapping = self.certificate["mapping"]
        self.assertEqual((len(mapping), len(set(mapping.values()))), (256, 30))
        self.assertEqual(len(cert.PROJECTION), 29)
        self.assertTrue(all(mapping[rep] == rep for rep in mapping.values()))
        self.assertEqual(
            cert.canonical_mapping(dict(reversed(list(self.observations.items())))), mapping
        )
        self.assertEqual(mapping["O2/u65536/i1-o8/short"], "O0/u255/i1-o1/default")

    def test_invalid_certificate(self):
        case = "O0/u255/i1-o1/default"
        for section, key, value, message in (
            ("provenance", "results_sha256", "0" * 64, "digest mismatch"),
            ("provenance", "environment", {}, "provenance mismatch"),
            ("mapping", case, [], "invalid representative"),
            ("mapping", case, "O2/u255/i1-o1/default", "noncanonical"),
            ("mapping", case, "O0/u255/i1-o1/short", "full layout mismatch"),
            ("summary", "layout_classes", 29, "summary mismatch"),
        ):
            with self.subTest(message=message):
                original = self.certificate
                self.certificate = copy.deepcopy(original)
                self.certificate[section][key] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.verify()
                self.certificate = original

    def test_mapping_coverage_and_projection(self):
        for case in ("O0/u255/i1-o1/default", "extra"):
            with self.subTest(case=case):
                self.certificate = cert.build(self.provenance, self.observations)
                mapping = self.certificate["mapping"]
                if case in mapping:
                    del mapping[case]
                else:
                    mapping[case] = case
                with self.assertRaisesRegex(ValueError, "mapping coverage"):
                    self.verify()
        for field in ("Inner.align", "RecordArray.items.stride", "E.align"):
            with self.subTest(field=field):
                self.certificate = cert.build(self.provenance, self.observations)
                self.certificate["projection_bytes"].remove(field)
                with self.assertRaisesRegex(ValueError, "certificate projection"):
                    self.verify()

    def test_shared_pack_only_fails_full_projection(self):
        self.certificate["mapping"] = cert.shared_pack_mapping(self.observations)
        with self.assertRaisesRegex(ValueError, "full layout mismatch for 96 configurations"):
            self.verify()
        mismatches = cert.layout_mismatches(self.certificate["mapping"], self.observations)
        strictly_lower_outer = {
            case for case, (_, _, inner, outer, _) in cert.CONFIGS.items() if outer < inner
        }
        self.assertEqual(set(mismatches), strictly_lower_outer)
        result = cert.challenge(self.observations)["shared_pack_only"]
        self.assertEqual(result["globally_unrepresented_layout_classes"], 18)
        self.assertEqual(result["globally_unrepresented_configurations"], 96)

    def test_omission_search(self):
        result = cert.challenge(self.observations)["omissions"]
        alignment = result["inner_alignment"]
        self.assertEqual(alignment["colliding_pairs"], 80)
        self.assertEqual(alignment["first_witness"], {
            "cases": ["O0/u255/i2-o1/short", "O0/u255/i4-o1/short"],
            "differences_bytes": {"Inner.align": [2, 4]},
        })
        self.assertEqual(result["array_stride"], {"colliding_pairs": 0, "first_witness": None})
        self.assertEqual(result["both"], alignment)
        stride = cert.PROJECTION.index("RecordArray.items.stride")
        inner_size = cert.PROJECTION.index("Inner.size")
        self.assertTrue(all(row[stride] == row[inner_size] for row in self.observations.values()))

    def test_invalid_campaign(self):
        data = json.loads(cert.RESULTS.read_bytes())
        # Probe rows alone suffice once the campaign provenance is present.
        data = {key: data[key] for key in ("environment", "probes")}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            first = data["probes"][0]
            for rows, message in (
                (data["probes"][1:], "probe coverage"),
                (data["probes"] + [first], "duplicate probe"),
                ([dict(first, status="failed")], "probe did not match"),
                ([dict(first, observed={})], "invalid observed projection"),
            ):
                with self.subTest(message=message):
                    path.write_text(json.dumps(dict(data, probes=rows)))
                    with self.assertRaisesRegex(ValueError, message):
                        cert.read_campaign(path)

    def test_cli_rebuild_verify_and_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "certificate.json"

            def run(command):
                return subprocess.run(
                    [sys.executable, str(cert.HERE / "certificate.py"), command,
                     "--certificate", str(path)],
                    capture_output=True, text=True, check=False,
                )

            for command in ("build", "verify"):
                result = run(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"configurations": 256, "layout_classes": 30})
            self.assertEqual(path.read_bytes(), cert.CERTIFICATE.read_bytes())
            result = run("challenge")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), cert.challenge(self.observations))
            path.write_text('{"mapping": {}, "mapping": {}}')
            result = run("verify")
            self.assertEqual(result.returncode, 1)
            self.assertIn("duplicate JSON key: mapping", result.stderr)


if __name__ == "__main__":
    unittest.main()

```
