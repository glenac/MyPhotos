"""Serveur local de MyPhotos : la page (page/index.html) et son API JSON, sur 127.0.0.1 uniquement.

    python -m myphotos          (ou MyPhotos.exe, ou python lancer.py)
    python -m myphotos --navigateur    (ou MYPHOTOS_NAVIGATEUR=1) : la page dans le navigateur par défaut

Depuis la 0.3.0, MyPhotos s'ouvre dans SA fenêtre « MyPhotos » (WebView2 de Windows, par pywebview) : main() démarre
le serveur, puis fenetre.Hote le fait tourner dans un fil et garde la fenêtre dans le fil principal. Repli sur Edge
en mode application, puis sur le navigateur par défaut ; tout est décrit en tête de fenetre.py. /api/etat dit le
mode : « fenetre », « edge » ou « navigateur ». Fermer la fenêtre arrête MyPhotos proprement (fin du lot d'analyse
en cours, fermeture des bases).

Une seule instance : si le port est déjà pris par MyPhotos, sa fenêtre revient au premier plan (événement Windows
nommé, hors HTTP), sinon sa page s'ouvre ; une ancienne version sans fenêtre (0.1.0, 0.2.0) est signalée par un
message. En mode navigateur, la page interroge /api/etat toutes les 2 s (toutes les 30 s quand elle est cachée) ;
sans aucune requête pendant 3 minutes, le serveur s'arrête proprement. Pas d'arrêt pour inactivité tant que la
fenêtre est ouverte.

Au lancement, le moteur de reconnaissance se charge en arrière-plan, après la première réponse à la page : la page
s'affiche tout de suite, /api/etat dit « preparation » pendant ce temps et la recherche répond « préparation »
(la page la relance d'elle-même) au lieu d'une erreur.

Sécurité : n'importe quel site ouvert dans le navigateur peut viser 127.0.0.1, et le port 127.0.0.1 est commun à
tous les comptes Windows du PC. D'où :
- jeton d'accès (0.4.0) : un secret aléatoire tiré à chaque lancement, rangé dans les données (config.JETON, privées
  au compte Windows), exigé par TOUTE requête, en un seul endroit (Gestionnaire._acces, avant tout aiguillage : les
  routes à venir sont couvertes d'office). La fenêtre, Edge, le navigateur et une relance ouvrent l'adresse d'entrée
  « /?jeton=… » (adresse_entree) : le serveur pose le cookie myphotos-<port> (HttpOnly, SameSite=Strict, Path=/) et
  redirige vers « / », sans jeton dans l'adresse affichée. Sans jeton valable : 403, une petite page « Ouvrez MyPhotos
  depuis son raccourci » (JSON pour /api/…), rien d'autre ; comparaison en temps constant ; jamais dans le journal.
  Une personne connectée en même temps sur un autre compte du PC n'affiche donc plus la page de MyPhotos ;
- Host obligatoire : 127.0.0.1:PORT ou localhost:PORT (parade au « DNS rebinding ») ;
- POST : en-tête X-MyPhotos: 1 (un autre site ne peut pas le poser sans autorisation CORS, jamais donnée),
  corps JSON de 64 Ko au plus, Origin identique à l'adresse du serveur quand elle est présente ;
- GET refusés quand Sec-Fetch-Site désigne un autre site, sauf l'ouverture de la page dans un onglet (navigate +
  document) : un autre site ne lit rien et ne garde pas MyPhotos éveillé (arrêt automatique) ;
- CSP stricte (nonce, aucune ressource externe), nosniff, no-referrer, no-store sur l'API, y compris pour les
  erreurs du protocole (send_error) ; requêtes malformées refusées sans trace dans le journal ;
- photos servies, ouvertes, montrées, copiées ou mises à la Corbeille uniquement par un identifiant présent en base,
  et seulement si leur extension est celle d'une image ; aucun chemin de fichier n'est jamais lu dans l'URL ni dans
  un corps de requête (la destination d'une copie vient de la fenêtre « Choisir un dossier », ouverte ici) ;
- jamais transmis à Windows un chemin qu'il lirait autrement (dossier « Vacances. » -> « Vacances ») ; dossiers des
  photos tenus ouverts entre la vérification et l'action (GardeDossiers) : pas de jonction glissée entre les deux.

Promesse : MyPhotos ne modifie jamais vos photos. Il ne les supprime que si vous le demandez, et alors vers la Corbeille
de Windows (jamais de suppression définitive : un disque sans Corbeille est refusé, voir infos_corbeille).

Doublons (écran « Doublons » de la page) : la recherche tourne dans son propre fil, avec la base et numpy seulement
(doublons.py n'utilise ni le moteur ni la carte graphique) ; la recherche de photos et l'analyse continuent pendant ce
temps. Elle ne fait que proposer : les photos partent par /api/supprimer, après un clic et une confirmation.

Raccourci (exécutable seulement) : POST /api/raccourci crée MyPhotos.lnk sur le Bureau et dans le menu Démarrer
(raccourci.py : interface IShellLink de Windows, sans installation, sans PowerShell ni cmd).

Variables d'environnement : MYPHOTOS_PORT, MYPHOTOS_DOSSIER, MYPHOTOS_MODELES (voir config.py) ;
MYPHOTOS_SANS_PAGE=1 n'ouvre ni fenêtre ni page ; MYPHOTOS_NAVIGATEUR=1 ouvre la page dans le navigateur ;
MYPHOTOS_DEBUG=1 rend les outils de développement de la fenêtre et garde les variables WEBVIEW2_* (essais ; pour
MyPhotos.exe, seulement avec en plus l'argument --essai-debogage, voir fenetre.debogage) ;
MYPHOTOS_TELECHARGEMENT_FACTICE=1 simule le téléchargement du moteur (essais de l'interface ; les fichiers réels
de MYPHOTOS_MODELES servent ensuite) ; MYPHOTOS_INACTIVITE_S change le délai d'arrêt automatique (180 s).
"""
import base64
import concurrent.futures
import ctypes
import errno
import gc
import hashlib
import hmac
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
import sqlite3
import stat
import struct
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

from . import config, dialogue, fenetre, materiel, mise_a_jour, modele, raccourci
from . import doublons as _doublons
from . import recherche as _recherche
from .base import (Base, DossierRefuse, chemin_affiche, chemin_systeme, cle_chemin, dedans, identite_volume,
                   maintenant, normaliser_chemin)
from .indexation import _BALISES_LIENS, Indexeur, Parcours, dossier_vide, examiner_dossier
from .recherche import Recherche

NOM = 'MyPhotos'
journal = logging.getLogger(__name__)

INACTIVITE_S = float(os.environ.get('MYPHOTOS_INACTIVITE_S') or 180)
SANS_PAGE = bool(os.environ.get('MYPHOTOS_SANS_PAGE'))
FACTICE = os.environ.get('MYPHOTOS_TELECHARGEMENT_FACTICE') == '1'
DUREE_FACTICE_S = float(os.environ.get('MYPHOTOS_FACTICE_S') or 8)


def _essai_mise_a_jour():
    """Essais (test_serveur), en mode FACTICE seulement : manifeste lu dans un fichier local (MYPHOTOS_ESSAI_MANIFESTE)
    et signé par une clé d'essai (MYPHOTOS_ESSAI_CLE_MAJ, clé publique en base64) ; téléchargement et installation
    simulés : aucun programme n'est lancé. Hors de ce mode, la vraie vérification (mise_a_jour.py)."""
    chemin = os.environ.get('MYPHOTOS_ESSAI_MANIFESTE')
    if not FACTICE or not chemin:
        return {}
    try:
        cle = base64.b64decode(os.environ.get('MYPHOTOS_ESSAI_CLE_MAJ') or '', validate=True)
    except ValueError:
        cle = b''

    def chercher():
        try:
            brut = Path(chemin).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            raise mise_a_jour.Indisponible(str(e)) from None
        return mise_a_jour.lire_manifeste(brut, cle=cle)

    def telecharger(info, progression, arret, racine):
        for i in range(1, 11):
            if arret.wait(DUREE_FACTICE_S / 40):
                raise InterruptedError('téléchargement interrompu')
            progression(info['taille'] * i // 10, info['taille'])
        return mise_a_jour.dossier(racine) / info['nom']

    def lancer(chemin_installateur, info):
        journal.info('essai : installation de %s simulée', info['nom'])
        return 0
    return {'chercher': chercher, 'telecharger': telecharger, 'lancer': lancer, 'possible': lambda: (True, '')}

EN_TETE = 'X-MyPhotos'
# Jeton d'accès (voir Gestionnaire._acces) : cookie myphotos-<port> (les cookies ne distinguent pas les ports : deux
# MyPhotos sur deux ports ne se marchent pas dessus), adresse d'entrée « /?jeton=… », refus sans rien révéler.
COOKIE_JETON = 'myphotos-{port}'
PARAMETRE_JETON = 'jeton'
PARAMETRE_ARRIVEE = 'entree'           # « /?entree=1 » : marqueur NON secret, posé par la redirection d'entrée
JETON_REFUSE = 'Ouvrez MyPhotos depuis son raccourci.'
# Relance : le port est tenu par un programme d'un autre compte Windows (ou que Windows ne laisse pas identifier) ;
# rien ne lui est envoyé (fenetre.port_tenu_par).
PORT_PRIS = ('MyPhotos ne peut pas démarrer : le port {port} est déjà utilisé par un autre programme, ou par MyPhotos '
             'ouvert sur un autre compte de cet ordinateur.')
PAGE_REFUS = ('<!doctype html><html lang="fr"><meta charset="utf-8"><title>MyPhotos</title>'
              f'<p>{JETON_REFUSE}</p></html>').encode('utf-8')
# Mode navigateur : le navigateur a refusé le cookie du jeton (réglage « bloquer les cookies ») ; dit seulement à
# l'arrivée de la redirection d'entrée (« /?entree=1 »), jamais à une requête ordinaire.
COOKIES_REFUSES = ('Votre navigateur refuse les cookies de MyPhotos : autorisez-les pour 127.0.0.1 (MyPhotos, sur cet '
                   'ordinateur), puis ouvrez de nouveau MyPhotos depuis son raccourci.')
PAGE_COOKIES = ('<!doctype html><html lang="fr"><meta charset="utf-8"><title>MyPhotos</title>'
                f'<p>{COOKIES_REFUSES}</p></html>').encode('utf-8')
_JETON = re.compile(r'[A-Za-z0-9_-]{32,128}')
JETON_ESSAIS, JETON_PAUSE_S = 20, 0.05     # fichier « jeton » lu à l'instant par une relance : 1 s pour le remplacer
PAGES = ('/', '/index.html')
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
FERMETURE_S = 5.0                  # … puis elle est fermée (comme « Annuler ») ; 5 s au plus pour qu'elle le soit
ATTENTE_ARRET_S = 60               # relance pendant qu'une MyPhotos s'arrête : on attend qu'elle libère le port
PORT_LIBERE_S = 10                 # port tenu sans réponse (MyPhotos qui finit de s'arrêter) : on réessaie 10 s
ORDRE_MOTEURS = ('rapide', 'base', 'precis', 'tres_precis')
# Préparation du moteur. Pendant qu'ONNX Runtime crée ses sessions (3 à 6 s), il garde le GIL jusqu'à 1,2 s
# d'affilée : chargé au lancement, le moteur retardait la page de 6 s (mesuré le 24/11/2026). Il attend donc que
# la page (ou /api/etat) ait répondu, puis un court moment sans requête : la page s'affiche d'abord.
PAGE_ATTENDUE_S = 15.0             # aucune page servie : préparation quand même au bout de 15 s…
SANS_PAGE_ATTENTE_S = 2.0          # … ou de 2 s quand MyPhotos est lancé sans ouvrir la page (MYPHOTOS_SANS_PAGE)
CALME_S = 0.4                      # après la première réponse : 0,4 s sans aucune requête…
CALME_MAX_S = 2.0                  # … sans attendre plus de 2 s
PREPARATION_MAX_S = 300            # un changement de moteur attend sa préparation au plus 5 minutes
ATTENTE_RECHERCHE_S = 2.0          # moteur en plein chargement : une recherche attend jusqu'à 2 s qu'il soit prêt
PREPARATION = 'Préparation du moteur de reconnaissance…'
# « Plus comme celle-ci » (0.4.1) : sans moteur, ou photo pas encore analysée par le moteur actuel, une réponse vide avec
# une phrase simple, jamais une erreur.
SEMBLABLES_SANS_MOTEUR = ('Les photos qui se ressemblent pourront être trouvées dès que le moteur de reconnaissance '
                          'sera installé.')
SEMBLABLES_PAS_ANALYSEE = ("Cette photo n'est pas encore analysée : MyPhotos ne peut pas encore trouver les photos qui "
                           "lui ressemblent. Réessayez un peu plus tard.")
# Taille du texte de la page (0.4.1), gardée dans la table meta (le profil de la fenêtre est effacé à chaque fermeture).
TAILLES_TEXTE = ('normale', 'grande', 'tres-grande')

# Images que le navigateur affiche telles quelles ; les autres extensions de config.EXTENSIONS (TIFF) sont
# converties en JPEG à la volée.
TYPES_IMAGES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.jfif': 'image/jpeg', '.png': 'image/png',
                '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'}

PHOTO_ABSENTE = ("La photo d'origine est introuvable : le disque est peut-être débranché, ou le fichier a été "
                 "déplacé.")
# Photo ouverte (visionneuse, Ouvrir, Afficher dans le dossier, Copier) dont le fichier manque alors que son dossier
# suivi répond : rangée ailleurs, renommée ou supprimée. Rien n'est oublié sur place ; un tour de son dossier la retrouve,
# avec son analyse, ou l'oublie (réponse 404 ; la visionneuse affiche ce message). Dossier injoignable : PHOTO_ABSENTE.
PHOTO_DEPLACEE = ("Cette photo n'est plus à son emplacement (déplacée, renommée ou supprimée) : MyPhotos refait le "
                  "tour de son dossier pour la retrouver.")
# Assistant : sous-dossiers décochés d'office, et pourquoi (une phrase simple).
RAISONS_SOUS_DOSSIER = {'captures': "Décoché : ce dossier semble contenir des captures d'écran, pas des photos.",
                        'fonds': "Décoché : ce dossier semble contenir des fonds d'écran, pas des photos."}
_IDENTIFIANT = re.compile(r'[1-9][0-9]{0,17}')
_FOLDERID_PICTURES = '{33E28130-4E1E-4676-835A-98395C3BC3BB}'

# Sélection : copier, copier dans un dossier, mettre à la Corbeille. Jamais de chemin venu de la page : seulement des
# identifiants de photos présentes en base, au plus LIMITE_SELECTION par requête.
LIMITE_SELECTION = 2000
COPIE_BLOC = 1 << 20               # copie vers un dossier : par blocs de 1 Mo (annulable, avancement)
COPIE_MARGE = 16 * 1024 * 1024     # place gardée libre sur le disque de destination
REFUS_MAX = 200                    # refus détaillés dans un compte rendu (le nombre total est toujours donné)
INTROUVABLE = "Cette photo n'est plus dans MyPhotos."
PAS_UNE_PHOTO = "Ce fichier n'est pas une photo."
PAR_UN_LIEN = ("Cette photo est atteinte par un raccourci vers un autre emplacement (lien symbolique ou jonction) : "
               "MyPhotos n'y touche pas.")
CHANGEE = ("Cette photo a changé depuis son analyse : MyPhotos ne la supprime pas. Réessayez quand l'analyse l'aura "
           "revue.")
SANS_CORBEILLE = "Ce disque n'a pas de Corbeille : MyPhotos ne supprime pas définitivement."
CORBEILLE_DESACTIVEE = "La Corbeille de ce disque est désactivée : MyPhotos ne supprime pas définitivement."
TROP_GRANDE = ("Cette photo est trop grande pour la Corbeille de ce disque : MyPhotos ne la supprime pas "
               "définitivement.")
CHEMIN_TROP_LONG = "Le chemin de cette photo est trop long pour Windows."
# Un nom de dossier qui finit par un point (« Vacances. », possible hors de l'Explorateur) : sans le préfixe \\?\, Windows
# retire ce point et désigne un AUTRE dossier (« Vacances »). Mise à la Corbeille, copie, ouverture : refusées.
CHEMIN_AMBIGU = ("Un dossier de cette photo a un nom que Windows confond avec un autre (il finit par un point) : "
                 "MyPhotos n'y touche pas.")
DESTINATION_AMBIGUE = ("Le nom de ce dossier finit par un point ou une espace : Windows le confondrait avec un autre "
                       "dossier. Choisissez-en un autre.")
PAS_MISE_A_LA_CORBEILLE = ("Windows n'a pas pu mettre cette photo à la Corbeille : elle est peut-être ouverte dans un "
                           "autre programme.")
GARDEE = "Windows allait la supprimer définitivement : elle a été gardée."
PHOTO_ILLISIBLE = "Cette photo n'a pas pu être lue : le disque est peut-être débranché."

# Doublons (voir doublons.py) : calcul en arrière-plan, avec la base et numpy seulement (ni moteur ni carte graphique).
DOUBLONS_RELANCE_S = 30            # index changé pendant l'analyse : nouveau calcul demandé par la page, au plus toutes les 30 s
DOUBLONS_ECHEC = 'La recherche des doublons n\'a pas abouti. Réessayez ; si cela continue, relancez MyPhotos.'
DOUBLONS_GARDER = 'À garder : la meilleure des photos qui restent.'

# Le public ne connaît ni « modèle » ni « indexation » : on parle de moteur de reconnaissance et d'analyse.
_JARGON = ((re.compile(r'\bmodèles\b'), 'moteurs'), (re.compile(r'\bmodèle\b'), 'moteur'),
           (re.compile(r'\bModèles\b'), 'Moteurs'), (re.compile(r'\bModèle\b'), 'Moteur'),
           (re.compile(r'\bindexation\b'), 'analyse'), (re.compile(r'\bIndexation\b'), 'Analyse'),
           # Ancien nom du logiciel, encore présent dans quelques messages du cœur.
           (re.compile(r'\b(de|à) la [Pp]hotothèque\b'), r'\1 MyPhotos'),
           (re.compile(r'\bla [Pp]hotothèque\b'), 'MyPhotos'), (re.compile(r'\b[Pp]hotothèque\b'), 'MyPhotos'))
