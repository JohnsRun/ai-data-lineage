import importlib.util
import pathlib
import sys
import types
import unittest
import unittest.mock


ORACLEDB = types.SimpleNamespace(DatabaseError=Exception, connect=None, init_oracle_client=None)
sys.modules.setdefault("oracledb", ORACLEDB)
MODULE_PATH = pathlib.Path(__file__).with_name("db_connect_edit.py")
SPEC = importlib.util.spec_from_file_location("db_connect_action", MODULE_PATH)
ACTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTION)


class ValidateDmlSqlTests(unittest.TestCase):
    def test_accepts_bounded_update_with_trailing_semicolon(self):
        sql, operation = ACTION.validate_dml_sql("UPDATE staff SET name = 'Ada' WHERE id = 1;")
        self.assertEqual(sql, "UPDATE staff SET name = 'Ada' WHERE id = 1")
        self.assertEqual(operation, "UPDATE")

    def test_rejects_unbounded_delete(self):
        with self.assertRaisesRegex(ValueError, "top-level WHERE"):
            ACTION.validate_dml_sql("DELETE FROM audit_log")

    def test_rejects_multiple_statements(self):
        with self.assertRaisesRegex(ValueError, "one SQL statement"):
            ACTION.validate_dml_sql("DELETE FROM audit_log WHERE id = 1; COMMIT")

    def test_rejects_unsupported_sql_before_loading_configuration(self):
        with unittest.mock.patch.object(ACTION, "_load_config") as load_config:
            self.assertEqual(ACTION.main(["--sql", "COMMIT"]), 2)
        load_config.assert_not_called()

    def test_accepts_read_only_select(self):
        sql, operation = ACTION.validate_sql("SELECT id FROM staff FETCH FIRST 50 ROWS ONLY")
        self.assertEqual(operation, "SELECT")
        self.assertIn("FROM staff", sql)

    def test_rejects_select_for_update(self):
        with self.assertRaisesRegex(ValueError, "unsupported operation"):
            ACTION.validate_sql("SELECT id FROM staff FOR UPDATE")


class ConfigurationTests(unittest.TestCase):
    def test_uat_uses_action_specific_environment_variables(self):
        environment = {
            "ORA_HOST": "localhost",
            "ORA_PORT": "1521",
            "ORA_SERVICE_UAT": "XE",
            "ORA_USER_ACTION_UAT": "uat_user",
            "ORA_PASSWORD_ACTION_UAT": "uat_password",
        }
        with unittest.mock.patch.dict(ACTION.os.environ, environment, clear=True):
            config = ACTION._load_config("UAT")
        self.assertEqual(config["user"], "uat_user")
        self.assertEqual(config["service"], "XE")


class ExecuteDmlTests(unittest.TestCase):
    def test_commits_after_successful_execution(self):
        class Cursor:
            rowcount = 3

            def execute(self, sql):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        class Connection:
            def __init__(self):
                self.commit = unittest.mock.Mock()
                self.cursor = Cursor

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        connection = Connection()
        with unittest.mock.patch.object(ACTION.oracledb, "connect", return_value=connection):
            rowcount = ACTION.execute_dml("DELETE FROM audit_log WHERE id = 1", {
                "host": "localhost", "port": 1521, "service": "XE", "user": "dev", "password": "secret"
            })
        self.assertEqual(rowcount, 3)
        connection.commit.assert_called_once()

    def test_query_does_not_commit(self):
        class Cursor:
            def __init__(self):
                self.description = [("ID",), ("NAME",)]

            def execute(self, sql):
                return None

            def fetchall(self):
                return [(1, "Ada")]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        class Connection:
            def __init__(self):
                self.commit = unittest.mock.Mock()
                self.cursor = Cursor

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        connection = Connection()
        with unittest.mock.patch.object(ACTION.oracledb, "connect", return_value=connection):
            columns, rows = ACTION.execute_query("SELECT id, name FROM staff", {
                "host": "localhost", "port": 1521, "service": "XE", "user": "dev", "password": "secret"
            })
        self.assertEqual(columns, ["id", "name"])
        self.assertEqual(rows, [(1, "Ada")])
        connection.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()