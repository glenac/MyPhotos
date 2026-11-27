r"""Prépare myphotos/lieux.bin, les lieux connus de MyPhotos (0.5.0), à partir des données de GeoNames.

    python -I outils\preparer_lieux.py [--cache <dossier>] [--sortie <fichier>] [--actualiser]

Sources (GeoNames, https://www.geonames.org, licence Creative Commons Attribution 4.0) :
https://download.geonames.org/export/dump/ : cities500.zip (villes du monde), FR.zip et les fichiers des territoires
d'outre-mer (GP, MQ, GF, RE, YT, PM, BL, MF, WF, PF, NC, TF : toutes les communes), admin1CodesASCII.txt (régions),
admin2Codes.txt (départements), countryInfo.txt (pays) et alternateNamesV2.zip (noms en français et dans la langue du
pays). Ils sont téléchargés une seule fois (près de 240 Mo, dont 205 pour alternateNamesV2.zip) dans un cache HORS du
dépôt : --cache, sinon %MYPHOTOS_CACHE_CONSTRUCTION%\geonames, sinon %LOCALAPPDATA%\MyPhotos-construction\cache\geonames.
--actualiser les télécharge de nouveau. Le script est relançable : les mêmes sources donnent exactement le même
fichier (même empreinte, même version : la version vient du contenu seul, jamais de la date des fichiers du cache).

Ce qui est gardé (seuils choisis en mesurant la taille du fichier, qui doit rester sous 2 Mo : PROCHES, SEUIL_PROCHES
et SEUIL_MONDE ci-dessous ; mesures dans docs/ARCHITECTURE.md, section « Lieux ») :
- France, métropole et outre-mer : toutes les communes (chef-lieu de chaque commune, et anciennes communes d'une
  commune nouvelle quand GeoNames leur donne une population : Cherbourg et Tourlaville dans Cherbourg-en-Cotentin) ;
- pays voisins et lieux de vacances habituels (PROCHES) : les villes et villages d'au moins SEUIL_PROCHES habitants ;
- reste du monde : les villes d'au moins SEUIL_MONDE habitants, plus les capitales et les chefs-lieux de région ;
- près des frontières : tout lieu habité écarté par les seuils qui, sinon, tomberait dans un autre pays que le sien
  (villages du Luxembourg près de l'Allemagne, de Belgique près de Givet…) ; les quartiers (PPLX) concernés deviennent
  des points en plus de leur ville (Larvotto, Monte-Carlo : Monaco) ;
- points en plus d'une ville : les quartiers de Paris, Lyon et Marseille (sinon Roland-Garros est à
  Boulogne-Billancourt, la Villette à Pantin).

Noms : le français d'abord (noms alternatifs « fr » de GeoNames : Londres, Espagne, Bretagne, Séville, Venise), sinon
le nom de GeoNames ; pour la recherche, aussi le nom de GeoNames, le nom dans la langue du pays (Sevilla, Venezia,
München) et, pour la France, le nom sans son article (« Mont-Saint-Michel », quand il reste au moins deux mots) et le
nom de la commune nouvelle sur ses anciennes communes. Les anciennes régions (Alsace, Auvergne, Lorraine…) sont des
noms de leurs départements.

Format du fichier (lu par myphotos/lieux.py) : MAGIQUE (15 octets), numéro du format (1 octet), longueur de l'en-tête
(4 octets), en-tête JSON (version, sources avec leur date et leur taille, couverture, comptes, empreinte, sections),
puis le contenu compressé par LZMA : des sections (blocs de textes terminés par un saut de ligne, tableaux numériques
little-endian). Endroits numérotés dans cet ordre : pays, régions et départements (chacun par population
décroissante), puis villes (par numéro GeoNames). Sections : noms (nom affiché de chaque endroit), cles (son nom
normalisé), variantes et variantes_endroits (autres noms normalisés et leur endroit, en différences), codes (codes des
pays, régions et départements), population, region_pays, departement_region, ville_pays, ville_region,
ville_departement, ville_geonames, ville_lat et ville_lon (au dix-millième de degré ; numéros et positions en
différences successives) ; points_ville, points_lat, points_lon (en différences) et points_poids (poids × 1000) : les
points en plus d'une ville.
"""
import argparse
import hashlib
import io
import json
import lzma
import os
import re
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
from myphotos.lieux import FORMAT, MAGIQUE, POIDS_POPULATION, normaliser  # noqa: E402  (après sys.path)

ADRESSE = 'https://download.geonames.org/export/dump/'
AGENT = 'MyPhotos/1 (preparation des donnees de lieux)'
SORTIE = RACINE / 'myphotos' / 'lieux.bin'

# Outre-mer français : chaque territoire est une région de la France, sans département (son nom suffit).
# code GeoNames -> (nom français, autres noms, niveau administratif de ses communes dans GeoNames)
OUTRE_MER = {
    'GP': ('Guadeloupe', (), 'ADM4'),
    'MQ': ('Martinique', (), 'ADM4'),
    'GF': ('Guyane', ('Guyane française',), 'ADM4'),
    'RE': ('La Réunion', ('Réunion', 'île de la Réunion'), 'ADM4'),
    'YT': ('Mayotte', (), 'ADM1'),
    'PM': ('Saint-Pierre-et-Miquelon', (), 'ADM1'),
    'BL': ('Saint-Barthélemy', (), None),
    'MF': ('Saint-Martin', (), None),
    'WF': ('Wallis-et-Futuna', (), 'ADM1'),
    'PF': ('Polynésie française', ('Tahiti',), 'ADM2'),
    'NC': ('Nouvelle-Calédonie', (), 'ADM2'),
    'TF': ('Terres australes et antarctiques françaises', ('TAAF',), None),
}
# Champ de GeoNames qui donne le code de la commune, selon son niveau.
_CHAMP_NIVEAU = {'ADM1': 10, 'ADM2': 11, 'ADM3': 12, 'ADM4': 13}

