"""La mosaique du mur : des videos de toutes formes, et l'ecran rempli.

Une grille de cases egales ne va qu'a des videos de meme forme. Melanger
verticales et horizontales y laissait de larges bandes noires : huit videos,
quatre de chaque, n'occupaient que 59 % d'un ecran 1920 x 1080.

On fait comme les galeries de photos : les videos se rangent en rangees (ou en
colonnes) dont la hauteur s'adapte a ce qu'elles contiennent, chaque video
prenant une largeur a la mesure de sa forme. On essaie toutes les facons de
repartir les videos entre les rangees -- quelques millisecondes pour dix -- et
l'on garde celle qui montre le plus d'image : les memes huit videos occupent
alors 96 % de l'ecran.

Les cases remplissent toujours tout l'ecran. Ce qui reste, c'est le choix de
chaque panneau : montrer toute la video (de fines bandes noires), ou remplir sa
case (un leger recadrage).
"""
from __future__ import annotations

from functools import lru_cache

# La forme par defaut d'une video dont on ignore encore la resolution.
WIDE = 16 / 9
TALL = 9 / 16

# Pour essayer les repartitions, les videos se rangent en trois familles :
# debout, carrees, couchees. Dix videos de dix formes differentes donneraient
# sinon cent mille repartitions a essayer ; trois familles en laissent
# quelques centaines. Les largeurs, elles, suivent la vraie forme de chacune.
FAMILIES = (0.8, 1.25)


# La forme type de chaque famille : debout, carree, couchee.
FAMILY_SHAPES = (TALL, 1.0, WIDE)


def family(aspect: float) -> int:
    """0 debout, 1 a peu pres carree, 2 couchee."""
    if aspect < FAMILIES[0]:
        return 0
    if aspect <= FAMILIES[1]:
        return 1
    return 2


@lru_cache(maxsize=4096)
def _parts(counts: tuple, ceiling: tuple | None) -> tuple:
    """Toutes les facons de repartir `counts` (combien de videos de chaque
    famille) en groupes, sans doublons : les groupes sortent du plus grand
    au plus petit, chacun au plus egal au precedent. Garde en memoire :
    les memes sous-problemes reviennent sans cesse."""
    if not any(counts):
        return ((),)
    found = []
    for part in _subsets(counts):
        if ceiling is not None and part > ceiling:
            continue
        rest = tuple(c - p for c, p in zip(counts, part))
        for tail in _parts(rest, part):
            found.append((part,) + tail)
    return tuple(found)


@lru_cache(maxsize=1024)
def _subsets(counts: tuple) -> tuple:
    """Les groupes non vides qu'on peut tirer de `counts`, du plus grand au
    plus petit."""
    found = [()]
    for c in counts:
        found = [prefix + (value,) for prefix in found for value in range(c, -1, -1)]
    return tuple(part for part in found if any(part))


# La place d'une video ne doit pas tomber trop loin de celle des autres :
# sans cette regle, sept verticales empilees en une colonne de quatre-vingts
# pixels et trois grandes a cote couvraient 98 % de l'ecran -- et l'on ne
# voyait plus rien des sept.
def _balance(areas: list) -> float:
    """1 quand toutes les videos ont la meme place, de moins en moins sinon
    (moyenne geometrique sur moyenne arithmetique)."""
    mean = sum(areas) / len(areas)
    if mean <= 0:
        return 0.0
    product = 1.0
    for area in areas:
        product *= max(area, 1e-9) / mean
    return product ** (1.0 / len(areas))


