"""Essais des lieux (0.5.0) : données GeoNames embarquées (myphotos/lieux.bin), noms, proximité, base et calcul en
arrière-plan. Images générées par Pillow, avec des positions GPS fabriquées (jamais de vraies photos) ; aucun accès à
Internet.

    .venv\\Scripts\\python.exe -m unittest tests.test_lieux
"""
import importlib.util
import io
import logging
import math
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                                     # noqa: E402
from PIL import Image                                  # noqa: E402

import images_essai as ie                              # noqa: E402
from myphotos import indexation, lieux, modele          # noqa: E402
from myphotos.base import Base                         # noqa: E402
from myphotos.indexation import Indexeur               # noqa: E402
from myphotos.lieux import Endroit, Lieux, normaliser  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
FICHIER = RACINE / 'myphotos' / 'lieux.bin'
VERSION_0_4_1 = 'f999167'                              # dernier commit de la 0.4.1
logging.getLogger('myphotos').setLevel(logging.CRITICAL)   # pannes simulées : avertissements attendus

# Positions connues (degrés décimaux).
BREST = (48.3904, -4.4861)
QUIMPER = (47.9960, -4.1024)
NOTRE_DAME = (48.8530, 2.3499)
TOUR_EIFFEL = (48.8584, 2.2945)
LOCRONAN = (48.0986, -4.2084)           # village du Finistère, environ 800 habitants
SEVILLE = (37.3891, -5.9845)
TIMES_SQUARE = (40.7580, -73.9855)
SHIBUYA = (35.6595, 139.7005)
SAINT_DENIS_REUNION = (-20.8789, 55.4481)
AU_LARGE = (47.0, -7.0)                 # golfe de Gascogne, à 200 km des côtes
PRES_DES_COTES = (48.0, -5.6)           # mer d'Iroise, à 50-60 km du Finistère
OCEAN = (40.0, -30.0)                   # au milieu de l'Atlantique

_partage = None


def donnees():
    """Une seule instance pour tous les essais qui ne mesurent pas le chargement."""
    global _partage
    if _partage is None:
        _partage = Lieux(FICHIER)
    return _partage


def dms(valeur):
    """Degrés décimaux -> (degrés, minutes, secondes) EXIF."""
    valeur = abs(valeur)
    d = int(valeur)
    m = int((valeur - d) * 60)
    s = round(((valeur - d) * 60 - m) * 60, 2)
    return float(d), float(m), s


def gps_exif(lat, lon):
    return {1: 'N' if lat >= 0 else 'S', 2: dms(lat), 3: 'E' if lon >= 0 else 'W', 4: dms(lon)}


# ---------------------------------------------------------------- normalisation

class TestNormaliser(unittest.TestCase):

    def test_saint_et_sainte(self):
        for ecrit in ('Saint-Malo', 'St Malo', 'saint malo', 'ST-MALO', 'St.-Malo', '  Saint   Malo ', 'st_malo'):
            self.assertEqual(normaliser(ecrit), 'saint malo', ecrit)
        self.assertEqual(normaliser('Ste-Maxime'), 'sainte maxime')
        self.assertEqual(normaliser('Sainte Maxime'), 'sainte maxime')
        # « st » au milieu d'un mot n'est pas « saint ».
        for mot in ('Stuttgart', 'Est', 'Bastia', 'Steenvoorde', 'Brest'):
            self.assertEqual(normaliser(mot), mot.lower())

    def test_accents_apostrophes_tirets(self):
        self.assertEqual(normaliser('Finistère'), 'finistere')
        self.assertEqual(normaliser('Île-de-France'), 'ile de france')
        self.assertEqual(normaliser('Séville'), 'seville')
        self.assertEqual(normaliser("L'Haÿ-les-Roses"), 'l hay les roses')
        self.assertEqual(normaliser('Côtes-d’Armor'), 'cotes d armor')          # apostrophe typographique
        self.assertEqual(normaliser('Val–d‘Oise'), 'val d oise')               # tiret demi-cadratin
        self.assertEqual(normaliser('Œuilly'), 'oeuilly')
        self.assertEqual(normaliser('Straße'), 'strasse')
        self.assertEqual(normaliser('København'), 'kobenhavn')
        self.assertEqual(normaliser('Paris\t15\n'), 'paris 15')
        self.assertEqual(normaliser(''), '')
        self.assertEqual(normaliser('—'), '')

    def test_idempotente(self):
        for nom in ("L'Haÿ-les-Roses", 'St-Malo', 'Œuilly', 'Saint-Étienne', 'Ste  Anne'):
            self.assertEqual(normaliser(normaliser(nom)), normaliser(nom))


class TestCoordonnees(unittest.TestCase):

    def test_invalides(self):
        for lat, lon in ((None, 2.0), (48.0, None), (float('nan'), 2.0), (48.0, float('inf')), (91.0, 0.0),
                         (-90.5, 10.0), (10.0, 180.5), (0, 0), (0.0, 0.0), (0.005, -0.004), (-0.009, 0.0099),
                         ('abc', 2.0), (True, 2.0), ([], 1)):
            self.assertIsNone(lieux.coordonnees_valides(lat, lon), (lat, lon))
            self.assertEqual(donnees().proche(lat, lon), (None, None), (lat, lon))
            self.assertIsNone(donnees().localiser(lat, lon), (lat, lon))

    def test_valides(self):
        self.assertEqual(lieux.coordonnees_valides(48, -4), (48.0, -4.0))
        self.assertEqual(lieux.coordonnees_valides('48.39', '-4.48'), (48.39, -4.48))
        self.assertEqual(lieux.coordonnees_valides(0.0, 12.0), (0.0, 12.0))      # équateur : valide
        self.assertEqual(lieux.coordonnees_valides(0.02, 0.0), (0.02, 0.0))       # à plus de 0,01° de (0, 0)
        self.assertEqual(lieux.coordonnees_valides(90, -180), (90.0, -180.0))

    def test_distance(self):
        self.assertAlmostEqual(lieux.distance_km(*BREST, *QUIMPER), 52.3, delta=0.5)       # à vol d'oiseau
        self.assertAlmostEqual(lieux.distance_km(*NOTRE_DAME, *NOTRE_DAME), 0.0)
        self.assertAlmostEqual(lieux.distance_km(0, 0, 0, 180), math.pi * 6371.0088, delta=0.01)


