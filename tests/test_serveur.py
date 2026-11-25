"""Essais du serveur local de MyPhotos (myphotos/serveur.py), par de vraies requêtes HTTP.

Le serveur tourne dans un sous-processus, sur un port d'essai, avec ses propres données :

    set MYPHOTOS_ESSAI_UI=C:\\chemin\\essai-ui              (défaut : %TEMP%\\myphotos-essai-ui)
    set MYPHOTOS_ESSAI_PHOTOS=C:\\chemin\\images-publiques  (facultatif : essai de bout en bout)
    .venv\\Scripts\\python -m unittest tests.test_serveur -v

Les moteurs déjà téléchargés (donnees/modele, ou MYPHOTOS_MODELES) servent tels quels : aucun téléchargement.
Les serveurs d'essai ne proposent aucun dossier : les dossiers d'images de cet ordinateur ne sont jamais parcourus.
Aucune photo personnelle : l'essai de bout en bout prend des images publiques (Wikimedia Commons), nommées
d'après ce qu'elles montrent (chien-1.jpg, plage-2.jpg…). Aucune fenêtre ne s'ouvre : « Ouvrir » et « Afficher
dans le dossier » sont essayés dans ce processus, les appels à Windows étant simulés.
"""
import base64
import http.client
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / 'tests'))

import processus_essai  # noqa: E402

PORT = int(os.environ.get('MYPHOTOS_ESSAI_PORT') or 7896)
HOTE = f'127.0.0.1:{PORT}'
DONNEES = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
MODELES = Path(os.environ.get('MYPHOTOS_MODELES') or RACINE / 'donnees' / 'modele')
PHOTOS = os.environ.get('MYPHOTOS_ESSAI_PHOTOS')
INCONNU = 987654321
SANS_FENETRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


DONNEES_PAR_PORT = {}                    # port -> données du serveur d'essai (lancer_serveur) : son jeton d'accès


def jeton_essai(port=PORT):
    """Jeton d'accès du serveur d'essai de ce port (fichier « jeton » de ses données, réécrit à chaque lancement)."""
    dossier = DONNEES_PAR_PORT.get(port)
    try:
        return (Path(dossier) / 'jeton').read_text(encoding='ascii').strip() if dossier else None
    except OSError:
        return None


def requete(methode, chemin, corps=None, entetes=None, brut=None, hote=HOTE, port=PORT, delai=60, jeton=True):
    """(statut, en-têtes en minuscules, corps). Host et tous les en-têtes sont exactement ceux donnés, plus le cookie
    du jeton d'accès comme la page (jeton=True : celui du serveur d'essai de ce port ; une chaîne : celle-ci ;
    False : aucun cookie). Un en-tête Cookie donné dans `entetes` remplace celui-ci."""
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=delai)
    e = {'Host': hote}
    valeur = jeton_essai(port) if jeton is True else jeton
    if valeur:
        e['Cookie'] = f'myphotos-{port}={valeur}'
    if corps is not None:
        brut = json.dumps(corps).encode('utf-8')
        e['Content-Type'] = 'application/json'
    e.update(entetes or {})
    try:
        c.request(methode, chemin, body=brut, headers=e)
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def api(chemin, corps=None, **kw):
    """Requête comme la page : GET simple, ou POST JSON avec X-MyPhotos et la même origine."""
    if corps is None:
        statut, _, d = requete('GET', chemin, **kw)
    else:
        origine = f'http://{kw.get("hote", HOTE)}'
        statut, _, d = requete('POST', chemin, corps,
                               {'X-MyPhotos': '1', 'Origin': origine, 'Sec-Fetch-Site': 'same-origin'}, **kw)
    return statut, json.loads(d or b'null')


def port_libre(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def arreter_essai(proc):
    """Filet d'arrêt d'un serveur d'essai : par SON Job Object (tests/processus_essai.py), qui ne contient que les
    processus lancés par l'essai ; job fermé ensuite. Jamais par identifiant retrouvé, ni par nom de programme."""
    if os.name == 'nt':
        processus_essai.arreter(proc)
        return
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=30)


def lancer_serveur(donnees, port=PORT, neutre=False, **variables):
    """neutre=True : aucun dossier proposé (les dossiers d'images de cet ordinateur ne sont jamais parcourus)."""
    env = dict(os.environ, MYPHOTOS_PORT=str(port), MYPHOTOS_SANS_PAGE='1', MYPHOTOS_DOSSIER=str(donnees),
               MYPHOTOS_MODELES=str(MODELES), PYTHONIOENCODING='utf-8')
    env.pop('MYPHOTOS_TELECHARGEMENT_FACTICE', None)
    env.update(variables)
    Path(donnees).mkdir(parents=True, exist_ok=True)
    DONNEES_PAR_PORT[port] = Path(donnees)
    sortie = open(Path(donnees) / 'essai-serveur.err', 'ab')
    lancement = ['-c', 'import sys; import myphotos.serveur as s; s.dossiers_proposes = lambda: []; '
                       's.dossier_images = lambda: None; sys.exit(s.main())'] if neutre else ['-m', 'myphotos']
    arguments = [sys.executable, '-X', 'faulthandler', *lancement]
    options = dict(cwd=str(RACINE), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sortie,
                   creationflags=SANS_FENETRE)
    # Dans un Job Object à l'essai : le vrai Python (enfant du lanceur d'un environnement virtuel) y entre aussi.
    if os.name == 'nt':
        proc = processus_essai.popen(processus_essai.Job(), arguments, **options)
    else:
        proc = subprocess.Popen(arguments, **options)
    proc.sortie = sortie
    return proc


def attendre_serveur(proc, port=PORT, delai=40):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if proc.poll() is not None:
            raise RuntimeError(f'le serveur s\'est arrêté au démarrage (code {proc.returncode})')
        try:
            if requete('GET', '/api/etat', hote=f'127.0.0.1:{port}', port=port, delai=5)[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.3)
    raise RuntimeError('le serveur ne répond pas')


def attendre(condition, delai, pas=0.5):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        v = condition()
        if v:
            return v
        time.sleep(pas)
    return condition()


def icone_64():
    """Image de 64 px de construction/MyPhotos.ico (chaque taille y est enregistrée en PNG)."""
    data = (RACINE / 'construction' / 'MyPhotos.ico').read_bytes()
    for i in range(struct.unpack_from('<H', data, 4)[0]):
        largeur, _, _, _, _, _, taille, debut = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
        if largeur == 64:
            return data[debut:debut + taille]
    raise AssertionError('MyPhotos.ico sans image de 64 px')


class EssaiServeur(unittest.TestCase):
    proc = None

    @classmethod
    def setUpClass(cls):
        if not port_libre(PORT):
            raise unittest.SkipTest(f'le port {PORT} est déjà pris')
        cls.proc = lancer_serveur(DONNEES, neutre=True)     # jamais les dossiers d'images de cet ordinateur
        try:
            attendre_serveur(cls.proc)
        except Exception:
            arreter_essai(cls.proc)
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.proc is None:
            return
        try:
            # Arrêt propre demandé comme le ferait la page ; à défaut, par le PID.
            if cls.proc.poll() is None:
                api('/api/quitter', {})
                cls.proc.wait(timeout=90)
        except Exception:
            pass
        finally:
            arreter_essai(cls.proc)
            cls.proc.sortie.close()

    def vivant(self):
        self.assertIsNone(self.proc.poll(), 'le serveur s\'est arrêté (voir essai-serveur.err)')


