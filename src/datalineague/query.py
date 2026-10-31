"""Phase 5 (query only): narrow, evidence-backed queries over the relationship index."""

from __future__ import annotations

import re
from collections import deque
from typing import Any

COMMANDS = ("inventory", "calls", "callers", "reads", "writes", "unresolved", "brief")


class QueryError(ValueError):
    pass


class Index:
    def __init__(self, registry: dict[str, Any]) -> None:
        if "relations" not in registry:
            raise QueryError("The registry has no relations; run `datalineague relate` first.")
        self.entities: list[dict[str, Any]] = registry["entities"]
        self.relations: list[dict[str, Any]] = registry["relations"]
        self.by_id = {entity["object_id"]: entity for entity in self.entities}

    def resolve_target(self, target: str) -> str:
        """Accept a full object id or an unambiguous routine name."""
        upper = target.upper()
        if upper in self.by_id:
            return upper
        matches = sorted(
            object_id
            for object_id, entity in self.by_id.items()
            if entity["object_type"] in {"PROCEDURE", "FUNCTION"}
            and object_id.split(".")[-1].split("#")[0] == upper
        )
        if not matches:
            raise QueryError(f"Target {target} was not found in the registry.")
        if len(matches) > 1:
            raise QueryError(f"Target {target} is ambiguous: {', '.join(matches)}")
        return matches[0]

    def edges(self, relation: str, status: str | None = "resolved") -> list[dict[str, Any]]:
        return [
            item
            for item in self.relations
            if item["relation"] == relation and (status is None or item["resolution_status"] == status)
        ]


def _row(relation: dict[str, Any], depth: int, via: list[str]) -> dict[str, Any]:
    return {**relation, "depth": depth, "via": via}


def _closure(index: Index, start: str, upstream: bool, transitive: bool) -> list[tuple[dict[str, Any], int, list[str]]]:
    """Breadth-first walk over resolved CALLS edges, safe for cycles."""
    results: list[tuple[dict[str, Any], int, list[str]]] = []
    calls = index.edges("CALLS")
    seen = {start}
    queue: deque[tuple[str, int, list[str]]] = deque([(start, 1, [start])])
    while queue:
        current, depth, path = queue.popleft()
        step = sorted(
            (
                item
                for item in calls
                if (item["target_id"] if upstream else item["source_id"]) == current
            ),
            key=lambda item: (item["source_file"], item["start_line"], item["relation_id"]),
        )
        for item in step:
            nxt = item["source_id"] if upstream else item["target_id"]
            results.append((item, depth, path))
            if transitive and nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, depth + 1, path + [nxt]))
    return results


def run_query(
    registry: dict[str, Any], command: str, target: str | None, transitive: bool = False
) -> list[dict[str, Any]]:
    index = Index(registry)
    if command not in COMMANDS:
        raise QueryError(f"Unknown query command: {command}")
    if target is None and command != "unresolved":
        raise QueryError(f"The {command} command requires a target.")
    resolved_target = index.resolve_target(target) if target else None

    if command == "inventory":
        prefix = resolved_target + "."
        return [
            {key: entity.get(key) for key in ("object_id", "object_type", "parent_id", "signature", "datatype", "mode", "start_line", "end_line")}
            for entity in index.entities
            if entity["object_id"] == resolved_target or entity["object_id"].startswith(prefix)
        ]
    if command in {"calls", "callers"}:
        return [
            _row(item, depth, path)
            for item, depth, path in _closure(index, resolved_target, command == "callers", transitive)
        ]
    if command in {"reads", "writes"}:
        relation = command.upper()
        rows: list[dict[str, Any]] = []
        scopes: list[tuple[str, int, list[str]]] = [(resolved_target, 0, [resolved_target])]
        if transitive:
            scopes += [(item["target_id"], depth, path + [item["target_id"]]) for item, depth, path in _closure(index, resolved_target, False, True)]
        seen_scopes: set[str] = set()
        for scope_id, depth, path in scopes:
            if scope_id in seen_scopes:
                continue
            seen_scopes.add(scope_id)
            rows.extend(
                _row(item, depth, path)
                for item in index.relations
                if item["relation"] == relation and item["source_id"] == scope_id and item["resolution_status"] == "resolved"
            )
        return rows
    if command == "unresolved":
        scopes_ids = {resolved_target} if resolved_target else None
        if transitive and resolved_target:
            scopes_ids |= {item["target_id"] for item, _, _ in _closure(index, resolved_target, False, True)}
        return [
            _row(item, 0, [])
            for item in index.relations
            if item["resolution_status"] != "resolved" and (scopes_ids is None or item["source_id"] in scopes_ids)
        ]
    return []


