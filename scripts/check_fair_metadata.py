#!/usr/bin/env python3
"""Check FAIR-facing metadata and the published JSON Schema contract.

This intentionally checks local, deterministic invariants.  The Pages workflow
performs the separate post-deploy HTTP retrieval check after publishing.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "abicheck" / "schemas"
PUBLISHED_SCHEMA_DIR = ROOT / "docs" / "schemas" / "v1"
SCHEMA_BASE = "https://abicheck.github.io/abicheck/schemas/v1/"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"{path.relative_to(ROOT)} is not valid JSON: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} must contain a JSON object")
    return value


def require(mapping: dict[str, object], key: str, location: str) -> object:
    value = mapping.get(key)
    if value in (None, "", [], {}):
        fail(f"{location} is missing required field {key!r}")
    return value


def https_url(value: object, location: str) -> str:
    if not isinstance(value, str):
        fail(f"{location} must be a string URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        fail(f"{location} must be an absolute HTTPS URL, got {value!r}")
    return value


def check_citation() -> None:
    path = ROOT / "CITATION.cff"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        fail(f"CITATION.cff is not valid YAML: {error}")
    if not isinstance(data, dict):
        fail("CITATION.cff must contain a mapping")
    for key in ("cff-version", "message", "title", "type", "authors", "license", "repository-code", "url"):
        require(data, key, "CITATION.cff")
    if data["cff-version"] != "1.2.0":
        fail("CITATION.cff must use CFF 1.2.0")
    if data["type"] != "software":
        fail("CITATION.cff type must be 'software'")
    if data["license"] != "Apache-2.0":
        fail("CITATION.cff license must agree with project metadata")
    for key in ("repository-code", "url"):
        https_url(data[key], f"CITATION.cff {key}")
    authors = data["authors"]
    if not isinstance(authors, list) or not authors:
        fail("CITATION.cff authors must be a non-empty list")
    for author in authors:
        if not isinstance(author, dict) or not author.get("family-names") or not author.get("given-names"):
            fail("every CITATION.cff author must have family-names and given-names")


def check_codemeta_and_zenodo(project: dict[str, object]) -> None:
    codemeta = load_json(ROOT / "codemeta.json")
    for key in ("@context", "@type", "name", "description", "codeRepository", "url", "license", "author", "softwareRequirements"):
        require(codemeta, key, "codemeta.json")
    if codemeta["@context"] != "https://doi.org/10.5063/schema/codemeta-2.0":
        fail("codemeta.json must use the CodeMeta 2.0 context")
    if codemeta["@type"] != "SoftwareSourceCode":
        fail("codemeta.json @type must be SoftwareSourceCode")
    if codemeta["name"] != project["name"]:
        fail("codemeta.json name must agree with pyproject.toml")
    if codemeta["codeRepository"] != project["urls"]["Repository"]:
        fail("codemeta.json codeRepository must agree with pyproject.toml")
    requirements = codemeta["softwareRequirements"]
    if not isinstance(requirements, list):
        fail("codemeta.json softwareRequirements must be a list")
    try:
        codemeta_requirements = {
            canonicalize_name(Requirement(requirement).name) + str(Requirement(requirement).specifier)
            for requirement in requirements
        }
        project_requirements = {
            canonicalize_name(Requirement(requirement).name) + str(Requirement(requirement).specifier)
            for requirement in project["dependencies"]
        }
    except TypeError as error:
        fail(f"software requirements must be strings: {error}")
    if codemeta_requirements != project_requirements:
        fail("codemeta.json softwareRequirements must agree with pyproject.toml dependencies")
    for key in ("codeRepository", "issueTracker", "url", "license"):
        https_url(require(codemeta, key, "codemeta.json"), f"codemeta.json {key}")

    zenodo = load_json(ROOT / ".zenodo.json")
    for key in ("title", "description", "upload_type", "access_right", "license", "creators", "keywords"):
        require(zenodo, key, ".zenodo.json")
    if zenodo["upload_type"] != "software" or zenodo["access_right"] != "open":
        fail(".zenodo.json must describe openly accessible software")
    if zenodo["license"] != "Apache-2.0":
        fail(".zenodo.json license must agree with project metadata")
    creators = zenodo["creators"]
    if not isinstance(creators, list) or not all(isinstance(creator, dict) and creator.get("name") for creator in creators):
        fail(".zenodo.json creators must be a non-empty list of named creators")


def check_schemas() -> None:
    try:
        from jsonschema.validators import validator_for
    except ImportError as error:
        fail(f"jsonschema is required for schema checks: {error}")
    sources = sorted(SCHEMA_DIR.glob("*.schema.json"))
    if not sources:
        fail("no package JSON schemas found")
    for source in sources:
        schema = load_json(source)
        expected_id = SCHEMA_BASE + source.name
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            fail(f"{source.relative_to(ROOT)} must declare JSON Schema draft 2020-12")
        if schema.get("$id") != expected_id:
            fail(f"{source.relative_to(ROOT)} $id must be {expected_id}")
        require(schema, "title", str(source.relative_to(ROOT)))
        require(schema, "description", str(source.relative_to(ROOT)))
        validator_for(schema).check_schema(schema)
        published = PUBLISHED_SCHEMA_DIR / source.name
        if not published.is_file():
            fail(f"published schema is missing: {published.relative_to(ROOT)}")
        if source.read_bytes() != published.read_bytes():
            fail(f"published schema is stale: {published.relative_to(ROOT)}")


def main() -> int:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    check_citation()
    check_codemeta_and_zenodo(project)
    check_schemas()
    print("FAIR metadata and published-schema checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
