"""What the numbered migrations promise.

`utils/migrations.py` imports nothing, so every migration can be run
against a recorder instead of a database — which is the whole reason the
schema is testable at all now that it is no longer eighty statements
executed on every boot.

Two of these read the source the way `tests/test_help.py` and
`tests/test_logging.py` do, because the properties they pin are about what
is *written*, not what runs: that every table the queries name is created
by a migration, and that every statement is shaped so running it twice is
harmless. The second is what makes migration 1 safe against the live
database, which is the one thing about this change that could not be
undone.
"""

import ast
import asyncio
import pathlib
import re

import pytest

from utils import migrations

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Recorder:
    """Stands in for an asyncpg connection, and writes down what it is told.

    `fetchval` answers whatever `answers` says, because two migrations look
    at `information_schema` before deciding — the `handover_fired` backfill
    and lifting `reserved_unit` off the slot.
    """

    def __init__(self, applied=(), answers=None):
        self.statements = []
        self.transactions = 0
        self.applied = list(applied)
        self.answers = answers

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return 'OK'

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        if 'FROM schema_version' in sql:
            return [{'version': v} for v in self.applied]
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


def sql_of(recorder):
    return [sql for sql, _ in recorder.statements]


# --- the ledger -------------------------------------------------------------

def test_migration_numbers_are_unique_and_start_at_one():
    numbers = [version for version, _, _ in migrations.MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1)), (
        'migrations are numbered 1..n with no gaps and no repeats — a '
        'repeated number is recorded once and the second one never runs'
    )


def test_every_migration_has_a_name_and_something_to_do():
    for version, name, migrate in migrations.MIGRATIONS:
        assert re.fullmatch(r'[a-z][a-z0-9_]*', name), (version, name)
        assert callable(migrate), (version, name)
    names = [name for _, name, _ in migrations.MIGRATIONS]
    assert len(set(names)) == len(names)


# --- which ones run ---------------------------------------------------------

def test_nothing_applied_runs_everything_in_order():
    assert migrations.pending(set()) == tuple(
        sorted(migrations.MIGRATIONS, key=lambda m: m[0]))


def test_an_applied_migration_is_never_run_again():
    done = {version for version, _, _ in migrations.MIGRATIONS}
    assert migrations.pending(done) == ()


def test_a_migration_numbered_below_the_highest_applied_one_still_runs():
    # Two branches each add a migration and the lower number merges second:
    # a high-water mark would skip it for ever, a set of versions does not.
    ledger = ((1, 'one', None), (2, 'two', None), (3, 'three', None))
    assert migrations.pending({1, 3}, ledger) == ((2, 'two', None),)


# --- running them -----------------------------------------------------------

def test_a_fresh_database_gets_every_migration_recorded():
    db = Recorder()
    ran = run(migrations.run(db))
    assert ran == [(version, name) for version, name, _ in migrations.MIGRATIONS]
    assert db.transactions == len(migrations.MIGRATIONS), (
        'each migration runs in its own transaction, so a failure leaves the '
        'schema as it was and the version unrecorded'
    )
    inserts = [sql for sql in sql_of(db) if 'INSERT INTO schema_version' in sql]
    assert len(inserts) == len(migrations.MIGRATIONS)


def test_an_up_to_date_database_runs_no_schema_statement():
    # The point of the change: a boot is one version query, not eighty
    # CREATE/ALTER statements.
    db = Recorder(applied=[version for version, _, _ in migrations.MIGRATIONS])
    assert run(migrations.run(db)) == []
    assert db.transactions == 0
    schema = [sql for sql in sql_of(db)
              if re.search(r'\b(CREATE TABLE|ALTER TABLE|CREATE INDEX|CREATE UNIQUE)\b', sql)
              and migrations.VERSION_TABLE not in sql]
    assert schema == [], schema


def test_the_run_is_locked_and_the_lock_is_let_go():
    db = Recorder()
    run(migrations.run(db))
    locks = [(sql, args) for sql, args in db.statements if 'pg_advisory' in sql]
    assert [sql for sql, _ in locks] == [
        'SELECT pg_advisory_lock($1)', 'SELECT pg_advisory_unlock($1)']
    assert all(args == (migrations.LOCK_KEY,) for _, args in locks)