@lru_cache(maxsize=256)
def _best(counts: tuple, means: tuple, width: int, height: int, gap: int,
          full: bool = False) -> tuple:
    """La meilleure repartition en rangees, et sa part d'ecran montree.

    Une rangee de videos de formes a1..ak, sur toute la largeur L, a pour
    hauteur naturelle h = (L - ecarts) / (a1 + ... + ak). Si les rangees,
    empilees, depassent la hauteur de l'ecran, tout se reduit d'un meme
    facteur s ; sinon les cases s'etirent et les videos y gardent leur
    taille. Une video de forme a y montre (s h)^2 a d'image.

    On garde la repartition qui montre le plus, ponderee par l'equilibre des
    places (`_balance`) : beaucoup d'image, et chaque video lisible.

    `full` : tout l'ecran est couvert, quitte a rogner. Les rangees
    s'etirent toutes du meme facteur s, si bien que chaque video perd la
    meme part de son image (1 - s, ou 1 - 1/s) : jamais une seule tres
    zoomee pour boucher le trou des autres. On garde alors la repartition
    qui rogne le moins, ponderee de meme par l'equilibre des places.
    """
    best, seen, score = (), 0.0, -1.0
    for rows in _parts(counts, None):
        room = height - gap * (len(rows) - 1)
        if room <= 0:
            continue
        natural = []
        for part in rows:
            k = sum(part)
            total = sum(n * a for n, a in zip(part, means))
            natural.append((width - gap * (k - 1)) / total)
        if min(natural) <= 0:
            continue                # trop de videos pour si peu de place
        stretch = room / sum(natural)
        if full:
            # Les cases couvrent tout ; chaque video garde min(s, 1/s) de
            # son image.
            areas = []
            for part, h in zip(rows, natural):
                for n, a in zip(part, means):
                    areas.extend([a * h * h * stretch] * n)
            shown = min(stretch, 1.0 / stretch) * width * height
            value = shown * _balance(areas)
            if value > score + 1e-6:
                best, seen, score = rows, shown, value
            continue
        scale = min(1.0, stretch)
        areas = []
        for part, h in zip(rows, natural):
            side = (scale * h) ** 2
            for n, a in zip(part, means):
                areas.extend([side * a] * n)
        shown = sum(areas)
        value = shown * _balance(areas)
        if value > score + 1e-6:
            best, seen, score = rows, shown, value
    return best, seen / float(width * height)


def layout(aspects: list, width: int, height: int, gap: int = 8, full: bool = False) -> tuple:
    """Ou poser chaque video : ([(x, y, l, h) par video], forme, part montree).

    On essaie des rangees (pour un ecran large, le cas courant) et des
    colonnes (deux horizontales empilees a cote d'une verticale), et l'on
    garde ce qui montre le plus d'image. La forme est un resume de la
    disposition retenue : elle ne change pas tant que les familles de
    videos restent les memes, ce qui garde le mur immobile quand une video
    est remplacee par une autre de meme forme.

    `full` : l'ecran entier est couvert, chaque video rognee de la meme
    part ; la part montree rendue est alors celle de l'image gardee.
    """
    count = len(aspects)
    # Une fenetre pas encore a sa taille (quelques pixels) : rien a poser.
    if count == 0 or min(width, height) < 2 * gap + 40:
        return [], (), 0.0
    aspects = [a if a and a > 0 else WIDE for a in aspects]
    rows = _arrange(aspects, width, height, gap, full)
    cols = _arrange([1.0 / a for a in aspects], height, width, gap, full)
    if not rows[0] and not cols[0]:
        return [], (), 0.0
    if not rows[0] or cols[2] > rows[2] + 1e-6:
        rects = [(y, x, h, w) for x, y, w, h in cols[0]]
        rects, shape = rects, ("colonnes",) + cols[1]
    else:
        rects, shape = rows[0], ("rangées",) + rows[1]
    if full:
        return rects, shape, max(rows[2], cols[2])
    shown = _shown(rects, aspects, width, height)
    # La grille de cases egales reste candidate : trois horizontales y
    # tiennent mieux (deux en haut, une centree en bas) qu'en rangees
    # etirees. La mosaique ne fait ainsi jamais moins bien que l'ancien mur.
    grid = _grid(aspects, width, height, gap)
    if grid is not None and grid[2] > shown + 0.005:
        return grid
    return rects, shape, shown


