// Copyright 2026 Nikolay Petrov
// SPDX-License-Identifier: Apache-2.0
//
// Executable test harness for the `verify-merge-checks.yml` workflow's
// embedded `actions/github-script` step. See test_verify_merge_checks_race_logic.py
// for why this exists: several rounds of Codex review on that step's
// poll/select/decide logic found real race-condition bugs that a purely
// textual/structural test (test_required_checks_governance.py's
// TestVerifyMergeChecksWorkflow) cannot see, because it never actually
// executes the script.
//
// This harness mocks the three things `actions/github-script` normally
// injects (`context`, `core`, `github`) plus time itself, then runs the
// *real* script text (extracted from the workflow YAML by the calling
// Python test, never hand-copied here) against a scripted sequence of
// `checks.listForRef` responses -- one array per poll attempt, the last
// entry repeating for any attempt beyond the sequence's length.
//
// Usage: node verify_merge_checks_harness.mjs <scenario.json>
// scenario.json: {
//   "scriptPath": "<path to a file containing the extracted script text>",
//   "sha": "<merge commit sha the script sees as context.sha>",
//   "prs": [<listPullRequestsAssociatedWithCommit response items>],
//   "pollSequence": [[<check run>, ...], ...],
//   "pollIntervalMs": <optional override, defaults to whatever the script itself uses>
// }
// Prints one JSON line to stdout: { failedMessage, infoLogs, warnLogs, error? }

import { readFileSync } from 'node:fs';

const scenarioPath = process.argv[2];
if (!scenarioPath) {
  console.error('usage: node verify_merge_checks_harness.mjs <scenario.json>');
  process.exit(2);
}
const scenario = JSON.parse(readFileSync(scenarioPath, 'utf8'));
const scriptText = readFileSync(scenario.scriptPath, 'utf8');

// A fake, monotonically-advancing clock: `setTimeout` below advances it by
// exactly the requested delay before invoking the callback, so the script's
// own `Date.now() >= deadline` logic sees real elapsed time without this
// harness actually waiting -- letting a scenario that needs the poll budget
// to run out still execute in milliseconds.
let fakeNow = Date.parse('2026-01-01T00:00:00Z');
const RealDate = Date;
class FakeDate extends RealDate {
  static now() {
    return fakeNow;
  }
}
// eslint-disable-next-line no-global-assign
Date = FakeDate;
// eslint-disable-next-line no-global-assign
setTimeout = (fn, ms) => {
  fakeNow += ms;
  fn();
};

let attemptIndex = 0;
const infoLogs = [];
const warnLogs = [];
let failedMessage = null;

const context = {
  sha: scenario.sha,
  repo: { owner: 'owner', repo: 'repo' },
};
const core = {
  info: msg => infoLogs.push(msg),
  warning: msg => warnLogs.push(msg),
  setFailed: msg => {
    failedMessage = msg;
  },
};
const github = {
  rest: {
    repos: {
      listPullRequestsAssociatedWithCommit: async () => ({ data: scenario.prs }),
    },
    checks: {
      listForRef: async () => {
        const idx = Math.min(attemptIndex, scenario.pollSequence.length - 1);
        attemptIndex += 1;
        return { data: scenario.pollSequence[idx] };
      },
    },
  },
  // Mirrors the real Octokit `paginate(route, parameters)` signature the
  // script calls: invoke the route function once with the given
  // parameters and return its `.data`. The scenario's poll sequence
  // already represents one fully-paginated result per attempt, so no
  // actual pagination needs simulating here.
  paginate: async (fn, params) => {
    const res = await fn(params);
    return res.data;
  },
};

(async () => {
  // eslint-disable-next-line no-eval
  await eval(`(async () => { ${scriptText} })()`);
})()
  .then(() => {
    process.stdout.write(JSON.stringify({ failedMessage, infoLogs, warnLogs, attempts: attemptIndex }));
  })
  .catch(err => {
    process.stdout.write(JSON.stringify({ error: String((err && err.stack) || err) }));
    process.exitCode = 1;
  });
