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

Le bouton **Mode** force l'autre mode si la détection ne correspond pas à l'envie
du moment.

### Entrer dans un dossier

Un dossier trop mélangé pour recevoir une seule étiquette ? **Entrer** (ou
`Ctrl+↓`) en fait la nouvelle racine et bascule sur ses vidéos, à trier une par
une. **Remonter** (`Ctrl+↑`, ou `Échap`) revient au dossier parent, **et
repositionne le curseur sur le dossier d'où l'on était parti** — le parcours
reprend là où il s'était arrêté.

On peut descendre de plusieurs niveaux : l'entête indique la profondeur. Si le
dossier ouvert ne contient pas de vidéo directement mais d'autres dossiers, on y
entre en mode dossiers, et l'on continue à descendre.

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

Dans la fiche du haut, la durée de la vidéo est accolée à son titre, et la ligne
d'informations — poids, résolution, dimensions, codec, date — est dimensionnée
pour se lire d'un coup d'œil. Juste en dessous figure **la chaîne des dossiers
depuis la racine du tri** (`Archives › 2019 › Vacances`) : en mode fichier, le
seul nom du dossier parent ne suffit pas à se situer, plusieurs dossiers portant
souvent le même nom à des endroits différents.
- Un double-clic sur une case ouvre l'explorateur sur ce fichier.

Les vignettes des deux éléments suivants sont fabriquées à l'avance, donc
l'enchaînement reste immédiat. Elles sont mises en cache : repasser sur un dossier
déjà vu est instantané.

## Commandes

| Touche | Action |
|---|---|
| `Suppr` / `Retour arrière` | Envoie à la corbeille, puis passe au suivant |
| `Espace` | Passer (je ne sais pas encore), sans rien toucher |
| `0`…`5` | Note l'élément de 0 à 5 étoiles |
| `6`…`9`, `a`…`z` | Déplace vers la destination configurée, puis passe au suivant |
| `←` / `→` | Revenir en arrière / avancer sans décider |
| **molette** | Avance ou recule dans la vidéo survolée |
| **clic gauche maintenu + molette** | Zoome sur l'image, centré là où pointe la souris (×1 à ×6) — `Ctrl+molette` fait de même |
| **clic droit** | Ramène l'image à sa taille normale |
| `F` ou double-clic | Ouvre la vidéo en grand, dans l'application |
| `Ctrl+Z` | Annuler la dernière action |
| `Ctrl+F` | Aller au champ de filtre |
| `Ctrl+P` | Basculer entre la fiche unique et la planche |
| `Ctrl+H` | Se placer sur un élément au hasard |
| `Ctrl+←/→` | Page d'aperçus précédente / suivante |
| `Ctrl+B` | Ouvrir la corbeille de session |
| `Alt+←` | Revenir à l'endroit précédemment visité |
| `Ctrl+R` | Réanalyser tout le disque, sans se fier au cache |
| `Ctrl+↓` | Entrer dans le dossier affiché pour en trier les vidéos |
| `Ctrl+↑` | Remonter au dossier parent |
| `Ctrl+T` | Afficher ou masquer l'arborescence |
| `Ctrl+M` | Couper ou remettre le son |
| `Ctrl+O` | Ouvrir l'élément courant dans l'explorateur |
| `Ctrl+D` | Ouvrir la configuration des destinations |
| `Entrée` | Pause / reprise (mode fichier) |
| `Échap` | Remonter d'un niveau, ou quitter le tri si l'on est à la racine |

Chaque vignette de la barre du bas est aussi **un bouton** : un clic déclenche
exactement la même action que sa touche, et enchaîne sur l'élément suivant. Le
clavier pour aller vite, la souris quand on n'a pas la main dessus.

Les commandes de l'application sont sur `Ctrl` ou sur des touches de
navigation, ce qui laisse 30 destinations possibles, attribuées d'office dans l'ordre `6 7 8 9` puis
`a z e r t y…` (ordre du clavier AZERTY) : **les chiffres 0 à 5 sont réservés à
la notation**, une main note pendant que l'autre range. La barre de commandes passe à la ligne,
elle ne déborde jamais.

## Vitesse de l'analyse

Une collection ne change presque pas d'un lancement à l'autre. L'application
part donc de ce qu'elle savait, et ne relit le disque qu'ensuite, en tâche de
fond.

**À l'ouverture d'une racine déjà vue, rien n'est demandé au disque** : la liste
est à l'écran tout de suite, telle qu'au dernier passage. Une relecture démarre
derrière, en priorité basse, compare les dates, et ne publie que les
différences — un dossier qui n'a pas bougé ne coûte ni parcours, ni signal, ni
repeinte. Sur une collection stable, elle ne publie strictement rien.

Le premier inventaire, lui, parcourt les dossiers **huit de front** : il attend
le réseau, pas le processeur.

