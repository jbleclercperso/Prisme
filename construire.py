"""Fabrique Prisme.exe, à emporter sur un autre PC.

Un dossier plutôt qu'un fichier unique : un exécutable unique se décompresse
dans un dossier temporaire à **chaque** lancement — trois cents mégaoctets à
recopier avant de voir la fenêtre. Le dossier, lui, démarre tout de suite, et
se transporte aussi bien dans une archive.

    python construire.py

Le résultat est dans `dist/Prisme/`. ffmpeg n'y est pas : les deux binaires
pèsent plus de quatre cents mégaoctets à eux seuls, et l'application dit
clairement comment les installer si elle ne les trouve pas.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "dist" / "Prisme"

# Les modules Qt dont l'application ne se sert pas. Sans cette liste,
# PyInstaller emporte le moteur web et les outils de conception : deux cents
# mégaoctets pour rien.
EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngine",
    "PySide6.QtQuick3D", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtDesigner",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtHelp", "PySide6.QtPdf",
    "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtQuick",
    "PySide6.QtQml", "matplotlib", "numpy", "scipy", "PIL", "tkinter",
]


def build() -> int:
    for folder in (HERE / "build", HERE / "dist"):
        shutil.rmtree(folder, ignore_errors=True)

    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "Prisme",
        # Sans console : c'est une application à fenêtre, pas un outil en ligne.
        "--windowed",
        "--icon", str(HERE / "prisme.ico"),
        # Le lecteur vidéo de Qt vit dans un greffon chargé au vol : sans
        # cette mention, PyInstaller ne le voit pas et la fenêtre reste noire.
        "--collect-all", "PySide6.QtMultimedia",
        "--hidden-import", "PySide6.QtMultimediaWidgets",
        "--hidden-import", "rapidfuzz",
    ]
    for name in EXCLUDES:
        command += ["--exclude-module", name]
    command.append(str(HERE / "main.py"))

    print("Construction…", flush=True)
    code = subprocess.call(command, cwd=str(HERE))
    if code != 0:
        return code

    # Un exemple à côté du programme : il suffit de le renommer pour partager
    # les vignettes entre deux ordinateurs.
    (OUT / "prisme.cache.exemple").write_text(
        "X:\\_prisme\n", encoding="utf-8")
    (OUT / "LISEZ-MOI.txt").write_text(LISEZMOI, encoding="utf-8")

    archive = HERE / "dist" / "Prisme.zip"
    print("Compression…", flush=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in OUT.rglob("*"):
            if path.is_file():
                zf.write(path, Path("Prisme") / path.relative_to(OUT))

    weight = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\ndist/Prisme/      {weight / 1024 / 1024:.0f} Mo")
    print(f"dist/Prisme.zip   {archive.stat().st_size / 1024 / 1024:.0f} Mo")
    return 0


LISEZMOI = """Prisme — tri rapide d'une collection vidéo
=========================================

POUR L'INSTALLER
Décompressez ce dossier où vous voulez, puis lancez Prisme.exe.
Rien d'autre à installer : Python et Qt sont dedans.

IL FAUT FFMPEG
Prisme s'en sert pour fabriquer les vignettes. S'il ne le trouve pas, il
le dit au démarrage. Pour l'installer, dans une invite de commandes :

    winget install Gyan.FFmpeg

Puis relancez Prisme. (Fermez et rouvrez l'invite après l'installation.)

PARTAGER LES VIGNETTES ENTRE DEUX ORDINATEURS
Fabriquer les vignettes est le travail le plus long. Deux machines qui
regardent le même partage fabriquent exactement les mêmes : leur nom ne
dépend que du chemin, de la taille et de la date de chaque vidéo.

Pour qu'elles se les partagent :
  1. choisissez un dossier sur le partage, par exemple X:\\_prisme ;
  2. renommez « prisme.cache.exemple » en « prisme.cache » et mettez-y
     ce seul chemin ;
  3. faites de même sur l'autre ordinateur.

Condition : le partage doit porter la même lettre de lecteur des deux
côtés. Si c'est X: ici, ce doit être X: là-bas.

L'index (durées, résolutions, empreintes) reste propre à chaque machine :
c'est une base de données, et deux écritures simultanées par le réseau la
fragiliseraient. Seules les vignettes se partagent — ce sont elles qui
coûtent des heures.

OÙ TROUVER TOUT ÇA
Le menu ⋯ → « Où sont les vignettes » le dit, avec le compte des fichiers.

LES RACCOURCIS
Le menu ⋯ → « Raccourcis et recherche ».
"""


if __name__ == "__main__":
    sys.exit(build())
