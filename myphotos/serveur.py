"""Serveur local de MyPhotos : la page (page/index.html) et son API JSON, sur 127.0.0.1 uniquement.

    python -m myphotos          (ou MyPhotos.exe, ou python lancer.py)

Une seule instance : si le port est déjà pris par MyPhotos, on ouvre la page et on s'arrête. La page interroge
/api/etat toutes les 2 s (toutes les 30 s quand elle est cachée) ; sans aucune requête pendant 3 minutes, le
serveur s'arrête proprement (fin du lot d'analyse en cours, fermeture des bases).

Sécurité : n'importe quel site ouvert dans le navigateur peut viser 127.0.0.1. D'où :
- Host obligatoire : 127.0.0.1:PORT ou localhost:PORT (parade au « DNS rebinding ») ;
- POST : en-tête X-MyPhotos: 1 (un autre site ne peut pas le poser sans autorisation CORS, jamais donnée),
  corps JSON de 64 Ko au plus, Origin identique à l'adresse du serveur quand elle est présente ;
- GET des données (/api/, /miniature/, /photo/) refusés quand Sec-Fetch-Site désigne un autre site ;
- CSP stricte (nonce, aucune ressource externe), nosniff, no-referrer, no-store sur l'API ;
- photos servies, ouvertes ou montrées uniquement par un identifiant présent en base, et seulement si leur
  extension est celle d'une image ; aucun chemin de fichier n'est jamais lu dans l'URL.

Variables d'environnement : MYPHOTOS_PORT, MYPHOTOS_DOSSIER, MYPHOTOS_MODELES (voir config.py) ;
MYPHOTOS_SANS_PAGE=1 n'ouvre pas la page ; MYPHOTOS_TELECHARGEMENT_FACTICE=1 simule le téléchargement du
moteur (essais de l'interface ; les fichiers réels de MYPHOTOS_MODELES servent ensuite) ;
MYPHOTOS_INACTIVITE_S change le délai d'arrêt automatique (180 s).
"""
import concurrent.futures
import ctypes
import errno
import gc
import hashlib
import http.client
import http.server
import io
import json
import logging
import os
import queue
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import zlib
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config, materiel, modele
from . import recherche as _recherche
from .base import Base, DossierRefuse, chemin_affiche, chemin_systeme, cle_chemin, dedans
from .indexation import Indexeur, Parcours
from .recherche import Recherche

NOM = 'MyPhotos'
journal = logging.getLogger(__name__)

INACTIVITE_S = float(os.environ.get('MYPHOTOS_INACTIVITE_S') or 180)
SANS_PAGE = bool(os.environ.get('MYPHOTOS_SANS_PAGE'))
FACTICE = os.environ.get('MYPHOTOS_TELECHARGEMENT_FACTICE') == '1'
DUREE_FACTICE_S = float(os.environ.get('MYPHOTOS_FACTICE_S') or 8)

EN_TETE = 'X-MyPhotos'
CORPS_MAX = 64 * 1024              # corps JSON d'un POST
LISTE_MAX = 500                    # photos par page de résultats
LISTE_DEFAUT = 150
INVENTAIRE_AUTO_S = 3600           # nouvel inventaire des dossiers toutes les heures tant que MyPhotos tourne
JOURNAL_OCTETS = 1_000_000         # journal borné : 3 fichiers de 1 Mo au plus
JOURNAL_COPIES = 2
ESTIMATION_S = 4.0                 # temps accordé au comptage des photos d'un dossier proposé
MATERIEL_VALIDITE_S = 600
COTE_CONVERSION = 2560             # TIFF : converti en JPEG de 2560 px au plus pour la visionneuse
MARGE_DISQUE = 200 * 1024 * 1024
CHOIX_DOSSIER_S = 900              # la fenêtre « Choisir un dossier » attend au plus 15 minutes
ORDRE_MOTEURS = ('rapide', 'base', 'precis', 'tres_precis')

# Images que le navigateur affiche telles quelles ; les autres extensions de config.EXTENSIONS (TIFF) sont
# converties en JPEG à la volée.
TYPES_IMAGES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jfif': 'image/jpeg', '.png': 'image/png',
                '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'}

PHOTO_ABSENTE = ("La photo d'origine est introuvable : le disque est peut-être débranché, ou le fichier a été "
                 "déplacé.")
_IDENTIFIANT = re.compile(r'[1-9][0-9]{0,17}')
_FOLDERID_PICTURES = '{33E28130-4E1E-4676-835A-98395C3BC3BB}'

# Le public ne connaît ni « modèle » ni « indexation » : on parle de moteur de reconnaissance et d'analyse.
_JARGON = ((re.compile(r'\bmodèles\b'), 'moteurs'), (re.compile(r'\bmodèle\b'), 'moteur'),
           (re.compile(r'\bModèles\b'), 'Moteurs'), (re.compile(r'\bModèle\b'), 'Moteur'),
           (re.compile(r'\bindexation\b'), 'analyse'), (re.compile(r'\bIndexation\b'), 'Analyse'),
           # Ancien nom du logiciel, encore présent dans quelques messages du cœur.
           (re.compile(r'\b(de|à) la [Pp]hotothèque\b'), r'\1 MyPhotos'),
           (re.compile(r'\bla [Pp]hotothèque\b'), 'MyPhotos'), (re.compile(r'\b[Pp]hotothèque\b'), 'MyPhotos'))


class Refus(Exception):
    """Requête refusée ; le message, en français simple, s'affiche tel quel dans la page."""

    def __init__(self, message, statut=400):
        super().__init__(message)
        self.statut = statut


class PlaceInsuffisante(OSError):
    pass


class MoteurPartage:
    """Moteur commun à l'analyse et à la recherche, gardé en vie par le serveur, appels à la carte graphique
    passés un par un.

    Contournement de deux bogues d'ONNX Runtime 1.24.4 avec DirectML (reproduits le 23/11/2026, sans le serveur) :
    - deux sessions DirectML qui calculent en même temps depuis deux fils (vision pendant l'analyse, texte pendant
      une recherche) : violation d'accès ;
    - une session DirectML détruite alors qu'une session plus ancienne vit encore (l'indexeur libère son moteur
      à la fin de chaque passage, puis le recrée) : la plus ancienne plante à son appel suivant.
    Avec un seul moteur (vision créée avant texte), jamais libéré avant le changement de moteur, et un verrou sur
    les calculs faits par la carte graphique, aucun des deux cas ne se produit.
    """

    def __init__(self, moteur, verrou):
        self._moteur = moteur
        self._verrou = verrou

    def __getattr__(self, nom):
        return getattr(self._moteur, nom)

    def vecteurs_images(self, lot):
        if self._moteur.materiel == 'carte graphique':
            with self._verrou:
                return self._moteur.vecteurs_images(lot)
        return self._moteur.vecteurs_images(lot)

    def vecteurs_textes(self, textes):
        if self._moteur.materiel_texte == 'carte graphique':
            with self._verrou:
                return self._moteur.vecteurs_textes(textes)
        return self._moteur.vecteurs_textes(textes)


def sans_jargon(texte):
    if not isinstance(texte, str):
        return texte
    for motif, mot in _JARGON:
        texte = motif.sub(mot, texte)
    return texte


