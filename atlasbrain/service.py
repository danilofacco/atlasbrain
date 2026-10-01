"""One loopback HTTP service for every project and MCP client (no stdio bridges)."""
import asyncio
from . import locking
import hashlib
import io
import json
import secrets
import os
import signal
import socket
import subprocess
from .processes import background_options
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from email.message import Message
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from . import config


def state_dir():
    return Path(os.environ.get('ATLASBRAIN_SERVICE_DIR', '~/.config/atlasbrain')).expanduser()


def project_id(vault):
    return hashlib.sha256(str(Path(vault).resolve()).encode()).hexdigest()[:20]


def health(port):
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/health', timeout=1) as r:
            data = json.load(r)
        if data.get('service') == 'atlasbrain' and data.get('protocol') == 1:
            return data
    except (OSError, URLError, ValueError):
        pass
    return None


def owned_health():
    try:
        state = json.loads((state_dir() / 'server.json').read_text(encoding="utf-8"))
        live = health(state['port'])
        if live and all(live.get(k) == state.get(k) for k in ('pid', 'instance', 'port')):
            return live
    except (OSError, ValueError, KeyError):
        pass
    return None


def check_port(port):
    with socket.socket() as sock:
        # Windows SO_REUSEADDR permits overlapping listeners; require exclusivity.
        option = socket.SO_EXCLUSIVEADDRUSE if os.name == 'nt' else socket.SO_REUSEADDR
        sock.setsockopt(socket.SOL_SOCKET, option, 1)
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'Port {port} is occupied. No alternate port or extra server was started.') from exc


def start(vault, port=8765, auto_index=True):
    if not 1 <= port <= 65535:
        raise RuntimeError('Port must be between 1 and 65535.')
    # Serialize launchers too: simultaneous setup calls spawn only one daemon.
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'launch.lock').open('a') as lock:
        locking.acquire(lock)
        config.data_dir(vault)
        live = owned_health()
        if not live:
            with (directory / 'server.lock').open('a') as running_lock:
                try:
                    locking.acquire(running_lock, blocking=False)
                    locking.release(running_lock)
                except BlockingIOError:
                    deadline = time.monotonic() + 60
                    locked = True
                    while time.monotonic() < deadline and not live:
                        time.sleep(.2)
                        live = owned_health()
                        if not live:
                            try:
                                locking.acquire(running_lock, blocking=False)
                                locking.release(running_lock)
                                locked = False
                                break  # the previous process finished shutting down
                            except BlockingIOError:
                                pass
                    if not live and locked:
                        raise RuntimeError('Existing AtlasBrain process is busy or unresponsive; see server.log. No extra process started.')
        if live:
            if live['port'] != port:
                raise RuntimeError(f"AtlasBrain already runs on port {live['port']}; use that port or stop it first.")
            if live['auto_index'] != auto_index:
                raise RuntimeError('Indexing mode differs. Stop the service before changing it.')
            return live
        check_port(port)
        args = [sys.executable, '-m', 'atlasbrain.cli', '_daemon', '--vault', str(vault), '--porta', str(port)]
        if not auto_index:
            args.append('--sem-auto')
        with (directory / 'server.log').open('ab') as log:
            child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     **background_options(), cwd=Path(__file__).resolve().parent.parent)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            live = owned_health()
            if live:
                return live
            if child.poll() is not None:
                break
            time.sleep(.1)
        if child.poll() is None:
            child.terminate()
        raise RuntimeError(f'Service did not become ready; see {directory / "server.log"}.')


def stop():
    state_dir().mkdir(parents=True, exist_ok=True)
    # Only signal the PID authenticated against our saved instance and live health.
    with (state_dir() / 'launch.lock').open('a') as lock:
        locking.acquire(lock)
        live = owned_health()
        if not live and (state_dir() / 'server.json').exists():
            deadline = time.monotonic() + 30
            while not live and time.monotonic() < deadline:
                with (state_dir() / 'server.lock').open('a') as running_lock:
                    try:
                        locking.acquire(running_lock, blocking=False)
                        return False  # stale state left by an interrupted process
                    except BlockingIOError:
                        pass
                time.sleep(.2)
                live = owned_health()
        if not live:
            return False
        state = json.loads((state_dir() / 'server.json').read_text(encoding='utf-8'))
        if state.get('shutdown_token'):
            request = Request(f"http://127.0.0.1:{live['port']}/_shutdown", data=b'',
                              headers={'X-AtlasBrain-Stop': state['shutdown_token']}, method='POST')
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                response.read()
        elif os.name != 'nt':
            # Upgrade compatibility with an already running pre-native daemon.
            os.kill(live['pid'], signal.SIGTERM)
        else:
            raise RuntimeError('Service has no shutdown token; disable its startup task first.')
        for _ in range(100):
            if not owned_health():
                with (state_dir() / 'server.lock').open('a') as running_lock:
                    try:
                        locking.acquire(running_lock, blocking=False)
                        return True
                    except BlockingIOError:
                        pass
            time.sleep(.1)
        raise RuntimeError('Service is still shutting down; inspect server.log.')


def client_config(vault, client, port=8765, name='atlasbrain'):
    url = f'http://127.0.0.1:{port}/mcp'
    if client == 'codex':
        return f'[mcp_servers.{json.dumps(name)}]\nurl = {json.dumps(url)}\n'
    entry = {'serverUrl': url} if client == 'antigravity' else {'type': 'http', 'url': url}
    return json.dumps({'mcpServers': {name: entry}}, indent=2) + '\n'


