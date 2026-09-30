"""Aide-mémoire : tous les raccourcis, et la syntaxe de recherche.

Cette table est la seule source : la fiche affichée à l'écran en sort, et un
test la confronte au code des touches. Ajouter un raccourci sans l'inscrire ici
fait échouer le jeu de tests — c'est ce qui garantit que la fiche reste vraie.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

# (section, [(touche, ce qu'elle fait)])
SHORTCUTS = [
    ("Trier", [
        ("1 à 5, 0", "1 à 5 : en favori ; 0 : retiré des favoris"),
        ("6, 7, 8… puis A, Z, E…", "l'envoyer vers la destination de cette touche"),
        ("Espace", "passer au suivant sans rien décider"),
        ("Suppr", "le supprimer : il passe par la corbeille de session "
                  "(Ctrl+Z le reprend) — sur le NAS, détruit pour de bon à la "
                  "fermeture"),
        ("Planche et mur", "Suppr, les destinations, 1 à 5 et 0 visent la vignette "
                           "ou le panneau sous la souris, ou les éléments cochés ; "
                           "sinon, rien"),
        ("Clic sur ☆", "sur la vignette survolée ou le bandeau d'un panneau du "
                       "mur : en favori, ou retiré"),
        ("F2", "renommer ce qu'on regarde, sur place : Entrée valide, Échap "
               "annule ; l'extension reste"),
        ("Ctrl+Z", "annuler la dernière décision, ou le dernier renommage"),
        ("Ctrl+B", "ouvrir la corbeille de session"),
        ("⋯ → Passer à la suivante après ★", "sur la fiche d'une vidéo, 1 à 5 "
                                             "passent aussi à la suivante"),
    ]),
    ("Se déplacer", [
        ("← →", "élément précédent, suivant — sans sortir du dossier si « rester "
                "dans ce dossier » est cochée ; sur la planche, page précédente, "
                "suivante"),
        ("Molette", "avancer ou reculer dans la vidéo"),
        ("Ctrl+↓", "entrer dans le dossier affiché"),
        ("Ctrl+↑", "remonter au dossier parent"),
        ("Échap", "quitter le plein écran, puis la fiche — vers le mur si elle "
                  "en vient —, puis remonter d'un dossier"),
        ("Alt+←", "revenir à l'endroit visité juste avant"),
        ("Ctrl+←/→", "page précédente, suivante : de vignettes sur la planche, "
                     "d'aperçus sur une fiche"),
        ("Ctrl+H, ou le dé", "une vidéo au hasard dans toute la collection : "
                             "aucune ne revient avant que toutes soient passées"),
        ("« Au hasard ici »", "de même, dans ce dossier ou dans la liste affichée"),
        ("Vidéos | Photos", "en haut à gauche : passer d'une collection à "
                            "l'autre, à sa dernière racine, sur « Dossiers »"),
    ]),
    ("Regarder", [
        ("F", "la fiche de la vidéo sous la souris : vignette, aperçu, panneau "
              "du mur"),
        ("Entrée, clic sur l'image", "pause, reprise"),
        ("Ctrl+L", "lecteur flottant : la vidéo dans une fenêtre toujours "
                   "devant, sans cadre ; on la déplace par son titre, on "
                   "l'agrandit par ses bords ; Échap y revient"),
        ("Photos : ⏯, Entrée, clic", "lancer ou arrêter le diaporama — dans "
                                     "le dossier si la case est cochée ; sur "
                                     "le mur, chaque panneau a le sien"),
        ("F11, Alt+Entrée, Ctrl+J", "plein écran : l'image seule, sans rien "
                                    "autour — sur le mur, le mur seul"),
        ("Double-clic sur l'image", "plein écran, et retour ; sur la vidéo "
                                    "ouverte à côté, sa fiche en plein écran"),
        ("Ctrl+P", "basculer entre la planche et la fiche"),
        ("Ctrl+M", "couper ou rendre le son"),
        ("Ctrl+molette", "zoomer dans l'image"),
        ("Clic droit sur l'image", "les destinations en rond autour de la souris : "
                                   "en cliquer une envoie la vidéo, sans rien arrêter"),
        ("Maj (maintenue)", "neuf instants en mosaïque ; cliquer une case y va"),
        ("Glisser sur l'image", "avancer ou reculer : toute la largeur, toute la durée"),
        ("Clic droit sur une vignette", "l'ouvrir à côté, sans quitter la planche"),
    ]),
    ("Choisir et agir", [
        ("Ctrl+A", "cocher tout"),
        ("Ctrl+N", "tout décocher"),
        ("Ctrl+I", "inverser la sélection"),
        ("Ctrl+E", "montrer dans l'explorateur : la vignette ou le panneau "
                   "survolé, sinon ce qu'on regarde"),
        ("Ctrl+O", "l'ouvrir dans le lecteur du système"),
        ("Ctrl+D", "modifier les destinations"),
        ("Ctrl+T", "afficher ou masquer l'arborescence"),
        ("Ctrl+F", "aller au champ de recherche"),
        ("Ctrl+R", "réanalyser ce dossier en entier — à la racine, toute la "
                   "collection, après confirmation"),
        ("Ctrl+K", "passer à autre chose — Ctrl+K, Échap ou un double-clic pour "
                   "revenir (le code PIN, s'il est posé : ⋯ › Affichage)"),
        ("Ctrl+Alt+K", "la même chose depuis n'importe quelle fenêtre — sans jamais "
                       "ramener Prisme"),
        ("F1", "cette fiche"),
        ("« Non vus »", "ne garder que ce qui n'a été ni décidé ni regardé"),
        ("⋯ → Rafale", "la suivante arrive toute seule après 8 s sans décision"),
        ("⋯ → Doublons", "le meilleur de chaque groupe est marqué « ✓ à garder », "
                         "les autres sont cochés d'office ; « Pas des doublons » "
                         "retire un groupe pour de bon"),
        ("⋯ → Empreintes", "sonde ce qui manque ; ensuite les doublons sortent sans toucher au disque"),
        ("⋯ → Journal des gels", "quand l'interface s'est figée, combien de temps, après quoi"),
        ("⋯ → Analyser les titres", "lit le titre des métadonnées de chaque vidéo, pour les mots fréquents"),
        ("⋯ → Repérer les plans", "les vignettes se posent sur des changements de plan, non sur des fractions"),
        ("« À trier », en tête", "les fichiers dont le nom ne porte aucun vrai mot "
                                 "(0x56b47…, IMG_2041) : à regarder pour les ranger"),
        ("« Orphelins », en tête", "les fichiers posés directement dans un dossier « + » "
                                   "ou à la racine, hors de tout sous-dossier"),
        ("Bouton ⧉ en haut", "active ou coupe le lecteur flottant automatique ; "
                            "✕ sur le lecteur le ferme sans ramener Prisme"),
        ("⋯ → Durée du diaporama…", "combien de secondes chaque photo reste, "
                                    "au diaporama comme au mur (6 par défaut)"),
        ("⋯ → Dossiers masqués…","les noms de dossiers cachés de la recherche et des listes ; "
                                  "en ajouter, ou en décocher un pour le réafficher"),
        ("⋯ → Afficher les dossiers masqués", "montre, ou remasque, tous ces dossiers d'un coup"),
        ("⋯ → Partage à distance", "le mot de passe, l'adresse publique à scanner, "
                                   "et qui a regardé quoi"),
        ("⋯ → Où sont les vignettes", "leur dossier, et comment le partager avec un autre PC"),
        ("⋯ → Ignorer la mise à l'échelle", "sur un écran agrandi, rend à la fenêtre une taille qui tient"),
    ]),
    ("Le mur", [
        ("◂ ⏯ ▸", "la précédente du panneau ; pause ; une autre au hasard, sans "
                  "remise — ou la suivante du même dossier si « rester dans ce "
                  "dossier » est cochée"),
        ("☆ ⌸ ⛶", "en favori ; montrer le fichier dans l'explorateur ; cette "
                  "vidéo seule en grand"),
        ("F sur un panneau", "ouvrir sa fiche (Échap ramène au mur)"),
        ("Clic sur un panneau", "pause, reprise"),
        ("Clic droit sur un panneau", "les destinations, pour le ranger sans quitter le mur"),
        ("Maj + clic droit", "ses neuf instants ; cliquer une case y va"),
        ("Molette sur un panneau", "avancer ou reculer dans cette vidéo seulement"),
    ]),
]

SEARCH_HELP = [
    ("plage montagne", "les deux mots sont exigés"),
    ("plage or mer, plage ou mer", "l'un ou l'autre suffit"),
    ("plage -hiver", "« plage », mais pas « hiver »"),
    ('"saison 2"', "l'expression exacte, espaces compris"),
    ("~montagne", "à peu près : « mongagne » et « Montaigne » aussi"),
    ("⋯ → Enregistrer cette recherche", "la retrouver d'un clic, avec son tri et « Non vus »"),
]

HELP_STYLE = """
QDialog { background: #0e1116; }
QLabel#helpSection { color: #ffffff; font-size: 14px; font-weight: 600;
                     padding: 14px 0 4px 0; }
