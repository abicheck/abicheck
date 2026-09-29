// Copyright 2026 Nikolay Petrov
// SPDX-License-Identifier: Apache-2.0
//
// Unit tests for the abicheck-skills installer. Run with `npm test`
// (tests/test_npm_skill_package.py runs them in the Python suite, too).
// They use a synthetic skills/ tree, so they do not need the package built.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  TARGETS,
  UsageError,
  destinations,
  doctor,
  install,
  list,
  parseArgs,
  resolveAgents,
  satisfies,
  uninstall,
  verifyPackage,
} from "../bin/abicheck-skills.mjs";

function tmp() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "abicheck-skills-test-"));
}

function fakePackage() {
  const skillsDir = tmp();
  const files = ["SKILL.md", "references/a.md", "references/shared/b.md"];
  for (const rel of files) {
    fs.mkdirSync(path.dirname(path.join(skillsDir, "demo-skill", rel)), { recursive: true });
    fs.writeFileSync(path.join(skillsDir, "demo-skill", rel), `content of ${rel}\n`);
  }
  const manifest = {
    package_version: "1.2.3",
    skills: [{ name: "demo-skill", abicheck_version_range: ">=0.6.0,<0.7.0", files }],
  };
  fs.writeFileSync(path.join(skillsDir, "manifest.json"), JSON.stringify(manifest));
  return { skillsDir, manifest, files };
}

const quiet = () => {};

test("parseArgs: default command is install, options parse", () => {
  assert.deepEqual(parseArgs([]).command, "install");
  const o = parseArgs(["uninstall", "--agent", "claude,gemini", "-g", "--dry-run", "--force"]);
  assert.equal(o.command, "uninstall");
  assert.equal(o.agents, "claude,gemini");
  assert.ok(o.global && o.dryRun && o.force);
});

test("parseArgs: usage errors are UsageError", () => {
  for (const argv of [["--nope"], ["frobnicate"], ["--agent"], ["--global", "--dir", "x"], ["--path", "x", "--agent", "claude"]]) {
    assert.throws(() => parseArgs(argv), UsageError, argv.join(" "));
  }
});

test("resolveAgents: aliases, all, dedup, unknown", () => {
  const base = tmp();
  assert.deepEqual(resolveAgents("codex,copilot,cursor,agents", base), ["agents"]);
  assert.deepEqual(resolveAgents("all", base), Object.keys(TARGETS));
  assert.deepEqual(resolveAgents("claude-code", base), ["claude"]);
  assert.throws(() => resolveAgents("vim", base), UsageError);
});

test("resolveAgents: detects agents present in the base, falls back otherwise", () => {
  const base = tmp();
  assert.deepEqual(resolveAgents(null, base), ["claude", "agents"]);
  fs.mkdirSync(path.join(base, ".gemini"));
  assert.deepEqual(resolveAgents(null, base), ["gemini"]);
  fs.writeFileSync(path.join(base, "AGENTS.md"), "");
  assert.deepEqual(resolveAgents(null, base), ["agents", "gemini"]);
});

test("install copies every manifest file, records ownership, and is idempotent", () => {
  const { skillsDir, manifest, files } = fakePackage();
  const project = tmp();
  const opts = { ...parseArgs(["--agent", "all", "--dir", project]) };
  const first = install(opts, { manifest, skillsDir, log: quiet });
  assert.equal(first.length, Object.keys(TARGETS).length);
  for (const rel of Object.values(TARGETS)) {
    const dest = path.join(project, rel, "demo-skill");
    for (const f of files) assert.equal(fs.readFileSync(path.join(dest, f), "utf8"), `content of ${f}\n`);
    const owner = JSON.parse(fs.readFileSync(path.join(dest, ".abicheck-skill.json"), "utf8"));
    assert.equal(owner.version, "1.2.3");
  }
  const again = install(opts, { manifest, skillsDir, log: quiet });
  assert.ok(again.every((r) => r.action === "updated"));
  // No staging directories left behind.
  for (const rel of Object.values(TARGETS)) {
    assert.deepEqual(fs.readdirSync(path.join(project, rel)), ["demo-skill"]);
  }
});

test("install removes files a previous version had but this one does not", () => {
  const { skillsDir, manifest } = fakePackage();
  const project = tmp();
  const opts = parseArgs(["--agent", "claude", "--dir", project]);
  install(opts, { manifest, skillsDir, log: quiet });
  const stale = path.join(project, TARGETS.claude, "demo-skill", "references", "old.md");
  fs.writeFileSync(stale, "stale");
  install(opts, { manifest, skillsDir, log: quiet });
  assert.equal(fs.existsSync(stale), false);
});

