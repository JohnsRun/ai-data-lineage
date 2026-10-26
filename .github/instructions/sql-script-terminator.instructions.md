---
description: "Use when creating or editing Oracle script files (.sql, .pkb, .pkg). Ensure each executable script ends with a standalone slash delimiter and report any fix made."
name: "Oracle Script Terminator Rule"
applyTo:
  - "**/*.sql"
  - "**/*.pkb"
  - "**/*.pkg"
---
# Oracle Script Terminator Rule

For files ending in .sql, .pkb, or .pkg:

- Ensure a standalone slash character exists as a script terminator when required by Oracle script execution flow.
- If the script is missing the trailing slash terminator, add a new line that contains only / at the end of the script.
- Do not add duplicate trailing slash lines when one already exists.
- Preserve existing file content and formatting, and only add the minimal terminator fix when needed.
- In the final response to the user, explicitly state that the missing slash terminator was added and list the updated files.
