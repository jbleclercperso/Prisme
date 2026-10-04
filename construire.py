"""Fabrique Prisme.exe, à emporter sur un autre PC.

Un dossier plutôt qu'un fichier unique : un exécutable unique se décompresse
dans un dossier temporaire à **chaque** lancement — trois cents mégaoctets à
recopier avant de voir la fenêtre. Le dossier, lui, démarre tout de suite, et
se transporte aussi bien dans une archive.

    python construire.py              le programme seul
    python construire.py --complet    avec les vignettes deja fabriquees

Le résultat est dans `dist/Prisme/`. Avec `--complet`, le cache de cette
machine y est copié dans `cache/` : le programme le trouve tout seul et
retrouve les vignettes sur n'importe quel ordinateur, sans rien refabriquer.

ffmpeg n'y est pas : les deux binaires pèsent plus de quatre cents mégaoctets
à eux seuls, et l'application dit clairement comment les installer si elle ne
les trouve pas.
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
    # Le labo IA : il ne marche que depuis les sources (ia.can_install le dit),
    # mais s'il est installé sur la machine qui construit, PyInstaller suit
    # ses imports et emporte plusieurs gigaoctets de torch.
    "torch", "torchvision", "torchaudio", "transformers", "open_clip",
    "huggingface_hub", "safetensors", "tokenizers", "timm", "sympy",
    "faster_whisper", "ctranslate2", "onnxruntime", "sklearn", "pandas",
]


def gather_cache(into: Path) -> tuple:
    """Copie le cache de cette machine dans le paquet, pour qu'il voyage.

    Les vignettes sont le bien le plus cher : des heures de fabrication sur
    un partage reseau. Les emporter evite a l'autre ordinateur de tout
    refaire. L'index les accompagne — durees, resolutions, plans, empreintes.
    """
    from videosorter.config import APP_DIR

    source = APP_DIR
    if not (source / "thumbs").is_dir():
        older = source.parent / "VideoSorter"
        if (older / "thumbs").is_dir():
            source = older
    if not source.is_dir():
        return 0, 0

    into.mkdir(parents=True, exist_ok=True)
    count = weight = 0
    # Ni les journaux ni la liste de lecture : ils ne disent rien de la
    # collection et changent a chaque seance.
    # Ni les journaux, ni la liste de lecture, ni le registre des acces :
    # celui-ci ne regarde que la machine ou il a ete tenu.
    skip = {"gel.log", "preparation.log", "selection.m3u", "acces.db"}
    for entry in source.rglob("*"):
        if not entry.is_file() or entry.name in skip:
            continue
        target = into / entry.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)
        count += 1
        weight += entry.stat().st_size
        if count % 5000 == 0:
            print(f"  {count} fichiers…", flush=True)
    return count, weight


def build(complete: bool = False, source: str = "") -> int:
    for folder in (HERE / "build", HERE / "dist"):
        shutil.rmtree(folder, ignore_errors=True)

    spec = HERE / "Prisme.spec"
    spec.write_text(SPEC.format(
        here=str(HERE).replace("\\", "\\\\"),
        excludes=repr(EXCLUDES),
    ), encoding="utf-8")

    print("Construction…", flush=True)
    code = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)],
        cwd=str(HERE))
    if code != 0:
        return code

    # Un exemple à côté du programme : il suffit de le renommer pour partager
    # les vignettes entre deux ordinateurs.
    (OUT / "prisme.cache.exemple").write_text(
        "X:\\_prisme\n", encoding="utf-8")
    (OUT / "LISEZ-MOI.txt").write_text(LISEZMOI, encoding="utf-8")
    # Ou ce programme cherchera ses mises a jour : sans rien regler, il sait.
    if not source and PUBLICATION.is_file():
        lines = PUBLICATION.read_text(encoding="utf-8").strip().splitlines()
        source = ((lines[1].strip() if len(lines) > 1 else "")
                  or (lines[0].strip() if lines else ""))
    if source:
        (OUT / "mise-a-jour.txt").write_text(source + "\n", encoding="utf-8")
    if not sign(OUT):
        return 3

    cached = 0
    if complete:
        print("Copie du cache…", flush=True)
        cached, cache_weight = gather_cache(OUT / "cache")
        print(f"  {cached} fichier(s), {cache_weight / 1024 / 1024:.0f} Mo")

    name = "Prisme-complet.zip" if complete else "Prisme.zip"
    archive = HERE / "dist" / name
    print("Compression…", flush=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in OUT.rglob("*"):
            if not path.is_file():
                continue
            inside = Path("Prisme") / path.relative_to(OUT)
            # Une vignette est deja compressee : la recomprimer prendrait des
            # minutes pour gagner quelques pour cent.
            how = (zipfile.ZIP_STORED if path.suffix.lower() in (".jpg", ".jpeg")
                   else zipfile.ZIP_DEFLATED)
            zf.write(path, inside, compress_type=how)

    weight = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\ndist/Prisme/      {weight / 1024 / 1024:.0f} Mo")
    print(f"dist/{name}   {archive.stat().st_size / 1024 / 1024:.0f} Mo")
    return 0


# Deux executables pour un seul dossier : celui qu'on lance tous les jours,
# sans console, et son jumeau bavard. Une application sans console qui echoue
# au demarrage ne dit rien du tout — c'est le seul moyen de voir pourquoi.
SPEC = r"""# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

