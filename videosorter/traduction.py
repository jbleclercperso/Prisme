"""L'aide à la traduction de la recherche : le français trouve l'anglais, et
l'inverse.

Les noms de fichiers sont souvent en anglais : « deux filles » ne trouvait
rien quand « two girls » trouvait tout. Chaque mot cherché devient donc « lui
ou ses traductions » (`query.parse`). Un petit dictionnaire, tenu ici, pour
le vocabulaire des noms de vidéos -- sans réseau, sans service en ligne.

La recherche se fait par morceau de nom (« girl » trouve « girls ») : on
donne la forme courte, et l'on écarte les mots anglais trop courts qui se
cachent dans d'autres (« ass » dans « class », « men » dans « women »).

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

from .textfold import fold

# Francais -> anglais, deja sans accents. Les traductions sont des debuts de
# mots : « masturbat » trouve « masturbation » et « masturbating ».
FR_EN = {
    # les nombres
    "deux": ["two"], "trois": ["three"], "quatre": ["four"], "cinq": ["five"],
    # les gens
    "fille": ["girl"], "femme": ["woman", "women", "wife"], "epouse": ["wife"],
    "copine": ["girlfriend"], "copain": ["boyfriend"], "mari": ["husband"],
    "mec": ["guy"], "amie": ["friend"], "ami": ["friend"], "voisine": ["neighbo"],
    "voisin": ["neighbo"], "mere": ["mom", "mother"], "maman": ["mom"],
    "belle-mere": ["stepmom"], "soeur": ["sister"], "demi-soeur": ["stepsis"],
    "pere": ["dad", "father"], "beau-pere": ["stepdad"], "frere": ["brother"],
    "tante": ["aunt"], "cousine": ["cousin"], "etudiante": ["student", "college"],
    "prof": ["teacher"], "professeur": ["teacher"], "infirmiere": ["nurse"],
    "docteur": ["doctor"], "medecin": ["doctor"], "secretaire": ["secretary"],
    "patronne": ["boss"], "patron": ["boss"], "serveuse": ["waitress"],
    "inconnue": ["stranger"], "inconnu": ["stranger"], "touriste": ["tourist"],
    "mannequin": ["model"], "lesbienne": ["lesbian"], "bisexuel": ["bisexual"],
    "mature": ["mature"], "cougar": ["cougar", "milf"],
    # le corps
    "seins": ["tits", "boobs", "breast"], "sein": ["tits", "boobs", "breast"],
    "poitrine": ["breast", "boobs"], "fesses": ["butt", "booty"], "cul": ["butt", "booty"],
    "chatte": ["pussy"], "bite": ["cock", "dick"], "queue": ["cock", "dick"],
    "jambes": ["legs"], "pieds": ["feet", "foot"], "pied": ["feet", "foot"],
    "bouche": ["mouth"], "visage": ["face"], "langue": ["tongue"], "cheveux": ["hair"],
    "poilue": ["hairy"], "poilu": ["hairy"], "rasee": ["shaved"], "rase": ["shaved"],
    "gros": ["big", "fat"], "grosse": ["big", "chubby", "bbw"], "enorme": ["huge", "giant"],
    "petite": ["petite", "small", "tiny"], "mince": ["skinny", "slim"],
    "ronde": ["curvy", "chubby"], "tatouee": ["tattoo"], "tatouage": ["tattoo"],
    # les cheveux, les origines
    "blonde": ["blonde"], "brune": ["brunette"], "rousse": ["redhead", "ginger"],
    "noire": ["ebony", "black"], "asiatique": ["asian"], "japonaise": ["japan"],
    "chinoise": ["chinese"], "coreenne": ["korean"], "francaise": ["french"],
    "allemande": ["german"], "russe": ["russian"], "italienne": ["italian"],
    "espagnole": ["spanish"], "latine": ["latina"], "arabe": ["arab"],
    "africaine": ["african"], "indienne": ["indian"], "bresilienne": ["brazil"],
    "americaine": ["american"], "anglaise": ["british", "english"],
    # ce qu'on fait
    "baise": ["fuck"], "baiser": ["fuck", "kiss"], "sucer": ["suck"], "suce": ["suck"],
    "pipe": ["blowjob"], "fellation": ["blowjob"], "lecher": ["lick"], "leche": ["lick"],
    "sodomie": ["anal"], "branlette": ["handjob"], "masturbation": ["masturbat"],
    "masturbe": ["masturbat"], "jouir": ["orgasm"], "orgasme": ["orgasm"],
    "ejaculation": ["cumshot"], "faciale": ["facial"], "embrasser": ["kiss"],
    "bisou": ["kiss"], "caresse": ["caress"], "doigt": ["finger"], "doigte": ["finger"],
    "levrette": ["doggy"], "missionnaire": ["missionary"], "trio": ["threesome"],
    "partouze": ["orgy"], "orgie": ["orgy"], "echangiste": ["swinger"],
    "infidele": ["cheat"], "tromper": ["cheat"], "trompe": ["cheat"], "cocu": ["cuckold"],
    "soumise": ["submissive"], "soumis": ["submissive"], "maitresse": ["mistress"],
    "esclave": ["slave"], "attachee": ["tied", "bondage"], "attache": ["tied", "bondage"],
    "fessee": ["spank"], "pisse": ["piss"], "fontaine": ["squirt"],
    "danse": ["dance"], "strip-tease": ["striptease"], "douche": ["shower"],
    # les jouets, les habits
    "jouet": ["toy"], "gode": ["dildo"], "vibromasseur": ["vibrator"],
    "bas": ["stocking"], "collant": ["pantyhose"], "culotte": ["panties"],
    "jupe": ["skirt"], "robe": ["dress"], "talons": ["heels"], "bottes": ["boots"],
    "lunettes": ["glasses"], "uniforme": ["uniform"], "maillot": ["swimsuit"],
    "nue": ["naked", "nude"], "nues": ["naked", "nude"], "deshabille": ["undress"],
    # les lieux
    "plage": ["beach"], "piscine": ["pool"], "bain": ["bath"], "baignoire": ["bathtub"],
    "chambre": ["bedroom"], "cuisine": ["kitchen"], "canape": ["couch", "sofa"],
    "bureau": ["office"], "foret": ["forest"], "jardin": ["garden"], "parc": ["park"],
    "montagne": ["mountain"], "campagne": ["countryside"], "dehors": ["outdoor", "outside"],
    "exterieur": ["outdoor", "outside"], "bateau": ["boat"], "vestiaire": ["locker"],
    "toilettes": ["toilet"], "escalier": ["stairs"], "balcon": ["balcony"],
    "fenetre": ["window"], "salle de sport": ["gym"],
    # le reste
    "soiree": ["party"], "fete": ["party"], "vacances": ["vacation", "holiday"],
    "nuit": ["night"], "matin": ["morning"], "noel": ["christmas", "xmas"],
    "anniversaire": ["birthday"], "mariage": ["wedding"], "premiere": ["first"],
    "vrai": ["real"], "reel": ["real"], "cachee": ["hidden"], "camera": ["camera", "cam"],
    "film": ["movie"], "maison": ["home"], "romantique": ["romantic"],
    "sensuelle": ["sensual"], "sensuel": ["sensual"], "amour": ["love"],
    "douce": ["gentle", "soft"], "doux": ["gentle", "soft"], "sauvage": ["wild"],
    "brutal": ["rough"], "lent": ["slow"], "chaude": ["horny"], "excitee": ["horny"],
    "belle": ["beautiful", "pretty"], "jolie": ["pretty", "cute"], "mignonne": ["cute"],
    "magnifique": ["gorgeous"], "parfaite": ["perfect"], "seule": ["solo", "alone"],
}

# Anglais -> francais : le meme dictionnaire, a l'envers.
EN_FR: dict = {}
for _fr, _ens in FR_EN.items():
    for _en in _ens:
        if _en != _fr:
            EN_FR.setdefault(_en, [])
            if _fr not in EN_FR[_en]:
                EN_FR[_en].append(_fr)
EN_FR.update({"girls": EN_FR["girl"], "women": ["femme"], "boobs": EN_FR["boobs"],
              "ladies": ["femme"], "lady": ["femme"], "teacher": ["prof"]})


def _forms(term: str) -> list:
    """Le mot, puis ses formes sans pluriel : « filles » → « fille »."""
    forms = [term]
    if len(term) > 4:
        if term.endswith("ies"):
            forms.append(term[:-3] + "y")
        if term.endswith("es"):
            forms.append(term[:-2])
        if term[-1] in "sx":
            forms.append(term[:-1])
    return forms


def translations(term: str) -> list:
    """Les traductions d'un mot deja replie (aucune s'il n'est pas connu)."""
    term = fold(term).strip()
    if len(term) < 2:
        return []
    for form in _forms(term):
        found = FR_EN.get(form) or EN_FR.get(form)
        if found:
            return [word for word in found if word != term]
    return []


def expand(group: list) -> list:
    """Un groupe d'alternatives (`query.parse`), et les traductions de
    chacune, sans doublon."""
    out = list(group)
    for term in group:
        if term.startswith("~"):
            continue
        for word in translations(term):
            if word not in out:
                out.append(word)
    return out
