#!/usr/bin/env node
// Copyright 2026 Nikolay Petrov
// SPDX-License-Identifier: Apache-2.0
//
// abicheck-skills — install abicheck's Agent Skill(s) into a coding agent's
// skill directory. Zero runtime dependencies on purpose: this runs through
// `npx`, so every dependency is a download on every first use.
//
// The skill files under ../skills/ are generated from the repository's
// skills-src/ by scripts/build_npm_skill_package.py and are never
// hand-edited here.

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PKG_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SKILLS_DIR = path.join(PKG_DIR, "skills");
const MANIFEST = path.join(SKILLS_DIR, "manifest.json");
const OWNER_FILE = ".abicheck-skill.json";

// Agent -> skill directory relative to the install base (a project root, or
// the home directory under --global). `.agents/skills/` is the portable
// Agent Skills location read by Codex, GitHub Copilot and Cursor.
export const TARGETS = {
  claude: ".claude/skills",
  agents: ".agents/skills",
  gemini: ".gemini/skills",
};
const ALIASES = { codex: "agents", copilot: "agents", cursor: "agents", "claude-code": "claude" };
// Directories whose presence at the base means that agent is in use there.
const DETECT = {
  claude: [".claude", "CLAUDE.md"],
  agents: [".agents", ".codex", ".cursor", "AGENTS.md", ".github/copilot-instructions.md"],
  gemini: [".gemini", "GEMINI.md"],
};

const HELP = `abicheck-skills — install abicheck's Agent Skill for your coding agent

Usage:
  npx abicheck-skills [install] [options]   install (default command)
  npx abicheck-skills uninstall [options]   remove skills this tool installed
  npx abicheck-skills list [options]        show what is installed where
  npx abicheck-skills doctor                check the abicheck CLI the skill drives

Options:
  --agent <names>   comma-separated: claude, agents (alias codex/copilot/cursor),
                    gemini, or all. Default: every agent detected in the target
                    directory, else claude,agents.
  --global, -g      install into your home directory (all projects)
  --dir <path>      project directory to install into (default: current dir)
  --path <dir>      install into this exact skills directory instead
  --force           replace a same-named skill directory not installed by this tool
  --dry-run         print what would change, change nothing
  --version, -v     print this package's version
  --help, -h        show this help

The skill drives the abicheck CLI (Python). Install it with:
  pipx install abicheck      (or: pip install abicheck)
`;

const COMMANDS = ["install", "uninstall", "list", "doctor", "help", "version", "verify-package"];
// Flag -> [option key, takes a value]; value-less flags set `true`.
const FLAGS = {
  "--agent": ["agents", true], "--agents": ["agents", true], "-a": ["agents", true],
  "--dir": ["dir", true], "--path": ["path", true],
  "--global": ["global", false], "-g": ["global", false],
  "--force": ["force", false], "-f": ["force", false],
  "--dry-run": ["dryRun", false], "-n": ["dryRun", false],
};
// Flags that are really commands.
const COMMAND_FLAGS = { "--help": "help", "-h": "help", "--version": "version", "-v": "version", "--verify-package": "verify-package" };

export function parseArgs(argv) {
  const opts = { command: "install", agents: null, global: false, dir: null, path: null, force: false, dryRun: false };
  const rest = [...argv];
  if (rest.length && !rest[0].startsWith("-")) opts.command = rest.shift();
  while (rest.length) {
    const arg = rest.shift();
    if (arg in COMMAND_FLAGS) {
      opts.command = COMMAND_FLAGS[arg];
      continue;
    }
    const flag = FLAGS[arg];
    if (!flag) throw new UsageError(`unknown option ${arg}`);
    const [key, takesValue] = flag;
    if (takesValue && (!rest.length || rest[0].startsWith("-"))) throw new UsageError(`${arg} needs a value`);
    opts[key] = takesValue ? rest.shift() : true;
  }
  validateOptions(opts);
  return opts;
}

function validateOptions(opts) {
  if (!COMMANDS.includes(opts.command)) throw new UsageError(`unknown command ${opts.command}`);
  if (opts.global && opts.dir) throw new UsageError("--global and --dir are mutually exclusive");
  if (opts.path && (opts.global || opts.dir || opts.agents)) {
    throw new UsageError("--path names the skills directory itself; do not combine it with --global/--dir/--agent");
  }
}

export class UsageError extends Error {}