QLabel#helpBody { color: #b9c2cd; font-size: 13px; }
QLabel#helpIntro { color: #8b94a1; font-size: 13px; }
"""


def _table(rows: list) -> str:
    """Deux colonnes, en HTML : la touche, puis ce qu'elle fait.

    Toute la largeur, et des lignes qui se replient : en « nowrap », la fiche
    defilait a l'horizontale des qu'une description etait un peu longue.
    """
    lines = []
    for key, what in rows:
        lines.append(
            f'<tr><td width="36%" style="padding:2px 14px 2px 0;'
            f'color:#e9eef4"><b>{key}</b></td>'
            f'<td style="padding:2px 0">{what}</td></tr>'
        )
    return '<table width="100%">' + "".join(lines) + "</table>"


class HelpDialog(QDialog):
    """La fiche des raccourcis, telle que la table la décrit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Raccourcis et recherche")
        self.setStyleSheet(HELP_STYLE)
        self.resize(640, 700)

        layout = QVBoxLayout(self)
        inner = QWidget(self)
        body = QVBoxLayout(inner)
        body.setContentsMargins(4, 0, 10, 0)

        intro = QLabel(
            "Les commandes sont sur Ctrl, sur les touches F1 à F12 ou de "
            "navigation : chiffres et lettres restent aux destinations — sauf "
            "0 à 5, pour le favori, et F, pour la fiche survolée.", inner)
        intro.setObjectName("helpIntro")
        intro.setWordWrap(True)
        body.addWidget(intro)

        for title, rows in SHORTCUTS:
            head = QLabel(title, inner)
            head.setObjectName("helpSection")
            body.addWidget(head)
            table = QLabel(_table(rows), inner)
            table.setObjectName("helpBody")
            table.setTextFormat(Qt.RichText)
            table.setWordWrap(True)
            body.addWidget(table)

        head = QLabel("Chercher", inner)
        head.setObjectName("helpSection")
        body.addWidget(head)
        search = QLabel(_table(SEARCH_HELP), inner)
        search.setObjectName("helpBody")
        search.setTextFormat(Qt.RichText)
        search.setWordWrap(True)
        body.addWidget(search)

        note = QLabel(
            "La casse et les accents sont ignorés : « Été » trouve « ete ». "
            "Quand rien d'exact ne sort, l'à-peu-près est tenté tout seul.",
            inner)
        note.setObjectName("helpIntro")
        note.setWordWrap(True)
        body.addWidget(note)
        body.addStretch(1)

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        # Jamais de defilement horizontal : le texte se replie a la largeur.
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(inner)
        layout.addWidget(area, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText("Fermer")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)


def documented_keys() -> set:
    """Touches citees par la fiche, pour que le test puisse la confronter :
    les lettres (« Ctrl+E ») et les touches de fonction (« F2 », « F11 »)."""
    found = set()
    for _title, rows in SHORTCUTS:
        for key, _what in rows:
            for piece in re.split(r"[\s+/,]+", key):
                if len(piece) == 1 and piece.isalpha():
                    found.add(piece.upper())
                elif re.fullmatch(r"F\d{1,2}", piece):
                    found.add(piece)
    return found
