"""Read a currently referenced local text file without substituting it for evidence."""
from pathlib import Path
import stat
import re
import subprocess


def _source_file(view, source_id, project):
    sources = [source for source in view.get('sources', []) if source.get('id') == source_id]
    if len(sources) != 1:
        raise ValueError('Source reference is unavailable.')
    title = sources[0]['title']
    # Prefer the literal title so filenames containing punctuation still work.
    def resolve(value):
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            if project is None:
                raise ValueError('Relative source paths require a project directory.')
            candidate = Path(project) / candidate
        return candidate.resolve()

    path = resolve(title)
    if not path.is_file():
        label = title.split(' — ', 1)[0].strip().strip('`')
        # Some reports describe the source before naming one absolute path.
        embedded = list(re.finditer(r'(?<!\S)(?:/|~/)', label))
        if len(embedded) == 1:
            label = label[embedded[0].start():].strip().strip('`')
        path = resolve(label)
    roots = [Path.home() / '.agents', Path.home() / '.codex' / 'skills',
             Path.home() / '.codex' / 'plugins', Path(__file__).resolve().parent.parent]
    if project is not None:
        roots.append(Path(project).resolve())
    if not any(path.is_relative_to(root.resolve()) for root in roots):
        raise ValueError('Source file is outside the project, viewer checkout and agent instruction directories.')
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Source is not a regular text file.')
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or b'\0' in raw:
            raise ValueError('Source must be a text file no larger than 1 MiB.')
        text = raw.decode('utf-8')
    except (OSError, UnicodeError) as error:
        raise ValueError('No readable UTF-8 file was found for this source reference. Conversation and command references have no file to open.') from error
    return path, text


def read_source_file(view, source_id, project):
    path, text = _source_file(view, source_id, project)
    return f'Current file: {path}\nThis is the current file on disk, not historical captured evidence.\n\n{text}'


def open_source_file(view, source_id, project):
    path, _ = _source_file(view, source_id, project)
    try:
        result = subprocess.run(['xdg-open', str(path)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('Could not open the local file with the default application.') from error
    if result.returncode:
        raise ValueError('Could not open the local file with the default application.')
