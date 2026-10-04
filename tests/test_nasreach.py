"""Le NAS cache par un VPN : le dire vite, et reparer sans rien casser.

Court, sans Qt, sans reseau exterieur et sans toucher au vrai fichier `hosts`
de Windows : un faux « NAS » local (un port qui ecoute), un faux fichier
`hosts` (PRISME_HOSTS_FILE), et un nom qui ne repond pas.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-nas"))
FAKE_HOSTS = Path(tempfile.gettempdir()) / "prisme-tests-nas-hosts"
os.environ["PRISME_HOSTS_FILE"] = str(FAKE_HOSTS)
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videosorter import nasreach  # noqa: E402

FAILS: list = []


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


def main() -> int:
    print("\n[1] Lire un chemin")
    check(nasreach.server_of(r"\\as1104t\Volume 3\Films") == "as1104t", "le nom du NAS")
    check(nasreach.server_of(r"D:\Vidéos") == "", "un disque local n'en a pas")
    check(nasreach.diagnose(r"D:\Vidéos")[0] == "local", "rien à diagnostiquer en local")

    print("\n[2] Un nom qui ne répond pas : vite")
    started = time.monotonic()
    state, ip = nasreach.diagnose(r"\\nas-introuvable-prisme.invalid\partage")
    spent = time.monotonic() - started
    check(state == "absent" and spent < 12,
          f"« absent », en quelques secondes et non des minutes ({spent:.1f} s)")

    print("\n[3] Le NAS répond à son adresse retenue, pas à son nom")
    # Le « partage » du faux NAS : le port 445 est pris par Windows, on
    # ecoute sur un autre et l'on y redirige la verification.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: [server.accept() for _ in range(20)],
                     daemon=True).start()
    real = nasreach.answers

    def fake_answers(ip, seconds=2.5):
        if ip != "127.0.0.1":
            return False
        try:
            socket.create_connection((ip, port), seconds).close()
            return True
        except OSError:
            return False

    nasreach.answers = fake_answers
    state, ip = nasreach.diagnose(r"\\nas-cache-prisme.invalid\Volume 3", remembered="127.0.0.1")
    check(state == "cache" and ip == "127.0.0.1",
          f"« caché », avec l'adresse où il répond ({state}, {ip})")
    nasreach.answers = real

    print("\n[4] La réparation, dans un faux fichier hosts")
    FAKE_HOSTS.write_text("# fichier hosts\r\n127.0.0.1 localhost\r\n"
                          "192.168.1.9  nas-cache-prisme.invalid\r\n", encoding="utf-8")
    error = nasreach.repair("nas-cache-prisme.invalid", "192.168.1.70")
    text = FAKE_HOSTS.read_text(encoding="utf-8")
    check(error == "" and nasreach.hosts_line("nas-cache-prisme.invalid") == "192.168.1.70",
          f"la ligne du NAS est écrite ({error or 'ok'})")
    check("192.168.1.9" not in text and "127.0.0.1 localhost" in text
          and text.count("nas-cache-prisme.invalid") == 1,
          "l'ancienne adresse remplacée, le reste du fichier intact")
    nasreach.repair("nas-cache-prisme.invalid", "192.168.1.70")
    check(FAKE_HOSTS.read_text(encoding="utf-8").count("nas-cache-prisme.invalid") == 1,
          "réparer deux fois n'ajoute pas de doublon")
    FAKE_HOSTS.unlink()

    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
