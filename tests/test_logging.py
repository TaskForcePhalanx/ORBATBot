"""The logging rules, enforced the way tests/test_help.py enforces the how-tos:
by reading the source rather than by trusting everyone to remember.

`bot.py`'s setup_logging() is what configures it; these pin the three things
about using it that are easy to get wrong in a later change.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
# scripts/ still prints: those are command-line tools talking to whoever ran
# them, not a service writing a log.
SOURCES = sorted(
    [ROOT / 'bot.py']
    + [p for d in ('cogs', 'utils', 'web') for p in (ROOT / d).rglob('*.py')]
)


def _tree(path):
    return ast.parse(path.read_text())


def _log_calls(tree):
    """Every `log.<level>(...)` in the file, with its level and line."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'log'):
            yield node.func.attr, node.lineno


def _except_ranges(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            lines = [c.lineno for c in node.body] or [node.lineno]
            ends = [getattr(c, 'end_lineno', c.lineno) for c in node.body] or [node.lineno]
            yield min(lines), max(ends)


def test_the_service_logs_rather_than_prints():
    # A print has no level, no timestamp and no module name, so on Railway it
    # is indistinguishable from a traceback. Anything worth saying goes
    # through the module's logger.
    printing = []
    for path in SOURCES:
        for node in ast.walk(_tree(path)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'print'):
                printing.append(f'{path.relative_to(ROOT)}:{node.lineno}')
    assert not printing, 'print() instead of a logger: ' + ', '.join(printing)


def test_exception_is_only_logged_where_there_is_one():
    # log.exception() outside an except block writes "NoneType: None" under
    # the message, which reads as a broken traceback rather than no traceback.
    stray = []
    for path in SOURCES:
        tree = _tree(path)
        ranges = list(_except_ranges(tree))
        for level, lineno in _log_calls(tree):
            if level == 'exception' and not any(lo <= lineno <= hi for lo, hi in ranges):
                stray.append(f'{path.relative_to(ROOT)}:{lineno}')
    assert not stray, 'log.exception() outside an except block: ' + ', '.join(stray)


def test_every_module_that_logs_has_its_own_logger():
    # `logging.getLogger(__name__)`, so the line says which module wrote it
    # and so bot.py's per-package levels reach it.
    missing = []
    for path in SOURCES:
        source = path.read_text()
        if not any(True for _ in _log_calls(ast.parse(source))):
            continue
        if 'log = logging.getLogger(__name__)' not in source:
            missing.append(str(path.relative_to(ROOT)))
    assert not missing, 'logs without defining `log`: ' + ', '.join(missing)


def test_the_levels_bot_py_raises_are_the_packages_that_log():
    # LOG_LEVELS is what lifts this project above the root's WARNING. A new
    # top-level package that logs and is not in it would write INFO lines
    # nobody ever sees.
    source = (ROOT / 'bot.py').read_text()
    listed = {part.strip().strip("'\"") for part in
              source.split('LOG_LEVELS = (')[1].split(')')[0].split(',') if part.strip()}
    packages = set()
    for path in SOURCES:
        if not any(True for _ in _log_calls(_tree(path))):
            continue
        rel = path.relative_to(ROOT)
        packages.add('__main__' if rel.name == 'bot.py' else rel.parts[0])
    assert packages <= listed, f'these log but bot.py never raises them: {packages - listed}'
