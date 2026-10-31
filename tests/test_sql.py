from __future__ import annotations

import unittest

from tests.support import RelationTestCase


def package(body: str, declarations: str = "") -> str:
    return (
        "create or replace package body p as\n"
        f"procedure q (p_id in number) is\n  v number;\n{declarations}begin\n{body}\nend q;\n"
        "end p;\n/\n"
    )


class StaticSqlTests(RelationTestCase):
    def test_select_into_reads_tables_and_ignores_variable_and_dual(self) -> None:
        registry = self.relate(
            package("  select max(x) into v from hr.t1 a join t2 b on a.k = b.k where a.id = p_id;\n  select 1 into v from dual;")
        )
        self.assertEqual(
            self.pairs(self.edges(registry, "READS")),
            {("TABLE:HR.T1", "resolved"), ("TABLE:T2", "resolved")},
        )
        self.assertEqual(self.edges(registry, "WRITES"), [])
        qualified = {e["object_id"]: e["qualified"] for e in registry["external_entities"] if e["object_type"] == "TABLE"}
        self.assertEqual(qualified, {"TABLE:HR.T1": True, "TABLE:T2": False})

    def test_dml_forms_split_reads_and_writes(self) -> None:
        registry = self.relate(
            package(
                "  insert into t2 (c1) select a from s;\n"
                "  update t3 set a = 1 where id in (select id from r);\n"
                "  delete from t4 where id in (select id from u);\n"
                "  merge into t5 using t6 on (t5.id = t6.id) when matched then update set t5.a = t6.a;"
            )
        )
        writes = {item["target_id"] for item in self.edges(registry, "WRITES")}
        reads = {item["target_id"] for item in self.edges(registry, "READS")}
        self.assertEqual(writes, {"TABLE:T2", "TABLE:T3", "TABLE:T4", "TABLE:T5"})
        self.assertEqual(reads, {"TABLE:S", "TABLE:R", "TABLE:U", "TABLE:T6"})

    def test_cte_names_are_not_tables(self) -> None:
        registry = self.relate(
            package("  for r in (with c as (select 1 x from base) select x from c) loop null; end loop;")
        )
        self.assertEqual(self.pairs(self.edges(registry, "READS")), {("TABLE:BASE", "resolved")})

    def test_cursor_declaration_and_returning_clause(self) -> None:
        registry = self.relate(
            package(
                "  insert into t11 (a) values (p_id) returning a into v;",
                "  cursor c is select a.x from s a;\n",
            )
        )
        self.assertEqual(self.pairs(self.edges(registry, "READS")), {("TABLE:S", "resolved")})
        self.assertEqual(self.pairs(self.edges(registry, "WRITES")), {("TABLE:T11", "resolved")})

    def test_sql_in_comments_and_strings_is_ignored(self) -> None:
        registry = self.relate(package("  -- select 1 from ghost;\n  v := length('select 1 from ghost2;');"))
        self.assertEqual(self.edges(registry, "READS"), [])

    def test_dynamic_sql_is_flagged_not_resolved(self) -> None:
        registry = self.relate(
            package("  execute immediate 'drop table x';\n  open c for l_sql;", "  l_sql varchar2(10);\n  c sys_refcursor;\n")
        )
        dynamic = self.edges(registry, "REFERENCES")
        self.assertEqual({item["reason"] for item in dynamic}, {"DYNAMIC_SQL_EXECUTE_IMMEDIATE", "DYNAMIC_SQL_OPEN_FOR"})
        for item in dynamic:
            self.assertEqual(item["resolution_status"], "dynamic_sql")
            self.assertIsNone(item["target_id"])

    def test_parse_failure_is_unsupported_without_table(self) -> None:
        registry = self.relate(package("  select foo( into v from dual;"))
        failed = self.edges(registry, "READS")
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["resolution_status"], "unsupported")
        self.assertTrue(failed[0]["reason"].startswith("SQL_PARSE_FAILED"))
        self.assertIsNone(failed[0]["target_id"])


class ColumnLineageTests(RelationTestCase):
    def derives(self, registry: dict) -> dict[str, set[tuple[str | None, str, str | None]]]:
        result: dict[str, set[tuple[str | None, str, str | None]]] = {}
        for item in self.edges(registry, "DERIVES_FROM"):
            result.setdefault(item["source_id"], set()).add(
                (item["target_id"], item["resolution_status"], item["reason"])
            )
        return result

    def test_insert_select_maps_columns_through_aliases(self) -> None:
        registry = self.relate(
            package("  insert into t2 (c1, c2) select a.x, b.y + 1 from s a join u b on a.k = b.k;")
        )
        derived = self.derives(registry)
        self.assertEqual(derived["COLUMN:T2.C1"], {("COLUMN:S.X", "resolved", None)})
        self.assertEqual(derived["COLUMN:T2.C2"], {("COLUMN:U.Y", "resolved", None)})
        quality = {item["source_id"]: item["evidence_quality"] for item in self.edges(registry, "DERIVES_FROM")}
        self.assertEqual(quality, {"COLUMN:T2.C1": "source_exact", "COLUMN:T2.C2": "source_derived"})

    def test_unqualified_column_with_several_tables_is_ambiguous(self) -> None:
        registry = self.relate(package("  insert into t2 (c1) select x from s join u on s.k = u.k;"))
        self.assertEqual(
            self.derives(registry)["COLUMN:T2.C1"],
            {(None, "ambiguous", "UNQUALIFIED_COLUMN_MULTIPLE_TABLES")},
        )

    def test_select_star_and_missing_column_list_are_unsupported(self) -> None:
        registry = self.relate(package("  insert into t2 (c1) select * from s;\n  insert into t3 select a from s;"))
        reasons = {item["reason"] for item in self.edges(registry, "DERIVES_FROM")}
        self.assertEqual(reasons, {"SELECT_STAR_UNSUPPORTED", "INSERT_WITHOUT_COLUMN_LIST"})

    def test_update_set_excludes_plsql_variables_and_flags_subqueries(self) -> None:
        registry = self.relate(
            package("  update t3 set a = b + p_id, d = (select 1 from r) where id = 1;")
        )
        derived = self.derives(registry)
        self.assertEqual(derived["COLUMN:T3.A"], {("COLUMN:T3.B", "resolved", None)})
        self.assertEqual(derived["COLUMN:T3.D"], {(None, "unsupported", "SUBQUERY_EXPRESSION_UNSUPPORTED")})

    def test_merge_column_lineage_is_unsupported(self) -> None:
        registry = self.relate(
            package("  merge into t5 using t6 on (t5.id = t6.id) when matched then update set t5.a = t6.a;")
        )
        self.assertEqual(
            self.derives(registry)["TABLE:T5"], {(None, "unsupported", "MERGE_COLUMN_LINEAGE_UNSUPPORTED")}
        )


if __name__ == "__main__":
    unittest.main()