# Messages bruts en anglais (SQLite, ONNX Runtime, numéros d'erreur) qu'une exception peut glisser dans un message
# du cœur (« L'indexation s'est arrêtée sur une erreur inattendue : database is locked ») : jamais tels quels.
_ANGLAIS = (
    (re.compile(r'\[ONNXRuntimeError\][^()]*'), 'erreur interne du moteur'),
    (re.compile(r'\[(?:WinError|Errno) -?\d+\]\s*'), ''),
    (re.compile(r'\bdatabase (?:table )?is locked\b', re.I), 'les fichiers de MyPhotos sont momentanément occupés'),
    (re.compile(r'\bdatabase or disk is full\b', re.I), 'le disque est plein'),
    (re.compile(r'\bdisk I/O error\b', re.I), "erreur de lecture ou d'écriture sur le disque"),
    (re.compile(r'\bunable to open database file\b', re.I), 'les fichiers de MyPhotos sont inaccessibles'),
    (re.compile(r'\battempt to write a readonly database\b', re.I), 'les fichiers de MyPhotos sont en lecture seule'),
    (re.compile(r'\b(?:database disk image is malformed|file is not a database)\b', re.I),
     'un fichier de MyPhotos est abîmé'),
    (re.compile(r'\bno such (?:table|column)\b[^.;)]*', re.I), 'un fichier de MyPhotos est incomplet'),
    (re.compile(r'\bout of memory\b', re.I), 'mémoire insuffisante'),
)


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
    """Message du cœur dit avec les mots du public : ni « modèle » ni « indexation », jamais de message brut en
    anglais (traduit, en commençant par une majuscule s'il ouvre la phrase)."""
    if not isinstance(texte, str):
        return texte
    for motif, mot in _ANGLAIS:
        m = motif.search(texte)
        if m and m.start() == 0 and mot:
            texte = mot[0].upper() + mot[1:] + texte[m.end():]
        texte = motif.sub(mot, texte)
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

class _FormatSansJeton(logging.Formatter):
    """Filet : un jeton d'accès glissé dans un message (adresse d'entrée « /?jeton=… » écrite par pywebview en mode
    débogage, cookie…) n'arrive jamais dans le journal, ni dans sa trace d'erreur."""
    _MOTIF = re.compile(r'(jeton=|myphotos-\d+=)[A-Za-z0-9_-]+')

    def format(self, record):
        return self._MOTIF.sub(r'\1…', super().format(record))


def configurer_journal():
    """Journal borné (config.JOURNAL, rotation à 1 Mo, 2 copies) ; console seulement depuis les sources."""
    racine = logging.getLogger('myphotos')
    if getattr(racine, '_myphotos', False):
        return
    racine._myphotos = True
    racine.setLevel(logging.INFO)
    racine.propagate = False
    forme = _FormatSansJeton('%(asctime)s %(levelname)-7s %(name)s : %(message)s', '%Y-%m-%d %H:%M:%S')
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
_32_BITS = ctypes.sizeof(ctypes.c_void_p) == 4     # en 32 bits, shellapi.h serre ses structures (pshpack1.h)


class _OperationFichiers(ctypes.Structure):
    """SHFILEOPSTRUCTW."""
    if _32_BITS:
        _pack_ = 1
    _fields_ = [('hwnd', ctypes.c_void_p), ('wFunc', ctypes.c_uint), ('pFrom', ctypes.c_void_p),
                ('pTo', ctypes.c_void_p), ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', ctypes.c_int),
                ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', ctypes.c_wchar_p)]


class _InfosCorbeille(ctypes.Structure):
    """SHQUERYRBINFO."""
    if _32_BITS:
        _pack_ = 1
    _fields_ = [('cbSize', ctypes.c_uint32), ('i64Size', ctypes.c_int64), ('i64NumItems', ctypes.c_int64)]


def _dll(nom):
    """Instance privée d'une DLL : nos argtypes ne touchent pas ceux des autres modules."""
    if nom not in _DLL:
        d = ctypes.WinDLL(nom, use_last_error=True)
        v, u, b, w = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p
        signatures = {
            'user32': {'GetForegroundWindow': ([], v), 'GetWindowThreadProcessId': ([v, v], u),
                       'AttachThreadInput': ([u, u, b], b), 'SetForegroundWindow': ([v], b),
                       'BringWindowToTop': ([v], b), 'GetAncestor': ([v, u], v),
                       'MessageBoxW': ([v, w, w, u], b),
                       'CreateWindowExW': ([u, w, w, u, b, b, b, b, v, v, v, v], v), 'DestroyWindow': ([v], b),
                       'OpenClipboard': ([v], b), 'EmptyClipboard': ([], b), 'CloseClipboard': ([], b),
                       'SetClipboardData': ([u, v], v), 'RegisterClipboardFormatW': ([w], u),
                       'GetOpenClipboardWindow': ([], v)},
            'kernel32': {'GetCurrentThreadId': ([], u), 'GlobalAlloc': ([u, ctypes.c_size_t], v),
                         'GlobalLock': ([v], v), 'GlobalUnlock': ([v], b), 'GlobalFree': ([v], v),
                         'GetDriveTypeW': ([w], u), 'GetVolumePathNameW': ([w, w, u], b),
                         'GetVolumeNameForVolumeMountPointW': ([w, w, u], b), 'GetShortPathNameW': ([w, w, u], u),
                         'CreateFileW': ([w, u, u, v, u, u, v], v), 'CloseHandle': ([v], b),
                         'GetFinalPathNameByHandleW': ([v, w, u, u], u),
                         'WriteFile': ([v, ctypes.c_char_p, u, ctypes.POINTER(u), v], b), 'LocalFree': ([v], v)},
            'advapi32': {'ConvertStringSecurityDescriptorToSecurityDescriptorW': ([w, u, ctypes.POINTER(v), v], b)},
            'shell32': {'SHGetKnownFolderPath': ([v, u, v, ctypes.POINTER(v)], ctypes.c_long),
                        'ILCreateFromPathW': ([w], v), 'ILFree': ([v], None),
                        'SHOpenFolderAndSelectItems': ([v, u, v, u], ctypes.c_long),
                        'SHFileOperationW': ([ctypes.POINTER(_OperationFichiers)], b),
                        'SHQueryRecycleBinW': ([w, ctypes.POINTER(_InfosCorbeille)], ctypes.c_long)},
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
    """Dossiers proposés au premier lancement : Images, puis OneDrive\\Images s'il existe (sans doublon).

    Essais (MYPHOTOS_ESSAI_NEUTRE=1) : aucun, pour qu'un MyPhotos.exe lancé tel quel par un essai (l'installateur
    réel) ne parcoure jamais les dossiers d'images de l'ordinateur, même pour compter ; comme neutre=True dans
    tests/test_serveur.py."""
    if os.environ.get('MYPHOTOS_ESSAI_NEUTRE') == '1':
        return []
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


def message_actualisation(b):
    """Réponse claire à la fin d'une actualisation : « 3 nouvelles photos, 5 012 photos retirées. »"""
    nouvelles, retirees, injoignables = b.get('nouvelles') or 0, b.get('retirees') or 0, b.get('injoignables') or []
    morceaux = []
    if nouvelles:
        morceaux.append(f'{_nombre_fr(nouvelles)} nouvelle photo' if nouvelles == 1
                        else f'{_nombre_fr(nouvelles)} nouvelles photos')
    if retirees:
        morceaux.append(f'{_nombre_fr(retirees)} photo retirée' if retirees == 1
                        else f'{_nombre_fr(retirees)} photos retirées')
    texte = ('Actualisation terminée : ' + ', '.join(morceaux) + '.') if morceaux \
        else 'Actualisation terminée : rien n\'a changé.'
    if nouvelles:
        texte += ' Les nouvelles photos vont être analysées.' if nouvelles > 1 else ' Elle va être analysée.'
    # Un autre disque sur la lettre d'un dossier suivi (autre numéro de volume, ses photos absentes) : dit à part.
    autres = b.get('autres_disques') or []
    injoignables = [x for x in injoignables if x not in autres]
    if len(autres) == 1:
        texte += (f' Un autre disque occupe la lettre du dossier « {autres[0]} » : ses photos sont gardées jusqu\'au '
                  'retour de son disque.')
    elif autres:
        texte += (f' Un autre disque occupe la lettre de {len(autres)} dossiers : leurs photos sont gardées jusqu\'au '
                  'retour de leur disque.')
    if len(injoignables) == 1:
        texte += (f' Le dossier « {injoignables[0]} » est inaccessible pour le moment (disque débranché ?) : ses photos '
                  'sont gardées.')
    elif injoignables:
        texte += (f' {len(injoignables)} dossiers sont inaccessibles pour le moment (disque débranché ?) : leurs '
                  'photos sont gardées.')
    return texte


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


def compris_tel_quel(chemin):
    """Vrai si Windows comprend ce chemin habituel (sans le préfixe \\\\?\\) tel quel. Sinon il le réinterprète : un nom
    de dossier qui finit par un point (« Vacances. », que l'Explorateur ne sait pas créer mais qu'une archive ou un
    autre système peut laisser) perd ce point et le chemin désigne alors un AUTRE dossier, « Vacances »."""
    if os.name != 'nt':
        return True
    try:
        return os.path.abspath(chemin) == chemin          # GetFullPathNameW : la lecture qu'en fera Windows
    except (OSError, ValueError):
        return False


def chemin_pour_windows(chemin):
    """Chemin à donner à l'Explorateur, au presse-papiers et à la Corbeille, qui n'acceptent pas le préfixe \\\\?\\ : le
    chemin habituel s'il désigne bien CE fichier (moins de 260 caractères, compris tel quel), sinon son nom court
    (8.3) ; None si aucun ne convient (voir refus_chemin_windows)."""
    if os.name != 'nt':
        return chemin if len(chemin) < 260 else None
    if len(chemin) < 260 and compris_tel_quel(chemin):
        return chemin
    tampon = ctypes.create_unicode_buffer(32768)
    n = _dll('kernel32').GetShortPathNameW(chemin_systeme(chemin), tampon, len(tampon))
    court = chemin_affiche(tampon.value) if 0 < n < len(tampon) else ''
    return court if court and len(court) < 260 and compris_tel_quel(court) else None


def refus_chemin_windows(chemin):
    """Pourquoi chemin_pour_windows n'a rien trouvé : chemin trop long, ou nom que Windows comprendrait autrement."""
    return CHEMIN_TROP_LONG if compris_tel_quel(chemin) else CHEMIN_AMBIGU


# ---------------------------------------------------------------- dossiers tenus pendant une action

_INVALIDE = ctypes.c_void_p(-1).value
_FILE_LIST_DIRECTORY, _FILE_READ_ATTRIBUTES = 0x0001, 0x0080
_FILE_SHARE_READ, _FILE_SHARE_WRITE, _FILE_SHARE_DELETE = 0x1, 0x2, 0x4
_OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS = 3, 0x02000000


def _ouvrir_dossier(chemin, acces, partage):
    """Poignée Windows sur un dossier (les liens et jonctions sont suivis), ou None."""
    h = _dll('kernel32').CreateFileW(chemin_systeme(chemin), acces, partage, None, _OPEN_EXISTING,
                                     _FILE_FLAG_BACKUP_SEMANTICS, None)
    return None if h in (None, 0, _INVALIDE) else h


def _emplacement_reel(h):
    """Chemin réel (habituel) de l'objet tenu par cette poignée, liens et jonctions résolus ; None si inconnu."""
    tampon = ctypes.create_unicode_buffer(32768)
    n = _dll('kernel32').GetFinalPathNameByHandleW(h, tampon, len(tampon), 0)
    return chemin_affiche(tampon.value) if 0 < n < len(tampon) else None


class GardeDossiers:
    """Tient ouverts les dossiers des photos pendant qu'on les vérifie puis qu'on agit (mise à la Corbeille, ouverture
    pour une copie). Sans partage de suppression, et avec un accès qui compte pour le partage (une poignée « attributs
    seuls » ne bloquerait rien), personne ne peut renommer ni supprimer un dossier tenu ; et NTFS refuse de renommer un
    dossier au-dessus d'une poignée ouverte. Le chemin ne peut donc plus être détourné par une jonction ou un lien posé
    entre la vérification et l'action (essai du 24/11/2026 : sans cela, un programme rival bien synchronisé envoyait à
    la Corbeille un fichier situé hors des dossiers suivis). Ailleurs que sous Windows : rien n'est tenu."""

    def __init__(self):
        self._poignees = {}                      # clé du dossier -> poignée (None : Windows a refusé)
        self._racines = {}                       # clé d'un dossier suivi -> son emplacement réel

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.relacher()

    def tenir(self, dossier):
        """Tient ce dossier (chemin habituel) jusqu'à relacher() ; faux si Windows refuse (dossier absent…)."""
        if os.name != 'nt':
            return True
        k = cle_chemin(dossier)
        if k not in self._poignees:
            self._poignees[k] = _ouvrir_dossier(dossier, _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES,
                                                _FILE_SHARE_READ | _FILE_SHARE_WRITE)
        return self._poignees[k] is not None

    def au_bon_endroit(self, dossier, racine):
        """Le dossier tenu est-il bien celui que désigne son chemin depuis le dossier suivi `racine`, sans lien ni
        jonction entre les deux ? Son emplacement réel doit être celui de la racine suivi du reste du chemin (la racine
        elle-même peut être un lien : c'est l'utilisateur qui l'a choisie)."""
        if os.name != 'nt':
            return True
        h = self._poignees.get(cle_chemin(dossier))
        if h is None:
            return False
        k = cle_chemin(racine)
        if k not in self._racines:
            hr = _ouvrir_dossier(racine, _FILE_READ_ATTRIBUTES,
                                 _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE)
            try:
                self._racines[k] = _emplacement_reel(hr) if hr is not None else None
            finally:
                if hr is not None:
                    _dll('kernel32').CloseHandle(hr)
        vraie_racine, reel = self._racines[k], _emplacement_reel(h)
        parties = _parties_sous(dossier, racine)
        if vraie_racine is None or reel is None or parties is None:
            return False
        return cle_chemin(reel) == cle_chemin(os.path.join(vraie_racine, *parties))

    def relacher(self):
        for h in self._poignees.values():
            if h is not None:
                _dll('kernel32').CloseHandle(h)
        self._poignees.clear()


# ---------------------------------------------------------------- presse-papiers de Windows

CF_HDROP = 15
CF_UNICODETEXT = 13
_GMEM = 0x0002 | 0x0040                    # GMEM_MOVEABLE | GMEM_ZEROINIT
_HWND_MESSAGE = -3                         # fenêtre « message-only » : jamais affichée
_DROPEFFECT_COPY = 1
PRESSE_PAPIERS_ESSAIS = 100                # presse-papiers tenu par un autre programme : 100 essais…
PRESSE_PAPIERS_PAUSE_S = 0.05              # … espacés de 50 ms (5 s : un gestionnaire de presse-papiers peut le garder)


class PressePapiersOccupe(OSError):
    """Un autre programme garde le presse-papiers ouvert."""


AIDE_LIGNES = 150                          # « Copier pour demander de l'aide » : dernières lignes du journal


def _sur_aide(fonction):
    """fonction(), ou None si elle échoue (le texte d'aide doit toujours pouvoir être copié)."""
    try:
        return fonction()
    except Exception:
        return None


def _nombre_aide(n):
    return f'{int(n or 0):,}'.replace(',', ' ')


def _programme_de(fenetre):
    """Nom du programme d'une fenêtre (« notepad.exe »), pour le journal seulement ; « ? » si inconnu."""
    if not fenetre:
        return '?'
    try:
        pid = ctypes.c_uint32()
        f = ctypes.WinDLL('user32', use_last_error=True).GetWindowThreadProcessId
        f.argtypes, f.restype = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_uint32
        f(fenetre, ctypes.byref(pid))
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes, k.OpenProcess.restype = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32], ctypes.c_void_p
        k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
                                                 ctypes.POINTER(ctypes.c_uint32)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return f'processus {pid.value}'
        try:
            tampon = ctypes.create_unicode_buffer(1024)
            n = ctypes.c_uint32(len(tampon))
            if k.QueryFullProcessImageNameW(h, 0, tampon, ctypes.byref(n)):
                return os.path.basename(tampon.value)
        finally:
            k.CloseHandle(h)
        return f'processus {pid.value}'
    except Exception:
        return '?'


def dropfiles(chemins):
    """Contenu du format CF_HDROP : structure DROPFILES (pFiles = 20, pt = (0, 0), fNC = 0, fWide = 1), puis chaque
    chemin en UTF-16 terminé par un zéro, et un zéro de plus pour finir la liste."""
    return struct.pack('<IiiII', 20, 0, 0, 0, 1) + (''.join(c + '\0' for c in chemins) + '\0').encode('utf-16-le')


def _poser(user32, kernel32, format_, octets):
    h = kernel32.GlobalAlloc(_GMEM, len(octets))
    if not h:
        raise MemoryError('mémoire insuffisante pour le presse-papiers')
    try:
        p = kernel32.GlobalLock(h)
        if not p:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(p, octets, len(octets))
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(format_, h):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel32.GlobalFree(h)                  # refusé par le presse-papiers : la mémoire reste à nous
        raise


def mettre_au_presse_papiers(chemins, essais=PRESSE_PAPIERS_ESSAIS, pause=PRESSE_PAPIERS_PAUSE_S):
    """Met ces fichiers dans le presse-papiers, comme « Copier » dans l'Explorateur : format CF_HDROP, plus « Preferred
    DropEffect » = copie (pas couper). Un Ctrl+V dans un dossier, un e-mail ou un document les colle.

    Une fenêtre invisible en est la propriétaire le temps de l'écriture, puis elle est détruite : les données restent,
    et aucun autre programme n'attendra jamais qu'elle réponde (elle n'a pas de boucle de messages)."""
    def remplir(user32, kernel32):
        _poser(user32, kernel32, CF_HDROP, dropfiles(chemins))
        effet = user32.RegisterClipboardFormatW('Preferred DropEffect')
        if effet:
            _poser(user32, kernel32, effet, struct.pack('<I', _DROPEFFECT_COPY))
    _remplir_presse_papiers(remplir, essais, pause)


def mettre_texte_au_presse_papiers(texte, essais=PRESSE_PAPIERS_ESSAIS, pause=PRESSE_PAPIERS_PAUSE_S):
    """Met ce texte dans le presse-papiers (CF_UNICODETEXT, fins de ligne de Windows) : un Ctrl+V le colle dans un
    e-mail, WhatsApp ou un document (« Copier pour demander de l'aide », 0.4.1)."""
    octets = (texte.replace('\r\n', '\n').replace('\n', '\r\n') + '\0').encode('utf-16-le')
    _remplir_presse_papiers(lambda user32, kernel32: _poser(user32, kernel32, CF_UNICODETEXT, octets), essais, pause)


def _remplir_presse_papiers(remplir, essais, pause):
    """Ouvre le presse-papiers (réessaie s'il est tenu par un autre programme), le vide, puis remplir(user32,
    kernel32) y pose les données ; voir mettre_au_presse_papiers."""
    if os.name != 'nt':
        raise OSError('presse-papiers de Windows indisponible')
    user32, kernel32 = _dll('user32'), _dll('kernel32')
    fenetre = user32.CreateWindowExW(0, 'STATIC', NOM, 0, 0, 0, 0, 0, _HWND_MESSAGE, None, None, None)
    if not fenetre:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        for _ in range(max(1, essais)):
            if user32.OpenClipboard(fenetre):
                break
            time.sleep(pause)
        else:
            raise PressePapiersOccupe(f'presse-papiers gardé ouvert par {_programme_de(user32.GetOpenClipboardWindow())}')
        try:
            if not user32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            remplir(user32, kernel32)
        finally:
            user32.CloseClipboard()
    finally:
        user32.DestroyWindow(fenetre)


class PressePapiers:
    """Fil réservé au presse-papiers : les demandes passent une à une, toujours par le même fil."""

    def __init__(self):
        self._demandes = queue.Queue()
        self._fil = None
        self._verrou = threading.Lock()

    def copier(self, chemins, delai=30):
        """Fichiers (comme « Copier » dans l'Explorateur) ; rend leur nombre."""
        chemins = list(chemins)

        def faire():
            mettre_au_presse_papiers(chemins)
            return len(chemins)
        return self._demander(faire, delai)

    def copier_texte(self, texte, delai=30):
        """Texte (CF_UNICODETEXT) ; rend son nombre de caractères."""
        def faire():
            mettre_texte_au_presse_papiers(texte)
            return len(texte)
        return self._demander(faire, delai)

    def _demander(self, faire, delai):
        with self._verrou:
            if self._fil is None or not self._fil.is_alive():
                self._fil = threading.Thread(target=self._boucle, name='myphotos-presse-papiers', daemon=True)
                self._fil.start()
        reponse = concurrent.futures.Future()
        self._demandes.put((reponse, faire))
        return reponse.result(timeout=delai)

    def _boucle(self):
        while True:
            reponse, faire = self._demandes.get()
            if not reponse.set_running_or_notify_cancel():
                continue
            try:
                r = faire()
            except BaseException as e:
                reponse.set_exception(e)
            else:
                reponse.set_result(r)


# ---------------------------------------------------------------- Corbeille de Windows

FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0004, 0x0010, 0x0040, 0x0400
FOF_NO_CONNECTED_ELEMENTS, FOF_WANTNUKEWARNING = 0x2000, 0x4000
DRIVE_FIXED = 3
CORBEILLE_PART_DEFAUT = 20                 # taille de la Corbeille non réglée : 1/20 du disque (5 %, prudent)


def racine_volume(chemin):
    """Racine du volume qui porte ce chemin : « C:\\ », « \\\\serveur\\partage\\ » ou un dossier de montage."""
    tampon = ctypes.create_unicode_buffer(32768)
    if not _dll('kernel32').GetVolumePathNameW(chemin_systeme(chemin), tampon, len(tampon)):
        raise ctypes.WinError(ctypes.get_last_error())
    return chemin_affiche(tampon.value)


def _reglages_corbeille(racine):
    """(detruire, capacite) : la Corbeille de ce volume supprime-t-elle définitivement (réglage « Ne pas déplacer les
    fichiers vers la Corbeille », ou stratégie NoRecycleFiles), et sa taille maximale en octets (None : non réglée)."""
    import winreg
    detruire, capacite = False, None
    for ruche in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(ruche, r'Software\Microsoft\Windows\CurrentVersion\Policies\Explorer') as k:
                detruire = detruire or winreg.QueryValueEx(k, 'NoRecycleFiles')[0] == 1
        except OSError:
            pass
    tampon = ctypes.create_unicode_buffer(64)
    if _dll('kernel32').GetVolumeNameForVolumeMountPointW(racine, tampon, len(tampon)):
        guid = re.search(r'\{[0-9A-Fa-f-]{36}\}', tampon.value)
        if guid:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Software\\Microsoft\\Windows\\CurrentVersion\\'
                                    'Explorer\\BitBucket\\Volume\\' + guid.group(0)) as k:
                    for nom in ('NukeOnDelete', 'MaxCapacity'):
                        try:
                            v = winreg.QueryValueEx(k, nom)[0]
                        except OSError:
                            continue
                        if nom == 'NukeOnDelete':
                            detruire = detruire or v == 1
                        elif isinstance(v, int) and v > 0:
                            capacite = v * 1024 * 1024
            except OSError:
                pass
    return detruire, capacite


