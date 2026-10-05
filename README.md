# Prisme

Trieur de vidéos et de photos pour Windows. On choisit un dossier racine, et
Prisme en montre le contenu en vignettes ; un clic ouvre la **fiche** d'un
élément : l'image en grand, ce qu'on en sait sur une ligne en haut, les touches
de tri en bas. Une touche = une décision, et l'élément suivant s'affiche
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
  « Lecteur flottant automatique » (le bouton ⧉ A de l'en-tête, ou ⋯ › Réglages). Ouvert tout
  seul, le lecteur ne prend pas le clavier.

La fenêtre est sans cadre, et toute la place va à l'image :

- elle prend les proportions de chaque vidéo, sans bandes noires ;
- le titre, discret, est posé en haut à gauche de l'image ; on l'attrape
  pour déplacer la fenêtre ;
- on l'agrandit par ses bords ;
- le double-clic la passe en plein écran.

Elle reprend à l'instant où l'on en était :

- le clic ou `Entrée` met en pause, la molette avance, clic maintenu + molette zoome ;
- `Maj+←` / `Maj+→` reculent ou avancent de 10 s ;
- le temps restant et le trait d'avancement sont affichés ; survoler le trait
  montre l'image à cet instant, un clic y va ;
- le bandeau ☆ ◂ ⏯ ▸ ⌸ ⛶ ⏏ ✕ paraît au survol, avec la place de la vidéo
  (« 12 / 340 ») — ☆ met la vidéo en favori ;
- le clic droit ouvre les destinations autour du pointeur, comme sur la
  fiche ;
- les touches de tri, de note et Suppr marchent depuis la fenêtre flottante.

Revenir dans Prisme (⏏, `Échap`, ou simplement cliquer dans sa fenêtre s'il
s'était ouvert tout seul) le referme, et la fiche reprend au même instant.

## Les deux modes

L'application regarde ce que contient la racine et choisit toute seule :

| Contenu de la racine | Mode | Ce qui défile |
|---|---|---|
| Des sous-dossiers | **dossiers** | Un sous-dossier à la fois, avec 10 aperçus pris dans ses vidéos (y compris celles des sous-dossiers imbriqués) |
| Uniquement des vidéos | **fichiers** | Une vidéo à la fois, lue en grand, avec une pellicule de 10 instants |

### Entrer dans un dossier

Trois étages, toujours les mêmes : le **panorama** (tous les dossiers, ou
toutes les vidéos), **l'intérieur d'un dossier** — ou d'un mot-clé, ou de
« Noms sans aucun mot » — en vignettes, et la **fiche** d'une vidéo, la même d'où qu'on
vienne.

Un **clic sur un dossier** y entre (de même `Entrée` sur sa vignette
survolée, ou `Ctrl+↓`) : il devient la nouvelle racine et bascule sur ses vidéos, à trier une par
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
| `Suppr` | Supprimer : la corbeille de session, puis le suivant. Le bandeau ambre se clique pour annuler |
| `Espace` | La suivante, partout — vidéo ou dossier, panneau du mur survolé, lecteur de côté. Un clic sur l'image : pause ; un double-clic : plein écran |
| `1` / `0` | En favori / retiré des favoris (`2` à `5` mettent aussi en favori) |
| `6`…`9`, `a`…`z` | Déplace vers la destination configurée, puis passe au suivant |
| `←` / `→` | Revenir en arrière / avancer sans décider |
| `Maj+←` / `Maj+→` | 10 s en arrière / en avant dans la vidéo |
| **molette** | Avance (cran vers le haut) ou recule dans la vidéo survolée — partout, mur compris |
| **survol du trait** | L'image à cet instant ; un clic y va |
| **clic gauche maintenu + molette** | Zoome sur l'image, centré là où pointe la souris (×1 à ×6) — `Ctrl+molette` fait de même |
| **clic droit** | Ramène l'image à sa taille normale |
| clic, ou `F` | Descend d'un étage : dans le dossier, ou dans la vidéo |
| `Ctrl+Z` | Annuler la dernière action |
| `Ctrl+F` | Aller au champ de filtre |
| `Ctrl+P` | Basculer entre les vignettes et la fiche |
| `Ctrl+H` | Se placer sur un élément au hasard |
| `Ctrl+←/→` | Page d'aperçus précédente / suivante |
| `Ctrl+B` | Ouvrir la corbeille de session — sa pastille, en haut, dit combien d'éléments elle tient et leur poids (« 12 · 3,48 Go ») |
| `Alt+←` | Revenir à l'endroit précédemment visité |
| `Ctrl+R` | Réanalyser tout le disque, sans se fier au cache |
| `Ctrl+↓` | Entrer dans le dossier affiché pour en trier les vidéos |
| `Ctrl+↑`, ou le bouton `↑` | Remonter au dossier parent, d'où qu'on soit |
| `Ctrl+T` | Afficher ou masquer l'arborescence |
| `Ctrl+M` | Couper ou remettre le son |
| `Ctrl+L` | Lecteur flottant : ouvrir, ou revenir dans Prisme |
| `Entrée` (photo) | Lancer ou arrêter le diaporama |
| `Ctrl+E` | Montrer l'élément dans l'Explorateur |
| `Ctrl+O` | Ouvrir l'élément dans le lecteur du système |
| `Ctrl+D` | Ouvrir la configuration des destinations |
| `Entrée` | Pause / reprise sur une vidéo ; entrer dans un dossier (sa vignette survolée, ou sa fiche) |
| `Échap` | Revenir aux vignettes, puis remonter d'un niveau ; sur le mur, revenir à l'onglet d'avant |

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

## Plusieurs racines

Le NAS, un disque externe, un dossier du PC : le bouton **⌂** à gauche du fil
d'Ariane les réunit. Il dit combien on en regarde (« ⌂ 2/3 ») ; son menu a une
case par racine : on coche celles qu'on veut voir, et la liste suit à la
fermeture du menu — une seule cochée, c'est elle qu'on ouvre ; plusieurs, leurs
dossiers et leurs vidéos ensemble, dans chaque onglet. « Ajouter des racines… »
en prend plusieurs d'un coup (Ctrl ou Maj), « Retirer une racine » les enlève de
la liste (rien ne bouge sur le disque).

- **Une racine injoignable** — disque débranché, NAS éteint — est grisée dans le
  menu, et ses dossiers et ses vidéos disparaissent des listes ; ils reviennent
  avec elle. Les racines sont sondées toutes les vingt secondes, sans jamais
  attendre plus de trois secondes chacune.
- **Rien n'est oublié** : l'index garde ce qu'il sait de chaque racine (vignettes,
  durées, empreintes, notes, index du Labo IA). Un fichier ou un dossier déplacé,
  d'une racine à l'autre ou non, garde tout cela : l'index le suit.
- **Les destinations sont les mêmes pour toutes les racines.**
- La racine virtuelle (`roots.UNION`) se déplie en vraies racines là où l'on
  parcourt le disque (`videosorter/roots.py`) : l'analyse, les vignettes, les
  doublons, les empreintes, les plans et les titres la traitent comme n'importe
  quelle racine.

## Réparer des vidéos abîmées

⋯ › **Réparer des vidéos abîmées…** : les vidéos cochées, sinon celle de la fiche,
sinon celles de la liste. Chaque vidéo est d'abord décodée en entier ; chaque
erreur du décodeur est datée, et un dégât s'étend de l'image clé qui le précède
à la suivante. La colonne « État » dit aussi de quel dégât il s'agit : des
images **décalées** (récupérables, Prisme propose « Reconstruire ») ou des
données **effacées** (Prisme propose « Figer »). Chaque échec est noté, avec
son message, dans `reparation.log` (dossier de données de Prisme). Puis, au
choix :

- **Reconstruire** (recommandé, la vraie réparation) : bien souvent, les images
  ne sont pas perdues. Quelques octets parasites (copie ou téléchargement
  troué) décalent tout un bloc du fichier, et le lecteur lit chaque image de
  travers jusqu'à l'image clé suivante — d'où la mosaïque. Prisme suit la
  chaîne des images dans les octets bruts (chacune commence par sa longueur et
  un en-tête reconnaissable), saute les parasites et reconstruit la piste vidéo
  avec ses instants d'origine ; le son est recopié tel quel. Les images
  retrouvées sont celles d'origine, sans perte, en quelques secondes (H.264 et
  H.265, `videosorter/resync.py`). Sur une vidéo du NAS : 429 images
  illisibles sur 439 retrouvées. Si le fichier n'a pas ce défaut, Prisme le
  dit ;