def _nombre_fr(x):
    """32.0 -> « 32 », 7.96 -> « 8 », 2.5 -> « 2,5 »."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return '?'
    if x >= 9.5 or abs(x - round(x)) < 0.05:
        return f'{round(x):,}'.replace(',', ' ')
    return f'{x:.1f}'.replace('.', ',')


# ---------------------------------------------------------------- journal

def configurer_journal():
    """Journal borné (config.JOURNAL, rotation à 1 Mo, 2 copies) ; console seulement depuis les sources."""
    racine = logging.getLogger('myphotos')
    if getattr(racine, '_myphotos', False):
        return
    racine._myphotos = True
    racine.setLevel(logging.INFO)
    racine.propagate = False
    forme = logging.Formatter('%(asctime)s %(levelname)-7s %(name)s : %(message)s', '%Y-%m-%d %H:%M:%S')
    try:
        config.JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        f = RotatingFileHandler(config.JOURNAL, maxBytes=JOURNAL_OCTETS, backupCount=JOURNAL_COPIES,
                                encoding='utf-8', delay=True)
        f.setFormatter(forme)
        racine.addHandler(f)
    except OSError:
        pass
    if sys.stderr is not None and not config.GELE:
        c = logging.StreamHandler()
        c.setLevel(logging.WARNING)
        c.setFormatter(forme)
        racine.addHandler(c)

    def crochet(args):
        if args.exc_type is SystemExit:
            return
        journal.error('erreur dans le fil %s', getattr(args.thread, 'name', '?'),
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = crochet


# ---------------------------------------------------------------- Windows

_DLL = {}


def _dll(nom):
    """Instance privée d'une DLL : nos argtypes ne touchent pas ceux des autres modules."""
    if nom not in _DLL:
        d = ctypes.WinDLL(nom, use_last_error=True)
        v, u, b = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        signatures = {
            'user32': {'GetForegroundWindow': ([], v), 'GetWindowThreadProcessId': ([v, v], u),
                       'AttachThreadInput': ([u, u, b], b), 'SetForegroundWindow': ([v], b),
                       'BringWindowToTop': ([v], b), 'GetAncestor': ([v, u], v),
                       'MessageBoxW': ([v, ctypes.c_wchar_p, ctypes.c_wchar_p, u], b)},
            'kernel32': {'GetCurrentThreadId': ([], u)},
            'shell32': {'SHGetKnownFolderPath': ([v, u, v, ctypes.POINTER(v)], ctypes.c_long),
                        'ILCreateFromPathW': ([ctypes.c_wchar_p], v), 'ILFree': ([v], None),
                        'SHOpenFolderAndSelectItems': ([v, u, v, u], ctypes.c_long)},
            'ole32': {'CoTaskMemFree': ([v], None), 'CoInitializeEx': ([v, u], ctypes.c_long),
                      'CoUninitialize': ([], None)},
        }[nom]
        for fonction, (arguments, retour) in signatures.items():
            f = getattr(d, fonction)
            f.argtypes, f.restype = arguments, retour
        _DLL[nom] = d
    return _DLL[nom]


def dossier_images():
    """Dossier « Images » de l'utilisateur (FOLDERID_Pictures, redirigé vers OneDrive le cas échéant)."""
    if os.name != 'nt':
        p = Path.home() / 'Pictures'
        return str(p) if p.is_dir() else None
    try:
        import uuid
        guid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(_FOLDERID_PICTURES).bytes_le)
        sortie = ctypes.c_void_p()
        if _dll('shell32').SHGetKnownFolderPath(ctypes.addressof(guid), 0, None, ctypes.byref(sortie)) != 0:
            return None
        try:
            return ctypes.wstring_at(sortie.value) if sortie.value else None
        finally:
            _dll('ole32').CoTaskMemFree(sortie)
    except Exception:
        journal.debug('dossier Images introuvable', exc_info=True)
        return None


def dossiers_proposes():
    """Dossiers proposés au premier lancement : Images, puis OneDrive\\Images s'il existe (sans doublon)."""
    vus, res = set(), []

    def ajouter(chemin, nom):
        if not chemin:
            return
        p = os.path.normpath(chemin)
        try:
            if not os.path.isabs(p) or not os.path.isdir(chemin_systeme(p)):
                return
        except (OSError, ValueError):
            return
        k = cle_chemin(p)
        if k not in vus:
            vus.add(k)
            res.append({'chemin': p, 'nom': nom})

    ajouter(dossier_images(), 'Images')
    for variable in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        racine = os.environ.get(variable)
        if racine:
            for sous in ('Images', 'Pictures'):
                ajouter(os.path.join(racine, sous), 'Images de OneDrive')
    return res


def compter_images(racine, limite_s=ESTIMATION_S):
    """Nombre d'images sous un dossier, sans en ouvrir aucune : (nombre, complet). S'arrête au bout de limite_s."""
    n, debut = 0, time.monotonic()
    try:
        for _ in Parcours(racine):
            n += 1
            if n % 256 == 0 and time.monotonic() - debut > limite_s:
                return n, False
    except OSError:
        return None, False
    return n, True


def _premier_plan(fenetre):
    """Amène une fenêtre tkinter au premier plan, alors que c'est le navigateur qui a le clavier."""
    if os.name != 'nt':
        return
    try:
        user32 = _dll('user32')
        hwnd = user32.GetAncestor(fenetre.winfo_id(), 2) or fenetre.winfo_id()      # GA_ROOT
        devant = user32.GetForegroundWindow()
        fil_devant = user32.GetWindowThreadProcessId(devant, None) if devant else 0
        moi = _dll('kernel32').GetCurrentThreadId()
        attache = bool(fil_devant and fil_devant != moi and user32.AttachThreadInput(moi, fil_devant, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attache:
                user32.AttachThreadInput(moi, fil_devant, False)
    except Exception:
        journal.debug('premier plan impossible', exc_info=True)


def montrer_dans_dossier(chemin):
    """Ouvre l'Explorateur sur le dossier de la photo, la photo sélectionnée."""
    if os.name != 'nt':
        raise OSError('Explorateur Windows indisponible')
    try:
        shell32, ole32 = _dll('shell32'), _dll('ole32')
        init = ole32.CoInitializeEx(None, 0x2)                       # COINIT_APARTMENTTHREADED
        try:
            pidl = shell32.ILCreateFromPathW(chemin)
            if not pidl:
                raise OSError('chemin refusé par l\'Explorateur')
            try:
                hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
                if hr != 0:
                    raise OSError(f'SHOpenFolderAndSelectItems : {hr & 0xFFFFFFFF:#x}')
            finally:
                shell32.ILFree(pidl)
        finally:
            if init in (0, 1):
                ole32.CoUninitialize()
    except OSError:
        journal.info('sélection dans l\'Explorateur impossible, essai avec explorer /select', exc_info=True)
        subprocess.Popen(f'explorer.exe /select,"{chemin}"')


def alerter(message):
    """Message d'erreur de démarrage : boîte de dialogue (l'exécutable n'a pas de console) et journal."""
    journal.error('%s', message)
    if SANS_PAGE or os.name != 'nt' or not config.GELE:
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return
    try:
        _dll('user32').MessageBoxW(None, message, NOM, 0x10 | 0x10000 | 0x40000)  # erreur, premier plan, dessus
    except Exception:
        pass


def ouvrir_page(adresse):
    try:
        os.startfile(adresse)
    except (AttributeError, OSError):
        import webbrowser
        webbrowser.open(adresse)


# ---------------------------------------------------------------- choix d'un dossier (tkinter)

class ChoixDossier:
    """Fenêtre « Choisir un dossier » de Windows, ouverte par tkinter dans un fil qui lui est réservé
    (Tk n'accepte qu'un seul fil). Une seule fenêtre à la fois ; annuler renvoie None."""

    def __init__(self):
        self._demandes = queue.Queue()
        self._fil = None
        self._ouverte = threading.Event()
        self._verrou = threading.Lock()

    def demander(self, initial=None, delai=CHOIX_DOSSIER_S):
        with self._verrou:
            if self._ouverte.is_set():
                raise Refus("La fenêtre de choix d'un dossier est déjà ouverte : regardez dans la barre des "
                            "tâches, en bas de l'écran.", 409)
            self._ouverte.set()
            if self._fil is None or not self._fil.is_alive():
                self._fil = threading.Thread(target=self._boucle, name='myphotos-choix-dossier', daemon=True)
                self._fil.start()
        reponse = concurrent.futures.Future()
        self._demandes.put((reponse, initial))
        try:
            return reponse.result(timeout=delai)
        except concurrent.futures.TimeoutError:
            return None

    def _boucle(self):
        racine = None
        while True:
            reponse, initial = self._demandes.get()
            resultat = erreur = None
            try:
                import tkinter
                from tkinter import filedialog
                if racine is None:
                    racine = tkinter.Tk()
                    racine.withdraw()
                    icone = config.CODE / 'construction' / 'MyPhotos.ico'
                    if icone.is_file():
                        try:
                            racine.iconbitmap(default=str(icone))
                        except Exception:
                            pass
                # Fenêtre hôte invisible, toujours au-dessus : la fenêtre de choix, qui lui appartient, l'est aussi.
                hote = tkinter.Toplevel(racine)
                hote.title(NOM)
                hote.overrideredirect(True)
                hote.attributes('-alpha', 0.0)
                hote.attributes('-topmost', True)
                hote.geometry(f'1x1+{hote.winfo_screenwidth() // 2 - 320}+{hote.winfo_screenheight() // 2 - 240}')
                hote.deiconify()
                hote.update()
                _premier_plan(hote)
                try:
                    chemin = filedialog.askdirectory(parent=hote, title='Choisissez un dossier de photos',
                                                     initialdir=initial or None, mustexist=True)
                finally:
                    hote.destroy()
                    racine.update()
                resultat = chemin if isinstance(chemin, str) and chemin else None
            except Exception as e:
                journal.exception('fenêtre de choix de dossier')
                erreur = e
                try:
                    if racine is not None:
                        racine.destroy()
                except Exception:
                    pass
                racine = None
            # La fenêtre est fermée avant que la réponse parte : la demande suivante est acceptée.
            self._ouverte.clear()
            if erreur is not None:
                reponse.set_exception(erreur)
            else:
                reponse.set_result(resultat)


# ---------------------------------------------------------------- matériel : résumé lisible

def _nom_processeur(nom):
    n = re.sub(r'\((R|TM|tm|r)\)', '', nom)
    n = re.sub(r'\s+@.*$', '', n)
    n = re.sub(r'\b\d+-Core\b|\bProcessor\b|\bCPU\b|\bwith Radeon Graphics\b', '', n, flags=re.I)
    return re.sub(r'\s+', ' ', n).strip() or nom


def resume_materiel(a):
    """Quelques lignes {libelle, valeur} sur l'ordinateur, pour la page."""
    a = a if isinstance(a, dict) else {}
    lignes = []
    s = a.get('systeme') or {}
    if s.get('edition') or s.get('nom'):
        lignes.append(('Système', s.get('edition') or s.get('nom')))
    p = a.get('processeur') or {}
    if p.get('nom'):
        v = _nom_processeur(p['nom'])
        if p.get('coeurs'):
            v += f" ({p['coeurs']} cœurs)"
        lignes.append(('Processeur', v))
    m = a.get('memoire') or {}
    go = m.get('installee_go') or m.get('totale_go')
    if go:
        lignes.append(('Mémoire', f'{_nombre_fr(go)} Go'))
    cartes = [c for c in (a.get('cartes') or []) if isinstance(c, dict) and not c.get('logicielle')]
    if cartes:
        noms = []
        for c in cartes:
            v = c.get('nom') or 'carte graphique'
            if (c.get('memoire_dediee_go') or 0) >= 1:
                v += f" ({_nombre_fr(c['memoire_dediee_go'])} Go)"
            noms.append(v)
        lignes.append(('Carte graphique', ', '.join(noms)))
    elif a.get('cartes') is not None:
        lignes.append(('Carte graphique', 'aucune carte graphique utilisable'))
    d = a.get('disque') or {}
    if d.get('libre_go') is not None:
        lignes.append(('Place libre', f"{_nombre_fr(d['libre_go'])} Go sur le disque {d.get('lecteur') or ''}".strip()))
    return [{'libelle': l, 'valeur': v} for l, v in lignes]


# ---------------------------------------------------------------- téléchargement du moteur

def message_telechargement(e):
    if isinstance(e, PlaceInsuffisante):
        return str(e)
    if isinstance(e, urllib.error.HTTPError):
        return (f"Le site de téléchargement a répondu par une erreur ({e.code}). Réessayez un peu plus tard : "
                "le téléchargement reprendra où il en était.")
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)) or \
            e.__class__.__name__.startswith('SSL'):
        return ("Impossible de télécharger le moteur : vérifiez que l'ordinateur est connecté à Internet, puis "
                "réessayez. Le téléchargement reprendra où il en était.")
    if isinstance(e, OSError) and e.errno == errno.ENOSPC:
        return 'Le disque est plein : libérez de la place, puis réessayez.'
    if 'empreinte' in str(e):
        return 'Un fichier reçu était abîmé. Réessayez : seul ce fichier sera téléchargé de nouveau.'
    return "Le téléchargement n'a pas abouti. Réessayez : il reprendra où il en était."


