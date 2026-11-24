"""
Arma el sitio que se publica en GitHub Pages a partir de data.js, con los datos CIFRADOS.

data.js (en claro, con tu cartera) nunca se publica: se comprime y se cifra con AES-256-GCM
usando una clave derivada de tu contraseña (PBKDF2-SHA256). El navegador del teléfono la pide
una vez y descifra los datos localmente (unlock.js); sin la contraseña solo se ve texto cifrado.

Uso:
    PANEL_PASSWORD='tu contraseña' python publicar.py --out site
"""
import argparse
import base64
import json
import os
import shutil
import sys
import zlib

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes

BASE = os.path.dirname(os.path.abspath(__file__))
ITERATIONS = 310_000  # recomendación OWASP para PBKDF2-SHA256
# archivos de la app que se publican (nada privado: el código y la interfaz)
APP_FILES = ["movil.html", "panel.html", "sim.js", "unlock.js", "sw.js", "manifest.webmanifest",
             "icon-192.png", "icon-512.png", "apple-touch-icon.png"]
HEADERS = """/*
  X-Robots-Tag: noindex, nofollow
  Referrer-Policy: no-referrer
"""


def encrypt(plain: bytes, password: str) -> dict:
    salt, nonce = get_random_bytes(16), get_random_bytes(12)
    key = PBKDF2(password.encode("utf-8"), salt, dkLen=32, count=ITERATIONS, hmac_hash_module=SHA256)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ct, tag = cipher.encrypt_and_digest(zlib.compress(plain, 9))
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    # WebCrypto espera el tag de autenticación pegado al final del texto cifrado
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS, "salt": b64(salt), "iv": b64(nonce),
            "z": "deflate", "ct": b64(ct + tag)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    password = os.environ.get("PANEL_PASSWORD", "")
    if len(password) < 10:
        sys.exit("PANEL_PASSWORD falta o tiene menos de 10 caracteres: no se publica nada sin cifrar.")
    raw = open(os.path.join(BASE, "data.js"), encoding="utf-8").read().strip()
    prefix = "window.PANEL_DATA = "
    if not raw.startswith(prefix):
        sys.exit("data.js no tiene el formato esperado")
    data = json.loads(raw[len(prefix):].rstrip(";"))
    data["host"] = "nube"
    plain = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    out = os.path.join(BASE, args.out)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for f in APP_FILES:
        shutil.copy2(os.path.join(BASE, f), os.path.join(out, f))
    shutil.copy2(os.path.join(BASE, "movil.html"), os.path.join(out, "index.html"))
    env = encrypt(plain, password)
    with open(os.path.join(out, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.PANEL_ENC = " + json.dumps(env) + ";\n")
    with open(os.path.join(out, "_headers"), "w") as f:
        f.write(HEADERS)
    with open(os.path.join(out, ".nojekyll"), "w") as f:
        f.write("")
    with open(os.path.join(out, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")
    print(f"Sitio listo en {args.out}/ ({len(plain) // 1024} KB de datos → {len(env['ct']) // 1024} KB cifrados)")


if __name__ == "__main__":
    main()
