"""Loopback-only HTTP service. Static assets and API share a single origin."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs

from .contexts import ContextStore, FileContexts
from .logs import discover_sessions
from .snapshots import parse_snapshot
from .source_files import read_source_file

WEB_ROOT = Path(__file__).resolve().parent.parent / 'web'
ASSETS = {'/': ('index.html', 'text/html'), '/styles.css': ('styles.css', 'text/css'),
          '/app.js': ('app.js', 'text/javascript'), '/snapshot-example.json': ('snapshot-example.json', 'application/json'), '/model.mjs': ('model.mjs', 'text/javascript')}
class ViewerServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def make_server(log_root: Path, port: int = 8765, state_file=None, *, project=None):
    root = log_root.resolve()
    store = ContextStore(root, state_file)

    selected_project = Path(project).resolve() if project is not None else None
    files = FileContexts(selected_project, store, state_file) if selected_project is not None else None

    def summaries():
        recorded = discover_sessions(root)
        if selected_project is not None:
            recorded = [s for s in recorded if s.project and Path(s.project).is_absolute()
                        and Path(s.project).resolve() == selected_project]
        return recorded + list(store.import_contexts.values())

    def listing():
        warnings = [] if root.is_dir() else ['Configured log root is unavailable.']
        histories = []
        if files:
            warnings.extend(files.collection['errors'])
            try: histories = files.summaries()
            except ValueError as error: warnings.append(str(error))
        return dict(sessions=[dict(s.public(), contexts=store.list_contexts(s)) for s in summaries()],
                    file_histories=histories, project=str(selected_project) if selected_project else None,
                    collection=files.collection if files else None,
                    status='available' if root.is_dir() else 'unavailable', warnings=warnings)


    class Handler(BaseHTTPRequestHandler):
        # Do not log request paths, captured bodies, source paths or metadata.
        def log_message(self, format, *args):
            pass

        def respond(self, status, body, content_type='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            # Preserve Firefox's Origin on local POSTs; omit cross-origin referrers.
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            if self.command != 'HEAD':
                self.wfile.write(body)

        def allowed(self):
            if len(self.path) > 4096:
                self.respond(414, {'error': 'Request target too long.'})
                return False
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            host = self.headers.get('Host')
            origins = self.headers.get_all('Origin', [])
            if (len(self.headers.get_all('Host', [])) != 1 or host not in hosts
                    or len(origins) > 1 or (origins and origins[0] != f'http://{host}')
                    or self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site')):
                self.respond(403, {'error': 'Local same-origin requests only.'})
                return False
            lengths = self.headers.get_all('Content-Length', [])
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                length = -1
            if len(lengths) > 1 or length < 0 or self.headers.get('Transfer-Encoding'):
                self.respond(400, {'error': 'Invalid request framing.'})
                return False
            if length > 65536:
                self.respond(413, {'error': 'Request body exceeds 64 KiB.'})
                return False
            if length and self.command in ('GET', 'HEAD'):
                self.respond(400, {'error': 'Read requests cannot contain a body.'})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if parsed.scheme or parsed.netloc or parsed.fragment or '..' in path.split('/'):
                self.respond(404, {'error': 'Unknown route.'})
                return
            if path in ASSETS:
                filename, content_type = ASSETS[path]
                self.respond(200, (WEB_ROOT / filename).read_bytes(), content_type)
                return
            if path == '/api/sessions':
                try:
                    self.respond(200, listing())
                except OSError:
                    self.respond(200, {'sessions': [], 'status': 'unreadable', 'warnings': ['Configured log root cannot be read.']})
                return
            if path.startswith('/api/file-histories/'):
                try:
                    if not files: raise ValueError('No project configured.')
                    history_id = path.removeprefix('/api/file-histories/')
                    if not history_id or '/' in history_id: raise ValueError('Unknown report history.')
                    query = parse_qs(parsed.query, strict_parsing=True)
                    if set(query) - {'cursor','limit','after','source'} or any(len(v)!=1 for v in query.values()):
                        raise ValueError('Invalid history page parameters.')
                    limit = int(query.get('limit',['100'])[0]); after = int(query.get('after',['-1'])[0])
                    if after < -1: raise ValueError('Invalid evidence page bounds.')
                    view = files.view(history_id, discover_sessions(root), cursor=query.get('cursor',[None])[0], limit=limit, after=after)
                    if 'source' in query:
                        self.respond(200, read_source_file(view, query['source'][0], selected_project).encode('utf-8'), 'text/plain')
                    else:
                        self.respond(200, view)
                except (ValueError, OSError) as error:
                    self.respond(400, {'error':str(error)})
                return
            if path.startswith('/api/contexts/'):
                context_id = path.removeprefix('/api/contexts/')
                if len(context_id) == 24 and all(c in '0123456789abcdef' for c in context_id):
                    try:
                        summary = next((s for s in summaries() if any(c['id'] == context_id for c in store.list_contexts(s))), None)
                    except OSError:
                        summary = None
                    if summary:
                        try:
                            query = parse_qs(parsed.query, strict_parsing=True)
                            if set(query) - {'after', 'limit', 'source'} or any(len(v) != 1 for v in query.values()):
                                raise ValueError('Invalid page parameters.')
                            after = int(query.get('after', ['-1'])[0])
                            limit = int(query.get('limit', ['100'])[0])
                            if after < -1 or not 1 <= limit <= 100:
                                raise ValueError('Invalid page bounds.')
                            view = store.view(summary, context_id, after, limit)
                            if 'source' in query:
                                self.respond(200, read_source_file(view, query['source'][0], selected_project or summary.project).encode('utf-8'), 'text/plain')
                            else:
                                self.respond(200, view)
                        except ValueError as error:
                            self.respond(400, {'error': str(error)})
                        except OSError:
                            self.respond(404, {'error': 'Selected log is unavailable or unreadable.'})
                        return
                self.respond(404, {'error': 'Context no longer available. Refresh the context list.'})
                return
            self.respond(404, {'error': 'Unknown route.'})

        def do_POST(self):
            if not self.allowed():
                return
            parsed = urlsplit(self.path)
            parts = parsed.path.strip('/').split('/')
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                self.respond(405, {'error': 'Unknown mutation route.'})
                return
            collect = parsed.path == '/api/collect'
            file_flag = len(parts) == 4 and parts[:2] == ['api','file-histories'] and parts[3] == 'flags'
            new_import = parsed.path == '/api/import-contexts'
            if not collect and not file_flag and not new_import and (len(parts) != 4 or parts[:2] != ['api', 'contexts']
                                   or parts[3] not in ('boundaries', 'imports', 'flags')):
                self.respond(405, {'error': 'Unknown mutation route.'})
                return
            try:
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('Expected application/json.')
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode('utf-8')
                if collect:
                    if parse_snapshot(raw) != {}: raise ValueError('Collect takes an empty object.')
                    if files: files.collect()
                    self.respond(200, listing())
                    return
                if file_flag:
                    if not files: raise ValueError('No project configured.')
                    self.respond(200, {'flag':files.flag(parts[2], discover_sessions(root), parse_snapshot(raw))})
                    return
                if new_import:
                    if parse_snapshot(raw) != {}:
                        raise ValueError('Create an empty import context with {} then import its snapshot.')
                    summary = store.create_import_context()
                    self.respond(200, {'context_id': summary.id})
                    return
                context_id = parts[2]
                summary = next((s for s in summaries() if any(c['id'] == context_id for c in store.list_contexts(s))), None)
                if not summary:
                    raise ValueError('Context unavailable.')
                if parts[3] == 'flags':
                    flag = store.flag(summary, context_id, parse_snapshot(raw))
                    self.respond(200, {'flag': flag})
                    return
                if parts[3] == 'imports':
                    generations = self.headers.get_all('X-Context-Generation', [])
                    if len(generations) != 1 or not generations[0]:
                        raise ValueError('Supply the inspected X-Context-Generation before importing.')
                    result = store.import_snapshot(summary, context_id, raw, generations[0])
                    self.respond(200 if result.valid else 400, {'context_id': context_id, 'errors': result.errors,
                                                              'error': '; '.join(result.errors)})
                    return
                if summary.status != 'supported':
                    raise ValueError('Boundaries require a supported recorded context.')
                body = parse_snapshot(raw)
                if not isinstance(body, dict):
                    raise ValueError('Expected object.')
                if body.get('action') == 'remove' and set(body) == {'action'}:
                    store.remove(summary, context_id)
                    result = summary.id
                elif set(body) == {'record_key'} and isinstance(body['record_key'], str):
                    result = store.declare(summary, context_id, body['record_key'])
                else:
                    raise ValueError('Supply record_key or explicit action: remove.')
                self.respond(200, {'context_id': result})
            except (ValueError, OSError, RecursionError) as error:
                self.respond(400, {'error': str(error) or 'Context update failed.'})

        def reject_method(self):
            if self.allowed():
                self.respond(405, {'error': 'Unsupported HTTP method.'})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = reject_method

    return ViewerServer(('127.0.0.1', port), Handler)
