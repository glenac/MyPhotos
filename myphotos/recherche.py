"""Recherche par mots : la requête devient un vecteur, comparé à celui de chaque photo analysée.

Le cosinus entre les deux vecteurs donne un score ; modele.probabilite le convertit en probabilité
(0 à 1, calibrée par le modèle) que la photo corresponde. Deux seuils, réglés pour chaque moteur
(REGLAGES), séparent les résultats sûrs des « peut-être ».

Dates et lieux (0.5.0) : requete.py lit les dates et les lieux d'une recherche ; la recherche par mots peut alors être
limitée aux photos de ces dates et de ces lieux (Filtre), avec les mêmes seuils et le même classement. Ce module donne
aussi la façon dont MyPhotos reconnaît un lieu dans une recherche (chercheur_lieux, idents_des_lieux).
"""
import re
import threading
import time
import unicodedata
from collections import OrderedDict

import numpy as np

from . import lieux as _lieux
from . import modele

# Réglages par moteur, mesurés sur un jeu d'images publiques étiquetées (outils/evaluation.py, résumé dans
# outils/EVALUATION.md). Lus à chaque recherche, donc modifiables à chaud.
#   gabarit : phrase donnée au modèle, {q} = la requête ; « de {q} » s'élide devant une voyelle (« d'enfant ») ;
#   seuil_sur : probabilité à partir de laquelle une photo est un résultat sûr ;
#   seuil_peut_etre : … un résultat « moins sûr » ;
#   minimum : si moins de photos que cela atteignent seuil_peut_etre, montrer quand même les meilleures jusqu'à
#             ce nombre (0 = non), pourvu que leur score se détache de la masse de la collection d'au moins
#             ecart_minimum écarts-types robustes (None = sans condition).
# Les seuils sont bas parce que le modèle est très prudent : une vraie photo a une probabilité médiane de 0,001
# à 0,003. Mesures du 23/11/2026 (306 images, 24 catégories) : « sûres » précises à 93-94 % et 68-75 % des vraies
# photos ; avec les « moins sûres », 84-90 % des vraies photos. Le repli « minimum » n'apportait rien : 0 partout.
REGLAGES = {
    'rapide': {'gabarit': '{q}', 'seuil_sur': 5e-4, 'seuil_peut_etre': 1e-4, 'minimum': 0, 'ecart_minimum': None},
    'base': {'gabarit': '{q}', 'seuil_sur': 8e-4, 'seuil_peut_etre': 2e-4, 'minimum': 0, 'ecart_minimum': None},
    'precis': {'gabarit': '{q}', 'seuil_sur': 5e-4, 'seuil_peut_etre': 1.5e-4, 'minimum': 0, 'ecart_minimum': None},
    'tres_precis': {'gabarit': '{q}', 'seuil_sur': 6e-4, 'seuil_peut_etre': 1.5e-4, 'minimum': 0,
                    'ecart_minimum': None},
}
# Moteur sans réglages mesurés : les seuils les plus exigeants des quatre.
REGLAGES_REPLI = {'gabarit': '{q}', 'seuil_sur': 8e-4, 'seuil_peut_etre': 2e-4, 'minimum': 0, 'ecart_minimum': None}

# Anciennes constantes, gardées pour la compatibilité : elles valent les réglages du moteur par défaut. Les
# modifier à chaud force la valeur pour tous les moteurs (un MINIMUM_RESULTATS forcé s'applique sans condition).
GABARIT = REGLAGES[modele.MODELE_DEFAUT]['gabarit']
SEUIL_SUR = REGLAGES[modele.MODELE_DEFAUT]['seuil_sur']
SEUIL_PEUT_ETRE = REGLAGES[modele.MODELE_DEFAUT]['seuil_peut_etre']
MINIMUM_RESULTATS = REGLAGES[modele.MODELE_DEFAUT]['minimum']
_ALIAS = {'GABARIT': 'gabarit', 'SEUIL_SUR': 'seuil_sur', 'SEUIL_PEUT_ETRE': 'seuil_peut_etre',
          'MINIMUM_RESULTATS': 'minimum'}