# ---------------------------------------------------------------- données

class TestDonnees(unittest.TestCase):

    def test_fichier(self):
        taille = FICHIER.stat().st_size
        self.assertLess(taille, 2_000_000)                    # sous la limite du garde-fou de publication (2 Mo)
        entete, sections = lieux.lire_fichier(FICHIER)
        self.assertEqual(entete['format'], lieux.FORMAT)
        self.assertIn('CC BY 4.0', entete['licence'])
        self.assertIn('GeoNames', entete['licence'])
        noms = {s['fichier'] for s in entete['sources']}
        for f in ('cities500.zip', 'FR.zip', 'admin1CodesASCII.txt', 'admin2Codes.txt', 'countryInfo.txt',
                  'alternateNamesV2.zip', 'RE.zip'):
            self.assertIn(f, noms)
        for s in entete['sources']:
            self.assertTrue(s['adresse'].startswith('https://download.geonames.org/export/dump/'), s)
            self.assertRegex(s['date'], r'^\d{4}-\d{2}-\d{2}$')
            self.assertGreater(s['taille'], 0)
        c = entete['comptes']
        self.assertGreater(c['villes_france'], 34000)        # toutes les communes de France
        self.assertGreater(c['villes'], 100000)
        self.assertEqual(c['departements'], 96)
        self.assertTrue(donnees().version.startswith(max(s['date'] for s in entete['sources'])))
        # Le contenu est binaire dès le début : le garde-fou ne cherche pas de données personnelles dedans.
        self.assertIn(b'\x00', FICHIER.read_bytes()[:64])

    def test_chargement_et_memoire(self):
        """Chargement paresseux, rapide et sobre (mesuré : 0,2 s pour le fichier, 0,06 s pour l'index des noms, 37 Mo
        au plus ; marges pour les machines lentes)."""
        x = Lieux(FICHIER)
        self.assertIsNone(x._d)                               # rien n'est lu avant la première question
        tracemalloc.start()
        try:
            debut = time.perf_counter()
            self.assertTrue(x.version)
            charge = time.perf_counter() - debut
            debut = time.perf_counter()
            x.chercher('brest')
            index = time.perf_counter() - debut
            _, pic = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(pic, 60 * 1024 * 1024, f'{pic / 1e6:.0f} Mo')
        x2 = Lieux(FICHIER)
        debut = time.perf_counter()
        x2.proche(*BREST)
        x2.chercher('brest')
        tout = time.perf_counter() - debut
        self.assertLess(tout, 1.0, f'chargement {tout:.2f} s')   # 0,5 s demandé ; 0,2 s mesuré
        self.assertGreater(charge + index, 0)

    def test_fils(self):
        """Plusieurs fils à la fois au premier chargement : une seule lecture, les mêmes réponses."""
        x = Lieux(FICHIER)
        lectures = []
        vrai = lieux.lire_fichier

        def compte(chemin):
            lectures.append(1)
            time.sleep(0.05)
            return vrai(chemin)
        resultats = []
        with mock.patch.object(lieux, 'lire_fichier', compte):
            fils = [threading.Thread(target=lambda: resultats.append((x.proche(*BREST)[0].ident,
                                                                      x.chercher('quimper')[0].ident)))
                    for _ in range(8)]
            for f in fils:
                f.start()
            for f in fils:
                f.join(30)
        self.assertEqual(len(lectures), 1)
        self.assertEqual(len(set(resultats)), 1)
        self.assertEqual(len(resultats), 8)

    def test_instance_partagee(self):
        self.assertIs(lieux.lieux(), lieux.lieux())
        self.assertEqual(lieux.lieux().chemin, lieux.FICHIER)
        self.assertEqual(lieux.FICHIER.resolve(), FICHIER.resolve())

    def test_fichier_absent_ou_abime(self):
        tmp = Path(tempfile.mkdtemp(prefix='myphotos-lieux-'))
        try:
            brut = FICHIER.read_bytes()
            abime = bytearray(brut)
            abime[len(brut) // 2] ^= 0xFF
            cas = {'absent.bin': None, 'vide.bin': b'', 'autre.bin': b'PK\x03\x04' + b'x' * 100,
                   'tronque.bin': brut[:len(brut) // 3], 'abime.bin': bytes(abime),
                   'format.bin': lieux.MAGIQUE + bytes([99]) + brut[len(lieux.MAGIQUE) + 1:]}
            for nom, contenu in cas.items():
                if contenu is not None:
                    (tmp / nom).write_bytes(contenu)
                x = Lieux(tmp / nom)
                self.assertFalse(x.disponible, nom)
                self.assertEqual(x.version, '', nom)
                self.assertEqual(x.proche(*BREST), (None, None), nom)
                self.assertIsNone(x.localiser(*BREST), nom)
                self.assertEqual(x.chercher('brest'), [], nom)
                self.assertIsNone(x.endroit('p:FR'), nom)
                self.assertEqual(x.entete, {}, nom)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNoms(unittest.TestCase):

    def premier(self, nom):
        r = donnees().chercher(normaliser(nom))
        self.assertTrue(r, nom)
        return r[0]

    def test_noms_francais(self):
        x = self.premier('Londres')
        self.assertEqual((x.type, x.nom, x.pays), ('ville', 'Londres', 'GB'))
        self.assertEqual(self.premier('London').ident, x.ident)              # nom local
        for nom, pays in (('Espagne', 'ES'), ('Allemagne', 'DE'), ('Belgique', 'BE'), ('Royaume-Uni', 'GB'),
                          ('Italie', 'IT'), ('Suisse', 'CH'), ('États-Unis', 'US'), ('Japon', 'JP'), ('France', 'FR')):
            x = self.premier(nom)
            self.assertEqual((x.type, x.nom, x.pays, x.ident), ('pays', nom, pays, 'p:' + pays))
        x = self.premier('Séville')
        self.assertEqual((x.type, x.nom, x.pays), ('ville', 'Séville', 'ES'))
        self.assertEqual(self.premier('Sevilla').ident, x.ident)
        self.assertEqual(self.premier('seville').ident, x.ident)
        x = self.premier('Venise')
        self.assertEqual((x.type, x.nom, x.pays), ('ville', 'Venise', 'IT'))
        self.assertEqual(self.premier('Venezia').ident, x.ident)
        self.assertEqual(self.premier('München').nom, 'Munich')
        self.assertEqual(self.premier('Andalousie').ident, 'r:ES.51')
        self.assertEqual(self.premier('Angleterre').ident, 'r:GB.ENG')

    def test_france(self):
        x = self.premier('Bretagne')
        self.assertEqual((x.type, x.ident, x.nom, x.pays, x.region, x.departement),
                         ('region', 'r:FR.53', 'Bretagne', 'FR', None, None))
        x = self.premier('Finistère')
        self.assertEqual((x.type, x.ident, x.nom, x.region), ('departement', 'd:FR.29', 'Finistère', 'r:FR.53'))
        self.assertEqual(self.premier('finistere').ident, 'd:FR.29')
        for nom in ('Normandie', 'Occitanie', 'Corse', 'Nouvelle-Aquitaine', 'Grand Est'):
            self.assertEqual(self.premier(nom).type, 'region', nom)
        self.assertEqual(self.premier('Haute-Corse').ident, 'd:FR.2B')
        self.assertEqual(self.premier("Val-d'Oise").ident, 'd:FR.95')
        for ecrit in ('Saint-Malo', 'St Malo', 'saint malo', 'St-Malo', 'SAINT MALO'):
            x = self.premier(ecrit)
            self.assertEqual((x.type, x.nom, x.departement, x.region), ('ville', 'Saint-Malo', 'd:FR.35', 'r:FR.53'))
        # Sans l'article, quand il reste deux mots au moins.
        self.assertEqual(self.premier('Mont-Saint-Michel').nom, 'Le Mont-Saint-Michel')
        self.assertEqual(self.premier("Sables-d'Olonne").nom, "Les Sables-d'Olonne")
        # Commune nouvelle : son nom désigne ses anciennes communes.
        noms = {x.nom for x in donnees().chercher('cherbourg en cotentin')}
        self.assertTrue({'Cherbourg', 'Tourlaville'} <= noms, noms)
        self.assertEqual(self.premier('Cherbourg').nom, 'Cherbourg')
        # Anciennes régions : leurs départements.
        self.assertEqual({x.ident for x in donnees().chercher('alsace')}, {'d:FR.67', 'd:FR.68'})
        self.assertEqual(len(donnees().chercher('auvergne')), 4)
        # Outre-mer : une région de la France.
        x = self.premier('La Réunion')
        self.assertEqual((x.type, x.ident, x.pays), ('region', 'r:FR.RE', 'FR'))
        self.assertEqual(self.premier('Réunion').ident, 'r:FR.RE')
        self.assertEqual(self.premier('Guadeloupe').ident, 'r:FR.GP')

    def test_tri_et_exactitude(self):
        r = donnees().chercher('paris')
        rangs = [lieux.TYPES.index(x.type) for x in r]
        self.assertEqual(rangs, sorted(rangs))               # pays, régions, départements, puis villes
        villes = [x for x in r if x.type == 'ville']
        self.assertEqual(villes[0].ident, 'v:2988507')
        self.assertEqual([x.population for x in villes], sorted((x.population for x in villes), reverse=True))
        self.assertIn('US', {x.pays for x in villes})        # Paris (Texas) aussi : un homonyme
        x = donnees().chercher('bretagne')
        self.assertEqual(x[0].type, 'region')
        self.assertTrue(all(y.type == 'ville' for y in x[1:]))   # les communes nommées Bretagne viennent après
        for rien in ('', 'bret', 'xyzzy plouf', 'Bretagne'):     # exact, et déjà normalisé
            self.assertEqual(donnees().chercher(rien), [], rien)
        self.assertEqual(donnees().chercher(None), [])

    def test_parents_et_libelles(self):
        brest = donnees().proche(*BREST)[0]
        self.assertEqual([x.ident for x in donnees().parents(brest)], ['d:FR.29', 'r:FR.53', 'p:FR'])
        self.assertEqual(donnees().libelle(brest), 'Brest, Finistère, Bretagne, France')
        seville = donnees().proche(*SEVILLE)[0]
        self.assertEqual([x.ident for x in donnees().parents(seville)], ['r:ES.51', 'p:ES'])
        self.assertEqual(donnees().libelle(seville), 'Séville, Andalousie, Espagne')
        self.assertEqual(donnees().libelle(donnees().proche(*NOTRE_DAME)[0]), 'Paris, Île-de-France, France')
        bretagne = donnees().endroit('r:FR.53')
        self.assertEqual(donnees().libelle(bretagne), 'Bretagne, France')
        self.assertEqual(donnees().parents(bretagne), [donnees().endroit('p:FR')])
        self.assertEqual(donnees().libelle(donnees().endroit('d:FR.29')), 'Finistère, Bretagne, France')
        self.assertEqual(donnees().libelle(donnees().endroit('p:ES')), 'Espagne')
        self.assertEqual(donnees().parents(donnees().endroit('p:ES')), [])
        reunion = donnees().proche(*SAINT_DENIS_REUNION)[0]
        self.assertEqual(donnees().libelle(reunion), 'Saint-Denis, La Réunion, France')
        self.assertEqual(donnees().libelle(None), '')
        self.assertEqual(donnees().parents(None), [])

    def test_idents(self):
        for nom in ('brest', 'finistere', 'bretagne', 'france', 'seville', 'andalousie', 'tokyo', 'la reunion'):
            for x in donnees().chercher(nom)[:3]:
                self.assertIsInstance(x, Endroit)
                self.assertEqual(donnees().endroit(x.ident), x, x)
        # 'd:FR.53' (la Mayenne) n'est pas 'r:FR.53' (la Bretagne).
        self.assertEqual(donnees().endroit('d:FR.53').nom, 'Mayenne')
        self.assertEqual(donnees().endroit('r:FR.53').nom, 'Bretagne')
        for faux in (None, '', 'x', 'v:', 'v:abc', 'v:999999999999999', 'p:ZZ', 'r:FR.99', 'FR', 'q:FR', 12):
            self.assertIsNone(donnees().endroit(faux), faux)


class TestProche(unittest.TestCase):

    def ville(self, position, nom, pays='FR', max_km=3.0):
        v, d = donnees().proche(*position)
        self.assertIsNotNone(v, position)
        self.assertEqual((v.type, v.nom, v.pays), ('ville', nom, pays), position)
        self.assertLessEqual(d, max_km, position)
        return v, d

    def test_villes_connues(self):
        v, _ = self.ville(BREST, 'Brest')
        self.assertEqual((v.departement, v.region), ('d:FR.29', 'r:FR.53'))
        self.ville(QUIMPER, 'Quimper')
        v, _ = self.ville(NOTRE_DAME, 'Paris')
        self.assertEqual(v.ident, 'v:2988507')
        # À la tour Eiffel, plus près de la mairie d'Issy-les-Moulineaux que du centre de Paris : Paris quand même
        # (une grande ville l'emporte sur sa voisine).
        self.ville(TOUR_EIFFEL, 'Paris', max_km=5.0)
        v, d = self.ville(LOCRONAN, 'Locronan', max_km=0.5)
        self.assertLess(v.population, 2000)
        v, _ = self.ville(SEVILLE, 'Séville', 'ES')
        self.assertEqual(v.region, 'r:ES.51')
        self.ville(TIMES_SQUARE, 'New York', 'US', max_km=10.0)
        self.ville(SHIBUYA, 'Tokyo', 'JP', max_km=10.0)
        self.ville(SAINT_DENIS_REUNION, 'Saint-Denis')

    def test_en_mer(self):
        self.assertEqual(donnees().proche(*OCEAN), (None, None))
        self.assertEqual(donnees().localiser(*OCEAN),
                         {'ville': None, 'departement': None, 'region': None, 'pays': None, 'distance': None})
        v, d = donnees().proche(*AU_LARGE)
        self.assertIsNone(v)
        self.assertTrue(d is None or d > lieux.RAYON_KM)
        # À 50-60 km des côtes : ni ville ni région, mais le pays de la ville la plus proche.
        v, d = donnees().proche(*PRES_DES_COTES)
        self.assertIsNone(v)
        self.assertTrue(lieux.RAYON_KM < d < lieux.RAYON_PAYS_KM, d)
        self.assertEqual(donnees().localiser(*PRES_DES_COTES),
                         {'ville': None, 'departement': None, 'region': None, 'pays': 'p:FR', 'distance': d})

    def test_localiser(self):
        r = donnees().localiser(*BREST)
        v, d = donnees().proche(*BREST)
        self.assertEqual(r, {'ville': v.ident, 'departement': 'd:FR.29', 'region': 'r:FR.53', 'pays': 'p:FR',
                             'distance': d})
        r = donnees().localiser(*SEVILLE)
        self.assertEqual((r['departement'], r['region'], r['pays']), (None, 'r:ES.51', 'p:ES'))

    def test_bords_du_monde(self):
        """Antiméridien, pôles : jamais d'erreur ; de part et d'autre de 180°, le même pays (Fidji)."""
        a = donnees().localiser(-16.8, 179.99)
        b = donnees().localiser(-16.8, -179.99)
        self.assertEqual(a['pays'], b['pays'])
        self.assertEqual(a['pays'], 'p:FJ')
        for lat, lon in ((89.99, 0), (-89.99, 120), (90, 180), (-90, -180), (0, 180), (0, -180), (78.22, 15.65)):
            donnees().proche(lat, lon)
            donnees().localiser(lat, lon)

    def test_rayon(self):
        """Une ville à plus de 30 km n'est jamais retenue."""
        rnd = random.Random(3)
        for _ in range(2000):
            lat, lon = rnd.uniform(-60, 70), rnd.uniform(-180, 180)
            v, d = donnees().proche(lat, lon)
            if v is not None:
                self.assertLessEqual(d, lieux.RAYON_KM + 0.1)
                self.assertAlmostEqual(d, lieux.distance_km(lat, lon, v.lat, v.lon), delta=0.01)

    def test_vitesse(self):
        """10 000 photos en moins d'une seconde (0,5 s mesuré ; marge pour les machines lentes)."""
        donnees().proche(*BREST)
        rnd = random.Random(1)
        points = [(rnd.uniform(42.5, 51), rnd.uniform(-4.5, 7.5)) for _ in range(10000)]
        debut = time.perf_counter()
        trouvees = sum(1 for p in points if donnees().proche(*p)[0] is not None)
        duree = time.perf_counter() - debut
        self.assertGreater(trouvees, 7000)                   # le reste : la mer, l'étranger proche
        self.assertLess(duree, 2.0, f'{duree:.2f} s pour 10 000 photos')


# ---------------------------------------------------------------- base

def ajouter_photos_gps(base, dossier, positions, modele_cle='base'):
    """Photos factices analysées avec ces positions ({nom: (lat, lon) ou None}) : {nom: id}."""
    d = base.ajouter_dossier(dossier)
    lignes = [(str(Path(dossier) / f'{nom}.jpg'), 1000 + i, 1, '2024-07-01T10:00:00')
              for i, nom in enumerate(positions)]
    base.ajouter_photos(d['id'], lignes)
    ids = {Path(ch).stem: pid for ch, (pid, _, _) in base.connus(d['id']).items()}
    v = np.zeros(8, np.float16)
    v[0] = 1
    analyses = []
    for i, (nom, pos) in enumerate(positions.items()):
        lat, lon = pos if pos else (None, None)
        analyses.append({'id': ids[nom], 'taille': 1000 + i, 'modifie': 1, 'largeur': 400, 'hauteur': 300,
                         'date_prise': f'2024-07-{1 + i % 28:02d}T10:00:00', 'lat': lat, 'lon': lon,
                         'vecteur': v.tobytes(), 'miniature': b'jpeg'})
    base.enregistrer(analyses, [], modele_cle)
    return ids, d


def localiser_tout(base, source=None):
    source = source or donnees()
    lot = base.a_localiser(100000, source.version)
    lignes = []
    for pid, lat, lon in lot:
        r = source.localiser(lat, lon) or {}
        lignes.append({'photo_id': pid, **r, 'version': source.version})
    return base.enregistrer_lieux(lignes)


class EssaiBase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-lieux-'))
        self.dossier = self.tmp / 'photos'
        self.dossier.mkdir()
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')

    def tearDown(self):
        self.base.fermer()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBaseLieux(EssaiBase):

    POSITIONS = {'brest': BREST, 'quimper': QUIMPER, 'seville': SEVILLE, 'zero': (0.0, 0.0), 'presque_zero':
                 (0.004, -0.006), 'hors_bornes': (95.0, 10.0), 'sans_gps': None, 'en_mer': OCEAN,
                 'locronan': LOCRONAN}

    def test_schema(self):
        c = self.base._cnx()
        self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 1)
        colonnes = [l[1] for l in c.execute('PRAGMA table_info(lieux_photos)')]
        self.assertEqual(colonnes, ['photo_id', 'ville', 'departement', 'region', 'pays', 'distance', 'version'])
        noms = {n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertIn('photos_gps', noms)
        # Rouverte : rien à créer (aucune écriture à l'ouverture d'une base à jour).
        self.base.fermer()
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.assertFalse(self.base._schema_lieux_manquant(self.base._cnx()))

    def test_localiser_et_lire(self):
        ids, _ = ajouter_photos_gps(self.base, self.dossier, self.POSITIONS)
        a_faire = {pid for pid, _, _ in self.base.a_localiser(100, donnees().version)}
        # Positions valides seulement : ni (0, 0), ni (0, 0) à 0,01° près, ni hors des bornes, ni sans GPS.
        self.assertEqual(a_faire, {ids[n] for n in ('brest', 'quimper', 'seville', 'en_mer', 'locronan')})
        self.assertEqual(len(self.base.a_localiser(2, donnees().version)), 2)
        self.assertEqual(self.base.a_localiser(100, ''), [])
        self.assertEqual(self.base.sans_position(), 4)
        self.assertEqual(localiser_tout(self.base), 5)
        self.assertEqual(self.base.a_localiser(100, donnees().version), [])
        brest = donnees().proche(*BREST)[0]
        l = self.base.lieu_photo(ids['brest'])
        self.assertEqual((l['ville'], l['departement'], l['region'], l['pays'], l['version']),
                         (brest.ident, 'd:FR.29', 'r:FR.53', 'p:FR', donnees().version))
        self.assertLess(l['distance'], 1)
        self.assertEqual(self.base.lieu_photo(ids['en_mer']),
                         {'ville': None, 'departement': None, 'region': None, 'pays': None, 'distance': None,
                          'version': donnees().version})
        self.assertIsNone(self.base.lieu_photo(ids['sans_gps']))
        self.assertIsNone(self.base.lieu_photo(ids['zero']))
        # Par lieu : ville, département, région, pays (Endroit ou ident).
        self.assertEqual(self.base.ids_par_lieu(brest), [ids['brest']])
        self.assertEqual(self.base.ids_par_lieu(brest.ident), [ids['brest']])
        finistere = {ids['brest'], ids['quimper'], ids['locronan']}
        self.assertEqual(set(self.base.ids_par_lieu(donnees().endroit('d:FR.29'))), finistere)
        self.assertEqual(set(self.base.ids_par_lieu('r:FR.53')), finistere)
        self.assertEqual(set(self.base.ids_par_lieu(donnees().endroit('p:FR'))), finistere)
        self.assertEqual(self.base.ids_par_lieu('p:ES'), [ids['seville']])
        self.assertEqual(self.base.ids_par_lieu('p:IT'), [])
        # Les plus récentes d'abord (date de prise de vue), comme photos_par_date.
        self.assertEqual(self.base.ids_par_lieu('d:FR.29'),
                         [ids['locronan'], ids['quimper'], ids['brest']])
        presents = self.base.lieux_presents()
        self.assertEqual(presents['p:FR'], 3)
        self.assertEqual(presents['r:FR.53'], 3)
        self.assertEqual(presents['d:FR.29'], 3)
        self.assertEqual(presents[brest.ident], 1)
        self.assertEqual(presents['p:ES'], 1)
        self.assertEqual(presents['r:ES.51'], 1)
        self.assertEqual(sum(1 for k in presents if k.startswith('v:')), 4)
        self.assertNotIn(None, presents)
        with self.assertRaises(ValueError):
            self.base.ids_par_lieu('Brest')

    def test_nouvelle_version(self):
        ids, _ = ajouter_photos_gps(self.base, self.dossier, self.POSITIONS)
        localiser_tout(self.base)
        self.assertEqual(len(self.base.a_localiser(100, 'autre-version')), 5)
        self.assertEqual(len(self.base.a_localiser(100, donnees().version)), 0)

    def test_oubli_dans_la_meme_transaction(self):
        ids, d = ajouter_photos_gps(self.base, self.dossier, self.POSITIONS)
        localiser_tout(self.base)
        c = self.base._cnx()

        def lignes():
            return {pid for (pid,) in c.execute('SELECT photo_id FROM lieux_photos')}
        self.base.oublier_photos([ids['brest']])
        self.assertNotIn(ids['brest'], lignes())
        self.assertEqual(set(self.base.ids_par_lieu('d:FR.29')), {ids['quimper'], ids['locronan']})
        # Si la suppression des photos échoue, les lieux restent aussi (même transaction).
        with mock.patch.object(Base, '_nouvelle_generation', side_effect=sqlite3.OperationalError('essai')):
            with self.assertRaises(sqlite3.OperationalError):
                self.base.oublier_photos([ids['quimper']])
        self.assertIn(ids['quimper'], lignes())
        self.assertIsNotNone(self.base.photo(ids['quimper']))
        # Dossier retiré : tous ses lieux partent.
        self.base.retirer_dossier(d['id'])
        self.assertEqual(lignes(), set())
        # Une photo oubliée entre le calcul et l'écriture n'a pas de ligne orpheline.
        self.assertEqual(self.base.enregistrer_lieux([(ids['seville'], None, None, None, 'p:ES', 1.0, 'x')]), 0)
        self.assertEqual(lignes(), set())

    def test_position_changee(self):
        """Fichier modifié puis analysé de nouveau : position changée, lieu refait ; même position, lieu gardé ;
        photo devenue illisible, lieu effacé."""
        ids, _ = ajouter_photos_gps(self.base, self.dossier, self.POSITIONS)
        localiser_tout(self.base)
        v = np.zeros(8, np.float16).tobytes()

        def analyse(nom, taille, lat, lon):
            return {'id': ids[nom], 'taille': taille, 'modifie': 2, 'largeur': 400, 'hauteur': 300,
                    'date_prise': None, 'lat': lat, 'lon': lon, 'vecteur': v, 'miniature': b'jpeg'}
        self.base.marquer_modifiees([(ids['brest'], 5000, 2, None), (ids['quimper'], 5001, 2, None),
                                     (ids['seville'], 5002, 2, None)])
        self.assertIsNotNone(self.base.lieu_photo(ids['brest']))       # gardé jusqu'à la nouvelle analyse
        self.base.enregistrer([analyse('brest', 5000, *SEVILLE), analyse('quimper', 5001, *QUIMPER)],
                              [{'id': ids['seville'], 'taille': 5002, 'modifie': 2, 'erreur': 'illisible'}], 'base')
        self.assertIsNone(self.base.lieu_photo(ids['brest']))
        self.assertIsNotNone(self.base.lieu_photo(ids['quimper']))
        self.assertIsNone(self.base.lieu_photo(ids['seville']))
        a_faire = {pid for pid, _, _ in self.base.a_localiser(100, donnees().version)}
        self.assertEqual(a_faire, {ids['brest']})                       # la photo en erreur n'est pas localisée
        localiser_tout(self.base)
        self.assertEqual(self.base.lieu_photo(ids['brest'])['pays'], 'p:ES')
        self.assertEqual(self.base.sans_position(), 4)                 # la photo illisible n'est plus comptée

    def test_deplacement(self):
        ids, d = ajouter_photos_gps(self.base, self.dossier, self.POSITIONS)
        localiser_tout(self.base)
        nouveau = str(self.dossier / 'rangees' / 'brest.jpg')
        self.base.ajouter_photos(d['id'], [(nouveau, 1000, 1, None)])
        self.base.deplacer_photos([(ids['brest'], nouveau)])
        self.assertEqual(self.base.photo(ids['brest'])['chemin'], nouveau)
        self.assertEqual(self.base.lieu_photo(ids['brest'])['departement'], 'd:FR.29')
        self.assertEqual(self.base._cnx().execute(
            'SELECT COUNT(*) FROM lieux_photos WHERE photo_id NOT IN (SELECT id FROM photos)').fetchone()[0], 0)

    def test_index_des_positions(self):
        """Les photos à localiser et le compte des photos sans position se lisent dans l'index photos_gps, sans
        parcourir la table des photos (et ses vecteurs)."""
        c = self.base._cnx()
        plan = ' '.join(str(l) for l in c.execute(
            'EXPLAIN QUERY PLAN SELECT p.id, p.lat, p.lon FROM photos p LEFT JOIN lieux_photos l ON l.photo_id = '
            'p.id WHERE p.lat IS NOT NULL AND p.erreur IS NULL AND p.lat BETWEEN -90 AND 90 AND p.lon BETWEEN -180 '
            'AND 180 AND NOT (ABS(p.lat) < 0.01 AND ABS(p.lon) < 0.01) AND (l.photo_id IS NULL OR l.version IS NOT '
            "'x') ORDER BY p.id LIMIT 10"))
        self.assertIn('COVERING INDEX photos_gps', plan)
        plan = ' '.join(str(l) for l in c.execute(
            'EXPLAIN QUERY PLAN SELECT COUNT(*) FROM photos WHERE lat IS NOT NULL AND erreur IS NULL AND miniature = 1 '
            'AND lat BETWEEN -90 AND 90'))
        self.assertIn('COVERING INDEX photos_gps', plan)


class TestCompatibilite(EssaiBase):
    """Base 0.5.0 relue par le base.py de la 0.4.1 (git show f999167:myphotos/base.py), et inversement."""

    def base_0_4_1(self):
        try:
            r = subprocess.run(['git', 'show', f'{VERSION_0_4_1}:myphotos/base.py'], cwd=RACINE, capture_output=True,
                               timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            self.skipTest(f'git indisponible ({e})')
        if r.returncode != 0 or not r.stdout:
            self.skipTest('base.py de la 0.4.1 introuvable (dépôt sans son historique)')
        dossier = self.tmp / 'v041'
        dossier.mkdir()
        (dossier / 'base_041.py').write_bytes(r.stdout)
        spec = importlib.util.spec_from_file_location('base_041', dossier / 'base_041.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertFalse(hasattr(module.Base, 'a_localiser'))
        return module

    def test_dans_les_deux_sens(self):
        ancien = self.base_0_4_1()
        index, mini = self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db'
        self.base.fermer()
        os.remove(index)
        os.remove(mini)
        # 1. Base créée par la 0.4.1, avec des photos analysées qui ont une position.
        b041 = ancien.Base(index, mini)
        ids, d = ajouter_photos_gps(b041, self.dossier, {'brest': BREST, 'seville': SEVILLE, 'sans': None})
        compte = b041.compter()
        b041.fermer()
        c = sqlite3.connect(index)
        self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name = 'lieux_photos'").fetchone())
        c.close()
        # 2. Rouverte par la 0.5.0 : table et index créés, user_version inchangé, rien perdu ; lieux calculés.
        self.base = Base(index, mini)
        self.assertEqual(self.base._cnx().execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(self.base.compter(), compte)
        self.assertEqual(localiser_tout(self.base), 2)
        self.assertEqual(self.base.ids_par_lieu('r:FR.53'), [ids['brest']])
        self.base.fermer()
        # 3. Rouverte par la 0.4.1 : elle lit, oublie et ajoute des photos sans rien savoir des lieux.
        b041 = ancien.Base(index, mini)
        self.assertEqual(b041.compter(), compte)
        self.assertEqual(b041.photo(ids['brest'])['lat'], BREST[0])
        b041.oublier_photos([ids['brest']])                  # sa ligne de lieu reste, orpheline
        (self.tmp / 'autres').mkdir()
        nouvelles, _ = ajouter_photos_gps(b041, self.tmp / 'autres', {'quimper': QUIMPER})
        self.assertEqual(b041.compter()['total'], compte['total'])
        b041.fermer()
        # 4. De nouveau la 0.5.0 : la ligne orpheline est ignorée, puis effacée par reparer() ; la nouvelle photo
        # est à localiser.
        self.base = Base(index, mini)
        self.assertEqual(self.base.ids_par_lieu('r:FR.53'), [])
        self.assertNotIn('r:FR.53', self.base.lieux_presents())
        c = self.base._cnx()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM lieux_photos').fetchone()[0], 2)
        self.base.reparer()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM lieux_photos').fetchone()[0], 1)
        self.assertEqual([p for p, _, _ in self.base.a_localiser(10, donnees().version)], [nouvelles['quimper']])
        localiser_tout(self.base)
        self.assertEqual(self.base.ids_par_lieu('d:FR.29'), [nouvelles['quimper']])
        self.assertEqual(self.base._cnx().execute('PRAGMA user_version').fetchone()[0], 1)


# ---------------------------------------------------------------- calcul en arrière-plan

class MoteurSimple:
    """Moteur instantané : un vecteur par couleur moyenne (comme le moteur factice de test_coeur)."""
    materiel = 'processeur'
    taille_image = 32

    def vecteurs_images(self, lot):
        v = np.zeros((len(lot), 8), np.float32)
        v[:, :3] = (lot.mean(axis=(2, 3)) + 1) / 2
        v[:, 3] = 0.05
        return modele.normaliser(v)


class TestCalculEnArrierePlan(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-lieux-'))
        self.photos = self.tmp / 'photos'
        positions = {'brest': BREST, 'seville': SEVILLE, 'zero': (0.0, 0.0), 'sans_gps': None, 'en_mer': OCEAN}
        for i, (nom, pos) in enumerate(positions.items()):
            ie.jpeg(self.photos / f'{nom}.jpg', (200, 150), ie.BLEU, gps=gps_exif(*pos) if pos else None,
                    quand=time.time() - 3600 + i)
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.base.ajouter_dossier(self.photos)
        self.indexeurs = []

    def tearDown(self):
        for ix in self.indexeurs:
            ix.arreter()
        self.base.fermer()
        ie.nettoyer(self.tmp)

    def indexer(self, **options):
        ix = Indexeur(self.base, lambda materiel='auto': MoteurSimple(), 'base', fils_decodage=2, **options)
        self.indexeurs.append(ix)
        ix.demarrer()
        self.assertTrue(ix.attendre(60), 'indexation trop longue')
        return ix

    def lieux_par_nom(self):
        return {Path(self.base.photo(pid)['chemin']).stem: self.base.lieu_photo(pid)
                for (pid,) in self.base._cnx().execute('SELECT id FROM photos')}

    def test_apres_l_analyse(self):
        ix = self.indexer()
        self.assertEqual(ix.etat()['phase'], 'termine')
        l = self.lieux_par_nom()
        self.assertEqual((l['brest']['departement'], l['brest']['region'], l['brest']['pays']),
                         ('d:FR.29', 'r:FR.53', 'p:FR'))
        self.assertEqual(donnees().endroit(l['brest']['ville']).nom, 'Brest')
        self.assertEqual(donnees().endroit(l['seville']['ville']).nom, 'Séville')
        self.assertIsNone(l['zero'])                         # (0, 0) : pas de position
        self.assertIsNone(l['sans_gps'])
        self.assertEqual(l['en_mer']['pays'], None)          # en plein océan : localisée, sans lieu
        self.assertEqual(self.base.sans_position(), 2)
        self.assertEqual(self.base.a_localiser(10), [])     # avec la version de l'instance partagée

    def test_nouvelle_photo_et_nouvelle_version(self):
        ix = self.indexer()
        ie.jpeg(self.photos / 'quimper.jpg', (200, 150), ie.VERT, gps=gps_exif(*QUIMPER))
        ix.relancer()
        self.assertTrue(ix.attendre(60))
        self.assertEqual(self.lieux_par_nom()['quimper']['departement'], 'd:FR.29')
        # Données de lieux d'une autre version : tout est refait au passage suivant.
        with self.base._transaction() as c:
            c.execute("UPDATE lieux_photos SET version = 'ancienne', ville = NULL")
        ix.relancer()
        self.assertTrue(ix.attendre(60))
        l = self.lieux_par_nom()
        self.assertEqual({x['version'] for x in l.values() if x}, {donnees().version})
        self.assertEqual(donnees().endroit(l['brest']['ville']).nom, 'Brest')

    def test_source_injectee_et_pannes(self):
        """Sans données de lieux, ou si leur calcul échoue, l'analyse se termine quand même, sans lieu."""
        ix = self.indexer(source_lieux=Lieux(self.tmp / 'absent.bin'))
        self.assertEqual(ix.etat()['phase'], 'termine')
        self.assertEqual(self.base._cnx().execute('SELECT COUNT(*) FROM lieux_photos').fetchone()[0], 0)
        panne = mock.Mock(version='v-panne')
        panne.localiser.side_effect = RuntimeError('panne (essai)')
        ix.source_lieux = panne
        ix.relancer()
        self.assertTrue(ix.attendre(60))
        self.assertEqual(ix.etat()['phase'], 'termine')
        self.assertEqual(self.base._cnx().execute('SELECT COUNT(*) FROM lieux_photos').fetchone()[0], 0)
        ix.source_lieux = donnees()
        ix.relancer()
        self.assertTrue(ix.attendre(60))
        self.assertEqual(self.base._cnx().execute('SELECT COUNT(*) FROM lieux_photos').fetchone()[0], 3)

    def test_par_lots_et_arret(self):
        """Par lots de LIEUX_LOT ; l'arrêt (ou une pause) interrompt entre deux lots, la suite au passage suivant."""
        ix = Indexeur(self.base, lambda materiel='auto': MoteurSimple(), 'base', fils_decodage=2)
        self.indexeurs.append(ix)
        d = self.base.dossiers()[0]
        lignes = [(str(self.photos / f'g{i:04d}.jpg'), 10 + i, 1, None) for i in range(250)]
        self.base.ajouter_photos(d['id'], lignes)
        connus = self.base.connus(d['id'])
        rnd = random.Random(5)
        v = np.zeros(8, np.float16).tobytes()
        self.base.enregistrer([{'id': connus[ch][0], 'taille': t, 'modifie': 1, 'largeur': 400, 'hauteur': 300,
                                'date_prise': None, 'lat': rnd.uniform(43, 50), 'lon': rnd.uniform(-1, 6),
                                'vecteur': v, 'miniature': b'jpeg'} for ch, t, _, _ in lignes], [], 'base')
        appels = []
        vraie = donnees()

        class Espion:
            version = vraie.version

            def localiser(self, lat, lon):
                appels.append(1)
                if len(appels) == 100:
                    ix._arret.set()                          # arrêt demandé pendant le premier lot
                return vraie.localiser(lat, lon)
        ix.source_lieux = Espion()
        with mock.patch.object(indexation, 'LIEUX_LOT', 100):
            self.assertEqual(ix._localiser(), 100)           # le lot commencé est écrit, puis on s'arrête
            self.assertEqual(len(self.base.a_localiser(1000, vraie.version)), 150)
            ix._arret.clear()
            ix.pause()
            self.assertEqual(ix._localiser(), 0)             # en pause : rien
            ix.reprendre()
            self.assertEqual(ix._localiser(), 150)           # deux lots de 100, puis 50
        self.assertEqual(self.base.a_localiser(1000, vraie.version), [])

    def test_pendant_une_longue_analyse(self):
        """Pendant une longue analyse, les photos déjà analysées ont leur lieu sans attendre la fin."""
        for i in range(40):
            ie.jpeg(self.photos / 'plus' / f'p{i:02d}.jpg', (200, 150), ie.VERT, gps=gps_exif(*QUIMPER))
        vus = []
        vrai = indexation.Indexeur._localiser

        def espion(ix):
            n = vrai(ix)
            vus.append((n, ix.base.compter()['a_faire']))
            return n
        with mock.patch.object(indexation, 'LIEUX_PENDANT_S', 0.0), mock.patch.object(indexation, 'ECRITURE_N', 8), \
                mock.patch.object(indexation.Indexeur, '_localiser', espion):
            self.indexer()
        self.assertTrue(any(n > 0 and reste > 0 for n, reste in vus), vus)   # des lieux avant la fin de l'analyse
        self.assertEqual(self.base.a_localiser(100), [])


if __name__ == '__main__':
    unittest.main()
