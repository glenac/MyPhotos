"""Tests de myphotos/mise_a_jour.py : manifeste signé, réseau en https, téléchargement contrôlé, orchestration.

    .venv\\Scripts\\python.exe -m unittest tests.test_mise_a_jour -v

Aucun accès à Internet : le réseau, le téléchargement et le lancement de l'installateur sont simulés. Les manifestes
sont signés avec des clés fabriquées pour l'essai (jamais la clé du projet).
"""
import base64
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from myphotos import config, mise_a_jour as M, signature  # noqa: E402

JOURNAL = logging.getLogger('essai-mise-a-jour')
JOURNAL.addHandler(logging.NullHandler())
JOURNAL.propagate = False

SUIVANTE = '.'.join(str(x + (1 if i == 1 else 0)) for i, x in enumerate(M.version_tuple(config.VERSION)))
TAILLE = 1 << 20 | 12345


def enveloppe(m, cle_privee, fmt=1):
    texte = json.dumps(m).encode('utf-8')
    return json.dumps({'format': fmt, 'manifeste': base64.b64encode(texte).decode(),
                       'signature': base64.b64encode(signature.signer(texte, cle_privee)).decode()}).encode()


def manifeste(version=SUIVANTE, **plus):
    m = {'application': 'MyPhotos', 'version': version, 'date': '2026-11-30',
         'installateur': {'nom': M.nom_installateur(version), 'taille': TAILLE, 'sha256': 'ab' * 32},
         'nouveautes': ['Mise à jour en un clic.', 'Correctifs.']}
    m.update(plus)
    return m


