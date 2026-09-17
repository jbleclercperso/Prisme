# VideoSorter

Trieur de dossiers vidéo pour Windows. On choisit un dossier racine, et l'application
présente ce qu'il contient **un élément à la fois** : infos en haut, dix aperçus au
milieu, commandes en bas. Une touche = une décision, et l'élément suivant s'affiche
aussitôt.

## Les deux modes

L'application regarde ce que contient la racine et choisit toute seule :

| Contenu de la racine | Mode | Ce qui défile |
|---|---|---|
| Des sous-dossiers | **dossiers** | Un sous-dossier à la fois, avec 10 aperçus pris dans ses vidéos (y compris celles des sous-dossiers imbriqués) |
| Uniquement des vidéos | **fichiers** | Une vidéo à la fois, lue en grand, avec une pellicule de 10 instants |

Le bouton **Mode** force l'autre mode si la détection ne correspond pas à l'envie du moment.

## Les aperçus

- **Dossier contenant beaucoup de vidéos** : les 10 cases montrent 10 vidéos
  différentes, échantillonnées sur toute la liste (pas les 10 premières).
- **Dossier contenant peu de vidéos** : les 10 cases se répartissent sur ces
  vidéos, à des instants échelonnés dans chacune.
- **Survol d'une case** : la vidéo démarre à cet instant et tourne en boucle sur
  10 secondes. Le son est coupé par défaut — `Ctrl+M`, ou le bouton *Son* dans
  l'entête, qui affiche l'état courant. La molette avance ou recule dans l'extrait.
- **Pastille en haut à droite** : la durée totale de la vidéo. Pendant un
  déplacement à la molette, elle affiche la position et la durée.
- **En bas à gauche** : la résolution (`720p`, `1080p`, `4K`…) et le nom du
  fichier. Les hauteurs non standard sont ramenées à l'appellation la plus
  proche, un 352p s'affiche donc `360p`.
- Un double-clic sur une case ouvre l'explorateur sur ce fichier.

Les vignettes des deux éléments suivants sont fabriquées à l'avance, donc
l'enchaînement reste immédiat. Elles sont mises en cache : repasser sur un dossier
déjà vu est instantané.

## Commandes

| Touche | Action |
|---|---|
| `Suppr` / `Retour arrière` | Envoie à la corbeille, puis passe au suivant |
| `Espace` | Passer (je ne sais pas encore), sans rien toucher |
| `1`…`0`, `a`…`z` | Déplace vers la destination configurée, puis passe au suivant |
| `←` / `→` | Revenir en arrière / avancer sans décider |
| **molette** | Avance ou recule dans la vidéo survolée (`Ctrl` pour des sauts six fois plus grands) |
| `Ctrl+Z` | Annuler la dernière action |
| `Ctrl+F` | Aller au champ de filtre |
| `Ctrl+T` | Afficher ou masquer l'arborescence |
| `Ctrl+M` | Couper ou remettre le son |
| `Ctrl+O` | Ouvrir l'élément courant dans l'explorateur |
| `Ctrl+D` | Ouvrir la configuration des destinations |
| `Entrée` | Pause / reprise (mode fichier) |
| `Échap` | Quitter le tri et revenir à l'écran d'accueil |

Chaque vignette de la barre du bas est aussi **un bouton** : un clic déclenche
exactement la même action que sa touche, et enchaîne sur l'élément suivant. Le
clavier pour aller vite, la souris quand on n'a pas la main dessus.

**Toutes** les touches simples sont libres pour vos destinations : les commandes
de l'application sont sur `Ctrl` ou sur des touches de navigation. Cela fait
36 destinations possibles, attribuées d'office dans l'ordre `1`…`0` puis
`a z e r t y…` (ordre du clavier AZERTY). La barre de commandes passe à la ligne,
elle ne déborde jamais.

## Filtrer

Deux champs au-dessus de la fiche, `Ctrl+F` pour y aller :

- **contient…** — ne garder que les noms qui comportent l'un des termes ;
- **exclure…** — écarter les noms qui en comportent un.

Plusieurs termes se séparent par des virgules, la casse est ignorée, et les deux
champs se combinent. Taper `+` dans « exclure » met de côté tous les dossiers
préfixés. `Échap` ou `Entrée` rend la main aux raccourcis de tri, *Effacer*
remet tout.

Le filtre masque sans rien perdre : le compteur indique combien d'éléments sont
écartés, et les termes sont conservés d'une session à l'autre — c'est pourquoi
ils restent visibles en permanence dans la barre.

## Mode arborescence

