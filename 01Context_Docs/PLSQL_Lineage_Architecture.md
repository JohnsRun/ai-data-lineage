# PL/SQL Static Lineage — Project Context & Implementation Plan

## 1. Project Overview

### Objective

Build a lightweight, evidence-based PL/SQL static analysis capability integrated with GitHub Copilot Agent Skills.

The solution identifies PL/SQL objects, extracts procedure call relationships, analyzes static SQL dependencies, and generates Markdown documentation for data lineage and execution sequences.

The primary goals are:

- Improve lineage accuracy by replacing unconstrained LLM inference with deterministic parsing and explicit evidence.
- Reduce analysis latency and token consumption by avoiding repeated scans of the entire workspace.
- Provide reusable, indexed PL/SQL metadata for package, procedure, table, variable, and column lineage queries.
- Generate auditable Markdown reports with source locations and uncertainty annotations.

### Scope

**In scope**
- Oracle PL/SQL package specifications and package bodies.
- Procedures, functions, variables, parameters, cursors, records/types, tables, columns, and sequences.
- Procedure/function call relationships.
- Static SQL read/write dependencies.
- Column-level lineage where it can be reliably determined.
- Source evidence, line numbers, and unresolved relationships.
- Sequence diagrams and static lineage documentation.

**Out of scope**
- SAP TrueComp and other non-PL/SQL modules.
- Database runtime tracing or mandatory Oracle database connections.
- Full Oracle compiler semantics.
- Automatic resolution of arbitrary dynamic SQL.
- Requiring an LLM to independently reconstruct lineage from the entire repository.

## 2. Architecture Principles

1. **Deterministic first:** Use Python parsing and explicit rules for relationships that can be established from source code.
2. **LLM as a reasoning assistant:** Use GitHub Copilot for complex interpretation, query understanding, and report generation, not as the sole source of lineage facts.
3. **Evidence-based relationships:** Every confirmed relationship must reference source evidence and a source location.
4. **Incremental analysis:** Build a reusable object registry and relationship index, then analyze only relevant objects and their dependencies.
5. **Graceful uncertainty:** Mark ambiguous, dynamic, or unsupported constructs as unresolved rather than inventing relationships.
6. **Lightweight dependencies:** Do not introduce ANTLR, Java-based parser generation, a graph database, or an Oracle connection in the initial version.

## 3. Technology Stack

### Required Python packages

```bash
pip install sqlglot sqlparse
```

- `sqlparse`: Tokenization, SQL statement splitting, and preliminary SQL structure inspection.
- `sqlglot`: Parsing extracted static SQL using the Oracle dialect, inspecting ASTs, and deriving SQL dependencies.
- Python standard library: `re`, `pathlib`, `json`, `dataclasses`, `hashlib`, and related modules for source scanning, models, indexing, and caching.

Do not use `sqlparse` or `sqlglot` as if either were a complete PL/SQL parser. The initial PL/SQL object scanner will use carefully designed lexical scanning and targeted extraction rules.

## 4. Target Architecture

```text
PL/SQL Source Files
        |
        v
Source Scanner
  - Ignore comments and string contents
  - Preserve source offsets and line numbers
        |
        v
PL/SQL Object Registry
  - Package / Procedure / Function
  - Variables / Parameters / Cursors / Types
        |
        +-------------------------+
        |                         |
        v                         v
Call Relationship Extractor   Static SQL Extractor
        |                         |
        |                    SQLGlot AST
        |                         |
        +------------+------------+
                     |
                     v
          Name and Scope Resolution
                     |
                     v
        Lineage Relationship Index
          - Entities and edges
          - Source evidence
          - Resolution status
                     |
                     v
             Query Interface
                     |
           GitHub Copilot Agent
                     |
                     v
       Markdown Lineage Documentation
         - Sequence diagrams
         - Static lineage
         - Unresolved dependencies
```

## 5. Object Ontology

### 5.1 Entity types

| Entity | Required metadata |
|---|---|
| PACKAGE | Name, source file, schema if known |
| PROCEDURE | Parent package, signature, source range |
| FUNCTION | Parent package, signature, return type if known |
| PARAMETER | Parent routine, mode, datatype if known |
| VARIABLE | Parent scope, datatype if known, declaration location |
| CURSOR | Parent scope, query source, declaration location |
| RECORD / TYPE | Parent scope, fields if extractable |
| TABLE | Qualified name, access type, source evidence |
| COLUMN | Qualified table and column name, source evidence |
| SEQUENCE | Qualified name and usage such as NEXTVAL/CURRVAL |

Each object must have a stable identifier that distinguishes same-named objects in different packages or scopes.

### 5.2 Relationship types

- `CONTAINS`: package contains a procedure or function.
- `CALLS`: procedure/function invokes another routine.
- `READS`: routine reads from a table or view.
- `WRITES`: routine inserts, updates, or deletes data in a table.
- `DERIVES_FROM`: target column or expression depends on source data.
- `ASSIGNS_TO`: source expression is assigned to a variable or parameter.
- `REFERENCES`: an object refers to another object without a more specific proven relationship.

