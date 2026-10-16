"""The game's own marker pictures, as a guild uploads them.

The pictures are Bohemia's and this repository is public, so none of them is
committed: `utils/arma_icons.py` holds names and paths, and these functions
match an upload against them.

Part of `utils/tacmap`, split out of one 3,257-line module. The package's
`__init__` re-exports every name, so `from utils import tacmap` and
`tacmap.anything` work exactly as they did.
"""
from .base import (MARKERS, MARKER_GROUPS, UNIT_CLASSES)


#
# Every marker and unit is drawn from Arma's own pictures (white on
# transparent PNGs exported from the game), which a guild uploads. They are
# Bohemia's artwork, so they never go into this repository: they are
# uploaded per guild, kept in the database and served only to the guild's
# members and to holders of a map's share link.

MAX_ICON_BYTES = 256 * 1024
MAX_ICON_ARCHIVE = 20 * 1024 * 1024


def _icon_stem(path: str) -> tuple:
    """(folder, name) of a picture's path, lower case, without the extension
    or the `_ca` / `_co` the game's texture names end in — what an upload's
    file and the game's own path have in common."""
    parts = [p for p in path.replace('\\', '/').lower().split('/') if p]
    name = parts[-1].rsplit('.', 1)[0] if parts else ''
    for tail in ('_ca', '_co'):
        if name.endswith(tail):
            name = name[:-len(tail)]
    return (parts[-2] if len(parts) > 1 else '', name)


def _picture_index() -> tuple:
    """Every picture this map can use, by (folder, name) and by name alone."""
    by_path, by_name = {}, {}
    entries = [(key, value['icon']) for key, value in MARKERS.items()]
    entries += [(key, value['icon']) for key, value in UNIT_CLASSES.items()]
    for key, path in entries:
        folder, name = _icon_stem(path)
        by_path.setdefault((folder, name), []).append(key)
        by_name.setdefault(name, []).append(key)
    return by_path, by_name


def picture_coverage(have) -> list:
    """Per group, how many of the game's pictures this server has and where
    the game keeps the ones it lacks — what the Terrains page lists, so
    whoever exports them from the game knows which files to fetch."""
    have = set(have or ())
    groups = [('NATO units', list(UNIT_CLASSES.items()))]
    for group in MARKER_GROUPS:
        groups.append((group, [(key, value) for key, value in MARKERS.items()
                               if value['group'] == group]))
    rows = []
    for name, entries in groups:
        missing = [value['icon'].lstrip('\\') for key, value in entries if key not in have]
        rows.append({'group': name, 'total': len(entries),
                     'have': len(entries) - len(missing), 'missing': missing})
    return rows


def read_marker_icons(data: bytes) -> tuple:
    """The marker pictures in an uploaded zip, as {marker key: PNG bytes},
    and what to tell the person.

    A picture is matched by where the game keeps it: `military/dot_ca.png`
    is `mil_dot`, `handdrawn/dot_ca.png` is `hd_dot`, `nato/b_inf.png` is
    `b_inf`. A file named after its class (`loc_Church.png`) is that class.
    Anything that is not a PNG, is too big, or is no picture the game uses is
    left out and counted.
    """
    import io
    import zipfile
    if len(data) > MAX_ICON_ARCHIVE:
        raise ValueError('That archive is far bigger than a set of marker icons.')
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError('That is not a zip file.')
    by_path, by_name = _picture_index()
    known = set(MARKERS) | set(UNIT_CLASSES)
    icons, unknown, refused = {}, [], 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace('\\', '/').rsplit('/', 1)[-1]
        if not name.lower().endswith('.png') or name.startswith('.'):
            continue
        folder, stem = _icon_stem(info.filename)
        # The game's own folder and file name first; then a file named after
        # its class (`loc_Church.png`); then a file name only one picture has.
        keys = (by_path.get((folder, stem))
                or ([stem] if stem in known else None)
                or (by_name.get(stem) if len(by_name.get(stem, ())) == 1 else None))
        if not keys:
            unknown.append(name)
            continue
        if info.file_size > MAX_ICON_BYTES:
            refused += 1
            continue
        body = archive.read(info)
        if not body.startswith(b'\x89PNG\r\n\x1a\n'):
            refused += 1
            continue
        for key in keys:
            icons[key] = body
    if not icons:
        raise ValueError('No marker pictures in that zip — expected PNGs such as '
                         'military/dot_ca.png, nato/b_inf.png or handdrawn/flag_ca.png.')
    notes = [f'{len(icons)} marker picture{"s" if len(icons) != 1 else ""} stored.']
    if unknown:
        notes.append(f'{len(unknown)} named no marker this map knows and were left out '
                     f'({", ".join(sorted(unknown)[:5])}{"…" if len(unknown) > 5 else ""}).')
    if refused:
        notes.append(f'{refused} were not PNGs, or too big, and were left out.')
    return icons, notes