- **Figer** : sur chaque passage abîmé, la dernière image saine reste à
  l'écran, le son continue, la durée ne change pas (réencodage) ;
- **Couper** : les passages abîmés sont retirés, le reste copié sans perte —
  instantané, mais la vidéo saute ;
- **Masquer** : le décodeur remplit les blocs perdus avec le mouvement voisin ;
- **Recoller l'enveloppe** : pour un fichier qui ne s'ouvre pas ou dont la durée
  est fausse.

Ce qui est vraiment effacé (des octets écrasés, pas ajoutés) ne revient pas :
les outils « IA » qui inventeraient les images manquantes restent, en 2026, des
prototypes de laboratoire.

La réparation est un nouveau fichier, « nom (réparée) », vérifié à son tour.
**Regarder avant / après** ouvre l'originale et la réparée côte à côte, et
saute d'un passage abîmé au suivant. « Remplacer les originales » envoie
l'original dans la corbeille de séance et donne son nom à la réparée (favoris
compris). Une pastille 🛠 suit l'avancement (`videosorter/repair.py`).

## Les vidéos seules dans leur dossier

Après chaque analyse, Prisme cherche les dossiers qui ne contiennent qu'**une
seule vidéo** et aucun autre dossier, et les nomme dans une fenêtre « Vidéos
seules dans leur dossier ». Rien ne bouge sans « Regrouper » :

