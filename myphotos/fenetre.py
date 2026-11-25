"""Fenêtre de MyPhotos : la page (page/index.html) dans SA fenêtre « MyPhotos », hors du navigateur.

    python -m myphotos             la fenêtre (repli : Edge en mode application, puis le navigateur par défaut)
    python -m myphotos --navigateur    l'ancien comportement : la page dans le navigateur par défaut
    MYPHOTOS_NAVIGATEUR=1              même chose ;  MYPHOTOS_SANS_PAGE=1 : ni fenêtre ni page (essais, accueil local)

serveur.main() reste le point d'entrée : il démarre le serveur local (127.0.0.1), puis confie la suite à Hote.
La page, son serveur et son API HTTP ne changent pas : la fenêtre affiche la même page, servie par le même serveur,
avec les mêmes contrôles de sécurité (Host, X-MyPhotos, Origin, Sec-Fetch-*, CSP à nonce). Aucun pont JavaScript vers
Python : l'API HTTP reste le seul canal entre la page et MyPhotos.

Trois modes, dans cet ordre (/api/etat dit lequel : « fenetre », « edge » ou « navigateur ») :

1. « fenetre » : pywebview 6.2.1 (licence BSD) avec WebView2 de Windows, par pythonnet (.NET Framework).
   - webview.start() exige le fil principal : le serveur HTTP tourne donc dans un fil.
   - Avant d'importer pywebview : WebView2 est cherché comme le fait pywebview (registre EdgeUpdate, .NET 4.6.2 ou
     plus), puis demandé au chargeur WebView2Loader.dll (GetAvailableCoreWebView2BrowserVersionString). Sans cela,
     pywebview afficherait une fenêtre blanche ou basculerait en silence sur MSHTML (Internet Explorer).
   - Si WebView2 échoue quand même (démarrage refusé, rien après DELAI_WEBVIEW2_S, processus du navigateur arrêté),
     la fenêtre se ferme sans rien montrer et le mode suivant prend le relais.
   - Durcissement (réglages de CoreWebView2, appliqués avant le premier chargement) :
     * pas de window.pywebview (inject_pywebview remplacé), messages web coupés (IsWebMessageEnabled), aucun objet
       hôte ; le filtre WebResourceRequested « * » de pywebview n'est pas posé (chaque requête passait par Python) ;
     * outils de développement, raccourcis du navigateur (impression, enregistrer, code source, F5, retour arrière…),
       barre d'état, zoom, balayage, saisie automatique : désactivés. MYPHOTOS_DEBUG=1 rend les outils de
       développement, les raccourcis et le menu complet ;
     * la fenêtre ne navigue QUE vers la page : http://127.0.0.1:<port>/ (voir navigation()). Un lien https cliqué
       part dans le navigateur par défaut ; tout le reste (file:, data:, javascript:, autres hôtes, autres chemins du
       serveur) est bloqué. Ni nouvelle fenêtre, ni téléchargement, ni autorisation (caméra, position…) ;
     * aucun dépôt venu d'ailleurs (AllowExternalDrop) : un fichier ou un lien glissé ne fait pas naviguer ;
     * menu contextuel réduit au texte : annuler, rétablir, couper, copier, coller, tout sélectionner ;
     * données de WebView2 en mode privé, dans DONNEES\\webview2. ATTENTION : en mode privé, pywebview efface TOUT
       ce dossier à la fermeture : il ne doit jamais désigner DONNEES lui-même ;
     * au démarrage, hors débogage, les variables WEBVIEW2_* et PYWEBVIEW_* (WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS,
       qui pourrait ouvrir un port de débogage…) sont retirées de l'environnement du processus ; toujours, celles du
       CLR .NET et de pythonnet (COR_PROFILER, COMPLUS_*, DOTNET_*, APPDOMAIN_MANAGER_*, PYTHONNET_*). Un fichier
       <exécutable>.config posé à côté du programme (MyPhotos n'en livre aucun) : .NET n'est pas chargé, repli sur Edge.
       Débogage : MYPHOTOS_DEBUG=1 depuis les sources ; MyPhotos.exe exige en plus l'argument --essai-debogage.
   - Fermer la fenêtre arrête MyPhotos proprement (fin du lot d'analyse, copie annulée sans fichier à moitié écrit,
     bases fermées). Analyse, copie, recherche des doublons ou téléchargement en cours : une question d'abord.
     « Arrêter MyPhotos » (page, Réglages) ferme la fenêtre. Pas d'arrêt pour inactivité tant qu'elle est ouverte.
   - « Choisir un dossier » : fenêtre de Windows ouverte par le fil de la fenêtre MyPhotos, à qui elle appartient
     (modale) : jamais derrière. Dans les replis (Edge, navigateur) : la même fenêtre de Windows, par COM
     (dialogue.py, serveur.ChoixDossier), au milieu de la fenêtre d'Edge quand on la connaît (FenetreEdge.zone).
   - Taille, position et état agrandi : DONNEES\\fenetre.json. Un écran disparu ramène la fenêtre sur un écran
     visible (placer()). Processus « par écran v2 » pour le DPI. Icône construction\\MyPhotos.ico ; identifiant
     d'application (AppUserModelID) explicite depuis les sources seulement (sinon les fenêtres de python.exe
     seraient regroupées) : l'exe garde l'identifiant que Windows tire de son chemin, le même que ses raccourcis.

2. « edge » : Microsoft Edge en mode application (--app), avec son profil à part, DONNEES\\edge (--disable-sync :
   jamais de connexion au compte). Fermer la fenêtre d'Edge arrête MyPhotos. Si Edge passe la main à une autre
   instance (profil déjà ouvert), sa fenêtre ne peut plus être suivie : arrêt après 3 minutes sans page.

3. « navigateur » : le navigateur par défaut, comme en 0.1 et 0.2 (arrêt 3 minutes après la dernière requête).

Relance pendant que MyPhotos tourne : un événement Windows nommé (Local\\MyPhotos-premier-plan-<port>), hors HTTP :
aucun site web ne peut le déclencher. La seconde instance permet le premier plan (AllowSetForegroundWindow), signale
l'événement et s'arrête ; la première restaure sa fenêtre et la passe devant. Une ancienne version sans fenêtre
(0.1.0, 0.2.0 : /api/etat sans « mode ») : message clair, et sa page s'ouvre comme avant.

Essais : tests/test_fenetre.py (sans interface) ; essai réel à la demande (MYPHOTOS_ESSAI_FENETRE=1), sur un
bureau Windows invisible, piloté par le protocole DevTools : MYPHOTOS_DEBUG=1 garde les variables WEBVIEW2_*
(--remote-debugging-port n'est jamais ouvert autrement ; pour l'exe : MYPHOTOS_DEBUG=1 ET l'argument
--essai-debogage).

Une relance depuis un AUTRE compte Windows du même PC (le port 127.0.0.1 est commun à la machine) : /api/etat donne
l'empreinte du compte (« compte ») ; message, et la page de l'autre compte ne s'ouvre pas. Mutex config.MUTEX tenu
toute la vie du processus (installateur).

Jeton d'accès (0.4.0, voir serveur.py) : la fenêtre, Edge et le navigateur ouvrent l'adresse d'entrée
http://127.0.0.1:<port>/?jeton=… (Hote.adresse) ; le serveur pose son cookie et redirige vers « / », seule adresse que
la fenêtre garde ensuite (navigation() permet les deux : même chemin « / »). Une relance lit le jeton dans les
données (config.JETON) pour interroger /api/etat et rouvrir la page ; un autre compte n'a pas ce fichier : la MyPhotos
déjà lancée le refuse (etat « jeton_refuse »), message AUTRE_COMPTE.
"""
import ctypes
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from ctypes import wintypes
from pathlib import Path

from . import config

journal = logging.getLogger(__name__)

NOM = 'MyPhotos'
FENETRE, EDGE, NAVIGATEUR, SANS_PAGE = 'fenetre', 'edge', 'navigateur', 'sans_page'
MODES = (FENETRE, EDGE, NAVIGATEUR)
VERSION_FENETRE = (0, 3, 0)            # première version avec sa propre fenêtre (et « mode » dans /api/etat)
AUMID = 'glenac.MyPhotos'              # identifiant d'application, depuis les sources seulement
DELAI_WEBVIEW2_S = 30.0                # WebView2 prêt au plus tard 30 s après l'ouverture de la fenêtre (mesuré : 2 s)
EDGE_PASSE_LA_MAIN_S = 5.0             # Edge qui s'arrête aussi vite a passé la main à une autre instance
EDGE_PAGE_ATTENDUE_S = 10.0            # … et sa page doit alors appeler MyPhotos dans les 10 s
RECHARGEMENTS_MAX = 3                  # page relancée au plus 3 fois en 2 minutes si son processus tombe
LARGEUR, HAUTEUR = 1280, 860           # taille au premier lancement (pixels logiques, à 96 ppp)
LARGEUR_MIN, HAUTEUR_MIN = 720, 520
FONDS = {'clair': '#f5f6f8', 'sombre': '#111316'}          # --fond de la page, clair et sombre
PLACEMENT = 'fenetre.json'
CLES_WEBVIEW2 = ('{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',     # runtime, puis versions bêta, dev, canary
                 '{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}', '{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}',
                 '{65C35B14-6C1D-4122-AC46-7148CC9D6497}')