class Telechargement:
    """Téléchargement d'un moteur dans un fil d'arrière-plan, ou recopie depuis le dossier « modele » posé à côté
    de MyPhotos.exe (empreintes contrôlées). Annulable ; un fichier interrompu reprend où il en était (.part)."""

    def __init__(self, app, cle, vision, texte, materiel_pref, activer):
        self.app = app
        self.cle, self.vision, self.texte = cle, vision, texte
        self.materiel_pref = materiel_pref
        self.activer = activer
        self.arret = threading.Event()
        self.etat = 'en_cours'                   # en_cours, fini, annule, erreur
        self.mode = 'preparation'                # preparation, copie, telechargement
        self.message = None
        self.fait = 0
        self.total = 0
        self._verrou = threading.Lock()
        self._mesures = deque()
        self.fil = threading.Thread(target=self._executer, name='myphotos-telechargement', daemon=True)

    # -------- progression

    def _progression(self, fait, total, nom=''):
        t = time.monotonic()
        with self._verrou:
            self.fait, self.total = int(fait), int(total)
            self._mesures.append((t, self.fait))
            while len(self._mesures) > 2 and t - self._mesures[0][0] > 6.0:
                self._mesures.popleft()

    def resume(self):
        with self._verrou:
            fait, total, mesures = self.fait, self.total, list(self._mesures)
        vitesse = None
        if len(mesures) >= 2 and mesures[-1][0] - mesures[0][0] >= 1.0 and \
                time.monotonic() - mesures[-1][0] < 4.0:
            vitesse = max(0.0, (mesures[-1][1] - mesures[0][1]) / (mesures[-1][0] - mesures[0][0]))
        restant = (total - fait) / vitesse if vitesse else None
        return {'cle': self.cle, 'nom': modele.info(self.cle).get('nom') or self.cle, 'vision': self.vision,
                'etat': self.etat, 'mode': self.mode, 'fait': fait, 'total': total,
                'pourcent': round(100 * fait / total, 1) if total else (100.0 if self.etat == 'fini' else 0.0),
                'vitesse': round(vitesse) if vitesse else None, 'restant_s': round(restant) if restant else None,
                'restant_texte': materiel.formater_duree(restant) if restant else None,
                'message': self.message, 'activer': self.activer}

    # -------- travail

    def _executer(self):
        try:
            noms = modele.fichiers_necessaires(self.cle, self.vision, self.texte)
            if FACTICE:
                self._simuler(noms)
            else:
                self._recopier(noms)
                manque = modele.manquants(self.cle, noms)
                if manque:
                    self._verifier_place(manque)
                    self.mode = 'telechargement'
                    taille = sum(modele.info(self.cle)['fichiers'][n][0] for n in manque)
                    self._progression(0, taille)
                    with self._verrou:
                        self._mesures.clear()
                    modele.telecharger(self.cle, manque, self._progression, self.arret)
                if modele.manquants(self.cle, noms):
                    raise IOError('fichiers du moteur incomplets')
            self.etat = 'fini'
            journal.info('moteur %s (%s) prêt', self.cle, self.vision)
            self.app._telechargement_fini(self)
        except InterruptedError:
            self.etat = 'annule'
            journal.info('téléchargement du moteur %s annulé', self.cle)
        except Exception as e:
            journal.warning('téléchargement du moteur %s : %r', self.cle, e, exc_info=True)
            self.message = message_telechargement(e)
            self.etat = 'erreur'

    def _verifier_place(self, noms):
        besoin = sum(modele.info(self.cle)['fichiers'][n][0] for n in noms) + MARGE_DISQUE
        dossier = modele.dossier_modele(self.cle)
        dossier.mkdir(parents=True, exist_ok=True)
        libre = shutil.disk_usage(dossier).free
        if libre < besoin:
            raise PlaceInsuffisante(
                f"Il n'y a pas assez de place sur le disque : il faut {materiel.formater_taille(besoin)}, il reste "
                f"{materiel.formater_taille(libre)}. Libérez de la place, puis réessayez.")

    def _recopier(self, noms):
        """Installation sans Internet : recopie le moteur depuis config.MODELE_A_COTE, empreintes contrôlées."""
        source = Path(config.MODELE_A_COTE) / self.cle
        try:
            if not source.is_dir() or source.resolve() == modele.dossier_modele(self.cle).resolve():
                return
        except OSError:
            return
        fiches = modele.info(self.cle)['fichiers']
        a_copier = [n for n in modele.manquants(self.cle, noms)
                    if (source / n).is_file() and (source / n).stat().st_size == fiches[n][0]]
        if not a_copier:
            return
        self.mode = 'copie'
        total, deja = sum(fiches[n][0] for n in a_copier), 0
        self._progression(0, total)
        for nom in a_copier:
            taille, empreinte = fiches[nom]
            dest = modele.dossier_modele(self.cle) / nom
            dest.parent.mkdir(parents=True, exist_ok=True)
            provisoire = dest.with_name(dest.name + '.copie')
            h = hashlib.sha256()
            with open(source / nom, 'rb') as f, open(provisoire, 'wb') as g:
                fait = 0
                while True:
                    if self.arret.is_set():
                        raise InterruptedError('recopie interrompue')
                    bloc = f.read(4 << 20)
                    if not bloc:
                        break
                    h.update(bloc)
                    g.write(bloc)
                    fait += len(bloc)
                    self._progression(deja + fait, total, nom)
            if h.hexdigest() != empreinte:
                journal.warning('recopie de %s : empreinte inattendue, le fichier sera téléchargé', nom)
                provisoire.unlink(missing_ok=True)
            else:
                os.replace(provisoire, dest)
                dest.with_name(dest.name + '.ok').write_text(empreinte, encoding='ascii')
            deja += taille

    def _simuler(self, noms):
        """MYPHOTOS_TELECHARGEMENT_FACTICE=1 : progression simulée, sans réseau, pour essayer l'interface."""
        self.mode = 'telechargement'
        total = sum(modele.info(self.cle)['fichiers'][n][0] for n in noms)
        debut = time.monotonic()
        while True:
            if self.arret.is_set():
                raise InterruptedError('téléchargement simulé interrompu')
            x = min(1.0, (time.monotonic() - debut) / max(0.1, DUREE_FACTICE_S))
            self._progression(int(total * x), total, '')
            if x >= 1.0:
                break
            time.sleep(0.2)
        if modele.manquants(self.cle, noms):
            raise IOError('mode d\'essai : les fichiers de ce moteur sont absents de MYPHOTOS_MODELES')
        self.app.factices.add((self.cle, self.vision))


