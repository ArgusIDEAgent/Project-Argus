# Investor demo brief

## The story

Developers spend time reconstructing code paths and searching for the cause of failures. CodeMind turns repository structure, exact search, and relevant history into an answer whose sources can be inspected. The first customer is a team with a TypeScript/JavaScript codebase and costly onboarding or incident investigation.

## Five-minute live demonstration

| Time | Action | What the audience should see |
|---|---|---|
| 0:00–0:45 | Open sample repository; show index status and local/cloud mode. | Product is installed and usable, with transparent data mode. |
| 0:45–2:00 | Ask “How does LoginForm reach the users table?” | A path through UI, route, service, and query code; every hop opens to a file and line. Inferred hops are labelled. |
| 2:00–3:15 | Run a prepared failing test. | The assistant detects the command result, captures the relevant output, and identifies the failing code location. |
| 3:15–4:15 | Ask whether a similar failure happened before. | A relevant prior commit or retained CI failure appears with a source link; similarity is distinguished from proof. |
| 4:15–5:00 | Review a proposed patch and rerun the test. | A diff is visible before application; the test result verifies this example fix. |

## Demo prerequisites

- Use a repository the team can show publicly, with a fixed commit, known endpoint path, reproducible failure, and related historical record.
- Keep a single command that resets the fixture to the initial state and a one-page setup guide.
- Rehearse on a clean machine and record a backup video of the same workflow.
- Measure index time, answer latency, answer accuracy, citation validity, and resource use on the demo hardware. Present observed numbers, not estimates.
- Prepare screenshots of the architecture and evidence path, plus a page stating the current supported language, IDE, and limits.

## Pitch materials to prepare after the product works

1. Problem and specific buyer.
2. Live product demonstration.
3. Why evidence-based answers are useful: code graph + exact search + historical context.
4. Measured outcomes against the manual baseline and early user feedback.
5. Market entry: TypeScript/JavaScript teams using VS Code; expansion path to more stacks and team systems.
6. Business model hypothesis, cost to serve, distribution, and 12-month milestones.
7. Team, funding ask, and what that funding will deliver.

Do not claim “error-free,” universal language support, automatic ticket maintenance, or privacy guarantees that have not been tested and documented.