extra_datas, extra_binaries, extra_hidden = collect_all("PySide6.QtMultimedia")

# Le serveur du NAS se publie à partir des sources : nas_publish recopie ces
# fichiers tels quels sur le partage. Empaquetés en bytecode, ils manqueraient.
extra_datas += [(r"{here}\videosorter\\" + name, "videosorter")
                for name in ("__init__.py", "web.py", "access.py", "config.py", "query.py",
                             "textfold.py", "brand_data.py", "demandes.py")]
extra_datas += [(r"{here}\nas\serveur.py", "nas")]

a = Analysis(
    [r"{here}\main.py"],
    pathex=[r"{here}"],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=extra_hidden + ["PySide6.QtMultimediaWidgets", "PySide6.QtSvg", "rapidfuzz",
                                  "segno", "yt_dlp", "truststore"],
    excludes={excludes},
    noarchive=False,
)
pyz = PYZ(a.pure)

fenetre = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Prisme",
    console=False, icon=r"{here}\prisme.ico",
)
console = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Prisme-diagnostic",
    console=True, icon=r"{here}\prisme.ico",
)
coll = COLLECT(
    fenetre, console, a.binaries, a.datas, strip=False, upx=False,
    name="Prisme",
)
"""


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

SI CE PAQUET CONTIENT UN DOSSIER « cache »
Alors tout est déjà là : vignettes, durées, plans, empreintes, notes et
réglages. Prisme s'en sert tout seul, sans rien refabriquer.

Les vignettes sont retrouvées par le chemin de chaque vidéo : le partage
doit donc porter la même lettre de lecteur qu'à la fabrication. Si c'était
X:, ce doit être X: ici aussi.

OÙ TROUVER TOUT ÇA
Le menu ⋯ → « Où sont les vignettes » le dit, avec le compte des fichiers.

SI RIEN NE SE LANCE
Lancez « Prisme-diagnostic.exe » : c'est le même programme, mais avec une
fenêtre noire qui affiche ce qui ne va pas. Le détail est aussi écrit
dans « prisme-erreur.log », à côté du programme.

Trois causes habituelles :
  1. Windows a bloqué les fichiers venus du réseau. Clic droit sur
     l'archive AVANT de la décompresser → Propriétés → cocher
     « Débloquer » → Appliquer.
  2. L'antivirus a mis Prisme.exe en quarantaine. Regardez son journal.
  3. Un composant Windows manque. La fenêtre de diagnostic le nomme.

LES RACCOURCIS
Le menu ⋯ → « Raccourcis et recherche ».
"""