@unittest.skipUnless(os.name == 'nt', 'signatures CNG : Windows seulement')
class Manifeste(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.privee, cls.publique = signature.nouvelle_cle()

    def lire(self, m, **k):
        return M.lire_manifeste(enveloppe(m, self.privee, **k), cle=self.publique)

    def test_valide(self):
        i = self.lire(manifeste())
        self.assertEqual(i['version'], SUIVANTE)
        self.assertEqual(i['nom'], f'MyPhotos-{SUIVANTE}-installation.exe')
        self.assertEqual(i['url'], f'https://github.com/glenac/MyPhotos/releases/download/v{SUIVANTE}/'
                                   f'MyPhotos-{SUIVANTE}-installation.exe')
        self.assertEqual(i['notes'], f'https://github.com/glenac/MyPhotos/releases/tag/v{SUIVANTE}')
        self.assertEqual((i['taille'], i['sha256'], i['date']), (TAILLE, 'ab' * 32, '2026-11-30'))
        self.assertEqual(i['nouveautes'], ['Mise à jour en un clic.', 'Correctifs.'])

    def test_cle_du_projet_par_defaut(self):
        # Sans clé donnée, c'est la clé publique de MyPhotos qui vérifie : un manifeste signé par une autre clé
        # (celle de l'essai, ou celle d'un compte GitHub volé) est refusé.
        with self.assertRaisesRegex(M.Refusee, 'signature'):
            M.lire_manifeste(enveloppe(manifeste(), self.privee))

    def test_signature_et_enveloppe(self):
        bon = json.loads(enveloppe(manifeste(), self.privee))
        texte = base64.b64decode(bon['manifeste'])
        faux = dict(bon, manifeste=base64.b64encode(texte.replace(f'"taille": {TAILLE}'.encode(), f'"taille": {TAILLE + 1}'.encode())).decode())
        self.assertNotEqual(faux['manifeste'], bon['manifeste'])
        for brut in (json.dumps(faux).encode(), json.dumps(dict(bon, signature='')).encode(),
                     json.dumps(dict(bon, signature='pas du base64 !')).encode(),
                     json.dumps(dict(bon, format=2)).encode(), json.dumps({'format': 1}).encode(),
                     b'', b'\xff\xfe', b'[]', b'null', b'{"format": 1, "manifeste": 3, "signature": 4}',
                     b' ' * (M.TAILLE_MAX_MANIFESTE + 1), 'texte'):
            with self.assertRaises(M.Refusee, msg=brut[:60]):
                M.lire_manifeste(brut, cle=self.publique)
        _, autre = signature.nouvelle_cle()
        with self.assertRaises(M.Refusee):
            M.lire_manifeste(enveloppe(manifeste(), self.privee), cle=autre)

    def test_contenu_signe_mais_invalide(self):
        inst = manifeste()['installateur']
        mauvais = [
            manifeste(application='AutreChose'), {'version': SUIVANTE}, manifeste(version='1.2'),
            manifeste(version='01.2.3'), manifeste(version='1.2.3.4'), manifeste(version=' 1.2.3'),
            manifeste(version='1.2.3\n'), manifeste(version=None), manifeste(version=123),
            manifeste(installateur=dict(inst, nom='autre.exe')),
            manifeste(installateur=dict(inst, nom=f'MyPhotos-{SUIVANTE}-installation.exe\\..\\x.exe')),
            manifeste(installateur=dict(inst, taille=10)), manifeste(installateur=dict(inst, taille=True)),
            manifeste(installateur=dict(inst, taille=float(TAILLE))), manifeste(installateur=dict(inst, taille=1 << 31)),
            manifeste(installateur=dict(inst, sha256='AB' * 32)), manifeste(installateur=dict(inst, sha256='ab' * 31)),
            manifeste(installateur=dict(inst, sha256=None)), manifeste(installateur='MyPhotos.exe'),
            manifeste(nouveautes='texte'), manifeste(nouveautes=['x'] * 9), manifeste(nouveautes=[3]),
            manifeste(nouveautes=['x' * 301]), manifeste(nouveautes=['\x07\x08']), manifeste(date='30/11/2026'),
            manifeste(date=20261130), [manifeste()],
        ]
        for m in mauvais:
            with self.assertRaises(M.Refusee, msg=str(m)[:80]):
                self.lire(m)

    def test_nouveautes_nettoyees(self):
        i = self.lire(manifeste(nouveautes=['  Ligne\u202e avec\x1b[31m contrôle \t ']))
        self.assertEqual(i['nouveautes'], ['Ligne\u202e avec[31m contrôle'])
        i = self.lire({k: v for k, v in manifeste().items() if k not in ('nouveautes', 'date')})
        self.assertEqual((i['nouveautes'], i['date']), ([], None))

    def test_champs_en_plus_ignores(self):
        i = self.lire(manifeste(url='http://evil.example/x.exe', notes='https://evil.example/'))
        self.assertTrue(i['url'].startswith('https://github.com/glenac/MyPhotos/'))
        self.assertTrue(i['notes'].startswith('https://github.com/glenac/MyPhotos/'))

    def test_faire_manifeste(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        inst = d / M.nom_installateur(SUIVANTE)
        contenu = os.urandom(TAILLE)
        inst.write_bytes(contenu)
        brut = M.faire_manifeste(SUIVANTE, inst, self.privee, ['Nouveau.'], date='2026-12-01')
        i = M.lire_manifeste(brut, cle=self.publique)
        self.assertEqual((i['taille'], i['sha256'], i['nouveautes'], i['date']),
                         (TAILLE, hashlib.sha256(contenu).hexdigest(), ['Nouveau.'], '2026-12-01'))
        with self.assertRaises(ValueError):
            M.faire_manifeste('9.9.9', inst, self.privee)
        petit = d / M.nom_installateur('9.9.9')
        petit.write_bytes(b'x')
        with self.assertRaises(M.Refusee):                # trop petit : refusé dès la publication
            M.faire_manifeste('9.9.9', petit, self.privee)


class Versions(unittest.TestCase):

    def test_comparaison(self):
        self.assertTrue(M.plus_recente('0.10.0', '0.9.9'))
        self.assertTrue(M.plus_recente('1.0.0', '0.99.99'))
        self.assertFalse(M.plus_recente('0.4.0', '0.4.0'))
        self.assertFalse(M.plus_recente('0.3.9', '0.4.0'))
        for v in ('1.2', '1.2.3.4', '01.2.3', 'v1.2.3', '', None, '1.2.3 ', '１.2.3', '1.2.-3', '12345.0.0'):  # publication: ok (version invalide, pas une adresse)
            with self.assertRaises(ValueError, msg=repr(v)):
                M.version_tuple(v)
        with self.assertRaises(ValueError):
            M.url_installateur('../x')

    def test_version_courante_valide(self):
        M.version_tuple(config.VERSION)


class Reseau(unittest.TestCase):

    def test_https_seulement(self):
        for url in ('http://github.com/x', 'file:///C:/x', 'ftp://x/y'):
            with self.assertRaises(urllib.error.URLError):
                M.ouvrir(url)
            with self.assertRaises(urllib.error.URLError):
                M.ouvrir(urllib.request.Request(url))
        h = M._HttpsSeulement()
        req = urllib.request.Request('https://github.com/a')
        with self.assertRaises(urllib.error.URLError):
            h.redirect_request(req, None, 302, 'Found', {}, 'http://objects.example/a')
        self.assertIsNotNone(h.redirect_request(req, None, 302, 'Found', {}, 'https://objects.example/a'))

    def reponse(self, corps):
        r = io.BytesIO(corps)
        r.__enter__ = lambda *a: r
        r.__exit__ = lambda *a: None
        return mock.MagicMock(__enter__=lambda s: r, __exit__=lambda s, *a: None)

    def test_chercher(self):
        vus = []

        def ouvrir(req, timeout):
            vus.append(req)
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, None)
        with mock.patch.object(M, 'ouvrir', ouvrir):
            self.assertIsNone(M.chercher())
        self.assertEqual(vus[0].full_url, M.URL_MANIFESTE)
        self.assertEqual(vus[0].get_header('User-agent'), 'MyPhotos/1 (logiciel local)')
        self.assertTrue(M.URL_MANIFESTE.startswith('https://github.com/glenac/MyPhotos/releases/latest/download/'))
        for erreur in (urllib.error.HTTPError('u', 500, 'x', {}, None), urllib.error.URLError('pas de réseau'),
                       TimeoutError('lent'), ConnectionResetError()):
            with mock.patch.object(M, 'ouvrir', side_effect=erreur):
                with self.assertRaises(M.Indisponible):
                    M.chercher()
        with mock.patch.object(M, 'ouvrir', return_value=self.reponse(b'x' * (M.TAILLE_MAX_MANIFESTE + 5))):
            with self.assertRaises(M.Refusee):
                M.chercher()


class Fichiers(unittest.TestCase):

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.contenu = os.urandom(TAILLE)
        self.info = {'version': SUIVANTE, 'nom': M.nom_installateur(SUIVANTE), 'taille': TAILLE,
                     'sha256': hashlib.sha256(self.contenu).hexdigest(), 'url': M.url_installateur(SUIVANTE)}

    def faux_telechargement(self, contenu):
        def f(url, partiel, taille, rappel, arret, ouvrir=None, agent=None):
            self.assertIs(ouvrir, M.ouvrir)
            self.assertEqual(url, self.info['url'])
            if arret is not None and arret.is_set():
                raise InterruptedError
            Path(partiel).write_bytes(contenu)
            rappel(len(contenu))
        return f

    def test_telecharger(self):
        vus = []
        with mock.patch.object(M.modele, '_telecharger_un', self.faux_telechargement(self.contenu)):
            p = M.telecharger(self.info, lambda f, t: vus.append((f, t)), threading.Event(), racine=self.d)
        self.assertEqual(p, self.d / 'mises-a-jour' / self.info['nom'])
        self.assertEqual(p.read_bytes(), self.contenu)
        self.assertFalse(p.with_name(p.name + '.part').exists())
        self.assertEqual(vus[-1], (TAILLE, TAILLE))
        # Déjà là et conforme : rien n'est retéléchargé.
        with mock.patch.object(M.modele, '_telecharger_un', side_effect=AssertionError('retéléchargé')):
            self.assertEqual(M.telecharger(self.info, racine=self.d), p)

    def test_empreinte_fausse(self):
        abime = bytearray(self.contenu)
        abime[100] ^= 1
        with mock.patch.object(M.modele, '_telecharger_un', self.faux_telechargement(bytes(abime))), \
                mock.patch.object(M.time, 'sleep'):
            with self.assertRaises(M.Indisponible):
                M.telecharger(self.info, racine=self.d)
        self.assertEqual(list((self.d / 'mises-a-jour').iterdir()), [])

    def test_interruption(self):
        arret = threading.Event()
        arret.set()
        with mock.patch.object(M.modele, '_telecharger_un', self.faux_telechargement(self.contenu)):
            with self.assertRaises(InterruptedError):
                M.telecharger(self.info, arret=arret, racine=self.d)

    def test_installation_possible(self):
        (self.d / 'unins000.exe').write_bytes(b'x')
        with mock.patch.object(M.config, 'GELE', True), mock.patch.object(M.config, 'EXE_DOSSIER', self.d):
            self.assertEqual(M.installation_possible(), (True, '') if os.name == 'nt' else (False, 'hors Windows'))
        with mock.patch.object(M.config, 'GELE', False), mock.patch.object(M.config, 'EXE_DOSSIER', self.d):
            self.assertFalse(M.installation_possible()[0])
        (self.d / 'unins000.exe').unlink()
        with mock.patch.object(M.config, 'GELE', True), mock.patch.object(M.config, 'EXE_DOSSIER', self.d):
            self.assertFalse(M.installation_possible()[0])

    def test_lancer_installation(self):
        p = self.d / self.info['nom']
        p.write_bytes(self.contenu)
        with mock.patch.object(M, 'installation_possible', return_value=(True, '')), \
                mock.patch.object(M.subprocess, 'Popen') as popen:
            popen.return_value.pid = 4242
            self.assertEqual(M.lancer_installation(p, self.info), 4242)
        commande = popen.call_args[0][0]
        self.assertEqual(commande[:6], [str(p), '/SP-', '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/MISEAJOUR'])
        self.assertTrue(commande[6].startswith('/LOG=') and commande[6].endswith(f'installation-{SUIVANTE}.log'))
        # Fichier modifié entre le téléchargement et le lancement : refusé, rien n'est lancé.
        p.write_bytes(self.contenu[:-1] + b'\0')
        with mock.patch.object(M, 'installation_possible', return_value=(True, '')), \
                mock.patch.object(M.subprocess, 'Popen') as popen:
            with self.assertRaises(M.Indisponible):
                M.lancer_installation(p, self.info)
            popen.assert_not_called()
        with mock.patch.object(M, 'installation_possible', return_value=(False, 'exe seul')), \
                mock.patch.object(M.subprocess, 'Popen') as popen:
            with self.assertRaises(M.Indisponible):
                M.lancer_installation(p, self.info)
            popen.assert_not_called()

    def test_nettoyer(self):
        d = self.d / 'mises-a-jour'
        d.mkdir()
        vieux = time.time() - 30 * 86400
        noms = {M.nom_installateur(config.VERSION): False, M.nom_installateur('0.0.1'): False,
                M.nom_installateur(SUIVANTE): True, M.nom_installateur(SUIVANTE) + '.part': True,
                M.nom_installateur('0.0.2') + '.part': False, 'autre.exe': True, 'installation-0.4.0.log': True,
                'installation-0.3.0.log': False, 'MyPhotos-1.2-installation.exe': True}
        for n in noms:
            (d / n).write_bytes(b'x')
        os.utime(d / 'installation-0.3.0.log', (vieux - 90 * 86400,) * 2)
        retires = M.nettoyer(self.d)
        self.assertEqual(sorted(retires), sorted(n for n, garde in noms.items() if not garde))
        for n, garde in noms.items():
            self.assertEqual((d / n).exists(), garde, n)
        os.utime(d / (M.nom_installateur(SUIVANTE) + '.part'), (vieux,) * 2)
        self.assertEqual(M.nettoyer(self.d), [M.nom_installateur(SUIVANTE) + '.part'])
        self.assertEqual(M.nettoyer(self.d / 'absent'), [])


class Gestion(unittest.TestCase):

    def setUp(self):
        self.meta = {}
        self.arrets = []
        self.travaux = []
        self.lances = []
        self.info = {'version': SUIVANTE, 'nom': M.nom_installateur(SUIVANTE), 'taille': TAILLE, 'sha256': 'ab' * 32,
                     'nouveautes': ['Nouveau.'], 'date': '2026-11-30', 'url': M.url_installateur(SUIVANTE),
                     'notes': M.url_notes(SUIVANTE)}
        self.reponse = self.info

    def chercher(self):
        if isinstance(self.reponse, Exception):
            raise self.reponse
        return self.reponse

    def gestion(self, telecharger=None, possible=(True, '')):
        def tel(info, progression, arret, racine):
            progression(TAILLE // 2, TAILLE)
            progression(TAILLE, TAILLE)
            return Path('C:/essai') / info['nom']
        return M.Gestion(self.meta.get, self.meta.__setitem__, lambda: self.arrets.append(1), lambda: self.travaux,
                         JOURNAL, chercher=self.chercher, telecharger=telecharger or tel,
                         lancer=lambda chemin, info: self.lances.append((chemin, info)) or 77,
                         possible=lambda: possible)

    @staticmethod
    def attendre(g):
        if g._fil is not None:
            g._fil.join(10)
            assert not g._fil.is_alive()

    def test_accord_et_verification(self):
        g = self.gestion()
        r = g.resume()
        self.assertEqual((r['accord'], r['etat'], r['version'], r['apres']), (None, 'inconnu', None, None))
        self.assertFalse(g.due())                          # pas d'accord : jamais de vérification automatique
        g.regler(True)
        self.attendre(g)
        r = g.resume()
        self.assertEqual((self.meta['maj_auto'], r['accord'], r['etat'], r['version']), ('oui', 'oui', 'disponible',
                                                                                       SUIVANTE))
        self.assertEqual(r['nouveautes'], ['Nouveau.'])
        self.assertAlmostEqual(r['derniere'], time.time(), delta=5)
        self.assertFalse(g.due())                          # vérifié à l'instant
        self.assertTrue(g.due(time.time() + 24 * 3600 + 1))
        self.assertTrue(g.due(time.time() - 3600))         # horloge remise en arrière : on revérifie
        g.regler(False)
        self.assertEqual(self.meta['maj_auto'], 'non')
        self.assertFalse(g.due(time.time() + 10 * 86400))

    def test_a_jour(self):
        for reponse in (None, dict(self.info, version=config.VERSION), dict(self.info, version='0.0.1')):
            self.reponse = reponse
            g = self.gestion()
            g.verifier()
            self.attendre(g)
            self.assertEqual((g.etat, g.info), ('a_jour', None))

    def test_erreurs_de_verification(self):
        for erreur, attendu in ((M.Indisponible('hors ligne'), 'joindre GitHub'), (M.Refusee('signature'), 'vérifié'),
                                (RuntimeError('bogue'), 'échoué')):
            self.reponse = erreur
            g = self.gestion()
            g.verifier(manuel=True)
            self.attendre(g)
            self.assertEqual(g.etat, 'erreur')
            self.assertIn(attendu, g.message)
            self.assertNotIn('maj_derniere', self.meta)
            g.verifier(manuel=False)
            self.attendre(g)
            self.assertEqual((g.etat, g.message), ('inconnu', None))     # automatique : rien ne s'affiche

    def test_reporter(self):
        g = self.gestion()
        with self.assertRaises(M.Refus):
            g.reporter()
        g.verifier()
        self.attendre(g)
        self.assertTrue(g.reporter()['reportee'])
        self.assertEqual(json.loads(self.meta['maj_reportee'])['version'], SUIVANTE)
        g.info = dict(self.info, version='99.0.0')          # une version plus récente encore : pas reportée
        self.assertFalse(g.resume()['reportee'])
        g.info = self.info
        self.meta['maj_reportee'] = json.dumps({'version': SUIVANTE, 'jusqua': time.time() - 1})
        self.assertFalse(g.resume()['reportee'])
        self.meta['maj_reportee'] = '{abîmé'
        self.assertFalse(g.resume()['reportee'])

    def test_installer(self):
        g = self.gestion()
        with self.assertRaises(M.Refus):
            g.installer()                                    # rien de disponible
        g.verifier()
        self.attendre(g)
        g.installer()
        self.attendre(g)
        self.assertEqual(g.etat, 'installation')
        self.assertEqual(self.lances, [(Path('C:/essai') / self.info['nom'], self.info)])
        self.assertEqual(self.arrets, [1])
        lancee = json.loads(self.meta['maj_lancee'])
        self.assertEqual((lancee['de'], lancee['vers']), (config.VERSION, SUIVANTE))
        self.assertEqual(g.resume()['pourcent'], 100.0)

    def test_installer_refuse(self):
        g = self.gestion(possible=(False, 'MyPhotos.exe sans installation'))
        g.verifier()
        self.attendre(g)
        with self.assertRaisesRegex(M.Refus, 'page'):
            g.installer()
        self.assertEqual(g.resume()['raison'], 'MyPhotos.exe sans installation')
        g = self.gestion()
        g.verifier()
        self.attendre(g)
        self.travaux.append('copie')
        with self.assertRaisesRegex(M.Refus, 'copie'):
            g.installer()
        self.travaux[:] = ['analyse', 'doublons']           # l'analyse reprendra après la mise à jour
        g.installer()
        self.attendre(g)
        self.assertEqual(g.etat, 'installation')

    def test_echec_du_telechargement(self):
        def tel(info, progression, arret, racine):
            raise M.Indisponible('coupure')
        g = self.gestion(telecharger=tel)
        g.verifier()
        self.attendre(g)
        g.installer()
        self.attendre(g)
        self.assertEqual(g.etat, 'erreur')
        self.assertIn('Internet', g.message)
        self.assertEqual((self.lances, self.arrets), ([], []))
        self.assertIsNone(self.meta.get('maj_lancee'))
        g.installer()                                        # « Réessayer » : la version est toujours proposée
        self.attendre(g)
        self.assertEqual(g.etat, 'erreur')

    def test_annuler(self):
        commence = threading.Event()

        def tel(info, progression, arret, racine):
            commence.set()
            if not arret.wait(10):
                raise AssertionError('jamais annulé')
            raise InterruptedError
        g = self.gestion(telecharger=tel)
        g.verifier()
        self.attendre(g)
        g.installer()
        self.assertTrue(commence.wait(5))
        self.assertEqual(g.resume()['etat'], 'telechargement')
        self.assertEqual(g.installer()['etat'], 'telechargement')     # deuxième clic : sans effet
        g.annuler()
        self.attendre(g)
        self.assertEqual(g.etat, 'disponible')
        self.assertEqual((self.lances, self.arrets), ([], []))

    def test_arret_pendant_le_telechargement(self):
        def tel(info, progression, arret, racine):
            g.fermer()
            return Path('C:/essai/x.exe')
        g = self.gestion(telecharger=tel)
        g.verifier()
        self.attendre(g)
        g.installer()
        self.attendre(g)
        self.assertEqual((self.lances, self.arrets), ([], []))          # MyPhotos s'arrêtait : rien n'est lancé
        with self.assertRaises(M.Refus):
            g.installer()

    def test_bilan_au_lancement_suivant(self):
        self.meta['maj_lancee'] = json.dumps({'de': '0.3.0', 'vers': config.VERSION, 'quand': 1})
        g = self.gestion()
        self.assertEqual(g.resume()['apres'], {'reussie': True, 'version': config.VERSION})
        g.bilan_vu()
        self.assertIsNone(self.meta['maj_lancee'])
        self.assertIsNone(g.resume()['apres'])
        self.meta['maj_lancee'] = json.dumps({'de': config.VERSION, 'vers': SUIVANTE, 'quand': 1})
        self.assertEqual(self.gestion().apres, {'reussie': False, 'version': SUIVANTE})
        for vieux in (json.dumps({'vers': '0.0.1'}), json.dumps({'vers': 'abc'}), '{abîmé', json.dumps([1])):
            self.meta['maj_lancee'] = vieux
            self.assertIsNone(self.gestion().apres, vieux)

    def test_veille(self):
        g = self.gestion()
        g.PREMIERE_ATTENTE = 0
        self.meta['maj_auto'] = 'oui'
        with mock.patch.object(M, 'nettoyer'):
            g.demarrer()
        fin = time.time() + 10
        while g.etat != 'disponible' and time.time() < fin:
            time.sleep(0.05)
        self.assertEqual(g.etat, 'disponible')
        g.fermer()


@unittest.skipUnless(os.name == 'nt', 'signatures CNG : Windows seulement')
class Publication(unittest.TestCase):
    """construire.py --manifeste, avec une clé d'essai (jamais la clé du projet) et un faux installateur."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'construction'))
        import construire
        self.construire = construire
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.privee, self.publique = signature.nouvelle_cle()

    def test_manifeste(self):
        inst = self.d / M.nom_installateur(config.VERSION)
        contenu = os.urandom(TAILLE)
        inst.write_bytes(contenu)
        cible = self.construire.manifeste(sortie=self.d, cle_privee=self.privee)
        self.assertEqual(cible, self.d / 'MyPhotos-mise-a-jour.json')
        i = M.lire_manifeste(cible.read_bytes(), cle=self.publique)
        self.assertEqual((i['version'], i['taille'], i['sha256']),
                         (config.VERSION, TAILLE, hashlib.sha256(contenu).hexdigest()))
        self.assertEqual(i['nouveautes'], self.construire.nouveautes(config.VERSION))
        self.assertTrue(cible.with_name(cible.name + '.sha256').read_text(encoding='ascii')
                        .endswith('  MyPhotos-mise-a-jour.json\n'))
        with self.assertRaises(M.Refusee):                           # signé par la clé d'essai : MyPhotos refuse
            M.lire_manifeste(cible.read_bytes())

    def test_sans_installateur_ni_nouveautes(self):
        with self.assertRaises(SystemExit):
            self.construire.manifeste(sortie=self.d, cle_privee=self.privee)
        (self.d / M.nom_installateur(config.VERSION)).write_bytes(os.urandom(TAILLE))
        vide = self.d / 'nouveautes.json'
        vide.write_text('{"0.0.1": ["x"]}', encoding='utf-8')
        with mock.patch.object(self.construire, 'NOUVEAUTES', vide):
            with self.assertRaisesRegex(SystemExit, 'aucune nouveauté'):
                self.construire.manifeste(sortie=self.d, cle_privee=self.privee)

    def test_nouveautes_de_la_version(self):
        # Chaque version publiée a ses nouveautés, acceptées par MyPhotos (longueur, nombre, caractères).
        lignes = self.construire.nouveautes(config.VERSION)
        self.assertLessEqual(len(lignes), M.NOUVEAUTES_MAX)
        self.assertEqual([M._texte_propre(n) for n in lignes], lignes)


if __name__ == '__main__':
    unittest.main()
