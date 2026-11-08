---
name: integration-testing
description: Run authorized integration or runtime tests with external services, simulators, databases, or brokers; diagnose prerequisites and preserve test evidence.
---

# Integration Testing Rules

Follow the current user request, applicable project instructions, and Codex permissions. Existing explicit authorization remains valid; do not ask again for the same approved action. These are personal working conventions, not universal language requirements.

Apply these rules when running integration/runtime tests against a live or simulated environment.

## 1. Verify Infrastructure First (Hard Prereq)

Before starting, identify the endpoints the selected test actually requires and check reachability through approved health checks or connection probes. A blocked ICMP ping alone does not prove an endpoint is down. Probe only authorized test infrastructure. Diagnose failed prerequisites before attributing failures to application code; redact secrets in reported results.

## 2. Structured Test Execution

Organize tests bottom-up — unit-level module tests first, then controller/service tests, then end-to-end scenarios, then error/recovery, then soak tests. Track each test case with ID, status (PASS/FAIL/SKIP), and notes.

## 3. Monitor-Trigger-Verify Loop

For each test — trigger the action, capture logs immediately (process manager logs, container logs, system journal, etc.), verify (a) no runtime errors, (b) expected output, (c) expected state changes.

## 4. Fix at the Source

Runtime bugs belong to the source project, not the test harness or consumer. Fix root cause → rebuild → restart → retest. Grep for the same pattern project-wide before moving on.

**Never SKIP a test** due to harness/flow issues. On any test failure: (1) identify root cause (application code vs flow/harness/preconditions), (2) fix the source within the authorized repositories; request missing authorization for other repositories, (3) retest and verify PASS, (4) only then move to the next test. Consumer-specific bugs (wrong params in test harness, stale test data) are still bugs that must be fixed.

**Diagnose blockers, don't bypass**: When a test hits a stuck step, silent failure, or unexpected state, immediately diagnose the root cause. Never use workflow controls (STEP_OVER, retry, skip, force-cancel) or config changes to bypass without first understanding why. If the root cause is hard to fix, explain the analysis and proposed fix, get user approval, then proceed. Continuing past a blocker without understanding it means the next step's results can't be trusted.

## 5. Set Up Proper Preconditions

When a test fails due to missing state (wrong initial data, wrong device position, stale records), set up the required preconditions and retest — never rationalize the failure as "simulator limitation", "expected fail", or "not a code issue" to justify skipping. Always attempt to set up required preconditions first using available tools/APIs. Only mark a test as SKIP with explicit user approval after demonstrating that precondition setup is technically impossible.

## 6. Remote Environment Diagnosis

When the system under test connects to remote services (simulators, test environments, external APIs), verify whether failures originate locally or remotely. Use SSH, network tools, or remote logs to confirm before assuming a code bug.

## 7. Don't Pivot Scope Without Approval

When the user authorizes a specific test scope (e.g. "full cycle", "end-to-end", "start to end/cancel"), do not silently fall back to weaker verification (isolated unit test, init-only smoke test, code review) on hitting blockers. Hitting a blocker during authorized work requires one of:
1. Actively fixing the blocker (SSH into the environment, orchestrate multi-API calls, modify config) and reporting progress
2. Explicitly requesting user help/approval to try an alternative approach
3. Stopping and asking how to proceed

Never declare a test "complete" or "passed" for a scope narrower than what was authorized.

## 8. Soak Test

When stability over time is in the agreed scope, run a soak test for a bounded, stated duration and verify the logs. Do not add an indefinite run or scheduled monitoring to an ordinary test request.

## 9. Clean Revert

After testing, revert all test-specific config changes (environment switches, commented-out code, dependency overrides). Never commit test-only modifications.

## 10. Preserve Logs

Never truncate log files during testing (no `pm2 flush`, `truncate`, `docker logs --tail` overwrites, etc.). Intermittent errors may not reproduce on demand, and the only evidence is in the log file. Use `grep` on the full log file directly instead of relying on tail-window commands which may miss errors past the window.

## 11. Test-Time Patches — Isolate, Document, Revert

