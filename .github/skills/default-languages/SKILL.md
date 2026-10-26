---
name: default-languages
migrated_from: "vscode-userdata:/Users/runjsh/Library/Application Support/Code/User/prompts/default-languages.prompt.md"
description: "Default code languages preference: PowerShell 7, Python 3, Oracle SQL"
---

# Default Languages (migrated prompt)

Original prompt content (for reference):

```
---
mode: ask
expression: Use pwsh7, Python3, Oracle SQL examples by default
---
默认代码示例语言要求：
1. PowerShell 7（文件类型 .ps1）
2. Python 3（文件类型 .py）
3. Oracle SQL（文件类型 .sql）

执行规则：
1. 用户未指定语言时，只使用以上三种语言给出示例。
2. 如果需要多语言对照，按 pwsh 7、python 3、oracle sql 的顺序给出。
3. 代码块必须标注正确语言标签：`powershell`、`python`、`sql`。
4. 任何示例都不要使用 JavaScript、Bash、TypeScript、Java 等其他语言，除非用户明确要求。
5. 示例应尽量短小可运行，并匹配对应文件扩展名（.ps1 / .py / .sql）。

输出前检查：
- 是否只出现上述三种语言示例。
- 是否每段示例都能直接复制到对应文件类型中运行或执行。
```

Notes: Migrated automatically by Copilot SDK migration; original file backed up in the recovery bundle.
