"""Lieux des photos (0.5.0) : la ville, le département, la région et le pays d'une position GPS, sans Internet.

Les données viennent de GeoNames (https://www.geonames.org, licence CC BY 4.0), préparées une fois pour toutes par
outils/preparer_lieux.py dans myphotos/lieux.bin (moins de 2 Mo, embarqué dans MyPhotos.exe) : toutes les communes de
France, métropole et outre-mer, les villes des pays voisins et des lieux de vacances habituels, et les villes d'une
certaine taille du reste du monde (seuils et mesures : voir l'en-tête du fichier, champ « couverture », et
docs/ARCHITECTURE.md). Les noms sont en français d'abord (Londres, Espagne, Bretagne, Séville), sinon le nom local ; la
recherche connaît aussi les autres écritures (nom local, sans accents, « St » pour « Saint », tirets).

Hiérarchie : ville -> département (en France seulement) -> région (en France, les régions actuelles ; ailleurs, le
premier niveau administratif : Andalousie, Angleterre, Bavière…) -> pays. L'outre-mer français est rattaché à la
France : chaque territoire y est une région (La Réunion, Guadeloupe, Nouvelle-Calédonie…).

Interface (utilisée par base.py, indexation.py et la recherche) :
- normaliser(texte) -> str ;
- Endroit (type 'ville' | 'departement' | 'region' | 'pays', ident stable, nom français, pays ISO 2, region et
  departement : idents de ceux qui le contiennent, ou None ; population, lat, lon) ;
- Lieux(chemin=None) : version, proche(lat, lon), chercher(nom_normalise), parents(endroit), libelle(endroit),
  et en plus endroit(ident) et localiser(lat, lon) ;
- lieux() : l'instance partagée, chargée une seule fois, à la première demande.

Chargement paresseux et sûr entre fils (un verrou, puis des données en lecture seule). Un fichier absent ou abîmé ne
fait jamais d'erreur : proche() répond (None, None), chercher() une liste vide, et `disponible` est faux.
"""
import hashlib
import json
import logging
import lzma
import math
import re
import threading
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config

journal = logging.getLogger(__name__)

FICHIER = Path(config.CODE) / 'myphotos' / 'lieux.bin'
MAGIQUE = b'MyPhotos-lieux\x00'          # 15 octets, puis le numéro du format sur 1 octet (le zéro : fichier binaire)
FORMAT = 1

# La ville retenue est à 30 km au plus. Écart mesuré entre une ville et sa plus proche voisine : en France
# métropolitaine, 2,2 km en médiane et 5,5 km pour 99 % des communes ; dans les pays proches, 3,3 et 19,7 km ; ailleurs
# (villes de 5 000 habitants), 10 km en médiane et 35 km pour 90 % d'entre elles. Une photo prise entre deux villes
# voisines est donc presque toujours à moins de 30 km de l'une d'elles ; au-delà, le nom d'une ville ne dit plus où la
# photo a été prise (en mer, en pleine montagne, dans le désert).
RAYON_KM = 30.0
# Sans ville à moins de 30 km, la région et le pays de la ville la plus proche sont retenus jusqu'à 100 km (au large des
# côtes, safari, désert, parc national), sans ville : assez près pour que ce soit presque toujours la bonne région.
RAYON_PAYS_KM = 100.0
# Une grande ville l'emporte sur un village voisin plus proche de la photo, comme sur une carte : le choix se fait sur
# distance / poids, avec poids = 1 + POIDS_POPULATION × racine(population / 1000) (Paris : 5,7 ; Brest : 2,2 ; un
# village de 800 habitants : 1,1). Une photo prise dans le 15e arrondissement est à Paris, pas à Issy-les-Moulineaux,
# dont la mairie est pourtant plus proche.
POIDS_POPULATION = 0.1
# Mais jamais une ville d'un autre pays quand la ville la plus proche est nettement plus près (relecture de la 0.5.0 :
# les lacs de Plitvice, en Croatie, donnaient Bihać, en Bosnie, à 23 km, plutôt que Korenica, à 17 km) : si la ville
# choisie est d'un autre pays que la plus proche et plus de ECART_PAYS fois plus loin, la meilleure de ce pays-là.
ECART_PAYS = 1.25
NULL_ISLAND = 0.01                       # (0, 0) à moins de 0,01° : appareil sans vraie position
CELLULE = 0.25                           # grille de recherche : cases de 0,25° de côté
_COLONNES = int(round(360 / CELLULE))
_KM_PAR_DEGRE = 111.195                  # sur un grand cercle (rayon moyen de la Terre : 6 371 km)
_RAYON_TERRE = 6371.0088

