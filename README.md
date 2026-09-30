# VideoSorter

Trieur de dossiers vidéo pour Windows. On choisit un dossier racine, et l'application
présente ce qu'il contient **un élément à la fois** : infos en haut, dix aperçus au
milieu, commandes en bas. Une touche = une décision, et l'élément suivant s'affiche
aussitôt.

Au lancement, Prisme rouvre toujours la dernière racine des **vidéos**, sur
l'onglet **Dossiers**, sans filtre : ni « Non vus », ni format, ni recherche
d'une séance précédente.

## Photos

Le même tri, pour des collections de photos : JPEG, PNG, WebP, GIF, BMP, TIFF
et HEIC. Le sélecteur **Vidéos | Photos**, en haut à gauche, bascule tout
Prisme et ouvre la dernière racine de l'autre collection :

- l'analyse ne voit plus que les images ;
- l'onglet « Vidéos » devient « Photos » ;
- les tris par durée et par taille disparaissent.

Les vignettes et les dimensions sont lues par Qt, sans ffmpeg, et redressées
selon l'orientation EXIF. Seul le HEIC, que Qt ne sait pas lire, passe par
ffmpeg.

- **La fiche** affiche la photo en grand, lue hors du fil de l'interface. La
  molette y zoome ; le clic droit ramène à la taille normale et propose les
  destinations.
- **Le diaporama** : ⏯, `Entrée` ou un clic sur la photo le lance et
  l'arrête. Une photo toutes les 4 s (`slideshow_seconds`), dans le dossier de
  la photo si « rester dans ce dossier » est cochée.
- **Le Mur** : 2 à 10 photos côte à côte, chacune en diaporama dès
  l'ouverture. ⏯ ou un clic arrête le panneau, la molette zoome, et le clic
  droit range.
- **Survoler un dossier** sur la planche fait défiler une dizaine de ses
  photos, tirées au hasard.
