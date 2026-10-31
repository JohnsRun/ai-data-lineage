from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from datalineague import main, scanner
from datalineague.calls import extract_calls
from datalineague.relations import dump_registry, relate_registry
from datalineague.sql_lineage import extract_sql
from tests.support import ROOT

SOAP = ROOT / "testcases" / "plsql" / "soap_server_pkg.pkb"
PKG = "SOAP_SERVER_PKG."


class SoapServerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.phase1 = json.loads(scanner.scan_files([SOAP]).to_json())
        cls.related = relate_registry([SOAP], json.loads(json.dumps(cls.phase1)), [extract_calls, extract_sql])

    def edges(self, relation: str, source: str) -> list[dict]:
        return [
            item
            for item in self.related["relations"]
            if item["relation"] == relation and item["source_id"] == PKG + source
        ]

    def test_phase1_entities_and_diagnostics_are_unchanged(self) -> None:
        self.assertEqual(self.related["entities"], self.phase1["entities"])
        self.assertEqual(self.related["diagnostics"], self.phase1["diagnostics"])
        self.assertEqual(self.related["relationship_contract"], "1.0")

    def test_parse_request_internal_calls_resolve(self) -> None:
        resolved = {
            item["target_id"]
            for item in self.edges("CALLS", "PARSE_REQUEST")
            if item["resolution_status"] == "resolved"
        }
        self.assertEqual(
            resolved,
            {PKG + name for name in ("RAISE_ERROR", "IS_WHITELISTED", "PRETTY_STR", "GET_ARGUMENTS")},
        )

    def test_parse_request_external_calls_are_unsupported(self) -> None:
        external = {
            item["target_id"].split(":", 1)[1].split(".")[0]
            for item in self.edges("CALLS", "PARSE_REQUEST")
            if item["resolution_status"] == "unsupported"
        }
        self.assertEqual(external, {"APEX_UTIL", "DBMS_ASSERT", "DBMS_XMLPARSER", "DBMS_XMLDOM"})
        self.assertTrue(
            all(item["reason"] == "EXTERNAL_BUILTIN_PACKAGE" for item in self.edges("CALLS", "PARSE_REQUEST") if item["resolution_status"] == "unsupported")
        )

    def test_handle_request_calls_parse_request(self) -> None:
        targets = {item["target_id"] for item in self.edges("CALLS", "HANDLE_REQUEST") if item["resolution_status"] == "resolved"}
        self.assertIn(PKG + "PARSE_REQUEST", targets)
        self.assertIn(PKG + "EXECUTE_REQUEST", targets)

    def test_user_arguments_is_read_by_metadata_helpers(self) -> None:
        for routine in ("GET_ARGUMENTS", "GET_PROGRAMS"):
            reads = {(item["target_id"], item["resolution_status"]) for item in self.edges("READS", routine)}
            self.assertEqual(reads, {("TABLE:USER_ARGUMENTS", "resolved")})
        self.assertEqual(self.edges("READS", "PARSE_REQUEST"), [])

    def test_execute_request_dynamic_sql_is_flagged(self) -> None:
        dynamic = self.edges("REFERENCES", "EXECUTE_REQUEST")
        self.assertEqual([item["resolution_status"] for item in dynamic], ["dynamic_sql"])
        self.assertEqual(dynamic[0]["reason"], "DYNAMIC_SQL_DBMS_SQL")

    def test_every_relation_has_evidence_and_valid_status(self) -> None:
        ids = set()
        for item in self.related["relations"]:
            self.assertTrue(item["evidence"])
            self.assertIn(item["resolution_status"], {"resolved", "ambiguous", "dynamic_sql", "unsupported"})
            self.assertEqual(item["resolution_status"] == "resolved", item["target_id"] is not None and item["reason"] is None)
            ids.add(item["relation_id"])
        self.assertEqual(len(ids), len(self.related["relations"]))

    def test_output_is_deterministic(self) -> None:
        again = relate_registry([SOAP], json.loads(json.dumps(self.phase1)), [extract_calls, extract_sql])
        self.assertEqual(dump_registry(self.related), dump_registry(again))

    def test_cli_scan_relate_query_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / "registry.json"
            self.assertEqual(main([str(SOAP), "-o", str(registry)]), 0)
            self.assertNotIn("relations", json.loads(registry.read_text(encoding="utf-8")))
            self.assertEqual(main(["relate", str(SOAP), "--registry", str(registry)]), 0)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["query", str(registry), "reads", "parse_request", "--transitive"])
            self.assertEqual(code, 0)
            rows = json.loads(output.getvalue())
            self.assertEqual([row["target_id"] for row in rows], ["TABLE:USER_ARGUMENTS"])
            self.assertEqual(rows[0]["via"], [PKG + "PARSE_REQUEST", PKG + "GET_ARGUMENTS"])
            brief = io.StringIO()
            with contextlib.redirect_stdout(brief):
                self.assertEqual(main(["query", str(registry), "brief", "parse_request"]), 0)
            self.assertIn('["HANDLE_REQUEST()"]', brief.getvalue())
            self.assertIn('["[Table] USER_ARGUMENTS"]', brief.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["query", str(registry), "calls", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