def test_the_lock_is_let_go_even_when_a_migration_fails():
    def boom(_db):
        raise RuntimeError('no')

    async def failing(db):
        boom(db)

    db = Recorder()
    with pytest.raises(RuntimeError):
        run(migrations.run(db, ((1, 'failing', failing),)))
    assert any('pg_advisory_unlock' in sql for sql in sql_of(db))
    assert not any('INSERT INTO schema_version' in sql for sql in sql_of(db))


def test_the_baseline_runs_whichever_way_its_two_questions_are_answered():
    # The handover backfill and the reserved_unit lift both branch on what
    # information_schema says, so both answers have to reach the end.
    for answer in (None, 1):
        db = Recorder(answers=answer)
        run(migrations._baseline(db))
        assert any('kt_unit_overrides' in sql for sql in sql_of(db))
        assert any('cp_settings' in sql for sql in sql_of(db)), (
            'the baseline ends with the campaign planner, after everything it '
            'points at'
        )

    old = Recorder(answers=None)
    run(migrations._baseline(old))
    assert any('handover_fired = 1' in sql for sql in sql_of(old)), (
        'an events table without the column is backfilled: an event already '
        'completed had its handover done synchronously under the old code'
    )
    already = Recorder(answers=1)
    run(migrations._baseline(already))
    assert not any('handover_fired = 1' in sql for sql in sql_of(already))
    assert any('DROP COLUMN' in sql for sql in sql_of(already)), (
        "a slot that still carries reserved_unit has it lifted to its squad "
        "and the column retired — the other side of the same question"
    )
    assert not any('orbat_slots DROP COLUMN' in sql for sql in sql_of(old))


# --- what is written --------------------------------------------------------

def sql_literals(path):
    """Every string handed to the connection — where a module's SQL is.

    Read off the `db.execute(…)` calls rather than every string constant in
    the file, or this module's own docstring (which quotes `CREATE TABLE`
    to explain itself) would be checked as though it were a statement.
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in ('execute', 'fetch', 'fetchval', 'fetchrow'):
            continue
        for arg in node.args:
            for part in ast.walk(arg):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    found.append(part.value)
    return found


def tables_created():
    found = set()
    for text in sql_literals(ROOT / 'utils' / 'migrations.py'):
        found.update(re.findall(r'CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)',
                                text, re.IGNORECASE))
    return found


def test_every_table_the_queries_name_is_created_by_a_migration():
    # The risk this change carried: a baseline that does not match what the
    # rest of the module reads and writes.
    wanted = set()
    for text in sql_literals(ROOT / 'utils' / 'database.py'):
        # `EXTRACT(EPOCH FROM updated_at)` names a column after FROM, and
        # `ON CONFLICT … DO UPDATE` names none at all.
        text = re.sub(r'EXTRACT\s*\([^)]*\)', '', text, flags=re.IGNORECASE)
        wanted.update(re.findall(
            r'\b(?:FROM|JOIN|INTO|(?<!DO )UPDATE)\s+([a-z_][a-z0-9_]*)', text))
    wanted = {name for name in wanted
              if name != migrations.VERSION_TABLE
              and not name.startswith('information_')}
    missing = sorted(wanted - tables_created())
    assert missing == [], missing
    assert len(wanted) > 30, wanted  # a scan that finds nothing must not pass


def test_the_migrations_are_safe_to_run_twice():
    # What makes migration 1 a no-op against the live database, and what has
    # to stay true of every one added after it: nothing in here fails because
    # it has already been done.
    for text in sql_literals(ROOT / 'utils' / 'migrations.py'):
        flat = ' '.join(text.split())
        for match in re.finditer(
                r'CREATE (?:UNIQUE )?(?:TABLE|INDEX)\b|ADD COLUMN\b', flat):
            rest = flat[match.end():match.end() + 80].lstrip()
            assert rest.upper().startswith('IF NOT EXISTS'), (
                match.group(0) + ' ' + rest[:40])