Ce qui déclenche une réanalyse : la date de modification du dossier. Windows la
met à jour dès qu'une entrée y est ajoutée, retirée ou renommée. Plus profond,
la modification passe inaperçue — **`Ctrl+R` force une relecture complète**
quand vous avez remanié une arborescence à la main.

Tout ce qui est appris est écrit **au fil de l'eau**, dans
`%LOCALAPPDATA%\VideoSorter\index.db`. C'est la différence avec le cache
précédent, un fichier JSON relu et réécrit en entier, donc sauvegardé une seule
fois, à la toute fin d'une analyse complète : fermer la fenêtre ou entrer dans
un dossier avant ce moment jetait tout, et chaque lancement repayait le parcours
entier. Ici, une analyse interrompue garde ce qu'elle a appris, et la
composition de la racine est notée avant même la vérification.

L'index retient aussi les sondages ffprobe, repris de l'ancien cache au premier
lancement. `use_scan_cache: false` le désactive entièrement.

## Vue planche

`Ctrl+P`, ou le bouton **Planche**. Les éléments passent en cartes. Sous chaque
image, **une seule ligne discrète**, la même que sous un aperçu : la durée en
pastille en haut à droite, puis `1080p · nom du fichier` pour une vidéo,
`12 vidéos · nom du dossier` pour un dossier. Survoler une carte la lit en
boucle, cliquer l'ouvre.

Ce n'est pas un second logiciel mais une autre présentation du même contenu :
même racine, même filtre, même arborescence, mêmes touches de destination.

## Les quatre onglets

Un onglet est un point de vue sur **toute** la collection, jamais sur l'endroit
où l'on se trouve : en changer **ramène à la racine du tri**.

| Onglet | Ce qu'il montre |
|---|---|
| **Dossiers** | Tous les dossiers de la racine, en planche. Les dossiers de tête `+` en sont exclus : ce sont les destinations, pas ce qu'on trie. |
| **Vidéos** | Toutes les vidéos de l'arborescence, à plat, **en ordre aléatoire** — sans quoi les mêmes reviendraient toujours en tête. |
| **Édition** | Un élément à la fois, à partir du **premier dossier à trier**. |
| **Mots-clés** | Les vidéos réunies en catégories d'après les mots de leurs noms. |

Entrer dans un dossier ne change plus l'onglet ouvert : seul un clic sur un
onglet en change.

## Mots-clés

Les mots qui reviennent le plus dans vos noms de fichiers deviennent des
catégories, sans rien saisir (*Mots fréquents*) — ou bien les vôtres
(*Mes mots-clés*, `Ctrl+D` puis *Mots-clés automatiques…*).

**Une vidéo ne va que dans une seule catégorie** : celle du mot qui la décrit le
mieux, le plus fréquent de ceux que son nom porte. Sans cela les mots fréquents
se recouvraient presque entièrement, et l'on ouvrait dix catégories pour y
retrouver les dix mêmes vidéos. Les catégories sortent de la plus fournie à la
plus rare.

La comparaison ignore casse et accents : `Été`, `ete` et `ETE` tombent dans la
même.

## Deux hasards

Deux boutons, côte à côte dans l'entête, parce que ce sont deux portées
différentes :

- **⚄ Aléatoire** (`Ctrl+H`) pioche dans **toute la collection** analysée ;
- **⚄ Ici** pioche dans le **seul élément affiché**.

Un **sélecteur de densité** choisit de 2 à 8 cartes par rangée.

Les filtres chiffrés accompagnent la planche : durée *plus longue que* / *plus
courte que*, résolution *au moins* / *au plus*, note *au moins*. La durée d'un
dossier n'est pas mesurée mais **extrapolée** à partir des vidéos déjà sondées
pour les aperçus — sonder une collection entière coûterait des heures sur un
NAS. Un élément dont on ne sait rien passe le filtre plutôt que de disparaître
sans explication, et l'estimation se précise à mesure que vous parcourez.

**◂ Précédent** (`Alt+←`) revient à l'endroit visité juste avant, y compris
après un déplacement latéral — à distinguer de **Remonter**, qui monte d'un
niveau dans l'arborescence.

## Lire en grand

Un double-clic sur un aperçu ou une carte, ou la touche `F`, ouvre la vidéo dans
un **lecteur intégré** qui recouvre la page : image en grand, temps restant,
avancement, zoom. `Échap` ou un nouveau double-clic referme.

Rien n'est confié au lecteur du système : on reste dans le tri, et le fichier
est relâché à la fermeture — sans quoi Windows le garderait verrouillé et vous
ne pourriez pas le ranger juste après l'avoir regardé.

Partout où une vidéo se lit — aperçu survolé, carte survolée, lecteur plein
cadre, lecteur intégré — le **temps restant** s'affiche en haut à droite de
l'image, et un trait bleu marque l'avancement.

## Notation

