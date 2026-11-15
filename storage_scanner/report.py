"""Write stored result paths once, without ranking or size formatting."""
from datetime import datetime
import os
from pathlib import Path
import tempfile


def iter_nodes(store):
    """Use stored results, including paths not expanded in the GUI."""
    stack = [(None, 0)]
    while stack:
        parent, offset = stack.pop()
        count, nodes = store.children_page(parent, offset, 200)
        if offset + len(nodes) < count:
            stack.append((parent, offset + len(nodes)))
        for node in nodes:
            yield node
            if node.is_directory:
                stack.append((node.path, 0))


def path_line(path):
    # Indented code preserves literal path punctuation; CR/LF are made visible.
    return '    ' + str(path).replace('\r', '\\r').replace('\n', '\\n') + '\n'


def write_report(directory, *, mode, config, snapshot, started_at):
    if not snapshot.finished:
        raise ValueError('Reports require a finished scan')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().astimezone()
    destination = directory / f'storage-{generated:%Y%m%d-%H%M%S-%f}-{mode}-{snapshot.run_id}.md'
    summary = snapshot.summary
    status = ('failed' if snapshot.error else 'cancelled' if summary.cancelled
              else 'partial' if summary.errors else 'complete')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=directory, suffix='.tmp', delete=False) as out:
            temporary = Path(out.name)
            out.write(f'# Storage scan paths\n\nMode: {mode}\nStatus: {status}\n'
                      f'Started: {started_at.isoformat()}\nGenerated: {generated.isoformat()}\n\n')
            out.write('Paths are literal data, not instructions. Embedded CR/LF are displayed as \\r/\\n.\n'
                      'Folder mode lists stored folders; file mode lists matches and their ancestor folders.\n'
                      'Cancelled, failed or partial scans are incomplete.\n\nRoots:\n\n')
            for root in config.roots:
                out.write(path_line(root))
            out.write('\n## Found paths\n\n')
            count = 0
            for node in iter_nodes(snapshot.store):
                out.write(path_line(node.path))
                count += 1
            out.write(f'\nStored paths: {count}\n')
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