def infos_corbeille(racine):
    """Ce volume a-t-il une Corbeille où envoyer des photos ? {'ok', 'raison', 'capacite'} ; capacite : taille
    maximale d'un fichier qu'elle accepte, en octets (au-delà, Windows supprimerait définitivement).

    Refusés : tout ce qui n'est pas un disque fixe (partage réseau, Freebox, clé USB, carte mémoire…), un volume pour
    lequel SHQueryRecycleBinW échoue, et une Corbeille réglée pour supprimer définitivement."""
    non = {'ok': False, 'raison': SANS_CORBEILLE, 'capacite': None}
    if os.name != 'nt':
        return non
    try:
        if _dll('kernel32').GetDriveTypeW(racine) != DRIVE_FIXED:
            return non
        info = _InfosCorbeille()
        info.cbSize = ctypes.sizeof(info)
        if _dll('shell32').SHQueryRecycleBinW(racine, ctypes.byref(info)) != 0:
            return non
        detruire, capacite = _reglages_corbeille(racine)
    except OSError:
        journal.info('Corbeille du volume %s : vérification impossible', racine, exc_info=True)
        return non
    if detruire:
        return {'ok': False, 'raison': CORBEILLE_DESACTIVEE, 'capacite': None}
    if capacite is None:
        try:
            capacite = shutil.disk_usage(racine).total // CORBEILLE_PART_DEFAUT
        except OSError:
            capacite = None
    return {'ok': True, 'raison': None, 'capacite': capacite}


def mettre_a_la_corbeille(chemins):
    """Envoie ces fichiers à la Corbeille : SHFileOperationW, FO_DELETE avec FOF_ALLOWUNDO. Aucune fenêtre (FOF_SILENT,
    FOF_NOCONFIRMATION, FOF_NOERRORUI), sauf une : FOF_WANTNUKEWARNING. Si Windows s'apprêtait malgré tout à supprimer
    définitivement un fichier (ce que les vérifications d'infos_corbeille écartent), il le demanderait. Jamais les
    fichiers « liés » (FOF_NO_CONNECTED_ELEMENTS). Chemins complets, de moins de 260 caractères, sans * ni ?.

    Renvoie (code, abandon) ; l'appelant regarde ensuite quels fichiers sont partis."""
    if os.name != 'nt':
        raise OSError('Corbeille de Windows indisponible')
    if not chemins:
        return 0, False
    tampon = ctypes.create_string_buffer((''.join(c + '\0' for c in chemins) + '\0').encode('utf-16-le') + b'\0\0')
    op = _OperationFichiers()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.addressof(tampon)
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_NO_CONNECTED_ELEMENTS
                 | FOF_WANTNUKEWARNING)
    ole32 = _dll('ole32')
    init = ole32.CoInitializeEx(None, 0x2)                   # COINIT_APARTMENTTHREADED
    try:
        code = _dll('shell32').SHFileOperationW(ctypes.byref(op))
    finally:
        if init in (0, 1):
            ole32.CoUninitialize()
    return code, bool(op.fAnyOperationsAborted)


def _est_un_lien(st):
    """Lien symbolique ou jonction (comme indexation.Parcours, qui ne les suit jamais). Les autres points d'analyse,
    dont les fichiers OneDrive à la demande, n'en sont pas."""
    return stat.S_ISLNK(st.st_mode) or (bool(getattr(st, 'st_file_attributes', 0) & 0x400)
                                       and getattr(st, 'st_reparse_tag', 0) in _BALISES_LIENS)


def _parties_sous(chemin, racine):
    """Éléments de `chemin` situés sous le dossier `racine`, ou None s'il n'est pas dessous. Comparaison élément par
    élément, sans os.path.relpath : celui-ci passe par GetFullPathNameW, qui retire le point final d'un nom de dossier
    (« Vacances. » devenait « Vacances », un autre dossier)."""
    pc = chemin_affiche(chemin).rstrip(os.sep).split(os.sep)
    pr = chemin_affiche(racine).rstrip(os.sep).split(os.sep)
    if len(pc) < len(pr) or [os.path.normcase(x) for x in pc[:len(pr)]] != [os.path.normcase(x) for x in pr]:
        return None
    return pc[len(pr):]


def _passe_par_un_lien(chemin, racine):
    """Vrai si l'un des dossiers entre `racine` (le dossier suivi, choisi par l'utilisateur) et le fichier est un lien
    symbolique ou une jonction : l'analyse ne les suit pas, la suppression non plus."""
    parties = _parties_sous(chemin, racine)
    if not parties or any(x in ('', '.', '..') for x in parties):
        return True
    courant = racine
    for nom in parties[:-1]:
        courant = os.path.join(courant, nom)
        try:
            if _est_un_lien(os.lstat(chemin_systeme(courant))):
                return True
        except OSError:
            return True
    return False


def message_copie(e):
    """Panne du côté du dossier de destination, en français simple."""
    if getattr(e, 'winerror', None) in (39, 112) or getattr(e, 'errno', None) == errno.ENOSPC:
        return 'Le disque de destination est plein : la copie s\'est arrêtée.'
    if isinstance(e, PermissionError) or getattr(e, 'winerror', None) == 5:
        return 'MyPhotos n\'a pas le droit d\'écrire dans ce dossier : choisissez-en un autre.'
    return 'La copie s\'est arrêtée : le dossier de destination n\'est plus accessible (disque débranché ?).'


def _effacer_provisoire(chemin):
    """Efface un fichier provisoire de MyPhotos (copie interrompue) ; jamais une photo."""
    try:
        os.chmod(chemin, stat.S_IWRITE)
    except OSError:
        pass
    try:
        os.unlink(chemin)
    except FileNotFoundError:
        pass
    except OSError:
        journal.warning('fichier provisoire impossible à effacer : %s', os.path.basename(chemin))


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


def informer(message):
    """Message d'information au lancement (une ancienne MyPhotos est ouverte…) : boîte de dialogue depuis
    l'exécutable, console depuis les sources."""
    journal.info('%s', message)
    if SANS_PAGE or os.name != 'nt' or not config.GELE:
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return
    try:
        _dll('user32').MessageBoxW(None, message, NOM, 0x40 | 0x10000 | 0x40000)  # information, premier plan, dessus
    except Exception:
        pass


def ouvrir_page(adresse):
    try:
        os.startfile(adresse)
    except (AttributeError, OSError):
        import webbrowser
        webbrowser.open(adresse)


# ---------------------------------------------------------------- jeton d'accès (0.4.0)

def creer_jeton(chemin=None):
    """Nouveau jeton d'accès (256 bits), écrit d'un coup dans les données (fichier provisoire, puis remplacement) :
    une relance le lit pour rouvrir la page (lire_jeton). Appelé seulement par l'instance qui vient d'obtenir le
    port : jamais par-dessus le jeton d'une MyPhotos déjà lancée. Rend le jeton, même s'il n'a pas pu être écrit
    (MyPhotos marche alors quand même ; seule une relance ne pourrait pas rouvrir sa page)."""
    chemin = Path(chemin or config.JETON)
    jeton = secrets.token_urlsafe(32)
    provisoire = chemin.with_name(chemin.name + '.part')
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        provisoire.unlink(missing_ok=True)               # reste d'un arrêt brutal : recréé avec ses propres droits
        ecrire_prive(provisoire, jeton.encode('ascii'))
        remplacer_fichier(provisoire, chemin)            # le fichier renommé garde ses droits
    except OSError:
        journal.warning('jeton d\'accès impossible à écrire dans les données : une relance ne rouvrira pas la page')
        try:
            provisoire.unlink(missing_ok=True)           # pas de jeton qui traîne à côté
        except OSError:
            pass
    return jeton


class _ATTRIBUTS_SECURITE(ctypes.Structure):
    """SECURITY_ATTRIBUTES."""
    _fields_ = [('nLength', ctypes.c_uint32), ('lpSecurityDescriptor', ctypes.c_void_p), ('bInheritHandle', ctypes.c_int)]


def droits_prives():
    """SDDL d'une liste de droits PROTÉGÉE (rien d'hérité du dossier), qui ne donne accès qu'à notre compte Windows ;
    « OW » (le propriétaire du fichier) si notre SID est illisible."""
    sid = fenetre.sid_compte()
    return f'D:P(A;;FA;;;{sid})' if sid else 'D:P(A;;FA;;;OW)'


def ecrire_prive(chemin, octets):
    """Crée le fichier `chemin` (qui ne doit pas exister) avec droits_prives(), dès sa création (CreateFileW) : il
    reste privé même quand le dossier ne l'est pas. Relecture adverse de la 0.4.0 : avec MYPHOTOS_DOSSIER sur un
    autre disque (D:\\…, « Utilisateurs authentifiés : modification » hérité), les autres comptes lisaient le jeton."""
    if os.name != 'nt':
        fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(octets)
        return
    k32 = _dll('kernel32')
    descripteur = ctypes.c_void_p()
    if not _dll('advapi32').ConvertStringSecurityDescriptorToSecurityDescriptorW(
            droits_prives(), 1, ctypes.byref(descripteur), None):                  # SDDL_REVISION_1
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributs = _ATTRIBUTS_SECURITE(ctypes.sizeof(_ATTRIBUTS_SECURITE), descripteur.value, 0)
        h = k32.CreateFileW(str(chemin), 0x40000000, 0, ctypes.addressof(attributs), 1, 0x80, None)
        if not h or h == ctypes.c_void_p(-1).value:      # GENERIC_WRITE, aucun partage, CREATE_NEW, NORMAL
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ecrit = ctypes.c_uint32()
            if not k32.WriteFile(h, octets, len(octets), ctypes.byref(ecrit), None) or ecrit.value != len(octets):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k32.CloseHandle(h)
    finally:
        k32.LocalFree(descripteur)


def remplacer_fichier(source, cible, essais=None, pause=None):
    """os.replace, réessayé un moment (JETON_ESSAIS × JETON_PAUSE_S) : Windows refuse de remplacer un fichier qu'un
    autre programme lit à cet instant (une relance qui lit le jeton, un antivirus). Relecture adverse de la 0.4.0 :
    sans cela, l'ancien jeton restait dans le fichier, et chaque relance de la séance se croyait face à un autre
    compte."""
    essais = JETON_ESSAIS if essais is None else essais
    pause = JETON_PAUSE_S if pause is None else pause
    for i in range(max(1, essais)):
        try:
            return os.replace(source, cible)
        except PermissionError:
            if i >= essais - 1:
                raise
            time.sleep(pause)


def lire_jeton(chemin=None):
    """Jeton d'accès de la MyPhotos lancée avec ces données, ou None (absent, illisible, abîmé)."""
    try:
        texte = Path(chemin or config.JETON).read_text(encoding='ascii').strip()
    except (OSError, ValueError):
        return None
    return texte if _JETON.fullmatch(texte) else None


def jeton_egal(donne, jeton):
    """Comparaison en temps constant (hmac.compare_digest) ; jamais vrai sans jeton."""
    if not jeton or not isinstance(donne, str):
        return False
    return hmac.compare_digest(donne.encode('utf-8', 'replace'), jeton.encode('ascii'))


def nom_cookie(port):
    return COOKIE_JETON.format(port=int(port))


def adresse_entree(port, jeton):
    """Adresse d'entrée de la page : http://127.0.0.1:<port>/?jeton=… ; le serveur pose le cookie et redirige vers
    « / ». Pour la fenêtre, Edge, le navigateur et la relance ; jamais dans le journal."""
    return f'http://{config.HOTE}:{int(port)}/?{PARAMETRE_JETON}={jeton}'


# ---------------------------------------------------------------- choix d'un dossier (fenêtre de Windows)

class ChoixDossier:
    """Fenêtre « Choisir un dossier » de Windows (IFileOpenDialog, voir dialogue.py), ouverte dans un fil COM STA qui
    lui est réservé, devant la page : elle appartient à une petite fenêtre hôte invisible de MyPhotos, toujours
    au-dessus, posée au milieu de la fenêtre de MyPhotos (zone : celle d'Edge, donnée par fenetre.FenetreEdge) quand
    on la connaît. Une seule à la fois ; annuler renvoie None. Depuis la 0.4.0, sans tkinter ni Tcl/Tk.

    Délai dépassé (CHOIX_DOSSIER_S) : la fenêtre est fermée comme par « Annuler » (dialogue.fermer, par son fil),
    pour qu'aucun choix fait ensuite ne se perde ; un choix fait à cet instant est rendu. Si elle ne se ferme pas
    (FERMETURE_S), la page l'apprend (« toujours ouverte », 409). Avant : la requête répondait « annulé », la fenêtre
    restait ouverte, et le dossier choisi ensuite était perdu.

    Modes « edge » et « navigateur » seulement : dans la fenêtre de MyPhotos, fenetre.ChoixDossierFenetre la
    remplace (la même fenêtre de Windows, qui appartient alors à la fenêtre MyPhotos)."""

    def __init__(self, ouvrir=None, fermer=None):
        self._ouvrir = ouvrir or dialogue.ouvrir_devant    # (titre, initial, zone) -> chemin ou None
        self._fermer = fermer or dialogue.fermer           # (identifiant Windows du fil) -> fenêtres fermées
        self.zone = None                                   # () -> (gauche, haut, droite, bas) ou None
        self._fil_id = None                                # fil de la fenêtre ouverte (identifiant Windows)
        self._ouverte = threading.Event()
        self._verrou = threading.Lock()

    def demander(self, initial=None, delai=CHOIX_DOSSIER_S, titre='Choisissez un dossier de photos'):
        with self._verrou:
            if self._ouverte.is_set():
                raise Refus("La fenêtre de choix d'un dossier est déjà ouverte : regardez dans la barre des "
                            "tâches, en bas de l'écran.", 409)
            self._ouverte.set()
        reponse = concurrent.futures.Future()
        try:
            threading.Thread(target=self._fil, args=(reponse, initial, titre), name='myphotos-choix-dossier',
                             daemon=True).start()
        except BaseException:
            self._ouverte.clear()
            raise
        try:
            return reponse.result(timeout=delai)
        except concurrent.futures.TimeoutError:
            return self._expirer(reponse, delai)

    def _expirer(self, reponse, delai):
        journal.info('fenêtre « Choisir un dossier » ouverte depuis %d s : fermée', delai)
        fin = time.monotonic() + FERMETURE_S
        while True:
            fil = self._fil_id
            if fil:
                try:
                    self._fermer(fil)
                except Exception:
                    journal.debug('fenêtre « Choisir un dossier » impossible à fermer', exc_info=True)
            try:
                return reponse.result(timeout=0.25)        # fermée : None ; choisie à l'instant : le dossier
            except concurrent.futures.TimeoutError:
                if time.monotonic() >= fin:
                    raise Refus("La fenêtre de choix d'un dossier est toujours ouverte : choisissez-y un dossier ou "
                                "fermez-la, puis réessayez.", 409)

    def _fil(self, reponse, initial, titre):
        resultat = erreur = None
        self._fil_id = threading.get_native_id()
        try:
            zone = None
            if self.zone is not None:
                try:
                    zone = self.zone()
                except Exception:
                    journal.debug('place de la fenêtre de MyPhotos inconnue', exc_info=True)
            chemin = self._ouvrir(titre, initial, zone)
            resultat = chemin if isinstance(chemin, str) and chemin else None
        except Exception:
            journal.exception('fenêtre de choix de dossier')
            erreur = Refus("La fenêtre de choix d'un dossier n'a pas pu s'ouvrir.", 503)
        # La fenêtre est fermée avant que la réponse parte : la demande suivante est acceptée.
        self._fil_id = None
        self._ouverte.clear()
        if erreur is not None:
            reponse.set_exception(erreur)
        else:
            reponse.set_result(resultat)


