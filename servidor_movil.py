"""
Servidor local para ver el panel en el teléfono (misma red WiFi que este PC).

Solo publica los archivos de la app móvil y data.js: el Excel del portafolio,
config.json y el código NO quedan accesibles desde la red.

Uso:
    python servidor_movil.py          # puerto 8765
    python servidor_movil.py 9000     # otro puerto
"""
import os
import socket
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
ALLOW = {"movil.html", "data.js", "sim.js", "unlock.js", "manifest.webmanifest", "sw.js",
         "icon-192.png", "icon-512.png", "apple-touch-icon.png"}


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json", ".js": "text/javascript; charset=utf-8"}

    def _allowed(self):
        path = self.path.split("?")[0].lstrip("/") or "movil.html"
        if path not in ALLOW:
            self.send_error(404)
            return False
        self.path = "/" + path
        return True

    def do_GET(self):
        if self._allowed():
            super().do_GET()

    def do_HEAD(self):
        if self._allowed():
            super().do_HEAD()

    def end_headers(self):
        # data.js cambia cada 2 minutos: nunca desde caché del navegador
        if self.path.startswith(("/data.js", "/sw.js", "/sim.js", "/movil.html")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=BASE))
    print("=" * 60)
    print("  Panel en el teléfono (misma red WiFi que este PC):")
    print(f"     http://{lan_ip()}:{PORT}")
    print("  Luego: Compartir > 'Agregar a inicio' para instalarla.")
    print("=" * 60, flush=True)
    srv.serve_forever()
