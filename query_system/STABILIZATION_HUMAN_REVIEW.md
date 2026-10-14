# Human review packet — not an automatic factual-quality pass

Reviewer decision: **accepted with corrections, pending source verification**.
The user's review is provisional, not full approval of the original output.
Citation membership does not establish whether model prose is accurate or useful.

Question: **Why might verifiedSourcePath fail?**

Model: the installed local `llama3:latest`, temperature 0; no remote provider.
The only accepted generated claim in the initial five-task run was explicitly
non-authoritative and classified `hypothetical_consequence`:

> verifiedSourcePath might fail due to invalid indexed source path, absolute path,
> or path including '\0'. It could also fail if the file is outside the selected
> repository, not a regular file, or too large to open. Additionally, the source
> hash might not match the expected value, indicating the source has changed.

Compare with `extension/src/sourceNavigation.ts`, lines 12–31. The complete
redacted source excerpt and canonical record mapping are retained in
`benchmarks/stabilization_ollama_results.json` → `ollama.details` → case `behavior`.
The excerpt is a historical frozen copy; do not treat its expired disposable
repository citation as current navigation authority.

Please assess:

1. Is every assertion supported by the code? Distinguish a NUL character from
   the literal two characters backslash and zero.
2. Is uncertainty clear? A hash mismatch need not prove which input/file changed.
3. Is the explanation useful, and does it omit a critical failure condition?
4. Identify any unsupported claim, hallucination or unsafe implication.

Return **accepted**, **accepted with corrections**, or **rejected**, with notes.
Reviewing this single claim does not qualify all four explanation task families.
Function explanations currently fall back after fact-only output; the workflow
lost a complete target excerpt under the default context budget. The initial
unqualified comparison name `isInside` was genuinely ambiguous (both top-level
`isInside` and `activate.isInside` exist), so a corrected positive case uses
`extension/src/sourceNavigation.ts::isInside` and retains the ambiguous case as
a negative probe. Initial results are preserved, not relabeled as successes.

## Corrections recorded separately from the original output

The reviewer requested verification of every condition, a checked size limit,
the NUL/literal-backslash distinction, all omitted returns/exceptions, and no
claim that a hash mismatch proves a file change. The original claim above is
preserved verbatim. The reviewer explicitly classified the suggested correction
as provisional and did not qualify other explanation families.

Source verification performed against the actual current file, not citation
membership alone: `extension/src/sourceNavigation.ts:5–31` and `isInside:7–10`.

The user's provisional corrected claim (preserved separately):

> verifiedSourcePath may reject a source when its indexed path is invalid,
> absolute, contains a NUL character (U+0000), resolves outside the selected
> repository, or does not identify an acceptable regular file. Source verification
> may also fail when the current file hash differs from the expected hash. A hash
> mismatch indicates inconsistent content or metadata; it does not establish
> whether the file changed or the expected hash was incorrect.

| Condition | Source support |
| --- | --- |
| Empty, absolute, or NUL-containing indexed path | Explicit throw at 13–15; `'\0'` is the JavaScript escape for U+0000, not the two characters backslash and zero |
| Resolved path outside repository | Checks at 18 and 22, including symlink target; `isInside` also rejects the repository root itself |
| File resolution fails | Catch at 20–21 converts any `realpathSync(file)` failure into the refresh message; not exclusively missing files |
| Not a regular file or oversized | Explicit check at 23–25; constant at 5 is 5 × 1024 × 1024 bytes; exactly 5 MiB is not rejected by size alone |
| Hash differs | Explicit check at 27–29; this establishes a mismatch, not which input changed |
| Other filesystem errors | `realpathSync(rootPath)`, `statSync(real)` and `readFileSync(real)` can throw without a local catch; permissions or races can therefore also fail verification |
| Success | Returns the canonical real path at 30 after all checks |

Source-checked corrected explanation (not a replacement for model output):

> `verifiedSourcePath` may reject an empty, absolute, or NUL-containing indexed
> path, a resolved path equal to or outside the selected repository root (including
> a symlink target), a path whose real-file resolution fails, a non-regular file,
> or a file larger than 5 MiB. Filesystem operations on the root or file can also
> throw. It rejects a current SHA-256 value that differs from the expected hash;
> this indicates inconsistent content or metadata, not proof that the file rather
> than the expected hash changed. On success it returns the canonical real path.

Classification remains **non-authoritative**. The listed guards are now checked
against source, but no independent final human acceptance of this corrected text
has been received. The original model claim's changed-file inference was too
strong. Human-reviewed explanation accuracy and a hallucination rate are not
reported as passed or estimated from this single sample. The existing UI error
message says “This source changed”; changing that message is not part of this
review correction.
