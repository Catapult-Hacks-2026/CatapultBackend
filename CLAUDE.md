# Commit Guidelines

## Style

Do not use emojis in responses or code output.

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