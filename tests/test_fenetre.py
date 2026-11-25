"""Essais de la fenêtre de MyPhotos (myphotos/fenetre.py) et de ses raccourcis (myphotos/raccourci.py).

    .venv\\Scripts\\python -m unittest tests.test_fenetre -v

Sans interface, toujours : choix du mode et replis, placement de la fenêtre (écrans), relance (premier plan,
ancienne version), filtre des navigations, question à la fermeture, raccourcis (chemins, fichier .lnk écrit dans un
dossier temporaire). Aucune fenêtre ne s'ouvre, aucun navigateur, ni Bureau ni menu Démarrer touchés.

Essai réel, à la demande (Windows) :

    set MYPHOTOS_ESSAI_FENETRE=1
    set MYPHOTOS_ESSAI_UI=C:\\chemin\\essai-ui                 (données d'essai)
    set MYPHOTOS_ESSAI_PHOTOS=C:\\chemin\\images-publiques     (images publiques : chien-1.jpg, plage-2.jpg…)
    set MYPHOTOS_MODELES=C:\\chemin\\modele                    (moteur « base » déjà téléchargé)
    .venv\\Scripts\\python -m unittest tests.test_fenetre.TestFenetreReelle -v

Il prépare des données (photos publiques analysées), puis lance MyPhotos sur un bureau Windows INVISIBLE (rien sur
l'écran de l'utilisateur, ni son clavier), avec MYPHOTOS_DEBUG=1 et un port de débogage pour WebView2. Piloté par
le protocole DevTools : page affichée (DPI « par écran », aucun window.pywebview), recherche, Ctrl+A et Échap, lien
https (navigateur simulé), navigation vers un fichier bloquée, « Choisir un dossier » (fenêtre de Windows qui
appartient à la fenêtre MyPhotos et la bloque), relance (la fenêtre réduite revient), fermeture pendant un
téléchargement simulé du moteur (une question : « Non » garde tout, « Oui » arrête tout ; code 0, aucun processus
restant, données de WebView2 effacées, position retenue). Le navigateur, le presse-papiers, la Corbeille et
l'Explorateur sont simulés dans le processus d'essai ; « Choisir un dossier » s'ouvre sur un dossier vide.
"""
import ctypes
import ctypes.wintypes
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / 'tests'))

from myphotos import config, fenetre, raccourci, serveur  # noqa: E402

PORT_ESSAI = 65011                          # nom d'événement d'essai : jamais celui d'une vraie MyPhotos


# ---------------------------------------------------------------- choix du mode, environnement

class TestModes(unittest.TestCase):
    def test_mode_demande(self):
        self.assertEqual(fenetre.mode_demande([], {}, sans_page=True), 'sans_page')
        self.assertEqual(fenetre.mode_demande(['--navigateur'], {}, sans_page=True), 'sans_page')
        self.assertEqual(fenetre.mode_demande(['--navigateur'], {}), 'navigateur')
        self.assertEqual(fenetre.mode_demande([], {'MYPHOTOS_NAVIGATEUR': '1'}), 'navigateur')
        self.assertEqual(fenetre.mode_demande([], {'MYPHOTOS_NAVIGATEUR': '0'}), 'fenetre')
        self.assertEqual(fenetre.mode_demande(None, {}), 'fenetre')

    CLR = {'COR_ENABLE_PROFILING': '1', 'COR_PROFILER': '{0}', 'COMPlus_Version': 'v4', 'DOTNET_x': '1',
           'APPDOMAIN_MANAGER_ASM': 'Canari', 'appdomain_manager_type': 'G', 'PYTHONNET_RUNTIME': 'coreclr',
           'PYTHONNET_NETFX_CONFIG_FILE': 'x.config'}

    def test_environnement_nettoye(self):
        env = {'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': '--remote-debugging-port=9222',
               'WEBVIEW2_BROWSER_EXECUTABLE_FOLDER': 'x', 'Webview2_User_Data_Folder': 'y', 'PATH': 'p',
               'MYPHOTOS_PORT': '1', 'PYWEBVIEW_GUI': 'qt', **self.CLR}
        garde = dict(env)
        # Débogage : WEBVIEW2_* et PYWEBVIEW_* gardées ; les réglages du CLR, jamais.
        self.assertEqual(sorted(fenetre.nettoyer_environnement(garde, debug=True)), sorted(self.CLR))
        self.assertEqual(set(garde), set(env) - set(self.CLR))
        retirees = fenetre.nettoyer_environnement(env)
        self.assertEqual(sorted(retirees), sorted(['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', 'PYWEBVIEW_GUI',
                                                   'WEBVIEW2_BROWSER_EXECUTABLE_FOLDER', 'Webview2_User_Data_Folder',
                                                   *self.CLR]))
        self.assertEqual(env, {'PATH': 'p', 'MYPHOTOS_PORT': '1'})

    def test_debogage(self):
        """Depuis les sources, MYPHOTOS_DEBUG=1 suffit ; l'exe exige EN PLUS --essai-debogage (une variable
        d'environnement persistante seule n'ouvre jamais de port de débogage)."""
        with mock.patch.object(config, 'GELE', False):
            self.assertTrue(fenetre.debogage({'MYPHOTOS_DEBUG': '1'}, argv=[]))
            self.assertFalse(fenetre.debogage({'MYPHOTOS_DEBUG': 'oui'}, argv=[]))
        with mock.patch.object(config, 'GELE', True):
            self.assertFalse(fenetre.debogage({'MYPHOTOS_DEBUG': '1'}, argv=[]))
            self.assertFalse(fenetre.debogage({}, argv=['--essai-debogage']))
            self.assertTrue(fenetre.debogage({'MYPHOTOS_DEBUG': '1'}, argv=['--essai-debogage']))

    def test_config_clr_etrangere(self):
        """<exécutable>.config posé à côté du programme : .NET n'est pas chargé, repli sur Edge (sans même chercher
        WebView2)."""
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / 'MyPhotos.exe'
            exe.write_bytes(b'MZ')
            with mock.patch.object(fenetre.sys, 'executable', str(exe)):
                propre = fenetre.config_clr_etrangere()
                (Path(d) / 'MyPhotos.exe.config').write_text('<configuration/>', encoding='utf-8')
                self.assertEqual(fenetre.config_clr_etrangere(), str(exe) + '.config')
                with mock.patch.object(fenetre, 'version_webview2') as version:
                    self.assertEqual(fenetre.FenetreWebView2(mock.Mock()).lancer(), 'echec')
                version.assert_not_called()
        self.assertIsNone(propre)

    def test_edge_jamais_depuis_hkcu(self):
        """Repli Edge : App Paths de la MACHINE et Program Files seulement (HKCU et %LOCALAPPDATA% : inscriptibles
        par n'importe quel programme de l'utilisateur)."""
        if os.name != 'nt':
            self.skipTest('Windows')
        import winreg
        lus = []

        def ouvrir(racine, chemin):
            lus.append(racine)
            raise OSError('absent')
        with mock.patch.object(winreg, 'OpenKey', ouvrir), \
                mock.patch.dict(os.environ, {'ProgramFiles(x86)': r'C:\nulle-part', 'ProgramFiles': r'C:\nulle-part',
                                             'LOCALAPPDATA': str(RACINE)}), \
                mock.patch.object(fenetre.os.path, 'isfile', return_value=True) as existe:
            trouve = fenetre.trouver_edge()
        self.assertEqual(lus, [winreg.HKEY_LOCAL_MACHINE])
        self.assertTrue(trouve.startswith(r'C:\nulle-part'))
        self.assertFalse(any(str(RACINE) in str(c.args[0]) for c in existe.call_args_list))

    def test_net_framework_4_7_2(self):
        """pythonnet 3 exige .NET Framework 4.7.2 (Release 461808) : en dessous, directement Edge."""
        if os.name != 'nt':
            self.skipTest('Windows')
        import winreg
        vrai = winreg.QueryValueEx
        for release, attendu in ((394802, False), (461308, False), (461808, None)):
            def lire(k, nom, release=release):
                return (release, 4) if nom == 'Release' else vrai(k, nom)
            with mock.patch.object(winreg, 'QueryValueEx', lire):
                r = fenetre._version_registre()
            if attendu is False:
                self.assertFalse(r, release)

    def test_versions(self):
        self.assertEqual(fenetre.version_tuple('0.2.0'), (0, 2, 0))
        self.assertEqual(fenetre.version_tuple('0.3.0b1'), (0, 3, 0))
        self.assertEqual(fenetre.version_tuple(None), ())
        self.assertGreaterEqual(fenetre.version_tuple(config.VERSION), fenetre.VERSION_FENETRE)


class FauxServeur:
    def __init__(self):
        self.server_address = ('127.0.0.1', PORT_ESSAI)
        self.arret = threading.Event()
        self.ferme = False

    def serve_forever(self, poll_interval=0.5):
        self.arret.wait(30)

    def shutdown(self):
        self.arret.set()

    def server_close(self):
        self.ferme = True


class FausseApp:
    def __init__(self, serveur_):
        self.serveur = serveur_
        self.mode = 'navigateur'
        self.inactivite = True
        self.quand_arret = None
        self.derniere_activite = time.monotonic()
        self.arret_lance = False
        self.ferme = False
        self.choix_dossier = None

    def serveur_pret(self):
        pass

    def activite(self):
        self.derniere_activite = time.monotonic()

    def arreter(self):
        if not self.arret_lance:
            self.arret_lance = True
            if self.quand_arret:
                self.quand_arret()
            self.serveur.shutdown()

    def fermer(self):
        self.ferme = True