- la vidéo prend **le nom de son dossier** et part dans le dossier **« 1 »** de
  la racine principale (le NAS) ; le dossier vide est supprimé ;
- s'il restait des photos ou d'autres fichiers à côté, le dossier, vidéo en
  moins, part dans **« PICS OK »** ;
- les « 1 » des autres racines se fusionnent dans celui du NAS (un nom déjà
  pris reçoit « (2) ») ;
- un dossier décoché est laissé tel quel et n'est plus proposé ; « Plus tard »
  attend la séance suivante.

Tout est revérifié sur le disque juste avant d'agir, et chaque déplacement
s'annule (`Ctrl+Z`). Les vignettes que le NAS dépose (`@eaDir`, `Thumbs.db`) ne
comptent pas (`videosorter/solos.py`).

## Demandes depuis le téléphone

Sur la page mobile, le bouton **➤ Demande** (à côté de la recherche) ouvre une
feuille : un titre, un style de vidéo, une envie. La demande arrive dans Prisme —
que la page soit servie par le PC ou par le NAS — : une pastille **➤ N demandes**
paraît en haut, et « Demandes reçues… » (⋯ › Connexion) les liste, la plus récente
d'abord, avec l'appareil qui l'a envoyée. De là : **Chercher sur le web** (la
recherche web, préremplie), **Chercher dans le Labo IA**, **Marquer comme faite**,
**Supprimer**. Au plus trente demandes par appareil et par heure
(`videosorter/demandes.py`).

## Les onglets

Un onglet est un point de vue sur **toute** la collection, jamais sur l'endroit
où l'on se trouve : en changer **ramène à la racine du tri**.

| Onglet | Ce qu'il montre |
|---|---|
| **Dossiers** | Tous les dossiers, en vignettes. Un dossier de tête `+` ne s'y trie pas lui-même — c'est une destination — mais **ce qu'il contient, oui** : ses sous-dossiers y figurent, et ses vidéos posées en vrac forment une entrée `+ Beach (sans dossier)`. |
| **Vidéos** | Toutes les vidéos, à plat, **en ordre aléatoire** — sans quoi les mêmes reviendraient toujours en tête. |
| **Mots-clés** | Les vidéos réunies en catégories d'après les mots de leurs noms. |
| **Mur** | Plusieurs vidéos à la fois (voir « Le mur »). `Échap` ramène à l'onglet d'avant. |
| **★ Favoris** | Dossiers et vidéos mis en favori, ensemble. |

**La fiche n'est pas un onglet, c'est l'étage du dessous.** On y entre
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

Après un tirage de **⚄ Aléatoire**, la suite est un autre tirage : la fiche
s'ouvre dans le dossier de la vidéo, mais Suivante (Espace, →, un rangement, le
clic gauche de l'ultra tri) tire une nouvelle vidéo au hasard, et ← revient à la
précédente tirée. Avec « rester dans ce dossier » coché, la suite reste dans le
dossier ; aller ailleurs rend la navigation habituelle.

### L'ordre au hasard : d'abord ce qu'on n'a jamais vu

Classées « au hasard » (le réglage par défaut), les listes — un dossier où
l'on entre, l'onglet Vidéos, et les vidéos d'un dossier quand « rester dans ce
dossier » est allumé — mettent **d'abord ce qu'on n'a jamais regardé**, puis
le reste. Les deux groupes sont mélangés, et **rebattus à chaque séance** : on
ne retombe pas le lendemain sur les mêmes vidéos. Pendant la séance, l'ordre
ne bouge pas (◂ ▸ et « 12 / 340 » restent fiables) ; ce qu'on vient de voir
passe derrière à la séance suivante.

Un **sélecteur de densité** choisit de 2 à 8 cartes par rangée.

Les filtres chiffrés accompagnent la planche : durée *plus longue que* / *plus
courte que*, résolution *au moins* / *au plus*, note *au moins*. La durée d'un
dossier n'est pas mesurée mais **extrapolée** à partir des vidéos déjà sondées
pour les aperçus — sonder une collection entière coûterait des heures sur un
NAS. Un élément dont on ne sait rien passe le filtre plutôt que de disparaître
sans explication, et l'estimation se précise à mesure que vous parcourez.

Les pastilles de tri — **Date**, **Durée**, **Taille**, **Résolution** — se
lisent sans rien ouvrir : un clic du plus grand au plus petit (pour la date, du
plus récent au plus ancien), un deuxième l'inverse, un troisième revient au
hasard. **Date**, c'est la date de modification sur le disque, celle de
l'explorateur Windows — ni celle de l'analyse, ni celle de l'index. Sous
« Vidéos », celles que l'analyse n'a pas relevées se lisent en tâche de fond, un
dossier à la fois, et la liste se reclasse d'elle-même. Les vidéos d'une carte
suivent le même ordre. Sans pastille, l'ordre est le hasard : sous « Dossiers »,
le même toute la séance, les jamais vus d'abord ; sous « Vidéos », rebattu à
chaque visite, les jamais vues d'abord.