export function resolveAgents(spec, base) {
  if (spec) {
    const names = spec.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (names.includes("all")) return Object.keys(TARGETS);
    const out = [];
    for (const name of names) {
      const agent = ALIASES[name] ?? name;
      if (!(agent in TARGETS)) {
        throw new UsageError(`unknown agent '${name}' (choices: ${[...Object.keys(TARGETS), ...Object.keys(ALIASES), "all"].join(", ")})`);
      }
      if (!out.includes(agent)) out.push(agent);
    }
    if (!out.length) throw new UsageError("--agent named no agent");
    return out;
  }
  const detected = Object.keys(TARGETS).filter((agent) => DETECT[agent].some((m) => fs.existsSync(path.join(base, m))));
  return detected.length ? detected : ["claude", "agents"];
}

export function destinations(opts) {
  if (opts.path) return [{ agent: "custom", root: path.resolve(opts.path) }];
  const base = opts.global ? os.homedir() : path.resolve(opts.dir ?? process.cwd());
  return resolveAgents(opts.agents, base).map((agent) => ({ agent, root: path.join(base, TARGETS[agent]) }));
}

export function loadManifest(skillsDir = SKILLS_DIR) {
  const file = path.join(skillsDir, "manifest.json");
  if (!fs.existsSync(file)) {
    throw new Error(`${file} is missing — this package was not built (run scripts/build_npm_skill_package.py)`);
  }
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function readOwner(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, OWNER_FILE), "utf8"));
  } catch {
    return null;
  }
}

// Every path is checked to stay inside its skill directory: the manifest is
// package data, but a path-traversal bug here would write anywhere.
function safeJoin(root, rel) {
  const out = path.resolve(root, rel);
  if (out !== root && !out.startsWith(root + path.sep)) throw new Error(`refusing path outside ${root}: ${rel}`);
  return out;
}

export function install(opts, { manifest = loadManifest(), skillsDir = SKILLS_DIR, log = console.log } = {}) {
  const results = [];
  for (const { agent, root } of destinations(opts)) {
    for (const skill of manifest.skills) {
      const dest = path.join(root, skill.name);
      const exists = fs.existsSync(dest);
      const owner = exists ? readOwner(dest) : null;
      if (exists && !owner && !opts.force) {
        throw new Error(`${dest} exists and was not installed by abicheck-skills; re-run with --force to replace it`);
      }
      const action = exists ? "updated" : "installed";
      results.push({ agent, skill: skill.name, dest, action });
      log(`${opts.dryRun ? "would be " : ""}${action}: ${skill.name} -> ${dest}`);
      if (opts.dryRun) continue;
      // Stage then swap, so an interrupted copy never leaves a half-written
      // skill where the agent will load it.
      fs.mkdirSync(root, { recursive: true });
      const staging = fs.mkdtempSync(path.join(root, `.${skill.name}.tmp-`));
      try {
        for (const rel of skill.files) {
          const target = safeJoin(staging, rel);
          fs.mkdirSync(path.dirname(target), { recursive: true });
          fs.copyFileSync(safeJoin(path.join(skillsDir, skill.name), rel), target);
        }
        const record = { name: skill.name, package: "abicheck-skills", version: manifest.package_version, abicheck_version_range: skill.abicheck_version_range };
        fs.writeFileSync(path.join(staging, OWNER_FILE), JSON.stringify(record, null, 2) + "\n");
        if (exists) fs.rmSync(dest, { recursive: true, force: true });
        fs.renameSync(staging, dest);
      } catch (err) {
        fs.rmSync(staging, { recursive: true, force: true });
        throw err;
      }
    }
  }
  return results;
}

export function uninstall(opts, { manifest = loadManifest(), log = console.log } = {}) {
  const results = [];
  for (const { agent, root } of destinations(opts)) {
    for (const skill of manifest.skills) {
      const dest = path.join(root, skill.name);
      if (!fs.existsSync(dest)) continue;
      if (!readOwner(dest)) {
        log(`skipped: ${dest} was not installed by abicheck-skills`);
        continue;
      }
      results.push({ agent, skill: skill.name, dest, action: "removed" });
      log(`${opts.dryRun ? "would be " : ""}removed: ${dest}`);
      if (!opts.dryRun) fs.rmSync(dest, { recursive: true, force: true });
    }
  }
  if (!results.length) log("nothing to remove");
  return results;
}

export function list(opts, { manifest = loadManifest(), log = console.log } = {}) {
  const found = [];
  const listOpts = opts.agents || opts.path ? opts : { ...opts, agents: "all" };
  for (const { agent, root } of destinations(listOpts)) {
    for (const skill of manifest.skills) {
      const owner = readOwner(path.join(root, skill.name));
      if (owner) found.push({ agent, skill: skill.name, version: owner.version, dest: path.join(root, skill.name) });
    }
  }
  if (!found.length) log("no abicheck skills installed here");
  for (const f of found) log(`${f.agent.padEnd(7)} ${f.skill} ${f.version}  ${f.dest}`);
  return found;
}