def _shown(rects: list, aspects: list, width: int, height: int) -> float:
    """La part de l'ecran ou l'on voit de l'image, chaque video entiere
    dans sa case."""
    total = sum(min(w, h * a) * min(h, w / a) for (_x, _y, w, h), a in zip(rects, aspects))
    return total / float(width * height)


def _grid(aspects: list, width: int, height: int, gap: int):
    """La meilleure grille de cases egales (la derniere rangee centree)."""
    count = len(aspects)
    best = None
    for rows in range(1, count + 1):
        cols = -(-count // rows)
        if rows * cols - count >= cols:
            continue                # une rangee resterait vide
        cell_w = (width - gap * (cols - 1)) / cols
        cell_h = (height - gap * (rows - 1)) / rows
        if cell_w < 40 or cell_h < 40:
            continue
        rects = []
        for at in range(count):
            row, col = divmod(at, cols)
            in_row = min(cols, count - row * cols)
            shift = (cols - in_row) * (cell_w + gap) / 2
            x = round(shift + col * (cell_w + gap))
            y = round(row * (cell_h + gap))
            rects.append((x, y, round(cell_w), round(cell_h)))
        shown = _shown(rects, aspects, width, height)
        if best is None or shown > best[2] + 1e-6:
            best = (rects, ("grille", rows, cols), shown)
    return best


def _arrange(aspects: list, width: int, height: int, gap: int, full: bool = False) -> tuple:
    families = [family(a) for a in aspects]
    counts = tuple(families.count(f) for f in range(3))
    # La repartition se choisit sur la forme type de chaque famille, pas sur
    # les formes exactes : une verticale remplacee par une autre verticale
    # (9:16 puis 3:5) laisse la mosaique telle quelle. Les largeurs, ensuite,
    # suivent les vraies formes.
    means = FAMILY_SHAPES
    # La repartition ne depend que des proportions de l'ecran : arrondie a
    # 40 pixels, elle se retrouve en memoire pendant qu'on etire la fenetre.
    rows, shown = _best(counts, means, max(40, round(width / 40) * 40),
                        max(40, round(height / 40) * 40), int(gap), bool(full))
    if not rows:
        return [], (), 0.0
    # Les videos de chaque famille vont aux rangees dans leur ordre : le
    # panneau 3 reste a sa place quand sa video est remplacee par une autre
    # de la meme famille.
    waiting = {fam: [i for i, f in enumerate(families) if f == fam] for fam in range(3)}
    members = []
    for part in rows:
        row = []
        for fam, n in enumerate(part):
            row.extend(waiting[fam][:n])
            waiting[fam] = waiting[fam][n:]
        members.append(sorted(row))
    # Les rangees dans l'ordre de leurs panneaux : le premier panneau en haut.
    members.sort(key=lambda row: row[0])
    natural = [(width - gap * (len(row) - 1)) / sum(aspects[i] for i in row)
               for row in members]
    room = height - gap * (len(members) - 1)
    stacked = sum(natural)
    # Les cases remplissent tout : les rangees se partagent la hauteur au
    # prorata de leur hauteur naturelle.
    rects = [None] * len(aspects)
    y = 0.0
    for at, (row, h) in enumerate(zip(members, natural)):
        tall = room * h / stacked
        top = round(y)
        bottom = height if at == len(members) - 1 else round(y + tall)
        span = width - gap * (len(row) - 1)
        total = sum(aspects[i] for i in row)
        x = 0.0
        for pos, i in enumerate(row):
            left = round(x)
            right = width if pos == len(row) - 1 else round(x + span * aspects[i] / total)
            rects[i] = (left, top, right - left, bottom - top)
            x += span * aspects[i] / total + gap
        y += tall + gap
    shape = tuple(tuple(families[i] for i in row) for row in members)
    return rects, shape, shown