TYPES = ('pays', 'region', 'departement', 'ville')
_PREFIXES = {'pays': 'p:', 'region': 'r:', 'departement': 'd:', 'ville': 'v:'}


# ---------------------------------------------------------------- noms

# Lettres que la décomposition Unicode ne sépare pas de leur accent ou de leur ligature.
_LETTRES = str.maketrans({'œ': 'oe', 'æ': 'ae', 'ø': 'o', 'ł': 'l', 'đ': 'd', 'ð': 'd', 'þ': 'th', 'ı': 'i',
                          'ħ': 'h', 'ŧ': 't', 'ŀ': 'l', 'ĸ': 'k'})
_COMBINANTS = re.compile('[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]+')
# Tout ce qui n'est ni lettre ni chiffre sépare deux mots : apostrophes (' ’ ʼ ʻ), tirets, points, virgules, espaces…
_SEPARATEURS = re.compile('[\\W_\u02b0-\u02ff]+')
_ABREGES = {'st': 'saint', 'ste': 'sainte'}


def normaliser(texte):
    """Forme de comparaison d'un nom : minuscules, sans accents, apostrophes, tirets et autres signes changés en
    espaces, espaces simples, « st » et « ste » écrits « saint » et « sainte ». « Saint-Malo », « St Malo » et
    « saint malo » donnent tous « saint malo » ; « L'Haÿ-les-Roses » donne « l hay les roses ». None donne ''."""
    if texte is None:
        return ''
    t = unicodedata.normalize('NFKD', str(texte).casefold())
    t = _COMBINANTS.sub('', t).translate(_LETTRES)
    return ' '.join(_ABREGES.get(m, m) for m in _SEPARATEURS.sub(' ', t).split())


@dataclass(frozen=True)
class Endroit:
    """Un lieu connu. ident est stable d'une version des données à l'autre : 'v:<numéro GeoNames>' pour une ville,
    'd:FR.29' (département), 'r:FR.53' ou 'r:ES.51' (région), 'p:FR' (pays). region et departement désignent ceux qui
    le contiennent (None pour lui-même et au-dessus : une région n'a ni région ni département). lat et lon : position
    de la ville, None pour les autres types. population : celle de la ville, ou des villes connues du territoire
    (celle du pays pour un pays)."""
    type: str
    ident: str
    nom: str
    pays: str
    region: str | None
    departement: str | None
    population: int
    lat: float | None
    lon: float | None


def coordonnees_valides(lat, lon):
    """(lat, lon) en flottants si la position est utilisable, sinon None : manquante, pas un nombre, NaN, hors des
    bornes, ou (0, 0) à moins de 0,01° près (« Null Island » : appareil sans vraie position GPS)."""
    try:
        if isinstance(lat, bool) or isinstance(lon, bool):
            return None
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return None
    if abs(lat) < NULL_ISLAND and abs(lon) < NULL_ISLAND:
        return None
    return lat, lon


def distance_km(lat1, lon1, lat2, lon2):
    """Distance sur la Terre (formule de haversine), en kilomètres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * _RAYON_TERRE * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------- fichier de données

class DonneesInvalides(ValueError):
    """Le fichier de données n'est pas un fichier de lieux de MyPhotos lisible."""