Cinq étoiles en bas à droite de la fiche, et sur chaque carte de la planche.
Survoler montre la note qui serait posée, cliquer la pose, et **rappuyer sur la
même valeur l'efface** — pas besoin d'un bouton de remise à zéro. Au clavier :
`Ctrl+1` à `Ctrl+5`, `Ctrl+0` pour effacer.

Les notes vivent dans `%LOCALAPPDATA%\VideoSorter
atings.json` : rien n'est
écrit dans vos dossiers. Elles **suivent l'élément quand il est déplacé**, sans
quoi ranger une vidéo notée lui aurait fait perdre sa note.

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

`Ctrl+T` (ou le bouton *Arborescence*) affiche à gauche l'arbre de vos
destinations, pendant que le lecteur garde la droite.

**Seuls les dossiers de tête — ceux préfixés `+` — y figurent** : ce sont les
seules destinations. Montrer toute l'arborescence obligeait à les chercher
parmi des centaines, et invitait à la faute.

Un clic fait l'une de deux choses opposées : **y envoyer l'élément courant**,
sans confirmation, ou **s'y rendre**. Chaque geste porte donc sa couleur — ambre
pour *envoyer vers*, qui déplace des fichiers, bleu pour *aller dans*, qui ne
touche à rien — sur le cadre du panneau, son bouton, les icônes de dossier et le
survol. Le panneau prend le geste de l'onglet ouvert : on range en **Édition**,
on se promène en **Vidéos**. Le bouton en haut bascule à tout moment.

Le lien *changer…* choisit la racine de l'arbre ; par défaut c'est le dossier
parent de la racine triée. Les niveaux ne sont lus que lorsqu'on les déplie.

## Destinations

Bouton **Destinations…** (ou `Ctrl+D`). Deux façons de les remplir :

- **Ajouter des dossiers…** — `Ctrl` ou `Maj` pour en sélectionner plusieurs
  d'un coup, chacun devenant un raccourci. Le sélecteur natif de Windows ne sait
  choisir qu'un dossier à la fois, c'est donc celui de Qt qui s'ouvre.
- **Ajouter tous les sous-dossiers de…** pour peupler la liste d'un coup — par
  exemple un dossier `Archives` contenant `2019`, `2020`, `2021`…

La touche et le libellé se modifient en double-cliquant la cellule. **Glissez une
ligne par sa poignée** pour changer l'ordre : les boutons de la barre du bas
suivent celui de la liste. **Renuméroter** réattribue les touches dans cet ordre,
**Réinitialiser** vide la liste (sans toucher aux dossiers).

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
`filter_include`, `filter_exclude`, `skip_hidden`, `use_scan_cache`.
L'index est à côté, dans `index.db`, et le cache de vignettes dans `thumbs\`.

## Tests

```bash
python tests/test_app.py
```

Fabrique une arborescence avec de vraies vidéos (ffmpeg), puis vérifie l'analyse,
la répartition des aperçus, les déplacements, les suppressions, les annulations,
les collisions de noms, les deux modes, la molette, l'arborescence, la barre de
commandes à la souris, le filtre par nom, les métadonnées des vignettes, la
descente dans les sous-dossiers, le cache d'analyse, la gestion des destinations,
la corbeille de session, la vue planche, la notation, le zoom et les transferts
la densité de la planche, les filtres chiffrés, l'historique de navigation et
les transferts en tâche de fond — interface comprise, en mode sans affichage.
245 vérifications.

La suite porte un garde-fou : passé un délai, elle imprime la pile plutôt que
d'attendre indéfiniment. Une interface graphique arrêtée sur une boîte de
dialogue modale ne le signale pas autrement.

```bash
python tests/test_network.py
```

Vérifie la reconnaissance d'un stockage réseau et le parallélisme qui en découle.

```bash
python tests/bench_scan.py
```

Mesure le coût de l'analyse sur une arborescence synthétique, avec et sans cache.

```bash
python tests/test_recycle_and_render.py <dossier_fixture> <sortie.png>
```

Vérifie la corbeille Windows réelle et exporte une capture de la fenêtre.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `main.py` | Démarrage |
| `videosorter/config.py` | Configuration persistante, emplacements |
| `videosorter/scan.py` | Inventaire de la racine, et sa relecture en tâche de fond |
| `videosorter/index.py` | Index persistant : ce qu'on sait déjà du disque |
| `videosorter/board.py` | Vue planche : les éléments en cartes |
| `videosorter/ratings.py` | Notes de 0 à 5 étoiles |
| `videosorter/trash.py` | Corbeille de session |
| `videosorter/media.py` | ffmpeg/ffprobe, cache, vignettes en arrière-plan |
| `videosorter/actions.py` | Déplacer, supprimer, annuler |
| `videosorter/transfer.py` | File de transferts en tâche de fond |
| `videosorter/tree.py` | Panneau d'arborescence |
| `videosorter/widgets.py` | Grille d'aperçus, lecteur, barre de commandes, réglages |
| `videosorter/window.py` | Fenêtre principale, enchaînement, raccourcis |
