"""Recherche par mots : la requête devient un vecteur, comparé à celui de chaque photo analysée.

Le cosinus entre les deux vecteurs donne un score ; modele.probabilite le convertit en probabilité
(0 à 1, calibrée par le modèle) que la photo corresponde. Deux seuils, réglés pour chaque moteur
(REGLAGES), séparent les résultats sûrs des « peut-être ».
"""
import re
import threading
import time
import unicodedata
from collections import OrderedDict

import numpy as np

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

    # ------------------------------------------------ recherche

    def chercher(self, texte, debut=0, n=200):
        """Photos qui correspondent au texte, de la plus probable à la moins probable.

        Renvoie None si le texte est vide (afficher alors les photos par date), sinon
        {requete, total_sures, total_peut_etre, sures: [{id, score, proba}], peut_etre: [...], debut,
        analysees, duree_ms}. La page [debut, debut + n) court sur la liste complète : les sûres d'abord,
        puis les « peut-être ». score = cosinus, proba = probabilité calibrée.
        """
        t0 = time.perf_counter()
        requete = normaliser_requete(texte)
        if not requete:
            return None
        debut = max(0, int(debut))
        n = None if n is None else max(0, int(n))
        r = reglages(self.cle_modele)
        q = self._vecteur_texte(requete, r['gabarit'])
        ids, mat = self._vecteurs()
        if mat is None or len(ids) == 0 or mat.shape[1] != q.shape[0]:
            scores = np.empty(0, np.float32)
        else:
            scores = mat @ q
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
        page = garde[debut:] if n is None else garde[debut:debut + n]
        items = [{'id': int(ids[i]), 'score': round(float(scores[i]), 4), 'proba': float(f'{proba[i]:.4g}')}
                 for i in page]
        k = max(0, min(len(items), nb_sures - debut))
        return {'requete': requete, 'total_sures': nb_sures, 'total_peut_etre': int(len(garde)) - nb_sures,
                'sures': items[:k], 'peut_etre': items[k:], 'debut': debut, 'analysees': int(len(ids)),
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
        with self._verrou_matrice:
            t = time.monotonic()
            if self._matrice is not None and t - self._controle < self._intervalle:
                return self._ids, self._matrice
            self._controle = t
            g = self.base.generation()
            if self._matrice is None or g != self._generation:
                debut = time.perf_counter()
                ids, mat, dates = self.base.vecteurs(self.cle_modele)
                self._ids, self._matrice, self._dates, self._generation = ids, mat, dates, g
                self._intervalle = max(RECHARGEMENT_MIN_S, 3 * (time.perf_counter() - debut))
            return self._ids, self._matrice

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
