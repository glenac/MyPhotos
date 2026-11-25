"""Essais du cœur de MyPhotos (base, indexation, recherche) avec un moteur factice rapide et déterministe.

Images générées par Pillow dans un dossier temporaire (jamais de vraies photos).
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import io
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                                     # noqa: E402
from PIL import Image, ImageDraw                       # noqa: E402

import images_essai as ie                              # noqa: E402
from myphotos import base as base_mod                  # noqa: E402
from myphotos import indexation, modele, recherche  # noqa: E402
from myphotos.base import (Base, DossierRefuse, casse_reelle, chemin_affiche, chemin_systeme,  # noqa: E402
                              cle_chemin, dedans, normaliser_chemin)
from myphotos.indexation import Indexeur, Parcours  # noqa: E402
from myphotos.recherche import MoteurIndisponible, Recherche, normaliser_requete  # noqa: E402

PRIORITE_INFERIEURE = 0x4000
logging.getLogger('myphotos').setLevel(logging.CRITICAL)   # pannes simulées : avertissements attendus


def priorite_processus():
    return indexation._Priorite().actuelle()


class MoteurFactice:
    """Moteur instantané : le vecteur d'une image vient de sa couleur moyenne ; « rouge », « vert », « bleu »
    donnent le vecteur de la couleur, tout autre mot un vecteur sans rapport avec les images."""
    DIM = 8
    COULEURS = {'rouge': (1, 0, 0), 'vert': (0, 1, 0), 'bleu': (0, 0, 1)}

    def __init__(self, materiel='processeur', panne=False, lenteur=0.0):
        self.materiel = materiel
        self.taille_image = 32
        self.panne = panne
        self.lenteur = lenteur
        self.images = 0
        self.lots = []
        self.priorites = []
        self.textes = []

    def vecteurs_images(self, lot):
        assert lot.dtype == np.float32 and lot.shape[1:] == (3, 32, 32), lot.shape
        if self.panne:
            raise RuntimeError('pilote graphique en panne (essai)')
        if self.lenteur:
            time.sleep(self.lenteur)
        self.images += len(lot)
        self.lots.append(len(lot))
        self.priorites.append(priorite_processus())
        rvb = (lot.mean(axis=(2, 3)) + 1) / 2
        v = np.zeros((len(lot), self.DIM), np.float32)
        v[:, :3] = rvb ** 2
        v[:, 3] = 0.05
        return modele.normaliser(v)

    def vecteurs_textes(self, textes):
        self.textes.extend(textes)
        v = np.zeros((len(textes), self.DIM), np.float32)
        for i, t in enumerate(textes):
            for mot in t.lower().split():
                if mot in self.COULEURS:
                    v[i, :3] += self.COULEURS[mot]
            if not v[i].any():
                v[i, 4 + zlib.crc32(t.encode()) % 4] = 1
        return modele.normaliser(v)


class Fabrique:
    """fabrique_moteur factice : note les appels ; peut simuler une carte graphique en panne ou un modèle absent."""

    def __init__(self, panne_carte=False, lenteur=0.0, absent=False):
        self.panne_carte, self.lenteur, self.absent = panne_carte, lenteur, absent
        self.appels = []
        self.moteurs = []

    def __call__(self, materiel='auto'):
        self.appels.append(materiel)
        if self.absent:
            raise FileNotFoundError('modèle absent (essai)')
        if materiel == 'auto' and self.panne_carte:
            m = MoteurFactice('carte graphique', panne=True)
        else:
            m = MoteurFactice('processeur', lenteur=self.lenteur)
        self.moteurs.append(m)
        return m

    @property
    def images(self):
        return sum(m.images for m in self.moteurs)


class MoteurRetenu(MoteurFactice):
    """Moteur dont chaque lot attend le feu vert : un lot très long, que l'arrêt de l'indexeur n'interrompt pas."""

    def __init__(self, feu):
        super().__init__()
        self.feu = feu
        self.dans_un_lot = threading.Event()

    def vecteurs_images(self, lot):
        self.dans_un_lot.set()
        self.feu.wait(30)
        return super().vecteurs_images(lot)


class Modifie:
    """Change temporairement une constante de module."""

    def __init__(self, module, nom, valeur):
        self.module, self.nom, self.valeur = module, nom, valeur

    def __enter__(self):
        self.avant = getattr(self.module, self.nom)
        setattr(self.module, self.nom, self.valeur)

    def __exit__(self, *_):
        setattr(self.module, self.nom, self.avant)


def attendre_que(condition, delai=30.0):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- fonctions simples

class TestChemins(unittest.TestCase):

    def test_normaliser(self):
        # Textuel seulement : aucun disque consulté (jamais le vrai E: de cet ordinateur).
        with mock.patch('myphotos.base.os.path.isdir', return_value=False):
            self.assertEqual(normaliser_chemin('e:/Photos/Vacances/'), 'E:\\Photos\\Vacances')
            self.assertEqual(normaliser_chemin('  "E:\\Photos"  '), 'E:\\Photos')
            self.assertEqual(normaliser_chemin('e:'), 'E:\\')
            self.assertEqual(normaliser_chemin('E:\\Photos\\..\\Albums'), 'E:\\Albums')
            self.assertEqual(normaliser_chemin('\\\\?\\E:\\Photos'), 'E:\\Photos')
            self.assertEqual(normaliser_chemin('E:\\Photos.'), 'E:\\Photos')      # saisi, inexistant : nettoyé
            with self.assertRaises(DossierRefuse):
                normaliser_chemin('   ')

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_point_ou_espace_final(self):
        """« Vacances. » (point final) et « Espace  » (espace final) choisis dans la fenêtre : gardés tels quels, et
        jamais confondus avec « Vacances » ; une base qui suit déjà « Vacances » ne perd ni ne double rien."""
        tmp = Path(tempfile.mkdtemp(prefix='myphotos-points-'))
        try:
            for nom in ('Vacances', 'Vacances.', 'Seul.', 'Espace ', 'Album.'):
                os.mkdir(chemin_systeme(str(tmp / nom)))
            os.mkdir(chemin_systeme(str(tmp / 'Album.') + '\\Sous'))
            for nom in ('Vacances.', 'Seul.', 'Espace '):
                self.assertEqual(normaliser_chemin(str(tmp) + '\\' + nom), str(tmp) + '\\' + nom)
            self.assertEqual(normaliser_chemin(str(tmp) + '\\Album.\\Sous\\'), str(tmp) + '\\Album.\\Sous')
            self.assertEqual(normaliser_chemin(str(tmp).replace('\\', '/') + '/Vacances./'), str(tmp) + '\\Vacances.')
            b = Base(tmp / 'donnees' / 'index.db', tmp / 'donnees' / 'miniatures.db')
            try:
                d1 = b.ajouter_dossier(str(tmp / 'Vacances'))
                with self.assertRaises(DossierRefuse):
                    b.ajouter_dossier(str(tmp / 'Vacances') + '\\')            # déjà suivi : pas de doublon
                d2 = b.ajouter_dossier(str(tmp) + '\\Vacances.')
                self.assertEqual(d2['chemin'], str(tmp) + '\\Vacances.')
                d3 = b.ajouter_dossier(str(tmp) + '\\Seul.')                   # avant : « introuvable »
                self.assertEqual({d['chemin'] for d in b.dossiers()},
                                 {d1['chemin'], d2['chemin'], d3['chemin']})
            finally:
                b.fermer()
        finally:
            for nom in ('Album.\\Sous', 'Album.', 'Vacances.', 'Seul.', 'Espace '):
                try:
                    os.rmdir(chemin_systeme(str(tmp) + '\\' + nom))
                except OSError:
                    pass
            ie.nettoyer(tmp)

    def test_prefixe_chemins_longs(self):
        self.assertEqual(chemin_systeme('C:\\a\\b.jpg'), '\\\\?\\C:\\a\\b.jpg')
        self.assertEqual(chemin_systeme('\\\\freebox\\disque\\x.jpg'), '\\\\?\\UNC\\freebox\\disque\\x.jpg')
        self.assertEqual(chemin_systeme('\\\\?\\C:\\a'), '\\\\?\\C:\\a')
        for p in ('C:\\a\\b.jpg', '\\\\freebox\\disque\\x.jpg', 'E:\\'):
            self.assertEqual(chemin_affiche(chemin_systeme(p)), p)

    def test_dedans(self):
        self.assertTrue(dedans('e:\\a\\b', 'e:\\a'))
        self.assertFalse(dedans('e:\\ab', 'e:\\a'))
        self.assertFalse(dedans('e:\\a', 'e:\\a'))
        self.assertTrue(dedans('e:\\a', 'e:\\'))

    def test_casse_reelle(self):
        tmp = Path(tempfile.mkdtemp(prefix='myphotos-casse-'))
        try:
            (tmp / 'Mes Photos').mkdir()
            reel = casse_reelle(normaliser_chemin(str(tmp / 'mes photos')))
            self.assertTrue(reel.endswith('Mes Photos'), reel)
            self.assertEqual(cle_chemin(reel), cle_chemin(str(tmp / 'Mes Photos')))
        finally:
            ie.nettoyer(tmp)

    def test_dates_exif(self):
        lire = indexation.lire_date_exif
        self.assertEqual(lire('2021:07:14 15:30:00'), '2021-07-14T15:30:00')
        self.assertEqual(lire(b'2020:02:29 10:00:00\x00'), '2020-02-29T10:00:00')
        self.assertEqual(lire('2021:07:14'), '2021-07-14T00:00:00')
        for absurde in ('0000:00:00 00:00:00', '2021:13:01 00:00:00', '2021:02:30 00:00:00', '1850:01:01 00:00:00',
                        '2999:01:01 00:00:00', '    ', '', None, 12, 'inconnue'):
            self.assertIsNone(lire(absurde), absurde)

    def test_requete(self):
        self.assertEqual(normaliser_requete('  chien \t  noir\n'), 'chien noir')
        self.assertEqual(normaliser_requete('géteau'), 'géteau')          # é décomposé -> composé
        self.assertEqual(len(normaliser_requete('a' * 500)), recherche.LONGUEUR_MAX)
        self.assertEqual(normaliser_requete(None), '')