class TestSecurite(EssaiServeur):

    def test_page_et_en_tetes(self):
        statut, e, corps = requete('GET', '/')
        self.assertEqual(statut, 200)
        self.assertTrue(e['content-type'].startswith('text/html'))
        csp = e['content-security-policy']
        self.assertIn("default-src 'none'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
        page = corps.decode('utf-8')
        self.assertIn(f'<script nonce="{nonce}">', page)
        self.assertIn(f'<style nonce="{nonce}">', page)
        self.assertNotIn('<script>', page)
        self.assertIn('<title>MyPhotos</title>', page)
        for externe in ('src="http', "src='http", 'href="http', "href='http", 'url(http', '@import'):
            self.assertNotIn(externe, page)
        self.assertEqual(e['x-content-type-options'], 'nosniff')
        self.assertEqual(e['referrer-policy'], 'no-referrer')
        self.assertEqual(e['cache-control'], 'no-store')
        # Un nonce neuf à chaque page.
        autre = requete('GET', '/')[1]['content-security-policy']
        self.assertNotEqual(csp, autre)

    def test_api_sans_cache(self):
        statut, e, corps = requete('GET', '/api/etat')
        self.assertEqual(statut, 200)
        self.assertEqual(e['cache-control'], 'no-store')
        self.assertTrue(e['content-type'].startswith('application/json'))
        self.assertEqual(e['x-content-type-options'], 'nosniff')
        self.assertIn("default-src 'none'", e['content-security-policy'])

    def test_hote_etranger(self):
        for hote in ('evil.example', 'evil.example:7896', f'127.0.0.1:{PORT}.evil.example', '127.0.0.1',
                     f'127.0.0.2:{PORT}', f'[::1]:{PORT}', 'localhost', ''):
            for chemin in ('/', '/api/etat', '/miniature/1'):
                with self.subTest(hote=hote, chemin=chemin):
                    self.assertEqual(requete('GET', chemin, hote=hote)[0], 403)
            statut = requete('POST', '/api/analyse/pause', {}, {'X-MyPhotos': '1'}, hote=hote)[0]
            self.assertEqual(statut, 403)
        self.assertEqual(requete('GET', '/api/etat', hote=f'localhost:{PORT}')[0], 200)
        self.assertEqual(requete('GET', '/api/etat', hote=f'LOCALHOST:{PORT}')[0], 200)

    def test_post_sans_en_tete(self):
        for entetes in ({}, {'X-MyPhotos': '0'}, {'X-MyPhotos': 'oui'}, {'X-Requested-With': 'XMLHttpRequest'}):
            with self.subTest(entetes=entetes):
                statut, _, d = requete('POST', '/api/moteur/annuler', {}, entetes)
                self.assertEqual(statut, 403)
                self.assertIn('erreur', json.loads(d))
        self.assertEqual(requete('POST', '/api/moteur/annuler', {}, {'X-MyPhotos': '1'})[0], 200)

    def test_origine_etrangere(self):
        for origine in ('http://evil.example', 'null', f'http://localhost:{PORT}', f'https://127.0.0.1:{PORT}',
                        f'http://127.0.0.1:{PORT + 1}', 'http://127.0.0.1'):
            with self.subTest(origine=origine):
                self.assertEqual(requete('POST', '/api/moteur/annuler', {},
                                         {'X-MyPhotos': '1', 'Origin': origine})[0], 403)
        self.assertEqual(requete('POST', '/api/moteur/annuler', {},
                                 {'X-MyPhotos': '1', 'Origin': f'http://{HOTE}'})[0], 200)

    def test_sec_fetch_site(self):
        for site in ('cross-site', 'same-site', 'CROSS-SITE'):
            for chemin in ('/api/etat', '/api/photos', '/miniature/1', '/photo/1', '/api/photo/1'):
                with self.subTest(site=site, chemin=chemin):
                    self.assertEqual(requete('GET', chemin, entetes={'Sec-Fetch-Site': site})[0], 403)
            self.assertEqual(requete('POST', '/api/moteur/annuler', {},
                                     {'X-MyPhotos': '1', 'Sec-Fetch-Site': site})[0], 403)
        for site in ('same-origin', 'none'):
            self.assertEqual(requete('GET', '/api/etat', entetes={'Sec-Fetch-Site': site})[0], 200)
        # La page elle-même peut être ouverte depuis un lien.
        self.assertEqual(requete('GET', '/', entetes={'Sec-Fetch-Site': 'cross-site'})[0], 200)

    def test_identifiant_inexistant(self):
        for chemin in (f'/api/photo/{INCONNU}', f'/miniature/{INCONNU}', f'/photo/{INCONNU}'):
            with self.subTest(chemin=chemin):
                statut, _, d = requete('GET', chemin)
                self.assertEqual(statut, 404)
                self.assertIn('erreur', json.loads(d))
        for valeur in (INCONNU, str(INCONNU), 0, '0', -1, '-1', 'abc', 1.5, '1.5', True, None, [1], {'id': 1},
                       '12345678901234567890', '1; calc', ' 1'):
            for action in ('/api/ouvrir', '/api/montrer'):
                with self.subTest(action=action, valeur=valeur):
                    statut, d = api(action, {'id': valeur})
                    self.assertEqual(statut, 404)
                    self.assertIn('erreur', d)
        self.assertEqual(api('/api/ouvrir', {})[0], 404)
        self.assertEqual(api('/api/dossiers/retirer', {'id': INCONNU})[0], 404)
        self.assertEqual(api('/api/dossiers/retirer', {'id': 'E:\\Albums'})[0], 404)

    def test_chemin_dans_url(self):
        for chemin in ('/photo/..%2F..%2FWindows%2Fwin.ini', '/photo/C:/Windows/win.ini', '/photo/C:%5CWindows%5Cwin.ini',
                       '/miniature/../../myphotos/config.py', '/../myphotos/config.py', '/page/index.html',
                       '/myphotos/serveur.py', '/api/photo/1/../../config.py', '/photo/1%00.jpg', '/photo/1.jpg',
                       '/%2e%2e/%2e%2e/Windows/win.ini', '/photo/%5C%5Cserveur%5Cpartage%5Cx.jpg', '/photo/',
                       '/miniature/01', '/photo/+1', '/index.html/../lancer.py', '/api/../lancer.py',
                       '//127.0.0.1/c$/Windows/win.ini', '/C:/Windows/win.ini', '/favicon.ico'):
            with self.subTest(chemin=chemin):
                statut, e, d = requete('GET', chemin)
                self.assertEqual(statut, 404)
                self.assertTrue(e['content-type'].startswith('application/json'))
                self.assertNotIn(b'[fonts]', d)
                self.assertNotIn(b'import', d)
        # Un chemin donné à la place d'un identifiant ne sert jamais à ouvrir quoi que ce soit.
        self.assertEqual(api('/api/ouvrir', {'chemin': 'C:\\Windows\\notepad.exe'})[0], 404)
        self.assertEqual(api('/api/montrer', {'id': 'C:\\Windows\\win.ini'})[0], 404)

    def test_corps_refuses(self):
        en_tetes = {'X-MyPhotos': '1', 'Content-Type': 'application/json'}
        self.assertEqual(requete('POST', '/api/assistant', entetes=en_tetes, brut=b'{"a":"' + b'x' * 70000 + b'"}')[0], 413)
        self.assertEqual(requete('POST', '/api/assistant', entetes=en_tetes, brut=b'{pas du json')[0], 400)
        self.assertEqual(requete('POST', '/api/assistant', entetes=en_tetes, brut=b'[1, 2]')[0], 400)
        self.assertEqual(requete('POST', '/api/assistant', entetes=en_tetes, brut=b'\xff\xfe')[0], 400)
        self.assertEqual(requete('POST', '/api/assistant', entetes={'X-MyPhotos': '1', 'Content-Type': 'text/plain'},
                                 brut=b'{"etape": 1}')[0], 415)
        self.assertEqual(api('/api/assistant', {'etape': 'n\'importe quoi'})[0], 400)
        self.assertEqual(api('/api/inconnue', {})[0], 404)
        self.assertEqual(requete('GET', '/api/inconnue')[0], 404)
        self.assertNotEqual(requete('PUT', '/api/etat', brut=b'{}')[0], 200)
        self.assertNotEqual(requete('DELETE', '/api/etat')[0], 200)
        self.assertEqual(requete('GET', '/api/photos?n=abc')[0], 400)
        self.assertEqual(requete('GET', '/api/photos?debut=-5')[0], 400)
        self.assertEqual(requete('GET', '/api/recherche?q=' + 'a' * 1200)[0], 400)
        self.vivant()

    def test_moteur_refuse(self):
        for corps in ({'cle': 'inconnu'}, {'cle': None}, {'cle': ['base']}, {'cle': 'base', 'vision': 'fp64'},
                      {'cle': '../base'}):
            with self.subTest(corps=corps):
                statut, d = api('/api/moteur/telecharger', corps)
                self.assertEqual(statut, 400)
                self.assertIn('erreur', d)
                self.assertEqual(api('/api/moteur/choisir', corps)[0], 400)
        statut, d = api('/api/dossiers/ajouter', {'chemin': str(DONNEES / 'dossier-qui-n-existe-pas')})
        self.assertEqual(statut, 409)
        self.assertIn('introuvable', d['erreur'])
        self.assertEqual(api('/api/dossiers/ajouter', {'chemin': ''})[0], 400)
        self.assertEqual(api('/api/dossiers/ajouter', {'chemin': 12})[0], 400)

    # ---------------------------------------------------------------- relecture de sécurité du 24/11/2026

    def test_autre_site_hors_navigation(self):
        """Un autre site peut ouvrir la page dans un onglet, rien de plus : ni fetch « no-cors », ni cadre, ni feuille
        de style, ni image (avant : la page lui était servie, ce qui comptait comme une activité)."""
        for mode, dest in (('no-cors', 'empty'), ('navigate', 'iframe'), ('no-cors', 'style'), ('no-cors', 'image'),
                           ('no-cors', 'script'), ('cors', 'empty')):
            for site in ('cross-site', 'same-site'):
                for chemin in ('/', '/index.html', '/favicon.ico', '/inconnu?x=1'):
                    with self.subTest(mode=mode, dest=dest, site=site, chemin=chemin):
                        statut, e, _ = requete('GET', chemin, entetes={'Sec-Fetch-Site': site, 'Sec-Fetch-Mode': mode,
                                                                       'Sec-Fetch-Dest': dest})
                        self.assertEqual(statut, 403)
                        self.assertTrue(e['content-type'].startswith('application/json'))
        for site in ('cross-site', 'same-site', 'none', 'same-origin'):
            statut, e, corps = requete('GET', '/', entetes={'Sec-Fetch-Site': site, 'Sec-Fetch-Mode': 'navigate',
                                                            'Sec-Fetch-Dest': 'document', 'Sec-Fetch-User': '?1'})
            self.assertEqual(statut, 200, site)
            self.assertIn(b'<title>MyPhotos</title>', corps)

    def test_requetes_malformees_sans_trace(self):
        """Requêtes piégées : un refus simple (400), jamais une « erreur inattendue » avec sa trace dans le journal
        (avant : 500 et une trace de 1 Ko par requête, qu'un autre site pouvait répéter pour noyer le journal)."""
        journal = DONNEES / 'journal.log'
        avant = journal.stat().st_size if journal.exists() else 0
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        q = '&'.join(f'a{i}=1' for i in range(30))
        self.assertEqual(requete('GET', f'/?{q}', entetes=navigation)[0], 400)
        self.assertEqual(requete('GET', f'/api/photos?{q}')[0], 400)
        self.assertEqual(requete('GET', 'http://[x/api/etat')[0], 400)            # forme absolue, hôte illisible
        imbrique = b'{"ids":' + b'[' * 20000 + b']' * 20000 + b', "confirme": true}'      # moins de 64 Ko
        self.assertEqual(requete('POST', '/api/supprimer', brut=imbrique,
                                 entetes={'X-MyPhotos': '1', 'Content-Type': 'application/json'})[0], 400)
        self.vivant()
        ajout = journal.read_bytes()[avant:].decode('utf-8', 'replace') if journal.exists() else ''
        self.assertNotIn('Traceback', ajout)
        self.assertNotIn('Max number of fields', ajout)

    def test_erreurs_du_protocole(self):
        """Méthode inconnue, demande préalable CORS : JSON et en-têtes de sécurité, jamais de CORS accordé (avant : la
        page d'erreur HTML de Python, sans CSP ni nosniff ni X-Frame-Options)."""
        for methode in ('OPTIONS', 'PUT', 'DELETE', 'PATCH', 'TRACE'):
            with self.subTest(methode=methode):
                statut, e, corps = requete(methode, '/api/quitter',
                                           entetes={'Origin': 'http://evil.example',
                                                    'Access-Control-Request-Method': 'POST',
                                                    'Access-Control-Request-Headers': 'x-myphotos, content-type'})
                self.assertEqual(statut, 501)
                self.assertTrue(e['content-type'].startswith('application/json'))
                self.assertEqual(json.loads(corps), {'erreur': 'Requête refusée.'})
                for k in ('x-content-type-options', 'content-security-policy', 'x-frame-options',
                          'cross-origin-resource-policy', 'referrer-policy'):
                    self.assertIn(k, e)
                self.assertFalse(any(k.startswith('access-control-') for k in e))
        self.vivant()


class TestJeton(EssaiServeur):
    """0.4.0 : jeton d'accès exigé par TOUTE requête (page, API, miniatures, photos, routes inconnues ou à venir),
    contrôlé à un seul endroit du serveur (Gestionnaire._acces). Problème connu de la 0.3.0 : une personne connectée
    en même temps sur un autre compte Windows du PC affichait la page de MyPhotos en tapant son adresse locale."""

    REFUS = 'Ouvrez MyPhotos depuis son raccourci.'
    GET = ('/', '/index.html', '/api/etat', '/api/photos', '/api/recherche?q=chien', '/api/dossiers',
           '/api/materiel', '/api/copie', '/api/doublons', f'/miniature/{INCONNU}', f'/photo/{INCONNU}',
           f'/api/photo/{INCONNU}', '/favicon.ico', '/inconnu', '/api/maj', '/api/maj/etat')
    POST = ('/api/quitter', '/api/analyse/pause', '/api/dossiers/choisir', '/api/supprimer', '/api/assistant',
            '/api/inconnue', '/api/maj/installer')
    NAVIGATION = {'Sec-Fetch-Site': 'none', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}

    def faux_jetons(self):
        j = jeton_essai()
        self.assertRegex(j or '', r'^[A-Za-z0-9_-]{43}$')                 # 256 bits, sans caractère à échapper
        autre = ('A' if j[0] != 'A' else 'B') + j[1:]
        return (False, '', 'x', autre, j[:-1], j + 'x', j.lower() if j != j.lower() else j.upper(), f'"{autre}"',
                j + ' ' + j)

    def assert_refus(self, statut, e, corps, chemin):
        self.assertEqual(statut, 403, chemin)
        self.assertNotIn('set-cookie', e)
        self.assertEqual(e['cache-control'], 'no-store')
        self.assertIn("default-src 'none'", e['content-security-policy'])
        if chemin.startswith('/api/'):
            self.assertEqual(json.loads(corps), {'erreur': self.REFUS, 'jeton': 'requis'}, chemin)
        else:
            self.assertTrue(e['content-type'].startswith('text/html'), chemin)
            from myphotos import config
            texte = corps.decode('utf-8')
            self.assertIn(self.REFUS, texte)
            self.assertLess(len(corps), 300)                                # une petite page, rien d'autre
            for secret in ('<script', 'version', 'compte', 'jeton', config.VERSION, str(DONNEES), 'etat'):
                self.assertNotIn(secret.lower(), texte.lower())

    def test_sans_jeton_tout_est_refuse(self):
        for jeton in self.faux_jetons():
            for chemin in self.GET:
                with self.subTest(jeton=jeton, chemin=chemin):
                    self.assert_refus(*requete('GET', chemin, entetes=self.NAVIGATION if chemin == '/' else None,
                                               jeton=jeton), chemin.split('?')[0])
            for chemin in self.POST:
                with self.subTest(jeton=jeton, chemin=chemin):
                    statut, e, corps = requete('POST', chemin, {'ids': [1], 'confirme': True},
                                               {'X-MyPhotos': '1', 'Origin': f'http://{HOTE}',
                                                'Sec-Fetch-Site': 'same-origin'}, jeton=jeton)
                    self.assert_refus(statut, e, corps, chemin)
        self.vivant()                                                       # /api/quitter sans jeton : rien
        # Le bon jeton sous le nom de cookie d'un AUTRE port (une autre MyPhotos) ne vaut rien ici, et inversement
        # les cookies des autres programmes de 127.0.0.1 ne gênent pas.
        j = jeton_essai()
        self.assert_refus(*requete('GET', '/api/etat', entetes={'Cookie': f'myphotos-{PORT + 1}={j}'}), '/api/etat')
        self.assertEqual(requete('GET', '/api/etat', entetes={
            'Cookie': f'session=abc; myphotos-{PORT + 1}=x; myphotos-{PORT}={j}; autre="y"'})[0], 200)
        # Le jeton dans l'adresse n'ouvre rien d'autre que la page (ni l'API, ni les photos).
        for chemin in ('/api/etat', f'/miniature/{INCONNU}', '/api/photos'):
            with self.subTest(chemin=chemin):
                self.assert_refus(*requete('GET', f'{chemin}?jeton={j}', jeton=False), chemin)
        self.assert_refus(*requete('POST', f'/?jeton={j}', {}, {'X-MyPhotos': '1'}, jeton=False), '/')
        self.vivant()

    def test_adresse_d_entree(self):
        """La fenêtre, Edge, le navigateur et la relance ouvrent « /?jeton=… » : cookie HttpOnly, SameSite=Strict,
        Path=/, sans date (le temps de la séance), puis « / » sans le jeton dans l'adresse."""
        j = jeton_essai()
        for page in ('/', '/index.html'):
            with self.subTest(page=page):
                statut, e, corps = requete('GET', f'{page}?jeton={j}', entetes=self.NAVIGATION, jeton=False)
                self.assertEqual((statut, e['location'], corps), (303, '/', b''))
                self.assertEqual(e['cache-control'], 'no-store')
                self.assertEqual(e['referrer-policy'], 'no-referrer')
                morceaux = [m.strip() for m in e['set-cookie'].split(';')]
                self.assertEqual(morceaux[0], f'myphotos-{PORT}={j}')
                self.assertEqual(set(morceaux[1:]), {'Path=/', 'HttpOnly', 'SameSite=Strict'})
                biscuit = morceaux[0].split('=', 1)[1]
                statut, _, corps = requete('GET', '/', entetes=self.NAVIGATION, jeton=biscuit)
                self.assertEqual(statut, 200)
                self.assertIn(b'<title>MyPhotos</title>', corps)
                self.assertNotIn(j.encode(), corps)                         # jamais dans la page
        # Mauvais jeton dans l'adresse : la petite page, sans cookie.
        for faux in self.faux_jetons()[1:]:
            with self.subTest(faux=faux):
                self.assert_refus(*requete('GET', '/?' + urllib.parse.urlencode({'jeton': faux}),
                                           entetes=self.NAVIGATION, jeton=False), '/')
        # Adresse d'entrée périmée (historique) avec un cookie valable : « / », sans nouveau cookie.
        statut, e, _ = requete('GET', '/?jeton=perime', entetes=self.NAVIGATION)
        self.assertEqual((statut, e['location']), (303, '/'))
        self.assertNotIn('set-cookie', e)
        # Un autre site peut mener à l'adresse d'entrée, mais sans le jeton il n'obtient que la petite page.
        self.assert_refus(*requete('GET', '/?jeton=devine', jeton=False, entetes=dict(
            self.NAVIGATION, **{'Sec-Fetch-Site': 'cross-site'})), '/')
        self.vivant()

    def test_jamais_dans_le_journal(self):
        j = jeton_essai()
        journal = DONNEES / 'journal.log'
        avant = journal.stat().st_size if journal.exists() else 0
        requete('GET', f'/?jeton={j}', entetes=self.NAVIGATION, jeton=False)
        requete('GET', f'/?jeton={j}&' + '&'.join(f'a{i}=1' for i in range(30)), jeton=False)   # refus simple (400)
        requete('GET', f'/api/recherche?q=chien&jeton={j}')
        requete('POST', '/api/assistant', entetes={'X-MyPhotos': '1', 'Content-Type': 'application/json'},
                brut=b'{pas du json')
        requete('GET', f'/photo/{INCONNU}?jeton={j}', jeton=j)
        self.vivant()
        ajout = journal.read_bytes()[avant:].decode('utf-8', 'replace') if journal.exists() else ''
        self.assertNotIn(j, ajout)
        self.assertNotIn(j, journal.read_text(encoding='utf-8', errors='replace') if journal.exists() else '')

    def test_simple_connexion_tcp(self):
        """L'outil local qui regarde si MyPhotos tourne ouvre une connexion au port, sans requête : toujours possible,
        sans erreur dans le journal."""
        for _ in range(3):
            with socket.create_connection(('127.0.0.1', PORT), timeout=5):
                pass
        self.vivant()
        self.assertEqual(api('/api/etat')[0], 200)

    def test_etat_instance(self):
        """Relance (serveur.etat_instance) : avec le jeton des données, l'état ; sans lui, « jeton_refuse » (MyPhotos
        d'un autre compte Windows : message, rien n'est ouvert), sans rien de son état."""
        from myphotos import serveur
        e = serveur.etat_instance(PORT, jeton_essai())
        self.assertEqual((e['nom'], e['acces_par_jeton']), ('MyPhotos', True))
        for faux in ('', 'faux', jeton_essai() + 'x'):
            self.assertEqual(serveur.etat_instance(PORT, faux), {'nom': 'MyPhotos', 'jeton_refuse': True})
        with mock.patch.object(serveur.config, 'JETON', DONNEES / 'jeton'):
            self.assertEqual(serveur.etat_instance(PORT)['acces_par_jeton'], True)   # lu dans les données
        self.assertIsNone(serveur.etat_instance(PORT + 37, jeton_essai()))          # personne sur ce port


class TestJetonFonctions(unittest.TestCase):
    """Jeton d'accès, sans serveur : fichier écrit d'un coup, relu avec prudence, comparé en temps constant, masqué
    dans le journal."""

    def test_creer_et_lire(self):
        from myphotos import serveur
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / 'sous-dossier' / 'jeton'
            j = serveur.creer_jeton(chemin)
            self.assertRegex(j, r'^[A-Za-z0-9_-]{43}$')
            self.assertEqual(serveur.lire_jeton(chemin), j)
            self.assertEqual(sorted(p.name for p in chemin.parent.iterdir()), ['jeton'])
            self.assertNotEqual(serveur.creer_jeton(chemin), j)                 # un nouveau à chaque lancement
            for abime in (b'', b'court', b'\xff\xfe' + b'a' * 40, b'a' * 40 + b'<script>', b'a b' * 20):
                chemin.write_bytes(abime)
                self.assertIsNone(serveur.lire_jeton(chemin), abime)
            self.assertIsNone(serveur.lire_jeton(Path(d) / 'absent'))
            # Dossier de données impossible à écrire : MyPhotos marche quand même (seule la relance ne rouvrira rien).
            with mock.patch.object(Path, 'write_text', side_effect=PermissionError('refusé')):
                self.assertRegex(serveur.creer_jeton(Path(d) / 'autre'), r'^[A-Za-z0-9_-]{43}$')

    def test_comparaison(self):
        from myphotos import serveur
        j = 'a' * 43
        self.assertTrue(serveur.jeton_egal(j, j))
        for donne in ('a' * 42, 'a' * 44, 'b' + 'a' * 42, '', None, 12, 'é' * 43, '\udcff' * 43):
            self.assertFalse(serveur.jeton_egal(donne, j), donne)
        self.assertFalse(serveur.jeton_egal('', ''))
        self.assertFalse(serveur.jeton_egal(j, None))
        self.assertEqual(serveur.adresse_entree(7796, j), f'http://127.0.0.1:7796/?jeton={j}')
        self.assertEqual(serveur.nom_cookie(7796), 'myphotos-7796')

    def test_journal_masque_le_jeton(self):
        from myphotos import serveur
        j = 'Zx9_-' * 9
        forme = serveur._FormatSansJeton('%(message)s')
        for message in (f'chargement de http://127.0.0.1:7796/?jeton={j}', f'Cookie: myphotos-7796={j}; autre=1'):
            r = logging.LogRecord('pywebview', logging.DEBUG, '', 0, message, None, None)
            self.assertNotIn(j, forme.format(r))
        try:
            raise ValueError(f'/?jeton={j}')
        except ValueError:
            import sys as _sys
            r = logging.LogRecord('myphotos', logging.ERROR, '', 0, 'erreur', None, _sys.exc_info())
        self.assertNotIn(j, forme.format(r))


class TestSelectionHttp(EssaiServeur):
    """Routes de la sélection (copier, copier dans un dossier, mettre à la Corbeille) : mêmes protections que les autres
    POST, et seulement des identifiants. Base d'essai sans photo : rien ne peut être copié ni supprimé, aucune fenêtre
    ne s'ouvre (une copie sans photo valable est refusée avant la fenêtre « Choisir un dossier »)."""

    ROUTES = ('/api/copier', '/api/copier-vers', '/api/supprimer', '/api/copie/annuler', '/api/copie/montrer',
              '/api/base-ecartee/vu')

    def test_csrf_et_origine(self):
        corps = {'ids': [INCONNU], 'confirme': True}
        for route in self.ROUTES:
            with self.subTest(route=route):
                self.assertEqual(requete('POST', route, corps)[0], 403)                          # sans X-MyPhotos
                self.assertEqual(requete('POST', route, corps, {'X-MyPhotos': '0'})[0], 403)
                for origine in ('http://evil.example', 'null', f'http://127.0.0.1:{PORT + 1}'):
                    self.assertEqual(requete('POST', route, corps, {'X-MyPhotos': '1', 'Origin': origine})[0], 403)
                self.assertEqual(requete('POST', route, corps, {'X-MyPhotos': '1', 'Sec-Fetch-Site': 'cross-site'})[0],
                                 403)
                self.assertEqual(requete('POST', route, corps, {'X-MyPhotos': '1'}, hote='evil.example')[0], 403)
                self.assertEqual(requete('POST', route, entetes={'X-MyPhotos': '1', 'Content-Type': 'text/plain'},
                                         brut=json.dumps(corps).encode())[0], 415)
        self.assertEqual(requete('GET', '/api/copie', entetes={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.vivant()

    def test_identifiants_seulement(self):
        for valeur in (None, [], 'abc', 12, {'id': 1}, [0], [-1], ['01'], ['1; calc'], [1.5], [True], [None], [[1]],
                       [{'id': 1}], ['C:\\Windows\\win.ini'], ['..\\..\\config.py'], [' 1'], ['12345678901234567890']):
            for route in ('/api/copier', '/api/copier-vers', '/api/supprimer'):
                with self.subTest(route=route, valeur=valeur):
                    statut, d = api(route, {'ids': valeur, 'confirme': True})
                    self.assertEqual(statut, 400)
                    self.assertIn('erreur', d)
        for route in ('/api/copier', '/api/copier-vers', '/api/supprimer'):
            self.assertEqual(api(route, {'chemins': ['C:\\Windows\\win.ini'], 'confirme': True})[0], 400)
        statut, d = api('/api/supprimer', {'ids': list(range(1, 2002)), 'confirme': True})
        self.assertEqual(statut, 400)
        self.assertRegex(d['erreur'], r'2\s000 au plus')                  # espace fine insécable, comme ailleurs
        self.vivant()

    def test_photos_inconnues(self):
        statut, d = api('/api/copier', {'ids': [INCONNU]})
        self.assertEqual(statut, 409)
        self.assertIn('plus dans MyPhotos', d['erreur'])
        self.assertEqual(api('/api/copier-vers', {'ids': [INCONNU, INCONNU + 1]})[0], 409)
        for confirme in (None, False, 'oui', 'true', 1, [True]):
            with self.subTest(confirme=confirme):
                statut, d = api('/api/supprimer', {'ids': [INCONNU], 'confirme': confirme})
                self.assertEqual(statut, 400)
                self.assertIn('confirm', d['erreur'])
        statut, d = api('/api/supprimer', {'ids': [INCONNU, str(INCONNU + 1), INCONNU], 'confirme': True})
        self.assertEqual(statut, 200, d)
        self.assertEqual(d['supprimees'], [])
        self.assertEqual([r['id'] for r in d['refusees']], [INCONNU, INCONNU + 1])   # sans doublon, dans l'ordre
        self.assertTrue(all('plus dans MyPhotos' in r['raison'] for r in d['refusees']))
        self.assertEqual(d['octets_liberes'], 0)
        self.assertIsInstance(d['generation'], int)
        self.assertEqual(api('/api/copie'), (200, None))
        self.assertEqual(api('/api/copie/annuler', {}), (200, {'annule': False}))
        self.assertEqual(api('/api/copie/montrer', {})[0], 404)
        self.assertEqual(api('/api/base-ecartee/vu', {}), (200, {'ok': True}))
        e = api('/api/etat')[1]
        self.assertEqual((e['limite_selection'], e['copie'], e['base_ecartee']), (2000, None, None))
        self.vivant()


class TestDoublonsHttp(EssaiServeur):
    """Routes des doublons : mêmes protections que les autres routes, aucun chemin accepté ni rendu en dehors du
    dossier affiché de chaque photo. La base d'essai peut être vide ou contenir les images publiques (TestBoutEnBout)."""

    CLES_PHOTO = {'id', 'garder', 'meilleure', 'raison', 'nom', 'dossier', 'taille', 'largeur', 'hauteur', 'date'}

    def test_protections(self):
        for route in ('/api/doublons/lancer', '/api/doublons/annuler'):
            with self.subTest(route=route):
                self.assertEqual(requete('POST', route, {})[0], 403)                              # sans X-MyPhotos
                for origine in ('http://evil.example', 'null', f'http://127.0.0.1:{PORT + 1}'):
                    self.assertEqual(requete('POST', route, {}, {'X-MyPhotos': '1', 'Origin': origine})[0], 403)
                self.assertEqual(requete('POST', route, {}, {'X-MyPhotos': '1', 'Sec-Fetch-Site': 'cross-site'})[0],
                                 403)
                self.assertEqual(requete('POST', route, {}, {'X-MyPhotos': '1'}, hote='evil.example')[0], 403)
                self.assertEqual(requete('POST', route, entetes={'X-MyPhotos': '1', 'Content-Type': 'text/plain'},
                                         brut=b'{}')[0], 415)
        self.assertEqual(requete('GET', '/api/doublons', entetes={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(requete('GET', '/api/doublons', hote='evil.example')[0], 403)
        for depuis in ('abc', '-1', '1e3', '9' * 12):
            self.assertEqual(api(f'/api/doublons?depuis={depuis}')[0], 400)
        self.assertEqual(api('/api/doublons/inconnue', {})[0], 404)
        self.vivant()

    def test_lancer_suivre_lire(self):
        statut, e = api('/api/etat')
        self.assertEqual(set(e['doublons']), {'en_cours', 'phase', 'avancement', 'message', 'numero', 'perime',
                                              'deja', 'arretee', 'erreur'})
        avant = e['doublons']['numero']
        statut, r = api('/api/doublons/lancer', {'chemin': 'C:\\Windows'})    # tout autre champ est ignoré
        self.assertEqual(statut, 200, r)
        self.assertIn('lance', r)
        x = attendre(lambda: (lambda d: d if not d['en_cours'] and d['numero'] > avant else None)(
            api('/api/etat')[1]['doublons']), 120)
        self.assertTrue(x, 'recherche des doublons non terminée')
        self.assertTrue(x['deja'])
        self.assertIsNone(x['erreur'])
        statut, d = api('/api/doublons')
        self.assertEqual(statut, 200, d)
        res = d['resultat']
        self.assertEqual(res['numero'], x['numero'])
        self.assertIsInstance(res['groupes'], list)
        for g in res['groupes']:
            self.assertIn(g['type'], ('identiques', 'retouchees', 'proches'))
            self.assertGreaterEqual(len(g['photos']), 2)
            self.assertTrue(any(p['garder'] for p in g['photos']))
            for p in g['photos']:
                self.assertEqual(set(p), self.CLES_PHOTO)
                self.assertIsInstance(p['id'], int)
        # La page a déjà ces groupes : seul l'état revient.
        statut, d = api(f'/api/doublons?depuis={res["numero"]}')
        self.assertEqual(statut, 200)
        self.assertNotIn('groupes', d['resultat'])
        self.assertEqual(api('/api/doublons/annuler', {}), (200, {'annule': False}))     # rien en cours
        self.vivant()


class TestReponses(EssaiServeur):

    def test_01_etat(self):
        statut, e = api('/api/etat')
        self.assertEqual(statut, 200)
        self.assertEqual(e['nom'], 'MyPhotos')
        for cle in ('version', 'assistant', 'moteur', 'telechargement', 'changement', 'analyse', 'photos',
                    'nb_dossiers', 'recherche_prete', 'generation', 'arret', 'preparation'):
            self.assertIn(cle, e)
        self.assertEqual(set(e['photos']), {'total', 'analysees', 'a_faire', 'erreurs', 'indisponibles', 'en_attente'})
        self.assertFalse(e['arret'])
        self.assertIsInstance(e['preparation'], bool)
        if e['moteur'] is None:
            self.assertFalse(e['preparation'])
        else:
            self.assertIsInstance(e['moteur']['pret'], bool)

    def test_02_materiel(self):
        statut, m = api('/api/materiel')
        self.assertEqual(statut, 200)
        self.assertTrue(m['resume'])
        self.assertTrue(all(set(l) == {'libelle', 'valeur'} for l in m['resume']))
        self.assertEqual([x['cle'] for x in m['moteurs']], ['rapide', 'base', 'precis', 'tres_precis'])
        self.assertIn(m['conseil'], [x['cle'] for x in m['moteurs']])
        self.assertEqual(sum(x['conseille'] for x in m['moteurs']), 1)
        for x in m['moteurs']:
            self.assertTrue(x['nom'])
            self.assertIsInstance(x['telechargement_octets'], int)
            self.assertEqual(x['deja'], x['telechargement_octets'] == 0)
            for texte in [x['description'], x['compromis'], *x['raisons'], *x['alertes']]:
                if texte:
                    self.assertNotRegex(texte, r'\b[Mm]odèles?\b|\bindexation\b|\bvecteur')
        self.assertIsInstance(m['nb_photos'], int)
        statut, m2 = api('/api/materiel?refaire=1')
        self.assertEqual(statut, 200)
        self.assertEqual(len(m2['moteurs']), 4)

    def test_03_dossiers_et_photos(self):
        statut, d = api('/api/dossiers')
        self.assertEqual(statut, 200)
        self.assertIsInstance(d['dossiers'], list)
        self.assertIsInstance(d['proposes'], list)
        for p in d['proposes']:
            self.assertEqual(set(p) >= {'chemin', 'nom', 'deja', 'photos_estimees'}, True)
        statut, p = api('/api/photos?debut=0&n=5')
        self.assertEqual(statut, 200)
        self.assertEqual(p['mode'], 'dates')
        self.assertLessEqual(len(p['photos']), 5)
        statut, p = api('/api/recherche?q=&n=3')
        self.assertEqual(statut, 200)
        self.assertEqual(p['mode'], 'dates')
        self.assertEqual(api('/api/photos?n=100000')[0], 200)          # borné, pas refusé

    def test_04_moteur_et_recherche(self):
        statut, d = api('/api/moteur/choisir', {'cle': 'base', 'vision': 'fp16'})
        self.assertEqual(statut, 200, d)
        # Pendant le changement et la préparation du moteur, la recherche attend : « préparation », jamais d'erreur.
        statut, r = api('/api/recherche?q=chien&n=10')
        self.assertEqual(statut, 200, r)
        self.assertEqual(r['mode'], 'recherche')
        if r['preparation']:
            self.assertEqual((r['sures'], r['peut_etre'], r['requete']), ([], [], 'chien'))
        e = attendre(lambda: (lambda x: x if x['recherche_prete'] and not x['changement'] else None)(api('/api/etat')[1]), 60)
        self.assertTrue(e, 'le moteur n\'a pas été activé')
        self.assertEqual(e['moteur']['cle'], 'base')
        self.assertTrue(e['moteur']['pret'])
        self.assertFalse(e['preparation'])
        self.assertEqual(api('/api/moteur/choisir', {'cle': 'base', 'vision': 'fp16'})[1], {'deja': True})
        statut, r = api('/api/recherche?q=chien&n=10')
        self.assertEqual(statut, 200, r)
        self.assertEqual(r['mode'], 'recherche')
        self.assertFalse(r['preparation'])
        for cle in ('requete', 'total_sures', 'total_peut_etre', 'total_proches', 'sures', 'peut_etre', 'suivant',
                    'analysees'):
            self.assertIn(cle, r)
        # « Voir les photos les plus proches » : la suite, sous les seuils, par pages.
        statut, p = api('/api/recherche?q=chien&proches=1&n=60')
        self.assertEqual(statut, 200, p)
        self.assertEqual((p['mode'], p['preparation'], p['debut']), ('proches', False, 0))
        self.assertEqual(p['total_proches'], r['total_proches'])
        self.assertLessEqual(len(p['proches']), 60)
        self.assertEqual(p['suivant'], len(p['proches']))
        self.assertEqual(api('/api/recherche?q=&proches=1')[1]['mode'], 'dates')     # sans mots : par date
        self.vivant()

    def test_05_page_logo_et_proches(self):
        statut, _, corps = requete('GET', '/')
        self.assertEqual(statut, 200)
        page = corps.decode('utf-8')
        # Le logo de l'en-tête est l'icône de MyPhotos : son image de 64 px, une seule fois dans la page.
        self.assertNotIn('href="#logo"', page)
        self.assertEqual(page.count('class="logo"'), 4)
        debut = '.logo {\n  display: inline-block; width: 40px; height: 40px; flex: none;\n  background: url("'
        self.assertEqual(page.count(debut), 1)
        uri = page.split(debut, 1)[1].split('"', 1)[0]
        self.assertTrue(uri.startswith('data:image/png;base64,'))
        png = base64.b64decode(uri.split(',', 1)[1])
        self.assertEqual(struct.unpack('>II', png[16:24]), (64, 64))
        self.assertEqual(png, icone_64(), 'logo différent de construction/MyPhotos.ico (image de 64 px)')
        # Bouton « Voir les photos les plus proches », section « Moins probables ».
        for texte in ('id="proches-voir"', 'Voir les photos les plus proches', '>Moins probables<',
                      'id="grille-proches"'):
            self.assertIn(texte, page)


@unittest.skipUnless(PHOTOS and Path(PHOTOS).is_dir(), 'MYPHOTOS_ESSAI_PHOTOS : dossier d\'images publiques')
class TestBoutEnBout(EssaiServeur):
    """Images publiques nommées d'après leur contenu : chien-1.jpg, plage-2.jpg…"""

    REQUETES = {'chien': 'chien', 'plage': 'plage', 'voiture': 'voiture', 'fleurs': 'fleur', 'chat': 'chat',
                'neige': 'neige', 'coucher de soleil': 'coucher-de-soleil', 'gâteau d\'anniversaire': 'gateau'}

    def noms(self, items):
        return [api(f'/api/photo/{i["id"]}')[1]['nom'] for i in items]

    def recherche(self, q, n=8):
        statut, r = api('/api/recherche?q=' + urllib.parse.quote(q) + f'&n={n}')
        self.assertEqual(statut, 200, r)
        return r

    def test_bout_en_bout(self):
        statut, d = api('/api/moteur/choisir', {'cle': 'base', 'vision': 'fp16'})
        self.assertEqual(statut, 200, d)
        self.assertTrue(attendre(lambda: api('/api/etat')[1]['recherche_prete'], 60))
        statut, d = api('/api/dossiers/ajouter', {'chemin': PHOTOS})
        self.assertIn(statut, (200, 409), d)
        if statut == 409:
            self.assertIn('déjà dans MyPhotos', d['erreur'])
        else:
            self.assertEqual(api('/api/dossiers/ajouter', {'chemin': PHOTOS})[0], 409)

        # Des recherches pendant l'analyse : la carte graphique sert aux deux à la fois (cf. MoteurPartage).
        erreurs, fin = [], threading.Event()

        def chercher_sans_cesse():
            while not fin.is_set():
                try:
                    statut, _ = api('/api/recherche?q=chien&n=5')
                    if statut not in (200, 503):
                        erreurs.append(statut)
                except OSError as e:
                    erreurs.append(repr(e))
                time.sleep(0.05)
        fils = [threading.Thread(target=chercher_sans_cesse) for _ in range(2)]
        for f in fils:
            f.start()
        try:
            e = attendre(lambda: (lambda x: x if (x['analyse'] or {}).get('phase') == 'termine'
                                  and x['photos']['analysees'] >= 20 else None)(api('/api/etat')[1]), 300, 1.0)
        finally:
            fin.set()
            for f in fils:
                f.join()
        self.assertTrue(e, 'analyse non terminée')
        self.assertEqual(erreurs, [])
        self.vivant()

        # La recherche relit les photos analysées au plus une fois par seconde : on attend qu'elle les voie toutes.
        self.assertTrue(attendre(lambda: self.recherche('chien')['analysees'] >= e['photos']['analysees'], 15))

        # Recherches : les bonnes images arrivent en tête. Les seuils « sûres / moins sûres » sont réglés à part
        # (recherche.REGLAGES) : on juge l'ordre des résultats rendus, sur des requêtes au signal net.
        reussies, vues = 0, {}
        for q, prefixe in self.REQUETES.items():
            r = self.recherche(q)
            noms = self.noms((r['sures'] + r['peut_etre'])[:4])
            vues[q] = noms
            reussies += bool(noms) and noms[0].startswith(prefixe)
        for q, minimum in (('gâteau d\'anniversaire', 3), ('chat', 2), ('chien', 1), ('coucher de soleil', 1)):
            with self.subTest(q=q, noms=vues[q]):
                self.assertGreaterEqual(sum(n.startswith(self.REQUETES[q]) for n in vues[q]), minimum)
                self.assertTrue(vues[q][0].startswith(self.REQUETES[q]))
        self.assertGreaterEqual(reussies, 4, f'trop peu de premières places justes : {vues}')

        # Photos par date, miniature (ETag, 304), photo d'origine, fiche.
        statut, p = api('/api/photos?n=200')
        self.assertEqual(p['total'], e['photos']['analysees'])
        pid = p['photos'][0]['id']
        statut, en_tetes, jpeg = requete('GET', f'/miniature/{pid}')
        self.assertEqual(statut, 200)
        self.assertEqual(en_tetes['content-type'], 'image/jpeg')
        self.assertEqual(jpeg[:2], b'\xff\xd8')
        self.assertEqual(requete('GET', f'/miniature/{pid}', entetes={'If-None-Match': en_tetes['etag']})[0], 304)
        statut, fiche = api(f'/api/photo/{pid}')
        self.assertEqual(statut, 200)
        original = Path(fiche['dossier']) / fiche['nom']
        statut, en_tetes, octets = requete('GET', f'/photo/{pid}')
        self.assertEqual(statut, 200)
        self.assertEqual(en_tetes['content-type'], 'image/jpeg')
        self.assertEqual(octets, original.read_bytes())
        self.assertEqual(en_tetes['cross-origin-resource-policy'], 'same-origin')

        # Pagination : les pages se suivent sans doublon.
        r1, r2 = self.recherche('chien', 3), None
        statut, r2 = api(f'/api/recherche?q=chien&debut={r1["suivant"]}&n=3')
        ids1 = [i['id'] for i in r1['sures'] + r1['peut_etre']]
        ids2 = [i['id'] for i in r2['sures'] + r2['peut_etre']]
        self.assertFalse(set(ids1) & set(ids2))

        # Un nouveau dossier après la fin de l'analyse : l'indexeur reprend son moteur, puis on cherche encore
        # (avant le contournement, cette séquence faisait planter le processus).
        ajout = DONNEES / 'essai-ajout'
        ajout.mkdir(exist_ok=True)
        source = sorted(Path(PHOTOS).glob('chien-*.jpg'))[0]
        if not (ajout / 'chien-copie.jpg').exists():
            shutil.copyfile(source, ajout / 'chien-copie.jpg')
        statut, d = api('/api/dossiers/ajouter', {'chemin': str(ajout)})
        self.assertIn(statut, (200, 409), d)
        if statut == 409:
            api('/api/analyse/relancer', {})
        avant = e['photos']['analysees']
        self.assertTrue(attendre(lambda: (lambda x: (x['analyse'] or {}).get('phase') == 'termine'
                                          and x['photos']['analysees'] >= min(avant + 1, avant + (statut == 200)))(
            api('/api/etat')[1]), 120, 1.0))
        for _ in range(3):
            self.assertEqual(self.recherche('chien')['mode'], 'recherche')
        self.vivant()

        # Doublons : la copie exacte ajoutée est trouvée, l'originale est à garder ; la recherche répond pendant le
        # calcul (il ne touche pas au moteur).
        avant = api('/api/etat')[1]['doublons']['numero']
        statut, d = api('/api/doublons/lancer', {})
        self.assertEqual(statut, 200, d)
        debut = time.monotonic()
        self.assertEqual(self.recherche('chien')['mode'], 'recherche')
        self.assertLess(time.monotonic() - debut, 10)
        x = attendre(lambda: (lambda y: y if not y['en_cours'] and y['numero'] > avant else None)(
            api('/api/etat')[1]['doublons']), 120)
        self.assertTrue(x and not x['erreur'], x)
        groupes = api('/api/doublons')[1]['resultat']['groupes']
        groupe = next(g for g in groupes if 'chien-copie.jpg' in [p['nom'] for p in g['photos']])
        self.assertEqual(groupe['type'], 'identiques')
        self.assertEqual({p['nom']: p['garder'] for p in groupe['photos']}, {'chien-copie.jpg': False, source.name: True})
        self.assertEqual(groupe['gain_octets'], (ajout / 'chien-copie.jpg').stat().st_size)
        self.vivant()

        # Retrait du dossier ajouté : ses photos disparaissent, les fichiers restent.
        dossiers = api('/api/dossiers')[1]['dossiers']
        did = next(x['id'] for x in dossiers if x['chemin'].lower() == str(ajout).lower())
        statut, d = api('/api/dossiers/retirer', {'id': did})
        self.assertEqual(statut, 200, d)
        self.assertTrue((ajout / 'chien-copie.jpg').exists())
        # Les doublons suivent tout de suite : le groupe de la copie disparaît, et le résultat est à refaire.
        d = api('/api/doublons')[1]
        self.assertTrue(d['perime'])
        self.assertFalse(any('chien-copie.jpg' in [p['nom'] for p in g['photos']] for g in d['resultat']['groupes']))
        self.vivant()

        # Changement de moteur, aller et retour : l'ancien moteur part en entier, toutes les photos sont analysées
        # de nouveau, les miniatures restent les mêmes, et la recherche marche avec le nouveau moteur.
        etag = requete('GET', f'/miniature/{pid}')[1]['etag']
        nombre = api('/api/etat')[1]['photos']['analysees']
        for cle in ('rapide', 'base'):
            statut, d = api('/api/moteur/choisir', {'cle': cle, 'vision': 'fp16'})
            self.assertEqual(statut, 200, d)
            self.assertFalse(d['deja'])

            def fini(cle=cle):
                x = api('/api/etat')[1]
                return (x['moteur'] and x['moteur']['cle'] == cle and not x['changement']
                        and (x['analyse'] or {}).get('phase') == 'termine' and x['photos']['analysees'] >= nombre)
            self.assertTrue(attendre(fini, 180, 1.0), f'analyse avec « {cle} » non terminée')
            self.assertTrue(attendre(lambda: self.recherche('gâteau d\'anniversaire')['analysees'] >= nombre, 15))
            noms = self.noms((lambda r: r['sures'] + r['peut_etre'])(self.recherche('gâteau d\'anniversaire'))[:3])
            self.assertTrue(noms and noms[0].startswith('gateau'), (cle, noms))
            self.assertEqual(requete('GET', f'/miniature/{pid}')[1]['etag'], etag)
            self.vivant()


class TestInstances(unittest.TestCase):
    """Instance unique et arrêt automatique, sur un autre port et d'autres données."""

    PORT2 = PORT + 1

    def test_arret_apres_inactivite_et_instance_unique(self):
        if not port_libre(self.PORT2):
            self.skipTest(f'port {self.PORT2} déjà pris')
        donnees = DONNEES / 'essai-instances'
        proc = lancer_serveur(donnees, self.PORT2, neutre=True, MYPHOTOS_INACTIVITE_S='4')
        try:
            attendre_serveur(proc, self.PORT2)
            # Deuxième lancement sur le même port : il voit MyPhotos et s'arrête aussitôt, sans erreur.
            second = lancer_serveur(donnees, self.PORT2, neutre=True)
            try:
                self.assertEqual(second.wait(timeout=60), 0)
            finally:
                arreter_essai(second)
                second.sortie.close()
            self.assertIsNone(proc.poll())
            # Plus aucune requête : arrêt propre tout seul (délai réduit à 4 s pour l'essai).
            self.assertEqual(proc.wait(timeout=60), 0)
        finally:
            arreter_essai(proc)
            proc.sortie.close()
        self.assertTrue(port_libre(self.PORT2))

    def test_arret_malgre_un_autre_site(self):
        """Un site ouvert dans le navigateur qui interroge MyPhotos toutes les secondes (page en « no-cors », données,
        cadre) ne le garde pas éveillé : l'arrêt automatique a lieu quand même (avant : jamais, tant que le site
        restait ouvert)."""
        if not port_libre(self.PORT2):
            self.skipTest(f'port {self.PORT2} déjà pris')
        donnees = DONNEES / 'essai-instances'
        proc = lancer_serveur(donnees, self.PORT2, neutre=True, MYPHOTOS_INACTIVITE_S='4')
        hote = f'127.0.0.1:{self.PORT2}'
        try:
            attendre_serveur(proc, self.PORT2)
            debut = time.monotonic()
            while proc.poll() is None and time.monotonic() - debut < 20:
                for chemin, mode, dest in (('/', 'no-cors', 'empty'), ('/api/etat', 'no-cors', 'empty'),
                                           ('/', 'navigate', 'iframe'), ('/favicon.ico', 'no-cors', 'image')):
                    try:
                        requete('GET', chemin, hote=hote, port=self.PORT2, delai=5,
                                entetes={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': mode, 'Sec-Fetch-Dest': dest})
                    except (OSError, http.client.HTTPException):
                        pass                                       # déjà arrêté, ou en train de s'arrêter
                time.sleep(1.0)
            self.assertIsNotNone(proc.poll(), 'MyPhotos reste lancé tant qu\'un autre site l\'interroge')
            self.assertEqual(proc.wait(timeout=60), 0)
        finally:
            arreter_essai(proc)
            proc.sortie.close()
        self.assertTrue(port_libre(self.PORT2))

    def test_jeton_neuf_a_chaque_lancement(self):
        """0.4.0 : un nouveau jeton à chaque lancement (l'ancien ne vaut plus rien) ; des requêtes sans le jeton
        (programme d'un autre compte Windows qui interroge le port chaque seconde) ne gardent pas MyPhotos éveillé ;
        une seconde instance ne remplace jamais le jeton de celle qui tourne."""
        if not port_libre(self.PORT2):
            self.skipTest(f'port {self.PORT2} déjà pris')
        donnees = DONNEES / 'essai-jeton'
        hote = f'127.0.0.1:{self.PORT2}'
        jetons = []
        for tour in range(2):
            proc = lancer_serveur(donnees, self.PORT2, neutre=True, MYPHOTOS_INACTIVITE_S='4')
            try:
                attendre_serveur(proc, self.PORT2)
                jetons.append(jeton_essai(self.PORT2))
                self.assertEqual(requete('GET', '/api/etat', hote=hote, port=self.PORT2)[0], 200)
                if tour == 0:
                    second = lancer_serveur(donnees, self.PORT2, neutre=True)       # voit MyPhotos, s'arrête
                    try:
                        self.assertEqual(second.wait(timeout=60), 0)
                    finally:
                        arreter_essai(second)
                        second.sortie.close()
                    self.assertEqual(jeton_essai(self.PORT2), jetons[0])          # jeton inchangé
                    self.assertIsNone(proc.poll())
                else:
                    self.assertNotEqual(jetons[1], jetons[0])
                    self.assertEqual(requete('GET', '/api/etat', hote=hote, port=self.PORT2, jeton=jetons[0])[0], 403)
                debut = time.monotonic()
                while proc.poll() is None and time.monotonic() - debut < 20:
                    for chemin, jeton in (('/', False), ('/api/etat', False), ('/api/etat', 'faux'),
                                          (f'/?jeton={jetons[0] if tour else "faux"}', False)):
                        try:
                            requete('GET', chemin, hote=hote, port=self.PORT2, delai=5, jeton=jeton)
                        except (OSError, http.client.HTTPException):
                            pass                                   # déjà arrêté, ou en train de s'arrêter
                    time.sleep(1.0)
                self.assertIsNotNone(proc.poll(), 'des requêtes sans jeton gardent MyPhotos lancé')
                self.assertEqual(proc.wait(timeout=60), 0)
            finally:
                arreter_essai(proc)
                proc.sortie.close()
            self.assertTrue(port_libre(self.PORT2))


class TestActionsWindows(unittest.TestCase):
    """« Ouvrir » et « Afficher dans le dossier » : identifiant en base et extension d'image exigés. Les appels
    à Windows sont simulés : aucune fenêtre ne s'ouvre pendant les essais."""

    def setUp(self):
        from PIL import Image
        from myphotos import base, serveur
        self.serveur = serveur
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-actions-'))
        (self.tmp / 'photos').mkdir()
        self.image = self.tmp / 'photos' / 'rouge.jpg'
        Image.new('RGB', (200, 150), (200, 30, 30)).save(self.image)
        self.texte = self.tmp / 'photos' / 'pas-une-image.exe'
        self.texte.write_bytes(b'MZ')
        self.base = base.Base(self.tmp / 'index.db', self.tmp / 'miniatures.db')
        d = self.base.ajouter_dossier(str(self.tmp / 'photos'))
        lignes = [(str(p), p.stat().st_size, p.stat().st_mtime_ns, None) for p in (self.image, self.texte)]
        self.base.ajouter_photos(d['id'], lignes)
        ids = {p['nom']: p['id'] for p in self.base.photos(range(1, 10))}
        self.id_image, self.id_texte = ids['rouge.jpg'], ids['pas-une-image.exe']
        self.app = serveur.Application.__new__(serveur.Application)
        self.app.base = self.base
        self.app.indexeur = None

    def tearDown(self):
        self.base.fermer()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ouvrir(self):
        with mock.patch.object(self.serveur.os, 'startfile', create=True) as ouvrir:
            self.assertEqual(self.app.ouvrir(self.id_image), {'ok': True})
        ouvrir.assert_called_once_with(str(self.image))

    def test_montrer(self):
        with mock.patch.object(self.serveur, 'montrer_dans_dossier') as montrer:
            self.assertEqual(self.app.montrer(self.id_image), {'ok': True})
        montrer.assert_called_once_with(str(self.image))

    def test_refus(self):
        with mock.patch.object(self.serveur.os, 'startfile', create=True) as ouvrir, \
                mock.patch.object(self.serveur, 'montrer_dans_dossier') as montrer:
            for action in (self.app.ouvrir, self.app.montrer):
                with self.assertRaises(self.serveur.Refus) as e:
                    action(self.id_texte)                         # pas une image : jamais ouvert
                self.assertEqual(e.exception.statut, 403)
                with self.assertRaises(self.serveur.Refus) as e:
                    action(INCONNU)
                self.assertEqual(e.exception.statut, 404)
            self.image.unlink()                                    # fichier disparu, dossier là : rien n'est oublié,
            with self.assertRaises(self.serveur.Refus) as e:       # un tour du dossier (s'il y a un indexeur) tranchera
                self.app.ouvrir(self.id_image)
            self.assertEqual((e.exception.statut, str(e.exception)), (404, self.serveur.PHOTO_DEPLACEE))
            self.assertIsNotNone(self.base.photo(self.id_image))
        ouvrir.assert_not_called()
        montrer.assert_not_called()


class TestSelection(unittest.TestCase):
    """Copier, copier dans un dossier, mettre à la Corbeille : Application dans ce processus, base et photos générées
    dans un dossier temporaire. Le presse-papiers, la fenêtre « Choisir un dossier » et la Corbeille de Windows sont
    simulés (la « Corbeille » est un dossier d'essai) : rien ne touche le presse-papiers ni la vraie Corbeille."""

    def setUp(self):
        from PIL import Image
        from myphotos import serveur
        self.serveur = serveur
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-selection-'))
        self.photos = self.tmp / 'photos'
        (self.photos / 'sous').mkdir(parents=True)
        self.fichiers = {}
        for nom, couleur in (('rouge.jpg', (200, 30, 30)), ('vert.jpg', (30, 200, 30)), ('bleu.png', (30, 30, 200)),
                             ('sous/rouge.jpg', (250, 120, 0))):
            p = self.photos / nom
            Image.new('RGB', (160, 120), couleur).save(p)
            self.fichiers[nom] = p
        self.correctifs = [mock.patch.object(serveur.config, 'BASE', self.tmp / 'index.db'),
                           mock.patch.object(serveur.config, 'MINIATURES', self.tmp / 'miniatures.db'),
                           mock.patch.object(serveur, 'dossiers_proposes', lambda: []),
                           mock.patch.object(serveur, 'dossier_images', lambda: None)]
        for c in self.correctifs:
            c.start()
        self.app = serveur.Application(serveur=None)
        self.dossier = self.app.base.ajouter_dossier(str(self.photos))
        self.ids = self.ajouter(self.fichiers.values())
        self.corbeille = self.tmp / 'corbeille-simulee'
        self.corbeille.mkdir()
        self.envois = []

    def tearDown(self):
        try:
            c = self.app._copie
            if c is not None:
                c.arret.set()
                c.fil.join(10)
            self.app.base.fermer()
        finally:
            for c in reversed(self.correctifs):
                c.stop()
            lien = self.photos / 'lien'
            if lien.exists() or os.path.lexists(lien):
                os.rmdir(lien)                                  # la jonction seule, jamais ce qu'elle désigne
            shutil.rmtree(self.tmp, ignore_errors=True)

    def ajouter(self, chemins):
        """Photos connues de la base, comme après l'inventaire : {chemin relatif au dossier : identifiant}."""
        lignes = [(str(p), p.stat().st_size, p.stat().st_mtime_ns, None) for p in chemins]
        self.app.base.ajouter_photos(self.dossier['id'], lignes)
        connues = self.app.base.connus(self.dossier['id'])
        return {Path(ch).relative_to(self.photos).as_posix(): v[0] for ch, v in connues.items()}

    def corbeille_simulee(self, ok=True, capacite=None):
        """infos_corbeille et mettre_a_la_corbeille simulés : la « Corbeille » est un dossier d'essai."""
        essai = self

        def mettre(chemins):
            essai.envois.append(list(chemins))
            for c in chemins:
                shutil.move(c, essai.corbeille / f'{len(essai.envois)}-{Path(c).name}')
            return 0, False
        infos = {'ok': True, 'raison': None, 'capacite': capacite} if ok else \
            {'ok': False, 'raison': self.serveur.SANS_CORBEILLE, 'capacite': None}
        return (mock.patch.object(self.serveur, 'infos_corbeille', return_value=infos),
                mock.patch.object(self.serveur, 'mettre_a_la_corbeille', side_effect=mettre))

    # ---------------------------------------------------------------- copier (presse-papiers)

    def test_dropfiles(self):
        d = self.serveur.dropfiles(['D:\\Photos\\a.jpg', 'E:\\Photos\\été 2024\\b.png'])
        self.assertEqual(struct.unpack('<IiiII', d[:20]), (20, 0, 0, 0, 1))           # pFiles, pt, fNC, fWide
        self.assertEqual(d[20:].decode('utf-16-le'), 'D:\\Photos\\a.jpg\0E:\\Photos\\été 2024\\b.png\0\0')

    def test_copier_dans_le_presse_papiers(self):
        recus = []
        with mock.patch.object(self.serveur, 'mettre_au_presse_papiers', side_effect=lambda c: recus.append(c)):
            r = self.app.copier([self.ids['vert.jpg'], INCONNU, self.ids['rouge.jpg']])
        self.assertEqual(recus, [[str(self.fichiers['vert.jpg']), str(self.fichiers['rouge.jpg'])]])
        self.assertEqual((r['copiees'], r['refusees'], r['nb_refusees']),
                         (2, [{'id': INCONNU, 'raison': self.serveur.INTROUVABLE}], 1))
        self.fichiers['bleu.png'].unlink()                                    # fichier supprimé hors de MyPhotos
        with mock.patch.object(self.serveur, 'mettre_au_presse_papiers') as presse:
            with self.assertRaises(self.serveur.Refus) as e:
                self.app.copier([self.ids['bleu.png'], INCONNU])
        self.assertEqual(e.exception.statut, 409)
        presse.assert_not_called()
        self.assertIsNotNone(self.app.base.photo(self.ids['bleu.png']))       # gardée : l'inventaire tranchera
        self.fichiers['sous/rouge.jpg'].unlink()
        with mock.patch.object(self.serveur, 'mettre_au_presse_papiers'):
            r = self.app.copier([self.ids['sous/rouge.jpg'], self.ids['rouge.jpg']])
        self.assertEqual((r['copiees'], r['refusees']),
                         (1, [{'id': self.ids['sous/rouge.jpg'], 'raison': self.serveur.PHOTO_DEPLACEE}]))
        occupe = self.serveur.PressePapiersOccupe('presse-papiers gardé ouvert par exemple.exe')
        with mock.patch.object(self.serveur, 'mettre_au_presse_papiers', side_effect=occupe), \
                self.assertLogs('myphotos.serveur', 'WARNING') as journal:
            with self.assertRaises(self.serveur.Refus) as e:
                self.app.copier([self.ids['vert.jpg']])
        self.assertIn('occupé par un autre programme', str(e.exception))
        self.assertIn('exemple.exe', journal.output[0])                          # le journal dit qui le gardait

    # ---------------------------------------------------------------- copier dans un dossier

    def copier_vers(self, ids, dossier):
        self.app.choix_dossier = mock.Mock(demander=mock.Mock(return_value=str(dossier) if dossier else None))
        r = self.app.copier_vers(ids)
        if not r.get('annule'):
            self.app._copie.fil.join(30)
            self.assertFalse(self.app._copie.fil.is_alive())
        return r

    def test_copier_vers_sans_jamais_ecraser(self):
        dest = self.tmp / 'export'
        dest.mkdir()
        (dest / 'rouge.jpg').write_bytes(b'une autre photo, deja la')
        ids = [self.ids['rouge.jpg'], self.ids['sous/rouge.jpg'], self.ids['vert.jpg'], INCONNU]
        r = self.copier_vers(ids, dest)
        self.assertFalse(r['annule'])
        options = self.app.choix_dossier.demander.call_args.kwargs
        self.assertEqual(options['titre'], 'Choisissez où copier les photos')
        c = self.app.copie()
        self.assertEqual((c['etat'], c['total'], c['fait'], c['copiees'], c['nb_renommees'], c['nb_refusees']),
                         ('fini', 4, 4, 3, 2, 1))
        self.assertEqual(c['pourcent'], 100.0)
        self.assertEqual(c['refusees'], [{'id': INCONNU, 'raison': self.serveur.INTROUVABLE}])
        self.assertEqual({x['nom'] for x in c['renommees']}, {'rouge (2).jpg', 'rouge (3).jpg'})
        self.assertEqual((dest / 'rouge.jpg').read_bytes(), b'une autre photo, deja la')        # jamais écrasée
        self.assertEqual((dest / 'rouge (2).jpg').read_bytes(), self.fichiers['rouge.jpg'].read_bytes())
        self.assertEqual((dest / 'rouge (3).jpg').read_bytes(), self.fichiers['sous/rouge.jpg'].read_bytes())
        self.assertEqual((dest / 'vert.jpg').read_bytes(), self.fichiers['vert.jpg'].read_bytes())
        self.assertEqual((dest / 'vert.jpg').stat().st_mtime_ns, self.fichiers['vert.jpg'].stat().st_mtime_ns)
        self.assertEqual(sorted(p.name for p in dest.iterdir()),
                         ['rouge (2).jpg', 'rouge (3).jpg', 'rouge.jpg', 'vert.jpg'])       # aucun fichier provisoire
        self.assertEqual(self.app.base.meta_get('copie_dossier'), str(dest))
        # Les photos d'origine n'ont pas bougé.
        self.assertTrue(all(p.exists() for p in self.fichiers.values()))
        # Une seconde copie : la fenêtre s'ouvre sur le dernier dossier choisi, les noms continuent.
        self.copier_vers([self.ids['vert.jpg']], dest)
        self.assertEqual(self.app.choix_dossier.demander.call_args.kwargs['initial'], str(dest))
        self.assertTrue((dest / 'vert (2).jpg').exists())

    def test_copier_vers_annulation_et_refus(self):
        self.assertEqual(self.copier_vers([self.ids['vert.jpg']], None), {'annule': True})   # fenêtre fermée
        self.assertIsNone(self.app.copie())
        self.app._copie = types.SimpleNamespace(etat='en_cours')                 # une copie à la fois
        with self.assertRaises(self.serveur.Refus) as e:
            self.copier_vers([self.ids['vert.jpg']], self.tmp)
        self.assertEqual(e.exception.statut, 409)
        self.app._copie = None
        with mock.patch.object(self.serveur.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=1000)):
            with self.assertRaises(self.serveur.Refus) as e:
                self.copier_vers([self.ids['vert.jpg']], self.tmp)
        self.assertIn('pas assez de place', str(e.exception))
        with self.assertRaises(self.serveur.Refus):
            self.copier_vers([INCONNU], self.tmp)                                # rien à copier : pas de fenêtre
        self.app.choix_dossier.demander.assert_not_called()

    def test_copie_interrompue_sans_fichier_a_moitie_ecrit(self):
        import numpy as np
        from PIL import Image
        grande = self.photos / 'grande.png'
        Image.fromarray(np.random.default_rng(1).integers(0, 255, (900, 1200, 3), np.uint8)).save(grande)
        pid = self.ajouter([grande])['grande.png']
        dest = self.tmp / 'export'
        dest.mkdir()
        vrai = self.serveur.Copie._avancer

        def avancer(copie, n):
            vrai(copie, n)
            copie.arret.set()                                                  # « Annuler » après le premier bloc
        with mock.patch.object(self.serveur, 'COPIE_BLOC', 64 * 1024), \
                mock.patch.object(self.serveur.Copie, '_avancer', avancer):
            self.copier_vers([pid, self.ids['vert.jpg']], dest)
        c = self.app.copie()
        self.assertEqual((c['etat'], c['copiees']), ('annule', 0))
        self.assertEqual(list(dest.iterdir()), [])                             # ni copie partielle ni provisoire

    # ---------------------------------------------------------------- mettre à la Corbeille

    def test_corbeille_puis_oubli(self):
        b = self.app.base
        pid = self.ids['rouge.jpg']
        taille = self.fichiers['rouge.jpg'].stat().st_size
        b.enregistrer([{'id': pid, 'taille': taille, 'modifie': self.fichiers['rouge.jpg'].stat().st_mtime_ns,
                        'largeur': 160, 'hauteur': 120, 'date_prise': None, 'lat': None, 'lon': None,
                        'vecteur': bytes(16), 'miniature': b'\xff\xd8miniature'}], [], 'base')
        self.assertIsNotNone(b.miniature(pid))
        generation = b.generation()
        ids = [pid, self.ids['vert.jpg'], INCONNU]
        tailles = taille + self.fichiers['vert.jpg'].stat().st_size
        infos, mettre = self.corbeille_simulee()
        with infos, mettre:
            r = self.app.supprimer(ids, True)
        self.assertEqual(r['supprimees'], [pid, self.ids['vert.jpg']])
        self.assertEqual(r['refusees'], [{'id': INCONNU, 'raison': self.serveur.INTROUVABLE}])
        self.assertEqual(r['octets_liberes'], tailles)
        self.assertGreater(r['generation'], generation)
        self.assertEqual(len(self.envois), 1)                                    # un seul appel pour tout le lot
        self.assertEqual(self.envois[0], [str(self.fichiers['rouge.jpg']), str(self.fichiers['vert.jpg'])])
        # Envoyées à la « Corbeille », pas détruites ; oubliées de l'index et des miniatures.
        self.assertEqual(sorted(p.name for p in self.corbeille.iterdir()), ['1-rouge.jpg', '1-vert.jpg'])
        self.assertIsNone(b.photo(pid))
        self.assertIsNone(b.miniature(pid))
        self.assertIsNone(b.photo(self.ids['vert.jpg']))
        self.assertIsNotNone(b.photo(self.ids['bleu.png']))
        self.assertTrue(self.fichiers['bleu.png'].exists())

    def test_selection_ancienne_jamais_une_autre_photo(self):
        """Une sélection restée dans la page désigne ses photos par leur identifiant. Une photo supprimée dans
        l'Explorateur est oubliée par l'inventaire, puis une nouvelle photo arrive : elle ne reprend jamais cet
        identifiant, et « Mettre à la Corbeille » sur l'ancienne sélection ne l'envoie pas à la Corbeille à sa place
        (0.3.0 : SQLite redonnait le plus haut identifiant libéré, et la nouvelle photo partait)."""
        from PIL import Image
        dernier = max(self.ids.values())
        nom = next(n for n, i in self.ids.items() if i == dernier)
        self.fichiers[nom].unlink()
        self.app.base.oublier_photos([dernier])                                 # l'inventaire l'oublie
        nouvelle = self.photos / 'nouvelle.jpg'
        Image.new('RGB', (160, 120), (90, 90, 90)).save(nouvelle)
        self.assertNotEqual(self.ajouter([nouvelle])['nouvelle.jpg'], dernier)
        infos, mettre = self.corbeille_simulee()
        with infos, mettre:
            r = self.app.supprimer([dernier], True)
        self.assertEqual((r['supprimees'], r['refusees']), ([], [{'id': dernier, 'raison': self.serveur.INTROUVABLE}]))
        self.assertEqual(self.envois, [])
        self.assertTrue(nouvelle.exists())

    def test_disque_sans_corbeille(self):
        infos, mettre = self.corbeille_simulee(ok=False)
        with infos, mettre as m:
            r = self.app.supprimer([self.ids['rouge.jpg'], self.ids['vert.jpg']], True)
        m.assert_not_called()
        self.assertEqual(r['supprimees'], [])
        self.assertEqual({x['raison'] for x in r['refusees']},
                         {'Ce disque n\'a pas de Corbeille : MyPhotos ne supprime pas définitivement.'})
        self.assertTrue(self.fichiers['rouge.jpg'].exists())
        self.assertIsNotNone(self.app.base.photo(self.ids['rouge.jpg']))

    def test_verification_corbeille(self):
        """infos_corbeille : un disque qui n'est pas fixe (réseau, Freebox, clé USB) ou dont la Corbeille ne répond pas,
        ou une Corbeille réglée pour supprimer définitivement, est refusé. Appels à Windows simulés."""
        s = self.serveur

        class Faux:
            def __init__(self, type_disque=s.DRIVE_FIXED, reponse=0):
                self.type_disque, self.reponse = type_disque, reponse

            def GetDriveTypeW(self, racine):
                return self.type_disque

            def SHQueryRecycleBinW(self, racine, info):
                return self.reponse
        if os.name != 'nt':
            self.skipTest('Windows seulement')
        for faux, reglages, attendu in ((Faux(4), (False, None), s.SANS_CORBEILLE),           # partage réseau
                                        (Faux(2), (False, None), s.SANS_CORBEILLE),           # clé USB
                                        (Faux(reponse=-2147467259), (False, None), s.SANS_CORBEILLE),
                                        (Faux(), (True, None), s.CORBEILLE_DESACTIVEE),
                                        (Faux(), (False, 50 << 20), None)):
            with self.subTest(type=faux.type_disque, reponse=faux.reponse, reglages=reglages), \
                    mock.patch.object(s, '_dll', lambda nom, f=faux: f), \
                    mock.patch.object(s, '_reglages_corbeille', return_value=reglages):
                r = s.infos_corbeille('Z:\\')
                self.assertEqual(r['raison'], attendu)
                self.assertEqual(r['ok'], attendu is None)
                if attendu is None:
                    self.assertEqual(r['capacite'], 50 << 20)

    def test_refus_fichier_change_trop_grand_ou_non_confirme(self):
        infos, mettre = self.corbeille_simulee(capacite=10)
        rouge, vert = self.fichiers['rouge.jpg'], self.fichiers['vert.jpg']
        with open(rouge, 'ab') as f:
            f.write(b'retouche')                                            # changée depuis l'analyse : taille
        st = vert.stat()
        os.utime(vert, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))  # … ou date seulement
        with infos, mettre as m:
            r = self.app.supprimer([self.ids['rouge.jpg'], self.ids['vert.jpg'], self.ids['bleu.png']], True)
            for confirme in (None, False, 'oui', 1):
                with self.assertRaises(self.serveur.Refus) as e:
                    self.app.supprimer([self.ids['bleu.png']], confirme)
                self.assertEqual(e.exception.statut, 400)
        m.assert_not_called()
        raisons = {x['id']: x['raison'] for x in r['refusees']}
        self.assertEqual(raisons[self.ids['rouge.jpg']], self.serveur.CHANGEE)
        self.assertEqual(raisons[self.ids['vert.jpg']], self.serveur.CHANGEE)
        self.assertEqual(raisons[self.ids['bleu.png']], self.serveur.TROP_GRANDE)   # Corbeille de 10 octets
        self.assertTrue(all(p.exists() for p in self.fichiers.values()))

    @unittest.skipUnless(os.name == 'nt', 'jonctions : Windows')
    def test_pas_a_travers_une_jonction(self):
        import _winapi
        from PIL import Image
        ailleurs = self.tmp / 'ailleurs'
        ailleurs.mkdir()
        Image.new('RGB', (160, 120), (90, 90, 90)).save(ailleurs / 'gris.jpg')
        _winapi.CreateJunction(str(ailleurs), str(self.photos / 'lien'))
        pid = self.ajouter([self.photos / 'lien' / 'gris.jpg'])['lien/gris.jpg']
        infos, mettre = self.corbeille_simulee()
        with infos, mettre as m:
            r = self.app.supprimer([pid], True)
        m.assert_not_called()
        self.assertEqual(r['refusees'], [{'id': pid, 'raison': self.serveur.PAR_UN_LIEN}])
        self.assertTrue((ailleurs / 'gris.jpg').exists())

    # ---------------------------------------------------------------- relecture de sécurité du 24/11/2026

    @unittest.skipUnless(os.name == 'nt', 'noms réinterprétés par Windows')
    def test_dossier_finissant_par_un_point(self):
        """« sous. » (point final, créé avec le préfixe \\\\?\\) à côté de « sous » : sans préfixe, Windows lit
        « sous\\rouge.jpg ». La mise à la Corbeille, le presse-papiers, « Ouvrir » et « Afficher dans le dossier »
        de la photo de « sous. » ne doivent jamais désigner celle de « sous » (avant : elle partait à la Corbeille)."""
        from PIL import Image
        long_ = '\\\\?\\'
        piege = str(self.photos / 'sous') + '.'                      # chaînes : jamais réinterprétées
        os.makedirs(long_ + piege)
        cible = long_ + piege + '\\rouge.jpg'
        Image.new('RGB', (160, 120), (10, 10, 240)).save(cible, 'JPEG')
        st = os.stat(cible)
        self.app.base.ajouter_photos(self.dossier['id'], [(piege + '\\rouge.jpg', st.st_size, st.st_mtime_ns, None)])
        pid = self.app.base.connus(self.dossier['id'])[piege + '\\rouge.jpg'][0]
        leurre = self.fichiers['sous/rouge.jpg']
        avant = leurre.read_bytes()

        def designe_la_cible(c):
            try:
                return os.path.samefile(c, cible)
            except OSError:
                return False
        recus = []
        with mock.patch.object(self.serveur, 'mettre_au_presse_papiers', side_effect=lambda c: recus.append(c)):
            try:
                self.app.copier([pid])
            except self.serveur.Refus as e:
                self.assertEqual(str(e), self.serveur.CHEMIN_AMBIGU)
        self.assertTrue(all(designe_la_cible(c) for x in recus for c in x), recus)
        with mock.patch.object(self.serveur.os, 'startfile', create=True) as ouvrir, \
                mock.patch.object(self.serveur, 'montrer_dans_dossier') as montrer:
            self.app.ouvrir(pid)
            try:
                self.app.montrer(pid)
            except self.serveur.Refus as e:
                self.assertEqual(str(e), self.serveur.CHEMIN_AMBIGU)
        for appel in ouvrir.call_args_list + montrer.call_args_list:
            self.assertTrue(designe_la_cible(appel.args[0]), appel)
        infos, mettre = self.corbeille_simulee()                   # shutil.move : lit les chemins comme Windows
        with infos, mettre:
            r = self.app.supprimer([pid], True)
        self.assertEqual(leurre.read_bytes(), avant)                # l'autre photo n'a jamais bougé
        if r['supprimees']:
            self.assertFalse(os.path.exists(cible))                 # nom court (8.3) : la bonne photo est partie
        else:
            self.assertEqual(r['refusees'], [{'id': pid, 'raison': self.serveur.CHEMIN_AMBIGU}])
            self.assertTrue(os.path.exists(cible))
        self.assertIsNotNone(self.app.base.photo(self.ids['sous/rouge.jpg']))

    @unittest.skipUnless(os.name == 'nt', 'jonctions : Windows')
    def test_copie_vers_jonction_posee_pendant_la_fenetre(self):
        """La fenêtre « Choisir un dossier » reste ouverte le temps que l'utilisateur choisisse : la photo est vérifiée
        de nouveau juste avant d'être copiée (avant : une jonction posée entre-temps faisait copier un fichier situé
        hors des dossiers suivis)."""
        import _winapi
        from PIL import Image
        ailleurs = self.tmp / 'ailleurs'
        ailleurs.mkdir()
        Image.new('RGB', (160, 120), (90, 90, 90)).save(ailleurs / 'rouge.jpg')
        secret = (ailleurs / 'rouge.jpg').read_bytes()
        dest = self.tmp / 'export'
        dest.mkdir()
        sous = self.photos / 'sous'

        def fenetre(**_):
            os.rename(sous, str(sous) + '-avant')                   # un programme rival, pendant que l'on choisit
            _winapi.CreateJunction(str(ailleurs), str(sous))
            return str(dest)
        self.app.choix_dossier = mock.Mock(demander=mock.Mock(side_effect=fenetre))
        try:
            self.app.copier_vers([self.ids['sous/rouge.jpg'], self.ids['vert.jpg']])
            self.app._copie.fil.join(30)
            c = self.app.copie()
        finally:
            if os.path.isdir(sous) and os.path.exists(str(sous) + '-avant'):
                os.rmdir(sous)                                      # la jonction seule
                os.rename(str(sous) + '-avant', sous)
        self.assertEqual((c['etat'], c['copiees']), ('fini', 1))
        self.assertEqual(c['refusees'], [{'id': self.ids['sous/rouge.jpg'], 'raison': self.serveur.PAR_UN_LIEN}])
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ['vert.jpg'])
        self.assertFalse(any(p.read_bytes() == secret for p in dest.iterdir()))

    @unittest.skipUnless(os.name == 'nt', 'jonctions : Windows')
    def test_course_jonction_avant_l_appel_a_windows(self):
        """Un programme rival remplace le dossier de la photo par une jonction APRÈS les vérifications et juste avant
        l'appel à Windows. Les dossiers sont tenus pendant toute l'opération : le remplacement échoue (avant : le
        fichier situé hors des dossiers suivis partait à la Corbeille, et la photo était oubliée)."""
        import _winapi
        from PIL import Image
        ailleurs = self.tmp / 'ailleurs'
        ailleurs.mkdir()
        Image.new('RGB', (160, 120), (90, 90, 90)).save(ailleurs / 'rouge.jpg')
        sous = self.photos / 'sous'
        essai, rival = self, []

        def mettre(chemins):
            try:
                os.rename(sous, str(sous) + '-avant')
                _winapi.CreateJunction(str(ailleurs), str(sous))
                rival.append('jonction posée')
            except OSError as e:
                rival.append(f'refusé ({e.winerror})')
            essai.envois.append(list(chemins))
            for c in chemins:
                shutil.move(c, essai.corbeille / Path(c).name)
            return 0, False
        try:
            with mock.patch.object(self.serveur, 'infos_corbeille', return_value={'ok': True, 'raison': None,
                                                                                 'capacite': None}), \
                    mock.patch.object(self.serveur, 'mettre_a_la_corbeille', side_effect=mettre):
                r = self.app.supprimer([self.ids['sous/rouge.jpg']], True)
        finally:
            if os.path.isdir(sous) and os.path.exists(str(sous) + '-avant'):
                os.rmdir(sous)
                os.rename(str(sous) + '-avant', sous)
        self.assertTrue(rival and rival[0].startswith('refusé'), rival)
        self.assertTrue((ailleurs / 'rouge.jpg').exists())          # rien hors des dossiers suivis
        self.assertEqual(r['supprimees'], [self.ids['sous/rouge.jpg']])
        self.assertFalse(self.fichiers['sous/rouge.jpg'].exists())  # c'est bien la photo qui est partie

    @unittest.skipUnless(os.name == 'nt', 'noms réinterprétés par Windows')
    def test_destination_finissant_par_un_point(self):
        """« export. » choisi dans la fenêtre : MyPhotos aurait copié dans « export », un autre dossier."""
        long_ = '\\\\?\\'
        (self.tmp / 'export').mkdir()
        os.makedirs(long_ + str(self.tmp / 'export.'))
        self.app.choix_dossier = mock.Mock(demander=mock.Mock(return_value=str(self.tmp / 'export.')))
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.copier_vers([self.ids['vert.jpg']])
        self.assertEqual(str(e.exception), self.serveur.DESTINATION_AMBIGUE)
        self.assertEqual(list((self.tmp / 'export').iterdir()), [])

    def test_journal_sans_le_texte_cherche(self):
        """Une recherche qui échoue est notée dans le journal sans ce que l'utilisateur cherchait."""
        self.app.recherche = mock.Mock(chercher=mock.Mock(side_effect=ValueError('panne')))
        self.app._preparation = {'etat': 'pret', 'finie': threading.Event()}
        with mock.patch.object(self.app, 'en_preparation', return_value=False), \
                self.assertLogs('myphotos.serveur', 'ERROR') as journal:
            with self.assertRaises(self.serveur.Refus):
                self.app.chercher('photos de mon secret médical', 0, 10)
        self.assertNotIn('secret', '\n'.join(journal.output))

    def test_echec_de_windows(self):
        """Un fichier ouvert ailleurs fait échouer tout le lot : on reprend un par un ; ce qui reste est refusé. Si
        Windows a voulu supprimer définitivement et qu'on a répondu non, la photo est gardée."""
        essai = self

        def lot_refuse(chemins):
            essai.envois.append(list(chemins))
            if len(chemins) == 1 and not chemins[0].endswith('vert.jpg'):
                shutil.move(chemins[0], essai.corbeille / Path(chemins[0]).name)
            return 2, False
        ids = [self.ids['rouge.jpg'], self.ids['vert.jpg'], self.ids['bleu.png']]
        with mock.patch.object(self.serveur, 'infos_corbeille', return_value={'ok': True, 'raison': None,
                                                                             'capacite': None}), \
                mock.patch.object(self.serveur, 'mettre_a_la_corbeille', side_effect=lot_refuse):
            r = self.app.supprimer(ids, True)
        self.assertEqual([len(x) for x in self.envois], [3, 1, 1, 1])
        self.assertEqual(r['supprimees'], [self.ids['rouge.jpg'], self.ids['bleu.png']])
        self.assertEqual(r['refusees'], [{'id': self.ids['vert.jpg'], 'raison': self.serveur.PAS_MISE_A_LA_CORBEILLE}])
        self.assertIsNotNone(self.app.base.photo(self.ids['vert.jpg']))
        with mock.patch.object(self.serveur, 'infos_corbeille', return_value={'ok': True, 'raison': None,
                                                                             'capacite': None}), \
                mock.patch.object(self.serveur, 'mettre_a_la_corbeille', return_value=(0x4C7, True)):
            r = self.app.supprimer([self.ids['vert.jpg']], True)
        self.assertEqual(r['refusees'], [{'id': self.ids['vert.jpg'], 'raison': self.serveur.GARDEE}])
        self.assertTrue(self.fichiers['vert.jpg'].exists())

    def test_base_abimee_signalee_une_fois(self):
        self.app.base.fermer()
        for c in reversed(self.correctifs):
            c.stop()
        autre = self.tmp / 'abimee'
        autre.mkdir()
        (autre / 'index.db').write_bytes(b'ceci n\'est pas une base SQLite' * 100)
        self.correctifs = [mock.patch.object(self.serveur.config, 'BASE', autre / 'index.db'),
                           mock.patch.object(self.serveur.config, 'MINIATURES', autre / 'miniatures.db')]
        for c in self.correctifs:
            c.start()
        with self.assertLogs('myphotos.serveur', 'WARNING'):
            self.app = self.serveur.Application(serveur=None)
        b = self.app.etat()['base_ecartee']
        self.assertEqual((b['index'], b['miniatures']), (True, False))
        self.app.base.fermer()
        self.app = self.serveur.Application(serveur=None)                      # retenu tant que la page ne l'a pas dit
        self.assertTrue(self.app.etat()['base_ecartee']['index'])
        self.assertEqual(self.app.base_ecartee_vue(), {'ok': True})
        self.assertIsNone(self.app.etat()['base_ecartee'])

    def test_identifiants(self):
        lire = self.serveur.Gestionnaire._identifiants
        self.assertEqual(lire([3, '7', 3, 12]), [3, 7, 12])
        for mauvais in (None, [], [0], ['x'], [True], [2.0], list(range(1, 2002))):
            with self.assertRaises(self.serveur.Refus):
                lire(mauvais)


class FauxIndexeur:
    """Ce qu'Application demande à l'indexeur pour « Actualiser » et « Ne plus exclure »."""

    def __init__(self):
        self.demandes = []
        self.reprises = 0
        self.fait = 0
        self.phase = 'inventaire'
        self.bilan = None
        self.actif = True
        self.en_traitement = 0

    def reprendre(self):
        self.reprises += 1

    def relancer(self, erreurs=False, dossiers=None):
        self.demandes.append(dossiers)
        return len(self.demandes)

    def etat(self):
        return {'phase': self.phase, 'inventaire_fait': self.fait, 'bilan': self.bilan, 'message': None,
                'inventaire_en_cours': self.en_traitement,
                'dossier_courant': 'D:\\Photos', 'trouvees': 1234}


class TestDossiersEtActualiser(unittest.TestCase):
    """Actualiser, exclure un sous-dossier, assistant, photo introuvable : Application dans ce processus, base et
    photos générées dans un dossier temporaire ; la fenêtre « Choisir un dossier » est simulée."""

    def setUp(self):
        from PIL import Image
        from myphotos import serveur
        self.serveur = serveur
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-dossiers-'))
        self.photos = self.tmp / 'Images'
        self.fichiers = {}
        for nom in ('a.jpg', 'Vacances/b.jpg', 'Captures d\u2019écran/c1.png', 'Captures d\u2019écran/c2.png',
                    'Captures d\u2019écran/c3.png', 'Fonds d’écran/f.jpg'):
            p = self.photos / nom
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (160, 120), (200, 30, 30)).save(p)
            self.fichiers[nom] = p
        self.correctifs = [mock.patch.object(serveur.config, 'BASE', self.tmp / 'index.db'),
                           mock.patch.object(serveur.config, 'MINIATURES', self.tmp / 'miniatures.db'),
                           mock.patch.object(serveur, 'dossiers_proposes', lambda: []),
                           mock.patch.object(serveur, 'dossier_images', lambda: None)]
        for c in self.correctifs:
            c.start()
        self.app = serveur.Application(serveur=None)

    def tearDown(self):
        try:
            self.app.base.fermer()
        finally:
            for c in reversed(self.correctifs):
                c.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def connaitre(self, dossier_id):
        lignes = [(str(p), p.stat().st_size, p.stat().st_mtime_ns, None) for p in self.fichiers.values()]
        self.app.base.ajouter_photos(dossier_id, lignes)
        return {Path(ch).relative_to(self.photos).as_posix(): v[0]
                for ch, v in self.app.base.connus(dossier_id).items()}

    def test_assistant_examine_puis_ajoute_sans_les_captures(self):
        ex = self.app.examiner_dossier(str(self.photos) + '\\')
        self.assertEqual((ex['chemin'], ex['nom'], ex['total'], ex['complet']), (str(self.photos), 'Images', 6, True))
        par_nom = {s['nom']: s for s in ex['sous_dossiers']}
        self.assertEqual(set(par_nom), {'Captures d\u2019écran', 'Vacances', 'Fonds d’écran'})
        self.assertEqual((par_nom['Captures d\u2019écran']['photos'], par_nom['Captures d\u2019écran']['coche']), (3, False))
        self.assertIn('captures d\'écran', par_nom['Captures d\u2019écran']['raison'])
        self.assertIn('fonds d\'écran', par_nom['Fonds d’écran']['raison'])
        self.assertEqual((par_nom['Vacances']['coche'], par_nom['Vacances']['raison']), (True, None))
        ix = FauxIndexeur()
        self.app.indexeur = ix
        exclus = [s['chemin'] for s in ex['sous_dossiers'] if not s['coche']] + [str(self.tmp)]   # hors dossier : ignoré
        r = self.app.ajouter_dossier(ex['chemin'], exclus)
        self.assertEqual(sorted(r['exclus']), sorted(exclus[:2]))
        self.assertEqual(ix.demandes, [None])                                 # un tour, après les exclusions
        liste = self.app.liste_dossiers()['dossiers'][0]
        self.assertEqual(sorted(x['chemin'] for x in liste['exclusions']), sorted(exclus[:2]))
        for mauvais in (None, 'texte', [1, 2]):
            with self.assertRaises(self.serveur.Refus):
                self.app.ajouter_dossier(str(self.tmp / 'autre'), mauvais if mauvais != 'texte' else 'x')
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.examiner_dossier(str(self.photos / 'Vacances'))             # déjà dans MyPhotos
        self.assertEqual(e.exception.statut, 409)
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.examiner_dossier(str(self.photos / 'Captures d\u2019écran'))
        self.assertIn('Ne plus exclure', str(e.exception))

    def test_choisir_pour_l_assistant(self):
        self.app.choix_dossier = mock.Mock(demander=mock.Mock(return_value=str(self.photos).replace('\\', '/')))
        self.assertEqual(self.app.choisir_dossier(examiner=True), {'annule': False, 'chemin': str(self.photos)})
        self.assertEqual(self.app.base.dossiers(), [])                       # rien d'ajouté avant l'examen
        self.app.choix_dossier.demander.return_value = None
        self.assertEqual(self.app.choisir_dossier(examiner=True), {'annule': True})

    def test_exclure_par_la_fenetre(self):
        d = self.app.base.ajouter_dossier(str(self.photos))
        ids = self.connaitre(d['id'])
        ix = FauxIndexeur()
        self.app.indexeur = ix
        captures = self.photos / 'Captures d\u2019écran'
        self.app.choix_dossier = mock.Mock(demander=mock.Mock(return_value=str(captures)))
        r = self.app.choisir_exclusion(d['id'])
        options = self.app.choix_dossier.demander.call_args.kwargs
        self.assertEqual((options['initial'], options['titre']), (d['chemin'], 'Choisissez le sous-dossier à exclure'))
        self.assertEqual((r['annule'], r['photos'], r['exclusion']['chemin']), (False, 3, str(captures)))
        for nom in ('Captures d\u2019écran/c1.png', 'Captures d\u2019écran/c2.png', 'Captures d\u2019écran/c3.png'):
            self.assertIsNone(self.app.base.photo(ids[nom]))
            self.assertTrue(self.fichiers[nom].exists())                       # jamais supprimée du disque
        self.assertIsNotNone(self.app.base.photo(ids['a.jpg']))
        # Hors du dossier suivi : refus clair.
        self.app.choix_dossier.demander.return_value = str(self.tmp)
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.choisir_exclusion(d['id'])
        self.assertEqual(e.exception.statut, 409)
        self.assertIn('n\'est pas dans', str(e.exception))
        self.app.choix_dossier.demander.return_value = None
        self.assertEqual(self.app.choisir_exclusion(d['id']), {'annule': True})
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.choisir_exclusion(d['id'] + 50)
        self.assertEqual(e.exception.statut, 404)
        # « Ne plus exclure » : un tour de ce dossier seulement, pause levée.
        self.assertEqual(self.app.ne_plus_exclure(r['exclusion']['id'])['dossier_id'], d['id'])
        self.assertEqual((ix.demandes, ix.reprises), ([[d['id']]], 1))
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.ne_plus_exclure(r['exclusion']['id'])
        self.assertEqual(e.exception.statut, 404)

    def test_actualiser(self):
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.actualiser()                                             # pas encore de moteur
        self.assertEqual(e.exception.statut, 409)
        ix = FauxIndexeur()
        self.app.indexeur = ix
        r = self.app.actualiser()
        self.assertFalse(r['deja'])
        a = r['actualisation']
        self.assertEqual((a['numero'], a['en_cours']), (1, True))
        self.assertIn('Actualisation…', a['message'])
        self.assertIn('1 234 trouvées', a['message'])
        self.assertTrue(self.app.actualiser()['deja'])                       # un seul tour à la fois
        self.assertEqual((len(ix.demandes), ix.reprises), (1, 1))
        ix.fait = 1
        ix.phase = 'analyse'
        ix.bilan = {'numero': 1, 'nouvelles': 3, 'retirees': 5012, 'injoignables': ['E:\\Photos'], 'dossiers': 2}
        a = self.app.etat()['actualisation']
        self.assertEqual((a['en_cours'], a['nouvelles'], a['retirees']), (False, 3, 5012))
        self.assertTrue(a['message'].startswith('Actualisation terminée : 3 nouvelles photos, 5 012 photos retirées.'),
                        a['message'])
        self.assertIn('« E:\\Photos » est inaccessible', a['message'])
        self.assertFalse(self.app.actualiser()['deja'])                      # fini : un nouveau tour est possible
        self.assertEqual(len(ix.demandes), 2)
        ix.phase = 'erreur'                                                   # erreur d'un passage plus ancien
        self.assertTrue(self.app.etat()['actualisation']['en_cours'])
        ix.en_traitement = 2                                                  # erreur du passage demandé
        a = self.app.etat()['actualisation']
        self.assertFalse(a['en_cours'])

    def test_messages_actualisation(self):
        m = self.serveur.message_actualisation
        self.assertEqual(m({'nouvelles': 0, 'retirees': 0, 'injoignables': []}),
                         'Actualisation terminée : rien n\'a changé.')
        self.assertEqual(m({'nouvelles': 1, 'retirees': 1, 'injoignables': []}),
                         'Actualisation terminée : 1 nouvelle photo, 1 photo retirée. Elle va être analysée.')
        self.assertIn('2 dossiers sont inaccessibles', m({'nouvelles': 0, 'retirees': 0, 'injoignables': ['A', 'B']}))
        # Un autre disque occupe la lettre d'un dossier : dit à part, pas « disque débranché ? ».
        texte = m({'nouvelles': 0, 'retirees': 0, 'injoignables': ['E:\\Photos'], 'autres_disques': ['E:\\Photos']})
        self.assertIn('Un autre disque occupe la lettre du dossier « E:\\Photos »', texte)
        self.assertNotIn('débranché', texte)

    def test_photo_introuvable(self):
        d = self.app.base.ajouter_dossier(str(self.photos))
        ids = self.connaitre(d['id'])
        v = self.app.verifier_photo
        self.assertEqual(v(ids['a.jpg'])['etat'], 'presente')
        self.assertEqual(v(INCONNU), {'etat': 'inconnue', 'message': self.serveur.INTROUVABLE})
        # Fichier absent alors que le dossier suivi répond : supprimé, rangé ailleurs ou renommé, on ne peut pas le
        # savoir d'ici. Rien n'est oublié sur place ; un tour de ce dossier est demandé : il retrouve la photo à son
        # nouvel emplacement (avec son analyse), ou l'oublie si elle a vraiment été supprimée.
        ix = FauxIndexeur()
        self.app.indexeur = ix
        self.fichiers['a.jpg'].unlink()
        self.assertEqual(v(ids['a.jpg']), {'etat': 'deplacee', 'message': self.serveur.PHOTO_DEPLACEE})
        self.assertIsNotNone(self.app.base.photo(ids['a.jpg']))
        self.assertEqual(ix.demandes, [[d['id']]])
        # Sous-dossier entier disparu : pareil, pour Afficher dans le dossier (404, même message).
        shutil.rmtree(self.photos / 'Vacances')
        with self.assertRaises(self.serveur.Refus) as e:
            self.app.montrer(ids['Vacances/b.jpg'])
        self.assertEqual((e.exception.statut, str(e.exception)), (404, self.serveur.PHOTO_DEPLACEE))
        self.assertIsNotNone(self.app.base.photo(ids['Vacances/b.jpg']))
        self.assertEqual(ix.demandes, [[d['id']], [d['id']]])
        # Copier : refusée avec le même message ; rien n'est oublié, rien ne part au presse-papiers.
        self.fichiers['Captures d\u2019écran/c1.png'].unlink()
        with mock.patch.object(self.serveur, 'mettre_au_presse_papiers') as presse:
            with self.assertRaises(self.serveur.Refus) as e:
                self.app.copier([ids['Captures d\u2019écran/c1.png']])
        self.assertEqual((e.exception.statut, str(e.exception)), (409, self.serveur.PHOTO_DEPLACEE))
        presse.assert_not_called()
        self.assertIsNotNone(self.app.base.photo(ids['Captures d\u2019écran/c1.png']))
        # Dossier injoignable (disque débranché) : rien n'est oublié, message d'aujourd'hui.
        os.replace(self.photos, self.tmp / 'debranche')
        try:
            p = ids['Fonds d’écran/f.jpg']
            self.assertEqual(v(p), {'etat': 'injoignable', 'message': self.serveur.PHOTO_ABSENTE})
            with self.assertRaises(self.serveur.Refus) as e:
                self.app.ouvrir(p)
            self.assertEqual((e.exception.statut, str(e.exception)), (404, self.serveur.PHOTO_ABSENTE))
            self.assertIsNotNone(self.app.base.photo(p))
            # Dossier présent mais vide (partage réseau monté sans son disque) : rien n'est oublié non plus.
            self.photos.mkdir()
            self.assertEqual(v(p)['etat'], 'injoignable')
            self.assertIsNotNone(self.app.base.photo(p))
        finally:
            if self.photos.exists():
                self.photos.rmdir()
            os.replace(self.tmp / 'debranche', self.photos)

    def test_sous_dossier_renomme_puis_visionneuse(self):
        """Sous-dossier renommé, puis l'une de ses photos ouverte dans la visionneuse avant toute actualisation : rien
        n'est oublié (0.3.0 : la photo était oubliée, puis retrouvée comme nouvelle et analysée de nouveau). Le tour du
        dossier demandé aussitôt la retrouve sous son nouveau nom : même identifiant, analyse gardée, rien de refait."""
        from myphotos.indexation import Indexeur
        from tests.test_doublons import CLE, MoteurPixels

        class Moteur(MoteurPixels):
            images = 0

            def vecteurs_images(self, lot):
                Moteur.images += len(lot)
                return super().vecteurs_images(lot)

        d = self.app.base.ajouter_dossier(str(self.photos))
        ix = Indexeur(self.app.base, lambda materiel='auto': Moteur(), CLE, fils_decodage=2)
        self.app.indexeur = ix
        try:
            ix.relancer()
            self.assertTrue(ix.attendre(60), 'analyse trop longue')
            pid = {Path(ch).relative_to(self.photos).as_posix(): v[0]
                   for ch, v in self.app.base.connus(d['id']).items()}['Vacances/b.jpg']
            self.assertTrue(self.app.base.photo(pid)['analysee'])
            analysees = Moteur.images
            os.rename(self.photos / 'Vacances', self.photos / 'Vacances 2021')
            self.assertEqual(self.app.verifier_photo(pid)['etat'], 'deplacee')
            self.assertIsNotNone(self.app.base.photo(pid))
            self.assertTrue(ix.attendre(60), 'tour du dossier trop long')
            p = self.app.base.photo(pid)
            self.assertEqual(p['chemin'], str(self.photos / 'Vacances 2021' / 'b.jpg'))
            self.assertTrue(p['analysee'] and p['miniature'])
            self.assertEqual(Moteur.images, analysees)
            self.assertEqual(self.app.verifier_photo(pid)['etat'], 'presente')
        finally:
            ix.arreter()

    def test_photo_introuvable_autre_disque(self):
        """Visionneuse, fichier absent : rien n'est oublié quand un autre disque a pris la lettre de celui du dossier
        suivi (autre numéro de volume que celui retenu à l'ajout)."""
        d = self.app.base.ajouter_dossier(str(self.photos))
        ids = self.connaitre(d['id'])
        self.fichiers['a.jpg'].unlink()
        with mock.patch.object(self.serveur, 'identite_volume', lambda chemin: 0x1234):
            self.assertEqual(self.app.verifier_photo(ids['a.jpg'])['etat'], 'injoignable')
            with self.assertRaises(self.serveur.Refus) as e:
                self.app.ouvrir(ids['a.jpg'])
            self.assertEqual(e.exception.statut, 404)
        self.assertIsNotNone(self.app.base.photo(ids['a.jpg']))
        self.assertEqual(self.app.verifier_photo(ids['a.jpg'])['etat'], 'deplacee')       # le bon disque

    def test_photo_introuvable_desktop_ini(self):
        """Visionneuse, fichier absent : le dossier suivi qui ne contient plus que desktop.ini (dossier Images que
        OneDrive n'a pas encore rempli) compte comme débranché ; avec un vrai contenu en plus, il répond (« deplacee » :
        un tour de ce dossier tranchera)."""
        import images_essai as ie
        d = self.app.base.ajouter_dossier(str(self.photos))
        ids = self.connaitre(d['id'])
        # Le dossier suivi ne contient plus que desktop.ini (caché et système, comme Windows le pose).
        ailleurs = self.tmp / 'ailleurs'
        ailleurs.mkdir()
        for x in list(self.photos.iterdir()):
            os.replace(x, ailleurs / x.name)
        for sous in ('', 'Pellicule', 'Images enregistrées'):                  # comme Windows les crée
            (self.photos / sous).mkdir(exist_ok=True)
            ini = self.photos / sous / 'desktop.ini'
            ini.write_text('[.ShellClassInfo]\r\n', encoding='utf-8')
            ie.attribut(ini, ie.CACHE | ie.SYSTEME)
        for nom in ('a.jpg', 'Vacances/b.jpg'):
            self.assertEqual(self.app.verifier_photo(ids[nom])['etat'], 'injoignable', nom)
            self.assertIsNotNone(self.app.base.photo(ids[nom]))
        # Le même dossier avec un vrai contenu en plus : il répond.
        (self.photos / 'notes.txt').write_text('liste de courses', encoding='utf-8')
        self.assertEqual(self.app.verifier_photo(ids['a.jpg'])['etat'], 'deplacee')

    def test_photos_rangees_puis_visionneuse(self):
        """Photos rangées dans l'Explorateur (déplacées dans un sous-dossier nouveau ou existant, ou renommées sur
        place), puis ouvertes dans la visionneuse avant toute actualisation : rien n'est oublié (première 0.4.0 : la
        photo était oubliée sur-le-champ, puis retrouvée comme nouvelle et analysée de nouveau). Le tour du dossier
        demandé les retrouve : mêmes identifiants, analyses gardées, rien de refait ; une photo vraiment supprimée est
        oubliée par ce tour."""
        from myphotos.indexation import Indexeur
        from tests.test_doublons import CLE, MoteurPixels

        class Moteur(MoteurPixels):
            images = 0

            def vecteurs_images(self, lot):
                Moteur.images += len(lot)
                return super().vecteurs_images(lot)

        d = self.app.base.ajouter_dossier(str(self.photos))
        ix = Indexeur(self.app.base, lambda materiel='auto': Moteur(), CLE, fils_decodage=2)
        self.app.indexeur = ix
        try:
            ix.relancer()
            self.assertTrue(ix.attendre(60), 'analyse trop longue')
            ids = {Path(ch).relative_to(self.photos).as_posix(): v[0]
                   for ch, v in self.app.base.connus(d['id']).items()}
            analysees = Moteur.images
            (self.photos / 'Plage').mkdir()
            os.replace(self.photos / 'a.jpg', self.photos / 'Plage' / 'a.jpg')                  # nouveau sous-dossier
            os.replace(self.photos / 'Fonds d’écran' / 'f.jpg', self.photos / 'Vacances' / 'f.jpg')  # existant
            os.replace(self.photos / 'Vacances' / 'b.jpg', self.photos / 'Vacances' / 'Coucher de soleil.jpg')
            self.fichiers['Captures d\u2019écran/c1.png'].unlink()                               # vraiment supprimée
            for nom in ('a.jpg', 'Fonds d’écran/f.jpg', 'Vacances/b.jpg', 'Captures d\u2019écran/c1.png'):
                self.assertEqual(self.app.verifier_photo(ids[nom])['etat'], 'deplacee', nom)
                self.assertIsNotNone(self.app.base.photo(ids[nom]), nom)
            self.assertTrue(ix.attendre(60), 'tour du dossier trop long')
            for nom, ici in (('a.jpg', 'Plage/a.jpg'), ('Fonds d’écran/f.jpg', 'Vacances/f.jpg'),
                             ('Vacances/b.jpg', 'Vacances/Coucher de soleil.jpg')):
                p = self.app.base.photo(ids[nom])
                self.assertEqual(p['chemin'], str(self.photos / ici), nom)
                self.assertTrue(p['analysee'] and p['miniature'], nom)
            self.assertIsNone(self.app.base.photo(ids['Captures d\u2019écran/c1.png']))
            self.assertEqual(Moteur.images, analysees)
        finally:
            ix.arreter()


class TestDoublons(unittest.TestCase):
    """Recherche des doublons branchée dans l'application (dans ce processus) : photos générées par Pillow (jamais de
    vraies photos), analysées par le moteur factice de test_doublons ; Corbeille simulée (un dossier d'essai)."""

    def setUp(self):
        from PIL import Image
        from myphotos import doublons, serveur
        from myphotos.indexation import Indexeur
        from tests.test_doublons import CLE, DATE_EXIF, dessin, ecrire, fabrique
        self.serveur, self.doublons_mod, self.cle = serveur, doublons, CLE
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-doublons-serveur-'))
        self.photos = p = self.tmp / 'photos'
        a = dessin(1)
        ecrire(p / 'vacances' / 'IMG_0001.jpg', a, date=DATE_EXIF)
        shutil.copy2(p / 'vacances' / 'IMG_0001.jpg', p / 'vacances' / 'IMG_0001 - Copie.jpg')
        ecrire(p / 'WhatsApp Images' / 'IMG-20230501-WA0001.jpg',
               a.resize((a.width // 2, a.height // 2), Image.Resampling.LANCZOS), qualite=70)
        ecrire(p / 'divers' / 'C.jpg', dessin(3))
        ecrire(p / 'divers' / 'D.jpg', dessin(4))
        self.correctifs = [mock.patch.object(serveur.config, 'BASE', self.tmp / 'index.db'),
                           mock.patch.object(serveur.config, 'MINIATURES', self.tmp / 'miniatures.db'),
                           mock.patch.object(serveur, 'dossiers_proposes', lambda: []),
                           mock.patch.object(serveur, 'dossier_images', lambda: None)]
        for c in self.correctifs:
            c.start()
        self.app = serveur.Application(serveur=None)
        self.app.base.ajouter_dossier(str(p))
        ix = Indexeur(self.app.base, fabrique, CLE, fils_decodage=2)
        try:
            ix.relancer()
            self.assertTrue(ix.attendre(90), 'analyse trop longue')
        finally:
            ix.arreter(attendre=True)
        self.chemins = dict(self.app.base._cnx().execute('SELECT id, chemin FROM photos'))
        self.ids = {os.path.relpath(ch, p).replace('\\', '/'): pid for pid, ch in self.chemins.items()}
        self.corbeille = self.tmp / 'corbeille-simulee'
        self.corbeille.mkdir()

    def tearDown(self):
        try:
            self.app.fermer()
        finally:
            for c in reversed(self.correctifs):
                c.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def corbeille_simulee(self):
        essai = self

        def mettre(chemins):
            for c in chemins:
                shutil.move(c, essai.corbeille / Path(c).name)
            return 0, False
        return (mock.patch.object(self.serveur, 'infos_corbeille',
                                  return_value={'ok': True, 'raison': None, 'capacite': None}),
                mock.patch.object(self.serveur, 'mettre_a_la_corbeille', side_effect=mettre))

    def finir(self, avant=0, delai=60):
        """Attend la fin du calcul lancé ; renvoie le résumé de l'état (comme dans /api/etat)."""
        e = attendre(lambda: (lambda x: x if not x['en_cours'] and x['numero'] > avant else None)(
            self.app.etat_doublons()), delai, 0.05)
        self.assertTrue(e, f'recherche des doublons non terminée : {self.app.etat_doublons()}')
        return e

    def noms(self, groupe):
        inverse = {v: k for k, v in self.ids.items()}
        return {inverse[p['id']]: p['garder'] for p in groupe['photos']}

    def test_lancer_lire_puis_supprimer_depuis_un_groupe(self):
        app = self.app
        e = app.etat()['doublons']
        self.assertEqual((e['numero'], e['en_cours'], e['deja'], e['perime']), (0, False, False, False))
        self.assertFalse(app.lancer_doublons(auto=True)['lance'])     # jamais lancée : la page montre le bouton
        with mock.patch.object(app, '_cle_doublons', return_value=self.cle):
            r = app.lancer_doublons()
            self.assertTrue(r['lance'])
            e = self.finir()
        self.assertEqual((e['numero'], e['deja'], e['perime'], e['erreur']), (1, True, False, None))
        self.assertEqual(app.base.meta_get('doublons'), '1')
        d = app.doublons()
        self.assertEqual(d['numero'], 1)
        res = d['resultat']
        self.assertEqual(res['photos'], 5)
        par_type = {g['type']: g for g in res['groupes']}
        self.assertEqual(set(par_type), {'identiques', 'retouchees'})
        self.assertEqual(self.noms(par_type['identiques']),
                         {'vacances/IMG_0001.jpg': True, 'vacances/IMG_0001 - Copie.jpg': False})
        self.assertEqual(self.noms(par_type['retouchees']),
                         {'vacances/IMG_0001.jpg': True, 'WhatsApp Images/IMG-20230501-WA0001.jpg': False})
        for g in res['groupes']:
            self.assertEqual(g['photos'][0]['garder'], True)                    # la photo à garder d'abord
            self.assertEqual(g['gain_octets'], sum(p['taille'] for p in g['photos'] if not p['garder']))
            for p in g['photos']:
                self.assertEqual(p['dossier'], os.path.dirname(self.chemins[p['id']]))   # son dossier à elle
                self.assertNotIn('chemin', p)
                self.assertTrue(p['raison'])
        wa = next(p for p in par_type['retouchees']['photos'] if not p['garder'])
        self.assertEqual(os.path.basename(wa['dossier']), 'WhatsApp Images')
        self.assertNotIn('groupes', app.doublons(depuis=1)['resultat'])          # la page les a déjà

        # « Supprimer les copies cochées » : la route de la Corbeille, avec les identifiants du groupe.
        copies = [p['id'] for g in res['groupes'] for p in g['photos'] if not p['garder']]
        infos, mettre = self.corbeille_simulee()
        with infos, mettre:
            r = app.supprimer(copies, True)
        self.assertEqual(sorted(r['supprimees']), sorted(copies))
        self.assertEqual(sorted(x.name for x in self.corbeille.iterdir()),
                         ['IMG-20230501-WA0001.jpg', 'IMG_0001 - Copie.jpg'])
        self.assertTrue((self.photos / 'vacances' / 'IMG_0001.jpg').exists())    # l'originale reste
        # Tout de suite, avant tout nouveau calcul : les groupes ne montrent plus les photos parties.
        d = app.doublons()
        self.assertTrue(d['perime'])
        self.assertEqual(d['resultat']['groupes'], [])
        # L'index a changé : la page relance d'elle-même, le nouveau calcul ne trouve plus rien.
        with mock.patch.object(app, '_cle_doublons', return_value=self.cle):
            self.assertTrue(app.lancer_doublons(auto=True)['lance'])
            e = self.finir(avant=1)
        self.assertFalse(e['perime'])
        self.assertEqual(app.doublons()['resultat']['groupes'], [])
        self.assertFalse(app.lancer_doublons(auto=True)['lance'])                # à jour : rien à refaire

    def inverse(self, pid):
        return next(k for k, v in self.ids.items() if v == pid)

    def test_photo_a_garder_partie(self):
        """La photo à garder a été mise à la Corbeille (depuis la visionneuse) : une autre la remplace, jamais toutes
        proposées."""
        shutil.copy2(self.photos / 'vacances' / 'IMG_0001.jpg', self.photos / 'vacances' / 'IMG_0001 (2).jpg')
        self.ids.update(self.ajouter(['vacances/IMG_0001 (2).jpg']))
        self.app.lancer_doublons()
        self.finir()
        g = next(g for g in self.app.doublons()['resultat']['groupes'] if g['type'] == 'identiques')
        self.assertEqual(len(g['photos']), 3)
        garde = g['photos'][0]
        self.assertEqual(self.inverse(garde['id']), 'vacances/IMG_0001.jpg')
        infos, mettre = self.corbeille_simulee()
        with infos, mettre:
            self.assertEqual(self.app.supprimer([garde['id']], True)['supprimees'], [garde['id']])
        g = next(g for g in self.app.doublons()['resultat']['groupes'] if g['type'] == 'identiques')
        self.assertEqual(len(g['photos']), 2)
        self.assertEqual([p['garder'] for p in g['photos']], [True, False])
        self.assertEqual(g['photos'][0]['raison'], self.serveur.DOUBLONS_GARDER)
        self.assertEqual(g['gain_octets'], g['photos'][1]['taille'])

    def ajouter(self, relatifs):
        """Photos ajoutées après l'analyse (connues, pas encore analysées : sans miniature)."""
        lignes = []
        for r in relatifs:
            f = self.photos / r
            lignes.append((str(f), f.stat().st_size, f.stat().st_mtime_ns, None))
        dossier = self.app.base.dossiers()[0]['id']
        self.app.base.ajouter_photos(dossier, lignes)
        connues = {os.path.normcase(ch): v[0] for ch, v in self.app.base.connus(dossier).items()}
        return {r: connues[os.path.normcase(str(self.photos / r))] for r in relatifs}

    def test_jamais_bloquant_ni_moteur_ni_carte(self):
        """Pendant le calcul : l'état, les photos et leurs fiches répondent tout de suite ; aucun verrou de
        l'application n'est pris, ni celui de la carte graphique, et aucun moteur n'est chargé."""
        app = self.app
        porte, entree = threading.Event(), threading.Event()
        vrai = self.doublons_mod.Doublons._lire_photos

        def lire(d):
            entree.set()
            self.assertTrue(porte.wait(20))
            return vrai(d)
        with mock.patch.object(self.doublons_mod.Doublons, '_lire_photos', lire), \
                mock.patch.object(self.serveur.modele, 'Moteur', side_effect=AssertionError('moteur chargé')):
            with app._verrou_carte, app.verrou, app._verrou_suppression, app._verrou_copie:
                self.assertTrue(app.lancer_doublons()['lance'])
                self.assertTrue(entree.wait(20))
                self.assertIn('myphotos-doublons', [t.name for t in threading.enumerate()])
                debut = time.monotonic()
                e = app.etat()
                self.assertTrue(e['doublons']['en_cours'])
                self.assertEqual(e['doublons']['phase'], 'empreintes')
                self.assertEqual(app.chercher('', 0, 10)['total'], 5)
                self.assertEqual(app.info_photo(self.ids['divers/C.jpg'])['nom'], 'C.jpg')
                self.assertFalse(app.lancer_doublons()['lance'])                # une seule à la fois
                self.assertLess(time.monotonic() - debut, 2.0)
                porte.set()
                self.finir()
        self.assertTrue(app.doublons()['resultat']['groupes'])

    def test_annulation_et_relance(self):
        app = self.app
        entree = threading.Event()

        def lire(d):
            entree.set()
            self.assertTrue(app._doublons_arret.wait(20))                       # « Arrêter » attendu
            return {}
        with mock.patch.object(self.doublons_mod.Doublons, '_lire_photos', lire):
            app.lancer_doublons()
            self.assertTrue(entree.wait(20))
            self.assertEqual(app.annuler_doublons(), {'annule': True})
            self.assertTrue(attendre(lambda: not app.etat_doublons()['en_cours'], 20, 0.05))
        e = app.etat_doublons()
        self.assertEqual((e['numero'], e['erreur'], e['arretee']), (0, None, True))
        self.assertEqual(app.annuler_doublons(), {'annule': False})
        self.assertFalse(app.lancer_doublons(auto=True)['lance'])     # arrêtée par l'utilisateur : pas de relance seule
        self.assertTrue(app.lancer_doublons()['lance'])                 # un clic la relance
        self.assertFalse(self.finir()['arretee'])

    def test_relance_espacee_pendant_l_analyse_et_echec(self):
        app = self.app
        app.lancer_doublons()
        self.finir()
        app.base.marquer_modifiees([])                                   # l'index change (nouvelle génération)
        self.assertTrue(app.etat_doublons()['perime'])
        with mock.patch.object(app, '_analyse_active', return_value=True):
            self.assertFalse(app.lancer_doublons(auto=True)['lance'])   # analyse en cours, calcul tout récent
            with mock.patch.object(self.serveur, 'DOUBLONS_RELANCE_S', 0):
                self.assertTrue(app.lancer_doublons(auto=True)['lance'])
                self.finir(avant=1)
        # Un échec : message en français, pas de relance automatique immédiate.
        app.base.marquer_modifiees([])
        with mock.patch.object(self.doublons_mod.Doublons, 'calculer', side_effect=RuntimeError('boom')), \
                self.assertLogs('myphotos.serveur', 'ERROR'):
            self.assertTrue(app.lancer_doublons()['lance'])
            self.assertTrue(attendre(lambda: not app.etat_doublons()['en_cours'], 20, 0.05))
        e = app.etat_doublons()
        self.assertEqual(e['erreur'], self.serveur.DOUBLONS_ECHEC)
        self.assertNotIn('boom', json.dumps(app.doublons()))
        self.assertFalse(app.lancer_doublons(auto=True)['lance'])
        self.assertTrue(app.lancer_doublons()['lance'])
        self.finir(avant=2)
        self.assertIsNone(app.etat_doublons()['erreur'])

    def test_changement_de_moteur_et_arret_pendant_le_calcul(self):
        app = self.app
        with mock.patch.object(app, '_cle_doublons', return_value='a'):
            app.lancer_doublons()
            self.finir()
        premier = app._doublons
        with mock.patch.object(app, '_cle_doublons', return_value='b'):
            app.lancer_doublons()
            self.finir(avant=1)
        self.assertEqual(app._doublons.cle_modele, 'b')
        self.assertIsNot(app._doublons, premier)
        self.assertTrue(premier._fermee)                                  # l'ancien a rendu ses connexions
        # Arrêt de MyPhotos en plein calcul : le calcul s'interrompt, tout se ferme sans attendre la fin.
        entree = threading.Event()

        def lire(d):
            entree.set()
            app._doublons_arret.wait(20)
            return {}
        with mock.patch.object(self.doublons_mod.Doublons, '_lire_photos', lire):
            app.lancer_doublons()
            self.assertTrue(entree.wait(20))
            debut = time.monotonic()
            app.fermer()
        self.assertLess(time.monotonic() - debut, 10)
        self.assertFalse(app._doublons_fil.is_alive())
        with self.assertRaises(self.serveur.Refus):
            app.lancer_doublons()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('MYPHOTOS_ESSAI_WINDOWS') == '1',
                     'MYPHOTOS_ESSAI_WINDOWS=1 : vrais presse-papiers et Corbeille de Windows')
class TestWindowsReel(unittest.TestCase):
    """Les vrais appels à Windows, à la demande seulement (ils touchent le presse-papiers et la Corbeille de la session) :
    fichiers générés dans un dossier temporaire, nommés avec un marqueur unique. Le presse-papiers est collé par
    l'Explorateur lui-même (verbe « paste » du dossier, celui de Ctrl+V) ; la Corbeille n'est purgée que de ces
    fichiers-là, repérés par leur chemin d'origine."""

    def setUp(self):
        import uuid
        from PIL import Image
        self.marqueur = 'myphotos-essai-' + uuid.uuid4().hex[:10]
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-windows-'))
        self.chemins = []
        for i in range(2):
            p = self.tmp / f'{self.marqueur}-{i + 1}.jpg'
            Image.new('RGB', (200, 150), (60 * i + 40, 120, 180)).save(p)
            self.chemins.append(p)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def powershell(script):
        return subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', script], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=120).stdout

    def test_presse_papiers_puis_ctrl_v_dans_l_explorateur(self):
        from myphotos import serveur
        dest = self.tmp / 'collees'
        dest.mkdir()
        # Le texte que l'utilisateur avait copié est remis à la fin.
        avant = self.powershell('$t = Get-Clipboard -Raw -Format Text; if ($t) { [Convert]::ToBase64String('
                                '[Text.Encoding]::UTF8.GetBytes($t)) }').strip()
        try:
            serveur.PressePapiers().copier([str(p) for p in self.chemins])
            lus = self.powershell('Get-Clipboard -Format FileDropList | ForEach-Object { $_.FullName }').split()
            self.assertEqual([Path(x).name for x in lus], [p.name for p in self.chemins])
            self.powershell(f"(New-Object -ComObject Shell.Application).NameSpace('{dest}').Self.InvokeVerb('paste');"
                            " Start-Sleep -Seconds 3")
            self.assertTrue(attendre(lambda: len(list(dest.glob('*.jpg'))) == 2, 15))
            for p in self.chemins:
                self.assertEqual((dest / p.name).read_bytes(), p.read_bytes())
        finally:
            if avant:
                texte = self.tmp / 'presse-papiers.txt'
                texte.write_text(base64.b64decode(avant).decode('utf-8'), encoding='utf-8')
                self.powershell(f"Set-Clipboard -Value (Get-Content -Raw -Encoding UTF8 '{texte}')")

    def test_corbeille(self):
        from myphotos import serveur
        racine = serveur.racine_volume(str(self.chemins[0]))
        self.assertTrue(serveur.infos_corbeille(racine)['ok'])
        self.assertEqual(serveur.mettre_a_la_corbeille([str(p) for p in self.chemins]), (0, False))
        self.assertFalse(any(p.exists() for p in self.chemins))
        trouves = []
        for i in Path(racine, '$Recycle.Bin').glob('*/$I*'):
            try:
                d = i.read_bytes()
            except OSError:
                continue
            n = struct.unpack_from('<i', d, 24)[0] if struct.unpack_from('<q', d, 0)[0] == 2 else 260
            origine = d[28:28 + 2 * n].decode('utf-16-le', 'replace').split('\0')[0] if n != 260 else \
                d[24:544].decode('utf-16-le', 'replace').split('\0')[0]
            if self.marqueur in Path(origine).name and Path(origine).parent == self.tmp:
                trouves.append(i)
        self.assertEqual(len(trouves), 2, 'fichiers absents de la Corbeille')
        for i in trouves:                                              # purge : nos deux fichiers d'essai, rien d'autre
            for f in (i.with_name('$R' + i.name[2:]), i):
                f.unlink(missing_ok=True)


class TestChoixDossier(unittest.TestCase):
    """Fenêtre « Choisir un dossier » des replis (serveur.ChoixDossier) : un fil COM à elle par demande, une fenêtre à
    la fois, annuler ne fait rien, une panne donne un message clair. La fenêtre de Windows (dialogue.ouvrir_devant)
    est simulée : rien n'apparaît sur le bureau pendant ces essais."""

    def setUp(self):
        from myphotos import serveur
        self.serveur = serveur
        self.reponses = []
        self.appels = []
        self.ouverte = threading.Event()
        self.relacher = threading.Event()

        def ouvrir(titre, initial, zone):
            self.appels.append((titre, initial, zone, threading.current_thread().name))
            self.ouverte.set()
            self.relacher.wait(10)
            r = self.reponses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        self.ouvrir = ouvrir

    def tearDown(self):
        self.relacher.set()

    def test_choix_annulation_et_fenetre_unique(self):
        choix = self.serveur.ChoixDossier(ouvrir=self.ouvrir)
        self.reponses = ['E:\\Photos', None, '', OSError('COM en panne'), 'D:\\Vacances \U0001F334']
        resultats = []
        fil = threading.Thread(target=lambda: resultats.append(choix.demander(initial='C:\\Images')))
        fil.start()
        self.assertTrue(self.ouverte.wait(10))
        with self.assertRaises(self.serveur.Refus) as e:            # une seule fenêtre à la fois
            choix.demander()
        self.assertEqual(e.exception.statut, 409)
        self.relacher.set()
        fil.join(10)
        self.assertEqual(resultats, ['E:\\Photos'])
        self.assertEqual(self.appels[0][:3], ('Choisissez un dossier de photos', 'C:\\Images', None))
        self.assertEqual(self.appels[0][3], 'myphotos-choix-dossier')          # son propre fil (COM STA)
        self.assertIsNone(choix.demander())                          # annuler : rien
        self.assertIsNone(choix.demander())
        with self.assertRaises(self.serveur.Refus) as e:             # panne : message clair (comme dans la fenêtre)…
            choix.demander()
        self.assertEqual((e.exception.statut, str(e.exception)),
                         (503, "La fenêtre de choix d'un dossier n'a pas pu s'ouvrir."))
        self.assertEqual(choix.demander(titre='Choisissez où copier les photos'), 'D:\\Vacances \U0001F334')
        self.assertEqual(self.appels[-1][0], 'Choisissez où copier les photos')   # … et la suivante fonctionne

    def test_place_de_la_fenetre_d_edge(self):
        """Mode « edge » : la fenêtre s'ouvre au milieu de la fenêtre d'Edge (FenetreEdge.zone) ; une zone illisible
        ne l'empêche pas de s'ouvrir."""
        self.relacher.set()
        choix = self.serveur.ChoixDossier(ouvrir=self.ouvrir)
        self.reponses = ['E:\\A', 'E:\\B']
        choix.zone = lambda: (10, 20, 810, 620)
        self.assertEqual(choix.demander(), 'E:\\A')
        self.assertEqual(self.appels[-1][2], (10, 20, 810, 620))
        choix.zone = mock.Mock(side_effect=OSError('fenêtre disparue'))
        self.assertEqual(choix.demander(), 'E:\\B')
        self.assertIsNone(self.appels[-1][2])

    def test_delai_depasse(self):
        """Fenêtre laissée ouverte au-delà du délai : la requête répond « annulé » ; la fenêtre, toujours ouverte, en
        refuse une seconde jusqu'à sa fermeture."""
        choix = self.serveur.ChoixDossier(ouvrir=self.ouvrir)
        self.reponses = ['E:\\Tard']
        self.assertIsNone(choix.demander(delai=0.2))
        with self.assertRaises(self.serveur.Refus):
            choix.demander()
        self.relacher.set()
        self.assertTrue(attendre(lambda: not choix._ouverte.is_set(), 10))

    def test_par_defaut_la_fenetre_de_windows(self):
        from myphotos import dialogue
        self.assertIs(self.serveur.ChoixDossier()._ouvrir, dialogue.ouvrir_devant)
        source = (RACINE / 'myphotos' / 'serveur.py').read_text(encoding='utf-8')
        self.assertNotIn('import tkinter', source)
        self.assertNotIn('filedialog', source)

    def test_annuler_ne_fait_rien(self):
        app = self.serveur.Application.__new__(self.serveur.Application)
        app.choix_dossier = mock.Mock(demander=mock.Mock(return_value=None))
        app.ajouter_dossier = mock.Mock()
        self.assertEqual(app.choisir_dossier(), {'annule': True})
        app.ajouter_dossier.assert_not_called()


class FauxCom:
    """Couche COM simulée pour dialogue.py : CoCreateInstance, tables virtuelles (_methode), Release,
    SHCreateItemFromParsingName et CoTaskMemFree. Objets : 1, la fenêtre (IFileOpenDialog) ; 2, le dossier proposé ;
    3, le dossier choisi (IShellItem)."""

    def __init__(self, show=0, chemin='E:\\Photos', creer=0, dossier_lisible=True, nom=0):
        from myphotos import dialogue
        self.d = dialogue
        self.show, self.chemin, self.creer, self.dossier_lisible, self.nom = show, chemin, creer, dossier_lisible, nom
        self.journal = []
        self.liberes = []
        self.textes_liberes = []
        self.adresse = None
        self._garde = []

    @staticmethod
    def guid(ref):
        import uuid
        return str(uuid.UUID(bytes_le=bytes(ref._obj))).upper()

    # ole32
    def CoCreateInstance(self, clsid, exterieur, contexte, iid, sortie):
        self.journal.append(('CoCreateInstance', self.guid(clsid), contexte, self.guid(iid)))
        if self.creer < 0:
            return self.creer
        sortie._obj.value = 1
        return 0

    def CoTaskMemFree(self, texte):
        self.textes_liberes.append(texte.value)

    # shell32
    def SHCreateItemFromParsingName(self, chemin, contexte, iid, sortie):
        self.journal.append(('SHCreateItemFromParsingName', chemin, self.guid(iid)))
        if not self.dossier_lisible:
            return 0x80070002 - (1 << 32)                    # HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND)
        sortie._obj.value = 2
        return 0

    def dll(self, _nom):
        return self

    def methode(self, objet, index, *_types):
        import ctypes
        o = objet.value
        d = self.d

        def appel(*valeurs):
            if index == 2:                                   # Release
                self.liberes.append(o)
                return 0
            if o == 1 and index == d.GET_OPTIONS:
                valeurs[0]._obj.value = 0x1000               # FOS_FILEMUSTEXIST, comme Windows
            elif o == 1 and index == d.SET_OPTIONS:
                self.journal.append(('SetOptions', valeurs[0]))
            elif o == 1 and index == d.SET_CLIENT_GUID:
                self.journal.append(('SetClientGuid', self.guid(valeurs[0])))
            elif o == 1 and index == d.SET_TITLE:
                self.journal.append(('SetTitle', valeurs[0]))
            elif o == 1 and index == d.SET_FOLDER:
                self.journal.append(('SetFolder', valeurs[0].value))
            elif o == 1 and index == d.SHOW:
                self.journal.append(('Show', valeurs[0]))
                return self.show
            elif o == 1 and index == d.GET_RESULT:
                valeurs[0]._obj.value = 3
                self.journal.append(('GetResult',))
            elif o == 3 and index == d.GET_DISPLAY_NAME:
                self.journal.append(('GetDisplayName', valeurs[0]))
                if self.nom < 0:
                    return self.nom
                tampon = ctypes.create_unicode_buffer(self.chemin)
                self._garde.append(tampon)
                self.adresse = ctypes.addressof(tampon)
                valeurs[1]._obj.value = self.adresse
            else:
                raise AssertionError(f'méthode inattendue : objet {o}, n° {index}')
            return 0
        return appel

    def __enter__(self):
        from myphotos import raccourci
        self.patchs = [mock.patch.object(self.d, '_methode', self.methode),
                       mock.patch.object(raccourci, '_methode', self.methode),      # Release (_liberer)
                       mock.patch.object(self.d, '_ole32', lambda: self),
                       mock.patch.object(self.d, '_dll', self.dll)]
        for p in self.patchs:
            p.start()
        return self

    def __exit__(self, *_):
        for p in self.patchs:
            p.stop()

    def noms(self):
        return [x[0] for x in self.journal]


class TestDialogueCom(unittest.TestCase):
    """dialogue.choisir_dossier, couche COM simulée (FauxCom) : réglages de la fenêtre de Windows, dossier proposé,
    propriétaire, annulation, pannes, et chaque objet COM libéré."""

    ANNULE = 0x800704C7 - (1 << 32)                          # HRESULT tel que ctypes le rend (c_long)
    E_FAIL = 0x80004005 - (1 << 32)

    def setUp(self):
        from myphotos import dialogue
        self.d = dialogue

    def test_dossier_choisi(self):
        with FauxCom(chemin='E:\\Photos d\u2019été \U0001F334') as com:
            r = self.d.choisir_dossier('Choisissez un dossier de photos', 'C:\\Images', proprietaire=4242)
        self.assertEqual(r, 'E:\\Photos d\u2019été \U0001F334')
        self.assertEqual(com.journal[0], ('CoCreateInstance', self.d.CLSID_FILEOPENDIALOG.strip('{}'), 1,
                                          self.d.IID_IFILEOPENDIALOG.strip('{}')))
        options = next(x[1] for x in com.journal if x[0] == 'SetOptions')
        for drapeau in (0x20, 0x40, 0x800, 0x8, 0x2000000, 0x1000):   # dossiers, du disque, existant… et Windows
            self.assertTrue(options & drapeau, hex(drapeau))
        self.assertIn(('SetTitle', 'Choisissez un dossier de photos'), com.journal)
        self.assertIn(('SetClientGuid', self.d.CLIENT.strip('{}')), com.journal)
        self.assertIn(('SHCreateItemFromParsingName', 'C:\\Images', self.d.IID_ISHELLITEM.strip('{}')), com.journal)
        self.assertEqual(com.noms()[-4:], ['SetFolder', 'Show', 'GetResult', 'GetDisplayName'])
        self.assertIn(('SetFolder', 2), com.journal)
        self.assertIn(('Show', 4242), com.journal)                    # la fenêtre hôte en est la propriétaire
        self.assertIn(('GetDisplayName', self.d.SIGDN_FILESYSPATH), com.journal)
        self.assertEqual(sorted(com.liberes), [1, 2, 3])               # chaque objet COM libéré, une fois
        self.assertEqual(com.textes_liberes, [com.adresse])            # chemin rendu par Windows libéré

    def test_annuler(self):
        with FauxCom(show=self.ANNULE) as com:
            self.assertIsNone(self.d.choisir_dossier('T', None))
        self.assertNotIn('GetResult', com.noms())
        self.assertNotIn('SHCreateItemFromParsingName', com.noms())    # aucun dossier proposé
        self.assertEqual(com.liberes, [1])

    def test_pannes(self):
        with FauxCom(show=self.E_FAIL) as com:
            with self.assertRaisesRegex(OSError, 'Show : erreur 0x80004005'):
                self.d.choisir_dossier('T', 'C:\\Images')
        self.assertEqual(sorted(com.liberes), [1, 2])
        with FauxCom(creer=self.E_FAIL) as com:
            with self.assertRaisesRegex(OSError, 'FileOpenDialog'):
                self.d.choisir_dossier('T', 'C:\\Images')
        self.assertEqual(com.liberes, [])
        with FauxCom(nom=self.E_FAIL) as com:
            with self.assertRaisesRegex(OSError, 'GetDisplayName'):
                self.d.choisir_dossier('T', None)
        self.assertEqual(sorted(com.liberes), [1, 3])

    def test_dossier_propose_disparu(self):
        """Dossier proposé débranché ou supprimé : la fenêtre s'ouvre quand même (là où Windows l'avait laissée)."""
        with FauxCom(dossier_lisible=False) as com:
            self.assertEqual(self.d.choisir_dossier('T', 'Z:\\Débranché'), 'E:\\Photos')
        self.assertNotIn('SetFolder', com.noms())
        self.assertIn('Show', com.noms())

    def test_position_de_l_hote(self):
        """La fenêtre de Windows s'ouvre avec son coin sur celui de son hôte, à la moitié de la zone de travail :
        l'hôte est posé pour qu'elle soit au milieu de la fenêtre de MyPhotos (ou de l'écran), sans sortir de
        l'écran."""
        travail = (0, 0, 1920, 1040)
        self.assertEqual(self.d.position_hote(None, travail), (480, 260))
        self.assertEqual(self.d.position_hote((1000, 100, 1800, 900), travail), (920, 240))
        self.assertEqual(self.d.position_hote((0, 0, 200, 200), travail), (0, 0))           # jamais hors de l'écran
        self.assertEqual(self.d.position_hote((-1920, 0, 0, 1040), (-1920, 0, 0, 1040)), (-1440, 260))
        self.assertEqual(self.d.position_hote((5, 5, 5, 5), travail), (480, 260))          # zone vide : l'écran

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_vraie_classe_com_sans_afficher(self):
        """La vraie fenêtre de Windows, créée et réglée SANS être affichée (Show n'est jamais appelé), dans un fil
        STA : classe, interfaces et ordre des méthodes justes ; options relues."""
        res = {}

        def fil():
            try:
                with self.d.Sta():
                    res['options'] = self.d.verifier('Essai', tempfile.gettempdir())
                    res['absent'] = self.d.verifier('Essai', str(Path(tempfile.gettempdir()) / 'absent-myphotos'))
            except Exception as e:
                res['erreur'] = e
        t = threading.Thread(target=fil)
        t.start()
        t.join(30)
        self.assertNotIn('erreur', res)
        for cle in ('options', 'absent'):
            self.assertEqual(res[cle] & self.d.OPTIONS, self.d.OPTIONS, hex(res[cle]))


class TestOutils(unittest.TestCase):

    def test_sans_jargon(self):
        from myphotos.serveur import sans_jargon
        self.assertEqual(sans_jargon('Le modèle « Standard » convient.'), 'Le moteur « Standard » convient.')
        self.assertEqual(sans_jargon("L'indexation s'est arrêtée ; Modèles proposés."),
                         "L'analyse s'est arrêtée ; Moteurs proposés.")
        self.assertIsNone(sans_jargon(None))
        self.assertEqual(sans_jargon('Ce dossier est déjà dans la photothèque : « E:\\X ».'),
                         'Ce dossier est déjà dans MyPhotos : « E:\\X ».')
        self.assertEqual(sans_jargon('Une version plus récente de MyPhotos.'), 'Une version plus récente de MyPhotos.')
        # Jamais de message brut en anglais dans la page (exception glissée dans un message du cœur).
        self.assertEqual(sans_jargon("L'indexation s'est arrêtée sur une erreur inattendue : database is locked"),
                         "L'analyse s'est arrêtée sur une erreur inattendue : les fichiers de MyPhotos sont "
                         "momentanément occupés")
        self.assertEqual(sans_jargon('database is locked'), 'Les fichiers de MyPhotos sont momentanément occupés')
        self.assertEqual(sans_jargon('disk I/O error'), "Erreur de lecture ou d'écriture sur le disque")
        self.assertEqual(sans_jargon("Le moteur de recherche n'a pas pu démarrer ([ONNXRuntimeError] : 6 : "
                                     "RUNTIME_EXCEPTION : Non-zero status code returned while running Conv node)."),
                         "Le moteur de recherche n'a pas pu démarrer (erreur interne du moteur).")
        self.assertEqual(sans_jargon('[WinError 32] Le processus ne peut pas accéder au fichier'),
                         'Le processus ne peut pas accéder au fichier')
        for anglais in ('database or disk is full', 'unable to open database file', 'file is not a database',
                        'attempt to write a readonly database', 'database disk image is malformed',
                        'no such table: photos', 'out of memory', 'database table is locked'):
            self.assertNotRegex(sans_jargon(anglais), r'\b(?:database|disk|file|table|memory|unable|attempt)\b',
                                anglais)

    def test_base_occupee_jamais_en_anglais(self):
        """Une requête qui bute sur « database is locked » répond en français, sans le message brut."""
        import sqlite3
        from myphotos import serveur
        import email.message
        g = serveur.Gestionnaire.__new__(serveur.Gestionnaire)
        g.server = types.SimpleNamespace(hotes={'h'}, jeton='j' * 43, server_port=1)
        g.headers = email.message.Message()
        g.headers['Host'] = 'h'
        g.headers['Cookie'] = 'myphotos-1=' + 'j' * 43                 # jeton d'accès (0.4.0)
        g.path = '/api/etat'
        g.close_connection = False
        envoye = []
        g._erreur = lambda statut, message, fermer=False: envoye.append((statut, message))
        app = mock.Mock()
        app.etat.side_effect = sqlite3.OperationalError('database is locked')
        g._aiguiller(app, 'GET')
        self.assertEqual(envoye, [(503, 'Les fichiers de MyPhotos sont momentanément occupés : réessayez dans un '
                                        'moment.')])
        envoye.clear()
        app.etat.side_effect = sqlite3.OperationalError('cannot rollback - no transaction is active')
        g._aiguiller(app, 'GET')
        self.assertEqual(envoye[0][0], 503)
        self.assertNotIn('transaction', envoye[0][1])

    def test_resume_materiel(self):
        from myphotos.serveur import resume_materiel
        lignes = resume_materiel({
            'systeme': {'edition': 'Windows 11 Famille'},
            'processeur': {'nom': 'Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz', 'coeurs': 4},
            'memoire': {'installee_go': 8.0}, 'cartes': [{'nom': 'Intel UHD 620', 'memoire_dediee_go': 0.1},
                                                         {'nom': 'Microsoft Basic Render Driver', 'logicielle': True}],
            'disque': {'libre_go': 41.6, 'lecteur': 'C:'}})
        d = {l['libelle']: l['valeur'] for l in lignes}
        self.assertEqual(d['Processeur'], 'Intel Core i5-8250U (4 cœurs)')
        self.assertEqual(d['Mémoire'], '8 Go')
        self.assertEqual(d['Carte graphique'], 'Intel UHD 620')
        self.assertEqual(d['Place libre'], '42 Go sur le disque C:')
        self.assertEqual(resume_materiel(None), [])


class TestDemarrage(unittest.TestCase):
    """Lancement avec un moteur déjà choisi : la page et /api/etat répondent avant que le moteur ne se charge
    (« préparation ») ; une recherche lancée pendant ce temps attend sans erreur, puis aboutit. Vrai serveur, vrai
    moteur « base », sur un autre port et d'autres données ; dossiers proposés neutralisés."""

    PORT3 = PORT + 2

    def test_page_avant_le_moteur(self):
        if not port_libre(self.PORT3):
            self.skipTest(f'port {self.PORT3} déjà pris')
        from myphotos import base
        donnees = DONNEES / 'essai-demarrage'
        donnees.mkdir(parents=True, exist_ok=True)
        b = base.Base(donnees / 'index.db', donnees / 'miniatures.db')
        try:
            for cle, valeur in (('moteur', 'base'), ('moteur_vision', 'fp16'), ('moteur_materiel', 'auto'),
                                ('assistant', 'fini'), ('telechargement', None)):
                b.meta_set(cle, valeur)
        finally:
            b.fermer()
        hote = f'127.0.0.1:{self.PORT3}'
        proc = lancer_serveur(donnees, self.PORT3, neutre=True)
        try:
            # Le port s'ouvre avant que MyPhotos ne serve : on attend qu'il accepte, sans requête HTTP.
            fin = time.monotonic() + 40
            while port_libre(self.PORT3):
                self.assertIsNone(proc.poll(), 'le serveur s\'est arrêté au démarrage')
                self.assertLess(time.monotonic(), fin, 'le serveur ne s\'ouvre pas')
                time.sleep(0.05)
            debut = time.monotonic()
            statut, _, _ = requete('GET', '/', hote=hote, port=self.PORT3)
            duree_page = time.monotonic() - debut
            self.assertEqual(statut, 200)
            statut, e = api('/api/etat', hote=hote, port=self.PORT3)
            self.assertEqual(statut, 200)
            # Rien ne se charge avant la première réponse à la page, puis un moment de calme : encore en préparation.
            self.assertTrue(e['preparation'], e)
            self.assertFalse(e['recherche_prete'])
            self.assertEqual((e['moteur']['cle'], e['moteur']['pret']), ('base', False))
            statut, r = api('/api/recherche?q=chien&n=10', hote=hote, port=self.PORT3)
            self.assertEqual(statut, 200, r)
            self.assertTrue(r['preparation'])
            self.assertEqual((r['sures'], r['peut_etre']), ([], []))
            statut, r = api('/api/recherche?q=chien&proches=1&n=60', hote=hote, port=self.PORT3)
            self.assertEqual((statut, r['mode'], r['preparation'], r['proches']), (200, 'proches', True, []))
            self.assertLess(duree_page, 3.0, 'la page attend le moteur')
            # Puis le moteur se prépare tout seul, et la même recherche aboutit.
            e = attendre(lambda: (lambda x: x if x['recherche_prete'] else None)(
                api('/api/etat', hote=hote, port=self.PORT3)[1]), 90)
            self.assertTrue(e, 'le moteur ne s\'est pas préparé')
            self.assertFalse(e['preparation'])
            self.assertTrue(e['moteur']['pret'])
            statut, r = api('/api/recherche?q=chien&n=10', hote=hote, port=self.PORT3)
            self.assertEqual((statut, r['mode'], r['preparation']), (200, 'recherche', False), r)
            self.assertIsNone(proc.poll())
        finally:
            try:
                if proc.poll() is None:
                    api('/api/quitter', {}, hote=hote, port=self.PORT3)
                    proc.wait(timeout=90)
            except Exception:
                pass
            finally:
                arreter_essai(proc)
                proc.sortie.close()
        self.assertEqual(proc.returncode, 0)


class EssaiApplication(unittest.TestCase):
    """Application dans ce processus, sur une base temporaire. Moteur, matériel et dossiers proposés simulés : rien
    n'est chargé ni parcouru. Le faux moteur attend self.barriere avant d'être « chargé »."""

    def setUp(self):
        import numpy as np
        from myphotos import serveur
        self.serveur = serveur
        self.tmp = Path(tempfile.mkdtemp(prefix='myphotos-preparation-'))
        self.barriere = threading.Event()
        self.crees = []
        essai = self

        class FauxMoteur:
            def __init__(self, cle=None, vision=None, texte=None, materiel='auto', carte=0, **options):
                essai.crees.append({'cle': cle, 'vision': vision, 'materiel': materiel, 'carte': carte})
                essai.barriere.wait(20)                     # chargement « long », relâché par l'essai
                self.cle, self.carte = cle, carte
                self.materiel = self.materiel_texte = 'processeur'

            def vecteurs_textes(self, textes):
                v = np.zeros((len(textes), 8), np.float32)
                v[:, 0] = 1
                return v

            def vecteurs_images(self, lot):
                v = np.zeros((len(lot), 8), np.float32)
                v[:, 0] = 1
                return v

        conseil = {'cle': 'base', 'vision': 'fp16', 'materiel': 'carte', 'indice_carte': 1, 'alternatives': [
            {'cle': 'rapide', 'vision': 'fp16', 'materiel': 'carte', 'indice_carte': 1}]}
        self.correctifs = [
            mock.patch.object(serveur.config, 'BASE', self.tmp / 'index.db'),
            mock.patch.object(serveur.config, 'MINIATURES', self.tmp / 'miniatures.db'),
            mock.patch.object(serveur.modele, 'Moteur', FauxMoteur),
            mock.patch.object(serveur.modele, 'manquants', lambda *a, **k: []),
            mock.patch.object(serveur, 'dossiers_proposes', lambda: []),
            mock.patch.object(serveur, 'dossier_images', lambda: None),
            mock.patch.object(serveur.materiel, 'analyser', lambda n=None: {'directml': True}),
            mock.patch.object(serveur.materiel, 'recommander', lambda analyse, n=None: conseil),
            mock.patch.object(serveur, 'SANS_PAGE', True),
        ]
        for c in self.correctifs:
            c.start()
        self.app = serveur.Application(serveur=mock.Mock())

    def tearDown(self):
        self.barriere.set()
        try:
            self.app.fermer()
        finally:
            for c in reversed(self.correctifs):
                c.stop()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def prete(self, cle=None):
        e = self.app.etat()
        return (e['recherche_prete'] and not self.app.changement and (cle is None or e['moteur']['cle'] == cle)
                and e)


class TestPreparation(EssaiApplication):

    def test_rien_avant_la_page_puis_preparation(self):
        app = self.app
        for cle, valeur in (('moteur', 'base'), ('moteur_vision', 'fp16'), ('moteur_carte', 1),
                            ('assistant', 'fini')):
            app.base.meta_set(cle, valeur)
        app.demarrer()
        e = app.etat()
        self.assertTrue(e['preparation'])
        self.assertFalse(e['recherche_prete'])
        self.assertFalse(e['moteur']['pret'])
        # Une recherche pendant la préparation : réponse vide marquée « préparation », jamais une erreur.
        r = app.chercher('chien', 0, 10)
        self.assertEqual((r['mode'], r['preparation'], r['sures'], r['peut_etre']), ('recherche', True, [], []))
        r = app.chercher('chien', 0, 60, proches=True)
        self.assertEqual((r['mode'], r['preparation'], r['proches']), ('proches', True, []))
        self.assertEqual(app.chercher('', 0, 10)['mode'], 'dates')
        time.sleep(0.6)
        self.assertEqual(self.crees, [], 'moteur chargé avant que le serveur ne soit prêt')
        app.serveur_pret()
        time.sleep(0.6)
        self.assertEqual(self.crees, [], 'moteur chargé avant la première réponse à la page')
        app.page_servie()
        self.assertTrue(attendre(lambda: self.crees, 5, 0.05), 'le moteur ne se charge pas')
        self.assertTrue(app.etat()['preparation'])          # chargement en cours : toujours « préparation »
        self.assertTrue(app.chercher('chien', 0, 10)['preparation'])
        self.barriere.set()
        e = attendre(self.prete, 10, 0.05)
        self.assertTrue(e, 'la préparation ne finit pas')
        self.assertFalse(e['preparation'])
        self.assertTrue(e['moteur']['pret'])
        r = app.chercher('chien', 0, 10)
        self.assertEqual((r['mode'], r['preparation'], r['total_proches']), ('recherche', False, 0))
        # Un seul chargement, partagé par l'analyse et la recherche, sur la carte mémorisée avec le moteur.
        self.assertEqual(self.crees, [{'cle': 'base', 'vision': 'fp16', 'materiel': 'auto', 'carte': 1}])
        self.assertTrue(attendre(lambda: app.indexeur.actif or app.etat()['analyse']['phase'] == 'termine', 5, 0.05))

    def test_sans_page_le_moteur_se_prepare_quand_meme(self):
        app = self.app
        app.base.meta_set('moteur', 'base')
        app.demarrer()
        self.barriere.set()
        app.serveur_pret()                                   # aucune page : préparation au bout de 2 s
        self.assertTrue(attendre(self.prete, 10, 0.1))
        self.assertEqual(self.crees[0]['carte'], 0)          # aucune carte mémorisée : la carte principale

    def test_carte_conseillee_memorisee(self):
        app = self.app
        app.base.meta_set('assistant', 'fini')
        app.demarrer()
        self.barriere.set()
        app.serveur_pret()
        app.page_servie()
        self.assertEqual(app.choisir_moteur('base', 'fp16', 'carte'), {'deja': False})
        self.assertTrue(attendre(lambda: self.prete('base'), 15, 0.05))
        self.assertEqual(app.base.meta_get('moteur_carte'), 1)          # materiel.recommander()['indice_carte']
        self.assertEqual(self.crees[-1]['carte'], 1)
        self.assertEqual(app.moteur['carte'], 1)
        # Sur le processeur : aucune carte.
        self.assertEqual(app.choisir_moteur('rapide', 'fp16', 'processeur'), {'deja': False})
        self.assertTrue(attendre(lambda: self.prete('rapide'), 15, 0.05))
        self.assertIsNone(app.base.meta_get('moteur_carte'))
        self.assertEqual((self.crees[-1]['cle'], self.crees[-1]['materiel'], self.crees[-1]['carte']),
                         ('rapide', 'processeur', 0))


class TestRechercheProches(unittest.TestCase):
    """recherche.chercher(..., proches=True) : les photos sous les seuils, de la plus proche à la moins proche, par
    pages sans doublon ; les résultats et les seuils (REGLAGES) ne changent pas."""

    def setUp(self):
        import numpy as np
        from myphotos import modele, recherche
        self.recherche = recherche
        r = recherche.reglages('base')
        m = modele.info('base')

        def cosinus(p):                                        # probabilité -> cosinus (inverse de probabilite)
            return (np.log(p / (1 - p)) - m['biais']) / m['echelle']
        # 3 sûres, 2 « peut-être », 9 au-dessous, dont deux photos identiques (même score : ordre stable).
        c = ([cosinus(r['seuil_sur']) + d for d in (0.03, 0.02, 0.01)]
             + [cosinus(r['seuil_peut_etre']) + d for d in (0.004, 0.002)]
             + [cosinus(r['seuil_peut_etre']) - d for d in (0.01, 0.05, 0.02, 0.02, 0.3, 0.08, 0.1, 0.2, 0.03)])
        dim = 16
        mat = np.zeros((len(c), dim), np.float32)
        for i, x in enumerate(c):
            mat[i, 0] = x
            mat[i, 1 + (i % (dim - 1))] = np.sqrt(1 - x * x)
        mat[8] = mat[7]                                        # deux photos identiques
        self.ids = np.arange(101, 101 + len(c), dtype=np.int64)
        self.mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        self.scores = self.mat[:, 0]
        p = modele.probabilite(self.scores, 'base')
        self.garde = {int(i) for i in self.ids[p >= r['seuil_peut_etre']]}
        self.sures = {int(i) for i in self.ids[p >= r['seuil_sur']]}
        ids, matrice = self.ids, self.mat

        class Base:
            def generation(self):
                return 1

            def vecteurs(self, cle):
                return ids, matrice, np.full(len(ids), np.datetime64('NaT', 's'))

        class Texte:
            def vecteurs_textes(self, textes):
                v = np.zeros((len(textes), dim), np.float32)
                v[:, 0] = 1
                return v
        self.reglages_avant = {k: dict(v) for k, v in recherche.REGLAGES.items()}
        self.r = recherche.Recherche(Base(), Texte, 'base')

    def test_resultats_inchanges_et_total_proches(self):
        res = self.r.chercher('chien', 0, 100)
        self.assertEqual((res['total_sures'], res['total_peut_etre'], res['total_proches']), (3, 2, 9))
        self.assertEqual({i['id'] for i in res['sures']}, self.sures)
        self.assertEqual({i['id'] for i in res['sures'] + res['peut_etre']}, self.garde)
        self.assertNotIn('proches', res)
        self.assertEqual(self.recherche.REGLAGES, self.reglages_avant)

    def test_proches_par_pages(self):
        tout = self.r.chercher('chien', 0, None, proches=True)
        ids = [i['id'] for i in tout['proches']]
        self.assertEqual((tout['total_proches'], len(ids)), (9, 9))
        self.assertFalse(set(ids) & self.garde)                 # aucun résultat « repêché »
        self.assertEqual(set(ids) | self.garde, {int(i) for i in self.ids})
        scores = [i['score'] for i in tout['proches']]
        self.assertEqual(scores, sorted(scores, reverse=True))  # de la plus proche à la moins proche
        self.assertLess(ids.index(108), ids.index(109))         # même score : ordre stable
        self.assertNotIn('sures', tout)
        pages = []
        for debut in range(0, 12, 4):
            p = self.r.chercher('chien', debut, 4, proches=True)
            self.assertEqual(p['debut'], debut)
            pages += [i['id'] for i in p['proches']]
        self.assertEqual(pages, ids)                             # pages contiguës, sans doublon
        self.assertEqual(self.r.chercher('chien', 50, 4, proches=True)['proches'], [])
        self.assertIsNone(self.r.chercher('   ', 0, 4, proches=True))


class TestCarte(unittest.TestCase):
    """modele.Moteur(carte=…) : indice DirectML transmis à ONNX Runtime ; carte refusée : la carte principale, puis
    le processeur. ONNX Runtime est simulé : aucun moteur réel n'est chargé."""

    @staticmethod
    def faux_ort(acceptees):
        essais = []

        class Session:
            def __init__(self, chemin, sess_options=None, providers=None):
                p = providers[0]
                if isinstance(p, tuple):
                    essais.append(p[1]['device_id'])
                    if p[1]['device_id'] not in acceptees:
                        raise RuntimeError('carte refusée')
                    self.fournisseurs = ['DmlExecutionProvider', 'CPUExecutionProvider']
                else:
                    essais.append('processeur')
                    self.fournisseurs = ['CPUExecutionProvider']

            def get_providers(self):
                return self.fournisseurs

            def get_inputs(self):
                return [types.SimpleNamespace(name='pixel_values', type='tensor(float16)')]

            def get_outputs(self):
                return [types.SimpleNamespace(name='pooler_output')]
        ort = types.SimpleNamespace(
            InferenceSession=Session, SessionOptions=types.SimpleNamespace,
            GraphOptimizationLevel=types.SimpleNamespace(ORT_ENABLE_BASIC=1, ORT_ENABLE_ALL=99),
            ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL=0),
            get_available_providers=lambda: ['DmlExecutionProvider', 'CPUExecutionProvider'])
        return ort, essais

    def moteur(self, acceptees, **options):
        from myphotos import modele
        ort, essais = self.faux_ort(acceptees)
        with mock.patch.dict(sys.modules, {'onnxruntime': ort}):
            m = modele.Moteur('base', vision='fp16', texte='fp16', charger_texte=False, **options)
        return m, essais

    def test_carte_choisie(self):
        m, essais = self.moteur({0, 1}, carte=1)
        self.assertEqual((m.materiel, m.carte, essais), ('carte graphique', 1, [1]))

    def test_carte_principale_par_defaut(self):
        m, essais = self.moteur({0, 1})
        self.assertEqual((m.carte, essais), (0, [0]))
        for bizarre in (None, -1, 64, True, '1', 1.0):
            self.assertEqual(self.moteur({0, 1}, carte=bizarre)[1], [0], bizarre)

    def test_carte_refusee(self):
        m, essais = self.moteur({0}, carte=2)
        self.assertEqual((m.materiel, m.carte, essais), ('carte graphique', 0, [2, 0]))
        m, essais = self.moteur(set(), carte=2)
        self.assertEqual((m.materiel, essais), ('processeur', [2, 0, 'processeur']))
        m, essais = self.moteur({0, 1}, carte=1, materiel='processeur')
        self.assertEqual((m.materiel, essais), ('processeur', ['processeur']))


@unittest.skipUnless(os.name == 'nt', 'signatures CNG : Windows seulement')
class TestMiseAJour(unittest.TestCase):
    """Mise à jour proposée (0.4.0) sur un vrai serveur d'essai : manifeste local signé par une clé d'essai
    (MYPHOTOS_ESSAI_MANIFESTE, MYPHOTOS_ESSAI_CLE_MAJ, mode FACTICE). Rien n'est téléchargé, rien n'est lancé :
    l'installation simulée arrête MyPhotos, comme la vraie ; au lancement suivant, la page apprend qu'elle n'a pas
    abouti (la version n'a pas changé)."""

    PORT4 = PORT + 3

    def setUp(self):
        if not port_libre(self.PORT4):
            self.skipTest(f'port {self.PORT4} déjà pris')
        from myphotos import config, mise_a_jour, signature
        self.M = mise_a_jour
        self.config = config
        self.donnees = DONNEES / 'essai-mise-a-jour'
        shutil.rmtree(self.donnees, ignore_errors=True)
        self.donnees.mkdir(parents=True)
        self.privee, publique = signature.nouvelle_cle()
        v = mise_a_jour.version_tuple(config.VERSION)
        self.suivante = f'{v[0]}.{v[1] + 1}.0'
        self.manifeste = self.donnees.parent / 'essai-mise-a-jour-manifeste.json'
        self.ecrire_manifeste(self.privee)
        self.hote = f'127.0.0.1:{self.PORT4}'
        self.variables = dict(MYPHOTOS_TELECHARGEMENT_FACTICE='1', MYPHOTOS_FACTICE_S='1',
                              MYPHOTOS_ESSAI_MANIFESTE=str(self.manifeste),
                              MYPHOTOS_ESSAI_CLE_MAJ=base64.b64encode(publique).decode())
        self.proc = None
        self.addCleanup(self.arreter)
        self.lancer()

    def ecrire_manifeste(self, cle_privee):
        from myphotos import signature
        m = {'application': 'MyPhotos', 'version': self.suivante, 'date': '2026-12-01',
             'installateur': {'nom': self.M.nom_installateur(self.suivante), 'taille': 40_000_000,
                              'sha256': 'cd' * 32},
             'nouveautes': ['Une nouveauté.']}
        texte = json.dumps(m).encode()
        self.manifeste.write_text(json.dumps({'format': 1, 'manifeste': base64.b64encode(texte).decode(),
                                              'signature': base64.b64encode(signature.signer(texte, cle_privee))
                                              .decode()}), encoding='ascii')

    def lancer(self):
        self.proc = lancer_serveur(self.donnees, self.PORT4, neutre=True, **self.variables)
        attendre_serveur(self.proc, self.PORT4)

    def arreter(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    self.api('/api/quitter', {})
                    self.proc.wait(30)
                except Exception:
                    pass
            arreter_essai(self.proc)
            self.proc.sortie.close()
            self.proc = None

    def api(self, chemin, corps=None, **kw):
        return api(chemin, corps, hote=self.hote, port=self.PORT4, **kw)

    def maj(self):
        statut, e = self.api('/api/etat')
        self.assertEqual(statut, 200)
        return e['mise_a_jour']

    def test_parcours(self):
        m = self.maj()
        self.assertEqual((m['accord'], m['etat'], m['version'], m['apres']), (None, 'inconnu', None, None))
        self.assertEqual(self.api('/api/mise-a-jour/reporter', {})[0], 409)          # rien à reporter
        self.assertEqual(self.api('/api/mise-a-jour/installer', {})[0], 409)         # rien à installer
        statut, r = self.api('/api/mise-a-jour/reglage', {'oui': True})
        self.assertEqual((statut, r['accord']), (200, 'oui'))
        m = attendre(lambda: (lambda x: x if x['etat'] == 'disponible' else None)(self.maj()), 20)
        self.assertIsNotNone(m, self.maj())
        self.assertEqual((m['version'], m['nouveautes'], m['possible'], m['reportee']),
                         (self.suivante, ['Une nouveauté.'], True, False))
        self.assertTrue(m['notes'].startswith('https://github.com/glenac/MyPhotos/releases/tag/v'))
        self.assertTrue(self.api('/api/mise-a-jour/reporter', {})[1]['reportee'])
        # « Mettre à jour » : téléchargement (simulé), installation (simulée), puis MyPhotos s'arrête.
        statut, r = self.api('/api/mise-a-jour/installer', {})
        self.assertEqual((statut, r['etat']), (200, 'telechargement'))
        self.assertTrue(attendre(lambda: self.proc.poll() is not None, 30), 'MyPhotos ne s\'est pas arrêté')
        self.proc.sortie.close()
        self.proc = None
        # Lancement suivant : même version, donc la mise à jour « n'a pas abouti » ; la page le dit une fois.
        self.lancer()
        m = self.maj()
        self.assertEqual((m['accord'], m['apres']), ('oui', {'reussie': False, 'version': self.suivante}))
        self.assertEqual(self.api('/api/mise-a-jour/vu', {})[0], 200)
        self.assertIsNone(self.maj()['apres'])

    def test_refus(self):
        # Sans jeton, sans l'en-tête de la page, ou d'un autre site : refusé avant toute action.
        self.assertEqual(self.api('/api/mise-a-jour/verifier', {}, jeton=False)[0], 403)
        statut, _, _ = requete('POST', '/api/mise-a-jour/verifier', {}, {'Origin': f'http://{self.hote}'},
                               hote=self.hote, port=self.PORT4)
        self.assertEqual(statut, 403)
        statut, _, _ = requete('POST', '/api/mise-a-jour/verifier', {}, {'X-MyPhotos': '1',
                               'Origin': 'http://evil.example', 'Sec-Fetch-Site': 'cross-site'},
                               hote=self.hote, port=self.PORT4)
        self.assertEqual(statut, 403)
        for corps in ({'oui': 'oui'}, {'oui': 1}, {}, {'oui': None}):
            self.assertEqual(self.api('/api/mise-a-jour/reglage', corps)[0], 400, corps)
        for chemin in ('/api/mise-a-jour/inconnue', '/api/mise-a-jour/', '/api/mise-a-jour/a/b'):
            self.assertEqual(self.api(chemin, {})[0], 404, chemin)
        self.assertIsNone(self.maj()['accord'])                       # aucune réponse enregistrée par ces essais

    def test_manifeste_d_une_autre_cle(self):
        from myphotos import signature
        autre, _ = signature.nouvelle_cle()
        self.ecrire_manifeste(autre)
        statut, _ = self.api('/api/mise-a-jour/verifier', {})
        self.assertEqual(statut, 200)
        m = attendre(lambda: (lambda x: x if x['etat'] not in ('verification', 'inconnu') else None)(self.maj()), 20)
        self.assertEqual((m['etat'], m['version']), ('erreur', None))
        self.assertIn('vérifié', m['message'])
        self.assertEqual(self.api('/api/mise-a-jour/installer', {})[0], 409)
        self.assertIn('fichier de mise à jour refusé', (self.donnees / 'journal.log').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
