"""The migration list's own rules, read out of the source the way
tests/test_logging.py reads the logging rules.

None of this touches a database. What a migration *does* is checked by running
it against one; what these pin is the numbering, which is what makes two
databases agree about which migrations they have run.
"""
import ast
import asyncio
import hashlib
import inspect
import pathlib
import re

import asyncpg
import pytest

from utils import schema

ROOT = pathlib.Path(__file__).resolve().parent.parent
# sha256 of migration 1's source, first 16 — see test_the_baseline_is_frozen.
BASELINE_DIGEST = 'e8e521d2528b44ab'


def test_versions_are_unique_and_contiguous_from_one():
    # A gap or a repeat means two databases can both call themselves "up to
    # 4" having run different things, which there is then no way to notice.
    versions = [m.version for m in schema.MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))


def test_they_are_listed_in_the_order_they_run():
    # migrate() walks the list, so the list order is the apply order.
    assert [m.version for m in schema.MIGRATIONS] == sorted(
        m.version for m in schema.MIGRATIONS)


def test_every_migration_is_a_named_coroutine():
    for migration in schema.MIGRATIONS:
        assert migration.name.strip(), f'migration {migration.version} has no name'
        assert inspect.iscoroutinefunction(migration.run), migration.name


def test_the_baseline_is_frozen():
    """Migration 1 is the schema as it stood when migrations arrived.

    Editing a shipped migration changes nothing on any database that has
    already run it, so the code and those databases silently disagree from
    then on. If this fails because you meant to change the schema: good —
    add a new migration at the end of MIGRATIONS instead. If it fails
    because you reformatted the file, update the digest below in the same
    commit, having checked the SQL really is untouched.
    """
    body = inspect.getsource(schema._v1_baseline)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    assert digest == BASELINE_DIGEST, (
        'migration 1 has been edited — add a new migration instead '
        f'(see the docstring). Its digest is now {digest}.')


def db_files():
    """Every module of the `utils/database` package.

    It was one file; the tests that read it as source have to read all of it
    or they quietly stop covering most of the queries.
    """
    files = sorted((ROOT / 'utils' / 'database').glob('*.py'))
    assert len(files) > 5, 'utils/database lost its modules'
    return files


def test_the_baseline_is_the_old_init_db_and_nothing_calls_it_twice():
    # init_db() is the only caller of migrate(), and it does no DDL of its
    # own: everything about the schema has to go through the list, or a
    # database can be up to date and still be missing something.
    # `utils/database` is a package; init_db() lives in its core module.
    init = None
    for path in db_files():
        tree = ast.parse(path.read_text())
        init = next((n for n in tree.body
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == 'init_db'), init)
    assert init is not None, 'init_db() is not in utils/database any more'
    sql = [n.value for n in ast.walk(init) if isinstance(n, ast.Constant)
           and isinstance(n.value, str) and 'CREATE TABLE' in n.value.upper()]
    assert not sql, 'init_db() creates something outside the migration list'


# --- running them, without a database ---------------------------------------
#
# `utils/schema.py` takes a connection rather than finding one, so a recorder
# can stand in for it and the suite can pin what a migration *does* — which
# until now was only ever checked by hand against a real PostgreSQL.


class Recorder:
    """A connection that writes down what it is told, and nothing else.

    It models the one thing `migrate()` leans on: `schema_version` is missing
    exactly once in a database's life, and asking for it then raises
    `UndefinedTableError`. `fetchval` answers whatever `answers` says, because
    two of migration 1's statements look at `information_schema` before
    deciding — the `handover_fired` backfill and lifting `reserved_unit` off
    the slot onto its squad.
    """

    def __init__(self, applied=(), has_table=False, answers=None):
        self.statements = []
        self.transactions = 0
        self.applied = set(applied)
        self.has_table = has_table or bool(applied)
        self.answers = answers

    @property
    def sql(self):
        return [sql for sql, _ in self.statements]

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        if 'CREATE TABLE IF NOT EXISTS schema_version' in ' '.join(sql.split()):
            self.has_table = True
        if 'INSERT INTO schema_version' in sql:
            self.applied.add(args[0])
        return 'OK'

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        if 'FROM schema_version' in sql:
            if not self.has_table:
                raise asyncpg.UndefinedTableError('no schema_version')
            return [{'version': version} for version in sorted(self.applied)]
        return []

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args))
        return self.answers

    def transaction(self):
        recorder = self

        class _Tx:
            async def __aenter__(self):
                recorder.transactions += 1

            async def __aexit__(self, *exc):
                return False

        return _Tx()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_a_fresh_database_gets_every_migration_recorded():
    db = Recorder()
    assert run(schema.migrate(db)) == [
        f'{m.version} — {m.name}' for m in schema.MIGRATIONS]
    assert db.applied == {m.version for m in schema.MIGRATIONS}
    assert db.transactions == len(schema.MIGRATIONS), (
        'each migration runs in its own transaction, so one that fails halfway '
        'leaves the schema as it was and its version unrecorded'
    )


def test_the_version_table_is_created_inside_the_lock():
    """The race eight simultaneous boots found, pinned.

    `CREATE TABLE IF NOT EXISTS` checks and then creates: two sessions doing
    it at the same instant does not give the loser a no-op but a duplicate-key
    error out of the catalogue, which is a crashed boot. So it has to happen
    under the advisory lock, not before it.
    """
    db = Recorder()
    run(schema.migrate(db))
    order = [i for i, sql in enumerate(db.sql)
             if 'pg_advisory_lock' in sql
             or 'CREATE TABLE IF NOT EXISTS schema_version' in ' '.join(sql.split())]
    assert len(order) == 2
    assert 'pg_advisory_lock' in db.sql[order[0]], (
        'the version table must be created inside the lock')


