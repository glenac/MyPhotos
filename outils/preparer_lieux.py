r"""Prépare myphotos/lieux.bin, les lieux connus de MyPhotos (0.5.0), à partir des données de GeoNames.

    python -I outils\preparer_lieux.py [--cache <dossier>] [--sortie <fichier>] [--actualiser]

Sources (GeoNames, https://www.geonames.org, licence Creative Commons Attribution 4.0) :
https://download.geonames.org/export/dump/ : cities500.zip (villes du monde), FR.zip et les fichiers des territoires
d'outre-mer (GP, MQ, GF, RE, YT, PM, BL, MF, WF, PF, NC, TF : toutes les communes), admin1CodesASCII.txt (régions),
admin2Codes.txt (départements), countryInfo.txt (pays) et alternateNamesV2.zip (noms en français et dans la langue du
pays). Ils sont téléchargés une seule fois (près de 240 Mo, dont 205 pour alternateNamesV2.zip) dans un cache HORS du
dépôt : --cache, sinon %MYPHOTOS_CACHE_CONSTRUCTION%\geonames, sinon %LOCALAPPDATA%\MyPhotos-construction\cache\geonames.
--actualiser les télécharge de nouveau. Le script est relançable : les mêmes sources donnent exactement le même
fichier (même empreinte, même version).

Ce qui est gardé (seuils choisis en mesurant la taille du fichier, qui doit rester sous 2 Mo : PROCHES, SEUIL_PROCHES
et SEUIL_MONDE ci-dessous ; mesures dans docs/ARCHITECTURE.md, section « Lieux ») :
- France, métropole et outre-mer : toutes les communes (chef-lieu de chaque commune, et anciennes communes d'une
  commune nouvelle quand GeoNames leur donne une population : Cherbourg et Tourlaville dans Cherbourg-en-Cotentin) ;
- pays voisins et lieux de vacances habituels (PROCHES) : les villes et villages d'au moins SEUIL_PROCHES habitants ;
- reste du monde : les villes d'au moins SEUIL_MONDE habitants, plus les capitales et les chefs-lieux de région.

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
différences successives).
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
from myphotos.lieux import FORMAT, MAGIQUE, normaliser  # noqa: E402  (après sys.path)

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
           'HR', 'MT', 'CY', 'SI', 'ME', 'JE', 'GG', 'IM', 'GI', 'MA', 'TN', 'MU'}
SEUIL_PROCHES = 1000
SEUIL_MONDE = 5000
TOUJOURS = {'PPLC', 'PPLA', 'PPLG'}         # capitales, chefs-lieux de région, sièges de gouvernement
# Écartés : quartiers (PPLX), lieux historiques, abandonnés ou détruits.
ECARTES = {'PPLX', 'PPLH', 'PPLQ', 'PPLW', 'PPLCH', 'PPLCD'}

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
    'p:GB': 'Royaume-Uni', 'p:US': 'États-Unis', 'p:FR': 'France',
}
ARTICLES = ('le ', 'la ', 'les ', 'l ')
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
                 'gid')

    def __init__(self, type, code, nom, pays=None, region=None, departement=None, population=0, lat=None, lon=None,
                 gid=None):
        self.type, self.code, self.nom = type, code, nom
        self.variantes = set()
        self.pays, self.region, self.departement = pays, region, departement
        self.population, self.lat, self.lon, self.gid = int(population or 0), lat, lon, gid


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
                    lat=float(r[4]), lon=float(r[5]), gid=int(gid))
            v.variantes.update((r[1], r[2]))
            if commune:
                v.variantes.add(commune)
            villes.append(v)
    print(f'France : {len(villes)} villes et villages ({communes_nouvelles} communes nouvelles, '
          f'{anciennes_communes} anciennes communes en plus)', flush=True)

    n_france = len(villes)
    for r in lignes_zip(chemins['cities500.zip'], 'cities500.txt'):
        if len(r) < 19 or r[8] in OUTRE_MER or r[8] == 'FR' or r[8] not in pays or r[7] in ECARTES:
            continue
        pop = _population(r)
        seuil = SEUIL_PROCHES if r[8] in PROCHES else SEUIL_MONDE
        if pop < seuil and r[7] not in TOUJOURS:
            continue
        code_region = f'{r[8]}.{r[10]}'
        v = Ent('ville', None, r[1], pays=r[8], region=code_region if code_region in regions else None,
                population=pop, lat=float(r[4]), lon=float(r[5]), gid=int(r[0]))
        v.variantes.update((r[1], r[2]))
        villes.append(v)
    print(f'reste du monde : {len(villes) - n_france} villes', flush=True)

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
    for code, e in departements.items():
        nommer(e, gid_departements.get(code), 'FR')
    for v in villes[n_france:]:
        nommer(v, v.gid, v.pays)
    for prefixe, table in (('p:', pays), ('r:', regions), ('d:', departements)):
        for code, e in table.items():
            if prefixe + code in NOMS_CORRIGES:
                e.variantes.add(e.nom)
                e.nom = NOMS_CORRIGES[prefixe + code]

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
            'n_france': n_france, 'chemins': chemins}


def preparer(cache, sortie, actualiser=False):
    debut = time.monotonic()
    r = rassembler(cache, actualiser)
    liste_pays, liste_regions, liste_deps, villes = r['pays'], r['regions'], r['departements'], r['villes']
    n_france, chemins = r['n_france'], r['chemins']
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
    dates = {nom: datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime('%Y-%m-%d')
             for nom, p in chemins.items()}
    entete = {
        'format': FORMAT,
        'version': f'{max(dates.values())}-{empreinte[:8]}',
        'licence': 'GeoNames (https://www.geonames.org), Creative Commons Attribution 4.0 (CC BY 4.0)',
        'sources': [{'fichier': nom, 'adresse': ADRESSE + nom, 'date': dates[nom], 'taille': p.stat().st_size}
                    for nom, p in chemins.items()],
        'couverture': {'france': 'toutes les communes, métropole et outre-mer',
                       'proches': sorted(PROCHES), 'seuil_proches': SEUIL_PROCHES, 'seuil_monde': SEUIL_MONDE,
                       'toujours': sorted(TOUJOURS)},
        'comptes': {'pays': len(liste_pays), 'regions': len(liste_regions), 'departements': len(liste_deps),
                    'villes': len(villes), 'villes_france': n_france, 'variantes': len(variantes)},
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