_ALIAS_INITIAUX = {nom: globals()[nom] for nom in _ALIAS}

LONGUEUR_MAX = 200           # caractères gardés de la requête
TAILLE_CACHE = 64            # vecteurs de requêtes gardés en mémoire
RECHARGEMENT_MIN_S = 1.0     # la matrice des vecteurs est rechargée au plus une fois par seconde

_ESPACES = re.compile(r'\s+')
_VOYELLES = tuple('aeiouàâäéèêëîïôöûüœæ')
_DE = re.compile(r'\bde \{q\}')


def reglages(cle):
    """Réglages du moteur `cle` (copie), y compris les forçages à chaud des anciennes constantes."""
    r = dict(REGLAGES.get(cle) or REGLAGES_REPLI)
    g = globals()
    for nom, champ in _ALIAS.items():
        if g[nom] != _ALIAS_INITIAUX[nom]:
            r[champ] = g[nom]
            if champ == 'minimum':
                r['ecart_minimum'] = None
    return r


def appliquer_gabarit(gabarit, requete):
    """Phrase donnée au modèle : 'une photo de {q}' + 'enfant' -> « une photo d'enfant »."""
    if requete[:1].lower() in _VOYELLES:
        gabarit = _DE.sub("d'{q}", gabarit)
    return gabarit.replace('{q}', requete)


def ecarts(scores):
    """Écart de chaque score à la masse de la collection, en écarts-types robustes (médiane, écart absolu médian)."""
    med = np.median(scores)
    return (scores - med) / max(float(np.median(np.abs(scores - med))) * 1.4826, 1e-6)


class MoteurIndisponible(RuntimeError):
    """Le moteur de texte n'est pas (encore) disponible ; le message peut s'afficher tel quel."""


def normaliser_requete(texte):
    """Forme Unicode composée, espaces réduits, 200 caractères au plus. '' si rien d'utile."""
    if not texte:
        return ''
    t = _ESPACES.sub(' ', unicodedata.normalize('NFC', str(texte))).strip()
    return t[:LONGUEUR_MAX].rstrip()