MENU_TEXTE = {'undo', 'redo', 'cut', 'copy', 'paste', 'pasteAndMatchStyle', 'selectAll'}
ARG_DEBOGAGE = '--essai-debogage'      # exe : débogage seulement avec cet argument ET MYPHOTOS_DEBUG=1
PREFIXES_CLR = ('COMPLUS_', 'COR_', 'DOTNET_', 'PYTHONNET_')
NOMS_CLR = {'APPDOMAIN_MANAGER_ASM', 'APPDOMAIN_MANAGER_TYPE'}
RELEASE_NET_MIN = 461808               # .NET Framework 4.7.2 (minimum de pythonnet 3 ; Windows 10 1803 et plus)
RELANCE_VERIF_S = 1.0                  # relance : l'instance signalée est-elle toujours là, et pas en train de s'arrêter ?
WEBVIEW2_DOSSIER = 'webview2'          # DONNEES\webview2 (mode privé : effacé à chaque fermeture)
EDGE_DOSSIER = 'edge'                  # DONNEES\edge : profil d'Edge en mode application (repli)
AUTRE_COMPTE = ('MyPhotos est déjà ouvert par un autre compte de cet ordinateur. Il ne peut être ouvert que par un '
                'compte à la fois : demandez à cette personne de l\'arrêter (Réglages, puis Arrêter MyPhotos).')
PLANTAGES = ('L’affichage de MyPhotos a rencontré un problème plusieurs fois de suite.\n\n'
             'Réessayer ?\n\n(Non : fermer MyPhotos. Si cela recommence, redémarrez l’ordinateur.)')

ANCIENNE_VERSION = ('Une ancienne version de MyPhotos est ouverte : arrêtez-la (Réglages, puis Arrêter MyPhotos), '
                    'puis relancez MyPhotos.')
PHRASES_FERMETURE = {
    'analyse': 'L’analyse reprendra au prochain lancement.',
    'copie': 'La copie en cours s’arrêtera : les photos déjà copiées restent dans le dossier.',
    'doublons': 'La recherche des doublons reprendra au prochain lancement.',
    'telechargement': 'Le téléchargement du moteur reprendra au prochain lancement.',
}


# ---------------------------------------------------------------- choix du mode, environnement

def debogage(environ=None, argv=None):
    """Mode débogage (outils de développement de WebView2, variables WEBVIEW2_* gardées : essais par CDP).

    Depuis les sources : MYPHOTOS_DEBUG=1. L'exécutable exige EN PLUS l'argument --essai-debogage sur sa ligne de
    commande : une variable d'environnement persistante (HKCU\\Environment) ne suffit jamais à ouvrir un port de
    débogage ni les outils de développement de MyPhotos.exe."""
    env = os.environ if environ is None else environ
    if env.get('MYPHOTOS_DEBUG') != '1':
        return False
    if config.GELE:
        return ARG_DEBOGAGE in (sys.argv[1:] if argv is None else list(argv))
    return True


def _variable_retiree(nom, debug):
    n = nom.upper()
    if n.startswith(PREFIXES_CLR) or n in NOMS_CLR:
        return True                                  # jamais utiles à MyPhotos, même en débogage
    return not debug and n.startswith(('WEBVIEW2_', 'PYWEBVIEW_'))


def nettoyer_environnement(environ=None, debug=False):
    """Retire de l'environnement du processus, avant tout chargement de .NET ou de WebView2 :
    - les réglages du CLR .NET et de pythonnet (COMPLUS_*, COR_* dont COR_PROFILER, DOTNET_*, PYTHONNET_*,
      APPDOMAIN_MANAGER_ASM/TYPE) : un programme tiers ne fait pas charger ainsi son code dans MyPhotos ;
    - hors débogage, les variables WEBVIEW2_* (arguments du navigateur, port de débogage, dossier du runtime…) et
      PYWEBVIEW_* : un programme tiers ne peut pas ouvrir ainsi un accès à la page.
    Rend leurs noms."""
    env = os.environ if environ is None else environ
    noms = [n for n in list(env) if _variable_retiree(n, debug)]
    for n in noms:
        env.pop(n, None)
    return noms


def config_clr_etrangere():
    """Fichier <exécutable>.config posé à côté du programme (MyPhotos n'en livre aucun) : le CLR .NET le lirait et
    pourrait charger le code d'un tiers dans MyPhotos (appDomainManager, redirections d'assemblies…). Rend son
    chemin, ou None. Vérifié pour sys.executable et pour l'image réelle du processus (python.exe d'un environnement
    virtuel n'est qu'un lanceur)."""
    images = [sys.executable]
    if os.name == 'nt':
        try:
            tampon = ctypes.create_unicode_buffer(32768)
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            f = k32.GetModuleFileNameW
            f.argtypes, f.restype = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD
            if 0 < f(None, tampon, len(tampon)) < len(tampon):
                images.append(tampon.value)
        except (OSError, AttributeError):
            pass
    for image in images:
        if image:
            chemin = image + '.config'
            if os.path.lexists(chemin):
                return chemin
    return None


def mode_demande(argv, environ=None, sans_page=False):
    """« sans_page », « navigateur » (forcé : --navigateur ou MYPHOTOS_NAVIGATEUR=1) ou « fenetre » (avec replis)."""
    env = os.environ if environ is None else environ
    if sans_page:
        return SANS_PAGE
    if '--navigateur' in (argv or ()) or env.get('MYPHOTOS_NAVIGATEUR') == '1':
        return NAVIGATEUR
    return FENETRE


def version_tuple(texte):
    """'0.2.0' -> (0, 2, 0) ; '0.3.0b1' -> (0, 3, 0) ; rien de lisible -> ()."""
    res = []
    for morceau in str(texte or '').split('.')[:4]:
        chiffres = ''
        for c in morceau:
            if not c.isdigit():
                break
            chiffres += c
        if not chiffres:
            break
        res.append(int(chiffres))
    return tuple(res)


def ancienne_version(etat):
    """Vrai pour une MyPhotos sans fenêtre (0.1.0, 0.2.0) : /api/etat sans « mode », ou version plus ancienne."""
    return etat.get('mode') not in MODES or version_tuple(etat.get('version')) < VERSION_FENETRE


def decision_relance(etat, demande):
    """Que faire quand MyPhotos tourne déjà (etat : son /api/etat) : « rien », « ancienne » (message, puis sa page),
    « premier_plan » (sa fenêtre revient devant) ou « page » (ouvrir sa page dans le navigateur)."""
    if demande == SANS_PAGE:
        return 'rien'
    if ancienne_version(etat):
        return 'page' if demande == NAVIGATEUR else 'ancienne'
    if demande == NAVIGATEUR:
        return 'page'
    return 'premier_plan' if etat.get('mode') in (FENETRE, EDGE) else 'page'


def autre_compte(etat, moi=None):
    """Vrai si la MyPhotos qui répond (etat : son /api/etat) tourne sous un AUTRE compte Windows de ce PC : le port
    127.0.0.1 est commun à toute la machine. Une version sans « compte » (0.1, 0.2) : inconnu, donc faux."""
    sien = etat.get('compte')
    if not sien:
        return False
    moi = identite_compte() if moi is None else moi
    return bool(moi) and sien != moi


def relancer(etat, adresse, port, demande, ouvrir_page, informer, etat_actuel=None):
    """Seconde instance : MyPhotos répond déjà sur ce port. Rend le code de sortie (0), ou None si l'instance
    signalée s'arrête en fait (fenêtre fermée à l'instant) : main() attend alors qu'elle libère le port.

    adresse : celle de sa page, avec le jeton d'accès de ses données pour une 0.4.0 ou plus (serveur.adresse_entree).
    etat['jeton_refuse'] (serveur.etat_instance) : une MyPhotos 0.4.0 ou plus a refusé notre jeton ; ses données, donc
    elle-même, sont à un autre compte Windows (ou à d'autres données) : même message que pour un autre compte."""
    if etat.get('jeton_refuse') or autre_compte(etat):
        journal.warning('MyPhotos est déjà ouvert par un autre compte de cet ordinateur (ou avec d\'autres données) : '
                        'rien n\'est ouvert')
        if demande != SANS_PAGE:
            informer(AUTRE_COMPTE)
        return 0
    decision = decision_relance(etat, demande)
    if decision == 'rien':
        return 0
    if decision == 'ancienne':
        journal.info('MyPhotos %s (sans fenêtre) est déjà ouvert : ouverture de sa page', etat.get('version'))
        ouvrir_page(adresse)
        informer(ANCIENNE_VERSION)
        return 0
    if decision == 'premier_plan' and signaler_premier_plan(port):
        if etat_actuel is not None:
            time.sleep(RELANCE_VERIF_S)
            e = etat_actuel()
            if e is None or e.get('arret'):
                journal.info('MyPhotos s\'arrêtait : on attend qu\'il ait fini pour ouvrir une nouvelle fenêtre')
                return None
        journal.info('MyPhotos est déjà ouvert : sa fenêtre revient au premier plan')
        return 0
    journal.info('MyPhotos est déjà ouvert : ouverture de la page')
    ouvrir_page(adresse)
    return 0


