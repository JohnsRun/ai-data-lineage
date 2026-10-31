"""Shared helpers: build a Phase 1 registry from text and relate it."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from datalineague import scanner
from datalineague.calls import extract_calls
from datalineague.relations import relate_registry
from datalineague.sql_lineage import extract_sql

ROOT = Path(__file__).resolve().parents[1]


def relate_text(source: str, name: str = "fixture.pkb") -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (phase1_registry, related_registry) for one source text."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_text(source, encoding="utf-8")
        phase1 = json.loads(scanner.scan_files([path]).to_json())
        related = relate_registry([path], json.loads(json.dumps(phase1)), [extract_calls, extract_sql])
    return phase1, related


class RelationTestCase(unittest.TestCase):
    def relate(self, source: str) -> dict[str, Any]:
        return relate_text(source)[1]

    @staticmethod
    def edges(registry: dict[str, Any], relation: str, source_suffix: str | None = None) -> list[dict[str, Any]]:
        return [
            item
            for item in registry["relations"]
            if item["relation"] == relation
            and (source_suffix is None or item["source_id"].endswith(source_suffix))
        ]

    @staticmethod
    def pairs(edges: list[dict[str, Any]]) -> set[tuple[str | None, str]]:
        return {(item["target_id"], item["resolution_status"]) for item in edges}