# Pays voisins de la France et lieux de vacances habituels d'une famille française : villes et villages d'au moins
# SEUIL_PROCHES habitants. Ailleurs : SEUIL_MONDE, plus les capitales et chefs-lieux de région.
PROCHES = {'BE', 'LU', 'CH', 'MC', 'AD', 'LI', 'SM', 'VA', 'DE', 'IT', 'ES', 'PT', 'GB', 'IE', 'NL', 'AT', 'GR',
           'HR', 'MT', 'CY', 'SI', 'ME', 'JE', 'GG', 'IM', 'GI', 'MA', 'TN', 'MU', 'IS', 'NO'}
SEUIL_PROCHES = 1000
SEUIL_MONDE = 5000
TOUJOURS = {'PPLC', 'PPLA', 'PPLG'}         # capitales, chefs-lieux de région, sièges de gouvernement
# Écartés : quartiers (PPLX), lieux historiques, abandonnés ou détruits.
ECARTES = {'PPLX', 'PPLH', 'PPLQ', 'PPLW', 'PPLCH', 'PPLCD'}
HISTORIQUES = ECARTES - {'PPLX'}
# Quartiers gardés comme points en plus de leur ville (code INSEE de la commune, champ admin4 de FR.zip).
GRANDES_VILLES = {'75056': 'Paris', '69123': 'Lyon', '13055': 'Marseille'}

# Langues dont les noms sont lus (le français, et les langues de pays les plus visités) ; pour chaque pays, ses deux
# premières langues (countryInfo.txt) parmi elles donnent le « nom local ».
LANGUES = ('fr', 'es', 'it', 'de', 'nl', 'pt', 'ca', 'en')

# Noms de régions que tout le monde emploie encore, chacun l'union de départements actuels : les anciennes régions
# (avant 2016) exactement, plus la Provence et la Côte d'Azur (au sens courant). Ce sont des noms de recherche de ces
# départements (chercher('alsace') : le Bas-Rhin et le Haut-Rhin). « Centre », trop courant comme mot, n'en fait pas
# partie.
NOMS_REGIONAUX = {
    'Alsace': ('67', '68'), 'Aquitaine': ('24', '33', '40', '47', '64'), 'Auvergne': ('03', '15', '43', '63'),
    'Basse-Normandie': ('14', '50', '61'), 'Bourgogne': ('21', '58', '71', '89'),
    'Champagne-Ardenne': ('08', '10', '51', '52'), 'Franche-Comté': ('25', '39', '70', '90'),
    'Haute-Normandie': ('27', '76'), 'Languedoc-Roussillon': ('11', '30', '34', '48', '66'),
    'Limousin': ('19', '23', '87'), 'Lorraine': ('54', '55', '57', '88'),
    'Midi-Pyrénées': ('09', '12', '31', '32', '46', '65', '81', '82'), 'Nord-Pas-de-Calais': ('59', '62'),
    'Picardie': ('02', '60', '80'), 'Poitou-Charentes': ('16', '17', '79', '86'),
    'Rhône-Alpes': ('01', '07', '26', '38', '42', '69', '73', '74'),
    'Provence': ('04', '13', '83', '84'), "Côte d'Azur": ('06', '83'),
}
# Noms français corrigés là où GeoNames n'en a pas de bon (accent manquant, forme officielle seule).
NOMS_CORRIGES = {
    'r:GB.SCT': 'Écosse', 'r:GB.WLS': 'Pays de Galles', 'r:GB.NIR': 'Irlande du Nord', 'r:GB.ENG': 'Angleterre',
    'p:GB': 'Royaume-Uni', 'p:US': 'États-Unis', 'p:FR': 'France', 'p:SX': 'Sint Maarten',
    'p:CD': 'République démocratique du Congo', 'r:US.DC': 'Washington D.C.', 'p:AE': 'Émirats arabes unis',
    'p:VI': 'Îles Vierges des États-Unis',
}
# Noms de recherche en plus, que GeoNames n'a pas : noms d'usage des pays, régions touristiques.
NOMS_EN_PLUS = {'p:NL': ('Hollande',), 'p:GB': ('Grande-Bretagne',), 'r:PT.09': ('Algarve',)}
# Îles : nom de recherche des villes qui s'y trouvent (cadre lat_min, lat_max, lon_min, lon_max ; pays).
ILES = {
    'Île de Ré': (46.12, 46.27, -1.58, -1.24, 'FR'), "Île d'Oléron": (45.78, 46.06, -1.43, -1.17, 'FR'),
    'Oléron': (45.78, 46.06, -1.43, -1.17, 'FR'), 'Belle-Île': (47.27, 47.40, -3.27, -3.04, 'FR'),
    'Belle-Île-en-Mer': (47.27, 47.40, -3.27, -3.04, 'FR'), 'Noirmoutier': (46.88, 47.07, -2.32, -2.12, 'FR'),
    "Île de Noirmoutier": (46.88, 47.07, -2.32, -2.12, 'FR'), "Île d'Yeu": (46.68, 46.75, -2.40, -2.27, 'FR'),
    'Majorque': (39.20, 40.00, 2.30, 3.50, 'ES'), 'Minorque': (39.78, 40.10, 3.78, 4.35, 'ES'),
    'Ibiza': (38.85, 39.15, 1.15, 1.65, 'ES'), 'Tenerife': (27.99, 28.60, -16.95, -16.10, 'ES'),
    'Lanzarote': (28.83, 29.25, -13.90, -13.40, 'ES'), 'Fuerteventura': (28.03, 28.77, -14.55, -13.80, 'ES'),
    'Grande Canarie': (27.73, 28.19, -15.85, -15.35, 'ES'), 'Djerba': (33.62, 33.95, 10.70, 11.10, 'TN'),
    'Santorin': (36.33, 36.49, 25.33, 25.50, 'GR'), 'Crète': (34.80, 35.70, 23.50, 26.35, 'GR'),
    'Corfou': (39.35, 39.83, 19.60, 20.15, 'GR'), 'Rhodes': (35.85, 36.47, 27.68, 28.25, 'GR'),
    'Capri': (40.53, 40.57, 14.19, 14.27, 'IT'), 'Sicile': (36.60, 38.35, 12.35, 15.70, 'IT'),
    'Sardaigne': (38.85, 41.32, 8.10, 9.85, 'IT'), 'Madère': (32.60, 32.90, -17.30, -16.60, 'PT'),
}
# Une ville : quelques noms de recherche en plus (Porquerolles est dans Hyères ; Monte-Carlo, un quartier de Monaco).
VILLES_EN_PLUS = {('FR', 'Hyères'): ('Porquerolles', 'Port-Cros', 'Îles d\'Hyères'),
                  ('MC', 'Monaco'): ('Monte-Carlo', 'La Condamine', 'Fontvieille', 'Larvotto', 'Monaco-Ville')}
