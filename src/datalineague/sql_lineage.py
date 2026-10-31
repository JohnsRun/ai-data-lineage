"""Phase 3: static SQL table reads/writes and a supported column DERIVES_FROM subset."""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from datalineague.relations import (
    ExternalEntity,
    Relation,
    RoutineContext,
    SourceContext,
    make_relation,
)

STATEMENT_START = re.compile(r"(?:;|>>|\b(?:THEN|ELSE|LOOP|BEGIN|EXCEPTION)\b)\s*$", re.IGNORECASE)
SQL_START = re.compile(r"\b(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)
OPEN_FOR = re.compile(r"\bOPEN\s+[\w$#.:]+\s+FOR\s*$", re.IGNORECASE)
CURSOR_IS = re.compile(r"\bCURSOR\b[^;]*\bIS\s*$", re.IGNORECASE | re.DOTALL)
CURSOR_FOR_LOOP = re.compile(r"\bFOR\s+[\w$#]+\s+IN\s*\(\s*$", re.IGNORECASE)
FORALL = re.compile(r"\bFORALL\b[^;]*$", re.IGNORECASE | re.DOTALL)
EXECUTE_IMMEDIATE = re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE)
OPEN_FOR_DYNAMIC = re.compile(r"\bOPEN\s+[\w$#.:]+\s+FOR\s+(?!SELECT\b|WITH\b)", re.IGNORECASE)
DBMS_SQL_PARSE = re.compile(r"\bDBMS_SQL\s*\.\s*PARSE\s*\(", re.IGNORECASE)


def _depth_zero(text: str, pattern: re.Pattern[str]) -> re.Match[str] | None:
    depth = 0
    for index, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif depth == 0:
            match = pattern.match(text, index)
            if match and (index == 0 or not re.match(r"[\w$#]", text[index - 1])):
                return match
    return None


def _statement_end(text: str, start: int, parenthesized: bool) -> int:
    depth = 0
    for index in range(start, len(text)):
        character = text[index]
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0 and parenthesized:
                return index
            depth -= 1
        elif character == ";" and depth == 0 and not parenthesized:
            return index
    return len(text)


def find_sql_statements(text: str) -> list[tuple[int, int]]:
    """Locate static SQL spans (start, end) in masked PL/SQL text."""
    spans: list[tuple[int, int]] = []
    consumed = 0
    for match in SQL_START.finditer(text):
        start = match.start()
        if start < consumed:
            continue
        before = text[max(0, start - 400) : start]
        keyword = match.group(1).upper()
        parenthesized = False
        if before.strip() == "" or STATEMENT_START.search(before[-40:]):
            pass
        elif keyword in {"SELECT", "WITH"} and (
            OPEN_FOR.search(before) or CURSOR_IS.search(before[before.rfind(";") + 1 :])
        ):
            pass
        elif keyword in {"SELECT", "WITH"} and CURSOR_FOR_LOOP.search(before):
            parenthesized = True
        elif keyword in {"INSERT", "UPDATE", "DELETE", "MERGE"} and FORALL.search(before[before.rfind(";") + 1 :]):
            pass
        else:
            continue
        end = _statement_end(text, start, parenthesized)
        spans.append((start, end))
        consumed = end
    return spans


def _strip_plsql_clauses(original: str, masked: str) -> str:
    """Remove PL/SQL-only INTO / RETURNING ... INTO clauses before SQL parsing."""
    first = masked.lstrip()[:7].upper()
    returning = _depth_zero(masked, re.compile(r"RETURNING\b", re.IGNORECASE))
    if returning:
        return original[: returning.start()]
    if first.startswith(("SELECT", "WITH")):
        into = _depth_zero(masked, re.compile(r"(?:BULK\s+COLLECT\s+)?INTO\b", re.IGNORECASE))
        if into:
            rest = masked[into.end() :]
            from_match = _depth_zero(rest, re.compile(r"FROM\b", re.IGNORECASE))
            cut_end = into.end() + from_match.start() if from_match else len(original)
            return original[: into.start()] + " " + original[cut_end:]
    return original


def _identifier_name(identifier: exp.Identifier) -> str:
    return identifier.name if identifier.args.get("quoted") else identifier.name.upper()


def _table_identity(table: exp.Table) -> tuple[str, str, bool] | None:
    if not isinstance(table.this, exp.Identifier):
        return None
    parts = [_identifier_name(part) for part in table.parts if isinstance(part, exp.Identifier)]
    qualified_name = ".".join(parts)
    return f"TABLE:{qualified_name}", qualified_name, len(parts) > 1


def _column_identity(table: exp.Table, column: exp.Column) -> tuple[str, str] | None:
    identity = _table_identity(table)
    if identity is None or not isinstance(column.this, exp.Identifier):
        return None
    qualified = f"{identity[1]}.{_identifier_name(column.this)}"
    return f"COLUMN:{qualified}", qualified