def lire_fichier(chemin):
    """(en-tête, sections) d'un fichier de lieux : en-tête JSON (version, sources, couverture, comptes) et
    {nom: octets} des sections, décompressées et vérifiées (empreinte SHA-256). DonneesInvalides sinon."""
    with open(chemin, 'rb') as f:
        brut = f.read()
    if len(brut) < len(MAGIQUE) + 5 or not brut.startswith(MAGIQUE):
        raise DonneesInvalides('pas un fichier de lieux de MyPhotos')
    if brut[len(MAGIQUE)] != FORMAT:
        raise DonneesInvalides(f'format {brut[len(MAGIQUE)]} inconnu')
    debut = len(MAGIQUE) + 1
    n = int.from_bytes(brut[debut:debut + 4], 'little')
    try:
        entete = json.loads(brut[debut + 4:debut + 4 + n].decode('utf-8'))
        charge = lzma.decompress(brut[debut + 4 + n:])
    except (ValueError, lzma.LZMAError) as e:
        raise DonneesInvalides(f'fichier illisible ({e})') from e
    if not isinstance(entete, dict) or hashlib.sha256(charge).hexdigest() != entete.get('empreinte'):
        raise DonneesInvalides('contenu abîmé (empreinte)')
    sections = {}
    for nom, (position, longueur) in entete.get('sections', {}).items():
        if position < 0 or longueur < 0 or position + longueur > len(charge):
            raise DonneesInvalides(f'section {nom} hors du fichier')
        sections[nom] = charge[position:position + longueur]
    return entete, sections


def _decouper(blob):
    """Débuts et fins des textes d'un bloc « texte\\ntexte\\n… » (sans le dernier saut de ligne)."""
    fins = np.flatnonzero(np.frombuffer(blob, np.uint8) == 10)
    debuts = np.empty(len(fins), np.int64)
    if len(fins):
        debuts[0] = 0
        debuts[1:] = fins[:-1] + 1
    return debuts, fins.astype(np.int64)