class TestReplis(unittest.TestCase):
    """Ordre des modes : WebView2, puis Edge, puis le navigateur par défaut ; serveur arrêté proprement ensuite."""

    def executer(self, webview2, edge, activite_page=False):
        srv = FauxServeur()
        app = FausseApp(srv)
        hote = fenetre.Hote(app, srv, 'http://127.0.0.1:65011/')
        appels = []

        def lancer_webview(_self):
            appels.append('fenetre')
            return webview2

        def lancer_edge(f):
            appels.append('edge')
            f.debut = time.monotonic()
            if activite_page:
                app.derniere_activite = time.monotonic() + 1
            return edge

        def navigateur(_hote, ouvrir):
            appels.append(('navigateur', ouvrir))
            srv.shutdown()

        with mock.patch.object(fenetre, 'preparer_processus'), \
                mock.patch.object(fenetre, 'creer_evenement', return_value=None), \
                mock.patch.object(fenetre.FenetreWebView2, 'lancer', lancer_webview), \
                mock.patch.object(fenetre.FenetreEdge, 'lancer', lancer_edge), \
                mock.patch.object(fenetre.Hote, '_navigateur', navigateur), \
                mock.patch.object(fenetre, 'arreter_webview2_restants', return_value=[]), \
                mock.patch.object(fenetre, 'EDGE_PAGE_ATTENDUE_S', 0.3):
            self.assertEqual(hote.executer(), 0)
        self.assertTrue(app.arret_lance and app.ferme and srv.ferme)          # arrêt propre dans tous les cas
        self.assertFalse(app.inactivite)                                     # la fenêtre décide de la fin
        return appels

    def test_fenetre_seule(self):
        self.assertEqual(self.executer('fini', 'fini'), ['fenetre'])

    def test_repli_edge(self):
        self.assertEqual(self.executer('echec', 'fini'), ['fenetre', 'edge'])

    def test_repli_navigateur(self):
        self.assertEqual(self.executer('echec', 'echec'), ['fenetre', 'edge', ('navigateur', True)])

    def test_edge_passe_la_main(self):
        # Edge a ouvert la page ailleurs : elle appelle MyPhotos, rien à rouvrir ; sinon, le navigateur l'ouvre.
        self.assertEqual(self.executer('echec', 'perdue', activite_page=True),
                         ['fenetre', 'edge', ('navigateur', False)])
        self.assertEqual(self.executer('echec', 'perdue'), ['fenetre', 'edge', ('navigateur', True)])

    def test_webview2_restants(self):
        """Filet de sécurité à la fermeture : seulement les processus navigateurs de WebView2 lancés par MyPhotos
        (enfants DIRECTS) ; jamais un descendant retrouvé par « PID parent », ni un identifiant réutilisé, ni le
        WebView2 d'un autre programme, ni Edge."""
        tous = {10: (1, 'MyPhotos.exe'),
                20: (10, 'msedgewebview2.exe'), 21: (20, 'msedgewebview2.exe'), 22: (21, 'crashpad_handler.exe'),
                30: (10, 'msedgewebview2.exe'),          # créé avant MyPhotos : identifiant de parent réutilisé
                40: (99, 'msedgewebview2.exe'),          # WebView2 d'un autre programme
                50: (10, 'msedge.exe')}                  # Edge du repli : fermé autrement
        dates = {10: 100, 20: 110, 21: 111, 22: 112, 30: 50, 40: 120, 50: 130}
        self.assertEqual(sorted(fenetre.webview2_restants(tous, 10, dates.get)), [20])
        self.assertEqual(fenetre.webview2_restants(tous, 10, lambda p: None), [])
        # Constat R3 : le PID 200 de WebView2 a appartenu à un programme qui a lancé Discord (300, puis 310) ;
        # Discord n'est jamais visé.
        tous = {100: (1, 'MyPhotos.exe'), 200: (100, 'msedgewebview2.exe'), 300: (200, 'Discord.exe'),
                310: (300, 'Discord.exe'), 400: (200, 'msedgewebview2.exe')}
        dates = {100: 1000, 200: 3000, 300: 1500, 310: 1600, 400: 3100}
        self.assertEqual(fenetre.webview2_restants(tous, 100, dates.get), [200])
        if os.name == 'nt':                               # le vrai processus d'essai n'a lancé aucun WebView2
            self.assertEqual(fenetre.webview2_restants(), [])
            self.assertEqual(fenetre.arreter_webview2_restants(0.1), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_verification_par_le_handle(self):
        """Avant tout arrêt, le processus est revérifié PAR SON HANDLE : notre enfant direct, créé après nous, avec le
        nom attendu. Essai sur un vrai processus d'essai (dans un Job Object), jamais arrêté par ce chemin."""
        import processus_essai
        job = processus_essai.Job()
        try:
            p = processus_essai.popen(job, [sys.executable, '-c', 'import time; time.sleep(30)'],
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            moi = os.getpid()
            debut = fenetre._creation(moi)
            nom = os.path.basename(sys.executable)
            k32 = fenetre._dll('kernel32')
            h = fenetre._ouvrir_verifie(p.pid, moi, debut, nom=nom)
            self.assertTrue(h)
            k32.CloseHandle(h)
            self.assertIsNone(fenetre._ouvrir_verifie(p.pid, moi + 4, debut, nom=nom))       # autre parent
            self.assertIsNone(fenetre._ouvrir_verifie(p.pid, moi, debut))                  # pas msedgewebview2
            self.assertIsNone(fenetre._ouvrir_verifie(p.pid, moi, 1 << 62, nom=nom))       # créé « avant » nous
        finally:
            processus_essai.arreter(p)

    def test_donnees_webview2_effacees(self):
        """Fenêtre fermée tout de suite ou WebView2 planté : MyPhotos efface lui-même DONNEES\\webview2 (jamais
        DONNEES), et le faux avertissement de pywebview est filtré."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'webview2' / 'EBWebView').mkdir(parents=True)
            (Path(d) / 'webview2' / 'EBWebView' / 'x.log').write_text('x')
            (Path(d) / 'index.db').write_text('base')
            self.assertTrue(fenetre.effacer_donnees_webview2(d))
            self.assertFalse((Path(d) / 'webview2').exists())
            self.assertEqual((Path(d) / 'index.db').read_text(), 'base')
            self.assertTrue(fenetre.effacer_donnees_webview2(d))                           # rien à effacer
        f = fenetre._FiltreEffacement()
        rec = lambda m: logging.LogRecord('pywebview', logging.WARNING, '', 0, m, None, None)   # noqa: E731
        self.assertFalse(f.filter(rec("Failed to delete user data folder: 'NoneType' object has no attribute")))
        self.assertTrue(f.filter(rec('autre avertissement')))

    def test_terminer_libere_le_port_avant_de_fermer(self):
        """Constat R1 : le port est libéré (server_close) AVANT app.fermer(), qui peut attendre le moteur ; les
        données de WebView2 effacées avant de libérer le port."""
        srv = FauxServeur()
        app = FausseApp(srv)
        ordre = []
        srv.server_close = lambda: ordre.append('server_close')
        app.fermer = lambda: ordre.append('fermer')
        hote = fenetre.Hote(app, srv, 'http://127.0.0.1:65011/')
        hote.webview = mock.Mock()
        with mock.patch.object(fenetre, 'arreter_webview2_restants', side_effect=lambda: ordre.append('restants')), \
                mock.patch.object(fenetre, 'effacer_donnees_webview2', side_effect=lambda: ordre.append('effacer')):
            hote._terminer()
        self.assertEqual(ordre, ['restants', 'effacer', 'server_close', 'fermer'])

    def test_plantages_a_repetition(self):
        """Constat R7 : 3 plantages du rendu en 2 minutes, la page est rechargée ; au 4e, une question de MyPhotos
        (pas la page d'erreur de WebView2), une seule fois."""
        f = fenetre.FenetreWebView2(mock.Mock())
        sender = mock.Mock()
        args = types.SimpleNamespace(ProcessFailedKind='RenderProcessExited')
        with mock.patch.object(f, '_demander_reessai') as question:
            for _ in range(5):
                f._processus_arrete(sender, args)
        self.assertEqual(sender.Reload.call_count, 3)
        question.assert_called_once_with(sender)
        self.assertIn('Réessayer', fenetre.PLANTAGES)
        self.assertNotIn('Edge', fenetre.PLANTAGES)

    def test_fenetres_d_edge(self):
        """Le processus d'Edge a d'autres fenêtres visibles (sans titre, utilitaires) : seule la sienne est visée."""
        fenetres = [{'hwnd': 1, 'classe': 'Chrome_WidgetWin_1', 'titre': ''},
                    {'hwnd': 2, 'classe': 'Chrome_WidgetWin_1', 'titre': 'Paramètres'},
                    {'hwnd': 3, 'classe': 'Intermediate D3D Window', 'titre': 'MyPhotos'},
                    {'hwnd': 4, 'classe': 'Chrome_WidgetWin_1', 'titre': 'MyPhotos'}]
        self.assertEqual([f['hwnd'] for f in fenetre.fenetres_edge(fenetres)], [4, 2])
        edge = fenetre.FenetreEdge(mock.Mock())
        edge.proc = mock.Mock(pid=123)
        with mock.patch.object(fenetre, 'fenetres_du_processus', return_value=fenetres) as liste, \
                mock.patch.object(fenetre, 'passer_devant') as devant:
            edge.premier_plan()
        liste.assert_called_once_with(123)
        devant.assert_called_once_with(4)

    def test_choix_dossier_au_milieu_d_edge(self):
        """Repli Edge : « Choisir un dossier » (serveur.ChoixDossier) s'ouvre au milieu de la fenêtre d'Edge qui
        montre MyPhotos (FenetreEdge.zone) ; Edge absent ou sans fenêtre : None (le milieu de l'écran)."""
        fenetres = [{'hwnd': 7, 'classe': 'Chrome_WidgetWin_1', 'titre': 'MyPhotos'}]

        def rect(hwnd, r):
            r._obj.left, r._obj.top, r._obj.right, r._obj.bottom = (hwnd * 10, 20, 830, 640)
            return True
        app = types.SimpleNamespace(choix_dossier=serveur.ChoixDossier(ouvrir=mock.Mock()), mode=None)
        edge = fenetre.FenetreEdge(types.SimpleNamespace(app=app, _fin=threading.Event(),
                                                         adresse='http://127.0.0.1:65011/?jeton=x'))
        self.assertIsNone(edge.zone())                                     # pas encore lancé
        proc = mock.Mock(pid=123)
        proc.poll.return_value = None
        proc.wait.return_value = 0
        with mock.patch.object(fenetre, 'trouver_edge', return_value='msedge.exe'), \
                mock.patch.object(fenetre.subprocess, 'Popen', return_value=proc), \
                mock.patch.object(fenetre, 'fenetres_du_processus', return_value=fenetres), \
                mock.patch.object(fenetre, '_dll', return_value=types.SimpleNamespace(GetWindowRect=rect)):
            self.assertEqual(edge.lancer(), 'perdue')                     # Edge rendu aussitôt : peu importe ici
            self.assertEqual(app.choix_dossier.zone, edge.zone)
            self.assertEqual(app.choix_dossier.zone(), (70, 20, 830, 640))
            with mock.patch.object(fenetre, 'fenetres_du_processus', return_value=[]):
                self.assertIsNone(edge.zone())
            proc.poll.return_value = 0                                    # Edge fermé
            self.assertIsNone(edge.zone())

    def test_arret_demande_ferme_la_fenetre(self):
        srv = FauxServeur()
        app = FausseApp(srv)
        hote = fenetre.Hote(app, srv, 'http://127.0.0.1:65011/')
        hote.webview = mock.Mock()
        hote._arret_demande()
        time.sleep(0.2)
        hote.webview.fermer.assert_called_once_with()
        self.assertTrue(hote._fin.is_set())


# ---------------------------------------------------------------- placement

class TestPlacement(unittest.TestCase):
    PRINCIPAL = {'zone': (0, 0, 1920, 1040), 'dpi': 96, 'principal': True}
    GAUCHE = {'zone': (-1920, 0, 0, 1040), 'dpi': 96, 'principal': False}
    PORTABLE = {'zone': (0, 0, 1366, 728), 'dpi': 96, 'principal': True}

    def test_premier_lancement_centre(self):
        p = fenetre.placer(None, [self.PRINCIPAL, self.GAUCHE])
        self.assertEqual(p, {'x': 320, 'y': 90, 'l': 1280, 'h': 860, 'maximisee': False})
        # Écran à 150 % : même taille en pixels logiques.
        p = fenetre.placer(None, [dict(self.PRINCIPAL, zone=(0, 0, 2880, 1680), dpi=144)])
        self.assertEqual((p['l'], p['h']), (1920, 1290))

    def test_petit_ecran_agrandie(self):
        p = fenetre.placer(None, [self.PORTABLE])
        self.assertTrue(p['maximisee'])
        self.assertLessEqual((p['l'], p['h']), (1366, 728))
        self.assertEqual((p['x'], p['y'], p['h']), (43, 0, 728))

    def test_position_retenue(self):
        sauve = {'x': -1500, 'y': 100, 'l': 1000, 'h': 700, 'dpi': 96, 'maximisee': True}
        self.assertEqual(fenetre.placer(sauve, [self.PRINCIPAL, self.GAUCHE]),
                         {'x': -1500, 'y': 100, 'l': 1000, 'h': 700, 'maximisee': True})

    def test_ecran_disparu(self):
        sauve = {'x': -1500, 'y': 100, 'l': 1000, 'h': 700, 'dpi': 96, 'maximisee': False}
        p = fenetre.placer(sauve, [self.PRINCIPAL])
        self.assertEqual(p, {'x': 460, 'y': 170, 'l': 1000, 'h': 700, 'maximisee': False})

    def test_a_moitie_dehors_et_trop_grande(self):
        p = fenetre.placer({'x': 1500, 'y': 900, 'l': 1000, 'h': 700, 'dpi': 96}, [self.PRINCIPAL])
        self.assertEqual(p, {'x': 920, 'y': 340, 'l': 1000, 'h': 700, 'maximisee': False})
        p = fenetre.placer({'x': 10, 'y': 10, 'l': 4000, 'h': 3000, 'dpi': 96}, [self.PRINCIPAL])
        self.assertEqual(p, {'x': 0, 'y': 0, 'l': 1920, 'h': 1040, 'maximisee': False})
        p = fenetre.placer({'x': 10, 'y': 10, 'l': 100, 'h': 50, 'dpi': 96}, [self.PRINCIPAL])
        self.assertEqual((p['l'], p['h']), (fenetre.LARGEUR_MIN, fenetre.HAUTEUR_MIN))

    def test_changement_de_dpi(self):
        # Retenue à 100 %, l'écran est passé à 150 % : même taille logique, donc 1,5 fois plus de pixels.
        p = fenetre.placer({'x': 100, 'y': 100, 'l': 1000, 'h': 600, 'dpi': 96},
                           [dict(self.PRINCIPAL, zone=(0, 0, 2880, 1680), dpi=144)])
        self.assertEqual((p['x'], p['y'], p['l'], p['h']), (100, 100, 1500, 900))

    def test_sauvegarde_illisible(self):
        for mauvais in ({'x': 'a'}, {'x': 1, 'y': 1, 'l': 0, 'h': 10}, {'x': 1, 'y': 1, 'l': 10, 'h': 10, 'dpi': 5},
                        [], 'texte'):
            self.assertEqual(fenetre.placer(mauvais, [self.PRINCIPAL]), fenetre.placer(None, [self.PRINCIPAL]))
        self.assertIsNone(fenetre.placer(None, []))

    def test_fichier(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(fenetre.lire_placement(d))
            fenetre.ecrire_placement(d, {'x': 1, 'y': 2, 'l': 3, 'h': 4, 'dpi': 96, 'maximisee': True})
            self.assertEqual(fenetre.lire_placement(d)['maximisee'], True)
            self.assertEqual(sorted(os.listdir(d)), [fenetre.PLACEMENT])
            (Path(d) / fenetre.PLACEMENT).write_text('{abîmé', encoding='utf-8')
            self.assertIsNone(fenetre.lire_placement(d))


# ---------------------------------------------------------------- relance

class TestRelance(unittest.TestCase):
    NOUVELLE = {'nom': 'MyPhotos', 'version': '0.3.0', 'mode': 'fenetre'}

    def setUp(self):
        # serveur.main() appelé ici : jamais le vrai mutex de MyPhotos dans le processus d'essai (TestMutex).
        patcher = mock.patch.object(fenetre, 'tenir_mutex')
        patcher.start()
        self.addCleanup(patcher.stop)
        # Port « tenu par notre compte » (fenetre.port_tenu_par) : jamais la vraie table des ports de cet ordinateur.
        patcher = mock.patch.object(fenetre, 'port_tenu_par', return_value='nous')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_decisions(self):
        ancienne = {'nom': 'MyPhotos', 'version': '0.2.0'}
        for etat, demande, attendu in (
                (ancienne, 'fenetre', 'ancienne'), ({'version': '0.1.0'}, 'fenetre', 'ancienne'),
                (ancienne, 'navigateur', 'page'), (ancienne, 'sans_page', 'rien'),
                (self.NOUVELLE, 'fenetre', 'premier_plan'), (dict(self.NOUVELLE, mode='edge'), 'fenetre',
                                                              'premier_plan'),
                (dict(self.NOUVELLE, mode='navigateur'), 'fenetre', 'page'), (self.NOUVELLE, 'navigateur', 'page'),
                (self.NOUVELLE, 'sans_page', 'rien'), (dict(self.NOUVELLE, version='0.2.0'), 'fenetre', 'ancienne')):
            self.assertEqual(fenetre.decision_relance(etat, demande), attendu, (etat, demande))

    def relancer(self, etat, demande='fenetre', signal_ok=True):
        page, infos = mock.Mock(), mock.Mock()
        with mock.patch.object(fenetre, 'signaler_premier_plan', return_value=signal_ok) as signal:
            self.assertEqual(fenetre.relancer(etat, 'http://127.0.0.1:1/', 1, demande, page, infos), 0)
        return page, infos, signal

    def test_ancienne_version(self):
        page, infos, signal = self.relancer({'nom': 'MyPhotos', 'version': '0.2.0'})
        page.assert_called_once_with('http://127.0.0.1:1/')
        infos.assert_called_once_with(fenetre.ANCIENNE_VERSION)
        signal.assert_not_called()
        self.assertIn('Arrêter MyPhotos', fenetre.ANCIENNE_VERSION)

    def test_premier_plan(self):
        page, infos, signal = self.relancer(self.NOUVELLE)
        signal.assert_called_once_with(1)
        page.assert_not_called()
        infos.assert_not_called()
        # Personne n'écoute l'événement : la page s'ouvre, comme avant.
        page, _, _ = self.relancer(self.NOUVELLE, signal_ok=False)
        page.assert_called_once_with('http://127.0.0.1:1/')

    def test_autre_compte(self):
        """Constat R9 : MyPhotos ouvert par un autre compte Windows du PC (port 127.0.0.1 commun) : un message, et
        jamais sa page (ses photos) ; ni premier plan, ni page."""
        moi = fenetre.identite_compte()
        if os.name == 'nt':
            self.assertRegex(moi, r'^[0-9a-f]{16}$')
        etat = dict(self.NOUVELLE, compte='0123456789abcdef')
        for demande in ('fenetre', 'navigateur'):
            page, infos, signal = self.relancer(etat, demande)
            page.assert_not_called()
            signal.assert_not_called()
            infos.assert_called_once_with(fenetre.AUTRE_COMPTE)
        page, infos, _ = self.relancer(etat, 'sans_page')
        page.assert_not_called()
        infos.assert_not_called()
        # Même compte, ou version sans « compte » : comme avant.
        page, infos, signal = self.relancer(dict(self.NOUVELLE, compte=moi))
        signal.assert_called_once_with(1)
        infos.assert_not_called()
        self.assertFalse(fenetre.autre_compte({'version': '0.2.0'}, moi))

    def test_jeton_refuse(self):
        """0.4.0 : notre MyPhotos (port tenu par notre compte, vérifié avant) refuse le jeton lu dans nos données
        (autres données, ou jeton pas encore écrit) : sa fenêtre revient si l'événement de notre session répond
        (relecture adverse : avant, faux message « autre compte ») ; sinon un message, et rien n'est ouvert."""
        etat = {'nom': 'MyPhotos', 'jeton_refuse': True}
        page, infos, signal = self.relancer(etat, 'fenetre')
        signal.assert_called_once_with(1)
        page.assert_not_called()
        infos.assert_not_called()
        for demande, signal_ok in (('fenetre', False), ('navigateur', True)):
            page, infos, signal = self.relancer(etat, demande, signal_ok=signal_ok)
            page.assert_not_called()
            infos.assert_called_once_with(fenetre.JETON_REFUSE)
        page, infos, signal = self.relancer(etat, 'sans_page')
        page.assert_not_called()
        infos.assert_not_called()
        signal.assert_not_called()

    def test_main_port_d_un_autre_compte(self):
        """Relecture adverse de la 0.4.0 : le port est tenu par un programme d'un AUTRE compte Windows (ou que Windows
        ne laisse pas identifier). Rien ne lui est envoyé : ni /api/etat avec notre jeton, ni la page ; un message."""
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / 'jeton'
            serveur.creer_jeton(chemin)
            with mock.patch.object(serveur, 'Serveur', side_effect=OSError('port pris')), \
                    mock.patch.object(fenetre, 'port_tenu_par', return_value='autre'), \
                    mock.patch.object(config, 'JETON', chemin), \
                    mock.patch.object(serveur, 'etat_instance') as etat, \
                    mock.patch.object(serveur, 'configurer_journal'), \
                    mock.patch.object(serveur, 'ouvrir_page') as page, \
                    mock.patch.object(fenetre, 'signaler_premier_plan') as signal, \
                    mock.patch.object(serveur, 'informer') as infos, \
                    mock.patch.object(serveur, 'alerter') as alerte:
                for argv in ([], ['--navigateur']):
                    self.assertEqual(serveur.main(argv), 1)
        etat.assert_not_called()
        page.assert_not_called()
        signal.assert_not_called()
        infos.assert_not_called()
        self.assertEqual(alerte.call_args[0][0], serveur.PORT_PRIS.format(port=config.PORT))
        self.assertIn('autre compte', alerte.call_args[0][0])

    def test_main_port_libere_puis_pris(self):
        """Plus personne n'écoute (une MyPhotos finit de s'arrêter) : on réessaie, sans rien envoyer ; si le port
        reste pris sans écouteur au-delà de PORT_LIBERE_S, le message d'avant."""
        essais = []

        def serveur_(*_a, **_k):
            essais.append(1)
            if len(essais) < 3:
                raise OSError('port pris')
            raise RuntimeError('démarré')
        with mock.patch.object(serveur, 'Serveur', side_effect=serveur_), \
                mock.patch.object(fenetre, 'port_tenu_par', return_value=None), \
                mock.patch.object(serveur, 'etat_instance') as etat, \
                mock.patch.object(serveur, 'configurer_journal'), \
                mock.patch.object(serveur.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'démarré'):
                serveur.main([])
        etat.assert_not_called()

    def test_main_adresse_de_relance(self):
        """Relance : la page d'une MyPhotos 0.4.0 (ou plus) s'ouvre par l'adresse d'entrée, avec le jeton lu dans les
        données ; celle d'une 0.3.0 ou plus ancienne, sans jeton. Une relance ne remplace jamais le jeton."""
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / 'jeton'
            jeton = serveur.creer_jeton(chemin)
            adresse = f'http://{config.HOTE}:{config.PORT}/'
            for etat, attendu in ((dict(self.NOUVELLE, mode='navigateur', acces_par_jeton=True),
                                   f'{adresse}?jeton={jeton}'),
                                  (dict(self.NOUVELLE, mode='navigateur'), adresse),
                                  ({'nom': 'MyPhotos', 'version': '0.2.0'}, adresse)):
                with mock.patch.object(serveur, 'Serveur', side_effect=OSError('port pris')), \
                        mock.patch.object(config, 'JETON', chemin), \
                        mock.patch.object(serveur, 'etat_instance', return_value=etat) as lire, \
                        mock.patch.object(serveur, 'SANS_PAGE', False), \
                        mock.patch.object(serveur, 'configurer_journal'), \
                        mock.patch.object(serveur, 'ouvrir_page') as page, \
                        mock.patch.object(serveur, 'informer'):
                    self.assertEqual(serveur.main(['--navigateur']), 0)
                page.assert_called_once_with(attendu)
                self.assertEqual(lire.call_args[0][1], jeton)              # /api/etat lu avec le jeton des données
            self.assertEqual(serveur.lire_jeton(chemin), jeton)

    def test_main_jeton_refuse_puis_reecrit(self):
        """Relance pendant le démarrage d'une MyPhotos : son jeton a été écrit pendant notre requête (refusée) ; on
        relit, on réessaie, et sa fenêtre revient au premier plan (pas de faux message « autre compte »)."""
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / 'jeton'
            ancien = serveur.creer_jeton(chemin)
            etats = []

            def etat_instance(_port, jeton):
                etats.append(jeton)
                if len(etats) == 1:
                    serveur.creer_jeton(chemin)                            # la MyPhotos qui démarre écrit le sien
                    return {'nom': 'MyPhotos', 'jeton_refuse': True}
                return dict(self.NOUVELLE, acces_par_jeton=True)
            with mock.patch.object(serveur, 'Serveur', side_effect=OSError('port pris')), \
                    mock.patch.object(config, 'JETON', chemin), \
                    mock.patch.object(serveur, 'etat_instance', side_effect=etat_instance), \
                    mock.patch.object(serveur, 'configurer_journal'), \
                    mock.patch.object(serveur, 'informer') as infos, \
                    mock.patch.object(serveur.fenetre, 'mode_demande', return_value='fenetre'), \
                    mock.patch.object(fenetre, 'signaler_premier_plan', return_value=True) as signal, \
                    mock.patch.object(fenetre, 'RELANCE_VERIF_S', 0), \
                    mock.patch.object(serveur.time, 'sleep'):
                self.assertEqual(serveur.main([]), 0)
            self.assertEqual(etats[0], ancien)
            self.assertEqual(etats[1], serveur.lire_jeton(chemin))
            self.assertNotEqual(etats[1], ancien)
            signal.assert_called_once()
            infos.assert_not_called()

    def test_main_jeton_et_adresse_d_entree(self):
        """Port obtenu : un nouveau jeton (256 bits) à chaque lancement, écrit dans les données et donné au serveur ;
        la fenêtre (et ses replis) reçoit l'adresse d'entrée ; le journal ne le contient pas."""
        jetons = []
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / 'jeton'
            for _ in range(2):
                srv = types.SimpleNamespace(server_address=('127.0.0.1', config.PORT), jeton=None, app=None)
                with mock.patch.object(serveur, 'Serveur', return_value=srv), \
                        mock.patch.object(config, 'JETON', chemin), \
                        mock.patch.object(serveur, 'Application'), \
                        mock.patch.object(serveur, 'configurer_journal'), \
                        mock.patch.object(serveur.fenetre, 'mode_demande', return_value='fenetre'), \
                        mock.patch.object(serveur.fenetre, 'Hote') as hote, \
                        mock.patch('builtins.print'), \
                        self.assertLogs('myphotos', 'INFO') as journal:
                    hote.return_value.executer.return_value = 0
                    self.assertEqual(serveur.main([]), 0)
                jeton = serveur.lire_jeton(chemin)
                self.assertRegex(jeton, r'^[A-Za-z0-9_-]{43}$')
                self.assertEqual(srv.jeton, jeton)
                self.assertEqual(hote.call_args[0][2], f'http://{config.HOTE}:{config.PORT}/?jeton={jeton}')
                self.assertNotIn(jeton, '\n'.join(journal.output))
                jetons.append(jeton)
            self.assertNotEqual(jetons[0], jetons[1])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ['jeton'])     # pas de « .part » restant

    def test_relance_pendant_un_arret(self):
        """Constat R1 : l'instance signalée s'arrêtait en fait (fenêtre fermée à l'instant) : relancer rend None et
        main() attend qu'elle libère le port, au lieu de sortir sans rien ouvrir."""
        page, infos = mock.Mock(), mock.Mock()
        for apres in (dict(self.NOUVELLE, arret=True), None):
            with mock.patch.object(fenetre, 'signaler_premier_plan', return_value=True), \
                    mock.patch.object(fenetre, 'RELANCE_VERIF_S', 0):
                self.assertIsNone(fenetre.relancer(self.NOUVELLE, 'http://127.0.0.1:1/', 1, 'fenetre', page, infos,
                                                   etat_actuel=lambda apres=apres: apres))
        with mock.patch.object(fenetre, 'signaler_premier_plan', return_value=True), \
                mock.patch.object(fenetre, 'RELANCE_VERIF_S', 0):
            self.assertEqual(fenetre.relancer(self.NOUVELLE, 'http://127.0.0.1:1/', 1, 'fenetre', page, infos,
                                              etat_actuel=lambda: dict(self.NOUVELLE)), 0)
        page.assert_not_called()

    def test_main_port_tenu_sans_reponse(self):
        """Constat R1 : une MyPhotos qui finit de s'arrêter garde le port un instant sans répondre : main() réessaie
        (jamais tout de suite « un autre programme ») ; au-delà de PORT_LIBERE_S, le message."""
        essais = []

        def serveur_(*_a, **_k):
            essais.append(1)
            if len(essais) < 4:
                raise OSError('port pris')
            raise RuntimeError('démarré')
        with mock.patch.object(serveur, 'Serveur', side_effect=serveur_), \
                mock.patch.object(serveur, 'etat_instance', return_value=None), \
                mock.patch.object(serveur, 'configurer_journal'), \
                mock.patch.object(serveur, 'alerter') as alerte, \
                mock.patch.object(serveur.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'démarré'):
                serveur.main([])
        alerte.assert_not_called()
        with mock.patch.object(serveur, 'Serveur', side_effect=OSError('port pris')), \
                mock.patch.object(serveur, 'etat_instance', return_value=None), \
                mock.patch.object(serveur, 'configurer_journal'), \
                mock.patch.object(serveur, 'PORT_LIBERE_S', 0), \
                mock.patch.object(serveur, 'alerter') as alerte:
            self.assertEqual(serveur.main([]), 1)
        self.assertIn('un autre programme', alerte.call_args[0][0])

    def test_main_relance_qui_tombe_sur_un_arret(self):
        essais = []

        def serveur_(*_a, **_k):
            essais.append(1)
            if len(essais) < 2:
                raise OSError('port pris')
            raise RuntimeError('démarré')
        with mock.patch.object(serveur, 'Serveur', side_effect=serveur_), \
                mock.patch.object(serveur, 'etat_instance', return_value=dict(self.NOUVELLE)), \
                mock.patch.object(serveur.fenetre, 'relancer', return_value=None) as relance, \
                mock.patch.object(serveur, 'configurer_journal'), \
                mock.patch.object(serveur.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'démarré'):
                serveur.main([])
        relance.assert_called_once()

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_evenement_nomme(self):
        """Le vrai mécanisme : événement Windows nommé (aucun site web ne peut l'atteindre)."""
        k32 = fenetre._dll('kernel32')
        self.assertFalse(fenetre.signaler_premier_plan(PORT_ESSAI))           # personne n'écoute
        h = fenetre.creer_evenement(PORT_ESSAI)
        self.assertTrue(h)
        try:
            self.assertEqual(k32.WaitForSingleObject(h, 0), 0x102)           # WAIT_TIMEOUT
            self.assertTrue(fenetre.signaler_premier_plan(PORT_ESSAI))
            self.assertEqual(k32.WaitForSingleObject(h, 0), 0)               # signalé…
            self.assertEqual(k32.WaitForSingleObject(h, 0), 0x102)           # … une fois (remise à zéro)
        finally:
            k32.CloseHandle(h)

    def test_main_ancienne_version(self):
        """serveur.main() : port pris par une MyPhotos 0.2.0 -> message et sa page ; rien n'est démarré."""
        with mock.patch.object(serveur, 'Serveur', side_effect=OSError('port pris')), \
                mock.patch.object(serveur, 'etat_instance', return_value={'nom': 'MyPhotos', 'version': '0.2.0'}), \
                mock.patch.object(serveur, 'SANS_PAGE', False), \
                mock.patch.object(serveur, 'configurer_journal'), \
                mock.patch.object(serveur, 'ouvrir_page') as page, \
                mock.patch.object(serveur, 'informer') as infos, \
                mock.patch.object(serveur, 'Application') as application:
            self.assertEqual(serveur.main([]), 0)
        page.assert_called_once()
        infos.assert_called_once_with(fenetre.ANCIENNE_VERSION)
        application.assert_not_called()

    def test_main_attend_une_instance_qui_s_arrete(self):
        """Relance juste après la fermeture de la fenêtre : on attend que l'ancienne instance libère le port."""
        essais = []

        def serveur_(*_a, **_k):
            essais.append(1)
            if len(essais) < 3:
                raise OSError('port pris')
            raise RuntimeError('démarré')                                   # assez loin pour l'essai

        with mock.patch.object(serveur, 'Serveur', side_effect=serveur_), \
                mock.patch.object(serveur, 'etat_instance', return_value=dict(self.NOUVELLE, arret=True)), \
                mock.patch.object(serveur, 'configurer_journal'), \
                mock.patch.object(serveur.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'démarré'):
                serveur.main([])
        self.assertEqual(len(essais), 3)


# ---------------------------------------------------------------- navigation

@unittest.skipUnless(os.name == 'nt', 'Windows')
class TestPortTenu(unittest.TestCase):
    """Relecture adverse de la 0.4.0 : la relance faisait confiance à n'importe quel programme qui tenait le port (un
    autre compte Windows recevait notre jeton dans le cookie de /api/etat, puis notre page s'ouvrait chez lui avec
    « ?jeton= »). Désormais, fenetre.port_tenu_par vérifie, AVANT tout envoi, que le programme qui écoute sur
    127.0.0.1:<port> tourne sous NOTRE compte (table des ports de Windows, puis compte du processus)."""

    PORT = int(os.environ.get('MYPHOTOS_ESSAI_PORT') or 7896) + 8

    def ecouter(self, adresse='127.0.0.1'):
        s = socket.socket()
        s.bind((adresse, self.PORT))
        s.listen()
        self.addCleanup(s.close)
        return s

    def test_vrais_ecouteurs(self):
        if not _port_libre(self.PORT):
            self.skipTest(f'port {self.PORT} déjà pris')
        self.assertIsNone(fenetre.port_tenu_par(self.PORT))               # personne
        s = self.ecouter()
        self.assertEqual(fenetre.ecouteurs(self.PORT), [os.getpid()])
        self.assertEqual(fenetre.port_tenu_par(self.PORT), 'nous')
        s.close()
        self.assertIsNone(fenetre.port_tenu_par(self.PORT))
        self.ecouter('0.0.0.0')                                           # toutes les adresses : compte aussi
        self.assertEqual(fenetre.port_tenu_par(self.PORT), 'nous')
        self.assertRegex(fenetre.sid_compte(), r'^S-1-5-')
        self.assertEqual(fenetre.sid_du_processus(os.getpid()), fenetre.sid_compte())
        self.assertEqual(fenetre.sid_du_processus(4), '')                 # le système : Windows refuse, donc « autre »

    def test_autre_compte_ou_illisible(self):
        moi = fenetre.sid_compte()
        with mock.patch.object(fenetre, 'ecouteurs', return_value=[1234]), \
                mock.patch.object(fenetre, 'sid_du_processus', return_value='S-1-5-21-1-2-3-1001'):
            self.assertEqual(fenetre.port_tenu_par(1), 'autre')
        with mock.patch.object(fenetre, 'ecouteurs', return_value=[1234, 5678]), \
                mock.patch.object(fenetre, 'sid_du_processus', side_effect=lambda p=None: moi if p == 1234 else ''):
            self.assertEqual(fenetre.port_tenu_par(1), 'autre')          # un seul écouteur douteux suffit
        with mock.patch.object(fenetre, 'ecouteurs', return_value=None):
            self.assertEqual(fenetre.port_tenu_par(1), 'autre')          # table illisible : rien n'est envoyé
        with mock.patch.object(fenetre, 'ecouteurs', side_effect=OSError('refusé')):
            self.assertEqual(fenetre.port_tenu_par(1), 'autre')
        with mock.patch.object(fenetre, 'ecouteurs', return_value=[1234]), \
                mock.patch.object(fenetre, 'sid_compte', return_value=''):
            self.assertEqual(fenetre.port_tenu_par(1), 'autre')          # notre compte illisible

    def test_relance_face_a_un_faux_serveur(self):
        """Bout en bout, comme la preuve du relecteur : un faux serveur tient le port et se présente comme une
        MyPhotos 0.4.0 en mode navigateur. Le même programme vu comme tournant sous un AUTRE compte (son processus est
        celui de l'essai : son compte est simulé) ne reçoit RIEN, et rien ne s'ouvre ; vu comme le nôtre, la relance
        l'interroge comme avant (témoin : sans la vérification, le jeton partait)."""
        if not _port_libre(self.PORT):
            self.skipTest(f'port {self.PORT} déjà pris')
        import http.server
        recues = []

        class Faux(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                recues.append((self.path, self.headers.get('Cookie')))
                corps = json.dumps({'nom': 'MyPhotos', 'version': '0.4.0', 'mode': 'navigateur',
                                    'acces_par_jeton': True}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)
        faux = http.server.ThreadingHTTPServer(('127.0.0.1', self.PORT), Faux)
        threading.Thread(target=faux.serve_forever, daemon=True).start()
        self.addCleanup(faux.server_close)
        self.addCleanup(faux.shutdown)
        vrai = fenetre.sid_du_processus
        with tempfile.TemporaryDirectory() as d:
            chemin = Path(d) / 'jeton'
            jeton = serveur.creer_jeton(chemin)
            for autre_compte in (True, False):
                recues.clear()

                def sid(pid=None, autre_compte=autre_compte):
                    return 'S-1-5-21-1-2-3-1001' if autre_compte and pid == os.getpid() else vrai(pid)
                with mock.patch.object(fenetre, 'tenir_mutex'), \
                        mock.patch.object(fenetre, 'sid_du_processus', side_effect=sid), \
                        mock.patch.object(config, 'PORT', self.PORT), \
                        mock.patch.object(config, 'JETON', chemin), \
                        mock.patch.object(serveur, 'SANS_PAGE', False), \
                        mock.patch.object(serveur, 'configurer_journal'), \
                        mock.patch.object(serveur, 'ouvrir_page') as page, \
                        mock.patch.object(serveur, 'informer'), \
                        mock.patch.object(serveur, 'alerter') as alerte:
                    code = serveur.main(['--navigateur'])
                if autre_compte:
                    self.assertEqual(code, 1)
                    self.assertEqual(recues, [])                          # ni /api/etat, ni cookie
                    page.assert_not_called()                              # ni page « ?jeton= »
                    alerte.assert_called_once_with(serveur.PORT_PRIS.format(port=self.PORT))
                else:
                    self.assertEqual(code, 0)
                    self.assertEqual(recues, [('/api/etat', f'myphotos-{self.PORT}={jeton}')])
                    page.assert_called_once_with(serveur.adresse_entree(self.PORT, jeton))


class TestNavigation(unittest.TestCase):
    PORT = 7942

    def verdict(self, uri):
        return fenetre.navigation(uri, self.PORT)

    def test_page_permise(self):
        for uri in ('http://127.0.0.1:7942/', 'http://127.0.0.1:7942/index.html', 'http://127.0.0.1:7942/#q=chien',
                    'http://127.0.0.1:7942/?a=1#photo=3'):
            self.assertEqual(self.verdict(uri), 'permise', uri)

    def test_liens_https_vers_le_navigateur(self):
        for uri in ('https://github.com/glenac/MyPhotos', 'https://example.org/licences#mit'):
            self.assertEqual(self.verdict(uri), 'externe', uri)

    def test_bloquees(self):
        for uri in ('http://127.0.0.1:7943/', 'http://localhost:7942/', 'http://127.0.0.1/',
                    'http://127.0.0.1:7942/api/etat', 'http://127.0.0.1:7942/photo/12',
                    'http://127.0.0.1:7942/miniature/3', 'http://user:mdp@127.0.0.1:7942/', 'https://127.0.0.1:7942/', 'http://example.com/',
                    'file:///C:/Windows/win.ini', 'data:text/html,<p>x', 'javascript:alert(1)', 'about:blank',
                    'blob:http://127.0.0.1:7942/abc', 'ms-settings:privacy', 'https://user@example.org/',
                    'https://example.org:8443/', 'https://exa mple.org/', 'https://example.org/\n', 'https://[x/',
                    'edge://settings', '', None):
            self.assertEqual(self.verdict(uri), 'bloquee', uri)


# ---------------------------------------------------------------- fermeture

class TestFermeture(unittest.TestCase):
    def test_message(self):
        self.assertIsNone(fenetre.message_fermeture([]))
        m = fenetre.message_fermeture(['analyse'])
        self.assertTrue(m.startswith('Fermer MyPhotos maintenant ?'))
        self.assertIn('L’analyse reprendra au prochain lancement.', m)
        m = fenetre.message_fermeture(['copie', 'doublons', 'telechargement', 'inconnu'])
        self.assertEqual(m.count('\n'), 4)

    def test_travaux_en_cours(self):
        app = types.SimpleNamespace(_analyse_en_cours=lambda: True, _copie=types.SimpleNamespace(etat='en_cours'),
                                    _calcul_doublons_en_cours=lambda: True,
                                    telechargement=types.SimpleNamespace(etat='en_cours'))
        self.assertEqual(serveur.Application.travaux_en_cours(app), ['analyse', 'copie', 'doublons', 'telechargement'])
        app = types.SimpleNamespace(_analyse_en_cours=lambda: False, _copie=types.SimpleNamespace(etat='fini'),
                                    _calcul_doublons_en_cours=lambda: False, telechargement=None)
        self.assertEqual(serveur.Application.travaux_en_cours(app), [])

    def test_inventaire_seul_sans_question(self):
        """Constat R10 : l'inventaire du début de chaque lancement (refait de toute façon) ne pose pas de question ;
        l'analyse, si."""
        for phase, attendu in (('inventaire', []), ('pause', []), ('termine', []), ('analyse', ['analyse'])):
            app = types.SimpleNamespace(indexeur=types.SimpleNamespace(etat=lambda phase=phase: {'phase': phase}),
                                        _copie=None, _calcul_doublons_en_cours=lambda: False, telechargement=None)
            app._analyse_en_cours = lambda app=app: serveur.Application._analyse_en_cours(app)
            self.assertEqual(serveur.Application.travaux_en_cours(app), attendu, phase)


class TestProcessusEssai(unittest.TestCase):
    """Constat R8 : les essais n'arrêtent que leurs propres processus, par LEUR Job Object ; jamais « /T » de l'outil d'arrêt de Windows,
    jamais un arbre retrouvé par « PID parent » (incident du 24/11 : Discord et des fenêtres PowerShell tués)."""

    def test_aucun_arret_par_identifiant_dans_les_essais(self):
        interdits = ('task' + 'kill', 'arreter_par_' + 'pid', 'def descend' + 'ants')
        for f in (RACINE / 'tests').glob('*.py'):
            texte = f.read_text(encoding='utf-8')
            for mot in interdits:
                self.assertNotIn(mot, texte, f.name)

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_job_arrete_le_processus_et_ses_enfants(self):
        import processus_essai
        code = ('import subprocess, sys, time; '
                'c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]); '
                'print(c.pid, flush=True); time.sleep(60)')
        p = processus_essai.popen(processus_essai.Job(), [sys.executable, '-c', code], stdout=subprocess.PIPE,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            p.stdout.readline()                                  # l'enfant existe
            self.assertGreaterEqual(len(p.job.pids()), 2)        # lanceur, vrai Python, et l'enfant : tous dans le job
            job = p.job
            job.terminer()
            self.assertTrue(job.attendre_vide(10), job.pids())   # tous arrêtés, et seulement eux
            processus_essai.arreter(p)
            self.assertIsNotNone(p.poll())
            self.assertIsNone(job.h)                             # job fermé
        finally:
            if p.poll() is None:
                processus_essai.arreter(p)
            p.stdout.close()


class TestOutilsFenetre(unittest.TestCase):
    """tests/outils_fenetre.lancer (essai réel de la fenêtre) : tampons de CreateProcessW comptés en unités UTF-16.
    Relecture de la 0.3.0 : le bloc d'environnement était dimensionné en caractères (len), si bien qu'un caractère
    hors du plan de base (émoji : DEUX unités UTF-16) faisait échouer le lancement."""

    EMOJI = 'Vacances \U0001F334 été \U0001F600'

    def test_bloc_environnement_en_unites_utf16(self):
        import outils_fenetre as O
        env = {'b': 'é', 'A': self.EMOJI, 'PATH': 'x'}
        attendu = f'A={self.EMOJI}\0b=é\0PATH=x\0\0'                   # trié sans tenir compte de la casse
        unites = len(attendu.encode('utf-16-le')) // 2
        self.assertEqual(unites, len(attendu) + 2)                     # deux émojis : deux unités de plus
        tampon = O.bloc_environnement(env)
        self.assertGreaterEqual(len(tampon), unites + 1)
        self.assertEqual(ctypes.wstring_at(ctypes.addressof(tampon), unites), attendu)
        self.assertEqual(tampon[unites - 2:unites + 1], '\0\0\0')       # fin du bloc : deux NUL (au moins)
        # Bloc vide : deux NUL quand même (Windows lit jusqu'au double NUL).
        vide = O.bloc_environnement({})
        self.assertEqual(vide[:2], '\0\0')
        self.assertEqual(O.unites_utf16(self.EMOJI), len(self.EMOJI) + 2)
        self.assertEqual(O.tampon_utf16(self.EMOJI).value, self.EMOJI)

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_lancer_avec_un_emoji(self):
        """Vrai lancement par outils_fenetre.lancer, sur un bureau invisible, dans un Job Object : la ligne de
        commande et l'environnement arrivent entiers dans le processus lancé (aucune fenêtre)."""
        import outils_fenetre as O
        import processus_essai
        with tempfile.TemporaryDirectory() as d:
            sortie = Path(d) / 'sortie.json'
            code = ('import json, os, sys; open(sys.argv[2], "w", encoding="utf-8").write(json.dumps('
                    '[sys.argv[1], os.environ.get("MYPHOTOS_ESSAI_EMOJI")]))')
            env = dict(os.environ, MYPHOTOS_ESSAI_EMOJI=self.EMOJI)
            nom_bureau = f'MyPhotosEssaiOutils{os.getpid()}'
            hbureau = O.creer_bureau(nom_bureau)
            job = processus_essai.Job()
            hproc = None
            try:
                _, hproc = O.lancer([sys.executable, '-c', code, 'argument ' + self.EMOJI, str(sortie)], nom_bureau,
                                    d, env, job)
                self.assertTrue(O.attendre_processus(hproc, 60))
                self.assertEqual(O.code_sortie(hproc), 0)
                self.assertEqual(json.loads(sortie.read_text(encoding='utf-8')),
                                 ['argument ' + self.EMOJI, self.EMOJI])
            finally:
                job.terminer()
                job.fermer()
                O.fermer_handle(hproc)
                O.fermer_bureau(hbureau)


@unittest.skipUnless(os.name == 'nt', 'Windows')
class TestMutex(unittest.TestCase):
    """Mutex de l'installateur (config.MUTEX) : il existe tant que MyPhotos tourne, et disparaît avec lui. Vrai
    serveur d'essai (MYPHOTOS_SANS_PAGE, port et données d'essai, aucun dossier proposé), dans un Job Object."""

    PORT = int(os.environ.get('MYPHOTOS_ESSAI_PORT_MUTEX') or 7944)

    @staticmethod
    def mutex_existe():
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
        k32.OpenMutexW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenMutexW(0x00100000, False, config.MUTEX)                # SYNCHRONIZE
        if h:
            k32.CloseHandle(h)
        return bool(h)

    def test_mutex_pendant_la_vie_de_myphotos(self):
        import outils_fenetre as O
        import processus_essai
        self.assertEqual(config.MUTEX, 'MyPhotos-25DE0724-F7B4-4956-8238-1E33E16ABFED')
        if self.mutex_existe():
            self.skipTest('une MyPhotos 0.3 (ou plus) tourne déjà sur cet ordinateur')
        if not _port_libre(self.PORT):
            self.skipTest(f'port {self.PORT} déjà pris')
        base = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
        donnees = base / f'essai-mutex-{time.strftime("%Y%m%d-%H%M%S")}'
        donnees.mkdir(parents=True)
        O.donnees_du_port(self.PORT, donnees)            # jeton d'accès lu dans ses données
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYPHOTOS_', 'WEBVIEW2_'))}
        env.update(MYPHOTOS_PORT=str(self.PORT), MYPHOTOS_DOSSIER=str(donnees), MYPHOTOS_SANS_PAGE='1',
                   MYPHOTOS_MODELES=str(Path(os.environ.get('MYPHOTOS_MODELES') or RACINE / 'donnees' / 'modele')),
                   PYTHONIOENCODING='utf-8')
        code = ('import sys; import myphotos.serveur as s; s.dossiers_proposes = lambda: []; '
                's.dossier_images = lambda: None; sys.exit(s.main())')
        p = processus_essai.popen(processus_essai.Job(), [sys.executable, '-c', code], cwd=str(RACINE), env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.assertTrue(O.attendre(lambda: O.requete_json(self.PORT, '/api/etat')[0] == 200, 60), 'serveur absent')
            self.assertTrue(self.mutex_existe())
            etat = O.requete_json(self.PORT, '/api/etat')[1]
            self.assertEqual(etat['compte'], fenetre.identite_compte())     # même compte que l'essai
            self.assertEqual(O.requete_json(self.PORT, '/api/quitter', {})[0], 200)
            self.assertEqual(p.wait(60), 0)
            self.assertTrue(p.job.attendre_vide(10))
            self.assertFalse(self.mutex_existe())
        finally:
            processus_essai.arreter(p)


# ---------------------------------------------------------------- raccourcis

class TestRaccourci(unittest.TestCase):
    def test_emplacements(self):
        e = raccourci.emplacements(bureau='B', programmes='P')
        self.assertEqual(e, {'bureau': Path('B') / 'MyPhotos.lnk', 'menu': Path('P') / 'MyPhotos.lnk'})

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_dossiers_de_windows(self):
        e = raccourci.emplacements()
        self.assertEqual(set(e), {'bureau', 'menu'})
        self.assertTrue(str(e['menu']).lower().endswith(os.path.join('start menu', 'programs', 'myphotos.lnk')))

    def test_refus(self):
        with mock.patch.object(raccourci, 'possible', return_value=False):
            with self.assertRaisesRegex(raccourci.RaccourciRefuse, 'MyPhotos.exe'):
                raccourci.verifier_cible(sys.executable)
        with mock.patch.object(raccourci, 'possible', return_value=True):
            with tempfile.TemporaryDirectory() as d:
                exe = Path(d) / 'MyPhotos.exe'
                exe.write_bytes(b'MZ')
                with self.assertRaisesRegex(raccourci.RaccourciRefuse, 'dossier temporaire'):
                    raccourci.verifier_cible(str(exe))
            with self.assertRaisesRegex(raccourci.RaccourciRefuse, 'introuvable'):
                raccourci.verifier_cible(str(RACINE / 'absent.exe'))
        self.assertFalse(raccourci.possible())                    # depuis les sources : jamais de raccourci

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_lien_ecrit_puis_relu(self):
        """Vrai raccourci .lnk (IShellLink), écrit dans un dossier temporaire seulement."""
        with tempfile.TemporaryDirectory() as d:
            lien = Path(d) / 'Sous dossier' / 'MyPhotos.lnk'
            with mock.patch.object(raccourci, 'possible', return_value=True):
                crees = raccourci.creer_raccourcis(sys.executable, {'bureau': lien, 'menu': Path(d) / 'M.lnk'})
            self.assertEqual(crees, ['bureau', 'menu'])
            cible, dossier, (icone, indice) = raccourci.lire_lien(lien)
            exe = os.path.abspath(sys.executable)
            self.assertEqual(os.path.normcase(cible), os.path.normcase(exe))
            self.assertEqual(os.path.normcase(dossier), os.path.normcase(os.path.dirname(exe)))
            self.assertEqual((os.path.normcase(icone), indice), (os.path.normcase(cible), 0))
            # Un raccourci du même nom est remplacé.
            raccourci.creer_lien(lien, str(RACINE / 'lancer.py'), str(RACINE), sys.executable)
            self.assertEqual(os.path.normcase(raccourci.lire_lien(lien)[0]),
                             os.path.normcase(str(RACINE / 'lancer.py')))

    def test_api(self):
        app = types.SimpleNamespace()
        with mock.patch.object(raccourci, 'creer_raccourcis', side_effect=raccourci.RaccourciRefuse('non')):
            with self.assertRaises(serveur.Refus) as e:
                serveur.Application.creer_raccourcis(app)
            self.assertEqual((str(e.exception), e.exception.statut), ('non', 409))
        with mock.patch.object(raccourci, 'creer_raccourcis', return_value=['bureau', 'menu']):
            self.assertEqual(serveur.Application.creer_raccourcis(app), {'crees': ['bureau', 'menu']})


class TestPage(unittest.TestCase):
    def test_textes_de_la_fenetre(self):
        page = (RACINE / 'page' / 'index.html').read_text(encoding='utf-8')
        for attendu in ('id="r-fermeture"', 'Fermer la fenêtre arrête MyPhotos.', 'id="r-raccourci"',
                        'Créer un raccourci sur le Bureau et dans le menu Démarrer', "'/api/raccourci'",
                        "E.serveur.mode === 'fenetre'"):
            self.assertIn(attendu, page)

    def test_aucune_navigation_sortante(self):
        """Relecture adverse de la 0.4.0 : le cookie du jeton part vers tous les ports de 127.0.0.1. En mode Edge
        (profil à part), aucune navigation ne doit quitter la page : aucun lien vers une autre adresse, aucun
        window.open, location.assign/replace ni formulaire envoyé ; les liens des miniatures restent dans la page
        (« # ») ; « Nouveautés » et « Télécharger » passent par le serveur ; un dépôt (lien glissé) est refusé en mode
        Edge comme dans la fenêtre."""
        page = (RACINE / 'page' / 'index.html').read_text(encoding='utf-8')
        import re
        for motif in (r'href\s*=\s*["\'](?!#|data:image/)', r'window\.open\s*\(', r'location\.(assign|replace)\s*\(',
                      r'location\.href\s*=', r'location\s*=[^=]', r'target\s*=\s*["\']_', r'<form[^>]*\baction\s*=',
                      r'\.submit\s*\(', r'https?://'):
            trouves = [m.group(0) for m in re.finditer(motif, page)]
            self.assertEqual(trouves, [], motif)
        for lien in re.findall(r"href: *([^,}]+)", page):
            self.assertTrue(lien.startswith(('adresse(', '`#')), lien)        # miniatures : un fragment de la page
        self.assertIn("E.serveur.mode === 'fenetre' || E.serveur.mode === 'edge'", page)   # enFenetre : dépôts refusés
        self.assertIn("ev.preventDefault();\n      if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'none';", page)

    def test_recherche_ne_garde_pas_les_anciennes_cases(self):
        """Constat R4 : avant de vider les grilles, chaque recherche désabonne leurs cases de l'observateur des
        miniatures (sinon ~31 nœuds et 5 écouteurs restent en mémoire à chaque recherche, toute la séance ; mesuré
        par CDP : 1279 -> 2511 nœuds en 40 gestes, 713 -> 741 après correction)."""
        page = (RACINE / 'page' / 'index.html').read_text(encoding='utf-8')
        debut = page.index('function chercher(')
        fin = page.index('\n}\n', debut)
        corps = page[debut:fin]
        desabonne = corps.find("#resultats a.tuile, #grille-peut a.tuile, #grille-proches a.tuile")
        self.assertGreater(desabonne, 0)
        self.assertIn('observateurImages.unobserve(a)', corps[desabonne:])
        self.assertLess(desabonne, corps.index("$('#resultats').replaceChildren()"))


# ---------------------------------------------------------------- essai réel (à la demande)

LANCEUR = '''\
"""Lanceur d'essai de MyPhotos : navigateur, presse-papiers, Corbeille et Explorateur simulés (trace JSON)."""
import json, os, sys, webbrowser
sys.path.insert(0, os.environ['MYPHOTOS_ESSAI_RACINE'])
trace = open(os.environ['MYPHOTOS_ESSAI_TRACE'], 'a', encoding='utf-8')


def note(quoi, *args):
    trace.write(json.dumps({'quoi': quoi, 'args': [str(a)[:300] for a in args]}) + '\\n')
    trace.flush()
    return True


def corbeille(*_a, **_k):
    raise OSError('Corbeille simulée (essai)')


import myphotos.serveur as s
s.dossiers_proposes = lambda: []
s.dossier_images = lambda: os.environ.get('MYPHOTOS_ESSAI_VIDE')      # dossier vide : jamais les vraies Images
s.mettre_au_presse_papiers = lambda *a, **k: note('presse_papiers')
s.mettre_a_la_corbeille = corbeille
s.montrer_dans_dossier = lambda *a, **k: note('explorateur', *a)
os.startfile = lambda *a, **k: note('startfile', *a)
webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = lambda *a, **k: note('navigateur', *a)
if os.environ.get('MYPHOTOS_ESSAI_CHOIX'):
    # « Choisir un dossier » simulé : rend le chemin écrit dans ce fichier (rien s'il est absent ou vide).
    import myphotos.fenetre as f

    class FauxChoix:
        def __init__(self, *a, **k):
            pass

        def demander(self, initial=None, delai=None, titre=''):
            note('choix', initial, titre)
            p = os.environ['MYPHOTOS_ESSAI_CHOIX']
            texte = open(p, encoding='utf-8').read().strip() if os.path.exists(p) else ''
            return texte or None
    f.ChoixDossierFenetre = FauxChoix
    s.ChoixDossier = FauxChoix
sys.exit(s.main(sys.argv[1:]))
'''


def _port_libre(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


ENFANT_CHOIX = '''\
"""Essai réel de « Choisir un dossier » des replis : dialogue.ouvrir_devant, résultat en JSON. Avec un 3e argument
(délai en secondes) : deux demandes de serveur.ChoixDossier, laissées sans réponse au-delà de ce délai."""
import json, os, sys, time
sys.path.insert(0, os.environ['MYPHOTOS_ESSAI_RACINE'])
from myphotos import dialogue, serveur
try:
    if len(sys.argv) > 3:
        choix = serveur.ChoixDossier()
        sortie = {'demandes': []}
        for _ in range(2):
            debut = time.monotonic()
            r = choix.demander(initial=sys.argv[2], delai=float(sys.argv[3]), titre=sys.argv[1])
            sortie['demandes'].append({'chemin': r, 'duree_s': round(time.monotonic() - debut, 1)})
    else:
        sortie = {'chemin': dialogue.ouvrir_devant(sys.argv[1], sys.argv[2])}
except Exception as e:
    sortie = {'erreur': repr(e)}
with open(os.environ['MYPHOTOS_ESSAI_SORTIE'], 'w', encoding='utf-8') as f:
    json.dump(sortie, f)
'''


@unittest.skipUnless(os.environ.get('MYPHOTOS_ESSAI_FENETRE') == '1' and os.name == 'nt',
                     'essai réel de la fenêtre : MYPHOTOS_ESSAI_FENETRE=1 (Windows)')
class TestChoixDossierReel(unittest.TestCase):
    """0.4.0 : la vraie fenêtre « Choisir un dossier » des replis (IFileOpenDialog par COM, dialogue.py), sur un
    bureau Windows INVISIBLE, dans un Job Object : elle appartient à la fenêtre hôte de MyPhotos (invisible,
    toujours au-dessus), qu'elle bloque ; elle est elle-même au-dessus, au milieu de l'écran ; « Annuler » (la croix)
    rend None, « Sélectionner un dossier » rend le dossier proposé, au nom accentué avec un émoji. Rien ne reste."""

    TITRE = 'Choisissez un dossier de photos'

    def setUp(self):
        import outils_fenetre as O
        self.O = O
        u32 = O.u32
        u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)]
        u32.GetDlgItem.restype = ctypes.c_void_p
        u32.GetDlgItem.argtypes = [ctypes.c_void_p, ctypes.c_int]
        base = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
        self.dossier = base / f'essai-choix-{time.strftime("%Y%m%d-%H%M%S")}'
        self.propose = self.dossier / 'Été \U0001F334'
        self.propose.mkdir(parents=True)
        (self.dossier / 'enfant.py').write_text(ENFANT_CHOIX, encoding='utf-8')

    def rect(self, hwnd):
        r = ctypes.wintypes.RECT()
        self.O.u32.GetWindowRect(hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def ouvrir(self, action):
        """Lance l'enfant, trouve la fenêtre de Windows, fait `action` (WM_CLOSE ou IDOK) ; rend (sortie, constats)."""
        O = self.O
        import processus_essai
        from myphotos import dialogue
        sortie = self.dossier / f'sortie-{action}.json'
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYPHOTOS_', 'WEBVIEW2_'))}
        env.update(MYPHOTOS_ESSAI_RACINE=str(RACINE), MYPHOTOS_ESSAI_SORTIE=str(sortie))
        nom_bureau = f'MyPhotosChoix{os.getpid()}'
        hbureau = O.creer_bureau(nom_bureau)
        fil = O.FilBureau(hbureau)
        job = processus_essai.Job()
        hproc = None
        constats = {}
        try:
            _pid, hproc = O.lancer([sys.executable, str(self.dossier / 'enfant.py'), self.TITRE, str(self.propose)],
                                   nom_bureau, str(self.dossier), env, job)
            d = O.attendre(lambda: next((f for f in fil.faire(lambda: O.fenetres(set(job.pids())))
                                         if f['classe'] == '#32770' and f['visible']), None), 30)
            self.assertTrue(d, 'fenêtre « Choisir un dossier » absente')
            tout = fil.faire(lambda: O.fenetres(set(job.pids())))
            hote = next((f for f in tout if f['hwnd'] == d['proprietaire']), None)
            constats.update(
                titre=d['titre'], hote=hote and (hote['classe'], hote['titre'], hote['visible'], hote['pid']),
                pid=d['pid'],
                dessus=bool(fil.faire(lambda: self.O.u32.GetWindowLongPtrW(d['hwnd'], -20)) & 0x8),
                hote_style=fil.faire(lambda: self.O.u32.GetWindowLongPtrW(d['proprietaire'], -20)),
                hote_actif=bool(fil.faire(lambda: O.u32.IsWindowEnabled(d['proprietaire']))),
                rect=fil.faire(lambda: self.rect(d['hwnd'])))
            centre = ((constats['rect'][0] + constats['rect'][2]) // 2, (constats['rect'][1] + constats['rect'][3]) // 2)
            travail = fil.faire(lambda: dialogue.zone_de_travail(*centre))
            constats['travail'] = travail
            time.sleep(0.5)
            if action == 'annuler':
                fil.faire(lambda: O.u32.PostMessageW(d['hwnd'], O.WM_CLOSE, 0, 0))
            else:
                bouton = fil.faire(lambda: O.u32.GetDlgItem(d['hwnd'], 1))
                fil.faire(lambda: O.u32.PostMessageW(d['hwnd'], 0x0111, 1, bouton))     # WM_COMMAND, IDOK
            self.assertTrue(O.attendre_processus(hproc, 30), 'la fenêtre ne se ferme pas')
            constats['code'] = O.code_sortie(hproc)
            self.assertTrue(job.attendre_vide(10), O.du_job(job))
            return json.loads(sortie.read_text(encoding='utf-8')), constats
        finally:
            if hproc is not None and O.code_sortie(hproc) is None:
                job.terminer()                        # filet : les processus de CE job seulement
            O.fermer_handle(hproc)
            job.fermer()
            O.fermer_bureau(hbureau)

    def verifier_constats(self, c):
        self.assertEqual(c['titre'], self.TITRE)
        self.assertEqual(c['hote'], ('Static', 'MyPhotos', True, c['pid']))   # hôte de MyPhotos, même processus
        self.assertTrue(c['hote_style'] & 0x80000 and c['hote_style'] & 0x8)  # transparente, toujours au-dessus
        self.assertTrue(c['dessus'])                                         # la fenêtre de Windows aussi
        self.assertFalse(c['hote_actif'])                                    # modale : l'hôte attend
        g, h, d, b = c['travail']
        x1, y1, x2, y2 = c['rect']
        self.assertTrue(g <= x1 < x2 <= d and h <= y1 < y2 <= b, c)           # entière sur l'écran…
        self.assertLess(abs((x1 + x2) // 2 - (g + d) // 2), (d - g) // 8, c)   # … et au milieu
        self.assertLess(abs((y1 + y2) // 2 - (h + b) // 2), (b - h) // 8, c)
        self.assertEqual(c['code'], 0)

    def test_delai_depasse_la_fenetre_se_ferme(self):
        """Relecture adverse (déjà en 0.3.0) : au bout du délai, la vraie fenêtre est fermée comme par « Annuler »
        (avant, elle restait ouverte et le choix fait ensuite était perdu) ; la demande suivante s'ouvre aussitôt,
        sans « déjà ouverte ». Rien n'est touché par l'essai : elle se ferme d'elle-même."""
        O = self.O
        import processus_essai
        sortie = self.dossier / 'sortie-delai.json'
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYPHOTOS_', 'WEBVIEW2_'))}
        env.update(MYPHOTOS_ESSAI_RACINE=str(RACINE), MYPHOTOS_ESSAI_SORTIE=str(sortie))
        nom_bureau = f'MyPhotosChoixDelai{os.getpid()}'
        hbureau = O.creer_bureau(nom_bureau)
        fil = O.FilBureau(hbureau)
        job = processus_essai.Job()
        hproc = None
        try:
            _pid, hproc = O.lancer([sys.executable, str(self.dossier / 'enfant.py'), self.TITRE, str(self.propose),
                                    '3'], nom_bureau, str(self.dossier), env, job)
            vues = set()

            def boite():
                return next((f['hwnd'] for f in fil.faire(lambda: O.fenetres(set(job.pids())))
                             if f['classe'] == '#32770' and f['visible']), None)
            fin = time.monotonic() + 60
            while O.code_sortie(hproc) is None and time.monotonic() < fin:
                h = boite()
                if h:
                    vues.add(h)
                time.sleep(0.2)
            self.assertTrue(O.attendre_processus(hproc, 5), 'les fenêtres ne se ferment pas d\'elles-mêmes')
            self.assertEqual(O.code_sortie(hproc), 0)
            self.assertEqual(len(vues), 2, vues)                                # deux fenêtres, l'une après l'autre
            d = json.loads(sortie.read_text(encoding='utf-8'))
            self.assertEqual([x['chemin'] for x in d['demandes']], [None, None], d)
            for x in d['demandes']:
                self.assertTrue(3 <= x['duree_s'] < 3 + 6, d)                   # délai, puis fermeture (5 s au plus)
            self.assertTrue(job.attendre_vide(10), O.du_job(job))
        finally:
            if hproc is not None and O.code_sortie(hproc) is None:
                job.terminer()
            O.fermer_handle(hproc)
            job.fermer()
            O.fermer_bureau(hbureau)

    def test_annuler_puis_choisir(self):
        sortie, constats = self.ouvrir('annuler')
        self.assertEqual(sortie, {'chemin': None})
        self.verifier_constats(constats)
        sortie, constats = self.ouvrir('choisir')
        self.assertEqual(sortie, {'chemin': str(self.propose)})              # le dossier proposé, nom intact
        self.verifier_constats(constats)


@unittest.skipUnless(os.environ.get('MYPHOTOS_ESSAI_FENETRE') == '1' and os.name == 'nt',
                     'essai réel de la fenêtre : MYPHOTOS_ESSAI_FENETRE=1 (Windows)')
class TestFenetreReelle(unittest.TestCase):
    PORT = int(os.environ.get('MYPHOTOS_ESSAI_PORT_FENETRE') or 7938)
    PORT_CDP = PORT + 1500
    ORIGINE = f'http://127.0.0.1:{PORT}'

    @classmethod
    def setUpClass(cls):
        import outils_fenetre as O
        cls.O = O
        photos = os.environ.get('MYPHOTOS_ESSAI_PHOTOS')
        modeles = Path(os.environ.get('MYPHOTOS_MODELES') or RACINE / 'donnees' / 'modele')
        if not photos or not Path(photos).is_dir():
            raise unittest.SkipTest('MYPHOTOS_ESSAI_PHOTOS : dossier d\'images publiques nécessaire')
        if not fenetre.version_webview2():
            raise unittest.SkipTest('WebView2 absent')
        for p in (cls.PORT, cls.PORT_CDP):
            if not _port_libre(p):
                raise unittest.SkipTest(f'port {p} déjà pris')
        base = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
        cls.dossier = base / f'essai-fenetre-{time.strftime("%Y%m%d-%H%M%S")}'
        cls.donnees = cls.dossier / 'donnees'
        cls.donnees.mkdir(parents=True)
        O.donnees_du_port(cls.PORT, cls.donnees)          # jeton d'accès lu dans ses données
        cls.trace = cls.dossier / 'trace.jsonl'
        cls.lanceur = cls.dossier / 'lanceur.py'
        cls.lanceur.write_text(LANCEUR, encoding='utf-8')
        (cls.dossier / 'vide').mkdir()
        cls.env = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(('MYPHOTOS_', 'WEBVIEW2_', 'PYWEBVIEW_'))}
        cls.env.update({'MYPHOTOS_PORT': str(cls.PORT), 'MYPHOTOS_DOSSIER': str(cls.donnees),
                        'MYPHOTOS_MODELES': str(modeles), 'MYPHOTOS_ESSAI_RACINE': str(RACINE),
                        'MYPHOTOS_ESSAI_TRACE': str(cls.trace), 'MYPHOTOS_ESSAI_VIDE': str(cls.dossier / 'vide'),
                        'PYTHONIOENCODING': 'utf-8',
                        'PYTHONDONTWRITEBYTECODE': '1'})
        cls.preparer(photos)

    @classmethod
    def preparer(cls, photos):
        """Données d'essai : moteur « base », photos publiques analysées, assistant terminé (sans fenêtre)."""
        O = cls.O
        import processus_essai
        env = dict(cls.env, MYPHOTOS_SANS_PAGE='1')
        err = open(cls.dossier / 'preparation.err', 'ab')
        p = processus_essai.popen(processus_essai.Job(), [sys.executable, '-X', 'faulthandler', str(cls.lanceur)],
                                  cwd=str(RACINE), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=err, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            assert O.attendre(lambda: O.requete_json(cls.PORT, '/api/etat')[0] == 200, 60), 'serveur absent'
            O.requete_json(cls.PORT, '/api/moteur/choisir', {'cle': 'base', 'vision': 'fp16'})
            assert O.attendre(lambda: O.requete_json(cls.PORT, '/api/etat')[1].get('recherche_prete'), 180), \
                'moteur pas prêt'
            O.requete_json(cls.PORT, '/api/dossiers/ajouter', {'chemin': str(photos)})

            def analyse_finie():
                e = O.requete_json(cls.PORT, '/api/etat')[1]
                a = e.get('analyse') or {}
                return a.get('phase') == 'termine' and e['photos']['analysees'] >= 10
            assert O.attendre(analyse_finie, 600, 1.0), 'analyse inachevée'
            O.requete_json(cls.PORT, '/api/assistant', {'etape': 'fini'})
            O.requete_json(cls.PORT, '/api/quitter', {})
            p.wait(90)
        finally:
            processus_essai.arreter(p)               # filet : par SON job seulement
            err.close()

    def lignes_trace(self):
        if not self.trace.exists():
            return []
        return [json.loads(x) for x in self.trace.read_text(encoding='utf-8').splitlines() if x.strip()]

    def journal(self):
        p = self.donnees / 'journal.log'
        return p.read_text(encoding='utf-8', errors='replace') if p.exists() else ''

    def test_fenetre_de_bout_en_bout(self):
        O = self.O
        # Téléchargement du moteur simulé (60 s, sans réseau) : un travail en cours pour la question à la fermeture.
        env = dict(self.env, MYPHOTOS_DEBUG='1', MYPHOTOS_TELECHARGEMENT_FACTICE='1', MYPHOTOS_FACTICE_S='60',
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f'--remote-debugging-port={self.PORT_CDP}')
        ligne = [sys.executable, '-X', 'faulthandler', str(self.lanceur)]
        import processus_essai
        nom_bureau = f'MyPhotosEssai{os.getpid()}'
        hbureau = O.creer_bureau(nom_bureau)
        fil = O.FilBureau(hbureau)
        job = processus_essai.Job()                 # tous les processus de l'essai, et seulement eux
        pid, hproc = O.lancer(ligne, nom_bureau, str(RACINE), env, job)
        ws = None
        try:
            # 1. La fenêtre s'ouvre et la page s'affiche, avec les photos.
            page = O.attendre_page(self.PORT_CDP, self.ORIGINE + '/', 120)
            ws = O.CDP(page['webSocketDebuggerUrl'])
            ws.appel('Runtime.enable')
            ws.appel('Emulation.setFocusEmulationEnabled', enabled=True)
            ecran = O.attendre(lambda: ws.evaluer(
                "!document.querySelector('#appli').hidden && document.querySelectorAll('#resultats a.tuile').length"),
                60)
            self.assertTrue(ecran, 'écran des photos absent')
            etat = O.requete_json(self.PORT, '/api/etat')[1]
            self.assertEqual((etat['mode'], etat['raccourci']), ('fenetre', False))
            self.assertEqual(ws.evaluer('typeof window.pywebview'), 'undefined')      # aucun pont vers Python
            # python.exe d'un environnement virtuel lance le vrai Python : la fenêtre est à un processus du job.
            arbre = set(job.pids())
            principales = fil.faire(lambda: [f for f in O.fenetres(arbre) if f['titre'] == 'MyPhotos'
                                             and f['classe'].startswith('WindowsForms') and f['visible']])
            self.assertEqual(len(principales), 1, principales)
            hwnd = principales[0]['hwnd']
            self.assertEqual(fil.faire(lambda: O.conscience_dpi(hwnd)), 2)           # DPI « par écran »
            ws.capture(str(self.dossier / 'fenetre.png'))

            # 2. Une recherche, Ctrl+A et Échap.
            ws.evaluer("document.querySelector('#q').focus()")
            ws.appel('Input.insertText', text='chien')
            ws.touche('Enter', 'Enter', 13, texte='\r')
            res = O.attendre(lambda: (lambda r: r if r['q'] == 'chien' and r['n'] else None)(ws.evaluer(
                "({q: document.querySelector('#q').value, "
                "n: document.querySelectorAll('#resultats a.tuile').length})")), 30)
            self.assertTrue(res, 'aucun résultat pour « chien »')
            ws.evaluer('document.activeElement && document.activeElement.blur()')
            selection = ("({compte: document.querySelector('#sel-compte').textContent, "
                         "actif: document.querySelector('#appli').classList.contains('en-selection')})")
            ws.touche('a', 'KeyA', 65, modificateurs=2)
            avec = ws.evaluer(selection)
            self.assertTrue(avec['actif'] and avec['compte'], avec)
            ws.touche('Escape', 'Escape', 27)
            self.assertFalse(ws.evaluer(selection)['actif'])

            # 3. Un lien https part vers le navigateur par défaut (simulé) ; la fenêtre reste sur MyPhotos.
            ws.evaluer("(() => { const a = document.createElement('a'); a.href = 'https://example.org/myphotos-essai';"
                       " document.body.append(a); a.click(); a.remove(); return 1; })()", geste=True)
            ws.evaluer("window.open('https://example.org/nouvelle-fenetre'); 1", geste=True)
            ouverts = O.attendre(lambda: (lambda t: t if len(t) >= 2 else None)(
                [x['args'][0] for x in self.lignes_trace() if x['quoi'] == 'startfile']), 10)
            self.assertEqual(sorted(ouverts or []), ['https://example.org/myphotos-essai',
                                                     'https://example.org/nouvelle-fenetre'])
            try:
                ws.evaluer("location.href = 'file:///C:/Windows/win.ini'; 1", geste=True)
            except RuntimeError:
                pass
            time.sleep(1.0)
            self.assertTrue(ws.evaluer('location.href').startswith(self.ORIGINE + '/'))
            self.assertEqual(len([x for x in self.lignes_trace() if x['quoi'] == 'startfile']), 2)

            # 3 bis. « Choisir un dossier » : la fenêtre de Windows appartient à la fenêtre MyPhotos et la bloque.
            reponse = {}

            def choisir():
                try:
                    reponse['r'] = O.requete_json(self.PORT, '/api/dossiers/choisir', {}, delai=60)
                except Exception as e:
                    reponse['r'] = repr(e)
            fil_choix = threading.Thread(target=choisir)
            fil_choix.start()
            dialogue = O.attendre(lambda: next((f for f in fil.faire(lambda: O.fenetres(arbre))
                                                if f['classe'] == '#32770' and f['visible']), None), 15)
            self.assertTrue(dialogue, 'fenêtre « Choisir un dossier » absente')
            self.assertEqual((dialogue['titre'], dialogue['proprietaire']), ('Choisissez un dossier de photos', hwnd))
            self.assertFalse(fil.faire(lambda: O.u32.IsWindowEnabled(hwnd)))     # modale : MyPhotos attend
            fil.faire(lambda: O.u32.PostMessageW(dialogue['hwnd'], O.WM_CLOSE, 0, 0))
            fil_choix.join(30)
            self.assertEqual(reponse.get('r'), (200, {'annule': True}))
            self.assertTrue(fil.faire(lambda: O.u32.IsWindowEnabled(hwnd)))

            # 4. Relance : la fenêtre réduite revient, sans seconde fenêtre.
            fil.faire(lambda: O.u32.ShowWindow(hwnd, O.SW_MINIMIZE))
            self.assertTrue(O.attendre(lambda: fil.faire(lambda: O.u32.IsIconic(hwnd)), 5))
            job2 = processus_essai.Job()
            _pid2, h2 = O.lancer(ligne, nom_bureau, str(RACINE), env, job2)
            try:
                self.assertTrue(O.attendre_processus(h2, 60), 'la seconde instance ne s\'arrête pas')
                self.assertEqual(O.code_sortie(h2), 0)
            finally:
                if O.code_sortie(h2) is None:
                    job2.terminer()
                O.fermer_handle(h2)
                job2.fermer()
            self.assertTrue(O.attendre(lambda: not fil.faire(lambda: O.u32.IsIconic(hwnd)), 10),
                            'la fenêtre réduite n\'est pas revenue')
            self.assertIn('fenêtre ramenée au premier plan', self.journal())
            self.assertEqual(len(fil.faire(lambda: [f for f in O.fenetres(arbre) if f['titre'] == 'MyPhotos'
                                                    and f['visible']])), 1)

            # 5. Fermeture pendant un travail (téléchargement simulé du moteur) : une question ; « Non » garde tout.
            self.assertTrue(O.attendre(lambda: not (O.requete_json(self.PORT, '/api/etat')[1].get('analyse') or {})
                                       .get('active'), 60), 'analyse toujours en cours')
            statut, t = O.requete_json(self.PORT, '/api/moteur/telecharger', {'cle': 'rapide', 'activer': False})
            self.assertEqual((statut, t['etat']), (200, 'en_cours'))

            def question():
                return next((f for f in fil.faire(lambda: O.fenetres(arbre)) if f['classe'] == '#32770'
                             and f['visible'] and f['proprietaire'] == hwnd), None)
            fil.faire(lambda: O.u32.PostMessageW(hwnd, 0x0112, 0xF060, 0))       # la croix : WM_SYSCOMMAND, SC_CLOSE
            q = O.attendre(question, 10)
            self.assertTrue(q, 'aucune question à la fermeture')
            self.assertEqual(q['titre'], 'MyPhotos')
            self.assertIn(fenetre.PHRASES_FERMETURE['telechargement'], fil.faire(lambda: O.textes(q['hwnd'])))
            fil.faire(lambda: O.u32.PostMessageW(q['hwnd'], 0x0111, 7, 0))         # WM_COMMAND, IDNO
            time.sleep(1.0)
            self.assertIsNone(question())
            self.assertIsNone(O.code_sortie(hproc))
            self.assertTrue(fil.faire(lambda: O.u32.IsWindowVisible(hwnd)))
            self.assertEqual(O.requete_json(self.PORT, '/api/etat')[1]['telechargement']['etat'], 'en_cours')

            # 6. « Oui » : tout s'arrête, sans processus restant (fermeture venue d'un autre programme : WM_CLOSE).
            ws.fermer()
            ws = None
            enfants = O.du_job(job)
            self.assertTrue(any(n.lower() == 'msedgewebview2.exe' for _, n in enfants), enfants)
            fil.faire(lambda: O.u32.PostMessageW(hwnd, O.WM_CLOSE, 0, 0))
            q = O.attendre(question, 10)
            self.assertTrue(q, 'aucune question à la seconde fermeture')
            fil.faire(lambda: O.u32.PostMessageW(q['hwnd'], 0x0111, 6, 0))         # WM_COMMAND, IDYES
            self.assertTrue(O.attendre_processus(hproc, 60), 'MyPhotos ne s\'arrête pas')
            self.assertEqual(O.code_sortie(hproc), 0)
            self.assertTrue(job.attendre_vide(10), O.du_job(job))              # aucun processus restant
            self.assertTrue(_port_libre(self.PORT))
            self.assertFalse((self.donnees / 'webview2').exists())            # mode privé : rien ne reste
            self.assertTrue(fenetre.lire_placement(self.donnees))              # position retenue
        finally:
            if ws is not None:
                ws.fermer()
            if O.code_sortie(hproc) is None:
                job.terminer()                       # filet : les processus de CE job seulement
            O.fermer_handle(hproc)
            job.fermer()
            O.fermer_bureau(hbureau)

    def test_sous_dossiers_actualiser_et_photo_introuvable(self):
        """0.3.0, dans la vraie fenêtre (bureau invisible, piloté par CDP), sur un dossier d'essai à part (copies
        d'images publiques, captures d'écran générées) : l'assistant décoche « Captures d\u2019écran » d'office ; « Actualiser »
        après la suppression de fichiers d'essai dit combien de photos sont retirées ; exclusion d'un sous-dossier puis
        « Ne plus exclure » ; refus d'un dossier hors du dossier suivi ; une photo dont le fichier a disparu est retirée à
        l'ouverture. « Choisir un dossier » est simulé (fichier choix.txt)."""
        O = self.O
        import shutil
        from PIL import Image, ImageDraw
        import processus_essai
        photos = Path(os.environ['MYPHOTOS_ESSAI_PHOTOS'])
        mes = self.dossier / 'mes-images'
        captures = mes / 'Captures d\u2019écran'
        famille = mes / 'Famille'
        vacances = mes / 'Vacances'
        for d, noms in ((mes, ('chat-1.jpg', 'fleur-2.jpg')), (vacances, ('plage-1.jpg', 'plage-3.jpg', 'neige-2.jpg')),
                        (famille, ('gateau-1.jpg', 'chien-2.jpg'))):
            d.mkdir(parents=True, exist_ok=True)
            for n in noms:
                shutil.copyfile(photos / n, d / n)
        captures.mkdir()
        for i in range(3):                                    # « captures d'écran » générées : fenêtres et texte
            im = Image.new('RGB', (1280, 720), (240, 240, 245))
            dessin = ImageDraw.Draw(im)
            dessin.rectangle((0, 0, 1280, 40), fill=(30, 60, 150 + 30 * i))
            for y in range(80, 680, 36):
                dessin.rectangle((60, y, 60 + 200 + (y * 7 + i * 90) % 900, y + 14), fill=(90, 90, 100))
            im.save(captures / f'Capture d’écran {i + 1}.png')
        choix = self.dossier / 'choix.txt'
        env = dict(self.env, MYPHOTOS_DEBUG='1', MYPHOTOS_ESSAI_CHOIX=str(choix),
                   WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=f'--remote-debugging-port={self.PORT_CDP}')
        ligne = [sys.executable, '-X', 'faulthandler', str(self.lanceur)]
        nom_bureau = f'MyPhotosDossiers{os.getpid()}'
        hbureau = O.creer_bureau(nom_bureau)
        fil = O.FilBureau(hbureau)
        job = processus_essai.Job()
        _pid, hproc = O.lancer(ligne, nom_bureau, str(RACINE), env, job)
        ws = None

        def etat():
            return O.requete_json(self.PORT, '/api/etat')[1]

        def analyse_au_repos():
            e = etat()
            a = e.get('analyse') or {}
            return a.get('phase') == 'termine' and not (e.get('actualisation') or {}).get('en_cours')

        def mon_dossier():
            return next((d for d in O.requete_json(self.PORT, '/api/dossiers')[1]['dossiers']
                         if d['chemin'].lower() == str(mes).lower()), None)

        def cliquer(selecteur):
            return ws.evaluer(f'(() => {{ const b = document.querySelector({json.dumps(selecteur)}); '
                              'if (!b || b.disabled) return false; b.click(); return true; })()', geste=True)

        def cliquer_libelle(libelle):
            return ws.evaluer('(() => { const b = [...document.querySelectorAll("button")].find((x) => '
                              f'x.getAttribute("aria-label") === {json.dumps(libelle)}); '
                              'if (!b || b.disabled) return false; b.click(); return true; })()', geste=True)

        def annonce(morceau):
            return O.attendre(lambda: (lambda t: t if morceau in t else None)(
                ws.evaluer("document.querySelector('#annonce').textContent")), 60)
        try:
            page = O.attendre_page(self.PORT_CDP, self.ORIGINE + '/', 120)
            ws = O.CDP(page['webSocketDebuggerUrl'])
            ws.appel('Runtime.enable')
            self.assertTrue(O.attendre(lambda: ws.evaluer(
                "!document.querySelector('#appli').hidden && document.querySelectorAll('#resultats a.tuile').length"),
                60), 'écran des photos absent')
            self.assertTrue(O.attendre(lambda: ws.evaluer("!document.querySelector('#actualiser').hidden"), 30))
            self.assertTrue(O.attendre(analyse_au_repos, 120))

            # 1. « Ajouter un dossier… » : la liste à cocher, les captures d'écran décochées d'office, avec la raison.
            choix.write_text(str(mes), encoding='utf-8')
            self.assertTrue(cliquer('#ouvrir-reglages'))
            self.assertTrue(O.attendre(lambda: ws.evaluer("document.querySelector('#reglages').open"), 10))
            self.assertTrue(cliquer('#r-ajouter'))
            self.assertTrue(O.attendre(lambda: ws.evaluer("document.querySelector('#sous-dossiers').open"), 30),
                            'liste des sous-dossiers absente')
            cases = ws.evaluer("[...document.querySelectorAll('#sd-liste li')].map((li) => ({"
                               "nom: li.querySelector('.nom-sd').textContent, coche: li.querySelector('input').checked,"
                               " raison: (li.querySelector('.note') || {}).textContent || null,"
                               " decrit: li.querySelector('input').getAttribute('aria-describedby')}))")
            par_nom = {c['nom']: c for c in cases}
            self.assertEqual(set(par_nom), {'Captures d\u2019écran', 'Vacances', 'Famille'}, cases)
            self.assertFalse(par_nom['Captures d\u2019écran']['coche'])
            self.assertIn('captures d\'écran', par_nom['Captures d\u2019écran']['raison'])
            self.assertTrue(par_nom['Captures d\u2019écran']['decrit'])                 # la raison est lue avec la case
            self.assertTrue(par_nom['Vacances']['coche'] and par_nom['Famille']['coche'])
            self.assertIn('3 photos', ws.evaluer("document.querySelector('#sd-liste').textContent"))
            ws.capture(str(self.dossier / 'assistant-sous-dossiers.png'))
            self.assertTrue(cliquer('#sd-ok'))
            d = O.attendre(mon_dossier, 30)
            self.assertTrue(d, 'dossier non ajouté')
            self.assertEqual([x['chemin'].lower() for x in d['exclusions']], [str(captures).lower()])
            racine = d['chemin']
            self.assertTrue(O.attendre(lambda: (mon_dossier() or {}).get('analysees') == 7 and analyse_au_repos(),
                                       240), mon_dossier())
            self.assertEqual(mon_dossier()['photos'], 7)                        # aucune capture d'écran

            # 2. « Actualiser » après la suppression de fichiers d'essai : réponse claire, photos oubliées.
            ws.evaluer("document.querySelector('#reglages').close(); 1")
            (vacances / 'plage-1.jpg').unlink()
            (vacances / 'plage-3.jpg').unlink()
            shutil.copyfile(photos / 'voiture-1.jpg', mes / 'voiture-1.jpg')
            self.assertTrue(cliquer('#actualiser'))
            texte = annonce('Actualisation terminée')
            self.assertTrue(texte, 'aucune réponse à la fin de l\'actualisation')
            self.assertIn('1 nouvelle photo, 2 photos retirées.', texte)
            a = etat()['actualisation']
            self.assertEqual((a['en_cours'], a['nouvelles'], a['retirees'], a['injoignables']), (False, 1, 2, []))
            ws.capture(str(self.dossier / 'actualiser.png'))
            self.assertTrue(O.attendre(lambda: mon_dossier()['photos'] == 6 and analyse_au_repos(), 120))

            # 3. Exclure « Famille » (fenêtre simulée), refuser un dossier hors du dossier suivi, puis « Ne plus exclure ».
            self.assertTrue(cliquer('#ouvrir-reglages'))
            choix.write_text(str(self.dossier / 'vide'), encoding='utf-8')
            self.assertTrue(O.attendre(lambda: cliquer_libelle(f'Exclure un sous-dossier de {racine}'), 10))
            self.assertTrue(annonce('n\'est pas dans'))
            choix.write_text(str(famille), encoding='utf-8')
            self.assertTrue(O.attendre(lambda: cliquer_libelle(f'Exclure un sous-dossier de {racine}'), 10))
            self.assertTrue(annonce('« Famille » est exclu : 2 photos ne sont plus dans MyPhotos.'))
            self.assertEqual(mon_dossier()['photos'], 4)
            self.assertTrue((famille / 'gateau-1.jpg').exists())                # rien n'est supprimé du disque
            self.assertTrue(O.attendre(lambda: ws.evaluer(
                "document.querySelector('#r-dossiers').textContent.includes('Famille')"), 10))
            ws.capture(str(self.dossier / 'exclusion.png'))
            self.assertTrue(O.attendre(lambda: cliquer_libelle('Ne plus exclure ' + racine + os.sep + 'Famille'), 10))
            self.assertTrue(annonce('n’est plus exclu'))
            self.assertTrue(O.attendre(lambda: mon_dossier()['photos'] == 6 and mon_dossier()['analysees'] == 6
                                       and analyse_au_repos(), 120), mon_dossier())
            self.assertEqual([x['chemin'].lower() for x in mon_dossier()['exclusions']], [str(captures).lower()])
            ws.evaluer("document.querySelector('#reglages').close(); 1")

            # 4. Photo introuvable : ouverte dans la visionneuse après la suppression de son fichier, elle est retirée.
            statut, liste = O.requete_json(self.PORT, '/api/photos?n=500')
            cible = None
            for p in liste['photos']:
                info = O.requete_json(self.PORT, f'/api/photo/{p["id"]}')[1]
                if info['dossier'].lower() == str(mes).lower() and info['nom'] == 'voiture-1.jpg':
                    cible = p['id']
                    break
            self.assertTrue(cible, 'photo d\'essai introuvable')
            (mes / 'voiture-1.jpg').unlink()
            ws.evaluer('location.reload(); 1')
            self.assertTrue(O.attendre(lambda: ws.evaluer(
                f"!!document.querySelector('.case[data-id=\"{cible}\"] a.tuile')"), 60))
            self.assertTrue(cliquer(f'.case[data-id="{cible}"] a.tuile'))
            self.assertTrue(O.attendre(lambda: ws.evaluer("document.querySelector('#v-erreur').textContent")
                                       == serveur.PHOTO_RETIREE, 30))
            vue = ws.evaluer("({erreur: document.querySelector('#v-erreur').textContent, "
                             "cache: document.querySelector('#v-erreur').hidden, "
                             "ouvrir: document.querySelector('#v-ouvrir').disabled, "
                             f"case: !!document.querySelector('.case[data-id=\"{cible}\"]')}})")
            self.assertEqual(vue, {'erreur': serveur.PHOTO_RETIREE,
                                   'cache': False, 'ouvrir': True, 'case': False})
            self.assertEqual(O.requete_json(self.PORT, f'/api/photo/{cible}')[0], 404)
            ws.capture(str(self.dossier / 'photo-introuvable.png'))
            ws.evaluer("document.querySelector('#v-fermer').click(); 1")

            # Fin : le dossier d'essai est retiré (les autres essais retrouvent leurs données), puis WM_CLOSE.
            self.assertEqual(O.requete_json(self.PORT, '/api/dossiers/retirer', {'id': mon_dossier()['id']})[0], 200)
            self.assertTrue(O.attendre(analyse_au_repos, 60))
            ws.fermer()
            ws = None
            principale = O.attendre(lambda: next((f for f in fil.faire(lambda: O.fenetres(set(job.pids())))
                                                  if f['titre'] == 'MyPhotos' and f['visible']
                                                  and f['classe'].startswith('WindowsForms')), None), 10)
            self.assertTrue(principale)
            fil.faire(lambda: O.u32.PostMessageW(principale['hwnd'], O.WM_CLOSE, 0, 0))
            self.assertTrue(O.attendre_processus(hproc, 60), 'MyPhotos ne s\'arrête pas')
            self.assertEqual(O.code_sortie(hproc), 0)
            self.assertTrue(job.attendre_vide(10), O.du_job(job))
            self.assertTrue(all(f.exists() for f in (mes / 'chat-1.jpg', famille / 'chien-2.jpg',
                                                     captures / 'Capture d’écran 1.png')))   # jamais supprimées
            choix_faits = [x for x in self.lignes_trace() if x['quoi'] == 'choix']
            self.assertEqual([x['args'][0] for x in choix_faits[1:]], [racine, racine])   # ouverte DANS le dossier
        finally:
            if ws is not None:
                ws.fermer()
            if O.code_sortie(hproc) is None:
                job.terminer()                       # filet : les processus de CE job seulement
            O.fermer_handle(hproc)
            job.fermer()
            O.fermer_bureau(hbureau)

    def test_relance_juste_apres_la_fermeture(self):
        """Constat R1 : fermer la fenêtre (WM_CLOSE dès qu'elle paraît) puis relancer MyPhotos 0 s, 0,5 s ou 1,5 s
        après : la nouvelle instance montre TOUJOURS sa fenêtre (jamais une sortie silencieuse ni « un autre
        programme ») ; la première sort en code 0. Puis : mutex de l'installateur présent tant que MyPhotos tourne,
        absent après ; DONNEES\\webview2 effacé même après une fermeture immédiate (constat R6) ; aucun faux
        avertissement de pywebview dans le journal."""
        O = self.O
        import processus_essai
        env = dict(self.env)                         # sans débogage : comme pour l'utilisateur
        ligne = [sys.executable, '-X', 'faulthandler', str(self.lanceur)]
        nom_bureau = f'MyPhotosRelance{os.getpid()}'
        hbureau = O.creer_bureau(nom_bureau)
        fil = O.FilBureau(hbureau)
        jobs, handles = [], []

        def lancer():
            j = processus_essai.Job()
            jobs.append(j)
            _pid, h = O.lancer(ligne, nom_bureau, str(RACINE), env, j)
            handles.append(h)
            return j, h

        def fenetre_de(j, delai):
            return O.attendre(lambda: next((f for f in fil.faire(lambda: O.fenetres(set(j.pids())))
                                            if f['titre'] == 'MyPhotos' and f['classe'].startswith('WindowsForms')
                                            and f['visible']), None), delai)
        try:
            ja, ha = lancer()
            for d in (0.0, 0.5, 1.5):
                fa = fenetre_de(ja, 90)
                self.assertTrue(fa, f'pas de fenêtre (avant la relance à {d} s)')
                self.assertTrue(TestMutex.mutex_existe())
                fil.faire(lambda: O.u32.PostMessageW(fa['hwnd'], O.WM_CLOSE, 0, 0))
                time.sleep(d)
                jb, hb = lancer()
                self.assertTrue(O.attendre_processus(ha, 60), f'la première ne s\'arrête pas ({d} s)')
                self.assertEqual(O.code_sortie(ha), 0)
                fb = fenetre_de(jb, 90)
                self.assertTrue(fb, f'relance à {d} s : aucune fenêtre')
                self.assertIsNone(O.code_sortie(hb), f'relance à {d} s : sortie {O.code_sortie(hb)}')
                ja, ha = jb, hb
            fa = fenetre_de(ja, 30)
            fil.faire(lambda: O.u32.PostMessageW(fa['hwnd'], O.WM_CLOSE, 0, 0))
            self.assertTrue(O.attendre_processus(ha, 60))
            self.assertEqual(O.code_sortie(ha), 0)
            self.assertTrue(all(j.attendre_vide(10) for j in jobs))
            self.assertFalse(TestMutex.mutex_existe())
            self.assertFalse((self.donnees / 'webview2').exists())
            journal = self.journal()
            self.assertNotIn('Failed to delete user data folder', journal)
            self.assertNotIn('déjà utilisé par un autre programme', journal)
            self.assertNotIn('alerter', ''.join(json.dumps(x) for x in self.lignes_trace()))
        finally:
            for j, h in zip(jobs, handles):
                if O.code_sortie(h) is None:
                    j.terminer()
                O.fermer_handle(h)
                j.fermer()
            O.fermer_bureau(hbureau)


if __name__ == '__main__':
    unittest.main()