def test_an_up_to_date_database_costs_one_query():
    # The whole point of the change: a boot that has nothing to do asks for
    # the versions and stops — no lock, no CREATE, no ALTER.
    db = Recorder(applied=[m.version for m in schema.MIGRATIONS])
    assert run(schema.migrate(db)) == []
    assert db.transactions == 0
    assert db.sql == ['SELECT version FROM schema_version'], db.sql


def test_a_migration_runs_under_a_lock_timeout():
    # A migration that cannot get its table locks fails the boot rather than
    # holding every other query behind it.
    db = Recorder()
    run(schema.migrate(db))
    assert sum('lock_timeout' in sql for sql in db.sql) == len(schema.MIGRATIONS)


def test_the_lock_is_let_go_even_when_a_migration_fails():
    async def failing(_db):
        raise RuntimeError('halfway')

    db = Recorder()
    ledger = [schema.Migration(1, 'failing', failing)]
    with pytest.raises(RuntimeError):
        run(_migrate_ledger(db, ledger))
    assert any('pg_advisory_unlock' in sql for sql in db.sql)
    assert not any('INSERT INTO schema_version' in sql for sql in db.sql)


def _migrate_ledger(db, ledger):
    """`migrate()` against a ledger of our own, by swapping MIGRATIONS out.

    The module reads the list it exports, which is what makes it a ledger
    rather than a hard-coded sequence — and what lets a test use a failing
    migration without a database to fail against.
    """
    async def go():
        real = schema.MIGRATIONS
        schema.MIGRATIONS = ledger
        try:
            return await schema.migrate(db)
        finally:
            schema.MIGRATIONS = real
    return go()


def test_a_migration_numbered_below_the_highest_applied_one_still_runs():
    # Two branches each add a migration and the lower number merges second: a
    # set of versions still runs it, a high-water mark would skip it for ever.
    ledger = [schema.Migration(n, str(n), None) for n in (1, 2, 3)]
    real, schema.MIGRATIONS = schema.MIGRATIONS, ledger
    try:
        assert [m.version for m in schema._pending({1, 3})] == [2]
    finally:
        schema.MIGRATIONS = real


def test_the_baseline_runs_whichever_way_its_two_questions_are_answered():
    # Both of migration 1's data migrations branch on what information_schema
    # says, and both answers have to reach the end of the function.
    old = Recorder(answers=None)
    run(schema._v1_baseline(old))
    new = Recorder(answers=1)
    run(schema._v1_baseline(new))
    for db in (old, new):
        assert any('cp_settings' in sql for sql in db.sql), (
            'the baseline ends with the campaign planner, after everything it '
            'points at')

    assert any('handover_fired = 1' in sql for sql in old.sql), (
        'an events table without the column is backfilled: an event already '
        'completed had its handover done synchronously under the old code')
    assert not any('handover_fired = 1' in sql for sql in new.sql)
    assert any('orbat_slots DROP COLUMN' in sql for sql in new.sql), (
        'a slot that still carries reserved_unit has it lifted to its squad '
        'and the column retired — the other side of the same question')
    assert not any('orbat_slots DROP COLUMN' in sql for sql in old.sql)


# --- what is written --------------------------------------------------------

def sql_of(path):
    """Every string handed to the connection — where a module's SQL is.

    Read off the `db.execute(…)` calls rather than every string in the file,
    or a docstring quoting `CREATE TABLE` to explain itself would be checked
    as though it were a statement.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('execute', 'fetch', 'fetchval', 'fetchrow')):
            for arg in node.args:
                for part in ast.walk(arg):
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        found.append(part.value)
    return found


def test_every_table_the_queries_name_is_created_by_a_migration():
    """The risk this change carried: a migration list that does not match
    what the rest of `utils/database.py` reads and writes."""
    created = set()
    for text in sql_of(ROOT / 'utils' / 'schema.py'):
        created.update(re.findall(
            r'CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)', text, re.IGNORECASE))

    wanted = set()
    for path in db_files():
        for text in sql_of(path):
            # `EXTRACT(EPOCH FROM updated_at)` names a column after FROM, and
            # `ON CONFLICT … DO UPDATE` names no table at all.
            text = re.sub(r'EXTRACT\s*\([^)]*\)', '', text, flags=re.IGNORECASE)
            wanted.update(re.findall(
                r'\b(?:FROM|JOIN|INTO|(?<!DO )UPDATE)\s+([a-z_][a-z0-9_]*)', text))
    wanted = {name for name in wanted
              if name != 'schema_version' and not name.startswith('information_')}

    assert len(wanted) > 30, wanted  # a scan that finds nothing must not pass
    assert sorted(wanted - created) == []


def test_every_statement_is_safe_to_run_twice():
    # What makes migration 1 a no-op against the live database, and what has
    # to stay true of every migration added after it.
    for text in sql_of(ROOT / 'utils' / 'schema.py'):
        flat = ' '.join(text.split())
        for match in re.finditer(
                r'CREATE (?:UNIQUE )?(?:TABLE|INDEX)\b|ADD COLUMN\b', flat):
            rest = flat[match.end():match.end() + 80].lstrip()
            assert rest.upper().startswith('IF NOT EXISTS'), (
                f'{match.group(0)} {rest[:40]}')