# ---------------------------------------------------------------- copie vers un dossier

class _SourceIllisible(Exception):
    """La photo d'origine ne se lit plus (disque débranché…) : elle est passée, la copie continue."""


class _SourceRefusee(Exception):
    """La photo d'origine n'est plus ce que MyPhotos a vérifié (atteinte par un lien ou une jonction posés depuis…) :
    elle est passée, avec sa raison, la copie continue."""

    def __init__(self, raison):
        super().__init__(raison)
        self.raison = raison


class Copie:
    """Copie de photos vers un dossier choisi, dans un fil d'arrière-plan.

    Jamais d'écrasement : un nom déjà pris devient « nom (2).jpg », « nom (3).jpg »… Chaque fichier est écrit sous un
    nom provisoire, puis renommé (un renommage n'écrase jamais rien sous Windows) ; ses dates sont gardées. Annulable
    entre deux blocs ; une copie interrompue ne laisse pas de fichier à moitié écrit. Une photo illisible est passée
    (compte rendu), une panne du dossier de destination (disque plein, débranché) arrête tout."""

    def __init__(self, app, numero, dossier, fichiers, refusees):
        self.app = app
        self.numero = numero
        self.dossier = dossier                   # chemin habituel du dossier de destination
        self.fichiers = fichiers                 # [(id, chemin système, nom, taille, fiche de la base)]
        self.refusees = list(refusees)           # [{id, raison}]
        self.renommees = []                      # [{id, nom}] : copies qui ont pris un autre nom
        self.copies = []                         # chemins habituels des copies
        self.octets_total = sum(f[3] for f in fichiers)
        self.octets_faits = 0
        self.total = len(fichiers) + len(self.refusees)
        self.fait = len(self.refusees)           # photos traitées : copiées ou refusées
        self.etat = 'en_cours'                   # en_cours, fini, annule, erreur
        self.message = None
        self.arret = threading.Event()
        self._verrou = threading.Lock()
        self._activite = 0.0
        self.fil = threading.Thread(target=self._executer, name='myphotos-copie', daemon=True)

    def resume(self):
        with self._verrou:
            total = self.octets_total
            return {'numero': self.numero, 'etat': self.etat, 'dossier': self.dossier, 'total': self.total,
                    'fait': self.fait, 'copiees': len(self.copies), 'octets': total, 'octets_faits': self.octets_faits,
                    'pourcent': round(100 * self.octets_faits / total, 1) if total else
                    (100.0 if self.etat == 'fini' else 0.0),
                    'renommees': self.renommees[:REFUS_MAX], 'nb_renommees': len(self.renommees),
                    'refusees': self.refusees[:REFUS_MAX], 'nb_refusees': len(self.refusees),
                    'message': self.message}

    def _avancer(self, n):
        with self._verrou:
            self.octets_faits += n
        t = time.monotonic()
        if t - self._activite > 1.0:             # une longue copie garde MyPhotos éveillé, page fermée ou non
            self._activite = t
            self.app.activite()

    def _executer(self):
        try:
            for pid, source, nom, taille, fiche in self.fichiers:
                if self.arret.is_set():
                    raise InterruptedError('copie annulée')
                avant = self.octets_faits
                try:
                    final = self._copier_un(source, nom, fiche)
                except (_SourceIllisible, _SourceRefusee) as e:
                    with self._verrou:
                        self.refusees.append({'id': pid, 'raison': getattr(e, 'raison', PHOTO_ILLISIBLE)})
                        self.fait += 1
                        self.octets_faits = avant + taille
                    continue
                with self._verrou:
                    self.copies.append(final)
                    if os.path.basename(final) != nom:
                        self.renommees.append({'id': pid, 'nom': os.path.basename(final)})
                    self.fait += 1
                    self.octets_faits = avant + taille
            self.etat = 'fini'
        except InterruptedError:
            self.etat = 'annule'
        except OSError as e:
            journal.warning('copie vers un dossier arrêtée : %r', e)
            self.message = message_copie(e)
            self.etat = 'erreur'
        except Exception:
            journal.exception('copie vers un dossier')
            self.message = 'La copie s\'est arrêtée sur une erreur inattendue.'
            self.etat = 'erreur'
        finally:
            journal.info('copie n° %d : %s, %d photo(s) copiée(s), %d refusée(s)', self.numero, self.etat,
                         len(self.copies), len(self.refusees))
            try:
                self.app._apres_copie(self)
            except Exception:
                journal.exception('après la copie')

    def _copier_un(self, source, nom, fiche):
        dossier = chemin_systeme(self.dossier)
        provisoire = os.path.join(dossier, f'myphotos-copie-{secrets.token_hex(6)}.partiel')
        # Vérifiée de nouveau juste avant de l'ouvrir, son dossier tenu : la vérification faite avant la fenêtre
        # « Choisir un dossier » date peut-être de plusieurs minutes (essai du 24/11/2026 : une jonction posée pendant
        # que la fenêtre était ouverte faisait copier un fichier situé hors des dossiers suivis).
        with GardeDossiers() as garde:
            garde.tenir(os.path.dirname(fiche['chemin']))        # au mieux (partage réseau…) : la vérification suit
            raison, _, _ = Application._fichier_present(fiche)
            if raison == PHOTO_ABSENTE:
                raise _SourceIllisible(OSError(raison))
            if raison:
                raise _SourceRefusee(raison)
            try:
                f = open(source, 'rb')
            except OSError as e:
                raise _SourceIllisible(e) from e
        with f:
            g = open(provisoire, 'xb')           # OSError : panne du dossier de destination
            try:
                with g:
                    while True:
                        if self.arret.is_set():
                            raise InterruptedError('copie annulée')
                        try:
                            bloc = f.read(COPIE_BLOC)
                        except OSError as e:
                            raise _SourceIllisible(e) from e
                        if not bloc:
                            break
                        g.write(bloc)
                        self._avancer(len(bloc))
                try:
                    shutil.copystat(source, provisoire)    # dates de la photo d'origine
                except OSError:
                    pass
                return self._nommer(provisoire, nom)
            except BaseException:
                _effacer_provisoire(provisoire)
                raise

    def _nommer(self, provisoire, nom):
        """Donne au fichier provisoire le nom de la photo, ou « nom (2).jpg »… si le nom est déjà pris."""
        base, ext = os.path.splitext(nom)
        dossier = chemin_systeme(self.dossier)
        for n in range(1, 10000):
            cible = os.path.join(dossier, nom if n == 1 else f'{base} ({n}){ext}')
            if os.name != 'nt' and os.path.lexists(cible):
                continue                         # ailleurs que sous Windows, rename() écraserait
            try:
                os.rename(provisoire, cible)
            except FileExistsError:
                continue
            except PermissionError:
                if os.path.lexists(cible):       # nom pris par un dossier ou un fichier verrouillé
                    continue
                raise
            return chemin_affiche(cible)
        raise FileExistsError(errno.EEXIST, 'trop de fichiers du même nom')


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
        # Mode d'affichage (fenetre.py) : « fenetre », « edge » ou « navigateur » ; l'arrêt pour inactivité ne vaut
        # que sans fenêtre à suivre ; quand_arret (fenêtre) est prévenu dès qu'un arrêt est lancé.
        self.mode = fenetre.NAVIGATEUR
        self.inactivite = True
        self.quand_arret = None
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
        # Préparation du moteur actif : {'etat': 'attente'|'en_cours'|'pret'|'erreur', 'fil', 'duree_s'}.
        self._preparation = None
        self._premiere_reponse = threading.Event()   # la page (ou /api/etat) a répondu une première fois
        self._feu_vert = threading.Event()           # le moteur peut se charger
        self._requetes = 0                           # requêtes HTTP en cours
        self._derniere_requete = time.monotonic()    # début ou fin de la plus récente
        self._verrou_requetes = threading.Lock()
        # Sélection : presse-papiers, copie vers un dossier (une à la fois), mise à la Corbeille (une à la fois).
        self.presse_papiers = PressePapiers()
        self._copie = None
        self._numero_copie = 0
        self._verrou_copie = threading.Lock()
        self._verrou_suppression = threading.Lock()
        # Doublons : un calcul à la fois, dans son fil ; dernier résultat {numero, generation, resultat} en mémoire.
        self._doublons = None                    # doublons.Doublons du moteur actif, créé par le premier calcul
        self._verrou_doublons = threading.Lock()
        self._doublons_fil = None
        self._doublons_arret = threading.Event()
        self._doublons_res = None
        self._doublons_numero = 0
        self._doublons_fin = 0.0                 # time.monotonic() de la fin du dernier calcul
        self._doublons_erreur = None             # message (français) du dernier échec
        self._doublons_deja = None               # déjà lancée une fois par l'utilisateur (meta 'doublons')
        self._doublons_annulee = False           # arrêtée par l'utilisateur : la page ne la relance plus seule
        # Bouton « Actualiser » : {numero (demande d'inventaire), indexeur, debut}, une à la fois.
        self._actualisation = None
        # Mise à jour proposée (mise_a_jour.py) : une vérification par jour, seulement si l'utilisateur l'a acceptée.
        self.mises_a_jour = mise_a_jour.Gestion(self.base.meta_get, self.base.meta_set, self.arreter,
                                                self.travaux_en_cours, journal, **_essai_mise_a_jour())
        if self.base.ecartes:
            self._noter_base_ecartee(self.base.ecartes)

    def _noter_base_ecartee(self, ecartes):
        """Une base abîmée vient d'être mise de côté (Base.ecartes) : la page le dira une fois, avec quoi faire. Retenu
        dans la base neuve jusqu'à ce que la page l'ait montré (POST /api/base-ecartee/vu), même après un arrêt."""
        index = any(x.startswith(self.base.chemin_index + '.') for x in ecartes)
        miniatures = any(x.startswith(self.base.chemin_miniatures + '.') for x in ecartes)
        journal.warning('base abîmée mise de côté : %s', ', '.join(os.path.basename(x) for x in ecartes))
        try:
            self.base.meta_set('base_ecartee', json.dumps({'index': index, 'miniatures': miniatures,
                                                           'quand': maintenant()}))
        except Exception:
            journal.exception('base abîmée : avertissement impossible à retenir')

    # ------------------------------------------------ démarrage et arrêt

    def demarrer(self):
        if self.serveur is None:
            self._feu_vert.set()                 # pas de page à servir : rien à attendre
        self._nb_dossiers = len(self.base.dossiers())
        cle = self.base.meta_get('moteur')
        if isinstance(cle, str) and cle in modele.MODELES_DISPO:
            try:
                cle, vision, texte = self._variante(cle, self.base.meta_get('moteur_vision'))
                materiel_pref = self.base.meta_get('moteur_materiel') or 'auto'
                if FACTICE:
                    self.factices.add((cle, vision))
                if self.pret(cle, vision):
                    self._activer(cle, vision, texte, materiel_pref, self._carte_enregistree())
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
        self.mises_a_jour.demarrer()

    def activite(self):
        self.derniere_activite = time.monotonic()

    # ------------------------------------------------ feu vert pour le moteur

    def serveur_pret(self):
        """Le serveur accepte les requêtes : le moteur se préparera après la première réponse à la page."""
        threading.Thread(target=self._donner_feu_vert, name='myphotos-feu-vert', daemon=True).start()

    def page_servie(self):
        """La page ou /api/etat vient de répondre (une page déjà ouverte se contente de /api/etat)."""
        self._premiere_reponse.set()

    def debut_requete(self):
        with self._verrou_requetes:
            self._requetes += 1
            self._derniere_requete = time.monotonic()

    def fin_requete(self):
        with self._verrou_requetes:
            self._requetes -= 1
            self._derniere_requete = time.monotonic()

    def _donner_feu_vert(self):
        """Première réponse à la page (ou délai dépassé), puis CALME_S sans requête (CALME_MAX_S au plus) : la page
        a ce qu'il lui faut pour s'afficher avant que le chargement du moteur ne ralentisse le serveur."""
        self._premiere_reponse.wait(SANS_PAGE_ATTENTE_S if SANS_PAGE else PAGE_ATTENDUE_S)
        debut = time.monotonic()
        while not self._fin.is_set() and time.monotonic() - debut < CALME_MAX_S:
            with self._verrou_requetes:
                calme = self._requetes <= 0 and time.monotonic() - self._derniere_requete >= CALME_S
            if calme:
                break
            time.sleep(0.05)
        self._feu_vert.set()

    def _veiller(self):
        while not self._fin.wait(2.0):
            if self.inactivite and time.monotonic() - self.derniere_activite > INACTIVITE_S:
                journal.info('aucune activité depuis %d s : arrêt', INACTIVITE_S)
                self.arreter()
                return

    def travaux_en_cours(self):
        """Ce que fermer MyPhotos interromprait (question avant de fermer la fenêtre) : 'analyse', 'copie',
        'doublons', 'telechargement'."""
        travaux = []
        # L'inventaire seul (au début de chaque lancement) ne compte pas : il est refait de toute façon.
        if self._analyse_en_cours():
            travaux.append('analyse')
        c = self._copie
        if c is not None and c.etat == 'en_cours':
            travaux.append('copie')
        if self._calcul_doublons_en_cours():
            travaux.append('doublons')
        t = self.telechargement
        if t is not None and t.etat == 'en_cours':
            travaux.append('telechargement')
        return travaux

    def arreter(self):
        """Arrêt propre en arrière-plan : fin du lot d'analyse en cours, puis arrêt du serveur."""
        with self.verrou:
            if self.arret_lance:
                return
            self.arret_lance = True
        self.mises_a_jour.fermer()               # un téléchargement de mise à jour s'interrompt (reprise plus tard)
        rappel = self.quand_arret
        if rappel is not None:
            try:
                rappel()                         # la fenêtre de MyPhotos se ferme tout de suite
            except Exception:
                journal.exception('fermeture de la fenêtre')

        def travail():
            try:
                t = self.telechargement
                if t is not None:
                    t.arret.set()
                c = self._copie
                if c is not None and c.fil.is_alive():
                    c.arret.set()                    # la copie s'arrête entre deux blocs, sans fichier à moitié écrit
                    c.fil.join(timeout=15)
                self._arreter_doublons(15)           # ce qui est déjà calculé est gardé pour la prochaine fois
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
        self._feu_vert.set()                     # une préparation en attente se réveille… et renonce
        self.mises_a_jour.fermer()
        t = self.telechargement
        if t is not None:
            t.arret.set()
        c = self._copie
        if c is not None:
            c.arret.set()
        ix = self.indexeur
        if ix is not None:
            try:
                ix.arreter(attendre=True)
            except Exception:
                journal.exception('arrêt de l\'analyse')
        if t is not None and t.fil.is_alive():
            t.fil.join(timeout=10)
        if c is not None and c.fil.is_alive():
            c.fil.join(timeout=15)
        self._arreter_doublons(15)
        d = self._doublons
        if d is not None:
            d.fermer()                           # ses connexions, avant celles de la base
        self._attendre_preparation(self._preparation, 60)   # jamais de sortie au milieu d'un chargement
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

    def _moteur_commun(self, cle, vision, texte, prefere, carte=None):
        """Le moteur (vision + texte) du moteur actif, chargé une fois au premier besoin (voir MoteurPartage)."""
        cle_partage = (cle, vision, texte, prefere, carte)
        with self._verrou_partage:
            if self._partage is not None and self._partage[0] == cle_partage:
                return self._partage[1]
            if self.arret_lance:
                raise RuntimeError('MyPhotos s\'arrête')
            debut = time.monotonic()
            m = MoteurPartage(modele.Moteur(cle, vision=vision, texte=texte, materiel=prefere,
                                            carte=modele.indice_carte(carte)), self._verrou_carte)
            self._partage = (cle_partage, m)
            journal.info('moteur %s chargé en %.1f s : images sur %s, texte sur %s (carte %s)', cle,
                         time.monotonic() - debut, m.materiel, m.materiel_texte, getattr(m, 'carte', '?'))
            return m

    def _carte_enregistree(self):
        """Indice DirectML mémorisé avec le moteur choisi (None : pas encore connu, la carte principale sert)."""
        c = self.base.meta_get('moteur_carte')
        try:
            c = int(c) if c is not None else None
        except (TypeError, ValueError):
            return None
        return c if c is not None and 0 <= c < 64 else None

    def _indice_carte(self, cle):
        """Indice DirectML de la carte graphique conseillée par materiel.recommander() pour ce moteur (0 à défaut)."""
        try:
            self.infos_materiel()                      # analyse gardée 10 minutes
            with self._verrou_materiel:
                cartes = dict((self._materiel[1] if self._materiel else {}).get('_cartes') or {})
        except Exception:
            journal.debug('choix de la carte graphique', exc_info=True)
            return 0
        c = cartes.get(cle)
        if c is None:                                  # moteur conseillé sur le processeur : la carte des autres
            c = next((x for x in cartes.values() if x is not None), None)
        return modele.indice_carte(c)

    def _liberer_moteurs(self):
        """Libère l'analyse, la recherche et leur moteur commun d'un coup (changement de moteur, arrêt)."""
        self.indexeur = None
        self.recherche = None
        with self._verrou_partage:
            self._partage = None
        gc.collect()

    def _activer(self, cle, vision, texte, materiel_pref, carte=None):
        """Moteur actif : analyse et recherche prêtes à servir, moteur chargé en arrière-plan (_preparer)."""
        prefere = 'processeur' if materiel_pref == 'processeur' else 'auto'
        carte = None if prefere == 'processeur' else carte

        def fabrique(materiel='auto'):
            self._feu_vert.wait()                       # jamais avant que la page ait répondu
            if materiel == 'processeur' and prefere != 'processeur':
                # Repli de l'indexeur quand la carte graphique a échoué : images seules, sur le processeur.
                return modele.Moteur(cle, vision=vision, texte=texte, materiel='processeur', charger_texte=False)
            return self._moteur_commun(cle, vision, texte, prefere, carte)

        def fournisseur():
            self._feu_vert.wait()
            return self._moteur_commun(cle, vision, texte, prefere, carte)

        self.moteur = {'cle': cle, 'vision': vision, 'texte': texte, 'materiel': prefere, 'carte': carte}
        self.indexeur = Indexeur(self.base, fabrique, cle, inventaire_auto_s=INVENTAIRE_AUTO_S)
        self.recherche = Recherche(self.base, fournisseur, cle)
        prep = {'etat': 'attente', 'fil': None, 'duree_s': None, 'finie': threading.Event()}
        prep['fil'] = threading.Thread(target=self._preparer, args=(prep, cle, vision, texte, prefere, carte),
                                       name='myphotos-preparation', daemon=True)
        self._preparation = prep
        prep['fil'].start()
        journal.info('moteur actif : %s (%s, %s, carte %s)', cle, vision, prefere, carte)

    def _perimee(self, prep):
        return prep is not self._preparation or self.arret_lance

    def _preparer(self, prep, *moteur):
        try:
            self._preparer_moteur(prep, *moteur)
        finally:
            prep['finie'].set()

    def _preparer_moteur(self, prep, cle, vision, texte, prefere, carte):
        """Charge le moteur, les photos analysées et un premier calcul de texte (la 1re recherche sera rapide),
        puis lance l'analyse. Attend le feu vert : la page et /api/etat répondent d'abord."""
        while not self._feu_vert.wait(0.2):
            if self._perimee(prep):                    # moteur changé ou arrêt avant même le feu vert
                return
        if self._perimee(prep):
            return
        prep['etat'] = 'en_cours'
        debut = time.monotonic()
        ok = False
        try:
            self._moteur_commun(cle, vision, texte, prefere, carte)
            r = self.recherche
            if r is not None and not self._perimee(prep):
                r.chercher('photo', 0, 1)
            ok = True
        except Exception as e:
            journal.warning('préparation du moteur %s impossible : %s', cle, e)
        if self._perimee(prep):
            return
        prep['duree_s'] = round(time.monotonic() - debut, 2)
        prep['etat'] = 'pret' if ok else 'erreur'      # en erreur, la recherche réessaiera et dira pourquoi
        if ok:
            journal.info('moteur %s prêt en %.1f s', cle, prep['duree_s'])
        ix = self.indexeur
        if ix is not None:
            ix.demarrer()                              # sans effet s'il tourne déjà (dossier ajouté entre-temps)

    @staticmethod
    def _attendre_preparation(prep, delai):
        fil = prep.get('fil') if prep else None
        if fil is not None and fil is not threading.current_thread() and fil.is_alive():
            fil.join(timeout=delai)

    def en_preparation(self):
        """Vrai tant que le moteur actif se charge (ou attend son feu vert)."""
        prep = self._preparation
        return self.moteur is not None and prep is not None and prep['etat'] in ('attente', 'en_cours')

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
            # Carte graphique conseillée par l'analyse du matériel (déjà faite pour proposer les moteurs).
            carte = None if prefere == 'processeur' else self._indice_carte(cle)
            ancien = self.indexeur
            if ancien is not None:
                ancien.arreter(attendre=True)          # fin du lot en cours
            if self.arret_lance:
                return
            prep, self._preparation = self._preparation, None   # l'ancienne préparation renonce…
            self._attendre_preparation(prep, PREPARATION_MAX_S)  # … après la fin d'un chargement commencé
            self._liberer_moteurs()                    # l'ancien moteur part en entier avant le nouveau
            self.base.meta_set('moteur', cle)
            self.base.meta_set('moteur_vision', vision)
            self.base.meta_set('moteur_materiel', prefere)
            self.base.meta_set('moteur_carte', carte)
            self._activer(cle, vision, texte, prefere, carte)
            self._attendre_preparation(self._preparation, PREPARATION_MAX_S)
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
        # Indice DirectML de la carte conseillée, par moteur (usage interne : jamais envoyé à la page).
        cartes = {cle: o.get('indice_carte') for cle, o in options.items()
                  if isinstance(o.get('indice_carte'), int) and not isinstance(o.get('indice_carte'), bool)}
        return {'resume': resume_materiel(analyse), 'carte': carte, 'nb_photos': n, 'nb_photos_origine': origine,
                'conseil': conseille, 'moteurs': moteurs, '_cartes': cartes}

    def _avec_etat_moteurs(self, res):
        """Ce qui bouge d'une fois à l'autre : moteur actuel et octets restant à télécharger."""
        res = {k: v for k, v in res.items() if not k.startswith('_')}
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
        exclusions = {}
        for x in self.base.exclusions():
            exclusions.setdefault(x['dossier_id'], []).append({'id': x['id'], 'chemin': x['chemin']})
        cles = [cle_chemin(d['chemin']) for d in suivis]
        proposes = []
        for p in self.proposes():
            k = cle_chemin(p['chemin'])
            n, complet = self._estimations.get(k, (None, None))
            proposes.append({'chemin': p['chemin'], 'nom': p['nom'],
                             'deja': any(k == c or dedans(k, c) for c in cles),
                             'photos_estimees': n, 'estimation_complete': complet})
        return {'dossiers': [dict(self._dossier_public(d), exclusions=exclusions.get(d['id'], [])) for d in suivis],
                'proposes': proposes, 'estimation_en_cours': not self._estimation_finie.is_set()}

    @staticmethod
    def _chemin_recu(chemin):
        if not isinstance(chemin, str) or not chemin.strip():
            raise Refus('Aucun dossier indiqué.')
        if len(chemin) > 4096 or '\0' in chemin:
            raise Refus('Ce chemin de dossier est invalide.')
        return chemin

    def ajouter_dossier(self, chemin, exclus=None):
        """Suit un dossier. exclus : sous-dossiers décochés dans l'assistant, exclus avant le premier inventaire
        (ceux qui ne sont pas dans le dossier, ou déjà couverts par un autre, sont ignorés)."""
        self._chemin_recu(chemin)
        if exclus is not None and (not isinstance(exclus, list) or len(exclus) > 200
                                   or not all(isinstance(x, str) for x in exclus)):
            raise Refus('Liste de sous-dossiers invalide.')
        try:
            d = self.base.ajouter_dossier(chemin)
        except DossierRefuse as e:
            raise Refus(sans_jargon(str(e)), 409)
        except OSError:
            raise Refus('Ce dossier est inaccessible.', 409)
        journal.info('dossier ajouté : %s', d['chemin'])
        exclues = []
        for x in sorted(set(exclus or []), key=len):          # les plus courts (les parents) d'abord
            try:
                self._chemin_recu(x)
                exclues.append(self.base.ajouter_exclusion(d['id'], x)['chemin'])
            except (Refus, DossierRefuse, OSError) as e:
                journal.info('sous-dossier non exclu à l\'ajout : %s', e)
        if exclues:
            journal.info('%d sous-dossier(s) exclu(s) dès l\'ajout', len(exclues))
        self._nb_dossiers = len(self.base.dossiers())
        ix = self.indexeur
        if ix is not None:
            ix.relancer()
        return {'dossier': self._dossier_public(self.base.dossier(d['id']) or d),
                'remplaces': d.get('remplaces') or [], 'exclus': exclues}

    def examiner_dossier(self, chemin):
        """Avant d'ajouter un dossier (assistant) : ses plus gros sous-dossiers et leur nombre de photos, comptés en
        ESTIMATION_S secondes au plus ; captures et fonds d'écran décochés d'office, avec la raison."""
        self._chemin_recu(chemin)
        try:
            p = self.base.verifier_dossier(chemin)
        except DossierRefuse as e:
            raise Refus(sans_jargon(str(e)), 409)
        except OSError:
            raise Refus('Ce dossier est inaccessible.', 409)
        try:
            ex = examiner_dossier(p, limite_s=ESTIMATION_S)
        except OSError:
            raise Refus('Ce dossier est inaccessible.', 409)
        sous = [{'chemin': s['chemin'], 'nom': s['nom'], 'photos': s['photos'], 'coche': not s['nature'],
                 'raison': RAISONS_SOUS_DOSSIER.get(s['nature'])} for s in ex['sous_dossiers']]
        return {'chemin': p, 'nom': os.path.basename(p.rstrip('\\/')) or p, 'total': ex['total'],
                'complet': ex['complet'], 'sous_dossiers': sous}

    def choisir_dossier(self, examiner=False):
        """Fenêtre « Choisir un dossier ». examiner=True (assistant) : renvoie seulement le dossier choisi, que la
        page examine puis ajoute ; sinon il est ajouté tout de suite (comme en 0.2.0)."""
        chemin = self.choix_dossier.demander(initial=dossier_images())
        if not chemin:
            return {'annule': True}
        if examiner:
            try:
                return {'annule': False, 'chemin': self.base.verifier_dossier(self._chemin_recu(chemin))}
            except DossierRefuse as e:
                raise Refus(sans_jargon(str(e)), 409)
            except OSError:
                raise Refus('Ce dossier est inaccessible.', 409)
        return self.ajouter_dossier(chemin)

    # ------------------------------------------------ sous-dossiers exclus

    def _dossier_suivi(self, dossier_id):
        d = self.base.dossier(dossier_id)
        if d is None:
            raise Refus('Ce dossier ne fait pas partie de MyPhotos.', 404)
        return d

    def exclure(self, dossier_id, chemin):
        """Exclut un sous-dossier : ses photos sont oubliées tout de suite, jamais supprimées du disque."""
        self._dossier_suivi(dossier_id)
        self._chemin_recu(chemin)
        try:
            r = self.base.ajouter_exclusion(dossier_id, chemin)
        except DossierRefuse as e:
            raise Refus(sans_jargon(str(e)), 409)
        except OSError:
            raise Refus('Ce dossier est inaccessible.', 409)
        journal.info('sous-dossier exclu : %s (%d photos oubliées)', r['chemin'], r['photos'])
        return {'annule': False, 'exclusion': {'id': r['id'], 'chemin': r['chemin'], 'dossier_id': dossier_id},
                'photos': r['photos'], 'remplacees': r['remplacees']}

    def choisir_exclusion(self, dossier_id):
        """« Exclure un sous-dossier… » : fenêtre « Choisir un dossier » ouverte DANS le dossier suivi."""
        d = self._dossier_suivi(dossier_id)
        try:
            present = os.path.isdir(chemin_systeme(d['chemin']))
        except (OSError, ValueError):
            present = False
        if not present:
            raise Refus('Ce dossier est inaccessible pour le moment (disque débranché ?) : réessayez quand il sera '
                        'de nouveau là.', 409)
        chemin = self.choix_dossier.demander(initial=d['chemin'], titre='Choisissez le sous-dossier à exclure')
        if not chemin:
            return {'annule': True}
        return self.exclure(dossier_id, chemin)

    def ne_plus_exclure(self, exclusion_id):
        """« Ne plus exclure » : un tour de ce dossier retrouve les photos du sous-dossier."""
        r = self.base.retirer_exclusion(exclusion_id)
        if r is None:
            raise Refus('Ce sous-dossier n\'est plus exclu.', 404)
        journal.info('sous-dossier de nouveau suivi : %s', r['chemin'])
        ix = self.indexeur
        if ix is not None:
            ix.reprendre()
            ix.relancer(dossiers=[r['dossier_id']])
        return r

    # ------------------------------------------------ actualiser

    def actualiser(self):
        """Bouton « Actualiser » : refait tout de suite le tour de tous les dossiers (nouvelles photos ajoutées puis
        analysées, photos supprimées oubliées, dossiers injoignables gardés). Un seul tour à la fois : un second clic
        pendant le premier ne relance rien. La recherche n'est jamais bloquée (l'inventaire tourne dans le fil de
        l'indexeur). Suivi : /api/etat, champ « actualisation »."""
        ix = self.indexeur
        if ix is None:
            raise Refus('L\'actualisation sera possible dès que le moteur de reconnaissance sera installé.', 409)
        with self.verrou:
            a = self._actualisation
            if a is not None and a['indexeur'] is ix:
                e = self.etat_actualisation()
                if e and e['en_cours']:
                    return {'deja': True, 'actualisation': e}
            ix.reprendre()                       # demandé par l'utilisateur : une pause est levée
            n = ix.relancer()
            self._actualisation = {'numero': n, 'indexeur': ix, 'debut': maintenant()}
        journal.info('actualisation demandée')
        return {'deja': False, 'actualisation': self.etat_actualisation()}

    def etat_actualisation(self, etat_indexeur=None):
        """{numero, en_cours, nouvelles, retirees, injoignables, message} de la dernière actualisation, ou None."""
        a = self._actualisation
        ix = self.indexeur
        if a is None or ix is None or a['indexeur'] is not ix:
            return None
        e = etat_indexeur if etat_indexeur is not None else ix.etat()
        r = {'numero': a['numero'], 'en_cours': False, 'nouvelles': None, 'retirees': None, 'injoignables': [],
             'message': None}
        b = e.get('bilan')
        if e.get('inventaire_fait', 0) >= a['numero'] and b:
            r.update(nouvelles=b['nouvelles'], retirees=b['retirees'], injoignables=list(b['injoignables']),
                     message=message_actualisation(b))
        elif not ix.actif or (e.get('phase') == 'erreur'
                              and e.get('inventaire_en_cours', a['numero']) >= a['numero']):
            # Erreur du passage qui répondait à CETTE demande (pas d'un passage plus ancien), ou indexeur arrêté.
            r['message'] = sans_jargon(e.get('message')) or 'L\'actualisation n\'a pas pu se faire.'
        else:
            r['en_cours'] = True
            m = e.get('dossier_courant')
            r['message'] = 'Actualisation…' + (f' Recherche des photos dans « {m} »' if m else '') + (
                f' ({_nombre_fr(e["trouvees"])} trouvées)' if e.get('trouvees') else '')
        return r

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
        analyse = actualisation = None
        if ix is not None:
            try:
                e = ix.etat()
                analyse = {k: e.get(k) for k in ('phase', 'faites', 'total', 'a_faire', 'debit', 'eta_s', 'erreurs',
                                                 'analysees', 'en_attente', 'trouvees', 'traitees', 'materiel')}
                analyse['message'] = sans_jargon(e.get('message'))
                analyse['eta_texte'] = materiel.formater_duree(e.get('eta_s')) if e.get('eta_s') else None
                analyse['active'] = e.get('phase') in ('inventaire', 'analyse', 'pause')
                actualisation = self.etat_actualisation(e)
            except Exception:
                journal.exception('état de l\'analyse')
        photos = self.base.compter()
        m = self.moteur
        t = self.telechargement
        partage = self._partage
        prep = self._preparation
        pret = m is not None and prep is not None and prep['etat'] == 'pret'
        sur = (analyse or {}).get('materiel') or (partage[1].materiel if partage is not None else None)
        generation = self.base.generation()
        return {
            'nom': NOM, 'version': config.VERSION,
            # Fenêtre de MyPhotos (« fenetre », « edge ») ou page du navigateur (« navigateur ») : voir fenetre.py.
            'mode': self.mode,
            # Raccourci sur le Bureau et dans le menu Démarrer : pour l'exécutable seulement.
            'raccourci': raccourci.possible(),
            # Empreinte du compte Windows (pas le SID) : une relance depuis un autre compte n'ouvre pas cette MyPhotos.
            'compte': fenetre.identite_compte(),
            # 0.4.0 : toute requête exige le jeton d'accès ; une relance ouvre donc l'adresse d'entrée (adresse_entree).
            'acces_par_jeton': True,
            'assistant': self.base.meta_get('assistant'),
            'moteur': None if m is None else {
                'cle': m['cle'], 'nom': modele.info(m['cle']).get('nom') or m['cle'],
                'description': sans_jargon(modele.info(m['cle']).get('description')), 'vision': m['vision'],
                'materiel': sur, 'pret': pret},
            'telechargement': t.resume() if t is not None else None,
            'changement': self.changement,
            # Le moteur se charge (quelques secondes après le lancement) : la recherche attend, sans erreur.
            'preparation': self.en_preparation(),
            'analyse': analyse,
            # Bouton « Actualiser » : tour en cours, puis son bilan (« 3 nouvelles photos, 5 012 photos retirées »).
            'actualisation': actualisation,
            'photos': photos,
            'nb_dossiers': self._nb_dossiers,
            'recherche_prete': self.recherche is not None and pret,
            'generation': generation,
            'arret': self.arret_lance,
            'factice': FACTICE,
            'copie': self._copie.resume() if self._copie is not None else None,
            # Base abîmée mise de côté au démarrage : {index, miniatures, quand}, jusqu'à ce que la page l'ait dit.
            'base_ecartee': self._json(self.base.meta_get('base_ecartee')),
            'limite_selection': LIMITE_SELECTION,
            # Recherche des doublons : avancement et numéro du dernier résultat (les groupes : GET /api/doublons).
            'doublons': self.etat_doublons(generation),
            # Nouvelle version de MyPhotos : accord, version proposée, téléchargement, bilan (mise_a_jour.py).
            'mise_a_jour': self.mises_a_jour.resume(),
            # Taille du texte de la page (Réglages, 0.4.1) : normale, grande, tres-grande.
            'taille_texte': self.taille_texte(),
        }

    # ------------------------------------------------ « Copier pour demander de l'aide » (0.4.1)

    def texte_aide(self):
        """Message à coller dans un e-mail ou WhatsApp pour demander de l'aide : version, Windows, affichage, moteur,
        photos et dossiers suivis, analyse, mise à jour, puis les AIDE_LIGNES dernières lignes du journal. Jamais une
        recherche (le journal n'en contient pas), jamais le jeton (masqué ici encore, comme dans le journal). Contient
        les noms des dossiers de photos : la page le dit avant la copie."""
        e = self.etat()
        lignes = ["Bonjour, j'ai besoin d'aide avec MyPhotos. Voici ce qu'il indique :", '',
                  f'MyPhotos {config.VERSION}']
        s = _sur_aide(materiel._systeme) or {}
        nom = s.get('edition') or s.get('nom') or 'Windows (version inconnue)'
        details = ', '.join(x for x in (
            s.get('mise_a_jour'), f"build {s['build']}.{s.get('revision') or 0}" if s.get('build') else None,
            s.get('architecture')) if x)
        lignes.append(f'Windows : {nom}' + (f' ({details})' if details else ''))
        lignes.append('Affichage : ' + {fenetre.FENETRE: 'fenêtre de MyPhotos', fenetre.EDGE: 'Microsoft Edge (mode '
                                        'application)', fenetre.NAVIGATEUR: 'navigateur'}.get(e['mode'], str(e['mode'])))
        m = e['moteur']
        if m:
            lignes.append(f"Moteur : {m['nom']} ({m['cle']}), {m.get('materiel') or 'matériel inconnu'}"
                          + ('' if m.get('pret') else ', en préparation'))
        else:
            t = e['telechargement'] or {}
            lignes.append('Moteur : aucun' + (f" (téléchargement : {t.get('etat')})" if t.get('etat') else ''))
        p = e['photos']
        lignes.append(f"Photos : {_nombre_aide(p['total'])} dont {_nombre_aide(p['analysees'])} analysées, "
                      f"{_nombre_aide(p['a_faire'])} à analyser, {_nombre_aide(p['erreurs'])} illisibles, "
                      f"{_nombre_aide(p['indisponibles'])} sur un disque débranché")
        dossiers = _sur_aide(self.base.dossiers) or []
        lignes.append(f'Dossiers suivis : {len(dossiers)}')
        for d in dossiers:
            lignes.append(f"  - {d['chemin']} : {_nombre_aide(d['photos'])} photos"
                          + ('' if d['disponible'] else ', injoignable'))
        a = e['analyse'] or {}
        lignes.append(f"Analyse : {a.get('phase') or 'arrêtée'}" + (f" ({a['message']})" if a.get('message') else ''))
        u = e['mise_a_jour'] or {}
        maj = {True: 'acceptée', False: 'refusée'}.get(u.get('accord'), 'pas encore demandée')
        lignes.append(f"Mise à jour : vérification {maj}, état {u.get('etat') or 'inconnu'}"
                      + (f", version {u['version']} proposée" if u.get('version') else '')
                      + (f" ({u['message']})" if u.get('message') else ''))
        lignes += ['', f'--- Les {AIDE_LIGNES} dernières lignes du journal ---']
        lignes += self._fin_du_journal(AIDE_LIGNES) or ['(journal vide ou illisible)']
        texte = '\n'.join(lignes) + '\n'
        jeton = _sur_aide(lambda: Path(config.JETON).read_text(encoding='ascii').strip())
        texte = _FormatSansJeton._MOTIF.sub(r'\1…', texte)
        if jeton and len(jeton) >= 16:
            texte = texte.replace(jeton, '…')
        return texte

    @staticmethod
    def _fin_du_journal(n):
        """Les n dernières lignes du journal (le fichier courant, puis la copie précédente s'il en manque)."""
        lignes = []
        for chemin in (Path(f'{config.JOURNAL}.1'), Path(config.JOURNAL)):
            try:
                lignes += chemin.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                pass
        return lignes[-n:]

    def copier_aide(self):
        """POST /api/aide/copier : met texte_aide() dans le presse-papiers de Windows (texte)."""
        texte = self.texte_aide()
        try:
            self.presse_papiers.copier_texte(texte)
        except PressePapiersOccupe as e:
            journal.warning('%s', e)
            raise Refus('Le presse-papiers de Windows est occupé par un autre programme : réessayez dans un instant.',
                        409)
        except (OSError, concurrent.futures.TimeoutError) as e:
            journal.warning('presse-papiers : %r', e)
            raise Refus('Windows n\'a pas pu copier ce texte. Réessayez dans un instant.', 409)
        journal.info('texte pour demander de l\'aide copié dans le presse-papiers (%d lignes)', texte.count('\n'))
        return {'ok': True, 'lignes': texte.count('\n')}

    def action_mise_a_jour(self, action, corps):
        """POST /api/mise-a-jour/<action> : reglage {oui}, verifier, installer, annuler, reporter, vu, page."""
        g = self.mises_a_jour
        try:
            if action == 'reglage':
                oui = corps.get('oui')
                if not isinstance(oui, bool):
                    raise Refus('Réponse attendue : oui ou non.', 400)
                return g.regler(oui)
            if action == 'verifier':
                return g.verifier(manuel=True)
            if action == 'installer':
                if self.arret_lance:
                    raise Refus('MyPhotos est en train de s\'arrêter.', 409)
                return g.installer()
            if action == 'annuler':
                return g.annuler()
            if action == 'reporter':
                return g.reporter()
            if action == 'vu':
                return g.bilan_vu()
            if action == 'page':
                # « Nouveautés », « Télécharger » : la page de la version proposée, ouverte par MyPhotos dans le
                # navigateur de Windows (jamais une navigation depuis la page : en mode Edge, elle resterait dans
                # le profil de MyPhotos, qui porte le cookie du jeton). Adresse construite depuis la version signée.
                info = g.info
                if info is None:
                    raise Refus('Aucune nouvelle version.', 409)
                adresse = mise_a_jour.url_notes(info['version'])
                if FACTICE:
                    journal.info('essai : page de la version %s non ouverte', info['version'])
                else:
                    fenetre.ouvrir_lien_externe(adresse)
                return {'ok': True}
        except mise_a_jour.Refus as e:
            raise Refus(str(e), e.code) from None
        raise Refus('Action inconnue.', 404)

    # ------------------------------------------------ photos

    def chercher(self, q, debut, n, proches=False):
        """Résultats d'une recherche (mode 'recherche'), ou, avec proches=True, les photos suivantes sous les seuils
        (mode 'proches', « moins probables »). Pendant la préparation du moteur : réponse vide marquée
        preparation=True, que la page relance d'elle-même, jamais une erreur."""
        if not (q or '').strip():
            return self.photos_par_date(debut, n)
        r = self.recherche
        prep = self._preparation
        if not self.changement and r is not None and prep is not None and prep['etat'] == 'en_cours':
            prep['finie'].wait(ATTENTE_RECHERCHE_S)   # moteur en plein chargement : la réponse attend un peu
        if self.changement or (r is not None and self.en_preparation()):
            return self._attente_preparation(q, debut, proches)
        if r is None:
            raise Refus('La recherche sera possible dès que le moteur de reconnaissance sera installé.', 503)
        try:
            res = r.chercher(q, debut, n, proches=True) if proches else r.chercher(q, debut, n)
        except getattr(_recherche, 'MoteurIndisponible', RuntimeError) as e:
            raise Refus(sans_jargon(str(e)), 503)
        except Exception:
            journal.exception('recherche (%d caractères)', len(q))     # jamais le texte cherché dans le journal
            raise Refus('La recherche a rencontré un problème. Réessayez ; si cela continue, relancez MyPhotos.', 500)
        if res is None:
            return self.photos_par_date(debut, n)
        groupes = ('proches',) if proches else ('sures', 'peut_etre')
        page = [i for g in groupes for i in (res.get(g) or [])]
        infos = {p['id']: p for p in self.base.photos([i['id'] for i in page])}

        def enrichir(items):
            sortie = []
            for i in items or []:
                p = infos.get(i['id'])
                if p is not None:                     # retirée entre-temps
                    sortie.append({'id': i['id'], 'proba': i.get('proba'), 'date': p['date'],
                                   'largeur': p['largeur'], 'hauteur': p['hauteur']})
            return sortie
        reponse = {'mode': 'proches' if proches else 'recherche', 'preparation': False,
                   'requete': res.get('requete'), 'debut': res.get('debut', debut),
                   'suivant': res.get('debut', debut) + len(page),
                   'total_sures': res.get('total_sures', 0), 'total_peut_etre': res.get('total_peut_etre', 0),
                   'total_proches': res.get('total_proches', 0),
                   'analysees': res.get('analysees'), 'duree_ms': res.get('duree_ms')}
        for g in groupes:
            reponse[g] = enrichir(res.get(g))
        return reponse

    def semblables(self, pid, debut, n):
        """« Plus comme celle-ci » : les photos qui ressemblent le plus à la photo pid (mode 'semblables'), de la plus
        proche à la moins proche, par pages. Pendant la préparation du moteur : réponse vide marquée preparation=True,
        que la page relance d'elle-même ; sans moteur, ou photo pas encore analysée par ce moteur : réponse vide avec
        un message. Jamais une erreur, sauf pour une photo inconnue (404)."""
        p = self.base.photo(pid)
        if p is None:
            raise Refus('Cette photo est introuvable.', 404)
        reponse = {'mode': 'semblables', 'id': pid, 'date': p['date'], 'preparation': False, 'message': None,
                   'non_analysee': False, 'debut': debut, 'suivant': debut, 'total': 0, 'photos': [],
                   'analysees': None, 'duree_ms': 0}
        r = self.recherche
        prep = self._preparation
        if not self.changement and r is not None and prep is not None and prep['etat'] == 'en_cours':
            prep['finie'].wait(ATTENTE_RECHERCHE_S)   # moteur en plein chargement : la réponse attend un peu
        if self.changement or (r is not None and self.en_preparation()):
            return dict(reponse, preparation=True, message=PREPARATION)
        if r is None:
            return dict(reponse, message=SEMBLABLES_SANS_MOTEUR)
        try:
            res = r.semblables(pid, debut, n)
        except Exception:
            journal.exception('photos semblables')
            raise Refus('La recherche a rencontré un problème. Réessayez ; si cela continue, relancez MyPhotos.', 500)
        if res is None:
            return dict(reponse, message=SEMBLABLES_PAS_ANALYSEE, non_analysee=True)
        infos = {x['id']: x for x in self.base.photos([i['id'] for i in res['photos']])}
        photos = []
        for i in res['photos']:
            x = infos.get(i['id'])
            if x is not None:                         # retirée entre-temps
                photos.append({'id': i['id'], 'score': i['score'], 'date': x['date'], 'largeur': x['largeur'],
                               'hauteur': x['hauteur']})
        reponse.update(debut=res['debut'], suivant=res['debut'] + len(res['photos']), total=res['total'],
                       photos=photos, analysees=res['analysees'], duree_ms=res['duree_ms'])
        return reponse

    def taille_texte(self):
        """Taille du texte choisie dans les Réglages : 'normale' (défaut), 'grande' ou 'tres-grande'."""
        try:
            v = self.base.meta_get('taille_texte')
        except sqlite3.Error:
            return 'normale'
        return v if v in TAILLES_TEXTE else 'normale'

    def regler_taille_texte(self, taille):
        if taille not in TAILLES_TEXTE:
            raise Refus('Taille du texte inconnue.', 400)
        self.base.meta_set('taille_texte', taille)
        return {'ok': True, 'taille_texte': taille}

    def _attente_preparation(self, q, debut, proches):
        return {'mode': 'proches' if proches else 'recherche', 'preparation': True, 'message': PREPARATION,
                'requete': _recherche.normaliser_requete(q), 'debut': debut, 'suivant': debut,
                'total_sures': 0, 'total_peut_etre': 0, 'total_proches': 0, 'sures': [], 'peut_etre': [],
                'proches': [], 'analysees': None, 'duree_ms': 0}

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

    def fichier_photo(self, pid, chercher=False):
        """(fiche, chemin habituel, chemin système, extension) d'une photo connue, qui est bien une image.
        chercher=True : si le fichier manque alors que son dossier suivi répond, un tour de ce dossier est demandé
        (Refus 404, PHOTO_DEPLACEE ; voir photo_absente). Rien n'est jamais oublié ici."""
        p = self.base.photo(pid)
        if p is None:
            raise Refus('Cette photo est introuvable.', 404)
        chemin = p['chemin']
        ext = os.path.splitext(chemin)[1].lower()
        if ext not in config.EXTENSIONS:
            raise Refus('Ce fichier n\'est pas une photo.', 403)
        systeme = chemin_systeme(chemin)
        if not os.path.isfile(systeme):
            etat = self._chercher_photo(p) if chercher else None
            raise Refus(PHOTO_DEPLACEE if etat == 'deplacee' else PHOTO_ABSENTE, 404)
        return p, chemin_affiche(chemin), systeme, ext

    @staticmethod
    def photo_absente(p):
        """Le fichier de cette photo manque-t-il ALORS QUE son dossier suivi répond ?
        - 'deplacee' : oui. Elle a été rangée ailleurs, renommée ou supprimée : on ne peut pas le savoir d'ici. Seul un
          tour du dossier suivi le dit : il reconnaît une photo déplacée ou renommée (même taille, même date) et lui
          garde son analyse et son identifiant, ou l'oublie si elle a vraiment disparu. L'oublier tout de suite la
          ferait analyser de nouveau sous son nouveau nom (et rangée dans l'Explorateur, c'est le cas le plus courant) ;
        - None : elle est là, ou on ne peut pas savoir (disque débranché, ou remplacé par un autre sur la même lettre :
          autre numéro de volume que celui retenu pour le dossier suivi ; réseau coupé, dossier vide comme un partage
          monté sans son disque ou un dossier Images qui ne contient plus que desktop.ini, accès refusé).
        Rien n'est oublié ni demandé ici (voir _chercher_photo)."""
        dossier = p.get('dossier')
        if not dossier:
            return None
        try:
            os.stat(chemin_systeme(p['chemin']))
            return None                          # le fichier est là
        except FileNotFoundError:
            pass                                 # fichier ou dossier introuvable (ERROR_FILE/PATH_NOT_FOUND)
        except (OSError, ValueError):
            return None                          # accès refusé, réseau, disque pas prêt… : on ne sait pas
        try:
            if dossier_vide(dossier):
                return None
        except OSError:
            return None
        if p.get('volume') is not None and identite_volume(dossier) != p['volume']:
            return None                          # pas le disque de la photo (numéro inconnu, base 0.3.0 : comme avant)
        return 'deplacee'

    def _chercher_photo(self, p):
        """Photo dont le fichier manque : si son dossier suivi répond (photo_absente), un tour de ce dossier seulement
        est demandé ; rien n'est oublié. Renvoie l'état de photo_absente."""
        etat = self.photo_absente(p)
        if etat == 'deplacee':
            journal.info('photo n° %d absente de son emplacement : tour de son dossier demandé', p['id'])
            ix = self.indexeur
            if ix is not None:
                ix.relancer(dossiers=[p['dossier_id']])
        return etat

    def verifier_photo(self, pid):
        """Visionneuse : la photo d'origine n'a pas pu s'afficher. {etat, message} : 'presente' (le fichier est là,
        mais ne s'affiche pas), 'deplacee' (absente alors que son dossier suivi répond : rien n'est oublié, un tour de
        ce dossier la retrouve, ou l'oublie si elle a été supprimée), 'injoignable' (disque débranché… : rien n'est
        oublié) ou 'inconnue' (plus dans MyPhotos)."""
        p = self.base.photo(pid)
        if p is None:
            return {'etat': 'inconnue', 'message': INTROUVABLE}
        try:
            if os.path.isfile(chemin_systeme(p['chemin'])):
                return {'etat': 'presente', 'message': None}
        except (OSError, ValueError):
            pass
        if self._chercher_photo(p) == 'deplacee':
            return {'etat': 'deplacee', 'message': PHOTO_DEPLACEE}
        return {'etat': 'injoignable', 'message': PHOTO_ABSENTE}

    def ouvrir(self, pid):
        _, chemin, systeme, _ = self.fichier_photo(pid, chercher=True)
        try:
            # Jamais un chemin que Windows lirait autrement (dossier « Vacances. ») : il ouvrirait une autre photo.
            os.startfile(chemin_pour_windows(chemin) or systeme)
        except OSError as e:
            journal.info('ouverture de la photo %d impossible : %r', pid, e)
            if getattr(e, 'winerror', None) in (1155, 1156):
                raise Refus('Aucun programme de Windows n\'est prévu pour ouvrir ce type de photo.', 409)
            raise Refus('Windows n\'a pas pu ouvrir cette photo.', 409)
        return {'ok': True}

    def montrer(self, pid):
        _, chemin, _, _ = self.fichier_photo(pid, chercher=True)
        cible = chemin_pour_windows(chemin) or (chemin if compris_tel_quel(chemin) else None)
        if cible is None:
            raise Refus(CHEMIN_AMBIGU, 409)
        try:
            montrer_dans_dossier(cible)
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

    def base_ecartee_vue(self):
        self.base.meta_set('base_ecartee', None)
        return {'ok': True}

    # ------------------------------------------------ sélection : copier, copier dans un dossier, supprimer

    @staticmethod
    def _fichier_present(p):
        """(raison, chemin système, stat) : la photo est-elle bien là, fichier ordinaire d'image, sans lien vers un
        autre emplacement entre son dossier suivi et elle ? raison vaut None si oui."""
        chemin = p['chemin']
        if os.path.splitext(chemin)[1].lower() not in config.EXTENSIONS or '*' in chemin or '?' in chemin:
            return PAS_UNE_PHOTO, None, None
        systeme = chemin_systeme(chemin)
        try:
            st = os.lstat(systeme)
        except OSError:
            return PHOTO_ABSENTE, None, None
        if _est_un_lien(st) or not p.get('dossier') or _passe_par_un_lien(chemin, p['dossier']):
            return PAR_UN_LIEN, None, None
        if not stat.S_ISREG(st.st_mode):
            return PAS_UNE_PHOTO, None, None
        return None, systeme, st

    def _a_copier(self, ids):
        """([(id, chemin habituel, chemin système, nom, taille, fiche)], refusees) dans l'ordre des identifiants."""
        fiches = {p['id']: p for p in self.base.photos(ids)}
        fichiers, refusees = [], []
        for pid in ids:
            p = fiches.get(pid)
            if p is None:
                refusees.append({'id': pid, 'raison': INTROUVABLE})
                continue
            raison, systeme, st = self._fichier_present(p)
            if raison == PHOTO_ABSENTE and self._chercher_photo(p) == 'deplacee':
                raison = PHOTO_DEPLACEE
            if raison:
                refusees.append({'id': pid, 'raison': raison})
                continue
            fichiers.append((pid, p['chemin'], systeme, os.path.basename(p['chemin']), st.st_size,
                             {'chemin': p['chemin'], 'dossier': p['dossier']}))
        return fichiers, refusees

    def copier(self, ids):
        """Met les fichiers de ces photos dans le presse-papiers de Windows : un Ctrl+V dans l'Explorateur les colle."""
        fichiers, refusees = self._a_copier(ids)
        chemins = []
        for pid, chemin, *_ in fichiers:
            w = chemin_pour_windows(chemin)
            if w is None:
                refusees.append({'id': pid, 'raison': refus_chemin_windows(chemin)})
            else:
                chemins.append(w)
        if not chemins:
            raise Refus(refusees[0]['raison'] if refusees else 'Aucune photo à copier.', 409)
        try:
            self.presse_papiers.copier(chemins)
        except PressePapiersOccupe as e:
            journal.warning('%s', e)
            raise Refus('Le presse-papiers de Windows est occupé par un autre programme : réessayez dans un instant.',
                        409)
        except (OSError, concurrent.futures.TimeoutError) as e:
            journal.warning('presse-papiers : %r', e)
            raise Refus('Windows n\'a pas pu copier ces photos. Réessayez dans un instant.', 409)
        journal.info('%d photo(s) copiée(s) dans le presse-papiers', len(chemins))
        return {'copiees': len(chemins), 'refusees': refusees[:REFUS_MAX], 'nb_refusees': len(refusees)}

    def copier_vers(self, ids):
        """Demande un dossier (fenêtre de Windows, ouverte ici et non par la page), puis y copie les photos en
        arrière-plan : la page suit l'avancement par GET /api/copie."""
        with self._verrou_copie:
            if self._copie is not None and self._copie.etat == 'en_cours':
                raise Refus('Une copie est déjà en cours : attendez qu\'elle se termine, ou annulez-la.', 409)
        fichiers, refusees = self._a_copier(ids)
        if not fichiers:
            raise Refus(refusees[0]['raison'] if refusees else 'Aucune photo à copier.', 409)
        initial = self.base.meta_get('copie_dossier')
        try:
            if not (isinstance(initial, str) and os.path.isdir(chemin_systeme(initial))):
                initial = dossier_images()
        except (OSError, ValueError):
            initial = dossier_images()
        chemin = self.choix_dossier.demander(initial=initial, titre='Choisissez où copier les photos')
        if not chemin:
            return {'annule': True}
        try:
            dossier = normaliser_chemin(chemin)
            # « Photos. » (point ou espace final) deviendrait « Photos » : un autre dossier que celui de la fenêtre.
            brut = chemin_affiche(chemin)
            if len(brut) == 2 and brut[1] == ':':
                brut += '\\'
            if cle_chemin(os.path.normpath(brut)) != cle_chemin(dossier) or not compris_tel_quel(dossier):
                raise Refus(DESTINATION_AMBIGUE, 409)
            if not os.path.isdir(chemin_systeme(dossier)):
                raise Refus('Ce dossier est introuvable.', 409)
            libre = shutil.disk_usage(chemin_systeme(dossier)).free
        except DossierRefuse as e:
            raise Refus(str(e), 409)
        except (OSError, ValueError):
            raise Refus('Ce dossier est inaccessible : choisissez-en un autre.', 409)
        besoin = sum(f[4] for f in fichiers)
        if libre < besoin + COPIE_MARGE:
            raise Refus(f'Il n\'y a pas assez de place sur ce disque : il faut {materiel.formater_taille(besoin)}, il '
                        f'reste {materiel.formater_taille(libre)}.', 409)
        self.base.meta_set('copie_dossier', dossier)
        with self._verrou_copie:
            if self._copie is not None and self._copie.etat == 'en_cours':
                raise Refus('Une copie est déjà en cours : attendez qu\'elle se termine, ou annulez-la.', 409)
            self._numero_copie += 1
            c = Copie(self, self._numero_copie, dossier, [(f[0], f[2], f[3], f[4], f[5]) for f in fichiers], refusees)
            self._copie = c
            c.fil.start()
        journal.info('copie n° %d : %d photo(s) vers un dossier choisi', c.numero, len(fichiers))
        return {'annule': False, 'copie': c.resume()}

    def creer_raccourcis(self):
        """MyPhotos.lnk sur le Bureau et dans le menu Démarrer, vers l'exécutable en cours (raccourci.py)."""
        try:
            crees = raccourci.creer_raccourcis()
        except raccourci.RaccourciRefuse as e:
            raise Refus(str(e), 409)
        except OSError as e:
            journal.warning('raccourcis impossibles : %r', e)
            raise Refus('Windows n\'a pas pu créer le raccourci. Réessayez dans un instant.', 409)
        journal.info('raccourcis créés : %s', ', '.join(crees))
        return {'crees': crees}

    def copie(self):
        return self._copie.resume() if self._copie is not None else None

    def annuler_copie(self):
        c = self._copie
        if c is None or c.etat != 'en_cours':
            return {'annule': False}
        c.arret.set()
        return {'annule': True}

    def montrer_copie(self):
        """Ouvre le dossier de la dernière copie dans l'Explorateur, la première photo copiée sélectionnée."""
        c = self._copie
        if c is None:
            raise Refus('Aucune copie récente.', 404)
        try:
            premiere = c.copies[0] if c.copies else None
            w = chemin_pour_windows(premiere) if premiere else None
            if w and os.path.isfile(chemin_systeme(premiere)):
                montrer_dans_dossier(w)
            else:
                os.startfile(chemin_pour_windows(c.dossier) or chemin_systeme(c.dossier))
        except (AttributeError, OSError):
            raise Refus('Windows n\'a pas pu ouvrir ce dossier.', 409)
        return {'ok': True}

    def _apres_copie(self, c):
        """Des photos copiées dans un dossier suivi : l'analyse les découvre tout de suite."""
        if not c.copies:
            return
        k = cle_chemin(c.dossier)
        if any(k == cle_chemin(d['chemin']) or dedans(k, cle_chemin(d['chemin'])) for d in self.base.dossiers()):
            ix = self.indexeur
            if ix is not None:
                ix.relancer()

    def _verifier_suppression(self, p, volumes, garde):
        """(raison, chemin pour Windows, chemin système) : raison vaut None si la photo peut partir à la Corbeille.
        Son dossier est tenu par `garde` AVANT les vérifications, et le reste jusqu'après l'appel à Windows."""
        parent = os.path.dirname(p['chemin'])
        if not garde.tenir(parent):
            return (PAS_MISE_A_LA_CORBEILLE if os.path.isdir(chemin_systeme(parent)) else PHOTO_ABSENTE), None, None
        raison, systeme, st = self._fichier_present(p)
        if raison:
            return raison, None, None
        if not garde.au_bon_endroit(parent, p['dossier']):
            return PAR_UN_LIEN, None, None             # le dossier tenu n'est pas celui que désigne le chemin
        if st.st_size != p['taille'] or st.st_mtime_ns != p['modifie']:
            return CHANGEE, None, None                 # ce n'est plus le fichier que MyPhotos connaît
        try:
            racine = racine_volume(p['chemin'])
        except OSError:
            return SANS_CORBEILLE, None, None
        infos = volumes.get(cle_chemin(racine))
        if infos is None:
            infos = volumes[cle_chemin(racine)] = infos_corbeille(racine)
        if not infos['ok']:
            return infos['raison'], None, None
        if infos['capacite'] is not None and st.st_size > infos['capacite']:
            return TROP_GRANDE, None, None
        w = chemin_pour_windows(p['chemin'])
        if w is None:
            return refus_chemin_windows(p['chemin']), None, None
        return None, w, systeme

    def supprimer(self, ids, confirme):
        """Met ces photos à la Corbeille de Windows, jamais ailleurs, puis les oublie (index et miniatures).

        Refusées, avec leur raison : photo inconnue ou absente, fichier changé depuis l'analyse (taille ou date),
        atteint par un lien ou une jonction, disque sans Corbeille (réseau, Freebox, clé USB…), Corbeille désactivée
        ou trop petite pour la photo. Renvoie {supprimees, refusees, octets_liberes, generation}."""
        if confirme is not True:
            raise Refus('La suppression doit être confirmée.', 400)
        # Les dossiers des photos restent tenus de leur vérification jusqu'après l'appel à Windows (GardeDossiers).
        with self._verrou_suppression, GardeDossiers() as garde:
            fiches = {p['id']: p for p in self.base.photos(ids)}
            refusees, candidats, volumes = [], [], {}
            for pid in ids:
                p = fiches.get(pid)
                if p is None:
                    refusees.append({'id': pid, 'raison': INTROUVABLE})
                    continue
                raison, chemin_windows, systeme = self._verifier_suppression(p, volumes, garde)
                if raison:
                    refusees.append({'id': pid, 'raison': raison})
                else:
                    candidats.append((pid, chemin_windows, systeme, p['taille']))
            partis, gardes = self._envoyer_a_la_corbeille(candidats)
            supprimees = [c[0] for c in candidats if c[0] in partis]
            for c in candidats:
                if c[0] not in partis:
                    refusees.append({'id': c[0], 'raison': GARDEE if c[0] in gardes else PAS_MISE_A_LA_CORBEILLE})
            if supprimees:
                self.base.oublier_photos(supprimees)
            octets = sum(c[3] for c in candidats if c[0] in partis)
        journal.info('%d photo(s) mise(s) à la Corbeille (%s), %d refusée(s)', len(supprimees),
                     materiel.formater_taille(octets), len(refusees))
        ordre = {pid: i for i, pid in enumerate(ids)}
        refusees.sort(key=lambda r: ordre.get(r['id'], 0))
        return {'supprimees': supprimees, 'refusees': refusees[:REFUS_MAX], 'nb_refusees': len(refusees),
                'octets_liberes': octets, 'generation': self.base.generation()}

    @staticmethod
    def _envoyer_a_la_corbeille(candidats):
        """Tous d'un coup (rapide), puis un par un ceux qui restent (un fichier ouvert ailleurs arrête le lot). Renvoie
        (identifiants partis, identifiants gardés parce que Windows allait les supprimer définitivement)."""
        def partis():
            return {c[0] for c in candidats if not os.path.lexists(c[2])}

        gardes = set()
        paquets, premier_tour = ([candidats] if candidats else []), True
        while paquets:
            for paquet in paquets:
                try:
                    _, abandon = mettre_a_la_corbeille([c[1] for c in paquet])
                except OSError as e:
                    journal.warning('Corbeille : %r', e)
                    abandon = False
                if abandon:                          # « supprimer définitivement ? » : non (FOF_WANTNUKEWARNING)
                    gardes.update(c[0] for c in paquet if os.path.lexists(c[2]))
                    return partis(), gardes
            if not premier_tour:
                break
            premier_tour = False
            restants = [c for c in candidats if os.path.lexists(c[2])]
            paquets = [[c] for c in restants] if len(candidats) > 1 else []
        return partis(), gardes

    # ------------------------------------------------ doublons
    # La recherche tourne dans son fil, sans aucun verrou de l'application (ni celui de la carte graphique : elle
    # n'utilise que la base et numpy) ; ce qu'elle calcule est gardé dans index.db, une relance est donc rapide.
    # Elle ne fait que proposer : les photos ne partent que par supprimer(), après un clic et une confirmation.

    def _cle_doublons(self):
        """Moteur dont les vecteurs aident à repérer les photos proches ; sans moteur (None), copies exactes et
        retouches seulement, d'après les miniatures."""
        m = self.moteur
        return m['cle'] if m else None

    def _analyse_active(self):
        ix = self.indexeur
        if ix is None:
            return False
        try:
            return ix.etat().get('phase') in ('inventaire', 'analyse')
        except Exception:
            return False

    def _analyse_en_cours(self):
        """Des photos sont en cours d'analyse (phase « analyse » : pas l'inventaire, pas la pause)."""
        ix = self.indexeur
        if ix is None:
            return False
        try:
            return ix.etat().get('phase') == 'analyse'
        except Exception:
            return False

    def _deja_doublons(self):
        """L'utilisateur a déjà lancé la recherche des doublons (retenu dans la base) : la page la relance seule."""
        if self._doublons_deja is None:
            self._doublons_deja = self.base.meta_get('doublons') == '1'
        return self._doublons_deja

    def _calcul_doublons_en_cours(self):
        f = self._doublons_fil
        return f is not None and f.is_alive()

    def etat_doublons(self, generation=None):
        """Résumé léger (dans /api/etat et GET /api/doublons) : {en_cours, phase, avancement (0-1), message, numero
        (du dernier résultat, 0 s'il n'y en a pas), perime (l'index a changé depuis), deja (lancée au moins une fois),
        arretee (arrêtée par l'utilisateur : pas de relance automatique), erreur}."""
        if generation is None:
            generation = self.base.generation()
        res = self._doublons_res
        en_cours = self._calcul_doublons_en_cours()
        sortie = {'en_cours': en_cours, 'phase': None, 'avancement': None, 'message': None,
                  'numero': res['numero'] if res else 0,
                  'perime': res is not None and res['generation'] != generation,
                  'deja': self._deja_doublons(), 'arretee': self._doublons_annulee,
                  'erreur': None if en_cours else self._doublons_erreur}
        if en_cours:
            d = self._doublons
            e = d.etat() if d is not None else {}
            if e.get('en_cours') and e.get('phase') in _doublons.PHASES[1:6]:
                sortie.update(phase=e['phase'], avancement=e['avancement'], message=e['message'])
            else:                                # le fil vient de partir : rien de mesuré encore
                sortie.update(phase='empreintes', avancement=0.0, message='Recherche des doublons…')
        return sortie

    def lancer_doublons(self, auto=False):
        """Lance la recherche des doublons dans son fil, sans effet si elle tourne déjà. La page suit l'avancement
        (/api/etat), puis lit les groupes (GET /api/doublons).

        auto=True : la page la relance d'elle-même parce que l'index a changé (photos analysées, supprimées…). Rien
        n'est fait si le résultat est à jour, si l'utilisateur ne l'a jamais lancée ou vient de l'arrêter, ou si le
        dernier calcul a moins de DOUBLONS_RELANCE_S alors que l'analyse tourne (il serait aussitôt dépassé) ou qu'il
        a échoué."""
        if self.arret_lance:
            raise Refus('MyPhotos est en train de s\'arrêter.', 409)
        generation = self.base.generation()
        if auto:
            res = self._doublons_res
            recent = time.monotonic() - self._doublons_fin < DOUBLONS_RELANCE_S
            if ((res is None and not self._deja_doublons()) or (res is not None and res['generation'] == generation)
                    or self._doublons_annulee
                    or (recent and (self._doublons_erreur or self._analyse_active()))):
                return dict(self.etat_doublons(generation), lance=False)
        else:
            self._doublons_annulee = False
            if not self._deja_doublons():
                self.base.meta_set('doublons', '1')
                self._doublons_deja = True
        with self._verrou_doublons:
            lance = not self.arret_lance and not self._calcul_doublons_en_cours()   # arrêt lancé entre-temps : rien
            if lance:
                self._doublons_arret = arret = threading.Event()
                self._doublons_erreur = None
                self._doublons_fil = threading.Thread(target=self._calculer_doublons, args=(generation, arret),
                                                      name='myphotos-doublons', daemon=True)
                self._doublons_fil.start()
        if lance:
            journal.info('doublons : recherche lancée%s', ' (relance automatique)' if auto else '')
        return dict(self.etat_doublons(generation), lance=lance)

    def annuler_doublons(self):
        with self._verrou_doublons:
            if not self._calcul_doublons_en_cours():
                return {'annule': False}
            self._doublons_arret.set()
            self._doublons_annulee = True        # la page ne la relance plus d'elle-même
        return {'annule': True}

    def _arreter_doublons(self, delai):
        self._doublons_arret.set()
        f = self._doublons_fil
        if f is not None and f.is_alive() and f is not threading.current_thread():
            f.join(timeout=delai)

    def _outil_doublons(self):
        """(dans le fil du calcul) Le doublons.Doublons du moteur actif, refait quand le moteur a changé. Un seul
        calcul à la fois : personne d'autre ne le remplace pendant ce temps."""
        cle = self._cle_doublons()
        d = self._doublons
        if d is not None and d.cle_modele == cle:
            return d
        nouveau = _doublons.Doublons(self.base, cle)
        self._doublons = nouveau
        if d is not None:
            d.fermer()
        return nouveau

    def _calculer_doublons(self, generation, arret):
        try:
            if arret.is_set():
                return
            res = self._outil_doublons().calculer(arret=arret)
        except _doublons.Annule:
            journal.info('doublons : recherche interrompue')
            return
        except Exception:
            journal.exception('doublons : échec de la recherche')
            self._doublons_erreur = DOUBLONS_ECHEC
            return
        finally:
            self._doublons_fin = time.monotonic()
        with self._verrou_doublons:
            self._doublons_numero += 1
            self._doublons_res = {'numero': self._doublons_numero, 'generation': generation, 'resultat': res}
        c = res['comptes']
        journal.info('doublons : %d photos examinées en %.1f s ; groupes %s ; %d photo(s) en trop (%s)', c['photos'],
                     res['duree_s'], c['groupes'], c['photos_en_trop'],
                     materiel.formater_taille(res['total_gain_octets']))

    def doublons(self, depuis=0):
        """GET /api/doublons : l'avancement (comme dans /api/etat) et le dernier résultat, {numero, quand, duree_s,
        photos (examinées), sans_miniature (pas encore analysées), groupes}. Les groupes ne sont pas renvoyés quand la
        page les a déjà (depuis = ce numéro)."""
        generation = self.base.generation()
        r = self.etat_doublons(generation)
        res = self._doublons_res
        r['resultat'] = None
        if res is not None:
            x = res['resultat']
            r['resultat'] = {'numero': res['numero'], 'quand': x['quand'], 'duree_s': x['duree_s'],
                             'photos': x['comptes']['photos'], 'sans_miniature': x['comptes']['sans_miniature']}
            if depuis != res['numero']:
                r['resultat']['groupes'] = self._groupes_publics(x['groupes'])
        return r

    def _groupes_publics(self, groupes):
        """Groupes tels que la page les montre, d'après la base actuelle : sans les photos parties entre-temps (mises à
        la Corbeille, dossier retiré ou débranché), d'au moins deux photos, et toujours avec au moins une photo à
        garder (si elle est partie, la meilleure des autres prend sa place)."""
        ids = list({p['id'] for g in groupes for p in g['photos']})
        fiches = {p['id']: p for p in self.base.photos(ids)} if ids else {}
        sortie = []
        for g in groupes:
            photos = []
            for p in g['photos']:
                f = fiches.get(p['id'])
                if f is None or not f['disponible']:
                    continue
                photos.append({'id': f['id'], 'garder': bool(p['garder']), 'meilleure': bool(p['meilleure']),
                               'raison': p['raison'], 'nom': f['nom'], 'dossier': os.path.dirname(f['chemin']),
                               'taille': f['taille'], 'largeur': f['largeur'], 'hauteur': f['hauteur'],
                               'date': f['date']})
            if len(photos) < 2:
                continue
            if not any(p['garder'] for p in photos):
                photos[0].update(garder=True, meilleure=True, raison=DOUBLONS_GARDER)
            sortie.append({'id': g['id'], 'type': g['type'], 'photos': photos,
                           'gain_octets': sum(p['taille'] for p in photos if not p['garder'])})
        return sortie


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

    def rendre(self, nonce, taille='normale'):
        """La page, avec ce nonce ; taille : taille du texte choisie (Réglages), posée sur <html> dès l'envoi, pour
        que la page s'affiche tout de suite à la bonne taille, sans changer sous les yeux."""
        p = self.chemin()
        if p is None:
            return None
        cle = (str(p), p.stat().st_mtime_ns)
        if self._cache[0] != cle:
            self._cache = (cle, p.read_text(encoding='utf-8'), None)
        texte = self._cache[1]
        if taille in TAILLES_TEXTE and taille != 'normale':
            texte = texte.replace('<html lang="fr">', f'<html lang="fr" data-taille="{taille}">', 1)
        return (texte.replace('<script>', f'<script nonce="{nonce}">')
                .replace('<style>', f'<style nonce="{nonce}">')).encode('utf-8')


