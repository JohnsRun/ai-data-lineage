from __future__ import annotations

import unittest

from datalineague.query import QueryError, brief_mermaid, render_markdown, run_query
from tests.support import RelationTestCase

SOURCE = """create or replace package body pkg_q as

procedure a is
begin
  b;
  dbms_output.put_line ('x');
  unknown_x (1);
end a;

procedure b is
  v number;
begin
  select max(x) into v from t_b;
  c;
end b;

procedure c is
begin
  insert into t_w (c1) values (1);
  update t_c set z = 1;
  a;
end c;

procedure dup (p in number) is
begin
  null;
end dup;

procedure dup (p in varchar2) is
begin
  null;
end dup;

end pkg_q;
/
"""


class QueryTests(RelationTestCase):
    def setUp(self) -> None:
        self.registry = self.relate(SOURCE)

    def targets(self, rows: list[dict], key: str = "target_id") -> list[str | None]:
        return [row[key] for row in rows]

    def test_direct_and_transitive_calls_are_cycle_safe(self) -> None:
        direct = run_query(self.registry, "calls", "a")
        self.assertEqual(self.targets(direct), ["PKG_Q.B"])
        deep = run_query(self.registry, "calls", "a", transitive=True)
        self.assertEqual(
            [(row["target_id"], row["depth"]) for row in deep],
            [("PKG_Q.B", 1), ("PKG_Q.C", 2), ("PKG_Q.A", 3)],
        )

    def test_callers_walk_upstream(self) -> None:
        direct = run_query(self.registry, "callers", "c")
        self.assertEqual(self.targets(direct, "source_id"), ["PKG_Q.B"])
        deep = run_query(self.registry, "callers", "c", transitive=True)
        self.assertEqual({row["source_id"] for row in deep}, {"PKG_Q.B", "PKG_Q.A", "PKG_Q.C"})

    def test_transitive_reads_and_writes_report_the_via_path(self) -> None:
        self.assertEqual(run_query(self.registry, "reads", "a"), [])
        rows = run_query(self.registry, "reads", "a", transitive=True)
        self.assertEqual([(row["target_id"], row["via"]) for row in rows], [("TABLE:T_B", ["PKG_Q.A", "PKG_Q.B"])])
        writes = run_query(self.registry, "writes", "a", transitive=True)
        self.assertEqual(
            {(row["target_id"], tuple(row["via"])) for row in writes},
            {("TABLE:T_W", ("PKG_Q.A", "PKG_Q.B", "PKG_Q.C")), ("TABLE:T_C", ("PKG_Q.A", "PKG_Q.B", "PKG_Q.C"))},
        )

    def test_unresolved_lists_only_non_resolved_edges(self) -> None:
        rows = run_query(self.registry, "unresolved", "a")
        self.assertEqual({row["reason"] for row in rows}, {"EXTERNAL_BUILTIN_PACKAGE", "UNRESOLVED_CALL_TARGET"})
        self.assertTrue(all(row["resolution_status"] != "resolved" for row in rows))

    def test_inventory_lists_routine_and_children(self) -> None:
        ids = {row["object_id"] for row in run_query(self.registry, "inventory", "b")}
        self.assertEqual(ids, {"PKG_Q.B", "PKG_Q.B.V"})

    def test_unknown_and_ambiguous_targets_are_errors(self) -> None:
        with self.assertRaises(QueryError):
            run_query(self.registry, "calls", "missing")
        with self.assertRaises(QueryError):
            run_query(self.registry, "calls", "dup")
        with self.assertRaises(QueryError):
            run_query({"entities": []}, "calls", "a")

    def test_brief_diagram_uses_only_resolved_edges_and_simple_syntax(self) -> None:
        text = brief_mermaid(self.registry, "a")
        self.assertIn('["B()"]', text)
        self.assertIn('["[Table] T_B"]', text)
        self.assertIn("style target fill:lightgreen", text)
        self.assertNotIn("DBMS_OUTPUT", text)
        self.assertNotIn("UNKNOWN_X", text)
        self.assertNotIn("-->|", text)
        self.assertNotIn("#", text)

    def test_markdown_renders_evidence_and_status(self) -> None:
        text = render_markdown(run_query(self.registry, "unresolved", "a"))
        self.assertIn("unknown_x (1)", text)
        self.assertIn("unsupported", text)
        self.assertEqual(render_markdown([]), "No matching relationships.\n")

    def test_markdown_shows_via_path_for_transitive_rows(self) -> None:
        text = render_markdown(run_query(self.registry, "reads", "a", transitive=True))
        self.assertIn("| via |", text)
        self.assertIn("PKG_Q.A > PKG_Q.B", text)


if __name__ == "__main__":
    unittest.main()