class Recherche:
    """Cherche des photos par mots.

    base : base.Base ; fournisseur_moteur() -> modele.Moteur avec la partie texte chargée, appelé au
    premier besoin puis gardé (oublier_moteur() pour en changer) ; cle_modele : modèle dont on compare
    les vecteurs (le même que celui de l'indexeur).
    """

    def __init__(self, base, fournisseur_moteur, cle_modele):
        self.base = base
        self.fournisseur_moteur = fournisseur_moteur
        self.cle_modele = cle_modele
        self._moteur = None
        self._verrou_moteur = threading.Lock()
        self._cache = OrderedDict()
        self._verrou_cache = threading.Lock()
        self._verrou_matrice = threading.Lock()
        self._ids = np.empty(0, np.int64)
        self._matrice = None
        self._dates = np.empty(0, 'datetime64[s]')
        self._generation = None
        self._controle = 0.0                     # dernier coup d'œil à base.generation()
        self._intervalle = RECHARGEMENT_MIN_S
        self._chargement = 0                     # numéro de la matrice chargée (change à chaque rechargement)
        self._dernier_classement = None          # « plus comme celle-ci » : (photo, chargement, classement)
        self._verrou_classement = threading.Lock()

    # ------------------------------------------------ recherche

    def chercher(self, texte, debut=0, n=200, proches=False, filtre=None):
        """Photos qui correspondent au texte, de la plus probable à la moins probable.

        Renvoie None si le texte est vide (afficher alors les photos par date), sinon
        {requete, total_sures, total_peut_etre, total_proches, sures: [{id, score, proba}], peut_etre: [...],
        debut, analysees, duree_ms}. La page [debut, debut + n) court sur la liste complète : les sûres d'abord,
        puis les « peut-être ». score = cosinus, proba = probabilité calibrée. total_proches : photos analysées
        restantes, sous les seuils.

        proches=True : ces photos restantes, de la plus proche à la moins proche, pour qui veut aller voir au-delà
        des résultats ({…, proches: [...]} ; la page [debut, debut + n) court sur elles seules). Les seuils
        (REGLAGES) sont les mêmes : rien n'est « repêché » parmi les résultats.

        filtre (0.5.0) : Filtre (ou tout objet qui a masque(ids, dates)) : seules les photos qu'il garde sont
        classées, avec les mêmes seuils ; analysees compte alors ces photos-là. Sans filtre, rien ne change.
        """
        t0 = time.perf_counter()
        requete = normaliser_requete(texte)
        if not requete:
            return None
        debut = max(0, int(debut))
        n = None if n is None else max(0, int(n))
        r = reglages(self.cle_modele)
        q = self._vecteur_texte(requete, r['gabarit'])
        ids, mat, dates = self._vecteurs_dates()
        if mat is None or len(ids) == 0 or mat.shape[1] != q.shape[0]:
            scores = np.empty(0, np.float32)
        else:
            scores = mat @ q
        if filtre is not None:
            # Le produit porte sur toute la matrice (la copier coûterait plus) ; on ne garde que les photos filtrées.
            garde_f = np.flatnonzero(np.asarray(filtre.masque(ids, dates), dtype=bool)) if len(scores) else []
            ids, scores = ids[garde_f], scores[garde_f]
        proba = modele.probabilite(scores, self.cle_modele) if len(scores) else np.empty(0)
        garde = np.flatnonzero(proba >= r['seuil_peut_etre'])
        minimum = int(r.get('minimum') or 0)
        if len(garde) < minimum:
            garde = np.argsort(-scores, kind='stable')[:minimum]
            if r.get('ecart_minimum') is not None and len(garde):
                garde = garde[(proba[garde] >= r['seuil_peut_etre']) | (ecarts(scores)[garde] >= r['ecart_minimum'])]
        else:
            garde = garde[np.argsort(-scores[garde], kind='stable')]
        nb_sures = int(np.count_nonzero(proba[garde] >= r['seuil_sur']))
        res = {'requete': requete, 'total_sures': nb_sures, 'total_peut_etre': int(len(garde)) - nb_sures,
               'total_proches': int(len(scores) - len(garde)), 'debut': debut, 'analysees': int(len(ids))}

        def fiches(indices):
            return [{'id': int(ids[i]), 'score': round(float(scores[i]), 4), 'proba': float(f'{proba[i]:.4g}')}
                    for i in indices]
        if proches:
            reste = np.ones(len(scores), dtype=bool)
            reste[garde] = False
            reste = np.flatnonzero(reste)
            reste = reste[np.argsort(-scores[reste], kind='stable')]   # ordre stable : pages sans doublon
            res['proches'] = fiches(reste[debut:] if n is None else reste[debut:debut + n])
        else:
            items = fiches(garde[debut:] if n is None else garde[debut:debut + n])
            k = max(0, min(len(items), nb_sures - debut))
            res['sures'], res['peut_etre'] = items[:k], items[k:]
        res['duree_ms'] = round((time.perf_counter() - t0) * 1000, 1)
        return res

    # ------------------------------------------------ photos qui se ressemblent

    def semblables(self, photo_id, debut=0, n=200):
        """« Plus comme celle-ci » : les photos qui ressemblent le plus à la photo photo_id, de la plus proche à la
        moins proche (cosinus entre son vecteur d'image et celui de chaque autre photo analysée par le même moteur).

        Renvoie None si la photo n'a pas de vecteur de ce moteur (pas encore analysée, ou analysée par un autre
        moteur), sinon {total, debut, photos: [{id, score}], analysees, duree_ms}. La photo elle-même est exclue ;
        la page [debut, debut + n) court sur un classement stable (pages sans doublon). Aucun moteur n'est chargé :
        seuls les vecteurs déjà en mémoire servent. Le dernier classement est gardé pour les pages suivantes.
        """
        t0 = time.perf_counter()
        debut = max(0, int(debut))
        n = None if n is None else max(0, int(n))
        photo_id = int(photo_id)
        ids, mat, numero = self._vecteurs_numerotes()
        with self._verrou_classement:
            garde = self._dernier_classement
            if garde is not None and garde[0] == photo_id and garde[1] == numero:
                classement, scores = garde[2], garde[3]
            else:
                classement = scores = None
                trouve = np.flatnonzero(ids == photo_id) if mat is not None and len(ids) else ()
                if len(trouve):
                    i = int(trouve[0])
                    scores = mat @ mat[i]
                    classement = np.argsort(-scores, kind='stable')
                    classement = classement[classement != i]
                self._dernier_classement = (photo_id, numero, classement, scores)
        if classement is None:
            return None
        page = classement[debut:] if n is None else classement[debut:debut + n]
        return {'total': int(len(classement)), 'debut': debut, 'analysees': int(len(ids)),
                'photos': [{'id': int(ids[i]), 'score': round(float(scores[i]), 4)} for i in page],
                'duree_ms': round((time.perf_counter() - t0) * 1000, 1)}

    # ------------------------------------------------ vecteur de la requête

    def _moteur_texte(self):
        with self._verrou_moteur:
            if self._moteur is None:
                try:
                    m = self.fournisseur_moteur()
                except Exception as e:
                    raise MoteurIndisponible(f"Le moteur de recherche n'a pas pu démarrer ({e}).") from e
                if m is None:
                    raise MoteurIndisponible("Le moteur de recherche n'est pas encore prêt.")
                self._moteur = m
            return self._moteur

    def _vecteur_texte(self, requete, gabarit=None):
        texte = appliquer_gabarit(reglages(self.cle_modele)['gabarit'] if gabarit is None else gabarit, requete)
        cle = texte.lower()                      # le modèle met tout en minuscules
        with self._verrou_cache:
            v = self._cache.get(cle)
            if v is not None:
                self._cache.move_to_end(cle)
                return v
        v = np.asarray(self._moteur_texte().vecteurs_textes([texte])[0], dtype=np.float32)
        with self._verrou_cache:
            self._cache[cle] = v
            self._cache.move_to_end(cle)
            while len(self._cache) > TAILLE_CACHE:
                self._cache.popitem(last=False)
        return v

    # ------------------------------------------------ vecteurs des photos

    def _vecteurs(self):
        """(ids, matrice) à jour : rechargés quand base.generation() change, au plus une fois par seconde
        (plus rarement si le chargement est long, pour ne pas freiner l'indexation)."""
        return self._vecteurs_numerotes()[:2]

    def _vecteurs_numerotes(self):
        """(ids, matrice, numéro du chargement), lus ensemble : le numéro change à chaque rechargement."""
        return self._charger()[:3]

    def _vecteurs_dates(self):
        """(ids, matrice, dates datetime64 de prise de vue, sinon du fichier), lus ensemble (0.5.0)."""
        ids, mat, _numero, dates = self._charger()
        return ids, mat, dates

    def _charger(self):
        with self._verrou_matrice:
            t = time.monotonic()
            if self._matrice is not None and t - self._controle < self._intervalle:
                return self._ids, self._matrice, self._chargement, self._dates
            self._controle = t
            g = self.base.generation()
            if self._matrice is None or g != self._generation:
                debut = time.perf_counter()
                ids, mat, dates = self.base.vecteurs(self.cle_modele)
                self._ids, self._matrice, self._dates, self._generation = ids, mat, dates, g
                self._chargement += 1
                self._intervalle = max(RECHARGEMENT_MIN_S, 3 * (time.perf_counter() - debut))
            return self._ids, self._matrice, self._chargement, self._dates

    # ------------------------------------------------ entretien

    def rafraichir(self):
        """La prochaine recherche vérifiera tout de suite si les vecteurs ont changé."""
        with self._verrou_matrice:
            self._controle = 0.0

    def vider_cache(self):
        with self._verrou_cache:
            self._cache.clear()

    def oublier_moteur(self):
        """Le prochain besoin redemandera le moteur à fournisseur_moteur (et videra le cache des requêtes)."""
        with self._verrou_moteur:
            self._moteur = None
        self.vider_cache()

    def prechauffer(self, attendre=False):
        """Charge le moteur de texte et les vecteurs en arrière-plan, pour que la 1re recherche soit rapide.
        Renvoie le fil lancé (ou None si attendre=True). Les erreurs sont ignorées ici : elles
        réapparaîtront à la première vraie recherche."""
        def travail():
            try:
                self._vecteurs()
                self._vecteur_texte('photo')
            except Exception:
                pass
        if attendre:
            travail()
            return None
        fil = threading.Thread(target=travail, name='myphotos-prechauffage', daemon=True)
        fil.start()
        return fil