def _short(object_id: str) -> str:
    return object_id.split(".", 1)[-1] if "." in object_id else object_id


def _label_for(object_id: str) -> str:
    if object_id.startswith("TABLE:"):
        return f"[Table] {object_id[len('TABLE:'):]}"
    return f"{_short(object_id).split('#')[0]}()"


def brief_mermaid(registry: dict[str, Any], target: str) -> str:
    """Compact dependency chart from resolved, non-builtin edges only."""
    index = Index(registry)
    target_id = index.resolve_target(target)
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []

    def node(object_id: str) -> str:
        if object_id not in nodes:
            nodes[object_id] = "target" if object_id == target_id else f"n{len(nodes) + 1}"
        return nodes[object_id]

    callers = [item for item, _, _ in _closure(index, target_id, True, False)]
    callees = [item for item, _, _ in _closure(index, target_id, False, False)]
    reads = [item for item in index.edges("READS") if item["source_id"] == target_id]
    writes = [item for item in index.edges("WRITES") if item["source_id"] == target_id]
    for item in callers:
        edges.append((node(item["source_id"]), node(target_id)))
    for item in reads:
        edges.append((node(item["target_id"]), node(target_id)))
    node(target_id)
    for item in callees:
        callee = node(item["target_id"])
        edges.append((node(target_id), callee))
        for read in index.edges("READS"):
            if read["source_id"] == item["target_id"]:
                edges.append((node(read["target_id"]), callee))
        for write in index.edges("WRITES"):
            if write["source_id"] == item["target_id"]:
                edges.append((callee, node(write["target_id"])))
    for item in writes:
        edges.append((node(target_id), node(item["target_id"])))
    lines = ["```mermaid", "flowchart TD"]
    for object_id, node_id in nodes.items():
        lines.append(f'  {node_id}["{_label_for(object_id)}"]')
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if edge not in seen:
            seen.add(edge)
            lines.append(f"  {edge[0]} --> {edge[1]}")
    lines.append("  style target fill:lightgreen")
    lines.append("```")
    return "\n".join(lines) + "\n"


def _cell(value: Any) -> str:
    text = "" if value is None else re.sub(r"\s+", " ", str(value))
    return text.replace("|", "\\|")[:80]


def render_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No matching relationships.\n"
    if "relation" not in rows[0]:
        header = ["object_id", "object_type", "start_line", "end_line"]
        lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        lines += ["| " + " | ".join(_cell(row.get(key)) for key in header) + " |" for row in rows]
        return "\n".join(lines) + "\n"
    show_via = any(len(row.get("via") or []) > 1 for row in rows)
    header = ["relation", "source_id", "target_id", "status", "lines", "reason", "evidence"]
    if show_via:
        header.insert(3, "via")
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        values = [
            row["relation"],
            row["source_id"],
            row["target_id"],
            row["resolution_status"],
            f"{row['source_file']}:{row['start_line']}-{row['end_line']}",
            row.get("reason"),
            row["evidence"],
        ]
        if show_via:
            values.insert(3, " > ".join(row.get("via") or []))
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    return "\n".join(lines) + "\n"
