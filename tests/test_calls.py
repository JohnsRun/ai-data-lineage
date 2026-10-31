from __future__ import annotations

import unittest

from tests.support import RelationTestCase

PACKAGE = """create or replace package body pkg_a as

procedure helper (p in number) is
begin
  null;
end helper;

procedure helper (p in varchar2) is
begin
  null;
end helper;

procedure other is
begin
  null;
end other;

procedure wrapper is
  procedure inner_proc is
  begin
    other;
  end inner_proc;
begin
  inner_proc;
end wrapper;

procedure outer_proc is
  l_text varchar2(100);
  type t_tab is table of number index by binary_integer;
  l_tab t_tab;
begin
  -- other_commented (1);
  l_text := 'ghost_call (1)';
  helper (1);
  other;
  pkg_a.other ();
  l_tab(1) := 2;
  l_text := upper (l_text);
  dbms_output.put_line (l_text);
  vendor_pkg.do_work (1);
  unknown_proc (1);
  for i in 1 .. l_tab.count loop
    l_text := l_tab (i);
  end loop;
end outer_proc;

end pkg_a;
/
"""


class CallExtractionTests(RelationTestCase):
    def setUp(self) -> None:
        self.registry = self.relate(PACKAGE)
        self.calls = self.edges(self.registry, "CALLS", "OUTER_PROC")

    def targets(self, status: str) -> set[str | None]:
        return {item["target_id"] for item in self.calls if item["resolution_status"] == status}

    def test_local_and_package_qualified_calls_resolve(self) -> None:
        self.assertIn("PKG_A.OTHER", self.targets("resolved"))
        qualified = [item for item in self.calls if item["evidence"].startswith("pkg_a.other")]
        self.assertEqual(qualified[0]["target_id"], "PKG_A.OTHER")
        self.assertEqual(qualified[0]["resolution_status"], "resolved")

    def test_nested_routine_call_resolves_through_enclosing_scopes(self) -> None:
        nested = self.edges(self.registry, "CALLS", "WRAPPER.INNER_PROC")
        self.assertEqual(self.pairs(nested), {("PKG_A.OTHER", "resolved")})

    def test_truncated_outer_routine_extent_is_disclosed(self) -> None:
        codes = {(item["code"], item["start_line"]) for item in self.registry["relation_diagnostics"]}
        self.assertIn(("ROUTINE_BODY_NOT_FOUND", 18), codes)

    def test_overloaded_name_is_ambiguous_not_resolved(self) -> None:
        overload = [item for item in self.calls if item["evidence"].startswith("helper")]
        self.assertEqual(len(overload), 1)
        self.assertEqual(overload[0]["resolution_status"], "ambiguous")
        self.assertIsNone(overload[0]["target_id"])
        self.assertEqual(overload[0]["reason"], "AMBIGUOUS_OVERLOAD")

    def test_external_routines_are_unsupported_with_reason(self) -> None:
        by_target = {item["target_id"]: item for item in self.calls}
        self.assertEqual(by_target["EXTERNAL:DBMS_OUTPUT.PUT_LINE"]["reason"], "EXTERNAL_BUILTIN_PACKAGE")
        self.assertEqual(by_target["EXTERNAL:VENDOR_PKG.DO_WORK"]["reason"], "EXTERNAL_ROUTINE_UNAVAILABLE")
        self.assertEqual(by_target["EXTERNAL:VENDOR_PKG.DO_WORK"]["resolution_status"], "unsupported")
        names = {item["object_id"] for item in self.registry["external_entities"]}
        self.assertIn("EXTERNAL:VENDOR_PKG.DO_WORK", names)

    def test_unresolved_single_name_has_no_target(self) -> None:
        unknown = [item for item in self.calls if item["evidence"].startswith("unknown_proc")]
        self.assertEqual(unknown[0]["reason"], "UNRESOLVED_CALL_TARGET")
        self.assertIsNone(unknown[0]["target_id"])

    def test_non_calls_are_not_reported(self) -> None:
        evidence = " ".join(item["evidence"] for item in self.calls)
        self.assertNotIn("other_commented", evidence)  # comment
        self.assertNotIn("ghost_call", evidence)  # string literal
        self.assertNotIn("l_tab", evidence)  # collection indexing / attribute
        self.assertNotIn("upper", evidence)  # SQL built-in function

    def test_sql_table_with_column_list_is_not_a_call(self) -> None:
        registry = self.relate(
            "create or replace package body p as\n"
            "procedure q is\nbegin\n  insert into t2 (c1) values (1);\nend q;\n"
            "end p;\n/\n"
        )
        self.assertEqual(self.edges(registry, "CALLS"), [])

    def test_confirmed_edges_carry_contract_fields(self) -> None:
        for item in self.registry["relations"]:
            self.assertTrue(item["evidence"])
            self.assertGreaterEqual(item["end_line"], item["start_line"])
            self.assertIn(item["evidence_quality"], {"source_exact", "source_derived"})
            if item["resolution_status"] == "resolved":
                self.assertIsNotNone(item["target_id"])
            else:
                self.assertTrue(item["reason"])


if __name__ == "__main__":
    unittest.main()