ARTICLES = ('le ', 'la ', 'les ', 'l ')
# Noms de régions affichés sans leur mot administratif (« Région Provence-Alpes-Côte d'Azur », « Préfecture de
# Tokyo », « État de Berlin », « Province de Groningue », « wilaya d'Oran », « Urban Municipality of Kranj ») ; le nom
# complet reste un nom de recherche. Insensible à la casse, apostrophe droite ou courbe, n'importe quelle lettre après.
PREFIXES_REGIONS = re.compile(
    r"^(?:(?:Région autonome|Province|Préfecture|Prefecture|Canton|District|État|Ville-État|Comté|Gouvernorat|Wilaya|"
    r"Municipalité|Voïvodie|Oblast|Division|Kraï|Kray|Ville) (?:de la |de l['’]|des |de |du |d['’])"
    r"|(?:Région|Region|Préfecture|District|Municipalité|Province|(?:Urban )?Municipality of|City of)\s+)"
    r"(?=[^\W\d_])", re.IGNORECASE)
# « Ville de Saint-Marin », « Ville de Capri » : le nom seul.
PREFIXE_VILLE = re.compile(r"^Ville (?:de |d['’])(?=[^\W\d_])")
ECHELLE = 10000                              # positions au dix-millième de degré (11 m au plus)
TAILLE_MAX = 1_950_000                       # le garde-fou de publication refuse les fichiers de plus de 2 Mo


def fichiers_sources():
    noms = ['countryInfo.txt', 'admin1CodesASCII.txt', 'admin2Codes.txt', 'cities500.zip', 'FR.zip']
    noms += [f'{cc}.zip' for cc in OUTRE_MER]
    return noms + ['alternateNamesV2.zip']


def dossier_cache(cache=None):
    if cache:
        return Path(cache)
    if os.environ.get('MYPHOTOS_CACHE_CONSTRUCTION'):
        return Path(os.environ['MYPHOTOS_CACHE_CONSTRUCTION']) / 'geonames'
    base = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(base) / 'MyPhotos-construction' / 'cache' / 'geonames'


def telecharger(nom, cache, actualiser=False):
    """Le fichier source `nom`, téléchargé dans le cache s'il n'y est pas (ou avec --actualiser), en HTTPS, sous un
    nom provisoire renommé à la fin ; sa date de modification est celle que donne GeoNames."""
    cible = cache / nom
    if cible.is_file() and not actualiser:
        return cible
    cache.mkdir(parents=True, exist_ok=True)
    provisoire = cible.with_name(cible.name + '.part')
    requete = urllib.request.Request(ADRESSE + nom, headers={'User-Agent': AGENT})
    print(f'téléchargement de {nom}…', flush=True)
    with urllib.request.urlopen(requete, timeout=120) as r, open(provisoire, 'wb') as f:
        if not r.geturl().startswith('https://'):
            raise SystemExit(f'{nom} : redirection hors HTTPS refusée')
        attendu = r.headers.get('Content-Length')
        while True:
            bloc = r.read(1 << 20)
            if not bloc:
                break
            f.write(bloc)
        date = r.headers.get('Last-Modified')
    if attendu is not None and provisoire.stat().st_size != int(attendu):
        provisoire.unlink()
        raise SystemExit(f'{nom} : téléchargement incomplet')
    os.replace(provisoire, cible)
    if date:
        try:
            t = parsedate_to_datetime(date).timestamp()
            os.utime(cible, (t, t))
        except (TypeError, ValueError, OverflowError):
            pass
    return cible


