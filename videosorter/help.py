"""Aide-mémoire : tous les raccourcis, et la syntaxe de recherche.

Cette table est la seule source : la fiche affichée à l'écran en sort, et un
test la confronte au code des touches. Ajouter un raccourci sans l'inscrire ici
fait échouer le jeu de tests — c'est ce qui garantit que la fiche reste vraie.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

# (section, [(touche, ce qu'elle fait)])
SHORTCUTS = [
    ("Trier", [
        ("0 à 5", "1 à 5 : en favori ; 0 : retiré des favoris"),
        ("6, 7, 8… puis A, Z, E…", "l'envoyer vers la destination de cette touche"),
        ("Espace", "passer au suivant sans rien décider"),
        ("Suppr", "l'écarter dans la corbeille de session"),
        ("Planche et mur", "Suppr, les destinations et 0 à 5 visent la vignette "
                           "ou le panneau sous la souris, ou les éléments cochés ; "
                           "sinon, rien"),
        ("Ctrl+Z", "annuler la dernière décision"),
        ("Ctrl+B", "ouvrir la corbeille de session"),
    ]),
    ("Se déplacer", [
        ("← →", "élément précédent, suivant ; sur la planche, page précédente, suivante"),
        ("Molette", "avancer ou reculer dans la vidéo"),
        ("Ctrl+↓", "entrer dans le dossier affiché"),
        ("Échap", "quitter le cinéma, puis la fiche, puis le dossier"),
        ("Ctrl+←/→", "page d'aperçus précédente, suivante"),
        ("Ctrl+H", "une vidéo au hasard, partout"),
    ]),
    ("Regarder", [
        ("Ctrl+J", "cinéma : l'image seule, sans rien autour"),
        ("Ctrl+J sur le mur", "le mur seul, plein écran — Échap pour revenir"),
        ("Ctrl+P", "basculer entre la planche et la fiche"),
        ("Ctrl+M", "couper ou rendre le son"),
        ("Ctrl+molette", "zoomer dans l'image"),
        ("Clic droit sur l'image", "les destinations en rond autour de la souris : "
                                   "en cliquer une envoie la vidéo, sans rien arrêter"),
        ("Maj (maintenue)", "neuf instants en mosaïque ; cliquer une case y va"),
        ("Glisser sur l'image", "avancer ou reculer : toute la largeur, toute la durée"),
        ("Clic sur l'image", "pause, reprise"),
        ("Clic droit sur une vignette", "l'ouvrir à côté, sans quitter la planche"),
    ]),
    ("Choisir et agir", [
        ("Ctrl+A", "cocher tout"),
        ("Ctrl+N", "tout décocher"),
        ("Ctrl+I", "inverser la sélection"),
        ("Ctrl+E", "ouvrir le dossier, le fichier déjà sélectionné"),
        ("Ctrl+O", "l'ouvrir dans le lecteur du système"),
        ("Ctrl+D", "modifier les destinations"),
        ("Ctrl+T", "afficher ou masquer l'arborescence"),
        ("Ctrl+F", "aller au champ de recherche"),
        ("Ctrl+R", "tout réanalyser"),
        ("Ctrl+K", "passer à autre chose — Échap ou un double-clic pour revenir"),
        ("Chip « Note »", "n'afficher que les éléments notés ainsi — exactement, pas « au moins »"),
        ("Chip « Non vus »", "ne garder que ce qui n'a été ni décidé ni regardé"),
        ("⋯ → Rafale", "la suivante arrive toute seule après 8 s sans décision"),
        ("⋯ → Journal des gels", "quand l'interface s'est figée, combien de temps, après quoi"),
        ("⋯ → Analyser les titres", "lit le titre des métadonnées de chaque vidéo, pour les mots fréquents"),
        ("⋯ → Repérer les plans", "les vignettes se posent sur des changements de plan, non sur des fractions"),
        ("⋯ → Empreintes", "sonde ce qui manque ; ensuite les doublons sortent sans toucher au disque"),
        ("⋯ → Afficher les dossiers masqués", "montre, ou remasque, « BIN » et ce qu'il contient"),
        ("⋯ → Partage à distance", "le mot de passe, l'adresse publique à scanner, "
                                   "et qui a regardé quoi"),
        ("⋯ → Où sont les vignettes", "leur dossier, et comment le partager avec un autre PC"),
        ("⋯ → Ignorer la mise à l'échelle", "sur un écran agrandi, rend à la fenêtre une taille qui tient"),
        ("Mur : ▸ ⚄ ⤢ ⛶", "la suivante du même dossier ; une autre n'importe où ; "
                            "ouvrir sa fiche ; cette vidéo seule en grand"),
        ("Mur : clic droit sur un panneau", "les destinations, pour le ranger sans quitter le mur"),
        ("Mur : Maj + clic droit", "ses neuf instants ; cliquer une case y va"),
    ]),
]

SEARCH_HELP = [
    ("plage montagne", "les deux mots sont exigés"),
    ("plage or mer", "l'un ou l'autre suffit"),
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
    """Deux colonnes alignees, en HTML : la touche, puis ce qu'elle fait."""
    lines = []
    for key, what in rows:
        lines.append(
            f'<tr><td style="padding:2px 18px 2px 0;color:#e9eef4;'
            f'white-space:nowrap"><b>{key}</b></td>'
            f'<td style="padding:2px 0">{what}</td></tr>'
        )
    return "<table>" + "".join(lines) + "</table>"


class HelpDialog(QDialog):
    """La fiche des raccourcis, telle que la table la décrit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Raccourcis et recherche")
        self.setStyleSheet(HELP_STYLE)
        self.resize(620, 680)

        layout = QVBoxLayout(self)
        inner = QWidget(self)
        body = QVBoxLayout(inner)
        body.setContentsMargins(4, 0, 10, 0)

        intro = QLabel(
            "Les commandes sont sur Ctrl ou sur des touches de navigation : "
            "chiffres et lettres restent libres pour les destinations.", inner)
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
            body.addWidget(table)

        head = QLabel("Chercher", inner)
        head.setObjectName("helpSection")
        body.addWidget(head)
        search = QLabel(_table(SEARCH_HELP), inner)
        search.setObjectName("helpBody")
        search.setTextFormat(Qt.RichText)
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
        area.setWidget(inner)
        layout.addWidget(area, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText("Fermer")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        layout.addWidget(box)


def documented_keys() -> set:
    """Touches citees par la fiche, pour que le test puisse la confronter."""
    found = set()
    for _title, rows in SHORTCUTS:
        for key, _what in rows:
            for piece in key.replace("+", " ").replace("/", " ").split():
                if len(piece) == 1 and piece.isalpha():
                    found.add(piece.upper())
    return found