test("install refuses to clobber a foreign directory unless --force", () => {
  const { skillsDir, manifest } = fakePackage();
  const project = tmp();
  const foreign = path.join(project, TARGETS.claude, "demo-skill");
  fs.mkdirSync(foreign, { recursive: true });
  fs.writeFileSync(path.join(foreign, "mine.md"), "user content");
  assert.throws(() => install(parseArgs(["--agent", "claude", "--dir", project]), { manifest, skillsDir, log: quiet }), /--force/);
  assert.equal(fs.readFileSync(path.join(foreign, "mine.md"), "utf8"), "user content");
  install(parseArgs(["--agent", "claude", "--dir", project, "--force"]), { manifest, skillsDir, log: quiet });
  assert.equal(fs.existsSync(path.join(foreign, "mine.md")), false);
});

test("dry-run changes nothing", () => {
  const { skillsDir, manifest } = fakePackage();
  const project = tmp();
  const results = install(parseArgs(["--agent", "all", "--dir", project, "--dry-run"]), { manifest, skillsDir, log: quiet });
  assert.equal(results.length, 3);
  assert.deepEqual(fs.readdirSync(project), []);
});

test("uninstall removes only what this tool installed; list reflects state", () => {
  const { skillsDir, manifest } = fakePackage();
  const project = tmp();
  install(parseArgs(["--agent", "claude,gemini", "--dir", project]), { manifest, skillsDir, log: quiet });
  const foreign = path.join(project, TARGETS.agents, "demo-skill");
  fs.mkdirSync(foreign, { recursive: true });
  assert.equal(list(parseArgs(["list", "--dir", project]), { manifest, log: quiet }).length, 2);
  const removed = uninstall(parseArgs(["uninstall", "--agent", "all", "--dir", project]), { manifest, log: quiet });
  assert.deepEqual(removed.map((r) => r.agent).sort(), ["claude", "gemini"]);
  assert.ok(fs.existsSync(foreign));
  assert.equal(list(parseArgs(["list", "--dir", project]), { manifest, log: quiet }).length, 0);
});

test("--path installs into exactly that directory; --global uses the home directory", () => {
  const custom = path.join(tmp(), "my-skills");
  assert.deepEqual(destinations(parseArgs(["--path", custom])), [{ agent: "custom", root: custom }]);
  const g = destinations(parseArgs(["-g", "--agent", "claude"]));
  assert.deepEqual(g, [{ agent: "claude", root: path.join(os.homedir(), TARGETS.claude) }]);
});

test("satisfies: PEP 440 subset, checked against an independent enumeration", () => {
  const range = ">=0.6.0,<0.7.0";
  const cases = {
    "0.5.9": false, "0.6.0": true, "0.6": true, "0.6.1": true, "0.6.10": true,
    "0.6.0.dev3": true, "0.6.99": true, "0.7.0": false, "0.7": false, "1.0.0": false, "0.10.0": false,
  };
  for (const [v, want] of Object.entries(cases)) assert.equal(satisfies(v, range), want, v);
  assert.equal(satisfies("2.0.0", "!=2.0.0"), false);
  assert.equal(satisfies("2.0.1", ">2.0,<=2.0.1"), true);
  assert.throws(() => satisfies("1.0", "~=1.0"), /unsupported/);
});

test("doctor reports a missing, in-range and out-of-range abicheck", () => {
  const { manifest } = fakePackage();
  const lines = [];
  const log = (l) => lines.push(l);
  assert.equal(doctor({ manifest, log, run: () => null }), false);
  assert.match(lines.join("\n"), /pipx install abicheck/);
  assert.equal(doctor({ manifest, log, run: (cmd) => (cmd === "abicheck" ? "abicheck, version 0.6.2" : "x") }), true);
  assert.equal(doctor({ manifest, log, run: (cmd) => (cmd === "abicheck" ? "abicheck 0.7.0" : "x") }), false);
});

test("verifyPackage catches a version mismatch and a missing file", () => {
  const { skillsDir } = fakePackage();
  const pkgDir = tmp();
  fs.writeFileSync(path.join(pkgDir, "package.json"), JSON.stringify({ version: "1.2.3" }));
  assert.deepEqual(verifyPackage({ skillsDir, pkgDir }), []);
  fs.writeFileSync(path.join(pkgDir, "package.json"), JSON.stringify({ version: "9.9.9" }));
  fs.rmSync(path.join(skillsDir, "demo-skill", "references", "a.md"));
  const problems = verifyPackage({ skillsDir, pkgDir });
  assert.equal(problems.length, 2);
});