### L'ultra tri : tout à la souris

`Ctrl+U`, le **⚡** du bandeau de la fiche ou ⋯ › « Ultra tri » : sur la fiche
d'une vidéo, **clic gauche** sur l'image, on la garde et c'est la suivante ;
**clic droit**, on la supprime (corbeille de séance, `Ctrl+Z` la reprend ; sur
le NAS, détruite à la fermeture). Deux clics trop rapprochés ne comptent qu'une
fois : le second tomberait sur une vidéo pas encore vue. Le clic droit ne
supprime jamais un dossier entier. Une pastille orange, en haut, rappelle que le
mode est actif ; un clic dessus l'arrête. Il ne survit pas à la fermeture de
Prisme, et la première activation de la séance demande confirmation.

### Ce qui tourne en fond, sous les yeux

Une pastille par travail de fond, en haut à droite à côté de celle de la
recherche web, se remplit comme une barre de progression : l'indexation du Labo
IA (même Labo fermé ; un clic le rouvre), les empreintes des doublons, le
regroupement dans « 1 », le téléchargement du modèle de la voix. L'analyse et la
recherche de doublons gardent leur barre, juste avant le dé.

### Que des vidéos qui ressemblent à celle-ci

Dans le bandeau d'une vidéo (fiche, lecteur de côté), l'interrupteur **✦** :
allumé, la liste devient les 150 vidéos qui ressemblent le plus à celle qu'on
regarde, **tous dossiers confondus**, de la plus proche à la moins. ▸ ou Espace
mènent à la suivante, ◂ revient ; trier, supprimer, mettre en favori marchent
comme partout. Éteint, on retrouve la liste d'avant et ses suivantes habituelles.

La ressemblance vient de l'index du Labo IA (l'empreinte moyenne des images de
chaque vidéo), un peu aidée par les mots communs des noms — jamais par eux
seuls. Aucun moteur à charger : le calcul prend un dixième de seconde
(`videosorter/similar.py`). Une vidéo que le Labo n'a pas encore indexée ne peut
pas servir de départ.

### Chercher à voix haute

Un micro dans le champ « chercher… », dans celui du Labo IA et à côté des
recherches web : un clic, on parle, Prisme écrit puis cherche. L'écoute s'arrête
seule quand on se tait (ou d'un second clic). La reconnaissance est Whisper
« small » (OpenAI), français et anglais mêlés, téléchargée une fois (970 Mo) puis
**entièrement sur le PC** : la voix ne part nulle part. Elle demande les paquets
du Labo IA (torch, transformers) et tourne dans son propre processus
(`videosorter/voice.py`).

**◂ Précédent** (`Alt+←`) revient à l'endroit visité juste avant, y compris
après un déplacement latéral — à distinguer de **Remonter**, qui monte d'un
niveau dans l'arborescence.

## Le mur

Plusieurs vidéos à la fois (de 2 à 10), qui se remplacent toutes seules. Le mur
**mélange verticales et horizontales** : chaque panneau prend la forme de sa
vidéo, et la mosaïque se compose pour remplir l'écran. Les vidéos se rangent en
rangées (ou en colonnes) de hauteurs différentes, comme les galeries de photos ;
on essaie toutes les répartitions et l'on garde celle qui montre le plus
d'image, sans qu'aucune vidéo ne devienne minuscule. Sur un écran 1920 × 1080,
quatre verticales et quatre horizontales occupent 95 % de l'écran, contre 59 %
dans une grille de cases égales. La grille reste candidate : la mosaïque ne
fait jamais moins bien qu'elle (trois horizontales, par exemple, y tiennent
mieux).

- **Tout montrer / Remplir à 100 %** (bouton du mur) : chaque vidéo tient
  entière dans sa case, avec de fines bandes noires ; ou bien l'écran entier est
  couvert, bord à bord : la mosaïque est alors choisie pour rogner le moins
  possible, et chaque vidéo perd la même part de son image — jamais une seule
  très zoomée. Huit vidéos mélangées en gardent 99 %, deux verticales seulement
  61 %.
- **Les vidéos « en boîte »** — filmées debout mais enregistrées dans un cadre
  couché, bandes noires sur les côtés — se reconnaissent à leurs premières
  images : leur case prend la forme de l'image utile et les bandes sont rognées.
  Quatre verticales de ce genre se posent côte à côte, au lieu d'être prises
  pour des horizontales et empilées deux par deux.