def navigation(uri, port):
    """Verdict pour une navigation demandée dans la fenêtre : « permise » (la page de MyPhotos elle-même),
    « externe » (lien https : navigateur par défaut, s'il vient d'un geste de l'utilisateur) ou « bloquee »."""
    try:
        texte = str(uri)
        if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in texte):
            return 'bloquee'
        u = urllib.parse.urlsplit(texte)
        sans_identifiants = u.username is None and u.password is None
        if (u.scheme == 'http' and u.hostname == config.HOTE and u.port == port and sans_identifiants
                and u.path in ('/', '/index.html')):
            return 'permise'
        if u.scheme == 'https' and u.hostname and sans_identifiants and u.port in (None, 443):
            return 'externe'
    except ValueError:                           # port invalide, crochets mal formés…
        pass
    return 'bloquee'


def message_fermeture(travaux):
    """Question posée quand on ferme la fenêtre pendant un travail ; None s'il n'y a rien en cours."""
    lignes = [PHRASES_FERMETURE[t] for t in travaux if t in PHRASES_FERMETURE]
    if not lignes:
        return None
    return 'Fermer MyPhotos maintenant ?\n\n' + '\n'.join(lignes)


# ---------------------------------------------------------------- placement de la fenêtre

def _intersection(a, b):
    g, h, d, bas = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return max(0, d - g) * max(0, bas - h)


def placer(sauve, ecrans, defaut=(LARGEUR, HAUTEUR), mini=(LARGEUR_MIN, HAUTEUR_MIN)):
    """Rectangle de la fenêtre, en pixels physiques : {x, y, l, h, maximisee}, ou None (Windows décide).

    sauve : {x, y, l, h, dpi, maximisee} de la fermeture précédente (fenetre.json) ou None ;
    ecrans : [{zone: (gauche, haut, droite, bas) de la zone de travail, dpi, principal}].
    La fenêtre reste entière sur l'écran qu'elle touche le plus ; sur l'écran principal, centrée, si son écran a
    disparu. Taille gardée en pixels logiques d'un écran à l'autre (DPI), jamais plus grande que la zone de travail.
    Au premier lancement : taille `defaut`, centrée ; agrandie si l'écran est trop petit pour elle."""
    ecrans = [e for e in ecrans or () if e.get('zone') and e['zone'][2] > e['zone'][0] and e['zone'][3] > e['zone'][1]]
    if not ecrans:
        return None
    principal = next((e for e in ecrans if e.get('principal')), ecrans[0])
    try:
        x, y, l, h = (int(sauve[k]) for k in ('x', 'y', 'l', 'h'))
        dpi_sauve = int(sauve.get('dpi') or 96)
        maximisee = bool(sauve.get('maximisee'))
        if l <= 0 or h <= 0 or not 48 <= dpi_sauve <= 960:
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError):
        sauve = None
    if sauve is None:
        zone, dpi = principal['zone'], int(principal.get('dpi') or 96)
        zl, zh = zone[2] - zone[0], zone[3] - zone[1]
        l, h = round(defaut[0] * dpi / 96), round(defaut[1] * dpi / 96)
        trop_petit = l > zl * 0.95 or h > zh * 0.95
        l, h = min(l, zl), min(h, zh)
        return {'x': zone[0] + (zl - l) // 2, 'y': zone[1] + (zh - h) // 2, 'l': l, 'h': h, 'maximisee': trop_petit}
    rect = (x, y, x + l, y + h)
    cible = max(ecrans, key=lambda e: _intersection(rect, e['zone']))
    recentrer = _intersection(rect, cible['zone']) == 0
    if recentrer:
        cible = principal
    zone, dpi = cible['zone'], int(cible.get('dpi') or 96)
    if dpi != dpi_sauve:
        l, h = round(l * dpi / dpi_sauve), round(h * dpi / dpi_sauve)
    zl, zh = zone[2] - zone[0], zone[3] - zone[1]
    l = min(max(l, round(mini[0] * dpi / 96)), zl)
    h = min(max(h, round(mini[1] * dpi / 96)), zh)
    if recentrer:
        x, y = zone[0] + (zl - l) // 2, zone[1] + (zh - h) // 2
    else:
        x = min(max(x, zone[0]), zone[2] - l)
        y = min(max(y, zone[1]), zone[3] - h)
    return {'x': x, 'y': y, 'l': l, 'h': h, 'maximisee': maximisee}


def lire_placement(dossier):
    try:
        d = json.loads((Path(dossier) / PLACEMENT).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def ecrire_placement(dossier, placement):
    """Écrit fenetre.json d'un coup (fichier provisoire, puis remplacement)."""
    cible = Path(dossier) / PLACEMENT
    provisoire = cible.with_name(PLACEMENT + '.part')
    try:
        provisoire.write_text(json.dumps(placement), encoding='utf-8')
        os.replace(provisoire, cible)
    except OSError:
        journal.warning('position de la fenêtre impossible à retenir', exc_info=True)


# ---------------------------------------------------------------- Windows (ctypes, instances privées des DLL)

_DLL = {}


class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [('length', wintypes.UINT), ('flags', wintypes.UINT), ('showCmd', wintypes.UINT),
                ('ptMinPosition', wintypes.POINT), ('ptMaxPosition', wintypes.POINT),
                ('rcNormalPosition', wintypes.RECT)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_wchar * 260)]


_ENUM_ECRANS = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT),
                                  wintypes.LPARAM)