# ---------------------------------------------------------------- dates et lieux d'une recherche (0.5.0)

class Filtre:
    """Photos auxquelles une recherche par mots est limitée : dates (requete.FiltreDates, ou None) et identifiants des
    photos des lieux (n'importe quel itérable, ou None : pas de filtre de lieu). masque(ids, dates) -> booléens, pour
    les vecteurs en mémoire (dates datetime64 de base.vecteurs : prise de vue, sinon date du fichier, en heure locale)."""

    def __init__(self, dates=None, ids=None):
        self.dates = dates if dates else None
        self.ids = None if ids is None else np.fromiter((int(i) for i in ids), np.int64)

    def masque(self, ids, dates):
        m = np.ones(len(ids), dtype=bool)
        if self.dates is not None:
            m &= self.dates.masque(dates)
        if self.ids is not None:
            m &= np.isin(ids, self.ids)
        return m


# Noms de lieux qui sont aussi des mots de tous les jours (formes de lieux.normaliser) : un lieu seulement avec une
# majuscule (« Orange », « photos de Nice ») ou une préposition sans article (« à orange », « en vienne ») ; « à la
# plage », « au port », « à l'aube », « un lot de photos », « avion » restent des mots. Les autres noms de lieux sont
# reconnus tels quels, mais seulement là où il y a des photos : la liste ne garde que les collisions probables (mots
# cherchés dans des photos, noms de communes, de départements ou de villes connues). Une erreur reste possible
# (« Nancy », « Florence » sont aussi des prénoms) : le × de la puce la défait.
MOTS_COURANTS = frozenset("""
    orange mer nice tours pau vienne sens bar port ile plage
    reunion montagne campagne bourg mont bois foret jardin riviere fontaine falaise rocher plaine lac plan rue cours pont
    chateau ecole college ville vignes champs marche cascade volcan bains moulins salon piano avion train velo cheval ski
    nord sud est ouest centre cher lot somme aube landes allier creuse manche
    champagne bordeaux cognac camembert brie roquefort munster chablis chantilly vichy perrier
    grand bonne belle eu ri vitre vire cannes rennes lourdes corse lys rose condom tulle ham gap
    mobile alliance normal bath reading spa deal march street best essen chili
    """.split()) | {'le port', 'la plage', 'la mer', 'la montagne', 'la cote', 'la tour', 'la reunion'}


