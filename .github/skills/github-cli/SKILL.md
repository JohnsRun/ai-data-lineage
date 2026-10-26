---
name: github-cli
description: Use GitHub CLI to inspect and manage repositories, issues, pull requests, branches, commits, releases, and authentication state safely. Use when users need common GitHub operations from the terminal.
argument-hint: Describe the GitHub action you want to perform (list repos, create issue, open PR, check auth, etc.)
user-invocable: true
---

# GitHub CLI Skill

## Purpose

Use the GitHub CLI (`gh`) to perform common GitHub workflows safely and predictably from the terminal. This skill is designed for repository management, issue and pull request work, branch operations, commit inspection, release management, and authentication checks.

## Activation Conditions

Use this skill when the user asks to:

- review or manage GitHub repositories
- list, create, or comment on issues
- create, review, or merge pull requests
- inspect or manage branches
- review commits or compare changes
- create or publish releases
- check current authentication or connection status

Use this skill only for GitHub operations that can be handled with `gh`.

## Core Workflow

Follow this pattern for every GitHub task:

1. Confirm the target repository or remote context.
2. Check authentication state when needed with `gh auth status`.
3. Choose the correct `gh` command for the operation.
4. Show the exact command and required parameters before running it.
5. If the operation is destructive or modifies remote state, ask for explicit confirmation.
6. Run the command only after approval.
7. Interpret the output and explain what happened, including errors or next steps.

## Required Command Guidance

Use the GitHub CLI for all operations. Prefer the standard `gh ...` commands over custom scripts or alternative clients.

### Authentication

Use `gh auth status` to verify login status.

```bash
gh auth status
```

If the user needs to sign in, explain that authentication is performed by GitHub CLI itself and never expose or request credentials in chat.

### Repositories

Common commands:

```bash
gh repo list --limit 20
gh repo view OWNER/REPO
gh repo create OWNER/REPO --public
gh repo clone OWNER/REPO
gh repo fork OWNER/REPO --clone
```

Required parameters:

- `OWNER/REPO` is required for most repository-specific commands
- `--public`, `--private`, or `--internal` are used when creating a repository
- `--clone` is used when forking and immediately cloning locally

### Issues

Common commands:

```bash
gh issue list --repo OWNER/REPO
gh issue view 123 --repo OWNER/REPO
gh issue create --repo OWNER/REPO --title "Bug: login fails" --body "Steps to reproduce..."
gh issue comment 123 --repo OWNER/REPO --body "Update: investigating the root cause"
gh issue close 123 --repo OWNER/REPO
```

Required parameters:

- `--repo OWNER/REPO` is required unless already in a Git repository context
- issue numbers are required for `view`, `comment`, `close`, and `reopen`
- `--title` and `--body` are required for creating a new issue

### Pull Requests

Common commands:

```bash
gh pr list --repo OWNER/REPO
gh pr view 456 --repo OWNER/REPO
gh pr create --repo OWNER/REPO --base main --head feature/login-fix --fill
gh pr checkout 456 --repo OWNER/REPO
gh pr merge 456 --repo OWNER/REPO --merge
gh pr close 456 --repo OWNER/REPO
```

Required parameters:

- `--repo OWNER/REPO` is usually required
- `--base` and `--head` are required for creating a PR from a branch
- `--fill` can auto-populate title/body from commits and branch metadata
- `--merge`, `--squash`, or `--rebase` choose the merge strategy

### Branches

Common commands:

```bash
gh api repos/OWNER/REPO/branches
gh branch list --repo OWNER/REPO
gh checkout -b feature/my-change
gh branch delete feature/my-change --repo OWNER/REPO
```

Required parameters:

- repository context is required for repo-scoped branch actions
- `gh branch delete` is destructive and must be confirmed explicitly

### Commits

Use `gh` with repository APIs when the user wants commit information or commit metadata:

```bash
gh api repos/OWNER/REPO/commits
gh api repos/OWNER/REPO/commits/COMMIT_SHA
gh api repos/OWNER/REPO/compare/base...head
```

Required parameters:

- `OWNER/REPO` identifies the target repository
- `COMMIT_SHA` identifies a specific commit
- `base...head` compares two refs or branches

### Releases

Common commands:

```bash
gh release list --repo OWNER/REPO
gh release view v1.2.3 --repo OWNER/REPO
gh release create v1.2.3 --repo OWNER/REPO --title "v1.2.3" --notes "Release notes"
gh release edit v1.2.3 --repo OWNER/REPO --title "v1.2.3"
gh release delete v1.2.3 --repo OWNER/REPO
```

Required parameters:

- release tag is required for `view`, `edit`, and `delete`
- `--title` and `--notes` are recommended though not always strictly required for create
- `--repo` is required when run outside the repository context

## Safety Rules

### Mandatory

1. Use GitHub CLI commands only; do not substitute with GitHub web APIs or other clients.
2. Never request or expose sensitive credentials, tokens, or personal access keys in the chat.
3. Ask for explicit confirmation before destructive or state-changing operations, including:
   - deleting a repository
   - deleting a branch
   - deleting a release
   - closing or merging pull requests
   - deleting issues or permanently removing data
4. Show the exact command and explain the required arguments before execution.
5. If the command fails, report the error directly and suggest the most likely corrective action.
6. Avoid broad destructive operations unless the user clearly confirms the scope.

### Output Handling

- If `gh` returns a success message, summarize what changed and the affected resource.
- If `gh` returns an error, explain the likely cause in plain language.
- For large outputs, limit the response to the relevant lines and highlight important values.
- When the command requires repo context, confirm the repository before running it.

## Workflow Quality Checklist

Before finalizing a GitHub operation, confirm all of the following:

- ✓ Repository target is clear
- ✓ Command uses `gh` and includes necessary arguments
- ✓ Authentication state has been checked when relevant
- ✓ Destructive actions have explicit approval
- ✓ Command output was reviewed for success or failure
- ✓ User was told what action was performed and its consequence

## Example Prompts

- "Check my GitHub auth status"
- "List the last 10 repositories I can access"
- "Create a new issue in owner/repo for the login bug"
- "Open a pull request from feature/login-fix into main"
- "Show the last 20 commits on main"
- "Create a release for v1.2.0 with release notes"
- "Delete the temporary branch after merging"

## Example Execution Flow

Example: create a pull request.

```bash
gh auth status
gh pr create --repo OWNER/REPO --base main --head feature/login-fix --fill
```

Before running the actual command, explain briefly:

- target repository: `OWNER/REPO`
- base branch: `main`
- source branch: `feature/login-fix`
- PR will be created from the current branch state

After the user confirms, run it and report the result.

## Limits

This skill should be used for everyday GitHub operations via `gh`. It does not replace deep repository-specific automation or external CI/CD actions. When a task exceeds `gh` capabilities, explain the limitation and suggest the next safe step.
