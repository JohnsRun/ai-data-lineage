"""Deterministic Phase 1 PL/SQL object-registry scanner."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from datalineague import _native


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


def _line_at(line_index: _native.LineIndex, offset: int) -> int:
    return line_index.line_at(offset)


def _mask_non_code(
    source: str, source_file: str, line_index: _native.LineIndex
) -> tuple[str, list[Diagnostic]]:
    masked, raw_diagnostics = _native.mask_non_code(source)
    diagnostics = [
        Diagnostic(
            code,
            message,
            source_file,
            _line_at(line_index, start),
            _line_at(line_index, end),
        )
        for code, message, start, end in raw_diagnostics
    ]
    return masked, diagnostics


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


def _routine_candidates(
    source: str,
    masked: str,
    source_file: str,
    diagnostics: list[Diagnostic],
    line_index: _native.LineIndex,
) -> list[_Routine]:
    raw_candidates, raw_diagnostics = _native.routine_candidates(masked)
    diagnostics.extend(
        Diagnostic(
            code,
            message,
            source_file,
            _line_at(line_index, start),
            _line_at(line_index, end),
        )
        for code, message, start, end in raw_diagnostics
    )
    candidates = [
        _Routine(
            kind,
            name,
            start,
            header_end,
            end,
            header_end,
        )
        for kind, name, start, header_end, end, _ in raw_candidates
    ]
    for index, candidate in enumerate(raw_candidates):
        parent_index = candidate[5]
        if parent_index is not None:
            candidates[index].parent = candidates[parent_index]
    return candidates


def _parameters(
    source: str,
    masked: str,
    routine: _Routine,
    source_file: str,
    line_index: _native.LineIndex,
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
                _line_at(line_index, parameter_start),
                _line_at(line_index, parameter_start + len(fragment)),
                "resolved",
                datatype=_normalize(match.group("datatype")),
                mode=_normalize(match.group("mode") or "IN").upper(),
                evidence=_evidence(source, parameter_start, parameter_start + len(fragment)),
            )
        )
    return parameters


def _declaration_entities(
    source: str,
    masked: str,
    routine: _Routine,
    source_file: str,
    line_index: _native.LineIndex,
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
                _line_at(line_index, start + match.start()),
                _line_at(line_index, statement_end),
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
                _line_at(line_index, start + match.start()),
                _line_at(line_index, statement_end),
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
                        _line_at(line_index, statement_start),
                        _line_at(line_index, statement_end + 1),
                        "resolved",
                        datatype=_normalize(match.group("datatype")),
                        evidence=_evidence(source, statement_start, statement_end + 1),
                    )
                )
        statement_start = statement_end + 1
    return entities


def scan_source(source: str, source_file: str) -> Registry:
    """Scan one PL/SQL source text into Phase 1 registry entities."""
    line_index = _native.LineIndex(source)
    masked, diagnostics = _mask_non_code(source, source_file, line_index)
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
                _line_at(line_index, len(source)),
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
                _line_at(line_index, first_package.start()),
                _line_at(line_index, len(source)),
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
                    _line_at(line_index, package_matches[1].start()),
                    _line_at(line_index, package_matches[-1].end()),
                )
            )

    routines = _routine_candidates(
        source, masked, source_file, diagnostics, line_index
    )
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
                _line_at(line_index, routine.start),
                _line_at(line_index, routine.end),
                "resolved",
                signature=_normalize(header),
                datatype=_normalize(return_match.group("datatype")) if return_match else None,
                evidence=_evidence(source, routine.start, routine.header_end),
            )
        )
        entities.extend(_parameters(source, masked, routine, source_file, line_index))
        entities.extend(
            _declaration_entities(source, masked, routine, source_file, line_index)
        )

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