def _parents(e):
    """Idents de ce qui contient l'endroit e (département, région, pays)."""
    return {x for x in (e.departement, e.region, None if e.type == 'pays' else 'p:' + e.pays) if x}


def chercheur_lieux(presents, source=None):
    """chercher_lieu pour requete.analyser : comment MyPhotos reconnaît un lieu dans une recherche.

    presents : {ident: nombre de photos} (base.lieux_presents) ; source : lieux.Lieux (défaut : l'instance partagée).
    Un nom n'est un lieu que s'il y a des photos (une recherche ne se vide jamais à cause d'un lieu sans photo), et
    seulement avec une majuscule ou une préposition sans article si c'est aussi un mot courant (MOTS_COURANTS).
    Entre homonymes (Paris en France et au Texas, Brest en Bretagne et en Biélorussie), celui qui a le plus de photos ;
    à égalité, le plus grand (pays, région, département, puis ville la plus peuplée, ordre de lieux.chercher). Un nom
    qui en désigne plusieurs à la fois les réunit : une commune nouvelle et ses anciennes communes (« Annecy », avec
    Seynod et Annecy-le-Vieux ; « Les Sables-d'Olonne »), un nom de région d'autrefois (« Alsace » : Bas-Rhin et
    Haut-Rhin ; « Provence »). Renvoie None, ou {'libelle', 'idents', 'candidats', 'nombres', 'photos'} : libelle pour
    la puce, idents à réunir, candidats (Endroit de lieux.py qui ont des photos) et leurs nombres de photos (pour
    idents_des_lieux), photos des lieux réunis."""
    if source is None:
        source = _lieux.lieux()

    def chercher(nom, indices):
        if not presents or not nom:
            return None
        if nom in MOTS_COURANTS and not (indices.get('majuscule')
                                         or (indices.get('preposition') and not indices.get('article'))):
            return None
        candidats = [e for e in source.chercher(nom) if presents.get(e.ident)]
        if not candidats:
            return None
        exacts = [e for e in candidats if _lieux.normaliser(e.nom) == nom]
        meilleur = max(exacts or candidats, key=lambda e: presents[e.ident])     # max : le premier à égalité
        # Même nom, même type, même département (ou région) : une commune nouvelle et ses anciennes communes, ou les
        # départements d'une région d'autrefois. Jamais un homonyme exact (deux communes homonymes ne sont jamais dans
        # le même département).
        zone = meilleur.departement or meilleur.region if meilleur.type == 'ville' else meilleur.region
        reunis = [meilleur] + [e for e in candidats if e is not meilleur and e not in exacts and e.type == meilleur.type
                               and zone and (e.departement or e.region if e.type == 'ville' else e.region) == zone]
        if len(reunis) > 1 and meilleur not in exacts:
            texte = (indices.get('texte') or nom).strip()
            libelle = texte[:1].upper() + texte[1:]
        else:
            libelle = meilleur.nom
        return {'libelle': libelle, 'idents': [e.ident for e in reunis], 'candidats': candidats,
                'nombres': {e.ident: presents[e.ident] for e in candidats},
                'photos': sum(presents[e.ident] for e in reunis)}
    return chercher


