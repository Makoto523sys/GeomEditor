"""Single-user, loopback-only HTTP service using Python's standard library."""
from __future__ import annotations

import argparse
import json
import secrets
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .kernel import Document, GeometryError

WEB = Path(__file__).resolve().parent.parent / 'web'
MAX_UPLOAD = 50 * 1024**2


class EditorServer(HTTPServer):
    def __init__(self, address):
        self.document = Document()
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args)>1 else '').startswith('5'):
            super().log_message(fmt, *args)

    def send(self, body, status=200, content_type='application/json', filename=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.allowed_host():
            self.send({'error':'Invalid host'}, 403)
            return
        path = urlsplit(self.path).path
        try:
            if path == '/api/state':
                self.send(self.server.document.scene())
            elif path == '/api/session':
                self.send({'token':self.server.token})
            elif path == '/api/export':
                fmt = parse_qs(urlsplit(self.path).query).get('format', ['step'])[0]
                self.send(self.server.document.export_cad(fmt), content_type='application/octet-stream', filename='geometry.'+fmt)
            elif path == '/api/project':
                self.send(self.server.document.save_project(), content_type='application/octet-stream', filename='geometry.geomproj')
            else:
                files = {'/':('index.html','text/html; charset=utf-8'), '/app.js':('app.js','text/javascript'), '/viewer.js':('viewer.js','text/javascript'), '/style.css':('style.css','text/css')}
                if path not in files:
                    self.send({'error':'Not found'}, 404)
                    return
                name, mime = files[path]
                self.send((WEB/name).read_bytes(), content_type=mime)
        except GeometryError as e:
            self.send({'error':str(e)}, 422)
        except Exception:
            traceback.print_exc()
            self.send({'error':'形状データを処理できませんでした。端末のログを確認してください。'}, 500)

    def do_POST(self):
        origin = self.headers.get('Origin')
        expected = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.allowed_host() or (origin and origin not in expected) or not secrets.compare_digest(self.headers.get('X-GeomEditor-Token',''), self.server.token):
            self.send({'error':'この画面からの操作だけ受け付けます。'}, 403)
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > MAX_UPLOAD:
                self.send({'error':'ファイル／リクエストは 0 MB より大きく 50 MB 以下にしてください。'}, 413)
                return
            data = self.rfile.read(length)
            path = urlsplit(self.path).path
            doc = self.server.document
            if path == '/api/import':
                doc.import_cad(data, parse_qs(urlsplit(self.path).query).get('name',[''])[0])
            elif path == '/api/project':
                doc.load_project(data)
            elif path == '/api/operation':
                params = json.loads(data)
                if not isinstance(params, dict):
                    raise GeometryError('操作データが不正です。')
                doc.operate(params)
            elif path == '/api/measure':
                self.send(doc.measure(json.loads(data).get('entities',[])))
                return
            else:
                self.send({'error':'Not found'},404)
                return
            self.send(doc.scene())
        except (GeometryError, ValueError, TypeError) as e:
            self.send({'error':str(e)},422)
        except Exception:
            traceback.print_exc()
            self.send({'error':'CAD演算に失敗しました。選択対象・交差・寸法を確認してください。'},422)


def main():
    parser = argparse.ArgumentParser(description='GeomEditor — local HTML CAD editor')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    server = EditorServer(('127.0.0.1',args.port))
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'GeomEditor: {url}\n停止: Ctrl+C / モデルは終了前に保存してください。',flush=True)
    if not args.no_browser:
        threading.Timer(0.8,lambda:webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