# -- publier une version ---------------------------------------------------
# Ou deposer les versions, et ou les PC les lisent : `publication.txt`, a cote
# de ce script. Ligne 1, le dossier ou deposer ; ligne 2, facultative,
# l'adresse que les PC lisent si elle differe (un stockage en ligne, plus
# tard). Sans fichier : `--vers <dossier>`.
PUBLICATION = HERE / "publication.txt"
# La signature Windows, des qu'il y aura un certificat : `signature.json`,
# a cote de ce script, {"signtool": "C:/.../signtool.exe", "arguments": [...]}
# -- les arguments de `signtool sign` que le fournisseur indique. Sans fichier,
# rien n'est signe ; avec, chaque .exe l'est avant la mise en archive, et un
# Prisme signe n'accepte plus que des mises a jour signees du meme editeur.
SIGNATURE = HERE / "signature.json"


def sign(folder: Path) -> bool:
    if not SIGNATURE.is_file():
        print("Pas de signature.json : programme non signé.")
        return True
    import json
    told = json.loads(SIGNATURE.read_text(encoding="utf-8"))
    for exe in sorted(folder.glob("*.exe")):
        code = subprocess.call([told["signtool"], "sign", *told.get("arguments", []),
                                str(exe)])
        if code != 0:
            print(f"Signature impossible : {exe.name}")
            return False
    print("Programme signé.")
    return True


def set_version(version: str) -> None:
    import re
    place = HERE / "videosorter" / "__init__.py"
    text = place.read_text(encoding="utf-8")
    text = re.sub(r'__version__ = "[^"]*"', f'__version__ = "{version}"', text)
    place.write_text(text, encoding="utf-8")


def publish(version: str, notes: list, into: str = "") -> int:
    """Construit la version, la depose, puis l'annonce (version.json en
    dernier : un PC qui regarde pendant la copie ne voit rien a moitie)."""
    import hashlib
    import json
    import re
    from datetime import date

    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        print(f"Numéro de version invalide : {version} (attendu : 1.2.0)")
        return 2
    lines = (PUBLICATION.read_text(encoding="utf-8").strip().splitlines()
             if PUBLICATION.is_file() else [])
    target = Path(into or (lines[0].strip() if lines else ""))
    readers = (lines[1].strip() if len(lines) > 1 else "") or str(target)
    if not str(target) or str(target) == ".":
        print("Où publier ? Mettez le dossier dans publication.txt, ou --vers <dossier>.")
        return 2
    target.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(HERE))
    from videosorter.update import MANIFEST, newer
    try:
        before = json.loads((target / MANIFEST).read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        before = ""
    if before and not newer(version, before):
        print(f"La version publiée est déjà la {before} : choisissez un numéro plus grand.")
        return 2

    set_version(version)
    code = build(source=readers)
    if code != 0:
        return code
    archive = HERE / "dist" / "Prisme.zip"
    name = f"Prisme-{version}.zip"
    print(f"Dépôt dans {target}…", flush=True)
    shutil.copyfile(archive, target / (name + ".partiel"))
    (target / (name + ".partiel")).replace(target / name)
    digest = hashlib.sha256()
    with open(target / name, "rb") as src:
        for piece in iter(lambda: src.read(1 << 20), b""):
            digest.update(piece)
    told = {"version": version, "date": date.today().isoformat(),
            "notes": [n for n in notes if n.strip()], "fichier": name,
            "taille": (target / name).stat().st_size, "sha256": digest.hexdigest()}
    (target / (MANIFEST + ".partiel")).write_text(
        json.dumps(told, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / (MANIFEST + ".partiel")).replace(target / MANIFEST)
    # Les deux dernieres archives suffisent : un PC en retard passe
    # directement a la derniere.
    olds = sorted((p for p in target.glob("Prisme-*.zip") if p.name != name),
                  key=lambda p: p.stat().st_mtime)
    for old in olds[:-1]:
        old.unlink(missing_ok=True)
    print(f"\nPrisme {version} publié : les PC le verront dans les six heures, "
          "ou tout de suite par le menu : Aide, Rechercher une mise à jour.")
    return 0


def _option(name: str) -> list:
    found, args = [], sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            found.append(args[i + 1])
    return found


if __name__ == "__main__":
    if "--publier" in sys.argv:
        chosen = _option("--publier")
        sys.exit(publish(chosen[0] if chosen else "", _option("--notes"),
                         (_option("--vers") or [""])[0]))
    sys.exit(build("--complet" in sys.argv))