def _write_node(statement: exp.Expression) -> exp.Table | None:
    target = statement.this if isinstance(statement, (exp.Insert, exp.Update, exp.Delete, exp.Merge)) else None
    if isinstance(target, exp.Schema):
        target = target.this
    return target if isinstance(target, exp.Table) else None


def _cte_names(statement: exp.Expression) -> set[str]:
    return {cte.alias_or_name.upper() for cte in statement.find_all(exp.CTE)}


class _Collector:
    def __init__(self, context: SourceContext, routine: RoutineContext) -> None:
        self.context = context
        self.routine = routine
        self.relations: list[Relation] = []
        self.externals: list[ExternalEntity] = []

    def add(
        self,
        relation: str,
        source_id: str,
        target_id: str | None,
        start: int,
        end: int,
        evidence: str,
        status: str,
        quality: str = "source_exact",
        reason: str | None = None,
    ) -> None:
        self.relations.append(
            make_relation(
                relation,
                source_id,
                target_id,
                self.context.source_file,
                self.context.line(start),
                self.context.line(max(end - 1, start)),
                evidence,
                status,
                quality,
                reason,
            )
        )

    def external(self, object_id: str, kind: str, qualified_name: str, qualified: bool, offset: int) -> None:
        self.externals.append(
            ExternalEntity(
                object_id, kind, qualified_name, qualified, self.context.source_file, self.context.line(offset)
            )
        )


