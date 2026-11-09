"""Read a currently referenced local text file without substituting it for evidence."""
from pathlib import Path
import stat


def read_source_file(view, source_id, project):
    sources = [source for source in view.get('sources', []) if source.get('id') == source_id]
    if len(sources) != 1:
        raise ValueError('Source reference is unavailable.')
    title = sources[0]['title']
    path = Path(title).expanduser()
    if not path.is_absolute():
        if project is None:
            raise ValueError('Relative source paths require a project directory.')
        path = Path(project) / path
    path = path.resolve()
    roots = [Path.home() / '.agents', Path.home() / '.codex' / 'skills']
    if project is not None:
        roots.append(Path(project).resolve())
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        raise ValueError('Source file is outside the project and agent instruction directories.')
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Source is not a regular text file.')
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or b'\0' in raw:
            raise ValueError('Source must be a text file no larger than 1 MiB.')
        text = raw.decode('utf-8')
    except (OSError, UnicodeError) as error:
        raise ValueError('Source file is unavailable or is not UTF-8 text.') from error
    return f'Current file: {path}\nThis is the current file on disk, not historical captured evidence.\n\n{text}'
