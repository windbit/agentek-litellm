import importlib.util
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRAS_PACKAGE = REPO_ROOT / "litellm-proxy-extras" / "litellm_proxy_extras"
AGENTEK_MIGRATION = "20261009120000_add_agentek_subscription_tables"
AGENTEK_TABLES = (
    "LiteLLM_AgentekSubscription",
    "LiteLLM_AgentekSubscriptionState",
    "LiteLLM_AgentekSubscriptionPolicy",
    "LiteLLM_AgentekAudit",
    "LiteLLM_AgentekSubscriptionDailyStat",
)
PENDING_MIGRATION = "29991231000000_pending_probe"
PENDING_TABLE = "LiteLLM_PendingProbe"
SETUP_RUNNER = (
    "import sys\n"
    "from litellm_proxy_extras.utils import ProxyExtrasDBManager\n"
    "sys.exit(0 if ProxyExtrasDBManager.setup_database(use_migrate=True) else 1)\n"
)
SETUP_TIMEOUT_SECONDS = 300

spec = importlib.util.spec_from_file_location(
    "check_agentek_schema", REPO_ROOT / "ci_cd" / "check_agentek_schema.py"
)
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

BASE_SCHEMA = """datasource client {
  provider = "postgresql"
}

model LiteLLM_Upstream {
  id String @id
}

// BEGIN agentek
model LiteLLM_AgentekThing {
  id    String @id
  name  String
  // a comment
  notes String?
}
// END agentek
"""


def with_block_replaced(schema: str, old: str, new: str) -> str:
    assert old in schema
    return schema.replace(old, new)


def test_repository_copies_hold_one_identical_block():
    copies = {path: (REPO_ROOT / path).read_text() for path in check.SCHEMA_COPIES}

    assert check.copy_problems(copies) == ()


@pytest.mark.parametrize(
    "schema",
    [
        "model A {\n  id String @id\n}\n",
        BASE_SCHEMA + BASE_SCHEMA,
        BASE_SCHEMA + "model LiteLLM_AfterBlock {\n  id String @id\n}\n",
        "// END agentek\n// BEGIN agentek\n",
    ],
    ids=["no-markers", "duplicated", "content-after-end", "end-before-begin"],
)
def test_malformed_block_is_rejected(schema):
    assert check.extract_block(schema) is None
    assert check.copy_problems({"schema.prisma": schema})


def test_diverged_copy_is_named():
    drifted = with_block_replaced(BASE_SCHEMA, "name  String", "name  Int")

    problems = check.copy_problems({"a.prisma": BASE_SCHEMA, "b.prisma": drifted})

    assert problems == ("b.prisma: agentek block differs from a.prisma",)


def test_comment_and_whitespace_changes_do_not_count_as_loss():
    reformatted = with_block_replaced(
        BASE_SCHEMA, "  // a comment\n", "  name  String\n"
    ).replace("name  String", "name    String")

    assert check.growth_problems(BASE_SCHEMA, reformatted) == ()


def test_added_field_and_model_are_growth():
    grown = with_block_replaced(
        BASE_SCHEMA,
        "  notes String?\n}\n",
        "  notes String?\n  extra Int\n}\n\nmodel LiteLLM_AgentekMore {\n  id String @id\n}\n",
    )

    assert check.growth_problems(BASE_SCHEMA, grown) == ()


@pytest.mark.parametrize(
    ("old", "new", "lost"),
    [
        ("  notes String?\n", "", "LiteLLM_AgentekThing: notes String?"),
        ("name  String", "name  Int", "LiteLLM_AgentekThing: name String"),
        (
            "model LiteLLM_AgentekThing {\n  id    String @id\n  name  String\n  // a comment\n  notes String?\n}\n",
            "model LiteLLM_AgentekOther {\n  id String @id\n}\n",
            "LiteLLM_AgentekThing: name String",
        ),
    ],
    ids=["field-removed", "field-retyped", "model-removed"],
)
def test_shrinking_block_is_rejected(old, new, lost):
    shrunk = with_block_replaced(BASE_SCHEMA, old, new)

    problems = check.growth_problems(BASE_SCHEMA, shrunk)

    assert f"agentek block lost a definition: {lost}" in problems


def test_base_without_block_accepts_any_block():
    assert check.growth_problems("model A {\n  id String @id\n}\n", BASE_SCHEMA) == ()


def test_block_removed_after_base_had_it_is_rejected():
    assert check.growth_problems(BASE_SCHEMA, "model A {\n  id String @id\n}\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=ci", "-c", "user.email=ci@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_main_compares_working_tree_with_base_ref(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    for path in check.SCHEMA_COPIES:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BASE_SCHEMA)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)

    assert check.main(["--base-ref", "HEAD"]) == 0

    shrunk = with_block_replaced(BASE_SCHEMA, "  notes String?\n", "")
    for path in check.SCHEMA_COPIES:
        (tmp_path / path).write_text(shrunk)

    assert check.main(["--base-ref", "HEAD"]) == 1


requires_postgres = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="requires a postgres database (DATABASE_URL)",
)