def lignes_zip(chemin, membre=None):
    """Lignes (champs séparés par des tabulations) d'un fichier texte de GeoNames dans une archive zip."""
    with zipfile.ZipFile(chemin) as z:
        membre = membre or Path(chemin).stem + '.txt'
        with z.open(membre) as f:
            for ligne in io.TextIOWrapper(f, encoding='utf-8'):
                yield ligne.rstrip('\n').split('\t')


def lignes_texte(chemin):
    with open(chemin, encoding='utf-8') as f:
        for ligne in f:
            if ligne.strip() and not ligne.startswith('#'):
                yield ligne.rstrip('\n').split('\t')


def lire_noms(chemin, voulus):
    """{geonameid: [(langue, nom, préféré, court)]} pour les numéros voulus, langues de LANGUES, sans les noms
    familiers ni historiques. alternateNamesV2.txt fait près de 800 Mo : il est lu par blocs, avec une expression
    régulière qui ne retient que ces langues."""
    motif = re.compile(rb'^\d+\t(\d+)\t(' + b'|'.join(x.encode() for x in LANGUES) +
                       rb')\t([^\t\n]*)\t([^\t\n]*)\t([^\t\n]*)\t([^\t\n]*)\t([^\t\n]*)', re.M)
    noms = defaultdict(list)
    with zipfile.ZipFile(chemin) as z, z.open('alternateNamesV2.txt') as f:
        reste = b''
        while True:
            bloc = f.read(1 << 24)
            if not bloc:
                break
            bloc = reste + bloc
            coupe = bloc.rfind(b'\n') + 1
            reste = bloc[coupe:]
            for m in motif.finditer(bloc, 0, coupe):
                gid = int(m.group(1))
                if gid not in voulus or m.group(6) == b'1' or m.group(7) == b'1':
                    continue                     # familier ou historique
                nom = m.group(3).decode('utf-8').strip()
                if nom and '\n' not in nom:
                    noms[gid].append((m.group(2).decode(), nom, m.group(4) == b'1', m.group(5) == b'1'))
    return noms


def nom_francais(alternatifs):
    """Le nom français : court et préféré, sinon court, sinon préféré, sinon le premier ; None s'il n'y en a pas."""
    fr = [x for x in alternatifs if x[0] == 'fr' and '.' not in x[1]]
    if not fr:
        return None
    for test in (lambda x: x[2] and x[3], lambda x: x[3], lambda x: x[2]):
        for x in fr:
            if test(x):
                return x[1]
    return fr[0][1]


class Ent:
    """Un endroit en préparation."""
    __slots__ = ('type', 'code', 'nom', 'variantes', 'pays', 'region', 'departement', 'population', 'lat', 'lon',
                 'gid', 'date')

    def __init__(self, type, code, nom, pays=None, region=None, departement=None, population=0, lat=None, lon=None,
                 gid=None, date=None):
        self.type, self.code, self.nom = type, code, nom
        self.variantes = set()
        self.pays, self.region, self.departement = pays, region, departement
        self.population, self.lat, self.lon, self.gid = int(population or 0), lat, lon, gid
        self.date = date                         # date de modification de la ligne dans GeoNames


def sans_prefixe(nom):
    """Nom de région sans son mot administratif, s'il est suivi d'un nom propre (voir PREFIXES_REGIONS : une
    majuscule de n'importe quel alphabet, « Đakovo », « Žilina » ; jamais « Région de la capitale »)."""
    court = PREFIXES_REGIONS.sub('', nom, count=1).strip()
    return court if court and court != nom and court[0].isupper() else nom


def arrondir(population):
    """Population arrondie à deux chiffres significatifs (144 899 -> 140 000) : assez pour comparer deux villes, et
    près de 100 Ko de moins dans le fichier (mesuré)."""
    n = len(str(population))
    return population if n <= 2 else round(population, 2 - n)


def _population(r):
    try:
        return max(0, int(r[14] or 0))
    except ValueError:
        return 0


def grille(villes, points):
    """La grille de myphotos/lieux.py (même choix de la ville : distance / poids, même règle des pays) sur ces villes
    et ces points [(ville, lat, lon, poids)] ; ville_de(i) -> Ent."""
    from myphotos.lieux import _Donnees
    codes = sorted({v.pays for v in villes})
    num = {c: i for i, c in enumerate(codes)}
    rang = {id(v): i for i, v in enumerate(villes)}
    g = object.__new__(_Donnees)
    g.n_admin, g.n_villes = 0, len(villes)
    g.v_lat = np.array([v.lat for v in villes], np.float64)
    g.v_lon = np.array([v.lon for v in villes], np.float64)
    g.pop = np.array([arrondir(v.population) for v in villes], np.float64)
    g.v_pays = np.array([num[v.pays] for v in villes], np.int64)
    g.p_ville = np.array([rang[id(v)] for v, _, _, _ in points], np.int64)
    g.p_lat = np.array([la for _, la, _, _ in points], np.float64)
    g.p_lon = np.array([lo for _, _, lo, _ in points], np.float64)
    g.p_poids = np.array([round(po, 3) for _, _, _, po in points], np.float64)
    g._preparer_grille()
    return g, num


