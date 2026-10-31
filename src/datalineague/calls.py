"""Phase 2: static routine-call extraction and scope-aware resolution."""

from __future__ import annotations

import re
from typing import Any

from datalineague.relations import (
    IDENTIFIER,
    ExternalEntity,
    Relation,
    RoutineContext,
    SourceContext,
    make_relation,
)

CALL_PATTERN = re.compile(
    rf"(?<![\w$#.@:])(?P<chain>{IDENTIFIER}(?:\s*\.\s*{IDENTIFIER})*)(?=\s*(?P<next>[(;]))"
)
STATEMENT_START = re.compile(r"(?:;|>>|\b(?:THEN|ELSE|LOOP|BEGIN|EXCEPTION)\b)\s*$", re.IGNORECASE)
SQL_OBJECT_POSITION = re.compile(r"\b(?:INTO|FROM|JOIN|UPDATE)\s+$", re.IGNORECASE)

KEYWORDS = {
    "AND", "AS", "BETWEEN", "BY", "CASE", "CONTINUE", "COMMIT", "ELSE", "ELSIF", "END", "EXCEPTION",
    "EXISTS", "EXIT", "FROM", "GOTO", "IF", "IN", "INTO", "IS", "LIKE", "LOOP", "NOT", "NULL", "ON",
    "OR", "RAISE", "RETURN", "ROLLBACK", "SAVEPOINT", "SELECT", "THEN", "UNION", "VALUES", "WHEN",
    "WHERE", "WHILE", "WITH", "FOR", "OF", "SET", "AT", "USING", "BEGIN",
}
BUILTIN_FUNCTIONS = {
    "ABS", "ADD_MONTHS", "ASCII", "AVG", "CAST", "CEIL", "CHR", "COALESCE", "CONCAT", "COUNT", "DECODE",
    "DELETE", "EXTEND", "EXTRACT", "FIRST", "FLOOR", "GREATEST", "INITCAP", "INSTR", "LAST", "LEAST",
    "LENGTH", "LENGTHB", "LIMIT", "LISTAGG", "LOWER", "LPAD", "LTRIM", "MAX", "MIN", "MOD", "MONTHS_BETWEEN",
    "NEXT", "NULLIF", "NVL", "NVL2", "PRIOR", "RAISE_APPLICATION_ERROR", "RANK", "REGEXP_INSTR",
    "REGEXP_LIKE", "REGEXP_REPLACE", "REGEXP_SUBSTR", "REPLACE", "ROUND", "ROW_NUMBER", "RPAD", "RTRIM",
    "SIGN", "TABLE", "SQLCODE", "SQLERRM", "SUBSTR", "SUM", "SYSDATE", "SYSTIMESTAMP", "TO_CHAR", "TO_CLOB",
    "TO_DATE", "TO_NUMBER", "TO_TIMESTAMP", "TRANSLATE", "TRIM", "TRUNC", "UPPER", "USER",
}
ORACLE_PACKAGE_PREFIXES = ("DBMS_", "UTL_", "APEX_", "OWA", "HTP", "HTF", "WPG_", "SYS", "XMLTYPE")


def _external_id(segments: list[str]) -> str:
    return "EXTERNAL:" + ".".join(segment.upper() for segment in segments)


def _is_oracle_supplied(segments: list[str]) -> bool:
    package = segments[-2].upper() if len(segments) >= 2 else ""
    return package.startswith(ORACLE_PACKAGE_PREFIXES) or segments[0].upper() == "SYS"


def _resolve(
    segments: list[str], routine: RoutineContext, context: SourceContext
) -> tuple[str, str | None, str | None] | None:
    """Return (status, target_id, reason), or None when the chain is not a routine call."""
    upper = [segment.upper() for segment in segments]
    if upper[0] in routine.declared:
        return None
    index = context.index
    if len(upper) == 1:
        name = upper[0]
        for scope_id in routine.scope_chain:
            candidates = index.routine_candidates(scope_id, name)
            if len(candidates) == 1:
                return "resolved", candidates[0], None
            if len(candidates) > 1:
                return "ambiguous", None, "AMBIGUOUS_OVERLOAD"
        if name in KEYWORDS or name in BUILTIN_FUNCTIONS:
            return None
        return "unsupported", None, "UNRESOLVED_CALL_TARGET"
    if len(upper) > 3:
        return "unsupported", None, "UNSUPPORTED_QUALIFICATION"
    package, name = upper[-2], upper[-1]
    if package in index.packages:
        candidates = index.routine_candidates(package, name)
        if len(candidates) == 1:
            return "resolved", candidates[0], None
        if len(candidates) > 1:
            return "ambiguous", None, "AMBIGUOUS_OVERLOAD"
        return "unsupported", _external_id(segments), "ROUTINE_NOT_IN_REGISTRY"
    reason = "EXTERNAL_BUILTIN_PACKAGE" if _is_oracle_supplied(segments) else "EXTERNAL_ROUTINE_UNAVAILABLE"
    return "unsupported", _external_id(segments), reason


def _closing_offset(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def extract_calls(
    context: SourceContext,
) -> tuple[list[Relation], list[ExternalEntity], list[dict[str, Any]]]:
    relations: list[Relation] = []
    externals: list[ExternalEntity] = []
    for routine in context.routines:
        if routine.begin is None:
            continue
        text = routine.executable_text
        for match in CALL_PATTERN.finditer(text):
            chain = match.group("chain")
            segments = [part.strip() for part in chain.split(".")]
            if match.group("next") == ";" and not STATEMENT_START.search(text[max(0, match.start() - 40) : match.start()]):
                continue
            if segments[0].upper() in KEYWORDS and len(segments) == 1:
                continue
            if SQL_OBJECT_POSITION.search(text[max(0, match.start() - 12) : match.start()]):
                continue
            resolved = _resolve(segments, routine, context)
            if resolved is None:
                continue
            status, target, reason = resolved
            start = routine.begin + match.start()
            end = routine.begin + match.end()
            if match.group("next") == "(":
                opening = match.end() + text[match.end() :].index("(")
                closing = _closing_offset(text, opening)
                end = routine.begin + (closing + 1 if closing is not None else opening + 1)
            start_line = context.line(start)
            end_line = context.line(max(end - 1, start))
            relations.append(
                make_relation(
                    "CALLS",
                    routine.object_id,
                    target,
                    context.source_file,
                    start_line,
                    end_line,
                    context.source[start:end].strip(),
                    status,
                    reason=reason,
                )
            )
            if target is not None and target.startswith("EXTERNAL:"):
                externals.append(
                    ExternalEntity(
                        target,
                        "EXTERNAL_ROUTINE",
                        target[len("EXTERNAL:") :],
                        len(segments) > 1,
                        context.source_file,
                        start_line,
                    )
                )
    return relations, externals, []