def create_app(vault, identity, auto_index=True, *, shutdown=None, shutdown_token=None):
    from starlette.applications import Starlette
    from starlette.requests import Request as WebRequest
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route
    from .web import create_server
    from .mcp_server import build_server
    import inspect

    handler = create_server(vault, auto_index=False, handler_only=True)
    apps = {}
    mutex = asyncio.Lock()
    done = threading.Event()
    sessions = []

    async def project_lifespan(app, ready, stopped):
        # AnyIO cancel scopes must enter and exit in the same asyncio task.
        try:
            async with app.router.lifespan_context(app):
                ready.set_result(None)
                await stopped.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise

    def index_loop():
        from .indexer import index_vault, scan, _log
        from .watch import IndexQueue
        queue = IndexQueue(scan, index_vault)
        while not done.is_set():
            brains = config.registered()
            queue.retain(b['path'] for b in brains)
            for brain in brains:
                if done.is_set():
                    break
                try:
                    result=queue.step(Path(brain['path']))
                    if result is not None and not result.get('skipped') and not result.get('erro'):
                        from .consolidation import refresh_queue
                        refresh_queue(Path(brain['path']),execute=True)
                except Exception as e:
                    _log(f'[atlasbrain] indexing failed: {e}')
            done.wait(3)

    @asynccontextmanager
    async def lifespan(app):
        if auto_index:
            threading.Thread(target=index_loop, daemon=True, name='atlasbrain-index').start()
        from .update import auto_loop
        threading.Thread(target=auto_loop, args=(done, vault), daemon=True,
                         name='atlasbrain-update').start()
        try:
            yield
        finally:
            done.set()
            for task, stopped in sessions:
                stopped.set()
            if sessions:
                await asyncio.gather(*(task for task, _ in sessions))

    class ProjectMCP:
        async def __call__(self, scope, receive, send):
            request = WebRequest(scope, receive)
            key = request.path_params.get('project', '__global__')
            projects = {project_id(b['path']): Path(b['path']) for b in config.registered()}
            if key == '__global__':
                config.GLOBAL_BRAIN.mkdir(parents=True, exist_ok=True)
                config.data_dir(config.GLOBAL_BRAIN)
                projects[key] = config.GLOBAL_BRAIN
            if key not in projects:
                return await JSONResponse({'error': 'Unknown project; run setup for this folder.'}, status_code=404)(scope, receive, send)
            async with mutex:
                if key not in apps:
                    server = build_server(projects[key], auto_index=False, global_endpoint=key == '__global__')
                    if 'streamable_http_path' in inspect.signature(server.streamable_http_app).parameters:
                        app = server.streamable_http_app(streamable_http_path=request.url.path)
                    else:
                        server.settings.streamable_http_path = request.url.path
                        app = server.streamable_http_app()
                    ready = asyncio.get_running_loop().create_future()
                    stopped = asyncio.Event()
                    task = asyncio.create_task(project_lifespan(app, ready, stopped))
                    sessions.append((task, stopped))
                    await ready
                    apps[key] = app
            await apps[key](scope, receive, send)

    async def web(request):
        # Reuse the tested HTTP handlers in memory, without a second socket/server.
        body = await request.body()
        def dispatch():
            h = handler.__new__(handler)
            h.path = request.url.path + ('?' + request.url.query if request.url.query else '')
            h.headers = Message()
            for k, v in request.headers.items():
                h.headers[k] = v
            h.rfile, h.wfile = io.BytesIO(body), io.BytesIO()
            status, headers = [200], {}
            h.send_response = lambda code: status.__setitem__(0, code)
            h.send_header = lambda k, v: headers.__setitem__(k, v)
            h.end_headers = lambda: None
            if request.method == 'POST':
                h.do_POST()
            elif request.method == 'GET':
                h.do_GET()
            else:
                return Response(status_code=405)
            return Response(h.wfile.getvalue(), status_code=status[0], headers=headers)
        return await asyncio.to_thread(dispatch)

    async def health_route(request):
        return JSONResponse(identity)

    async def shutdown_route(request):
        token = request.headers.get('X-AtlasBrain-Stop', '')
        if not shutdown or not shutdown_token or not secrets.compare_digest(token, shutdown_token):
            return JSONResponse({'error': 'Unauthorized'}, status_code=403)
        shutdown()
        return JSONResponse({'stopping': True})

    return Starlette(lifespan=lifespan, routes=[
        Route('/health', health_route),
        Route('/_shutdown', shutdown_route, methods=['POST']),
        Route('/mcp', ProjectMCP(), methods=['GET', 'POST', 'DELETE']),
        Route('/projects/{project}/mcp', ProjectMCP(), methods=['GET', 'POST', 'DELETE']),
        Route('/{path:path}', web, methods=['GET', 'POST']),
    ])


def run_daemon(vault, port=8765, auto_index=True):
    import uvicorn
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'server.lock').open('a') as lock:
        try:
            locking.acquire(lock, blocking=False)
        except BlockingIOError:
            raise SystemExit('An AtlasBrain service is already running.')
        identity = dict(service='atlasbrain', protocol=1, pid=os.getpid(), port=port, vault=str(vault),
                        instance=uuid.uuid4().hex, auto_index=auto_index)
        path = directory / 'server.json'
        temp = directory / 'server.json.tmp'
        shutdown_token = secrets.token_hex(32)
        temp.write_text(json.dumps({**identity, 'shutdown_token': shutdown_token}), encoding='utf-8')
        temp.chmod(0o600)
        temp.replace(path)
        try:
            server = uvicorn.Server(uvicorn.Config(
                create_app(vault, identity, auto_index,
                           shutdown=lambda: setattr(server, 'should_exit', True), shutdown_token=shutdown_token),
                host='127.0.0.1', port=port, log_level='warning', timeout_graceful_shutdown=5))
            server.run()
        finally:
            path.unlink(missing_ok=True)