# ---------------------------------------------------------------- application

class Application:
    """État de MyPhotos : base, moteur choisi, analyse, recherche, téléchargement, arrêt automatique."""

    def __init__(self, serveur=None):
        self.serveur = serveur
        self.base = Base(config.BASE, config.MINIATURES)
        self.verrou = threading.RLock()
        self.moteur = None                       # {cle, vision, texte, materiel}
        self.indexeur = None
        self.recherche = None
        self.telechargement = None
        self.changement = None                   # {cle, nom} pendant un changement de moteur
        self.factices = set()                    # (cle, vision) « téléchargés » en mode d'essai
        self.choix_dossier = ChoixDossier()
        self.derniere_activite = time.monotonic()
        self.arret_lance = False
        self._ferme = False
        self._fin = threading.Event()
        self._proposes = None
        self._estimations = {}
        self._estimation_finie = threading.Event()
        self._materiel = None
        self._verrou_materiel = threading.Lock()
        self._nb_dossiers = 0
        self._partage = None                     # (clé, MoteurPartage) du moteur actif
        self._verrou_partage = threading.Lock()
        self._verrou_carte = threading.Lock()    # un seul calcul à la fois sur la carte graphique

    # ------------------------------------------------ démarrage et arrêt

    def demarrer(self):
        self._nb_dossiers = len(self.base.dossiers())
        cle = self.base.meta_get('moteur')
        if isinstance(cle, str) and cle in modele.MODELES_DISPO:
            try:
                cle, vision, texte = self._variante(cle, self.base.meta_get('moteur_vision'))
                materiel_pref = self.base.meta_get('moteur_materiel') or 'auto'
                if FACTICE:
                    self.factices.add((cle, vision))
                if self.pret(cle, vision):
                    self._activer(cle, vision, texte, materiel_pref)
                else:
                    journal.warning('moteur %s : fichiers absents, à télécharger de nouveau', cle)
            except Refus as e:
                journal.warning('moteur enregistré inutilisable : %s', e)
        # Un téléchargement interrompu par l'arrêt précédent reprend tout seul.
        reprise = self._json(self.base.meta_get('telechargement'))
        if reprise:
            try:
                self.telecharger(reprise.get('cle'), reprise.get('vision'), reprise.get('materiel'),
                                 bool(reprise.get('activer', True)))
            except Refus:
                self.base.meta_set('telechargement', None)
        threading.Thread(target=self._estimer, name='myphotos-estimation', daemon=True).start()
        if self.base.meta_get('assistant') != 'fini':
            threading.Thread(target=self._materiel_en_avance, name='myphotos-materiel', daemon=True).start()
        threading.Thread(target=self._veiller, name='myphotos-veille', daemon=True).start()

    def activite(self):
        self.derniere_activite = time.monotonic()

    def _veiller(self):
        while not self._fin.wait(2.0):
            if time.monotonic() - self.derniere_activite > INACTIVITE_S:
                journal.info('aucune activité depuis %d s : arrêt', INACTIVITE_S)
                self.arreter()
                return

    def arreter(self):
        """Arrêt propre en arrière-plan : fin du lot d'analyse en cours, puis arrêt du serveur."""
        with self.verrou:
            if self.arret_lance:
                return
            self.arret_lance = True

        def travail():
            try:
                t = self.telechargement
                if t is not None:
                    t.arret.set()
                ix = self.indexeur
                if ix is not None:
                    ix.arreter(attendre=True)
            except Exception:
                journal.exception('arrêt de l\'analyse')
            finally:
                if self.serveur is not None:
                    self.serveur.shutdown()
        threading.Thread(target=travail, name='myphotos-arret', daemon=True).start()

    def fermer(self):
        with self.verrou:
            if self._ferme:
                return
            self._ferme = True
            self.arret_lance = True
        self._fin.set()
        t = self.telechargement
        if t is not None:
            t.arret.set()
        ix = self.indexeur
        if ix is not None:
            try:
                ix.arreter(attendre=True)
            except Exception:
                journal.exception('arrêt de l\'analyse')
        if t is not None and t.fil.is_alive():
            t.fil.join(timeout=10)
        self._liberer_moteurs()                  # dans un ordre sûr, plutôt qu'au hasard de la sortie de Python
        self.base.fermer()

    @staticmethod
    def _json(texte):
        try:
            d = json.loads(texte) if isinstance(texte, str) and texte else None
            return d if isinstance(d, dict) else None
        except ValueError:
            return None

    # ------------------------------------------------ moteur

    @staticmethod
    def _variante(cle, vision=None):
        if not isinstance(cle, str) or cle not in modele.MODELES_DISPO:
            raise Refus('Ce moteur de reconnaissance est inconnu.')
        fiche = modele.info(cle)
        vision = vision or ('fp16' if 'fp16' in fiche['vision'] else next(iter(fiche['vision'])))
        if not isinstance(vision, str) or vision not in fiche['vision']:
            raise Refus('Cette version du moteur de reconnaissance est inconnue.')
        texte = modele.TEXTE_DEFAUT if modele.TEXTE_DEFAUT in fiche['texte'] else next(iter(fiche['texte']))
        return cle, vision, texte

    def _noms(self, cle, vision):
        _, vision, texte = self._variante(cle, vision)
        return modele.fichiers_necessaires(cle, vision, texte)

    def pret(self, cle, vision=None):
        """Tous les fichiers du moteur sont là et vérifiés."""
        cle, vision, _ = self._variante(cle, vision)
        if FACTICE and (cle, vision) not in self.factices:
            return False
        return not modele.manquants(cle, self._noms(cle, vision))

    def reste(self, cle, vision=None):
        """Octets restant à télécharger pour ce moteur."""
        cle, vision, texte = self._variante(cle, vision)
        if FACTICE and (cle, vision) not in self.factices:
            return sum(modele.info(cle)['fichiers'][n][0] for n in self._noms(cle, vision))
        return modele.taille_telechargement(cle, vision, texte)

    def _moteur_commun(self, cle, vision, texte, prefere):
        """Le moteur (vision + texte) du moteur actif, chargé une fois au premier besoin (voir MoteurPartage)."""
        cle_partage = (cle, vision, texte, prefere)
        with self._verrou_partage:
            if self._partage is not None and self._partage[0] == cle_partage:
                return self._partage[1]
            m = MoteurPartage(modele.Moteur(cle, vision=vision, texte=texte, materiel=prefere), self._verrou_carte)
            self._partage = (cle_partage, m)
            journal.info('moteur %s chargé : images sur %s, texte sur %s', cle, m.materiel, m.materiel_texte)
            return m

    def _liberer_moteurs(self):
        """Libère l'analyse, la recherche et leur moteur commun d'un coup (changement de moteur, arrêt)."""
        self.indexeur = None
        self.recherche = None
        with self._verrou_partage:
            self._partage = None
        gc.collect()

    def _activer(self, cle, vision, texte, materiel_pref):
        prefere = 'processeur' if materiel_pref == 'processeur' else 'auto'

        def fabrique(materiel='auto'):
            if materiel == 'processeur' and prefere != 'processeur':
                # Repli de l'indexeur quand la carte graphique a échoué : images seules, sur le processeur.
                return modele.Moteur(cle, vision=vision, texte=texte, materiel='processeur', charger_texte=False)
            return self._moteur_commun(cle, vision, texte, prefere)

        def fournisseur():
            return self._moteur_commun(cle, vision, texte, prefere)

        self.moteur = {'cle': cle, 'vision': vision, 'texte': texte, 'materiel': prefere}
        self.indexeur = Indexeur(self.base, fabrique, cle, inventaire_auto_s=INVENTAIRE_AUTO_S)
        self.recherche = Recherche(self.base, fournisseur, cle)
        self.indexeur.demarrer()
        self._prechauffer(self.recherche)
        journal.info('moteur actif : %s (%s, %s)', cle, vision, prefere)

    @staticmethod
    def _prechauffer(r):
        """Charge le moteur de texte et les photos analysées tout de suite : la 1re recherche sera rapide."""
        def travail():
            try:
                r.chercher('photo', 0, 1)
            except Exception as e:
                journal.info('préchauffage de la recherche : %s', e)
        threading.Thread(target=travail, name='myphotos-prechauffage', daemon=True).start()

    def telecharger(self, cle, vision=None, materiel_pref=None, activer=True):
        cle, vision, texte = self._variante(cle, vision)
        with self.verrou:
            if self.arret_lance:
                raise Refus('MyPhotos est en train de s\'arrêter.', 409)
            t = self.telechargement
            if t is not None and t.etat == 'en_cours':
                if (t.cle, t.vision) == (cle, vision):
                    t.activer = t.activer or activer
                    return t.resume()
                raise Refus('Un autre téléchargement est déjà en cours : annulez-le d\'abord.', 409)
            t = Telechargement(self, cle, vision, texte, materiel_pref, activer)
            self.telechargement = t
            self.base.meta_set('telechargement', json.dumps(
                {'cle': cle, 'vision': vision, 'materiel': materiel_pref, 'activer': activer}))
            t.fil.start()
        journal.info('téléchargement du moteur %s (%s)', cle, vision)
        return t.resume()

    def annuler_telechargement(self):
        t = self.telechargement
        if t is None or t.etat != 'en_cours':
            return {'annule': False}
        t.arret.set()
        self.base.meta_set('telechargement', None)
        return {'annule': True}

    def _telechargement_fini(self, t):
        self.base.meta_set('telechargement', None)
        with self._verrou_materiel:
            self._materiel = None                  # tailles et durées à revoir
        if t.activer:
            try:
                self.choisir_moteur(t.cle, t.vision, t.materiel_pref)
            except Refus as e:
                journal.warning('moteur %s téléchargé mais pas activé : %s', t.cle, e)

    def choisir_moteur(self, cle, vision=None, materiel_pref=None):
        """Change de moteur (déjà téléchargé). L'analyse des photos repart de zéro, car les résultats d'un moteur
        ne servent pas à un autre ; les miniatures sont gardées. Le changement se fait en arrière-plan."""
        cle, vision, texte = self._variante(cle, vision)
        if not self.pret(cle, vision):
            raise Refus('Ce moteur n\'est pas encore téléchargé.', 409)
        prefere = 'processeur' if materiel_pref == 'processeur' else 'auto'
        with self.verrou:
            if self.arret_lance:
                raise Refus('MyPhotos est en train de s\'arrêter.', 409)
            if self.changement:
                raise Refus('Un changement de moteur est déjà en cours.', 409)
            m = self.moteur
            if m and (m['cle'], m['vision'], m['materiel']) == (cle, vision, prefere):
                return {'deja': True}
            self.changement = {'cle': cle, 'nom': modele.info(cle).get('nom') or cle}
        threading.Thread(target=self._changer_moteur, args=(cle, vision, texte, prefere),
                         name='myphotos-changement', daemon=True).start()
        return {'deja': False}

    def _changer_moteur(self, cle, vision, texte, prefere):
        try:
            ancien = self.indexeur
            if ancien is not None:
                ancien.arreter(attendre=True)          # fin du lot en cours
            if self.arret_lance:
                return
            self._liberer_moteurs()                    # l'ancien moteur part en entier avant le nouveau
            self.base.meta_set('moteur', cle)
            self.base.meta_set('moteur_vision', vision)
            self.base.meta_set('moteur_materiel', prefere)
            self._activer(cle, vision, texte, prefere)
        except Exception:
            journal.exception('changement de moteur')
        finally:
            with self.verrou:
                self.changement = None

    # ------------------------------------------------ matériel et conseil

    def _estimer(self):
        """Compte (sans les ouvrir) les photos des dossiers proposés : durée annoncée au premier lancement."""
        try:
            if self.base.compter()['total']:
                return
            for p in self.proposes():
                n, complet = compter_images(p['chemin'])
                self._estimations[cle_chemin(p['chemin'])] = (n, complet)
        except Exception:
            journal.debug('estimation du nombre de photos', exc_info=True)
        finally:
            self._estimation_finie.set()

    def _nb_photos(self):
        total = self.base.compter()['total']
        if total:
            return total, 'base'
        self._estimation_finie.wait(ESTIMATION_S * 2 + 1)
        estime = sum(n for n, _ in list(self._estimations.values()) if n)
        if estime:
            return estime, 'estimation'
        return getattr(materiel, 'NB_PHOTOS_TYPE', 10_000), 'suppose'

    def _materiel_en_avance(self):
        try:
            self.infos_materiel()
        except Exception:
            journal.debug('analyse du matériel en avance', exc_info=True)

    def infos_materiel(self, refaire=False):
        with self._verrou_materiel:
            c = self._materiel
            if c is None or refaire or time.monotonic() - c[0] > MATERIEL_VALIDITE_S:
                n, origine = self._nb_photos()
                analyse = materiel.analyser(n)
                try:
                    conseil = materiel.recommander(analyse, n)
                except Exception:
                    journal.exception('conseil du moteur impossible : repli sur le moteur standard')
                    conseil = None
                c = (time.monotonic(), self._presenter(analyse, conseil, n, origine))
                self._materiel = c
        return self._avec_etat_moteurs(c[1])

    def _presenter(self, analyse, conseil, n, origine):
        """Résumé du matériel et fiche de chaque moteur, à partir du conseil de materiel.recommander() ; si le
        conseil manque ou est incomplet, chaque moteur garde au moins son nom, sa description et sa taille."""
        options = {}
        if isinstance(conseil, dict) and conseil.get('cle') in modele.MODELES_DISPO:
            options[conseil['cle']] = conseil
            for o in conseil.get('alternatives') or []:
                if isinstance(o, dict) and o.get('cle') in modele.MODELES_DISPO and o['cle'] not in options:
                    options[o['cle']] = o
        conseille = next(iter(options), modele.MODELE_DEFAUT)
        carte = isinstance(analyse, dict) and analyse.get('directml') is True
        cles = [k for k in ORDRE_MOTEURS if k in modele.MODELES_DISPO]
        cles += [k for k in modele.MODELES_DISPO if k not in cles]
        moteurs = []
        for cle in cles:
            fiche = modele.info(cle)
            o = options.get(cle) or {}
            vision = o.get('vision') if o.get('vision') in fiche['vision'] else (
                'fp16' if 'fp16' in fiche['vision'] else next(iter(fiche['vision'])))
            sur = o.get('materiel') or ('carte' if carte else 'processeur')
            liste = (lambda v: [sans_jargon(x) for x in v if isinstance(x, str)] if isinstance(v, list) else [])
            moteurs.append({
                'cle': cle, 'nom': fiche.get('nom') or cle,
                'description': sans_jargon(o.get('description') or fiche.get('description')),
                'vision': vision, 'materiel': 'processeur' if sur == 'processeur' else 'carte',
                'duree_estimee_s': o.get('duree_estimee_s'), 'duree_texte': o.get('duree_texte'),
                'compromis': sans_jargon(o.get('compromis')),
                'raisons': liste(o.get('raisons')), 'alertes': liste(o.get('alertes')),
                'possible': bool(o.get('possible', True)), 'bloquant': bool(o.get('bloquant')),
                'raison_blocage': sans_jargon(o.get('raison_blocage')),
                'provisoire': bool(o.get('provisoire', not options)),
                'conseille': cle == conseille,
                'taille_totale': sum(fiche['fichiers'][x][0] for x in self._noms(cle, vision)),
            })
        return {'resume': resume_materiel(analyse), 'carte': carte, 'nb_photos': n, 'nb_photos_origine': origine,
                'conseil': conseille, 'moteurs': moteurs}

    def _avec_etat_moteurs(self, res):
        """Ce qui bouge d'une fois à l'autre : moteur actuel et octets restant à télécharger."""
        res = dict(res)
        actuel = self.moteur['cle'] if self.moteur else None
        moteurs = []
        for m in res['moteurs']:
            m = dict(m)
            try:
                r = self.reste(m['cle'], m['vision'])
            except Exception:
                r = m['taille_totale']
            m['telechargement_octets'] = r
            m['telechargement_texte'] = materiel.formater_taille(r) if r else None
            m['deja'] = not r
            m['actuel'] = m['cle'] == actuel
            # Phrase du conseil sur le téléchargement : toujours d'accord avec l'état réel des fichiers.
            m['raisons'] = [x for x in m['raisons'] if not (
                (r and 'rien à télécharger' in x) or (not r and 'se télécharge' in x))]
            moteurs.append(m)
        res['moteurs'] = moteurs
        res['actuel'] = actuel
        return res

    # ------------------------------------------------ dossiers

    def proposes(self):
        if self._proposes is None:
            self._proposes = dossiers_proposes()
        return self._proposes

    @staticmethod
    def _dossier_public(d):
        return {k: d.get(k) for k in ('id', 'chemin', 'photos', 'analysees', 'erreurs', 'a_faire', 'disponible')}

    def liste_dossiers(self):
        suivis = self.base.dossiers()
        self._nb_dossiers = len(suivis)
        cles = [cle_chemin(d['chemin']) for d in suivis]
        proposes = []
        for p in self.proposes():
            k = cle_chemin(p['chemin'])
            n, complet = self._estimations.get(k, (None, None))
            proposes.append({'chemin': p['chemin'], 'nom': p['nom'],
                             'deja': any(k == c or dedans(k, c) for c in cles),
                             'photos_estimees': n, 'estimation_complete': complet})
        return {'dossiers': [self._dossier_public(d) for d in suivis], 'proposes': proposes,
                'estimation_en_cours': not self._estimation_finie.is_set()}

    def ajouter_dossier(self, chemin):
        if not isinstance(chemin, str) or not chemin.strip():
            raise Refus('Aucun dossier indiqué.')
        if len(chemin) > 4096 or '\0' in chemin:
            raise Refus('Ce chemin de dossier est invalide.')
        try:
            d = self.base.ajouter_dossier(chemin)
        except DossierRefuse as e:
            raise Refus(sans_jargon(str(e)), 409)
        except OSError:
            raise Refus('Ce dossier est inaccessible.', 409)
        journal.info('dossier ajouté : %s', d['chemin'])
        self._nb_dossiers = len(self.base.dossiers())
        ix = self.indexeur
        if ix is not None:
            ix.relancer()
        return {'dossier': self._dossier_public(d), 'remplaces': d.get('remplaces') or []}

    def choisir_dossier(self):
        chemin = self.choix_dossier.demander(initial=dossier_images())
        if not chemin:
            return {'annule': True}
        return self.ajouter_dossier(chemin)

    def retirer_dossier(self, dossier_id):
        r = self.base.retirer_dossier(dossier_id)
        if r is None:
            raise Refus('Ce dossier ne fait pas partie de MyPhotos.', 404)
        journal.info('dossier retiré : %s (%d photos oubliées)', r['chemin'], r['photos'])
        self._nb_dossiers = len(self.base.dossiers())
        ix = self.indexeur
        if ix is not None:
            ix.relancer()
        return r

    # ------------------------------------------------ état

    def etat(self):
        ix = self.indexeur
        analyse = None
        if ix is not None:
            try:
                e = ix.etat()
                analyse = {k: e.get(k) for k in ('phase', 'faites', 'total', 'a_faire', 'debit', 'eta_s', 'erreurs',
                                                 'analysees', 'en_attente', 'trouvees', 'traitees', 'materiel')}
                analyse['message'] = sans_jargon(e.get('message'))
                analyse['eta_texte'] = materiel.formater_duree(e.get('eta_s')) if e.get('eta_s') else None
                analyse['active'] = e.get('phase') in ('inventaire', 'analyse', 'pause')
            except Exception:
                journal.exception('état de l\'analyse')
        photos = self.base.compter()
        m = self.moteur
        t = self.telechargement
        partage = self._partage
        sur = (analyse or {}).get('materiel') or (partage[1].materiel if partage is not None else None)
        return {
            'nom': NOM, 'version': config.VERSION,
            'assistant': self.base.meta_get('assistant'),
            'moteur': None if m is None else {
                'cle': m['cle'], 'nom': modele.info(m['cle']).get('nom') or m['cle'],
                'description': sans_jargon(modele.info(m['cle']).get('description')), 'vision': m['vision'],
                'materiel': sur},
            'telechargement': t.resume() if t is not None else None,
            'changement': self.changement,
            'analyse': analyse,
            'photos': photos,
            'nb_dossiers': self._nb_dossiers,
            'recherche_prete': self.recherche is not None,
            'generation': self.base.generation(),
            'arret': self.arret_lance,
            'factice': FACTICE,
        }

    # ------------------------------------------------ photos

    def chercher(self, q, debut, n):
        if not (q or '').strip():
            return self.photos_par_date(debut, n)
        r = self.recherche
        if r is None:
            if self.changement:
                raise Refus('Changement de moteur en cours : la recherche revient dans un instant.', 503)
            raise Refus('La recherche sera possible dès que le moteur de reconnaissance sera installé.', 503)
        try:
            res = r.chercher(q, debut, n)
        except getattr(_recherche, 'MoteurIndisponible', RuntimeError) as e:
            raise Refus(sans_jargon(str(e)), 503)
        except Exception:
            journal.exception('recherche « %s »', q[:100])
            raise Refus('La recherche a rencontré un problème. Réessayez ; si cela continue, relancez MyPhotos.', 500)
        if res is None:
            return self.photos_par_date(debut, n)
        page = list(res.get('sures') or []) + list(res.get('peut_etre') or [])
        infos = {p['id']: p for p in self.base.photos([i['id'] for i in page])}

        def enrichir(items):
            sortie = []
            for i in items or []:
                p = infos.get(i['id'])
                if p is not None:                     # retirée entre-temps
                    sortie.append({'id': i['id'], 'proba': i.get('proba'), 'date': p['date'],
                                   'largeur': p['largeur'], 'hauteur': p['hauteur']})
            return sortie
        return {'mode': 'recherche', 'requete': res.get('requete'), 'debut': res.get('debut', debut),
                'suivant': res.get('debut', debut) + len(page),
                'total_sures': res.get('total_sures', 0), 'total_peut_etre': res.get('total_peut_etre', 0),
                'sures': enrichir(res.get('sures')), 'peut_etre': enrichir(res.get('peut_etre')),
                'analysees': res.get('analysees'), 'duree_ms': res.get('duree_ms')}

    def photos_par_date(self, debut, n):
        r = self.base.photos_par_date(debut, n)
        return {'mode': 'dates', 'debut': r['debut'], 'suivant': r['debut'] + len(r['photos']), 'total': r['total'],
                'photos': r['photos']}

    def info_photo(self, pid):
        p = self.base.photo(pid)
        if p is None:
            raise Refus('Cette photo est introuvable.', 404)
        return {'id': p['id'], 'nom': p['nom'], 'dossier': os.path.dirname(p['chemin']), 'date': p['date'],
                'date_prise': p['date_prise'] is not None, 'largeur': p['largeur'], 'hauteur': p['hauteur'],
                'taille': p['taille'], 'disponible': p['disponible'], 'miniature': p['miniature'],
                'analysee': p['analysee']}

    def fichier_photo(self, pid):
        """(fiche, chemin habituel, chemin système, extension) d'une photo connue, qui est bien une image."""
        p = self.base.photo(pid)
        if p is None:
            raise Refus('Cette photo est introuvable.', 404)
        chemin = p['chemin']
        ext = os.path.splitext(chemin)[1].lower()
        if ext not in config.EXTENSIONS:
            raise Refus('Ce fichier n\'est pas une photo.', 403)
        systeme = chemin_systeme(chemin)
        if not os.path.isfile(systeme):
            raise Refus(PHOTO_ABSENTE, 404)
        return p, chemin_affiche(chemin), systeme, ext

    def ouvrir(self, pid):
        _, chemin, systeme, _ = self.fichier_photo(pid)
        try:
            os.startfile(chemin if len(chemin) < 260 else systeme)
        except OSError as e:
            journal.info('ouverture de la photo %d impossible : %r', pid, e)
            if getattr(e, 'winerror', None) in (1155, 1156):
                raise Refus('Aucun programme de Windows n\'est prévu pour ouvrir ce type de photo.', 409)
            raise Refus('Windows n\'a pas pu ouvrir cette photo.', 409)
        return {'ok': True}

    def montrer(self, pid):
        _, chemin, _, _ = self.fichier_photo(pid)
        try:
            montrer_dans_dossier(chemin)
        except OSError as e:
            journal.info('affichage du dossier de la photo %d impossible : %r', pid, e)
            raise Refus('Windows n\'a pas pu ouvrir le dossier de cette photo.', 409)
        return {'ok': True}

    def analyse(self, action):
        ix = self.indexeur
        if ix is None:
            raise Refus('L\'analyse commencera dès que le moteur de reconnaissance sera installé.', 409)
        if action == 'pause':
            ix.pause()
        elif action == 'reprendre':
            ix.reprendre()
        else:
            ix.reprendre()
            ix.relancer(erreurs=True)
        return {'ok': True}