def idents_des_lieux(trouves):
    """Idents à réunir pour filtrer les photos par les lieux d'une recherche (objets de chercheur_lieux, dans l'ordre).

    Plusieurs lieux : l'un OU l'autre (« Bretagne Normandie »). Mais un lieu qui en contient un autre de la même
    recherche le précise : « Brest Finistère » = Brest, « Paris Texas » = le Paris du Texas, « Saint-Denis La Réunion »
    = celle de La Réunion (le lien d'un lieu, dans la visionneuse, s'écrit ainsi quand son nom a un homonyme)."""
    trouves = [t for t in trouves if isinstance(t, dict) and t.get('idents')]
    res = []
    for k, lieu in enumerate(trouves):
        autres = [set(t['idents']) for j, t in enumerate(trouves) if j != k]
        mes_idents = set(lieu['idents'])
        if any(_parents(e) & mes_idents for j, t in enumerate(trouves) if j != k for e in t.get('candidats', ())):
            continue                                 # il contient un autre lieu de la recherche : l'autre dit où
        candidats = lieu.get('candidats', ())

        def dedans(e):
            return any(_parents(e) & a for a in autres)
        if not autres or any(dedans(e) for e in candidats if e.ident in mes_idents):
            res += lieu['idents']
            continue
        nombres = lieu.get('nombres', {})
        autres_choix = [e for e in candidats if dedans(e)]
        if autres_choix:                             # un homonyme dans l'autre lieu : celui-là, le plus photographié
            res.append(max(autres_choix, key=lambda e: nombres.get(e.ident, 0)).ident)
        else:
            res += lieu['idents']
    return list(dict.fromkeys(res))
