"""Essais du cœur de MyPhotos (base, indexation, recherche) avec un moteur factice rapide et déterministe.

Images générées par Pillow dans un dossier temporaire (jamais de vraies photos).
    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import io
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                                     # noqa: E402
from PIL import Image                                  # noqa: E402

import images_essai as ie                              # noqa: E402
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
        self.assertEqual(normaliser_chemin('e:/Photos/Vacances/'), 'E:\\Photos\\Vacances')
        self.assertEqual(normaliser_chemin('  "E:\\Photos"  '), 'E:\\Photos')
        self.assertEqual(normaliser_chemin('e:'), 'E:\\')
        self.assertEqual(normaliser_chemin('E:\\Photos\\..\\Albums'), 'E:\\Albums')
        self.assertEqual(normaliser_chemin('\\\\?\\E:\\Photos'), 'E:\\Photos')
        with self.assertRaises(DossierRefuse):
            normaliser_chemin('   ')

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