def frontieres(villes, points, ecartees, nouvelle_ville):
    """Lieux habités écartés par les seuils (et quartiers, PPLX) qui tomberaient dans un autre pays que le leur :
    localisés avec les villes gardées (vérité : GeoNames lui-même), réintégrés (un quartier : point
    en plus de la ville la plus proche de son pays), et ainsi de suite jusqu'à ce que plus rien ne change. Mesuré à la
    relecture de la 0.5.0 : 1 848 lieux sur 140 098 dans un autre pays (Monaco : 4 sur 11 ; Luxembourg : 17 sur 140),
    1 572 sans lieu. (villes ajoutées, points ajoutés)."""
    from myphotos.lieux import RAYON_KM, RAYON_PAYS_KM
    n_villes = n_points = 0
    restants = list(ecartees)
    for _tour in range(6):
        g, num = grille(villes, points)
        ajouts, encore = [], []
        for r in restants:
            lat, lon = float(r[4]), float(r[5])
            i, _d = g.plus_proche(lat, lon, RAYON_KM)
            if i is None:                        # sans ville à 30 km : le pays de la plus proche, jusqu'à 100 km
                i, _d = g.plus_proche(lat, lon, RAYON_PAYS_KM, pondere=False)
            if i is None or villes[i].pays == r[8]:
                encore.append(r)                 # bon pays (ou rien de connu à 100 km : pas un pays faux)
                continue
            ajouts.append(r)
        if not ajouts:
            break
        for r in ajouts:
            lat, lon = float(r[4]), float(r[5])
            if r[7] == 'PPLX':
                # Quartier : point en plus de la ville la plus proche de son pays (à 30 km au plus).
                idx = g.candidats(lat, lon, RAYON_KM)
                miens = [int(g._g_ordre[k]) for k in idx if int(g._g_pays[k]) == num.get(r[8], -1)]
                if not miens:
                    continue
                v = min(miens, key=lambda k: (villes[k].lat - lat) ** 2 + ((villes[k].lon - lon) * 0.7) ** 2)
                points.append((villes[v], lat, lon, 1.0 + POIDS_POPULATION * (_population(r) / 1000.0) ** 0.5))
                villes[v].variantes.update((r[1], r[2]))
                n_points += 1
            else:
                villes.append(nouvelle_ville(r))
                n_villes += 1
        restants = encore
    return n_villes, n_points


