"""Relationship contract and source context shared by the Phase 2/3 extractors."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from datalineague import _native
from datalineague.scanner import (
    Diagnostic,
    _line_at,
    _mask_non_code,
    _routine_candidates,
    discover_sources,
)

CONTRACT_VERSION = "1.0"
RELATION_TYPES = ("CALLS", "READS", "WRITES", "DERIVES_FROM", "REFERENCES")
STATUSES = ("resolved", "ambiguous", "dynamic_sql", "unsupported")
EVIDENCE_QUALITIES = ("source_exact", "source_derived")
DECLARED_TYPES = {"VARIABLE", "PARAMETER", "CURSOR", "TYPE"}
ROUTINE_TYPES = {"PROCEDURE", "FUNCTION"}
IDENTIFIER = r"[A-Za-z][A-Za-z0-9_$#]*"


@dataclass(frozen=True)
class Relation:
    relation_id: str
    relation: str
    source_id: str
    target_id: str | None
    source_file: str
    start_line: int
    end_line: int
    evidence: str
    resolution_status: str
    evidence_quality: str
    reason: str | None = None


@dataclass(frozen=True)
class ExternalEntity:
    object_id: str
    object_type: str
    qualified_name: str
    qualified: bool
    source_file: str
    start_line: int


def make_relation(
    relation: str,
    source_id: str,
    target_id: str | None,
    source_file: str,
    start_line: int,
    end_line: int,
    evidence: str,
    status: str,
    quality: str = "source_exact",
    reason: str | None = None,
) -> Relation:
    """Build a relation whose identity is a deterministic hash of its content."""
    if relation not in RELATION_TYPES or status not in STATUSES or quality not in EVIDENCE_QUALITIES:
        raise ValueError(f"invalid relation contract value: {relation}/{status}/{quality}")
    if status != "resolved" and not reason:
        raise ValueError("every non-resolved relation requires a reason")
    if status == "resolved" and target_id is None:
        raise ValueError("a resolved relation requires a target_id")
    key = "|".join([relation, source_id, target_id or "", source_file, str(start_line), str(end_line)])
    return Relation(
        hashlib.sha1(key.encode("utf-8")).hexdigest()[:16],
        relation,
        source_id,
        target_id,
        source_file,
        start_line,
        end_line,
        evidence,
        status,
        quality,
        reason,
    )


def blank_ranges(text: str, ranges: Iterable[tuple[int, int]], base: int) -> str:
    """Replace spans with spaces (keeping newlines) so offsets stay aligned."""
    characters = list(text)
    for start, end in ranges:
        for index in range(max(start - base, 0), min(end - base, len(characters))):
            if characters[index] != "\n":
                characters[index] = " "
    return "".join(characters)


@dataclass
class RoutineContext:
    object_id: str
    kind: str
    name: str
    parent_id: str
    start: int
    header_end: int
    end: int
    begin: int | None
    scope_chain: list[str]
    declared: set[str]
    children: list[tuple[int, int]] = field(default_factory=list)
    declaration_text: str = ""
    executable_text: str = ""


@dataclass
class RegistryIndex:
    packages: set[str]
    routines_by_parent: dict[tuple[str, str], list[str]]
    entities: dict[str, dict[str, Any]]
    declared_by_parent: dict[str, set[str]]

    @classmethod
    def build(cls, entities: list[dict[str, Any]]) -> "RegistryIndex":
        packages: set[str] = set()
        routines: dict[tuple[str, str], list[str]] = {}
        by_id: dict[str, dict[str, Any]] = {}
        declared: dict[str, set[str]] = {}
        for entity in entities:
            object_id = entity["object_id"]
            by_id.setdefault(object_id, entity)
            kind = entity["object_type"]
            parent = entity.get("parent_id")
            if kind == "PACKAGE":
                packages.add(object_id.upper())
            elif kind in ROUTINE_TYPES and parent:
                name = object_id[len(parent) + 1 :].split("#")[0].upper()
                routines.setdefault((parent, name), []).append(object_id)
            elif kind in DECLARED_TYPES and parent:
                declared.setdefault(parent, set()).add(object_id[len(parent) + 1 :].upper())
        for ids in routines.values():
            ids.sort()
        return cls(packages, routines, by_id, declared)

    def routine_candidates(self, parent_id: str, name: str) -> list[str]:
        return self.routines_by_parent.get((parent_id, name.upper()), [])


@dataclass
class SourceContext:
    source: str
    masked: str
    source_file: str
    line_index: Any
    package_id: str | None
    routines: list[RoutineContext]
    index: RegistryIndex
    diagnostics: list[dict[str, Any]]

    def line(self, offset: int) -> int:
        return _line_at(self.line_index, offset)


def _diagnostic(code: str, message: str, source_file: str, start: int, end: int) -> dict[str, Any]:
    return asdict(Diagnostic(code, message, source_file, start, end))


def build_context(source: str, source_file: str, index: RegistryIndex) -> SourceContext:
    """Re-derive routine extents from source and bind them to registry identities."""
    line_index = _native.LineIndex(source)
    masked, _ = _mask_non_code(source, source_file, line_index)
    scan_diagnostics: list[Diagnostic] = []
    candidates = _routine_candidates(source, masked, source_file, scan_diagnostics, line_index)
    package_match = re.search(
        rf"\bCREATE\s+OR\s+REPLACE\s+PACKAGE(?:\s+BODY)?\s+(?P<name>{IDENTIFIER})\b",
        masked,
        re.IGNORECASE,
    )
    package_id = package_match.group("name").upper() if package_match else None
    diagnostics: list[dict[str, Any]] = []
    name_counts: dict[tuple[str, str], int] = {}
    contexts: dict[int, RoutineContext] = {}
    ordered: list[RoutineContext] = []
    for position, routine in enumerate(candidates):
        parent_id = routine.parent.object_id if routine.parent else package_id
        if parent_id is None:
            parent_id = Path(source_file).stem.upper()
        key = (parent_id, routine.name)
        name_counts[key] = name_counts.get(key, 0) + 1
        suffix = "" if name_counts[key] == 1 else f"#{name_counts[key]}"
        routine.object_id = f"{parent_id}.{routine.name}{suffix}"
        entity = index.entities.get(routine.object_id)
        start_line = _line_at(line_index, routine.start)
        end_line = _line_at(line_index, routine.end)
        if (
            entity is None
            or entity.get("source_file") != source_file
            or entity.get("start_line") != start_line
            or entity.get("end_line") != end_line
        ):
            diagnostics.append(
                _diagnostic(
                    "ROUTINE_REGISTRY_MISMATCH",
                    f"{routine.object_id} was not found in the registry with the same source range; "
                    "relationships for it were skipped. Regenerate the registry for this source.",
                    source_file,
                    start_line,
                    end_line,
                )
            )
            continue
        chain = [routine.object_id]
        current = routine.parent
        while current is not None:
            chain.append(current.object_id)
            current = current.parent
        if package_id and chain[-1] != package_id:
            chain.append(package_id)
        context = RoutineContext(
            routine.object_id,
            routine.kind,
            routine.name,
            parent_id,
            routine.start,
            routine.header_end,
            routine.end,
            None,
            chain,
            set(),
        )
        contexts[position] = context
        ordered.append(context)

    by_id = {context.object_id: context for context in ordered}
    for context in ordered:
        parent = by_id.get(context.parent_id)
        if parent is not None:
            parent.children.append((context.start, context.end))
    for context in ordered:
        for scope_id in context.scope_chain:
            context.declared |= index.declared_by_parent.get(scope_id, set())
        context.begin = _find_begin(masked, context)
        if context.begin is None:
            diagnostics.append(
                _diagnostic(
                    "ROUTINE_BODY_NOT_FOUND",
                    f"No executable BEGIN was found inside the registered range of {context.object_id}; "
                    "a nested routine declaration can truncate the Phase 1 extent. "
                    "Relationships in this routine's own body were not extracted.",
                    source_file,
                    _line_at(line_index, context.start),
                    _line_at(line_index, context.end),
                )
            )
        else:
            declaration = masked[context.header_end : context.begin]
            executable = masked[context.begin : context.end]
            context.declaration_text = blank_ranges(declaration, context.children, context.header_end)
            context.executable_text = blank_ranges(executable, context.children, context.begin)
            for match in re.finditer(rf"\bFOR\s+({IDENTIFIER})\s+IN\b", context.executable_text, re.IGNORECASE):
                context.declared.add(match.group(1).upper())
    return SourceContext(source, masked, source_file, line_index, package_id, ordered, index, diagnostics)


def _find_begin(masked: str, context: RoutineContext) -> int | None:
    for match in re.finditer(r"\bBEGIN\b", masked[context.header_end : context.end], re.IGNORECASE):
        offset = context.header_end + match.start()
        if not any(start <= offset < end for start, end in context.children):
            return offset
    return None


def relate_registry(
    sources: Iterable[Path],
    registry: dict[str, Any],
    extractors: Iterable[Any],
) -> dict[str, Any]:
    """Return the registry with relationship-contract fields added."""
    entities = registry.get("entities", [])
    index = RegistryIndex.build(entities)
    registered_files = {entity["source_file"] for entity in entities}
    relations: dict[str, Relation] = {}
    externals: dict[str, ExternalEntity] = {}
    diagnostics: list[dict[str, Any]] = []
    for path in discover_sources(sources):
        source_file = path.as_posix()
        if source_file not in registered_files:
            diagnostics.append(
                _diagnostic(
                    "SOURCE_NOT_IN_REGISTRY",
                    "The registry has no entities for this source file; scan it before running relate.",
                    source_file,
                    1,
                    1,
                )
            )
            continue
        context = build_context(path.read_text(encoding="utf-8"), source_file, index)
        diagnostics.extend(context.diagnostics)
        for extractor in extractors:
            found_relations, found_externals, found_diagnostics = extractor(context)
            for relation in found_relations:
                relations.setdefault(relation.relation_id, relation)
            for external in found_externals:
                current = externals.get(external.object_id)
                if current is None or (external.source_file, external.start_line) < (
                    current.source_file,
                    current.start_line,
                ):
                    externals[external.object_id] = external
            diagnostics.extend(found_diagnostics)
    result = dict(registry)
    result["relationship_contract"] = CONTRACT_VERSION
    result["relations"] = [
        asdict(relation)
        for relation in sorted(
            relations.values(),
            key=lambda item: (
                item.source_file,
                item.start_line,
                item.relation,
                item.source_id,
                item.target_id or "",
                item.relation_id,
            ),
        )
    ]
    result["external_entities"] = [
        asdict(external) for external in sorted(externals.values(), key=lambda item: item.object_id)
    ]
    result["relation_diagnostics"] = sorted(
        diagnostics, key=lambda item: (item["source_file"], item["start_line"], item["code"])
    )
    return result


def dump_registry(registry: dict[str, Any]) -> str:
    return json.dumps(registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