# ---------------------------------------------------------------- avec un jeu d'images

class EssaiAvecImages(unittest.TestCase):
    """Dossier temporaire, base neuve, jeu d'images généré."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-essai-'))
        self.photos = self.tmp / 'photos'
        self.jeu = ie.creer_jeu(self.photos)
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.indexeurs = []

    def tearDown(self):
        for ix in self.indexeurs:
            ix.arreter()
        self.base.fermer()
        ie.nettoyer(self.tmp)

    def indexeur(self, fabrique=None, cle='base', **options):
        fabrique = fabrique or Fabrique()
        ix = Indexeur(self.base, fabrique, cle, fils_decodage=2, **options)
        self.indexeurs.append(ix)
        return ix, fabrique

    def indexer(self, fabrique=None, cle='base', dossier=None):
        if dossier is not False and not self.base.dossiers():
            self.base.ajouter_dossier(dossier or self.photos)
        ix, f = self.indexeur(fabrique, cle)
        ix.demarrer()
        self.assertTrue(ix.attendre(60), 'indexation trop longue')
        return ix, f

    def relancer(self, ix, **kw):
        ix.relancer(**kw)
        self.assertTrue(ix.attendre(60), 'indexation trop longue')

    def ids(self):
        """{clé de chemin: id} de toutes les photos connues."""
        return {cle_chemin(ch): pid for pid, ch in self.base._cnx().execute('SELECT id, chemin FROM photos')}

    def id_de(self, nom):
        return self.ids()[cle_chemin(str(self.jeu[nom]))]

    def ok(self):
        return self.jeu['attendues'] - self.jeu['erreurs']

    def plus_d_images(self, n=30):
        """Images en plus, pour que l'analyse dure plusieurs lots (essais d'arrêt et de pause)."""
        for i in range(n):
            ie.jpeg(self.photos / 'plus' / f'p{i:02d}.jpg', (200, 150), ie.VERT)
        self.jeu['attendues'] += n


class TestBase(EssaiAvecImages):

    def test_schema(self):
        c = self.base._cnx()
        self.assertEqual(c.execute('PRAGMA main.journal_mode').fetchone()[0], 'wal')
        self.assertEqual(c.execute('PRAGMA mini.journal_mode').fetchone()[0], 'wal')
        self.assertEqual(c.execute('PRAGMA main.user_version').fetchone()[0], 1)
        self.assertEqual(c.execute('PRAGMA mini.user_version').fetchone()[0], 1)
        self.assertEqual(c.execute('PRAGMA main.auto_vacuum').fetchone()[0], 2)
        colonnes = [l[1] for l in c.execute('PRAGMA table_info(photos)')]
        for col in ('id', 'chemin', 'dossier_id', 'taille', 'modifie', 'largeur', 'hauteur', 'date_prise',
                    'date_fichier', 'lat', 'lon', 'vecteur', 'modele', 'erreur', 'vu'):
            self.assertIn(col, colonnes)
        self.assertEqual(self.base.generation(), 0)
        self.assertEqual(self.base.compter(), {'total': 0, 'analysees': 0, 'a_faire': 0, 'erreurs': 0,
                                               'indisponibles': 0, 'en_attente': 0})
        ids, mat, dates = self.base.vecteurs('base')
        self.assertEqual((len(ids), mat.shape, len(dates)), (0, (0, 0), 0))
        # Réouverture : le schéma n'est pas recréé, les données restent.
        self.base.meta_set('essai', 42)
        self.base.fermer()
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.assertEqual(self.base.meta_get('essai'), 42)

    def test_meta(self):
        self.assertIsNone(self.base.meta_get('absent'))
        self.assertEqual(self.base.meta_get('absent', 'défaut'), 'défaut')
        self.base.meta_set('modele', 'base')
        self.base.meta_set('modele', 'grand')
        self.assertEqual(self.base.meta_get('modele'), 'grand')

    def test_ajouter_dossier_regles(self):
        vacances = self.photos / 'vacances'
        d = self.base.ajouter_dossier(str(vacances).lower() + '\\')
        self.assertEqual(cle_chemin(d['chemin']), cle_chemin(str(vacances)))
        self.assertTrue(d['chemin'].endswith('vacances'))
        self.assertEqual((d['photos'], d['remplaces'], d['disponible']), (0, [], True))
        with self.assertRaisesRegex(DossierRefuse, 'déjà dans MyPhotos'):
            self.base.ajouter_dossier(str(vacances).upper())
        (vacances / 'sous-dossier').mkdir()
        with self.assertRaisesRegex(DossierRefuse, 'fait déjà partie'):
            self.base.ajouter_dossier(vacances / 'sous-dossier')
        with self.assertRaisesRegex(DossierRefuse, 'introuvable'):
            self.base.ajouter_dossier(self.photos / 'nexiste-pas')
        # Les 4 photos de « vacances » sont analysées, puis le dossier parent le remplace sans les perdre.
        ix, f = self.indexer(dossier=False)
        self.assertEqual(self.base.compter()['analysees'], 4)
        avant = self.ids()
        parent = self.base.ajouter_dossier(self.photos)
        self.assertEqual([cle_chemin(p) for p in parent['remplaces']], [cle_chemin(str(vacances))])
        self.assertEqual(parent['photos'], 4)
        self.assertEqual([x['id'] for x in self.base.dossiers()], [parent['id']])
        self.relancer(ix)
        self.assertEqual(f.images, self.ok())            # 4 + les autres, sans refaire les 4
        apres = self.ids()
        for k, pid in avant.items():
            self.assertEqual(apres[k], pid)
        self.assertEqual(self.base.compter()['total'], self.jeu['attendues'])

    def test_retirer_dossier(self):
        self.indexer()
        d = self.base.dossiers()[0]
        un_id = self.id_de('exif')
        self.assertIsNotNone(self.base.miniature(un_id))
        g = self.base.generation()
        r = self.base.retirer_dossier(d['id'])
        self.assertEqual(r['photos'], self.jeu['attendues'])
        self.assertEqual(self.base.compter()['total'], 0)
        self.assertIsNone(self.base.miniature(un_id))
        self.assertIsNone(self.base.photo(un_id))
        self.assertEqual(self.base._cnx().execute('SELECT COUNT(*) FROM mini.miniatures').fetchone()[0], 0)
        self.assertNotEqual(self.base.generation(), g)
        self.assertEqual(self.base.dossiers(), [])
        self.assertIsNone(self.base.retirer_dossier(d['id']))
        for nom in ('exif', 'png', 'casse', 'icone', 'long', 'unicode'):  # les fichiers ne sont jamais touchés
            self.assertTrue(os.path.exists(chemin_systeme(str(self.jeu[nom]))), nom)

    def test_photos_par_date_et_vecteurs(self):
        self.indexer()
        page = self.base.photos_par_date(0, 10)
        self.assertEqual(page['total'], self.ok())
        self.assertEqual(len(page['photos']), 10)
        tout = self.base.photos_par_date(0, 1000)['photos']
        dates = [p['date'] for p in tout]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual([p['id'] for p in self.base.photos_par_date(5, 5)['photos']], [p['id'] for p in tout[5:10]])
        d = self.base.dossiers()[0]
        self.assertEqual(self.base.photos_par_date(0, 1000, dossier_id=d['id'])['total'], self.ok())
        ids, mat, dts = self.base.vecteurs('base')
        self.assertEqual(len(ids), self.ok())
        self.assertEqual(mat.shape, (self.ok(), MoteurFactice.DIM))
        self.assertEqual(mat.dtype, np.float32)
        np.testing.assert_allclose(np.linalg.norm(mat, axis=1), 1, atol=1e-5)
        self.assertEqual(dts.dtype, np.dtype('datetime64[s]'))
        self.assertEqual(len(self.base.vecteurs('autre')[0]), 0)
        infos = self.base.photos([ids[2], 999999, ids[0]])
        self.assertEqual([p['id'] for p in infos], [ids[2], ids[0]])

    def test_lectures_pendant_l_indexation(self):
        self.base.ajouter_dossier(self.photos)
        ix, _ = self.indexeur(Fabrique(lenteur=0.05))
        problemes = []
        fini = threading.Event()

        def lire():
            try:
                while not fini.is_set():
                    self.base.compter()
                    for p in self.base.photos_par_date(0, 50)['photos']:
                        self.base.miniature(p['id'])
                    self.base.vecteurs('base')
                    self.base.generation()
                    ix.etat()
            except Exception as e:                       # noqa: BLE001
                problemes.append(repr(e))

        fils = [threading.Thread(target=lire) for _ in range(4)]
        for t in fils:
            t.start()
        ix.demarrer()
        self.assertTrue(ix.attendre(60))
        fini.set()
        for t in fils:
            t.join()
        self.assertEqual(problemes, [])
        self.assertEqual(self.base.compter()['analysees'], self.ok())


class TestParcours(EssaiAvecImages):

    def test_parcours(self):
        trouves = {cle_chemin(ch): (t, m) for ch, t, m in Parcours(str(self.photos))}
        attendus = [k for k in self.jeu if k not in ('jonction', 'symlien', 'attendues', 'erreurs')]
        self.assertEqual(len(trouves), self.jeu['attendues'])
        for k in attendus:
            self.assertIn(cle_chemin(str(self.jeu[k])), trouves, k)
        noms = {os.path.basename(k) for k in trouves}
        for ignore in ('point.jpg', 'cachee.jpg', 'systeme.jpg', 'poubelle.jpg', 'vignette_nas.jpg',
                       'fichier_cache.jpg', 'notes.txt', '._mac.jpg'):
            self.assertNotIn(ignore, noms)
        self.assertFalse(any('\\boucle\\' in k or '\\lien_serie\\' in k for k in trouves))
        long = cle_chemin(str(self.jeu['long']))
        self.assertGreater(len(long), 260)
        self.assertFalse(long.startswith('\\\\?\\'))
        taille, modifie = trouves[long]
        self.assertEqual(taille, os.stat(chemin_systeme(str(self.jeu['long']))).st_size)
        if self.jeu['jonction'] is None:
            self.skipTest('jonction impossible à créer ici')

    def test_racine_injoignable(self):
        with self.assertRaises(OSError):
            list(Parcours(str(self.tmp / 'disque-debranche')))


class TestIndexeur(EssaiAvecImages):

    def test_indexation_complete(self):
        self.assertEqual(Indexeur(self.base, Fabrique(), 'base').etat()['phase'], 'attente')
        ix, f = self.indexer()
        e = ix.etat()
        self.assertEqual(e['phase'], 'termine', e)
        self.assertEqual((e['total'], e['faites'], e['a_faire'], e['erreurs']),
                         (self.jeu['attendues'], self.jeu['attendues'], 0, self.jeu['erreurs']))
        self.assertEqual(e['materiel'], 'processeur')
        self.assertIn('Toutes les photos', e['message'])
        self.assertEqual(self.base.compter(), {'total': self.jeu['attendues'], 'analysees': self.ok(), 'a_faire': 0,
                                               'erreurs': 2, 'indisponibles': 0, 'en_attente': 0})
        self.assertEqual(f.appels, ['auto'])
        self.assertEqual(f.images, self.ok())
        self.assertTrue(all(n <= indexation.LOT for n in f.moteurs[0].lots))
        self.assertEqual(f.moteurs[0].lots[0], indexation.LOT)
        self.assertGreater(self.base.generation(), 0)
        self.assertEqual(self.base.meta_get('modele'), 'base')

        p = self.base.photo(self.id_de('exif'))         # 400 x 300 tournée par l'EXIF (orientation 6)
        self.assertEqual((p['largeur'], p['hauteur']), (300, 400))
        self.assertEqual(p['date_prise'], '2021-07-14T15:30:00')
        self.assertEqual(p['date'], p['date_prise'])
        self.assertTrue(p['analysee'] and p['miniature'] and p['disponible'])
        self.assertIsNone(p['erreur'])
        self.assertEqual(p['nom'], 'rouge_exif.jpg')
        with Image.open(io.BytesIO(self.base.miniature(p['id']))) as m:
            self.assertEqual((m.format, m.size), ('JPEG', (270, 360)))
        self.assertEqual(self.base.photo(self.id_de('datetime'))['date_prise'], '2019-01-02T03:04:05')
        gps = self.base.photo(self.id_de('gps'))
        self.assertAlmostEqual(gps['lat'], 48.85679, places=4)
        self.assertAlmostEqual(gps['lon'], 2.351, places=4)
        absurde = self.base.photo(self.id_de('absurde'))
        self.assertIsNone(absurde['date_prise'])
        self.assertEqual(absurde['date'], absurde['date_fichier'])
        grand = self.base.photo(self.id_de('grand'))
        self.assertEqual((grand['largeur'], grand['hauteur']), (2000, 1500))
        with Image.open(io.BytesIO(self.base.miniature(grand['id']))) as m:
            self.assertEqual(m.size, (360, 270))
        png = self.base.photo(self.id_de('png'))
        with Image.open(io.BytesIO(self.base.miniature(png['id']))) as m:
            self.assertEqual(m.size, (200, 150))
            droite, gauche = m.convert('RGB').getpixel((170, 75)), m.convert('RGB').getpixel((30, 75))
        self.assertTrue(all(v > 235 for v in droite), droite)          # transparent -> blanc
        self.assertTrue(gauche[2] > 150 and gauche[0] < 90, gauche)     # bleu
        icone = self.base.photo(self.id_de('icone'))
        self.assertEqual((icone['erreur'], icone['largeur'], icone['hauteur']), ('trop petite', 50, 50))
        self.assertFalse(icone['miniature'])
        self.assertIsNone(self.base.miniature(icone['id']))
        self.assertTrue(self.base.photo(self.id_de('casse'))['erreur'].startswith('illisible'))
        for nom in ('tronquee', 'unicode', 'long'):
            self.assertTrue(self.base.photo(self.id_de(nom))['analysee'], nom)
        for pid in self.ids().values():
            j = self.base.miniature(pid)
            if j:
                with Image.open(io.BytesIO(j)) as m:
                    self.assertLessEqual(max(m.size), 360)
        blob = self.base._cnx().execute('SELECT vecteur FROM photos WHERE vecteur IS NOT NULL').fetchone()[0]
        self.assertEqual(len(blob), 2 * MoteurFactice.DIM)                # float16

    def test_plus_recentes_d_abord(self):
        ordre = []
        vrai = indexation.preparer

        def espion(chemin, taille):
            ordre.append(cle_chemin(chemin))
            return vrai(chemin, taille)

        with Modifie(indexation, 'preparer', espion):
            self.indexer()
        self.assertEqual(len(ordre), self.jeu['attendues'])
        dates = {cle_chemin(ch): df for ch, df in self.base._cnx().execute('SELECT chemin, date_fichier FROM photos')}
        suite = [dates[k] for k in ordre]
        self.assertEqual(suite, sorted(suite, reverse=True))      # les plus récentes (date du fichier) d'abord
        serie = [cle_chemin(str(self.jeu[f'serie{i}'])) for i in reversed(range(20))]
        self.assertEqual([k for k in ordre if k in serie], serie)

    def test_relance_sans_changement(self):
        ix, f = self.indexer()
        g = self.base.generation()
        self.relancer(ix)
        self.assertEqual(f.images, self.ok())             # rien n'est refait
        self.assertEqual(self.base.generation(), g)
        self.assertEqual(self.base.compter()['erreurs'], 2)
        self.assertEqual(ix.etat()['phase'], 'termine')

    def test_modification_disparition_deplacement(self):
        ix, f = self.indexer()
        avant = self.ids()
        modifiee, disparue = self.id_de('serie0'), self.id_de('serie1')
        v_avant = self.base._cnx().execute('SELECT vecteur FROM photos WHERE id = ?', (modifiee,)).fetchone()[0]
        ie.jpeg(self.jeu['serie0'], (240, 180), ie.VERT, quand=time.time())          # rouge -> vert
        os.remove(self.jeu['serie1'])
        vacances = {k: avant[cle_chemin(str(self.jeu[k]))] for k in ('exif', 'datetime', 'gps', 'absurde')}
        os.rename(self.photos / 'vacances', self.photos / 'vacances 2021')
        self.relancer(ix)
        self.assertEqual(f.images, self.ok() + 1)          # seule la photo modifiée est refaite
        self.assertEqual(self.base.compter()['total'], self.jeu['attendues'] - 1)
        self.assertIsNone(self.base.photo(disparue))
        self.assertIsNone(self.base.miniature(disparue))
        v_apres = self.base._cnx().execute('SELECT vecteur FROM photos WHERE id = ?', (modifiee,)).fetchone()[0]
        self.assertNotEqual(v_avant, v_apres)
        for k, pid in vacances.items():                   # renommées : même fiche, nouveau chemin, rien à refaire
            p = self.base.photo(pid)
            self.assertIn('vacances 2021', p['chemin'])
            self.assertTrue(p['analysee'] and p['miniature'], k)
        self.assertEqual(self.base.photo(vacances['exif'])['date_prise'], '2021-07-14T15:30:00')

    def test_dossier_injoignable(self):
        disque = self.tmp / 'disque'
        ie.jpeg(disque / 'a.jpg', couleur=ie.ROUGE)
        ie.jpeg(disque / 'sous' / 'b.jpg', couleur=ie.BLEU)
        self.base.ajouter_dossier(self.photos)
        d2 = self.base.ajouter_dossier(disque)
        ix, f = self.indexer()
        self.assertEqual(self.base.compter()['analysees'], self.ok() + 2)
        os.rename(disque, self.tmp / 'disque-range')       # disque débranché
        self.relancer(ix)
        c = self.base.compter()
        self.assertEqual((c['total'], c['indisponibles'], c['en_attente']), (self.jeu['attendues'] + 2, 2, 0))
        self.assertFalse(self.base.dossier(d2['id'])['disponible'])
        self.assertEqual(len(self.base.vecteurs('base')[0]), self.ok() + 2)    # toujours cherchables
        os.rename(self.tmp / 'disque-range', disque)       # rebranché
        self.relancer(ix)
        self.assertTrue(self.base.dossier(d2['id'])['disponible'])
        self.assertEqual(f.images, self.ok() + 2)          # rien n'est refait
        self.assertEqual(self.base.compter()['indisponibles'], 0)

    def test_dossier_vide_suspect(self):
        disque = self.tmp / 'partage'
        ie.jpeg(disque / 'a.jpg')
        self.base.ajouter_dossier(disque)
        ix, f = self.indexer(dossier=False)
        os.remove(disque / 'a.jpg')                       # partage réseau « vide » : on garde tout
        self.relancer(ix)
        self.assertEqual(self.base.compter()['total'], 1)
        self.assertFalse(self.base.dossiers()[0]['disponible'])

    def test_erreurs_pas_reessayees(self):
        ix, f = self.indexer()
        self.relancer(ix)
        self.assertEqual(f.images, self.ok())
        casse = self.id_de('casse')
        self.jeu['casse'].unlink()
        ie.jpeg(self.jeu['casse'], (300, 200), ie.BLEU, quand=time.time())  # réparé : il change, donc réessayé
        self.relancer(ix)
        self.assertEqual(f.images, self.ok() + 1)
        p = self.base.photo(casse)
        self.assertTrue(p['analysee'] and p['miniature'])
        self.assertIsNone(p['erreur'])
        self.assertEqual(self.base.compter()['erreurs'], 1)
        self.relancer(ix, erreurs=True)                   # redonne une chance : l'icône reste trop petite
        self.assertEqual(self.base.photo(self.id_de('icone'))['erreur'], 'trop petite')

    def test_changement_de_modele(self):
        ix, f = self.indexer(cle='factice-a')
        miniatures = {pid: self.base.miniature(pid) for pid in self.ids().values()}
        ix.arreter()
        ix2, f2 = self.indexer(cle='factice-b')
        self.assertEqual(f2.images, self.ok())             # tous les vecteurs refaits…
        self.assertEqual({pid: self.base.miniature(pid) for pid in self.ids().values()}, miniatures)  # … pas les miniatures
        self.assertEqual(self.base.meta_get('modele'), 'factice-b')
        self.assertEqual(len(self.base.vecteurs('factice-a')[0]), 0)
        self.assertEqual(len(self.base.vecteurs('factice-b')[0]), self.ok())

    def test_reprise_apres_arret(self):
        self.plus_d_images()
        self.base.ajouter_dossier(self.photos)
        with Modifie(indexation, 'ECRITURE_N', indexation.LOT):
            ix, f = self.indexeur(Fabrique(lenteur=0.3))
            ix.demarrer()
            self.assertTrue(attendre_que(lambda: self.base.compter()['analysees'] >= indexation.LOT))
            ix.arreter()
            self.assertEqual(ix.etat()['phase'], 'attente')
            faites = self.base.compter()['analysees']
            self.assertLess(faites, self.ok())
            ix2, f2 = self.indexer(dossier=False)
        self.assertEqual(self.base.compter()['analysees'], self.ok())
        self.assertEqual(f2.images, self.ok() - faites)    # reprend où il en était

    def test_carte_graphique_en_panne(self):
        ix, f = self.indexer(Fabrique(panne_carte=True))
        self.assertEqual(f.appels, ['auto', 'processeur'])
        self.assertEqual(self.base.compter()['analysees'], self.ok())
        self.assertEqual(ix.etat()['materiel'], 'processeur')

    def test_moteur_absent(self):
        fab = Fabrique(absent=True)
        ix, _ = self.indexer(fab)
        e = ix.etat()
        self.assertEqual(e['phase'], 'erreur')
        self.assertIn("n'a pas pu démarrer", e['message'])
        self.assertEqual(self.base.compter()['erreurs'], 0)   # les photos ne sont pas accusées
        fab.absent = False
        self.relancer(ix)
        self.assertEqual(ix.etat()['phase'], 'termine')
        self.assertEqual(self.base.compter()['analysees'], self.ok())

    def test_pause_reprise(self):
        self.plus_d_images()
        self.base.ajouter_dossier(self.photos)
        with Modifie(indexation, 'ECRITURE_N', indexation.LOT):
            ix, f = self.indexeur(Fabrique(lenteur=0.15))
            ix.demarrer()
            self.assertTrue(attendre_que(lambda: self.base.compter()['analysees'] >= indexation.LOT))
            ix.pause()
            self.assertEqual(ix.etat()['phase'], 'pause')
            self.assertTrue(attendre_que(lambda: ix._etat['phase'] == 'pause'))
            gele = self.base.compter()['analysees']
            time.sleep(0.8)
            self.assertEqual(self.base.compter()['analysees'], gele)
            self.assertLess(gele, self.ok())
            self.assertNotEqual(priorite_processus(), PRIORITE_INFERIEURE)   # rétablie pendant la pause
            ix.reprendre()
            self.assertTrue(ix.attendre(60))
        self.assertEqual(self.base.compter()['analysees'], self.ok())

    def test_priorite_abaissee_pendant_l_analyse(self):
        avant = priorite_processus()
        if avant is None:
            self.skipTest('Windows seulement')
        ix, f = self.indexer()
        self.assertTrue(all(p == PRIORITE_INFERIEURE for p in f.moteurs[0].priorites), f.moteurs[0].priorites)
        self.assertEqual(priorite_processus(), avant)

    def test_inventaire_automatique(self):
        self.base.ajouter_dossier(self.photos)
        ix, f = self.indexeur(inventaire_auto_s=0.3)
        ix.demarrer()
        self.assertTrue(ix.attendre(60))
        ie.jpeg(self.photos / 'nouvelle.jpg', couleur=ie.BLEU)
        self.assertTrue(attendre_que(lambda: self.base.compter()['analysees'] == self.ok() + 1, 10))


class TestRecherche(EssaiAvecImages):

    def setUp(self):
        super().setUp()
        self.ix, self.fab = self.indexer()
        self.texte = MoteurFactice()
        self.rech = Recherche(self.base, lambda: self.texte, 'base')

    def rouges(self):
        return {self.id_de(k) for k in ['exif', 'unicode'] + [f'serie{i}' for i in range(0, 20, 3)]}

    def autres_couleurs(self):
        return {self.id_de(k) for k in ['datetime', 'gps', 'grand', 'long'] +
                [f'serie{i}' for i in range(20) if i % 3]}

    def test_requete_vide(self):
        for vide in ('', '   ', '\n\t', None):
            self.assertIsNone(self.rech.chercher(vide))
        self.assertEqual(self.texte.textes, [])

    def test_rouge(self):
        r = self.rech.chercher('  rouge ', 0, 1000)
        self.assertEqual(r['requete'], 'rouge')
        sures = [x['id'] for x in r['sures']]
        self.assertEqual(r['total_sures'], len(sures))
        self.assertTrue(self.rouges() <= set(sures), self.rouges() - set(sures))
        self.assertFalse(self.autres_couleurs() & set(sures + [x['id'] for x in r['peut_etre']]))
        tous = r['sures'] + r['peut_etre']
        scores = [x['score'] for x in tous]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 <= x['proba'] <= 1 for x in tous))
        self.assertTrue(all(x['proba'] >= recherche.SEUIL_SUR for x in r['sures']))
        self.assertEqual(r['analysees'], self.ok())
        self.assertGreaterEqual(r['duree_ms'], 0)
        self.assertEqual(self.rech.chercher('zzz', 0, 1000)['total_sures'], 0)

    def test_pagination(self):
        tout = self.rech.chercher('rouge', 0, 1000)
        attendu = [x['id'] for x in tout['sures'] + tout['peut_etre']]
        vus = []
        for debut in range(0, len(attendu) + 3, 3):
            r = self.rech.chercher('rouge', debut, 3)
            self.assertEqual(r['total_sures'], tout['total_sures'])
            vus += [x['id'] for x in r['sures'] + r['peut_etre']]
        self.assertEqual(vus, attendu)
        self.assertEqual(len(self.rech.chercher('rouge', 0, 2)['sures']), 2)
        self.assertEqual(self.rech.chercher('rouge', 0, 0)['sures'], [])

    def test_cache_des_requetes(self):
        for q in ('rouge', 'Rouge', '  rouge  ', 'ROUGE'):
            self.rech.chercher(q)
        self.assertEqual(len(self.texte.textes), 1)
        for i in range(recherche.TAILLE_CACHE + 5):
            self.rech.chercher(f'mot{i}')
        self.rech.chercher('rouge')                        # chassée du cache par les 69 autres
        self.assertEqual(len(self.texte.textes), recherche.TAILLE_CACHE + 7)
        self.assertLessEqual(len(self.rech._cache), recherche.TAILLE_CACHE)

    def test_gabarit_et_seuils(self):
        with Modifie(recherche, 'GABARIT', 'une photo {q}'):
            self.rech.chercher('rouge')
        self.assertEqual(self.texte.textes[-1], 'une photo rouge')
        with Modifie(recherche, 'MINIMUM_RESULTATS', 5):
            r = self.rech.chercher('zzz')
        self.assertEqual((r['total_sures'], len(r['peut_etre'])), (0, 5))
        with Modifie(recherche, 'SEUIL_SUR', 1.01):           # rien n'est sûr : tout passe en « peut-être »
            r = self.rech.chercher('rouge', 0, 1000)
        self.assertEqual(r['total_sures'], 0)
        self.assertTrue(self.rouges() <= {x['id'] for x in r['peut_etre']})

    def test_rechargement_apres_indexation(self):
        self.assertEqual(self.rech.chercher('bleu')['analysees'], self.ok())
        nouvelle = ie.jpeg(self.photos / 'nouvelle_bleue.jpg', couleur=ie.BLEU)
        self.relancer(self.ix)
        self.rech.rafraichir()
        r = self.rech.chercher('bleu', 0, 1000)
        self.assertEqual(r['analysees'], self.ok() + 1)
        self.assertIn(self.ids()[cle_chemin(str(nouvelle))], [x['id'] for x in r['sures']])
        self.base.retirer_dossier(self.base.dossiers()[0]['id'])
        self.rech.rafraichir()
        r = self.rech.chercher('bleu')
        self.assertEqual((r['analysees'], r['total_sures'], r['sures']), (0, 0, []))

    def test_rechargement_au_plus_une_fois_par_seconde(self):
        self.rech.chercher('bleu')
        appels = []
        vrai = self.base.generation

        def compte():
            appels.append(1)
            return vrai()

        self.base.generation = compte
        try:
            for _ in range(20):
                self.rech.chercher('bleu')
            self.assertEqual(appels, [])
            time.sleep(recherche.RECHARGEMENT_MIN_S + 0.05)
            self.rech.chercher('bleu')
            self.assertEqual(appels, [1])
        finally:
            del self.base.generation

    def test_moteur_indisponible(self):
        def absent():
            raise FileNotFoundError('modèle absent')
        with self.assertRaises(MoteurIndisponible):
            Recherche(self.base, absent, 'base').chercher('chien')
        with self.assertRaises(MoteurIndisponible):
            Recherche(self.base, lambda: None, 'base').chercher('chien')
        r = Recherche(self.base, lambda: self.texte, 'base')
        r.prechauffer(attendre=True)
        self.assertIsNotNone(r._matrice)


# ---------------------------------------------------------------- relecture adverse du 24/11/2026
# Chaque essai ci-dessous échouait avant la correction du défaut qu'il décrit.

class TestRobustesse(EssaiAvecImages):

    def test_nom_unicode_invalide(self):
        """Un nom de fichier avec une moitié de caractère seule (UTF-16 invalide, que NTFS accepte) arrêtait toute
        l'indexation : 0 photo indexée, phase « erreur »."""
        bizarre = str(self.photos / 'serie' / 'photo\udc00x.jpg')
        try:
            ie.jpeg(bizarre, couleur=ie.BLEU)
        except (OSError, UnicodeError):
            self.skipTest('nom impossible à créer sur ce disque')
        ix, _ = self.indexer()
        self.assertEqual(ix.etat()['phase'], 'termine', ix.etat()['message'])
        self.assertEqual(self.base.compter()['total'], self.jeu['attendues'])     # toutes les autres, sans elle
        album = self.photos / 'album\udc00'
        os.makedirs(chemin_systeme(str(album)))
        with self.assertRaisesRegex(DossierRefuse, 'caractère'):
            self.base.ajouter_dossier(album)

    def test_base_abimee_mise_de_cote(self):
        """Un index.db ou un miniatures.db qui n'est plus une base empêchait MyPhotos de démarrer, à chaque fois."""
        self.assertEqual(self.base.ecartes, [])
        dechets = b'ceci n est pas une base SQLite ' * 200
        for nom in ('index.db', 'miniatures.db'):
            with self.subTest(fichier=nom):
                d = self.tmp / f'abimee-{nom}'
                d.mkdir()
                (d / nom).write_bytes(dechets)
                b = Base(d / 'index.db', d / 'miniatures.db')
                try:
                    self.assertEqual(len(b.ecartes), 1)
                    self.assertTrue(b.ecartes[0].startswith(str(d / nom) + '.abimee-'), b.ecartes)
                    self.assertEqual(Path(b.ecartes[0]).read_bytes(), dechets)      # mis de côté, rien d'effacé
                    b.meta_set('essai', 1)
                    self.assertEqual((b.meta_get('essai'), b.compter()['total']), (1, 0))
                finally:
                    b.fermer()

    def test_lecture_et_ouverture_pendant_une_ecriture(self):
        """Chaque connexion neuve demandait le verrou d'écriture (PRAGMA auto_vacuum) : une lecture depuis un fil neuf
        (requête du serveur) attendait la fin de l'écriture en cours, et MyPhotos ne démarrait plus du tout
        (« database is locked » au bout de 30 s) si un autre programme écrivait dans la base."""
        autre = sqlite3.connect(self.base.chemin_index, isolation_level=None)
        autre.execute('BEGIN IMMEDIATE')
        autre.execute("INSERT OR REPLACE INTO meta(cle, valeur) VALUES ('autre', 1)")
        resultats = []

        def lire():
            try:
                resultats.append(self.base.compter()['total'])
                deuxieme = Base(self.base.chemin_index, self.base.chemin_miniatures)
                resultats.append(deuxieme.generation())
                deuxieme.fermer()
            except Exception as e:                       # noqa: BLE001
                resultats.append(repr(e))

        fil = threading.Thread(target=lire, daemon=True)
        try:
            fil.start()
            fil.join(5)
            self.assertEqual(resultats, [0, 0])
        finally:
            autre.execute('ROLLBACK')
            autre.close()
            fil.join(40)

    def test_miniatures_perdues_puis_refaites(self):
        """miniatures.db perdue (ou mise de côté) : les photos analysées sortaient de la frise pour toujours, sans
        jamais être refaites. Même cas qu'une coupure de courant entre les écritures des deux fichiers."""
        ix, _ = self.indexer()
        ix.arreter()
        self.base.fermer()
        mini = self.tmp / 'donnees' / 'miniatures.db'
        for suffixe in ('', '-wal', '-shm'):
            Path(str(mini) + suffixe).unlink(missing_ok=True)
        self.base = Base(self.tmp / 'donnees' / 'index.db', mini)
        _, f = self.indexer(dossier=False)
        self.assertEqual(f.images, self.ok())                               # refaites…
        self.assertEqual(self.base.photos_par_date(0, 1000)['total'], self.ok())   # … et de retour dans la frise
        self.assertIsNotNone(self.base.miniature(self.id_de('exif')))

    def test_changement_de_moteur_pendant_un_lot(self):
        """L'ancien indexeur finit son lot après le démarrage du nouveau (arrêt qui tarde : disque réseau lent, gros
        modèle) : ses photos restaient analysées par l'ancien moteur, jamais refaites et introuvables."""
        self.base.ajouter_dossier(self.photos)
        feu = threading.Event()
        ancien = MoteurRetenu(feu)
        a, _ = self.indexeur(lambda materiel='auto': ancien, cle='moteur-a')
        a.demarrer()
        self.assertTrue(ancien.dans_un_lot.wait(30))
        a.arreter(attendre=False)                        # l'arrêt n'attend pas la fin du lot
        b, _ = self.indexeur(cle='moteur-b')
        b.demarrer()
        self.assertTrue(b.attendre(60))
        feu.set()
        a.arreter()                                      # l'ancien écrit son lot, puis s'arrête
        b.relancer()
        self.assertTrue(b.attendre(60))
        par_modele = dict(self.base._cnx().execute(
            'SELECT modele, COUNT(*) FROM photos WHERE modele IS NOT NULL GROUP BY modele'))
        self.assertEqual(par_modele, {'moteur-b': self.ok()})
        self.assertEqual(self.base.meta_get('modele'), 'moteur-b')

    def test_relance_pendant_un_passage(self):
        """Une relance reçue pendant un passage en provoque un seul autre : son réveil, resté levé, en lançait un
        troisième pour rien juste après la mise au repos (attendre() rendait la main, puis l'inventaire repartait)."""
        self.plus_d_images()
        self.base.ajouter_dossier(self.photos)
        ix, f = self.indexeur(Fabrique(lenteur=0.1))
        inventaires = []
        vrai = ix._inventaire

        def compte(*args):
            inventaires.append(1)
            vrai(*args)

        ix._inventaire = compte
        ix.demarrer()
        self.assertTrue(attendre_que(lambda: f.images > 0))
        ix.relancer()
        self.assertTrue(ix.attendre(60))
        time.sleep(0.3)
        self.assertEqual((len(inventaires), ix.etat()['phase']), (2, 'termine'))

    def test_relance_au_moment_de_la_mise_au_repos(self):
        """Une relance arrivée entre la fin d'un passage et la mise au repos était écrasée par ce repos : attendre()
        rendait la main alors que le passage demandé n'avait pas eu lieu."""
        self.base.ajouter_dossier(self.photos)
        ix, _ = self.indexeur()
        nouvelle = self.photos / 'arrivee_a_la_fin.jpg'
        vu, relances = [], []

        class Repos(threading.Event):                    # place la relance juste avant la mise au repos
            def set(s):
                if not relances and threading.current_thread() is ix._fil:
                    ie.jpeg(nouvelle, couleur=ie.BLEU)
                    t = threading.Thread(target=ix.relancer)
                    relances.append(t)
                    t.start()
                    t.join(0.5)                          # bloquée par le verrou de l'indexeur, une fois corrigé
                    super().set()
                    vu.append(ix._inventaire_demande)    # au repos avec une relance en attente ?
                    return
                super().set()

        ix._repos = Repos()
        ix.demarrer()
        self.assertTrue(attendre_que(lambda: relances, 60))
        relances[0].join(10)
        self.assertEqual(vu, [False])
        self.assertTrue(ix.attendre(60))
        self.assertIn(cle_chemin(str(nouvelle)), self.ids())


