"""Essais du serveur local de MyPhotos (myphotos/serveur.py), par de vraies requêtes HTTP.

Le serveur tourne dans un sous-processus, sur un port d'essai, avec ses propres données :

    set MYPHOTOS_ESSAI_UI=C:\\chemin\\essai-ui              (défaut : %TEMP%\\myphotos-essai-ui)
    set MYPHOTOS_ESSAI_PHOTOS=C:\\chemin\\images-publiques  (facultatif : essai de bout en bout)
    .venv\\Scripts\\python -m unittest tests.test_serveur -v

Les moteurs déjà téléchargés (donnees/modele, ou MYPHOTOS_MODELES) servent tels quels : aucun téléchargement.
Aucune photo personnelle : l'essai de bout en bout prend des images publiques (Wikimedia Commons), nommées
d'après ce qu'elles montrent (chien-1.jpg, plage-2.jpg…). Aucune fenêtre ne s'ouvre : « Ouvrir » et « Afficher
dans le dossier » sont essayés dans ce processus, les appels à Windows étant simulés.
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

PORT = int(os.environ.get('MYPHOTOS_ESSAI_PORT') or 7896)
HOTE = f'127.0.0.1:{PORT}'
DONNEES = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
MODELES = Path(os.environ.get('MYPHOTOS_MODELES') or RACINE / 'donnees' / 'modele')
PHOTOS = os.environ.get('MYPHOTOS_ESSAI_PHOTOS')
INCONNU = 987654321
SANS_FENETRE = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def requete(methode, chemin, corps=None, entetes=None, brut=None, hote=HOTE, port=PORT, delai=60):
    """(statut, en-têtes en minuscules, corps). Host et tous les en-têtes sont exactement ceux donnés."""
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=delai)
    e = {'Host': hote}
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
        statut, _, d = requete('POST', chemin, corps,
                               {'X-MyPhotos': '1', 'Origin': f'http://{HOTE}', 'Sec-Fetch-Site': 'same-origin'}, **kw)
    return statut, json.loads(d or b'null')


def port_libre(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def arreter_par_pid(proc):
    """Arrête un serveur d'essai par SON identifiant de processus (jamais par le nom du programme)."""
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
    else:
        os.kill(proc.pid, 15)
    proc.wait(timeout=30)


def lancer_serveur(donnees, port=PORT, **variables):
    env = dict(os.environ, MYPHOTOS_PORT=str(port), MYPHOTOS_SANS_PAGE='1', MYPHOTOS_DOSSIER=str(donnees),
               MYPHOTOS_MODELES=str(MODELES), PYTHONIOENCODING='utf-8')
    env.pop('MYPHOTOS_TELECHARGEMENT_FACTICE', None)
    env.update(variables)
    Path(donnees).mkdir(parents=True, exist_ok=True)
    sortie = open(Path(donnees) / 'essai-serveur.err', 'ab')
    proc = subprocess.Popen([sys.executable, '-X', 'faulthandler', '-m', 'myphotos'], cwd=str(RACINE), env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sortie,
                            creationflags=SANS_FENETRE)
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


class EssaiServeur(unittest.TestCase):
    proc = None

    @classmethod
    def setUpClass(cls):
        if not port_libre(PORT):
            raise unittest.SkipTest(f'le port {PORT} est déjà pris')
        cls.proc = lancer_serveur(DONNEES)
        try:
            attendre_serveur(cls.proc)
        except Exception:
            arreter_par_pid(cls.proc)
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
            arreter_par_pid(cls.proc)
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