// --- version ranges: ">=0.6.0,<0.7.0" (PEP 440 subset the skills declare) ---
function cmpVersions(a, b) {
  const pa = a.split(".").map((n) => parseInt(n, 10) || 0);
  const pb = b.split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return Math.sign(d);
  }
  return 0;
}

const RELEASE_PREFIX = new RegExp("^\\d+(\\.\\d+)*");
const CLAUSE = new RegExp("^(>=|<=|==|!=|>|<)\\s*(\\d+(?:\\.\\d+)*)$");
const COMPARATORS = {
  ">=": (c) => c >= 0, "<=": (c) => c <= 0, "==": (c) => c === 0,
  "!=": (c) => c !== 0, ">": (c) => c > 0, "<": (c) => c < 0,
};

export function satisfies(version, range) {
  const release = RELEASE_PREFIX.exec(version)?.[0];
  if (!release) return false;
  return range.split(",").map((s) => s.trim()).filter(Boolean).every((clause) => {
    const m = CLAUSE.exec(clause);
    if (!m) throw new Error(`unsupported version clause '${clause}'`);
    return COMPARATORS[m[1]](cmpVersions(release, m[2]));
  });
}

function probe(cmd, args) {
  const r = spawnSync(cmd, args, { encoding: "utf8" });
  if (r.error || r.status !== 0) return null;
  return (r.stdout || r.stderr).trim();
}

export function doctor({ manifest = loadManifest(), log = console.log, run = probe } = {}) {
  let ok = true;
  const out = run("abicheck", ["--version"]);
  const version = out?.match(/(\d+\.\d+(?:\.\d+)?\S*)/)?.[1];
  if (!version) {
    ok = false;
    log("✗ abicheck CLI not found on PATH — install it: pipx install abicheck (or pip install abicheck)");
  } else {
    for (const skill of manifest.skills) {
      if (satisfies(version, skill.abicheck_version_range)) {
        log(`✓ abicheck ${version} satisfies ${skill.name} (${skill.abicheck_version_range})`);
      } else {
        ok = false;
        log(`✗ abicheck ${version} is outside ${skill.name}'s supported range ${skill.abicheck_version_range} — install a matching abicheck-skills or abicheck version`);
      }
    }
  }
  const castxml = run("castxml", ["--version"]);
  log(castxml ? "✓ castxml found (header-aware analysis available)" : "! castxml not found — header-aware (L2) analysis unavailable; install castxml, or use clang via --ast-frontend clang");
  const cc = run("cc", ["--version"]) ?? run("gcc", ["--version"]) ?? run("clang", ["--version"]);
  log(cc ? "✓ C/C++ compiler found" : "! no C/C++ compiler found — the skill builds the two sides when no binaries are provided");
  return ok;
}

export function verifyPackage({ skillsDir = SKILLS_DIR, pkgDir = PKG_DIR } = {}) {
  const manifest = loadManifest(skillsDir);
  const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, "package.json"), "utf8"));
  const problems = [];
  if (manifest.package_version !== pkg.version) {
    problems.push(`skills/manifest.json was built for ${manifest.package_version} but package.json says ${pkg.version}`);
  }
  if (!manifest.skills?.length) problems.push("manifest lists no skills");
  for (const skill of manifest.skills ?? []) {
    if (!skill.files.includes("SKILL.md")) problems.push(`${skill.name}: no SKILL.md`);
    for (const rel of skill.files) {
      if (!fs.existsSync(path.join(skillsDir, skill.name, rel))) problems.push(`${skill.name}: missing ${rel}`);
    }
  }
  return problems;
}

export function main(argv = process.argv.slice(2)) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (err) {
    console.error(`error: ${err.message}\n\n${HELP}`);
    return 64;
  }
  try {
    switch (opts.command) {
      case "help": console.log(HELP); return 0;
      case "version": console.log(JSON.parse(fs.readFileSync(path.join(PKG_DIR, "package.json"), "utf8")).version); return 0;
      case "verify-package": {
        const problems = verifyPackage();
        for (const p of problems) console.error(`✗ ${p}`);
        return problems.length ? 1 : 0;
      }
      case "install": {
        install(opts);
        if (!opts.dryRun) {
          console.log("\nRestart your agent session to pick the skill up. Checking the abicheck CLI it drives:");
          doctor();
        }
        return 0;
      }
      case "uninstall": uninstall(opts); return 0;
      case "list": list(opts); return 0;
      case "doctor": return doctor() ? 0 : 1;
    }
  } catch (err) {
    console.error(`error: ${err.message}`);
    return err instanceof UsageError ? 64 : 1;
  }
  return 0;
}

const invokedDirectly = (() => {
  try {
    return fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
})();
if (invokedDirectly) process.exitCode = main();