- **Le mur d'un dossier** : entré dans un dossier, le bouton **▦ Mur** des
  filtres lance le mur avec ses vidéos, chaque panneau coché « rester dans ce
  dossier ». La coche vaut pour son seul panneau : décochée, il part au hasard
  dans toute la collection, les autres restent dans le dossier — et deux
  panneaux ne montrent jamais la même vidéo.
- Quand une vidéo se termine, sa remplaçante est choisie **de la même forme** :
  la mosaïque ne se recompose pas à chaque fin de vidéo.
- Une vidéo dont la résolution n'est pas encore connue prend sa place dès sa
  première image.
- Le panneau **qu'on entend** (celui sous la souris, son actif) porte un
  liseré doré.
- Son bandeau : ☆ (favori), le dossier verrouillé (« rester dans ce
  dossier »), ◂ ⏯, puis ⤮ (une autre au hasard) — ou ▸ (la suivante du
  dossier) quand on y reste —, ⌸ (Explorateur) et ⊙ (ce panneau seul, sur
  tout le mur).
- Un clic sur un panneau : pause. Double-clic : cette vidéo seule, en plein
  écran (un autre double-clic ou `Échap` rend le mur). `F` : sa fiche. Espace :
  une autre vidéo dans le panneau survolé. `Maj+←` / `Maj+→` : 10 s.

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