def rassembler(cache, actualiser=False):
    """Lit les sources (téléchargées au besoin) : {pays, regions, departements, villes (listes d'Ent, dans l'ordre
    du fichier), n_france, chemins}."""
    chemins = {nom: telecharger(nom, cache, actualiser) for nom in fichiers_sources()}

    # ------------------------------------------------ pays, régions, départements
    pays = {}                                   # code ISO -> Ent ; info en plus : langues, geonameid
    langues_pays, gid_pays = {}, {}
    for r in lignes_texte(chemins['countryInfo.txt']):
        if len(r) < 17 or r[0] in OUTRE_MER:
            continue
        pays[r[0]] = Ent('pays', r[0], r[4], population=int(r[7] or 0))
        pays[r[0]].variantes.add(r[4])
        langues_pays[r[0]] = [x.split('-')[0] for x in r[15].split(',') if x][:2]
        gid_pays[r[0]] = int(r[16]) if r[16].isdigit() else None
    regions, gid_regions = {}, {}               # 'ES.51' -> Ent
    for r in lignes_texte(chemins['admin1CodesASCII.txt']):
        cc = r[0].split('.')[0]
        if cc in pays:
            regions[r[0]] = Ent('region', r[0], r[1], pays=cc)
            regions[r[0]].variantes.update((r[1], r[2]))
            gid_regions[r[0]] = int(r[3])
    for cc, (nom, autres, _) in OUTRE_MER.items():
        e = Ent('region', 'FR.' + cc, nom, pays='FR')
        e.variantes.update(autres)
        regions['FR.' + cc] = e
    departements, gid_departements = {}, {}     # 'FR.29' -> Ent (France métropolitaine seulement)
    for r in lignes_texte(chemins['admin2Codes.txt']):
        morceaux = r[0].split('.')
        if morceaux[0] == 'FR' and len(morceaux) == 3:
            code = 'FR.' + morceaux[2]
            departements[code] = Ent('departement', code, r[1], pays='FR', region='FR.' + morceaux[1])
            departements[code].variantes.update((r[1], r[2]))
            gid_departements[code] = int(r[3])
    for nom, numeros in NOMS_REGIONAUX.items():
        for n in numeros:
            departements['FR.' + n].variantes.add(nom)

    # ------------------------------------------------ villes de France
    # Une ville par commune : le lieu habité (PPL) du même nom, à la population de la commune. Une commune nouvelle
    # sans lieu de son nom (Cherbourg-en-Cotentin, Vexin-sur-Epte) est représentée par ses anciennes communes qui ont
    # une population (Cherbourg, Tourlaville…), avec son nom comme nom de recherche ; une commune nouvelle qui a gardé
    # le nom de l'une d'elles (Annecy) garde aussi les autres (Annecy-le-Vieux, Seynod), reconnues à leur ancienne
    # commune (ADM4H de GeoNames, même nom, à moins de 5 km) : ainsi les quartiers qui ont une population (à Nice,
    # Pasteur ou Libération ; les arrondissements de Paris) ne deviennent pas des villes. Une commune sans aucun lieu
    # habité connu est représentée par son propre point.
    villes = []
    communes_nouvelles = anciennes_communes = 0
    centres, quartiers = {}, []                 # Paris, Lyon, Marseille : geonameid du centre ; leurs quartiers
    for cc in ['FR'] + list(OUTRE_MER):
        lignes = [r for r in lignes_zip(chemins[f'{cc}.zip']) if len(r) >= 19]
        niveau = 'ADM4' if cc == 'FR' else OUTRE_MER[cc][2]
        champ = _CHAMP_NIVEAU.get(niveau)
        communes = {r[champ]: r for r in lignes if niveau and r[7] == niveau and r[champ]}
        historiques = defaultdict(list)
        for r in lignes:
            if r[7] == 'ADM4H':
                historiques[normaliser(r[1])].append((float(r[4]), float(r[5])))
        lieux = [r for r in lignes if r[6] == 'P' and r[7] not in ECARTES]
        if cc == 'FR':
            quartiers += [r for r in lignes if r[6] == 'P' and r[7] == 'PPLX' and r[13] in GRANDES_VILLES]
        par_commune = defaultdict(list)
        for r in lieux:
            if champ is not None and r[champ] in communes:
                par_commune[r[champ]].append(r)

        def ancienne_commune(r):
            lat, lon = float(r[4]), float(r[5])
            return any(abs(lat - a) < 0.045 and abs(lon - b) < 0.065 for a, b in historiques.get(normaliser(r[1]), ()))

        gardes = {}                             # geonameid -> (ligne, population, nom de la commune en variante)
        for code, c in communes.items():
            dedans = par_commune.get(code, [])
            memes = [r for r in dedans if normaliser(r[1]) == normaliser(c[1])]
            if memes:
                r = max(memes, key=lambda x: (x[7] != 'PPL', _population(x)))
                gardes[r[0]] = (r, _population(c) or _population(r), None)   # population de la commune d'abord
                if cc == 'FR' and code in GRANDES_VILLES:
                    centres[code] = int(r[0])
                for x in dedans:
                    if x is not r and _population(x) > 0 and ancienne_commune(x):
                        gardes[x[0]] = (x, _population(x), c[1])
                        anciennes_communes += 1
                continue
            anciennes = [r for r in dedans if _population(r) > 0]
            if anciennes:
                communes_nouvelles += 1
                for r in anciennes:
                    gardes[r[0]] = (r, _population(r), c[1])     # « Cherbourg-en-Cotentin » sur Cherbourg…
            else:
                gardes[c[0]] = (c, _population(c), None)      # commune sans lieu habité connu : son propre point
        # Territoires sans communes dans GeoNames (Saint-Barthélemy, Saint-Martin, TAAF), lieu sans commune connue :
        # les lieux habités qui ont une population, et les chefs-lieux.
        for r in lieux:
            if r[0] not in gardes and (champ is None or r[champ] not in communes) and \
                    (_population(r) > 0 or r[7] in TOUJOURS):
                gardes[r[0]] = (r, _population(r), None)
        for gid, (r, pop, commune) in gardes.items():
            if cc == 'FR':
                region, dep = 'FR.' + r[10], 'FR.' + r[11]
                if region not in regions or dep not in departements:
                    continue                     # (aucun cas en 2026 : tout lieu habité de France a les deux)
            else:
                region, dep = 'FR.' + cc, None
            v = Ent('ville', None, r[1], pays='FR', region=region, departement=dep, population=pop,
                    lat=float(r[4]), lon=float(r[5]), gid=int(gid), date=r[18] or None)
            v.variantes.update((r[1], r[2]))
            if commune:
                v.variantes.add(commune)
            villes.append(v)
    print(f'France : {len(villes)} villes et villages ({communes_nouvelles} communes nouvelles, '
          f'{anciennes_communes} anciennes communes en plus)', flush=True)

    n_france = len(villes)
    par_gid = {v.gid: v for v in villes}
    points = []                                 # (ville, lat, lon, poids) : points en plus d'une ville
    for code, gid in centres.items():           # quartiers de Paris, Lyon et Marseille
        centre = par_gid[gid]
        siens = [r for r in quartiers if r[13] == code]
        poids = 1.0 + POIDS_POPULATION * (centre.population / (len(siens) + 1) / 1000.0) ** 0.5
        points += [(centre, float(r[4]), float(r[5]), poids) for r in siens]

    def nouvelle_ville(r):
        code_region = f'{r[8]}.{r[10]}'
        v = Ent('ville', None, r[1], pays=r[8], region=code_region if code_region in regions else None,
                population=_population(r), lat=float(r[4]), lon=float(r[5]), gid=int(r[0]), date=r[18] or None)
        v.variantes.update((r[1], r[2]))
        return v

    ecartees = []                               # lieux habités hors de France écartés par les seuils
    for r in lignes_zip(chemins['cities500.zip'], 'cities500.txt'):
        if len(r) < 19 or r[8] in OUTRE_MER or r[8] == 'FR' or r[8] not in pays or r[7] in HISTORIQUES:
            continue
        seuil = SEUIL_PROCHES if r[8] in PROCHES else SEUIL_MONDE
        if r[7] == 'PPLX' or (_population(r) < seuil and r[7] not in TOUJOURS):
            ecartees.append(r)
            continue
        villes.append(nouvelle_ville(r))
    print(f'reste du monde : {len(villes) - n_france} villes', flush=True)
    t = time.monotonic()
    villes_frontieres, points_frontieres = frontieres(villes, points, ecartees, nouvelle_ville)
    print(f'frontières : {villes_frontieres} villes et {points_frontieres} quartiers en plus '
          f'({time.monotonic() - t:.0f} s)', flush=True)

    # ------------------------------------------------ noms français et locaux
    voulus = {v.gid for v in villes[n_france:]}
    voulus.update(x for x in gid_pays.values() if x)
    voulus.update(gid_regions.values())
    voulus.update(gid_departements.values())
    t = time.monotonic()
    alternatifs = lire_noms(chemins['alternateNamesV2.zip'], voulus)
    print(f'noms alternatifs lus en {time.monotonic() - t:.0f} s ({len(alternatifs)} lieux)', flush=True)

    def nommer(e, gid, cc):
        alt = alternatifs.get(gid, [])
        fr = nom_francais(alt)
        if fr:
            e.variantes.add(e.nom)
            e.nom = fr
        locales = set(langues_pays.get(cc, [])) | {'fr'}
        e.variantes.update(nom for langue, nom, _, _ in alt if langue in locales and '.' not in nom)

    for code, e in pays.items():
        nommer(e, gid_pays.get(code), code)
        e.variantes.update(nom for langue, nom, _, _ in alternatifs.get(gid_pays.get(code), [])
                           if langue == 'en' and '.' not in nom)
    for code, e in regions.items():
        if code in gid_regions:
            nommer(e, gid_regions[code], e.pays)
        court = sans_prefixe(e.nom)
        if court != e.nom:
            e.variantes.add(e.nom)
            e.nom = court
    for code, e in departements.items():
        nommer(e, gid_departements.get(code), 'FR')
    for v in villes[n_france:]:
        nommer(v, v.gid, v.pays)
    for prefixe, table in (('p:', pays), ('r:', regions), ('d:', departements)):
        for code, e in table.items():
            if prefixe + code in NOMS_CORRIGES:
                e.variantes.add(e.nom)
                e.nom = NOMS_CORRIGES[prefixe + code]
            e.variantes.update(NOMS_EN_PLUS.get(prefixe + code, ()))
    for v in villes:
        court = PREFIXE_VILLE.sub('', v.nom, count=1)
        if court != v.nom:
            v.variantes.add(v.nom)
            v.nom = court
        v.variantes.update(VILLES_EN_PLUS.get((v.pays, v.nom), ()))
        for ile, (lat1, lat2, lon1, lon2, cc) in ILES.items():
            if v.pays == cc and lat1 <= v.lat <= lat2 and lon1 <= v.lon <= lon2:
                v.variantes.add(ile)

    # ------------------------------------------------ endroits gardés, numérotés
    utiles_regions = {v.region for v in villes if v.region}
    utiles_pays = {v.pays for v in villes}
    for code, e in departements.items():
        e.population = 0
    for e in regions.values():
        e.population = 0
    for v in villes:
        if v.departement:
            departements[v.departement].population += v.population
        if v.region:
            regions[v.region].population += v.population
    liste_pays = sorted((e for c, e in pays.items() if c in utiles_pays), key=lambda e: (-e.population, e.code))
    liste_regions = sorted((e for c, e in regions.items() if c in utiles_regions),
                           key=lambda e: (-e.population, e.code))
    liste_deps = sorted(departements.values(), key=lambda e: (-e.population, e.code))
    # Villes rangées par numéro GeoNames : GeoNames les a numérotées pays par pays, souvent dans l'ordre alphabétique ;
    # noms et numéros se compressent alors le mieux (mesuré sur une première sélection de 95 004 villes : 0,94 Mo pour
    # leurs noms, numéros, positions et régions, contre 1,00 Mo dans l'ordre de la grille et 1,36 Mo par population).
    villes.sort(key=lambda v: v.gid)

    # Variantes françaises sans article (« Les Sables-d'Olonne » -> « sables d olonne »), s'il reste deux mots au moins.
    for v in villes[:]:
        if v.pays == 'FR':
            for nom in [v.nom] + list(v.variantes):
                k = normaliser(nom)
                for a in ARTICLES:
                    if k.startswith(a) and len(k[len(a):].split()) >= 2:
                        v.variantes.add(k[len(a):])

    return {'pays': liste_pays, 'regions': liste_regions, 'departements': liste_deps, 'villes': villes,
            'n_france': n_france, 'chemins': chemins, 'points': points,
            'date': max(v.date for v in villes if v.date)}


