"""Run with python3 -m viewer. No installation or daemon required."""
import argparse
import os
from pathlib import Path

from .server import make_server


def main():
    parser = argparse.ArgumentParser(description='Local read-only Codex context viewer')
    parser.add_argument('--log-root', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions')
    parser.add_argument('--state-file', type=Path, default=Path(__file__).resolve().parent.parent / '.local/state.json', help='Atomic local sidecar for boundaries and user concern flags.')
    parser.add_argument('--project', type=Path, default=Path.cwd(), help='Exact project directory (default: caller directory).')
    parser.add_argument('--port', type=int, default=8765, help='Local HTTP port (default: 8765; 0 selects an available port).')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    with make_server(args.log_root, args.port, args.state_file, project=args.project) as server:
        print(f'Context viewer: http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