# ---------------------------------------------------------------- page

class Page:
    """page/index.html, relue quand elle change ; un nonce frais par réponse pour la CSP."""

    def __init__(self):
        self._cache = (None, None, None)

    @staticmethod
    def chemin():
        ici = Path(__file__).resolve().parent
        for p in (Path(config.CODE) / 'page' / 'index.html', ici.parent / 'page' / 'index.html',
                  ici / 'page' / 'index.html'):
            if p.is_file():
                return p
        return None

    def rendre(self, nonce):
        p = self.chemin()
        if p is None:
            return None
        cle = (str(p), p.stat().st_mtime_ns)
        if self._cache[0] != cle:
            self._cache = (cle, p.read_text(encoding='utf-8'), None)
        texte = self._cache[1]
        return (texte.replace('<script>', f'<script nonce="{nonce}">')
                .replace('<style>', f'<style nonce="{nonce}">')).encode('utf-8')


# ---------------------------------------------------------------- HTTP

class Serveur(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # Sous Windows, SO_REUSEADDR laisserait un second programme écouter sur le même port : exclusivité.
    allow_reuse_address = os.name != 'nt'
    request_queue_size = 128
    app = None

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)          # sans la résolution DNS de HTTPServer.server_bind
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]
        self.hotes = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}
        self.page = Page()

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, socket.timeout, TimeoutError)):
            return
        journal.exception('requête HTTP')