def preparer(cache, sortie, actualiser=False):
    debut = time.monotonic()
    r = rassembler(cache, actualiser)
    liste_pays, liste_regions, liste_deps, villes = r['pays'], r['regions'], r['departements'], r['villes']
    n_france, chemins = r['n_france'], r['chemins']
    rang_ville = {id(v): i for i, v in enumerate(villes)}
    points = sorted(((rang_ville[id(v)], round(la * ECHELLE), round(lo * ECHELLE), min(65535, round(po * 1000)))
                     for v, la, lo, po in r['points']))
    entites = liste_pays + liste_regions + liste_deps + villes
    i_pays = {e.code: i for i, e in enumerate(liste_pays)}
    i_regions = {e.code: i for i, e in enumerate(liste_regions)}
    i_deps = {e.code: i for i, e in enumerate(liste_deps)}

    # ------------------------------------------------ noms de recherche
    # Clé de chaque endroit (son nom normalisé, dans l'ordre des endroits), puis les autres clés (variantes), avec
    # l'endroit de chacune ; l'index est construit à la première recherche (myphotos/lieux.py).
    cles = [normaliser(e.nom) for e in entites]
    variantes = []
    for i, e in enumerate(entites):
        for k in sorted({normaliser(x) for x in e.variantes} - {cles[i]}):
            if len(k) >= 2:
                variantes.append((i, k))

    # ------------------------------------------------ sections
    def bloc(textes):
        return ''.join(t.replace('\n', ' ') + '\n' for t in textes).encode('utf-8')

    def deltas(valeurs):
        a = np.asarray(valeurs, np.int64)
        return np.diff(a, prepend=0).astype('<i4').tobytes()

    sections = {
        'noms': bloc(e.nom for e in entites),
        'cles': bloc(cles),
        'variantes': bloc(k for _, k in variantes),
        'variantes_endroits': deltas([i for i, _ in variantes]),
        'codes': bloc(e.code for e in liste_pays + liste_regions + liste_deps),
        'population': np.array([min(arrondir(e.population) if e.type == 'ville' else e.population, 2**32 - 1)
                                for e in entites], '<u4').tobytes(),
        'region_pays': np.array([i_pays[e.pays] for e in liste_regions], '<i2').tobytes(),
        'departement_region': np.array([i_regions[e.region] for e in liste_deps], '<i2').tobytes(),
        'ville_pays': np.array([i_pays[v.pays] for v in villes], '<i2').tobytes(),
        'ville_region': np.array([i_regions.get(v.region, -1) for v in villes], '<i2').tobytes(),
        'ville_departement': np.array([i_deps.get(v.departement, -1) for v in villes], '<i2').tobytes(),
        'ville_geonames': deltas([v.gid for v in villes]),
        'ville_lat': deltas([round(v.lat * ECHELLE) for v in villes]),
        'ville_lon': deltas([round(v.lon * ECHELLE) for v in villes]),
        'points_ville': deltas([x[0] for x in points]),
        'points_lat': deltas([x[1] for x in points]),
        'points_lon': deltas([x[2] for x in points]),
        'points_poids': np.array([x[3] for x in points], '<u2').tobytes(),
    }
    charge, positions = bytearray(), {}
    for nom, octets in sections.items():
        positions[nom] = [len(charge), len(octets)]
        charge += octets
    charge = bytes(charge)
    empreinte = hashlib.sha256(charge).hexdigest()
    # Dictionnaire de LZMA à la taille du contenu (et non 64 Mo, celui du réglage 9) : la lecture n'en alloue pas plus.
    dictionnaire = max(1 << 20, 1 << (len(charge) - 1).bit_length())
    compresse = lzma.compress(charge, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, filters=[
        {'id': lzma.FILTER_LZMA2, 'preset': 9 | lzma.PRESET_EXTREME, 'dict_size': dictionnaire}])
    # Version : du contenu seul (la dernière modification des lignes de GeoNames retenues, et l'empreinte), jamais de la
    # date des fichiers du cache (relecture de la 0.5.0 : une copie du cache sans ses dates changeait la version, et
    # tous les utilisateurs auraient refait le calcul des lieux de toutes leurs photos).
    entete = {
        'format': FORMAT,
        'version': f'{r["date"]}-{empreinte[:8]}',
        'date': r['date'],                       # dernière modification des lignes de GeoNames retenues
        'licence': 'GeoNames (https://www.geonames.org), Creative Commons Attribution 4.0 (CC BY 4.0)',
        'sources': [{'fichier': nom, 'adresse': ADRESSE + nom, 'taille': p.stat().st_size}
                    for nom, p in chemins.items()],
        'couverture': {'france': 'toutes les communes, métropole et outre-mer',
                       'proches': sorted(PROCHES), 'seuil_proches': SEUIL_PROCHES, 'seuil_monde': SEUIL_MONDE,
                       'toujours': sorted(TOUJOURS)},
        'comptes': {'pays': len(liste_pays), 'regions': len(liste_regions), 'departements': len(liste_deps),
                    'villes': len(villes), 'villes_france': n_france, 'variantes': len(variantes),
                    'points': len(points)},
        'echelle': ECHELLE,
        'empreinte': empreinte,
        'sections': positions,
    }
    texte = json.dumps(entete, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    contenu = MAGIQUE + bytes([FORMAT]) + len(texte).to_bytes(4, 'little') + texte + compresse
    if len(contenu) > TAILLE_MAX:
        raise SystemExit(f'{len(contenu)} octets : plus que {TAILLE_MAX}, la limite du garde-fou de publication '
                         '(2 Mo) est trop proche. Relevez SEUIL_MONDE ou SEUIL_PROCHES.')
    sortie = Path(sortie)
    provisoire = sortie.with_name(sortie.name + '.part')
    provisoire.write_bytes(contenu)
    os.replace(provisoire, sortie)
    print(f'{sortie} : {len(contenu):,} octets'.replace(',', ' '))
    print(f'  {len(liste_pays)} pays, {len(liste_regions)} régions, {len(liste_deps)} départements, '
          f'{len(villes)} villes dont {n_france} en France ; {len(variantes)} autres noms de recherche')
    print('  sections (octets avant compression) : ' +
          ', '.join(f'{n} {len(o)}' for n, o in sections.items()))
    print(f'  version {entete["version"]}, préparé en {time.monotonic() - debut:.0f} s')
    return entete


def main(argv=None):
    p = argparse.ArgumentParser(description='Prépare myphotos/lieux.bin à partir des données de GeoNames.')
    p.add_argument('--cache', help='dossier des sources téléchargées (hors du dépôt)')
    p.add_argument('--sortie', default=str(SORTIE), help='fichier produit (défaut : myphotos/lieux.bin)')
    p.add_argument('--actualiser', action='store_true', help='télécharger de nouveau les sources')
    a = p.parse_args(argv)
    preparer(dossier_cache(a.cache), a.sortie, a.actualiser)


if __name__ == '__main__':
    main()