**Le son** va toujours vers une vraie sortie d'écoute : si Windows a mis par
défaut le profil « Hands-Free » (mains libres) d'un casque Bluetooth, qui ne joue
rien hors d'un appel, Prisme prend la sortie « Stereo » du même casque. Un casque
connecté ou une sortie par défaut changée en cours de route est suivi, sans
relancer Prisme (`videosorter/audiodev.py`). Prisme a son propre volume : la
**molette sur le haut-parleur** de l'en-tête le règle par pas de 5 %, le **clic
droit** ouvre son curseur et le choix de **la sortie du son** (celle de
Windows, ou un casque, des haut-parleurs, l'écran…, retenue d'une fois sur
l'autre), le clic coupe ou rend le son. L'infobulle dit où part le son. Utile quand le curseur
de Windows règle une autre sortie que celle où joue Prisme. La saisie vocale évite de même le
micro mains libres d'un casque, qui le ferait basculer en mode appel.

**En plein écran**, la pellicule des cinq instants quitte sa colonne : l'image
prend toute la largeur. Quand la souris bouge sur l'image, les cinq instants
glissent depuis la droite, en fondu ; après deux secondes et demie sans
mouvement, ou si la souris quitte l'image, ils repartent vers la droite. Posée
sur eux, la souris les garde.

## Favoris

Une étoile, pas une note : ☆ vide, ★ dorée en favori. Elle se clique sur la
vignette survolée, dans le bandeau de toute vidéo qui joue (fiche, mur, lecteur
de côté, lecteur flottant), et sur la ligne du haut de la fiche, à côté de
l'épingle — d'une vidéo comme d'un dossier. Entré dans un dossier, une ★ en tête
de la barre des filtres met **le dossier lui-même** en favori.
Au repos, une ★ dorée près du temps restant dit qu'une vidéo est en favori.
Au clavier : `1` met en favori (`2` à `5` aussi), `0` retire.

Les notes vivent dans `%LOCALAPPDATA%\Prisme\ratings.json` : rien n'est
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

Bouton **Destinations…** (ou `Ctrl+D`), puis **Ajouter des dossiers…** —
`Ctrl` ou `Maj` pour en sélectionner plusieurs d'un coup, chacun devenant un
raccourci. Le sélecteur s'ouvre sur la racine ; ses boutons **⌂** mènent d'un
clic à chacune des racines — le NAS compris, qu'un partage réseau n'apparaît
jamais parmi les lecteurs —, **Lecteurs** à la liste des disques.

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

Quatre chiffres (⋯ › Confidentialité › Code PIN…), demandés :

- **à l'ouverture** : un cadenas, sous un titre et une icône neutres, avant
  que rien de la collection ne paraisse ;
- **au retour du repli** (`Ctrl+K`, ou la lune de l'en-tête) : on en sort
  par `Ctrl+K` ou un double-clic — pas par `Échap`, la touche du réflexe ;
  depuis le cadenas, `Ctrl+K`, `Échap` ou « Retour » ramènent à la page
  neutre ;
- **pour afficher les dossiers masqués**.

Le code n'est gardé que sous forme d'empreinte (PBKDF2, sel propre à chaque
installation). Après cinq erreurs, il faut attendre 30 s, puis le double à chaque
nouvelle série, jusqu'à 10 minutes ; l'attente survit à une relance.
Code oublié : Prisme fermé, vider `pin_salt` et `pin_digest` dans `config.json`.

## Essai et licence

- **Essai de 14 jours** : le compteur « Essai · N jours » s'affiche en haut de la
  fenêtre (un clic ouvre la licence). L'essai fini, Prisme demande une clé avant
  de s'ouvrir ; « Quitter » ne touche à rien.
- **Clés `PRISME1-<contenu>.<signature>`**, signées par le site (Ed25519) et
  vérifiées hors ligne ; le contenu est un JSON (`plan` : `mois`, `an` ou `vie` ;
  `expires` : date d'expiration en secondes, `0` à vie ; `email`). La
  vérification Ed25519 (RFC 8032) est écrite dans `licence.py`, sans dépendance.
- **Abonnement** : dans ses 10 derniers jours, Prisme demande une clé prolongée
  au site (`POST /api/licence`, corps `{"key": …}`, réponse `{"key": …}`), une
  fois par jour au plus.
- Le début de l'essai est aussi noté dans le registre (`HKCU\Software\Prisme`) ;
  reculer l'horloge ne rend pas de jours.
- **`PUBLIC_KEY` est vide dans `licence.py` : version de développement, sans
  essai ni licence.** Avant de distribuer, y coller la clé publique du site
  (hexadécimal ou base64), régler `SITE`, et vérifier que le générateur de clés
  du site produit bien le format ci-dessus.

## Labo IA (essai)

⋯ › Collection › Labo IA (essai)… : retrouver une scène par sa description
(« une plage au coucher du soleil »), et les **collections** reconnues au début
de leurs noms de fichiers, à réunir dans un dossier.

- Des modèles CLIP (`open_clip`) tournent sur ce PC, au choix, chacun avec son
  propre index :
  - **Rapide** (`xlm-roberta-base-ViT-B-32`, LAION) : 56 ms par image sur un i7
    de portable, français ou anglais ;
  - **Précis** (`xlm-roberta-large-ViT-H-14`, LAION) : 1,9 s par image, 4,8 Go,
    nettement plus juste ; français ou anglais ;
  - **SigLIP 2** (`ViT-SO400M-14-SigLIP2`, Google 2025) : le plus fort sur les
    tests généraux, mais l'on ignore ce qui a été retiré de son entraînement —
    MetaCLIP 2, DFN et DataComp ont écarté les contenus adultes ; les modèles
    LAION les ont vus. Il est là pour être jugé sur la collection elle-même.
  Un modèle lourd ne garde en mémoire que la moitié utile (images pour indexer,
  texte pour chercher), et ses poids se copient un à un depuis le fichier
  projeté en mémoire : 8 Go de mémoire suffisent. Ses empreintes se gardent en
  float16.
- **Comparer les moteurs** (onglet) : les mêmes vidéos (celles du moteur
  rapide), indexées par chaque moteur aux mêmes instants ; quelques recherches ;
  on coche ce qui est juste, dans n'importe quelle colonne (une vidéo cochée
  l'est partout), et chaque moteur reçoit sa note — la part de ses résultats qui
  sont justes, recherche par recherche et en tout. Les jugements se gardent.
- **Carte NVIDIA** : le labo la repère, dit si son pilote est trop ancien (le
  Gestionnaire de périphériques ne propose que celui de Windows Update), et
  « Utiliser la carte NVIDIA » remplace PyTorch « processeur » par la même
  version CUDA 12.6 — la dernière famille qui sert encore les GTX 10.
- **Chaque idée de la phrase est exigée** : « amatrice qui pisse en extérieur »
  se lit « amatrice » + « pisse » + « en extérieur », et la plus faible des trois
  décide — une simple plage ne passe plus devant. Les virgules séparent des idées
  à la main ; « -plage », « sans plage » ou « pas de plage » écartent un mot.
  Chaque idée est mesurée par rapport à l'ensemble des images (écarts à la
  moyenne), ce qui rend les idées comparables entre elles.
- **La recherche apprend** : cocher de bons résultats puis « 👍 Plus comme ça »
  (ou « 👎 Moins comme ça ») refait la recherche en tenant compte de ces
  exemples, jusqu'à « Oublier les exemples » ou une nouvelle phrase.
- Une vidéo par résultat, à son meilleur moment.
- **Le penchant de chaque image est retiré** : certaines images ressortent haut
  pour à peu près n'importe quelle phrase. On mesure ce penchant sur trente
  phrases très variées, et on le retire : sur une vraie collection, « forêt » et
  « une voiture » donnaient des classements corrélés à 0,69, ramenés à 0,37
  (« une douche » / « une voiture » : de 0,35 à 0,01).
- **Le moteur s'arrête avec Prisme**, quoi qu'il fasse : des moteurs orphelins de
  sessions fermées gardaient des gigaoctets de mémoire.
- Chaque vidéo est résumée par des images prises selon sa durée : 3 pour un clip
  de moins de deux minutes, puis environ une par minute, 16 au plus. Elles sont
  calées sur les changements de plan quand Prisme les connaît (⋯ › Collection ›
  Repérer les plans), et reprises d'abord parmi les aperçus déjà en cache ; seul
  ce qui manque est extrait, après ce que Prisme est en train de montrer.
- Une phrase se compare sous plusieurs formulations (« une photo de … »…),
  moyennées : les recherches et les tags en sont plus justes.
- Le modèle tourne dans son propre processus, à basse priorité, sur la moitié des
  cœurs : Prisme reste fluide pendant l'indexation, et l'on peut chercher dans ce
  qui est déjà indexé pendant que ça avance. Chaque vidéo n'est indexée qu'une
  fois (l'index se garde à côté des réglages, `labo/`, en float32 : environ 20 Ko
  par vidéo), et l'on peut arrêter et reprendre.
- Les bibliothèques sont **facultatives** (`requirements-ia.txt`) : le labo
  propose de les installer lui-même (plusieurs Go). Depuis les sources, elles
  vont dans le Python de Prisme ; dans le programme vendu (portable ou
  installé), Prisme se procure d'abord un Python à lui — l'archive officielle
  de python.org, dans `%LOCALAPPDATA%\Prisme\ia` — et y fait tourner le
  moteur, qui lui parle par une connexion locale (`iapython.py`). La saisie
  vocale passe par le même Python. Le premier usage télécharge le modèle.
- **Bilan du programme** : `Prisme-diagnostic.exe --verifier` essaie chaque
  pièce dont le programme empaqueté dépend (numpy, recherche web, navigateur
  invisible, ffmpeg, Python du labo) ; `--avec-python` et `--avec-clip`
  vont jusqu'au moteur.
- Double-clic sur un résultat : la vidéo se lit dans le lecteur flottant, au
  moment de l'image trouvée.
- **Collections par nom** (onglet) : un même morceau de nom, à la lettre près —
  même casse, même ponctuation —, au début ou au milieu : « Cum Fantasy, … »,
  « Anna - Cum Fantasy - … ». Ces fichiers viennent d'une même collection. Pas un
  mot-clé : un morceau exact, borné par la ponctuation (début du nom, virgule,
  tiret entouré d'espaces, souligné, parenthèse…), le reste du nom changeant.
  Trois finesses : « Fin » (chaque fichier va au morceau qui réunit le plus de
  fichiers — sa collection, plutôt qu'un titre partagé par hasard ; le réglage
  conseillé), « Très fin » (le plus long morceau commun), « Large » (le plus
  court). Les noms d'appareils (`VID_2024…`), les morceaux génériques (« Part 2 »,
  « Full HD ») et les collections déjà réunies dans un dossier à leur nom sont
  écartés. Sur les fichiers cochés d'une collection :
  - **Voir sur le mur**, **Playlist** (le lecteur de droite), **Parcourir** (comme
    un dossier : vignettes, fiches, Échap pour revenir) ;
  - **Créer un groupe** : un dossier virtuel, sous l'onglet Mots-clés (✦), sans
    rien déplacer ;
  - **Ranger dans un dossier** : un vrai dossier à ce nom, créé là où se trouvent
    la plupart des fichiers (« Ailleurs… » pour un autre endroit) — des
    rangements ordinaires de Prisme, Ctrl+Z annule.
  Quelques secondes pour des dizaines de milliers de noms (`videosorter/namegroups.py`).
- **L'index suit les fichiers rangés** : tout rangement dans Prisme (un fichier,
  ou un dossier entier) met à jour les index du Labo — une vidéo déplacée garde
  ses images, sans être indexée une seconde fois.
- Chaque résultat **se coche** ; « Ajouter au mot-clé… »
  verse les vidéos cochées dans un de vos mots-clés (un existant, ou un nouveau)
  : elles apparaissent sous l'onglet Mots-clés, « Mes mots », quel que soit leur
  nom de fichier. Une vue, comme le reste : rien ne bouge sur le disque. Un tel
  mot-clé porte ✦ au lieu de #.

## Mises à jour

Le programme construit regarde au lancement, puis toutes les six heures, s'il
existe une version plus récente. S'il y en a une, une pastille **⬆ Mise à jour
1.2.0** paraît en haut, avec un bandeau. Un clic montre les nouveautés, puis :

1. Prisme télécharge l'archive et vérifie son empreinte. Une archive abîmée
   est refusée, et rien n'est changé.
2. Il se ferme. Un petit script remplace `Prisme.exe`, `Prisme-diagnostic.exe`
   et `_internal` — rien d'autre : `cache/` et `prisme.cache` restent —, puis
   relance Prisme. En cas d'échec en route, l'ancienne version est remise en
   place. Le journal est dans `_mise-a-jour/pose.log`, à côté du programme.
3. Au lancement suivant, Prisme dit qu'il est passé à la nouvelle version.

« ⋯ › Aide › Rechercher une mise à jour » regarde tout de suite. Lancé depuis
les sources, Prisme se met à jour par git.

**Publier une version**, depuis les sources :

    python construire.py --publier 1.2.0 --notes "Ce qui change" --notes "Autre chose"

La commande construit le programme, le dépose avec `version.json` dans le
dossier de `publication.txt` (aujourd'hui le NAS : `.prisme-mises-a-jour`),
et inscrit cette adresse dans le programme : un Prisme installé sait où
regarder sans aucun réglage. Une deuxième ligne dans `publication.txt` donne
une autre adresse de lecture, par exemple un stockage en ligne en https.

**La signature** : avec un `signature.json` à côté de `construire.py`
(`{"signtool": "…/signtool.exe", "arguments": [...]}`, les arguments que donne
le fournisseur du certificat), chaque `.exe` est signé avant la mise en
archive. Dès lors, un Prisme signé n'accepte plus que des mises à jour
signées par le même éditeur.

## Installation et lancement

Prérequis : **Python 3.10+** et **ffmpeg/ffprobe** accessibles (déjà installés ici
via `winget install Gyan.FFmpeg` ; l'application les cherche dans le `PATH`, dans
le dossier winget, puis dans le sien). S'ils manquent, Prisme propose au
lancement de les télécharger (gyan.dev, environ 115 Mo, empreinte vérifiée) :
à côté du programme pour la version portable, sinon dans
`%LOCALAPPDATA%\Prisme\ffmpeg`.

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
`decoding` (`auto`, `cpu` ou `gpu`, aussi par ⋯ › Réglages › Avancé › Décodage vidéo ;
en automatique, le processeur décode s'il a au moins 8 cœurs logiques, la carte
graphique sinon ; l'ancien `hw_decoding: true` force la carte graphique). Mesuré sur un mur de 6 à 10
vidéos 1080p : aussi fluide dans les deux cas, processeur quatre fois moins
occupé avec la carte, mais chaque aperçu survolé gèle l'interface un peu plus
longtemps — 20 ms au lieu de 8. Qt fige ce choix au lancement, pour tout le
programme), `wall_fit` (`show`, `fill` ou `full` : comment le mur occupe ses
cases).
L'index est à côté, dans `index.db`, et le cache de vignettes dans `thumbs\`.

## Tests

```bash
python tests/smoke.py
```

Quelques secondes, sur une arborescence minuscule de fichiers vides : inventaire,
index, onglets, mots-clés, arborescence et état de l'analyse. C'est le test à
lancer après une modification.

```bash
python tests/test_lock.py
python tests/test_licence.py
python tests/test_labo.py
```

Le code PIN (empreinte, attentes, cadenas au lancement et au retour du repli), la
licence (vecteurs Ed25519 de la RFC 8032, clés, essai, prolongation) et le Labo IA
(avec un moteur factice, sans rien télécharger).

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
| `videosorter/media.py` | ffmpeg/ffprobe, cache, vignettes en arrière-plan |
| `videosorter/actions.py` | Déplacer, supprimer, annuler |
| `videosorter/transfer.py` | File de transferts en tâche de fond |
| `videosorter/tree.py` | Panneau d'arborescence |
| `videosorter/widgets.py` | Grille d'aperçus, lecteur, barre de commandes, réglages |
| `videosorter/window.py` | Fenêtre principale, enchaînement, raccourcis |
| `videosorter/lock.py` | Code PIN : empreinte, attentes, cadenas |
| `videosorter/licence.py` | Essai de 14 jours, clés de licence (Ed25519), prolongation |
| `videosorter/update.py` | Mises à jour : version publiée, téléchargement vérifié, pose et retour arrière |
| `videosorter/licence_dialog.py` | Fenêtre de licence, et le contrôle avant l'ouverture |
| `videosorter/ia.py` | Labo IA : moteurs CLIP, index des images, recherche, tags |
| `videosorter/labo.py` | Fenêtre du Labo IA |
| `videosorter/namegroups.py` | Les collections reconnues au début de leurs noms |
| `videosorter/repair.py` | Les vidéos abîmées : diagnostic, et réparation (figer, couper, masquer) |
| `videosorter/repair_dialog.py` | La fenêtre qui les vérifie et les répare |
| `videosorter/audiodev.py` | La bonne sortie son (jamais le « mains libres » d'un casque), suivie |
| `videosorter/solos.py` | Les vidéos seules dans leur dossier, regroupées dans « 1 » |
| `videosorter/solos_dialog.py` | La fenêtre qui les propose |
| `videosorter/similar.py` | Les vidéos qui se ressemblent (index du Labo IA) |
| `videosorter/voice.py` | La saisie vocale des recherches (Whisper, sur le PC) |
| `videosorter/roots.py` | Plusieurs racines, et la racine virtuelle qui les réunit |
| `videosorter/demandes.py` | Les demandes envoyées depuis le téléphone |
| `videosorter/demandes_dialog.py` | La fenêtre « Demandes reçues » |
| `videosorter/split.py` | Le mur : plusieurs vidéos à la fois |
| `videosorter/mosaic.py` | La mosaïque du mur : verticales et horizontales, l'écran rempli |
