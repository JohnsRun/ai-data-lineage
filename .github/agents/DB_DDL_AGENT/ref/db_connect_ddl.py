"""Export Oracle package body DDL through a constrained command-line interface.

Required environment variables for the project dotenv file:
    ORA_SERVICE_DEV
    ORA_USER_QUERY_DEV
    ORA_PASSWORD_QUERY_DEV
    ORA_SERVICE_UAT
    ORA_USER_QUERY_UAT
    ORA_PASSWORD_QUERY_UAT

Optional environment variables:
    ORA_HOST
    ORA_PORT
    ORACLE_CLIENT_LIB_DIR  Path to Oracle Instant Client libraries.
"""
import argparse
from pathlib import Path
import os
import re
import sys

import oracledb
from dotenv import load_dotenv


_ENVIRONMENT_VARIABLES = {
    "DEV": ("ORA_SERVICE_DEV", "ORA_USER_QUERY_DEV", "ORA_PASSWORD_QUERY_DEV"),
    "UAT": ("ORA_SERVICE_UAT", "ORA_USER_QUERY_UAT", "ORA_PASSWORD_QUERY_UAT"),
}
_OBJECT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_$#]*$")


def _build_ddl_sql(object_name: str, schema_name: str) -> str:
    """Build the metadata SQL string that Oracle will execute."""
    return (
        "SELECT DBMS_METADATA.GET_DDL(\n"
        "         'PACKAGE_BODY',\n"
        f"         '{object_name}',\n"
        f"         '{schema_name}'\n"
        "       ) AS package_body_ddl\n"
        "  FROM dual"
    )


def _load_config(environment: str) -> dict[str, str | int]:
    """Load connection settings without exposing their values."""
    service_variable, user_variable, password_variable = _ENVIRONMENT_VARIABLES[environment]

    if environment == "DEV":
        service = os.environ.get(service_variable) or ""
        user = os.environ.get(user_variable) or "DEV"
        password = os.environ.get(password_variable) or ""
        schema_name = user.upper() if user else "DEV"
    else:
        service = os.environ.get(service_variable) or ""
        user = os.environ.get(user_variable) or ""
        password = os.environ.get(password_variable) or ""
        schema_name = user.upper() if user else "DEV"

    missing = [
        variable_name
        for variable_name, value in (
            (service_variable, service),
            (user_variable, user),
            (password_variable, password),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"Missing required {environment} environment variable(s): " + ", ".join(missing)
        )

    host = os.environ.get("ORA_HOST")
    port_value = os.environ.get("ORA_PORT")

    if host and port_value:
        try:
            port = int(port_value)
        except ValueError as error:
            raise ValueError("ORA_PORT must be an integer.") from error
        dsn = f"{host}:{port}/{service}"
        return {
            "host": host,
            "port": port,
            "service": service,
            "user": user,
            "password": password,
            "schema": schema_name,
            "dsn": dsn,
        }

    return {
        "host": None,
        "port": None,
        "service": service,
        "user": user,
        "password": password,
        "schema": schema_name,
        "dsn": service,
    }


def _read_ddl(config: dict[str, str | int], sql: str) -> str | None:
    """Fetch one DDL CLOB by executing the supplied SQL string."""
    dsn = config["dsn"]
    with oracledb.connect(
        user=config["user"], password=config["password"], dsn=dsn
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            ddl = row[0].read() if hasattr(row[0], "read") else str(row[0])
            return ddl if isinstance(ddl, str) else ddl.decode("utf-8")


def _save_ddl(object_name: str, ddl: str) -> Path:
    """Persist the complete DDL in the agent-owned temporary output directory."""
    temp_directory = Path(__file__).resolve().parent.parent / "temp"
    temp_directory.mkdir(parents=True, exist_ok=True)
    output_path = temp_directory / f"{object_name}.package_body.sql"
    output_path.write_text(ddl, encoding="utf-8")
    return output_path


def _print_result(environment: str, output_path: Path, ddl: str) -> None:
    """Print the saved location and the first 100 lines of the DDL in a markdown block."""
    workspace_root = Path(__file__).resolve().parents[4]
    preview_lines = ddl.splitlines()[:100]
    print("---")
    print(f"Connection: {environment}")
    print()
    print(f"Saved: {output_path.relative_to(workspace_root)}")
    print()
    print("```sql")
    print("\n".join(preview_lines))
    print("```")


def main(argv: list[str] | None = None) -> int:
    """Export one validated package body and print its first 10 DDL lines."""
    parser = argparse.ArgumentParser(description="Export one Oracle package body DDL.")
    parser.add_argument(
        "--env",
        choices=("DEV", "UAT"),
        default="DEV",
        type=str.upper,
        help="Connection environment; defaults to DEV.",
    )
    parser.add_argument("--object-name", help="Oracle package name.")
    parser.add_argument(
        "--sql",
        help="Exact SQL string to execute, such as SELECT DBMS_METADATA.GET_DDL(...).",
    )
    args = parser.parse_args(argv)

    try:
        config = _load_config(args.env)
        schema_name = str(config["schema"]).upper()
        if args.sql:
            sql = args.sql.strip()
            object_name = "sql_input"
        else:
            if not args.object_name:
                print("MISSING_TARGET: Provide --object-name or --sql.", file=sys.stderr)
                return 2
            object_name = args.object_name.upper()
            if not _OBJECT_NAME_PATTERN.fullmatch(object_name):
                print("INVALID_OBJECT_NAME: Use one ordinary Oracle package name.", file=sys.stderr)
                return 2
            sql = _build_ddl_sql(object_name, schema_name)

        ddl = _read_ddl(config, sql)
    except ValueError as error:
        print(f"CONFIGURATION_ERROR: {error}", file=sys.stderr)
        return 2
    except oracledb.DatabaseError as error:
        print(f"ORACLE_ERROR: {error}", file=sys.stderr)
        return 1

    if ddl is None:
        label = args.object_name.upper() if args.object_name else "sql_input"
        print(f"NO_DDL: No package body metadata found for {label}.", file=sys.stderr)
        return 1

    output_path = _save_ddl(object_name, ddl)
    _print_result(args.env, output_path, ddl)
    return 0


if __name__ == "__main__":
    env_file = Path(__file__).resolve().parents[4] / "02Development_Zone" / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    client_library = os.environ.get("ORACLE_CLIENT_LIB_DIR")
    if client_library:
        oracledb.init_oracle_client(lib_dir=client_library)
    raise SystemExit(main())