- **Verticales / Horizontales** : les photos dont on ignore encore le format
  sont mesurées en tâche de fond, par lots (seul l'en-tête est lu), et la liste
  se complète à mesure.

Destinations, notes, touches et corbeille marchent comme pour les vidéos.

**Les deux collections vivent à part.** Chacune a sa racine, ses
destinations, ses filtres, ses mots-clés, ses notes (`ratings-photos.json`) et
son index (`index-photos.db`). On ne range pas une photo dans le dossier des
films. Le cache des vignettes, lui, est commun.

## Lecteur flottant

La vidéo continue par-dessus tout le reste, dans une fenêtre toujours au
premier plan. On l'ouvre de deux façons :

- à la demande : le bouton ⧉ du bandeau de la fiche, ou `Ctrl+L` ;
- tout seul, quand Prisme est réduit ou recouvert par une autre application
  (un dossier, une page web) pendant qu'une vidéo joue. Le réglage est
  « Lecteur flottant automatique », dans le menu ⋯ › Affichage. Ouvert tout
  seul, le lecteur ne prend pas le clavier.

La fenêtre est sans cadre, et toute la place va à l'image :

- elle prend les proportions de chaque vidéo, sans bandes noires ;
- le titre, discret, est posé en haut à gauche de l'image ; on l'attrape
  pour déplacer la fenêtre ;
- on l'agrandit par ses bords ;
- le double-clic la passe en plein écran.

Elle reprend à l'instant où l'on en était :

- le clic met en pause, la molette avance, clic maintenu + molette zoome ;
- le temps restant et le trait d'avancement sont affichés ;
- le bandeau ◂ ⏯ ▸ ⌸ ⛶ ↩ paraît au survol ;
- le clic droit ouvre les destinations autour du pointeur, comme sur la
  fiche ;
- les touches de tri, de note et Suppr marchent depuis la fenêtre flottante.

Revenir dans Prisme (↩, `Échap`, ou simplement cliquer dans sa fenêtre s'il
s'était ouvert tout seul) le referme, et la fiche reprend au même instant.

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
d'informations — poids, nombre de vidéos, date — est dimensionnée pour se lire
d'un coup d'œil.

**Le fil d'Ariane mène jusqu'au dossier de l'élément affiché**, et non plus
seulement jusqu'à la racine regardée : en vue à plat, où toutes les vidéos de la
collection se côtoient, le seul nom du fichier ne dit plus d'où il sort. Ses
segments restent cliquables, il sert donc aussi à y retourner. Sur une carte de
vidéo, le nom du dossier figure sous celui du fichier, pour la même raison.
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
| clic, ou `F` | Descend d'un étage : dans le dossier, ou dans la vidéo |
| `Ctrl+Z` | Annuler la dernière action |
| `Ctrl+F` | Aller au champ de filtre |
| `Ctrl+P` | Basculer entre les vignettes et la fiche |
| `Ctrl+H` | Se placer sur un élément au hasard |
| `Ctrl+←/→` | Page d'aperçus précédente / suivante |
| `Ctrl+B` | Ouvrir la corbeille de session |
| `Alt+←` | Revenir à l'endroit précédemment visité |
| `Ctrl+R` | Réanalyser tout le disque, sans se fier au cache |
| `Ctrl+↓` | Entrer dans le dossier affiché pour en trier les vidéos |
| `Ctrl+↑`, ou le bouton `↑` | Remonter au dossier parent, d'où qu'on soit |
| `Ctrl+T` | Afficher ou masquer l'arborescence |
| `Ctrl+M` | Couper ou remettre le son |
| `Ctrl+L` | Lecteur flottant : ouvrir, ou revenir dans Prisme |
| `Entrée` (photo) | Lancer ou arrêter le diaporama |
| `Ctrl+O` | Ouvrir l'élément courant dans l'explorateur |
| `Ctrl+D` | Ouvrir la configuration des destinations |
| `Entrée` | Pause / reprise (mode fichier) |
| `Échap` | Revenir aux vignettes, puis remonter d'un niveau |

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
`%LOCALAPPDATA%\VideoSorter\index.db`. Le fichier est **vérifié à l'ouverture**
et refait s'il est abîmé, avec un bandeau qui le dit : un index illisible ne se
signalait pas, chaque lecture répondait simplement « rien de connu », et l'on
réanalysait tout à chaque lancement sans qu'aucun message ne l'explique. C'est la différence avec le cache
précédent, un fichier JSON relu et réécrit en entier, donc sauvegardé une seule
fois, à la toute fin d'une analyse complète : fermer la fenêtre ou entrer dans
un dossier avant ce moment jetait tout, et chaque lancement repayait le parcours
entier. Ici, une analyse interrompue garde ce qu'elle a appris, et la
composition de la racine est notée avant même la vérification.

Le bouton **⟲ Analyser**, dans l'entête, dit à tout moment où l'on en est :
au repos il propose de relire, en marche il compte (`⟳ 461 / 649`) et son
infobulle nomme le dossier en cours — une longue lecture réseau ne se confond
donc plus avec un blocage. Un clic pendant l'analyse l'arrête, sans rien perdre
de ce qui a été lu. À la fin, un bandeau annonce la durée et ce qui a changé.

L'index retient aussi les sondages ffprobe, repris de l'ancien cache au premier
lancement. `use_scan_cache: false` le désactive entièrement.

## Vitesse des aperçus

Une vignette se paie en allers-retours sur le réseau, et c'est ce qui reste le
plus cher une fois l'analyse en cache. Trois choix, mesurés sur le NAS :

- **le sondage ne précède plus l'image.** Connaître la durée d'une vidéo coûte
  un `ffprobe` de 0,36 s, soit plus que l'extraction elle-même. Pour un dossier,
  où chaque case montre une vidéo différente, l'image part sans rien demander ;
  la durée et la résolution la rejoignent après, en tâche de fond. Seule la
  pellicule d'une vidéo seule a besoin de sa durée — et un sondage y sert dix
  images.
- **on cherche l'image tout au début.** C'est le réglage le plus cher de
  l'application : un saut oblige à faire venir ce qu'on saute, et la différence
  se multiplie par quarante à chaque page. Mesuré sur le partage — **0,48 s par
  image au tout début, 1,00 s à six secondes, 1,94 s à soixante**, cette
  dernière échouant en plus sur les vidéos trop courtes, ce qui oblige à
  recommencer. Le défaut est à deux secondes : assez pour dépasser l'image noire
  d'ouverture, mais ffmpeg y rejoint presque toujours la même image-clé qu'à
  zéro, donc sans rien faire venir de plus. `preview_start` déplace ce curseur.
- **huit extractions de front, pas davantage.** Elles attendent la ligne plus
  qu'elles n'occupent le processeur, mais au-delà le partage se met à piétiner :
  1,49 s par image à huit, 1,58 s à quatre, et **3,31 s à seize** — deux fois
  pire. Le débit d'un partage ne s'additionne pas indéfiniment, il s'écroule.

Borner l'analyse d'en-tête de ffmpeg (`-probesize`) a été essayé et **écarté** :
trois fois plus rapide sur certains fichiers, deux fois plus lent sur d'autres,
où ffmpeg doit relire après avoir échoué dans la borne.

### Les aperçus se préparent d'avance

C'est le choix qui change tout. Mesuré sur le partage : une page de 40 cartes
demande **33 s** la première fois, et **0,07 s** la seconde. Et l'on ne peut pas
y aller plus vite en lançant davantage d'extractions : au-delà de huit, le
partage piétine et le temps par image double. Rien ne peut donc raccourcir
cette première fois **au moment où on la regarde**.

Dès que l'analyse se termine, une **récolte** fabrique donc la vignette de
chaque élément, en arrière-plan, deux extractions à la fois. Elle s'efface dès
que vous demandez quelque chose, reprend deux secondes plus tard, saute ce qui
est déjà sur le disque, et reprend où elle s'était arrêtée au lancement suivant.
Le bouton d'état l'annonce (`◷ aperçus 120 / 649`) et un clic l'arrête.

Elle sert **tous les onglets**, y compris Vidéos et Mots-clés, qui se
construisent en mémoire sans passer par une analyse : c'est elle seule qui la
déclenchait, si bien qu'aucune vignette n'y était jamais préparée d'avance et
que chaque page se fabriquait sous les yeux.

**Elle commence par ce que vous regardez.** Changer de page la fait sauter à
cette page : elle parcourait sinon la collection dans l'ordre, et arrivé à la
page cinq on attendait ses aperçus pendant qu'elle préparait tranquillement la
page une.

Une fois passée, la navigation ne coûte plus rien.

Les vignettes obtenues restent sur le disque, indexées par fichier, date et
instant : revenir sur une page déjà vue est instantané. `thumb_count` (10 par
défaut) est le levier restant si l'on veut moins d'aperçus par fiche.

## Vue planche

`Ctrl+P`, ou le bouton **Planche**. Les éléments passent en cartes. Sous chaque
image, **une seule ligne discrète**, la même que sous un aperçu :
`1080p · nom du fichier` pour une vidéo, le seul nom pour un dossier.

La pastille en haut à droite porte la durée d'une vidéo, et **le nombre de
vidéos d'un dossier** — elle y affichait la durée de l'unique vidéo dont l'image
sert de vignette, ce qui ne disait rien du dossier.

Survoler une carte la lit en boucle, cliquer descend dedans.

Ce n'est pas un second logiciel mais une autre présentation du même contenu :
même racine, même filtre, même arborescence, mêmes touches de destination.

## Les quatre onglets

Un onglet est un point de vue sur **toute** la collection, jamais sur l'endroit
où l'on se trouve : en changer **ramène à la racine du tri**.

| Onglet | Ce qu'il montre |
|---|---|
| **Dossiers** | Tous les dossiers, en vignettes. Un dossier de tête `+` ne s'y trie pas lui-même — c'est une destination — mais **ce qu'il contient, oui** : ses sous-dossiers y figurent, et ses vidéos posées en vrac forment une entrée `+ Beach (sans dossier)`. |
| **Vidéos** | Toutes les vidéos, à plat, **en ordre aléatoire** — sans quoi les mêmes reviendraient toujours en tête. |
| **Mots-clés** | Les vidéos réunies en catégories d'après les mots de leurs noms. |

**L'édition n'est pas un quatrième onglet, c'est l'étage du dessous.** On y entre
en cliquant une vignette, et l'on s'y trouve sur *cet* élément : on note, on
range, on passe — et chaque décision avance au suivant de la même liste, sans
jamais remonter. `Échap` ramène aux vignettes, exactement là où on les avait
quittées.

Les vignettes vont par **pages de 40**, avec leur compte et leurs flèches dans
la barre du haut (`Ctrl+←/→`).

Recliquer l'onglet où l'on se trouve **ramène à la racine** : c'est le geste
qu'on fait après être descendu trop loin. Entrer dans un dossier, en revanche,
ne change pas l'onglet ouvert.

## Mots-clés

Les mots qui reviennent le plus dans vos noms de fichiers deviennent des
catégories, sans rien saisir (*Mots fréquents*) — ou bien les vôtres
(*Mes mots-clés*, `Ctrl+D` puis *Mots-clés automatiques…*).

Un mot-clé fréquent est **un seul mot**. Les noms de fichiers collent souvent
leurs mots (`BigTitsAsianGirl`) : ils sont coupés aux majuscules et aux
chiffres, sans quoi la catégorie était une phrase entière. Les mots de grammaire
des deux langues, et le jargon de fichier (`1080p`, `x264`, `web`…), sont
écartés. Pour chercher une expression de deux ou trois mots, on la saisit dans
**ses propres mots-clés**, où la recherche se fait par sous-chaîne.

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

**Un clic descend d'un étage**, le même geste partout : sur un dossier il
l'ouvre sur ses vidéos, sur un mot-clé sur les siennes, sur une vidéo — carte ou
aperçu — il la lance dans le **lecteur intégré**. La touche `F` fait de même qui recouvre la page : image en grand, temps restant,
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

## Code PIN

`⋯ › Affichage › Code PIN…` pose un code de quatre chiffres. Il est alors
demandé :

- **à l'ouverture de Prisme** : la fenêtre s'ouvre sur le cadenas, titre et
  icône neutres, et la dernière racine s'ouvre en coulisse ;
- **au retour du repli** (`Ctrl+K`) : le double-clic, `Échap` ou `Ctrl+K`
  sur la page neutre mènent au cadenas, et `Échap` sur le cadenas ramène à la
  page neutre ;
- **pour afficher les dossiers masqués**.

Le code se tape au clavier, pavé numérique compris, ou à la souris ; le
quatrième chiffre valide. Seule son empreinte (PBKDF2, deux cent mille tours)
est écrite, dans `pin_salt` et `pin_digest`. Après cinq erreurs de suite, il
faut attendre 30 s, puis le double à chaque nouvelle série, jusqu'à dix
minutes ; `pin_failures` et `pin_wait_until` gardent ce compte d'un lancement
à l'autre. Changer ou retirer le code redemande l'actuel.

**Code oublié** : Prisme fermé, effacer les valeurs de `pin_salt` et
`pin_digest` dans `config.json` (voir « Configuration »). C'est la réponse à
donner au client qui écrit au support : le code protège des regards, pas d'un
accès au disque.

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
`filter_include`, `filter_exclude`, `skip_hidden`, `use_scan_cache`,
`hw_decoding` (`true` pour décoder par la carte graphique ; par défaut le
processeur décode, sans le gel qu'impose chaque vidéo ouverte).
L'index est à côté, dans `index.db`, et le cache de vignettes dans `thumbs\`.

## Tests

```bash
python tests/smoke.py
```

Quelques secondes, sur une arborescence minuscule de fichiers vides : inventaire,
index, onglets, mots-clés, arborescence et état de l'analyse. C'est le test à
lancer après une modification.

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
python tests/test_floating.py
python tests/test_photos.py
```

Le lecteur flottant (passage de relais au même instant, gestes, touches,
ouverture automatique), le ⌸ du mur et la touche « Suppr » ; puis le mode
photo (lancement sur les vidéos, bascule, index et réglages séparés,
vignettes sans ffmpeg, diaporama, mur, feuilletage, mesure du format).

```bash
python tests/test_lock.py
```

Le code PIN : empreinte, cadenas à l'ouverture et au retour du repli, `Échap`
vers la page neutre, erreurs comptées et attente imposée, qui survit à un
redémarrage.

```bash
python tests/test_network.py
```

Vérifie la reconnaissance d'un stockage réseau et le parallélisme qui en découle.

```bash
python tests/bench_root.py X:```

Sépare, **sur votre vraie racine**, les trois temps d'une ouverture : ce que
l'index restitue sans toucher au disque, l'inventaire de la racine, la
vérification des dates, puis le coût du parcours récursif mesuré sur un
échantillon. C'est ce qui répond à « est-ce l'application ou le disque ». Rien
n'est écrit.

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
| `videosorter/floating.py` | Lecteur flottant, toujours au premier plan |
| `videosorter/config.py` | Configuration persistante, emplacements |
| `videosorter/scan.py` | Inventaire de la racine, et sa relecture en tâche de fond |
| `videosorter/index.py` | Index persistant : ce qu'on sait déjà du disque |
| `videosorter/board.py` | Vue planche : les éléments en cartes |
| `videosorter/ratings.py` | Notes de 0 à 5 étoiles |
| `videosorter/trash.py` | Corbeille de session |
| `videosorter/lock.py` | Code PIN : empreinte, cadenas, attente après erreurs |
| `videosorter/media.py` | ffmpeg/ffprobe, cache, vignettes en arrière-plan |
| `videosorter/actions.py` | Déplacer, supprimer, annuler |
| `videosorter/transfer.py` | File de transferts en tâche de fond |
| `videosorter/tree.py` | Panneau d'arborescence |
| `videosorter/widgets.py` | Grille d'aperçus, lecteur, barre de commandes, réglages |
| `videosorter/window.py` | Fenêtre principale, enchaînement, raccourcis |