# ---------------------------------------------------------------- HTTP

class Serveur(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # Sous Windows, SO_REUSEADDR laisserait un second programme écouter sur le même port : exclusivité.
    allow_reuse_address = os.name != 'nt'
    request_queue_size = 128
    app = None
    jeton = None                                 # jeton d'accès (main) ; sans lui, tout est refusé

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

    def _navigation(self):
        """La page ouverte dans un onglet (lien, favori…), pas chargée en douce par un autre site (fetch « no-cors »,
        cadre, image, feuille de style) : Sec-Fetch-Mode navigate et Sec-Fetch-Dest document, quand ils sont là."""
        mode = (self.headers.get('Sec-Fetch-Mode') or 'navigate').strip().lower()
        dest = (self.headers.get('Sec-Fetch-Dest') or 'document').strip().lower()
        return mode == 'navigate' and dest == 'document'

    def _origine_ok(self):
        origine = self.headers.get('Origin')
        return origine is None or origine.strip().lower() == 'http://' + self.headers.get('Host', '').strip().lower()

    def _cookie_ok(self, jeton):
        """Vrai si le cookie myphotos-<port> de la requête porte le jeton (comparaison en temps constant). Les
        autres cookies de 127.0.0.1 (ceux d'autres programmes locaux, sur d'autres ports) sont ignorés."""
        nom = nom_cookie(self.server.server_port)
        ok = False
        for entete in self.headers.get_all('Cookie') or ():
            for morceau in entete.split(';'):
                k, egal, v = morceau.strip().partition('=')
                if egal and k.strip() == nom:
                    ok = jeton_egal(v.strip().strip('"'), jeton) or ok
        return ok

    def _acces(self, methode, chemin, requete):
        """Jeton d'accès, pour TOUTE requête (page, API, miniatures, photos, et les routes à venir : contrôle fait ici,
        avant l'aiguillage). Le cookie myphotos-<port> doit porter le jeton du serveur. Seule autre entrée : l'adresse
        d'entrée « /?jeton=… » (fenêtre, Edge, navigateur, relance), qui pose le cookie (HttpOnly, SameSite=Strict,
        Path=/) et redirige vers « /?entree=1 », puis, le cookie reçu, vers « / » : le jeton ne reste ni dans l'adresse
        affichée ni dans l'historique de la page.

        « /?entree=1 » (marqueur non secret) sans cookie valable : le navigateur a refusé le cookie (réglage « bloquer
        les cookies ») ; une page le dit (PAGE_COOKIES), au lieu de « Ouvrez MyPhotos depuis son raccourci ».

        Rend True si la requête continue ; sinon la réponse est déjà partie : redirection, ou refus 403 sans rien
        révéler (« Ouvrez MyPhotos depuis son raccourci » ; JSON pour /api/…, avec « jeton: requis » pour qu'une
        relance reconnaisse une MyPhotos qui n'est pas la sienne)."""
        jeton = self.server.jeton
        valide = bool(jeton) and self._cookie_ok(jeton)
        arrivee = False
        if methode == 'GET' and chemin in PAGES and (PARAMETRE_JETON in requete or PARAMETRE_ARRIVEE in requete):
            try:
                params = urllib.parse.parse_qs(requete, keep_blank_values=True, max_num_fields=20)
            except ValueError:
                raise Refus('Requête refusée.', 400)
            donnes = params.get(PARAMETRE_JETON)
            arrivee = (params.get(PARAMETRE_ARRIVEE) or [''])[-1] == '1'
            if donnes:
                entree = jeton_egal(donnes[-1], jeton)
                if entree or valide:            # jeton juste, ou adresse d'entrée périmée avec un cookie valable
                    entetes = {'Location': '/', 'Cache-Control': 'no-store'}
                    if entree and not valide:
                        entetes['Set-Cookie'] = (f'{nom_cookie(self.server.server_port)}={jeton}; Path=/; HttpOnly; '
                                                 'SameSite=Strict')
                        entetes['Location'] = f'/?{PARAMETRE_ARRIVEE}=1'     # le cookie est-il bien revenu ?
                    self._envoyer(303, b'', 'text/plain; charset=utf-8', entetes)
                    return False
            elif arrivee and valide:
                self._envoyer(303, b'', 'text/plain; charset=utf-8', {'Location': '/', 'Cache-Control': 'no-store'})
                return False
        if valide:
            return True
        self.close_connection = True
        if chemin.startswith('/api/'):
            self._json({'erreur': JETON_REFUSE, 'jeton': 'requis'}, 403)
        else:
            self._envoyer(403, PAGE_COOKIES if arrivee else PAGE_REFUS, 'text/html; charset=utf-8',
                          {'Cache-Control': 'no-store'})
        return False

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
        except (UnicodeDecodeError, ValueError, RecursionError):   # RecursionError : « [[[[… » très imbriqué
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
        if app is None:
            return self._aiguiller(app, methode)
        app.debut_requete()                      # requêtes en cours : le moteur attend un moment de calme
        try:
            self._aiguiller(app, methode)
        finally:
            app.fin_requete()

    def _aiguiller(self, app, methode):
        try:
            if app is None:
                return self._erreur(503, 'MyPhotos démarre, un instant…', fermer=True)
            if not self._hote_ok():
                return self._erreur(403, 'Adresse non autorisée.', fermer=True)
            try:
                url = urllib.parse.urlsplit(self.path)
            except ValueError:                       # « http://[x/ » : jamais une erreur inattendue dans le journal
                return self._erreur(400, 'Requête refusée.', fermer=True)
            chemin = url.path
            if methode == 'POST':
                if self.headers.get(EN_TETE) != '1' or not self._origine_ok() or not self._site_ok():
                    return self._erreur(403, 'Requête refusée.', fermer=True)
            else:
                # Un autre site n'obtient rien, hormis l'ouverture de la page dans un onglet : ni données, ni page
                # chargée en douce (fetch « no-cors », cadre…), qui comptait comme une activité et empêchait l'arrêt
                # automatique (essai du 24/11/2026 : une requête toutes les 4 s gardait MyPhotos lancé indéfiniment).
                if not self._site_ok() and not (chemin in PAGES and self._navigation()):
                    return self._erreur(403, 'Requête refusée.', fermer=True)
            # Jeton d'accès : TOUTE requête, avant l'aiguillage et avant de compter une activité (0.4.0).
            if not self._acces(methode, chemin, url.query):
                return
            corps = self._corps() if methode == 'POST' else None
            app.activite()
            try:
                params = urllib.parse.parse_qs(url.query, keep_blank_values=True, max_num_fields=20)
            except ValueError:                       # plus de 20 paramètres : refus simple, sans trace d'erreur
                raise Refus('Requête refusée.', 400)
            if methode == 'GET':
                self._get(app, chemin, params)
            else:
                self._post(app, chemin, corps)
        except Refus as e:
            self._erreur(e.statut, str(e))
        except (ConnectionError, socket.timeout, TimeoutError):
            self.close_connection = True
        except sqlite3.Error as e:
            # « database is locked » (un autre programme écrit dans la base) et compagnie : jamais en anglais.
            journal.warning('requête %s %s : base : %r', methode, self._chemin_journal(), e)
            texte = sans_jargon(str(e))
            if texte == str(e):                  # message inconnu : rien d'anglais dans la page
                texte = 'Les fichiers de MyPhotos sont momentanément indisponibles'
            try:
                self._erreur(503, f'{texte.rstrip(".")} : réessayez dans un moment.', fermer=True)
            except Exception:
                pass
        except Exception:
            journal.exception('requête %s %s', methode, self._chemin_journal())
            try:
                self._erreur(500, 'Une erreur inattendue est survenue. Réessayez ; si elle revient, relancez MyPhotos.',
                             fermer=True)
            except Exception:
                pass

    def _chemin_journal(self):
        """Chemin de la requête pour le journal, sans ses paramètres : ils peuvent contenir ce que l'utilisateur
        cherche (« /api/recherche?q=… »), qui n'a rien à faire dans un fichier qu'on envoie parfois pour de l'aide."""
        return self.path.split('?', 1)[0][:200]

    def send_error(self, code, message=None, explain=None):
        """Erreurs du protocole (méthode inconnue, demande préalable OPTIONS, requête illisible…) : réponse JSON avec
        les mêmes en-têtes de sécurité que les autres, au lieu de la page HTML d'erreur de Python, qui n'en avait
        aucun (ni CSP, ni nosniff, ni X-Frame-Options)."""
        self.close_connection = True
        try:
            corps = json.dumps({'erreur': 'Requête refusée.'}, ensure_ascii=False).encode('utf-8')
            self.send_response(code)                     # libellé standard : rien de la requête n'y est recopié
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(corps)))
            for k, v in _securite({'Cache-Control': 'no-store'}).items():
                self.send_header(k, v)
            self.send_header('Connection', 'close')
            self.end_headers()
            if getattr(self, 'command', '') != 'HEAD' and code >= 200 and code not in (204, 304):
                self.wfile.write(corps)
        except (ConnectionError, socket.timeout, TimeoutError):
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
    def _identifiants(valeur):
        """Liste d'identifiants de photos venue de la page (sans doublon, dans l'ordre) ; jamais un chemin."""
        if not isinstance(valeur, list) or not valeur:
            raise Refus('Aucune photo choisie.', 400)
        if len(valeur) > LIMITE_SELECTION:
            raise Refus(f'Trop de photos à la fois : {_nombre_fr(LIMITE_SELECTION)} au plus.', 400)
        ids, vus = [], set()
        for v in valeur:
            if isinstance(v, bool) or not isinstance(v, (int, str)) or not _IDENTIFIANT.fullmatch(str(v)):
                raise Refus('Requête illisible.', 400)
            i = int(v)
            if i not in vus:
                vus.add(i)
                ids.append(i)
        return ids

    def _identifiant_dossier(self, valeur):
        try:
            return self._identifiant(valeur)
        except Refus:
            raise Refus('Ce dossier ne fait pas partie de MyPhotos.', 404)

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
            self._page(app.taille_texte())
            return app.page_servie()                  # le moteur pourra se préparer
        if chemin == '/api/etat':
            self._json(app.etat())
            return app.page_servie()
        if chemin == '/api/materiel':
            return self._json(app.infos_materiel(refaire=(params.get('refaire') or [''])[-1] == '1'))
        if chemin == '/api/dossiers':
            return self._json(app.liste_dossiers())
        if chemin == '/api/copie':
            return self._json(app.copie())
        if chemin == '/api/doublons':
            return self._json(app.doublons(self._entier(params, 'depuis', 0, 0, 10 ** 9)))
        if chemin in ('/api/recherche', '/api/photos'):
            q = (params.get('q') or [''])[-1] if chemin == '/api/recherche' else ''
            if len(q) > 1000:
                raise Refus('Recherche trop longue.', 400)
            debut = self._entier(params, 'debut', 0, 0, 10 ** 8)
            n = self._entier(params, 'n', LISTE_DEFAUT, 1, LISTE_MAX)
            if (params.get('proches') or [''])[-1] == '1' and q.strip():
                return self._json(app.chercher(q, debut, n, proches=True))   # « voir les plus proches »
            return self._json(app.chercher(q, debut, n))
        if chemin.startswith('/api/semblables/'):          # « Plus comme celle-ci » (0.4.1)
            pid = self._identifiant(chemin[len('/api/semblables/'):])
            debut = self._entier(params, 'debut', 0, 0, 10 ** 8)
            n = self._entier(params, 'n', LISTE_DEFAUT, 1, LISTE_MAX)
            return self._json(app.semblables(pid, debut, n))
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
            r = app.choisir_dossier(examiner=corps.get('examiner') is True)
        elif chemin == '/api/dossiers/examiner':
            r = app.examiner_dossier(corps.get('chemin'))
        elif chemin == '/api/dossiers/ajouter':
            r = app.ajouter_dossier(corps.get('chemin'), corps.get('exclus'))
        elif chemin == '/api/exclusions/choisir':
            r = app.choisir_exclusion(self._identifiant_dossier(corps.get('dossier')))
        elif chemin == '/api/exclusions/ajouter':
            r = app.exclure(self._identifiant_dossier(corps.get('dossier')), corps.get('chemin'))
        elif chemin == '/api/exclusions/retirer':
            try:
                r = app.ne_plus_exclure(self._identifiant(corps.get('id')))
            except Refus:
                raise Refus('Ce sous-dossier n\'est plus exclu.', 404)
        elif chemin == '/api/actualiser':
            r = app.actualiser()
        elif chemin == '/api/photo/verifier':
            r = app.verifier_photo(self._identifiant(corps.get('id')))
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
        elif chemin == '/api/copier':
            r = app.copier(self._identifiants(corps.get('ids')))
        elif chemin == '/api/copier-vers':
            r = app.copier_vers(self._identifiants(corps.get('ids')))
        elif chemin == '/api/copie/annuler':
            r = app.annuler_copie()
        elif chemin == '/api/copie/montrer':
            r = app.montrer_copie()
        elif chemin == '/api/supprimer':
            r = app.supprimer(self._identifiants(corps.get('ids')), corps.get('confirme'))
        elif chemin == '/api/base-ecartee/vu':
            r = app.base_ecartee_vue()
        elif chemin == '/api/doublons/lancer':
            r = app.lancer_doublons(auto=corps.get('auto') is True)
        elif chemin == '/api/doublons/annuler':
            r = app.annuler_doublons()
        elif chemin == '/api/assistant':
            etape = corps.get('etape')
            if etape not in (1, 2, 3, 'fini'):
                raise Refus('Étape inconnue.', 400)
            app.base.meta_set('assistant', str(etape))
            r = {'ok': True}
        elif chemin == '/api/raccourci':
            r = app.creer_raccourcis()
        elif chemin == '/api/taille-texte':
            r = app.regler_taille_texte(corps.get('taille'))
        elif chemin.startswith('/api/mise-a-jour/') and chemin.count('/') == 3:
            r = app.action_mise_a_jour(chemin.rsplit('/', 1)[1], corps)
        elif chemin == '/api/aide/copier':
            r = app.copier_aide()
        elif chemin == '/api/quitter':
            journal.info('arrêt demandé par la page')
            app.arreter()
            r = {'ok': True}
        else:
            raise Refus('Action inconnue.', 404)
        return self._json(r)

    # ------------------------------------------------ contenus

    def _page(self, taille='normale'):
        nonce = secrets.token_urlsafe(18)
        corps = self.server.page.rendre(nonce, taille)
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