class _Donnees:
    """Le contenu du fichier, en lecture seule une fois construit (voir outils/preparer_lieux.py pour le format)."""

    def __init__(self, entete, s):
        self.version = str(entete['version'])
        self.entete = entete
        c = entete['comptes']
        self.n_pays, self.n_regions, self.n_departements, self.n_villes = (
            int(c['pays']), int(c['regions']), int(c['departements']), int(c['villes']))
        self.n_admin = self.n_pays + self.n_regions + self.n_departements
        self.n = self.n_admin + self.n_villes
        self.noms = s['noms']
        self._noms_debuts, self._noms_fins = _decouper(self.noms)
        codes = s['codes'].decode('utf-8').split('\n')[:-1]
        if len(self._noms_debuts) != self.n or len(codes) != self.n_admin:
            raise DonneesInvalides('comptes incohérents')
        self.codes = codes
        self.pop = np.frombuffer(s['population'], '<u4')
        self.region_pays = np.frombuffer(s['region_pays'], '<i2').astype(np.int64)
        self.departement_region = np.frombuffer(s['departement_region'], '<i2').astype(np.int64)
        self.v_pays = np.frombuffer(s['ville_pays'], '<i2').astype(np.int64)
        self.v_region = np.frombuffer(s['ville_region'], '<i2').astype(np.int64)
        self.v_departement = np.frombuffer(s['ville_departement'], '<i2').astype(np.int64)
        # Numéros GeoNames et positions, écrits en différences successives (plus compressibles).
        echelle = float(entete['echelle'])
        self.v_gid = np.cumsum(np.frombuffer(s['ville_geonames'], '<i4').astype(np.int64))
        self.v_lat = np.cumsum(np.frombuffer(s['ville_lat'], '<i4').astype(np.int64)) / echelle
        self.v_lon = np.cumsum(np.frombuffer(s['ville_lon'], '<i4').astype(np.int64)) / echelle
        self._variantes_endroits = np.cumsum(np.frombuffer(s['variantes_endroits'], '<i4').astype(np.int64))
        # Points en plus d'une ville (facultatifs) : quartiers de Paris, Lyon, Marseille, de Monaco… ; une photo prise
        # près de l'un d'eux est dans sa ville (et non dans la commune voisine, dont la mairie est plus proche).
        if 'points_ville' in s:
            self.p_ville = np.cumsum(np.frombuffer(s['points_ville'], '<i4').astype(np.int64))
            self.p_lat = np.cumsum(np.frombuffer(s['points_lat'], '<i4').astype(np.int64)) / echelle
            self.p_lon = np.cumsum(np.frombuffer(s['points_lon'], '<i4').astype(np.int64)) / echelle
            self.p_poids = np.frombuffer(s['points_poids'], '<u2').astype(np.float64) / 1000.0
        else:
            self.p_ville = np.empty(0, np.int64)
            self.p_lat = self.p_lon = self.p_poids = np.empty(0, np.float64)
        if not (len(self.p_ville) == len(self.p_lat) == len(self.p_lon) == len(self.p_poids)) or (
                len(self.p_ville) and not (0 <= self.p_ville.min() and self.p_ville.max() < self.n_villes)):
            raise DonneesInvalides('points incohérents')
        for tableau, attendu in ((self.pop, self.n), (self.region_pays, self.n_regions),
                                 (self.departement_region, self.n_departements), (self.v_pays, self.n_villes),
                                 (self.v_region, self.n_villes), (self.v_departement, self.n_villes),
                                 (self.v_gid, self.n_villes), (self.v_lat, self.n_villes),
                                 (self.v_lon, self.n_villes),
                                 (self._variantes_endroits, int(c['variantes']))):
            if len(tableau) != attendu:
                raise DonneesInvalides('tableaux incohérents')
        if len(self._variantes_endroits) and not (0 <= self._variantes_endroits.min() and
                                                  self._variantes_endroits.max() < self.n):
            raise DonneesInvalides('variantes incohérentes')
        # Idents des pays, régions et départements ('r:FR.53' : la Bretagne ; 'd:FR.53' : la Mayenne).
        self.idents = {('p:' if i < self.n_pays else 'r:' if i < self.n_pays + self.n_regions else 'd:') + code: i
                       for i, code in enumerate(codes)}
        self._par_gid = np.argsort(self.v_gid, kind='stable')
        self._gid_tries = self.v_gid[self._par_gid]
        # Recherche par nom : la clé (nom normalisé) de chaque endroit, dans l'ordre des endroits, puis les autres
        # clés (variantes) et leur endroit. L'index {clé: endroits} n'est construit qu'à la première recherche.
        self._cles = s['cles']
        self._variantes = s['variantes']
        if self._cles.count(b'\n') != self.n or self._variantes.count(b'\n') != len(self._variantes_endroits):
            raise DonneesInvalides('noms de recherche incohérents')
        self._index = None
        self._verrou_index = threading.Lock()
        self._preparer_grille()

    # ------------------------------------------------ grille de proximité

    def _preparer_grille(self):
        """Villes et points en plus d'une ville, rangés par case : _g_ordre donne la ville de chacun."""
        lat = np.concatenate([self.v_lat, self.p_lat])
        lon = np.concatenate([self.v_lon, self.p_lon])
        ville = np.concatenate([np.arange(len(self.v_lat), dtype=np.int64), self.p_ville])
        poids = np.concatenate([1.0 + POIDS_POPULATION * np.sqrt(self.pop[self.n_admin:] / 1000.0), self.p_poids])
        lignes = np.clip(np.floor((lat + 90) / CELLULE), 0, _COLONNES // 2 - 1).astype(np.int64)
        colonnes = np.floor((lon + 180) / CELLULE).astype(np.int64) % _COLONNES
        cles = lignes * _COLONNES + colonnes
        ordre = np.argsort(cles, kind='stable')
        self._g_ordre = ville[ordre]
        self._g_cles = cles[ordre].tolist()          # bisect, plus rapide qu'un searchsorted pour une seule valeur
        self._g_lat = lat[ordre]
        self._g_lon = lon[ordre]
        self._g_poids = poids[ordre]
        self._g_pays = self.v_pays[self._g_ordre]

    def candidats(self, lat, lon, rayon):
        """Indices (dans l'ordre de la grille) des villes des cases à moins de `rayon` km de (lat, lon)."""
        ligne = min(max(int(math.floor((lat + 90) / CELLULE)), 0), _COLONNES // 2 - 1)
        colonne = int(math.floor((lon + 180) / CELLULE)) % _COLONNES
        dl = int(math.ceil(rayon / _KM_PAR_DEGRE / CELLULE))
        cos_min = min(math.cos(math.radians(min(90.0, abs(lat) + dl * CELLULE))), 1.0)
        dc = _COLONNES // 2 if cos_min <= 1e-3 else int(math.ceil(rayon / (_KM_PAR_DEGRE * cos_min) / CELLULE))
        morceaux = []
        for li in range(max(0, ligne - dl), min(_COLONNES // 2 - 1, ligne + dl) + 1):
            base = li * _COLONNES
            if 2 * dc + 1 >= _COLONNES:
                plages = [(0, _COLONNES - 1)]
            else:
                a, b = colonne - dc, colonne + dc
                plages = [(max(a, 0), min(b, _COLONNES - 1))]
                if a < 0:
                    plages.append((a + _COLONNES, _COLONNES - 1))
                if b >= _COLONNES:
                    plages.append((0, b - _COLONNES))
            for a, b in plages:
                i0 = bisect_left(self._g_cles, base + a)
                i1 = bisect_right(self._g_cles, base + b, i0)
                if i1 > i0:
                    morceaux.append(np.arange(i0, i1))
        if not morceaux:
            return np.empty(0, np.int64)
        return morceaux[0] if len(morceaux) == 1 else np.concatenate(morceaux)

    def plus_proche(self, lat, lon, rayon, pondere=True):
        """(indice de ville, distance en km) de la meilleure ville à `rayon` km au plus, ou (None, None)."""
        idx = self.candidats(lat, lon, rayon)
        if not len(idx):
            return None, None
        glat, glon = self._g_lat[idx], self._g_lon[idx]
        dlon = np.abs(glon - lon)
        dlon = np.minimum(dlon, 360.0 - dlon)
        # Approximation plane locale (écart < 0,1 % à 100 km), puis distance exacte pour la ville retenue.
        x = dlon * math.cos(math.radians(lat))
        d = np.hypot(glat - lat, x) * _KM_PAR_DEGRE
        dedans = d <= rayon
        if not dedans.any():
            return None, None
        score = np.where(dedans, d / self._g_poids[idx] if pondere else d, np.inf)
        k = int(np.argmin(score))
        if pondere:
            pays = self._g_pays[idx]
            proche = int(np.argmin(np.where(dedans, d, np.inf)))
            if pays[k] != pays[proche] and d[k] > ECART_PAYS * d[proche]:
                k = int(np.argmin(np.where(pays == pays[proche], score, np.inf)))
        i = int(self._g_ordre[idx[k]])
        return i, distance_km(lat, lon, float(self.v_lat[i]), float(self.v_lon[i]))

    # ------------------------------------------------ noms

    def nom(self, e):
        return self.noms[self._noms_debuts[e]:self._noms_fins[e]].decode('utf-8')

    def _construire_index(self):
        """{clé: endroit, ou tuple d'endroits} : plus de cent mille clés, en quelques dizaines de ms (le gros du travail
        est fait par dict, zip et Counter, en C)."""
        cles = self._cles.decode('utf-8').split('\n')
        cles.pop()
        index = dict(zip(cles, range(self.n)))
        if len(index) < len(cles):              # noms partagés (homonymes) : chacun reçoit tous ses endroits
            doublons = {k for k, c in Counter(cles).items() if c > 1}
            groupes = defaultdict(list)
            for e, k in enumerate(cles):
                if k in doublons:
                    groupes[k].append(e)
            index.update((k, tuple(g)) for k, g in groupes.items())
        variantes = self._variantes.decode('utf-8').split('\n')
        variantes.pop()
        for k, e in zip(variantes, self._variantes_endroits.tolist()):
            deja = index.get(k)
            if deja is None:
                index[k] = e
            elif isinstance(deja, int):
                if deja != e:
                    index[k] = (deja, e)
            elif e not in deja:
                index[k] = deja + (e,)
        return index

    def trouver(self, cle):
        """Endroits (indices globaux) dont un nom normalisé vaut exactement `cle`."""
        index = self._index
        if index is None:
            with self._verrou_index:
                if self._index is None:
                    self._index = self._construire_index()
                    self._cles = self._variantes = None       # l'index garde les clés : 2,7 Mo de moins
                index = self._index
        x = index.get(cle)
        if x is None:
            return []
        return [x] if isinstance(x, int) else list(x)

    def ville_par_gid(self, gid):
        k = int(np.searchsorted(self._gid_tries, gid))
        if k < len(self._gid_tries) and int(self._gid_tries[k]) == gid:
            return self.n_admin + int(self._par_gid[k])
        return None


class Lieux:
    """Les lieux connus (voir l'en-tête du module). chemin : fichier de données (défaut : myphotos/lieux.bin, à côté du
    code, aussi dans MyPhotos.exe). Rien n'est lu avant la première question ; ensuite, tout est en mémoire (une
    vingtaine de Mo) et en lecture seule : plusieurs fils peuvent s'en servir à la fois."""

    def __init__(self, chemin=None):
        self.chemin = Path(chemin) if chemin is not None else FICHIER
        self._verrou = threading.Lock()
        self._d = None
        self._essaye = False
        self._cache = {}

    # ------------------------------------------------ chargement

    def _donnees(self):
        d = self._d
        if d is not None or self._essaye:
            return d
        with self._verrou:
            if self._d is None and not self._essaye:
                try:
                    entete, sections = lire_fichier(self.chemin)
                    self._d = _Donnees(entete, sections)
                except Exception as e:                # fichier absent, abîmé, d'un autre format : jamais d'erreur
                    journal.warning('lieux : données inutilisables (%r), les photos ne seront pas localisées', e)
                finally:
                    self._essaye = True
            return self._d

    @property
    def disponible(self):
        """Vrai si les données sont chargées (ou chargeables)."""
        return self._donnees() is not None

    @property
    def version(self):
        """Version des données ('' si elles sont absentes) : elle change avec leur contenu. Les lieux des photos
        calculés avec une autre version sont refaits."""
        d = self._donnees()
        return d.version if d is not None else ''

    @property
    def entete(self):
        """En-tête du fichier (sources et leurs dates, couverture, comptes), ou {}."""
        d = self._donnees()
        return dict(d.entete) if d is not None else {}

    # ------------------------------------------------ endroits

    def _endroit(self, d, e):
        x = self._cache.get(e)
        if x is not None:
            return x
        if e < d.n_pays:
            x = Endroit('pays', 'p:' + d.codes[e], d.nom(e), d.codes[e], None, None, int(d.pop[e]), None, None)
        elif e < d.n_pays + d.n_regions:
            p = int(d.region_pays[e - d.n_pays])
            x = Endroit('region', 'r:' + d.codes[e], d.nom(e), d.codes[p], None, None, int(d.pop[e]), None, None)
        elif e < d.n_admin:
            r = d.n_pays + int(d.departement_region[e - d.n_pays - d.n_regions])
            p = int(d.region_pays[r - d.n_pays])
            x = Endroit('departement', 'd:' + d.codes[e], d.nom(e), d.codes[p], 'r:' + d.codes[r], None,
                        int(d.pop[e]), None, None)
        else:
            v = e - d.n_admin
            p, r, dep = int(d.v_pays[v]), int(d.v_region[v]), int(d.v_departement[v])
            x = Endroit('ville', f'v:{int(d.v_gid[v])}', d.nom(e), d.codes[p],
                        'r:' + d.codes[d.n_pays + r] if r >= 0 else None,
                        'd:' + d.codes[d.n_pays + d.n_regions + dep] if dep >= 0 else None,
                        int(d.pop[e]), round(float(d.v_lat[v]), 5), round(float(d.v_lon[v]), 5))
        if len(self._cache) > 4096:                  # quelques centaines de Ko au plus
            self._cache.clear()
        self._cache[e] = x
        return x

    @staticmethod
    def _indice(d, ident):
        if not isinstance(ident, str) or len(ident) < 3 or ident[1] != ':':
            return None
        if ident[0] == 'v':
            code = ident[2:]
            return d.ville_par_gid(int(code)) if code.isdigit() and len(code) < 12 else None
        return d.idents.get(ident)

    def endroit(self, ident):
        """L'Endroit de cet ident ('v:2988507', 'd:FR.29', 'r:FR.53', 'p:FR'), ou None s'il est inconnu."""
        d = self._donnees()
        if d is None:
            return None
        e = self._indice(d, ident)
        return self._endroit(d, e) if e is not None else None

    def proche(self, lat, lon):
        """(ville la plus proche, distance en km) : la ville qui l'emporte à 30 km au plus (une grande ville sur un
        village un peu plus proche, voir POIDS_POPULATION) ; sinon (None, distance de la ville la plus proche jusqu'à
        100 km) ; (None, None) pour une position invalide, ou sans aucune ville connue à moins de 100 km."""
        d = self._donnees()
        pos = coordonnees_valides(lat, lon)
        if d is None or pos is None:
            return None, None
        i, dist = d.plus_proche(pos[0], pos[1], RAYON_KM)
        if i is not None:
            return self._endroit(d, d.n_admin + i), round(dist, 3)
        _, dist = d.plus_proche(pos[0], pos[1], RAYON_PAYS_KM, pondere=False)
        return None, (round(dist, 3) if dist is not None else None)

    def localiser(self, lat, lon):
        """Ce qu'il faut retenir d'une photo prise en (lat, lon) : {ville, departement, region, pays (idents ou None),
        distance (km ou None)}, ou None pour une position invalide. Sans ville à moins de 30 km, la région et le pays
        de la ville la plus proche jusqu'à 100 km (en mer près des côtes, en pleine nature : Yosemite, la Californie) ;
        au-delà, tout est None."""
        d = self._donnees()
        pos = coordonnees_valides(lat, lon)
        if d is None or pos is None:
            return None
        i, dist = d.plus_proche(pos[0], pos[1], RAYON_KM)
        if i is not None:
            v = self._endroit(d, d.n_admin + i)
            return {'ville': v.ident, 'departement': v.departement, 'region': v.region, 'pays': 'p:' + v.pays,
                    'distance': round(dist, 3)}
        j, dist = d.plus_proche(pos[0], pos[1], RAYON_PAYS_KM, pondere=False)
        if j is None:
            return {'ville': None, 'departement': None, 'region': None, 'pays': None, 'distance': None}
        v = self._endroit(d, d.n_admin + j)
        return {'ville': None, 'departement': None, 'region': v.region, 'pays': 'p:' + v.pays,
                'distance': round(dist, 3)}

    def chercher(self, nom_normalise):
        """Endroits dont un nom (français, local, autre écriture) normalisé vaut EXACTEMENT nom_normalise (voir
        normaliser) : pays, puis régions, départements, et villes de la plus peuplée à la moins peuplée. Un nom peut en
        désigner plusieurs (homonymes : Paris en France et au Texas ; « Alsace » : ses deux départements)."""
        d = self._donnees()
        if d is None or not isinstance(nom_normalise, str) or not nom_normalise:
            return []
        indices = d.trouver(nom_normalise)
        # Par type (pays, régions, départements, villes), puis population décroissante ; à égalité, l'ordre du fichier.
        return [self._endroit(d, e) for e in sorted(indices, key=lambda e: (_rang(d, e), -int(d.pop[e]), e))]

    def parents(self, endroit):
        """Ce qui contient cet endroit, du plus proche au plus lointain : [département, région, pays] pour une ville
        de France, [région, pays] ailleurs, [] pour un pays."""
        if endroit is None:
            return []
        resultat = []
        for ident in (endroit.departement, endroit.region, 'p:' + endroit.pays if endroit.type != 'pays' else None):
            if ident:
                x = self.endroit(ident)
                if x is not None:
                    resultat.append(x)
        return resultat

    def libelle(self, endroit):
        """« Brest, Finistère, Bretagne, France » ; à l'étranger « Séville, Andalousie, Espagne » ; un nom déjà dit
        n'est pas répété (« Paris, Île-de-France, France »)."""
        if endroit is None:
            return ''
        morceaux, vus = [], set()
        for x in [endroit] + self.parents(endroit):
            cle = normaliser(x.nom)
            if cle not in vus:
                vus.add(cle)
                morceaux.append(x.nom)
        return ', '.join(morceaux)


def _rang(d, e):
    if e < d.n_pays:
        return 0
    if e < d.n_pays + d.n_regions:
        return 1
    return 2 if e < d.n_admin else 3


_instance = None
_verrou_instance = threading.Lock()


def lieux():
    """L'instance partagée de Lieux (données de myphotos/lieux.bin), créée à la première demande ; ses données ne sont
    lues qu'à la première question posée."""
    global _instance
    if _instance is None:
        with _verrou_instance:
            if _instance is None:
                _instance = Lieux()
    return _instance
