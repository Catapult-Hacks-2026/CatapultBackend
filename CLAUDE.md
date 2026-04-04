# Long-Running Agent Harness

Guidelines for operating across multiple sessions or context windows.

## Core Problem

You lose context between sessions. Without explicit handoff mechanisms, each new session starts blind — no memory of prior work, environment state, or what was already completed. The files on disk are your source of truth.

## Session Architecture

### First Session (Initializer)

Before any feature work begins (Do this once):

1. Start the development server via `init.sh`
2. Create a `progress.txt` changelog
3. Generate `features.json` with all requirements (all with `status: "not_started"`)
4. Put progress.txt, init.sh, and features.json into .gitignore
5. Make an initial git commit documenting the setup

### Subsequent Sessions (Coding Agent)

Begin every session by:

1. Running `pwd` to confirm your working directory
2. Reading `progress.txt`, `features.json`, and git logs
3. Running `init.sh` to restore the environment
4. Running the test suite and recording the pass/fail summary in `progress.txt`
5. Selecting the highest-priority incomplete item from `features.json`

## Feature List (`features.json`)

Track all requirements in a JSON file, not Markdown. It is unacceptable to remove or edit feature entries because this could lead to missing or incomplete functionality.

```json
{
  "features": [
    {
      "id": 1,
      "category": "functional",
      "description": "Users can start a new chat from the main interface",
      "acceptance_criteria": [
        "A 'New Chat' button is present and visible on the main interface",
        "Clicking it opens a fresh conversation with a welcome state",
        "The new conversation appears in the sidebar"
      ],
      "status": "not_started"
    }
  ],
  "total": 200,
  "complete": 0,
  "not_started": 200
}
```

Valid `status` values: `"not_started"`, `"complete"`

### Rules

- Only update the `status` field. Do not remove or edit any feature entry.
- Set `status: "complete"` only after running the test suite and verifying the feature end-to-end.
- Do not try to one-shot all the implementations.

## Progress File (`progress.txt`)

Append a summary at the end of every session:

- What was worked on
- Which features were completed (IDs and names)
- Test suite summary (how many passing, failing, not run)
- Any known issues, side effects, or blockers
- Environment state at session end

Example:
```
Session 3:
- Implemented new chat button (feature #1) — complete
- Test suite: 12 passing, 1 failing (user_management), 4 not run
- Next: investigate user_management test failure
```

## `init.sh`

Read and run `init.sh` at the start of every session. It:

- Starts the development server
- Runs baseline tests and linters
- Restores environment state needed before feature work

## State Management

- **Feature status**: use `features.json` so schema requirements are clear
- **Test results**: run the test suite each session and record the summary in `progress.txt` — the test files in the codebase are the source of truth
- **Progress notes** (general context, blockers): use freeform text in `progress.txt`
- **History and checkpoints**: use git — it provides a log of completed work and allows rollback

## Context Window Management

Your context window will be automatically compacted as it approaches its limit, allowing you to continue working indefinitely from where you left off. Do not stop tasks early due to token budget concerns. As you approach your token budget limit, save your current progress and state to disk before the context window refreshes. Be as persistent and autonomous as possible and complete tasks fully. Never artificially stop any task early regardless of remaining context.

### Starting Fresh vs. Compaction

When starting a fresh context window, discover state from the filesystem rather than relying on compaction:

- Run `pwd` — only read and write files in your working directory
- Review `progress.txt`, `features.json`, and git logs
- Run the test suite and record the summary in `progress.txt` before moving on to new features

## Incremental Progress

- Commit after each completed feature with a message from the Commit Format
- Write a progress summary before ending the session
- Never leave broken code uncommitted
- Use your entire context window productively — complete components before stopping:

```
This is a very long task. Plan your work clearly. Complete components before moving on. Do not run out of context with significant uncommitted work. Continue working systematically until the task is complete.
```

## Common Failure Modes

| Problem | Solution                                               |
|---|--------------------------------------------------------|
| Declaring the project complete prematurely | Create 200+ item `features.json`, all `not_started`    |
| Buggy or undocumented code left between sessions | Write `init.sh` + `progress.txt`; commit on completion |
| Marking features done without testing | Run test suite and verify end-to-end before setting `complete` |
| Wasted time on environment setup | Read and run `init.sh` at session start                |

## Testing

- Run the test suite at the start and end of every session. Record the pass/fail summary in `progress.txt`.
- The test files in the codebase are the source of truth — do not track test status in a separate JSON file.
- Prefer end-to-end browser automation (e.g., Playwright MCP, Puppeteer MCP) over unit tests or curl for verifying features.
- Do not remove or edit existing tests — doing so could lead to missing or buggy functionality.
- Browser-native alert modals are not visible to Puppeteer MCP — do not rely on them.

## Balancing Autonomy and Safety

Take local, reversible actions (editing files, running tests) freely. Before taking actions that are hard to reverse or affect shared systems, ask the user:

- Destructive: deleting files or branches, dropping database tables, `rm -rf`
- Hard-to-reverse: `git push --force`, `git reset --hard`, amending published commits
- Visible to others: pushing code, commenting on PRs or issues, sending messages, modifying shared infrastructure

Do not use destructive actions as a shortcut when you encounter an obstacle.

## Commit Format

All commits must follow this format:

```
<type>(<scope>): <short, imperative summary>

- Brief list of meaningful changes
- Focus on what and why
```

## Rules

- Use imperative tense (`add`, `fix`, `remove`)
- No period at the end of the summary line
- Keep the summary under 50 characters
- One logical change per commit

## Commit Types

| Type       | Description                                        |
| ---------- | -------------------------------------------------- |
| `feat`     | New user-visible feature                           |
| `fix`      | Bug fix                                            |
| `refactor` | Code restructuring with no behavior change         |
| `docs`     | Documentation only                                 |
| `test`     | Adding or updating tests                           |
| `style`    | Formatting, whitespace, linting (no logic changes) |
| `perf`     | Performance improvements                           |
| `chore`    | Maintenance tasks, tooling, dependencies           |
| `ci`       | CI/CD configuration                                |
| `build`    | Build system or build tooling                      |

## Scopes

Use the most specific scope available for where the change happened.

**Features / Domains**
- `auth`, `billing`, `search`, `checkout`, `profile`

**Technical Layers**
- `api`, `ui`, `db`, `backend`, `frontend`

**Infrastructure**
- `ci`, `deps`, `config`

## Summary Guidelines

The summary should answer: *What does this commit do?*

**Good examples:**
```
feat(auth): add password reset endpoint
fix(ui): prevent crash on empty results
refactor(api): simplify request validation
```

**Avoid:**
```
fix: implement step 1 of plan
update stuff
feat(auth): added password reset
```

## Commit Body

Include a body when the change is non-trivial:
- Use bullet points
- Explain why, not just what
- Mention side effects or important behavior changes

**Example:**
```
fix(search): handle empty query safely

- Return early when search term is empty
- Prevent null reference error in result parser
- Improves stability on initial page load
```