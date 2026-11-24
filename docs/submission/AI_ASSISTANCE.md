# AI assistance declaration

## Declaration

AI assistants were used for **code work, testing and documentation** in this
project. The author reviewed the changes, ran the build and test commands, and
remains responsible for correctness, verification and explanation of all
submitted work.

| Tool / model | What it helped with |
|---|---|
| DeepSeek Harness coding agent (`deepseek-flash`) | Reading and editing the backend/frontend, writing and running tests, debugging runtime and security issues, drafting README/version notes and the submission documents. |
| Claude Sonnet 5.5 (via the configured OpenAI-compatible writer endpoint) | The application's own planning/writer model during development and the model study; also used for drafting and editing report text. |
| DeepSeek Flash | The runtime constraint verifier; a benchmark arm in the part-model study. |
| Gemini 3.5 Flash-Lite | Benchmark arm in the part-model study (routing/planner comparison), not a deployment default. |
| Jev (`jev-latest`) | Optional System-1 router benchmarked against the LLM router. |

No AI tool is claimed as an author. The application's runtime model settings are
separate from this declaration and are documented in the root README.

## Project-related prompts (record)

The prompts below are the project-level instructions retained in the working log.
They are reproduced as written, with credentials and personal paths removed. They
are not a complete transcript.

1. “Diagnose and fix Pathfinder v5 bugs …, commit to GitHub main and pathfinder_v5.”
2. “please use this skill to keep finding bugs in codebases (both frontend and backend) list them all out” (Claude-BugHunter audit workflow).
3. “you may fix but the account settings features are for mock up purposes only … if all of the above fixes can improve the functionalities and will not break existing functions, please fix it and do backend tests to prove that it still works.”
4. “no i don't want to run judge on the demo.”
5. “can i make the the evaluation tab and error analysis tab can still is still runnable now? as i see not all tests cases we have on hand has included.”
6. “analyse why runtime liks this which only involves a day or two ==> still very slow.” (with the exported Taipei turn log and plan).
7. “you shall make sure the runtime will improve for both:” (the three demo prompts: 3-day Hong Kong, 4-day Kyoto, 2-day Taipei).
8. “can hotel options also be selected with x other options style isntead of dispalying everything out directly; also can the flight option lock in on right bottom corners instead?”
9. “can you make sure the readme contain the below requirement for project submission?” (the submission checklist, GitHub credits including Tripverse, and this AI declaration).

## Missing records

- The complete conversational transcript from the earliest sessions was **not
  exported** into the repository. The list above is the retained project-level
  prompt record; intermediate tool outputs and agent messages are not included.
- Provider API keys, account tokens, `.env` values and machine-specific paths are
  redacted and are not part of this file.
- The exported Taipei turn log under `docs/submission/traces/` is application
  data (a user prompt and its reply), not an AI-assistant prompt record.
- No personal information beyond project file paths and the public repository URL
  is included.

If the course requires the full raw prompt transcript, it can be supplied from
the development session on request; it is deliberately not committed because it
contains local paths, tool output and credentials-adjacent configuration.