def etat_instance(port, jeton=None):
    """/api/etat de la MyPhotos qui répond déjà sur ce port (version, mode, arret…), ou None. La requête porte, comme
    la page, le jeton d'accès (par défaut celui des données : lire_jeton) ; une 0.1, 0.2 ou 0.3 l'ignore.
    Une MyPhotos 0.4.0 (ou plus) qui le refuse rend {'nom': 'MyPhotos', 'jeton_refuse': True} : elle tourne pour un
    autre compte Windows (ou avec d'autres données), et sa page n'est pas pour nous."""
    jeton = lire_jeton() if jeton is None else jeton
    try:
        c = http.client.HTTPConnection(config.HOTE, port, timeout=3)
        try:
            entetes = {'Sec-Fetch-Site': 'same-origin'}
            if jeton:
                entetes['Cookie'] = f'{nom_cookie(port)}={jeton}'
            c.request('GET', '/api/etat', headers=entetes)
            r = c.getresponse()
            d = json.loads(r.read() or b'{}')
        finally:
            c.close()
        if r.status == 403 and isinstance(d, dict) and d.get('jeton') == 'requis':
            return {'nom': NOM, 'jeton_refuse': True}
        return d if r.status == 200 and isinstance(d, dict) and d.get('nom') == NOM else None
    except Exception:
        return None