_ENUM_FENETRES = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _dll(nom):
    """Instance privée d'une DLL : nos argtypes ne touchent ni ceux de pywebview ni ceux de serveur.py."""
    if nom not in _DLL:
        d = ctypes.WinDLL(nom, use_last_error=True)
        v, u, b, w, h = ctypes.c_void_p, wintypes.UINT, wintypes.BOOL, wintypes.LPCWSTR, wintypes.HANDLE
        signatures = {
            'kernel32': {'CreateEventW': ([v, b, b, w], h), 'OpenEventW': ([wintypes.DWORD, b, w], h),
                         'SetEvent': ([h], b), 'WaitForSingleObject': ([h, wintypes.DWORD], wintypes.DWORD),
                         'CloseHandle': ([h], b), 'OpenProcess': ([wintypes.DWORD, b, wintypes.DWORD], h),
                         'TerminateProcess': ([h, u], b), 'CreateToolhelp32Snapshot': ([wintypes.DWORD] * 2, h),
                         'Process32FirstW': ([h, ctypes.POINTER(_PROCESSENTRY32W)], b),
                         'Process32NextW': ([h, ctypes.POINTER(_PROCESSENTRY32W)], b),
                         'GetProcessTimes': ([h] + [ctypes.POINTER(wintypes.FILETIME)] * 4, b),
                         'CreateMutexW': ([v, b, w], h), 'GetCurrentProcess': ([], h),
                         'QueryFullProcessImageNameW': ([h, wintypes.DWORD, wintypes.LPWSTR,
                                                         ctypes.POINTER(wintypes.DWORD)], b),
                         'LocalFree': ([v], v)},
            'advapi32': {'OpenProcessToken': ([h, wintypes.DWORD, ctypes.POINTER(h)], b),
                         'GetTokenInformation': ([h, ctypes.c_int, v, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD)], b),
                         'ConvertSidToStringSidW': ([v, ctypes.POINTER(ctypes.c_void_p)], b)},
            'ntdll': {'NtQueryInformationProcess': ([h, ctypes.c_int, v, wintypes.ULONG,
                                                     ctypes.POINTER(wintypes.ULONG)], ctypes.c_long)},
            'user32': {'AllowSetForegroundWindow': ([wintypes.DWORD], b), 'SetForegroundWindow': ([v], b),
                       'BringWindowToTop': ([v], b), 'ShowWindow': ([v, ctypes.c_int], b), 'IsIconic': ([v], b),
                       'IsWindowVisible': ([v], b), 'GetWindowThreadProcessId': ([v, ctypes.POINTER(wintypes.DWORD)],
                                                                                  wintypes.DWORD),
                       'EnumWindows': ([_ENUM_FENETRES, wintypes.LPARAM], b),
                       'GetClassNameW': ([v, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                       'GetWindowTextW': ([v, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                       'PostMessageW': ([v, u, wintypes.WPARAM, wintypes.LPARAM], b),
                       'EnumDisplayMonitors': ([v, v, _ENUM_ECRANS, wintypes.LPARAM], b),
                       'GetMonitorInfoW': ([v, ctypes.POINTER(_MONITORINFO)], b),
                       'GetDpiForWindow': ([v], u), 'GetWindowRect': ([v, ctypes.POINTER(wintypes.RECT)], b),
                       'GetWindowPlacement': ([v, ctypes.POINTER(_WINDOWPLACEMENT)], b),
                       'SetProcessDpiAwarenessContext': ([v], b)},
            'shcore': {'GetDpiForMonitor': ([v, ctypes.c_int, ctypes.POINTER(u), ctypes.POINTER(u)], ctypes.c_long)},
            'shell32': {'SetCurrentProcessExplicitAppUserModelID': ([w], ctypes.c_long)},
            'ole32': {'CoTaskMemFree': ([v], None)},
        }[nom]
        for fonction, (arguments, retour) in signatures.items():
            try:
                f = getattr(d, fonction)
            except AttributeError:                  # Windows trop ancien pour cette fonction
                continue
            f.argtypes, f.restype = arguments, retour
        _DLL[nom] = d
    return _DLL[nom]


def preparer_processus():
    """Avant toute fenêtre : DPI « par écran v2 » (sinon pywebview impose « système ») ; depuis les sources, un
    identifiant d'application à MyPhotos, pour qu'il ait son bouton et son icône dans la barre des tâches."""
    if os.name != 'nt':
        return
    try:
        _dll('user32').SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except Exception:
        journal.debug('DPI par écran indisponible', exc_info=True)
    if not config.GELE:
        try:
            _dll('shell32').SetCurrentProcessExplicitAppUserModelID(AUMID)
        except Exception:
            journal.debug('identifiant d\'application impossible à poser', exc_info=True)


def ecrans():
    """Zones de travail des écrans (pixels physiques), leur DPI et l'écran principal."""
    if os.name != 'nt':
        return []
    res = []
    u32 = _dll('user32')

    def rappel(hmon, _hdc, _rect, _donnee):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if u32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            dpi = 96
            try:
                dx, dy = wintypes.UINT(), wintypes.UINT()
                if _dll('shcore').GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
                    dpi = dx.value
            except Exception:
                pass
            z = info.rcWork
            res.append({'zone': (z.left, z.top, z.right, z.bottom), 'dpi': dpi, 'principal': bool(info.dwFlags & 1)})
        return True
    try:
        u32.EnumDisplayMonitors(None, None, _ENUM_ECRANS(rappel), 0)
    except Exception:
        journal.debug('écrans illisibles', exc_info=True)
    return res


def theme_sombre():
    """Vrai si Windows affiche les applications en mode sombre (couleur de la fenêtre avant la page)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            return winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] == 0
    except (OSError, ImportError):
        return False


def nom_evenement(port):
    return f'Local\\MyPhotos-premier-plan-{int(port)}'


def creer_evenement(port):
    """Événement nommé de la première instance (remis à zéro automatiquement) ; None s'il est impossible."""
    if os.name != 'nt':
        return None
    h = _dll('kernel32').CreateEventW(None, False, False, nom_evenement(port))
    return h or None


def signaler_premier_plan(port):
    """Seconde instance : réveille la première, qui passe sa fenêtre devant. Faux si elle n'écoute pas."""
    if os.name != 'nt':
        return False
    k32 = _dll('kernel32')
    h = k32.OpenEventW(0x0002, False, nom_evenement(port))            # EVENT_MODIFY_STATE
    if not h:
        return False
    try:
        _dll('user32').AllowSetForegroundWindow(0xFFFFFFFF)          # ASFW_ANY : la première peut passer devant
        return bool(k32.SetEvent(h))
    finally:
        k32.CloseHandle(h)


def fenetres_du_processus(pid):
    """Fenêtres de premier niveau, visibles, d'un processus : [{hwnd, classe, titre}]."""
    if os.name != 'nt':
        return []
    u32 = _dll('user32')
    res = []

    def rappel(hwnd, _donnee):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            classe, titre = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(512)
            u32.GetClassNameW(hwnd, classe, 256)
            u32.GetWindowTextW(hwnd, titre, 512)
            res.append({'hwnd': hwnd, 'classe': classe.value, 'titre': titre.value})
        return True
    u32.EnumWindows(_ENUM_FENETRES(rappel), 0)
    return res


def fenetres_edge(fenetres):
    """Fenêtres de navigateur d'Edge (titrées), celle de MyPhotos d'abord : le processus en a d'autres, cachées
    ou sans titre, qui ne sont pas à toucher."""
    res = [f for f in fenetres if f['classe'].startswith('Chrome_WidgetWin') and f['titre']]
    return sorted(res, key=lambda f: not f['titre'].startswith(NOM))


def _processus():
    """{pid: (pid du parent, nom de l'exécutable)} de tous les processus."""
    k32 = _dll('kernel32')
    h = k32.CreateToolhelp32Snapshot(0x2, 0)                          # TH32CS_SNAPPROCESS
    if not h or h == ctypes.c_void_p(-1).value:
        return {}
    res = {}
    try:
        e = _PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(h, ctypes.byref(e))
        while ok:
            res[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
            ok = k32.Process32NextW(h, ctypes.byref(e))
    finally:
        k32.CloseHandle(h)
    return res


def _creation(pid):
    """Date de création d'un processus (FILETIME), ou None."""
    k32 = _dll('kernel32')
    h = k32.OpenProcess(0x1000, False, pid)                           # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        t = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return None
        return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
    finally:
        k32.CloseHandle(h)


NOM_WEBVIEW2 = 'msedgewebview2.exe'


def webview2_restants(tous=None, moi=None, creation=_creation):
    """Candidats du filet de sécurité : processus msedgewebview2.exe dont le parent est CE processus et créés après
    lui. Seulement les enfants DIRECTS : notre propre identifiant ne peut pas être réutilisé tant que MyPhotos vit,
    alors qu'un parcours des descendants par « PID parent » atteindrait les enfants d'un programme étranger qui aurait
    hérité d'un identifiant réutilisé (incident du 24/11). Les processus de rendu de WebView2 s'arrêtent d'eux-mêmes
    avec son processus navigateur. Chaque candidat est revérifié par son handle avant tout arrêt (_ouvrir_verifie)."""
    tous = _processus() if tous is None else tous
    moi = os.getpid() if moi is None else moi
    debut = creation(moi)
    if debut is None:
        return []
    return [p for p, (parent, nom) in tous.items()
            if parent == moi and p != moi and nom.lower() == NOM_WEBVIEW2 and (creation(p) or 0) >= debut]


class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [('ExitStatus', ctypes.c_long), ('PebBaseAddress', ctypes.c_void_p),
                ('AffinityMask', ctypes.c_size_t), ('BasePriority', ctypes.c_long),
                ('UniqueProcessId', ctypes.c_size_t), ('InheritedFromUniqueProcessId', ctypes.c_size_t)]


def _infos_handle(h):
    """(pid du parent, date de création, chemin de l'image) lus PAR LE HANDLE (le processus visé ne peut plus changer
    d'identité), ou None."""
    k32 = _dll('kernel32')
    pbi = _PROCESS_BASIC_INFORMATION()
    if _dll('ntdll').NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
        return None
    t = [wintypes.FILETIME() for _ in range(4)]
    if not k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
        return None
    tampon = ctypes.create_unicode_buffer(32768)
    n = wintypes.DWORD(len(tampon))
    if not k32.QueryFullProcessImageNameW(h, 0, tampon, ctypes.byref(n)):
        return None
    return (int(pbi.InheritedFromUniqueProcessId), (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime, tampon.value)


def _ouvrir_verifie(pid, moi, debut, nom=NOM_WEBVIEW2):
    """Handle (SYNCHRONIZE | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION) sur `pid`, seulement si CE
    processus-là est bien notre enfant direct `nom`, créé après nous (vérifié par le handle) ; sinon None."""
    k32 = _dll('kernel32')
    h = k32.OpenProcess(0x00100000 | 0x0001 | 0x1000, False, pid)
    if not h:
        return None
    try:
        infos = _infos_handle(h)
    except Exception:
        infos = None
    if (infos is not None and infos[0] == moi and infos[1] >= debut
            and os.path.basename(infos[2]).lower() == nom.lower()):
        return h
    k32.CloseHandle(h)
    return None


def arreter_webview2_restants(patience=3.0):
    """Filet de sécurité : la fenêtre fermée, un processus navigateur de WebView2 lancé par MyPhotos qui ne
    s'arrête pas (bloqué sur sa propre erreur de démarrage, par exemple) est arrêté après `patience` secondes, par
    le HANDLE ouvert et vérifié (_ouvrir_verifie), jamais par un identifiant relu après coup."""
    if os.name != 'nt':
        return []
    k32 = _dll('kernel32')
    moi = os.getpid()
    debut = _creation(moi)
    if debut is None:
        return []
    handles = {}
    for p in webview2_restants(moi=moi):
        h = _ouvrir_verifie(p, moi, debut)
        if h:
            handles[p] = h
    arretes = []
    try:
        fin = time.monotonic() + patience
        for p, h in handles.items():
            reste = max(0, int((fin - time.monotonic()) * 1000))
            if k32.WaitForSingleObject(h, reste) != 0:                 # toujours en vie après la patience
                k32.TerminateProcess(h, 1)
                arretes.append(p)
    finally:
        for h in handles.values():
            k32.CloseHandle(h)
    if arretes:
        journal.warning('WebView2 ne s\'est pas arrêté avec la fenêtre : %d processus arrêté(s)', len(arretes))
    return arretes


def effacer_donnees_webview2(dossier=None, essais=3, pause=0.5):
    """Efface DONNEES\\webview2 (mode privé), que pywebview n'efface pas si WebView2 n'a pas démarré ou s'est arrêté
    (fenêtre fermée tout de suite, plantage). Jamais DONNEES lui-même. Vrai si rien ne reste."""
    cible = Path(config.DONNEES if dossier is None else dossier) / WEBVIEW2_DOSSIER
    for i in range(essais):
        if not cible.exists():
            return True
        shutil.rmtree(cible, ignore_errors=True)
        if not cible.exists():
            return True
        if i + 1 < essais:
            time.sleep(pause)                    # fichier encore tenu un instant par un processus qui s'arrête
    journal.warning('données de WebView2 impossibles à effacer : %s', cible)
    return False


class _FiltreEffacement(logging.Filter):
    """pywebview signale à tort « Failed to delete user data folder » quand WebView2 n'a pas démarré : MyPhotos
    efface lui-même ce dossier (effacer_donnees_webview2) et ne signale que son propre échec."""

    def filter(self, record):
        return not str(record.getMessage()).startswith('Failed to delete user data folder')


_COMPTE = []


def identite_compte():
    """Empreinte (16 caractères) du compte Windows qui fait tourner ce processus (son SID) : /api/etat la donne pour
    qu'une relance depuis un AUTRE compte du même PC n'ouvre jamais la MyPhotos (les photos) de celui-ci. Le SID
    lui-même n'est pas exposé. '' si illisible."""
    if _COMPTE:
        return _COMPTE[0]
    sid = ''
    if os.name == 'nt':
        try:
            k32, a32 = _dll('kernel32'), _dll('advapi32')
            jeton = wintypes.HANDLE()
            if a32.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(jeton)):      # TOKEN_QUERY
                try:
                    taille = wintypes.DWORD()
                    a32.GetTokenInformation(jeton, 1, None, 0, ctypes.byref(taille))            # TokenUser
                    tampon = ctypes.create_string_buffer(max(taille.value, 64))
                    if a32.GetTokenInformation(jeton, 1, tampon, len(tampon), ctypes.byref(taille)):
                        psid = ctypes.cast(tampon, ctypes.POINTER(ctypes.c_void_p))[0]
                        texte = ctypes.c_void_p()
                        if a32.ConvertSidToStringSidW(psid, ctypes.byref(texte)):
                            sid = ctypes.wstring_at(texte.value)
                            k32.LocalFree(texte)
                finally:
                    k32.CloseHandle(jeton)
        except (OSError, AttributeError):
            journal.debug('compte Windows illisible', exc_info=True)
    else:
        sid = str(os.getuid())
    import hashlib
    _COMPTE.append(hashlib.sha256(('MyPhotos|' + sid).encode()).hexdigest()[:16] if sid else '')
    return _COMPTE[0]


_MUTEX = []


def tenir_mutex():
    """Mutex nommé config.MUTEX (l'installateur s'en sert pour savoir si MyPhotos tourne) : créé au démarrage du
    processus qui porte le serveur, dans tous les modes ; le handle reste ouvert toute la vie du processus (jamais
    fermé : Windows le libère à la sortie). Rend le handle (ou None hors Windows / en cas d'échec)."""
    if _MUTEX:
        return _MUTEX[0]
    if os.name != 'nt':
        return None
    h = _dll('kernel32').CreateMutexW(None, False, config.MUTEX)
    if not h:
        journal.warning('mutex %s impossible à créer (%d)', config.MUTEX, ctypes.get_last_error())
        return None
    _MUTEX.append(h)
    return h


def passer_devant(hwnd):
    """Restaure une fenêtre réduite et la met au premier plan (permis par la seconde instance)."""
    u32 = _dll('user32')
    if u32.IsIconic(hwnd):
        u32.ShowWindow(hwnd, 9)                                       # SW_RESTORE
    u32.BringWindowToTop(hwnd)
    return bool(u32.SetForegroundWindow(hwnd))


def _version_registre():
    """Le contrôle de pywebview 6.2.1 (winforms._is_chromium), refait sans l'importer : .NET Framework
    4.7.2 ou plus (pywebview : 4.6.2), et WebView2 86 ou plus inscrit dans EdgeUpdate (utilisateur, ou machine sous WOW6432Node). S'il
    échouait, importer pywebview basculerait en silence sur MSHTML et inscrirait l'exécutable dans le registre
    (FEATURE_BROWSER_EMULATION d'Internet Explorer) : on passe alors directement à Edge."""
    try:
        import platform
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full') as k:
            # pywebview se contente de 4.6.2 (394802) ; pythonnet 3 exige 4.7.2 (sa documentation) : en dessous, son
            # chargement échouerait, autant passer directement à Edge.
            if int(winreg.QueryValueEx(k, 'Release')[0]) < RELEASE_NET_MIN:
                return False
    except (OSError, ValueError):
        return False
    machine = r'SOFTWARE\Microsoft' if platform.machine() == 'x86' else r'SOFTWARE\WOW6432Node\Microsoft'
    for cle in CLES_WEBVIEW2:
        for racine, chemin in ((winreg.HKEY_CURRENT_USER, rf'SOFTWARE\Microsoft\EdgeUpdate\Clients\{cle}'),
                               (winreg.HKEY_LOCAL_MACHINE, rf'{machine}\EdgeUpdate\Clients\{cle}')):
            try:
                with winreg.OpenKey(racine, chemin) as k:
                    if version_tuple(winreg.QueryValueEx(k, 'pv')[0])[:1] >= (86,):
                        return True
            except OSError:
                continue
    return False


def _chargeur_webview2():
    """WebView2Loader.dll livré avec pywebview (dans l'exe : extrait avec lui)."""
    archi = 'win-arm64' if os.environ.get('PROCESSOR_ARCHITECTURE', '').upper() == 'ARM64' else (
        'win-x64' if ctypes.sizeof(ctypes.c_void_p) == 8 else 'win-x86')
    racines = []
    try:
        import importlib.util
        spec = importlib.util.find_spec('webview')
        racines += [Path(p) for p in (spec.submodule_search_locations or [])] if spec else []
    except (ImportError, ValueError):
        pass
    racines.append(Path(config.CODE) / 'webview')
    for r in racines:
        p = r / 'lib' / 'runtimes' / archi / 'native' / 'WebView2Loader.dll'
        if p.is_file():
            return p
    return None


def version_webview2():
    """Version du runtime WebView2 utilisable, ou None (absent, abîmé, pywebview manquant)."""
    if os.name != 'nt' or not _version_registre():
        return None
    chargeur = _chargeur_webview2()
    if chargeur is None:
        return None
    try:
        f = ctypes.WinDLL(str(chargeur)).GetAvailableCoreWebView2BrowserVersionString
        f.argtypes, f.restype = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_long
        p = ctypes.c_void_p()
        if f(None, ctypes.byref(p)) != 0 or not p.value:
            return None
        try:
            return ctypes.wstring_at(p.value)
        finally:
            _dll('ole32').CoTaskMemFree(p)
    except OSError:
        journal.debug('chargeur WebView2 inutilisable', exc_info=True)
        return None


def trouver_edge():
    """msedge.exe (inscription « App Paths » de la MACHINE, puis Program Files) ; None s'il est absent. Jamais
    HKEY_CURRENT_USER ni %LOCALAPPDATA% : n'importe quel programme de l'utilisateur peut y écrire, et le repli
    lancerait alors son exécutable."""
    candidats = []
    try:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe') as k:
                candidats.append(os.path.expandvars(str(winreg.QueryValueEx(k, '')[0]).strip().strip('"')))
        except OSError:
            pass
    except ImportError:
        return None
    for variable in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(variable):
            candidats.append(os.path.join(os.environ[variable], 'Microsoft', 'Edge', 'Application', 'msedge.exe'))
    return next((c for c in candidats if c and os.path.isfile(c)), None)


def ligne_edge(edge, adresse, profil, premiere_fois):
    """Edge en mode application, avec son propre profil : ni synchronisation ni écran d'accueil. Edge retient
    ensuite lui-même la taille de la fenêtre dans ce profil."""
    ligne = [edge, f'--app={adresse}', f'--user-data-dir={profil}', '--no-first-run', '--no-default-browser-check',
             '--disable-sync']
    if premiere_fois:
        ligne.append(f'--window-size={LARGEUR},{HAUTEUR}')
    return ligne


def _ouvrir(adresse):
    from .serveur import ouvrir_page
    ouvrir_page(adresse)


def ouvrir_lien_externe(uri):
    """Lien https demandé par la page : navigateur par défaut (adresse vérifiée par navigation())."""
    journal.info('lien ouvert dans le navigateur : %s', urllib.parse.urlsplit(uri).hostname)
    _ouvrir(uri)


# ---------------------------------------------------------------- l'hôte : serveur dans un fil, fenêtre devant

class Hote:
    """Mode « fenetre » et ses replis. executer() tourne dans le fil principal et rend la main quand MyPhotos
    s'arrête (fenêtre fermée, « Arrêter MyPhotos », ou inactivité en mode navigateur)."""

    def __init__(self, app, serveur, adresse, debug=False):
        self.app = app
        self.serveur = serveur
        self.adresse = adresse                   # adresse d'entrée, avec le jeton d'accès (serveur.adresse_entree)
        self.port = serveur.server_address[1]
        self.debug = debug
        self.webview = None                      # FenetreWebView2
        self.edge = None                         # FenetreEdge
        self._fin = threading.Event()            # MyPhotos s'arrête
        self._evenement = None
        self._fil_serveur = None

    def executer(self):
        preparer_processus()
        self._evenement = creer_evenement(self.port)
        if self._evenement:
            threading.Thread(target=self._veiller_relance, name='myphotos-relance', daemon=True).start()
        app = self.app
        app.mode = FENETRE
        app.inactivite = False                   # la fenêtre décide : pas d'arrêt pour inactivité
        app.quand_arret = self._arret_demande
        self._fil_serveur = threading.Thread(target=self.serveur.serve_forever, kwargs={'poll_interval': 0.5},
                                             name='myphotos-http', daemon=True)
        self._fil_serveur.start()
        app.serveur_pret()
        try:
            resultat = 'echec'
            if not self._fin.is_set():
                self.webview = FenetreWebView2(self)
                resultat = self.webview.lancer()
            if resultat == 'echec' and not self._fin.is_set():
                self.edge = FenetreEdge(self)
                resultat = self.edge.lancer()
            if resultat in ('echec', 'perdue') and not self._fin.is_set():
                self._navigateur(resultat == 'echec' or not self._page_appelee(self.edge.debut))
        except KeyboardInterrupt:
            journal.info('arrêt demandé (Ctrl+C)')
        finally:
            self._terminer()
        return 0

    def _page_appelee(self, depuis):
        """La page a-t-elle appelé MyPhotos depuis `depuis` (time.monotonic) ? Attend jusqu'à 10 s."""
        fin = time.monotonic() + EDGE_PAGE_ATTENDUE_S
        while time.monotonic() < fin and not self._fin.is_set():
            if self.app.derniere_activite > depuis:
                return True
            time.sleep(0.2)
        return self.app.derniere_activite > depuis

    def _navigateur(self, ouvrir):
        """Mode navigateur : arrêt 3 minutes après la dernière requête, comme avant la fenêtre."""
        app = self.app
        app.mode = NAVIGATEUR
        app.activite()
        app.inactivite = True
        if ouvrir:
            journal.info('page ouverte dans le navigateur par défaut')
            _ouvrir(self.adresse)
        while self._fil_serveur.is_alive():
            self._fil_serveur.join(0.5)

    def _arret_demande(self):
        """Application.arreter() (page, inactivité, fermeture) : la fenêtre se ferme tout de suite."""
        self._fin.set()
        for f in (self.webview, self.edge):
            if f is not None:
                threading.Thread(target=f.fermer, name='myphotos-fermeture', daemon=True).start()

    def _veiller_relance(self):
        k32 = _dll('kernel32')
        while True:
            k32.WaitForSingleObject(self._evenement, 0xFFFFFFFF)
            if self._fin.is_set():
                return
            try:
                self.premier_plan()
            except Exception:
                journal.exception('premier plan')

    def premier_plan(self):
        """Relance de MyPhotos : la fenêtre ouverte revient devant ; en mode navigateur, la page s'ouvre."""
        for f in (self.webview, self.edge):
            if f is not None and f.active():
                f.premier_plan()
                return
        if self.app.mode == NAVIGATEUR:
            _ouvrir(self.adresse)

    def _terminer(self):
        """Ordre voulu : une relance pendant l'arrêt doit trouver soit MyPhotos « en arrêt » (/api/etat), soit le port
        libre, jamais un port tenu sans réponse. Les données de WebView2 sont effacées AVANT de libérer le port (une
        nouvelle instance les recréera aussitôt) ; app.fermer(), qui peut attendre la préparation du moteur, après."""
        self._fin.set()
        if self._evenement:
            k32 = _dll('kernel32')
            k32.SetEvent(self._evenement)        # le fil de relance se réveille et s'arrête
            k32.CloseHandle(self._evenement)
            self._evenement = None
        self.app.arreter()                       # sans effet s'il est déjà lancé
        if self._fil_serveur is not None:
            fin = time.monotonic() + 60
            while self._fil_serveur.is_alive() and time.monotonic() < fin:
                self._fil_serveur.join(0.5)
        if self.webview is not None:
            arreter_webview2_restants()           # normalement aucun : WebView2 s'arrête avec la fenêtre
            effacer_donnees_webview2()
        self.serveur.server_close()
        self.app.fermer()
        journal.info('%s arrêté', NOM)


# ---------------------------------------------------------------- mode « fenetre » : pywebview et WebView2

def _sans_pont(_plateforme, fenetre):
    """Remplace webview.util.inject_pywebview : aucun window.pywebview dans la page (aucun pont vers Python)."""
    fenetre.events.before_load.set()
    fenetre.events.loaded.set()


class FenetreWebView2:
    """La fenêtre « MyPhotos » : un formulaire WinForms de pywebview, avec WebView2 réglé par MyPhotos."""

    def __init__(self, hote):
        self.hote = hote
        self.fen = None                          # webview.Window
        self.form = None                         # formulaire WinForms (fil de la fenêtre)
        self.hwnd = 0
        self.placement = None
        self._pret = threading.Event()           # CoreWebView2 réglé, page demandée
        self._montree = threading.Event()
        self._fermee = threading.Event()
        self._echec = False
        self._programmee = False                 # fermeture décidée par MyPhotos : aucune question
        self._acceptee = False                   # FormClosing accepté : la fenêtre se ferme, MyPhotos s'arrête
        self._rechargements = deque()
        self._question_plantage = False          # question « Réessayer ? » affichée

    def active(self):
        return self.form is not None and self._montree.is_set() and not self._fermee.is_set()

    def lancer(self):
        """'fini' (fenêtre fermée, MyPhotos s'arrête) ou 'echec' (au mode suivant)."""
        etranger = config_clr_etrangere()
        if etranger:
            journal.warning('fichier %s posé à côté de MyPhotos (MyPhotos n\'en livre aucun) : .NET Framework n\'est '
                            'pas chargé, repli sur Edge', os.path.basename(etranger))
            return 'echec'
        version = version_webview2()
        if not version:
            journal.warning('WebView2 absent ou inutilisable : repli sur Edge')
            return 'echec'
        effacer_donnees_webview2()                 # restes d'un plantage : le mode privé repart de rien
        try:
            import webview
            import webview.platforms.winforms as winforms          # charge .NET Framework (pythonnet)
            if getattr(winforms, 'renderer', None) != 'edgechromium':
                journal.warning('pywebview n\'utiliserait pas WebView2 (%s) : repli sur Edge', winforms.renderer)
                return 'echec'
            import webview.platforms.edgechromium as moteur
        except Exception:
            journal.warning('fenêtre impossible (pywebview ou .NET Framework) : repli sur Edge', exc_info=True)
            return 'echec'
        journal.info('fenêtre MyPhotos : WebView2 %s', version)
        self._preparer_pywebview(webview, moteur)
        donnees = Path(config.DONNEES)
        self.placement = placer(lire_placement(donnees), ecrans())
        icone = Path(config.CODE) / 'construction' / 'MyPhotos.ico'
        ancien_choix = self.hote.app.choix_dossier
        self.hote.app.choix_dossier = ChoixDossierFenetre(self)
        sigint = signal.getsignal(signal.SIGINT)
        try:
            self.fen = webview.create_window(NOM, self.hote.adresse, width=LARGEUR, height=HAUTEUR,
                                             min_size=(LARGEUR_MIN, HAUTEUR_MIN), text_select=True, focus=True,
                                             background_color=FONDS['sombre' if theme_sombre() else 'clair'])
            self.fen.events.before_show += self._avant_affichage
            self.fen.events.shown += self._quand_montree
            self.fen.events.closed += self._fermee.set
            threading.Thread(target=self._surveiller, name='myphotos-surveillance', daemon=True).start()
            # DONNEES\webview2 : dossier dédié, effacé à la fermeture par pywebview (mode privé).
            webview.start(gui='edgechromium', debug=False, private_mode=True, storage_path=str(donnees / 'webview2'),
                          icon=str(icone) if icone.is_file() else None)
        except Exception:
            journal.exception('fenêtre MyPhotos')
            self._echec = True
        finally:
            self._fermee.set()
            try:
                signal.signal(signal.SIGINT, sigint)          # pywebview avait pris Ctrl+C pour sa fenêtre
            except (ValueError, TypeError):
                pass
        if self._echec:
            arreter_webview2_restants(1.0)                # WebView2 bloqué au démarrage : avant de passer à Edge
            self.hote.app.choix_dossier = ancien_choix
            return 'echec'
        return 'fini'

    def _preparer_pywebview(self, webview, moteur):
        reglages = webview.settings
        for cle, valeur in (('ALLOW_DOWNLOADS', False), ('ALLOW_FILE_URLS', False),
                            ('OPEN_EXTERNAL_LINKS_IN_BROWSER', False), ('OPEN_DEVTOOLS_IN_DEBUG', False),
                            ('REMOTE_DEBUGGING_PORT', None), ('WEBVIEW2_RUNTIME_PATH', None)):
            reglages[cle] = valeur
        moteur.inject_pywebview = _sans_pont
        fenetre = self

        def pret(ec, sender, args):              # remplace EdgeChrome.on_webview_ready
            fenetre._coeur_pret(ec, sender, args)
        moteur.EdgeChrome.on_webview_ready = pret
        # Journal de pywebview : avertissements et erreurs dans celui de MyPhotos.
        lp = logging.getLogger('pywebview')
        lp.setLevel(logging.DEBUG if self.hote.debug else logging.WARNING)
        lp.propagate = False
        for h in list(lp.handlers):
            lp.removeHandler(h)
        for f in list(lp.filters):
            if isinstance(f, _FiltreEffacement):
                lp.removeFilter(f)
        lp.addFilter(_FiltreEffacement())
        for h in logging.getLogger('myphotos').handlers:
            lp.addHandler(h)

    # -------- fil de la fenêtre (WinForms)

    def _coeur_pret(self, ec, sender, args):
        if not args.IsSuccess:
            if self._programmee or self._fermee.is_set():     # fenêtre déjà fermée par MyPhotos : démarrage abandonné
                journal.info('WebView2 : démarrage abandonné (%s)', args.InitializationException)
                return
            journal.warning('WebView2 n\'a pas démarré : %s', args.InitializationException)
            self._echouer(ec.form)
            return
        try:
            self._regler(ec, sender.CoreWebView2)
        except Exception:
            journal.exception('WebView2 : réglages impossibles')
            self._echouer(ec.form)                # jamais la page sans ses réglages
            return
        self._pret.set()
        ec.load_url(ec.pywebview_window.real_url)

    def _regler(self, ec, core):
        debug = self.hote.debug
        r = core.Settings
        r.AreDevToolsEnabled = debug
        r.AreBrowserAcceleratorKeysEnabled = debug    # impression, enregistrer, code source, F5, retour arrière…
        r.AreDefaultContextMenusEnabled = True        # réduit au texte par _menu
        r.AreDefaultScriptDialogsEnabled = True
        r.AreHostObjectsAllowed = False
        r.IsBuiltInErrorPageEnabled = True
        r.IsGeneralAutofillEnabled = False
        r.IsPasswordAutosaveEnabled = False
        r.IsPinchZoomEnabled = False
        r.IsScriptEnabled = True
        r.IsStatusBarEnabled = False
        r.IsSwipeNavigationEnabled = False
        r.IsWebMessageEnabled = False                 # aucun message de la page vers Python
        r.IsZoomControlEnabled = False
        ec.webview.AllowExternalDrop = False          # fichier ou lien glissé depuis ailleurs : refusé
        core.NavigationStarting += self._garde
        core.FrameNavigationStarting += self._garde_cadre
        core.NewWindowRequested += self._nouvelle_fenetre
        core.DownloadStarting += self._telechargement
        core.PermissionRequested += self._autorisation
        core.ProcessFailed += self._processus_arrete
        if not debug:
            core.ContextMenuRequested += self._menu
        if debug:
            journal.info('WebView2 %s, données %s (mode débogage)', core.Environment.BrowserVersionString,
                         core.Environment.UserDataFolder)

    def _echouer(self, form):
        self._echec = True
        self._programmee = True
        try:
            if form is None or form.IsDisposed or self._fermee.is_set():
                return
            from System import Action
            form.Opacity = 0                          # rien de visible avant la fermeture
            form.BeginInvoke(Action(form.Close))
        except Exception:
            journal.warning('fermeture de la fenêtre en échec', exc_info=True)

    def _avant_affichage(self):
        """Fenêtre créée, pas encore montrée : position, taille, question à la fermeture."""
        import System.Windows.Forms as WinForms
        from System.Drawing import Point, Size
        form = self.fen.native
        self.form = form
        self.hwnd = int(form.Handle.ToInt64())
        p = self.placement
        if p and not self._echec:
            form.StartPosition = WinForms.FormStartPosition.Manual
            form.Location = Point(int(p['x']), int(p['y']))
            form.Size = Size(int(p['l']), int(p['h']))
        form.FormClosing += self._fermeture

    def _quand_montree(self):
        self._montree.set()
        if self.placement and self.placement.get('maximisee') and not self._echec:
            try:
                self.fen.maximize()               # maximized=True à la création est ignoré (mesuré)
            except Exception:
                journal.debug('agrandissement impossible', exc_info=True)

    def _fermeture(self, _sender, args):
        import System.Windows.Forms as WinForms
        try:
            # La croix, Alt+F4 (UserClosing), « Fin de tâche » ou un autre programme (WM_CLOSE : TaskManagerClosing
            # pour WinForms) : on demande. Arrêt de Windows, Application.Exit (Ctrl+C) : jamais de question.
            raison = args.CloseReason
            sans_question = (raison == WinForms.CloseReason.WindowsShutDown
                             or raison == WinForms.CloseReason.ApplicationExitCall)
            if not self._programmee and not sans_question:
                texte = message_fermeture(self.hote.app.travaux_en_cours())
                if texte:
                    r = WinForms.MessageBox.Show(self.form, texte, NOM, WinForms.MessageBoxButtons.YesNo,
                                                 WinForms.MessageBoxIcon.Question,
                                                 WinForms.MessageBoxDefaultButton.Button1)
                    if r != WinForms.DialogResult.Yes:
                        args.Cancel = True
                        return
            self._acceptee = True
            if self._montree.is_set() and not self._echec:
                self._retenir_placement()
            journal.info('fenêtre fermée')
        except Exception:
            journal.exception('fermeture de la fenêtre')
        if self._acceptee and not self._echec:
            # Arrêt lancé dès maintenant (pywebview met encore quelques secondes à rendre la main) : une relance voit
            # aussitôt « arret » dans /api/etat et attend, au lieu de signaler une fenêtre qui n'existe plus.
            threading.Thread(target=self.hote.app.arreter, name='myphotos-arret-fenetre', daemon=True).start()

    def _retenir_placement(self):
        import System.Windows.Forms as WinForms
        form = self.form
        etat = form.WindowState
        b = form.Bounds if etat == WinForms.FormWindowState.Normal else form.RestoreBounds
        maximisee = etat == WinForms.FormWindowState.Maximized
        if etat == WinForms.FormWindowState.Minimized:
            wp = _WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(wp)
            if _dll('user32').GetWindowPlacement(self.hwnd, ctypes.byref(wp)):
                maximisee = bool(wp.flags & 0x2)          # WPF_RESTORETOMAXIMIZED
        dpi = _dll('user32').GetDpiForWindow(self.hwnd) or 96
        if b.Width > 0 and b.Height > 0:
            ecrire_placement(config.DONNEES, {'x': b.X, 'y': b.Y, 'l': b.Width, 'h': b.Height, 'dpi': int(dpi),
                                              'maximisee': maximisee})

    def _garde(self, _sender, args):
        uri = str(args.Uri)
        verdict = navigation(uri, self.hote.port)
        if verdict == 'permise':
            return
        args.Cancel = True
        if verdict == 'externe' and args.IsUserInitiated:
            ouvrir_lien_externe(uri)
        else:
            journal.info('navigation bloquée (%s)', urllib.parse.urlsplit(uri).scheme or '?')

    @staticmethod
    def _garde_cadre(_sender, args):
        args.Cancel = True                        # la page n'a aucun cadre (et sa CSP les refuse)

    def _nouvelle_fenetre(self, _sender, args):
        args.Handled = True                       # jamais de seconde fenêtre
        uri = str(args.Uri)
        if navigation(uri, self.hote.port) == 'externe' and args.IsUserInitiated:
            ouvrir_lien_externe(uri)

    @staticmethod
    def _telechargement(_sender, args):
        args.Cancel = True
        args.Handled = True

    @staticmethod
    def _autorisation(_sender, args):
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState
        args.State = CoreWebView2PermissionState.Deny

    @staticmethod
    def _menu(_sender, args):
        """Menu contextuel du texte seulement (champ de recherche : couper, copier, coller…)."""
        elements = args.MenuItems
        for i in range(elements.Count - 1, -1, -1):
            if str(elements[i].Name) not in MENU_TEXTE:
                elements.RemoveAt(i)                  # Remove(element) ne fait rien (mesuré)
        if elements.Count == 0:
            args.Handled = True

    def _processus_arrete(self, sender, args):
        genre = str(args.ProcessFailedKind)
        if genre in ('RenderProcessExited', 'RenderProcessUnresponsive', 'FrameRenderProcessExited'):
            t = time.monotonic()
            while self._rechargements and t - self._rechargements[0] > 120:
                self._rechargements.popleft()
            if self._question_plantage:
                return                            # la question est déjà posée
            if len(self._rechargements) < RECHARGEMENTS_MAX:
                self._rechargements.append(t)
                journal.warning('WebView2 : %s, la page est rechargée', genre)
                sender.Reload()
                return
            journal.error('WebView2 : %s à répétition', genre)
            self._question_plantage = True
            self._demander_reessai(sender)
        elif genre == 'BrowserProcessExited':
            journal.error('WebView2 s\'est arrêté : repli sur Edge')
            self._echec = True
            self.fermer()
        else:
            journal.warning('WebView2 : %s', genre)

    def _demander_reessai(self, sender):
        """Page tombée plus de RECHARGEMENTS_MAX fois en 2 minutes : la page d'erreur de WebView2 (« Redémarrer
        Microsoft Edge », code d'erreur) n'est pas pour la famille. Question de MyPhotos, après la fin de l'événement :
        « Oui » recharge (compteur remis à zéro), « Non » ferme MyPhotos."""
        import System.Windows.Forms as WinForms
        from System import Action

        def demander():
            try:
                r = WinForms.MessageBox.Show(self.form, PLANTAGES, NOM, WinForms.MessageBoxButtons.YesNo,
                                             WinForms.MessageBoxIcon.Warning, WinForms.MessageBoxDefaultButton.Button1)
                if r == WinForms.DialogResult.Yes:
                    journal.info('affichage relancé à la demande de l\'utilisateur')
                    self._rechargements.clear()
                    self._question_plantage = False
                    sender.Reload()
                else:
                    journal.info('fermeture demandée après des problèmes d\'affichage')
                    self.fermer()
            except Exception:
                journal.exception('question après les problèmes d\'affichage')
                self._question_plantage = False
        try:
            self.form.BeginInvoke(Action(demander))
        except Exception:
            journal.exception('question après les problèmes d\'affichage')
            self._question_plantage = False

    # -------- autres fils

    def _surveiller(self):
        if self._pret.wait(DELAI_WEBVIEW2_S) or self._fermee.is_set():
            return
        journal.warning('WebView2 ne répond pas après %d s : repli sur Edge', DELAI_WEBVIEW2_S)
        self._echec = True
        self.fermer()

    def fermer(self):
        """Fermeture décidée par MyPhotos (Arrêter MyPhotos, échec) : sans question, depuis n'importe quel fil."""
        self._programmee = True
        form = self.form
        if form is None or self._fermee.is_set() or self._acceptee:
            return
        try:
            from System import Action
            form.BeginInvoke(Action(form.Close))
        except Exception:
            journal.debug('fenêtre déjà fermée', exc_info=True)

    def premier_plan(self):
        form = self.form
        if form is None:
            return
        from System import Action

        def devant():
            ok = passer_devant(self.hwnd)
            form.Activate()
            journal.info('fenêtre ramenée au premier plan (%s)', 'devant' if ok else 'clignote')
        form.BeginInvoke(Action(devant))


class ChoixDossierFenetre:
    """« Choisir un dossier » : la fenêtre de Windows, ouverte par le fil de la fenêtre MyPhotos (fil STA de
    WinForms), lui appartient et la bloque tant qu'elle est ouverte. Une seule à la fois ; annuler renvoie None."""

    def __init__(self, fenetre):
        self.fenetre = fenetre
        self._verrou = threading.Lock()
        self._ouverte = False

    def demander(self, initial=None, delai=None, titre='Choisissez un dossier de photos'):
        from .serveur import Refus
        form = self.fenetre.form
        if form is None or not self.fenetre.active():
            raise Refus('La fenêtre de MyPhotos n\'est pas prête : réessayez dans un instant.', 503)
        with self._verrou:
            if self._ouverte:
                raise Refus('La fenêtre de choix d\'un dossier est déjà ouverte.', 409)
            self._ouverte = True
        try:
            from System import Func, Object
            from webview.platforms.winforms import OpenFolderDialog
            dossier = initial if isinstance(initial, str) and os.path.isdir(initial) else None
            r = form.Invoke(Func[Object](lambda: OpenFolderDialog.show(form, dossier, False, titre)))
        except Exception:
            journal.exception('fenêtre de choix de dossier')
            raise Refus('La fenêtre de choix d\'un dossier n\'a pas pu s\'ouvrir.', 503)
        finally:
            self._ouverte = False
        return str(r[0]) if r else None


# ---------------------------------------------------------------- mode « edge » : Edge en mode application

class FenetreEdge:
    """Repli : Edge en mode application, profil DONNEES\\edge. Sa fin = fenêtre fermée = arrêt de MyPhotos."""

    def __init__(self, hote):
        self.hote = hote
        self.proc = None
        self.debut = time.monotonic()
        self._fermee = threading.Event()

    def active(self):
        return self.proc is not None and not self._fermee.is_set()

    def lancer(self):
        """'fini', 'echec' (Edge absent ou impossible à lancer) ou 'perdue' (Edge a passé la main)."""
        edge = trouver_edge()
        if not edge:
            journal.warning('Edge introuvable : repli sur le navigateur par défaut')
            return 'echec'
        profil = Path(config.DONNEES) / EDGE_DOSSIER
        ligne = ligne_edge(edge, self.hote.adresse, profil, not profil.exists())
        self.debut = time.monotonic()
        try:
            self.proc = subprocess.Popen(ligne, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, close_fds=True)
        except OSError:
            journal.warning('Edge ne démarre pas : repli sur le navigateur par défaut', exc_info=True)
            return 'echec'
        self.hote.app.mode = EDGE
        choix = getattr(self.hote.app, 'choix_dossier', None)
        if choix is not None and hasattr(choix, 'zone'):
            choix.zone = self.zone                   # « Choisir un dossier » s'ouvre au milieu de cette fenêtre
        journal.info('page ouverte dans Edge, en mode application')
        try:
            while True:
                try:
                    self.proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            self._fermee.set()
        if time.monotonic() - self.debut < EDGE_PASSE_LA_MAIN_S and not self.hote._fin.is_set():
            journal.warning('Edge a passé la main à une autre instance : arrêt après 3 minutes sans page')
            return 'perdue'
        journal.info('fenêtre d\'Edge fermée')
        # Arrêt lancé tout de suite : une relance voit « arret » dans /api/etat et attend que le port soit libre.
        threading.Thread(target=self.hote.app.arreter, name='myphotos-arret-edge', daemon=True).start()
        return 'fini'

    def fermer(self):
        p = self.proc
        if p is None or p.poll() is not None:
            return
        for f in fenetres_edge(fenetres_du_processus(p.pid)):
            _dll('user32').PostMessageW(f['hwnd'], 0x0010, 0, 0)    # WM_CLOSE
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            journal.warning('Edge ne se ferme pas : arrêt de son processus')
            p.terminate()                                           # notre processus Edge, par son identifiant

    def zone(self):
        """(gauche, haut, droite, bas) de la fenêtre d'Edge qui montre MyPhotos, ou None : serveur.ChoixDossier y
        pose sa fenêtre hôte (la fenêtre d'Edge, d'un autre processus, n'en est jamais la propriétaire)."""
        p = self.proc
        if p is None or p.poll() is not None:
            return None
        fenetres = fenetres_edge(fenetres_du_processus(p.pid))
        r = wintypes.RECT()
        if not fenetres or not _dll('user32').GetWindowRect(fenetres[0]['hwnd'], ctypes.byref(r)):
            return None
        return r.left, r.top, r.right, r.bottom

    def premier_plan(self):
        p = self.proc
        if p is None:
            return
        fenetres = fenetres_edge(fenetres_du_processus(p.pid))
        if fenetres:
            passer_devant(fenetres[0]['hwnd'])
            journal.info('fenêtre d\'Edge ramenée au premier plan')