class TestReponses(EssaiServeur):

    def test_01_etat(self):
        statut, e = api('/api/etat')
        self.assertEqual(statut, 200)
        self.assertEqual(e['nom'], 'MyPhotos')
        for cle in ('version', 'assistant', 'moteur', 'telechargement', 'changement', 'analyse', 'photos',
                    'nb_dossiers', 'recherche_prete', 'generation', 'arret'):
            self.assertIn(cle, e)
        self.assertEqual(set(e['photos']), {'total', 'analysees', 'a_faire', 'erreurs', 'indisponibles', 'en_attente'})
        self.assertFalse(e['arret'])

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
        e = attendre(lambda: (lambda x: x if x['recherche_prete'] and not x['changement'] else None)(api('/api/etat')[1]), 60)
        self.assertTrue(e, 'le moteur n\'a pas été activé')
        self.assertEqual(e['moteur']['cle'], 'base')
        self.assertEqual(api('/api/moteur/choisir', {'cle': 'base', 'vision': 'fp16'})[1], {'deja': True})
        statut, r = api('/api/recherche?q=chien&n=10')
        self.assertEqual(statut, 200, r)
        self.assertEqual(r['mode'], 'recherche')
        for cle in ('requete', 'total_sures', 'total_peut_etre', 'sures', 'peut_etre', 'suivant', 'analysees'):
            self.assertIn(cle, r)
        self.vivant()


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

        # Retrait du dossier ajouté : ses photos disparaissent, les fichiers restent.
        dossiers = api('/api/dossiers')[1]['dossiers']
        did = next(x['id'] for x in dossiers if x['chemin'].lower() == str(ajout).lower())
        statut, d = api('/api/dossiers/retirer', {'id': did})
        self.assertEqual(statut, 200, d)
        self.assertTrue((ajout / 'chien-copie.jpg').exists())
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
        proc = lancer_serveur(donnees, self.PORT2, MYPHOTOS_INACTIVITE_S='4')
        try:
            attendre_serveur(proc, self.PORT2)
            # Deuxième lancement sur le même port : il voit MyPhotos et s'arrête aussitôt, sans erreur.
            second = lancer_serveur(donnees, self.PORT2)
            try:
                self.assertEqual(second.wait(timeout=60), 0)
            finally:
                arreter_par_pid(second)
                second.sortie.close()
            self.assertIsNone(proc.poll())
            # Plus aucune requête : arrêt propre tout seul (délai réduit à 4 s pour l'essai).
            self.assertEqual(proc.wait(timeout=60), 0)
        finally:
            arreter_par_pid(proc)
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
            self.image.unlink()                                    # fichier disparu
            with self.assertRaises(self.serveur.Refus) as e:
                self.app.ouvrir(self.id_image)
            self.assertEqual(e.exception.statut, 404)
        ouvrir.assert_not_called()
        montrer.assert_not_called()


class TestChoixDossier(unittest.TestCase):
    """Fenêtre « Choisir un dossier » : fil réservé, une fenêtre à la fois, annuler ne fait rien. tkinter est
    entièrement simulé : aucune fenêtre n'apparaît sur le bureau pendant les essais."""

    def setUp(self):
        from myphotos import serveur
        self.serveur = serveur
        self.reponses = []
        self.ouverte = threading.Event()
        self.relacher = threading.Event()
        faux = mock.MagicMock()

        def askdirectory(**options):
            self.options = options
            self.ouverte.set()
            self.relacher.wait(10)
            r = self.reponses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        faux.filedialog.askdirectory.side_effect = askdirectory
        self.modules = mock.patch.dict(sys.modules, {'tkinter': faux, 'tkinter.filedialog': faux.filedialog})
        self.modules.start()
        self.premier_plan = mock.patch.object(serveur, '_premier_plan')
        self.premier_plan.start()

    def tearDown(self):
        self.relacher.set()
        self.premier_plan.stop()
        self.modules.stop()

    def test_choix_annulation_et_fenetre_unique(self):
        choix = self.serveur.ChoixDossier()
        self.reponses = ['E:/Photos', '', OSError('Tk en panne'), 'D:/Vacances']
        resultats = []
        fil = threading.Thread(target=lambda: resultats.append(choix.demander(initial='C:\\Images')))
        fil.start()
        self.assertTrue(self.ouverte.wait(10))
        with self.assertRaises(self.serveur.Refus) as e:            # une seule fenêtre à la fois
            choix.demander()
        self.assertEqual(e.exception.statut, 409)
        self.relacher.set()
        fil.join(10)
        self.assertEqual(resultats, ['E:/Photos'])
        self.assertEqual(self.options['initialdir'], 'C:\\Images')
        self.assertTrue(self.options['mustexist'])
        self.assertIsNone(choix.demander())                          # annuler : rien
        with self.assertRaises(OSError):                             # panne : signalée…
            choix.demander()
        self.assertEqual(choix.demander(), 'D:/Vacances')            # … et la fenêtre suivante fonctionne

    def test_annuler_ne_fait_rien(self):
        app = self.serveur.Application.__new__(self.serveur.Application)
        app.choix_dossier = mock.Mock(demander=mock.Mock(return_value=None))
        app.ajouter_dossier = mock.Mock()
        self.assertEqual(app.choisir_dossier(), {'annule': True})
        app.ajouter_dossier.assert_not_called()


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


if __name__ == '__main__':
    unittest.main()