class TestFormats(unittest.TestCase):
    """preparer() sur des images que le jeu commun n'a pas."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-formats-'))

    def tearDown(self):
        ie.nettoyer(self.tmp)

    @staticmethod
    def degrade(w=300, h=200):
        a = np.zeros((h, w, 3), np.uint8)
        a[..., 0] = np.linspace(0, 255, w)[None, :]
        a[..., 1] = np.linspace(0, 255, h)[:, None]
        a[..., 2] = 90
        return Image.fromarray(a)

    def miniature(self, r):
        self.assertNotIn('erreur', r, r.get('erreur'))
        with Image.open(io.BytesIO(r['miniature'])) as m:
            return np.asarray(m.convert('RGB'), np.float32)

    def test_gris_16_et_32_bits(self):
        """Pillow écrête à 255 : une numérisation en gris 16 bits devenait toute blanche, un TIFF flottant tout noir."""
        rampe = np.linspace(0, 65535, 300)[None, :].repeat(200, 0)
        Image.fromarray(rampe.astype(np.uint16)).save(self.tmp / 'gris16.png')
        Image.fromarray(rampe.astype(np.int32)).save(self.tmp / 'entier32.tif')
        Image.fromarray((rampe / 65535).astype(np.float32)).save(self.tmp / 'flottant.tif')
        for nom in ('gris16.png', 'entier32.tif', 'flottant.tif'):
            with self.subTest(nom):
                r = indexation.preparer(str(self.tmp / nom), 32)
                a = self.miniature(r)
                self.assertEqual((r['largeur'], r['hauteur']), (300, 200))
                self.assertLess(abs(a.mean() - 127.5), 12)
                self.assertGreater(a[:, -10:].mean() - a[:, :10].mean(), 200)     # du noir au blanc

    def test_formats_hors_liste_refuses(self):
        """Un « .jpg » qui est en fait un PostScript allait jusqu'à Ghostscript, un programme extérieur, s'il est
        installé ; un TGA déguisé n'est pas une photo non plus."""
        from PIL import EpsImagePlugin
        (self.tmp / 'eps.jpg').write_bytes(b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 300 200\nshowpage\n%%EOF\n')
        self.degrade().save(self.tmp / 'tga.jpg', 'TGA')
        with mock.patch.object(EpsImagePlugin, 'Ghostscript', side_effect=AssertionError('Ghostscript')) as gs:
            for nom in ('eps.jpg', 'tga.jpg'):
                self.assertEqual(indexation.preparer(str(self.tmp / nom), 32).get('erreur'),
                                 'illisible : format non reconnu', nom)
        gs.assert_not_called()
        self.degrade().save(self.tmp / 'webp.jpg', 'WEBP')                      # format admis, extension fausse
        self.miniature(indexation.preparer(str(self.tmp / 'webp.jpg'), 32))

    def test_ouvrir_image_comme_l_analyse(self):
        """modele.ouvrir_image (visionneuse, outils) suit les règles de l'analyse, avec son code : mêmes formats, donc
        jamais Ghostscript pour un « .jpg » qui serait un PostScript, et les gris 16 bits ou flottants ramenés sur 8 bits
        avec leurs nuances (la visionneuse les montrait tout blancs). La visionneuse (cote=2560) garde sa taille."""
        from PIL import EpsImagePlugin, UnidentifiedImageError
        (self.tmp / 'eps.jpg').write_bytes(b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 300 200\nshowpage\n%%EOF\n')
        self.degrade().save(self.tmp / 'tga.jpg', 'TGA')
        with mock.patch.object(EpsImagePlugin, 'Ghostscript', side_effect=AssertionError('Ghostscript')) as gs:
            for nom in ('eps.jpg', 'tga.jpg'):
                with self.assertRaises(UnidentifiedImageError, msg=nom):
                    modele.ouvrir_image(str(self.tmp / nom))
        gs.assert_not_called()
        rampe = np.linspace(0, 65535, 1200)[None, :].repeat(900, 0)
        Image.fromarray(rampe.astype(np.uint16)).save(self.tmp / 'gris16.tif')
        Image.fromarray((rampe / 65535).astype(np.float32)).save(self.tmp / 'flottant.tif')
        for nom in ('gris16.tif', 'flottant.tif'):
            for cote, taille in ((448, (600, 450)), (2560, (1200, 900))):
                with self.subTest(nom=nom, cote=cote):
                    im = modele.ouvrir_image(str(self.tmp / nom), cote=cote)
                    self.assertEqual((im.mode, im.size), ('RGB', taille))
                    a = np.asarray(im, np.float32)
                    self.assertLess(abs(a.mean() - 127.5), 12)
                    self.assertGreater(a[:, -10:].mean() - a[:, :10].mean(), 200)     # du noir au blanc
        with open(self.tmp / 'gris16.tif', 'rb') as f:                           # fichier déjà ouvert : idem
            self.assertGreater(np.asarray(modele.ouvrir_image(f), np.float32).std(), 50)
        self.degrade().save(self.tmp / 'normale.png')
        self.assertEqual(modele.ouvrir_image(str(self.tmp / 'normale.png')).size, (300, 200))

    def test_grande_image_preparee_a_part(self):
        """Une très grande image non JPEG se décode en entier, et le chemin habituel en faisait trois ou quatre copies :
        3,4 Go pour un PNG de 178 Mpx, et jusqu'à quatre à la fois (4 PNG de 45 Mpx : +3,3 Go mesurés). Elle passe
        maintenant seule et réduite tout de suite (+0,35 Go). Seuil abaissé pour l'essai."""
        exif = Image.Exif()
        exif[0x0112] = 6                                                       # à tourner d'un quart de tour
        png = Image.new('RGBA', (1200, 900), (30, 50, 210, 255))
        png.paste((0, 0, 0, 0), (600, 0, 1200, 900))                           # moitié droite transparente
        png.save(self.tmp / 'grand.png', exif=exif.tobytes())
        self.degrade(1200, 900).convert('P', palette=Image.Palette.ADAPTIVE).save(self.tmp / 'palette.gif')
        habituel = self.miniature(indexation.preparer(str(self.tmp / 'palette.gif'), 32))
        appels = []
        vrai = indexation._ouvrir_a_part

        def espion(f):
            appels.append(1)
            return vrai(f)

        with Modifie(indexation, 'GRANDE_IMAGE', 500_000), Modifie(indexation, '_ouvrir_a_part', espion):
            r = indexation.preparer(str(self.tmp / 'grand.png'), 32)
            a_part = self.miniature(indexation.preparer(str(self.tmp / 'palette.gif'), 32))
        self.assertEqual(len(appels), 2)
        self.assertEqual((r['largeur'], r['hauteur']), (900, 1200))           # dans le sens d'affichage
        a = self.miniature(r)
        self.assertEqual(a.shape[:2], (360, 270))
        haut, bas = a[:60].mean(axis=(0, 1)), a[-60:].mean(axis=(0, 1))
        self.assertTrue(haut[2] > 150 and haut[0] < 90, haut)                 # la moitié bleue, en haut
        self.assertTrue(all(v > 235 for v in bas), bas)                       # la transparente, blanche, en bas
        self.assertEqual(a_part.shape, habituel.shape)
        self.assertLess(np.abs(a_part - habituel).mean(), 8)                  # même image que le chemin habituel


def _moteur_reel_present():
    try:
        return not modele.manquants('base', modele.fichiers_necessaires('base'))
    except Exception:                                    # noqa: BLE001
        return False


@unittest.skipUnless(_moteur_reel_present(), 'moteur « base » absent de MYPHOTOS_MODELES : essai sauté')
class TestMoteurReel(unittest.TestCase):
    """Bout à bout avec le vrai moteur SigLIP 2 « base » (modele.Moteur('base'), carte graphique si possible) sur des
    images générées : inventaire, analyse par l'indexeur, vecteurs en base, et des mots qui retrouvent leur image."""

    COULEURS = {'rouge': ie.ROUGE, 'verte': ie.VERT, 'bleue': ie.BLEU, 'jaune': (230, 210, 30)}

    @classmethod
    def setUpClass(cls):
        cls.moteur = modele.Moteur('base')

    @classmethod
    def tearDownClass(cls):
        cls.moteur = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-moteur-'))
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')

    def tearDown(self):
        self.base.fermer()
        ie.nettoyer(self.tmp)

    def fabrique(self, materiel='auto'):
        if materiel == 'processeur':                     # repli de l'indexeur si la carte graphique échoue
            return modele.Moteur('base', materiel='processeur', charger_texte=False)
        return self.moteur

    def test_bout_a_bout(self):
        photos = self.tmp / 'photos'
        for nom, couleur in self.COULEURS.items():
            ie.jpeg(photos / f'{nom}.jpg', (320, 240), couleur)
        cercle = Image.new('RGB', (320, 240), 'white')
        ImageDraw.Draw(cercle).ellipse((60, 20, 260, 220), fill='black')
        cercle.save(photos / 'cercle.jpg')
        self.base.ajouter_dossier(photos)
        ix = Indexeur(self.base, self.fabrique, 'base', fils_decodage=2)
        try:
            ix.demarrer()
            self.assertTrue(ix.attendre(300), 'analyse trop longue')
            e = ix.etat()
        finally:
            ix.arreter()
        self.assertEqual((e['phase'], e['analysees'], e['erreurs']), ('termine', 5, 0), e['message'])
        self.assertIn(e['materiel'], ('carte graphique', 'processeur'))
        dim = modele.info('base')['dimension']
        ids, mat, _ = self.base.vecteurs('base')
        self.assertEqual(mat.shape, (5, dim))
        np.testing.assert_allclose(np.linalg.norm(mat, axis=1), 1, atol=1e-3)
        blob = self.base._cnx().execute('SELECT vecteur FROM photos WHERE vecteur IS NOT NULL').fetchone()[0]
        self.assertEqual(len(blob), 2 * dim)                                   # float16 en base
        noms = {p['id']: os.path.splitext(p['nom'])[0] for p in self.base.photos(ids)}
        textes = {f'une photo {n}': n for n in self.COULEURS} | {'un cercle noir': 'cercle'}
        cos = mat @ self.moteur.vecteurs_textes(list(textes)).T               # (images, textes)
        attendus = list(textes.values())
        for j, texte in enumerate(textes):
            self.assertEqual(noms[int(ids[np.argmax(cos[:, j])])], attendus[j], texte)   # le texte trouve son image
        for i, pid in enumerate(ids):
            self.assertEqual(attendus[int(np.argmax(cos[i]))], noms[int(pid)])            # l'image, son texte
        p = modele.probabilite(cos, 'base')
        self.assertTrue(np.all((p >= 0) & (p <= 1)))


if __name__ == '__main__':
    unittest.main(verbosity=2)


# ---------------------------------------------------------------- 0.3.0 : actualiser, exclure, assistant

class TestExclusions(EssaiAvecImages):
    """Sous-dossiers exclus : photos oubliées tout de suite (base, miniatures, doublons), jamais supprimées du disque,
    plus jamais parcourues ; « Ne plus exclure » les retrouve."""

    def captures(self, n=3, nom='Captures d\u2019écran'):
        dossier = self.photos / nom
        for i in range(n):
            ie.jpeg(dossier / f'capture{i}.jpg', (200, 150), ie.BLEU)
        return dossier

    def sous(self, dossier):
        k = cle_chemin(str(dossier))
        return [pid for pid, ch in self.base._cnx().execute('SELECT id, chemin FROM photos')
                if cle_chemin(ch).startswith(k + os.sep)]

    def test_exclure_puis_ne_plus_exclure(self):
        from myphotos import doublons
        captures = self.captures()
        ix, _ = self.indexer()
        total = self.base.compter()['total']
        oubliees = self.sous(captures)
        self.assertEqual(len(oubliees), 3)
        d = self.base.dossiers()[0]
        # Empreintes de doublons déjà calculées pour ces photos : effacées avec elles.
        doublons.Doublons(self.base, 'base').fermer()
        with self.base._transaction() as c:
            c.executemany('INSERT INTO doublons_empreintes(photo_id, taille, modifie) VALUES (?, 1, 1)',
                          [(i,) for i in oubliees])
            c.execute('INSERT INTO doublons_paires(a, b, mesure) VALUES (?, ?, ?)', (oubliees[0], oubliees[1], 'null'))
        generation = self.base.generation()

        r = self.base.ajouter_exclusion(d['id'], str(captures))
        self.assertEqual((r['photos'], r['chemin'], r['dossier']), (3, str(captures), d['chemin']))
        self.assertEqual(self.sous(captures), [])
        self.assertEqual(self.base.compter()['total'], total - 3)
        self.assertGreater(self.base.generation(), generation)
        c = self.base._cnx()
        for pid in oubliees:
            self.assertIsNone(self.base.miniature(pid))
        self.assertEqual(c.execute('SELECT COUNT(*) FROM doublons_empreintes').fetchone()[0], 0)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM doublons_paires').fetchone()[0], 0)
        self.assertEqual(len(list(captures.iterdir())), 3)                # rien n'est supprimé du disque
        self.assertEqual([x['chemin'] for x in self.base.exclusions(d['id'])], [str(captures)])

        # L'inventaire ne le parcourt plus, et ne compte pas ses photos comme retirées une seconde fois.
        n = ix.relancer()
        self.assertTrue(ix.attendre(60))
        e = ix.etat()
        self.assertGreaterEqual(e['inventaire_fait'], n)
        self.assertEqual((e['bilan']['nouvelles'], e['bilan']['retirees']), (0, 0))
        self.assertEqual(self.sous(captures), [])
        self.assertNotIn(cle_chemin(str(captures)),
                         {cle_chemin(os.path.dirname(ch)) for ch, *_ in Parcours(str(self.photos),
                                                                                  exclus={cle_chemin(str(captures))})})

        # « Ne plus exclure » : un tour de CE dossier retrouve les photos (de nouveau à analyser).
        retiree = self.base.retirer_exclusion(r['id'])
        self.assertEqual((retiree['dossier_id'], retiree['chemin']), (d['id'], str(captures)))
        self.assertIsNone(self.base.retirer_exclusion(r['id']))
        self.assertEqual(self.base.exclusions(), [])
        n = ix.relancer(dossiers=[d['id']])
        self.assertTrue(ix.attendre(60))
        e = ix.etat()
        self.assertGreaterEqual(e['inventaire_fait'], n)
        self.assertEqual((e['bilan']['nouvelles'], e['bilan']['dossiers'], e['bilan']['tous']), (3, 1, False))
        self.assertEqual(len(self.sous(captures)), 3)
        self.assertEqual(self.base.compter()['total'], total)

    def test_refus_et_regroupements(self):
        captures = self.captures(1)
        (captures / 'Anciennes').mkdir()
        d = self.base.ajouter_dossier(self.photos)
        autre = self.tmp / 'ailleurs'
        autre.mkdir()
        for chemin, attendu in ((autre, 'n\'est pas dans'), (self.photos, 'lui-même'),
                                (self.tmp / 'absent', 'introuvable')):
            with self.assertRaises(DossierRefuse) as e:
                self.base.ajouter_exclusion(d['id'], str(chemin))
            self.assertIn(attendu, str(e.exception))
        with self.assertRaises(DossierRefuse):
            self.base.ajouter_exclusion(d['id'] + 99, str(captures))
        # Un sous-dossier, puis celui qui le contient : il le remplace.
        a = self.base.ajouter_exclusion(d['id'], str(captures / 'Anciennes'))
        b = self.base.ajouter_exclusion(d['id'], str(captures).upper())       # même dossier, autre casse
        self.assertEqual((cle_chemin(b['chemin']), b['remplacees']), (cle_chemin(str(captures)), [a['chemin']]))
        for chemin in (captures, captures / 'Anciennes'):
            with self.assertRaises(DossierRefuse) as e:
                self.base.ajouter_exclusion(d['id'], str(chemin))
            self.assertIn('déjà exclu', str(e.exception))
        # Un dossier suivi ne peut pas se trouver dans un sous-dossier exclu : refus expliqué.
        with self.assertRaises(DossierRefuse) as e:
            self.base.ajouter_dossier(str(captures / 'Anciennes'))
        self.assertIn('Ne plus exclure', str(e.exception))
        # Le dossier parent est ajouté : il absorbe le dossier suivi ET garde ses exclusions.
        p = self.base.ajouter_dossier(self.tmp)
        self.assertEqual(p['remplaces'], [d['chemin']])
        self.assertEqual([(x['dossier_id'], x['chemin']) for x in self.base.exclusions()], [(p['id'], str(captures))])
        # Retirer le dossier oublie aussi ses exclusions.
        self.base.retirer_dossier(p['id'])
        self.assertEqual(self.base.exclusions(), [])

    def test_inventaire_en_route(self):
        """Une exclusion posée pendant un inventaire : les photos qu'il trouve ensuite dans ce sous-dossier ne sont
        pas ajoutées (ajouter_photos filtre)."""
        captures = self.captures(2)
        d = self.base.ajouter_dossier(self.photos)
        self.base.ajouter_exclusion(d['id'], str(captures))
        lignes = [(str(p), 1, 1, None) for p in captures.iterdir()] + [(str(self.photos / 'neuve.jpg'), 1, 1, None)]
        self.assertEqual(self.base.ajouter_photos(d['id'], lignes), 1)

    @unittest.skipUnless(os.name == 'nt', 'noms finissant par un point : Windows')
    def test_point_final(self):
        """« Album. » exclu : « Album » (un autre dossier) garde ses photos."""
        avec = str(self.photos) + '\\Album.'
        os.mkdir(chemin_systeme(avec))
        ie.jpeg(avec + '\\a.jpg', (200, 150))
        ie.jpeg(self.photos / 'Album' / 'b.jpg', (200, 150))
        try:
            self.indexer()
            d = self.base.dossiers()[0]
            r = self.base.ajouter_exclusion(d['id'], avec.replace('\\', '/'))      # comme la fenêtre de tkinter
            self.assertEqual((r['chemin'], r['photos']), (avec, 1))
            noms = {chemin_affiche(ch) for (ch,) in self.base._cnx().execute('SELECT chemin FROM photos')}
            self.assertIn(str(self.photos / 'Album' / 'b.jpg'), noms)
            self.assertNotIn(avec + '\\a.jpg', noms)
        finally:
            for p in (avec + '\\a.jpg',):
                try:
                    os.remove(chemin_systeme(p))
                except OSError:
                    pass
            try:
                os.rmdir(chemin_systeme(avec))
            except OSError:
                pass

    def test_migration_base_0_2_0(self):
        """Une base 0.2.0 (sans table exclusions) s'ouvre sans rien perdre ; la table est ajoutée."""
        self.indexer()
        avant = self.base.compter()
        self.base.fermer()
        c = sqlite3.connect(self.tmp / 'donnees' / 'index.db')
        c.execute('DROP TABLE exclusions')
        c.commit()
        self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 1)
        c.close()
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.indexeurs = []
        self.assertEqual(self.base.compter(), avant)
        self.assertEqual(self.base.exclusions(), [])
        self.assertEqual(self.base._cnx().execute('PRAGMA user_version').fetchone()[0], 1)
        d = self.base.dossiers()[0]
        self.assertEqual(self.base.ajouter_exclusion(d['id'], str(self.photos / 'vacances'))['photos'], 4)


class TestActualisation(EssaiAvecImages):
    """Bouton « Actualiser » : un inventaire numéroté, avec son bilan (nouvelles, retirées, dossiers injoignables)."""

    def test_nouvelles_et_disparues(self):
        ix, _ = self.indexer()
        total = self.base.compter()['total']
        ie.jpeg(self.photos / 'neuves' / 'n1.jpg', (200, 150), ie.VERT)
        ie.jpeg(self.photos / 'neuves' / 'n2.jpg', (200, 150), ie.BLEU)
        os.replace(self.jeu['exif'], self.photos / 'deplacee.jpg')     # déplacée : ni nouvelle ni retirée
        for p in (self.jeu['datetime'], self.jeu['gps'], self.jeu['absurde']):
            p.unlink()
        n = ix.relancer()
        self.assertTrue(ix.attendre(60))
        e = ix.etat()
        self.assertEqual(e['inventaire_fait'], n)
        b = e['bilan']
        self.assertEqual((b['nouvelles'], b['retirees'], b['injoignables'], b['numero'], b['tous']),
                         (2, 3, [], n, True))
        self.assertEqual(self.base.compter()['total'], total - 1)
        self.assertEqual(self.base.compter()['a_faire'], 0)                   # les nouvelles sont déjà analysées

    def test_dossier_injoignable_garde(self):
        ix, _ = self.indexer()
        loin = self.tmp / 'cle-usb'
        ie.jpeg(loin / 'u.jpg', (200, 150))
        d2 = self.base.ajouter_dossier(loin)
        self.relancer(ix)
        self.assertEqual(self.base.dossier(d2['id'])['photos'], 1)
        os.replace(loin, self.tmp / 'cle-usb-debranchee')                     # « disque débranché »
        n = ix.relancer()
        self.assertTrue(ix.attendre(60))
        b = ix.etat()['bilan']
        self.assertEqual((b['numero'], b['retirees'], b['injoignables']), (n, 0, [str(loin)]))
        self.assertEqual(self.base.dossier(d2['id'])['photos'], 1)            # gardée
        self.assertFalse(self.base.dossier(d2['id'])['disponible'])

    def test_numeros(self):
        ix, _ = self.indexeur()
        self.assertEqual((ix.etat()['inventaire_fait'], ix.etat()['bilan']), (0, None))
        self.assertEqual(ix.relancer(), 1)
        self.assertEqual(ix.relancer(), 2)
        self.assertTrue(ix.attendre(60))
        self.assertEqual(ix.etat()['inventaire_fait'], 2)


class TestExamen(unittest.TestCase):
    """Assistant : plus gros sous-dossiers comptés, captures et fonds d'écran reconnus."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-examen-'))

    def tearDown(self):
        ie.nettoyer(self.tmp)

    def fichiers(self, dossier, n):
        dossier.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (dossier / f'f{i:04d}.jpg').write_bytes(b'x')          # jamais ouverts : le nom suffit

    def test_noms_reconnus(self):
        for nom in ('Captures d\u2019écran', "Captures d'écran", 'Screenshots', 'screenshot', 'Captures', 'CAPTURE D ECRAN',
                    'Screenshots 2023'):
            self.assertEqual(indexation.nature_sous_dossier(nom), 'captures', nom)
        for nom in ('Wallpapers', 'Fonds d’écran', "Fond d'écran", 'Fonds d’écran Windows', 'Bing Wallpaper',
                    'Windows Spotlight', 'Arrière-plans', 'Wallpaper HD'):
            self.assertEqual(indexation.nature_sous_dossier(nom), 'fonds', nom)
        for nom in ('Vacances', 'Pellicule', 'Camera Roll', 'Images enregistrées', 'Capturer la lumière', 'Fondue', ''):
            self.assertIsNone(indexation.nature_sous_dossier(nom), nom)

    def test_comptage(self):
        self.fichiers(self.tmp / 'Captures d\u2019écran', 5)
        self.fichiers(self.tmp / 'Vacances', 3)
        self.fichiers(self.tmp / 'Divers' / 'Wallpapers', 2)
        self.fichiers(self.tmp / 'Divers', 1)
        self.fichiers(self.tmp / 'Vide', 0)
        self.fichiers(self.tmp, 1)
        ex = indexation.examiner_dossier(str(self.tmp))
        self.assertTrue(ex['complet'])
        self.assertEqual(ex['total'], 12)
        self.assertEqual([(s['nom'], s['photos'], s['nature']) for s in ex['sous_dossiers']],
                         [('Captures d\u2019écran', 5, 'captures'), ('Divers', 3, None), ('Vacances', 3, None),
                          (os.path.join('Divers', 'Wallpapers'), 2, 'fonds')])
        self.assertEqual(ex['sous_dossiers'][3]['chemin'], str(self.tmp / 'Divers' / 'Wallpapers'))
        ex = indexation.examiner_dossier(str(self.tmp), exclus={cle_chemin(str(self.tmp / 'Vacances'))})
        self.assertNotIn('Vacances', [s['nom'] for s in ex['sous_dossiers']])

    def test_comptage_borne(self):
        self.fichiers(self.tmp / 'Gros', 600)
        ex = indexation.examiner_dossier(str(self.tmp), limite_s=0)
        self.assertFalse(ex['complet'])
        self.assertEqual(ex['total'], 256)
        with self.assertRaises(OSError):
            indexation.examiner_dossier(str(self.tmp / 'absent'))


# ---------------------------------------------------------------- 0.4.0 : disque changé, dossier presque vide

class TestDisqueChange(EssaiAvecImages):
    """Deux disques qui échangent leur lettre : un autre disque à la place d'un dossier suivi (autre numéro de volume)
    le rend inaccessible, ses photos sont gardées ; revenu, rien n'est refait. Le numéro de volume du dossier suivi est
    simulé (on ne branche pas de disque pendant un essai) ; les fichiers, la base et l'inventaire sont réels."""

    def setUp(self):
        super().setUp()
        self.disque = self.tmp / 'F'
        ie.jpeg(self.disque / 'a.jpg', (200, 150), ie.ROUGE)
        ie.jpeg(self.disque / 'sous' / 'b.jpg', (200, 150), ie.BLEU)
        self.numero = 0xA1
        vrai = base_mod.identite_volume

        def simule(chemin):
            if cle_chemin(str(chemin)) != cle_chemin(str(self.disque)):
                return vrai(chemin)
            return self.numero if os.path.isdir(self.disque) else None
        for module in (base_mod, indexation):
            c = mock.patch.object(module, 'identite_volume', simule)
            c.start()
            self.addCleanup(c.stop)

    def test_autre_disque_sur_la_meme_lettre(self):
        d = self.base.ajouter_dossier(self.disque)
        self.assertEqual(self.base.dossier(d['id'])['volume'], 0xA1)          # retenu dès l'ajout
        ix, f = self.indexer(dossier=False)
        avant = self.ids()
        self.assertEqual(len(avant), 2)
        # Le disque A est débranché ; le disque B prend sa lettre, avec d'autres photos.
        os.rename(self.disque, self.tmp / 'disque-A')
        ie.jpeg(self.disque / 'c.jpg', (200, 150), ie.VERT)
        self.numero = 0xB2
        n = ix.relancer()
        self.assertTrue(ix.attendre(60))
        b = ix.etat()['bilan']
        self.assertEqual((b['numero'], b['retirees'], b['nouvelles'], b['injoignables']),
                         (n, 0, 0, [str(self.disque)]))
        self.assertEqual(self.ids(), avant)                                 # gardées, et rien du disque B
        self.assertFalse(self.base.dossier(d['id'])['disponible'])
        self.assertEqual(self.base.dossier(d['id'])['volume'], 0xA1)
        # Le disque A revient à sa lettre : tout est là, rien n'est refait.
        shutil.rmtree(self.disque)
        os.rename(self.tmp / 'disque-A', self.disque)
        self.numero = 0xA1
        self.relancer(ix)
        self.assertTrue(self.base.dossier(d['id'])['disponible'])
        self.assertEqual(self.ids(), avant)
        self.assertEqual(f.images, 2)

    def test_disque_recopie_adopte(self):
        """Les mêmes photos sur un nouveau disque (recopié, cloné, partage remonté) : son numéro est adopté, rien n'est
        retiré ni refait ; seule une vraie nouvelle photo est analysée."""
        d = self.base.ajouter_dossier(self.disque)
        ix, f = self.indexer(dossier=False)
        avant = self.ids()
        os.rename(self.disque, self.tmp / 'ancien-disque')
        shutil.copytree(self.tmp / 'ancien-disque', self.disque)            # dates de modification gardées
        ie.jpeg(self.disque / 'neuve.jpg', (200, 150), ie.VERT)
        self.numero = 0xC3
        self.relancer(ix)
        b = ix.etat()['bilan']
        self.assertEqual((b['retirees'], b['nouvelles'], b['injoignables']), (0, 1, []))
        self.assertTrue(self.base.dossier(d['id'])['disponible'])
        self.assertEqual(self.base.dossier(d['id'])['volume'], 0xC3)
        self.assertEqual({k: v for k, v in self.ids().items() if k in avant}, avant)
        self.assertEqual(f.images, 3)

    def test_base_0_3_0_sans_numero(self):
        """Base 0.3.0 (sans la colonne volume) : rouverte sans rien perdre ni changer user_version ; le numéro est
        appris au tour suivant. La 0.3.0 ne lit que les colonnes qu'elle connaît : la colonne ajoutée ne la gêne pas."""
        d = self.base.ajouter_dossier(self.disque)
        ix, _ = self.indexer(dossier=False)
        ix.arreter()
        avant = self.base.compter()
        self.base.fermer()
        c = sqlite3.connect(self.tmp / 'donnees' / 'index.db')
        c.execute('ALTER TABLE dossiers DROP COLUMN volume')
        c.commit()
        c.close()
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.indexeurs = []
        self.assertEqual(self.base.compter(), avant)
        self.assertIsNone(self.base.dossier(d['id'])['volume'])
        self.assertEqual(self.base._cnx().execute('PRAGMA user_version').fetchone()[0], 1)
        ix, f = self.indexer(dossier=False)
        self.assertEqual(self.base.dossier(d['id'])['volume'], 0xA1)
        self.assertEqual(f.images, 0)
        # Ce que la 0.3.0 écrit (dossier ajouté sans la colonne) : numéro inconnu, appris au tour suivant.
        autre = self.tmp / 'autre'
        ie.jpeg(autre / 'x.jpg', (200, 150))
        with self.base._transaction() as c:
            nid = c.execute("INSERT INTO dossiers(chemin, ajoute, disponible) VALUES (?, '2026-11-24T12:00:00', 1)",
                            (str(autre),)).lastrowid
        self.assertIsNone(self.base.dossier(nid)['volume'])
        self.relancer(ix)
        self.assertEqual(self.base.dossier(nid)['volume'], base_mod.identite_volume(str(autre)))
        self.assertEqual(self.base.dossier(nid)['photos'], 1)


class TestDossierPresqueVide(EssaiAvecImages):
    """Un dossier suivi qui ne contient plus que desktop.ini (dossier Images que OneDrive n'a pas encore rempli, ou
    vidé) ou d'autres fichiers que Windows y range est traité comme un dossier vide : inaccessible, ses photos sont
    gardées. Des photos vraiment supprimées d'un dossier qui a encore du contenu sont retirées, comme avant."""

    def setUp(self):
        super().setUp()
        self.images = self.tmp / 'Images'
        ie.jpeg(self.images / 'a.jpg', (200, 150), ie.ROUGE)
        ie.jpeg(self.images / 'Pellicule' / 'b.jpg', (200, 150), ie.BLEU)
        self.ini = self.images / 'desktop.ini'
        self.ini.write_text('[.ShellClassInfo]\r\nLocalizedResourceName=@%SystemRoot%\\system32\\shell32.dll,-21779\r\n',
                            encoding='utf-8')
        ie.attribut(self.ini, ie.CACHE | ie.SYSTEME)
        self.d = self.base.ajouter_dossier(self.images)
        self.ailleurs = self.tmp / 'ailleurs'
        self.ailleurs.mkdir()

    def vider(self):
        for nom in ('a.jpg', 'Pellicule'):
            os.replace(self.images / nom, self.ailleurs / nom)

    def test_seulement_desktop_ini(self):
        ix, f = self.indexer(dossier=False)
        avant = self.ids()
        self.vider()
        self.relancer(ix)
        b = ix.etat()['bilan']
        self.assertEqual((b['retirees'], b['injoignables']), (0, [str(self.images)]))
        self.assertEqual(self.ids(), avant)
        self.assertFalse(self.base.dossier(self.d['id'])['disponible'])
        # desktop.ini sans ses attributs (recopié d'ailleurs) et Thumbs.db : pareil.
        ie.attribut(self.ini, 0x80)                                         # FILE_ATTRIBUTE_NORMAL
        (self.images / 'Thumbs.db').write_bytes(b'\0' * 16)
        self.relancer(ix)
        self.assertEqual(self.ids(), avant)
        # Les photos reviennent (OneDrive a fini) : tout est là, rien n'est refait.
        for nom in ('a.jpg', 'Pellicule'):
            os.replace(self.ailleurs / nom, self.images / nom)
        self.relancer(ix)
        self.assertTrue(self.base.dossier(self.d['id'])['disponible'])
        self.assertEqual(self.ids(), avant)
        self.assertEqual(f.images, 2)

    def test_photos_vraiment_supprimees(self):
        """Toutes les photos supprimées, mais le dossier garde du contenu (un document) : elles sont retirées."""
        (self.images / 'notes.txt').write_text('liste de courses', encoding='utf-8')
        ix, _ = self.indexer(dossier=False)
        self.vider()
        self.relancer(ix)
        b = ix.etat()['bilan']
        self.assertEqual((b['retirees'], b['injoignables']), (2, []))
        self.assertEqual(self.base.compter()['total'], 0)
        self.assertTrue(self.base.dossier(self.d['id'])['disponible'])
