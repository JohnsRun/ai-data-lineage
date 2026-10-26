"""Deterministic Phase 1 PL/SQL object-registry scanner."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SOURCE_EXTENSIONS = {".pkb", ".pks", ".pkg", ".sql"}
IGNORED_DIRECTORIES = {".git", ".venv", "__pycache__"}
IDENTIFIER = r"[A-Za-z][A-Za-z0-9_$#]*"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    source_file: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Entity:
    object_id: str
    object_type: str
    parent_id: str | None
    source_file: str
    start_line: int
    end_line: int
    status: str
    signature: str | None = None
    datatype: str | None = None
    mode: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class Registry:
    entities: list[Entity]
    diagnostics: list[Diagnostic]

    def to_json(self) -> str:
        return json.dumps(
            {
                "entities": [asdict(entity) for entity in self.entities],
                "diagnostics": [asdict(diagnostic) for diagnostic in self.diagnostics],
            },
            indent=2,
            sort_keys=True,
        ) + "\n"


@dataclass
class _Routine:
    kind: str
    name: str
    start: int
    header_end: int
    end: int
    body_start: int | None
    parent: "_Routine | None" = None
    object_id: str | None = None


def discover_sources(paths: Iterable[Path]) -> list[Path]:
    """Return configured PL/SQL files in a deterministic order."""
    sources: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix.lower() in SOURCE_EXTENSIONS:
                sources.append(path)
            continue
        if path.is_dir():
            sources.extend(
                child
                for child in path.rglob("*")
                if child.is_file()
                and child.suffix.lower() in SOURCE_EXTENSIONS
                and not any(part in IGNORED_DIRECTORIES for part in child.parts)
            )
    return sorted(set(sources), key=lambda item: item.as_posix())


def _line_at(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _mask_span(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] != "\n":
            chars[index] = " "


def _mask_non_code(source: str, source_file: str) -> tuple[str, list[Diagnostic]]:
    chars = list(source)
    diagnostics: list[Diagnostic] = []
    index = 0
    while index < len(source):
        if source.startswith("--", index):
            end = source.find("\n", index)
            end = len(source) if end == -1 else end
            _mask_span(chars, index, end)
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                diagnostics.append(
                    Diagnostic(
                        "UNTERMINATED_BLOCK_COMMENT",
                        "Block comment is not closed; remaining text was ignored.",
                        source_file,
                        _line_at(source, index),
                        _line_at(source, len(source)),
                    )
                )
                _mask_span(chars, index, len(source))
                break
            end += 2
            _mask_span(chars, index, end)
            index = end
        elif source[index] == "'":
            end = index + 1
            while end < len(source):
                if source[end] == "'" and end + 1 < len(source) and source[end + 1] == "'":
                    end += 2
                elif source[end] == "'":
                    end += 1
                    break
                else:
                    end += 1
            else:
                diagnostics.append(
                    Diagnostic(
                        "UNTERMINATED_STRING",
                        "String literal is not closed; remaining text was ignored.",
                        source_file,
                        _line_at(source, index),
                        _line_at(source, len(source)),
                    )
                )
            _mask_span(chars, index, end)
            index = end
        elif (
            source[index : index + 2].lower() == "q'"
            and index + 2 < len(source)
        ):
            opening = source[index + 2]
            closing = {"[": "]", "(": ")", "{": "}", "<": ">"}.get(opening, opening)
            marker = closing + "'"
            end = source.find(marker, index + 3)
            if end == -1:
                diagnostics.append(
                    Diagnostic(
                        "UNTERMINATED_Q_STRING",
                        "Alternative quoted string is not closed; remaining text was ignored.",
                        source_file,
                        _line_at(source, index),
                        _line_at(source, len(source)),
                    )
                )
                end = len(source)
            else:
                end += len(marker)
            _mask_span(chars, index, end)
            index = end
        else:
            index += 1
    return "".join(chars), diagnostics


def _find_matching_parenthesis(source: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_top_level(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _evidence(source: str, start: int, end: int) -> str:
    return source[start:end].strip()


def _find_routine_end(masked: str, header_end: int) -> int | None:
    body_start = re.search(r"\bBEGIN\b", masked[header_end:], re.IGNORECASE)
    if body_start is None:
        return None
    begin_offset = header_end + body_start.start()
    stack = ["BEGIN"]
    tokens = re.compile(r"\bBEGIN\b|\bIF\b|\bLOOP\b|\bCASE\b|\bEND\b", re.IGNORECASE)
    skip_until = begin_offset
    for token in tokens.finditer(masked, begin_offset):
        if token.start() < skip_until:
            continue
        word = token.group().upper()
        if token.start() == begin_offset:
            continue
        if word in {"BEGIN", "IF", "LOOP", "CASE"}:
            stack.append(word)
            continue
        following = re.match(
            rf"\s*(?:(?P<kind>IF|LOOP|CASE)|(?P<name>{IDENTIFIER}))?\s*;",
            masked[token.end() :],
            re.IGNORECASE,
        )
        if following is None:
            continue
        expected = following.group("kind")
        if expected is not None:
            if stack and stack[-1] == expected.upper():
                stack.pop()
        elif stack and stack[-1] == "BEGIN":
            stack.pop()
        skip_until = token.end() + following.end()
        if not stack:
            return skip_until
    return None


def _routine_candidates(
    source: str, masked: str, source_file: str, diagnostics: list[Diagnostic]
) -> list[_Routine]:
    pattern = re.compile(
        rf"(?im)^[ \t]*(?P<kind>PROCEDURE|FUNCTION)[ \t]+(?P<name>{IDENTIFIER})"
        r"(?P<header>.*?)(?P<terminator>\bIS\b|\bAS\b|;)",
        re.DOTALL,
    )
    candidates: list[_Routine] = []
    for match in pattern.finditer(masked):
        name = match.group("name")
        terminator = match.group("terminator").upper()
        if terminator == ";":
            continue
        end = _find_routine_end(masked, match.end())
        if end is None:
            diagnostics.append(
                Diagnostic(
                    "UNTERMINATED_ROUTINE",
                    f"{match.group('kind').title()} {name} has no named END delimiter.",
                    source_file,
                    _line_at(source, match.start()),
                    _line_at(source, match.end()),
                )
            )
            end = match.end()
        candidates.append(
            _Routine(
                kind=match.group("kind").upper(),
                name=name.upper(),
                start=match.start(),
                header_end=match.end(),
                end=end,
                body_start=match.end(),
            )
        )
    for routine in candidates:
        parents = [
            candidate
            for candidate in candidates
            if candidate is not routine
            and candidate.start < routine.start
            and routine.end <= candidate.end
        ]
        routine.parent = min(parents, key=lambda item: item.end - item.start, default=None)
    return sorted(candidates, key=lambda item: (item.start, -item.end))


def _parameters(
    source: str, masked: str, routine: _Routine, source_file: str
) -> list[Entity]:
    opening = masked.find("(", routine.start, routine.header_end)
    if opening == -1:
        return []
    closing = _find_matching_parenthesis(masked, opening)
    if closing is None or closing > routine.header_end:
        return []
    parameters: list[Entity] = []
    for position, fragment in enumerate(_split_top_level(masked[opening + 1 : closing])):
        match = re.match(
            rf"\s*(?P<name>{IDENTIFIER})\s+"
            r"(?P<mode>IN\s+OUT|IN|OUT)?\s*(?:NOCOPY\s+)?(?P<datatype>.+?)\s*$",
            fragment,
            re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            continue
        parameter_start = opening + 1 + sum(
            len(part) + 1 for part in _split_top_level(masked[opening + 1 : closing])[:position]
        )
        name = match.group("name").upper()
        parameters.append(
            Entity(
                f"{routine.object_id}.{name}",
                "PARAMETER",
                routine.object_id,
                source_file,
                _line_at(source, parameter_start),
                _line_at(source, parameter_start + len(fragment)),
                "resolved",
                datatype=_normalize(match.group("datatype")),
                mode=_normalize(match.group("mode") or "IN").upper(),
                evidence=_evidence(source, parameter_start, parameter_start + len(fragment)),
            )
        )
    return parameters


def _declaration_entities(
    source: str, masked: str, routine: _Routine, source_file: str
) -> list[Entity]:
    assert routine.body_start is not None
    begin_match = re.search(r"\bBEGIN\b", masked[routine.body_start : routine.end], re.IGNORECASE)
    if begin_match is None:
        return []
    start = routine.body_start
    end = start + begin_match.start()
    declarations = masked[start:end]
    entities: list[Entity] = []

    cursor_ranges: list[tuple[int, int]] = []
    for match in re.finditer(
        rf"\bCURSOR\s+(?P<name>{IDENTIFIER})(?:\s*\([^;]*?\))?\s+IS\b",
        declarations,
        re.IGNORECASE | re.DOTALL,
    ):
        statement_end = masked.find(";", start + match.end(), end)
        statement_end = end if statement_end == -1 else statement_end + 1
        cursor_ranges.append((start + match.start(), statement_end))
        name = match.group("name").upper()
        entities.append(
            Entity(
                f"{routine.object_id}.{name}",
                "CURSOR",
                routine.object_id,
                source_file,
                _line_at(source, start + match.start()),
                _line_at(source, statement_end),
                "resolved",
                evidence=_evidence(source, start + match.start(), statement_end),
            )
        )

    type_ranges: list[tuple[int, int]] = []
    for match in re.finditer(rf"\bTYPE\s+(?P<name>{IDENTIFIER})\s+IS\b", declarations, re.IGNORECASE):
        statement_end = masked.find(";", start + match.end(), end)
        statement_end = end if statement_end == -1 else statement_end + 1
        type_ranges.append((start + match.start(), statement_end))
        name = match.group("name").upper()
        entities.append(
            Entity(
                f"{routine.object_id}.{name}",
                "TYPE",
                routine.object_id,
                source_file,
                _line_at(source, start + match.start()),
                _line_at(source, statement_end),
                "resolved",
                evidence=_evidence(source, start + match.start(), statement_end),
            )
        )

    excluded_ranges = cursor_ranges + type_ranges
    statement_start = start
    for statement_end in range(start, end):
        if masked[statement_end] != ";":
            continue
        if any(range_start <= statement_start < range_end for range_start, range_end in excluded_ranges):
            statement_start = statement_end + 1
            continue
        statement = masked[statement_start:statement_end]
        match = re.match(
            rf"\s*(?P<names>{IDENTIFIER}(?:\s*,\s*{IDENTIFIER})*)"
            r"\s+(?:CONSTANT\s+)?(?P<datatype>[^:=]+?)(?:\s*:=.*)?\s*$",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if match and not re.search(r"\b(PRAGMA|SUBTYPE)\b", statement, re.IGNORECASE):
            for name in re.split(r"\s*,\s*", match.group("names")):
                entities.append(
                    Entity(
                        f"{routine.object_id}.{name.upper()}",
                        "VARIABLE",
                        routine.object_id,
                        source_file,
                        _line_at(source, statement_start),
                        _line_at(source, statement_end + 1),
                        "resolved",
                        datatype=_normalize(match.group("datatype")),
                        evidence=_evidence(source, statement_start, statement_end + 1),
                    )
                )
        statement_start = statement_end + 1
    return entities


def scan_source(source: str, source_file: str) -> Registry:
    """Scan one PL/SQL source text into Phase 1 registry entities."""
    masked, diagnostics = _mask_non_code(source, source_file)
    package_matches = list(
        re.finditer(
            rf"\bCREATE\s+OR\s+REPLACE\s+PACKAGE(?:\s+BODY)?\s+(?P<name>{IDENTIFIER})\b",
            masked,
            re.IGNORECASE,
        )
    )
    entities: list[Entity] = []
    package_id: str | None = None
    if not package_matches:
        diagnostics.append(
            Diagnostic(
                "PACKAGE_NOT_FOUND",
                "No CREATE OR REPLACE PACKAGE declaration was found.",
                source_file,
                1,
                _line_at(source, len(source)),
            )
        )
    else:
        first_package = package_matches[0]
        package_id = first_package.group("name").upper()
        entities.append(
            Entity(
                package_id,
                "PACKAGE",
                None,
                source_file,
                _line_at(source, first_package.start()),
                _line_at(source, len(source)),
                "resolved",
                evidence=_evidence(source, first_package.start(), first_package.end()),
            )
        )
        if len(package_matches) > 1:
            diagnostics.append(
                Diagnostic(
                    "MULTIPLE_PACKAGES",
                    "Only the first package is registered from a single source file.",
                    source_file,
                    _line_at(source, package_matches[1].start()),
                    _line_at(source, package_matches[-1].end()),
                )
            )

    routines = _routine_candidates(source, masked, source_file, diagnostics)
    name_counts: dict[tuple[str, str], int] = {}
    for routine in routines:
        parent_id = routine.parent.object_id if routine.parent else package_id
        if parent_id is None:
            parent_id = Path(source_file).stem.upper()
        key = (parent_id, routine.name)
        name_counts[key] = name_counts.get(key, 0) + 1
        suffix = "" if name_counts[key] == 1 else f"#{name_counts[key]}"
        routine.object_id = f"{parent_id}.{routine.name}{suffix}"
        header = masked[routine.start : routine.header_end]
        return_match = re.search(r"\bRETURN\s+(?P<datatype>.+?)(?:\bIS\b|\bAS\b)$", header, re.IGNORECASE | re.DOTALL)
        entities.append(
            Entity(
                routine.object_id,
                routine.kind,
                parent_id,
                source_file,
                _line_at(source, routine.start),
                _line_at(source, routine.end),
                "resolved",
                signature=_normalize(header),
                datatype=_normalize(return_match.group("datatype")) if return_match else None,
                evidence=_evidence(source, routine.start, routine.header_end),
            )
        )
        entities.extend(_parameters(source, masked, routine, source_file))
        entities.extend(_declaration_entities(source, masked, routine, source_file))

    return Registry(
        sorted(entities, key=lambda entity: entity.object_id),
        sorted(diagnostics, key=lambda diagnostic: (diagnostic.source_file, diagnostic.start_line, diagnostic.code)),
    )


def scan_files(paths: Iterable[Path]) -> Registry:
    """Scan configured PL/SQL paths and produce one stable registry."""
    entities: list[Entity] = []
    diagnostics: list[Diagnostic] = []
    for path in discover_sources(paths):
        source_file = path.as_posix()
        registry = scan_source(path.read_text(encoding="utf-8"), source_file)
        entities.extend(registry.entities)
        diagnostics.extend(registry.diagnostics)
    return Registry(
        sorted(entities, key=lambda entity: (entity.object_id, entity.source_file)),
        sorted(diagnostics, key=lambda diagnostic: (diagnostic.source_file, diagnostic.start_line, diagnostic.code)),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Phase 1 PL/SQL object registry.")
    parser.add_argument("paths", nargs="+", type=Path, help="PL/SQL source files or directories")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Registry JSON output path")
    args = parser.parse_args(argv)
    registry = scan_files(args.paths)
    args.output.write_text(registry.to_json(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