Do not collapse these into a generic dependency relationship.

### 5.3 Relationship metadata

Each relationship should include:

- `source_id`
- `target_id`
- `relation`
- `source_file`
- `start_line`
- `end_line`
- `evidence`
- `resolution_status`
- `confidence` or an equivalent evidence-quality classification

Use explicit statuses such as `resolved`, `ambiguous`, `dynamic_sql`, and `unsupported`. Confidence must not be used to disguise an unverified LLM guess as a confirmed relationship.

## 6. Analysis Pipeline

### Stage A: Source scanning

- Discover relevant `.pks`, `.pkb`, `.sql`, and other explicitly configured PL/SQL source files.
- Preserve original source offsets and line numbers.
- Implement lexical handling for comments, quoted strings, and quoted identifiers before using regular expressions.
- Do not interpret text inside comments or strings as executable PL/SQL.
- Extract package boundaries and routine declarations.
- Identify declarations for variables, parameters, cursors, records, and types where supported.

**Important:** PL/SQL permits nested blocks, quoted strings, alternative quoting, and complex declaration syntax. A simple regex-only implementation must not claim complete syntax coverage. Unsupported constructs must be reported.

### Stage B: Object Registry

Create a reusable JSON-based registry containing entities and their source locations.

Example:

```json
{
  "object_id": "PKG_COMMISSION.CALC_COMMISSION",
  "object_type": "PROCEDURE",
  "parent_id": "PKG_COMMISSION",
  "source_file": "PKG_COMMISSION.pkb",
  "start_line": 120,
  "end_line": 168,
  "status": "resolved"
}
```

The example is illustrative, not extracted from a real source file.

### Stage C: Procedure call lineage

- Identify statically recognizable routine calls.
- Resolve local routines, package-qualified routines, and known external package references where sufficient source context exists.
- Use parent scope and declaration information to disambiguate names.
- Record call-site evidence and line numbers.
- Mark unresolved overloads or unavailable external implementations.

Do not infer that every identifier followed by parentheses is necessarily a procedure call.

### Stage D: Static SQL lineage

- Identify SQL statements embedded in PL/SQL.
- Use `sqlparse` for tokenization and statement handling where useful.
- Use SQLGlot with the Oracle dialect to parse supported static SQL.
- Extract table references and read/write operations.
- Derive column-level mappings when the AST and available schema information support them.
- Track aliases and qualified column references.
- Preserve the original SQL fragment as evidence.

Handle `INSERT ... SELECT`, `UPDATE`, `DELETE`, `MERGE`, and ordinary `SELECT` statements incrementally.

For `SELECT *`, ambiguous columns, unresolved synonyms, and missing schema definitions, record the limitations rather than inventing column mappings.

### Stage E: Variable data flow

Implement this only after the object registry and static SQL lineage are stable.

Initially support:
- Direct variable assignments.
- Parameter-to-variable assignments.
- Simple expressions with identifiable source references.
- Cursor query sources and fetch targets where supported.
- Variable values used in static SQL statements when their data dependencies can be established.

Do not claim full data-flow analysis across complex control flow, aliasing, dynamic SQL, or arbitrary nested blocks.

### Stage F: Query interface

Provide reusable queries for:
- Package object inventory.
- Procedure call hierarchy.
- Table read/write dependencies.
- Table and column upstream lineage.
- Variable assignment sources and destinations.
- Unresolved relationships and parsing gaps.

Return only the relevant graph neighborhood and source evidence required for the requested analysis.

### Stage G: Copilot report generation

The Copilot Skill should:
1. Identify the user's target object and lineage direction.
2. Query the existing registry and relationship index.
3. Expand relevant dependencies only as needed.
4. Inspect source code for missing or ambiguous facts.
5. Generate the requested Markdown report.
6. Clearly separate confirmed relationships from unresolved or inferred relationships.

Do not scan the entire workspace by default when the required index is available.

## 7. Implementation Plan

### Phase 1 — Foundation and Object Registry

**Goal:** Establish deterministic object identification.

Tasks:
1. Define the source file discovery rules.
2. Implement lexical scanning that preserves line numbers and ignores comments and strings correctly.
3. Extract package, procedure, and function declarations.
4. Add variable, parameter, cursor, and type extraction for supported patterns.
5. Create stable object identifiers and JSON output.
6. Build a small regression test suite using representative real packages.

Deliverables:
- Python scanner.
- Object registry JSON.
- Parsing diagnostics.
- Tests for declarations, comments, strings, nested blocks, and same-named objects in different scopes.

Acceptance criteria:
- Every extracted object has a source location.
- Objects are not falsely extracted from comments or strings in tested cases.
- Unsupported patterns are reported explicitly.
- Repeated runs over unchanged files produce stable registry output.

### Phase 2 — Call Graph

**Goal:** Identify routine invocation relationships.

Tasks:
1. Extract routine invocation candidates.
2. Resolve package-qualified calls and known local calls.
3. Handle scope and signatures where practical.
4. Store `CALLS` relationships with source evidence.
5. Support direct and transitive call hierarchy queries.