def build_image(
    root: Path, *, with_block: bool, with_pending_migration: bool = False
) -> Path:
    """A copy of the migrations package standing in for a gateway image.

    `with_block=False` is an image built before the agentek tables existed;
    `with_pending_migration` adds a migration the database has not seen.
    """
    image = root / f"image-{uuid.uuid4().hex[:8]}"
    package = image / "litellm_proxy_extras"
    shutil.copytree(EXTRAS_PACKAGE, package)
    schema_path = package / "schema.prisma"
    schema = schema_path.read_text()
    if not with_block:
        schema = schema[: schema.index(check.BEGIN_MARKER)]
        shutil.rmtree(package / "migrations" / AGENTEK_MIGRATION)
    if with_pending_migration:
        probe_model = f"model {PENDING_TABLE} {{\n  id String @id\n}}\n\n"
        marker = check.BEGIN_MARKER if with_block else None
        schema = (
            schema.replace(marker, probe_model + marker)
            if marker
            else schema + "\n" + probe_model
        )
        migration = package / "migrations" / PENDING_MIGRATION
        migration.mkdir()
        (migration / "migration.sql").write_text(
            f'CREATE TABLE "{PENDING_TABLE}" ("id" TEXT NOT NULL, '
            f'CONSTRAINT "{PENDING_TABLE}_pkey" PRIMARY KEY ("id"));\n'
        )
    schema_path.write_text(schema)
    return image


def start_gateway(image: Path, database_url: str) -> None:
    """What the gateway does on boot: ProxyExtrasDBManager.setup_database from the image's package."""
    result = subprocess.run(
        [sys.executable, "-c", SETUP_RUNNER],
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "PYTHONPATH": os.pathsep.join(
                filter(None, [str(image), os.environ.get("PYTHONPATH")])
            ),
        },
        capture_output=True,
        text=True,
        timeout=SETUP_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


class ScratchDatabase:
    def __init__(self, server_url: str):
        import psycopg

        self._psycopg = psycopg
        self._admin_url = server_url
        self.name = f"agentek_block_{uuid.uuid4().hex[:10]}"
        self.url = urlsplit(server_url)._replace(path=f"/{self.name}").geturl()
        with psycopg.connect(server_url, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{self.name}"')

    def execute(self, sql: str, *params: object) -> list[tuple]:
        with self._psycopg.connect(self.url, autocommit=True) as connection:
            cursor = connection.execute(sql, params)
            return cursor.fetchall() if cursor.description else []

    def tables(self) -> set[str]:
        return {
            name
            for (name,) in self.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }

    def drop(self) -> None:
        with self._psycopg.connect(self._admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')


@pytest.fixture
def database():
    scratch = ScratchDatabase(os.environ["DATABASE_URL"])
    yield scratch
    scratch.drop()


def insert_subscription(database: ScratchDatabase) -> None:
    database.execute(
        'INSERT INTO "LiteLLM_AgentekSubscription" (id, provider, name, credential_name, updated_at) '
        "VALUES ('sub-1', 'chatgpt', 'primary', 'cred-1', now())"
    )


def subscription_names(database: ScratchDatabase) -> list[tuple]:
    return database.execute('SELECT name FROM "LiteLLM_AgentekSubscription"')


@requires_postgres
def test_existing_database_gets_the_tables_and_keeps_its_rows(tmp_path, database):
    start_gateway(build_image(tmp_path, with_block=False), database.url)
    database.execute(
        "INSERT INTO \"LiteLLM_Config\" (param_name, param_value) VALUES ('keep', '1')"
    )
    shipped = build_image(tmp_path, with_block=True)

    start_gateway(shipped, database.url)

    assert set(AGENTEK_TABLES) <= database.tables()
    assert database.execute('SELECT param_name FROM "LiteLLM_Config"') == [("keep",)]
    insert_subscription(database)
    start_gateway(shipped, database.url)
    assert subscription_names(database) == [("primary",)]


@requires_postgres
def test_rolling_back_to_an_image_without_the_block_keeps_the_tables(
    tmp_path, database
):
    start_gateway(build_image(tmp_path, with_block=True), database.url)
    insert_subscription(database)

    start_gateway(build_image(tmp_path, with_block=False), database.url)

    assert subscription_names(database) == [("primary",)]


@requires_postgres
def test_newer_image_with_the_block_keeps_the_tables_while_migrating(
    tmp_path, database
):
    start_gateway(build_image(tmp_path, with_block=True), database.url)
    insert_subscription(database)

    start_gateway(
        build_image(tmp_path, with_block=True, with_pending_migration=True),
        database.url,
    )

    assert PENDING_TABLE in database.tables()
    assert subscription_names(database) == [("primary",)]


@requires_postgres
def test_image_without_the_block_and_a_pending_migration_drops_the_tables(
    tmp_path, database
):
    """Why the block must ship in every image: the default resolver drops what the schema lacks."""
    start_gateway(build_image(tmp_path, with_block=True), database.url)

    start_gateway(
        build_image(tmp_path, with_block=False, with_pending_migration=True),
        database.url,
    )

    assert not set(AGENTEK_TABLES) & database.tables()