def deja_lance(port):
    """Vrai si MyPhotos répond déjà sur ce port."""
    return etat_instance(port) is not None


def main(argv=None):
    fenetre.tenir_mutex()                        # le plus tôt possible : l'installateur sait que MyPhotos tourne
    argv = sys.argv[1:] if argv is None else list(argv)
    configurer_journal()
    debug = fenetre.debogage(argv=argv)
    retirees = fenetre.nettoyer_environnement(debug=debug)
    if retirees:
        journal.info('variables retirées de l\'environnement : %s', ', '.join(sorted(retirees)))
    demande = fenetre.mode_demande(argv, os.environ, SANS_PAGE)
    adresse = f'http://{config.HOTE}:{config.PORT}/'
    serveur = None
    debut = time.monotonic()
    patience = debut + ATTENTE_ARRET_S
    patience_port = debut + PORT_LIBERE_S
    while serveur is None:
        try:
            serveur = Serveur((config.HOTE, config.PORT), Gestionnaire)
        except OSError as e:
            # Qui tient le port ? AVANT d'envoyer quoi que ce soit (jeton, page) : seulement un programme de NOTRE
            # compte Windows. Un autre compte, ou un programme qui squatte le port, n'obtient ni cookie ni page (relecture
            # adverse de la 0.4.0 : sa page s'ouvrait avec notre jeton, et un service worker volait le suivant).
            tenu = fenetre.port_tenu_par(config.PORT)
            if tenu == 'autre':
                journal.warning('port %d tenu par un programme d\'un autre compte Windows (ou illisible) : rien ne lui '
                                'est envoyé', config.PORT)
                alerter(PORT_PRIS.format(port=config.PORT))
                return 1
            jeton_lu = lire_jeton()              # celui de la MyPhotos qui tient le port (mêmes données), relu à chaque tour
            etat = etat_instance(config.PORT, jeton_lu or '') if tenu == 'nous' else None
            if etat is None:
                if time.monotonic() < patience_port:
                    time.sleep(0.5)              # une MyPhotos qui s'arrête garde le port un instant sans répondre
                    continue
                alerter(f'MyPhotos ne peut pas démarrer : le port {config.PORT} est déjà utilisé par un autre '
                        f'programme ({e.__class__.__name__}).')
                return 1
            if etat.get('arret') and time.monotonic() < patience:
                time.sleep(0.5)                  # elle s'arrête (fenêtre fermée à l'instant) : on prend sa place
                continue
            if etat.get('jeton_refuse') and lire_jeton() != jeton_lu and time.monotonic() < patience:
                time.sleep(0.2)                  # elle démarrait : son jeton a été écrit pendant notre requête
                continue
            # Sa page s'ouvre par l'adresse d'entrée si elle exige le jeton (0.4.0 et plus), par « / » sinon.
            entree = adresse_entree(config.PORT, jeton_lu) if etat.get('acces_par_jeton') and jeton_lu else adresse
            code = fenetre.relancer(etat, entree, config.PORT, demande, ouvrir_page, informer,
                                    etat_actuel=lambda: (etat_instance(config.PORT, jeton_lu or '')
                                                         if fenetre.port_tenu_par(config.PORT) == 'nous' else None))
            if code is None and time.monotonic() < patience:
                time.sleep(0.5)                  # elle s'arrêtait en fait : on attend qu'elle libère le port
                continue
            return code or 0
    # Le port est à nous : nouveau jeton d'accès, avant toute réponse autre que « MyPhotos démarre » (503, app absente).
    jeton = creer_jeton()
    serveur.jeton = jeton
    entree = adresse_entree(config.PORT, jeton)
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
    if demande == fenetre.FENETRE:
        # Sa propre fenêtre (replis : Edge, puis le navigateur) ; le serveur tourne dans un fil.
        return fenetre.Hote(app, serveur, entree, debug).executer()
    if not SANS_PAGE:
        ouvrir_page(entree)
    app.serveur_pret()                           # le moteur se chargera après la première réponse à la page
    try:
        serveur.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        journal.info('arrêt demandé (Ctrl+C)')
    finally:
        serveur.server_close()                   # port libéré d'abord : une relance n'attend pas la fin du moteur
        app.fermer()
        journal.info('%s arrêté', NOM)
    return 0