Deliverables:
- Call relationship index.
- Upstream/downstream routine query functions.
- Regression tests for nested routines, overloaded names, and unresolved external references.

Acceptance criteria:
- Every confirmed call edge has source evidence.
- Ambiguous calls are not silently treated as resolved.
- Direct and transitive call queries return reproducible results.

### Phase 3 — Static SQL Lineage

**Goal:** Extract reliable table and column dependencies.

Tasks:
1. Locate SQL fragments inside PL/SQL code.
2. Parse supported SQL with SQLGlot's Oracle dialect.
3. Extract table reads and writes.
4. Derive column lineage for supported SQL constructs.
5. Track aliases and qualified columns.
6. Record unsupported syntax and missing-schema limitations.

Deliverables:
- Static SQL dependency extractor.
- Table-level lineage.
- Initial column-level lineage.
- Evidence-backed SQL diagnostics.

Acceptance criteria:
- Every confirmed table/column relationship has source evidence.
- Test cases cover common DML patterns.
- Unsupported or ambiguous column mappings are explicitly marked.

### Phase 4 — Variable Data Flow and Ontology Refinement

**Goal:** Improve lineage across variable assignments and cursor operations.

Tasks:
1. Model variable declarations and scopes.
2. Extract assignments and supported expressions.
3. Track simple data dependencies.
4. Connect cursor sources to fetch targets where possible.
5. Improve relation resolution and ambiguity handling.

Deliverables:
- Assignment relationship index.
- Variable upstream/downstream queries.
- Expanded regression suite.

Acceptance criteria:
- Supported assignments are traceable to their source statements.
- Unresolved data flow is distinguishable from confirmed lineage.

### Phase 5 — Copilot Skill Integration

**Goal:** Make the indexed lineage available through the existing GitHub Copilot workflow.

Tasks:
1. Define query inputs and outputs.
2. Add object lookup and lineage query scripts.
3. Update the Skill instructions to prefer the registry over full-workspace scanning.
4. Generate Markdown reports with evidence and unresolved sections.
5. Produce Mermaid sequence diagrams and static lineage diagrams where appropriate.
6. Integrate with existing project context and instructions without overwriting unrelated Skill behavior.

Deliverables:
- Copilot Skill instructions.
- Query interface.
- Markdown report templates.
- Example outputs for package, procedure, table, variable, and column questions.

Acceptance criteria:
- Reports distinguish confirmed facts from unresolved relationships.
- Source locations can be traced back to the repository.
- Queries do not trigger unnecessary full-workspace rescans when the index is current.

### Phase 6 — Validation and Performance

**Goal:** Demonstrate measurable improvement over the current LLM-only approach.

Tasks:
1. Select 10–20 representative packages or procedures.
2. Establish manually reviewed reference lineage.
3. Run the existing LLM-only workflow and the indexed workflow against the same questions.
4. Compare accuracy, latency, token consumption, and unresolved-case handling.
5. Fix high-impact errors before expanding coverage.

Metrics:
- Object identification precision and recall.
- Routine call relationship precision and recall.
- Table-level lineage precision and recall.
- Column-level lineage precision and recall.
- End-to-end analysis latency.
- Token consumption.
- Rate of unsupported relationships incorrectly presented as confirmed.

Do not claim improvement until measured against the reference set.

## 8. Non-Functional Requirements

- Python-first implementation.
- Minimal external dependencies.
- No Java or ANTLR requirement.
- No mandatory database connection.
- Deterministic outputs for unchanged source files.
- Source evidence for confirmed relationships.
- Explicit reporting of unsupported constructs.
- Incremental indexing and caching where practical.
- No silent conversion of LLM-generated guesses into verified graph edges.

## 9. Known Limitations and Risks

1. SQLGlot is a SQL parser, not a complete PL/SQL parser.
2. SQLParse tokenization does not provide complete PL/SQL semantic analysis.
3. Regex alone is insufficient for robust parsing of nested PL/SQL syntax.
4. Column-level lineage may require schema metadata to resolve `SELECT *`, aliases, and ambiguous identifiers.
5. Dynamic SQL may be only partially resolvable without runtime information.
6. External procedures, synonyms, overloaded routines, and conditional compilation can introduce ambiguity.
7. Source line numbers may become stale when files change; indexes must be refreshed or invalidated.
8. Sequence diagrams describe statically identified calls and should not imply that every path executes at runtime.

## 10. Immediate Next Actions

Implement Phase 1 only.

1. Inspect the existing repository and Copilot Skill conventions.
2. Identify a representative PL/SQL package with specification and body.
3. Build a lexical scanner and extract package, procedure, and function declarations.
4. Output a stable JSON Object Registry with source locations.
5. Validate it against manually reviewed objects.
6. Proceed to call graph extraction only after Phase 1 passes.

**Core design decision:** Use Python for deterministic extraction and indexing, SQLGlot for supported static SQL parsing, and GitHub Copilot for query interpretation and documentation. Treat the object ontology as a structured data model and validation framework, not as a replacement for parsing or data-flow analysis.