Test-time only changes (config switches like `config.test.sim`, simulator-friendly preset comments, debug-mode interval shortening, mock auth headers, etc.) MUST be isolated and reverted — never committed to source.

- **Isolation**: keep test-time edits in a **labeled git stash** (`git stash push -m "test-patches: <project>"`) or a dedicated **test-only branch**. Never commit them to feature/main branches.
- **Documentation**: enumerate the patch list (file + change description) in the project's AGENTS.md or README. Stashes can be lost (manual `stash drop`, `git gc`); the documented list is the recovery source.
- **Apply when resuming the authorized test**: inspect the exact labeled stash and the working tree before applying it; preserve the stash until the result is verified. Never pop an unrelated stash automatically at session start.
- **Revert at session end**: re-stash with the same label, never commit. Verify `git diff --staged` is clean of test-patch files before any commit.
- **Interval shortening**: if a test requires waiting on a periodic interval (cache refresh, retry backoff, etc.), shorten the constant temporarily as part of the test-patches stash. Don't shorten if the test is specifically validating the timing.

## 12. Multi-Clone Integration Testing

When integrating a test branch into a separate working clone (e.g., a production clone consumes a migration-branch clone through a `file:` reference), move changes between clones via standard git mechanisms — never ad-hoc directory copy:

- **Preferred (no push needed)**: `git fetch ../<other-clone> <branch>` from the consuming clone, then `git checkout <branch>` or `git merge FETCH_HEAD`. Local-to-local fetch keeps the change set transparent and reversible.
- **Alternative**: `git format-patch` from the source clone → `git am` in the consuming clone.
- **Forbidden**: `cp -r <other-clone>/<files> <this-clone>/` or rsync-style copy. Bypasses git tracking, mixes test changes with working tree, and produces unreviewable state.
- **Symlink-based consumer with two source clones — workflow varies by user habit**: when the downstream consumer uses a `file:` npm symlink and there are multiple parallel source clones (e.g., `library/main` and `library/test-branch`), there are two valid integration patterns. **Confirm with the user which one applies before any environment write**:
  - **Pattern A (consumer ref swap)**: edit the consumer's `package.json` `file:` to point at the test clone, run `npm install` to relink. Revert at session end. The source clones stay on their own branches.
  - **Pattern B (source branch swap)**: keep consumer's `file:` ref unchanged; in the source clone behind the symlink, `git checkout <test-branch>` so the working tree behind the symlink becomes the test code.
  - **Anti-pattern**: assuming one is "the standard" without asking. The user's habit determines which is correct. Picking the wrong one will either corrupt the production source clone or appear to do nothing.

## 13. Respect User-Stated Mutation Scope

When the user authorizes a specific environment mutation (e.g., "switch the service's branch only", "do all work in dir X"), do NOT mutate anything outside that scope. Before any extra mutation (other files, other repos, package manifests, lockfiles, dependency caches, env vars, parallel clones), STOP and ask. Each unrelated mutation requires its own approval.

- **Examples of out-of-scope mutations to avoid**: editing `package.json` `file:` refs when the user said "change branch", running `npm install` that rewrites lockfiles, deleting `node_modules`, modifying sibling repos, applying patches to a "reference-only" clone, touching configs the user did not name.
- **Designated work directory vs reference-only directory**: when the user says "work in dir X" and mentions another dir Y as "you can refer to its stash / configs", treat Y as **read-only**: read its files, read stash contents, read config patterns — but never `git checkout`, `git apply`, `git stash apply`, or any write to Y. Apply the learned patterns inside X instead.
- **Reverting an unauthorized mutation does not absolve the violation** — the violation IS the mutation itself, regardless of whether it was reverted. Each silent extra mutation erodes trust and may leave subtle leftovers (lockfile diffs, cached symlinks, npm tarball cache, detached HEADs).
- **Respond to user concern**: pause the disputed mutation, inspect the state, and follow the clarified scope. Do not automatically revert files that may contain concurrent user work; preserve it and explain the proposed recovery.
- **When in doubt about scope, ask "Should I also do X?" before doing X**. A 5-second confirmation is cheaper than an unwanted environment change followed by trust erosion.
