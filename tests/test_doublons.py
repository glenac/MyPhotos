"""Essais de la recherche des doublons (myphotos/doublons.py), sur des images générées par Pillow (jamais de vraies
photos) et un moteur factice dont les vecteurs viennent des pixels réduits.

    .venv\\Scripts\\python.exe -m unittest tests.test_doublons -v
"""
import io
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np                                     # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont   # noqa: E402

from myphotos import doublons, modele                 # noqa: E402
from myphotos.base import Base, chemin_systeme        # noqa: E402
from myphotos.doublons import Annule, Doublons        # noqa: E402
from myphotos.indexation import Indexeur              # noqa: E402

CLE = 'essai'                                          # moteur sans réglage mesuré : seuils de repli
DATE_EXIF = '2023:05:01 10:00:00'


# ---------------------------------------------------------------- images générées

def dessin(graine, w=900, h=675):
    """Image « photographique » générée : fond dégradé, formes, traits, grain. Deux graines = deux scènes."""
    rnd = random.Random(graine)
    petit = Image.new('RGB', (4, 3))
    petit.putdata([tuple(rnd.randrange(30, 226) for _ in range(3)) for _ in range(12)])
    im = petit.resize((w, h), Image.Resampling.BICUBIC)
    d = ImageDraw.Draw(im)
    for _ in range(40):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r = rnd.randrange(w // 60, w // 7)
        c = tuple(rnd.randrange(256) for _ in range(3))
        forme = rnd.random()
        if forme < 0.4:
            d.ellipse((x - r, y - r, x + r, y + r), fill=c)
        elif forme < 0.75:
            d.rectangle((x - r, y - r // 2, x + r, y + r // 2), fill=c)
        else:
            d.line((x, y, x + rnd.randrange(-w // 3, w // 3), y + rnd.randrange(-h // 3, h // 3)), fill=c,
                   width=rnd.randrange(2, 9))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    bruit = np.random.default_rng(graine).normal(0, 6, (h, w, 3))
    return Image.fromarray(np.clip(np.asarray(im, np.float32) + bruit, 0, 255).astype(np.uint8))


def exif_date(date=DATE_EXIF):
    e = Image.Exif()
    e.get_ifd(0x8769)[36867] = date
    return e.tobytes()


def ecrire(chemin, im, format_='JPEG', qualite=92, date=None):
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if format_ == 'JPEG':
        im.save(chemin, 'JPEG', quality=qualite, exif=exif_date(date) if date else b'')
    else:
        im.save(chemin, format_)
    return chemin


class MoteurPixels:
    """Moteur factice : le vecteur d'une image est sa version 4 × 4 en couleurs, centrée. Deux versions d'une même
    image ont des vecteurs très proches (cosinus > 0,97), deux scènes générées différentes non."""
    taille_image = 32
    materiel = 'processeur'

    def vecteurs_images(self, lot):
        n = len(lot)
        x = lot.reshape(n, 3, 4, 8, 4, 8).mean(axis=(3, 5)).reshape(n, -1)
        return modele.normaliser(x - x.mean(axis=1, keepdims=True))


def fabrique(materiel='auto'):
    return MoteurPixels()


class EssaiDoublons(unittest.TestCase):
    """Photothèque générée : une photo, sa copie exacte et ses retouches ; une rafale ; deux scènes différentes de
    même style ; deux images presque noires."""

    @classmethod
    def setUpClass(cls):
        cls.modeles = Path(tempfile.mkdtemp(prefix='myphotos-doublons-modeles-'))
        a = dessin(1)
        cls.a = a
        p = cls.modeles
        ecrire(p / 'vacances' / 'IMG_0001.jpg', a, date=DATE_EXIF)
        w, h = a.size
        ecrire(p / 'WhatsApp Images' / 'IMG-20230501-WA0001.jpg', a.resize((w // 2, h // 2), Image.Resampling.LANCZOS),
               qualite=70)
        ecrire(p / 'vacances' / 'IMG_0001_recadree.jpg', a.crop((40, 20, w - 14, h - 30)), qualite=90, date=DATE_EXIF)
        ecrire(p / 'export' / 'IMG_0001.png', a, 'PNG')
        ecrire(p / 'vacances' / 'IMG_0001_claire.jpg', ImageEnhance.Brightness(a).enhance(1.1), qualite=90,
               date=DATE_EXIF)
        # Rafale : trois cadres décalés de la même scène, deux avec une autre exposition.
        b = dessin(2, 980, 700)
        ecrire(p / 'rafale' / 'B1.jpg', b.crop((0, 0, 900, 675)), qualite=90)
        ecrire(p / 'rafale' / 'B2.jpg', ImageEnhance.Brightness(b.crop((50, 10, 950, 685))).enhance(1.14), qualite=90)
        ecrire(p / 'rafale' / 'B3.jpg', ImageEnhance.Brightness(b.crop((25, 25, 925, 700))).enhance(0.88), qualite=90)
        # Deux scènes différentes du même style, et deux images presque noires différentes.
        ecrire(p / 'divers' / 'C.jpg', dessin(3))
        ecrire(p / 'divers' / 'D.jpg', dessin(4))
        for i, g in enumerate((5, 6)):
            sombre = np.random.default_rng(g).normal(8, 1.5, (600, 800, 3))
            ecrire(p / 'divers' / f'noir{i}.jpg', Image.fromarray(np.clip(sombre, 0, 255).astype(np.uint8)))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.modeles, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-doublons-'))
        self.photos = self.tmp / 'photos'
        shutil.copytree(self.modeles, self.photos)
        shutil.copy2(self.photos / 'vacances' / 'IMG_0001.jpg', self.photos / 'vacances' / 'IMG_0001 - Copie.jpg')
        self.base = Base(self.tmp / 'donnees' / 'index.db', self.tmp / 'donnees' / 'miniatures.db')
        self.a_fermer = []

    def tearDown(self):
        for x in self.a_fermer:
            x()
        self.base.fermer()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def indexer(self):
        if not self.base.dossiers():
            self.base.ajouter_dossier(str(self.photos))
        ix = Indexeur(self.base, fabrique, CLE, fils_decodage=2)
        self.a_fermer.append(ix.arreter)
        ix.relancer()
        self.assertTrue(ix.attendre(60), 'indexation trop longue')
        return ix

    def doublons(self):
        d = Doublons(self.base, CLE)
        self.a_fermer.insert(0, d.fermer)
        return d

    def ids(self):
        return {os.path.relpath(ch, self.photos).replace('\\', '/'): pid
                for pid, ch in self.base._cnx().execute('SELECT id, chemin FROM photos')}

    @staticmethod
    def groupes(res, type_):
        return [g for g in res['groupes'] if g['type'] == type_]

    def noms(self, groupe):
        inverse = {v: k for k, v in self.ids().items()}
        return {inverse[p['id']] for p in groupe['photos']}


# ---------------------------------------------------------------- fonctions simples

class TestFonctions(unittest.TestCase):

    def test_copies_evidentes(self):
        for chemin in ('E:\\Photos\\IMG_1 - Copie.jpg', 'E:\\Photos\\WhatsApp Images\\IMG-1.jpg',
                       'E:\\Photos\\WhatsApp Images\\Envoyé\\IMG-1.jpg', 'E:\\Photos\\IMG_1 (1).jpg',
                       'E:\\Photos\\Copy of IMG_1.jpg', 'E:\\Photos\\IMG_1 - Copy.jpg', 'E:\\Copie\\IMG_1.jpg'):
            self.assertIsNotNone(doublons.motif_copie(chemin), chemin)
        for chemin in ('E:\\Photos\\IMG_1.jpg', 'E:\\Photos\\photocopieuse.jpg', 'E:\\Photos\\2019 (vacances)\\a.jpg',
                       None, ''):
            self.assertIsNone(doublons.motif_copie(chemin), chemin)

    def test_empreinte_et_distance(self):
        a = dessin(10, 360, 270)
        t = io.BytesIO()
        a.save(t, 'JPEG', quality=80)
        h1, c1 = doublons.empreinte(t.getvalue())
        h2, c2 = doublons.empreinte(t.getvalue())
        self.assertEqual((h1, c1), (h2, c2))
        self.assertTrue(-(1 << 63) <= h1 < (1 << 63))       # entier signé : SQLite sait l'enregistrer
        self.assertGreater(c1, doublons.CONTRASTE_MIN)
        t2 = io.BytesIO()
        a.resize((180, 135)).save(t2, 'JPEG', quality=60)
        self.assertLessEqual(doublons.distance(h1, doublons.empreinte(t2.getvalue())[0]), 6)
        t3 = io.BytesIO()
        dessin(11, 360, 270).save(t3, 'JPEG', quality=80)
        self.assertGreater(doublons.distance(h1, doublons.empreinte(t3.getvalue())[0]), 16)
        self.assertEqual(doublons.distance(0, -1), 64)

    def test_verification(self):
        def mini(im):
            t = io.BytesIO()
            im.thumbnail((360, 360))
            im.save(t, 'JPEG', quality=80)
            return doublons.preparer_verification(t.getvalue())
        a = dessin(20, 900, 600)
        ref = mini(a.copy())
        retouches = {'réduite': a.resize((450, 300)), 'recadrée': a.crop((30, 10, 860, 580)),
                     'bordure': Image.fromarray(np.pad(np.asarray(a), ((30, 30), (30, 30), (0, 0)), constant_values=255)),
                     'claire': ImageEnhance.Brightness(a).enhance(1.1), 'tournée': a.rotate(2, Image.Resampling.BICUBIC)
                     .crop((40, 30, 860, 570))}
        for nom, im in retouches.items():
            m = doublons.verifier(ref, mini(im))
            self.assertTrue(doublons.est_retouchee(m), (nom, m))
        # Cadre décalé de 6 % et autre exposition : pas une retouche, mais superposable (rafale).
        b = dessin(21, 960, 600)
        m = doublons.verifier(mini(b.crop((0, 0, 900, 600))),
                              mini(ImageEnhance.Brightness(b.crop((60, 0, 960, 600))).enhance(1.15)))
        self.assertFalse(doublons.est_retouchee(m), m)
        self.assertTrue(doublons.superposables(m), m)
        # Un texte ajouté : pas une retouche.
        t = a.copy()
        ImageDraw.Draw(t).text((250, 480), 'Joyeux anniversaire !', fill=(255, 255, 255),
                               font=ImageFont.load_default(size=48), stroke_width=3, stroke_fill=(0, 0, 0))
        self.assertFalse(doublons.est_retouchee(doublons.verifier(ref, mini(t))))
        # Deux scènes différentes.
        self.assertFalse(doublons.est_retouchee(doublons.verifier(ref, mini(dessin(22, 900, 600)))))

    def test_choix_de_la_photo_a_garder(self):
        def p(i, w, h, taille, chemin='E:\\Photos\\x.jpg', date=None):
            return {'id': i, 'chemin': chemin, 'nom': os.path.basename(chemin), 'dossier': 'E:\\Photos', 'taille': taille,
                    'largeur': w, 'hauteur': h, 'date_prise': date, 'date_fichier': '2024-01-01T00:00:00',
                    'copie': doublons.motif_copie(chemin)}
        # Définition d'abord.
        g = doublons.former_groupe('retouchees', [p(1, 1600, 1200, 300_000), p(2, 4000, 3000, 2_000_000)])
        self.assertEqual([x['id'] for x in g['photos']], [2, 1])
        self.assertEqual([x['garder'] for x in g['photos']], [True, False])
        self.assertEqual(g['photos'][1]['critere'], 'definition')
        self.assertEqual(g['gain_octets'], 300_000)
        # Même définition : pas une copie évidente, même si elle est plus légère.
        g = doublons.former_groupe('identiques', [p(3, 4000, 3000, 2_000_000, 'E:\\Photos\\a - Copie.jpg'),
                                                  p(4, 4000, 3000, 2_000_000, 'E:\\Photos\\a.jpg')])
        self.assertEqual(g['photos'][0]['id'], 4)
        self.assertEqual(g['photos'][0]['critere'], 'copie')
        # Puis le plus gros fichier, puis la prise de vue la plus ancienne.
        g = doublons.former_groupe('retouchees', [p(5, 4000, 3000, 1_000_000), p(6, 4000, 3000, 2_500_000)])
        self.assertEqual(g['photos'][0]['id'], 6)
        g = doublons.former_groupe('retouchees', [p(7, 10, 10, 5, date='2021-06-01T10:00:00'),
                                                  p(8, 10, 10, 5, date='2020-06-01T10:00:00')])
        self.assertEqual(g['photos'][0]['id'], 8)
        self.assertEqual(g['photos'][0]['critere'], 'date')
        # « proches » : la meilleure est indiquée, aucune n'est proposée à la suppression.
        g = doublons.former_groupe('proches', [p(9, 4000, 3000, 1), p(10, 4000, 3000, 2), p(11, 2000, 1500, 3)])
        self.assertTrue(all(x['garder'] for x in g['photos']))
        self.assertEqual([x['meilleure'] for x in g['photos']], [True, False, False])
        self.assertEqual(g['gain_octets'], 0)
        self.assertEqual(g['gain_possible_octets'], 4)


# ---------------------------------------------------------------- calcul complet

class TestDoublons(EssaiDoublons):

    def verifier_groupes_attendus(self, res, proches=True):
        ids = self.ids()
        identiques = self.groupes(res, 'identiques')
        self.assertEqual(len(identiques), 1, identiques)
        self.assertEqual(self.noms(identiques[0]), {'vacances/IMG_0001.jpg', 'vacances/IMG_0001 - Copie.jpg'})
        g = identiques[0]
        self.assertEqual(g['photos'][0]['id'], ids['vacances/IMG_0001.jpg'])      # l'original, pas « - Copie »
        self.assertEqual(g['gain_octets'], (self.photos / 'vacances' / 'IMG_0001.jpg').stat().st_size)
        retouchees = self.groupes(res, 'retouchees')
        self.assertEqual(len(retouchees), 1, retouchees)
        self.assertEqual(self.noms(retouchees[0]), {'WhatsApp Images/IMG-20230501-WA0001.jpg',
                                                    'vacances/IMG_0001_recadree.jpg', 'export/IMG_0001.png',
                                                    'vacances/IMG_0001_claire.jpg', 'vacances/IMG_0001.jpg'})
        # Gardée : la plus grande définition (900 × 675 : l'original, le PNG, l'éclaircie), puis le plus gros fichier.
        self.assertEqual(retouchees[0]['photos'][0]['id'], ids['export/IMG_0001.png'])
        self.assertEqual(retouchees[0]['photos'][0]['critere'], 'taille')
        self.assertEqual(sum(1 for p in retouchees[0]['photos'] if p['garder']), 1)
        petite = next(p for p in retouchees[0]['photos'] if p['id'] == ids['WhatsApp Images/IMG-20230501-WA0001.jpg'])
        self.assertEqual(petite['critere'], 'definition')
        self.assertIn('450 × 337', petite['raison'])
        series = self.groupes(res, 'proches')
        if proches:
            self.assertEqual(len(series), 1, series)
            self.assertEqual(self.noms(series[0]), {'rafale/B1.jpg', 'rafale/B2.jpg', 'rafale/B3.jpg'})
            self.assertTrue(all(p['garder'] for p in series[0]['photos']))
            self.assertEqual(series[0]['gain_octets'], 0)
        # Jamais ensemble : deux scènes différentes, deux images presque noires.
        for g in res['groupes']:
            noms = self.noms(g)
            self.assertFalse({'divers/C.jpg', 'divers/D.jpg'} & noms, g)
            self.assertFalse({'divers/noir0.jpg', 'divers/noir1.jpg'} & noms, g)
        # Une photo proposée à la suppression n'apparaît qu'une fois ; gain total = somme des groupes.
        proposees = [p['id'] for g in res['groupes'] for p in g['photos'] if not p['garder']]
        self.assertEqual(len(proposees), len(set(proposees)))
        self.assertEqual(res['total_gain_octets'], sum(g['gain_octets'] for g in res['groupes']))
        self.assertEqual(res['comptes']['photos_en_trop'], len(proposees))

    def test_trois_niveaux(self):
        self.indexer()
        d = self.doublons()
        etapes = []
        res = d.calculer(progression=lambda phase, fait, total: etapes.append(phase))
        self.verifier_groupes_attendus(res)
        self.assertEqual(res['comptes']['sans_vecteur'], 0)
        self.assertEqual(res['comptes']['unies'], 2)
        for phase in ('empreintes', 'identiques', 'comparaison', 'verification', 'regroupement', 'termine'):
            self.assertIn(phase, etapes)
        e = d.etat()
        self.assertEqual(e['phase'], 'termine')
        self.assertFalse(e['en_cours'])
        self.assertEqual(e['avancement'], 1.0)
        self.assertEqual(e['dernier']['groupes'], {'identiques': 1, 'retouchees': 1, 'proches': 1})
        self.assertIs(d.resultat(), res)
        # Les tables de doublons vivent dans index.db, à côté de celles de la base (inchangées).
        tables = {r[0] for r in self.base._cnx().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({'doublons_empreintes', 'doublons_paires', 'doublons_meta', 'photos'} <= tables)
        self.assertEqual(self.base._cnx().execute('PRAGMA user_version').fetchone()[0], 1)

    def test_relance_sans_rien_recalculer(self):
        self.indexer()
        d = self.doublons()
        r1 = d.calculer()
        self.assertGreater(r1['comptes']['paires_calculees'], 0)
        d2 = self.doublons()                            # autre instance : tout est relu dans index.db
        r2 = d2.calculer()
        self.assertEqual(r2['comptes']['paires_calculees'], 0)
        self.assertEqual([(g['type'], sorted(p['id'] for p in g['photos'])) for g in r1['groupes']],
                         [(g['type'], sorted(p['id'] for p in g['photos'])) for g in r2['groupes']])

    def test_sans_vecteurs(self):
        """Photos analysées par un autre moteur (vecteurs effacés) : empreintes et miniatures suffisent."""
        self.indexer()
        self.base.effacer_vecteurs()
        res = self.doublons().calculer()
        self.assertEqual(res['comptes']['sans_vecteur'], res['comptes']['photos'] - res['comptes']['unies'] - 1)
        self.verifier_groupes_attendus(res, proches=False)
        for g in self.groupes(res, 'proches'):
            self.assertLessEqual(self.noms(g), {'rafale/B1.jpg', 'rafale/B2.jpg', 'rafale/B3.jpg'})

    def test_photos_pas_encore_analysees(self):
        """Sans miniature ni vecteur : seules les copies exactes sont trouvées (SHA-256 des fichiers de même taille)."""
        d = self.base.ajouter_dossier(str(self.photos))
        lignes = []
        for p in sorted(self.photos.rglob('*.*')):
            st = p.stat()
            lignes.append((str(p), st.st_size, st.st_mtime_ns, None))
        self.base.ajouter_photos(d['id'], lignes)
        res = self.doublons().calculer()
        self.assertEqual([g['type'] for g in res['groupes']], ['identiques'])
        self.assertEqual(self.noms(res['groupes'][0]), {'vacances/IMG_0001.jpg', 'vacances/IMG_0001 - Copie.jpg'})
        self.assertEqual(res['comptes']['sans_miniature'], res['comptes']['photos'])

    def test_invalidation(self):
        self.indexer()
        d = self.doublons()
        d.calculer()
        ids = self.ids()
        copie = ids['vacances/IMG_0001 - Copie.jpg']
        cnx = d._cnx()
        self.assertIsNotNone(cnx.execute('SELECT sha256 FROM doublons_empreintes WHERE photo_id = ?',
                                         (copie,)).fetchone()[0])
        avant = cnx.execute('SELECT phash FROM doublons_empreintes WHERE photo_id = ?', (copie,)).fetchone()[0]
        # La copie est remplacée par une autre image : l'inventaire la marque modifiée, l'analyse la refait.
        ecrire(self.photos / 'vacances' / 'IMG_0001 - Copie.jpg', dessin(30))
        os.utime(chemin_systeme(str(self.photos / 'vacances' / 'IMG_0001 - Copie.jpg')),
                 (time.time() + 5, time.time() + 5))
        self.indexer()
        res = d.calculer()
        self.assertEqual(self.groupes(res, 'identiques'), [])
        t, ph, sha, prov = cnx.execute('SELECT taille, phash, sha256, provisoire FROM doublons_empreintes '
                                       'WHERE photo_id = ?', (copie,)).fetchone()
        self.assertEqual(t, (self.photos / 'vacances' / 'IMG_0001 - Copie.jpg').stat().st_size)
        self.assertNotEqual(ph, avant)                  # empreinte refaite sur la nouvelle miniature
        self.assertIsNone(sha)                          # plus aucune photo de même taille : SHA inutile
        self.assertEqual(prov, 0)
        # Une photo disparue est oubliée (empreinte et vérifications).
        whatsapp = ids['WhatsApp Images/IMG-20230501-WA0001.jpg']
        self.assertEqual(cnx.execute('SELECT COUNT(*) FROM doublons_empreintes WHERE photo_id = ?',
                                     (whatsapp,)).fetchone()[0], 1)
        self.base.oublier_photos([whatsapp])
        res = d.calculer()
        self.assertEqual(cnx.execute('SELECT COUNT(*) FROM doublons_empreintes WHERE photo_id = ?',
                                     (whatsapp,)).fetchone()[0], 0)
        self.assertEqual(cnx.execute('SELECT COUNT(*) FROM doublons_paires WHERE a = ? OR b = ?',
                                     (whatsapp, whatsapp)).fetchone()[0], 0)
        self.assertNotIn(whatsapp, [p['id'] for g in res['groupes'] for p in g['photos']])

    def test_dossier_injoignable(self):
        """Les photos d'un disque débranché ne sont pas proposées (la copie gardée serait peut-être inaccessible)."""
        self.indexer()
        d = self.base.dossiers()[0]
        self.base.marquer_dossier(d['id'], False)
        res = self.doublons().calculer()
        self.assertEqual(res['groupes'], [])
        self.assertEqual(res['comptes']['photos'], 0)

    def test_annulation(self):
        self.indexer()
        d = self.doublons()
        arret = threading.Event()

        def rappel(phase, fait, total):
            if phase == 'verification':
                arret.set()
        with self.assertRaises(Annule):
            d.calculer(progression=rappel, arret=arret)
        self.assertEqual(d.etat()['phase'], 'annule')
        self.assertIsNone(d.resultat())
        res = d.calculer()                              # repart et finit (les empreintes sont gardées)
        self.verifier_groupes_attendus(res)

    def test_arriere_plan(self):
        self.indexer()
        d = self.doublons()
        d.demarrer()
        self.assertTrue(d.attendre(60))
        self.assertEqual(d.etat()['phase'], 'termine')
        self.verifier_groupes_attendus(d.resultat())
        # Annulation d'un calcul en arrière-plan : il s'arrête vite.
        d.oublier()
        d.demarrer()
        d.annuler()
        self.assertTrue(d.attendre(30))
        self.assertIn(d.etat()['phase'], ('annule', 'termine'))

    def test_etat_depuis_un_autre_fil(self):
        """etat() répond pendant un calcul, depuis un autre fil (le serveur) ; un second calcul attend le premier."""
        self.indexer()
        d = self.doublons()
        en_verification = threading.Event()
        reprise = threading.Event()

        def rappel(phase, fait, total):
            if phase == 'verification' and not en_verification.is_set():
                en_verification.set()
                reprise.wait(10)
        fil = threading.Thread(target=d.calculer, kwargs={'progression': rappel})
        fil.start()
        try:
            self.assertTrue(en_verification.wait(30))
            e = d.etat()
            self.assertTrue(e['en_cours'])
            self.assertEqual(e['phase'], 'verification')
            self.assertGreater(e['avancement'], 0.5)
            self.assertLess(e['avancement'], 1.0)
        finally:
            reprise.set()
            fil.join(60)
        self.assertEqual(d.etat()['phase'], 'termine')


# ---------------------------------------------------------------- performance

class TestPerformance(unittest.TestCase):
    """Photothèque synthétique de 3 000 photos (miniatures dessinées, vecteurs groupés par scènes, rafales et
    retouches) : le premier calcul et la relance restent rapides. Mesures à 10 000 et 50 000 photos :
    outils/calibrer_doublons.py --perf, chiffres dans outils/DOUBLONS.md."""
    N = 3000

    def test_performance(self):
        tmp = Path(tempfile.mkdtemp(prefix='myphotos-doublons-perf-'))
        try:
            base = Base(tmp / 'index.db', tmp / 'miniatures.db')
            (tmp / 'photos').mkdir()
            d = base.ajouter_dossier(str(tmp / 'photos'))
            rnd = np.random.default_rng(3)
            dim = 64
            scenes = modele.normaliser(rnd.standard_normal((150, dim)))
            lignes, analyses = [], []
            k = 0
            attendues = 0
            while k < self.N:
                im = Image.fromarray((rnd.random((3, 4, 3)) * 255).astype(np.uint8)).resize((240, 180),
                                                                                           Image.Resampling.BICUBIC)
                dr = ImageDraw.Draw(im)
                for _ in range(8):
                    x, y, r = (int(v) for v in rnd.integers(0, 200, 3))
                    dr.ellipse((x, y, x + r // 3 + 5, y + r // 4 + 5), fill=tuple(int(v) for v in rnd.integers(0, 256, 3)))
                v0 = scenes[int(rnd.integers(0, len(scenes)))] + rnd.standard_normal(dim) * 0.1
                membres = [im]
                if rnd.random() < 0.05:                      # retouche : la même, réduite
                    membres.append(im.resize((160, 120)).resize((240, 180)))
                    attendues += 1
                for j, m in enumerate(membres):
                    t = io.BytesIO()
                    m.save(t, 'JPEG', quality=80)
                    v = modele.normaliser(v0 + rnd.standard_normal(dim) * 0.02)
                    ch = str(tmp / 'photos' / f'p{k:05d}.jpg')
                    taille = int(rnd.integers(1_000_000, 9_000_000))
                    lignes.append((ch, taille, 1, None))
                    analyses.append({'taille': taille, 'modifie': 1, 'largeur': 4000, 'hauteur': 3000 - j,
                                     'date_prise': None, 'lat': None, 'lon': None, 'miniature': t.getvalue(),
                                     'vecteur': v.astype(np.float16).tobytes()})
                    k += 1
            base.ajouter_photos(d['id'], lignes)
            ids = {ch: pid for ch, (pid, _, _) in base.connus(d['id']).items()}
            for (ch, *_), a in zip(lignes, analyses):
                a['id'] = ids[ch]
            base.enregistrer(analyses, [], CLE)
            dbl = Doublons(base, CLE)
            t0 = time.perf_counter()
            r1 = dbl.calculer()
            premier = time.perf_counter() - t0
            t0 = time.perf_counter()
            r2 = dbl.calculer()
            relance = time.perf_counter() - t0
            dbl.fermer()
            base.fermer()
            self.assertGreaterEqual(r1['comptes']['groupes']['retouchees'], attendues * 0.9)
            self.assertEqual(r2['comptes']['paires_calculees'], 0)
            # Larges marges (PC lents) : de l'ordre de 2 s et 0,3 s sur un PC de bureau récent.
            self.assertLess(premier, 30, f'premier calcul : {premier:.1f} s')
            self.assertLess(relance, 6, f'relance : {relance:.1f} s')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