def _analyze_statement(collector: _Collector, original: str, masked: str, start: int, end: int) -> None:
    routine = collector.routine
    evidence = original.strip()
    leading = masked.lstrip()[:6].upper()
    default_relation = "READS" if leading.startswith(("SELECT", "WITH")) else "WRITES"
    try:
        statement = sqlglot.parse_one(_strip_plsql_clauses(original, masked), read="oracle")
    except Exception as error:  # SQLGlot raises several parse/tokenize error types
        collector.add(
            default_relation, routine.object_id, None, start, end, evidence, "unsupported",
            reason=f"SQL_PARSE_FAILED: {str(error).splitlines()[0][:80]}",
        )
        return
    if statement is None or not isinstance(statement, (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        collector.add(
            default_relation, routine.object_id, None, start, end, evidence, "unsupported",
            reason="SQL_STATEMENT_TYPE_UNSUPPORTED",
        )
        return
    write = _write_node(statement)
    ctes = _cte_names(statement)
    for table in statement.find_all(exp.Table):
        if table.find_ancestor(exp.Into) is not None:
            continue
        identity = _table_identity(table)
        if identity is None:
            collector.add(
                "READS", routine.object_id, None, start, end, evidence, "unsupported",
                reason="UNSUPPORTED_TABLE_EXPRESSION",
            )
            continue
        object_id, qualified_name, qualified = identity
        if not qualified and (qualified_name in ctes or qualified_name == "DUAL"):
            continue
        relation = "WRITES" if table is write else "READS"
        collector.add(relation, routine.object_id, object_id, start, end, evidence, "resolved")
        collector.external(object_id, "TABLE", qualified_name, qualified, start)
    if isinstance(statement, exp.Insert):
        _insert_columns(collector, statement, evidence, start, end)
    elif isinstance(statement, exp.Update):
        _update_columns(collector, statement, evidence, start, end)
    elif isinstance(statement, exp.Merge) and write is not None:
        identity = _table_identity(write)
        if identity:
            collector.add(
                "DERIVES_FROM", identity[0], None, start, end, evidence, "unsupported",
                reason="MERGE_COLUMN_LINEAGE_UNSUPPORTED",
            )


def _from_tables(select: exp.Select) -> dict[str, exp.Expression]:
    sources: dict[str, exp.Expression] = {}
    from_clause = select.args.get("from") or select.args.get("from_")
    candidates: list[exp.Expression] = []
    if from_clause is not None:
        candidates.append(from_clause.this)
    candidates.extend(join.this for join in select.args.get("joins") or [])
    for candidate in candidates:
        sources[(candidate.alias_or_name or "").upper()] = candidate
    return sources


def _column_edges(
    collector: _Collector,
    derived_id: str,
    projection: exp.Expression,
    sources: dict[str, exp.Expression],
    evidence: str,
    start: int,
    end: int,
) -> None:
    if projection.find(exp.Subquery) or projection.find(exp.Select):
        collector.add(
            "DERIVES_FROM", derived_id, None, start, end, evidence, "unsupported",
            reason="SUBQUERY_EXPRESSION_UNSUPPORTED",
        )
        return
    quality = "source_exact" if isinstance(projection, exp.Column) else "source_derived"
    for column in projection.find_all(exp.Column):
        qualifier = (column.table or "").upper()
        if not qualifier and column.name.upper() in collector.routine.declared:
            continue  # an unqualified declared PL/SQL name is a variable (Phase 4), not a column
        if qualifier:
            source = sources.get(qualifier)
        else:
            source = next(iter(sources.values())) if len(sources) == 1 else None
            if source is None:
                collector.add(
                    "DERIVES_FROM", derived_id, None, start, end, evidence, "ambiguous",
                    reason="UNQUALIFIED_COLUMN_MULTIPLE_TABLES",
                )
                continue
        if not isinstance(source, exp.Table):
            collector.add(
                "DERIVES_FROM", derived_id, None, start, end, evidence, "unsupported",
                reason="DERIVED_TABLE_UNSUPPORTED",
            )
            continue
        identity = _column_identity(source, column)
        if identity is None:
            continue
        collector.add("DERIVES_FROM", derived_id, identity[0], start, end, evidence, "resolved", quality)
        collector.external(identity[0], "COLUMN", identity[1], "." in identity[1].split(".", 1)[0], start)


def _insert_columns(
    collector: _Collector, statement: exp.Insert, evidence: str, start: int, end: int
) -> None:
    target = statement.this
    source = statement.expression
    if not isinstance(source, exp.Select):
        return
    write = _write_node(statement)
    identity = _table_identity(write) if write is not None else None
    if identity is None:
        return
    if not isinstance(target, exp.Schema):
        collector.add(
            "DERIVES_FROM", identity[0], None, start, end, evidence, "unsupported",
            reason="INSERT_WITHOUT_COLUMN_LIST",
        )
        return
    columns = [item for item in target.expressions if isinstance(item, exp.Identifier)]
    projections = source.expressions
    if any(isinstance(item, exp.Star) or (isinstance(item, exp.Column) and isinstance(item.this, exp.Star)) for item in projections):
        collector.add(
            "DERIVES_FROM", identity[0], None, start, end, evidence, "unsupported", reason="SELECT_STAR_UNSUPPORTED"
        )
        return
    if len(columns) != len(projections):
        collector.add(
            "DERIVES_FROM", identity[0], None, start, end, evidence, "unsupported", reason="COLUMN_COUNT_MISMATCH"
        )
        return
    sources = _from_tables(source)
    for column, projection in zip(columns, projections):
        derived = f"COLUMN:{identity[1]}.{_identifier_name(column)}"
        collector.external(derived, "COLUMN", derived[len("COLUMN:") :], identity[2], start)
        _column_edges(collector, derived, projection.unalias(), sources, evidence, start, end)


def _update_columns(
    collector: _Collector, statement: exp.Update, evidence: str, start: int, end: int
) -> None:
    write = _write_node(statement)
    identity = _table_identity(write) if write is not None else None
    if identity is None:
        return
    sources = {(write.alias_or_name or "").upper(): write}
    for assignment in statement.expressions:
        if not isinstance(assignment, exp.EQ) or not isinstance(assignment.this, exp.Column):
            continue
        derived = f"COLUMN:{identity[1]}.{_identifier_name(assignment.this.this)}"
        collector.external(derived, "COLUMN", derived[len("COLUMN:") :], identity[2], start)
        _column_edges(collector, derived, assignment.expression, sources, evidence, start, end)


def _dynamic_sql(collector: _Collector, text: str, base: int) -> None:
    routine = collector.routine
    source = collector.context.source
    for match in EXECUTE_IMMEDIATE.finditer(text):
        end = _statement_end(text, match.start(), False)
        collector.add(
            "REFERENCES", routine.object_id, None, base + match.start(), base + end,
            source[base + match.start() : base + end].strip(), "dynamic_sql", reason="DYNAMIC_SQL_EXECUTE_IMMEDIATE",
        )
    for match in OPEN_FOR_DYNAMIC.finditer(text):
        end = _statement_end(text, match.start(), False)
        collector.add(
            "REFERENCES", routine.object_id, None, base + match.start(), base + end,
            source[base + match.start() : base + end].strip(), "dynamic_sql", reason="DYNAMIC_SQL_OPEN_FOR",
        )
    for match in DBMS_SQL_PARSE.finditer(text):
        end = _statement_end(text, match.start(), False)
        collector.add(
            "REFERENCES", routine.object_id, None, base + match.start(), base + end,
            source[base + match.start() : base + end].strip(), "dynamic_sql", reason="DYNAMIC_SQL_DBMS_SQL",
        )


def extract_sql(
    context: SourceContext,
) -> tuple[list[Relation], list[ExternalEntity], list[dict[str, Any]]]:
    relations: list[Relation] = []
    externals: list[ExternalEntity] = []
    for routine in context.routines:
        if routine.begin is None:
            continue
        collector = _Collector(context, routine)
        for text, base in (
            (routine.declaration_text, routine.header_end),
            (routine.executable_text, routine.begin),
        ):
            for start, end in find_sql_statements(text):
                original = context.source[base + start : base + end]
                masked = context.masked[base + start : base + end]
                _analyze_statement(collector, original, masked, base + start, base + end)
            _dynamic_sql(collector, text, base)
        relations.extend(collector.relations)
        externals.extend(collector.externals)
    return relations, externals, []