def _securite(entetes, csp=None):
    entetes.setdefault('X-Content-Type-Options', 'nosniff')
    entetes.setdefault('Referrer-Policy', 'no-referrer')
    entetes.setdefault('X-Frame-Options', 'DENY')
    entetes.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
    entetes.setdefault('Content-Security-Policy', csp or "default-src 'none'; frame-ancestors 'none'; "
                                                         "base-uri 'none'; form-action 'none'")
    return entetes


class Gestionnaire(http.server.BaseHTTPRequestHandler):
    server_version = NOM
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    timeout = 30                                 # connexion inactive fermée au bout de 30 s

    def log_message(self, format, *args):
        pass

    def log_error(self, format, *args):
        journal.debug('HTTP : ' + format, *args)

    # ------------------------------------------------ réponses

    def _envoyer(self, statut, corps, type_contenu, entetes=None, csp=None):
        e = _securite(dict(entetes or {}), csp)
        self.send_response(statut)
        self.send_header('Content-Type', type_contenu)
        self.send_header('Content-Length', str(len(corps)))
        for k, v in e.items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(corps)

    def _json(self, donnees, statut=200):
        corps = json.dumps(donnees, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self._envoyer(statut, corps, 'application/json; charset=utf-8', {'Cache-Control': 'no-store'})

    def _erreur(self, statut, message, fermer=False):
        if fermer:
            self.close_connection = True
        self._json({'erreur': message}, statut)

    def _pas_change(self, etag, cache):
        self.send_response(304)
        for k, v in _securite({'ETag': etag, 'Cache-Control': cache}).items():
            self.send_header(k, v)
        self.end_headers()

    # ------------------------------------------------ contrôles

    def _hote_ok(self):
        return self.headers.get('Host', '').strip().lower() in self.server.hotes

    def _site_ok(self):
        site = self.headers.get('Sec-Fetch-Site')
        return site is None or site.strip().lower() in ('same-origin', 'none')

    def _origine_ok(self):
        origine = self.headers.get('Origin')
        return origine is None or origine.strip().lower() == 'http://' + self.headers.get('Host', '').strip().lower()

    def _corps(self):
        """Corps JSON d'un POST (64 Ko au plus) ; {} s'il est vide."""
        if self.headers.get('Transfer-Encoding'):
            raise Refus('Requête refusée.', 411)
        try:
            longueur = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise Refus('Requête refusée.', 400)
        if longueur < 0 or longueur > CORPS_MAX:
            self.close_connection = True
            if 0 < longueur <= 16 * CORPS_MAX:
                self.rfile.read(longueur)          # lu et jeté : sinon Windows coupe avant que la réponse parte
            raise Refus('Requête trop volumineuse.', 413)
        if not longueur:
            return {}
        if not (self.headers.get('Content-Type') or '').lower().startswith('application/json'):
            raise Refus('Requête refusée.', 415)
        brut = self.rfile.read(longueur)
        try:
            d = json.loads(brut.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise Refus('Requête illisible.', 400)
        if not isinstance(d, dict):
            raise Refus('Requête illisible.', 400)
        return d

    # ------------------------------------------------ aiguillage

    def do_GET(self):
        self._traiter('GET')

    def do_POST(self):
        self._traiter('POST')

    def _traiter(self, methode):
        app = self.server.app
        try:
            if app is None:
                return self._erreur(503, 'MyPhotos démarre, un instant…', fermer=True)
            if not self._hote_ok():
                return self._erreur(403, 'Adresse non autorisée.', fermer=True)
            url = urllib.parse.urlsplit(self.path)
            chemin = url.path
            if methode == 'POST':
                if self.headers.get(EN_TETE) != '1' or not self._origine_ok() or not self._site_ok():
                    return self._erreur(403, 'Requête refusée.', fermer=True)
                corps = self._corps()
            else:
                corps = None
                if chemin.startswith(('/api/', '/miniature/', '/photo/')) and not self._site_ok():
                    return self._erreur(403, 'Requête refusée.', fermer=True)
            app.activite()
            params = urllib.parse.parse_qs(url.query, keep_blank_values=True, max_num_fields=20)
            if methode == 'GET':
                self._get(app, chemin, params)
            else:
                self._post(app, chemin, corps)
        except Refus as e:
            self._erreur(e.statut, str(e))
        except (ConnectionError, socket.timeout, TimeoutError):
            self.close_connection = True
        except Exception:
            journal.exception('requête %s %s', methode, self.path[:200])
            try:
                self._erreur(500, 'Une erreur inattendue est survenue. Réessayez ; si elle revient, relancez MyPhotos.',
                             fermer=True)
            except Exception:
                pass

    @staticmethod
    def _identifiant(texte):
        if not isinstance(texte, (str, int)) or isinstance(texte, bool):
            raise Refus('Cette photo est introuvable.', 404)
        texte = str(texte)
        if not _IDENTIFIANT.fullmatch(texte):
            raise Refus('Cette photo est introuvable.', 404)
        return int(texte)

    @staticmethod
    def _entier(params, nom, defaut, mini, maxi):
        v = (params.get(nom) or [''])[-1]
        if v == '':
            return defaut
        if not v.isdigit() or len(v) > 9:
            raise Refus('Paramètre invalide.', 400)
        return max(mini, min(maxi, int(v)))

    def _get(self, app, chemin, params):
        if chemin in ('/', '/index.html'):
            return self._page()
        if chemin == '/api/etat':
            return self._json(app.etat())
        if chemin == '/api/materiel':
            return self._json(app.infos_materiel(refaire=(params.get('refaire') or [''])[-1] == '1'))
        if chemin == '/api/dossiers':
            return self._json(app.liste_dossiers())
        if chemin in ('/api/recherche', '/api/photos'):
            q = (params.get('q') or [''])[-1] if chemin == '/api/recherche' else ''
            if len(q) > 1000:
                raise Refus('Recherche trop longue.', 400)
            debut = self._entier(params, 'debut', 0, 0, 10 ** 8)
            n = self._entier(params, 'n', LISTE_DEFAUT, 1, LISTE_MAX)
            return self._json(app.chercher(q, debut, n))
        for prefixe, action in (('/api/photo/', self._info_photo), ('/miniature/', self._miniature),
                                ('/photo/', self._photo)):
            if chemin.startswith(prefixe):
                return action(app, self._identifiant(chemin[len(prefixe):]))
        raise Refus('Page introuvable.', 404)

    def _post(self, app, chemin, corps):
        if chemin == '/api/moteur/telecharger':
            r = app.telecharger(corps.get('cle'), corps.get('vision'), corps.get('materiel'),
                                bool(corps.get('activer', True)))
        elif chemin == '/api/moteur/annuler':
            r = app.annuler_telechargement()
        elif chemin == '/api/moteur/choisir':
            r = app.choisir_moteur(corps.get('cle'), corps.get('vision'), corps.get('materiel'))
        elif chemin == '/api/dossiers/choisir':
            r = app.choisir_dossier()
        elif chemin == '/api/dossiers/ajouter':
            r = app.ajouter_dossier(corps.get('chemin'))
        elif chemin == '/api/dossiers/retirer':
            try:
                r = app.retirer_dossier(self._identifiant(corps.get('id')))
            except Refus:
                raise Refus('Ce dossier ne fait pas partie de MyPhotos.', 404)
        elif chemin in ('/api/analyse/pause', '/api/analyse/reprendre', '/api/analyse/relancer'):
            r = app.analyse(chemin.rsplit('/', 1)[1])
        elif chemin == '/api/ouvrir':
            r = app.ouvrir(self._identifiant(corps.get('id')))
        elif chemin == '/api/montrer':
            r = app.montrer(self._identifiant(corps.get('id')))
        elif chemin == '/api/assistant':
            etape = corps.get('etape')
            if etape not in (1, 2, 3, 'fini'):
                raise Refus('Étape inconnue.', 400)
            app.base.meta_set('assistant', str(etape))
            r = {'ok': True}
        elif chemin == '/api/quitter':
            journal.info('arrêt demandé par la page')
            app.arreter()
            r = {'ok': True}
        else:
            raise Refus('Action inconnue.', 404)
        return self._json(r)

    # ------------------------------------------------ contenus

    def _page(self):
        nonce = secrets.token_urlsafe(18)
        corps = self.server.page.rendre(nonce)
        if corps is None:
            corps = '<!doctype html><meta charset="utf-8"><title>MyPhotos</title><p>La page de MyPhotos est ' \
                    'introuvable : réinstallez MyPhotos.</p>'.encode('utf-8')
            return self._envoyer(500, corps, 'text/html; charset=utf-8', {'Cache-Control': 'no-store'})
        csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; img-src 'self' data:; "
               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self._envoyer(200, corps, 'text/html; charset=utf-8',
                      {'Cache-Control': 'no-store', 'Cross-Origin-Opener-Policy': 'same-origin'}, csp)

    def _info_photo(self, app, pid):
        self._json(app.info_photo(pid))

    def _miniature(self, app, pid):
        jpeg = app.base.miniature(pid)
        if jpeg is None:
            raise Refus('Miniature introuvable.', 404)
        etag = f'"m{pid}-{len(jpeg)}-{zlib.crc32(jpeg):08x}"'
        cache = 'private, max-age=600'
        if self.headers.get('If-None-Match') == etag:
            return self._pas_change(etag, cache)
        self._envoyer(200, jpeg, 'image/jpeg', {'Cache-Control': cache, 'ETag': etag})

    def _photo(self, app, pid):
        p, _, systeme, ext = app.fichier_photo(pid)
        etag = f'"p{pid}-{p["taille"]}-{p["modifie"]}"'
        cache = 'private, max-age=300'
        if self.headers.get('If-None-Match') == etag:
            return self._pas_change(etag, cache)
        if ext not in TYPES_IMAGES:                   # TIFF : le navigateur ne sait pas l'afficher
            try:
                im = modele.ouvrir_image(systeme, cote=COTE_CONVERSION)
                im.thumbnail((COTE_CONVERSION, COTE_CONVERSION))
                tampon = io.BytesIO()
                im.save(tampon, 'JPEG', quality=90)
            except Exception:
                raise Refus('Cette photo ne peut pas être affichée.', 409)
            return self._envoyer(200, tampon.getvalue(), 'image/jpeg', {'Cache-Control': cache, 'ETag': etag})
        try:
            f = open(systeme, 'rb')
        except OSError:
            raise Refus(PHOTO_ABSENTE, 404)
        with f:
            taille = os.fstat(f.fileno()).st_size
            e = _securite({'Cache-Control': cache, 'ETag': etag})
            self.send_response(200)
            self.send_header('Content-Type', TYPES_IMAGES[ext])
            self.send_header('Content-Length', str(taille))
            for k, v in e.items():
                self.send_header(k, v)
            self.end_headers()
            reste = taille
            while reste > 0:
                bloc = f.read(min(1 << 20, reste))
                if not bloc:                          # fichier raccourci pendant l'envoi
                    self.close_connection = True
                    break
                self.wfile.write(bloc)
                reste -= len(bloc)


# ---------------------------------------------------------------- lancement

def deja_lance(port):
    """Vrai si MyPhotos répond déjà sur ce port."""
    try:
        c = http.client.HTTPConnection(config.HOTE, port, timeout=3)
        try:
            c.request('GET', '/api/etat', headers={'Sec-Fetch-Site': 'same-origin'})
            r = c.getresponse()
            d = json.loads(r.read() or b'{}')
        finally:
            c.close()
        return r.status == 200 and isinstance(d, dict) and d.get('nom') == NOM
    except Exception:
        return False


def main(argv=None):
    configurer_journal()
    adresse = f'http://{config.HOTE}:{config.PORT}/'
    try:
        serveur = Serveur((config.HOTE, config.PORT), Gestionnaire)
    except OSError as e:
        if deja_lance(config.PORT):
            journal.info('MyPhotos est déjà lancé : ouverture de la page')
            if not SANS_PAGE:
                ouvrir_page(adresse)
            return 0
        alerter(f'MyPhotos ne peut pas démarrer : le port {config.PORT} est déjà utilisé par un autre programme '
                f'({e.__class__.__name__}).')
        return 1
    journal.info('%s %s : démarrage sur %s (données : %s)', NOM, config.VERSION, adresse, config.DONNEES)
    try:
        app = Application(serveur)
        app.demarrer()
    except Exception as e:
        journal.exception('démarrage impossible')
        serveur.server_close()
        alerter(f'MyPhotos ne peut pas démarrer : {sans_jargon(str(e))}')
        return 1
    serveur.app = app
    if sys.stdout is not None and not config.GELE:
        print(f'{NOM} : {adresse}  (Ctrl+C pour arrêter)', flush=True)
    if not SANS_PAGE:
        ouvrir_page(adresse)
    try:
        serveur.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        journal.info('arrêt demandé (Ctrl+C)')
    finally:
        app.fermer()
        serveur.server_close()
        journal.info('%s arrêté', NOM)
    return 0