`Ctrl+T` (ou le bouton *Arborescence*) affiche à gauche l'arbre de vos dossiers
de destination, volontairement discret, pendant que le lecteur garde la droite.

**Un clic sur un dossier y envoie l'élément courant, sans confirmation**, et
l'élément suivant s'affiche. Utile quand les destinations sont trop nombreuses
ou trop imbriquées pour tenir sur des touches.

Le lien *changer…* choisit la racine de l'arbre ; par défaut c'est le dossier
parent de la racine triée. Les niveaux ne sont lus que lorsqu'on les déplie,
une arborescence profonde ne coûte donc rien.

## Destinations

Bouton **Destinations…** (ou `Ctrl+D`). Deux façons de les remplir :

- **Ajouter un dossier…** pour en choisir un à la main ;
- **Ajouter tous les sous-dossiers de…** pour peupler la liste d'un coup — par
  exemple un dossier `Archives` contenant `2019`, `2020`, `2021`… chacun reçoit
  automatiquement une touche.

La touche et le libellé se modifient en double-cliquant la cellule.

## Sécurités

- **Suppression → corbeille Windows** par défaut : rien n'est effacé pour de bon.
  `delete_mode` accepte aussi `local_trash` (un dossier `_TRASH` que vous videz
  vous-même, annulable par `Ctrl+Z`) et `permanent`.
- **Pas d'écrasement** : si un élément du même nom existe déjà à destination, le
  nouveau est suffixé `(2)`, `(3)`…
- **`Ctrl+Z`** remet en place le dernier déplacement, et la dernière suppression
  quand le mode le permet. Une suppression partie à la corbeille Windows se
  restaure depuis l'explorateur ; l'application le rappelle plutôt que de
  prétendre l'annuler.
- Le lecteur et les processus ffmpeg relâchent les fichiers avant chaque
  opération : sans cela, Windows refuserait de renommer un dossier dont une
  vidéo est encore ouverte.
- **Les transferts se font en tâche de fond.** Dès la touche pressée, l'élément
  suivant s'affiche ; la copie se poursuit derrière, et un indicateur `⟳` dans
  l'entête compte ce qui reste en vol. Les opérations s'exécutent une par une,
  dans l'ordre des décisions, pour que l'annulation reste cohérente. `Ctrl+Z`
  attend poliment la fin d'un transfert en cours plutôt que de défaire autre
  chose. À la fermeture, l'application patiente le temps que tout soit arrivé :
  aucun dossier n'est laissé à moitié copié.

## Installation et lancement

Prérequis : **Python 3.10+** et **ffmpeg/ffprobe** accessibles (déjà installés ici
via `winget install Gyan.FFmpeg` ; l'application les cherche dans le `PATH` puis
dans le dossier winget).

```bash
python -m pip install -r requirements.txt
```

Puis double-cliquez `VideoSorter.bat`, ou :

```bash
python main.py
```

## Configuration

`%LOCALAPPDATA%\VideoSorter\config.json` — destinations, racines récentes, et
quelques réglages : `preview_seconds` (durée de la boucle au survol),
`thumb_count` (nombre d'aperçus), `thumb_width` (finesse des vignettes),
`delete_mode`, `scroll_seconds` (pas de la molette), `tree_root`,
`filter_include`, `filter_exclude`, `skip_hidden`.
Le cache de vignettes est à côté, dans `thumbs\`.

## Tests

```bash
python tests/test_app.py
```

Fabrique une arborescence avec de vraies vidéos (ffmpeg), puis vérifie l'analyse,
la répartition des aperçus, les déplacements, les suppressions, les annulations,
les collisions de noms, les deux modes, la molette, l'arborescence, la barre de
commandes à la souris, le filtre par nom, les métadonnées des vignettes et les
transferts en tâche de fond — interface comprise, en mode sans affichage.
108 vérifications.

```bash
python tests/test_recycle_and_render.py <dossier_fixture> <sortie.png>
```

Vérifie la corbeille Windows réelle et exporte une capture de la fenêtre.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `main.py` | Démarrage |
| `videosorter/config.py` | Configuration persistante, emplacements |
| `videosorter/scan.py` | Analyse de la racine, statistiques des dossiers |
| `videosorter/media.py` | ffmpeg/ffprobe, cache, vignettes en arrière-plan |
| `videosorter/actions.py` | Déplacer, supprimer, annuler |
| `videosorter/transfer.py` | File de transferts en tâche de fond |
| `videosorter/tree.py` | Panneau d'arborescence |
| `videosorter/widgets.py` | Grille d'aperçus, lecteur, barre de commandes, réglages |
| `videosorter/window.py` | Fenêtre principale, enchaînement, raccourcis |
