---
name: deep-prompt
description: Rewrite a request into a deeper prompt (expert scope, original sources, concrete methods, outside evidence), save it to a file, and hand it to a new session.
argument-hint: "[request to deepen]"
disable-model-invocation: true
---

# Deep Prompt

Rewrite the request in `$ARGUMENTS` (if empty, the user's latest request) into a prompt for a deeper version of the task, not a bigger one. Do not start the task itself in this session.

## 1. Answer Four Questions

Answer each briefly and concretely for this request:

1. **Expert scope**: what would the best practitioner in this field add that the request leaves out?
2. **Original material**: which sources would they work from (standards text with clause numbers, measured data, logs, the code itself) rather than secondhand summaries?
3. **Methods and tools**: which concrete methods, algorithms, and tools would they apply?
4. **Outside evidence**: what, independent of the assistant's or the user's judgment, would prove the result correct (hand-computable fixtures, two independent methods that must agree, property or mutation tests, captures, a measurement the user takes)?

Mark facts you could not check at their source as unverified; the rewritten prompt must not use them as defaults.

## 2. Rewrite the Prompt

Rewrite the request to ask for exactly what the four answers describe. The rewritten prompt states:

- the goal as an outcome for the person using the result, plus a precise definition of what to build or decide;
- the original sources to fetch and cite, and how to mark what cannot be verified;
- the methods and where the work lives in the project;
- spec or plan first when the project keeps them, with acceptance criteria tied to tests;
- the evidence from question 4, and the checks to run before finishing, reported with their output verbatim.

Keep the user's confirmed goals, constraints, and decisions, saying where each came from; label unverified factual claims and say how to verify them. Show the four answers and the rewritten prompt, then wait for the user's approval or edits.

## 3. Save and Hand Off

After approval, save only the rewritten prompt to `claude-plans/prompt-<topic>.md` in the project root (English kebab-case topic; create the folder if missing, and tell the user if it is not git-ignored). Then give the one-line command that runs it in a new session:

```bash
claude "$(cat claude-plans/prompt-<topic>.md)"
```

Where the app can start a session from a brief, offer that instead and start it only on the user's yes. If the run stops partway, the user continues it in that session (`claude --continue`).
