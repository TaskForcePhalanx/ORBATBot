"""The migration list's own rules, read out of the source the way
tests/test_logging.py reads the logging rules.

None of this touches a database. What a migration *does* is checked by running
it against one; what these pin is the numbering, which is what makes two
databases agree about which migrations they have run.
"""
import ast
import hashlib
import inspect
import pathlib

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


def test_the_baseline_is_the_old_init_db_and_nothing_calls_it_twice():
    # init_db() is the only caller of migrate(), and it does no DDL of its
    # own: everything about the schema has to go through the list, or a
    # database can be up to date and still be missing something.
    tree = ast.parse((ROOT / 'utils' / 'database.py').read_text())
    init = next(n for n in tree.body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == 'init_db')
    sql = [n.value for n in ast.walk(init) if isinstance(n, ast.Constant)
           and isinstance(n.value, str) and 'CREATE TABLE' in n.value.upper()]
    assert not sql, 'init_db() creates something outside the migration list'
