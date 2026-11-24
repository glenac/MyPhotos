"""Analyse du matériel du PC et conseil du modèle à télécharger.

analyser()        décrit l'ordinateur : Windows, processeur, mémoire, cartes graphiques (DXGI, et Direct3D 12 testé
                  sans créer de périphérique), DirectML, place libre sur le disque des données, alimentation.
                  Moins de 2 s, sans droits administrateur, sans WMI, sans charger de modèle.
recommander()     en déduit le modèle à télécharger, sa variante, le matériel de calcul et la durée de l'analyse,
                  avec des phrases simples pour un novice et les autres choix possibles.
mesurer()         chronomètre le vrai moteur après le téléchargement ; estimer_duree() refait alors le calcul.
formater_duree()  et formater_taille() écrivent « environ 2 minutes », « 749 Mo ».

Aucune fonction publique ne lève d'exception : une information introuvable vaut None. Les résultats sont des
dictionnaires simples, sérialisables en JSON pour l'interface.

Vitesses et mémoire de référence : mesures du 23/11/2026 des quatre modèles sur un Ryzen 5 5600X avec une Radeon
RX 6600 XT (voir EXIGENCES). Les autres matériels sont extrapolés prudemment (FACTEURS_CARTE, _facteur_coeur).
"""
import ctypes
import logging
import math
import os
import platform
import re
import shutil
import struct
import sys
import time
import types
import uuid

from . import config, modele

_journal = logging.getLogger(__name__)

GO = 1 << 30            # Windows compte la mémoire et les disques en Gio, affichés « Go » : on fait pareil
MO = 1 << 20

# ---------------------------------------------------------------- réglages du conseil

NB_PHOTOS_TYPE = 10_000               # bibliothèque supposée quand on ne connaît pas le nombre de photos
DUREE_RAPIDE_S = 30 * 60              # « precis » seulement si l'analyse dure moins d'une demi-heure...
CARTE_PRECIS_GO = 6                   # ... sur une carte graphique dédiée d'au moins 6 Go...
MEMOIRE_PRECIS_GO = 16                # ... avec au moins 16 Go de mémoire vive
COEURS_BASE = 6                       # sans carte graphique utilisable : « base » à partir de 6 cœurs...
MEMOIRE_BASE_GO = 8                   # ... et de 8 Go de mémoire vive ; sinon « rapide »
TELECHARGEMENT_RAISONNABLE = 2 * GO   # au-delà, un autre choix est signalé comme « gros téléchargement »
OCTETS_PAR_PHOTO = 50_000             # miniature + fiche + vecteur : 35 Ko mesurés le 23/11/2026, arrondi prudent
MARGE_DISQUE = GO // 2                # place à laisser libre en plus du strict nécessaire
ESPACE_CONFORT = 5 * GO               # en dessous (une fois tout installé), on prévient que le disque est presque plein
GAIN_FP32_MIN = 0.15                  # vision fp32 sur le processeur seulement si elle va nettement plus vite...
TEMPS_GAGNE_FP32_MIN_S = 60           # ... et fait gagner au moins une minute sur l'analyse complète
TOLERANCE_MEMOIRE = 0.9               # une machine « 16 Go » n'en laisse que 15,7 à Windows
DUREE_LONGUE_S = 2 * 3600             # au-delà, on prévient que l'analyse sera longue

# Mémoire vive nécessaire = pic de mémoire du moteur (mesuré, voir EXIGENCES) + une marge pour Windows et pour le
# navigateur qui affiche le logiciel. Deux niveaux :
MEMOIRE_PLANCHER_GO = 4               # Windows 10 / 11 en demande déjà autant
MARGE_MINIMUM_GO = 2.5                # minimum : Windows allégé (≈ 2 Go) + navigateur (≈ 0,5 Go) ; en dessous : impossible
MARGE_CONSEILLEE_GO = 4.0             # conseillé : Windows (≈ 3 Go) + navigateur (≈ 1 Go) ; en dessous : on prévient

# Préparer une photo dans l'indexeur (en-tête et EXIF, JPEG décodé en taille réduite et remis à l'endroit, miniature,
# pixels pour le modèle) : 52 ms sur un cœur de Ryzen 5 5600X, et 63 photos/s avec les 4 fils de lecture de
# l'indexeur, soit 60 ms par photo et par fil (mesures du 23/11/2026 : photos de 24 Mpx, 5,5 Mo, déjà en mémoire).
# C'est le processeur qui s'en charge : avec une carte graphique, c'est lui qui plafonne le débit.
# NON COMPTÉE : la lecture des fichiers. Sur un disque dur (photos de 5,5 Mo), elle a limité l'analyse à 12 photos/s.
DECODAGE_S = 0.060
FILS_DECODAGE_MAX = 4                 # comme indexation.Indexeur : min(4, fils logiques / 2) fils de lecture
COEURS_REFERENCE = 6                  # Ryzen 5 5600X : 6 cœurs ; mesures faites avec 6 fils de calcul
RAPPORT_FP32 = 1.21                   # vision fp32 / fp16 sur processeur : 8,2 / 6,8 photos/s, mesuré sur « base »

# Vitesse d'une carte graphique par rapport à la Radeon RX 6600 XT mesurée (1,0), par classe.
# EXTRAPOLATION PRUDENTE, sauf « carte_dediee » : DirectML exploite mal les très grosses cartes, et une puce
# intégrée partage la mémoire (lente) du processeur. À revoir si l'on mesure d'autres machines.
FACTEURS_CARTE = {
    'carte_tres_puissante': 2.5,  # RTX 3090 / 4080 / 4090 / 5080 / 5090, RX 7900 / 9070
    'carte_puissante': 1.6,       # RTX 2080 / 3070 / 3080 / 4070 / 5070, RX 6800 / 6900 / 7700 / 7800
    'carte_dediee': 1.0,          # MESURÉE : RX 6600 XT. Même classe : RTX 2060 / 3060 / 4060, RX 6600-6700 / 7600, Arc A750-B580
    'carte_entree': 0.4,          # GTX 900 à 1600, RTX 3050, MX, RX 400-500 / 5500 / 6400-6500, Arc A380, moins de 6 Go
    'integree_recente': 0.15,     # Iris Xe, Arc intégrée, Radeon 680M à 890M, Adreno
    'integree_ancienne': 0.05,    # Intel HD / UHD, Radeon Vega intégrée, petites Radeon intégrées
}


def _debits(carte, processeur_fp32, processeur_fp16):
    """Débits du modèle seul (photos/s, sans la préparation des photos) par classe de matériel, à partir de deux
    références : carte = Radeon RX 6600 XT (DirectML, fp16) ; processeur_* = Ryzen 5 5600X, 6 cœurs, 6 fils.
    Les autres processeurs sont extrapolés d'après leur nombre de cœurs (voir _facteur_coeur)."""
    d = {classe: round(carte * f, 2) for classe, f in FACTEURS_CARTE.items()}
    d['processeur_fp32'] = processeur_fp32
    d['processeur_fp16'] = processeur_fp16
    return d


def _minimum_go(pic_go):
    """Mémoire vive minimale (Go entiers) pour un moteur dont la mémoire de travail culmine à pic_go."""
    return max(MEMOIRE_PLANCHER_GO, math.ceil(pic_go + MARGE_MINIMUM_GO - 1e-9))


def _conseillee_go(pic_go):
    """Mémoire vive conseillée (Go entiers) : en dessous, on conseille de fermer les autres programmes."""
    return max(MEMOIRE_PLANCHER_GO, math.ceil(pic_go + MARGE_CONSEILLEE_GO - 1e-9))


def _video_min_go(video_go):
    """Mémoire de la carte graphique nécessaire : relevé + 0,25 Go, arrondi au demi-Go supérieur."""
    return math.ceil((video_go + 0.25) * 2 - 1e-9) / 2


# Exigences par modèle (clés de modele.MODELES_DISPO ; une clé absente du catalogue est ignorée, un modèle du
# catalogue absent d'ici reçoit des exigences déduites de sa fiche, voir _exigences_deduites).
#   memoire_moteur_go      pic de mémoire de travail du moteur (Go), par usage : 'carte', 'processeur' (vision fp16)
#                          et 'processeur_fp32' (vision fp32 sur le processeur)
#   memoire_min_go         mémoire vive minimale (Go) = _minimum_go(pic) ; en dessous, ce modèle est impossible
#   memoire_conseillee_go  mémoire vive conseillée (Go) = _conseillee_go(pic) ; en dessous, on prévient
#   memoire_video_go       mémoire de la carte graphique utilisée (Go), prise sur la mémoire vive pour une puce intégrée
#   memoire_video_min_go   mémoire de la carte graphique nécessaire = _video_min_go (sinon : calcul sur le processeur)
#   carte_conseillee       True : trop lent sur le processeur pour être conseillé sans carte graphique
#   debits                 photos/s du modèle seul, par classe de matériel (voir _debits)
#   a_confirmer            True : vitesses provisoires (modèle jamais mesuré)
#
# MESURES du 23/11/2026 sur ce PC (Ryzen 5 5600X, 6 fils ; Radeon RX 6600 XT ; modele.py du jour ; fp16) :
# - débits du modèle seul, lots de 16 (photos/s) ; vision fp32 sur processeur : x 1,21 (mesuré sur « base »,
#   appliqué aux autres) ;
# - mémoire : un processus par mesure (chargement, 2 lots de 16 images, 3 requêtes). Pic de mémoire de travail /
#   mémoire privée après usage / mémoire vidéo, en Go :
#                  carte (vision + texte)    processeur (vision fp16 + texte)   processeur, vision fp32
#     rapide       1,04 / 1,33 / 0,79        1,20 / 1,18                        1,55 extrapolé
#     base         1,04 / 1,47 / 0,93        1,40 / 1,18                        1,69 / 1,86
#     precis       1,71 / 2,46 / 1,90        3,28 / 2,16                        4,3 extrapolé
#     tres_precis  2,07 / 3,17 / 2,60        4,12 / 2,63                        5,5 extrapolé
#   Sur la carte, le pic a lieu au chargement : la mémoire de travail retombe ensuite à 0,25-0,3 Go.
EXIGENCES = {
    # siglip2-base-patch32-256 : 64 morceaux d'image au lieu de 196, trois fois plus rapide que « base ».
    'rapide': {
        'memoire_moteur_go': {'carte': 1.04, 'processeur': 1.2, 'processeur_fp32': 1.55},
        'memoire_min_go': {'carte': 4, 'processeur': 4, 'processeur_fp32': 5},
        'memoire_conseillee_go': {'carte': 6, 'processeur': 6, 'processeur_fp32': 6},
        'memoire_video_go': 0.79,
        'memoire_video_min_go': 1.5,
        'carte_conseillee': False,
        'debits': _debits(310, round(26.0 * RAPPORT_FP32, 2), 26.0),
        'a_confirmer': False,
    },
    # siglip2-base-patch16-224 : le modèle standard.
    'base': {
        'memoire_moteur_go': {'carte': 1.04, 'processeur': 1.4, 'processeur_fp32': 1.69},
        'memoire_min_go': {'carte': 4, 'processeur': 4, 'processeur_fp32': 5},
        'memoire_conseillee_go': {'carte': 6, 'processeur': 6, 'processeur_fp32': 6},
        'memoire_video_go': 0.93,
        'memoire_video_min_go': 1.5,
        'carte_conseillee': False,
        'debits': _debits(92, round(7.0 * RAPPORT_FP32, 2), 7.0),
        'a_confirmer': False,
    },
    # siglip2-large-patch16-256.
    'precis': {
        'memoire_moteur_go': {'carte': 1.71, 'processeur': 3.28, 'processeur_fp32': 4.3},
        'memoire_min_go': {'carte': 5, 'processeur': 6, 'processeur_fp32': 7},
        'memoire_conseillee_go': {'carte': 6, 'processeur': 8, 'processeur_fp32': 9},
        'memoire_video_go': 1.9,
        'memoire_video_min_go': 2.5,
        'carte_conseillee': True,
        'debits': _debits(20, round(1.5 * RAPPORT_FP32, 2), 1.5),
        'a_confirmer': False,
    },
    # siglip2-so400m-patch16-256 : +0,7 point de précision sur « precis » pour 2,3 Go à télécharger. Jamais
    # conseillé d'office (voir _modele_voulu) : seulement proposé parmi les autres choix.
    'tres_precis': {
        'memoire_moteur_go': {'carte': 2.07, 'processeur': 4.12, 'processeur_fp32': 5.5},
        'memoire_min_go': {'carte': 5, 'processeur': 7, 'processeur_fp32': 8},
        'memoire_conseillee_go': {'carte': 7, 'processeur': 9, 'processeur_fp32': 10},
        'memoire_video_go': 2.6,
        'memoire_video_min_go': 3.0,
        'carte_conseillee': True,
        'debits': _debits(15, round(1.3 * RAPPORT_FP32, 2), 1.3),
        'a_confirmer': False,
    },
}

# Échelle de précision quand le catalogue ne donne pas le rappel en français (rappel_fr, banc d'essai d'Immich).
_PRECISION_DEFAUT = {'rapide': 80.0, 'base': 83.0, 'precis': 85.0, 'tres_precis': 86.0}

__all__ = ['analyser', 'recommander', 'mesurer', 'estimer_duree', 'exigences', 'formater_duree', 'formater_taille',
           'EXIGENCES', 'FACTEURS_CARTE']


# ================================================================ petits outils

def _nombre(x):
    """x en flottant s'il s'agit d'un nombre fini (pas d'un booléen), sinon None. Ne lève jamais : un entier trop grand
    pour un flottant (10**400) faisait lever OverflowError jusque dans recommander() et formater_duree()."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        x = float(x)
    except (OverflowError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _entier(x):
    n = _nombre(x)
    return int(n) if n is not None and n >= 0 else None


def _dict(x):
    return x if isinstance(x, dict) else {}


def _sur(fonction, *args, defaut=None):
    """Appelle fonction ; en cas d'échec, note l'erreur dans le journal et renvoie defaut."""
    try:
        return fonction(*args)
    except Exception:
        _journal.debug('matériel : %s a échoué', getattr(fonction, '__name__', fonction), exc_info=True)
        return defaut


def _propre(nom):
    """« Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz  » -> « Intel Core i5-8250U CPU @ 1.60GHz »."""
    if not isinstance(nom, str):
        return None
    nom = re.sub(r'\((?:R|TM)\)|®|™', '', nom, flags=re.I)
    return ' '.join(nom.split()) or None


def _court(nom_processeur):
    """Nom de processeur sans le superflu : « AMD Ryzen 5 5600X 6-Core Processor » -> « AMD Ryzen 5 5600X »,
    « Intel Celeron CPU N2840 @ 2.16GHz » -> « Intel Celeron N2840 »."""
    if not isinstance(nom_processeur, str) or not nom_processeur:
        return None
    nom = re.sub(r'\s*@.*$', '', nom_processeur)                        # fréquence
    nom = re.sub(r'\b\d+-Cores?\b|\bProcessor\b|\bCPU\b', ' ', nom, flags=re.I)
    return ' '.join(nom.split()) or nom_processeur


def _nombre_fr(n):
    return f'{int(n):,}'.replace(',', ' ')          # 7 100 (espace insécable)


def _decimal_fr(x, chiffres=1):
    t = f'{x:.{chiffres}f}'.replace('.', ',')
    return t[:-2] if t.endswith(',0') else t


def _go_fr(go):
    return f'{_decimal_fr(go, 1)} Go'


def _assez(disponible, minimum):
    """La mémoire « disponible » (Go) couvre-t-elle « minimum » ? Inconnue : on suppose que oui."""
    return disponible is None or minimum is None or disponible >= minimum * TOLERANCE_MEMOIRE


# ================================================================ Windows (ctypes)

_DLL = {}


def _dll(nom):
    """Bibliothèque Windows chargée une fois, avec ses propres prototypes (sans toucher à ctypes.windll)."""
    if nom not in _DLL:
        _DLL[nom] = ctypes.WinDLL(nom, use_last_error=True)
    return _DLL[nom]


def _registre(chemin, noms):
    """Valeurs de HKLM\\<chemin> (lecture seule, sans droits administrateur) ; une valeur absente est omise."""
    valeurs = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, chemin, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            for nom in noms:
                try:
                    valeurs[nom] = winreg.QueryValueEx(k, nom)[0]
                except OSError:
                    pass
    except Exception:
        pass
    return valeurs


class _GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                ('Data4', ctypes.c_ubyte * 8)]


def _guid(texte):
    return _GUID.from_buffer_copy(uuid.UUID(texte).bytes_le)


# ---------------------------------------------------------------- système

def _version_windows():
    """(majeure, mineure, build) réels : RtlGetVersion ne ment pas, contrairement à GetVersionEx."""
    class OSVERSIONINFOEXW(ctypes.Structure):
        _fields_ = [('dwOSVersionInfoSize', ctypes.c_uint32), ('dwMajorVersion', ctypes.c_uint32),
                    ('dwMinorVersion', ctypes.c_uint32), ('dwBuildNumber', ctypes.c_uint32),
                    ('dwPlatformId', ctypes.c_uint32), ('szCSDVersion', ctypes.c_wchar * 128),
                    ('wServicePackMajor', ctypes.c_uint16), ('wServicePackMinor', ctypes.c_uint16),
                    ('wSuiteMask', ctypes.c_uint16), ('wProductType', ctypes.c_ubyte), ('wReserved', ctypes.c_ubyte)]
    try:
        f = _dll('ntdll').RtlGetVersion
        f.argtypes = [ctypes.POINTER(OSVERSIONINFOEXW)]
        f.restype = ctypes.c_long
        v = OSVERSIONINFOEXW()
        v.dwOSVersionInfoSize = ctypes.sizeof(v)
        if f(ctypes.byref(v)) == 0:
            return v.dwMajorVersion, v.dwMinorVersion, v.dwBuildNumber
    except Exception:
        pass
    w = sys.getwindowsversion()          # repli : juste aussi, grâce au manifeste de python.exe
    return w.major, w.minor, w.build


def _architecture():
    """Architecture réelle de la machine : 'x64', 'ARM64' ou 'x86' (même si Python tourne en émulation)."""
    machines = {0x8664: 'x64', 0xAA64: 'ARM64', 0x014C: 'x86', 0x01C4: 'ARM'}
    try:
        k32 = _dll('kernel32')
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        f = k32.IsWow64Process2                     # Windows 10 1709 et suivants
        f.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_uint16)]
        f.restype = ctypes.c_int
        processus, natif = ctypes.c_uint16(), ctypes.c_uint16()
        if f(k32.GetCurrentProcess(), ctypes.byref(processus), ctypes.byref(natif)) and natif.value in machines:
            return machines[natif.value]
    except Exception:
        pass
    nom = (os.environ.get('PROCESSOR_ARCHITEW6432') or os.environ.get('PROCESSOR_ARCHITECTURE')
           or platform.machine() or '').upper()
    return {'AMD64': 'x64', 'X86_64': 'x64', 'ARM64': 'ARM64', 'AARCH64': 'ARM64', 'X86': 'x86'}.get(nom)


def _systeme():
    s = {'nom': None, 'version': None, 'build': None, 'revision': None, 'mise_a_jour': None, 'edition': None,
         '64bits': None, 'architecture': None, 'compatible': None}
    s['architecture'] = _sur(_architecture)
    if s['architecture']:
        s['64bits'] = s['architecture'] in ('x64', 'ARM64')
    elif sys.maxsize > 2 ** 32:
        s['64bits'] = True                          # un Python 64 bits ne tourne que sur un système 64 bits
    if os.name != 'nt':
        s['nom'] = platform.system() or None
        return s
    v = _sur(_version_windows)
    if v:
        majeure, mineure, build = v
        s['version'] = f'{majeure}.{mineure}'
        s['build'] = int(build)
        if majeure == 10:
            s['nom'] = 'Windows 11' if build >= 22000 else 'Windows 10'
        else:
            s['nom'] = {(6, 3): 'Windows 8.1', (6, 2): 'Windows 8', (6, 1): 'Windows 7'}.get(
                (majeure, mineure), f'Windows {majeure}.{mineure}')
        s['compatible'] = (majeure, build) >= (10, 17763)       # Windows 10 1809 ou plus récent
    cv = _registre(r'SOFTWARE\Microsoft\Windows NT\CurrentVersion',
                   ('UBR', 'DisplayVersion', 'ReleaseId', 'ProductName'))
    s['revision'] = _entier(cv.get('UBR'))
    s['mise_a_jour'] = cv.get('DisplayVersion') or cv.get('ReleaseId') or None     # « 24H2 », ou « 1809 » avant 20H2
    produit = cv.get('ProductName')
    if isinstance(produit, str) and produit:
        if s['nom'] == 'Windows 11':
            produit = produit.replace('Windows 10', 'Windows 11')  # le registre dit toujours « Windows 10 »
        s['edition'] = produit
    return s


# ---------------------------------------------------------------- processeur

def _coeurs():
    """(cœurs physiques, fils logiques, cœurs performants, cœurs économes) via GetLogicalProcessorInformationEx.
    Les deux derniers ne sont connus que sur un processeur hybride (Intel 12e génération et suivants)."""
    f = _dll('kernel32').GetLogicalProcessorInformationEx
    f.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    f.restype = ctypes.c_int
    taille = ctypes.c_uint32(0)
    f(0, None, ctypes.byref(taille))           # 0 = RelationProcessorCore ; ce premier appel donne la taille
    if not taille.value:
        return None
    tampon = ctypes.create_string_buffer(taille.value)
    if not f(0, tampon, ctypes.byref(taille)):
        return None
    brut = tampon.raw[:taille.value]
    masque = ctypes.sizeof(ctypes.c_void_p)    # KAFFINITY
    pas = masque + 8                            # GROUP_AFFINITY : masque, Group, Reserved[3]
    coeurs = fils = 0
    classes = []
    i = 0
    while i + 8 <= len(brut):
        relation, longueur = struct.unpack_from('<II', brut, i)
        if longueur <= 0:
            break
        if relation == 0:
            # PROCESSOR_RELATIONSHIP à i+8 : Flags, EfficiencyClass, Reserved[20], GroupCount (i+30), GroupMask (i+32)
            classes.append(brut[i + 9])
            for g in range(struct.unpack_from('<H', brut, i + 30)[0]):
                debut = i + 32 + g * pas
                fils += int.from_bytes(brut[debut:debut + masque], 'little').bit_count()
            coeurs += 1
        i += longueur
    if not coeurs:
        return None
    performants = econes = None
    if len(set(classes)) > 1:                   # classes d'efficacité différentes : processeur hybride
        performants = classes.count(max(classes))
        econes = coeurs - performants
    return coeurs, fils or None, performants, econes


def _graphique_integre(nom, fabricant):
    """Le processeur contient-il une puce graphique ? D'après son nom (None : on ne sait pas)."""
    n = (nom or '').lower()
    if not n:
        return None
    if fabricant == 'AMD':
        if 'threadripper' in n or 'epyc' in n:
            return False
        if re.search(r'radeon|graphics|vega', n) or re.search(r'ryzen\s+(?:ai|z\d)', n):
            return True
        if re.search(r'\d{4}(?:g|ge|u|h|hs|hx)\b', n):                  # 5600G, 5500U, 7840HS...
            return True
        return bool(re.search(r'ryzen\s+\d\s+(?:pro\s+)?[79]\d{3}(?:x3d|x)?\b', n))   # Ryzen 7000 / 9000 de bureau, sauf « F »
    if fabricant == 'Intel':
        if 'xeon' in n:
            return None
        return not re.search(r'\d{3,5}k?f\b', n)                         # i5-12400F, i9-13900KF : sans puce graphique
    return None


def _processeur():
    p = {'nom': None, 'fabricant': None, 'coeurs': None, 'fils': None, 'coeurs_performants': None,
         'coeurs_econes': None, 'frequence_mhz': None, 'avx2': None, 'graphique_integre': None}
    reg = _registre(r'HARDWARE\DESCRIPTION\System\CentralProcessor\0',
                    ('ProcessorNameString', 'VendorIdentifier', '~MHz'))
    p['nom'] = _propre(reg.get('ProcessorNameString')) or _propre(platform.processor())
    texte = f"{reg.get('VendorIdentifier') or ''} {p['nom'] or ''}".lower()
    for marque, mots in (('AMD', ('amd',)), ('Intel', ('intel',)), ('Qualcomm', ('qualcomm', 'snapdragon'))):
        if any(m in texte for m in mots):
            p['fabricant'] = marque
            break
    p['frequence_mhz'] = _entier(reg.get('~MHz')) or None
    c = _sur(_coeurs)
    if c:
        p['coeurs'], p['fils'], p['coeurs_performants'], p['coeurs_econes'] = c
    p['fils'] = p['fils'] or os.cpu_count()
    p['avx2'] = _sur(_avx2)
    p['graphique_integre'] = _graphique_integre(p['nom'], p['fabricant'])
    return p


def _avx2():
    """AVX2 (calcul vectoriel, deux fois plus rapide pour le modèle). None si Windows ne sait pas le dire."""
    if (_architecture() or 'x64') not in ('x64', 'x86'):
        return None
    if _dll('kernel32').IsProcessorFeaturePresent(40):          # PF_AVX2_INSTRUCTIONS_AVAILABLE
        return True
    v = _sur(_version_windows)
    return False if v and v >= (10, 0, 19041) else None         # Windows trop ancien pour connaître ce drapeau


# ---------------------------------------------------------------- mémoire, alimentation, disque

def _memoire():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_uint32), ('dwMemoryLoad', ctypes.c_uint32),
                    ('ullTotalPhys', ctypes.c_uint64), ('ullAvailPhys', ctypes.c_uint64),
                    ('ullTotalPageFile', ctypes.c_uint64), ('ullAvailPageFile', ctypes.c_uint64),
                    ('ullTotalVirtual', ctypes.c_uint64), ('ullAvailVirtual', ctypes.c_uint64),
                    ('ullAvailExtendedVirtual', ctypes.c_uint64)]
    m = {'totale_go': None, 'disponible_go': None, 'installee_go': None}
    k32 = _dll('kernel32')
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    if k32.GlobalMemoryStatusEx(ctypes.byref(st)):
        m['totale_go'] = round(st.ullTotalPhys / GO, 1)          # ce que Windows peut utiliser
        m['disponible_go'] = round(st.ullAvailPhys / GO, 1)
    ko = ctypes.c_uint64()
    k32.GetPhysicallyInstalledSystemMemory.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    if k32.GetPhysicallyInstalledSystemMemory(ctypes.byref(ko)) and ko.value:
        m['installee_go'] = round(ko.value / (1 << 20), 1)       # barrettes installées (« 16 Go »)
    return m


def _alimentation():
    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte),
                    ('BatteryLifePercent', ctypes.c_ubyte), ('SystemStatusFlag', ctypes.c_ubyte),
                    ('BatteryLifeTime', ctypes.c_uint32), ('BatteryFullLifeTime', ctypes.c_uint32)]
    a = {'batterie': None, 'sur_secteur': None, 'charge_pourcent': None}
    st = SYSTEM_POWER_STATUS()
    f = _dll('kernel32').GetSystemPowerStatus
    f.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
    if f(ctypes.byref(st)):
        if st.BatteryFlag != 255:
            a['batterie'] = not (st.BatteryFlag & 128)            # 128 : pas de batterie
        if st.ACLineStatus in (0, 1):
            a['sur_secteur'] = st.ACLineStatus == 1
        if a['batterie'] and st.BatteryLifePercent <= 100:
            a['charge_pourcent'] = int(st.BatteryLifePercent)
    return a


def _disque(dossier=None):
    """Place libre sur le lecteur des données ; si le dossier n'existe pas encore, sur son parent existant."""
    d = {'dossier': None, 'lecteur': None, 'libre_go': None, 'total_go': None}
    p = os.path.abspath(str(dossier or config.DONNEES))
    d['dossier'] = p
    lecteur, _ = os.path.splitdrive(p)
    d['lecteur'] = lecteur or None
    existant = p
    while not os.path.isdir(existant):
        parent = os.path.dirname(existant)
        if parent == existant:
            break
        existant = parent
    try:
        u = shutil.disk_usage(existant)
        d['libre_go'] = round(u.free / GO, 1)
        d['total_go'] = round(u.total / GO, 1)
    except OSError:
        pass                                    # lecteur absent (clé USB retirée...) : place inconnue
    return d


# ---------------------------------------------------------------- cartes graphiques (DXGI + Direct3D 12)

_FABRICANTS = {0x1002: 'AMD', 0x1022: 'AMD', 0x10DE: 'NVIDIA', 0x8086: 'Intel', 0x1414: 'Microsoft',
               0x5143: 'Qualcomm', 0x4D4F4351: 'Qualcomm'}
_IID_IDXGIFactory1 = '770aae78-f26f-4dba-a829-253c83d1b387'
_IID_ID3D12Device = '189819f1-1db6-4b57-be54-1821339b85f7'
_D3D_FEATURE_LEVEL_11_0 = 0xB000           # niveau demandé par DirectML (ONNX Runtime crée son périphérique ainsi)


def _testeur_d3d12():
    """Fonction carte -> True / False / None : la carte sait-elle faire du Direct3D 12 ?
    D3D12CreateDevice sans pointeur de sortie répond S_FALSE (1) si oui, sans rien créer."""
    try:
        f = _dll('d3d12').D3D12CreateDevice
    except (OSError, AttributeError):
        return lambda carte: False             # pas de Direct3D 12 sur ce Windows
    f.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(_GUID), ctypes.c_void_p]
    f.restype = ctypes.c_long
    iid = _guid(_IID_ID3D12Device)

    def tester(carte):
        try:
            return f(carte, _D3D_FEATURE_LEVEL_11_0, ctypes.byref(iid), None) in (0, 1)
        except Exception:
            return None
    return tester


def _cartes():
    """Cartes vues par DXGI, dans l'ordre d'énumération : « indice » est le numéro de carte de DirectML."""
    class DXGI_ADAPTER_DESC1(ctypes.Structure):
        _fields_ = [('Description', ctypes.c_wchar * 128), ('VendorId', ctypes.c_uint32),
                    ('DeviceId', ctypes.c_uint32), ('SubSysId', ctypes.c_uint32), ('Revision', ctypes.c_uint32),
                    ('DedicatedVideoMemory', ctypes.c_size_t), ('DedicatedSystemMemory', ctypes.c_size_t),
                    ('SharedSystemMemory', ctypes.c_size_t), ('AdapterLuidBas', ctypes.c_uint32),
                    ('AdapterLuidHaut', ctypes.c_int32), ('Flags', ctypes.c_uint32)]
    hresult = ctypes.c_long
    creer = _dll('dxgi').CreateDXGIFactory1
    creer.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    creer.restype = hresult
    # Méthodes COM appelées par leur rang dans la table virtuelle (IUnknown 0-2, IDXGIObject 3-6, ...).
    enum_adapters1 = ctypes.WINFUNCTYPE(hresult, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(12, 'EnumAdapters1')
    get_desc1 = ctypes.WINFUNCTYPE(hresult, ctypes.POINTER(DXGI_ADAPTER_DESC1))(10, 'GetDesc1')
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong)(2, 'Release')

    fabrique = ctypes.c_void_p()
    if creer(ctypes.byref(_guid(_IID_IDXGIFactory1)), ctypes.byref(fabrique)) < 0 or not fabrique.value:
        return None
    tester = _testeur_d3d12()
    cartes = []
    try:
        for indice in range(16):
            carte = ctypes.c_void_p()
            if enum_adapters1(fabrique, indice, ctypes.byref(carte)) < 0 or not carte.value:
                break                           # DXGI_ERROR_NOT_FOUND : plus de carte
            try:
                d = DXGI_ADAPTER_DESC1()
                if get_desc1(carte, ctypes.byref(d)) < 0:
                    continue
                # Logicielle : rendu par le processeur (« Microsoft Basic Render Driver », WARP), bureau à distance...
                logicielle = bool(d.Flags & 3) or d.VendorId == 0x1414
                cartes.append({
                    'indice': indice,
                    'nom': _propre(d.Description),
                    'fabricant': _FABRICANTS.get(d.VendorId),
                    'id': f'VEN_{d.VendorId:04X}&DEV_{d.DeviceId:04X}',
                    'memoire_dediee_go': round(d.DedicatedVideoMemory / GO, 2),
                    'memoire_partagee_go': round(d.SharedSystemMemory / GO, 1),
                    'logicielle': logicielle,
                    'directx12': None if logicielle else tester(carte),
                    'integree': None,
                    'classe': None,
                })
            finally:
                release(carte)
    finally:
        release(fabrique)
    return cartes


_RE_AMD_DEDIEE = re.compile(r'\brx\s*\d|\bpro\s+w|\bfirepro|\binstinct|\bradeon\s+vii\b|\br9\s+\d|\bhd\s*\d{4}', re.I)
_RE_INTEGREE_RECENTE = re.compile(r'iris\s*xe|\barc\b|adreno|radeon\D*\d{3,4}[ms]\b', re.I)
_CLASSES_PAR_NOM = (            # l'ordre compte : la première qui correspond l'emporte
    ('carte_entree', re.compile(
        r'\bgtx\b|\bgt\s*\d|\bmx\s*\d|\brtx\s*[23]050|\brx\s*(?:4\d0|5[0-9]0|5[35]00|6[345]00)\b|\barc\s*a3\d0'
        r'|\bquadro\s*[kpm]\d|\bradeon\s+(?:r[579]|hd)\s|\bradeon\s+pro\s+wx', re.I)),
    ('carte_tres_puissante', re.compile(r'\brtx\s*(?:3090|[45]0[89]0)|\btitan\b|\brtx\s*a?6000|\brx\s*(?:7900|9070)', re.I)),
    ('carte_puissante', re.compile(r'\brtx\s*(?:2080|30[78]0|[45]070)|\brx\s*(?:6[89][05]0|7[78]00)', re.I)),
)


def _est_integree(carte, processeur):
    """Puce graphique intégrée au processeur (mémoire partagée), ou vraie carte graphique dédiée ?"""
    if carte.get('logicielle'):
        return False
    nom = _propre(carte.get('nom')) or ''
    fabricant = carte.get('fabricant')
    dediee = _nombre(carte.get('memoire_dediee_go'))
    if fabricant == 'Qualcomm':
        return True
    if fabricant == 'NVIDIA':
        return False
    if fabricant == 'Intel':
        return dediee is None or dediee < 2       # les Intel Arc dédiées ont 4 Go et plus ; les puces intégrées ~128 Mo
    if dediee is not None and dediee < 1:
        return True
    if fabricant == 'AMD' and _dict(processeur).get('fabricant') == 'AMD' and _dict(processeur).get('graphique_integre'):
        return not _RE_AMD_DEDIEE.search(nom)     # APU dont on a agrandi la mémoire vidéo réservée
    return False


def _classe_carte(carte):
    """Classe de vitesse (clé de FACTEURS_CARTE), d'après le nom puis la mémoire. Extrapolation prudente."""
    nom = _propre(carte.get('nom')) or ''
    if carte.get('integree'):
        return 'integree_recente' if _RE_INTEGREE_RECENTE.search(nom) else 'integree_ancienne'
    for classe, motif in _CLASSES_PAR_NOM:
        if motif.search(nom):
            return classe
    dediee = _nombre(carte.get('memoire_dediee_go'))
    return 'carte_entree' if dediee is not None and dediee < 5.5 else 'carte_dediee'


def _completer_cartes(cartes, processeur):
    """Copie des cartes, nom nettoyé, avec 'integree' et 'classe' renseignées (calculées si absentes).
    Une carte illisible est écartée plutôt que de faire échouer tout le conseil."""
    sortie = []
    for c in cartes or []:
        if not isinstance(c, dict):
            continue
        try:
            c = dict(c)
            c['nom'] = _propre(c.get('nom'))
            if c.get('integree') is None and not c.get('logicielle'):
                c['integree'] = _est_integree(c, processeur)
            if c.get('classe') not in FACTEURS_CARTE and not c.get('logicielle'):
                c['classe'] = _classe_carte(c)
            sortie.append(c)
        except Exception:
            _journal.debug('matériel : carte ignorée %r', c, exc_info=True)
    return sortie


def _onnxruntime():
    o = {'version': None, 'fournisseurs': None}
    import onnxruntime as ort
    o['version'] = getattr(ort, '__version__', None)
    o['fournisseurs'] = list(ort.get_available_providers())
    return o


# ================================================================ analyse

def analyser(nb_photos=None):
    """Décrit le matériel de ce PC. Ne lève jamais d'exception ; une information introuvable vaut None.

    nb_photos (facultatif) est conservé dans le résultat, pour que recommander() n'ait pas à le redemander.
    """
    debut = time.perf_counter()
    a = {'systeme': None, 'processeur': None, 'memoire': None, 'cartes': None, 'directml': None, 'disque': None,
         'alimentation': None, 'onnxruntime': None, 'nb_photos': None, 'duree_analyse_s': None}
    try:
        a['nb_photos'] = _entier(nb_photos)
        a['systeme'] = _sur(_systeme)
        a['processeur'] = _sur(_processeur)
        if os.name == 'nt':
            a['memoire'] = _sur(_memoire)
            a['alimentation'] = _sur(_alimentation)
            cartes = _sur(_cartes)
            a['cartes'] = None if cartes is None else _completer_cartes(cartes, a['processeur'])
        a['disque'] = _sur(_disque)
        a['onnxruntime'] = _sur(_onnxruntime)
        fournisseurs = (a['onnxruntime'] or {}).get('fournisseurs')
        if fournisseurs is not None and a['cartes'] is not None:
            a['directml'] = 'DmlExecutionProvider' in fournisseurs and any(
                c.get('directx12') is True and not c.get('logicielle') for c in a['cartes'])
    except Exception:
        _journal.warning('matériel : analyse incomplète', exc_info=True)
    a['duree_analyse_s'] = round(time.perf_counter() - debut, 3)
    return a


# ================================================================ exigences des modèles

def _cout_calcul(cle):
    """Calcul d'une photo ~ taille de la moitié vision x nombre de morceaux d'image (côté / taille d'un morceau)²."""
    m = modele.info(cle)
    fichiers, vision = m['fichiers'], m['vision']
    poids = fichiers[vision['fp16']][0] if 'fp16' in vision else fichiers[vision['fp32']][0] / 2
    trouve = re.search(r'patch(\d+)', m.get('depot') or '')
    morceau = int(trouve.group(1)) if trouve else 16
    return poids * (m['taille_image'] / morceau) ** 2


def _taille_fp16_go(cle):
    m = modele.info(cle)
    total = 0
    for moitie in ('vision', 'texte'):
        variantes = m[moitie]
        nom = variantes.get('fp16') or variantes.get('fp32')
        total += m['fichiers'][nom][0] / (1 if 'fp16' in variantes else 2)
    return total / GO


def _exigences_deduites(cle):
    """Pour un modèle absent de EXIGENCES : tout est déduit de sa fiche, par rapport à « base ». À CONFIRMER."""
    rapport = _cout_calcul(cle) / _cout_calcul('base')
    poids = _taille_fp16_go(cle) / _taille_fp16_go('base')
    b = EXIGENCES['base']
    prudence = 0.9
    pics = {k: round(v * poids, 2) for k, v in b['memoire_moteur_go'].items()}
    video = round(b['memoire_video_go'] * poids, 2)
    d = b['debits']
    return {
        'memoire_moteur_go': pics,
        'memoire_min_go': {k: _minimum_go(v) for k, v in pics.items()},
        'memoire_conseillee_go': {k: _conseillee_go(v) for k, v in pics.items()},
        'memoire_video_go': video,
        'memoire_video_min_go': _video_min_go(video),
        'carte_conseillee': rapport >= 2,
        'debits': _debits(round(d['carte_dediee'] / rapport * prudence, 1),
                          round(d['processeur_fp32'] / rapport * prudence, 2),
                          round(d['processeur_fp16'] / rapport * prudence, 2)),
        'a_confirmer': True,
        'deduit': True,
    }


def exigences(cle):
    """Exigences d'un modèle : celles de EXIGENCES, sinon déduites de sa fiche ; None s'il n'est pas au catalogue."""
    try:
        if cle not in modele.MODELES_DISPO:
            return None
        return EXIGENCES.get(cle) or _exigences_deduites(cle)
    except Exception:
        _journal.debug('matériel : exigences de %r introuvables', cle, exc_info=True)
        return None


def _precision(cle):
    """Place sur l'échelle de précision : rappel en français du banc d'essai d'Immich si le catalogue le donne."""
    try:
        r = _nombre(modele.info(cle).get('rappel_fr'))
        if r is not None:
            return r
    except Exception:
        pass
    if cle in _PRECISION_DEFAUT:
        return _PRECISION_DEFAUT[cle]
    try:
        return _PRECISION_DEFAUT['base'] + math.log2(_cout_calcul(cle) / _cout_calcul('base'))
    except Exception:
        return _PRECISION_DEFAUT['base']


def _cles_proposees():
    """Modèles du catalogue dont on connaît (ou devine) les exigences, du moins au plus précis."""
    cles = [c for c in list(modele.MODELES_DISPO) if exigences(c)]
    return sorted(cles, key=_precision)


# ================================================================ estimation des vitesses

def _facteur_coeur(processeur, systeme, alimentation):
    """Vitesse d'un cœur par rapport à un cœur de Ryzen 5 5600X (1,0). EXTRAPOLATION PRUDENTE, d'après le nom :
    on préfère annoncer une analyse plus longue qu'elle ne le sera."""
    nom = processeur.get('nom')
    nom = nom.lower() if isinstance(nom, str) else ''      # un nom illisible ne doit pas coûter tout le conseil
    f = 1.0
    if re.search(r'celeron|pentium|atom|athlon|sempron|phenom|turion|\bamd [ae]\d*-|\bfx-', nom):
        f *= 0.45                                       # gammes économiques ou très anciennes
    elif re.search(r'\bi[3579]-[2-7]\d{3}', nom):
        f *= 0.65                                       # Intel Core de 2e à 7e génération (2011-2017)
    elif re.search(r'ryzen\s+\d\s+(?:pro\s+)?[12]\d{3}', nom):
        f *= 0.7                                        # Ryzen 1000 et 2000 (2017-2018)
    if alimentation.get('batterie') is True or (alimentation.get('batterie') is None and re.search(
            r'\d{4,5}(?:u|y|h|hs|hx|p|g\d)\b|\d{3,4}[uy]\b', nom)):
        f *= 0.7                                        # portable : processeur bridé pour chauffer et consommer moins
    if processeur.get('avx2') is False:
        f *= 0.5                                        # sans AVX2, ONNX Runtime calcule deux fois moins vite
    if systeme.get('architecture') == 'ARM64':
        f *= 0.5                                        # MyPhotos (x64) émulée sur un processeur ARM
    return min(1.2, max(0.15, f))


def _profil(analyse):
    """Ce qui compte pour le conseil, tiré d'une analyse (réelle ou fabriquée pour les tests)."""
    a = _dict(analyse)
    proc, sysd, alim = _dict(a.get('processeur')), _dict(a.get('systeme')), _dict(a.get('alimentation'))
    mem, disque = _dict(a.get('memoire')), _dict(a.get('disque'))
    p = types.SimpleNamespace()
    p.ram = _nombre(mem.get('installee_go')) or _nombre(mem.get('totale_go'))
    p.ram_dispo = _nombre(mem.get('disponible_go'))
    p.proc_nom = proc.get('nom') if isinstance(proc.get('nom'), str) else None
    fils = _entier(proc.get('fils'))
    p.coeurs = _entier(proc.get('coeurs')) or (max(1, fils // 2) if fils else None)
    performants, econes = _entier(proc.get('coeurs_performants')), _entier(proc.get('coeurs_econes'))
    coeurs = p.coeurs or 2                              # inconnu : on suppose un petit processeur
    p.coeurs_effectifs = performants + 0.5 * econes if performants and econes else float(coeurs)
    p.fils_decodage = min(FILS_DECODAGE_MAX, max(1, (fils or coeurs) // 2))    # comme indexation.Indexeur
    p.facteur_coeur = _facteur_coeur(proc, sysd, alim)
    p.t_decodage = DECODAGE_S / p.facteur_coeur
    p.cartes = _completer_cartes(a.get('cartes') if isinstance(a.get('cartes'), list) else [], proc)
    p.cartes_connues = isinstance(a.get('cartes'), list)
    p.carte_materielle = any(not c.get('logicielle') for c in p.cartes)
    dx12 = [c for c in p.cartes if c.get('directx12') is True and not c.get('logicielle')]
    p.directml = a.get('directml') if isinstance(a.get('directml'), bool) else bool(dx12)
    p.carte_dx12 = bool(dx12)
    p.carte = max(dx12, key=lambda c: (not c.get('integree'), FACTEURS_CARTE.get(c.get('classe'), 0),
                                       _nombre(c.get('memoire_dediee_go')) or 0)) if (dx12 and p.directml) else None
    libre = _nombre(disque.get('libre_go'))
    p.libre = libre * GO if libre is not None and libre >= 0 else None      # négatif : relevé absurde, donc inconnu
    p.lecteur = disque.get('lecteur') if isinstance(disque.get('lecteur'), str) else None
    p.architecture = sysd.get('architecture')
    p.compatible = sysd.get('compatible')
    p.sur_batterie = alim.get('batterie') is True and alim.get('sur_secteur') is False
    return p


def _avec_decodage(debit_modele, materiel, profil):
    """Débit réel (photos/s) une fois comptée la préparation des photos, faite par le processeur."""
    if not debit_modele or debit_modele <= 0:
        return None
    if materiel == 'processeur':
        # Préparation et calcul se partagent les mêmes cœurs : leurs temps s'additionnent.
        return 1.0 / (1.0 / debit_modele + profil.t_decodage / max(1.0, profil.coeurs_effectifs))
    # Carte graphique : les fils de lecture de l'indexeur préparent pendant qu'elle calcule ; le plus lent l'emporte.
    return min(debit_modele, profil.fils_decodage / profil.t_decodage)


def _debit_processeur(e, profil, variante):
    reference = _nombre(e['debits'].get('processeur_' + variante))
    if not reference:
        return None
    c = profil.coeurs_effectifs
    utiles = min(c, COEURS_REFERENCE) + 0.6 * max(0.0, c - COEURS_REFERENCE)  # au-delà de 6 cœurs, gain moindre
    return _avec_decodage(reference * utiles / COEURS_REFERENCE * profil.facteur_coeur, 'processeur', profil)


def _debit_carte(e, profil):
    if profil.carte is None:
        return None
    return _avec_decodage(_nombre(e['debits'].get(profil.carte.get('classe'))), 'carte', profil)


def _memoire_video_ok(carte, minimum_go):
    disponible = _nombre(carte.get('memoire_dediee_go')) or 0.0
    if carte.get('integree'):
        disponible += _nombre(carte.get('memoire_partagee_go')) or 0.0   # une puce intégrée puise dans la mémoire vive
    return disponible >= (minimum_go or 0) * TOLERANCE_MEMOIRE


def _telechargement(cle, vision, texte):
    try:
        return int(modele.taille_telechargement(cle, vision, texte))
    except Exception:
        _journal.debug('matériel : taille de %s introuvable', cle, exc_info=True)
        return None


# ================================================================ conseil

def _usage(materiel, vision):
    """Clé de memoire_*_go : 'carte', 'processeur' ou 'processeur_fp32'."""
    if materiel == 'carte':
        return 'carte'
    return 'processeur_fp32' if vision == 'fp32' else 'processeur'


def _besoin_memoire(e, materiel, vision, profil, niveau='memoire_min_go'):
    """Mémoire vive totale (Go) nécessaire à ce modèle ; une puce graphique intégrée prend en plus sa mémoire vidéo
    sur la mémoire vive."""
    valeurs = e[niveau]
    besoin = valeurs.get(_usage(materiel, vision)) or valeurs['processeur']
    if materiel == 'carte' and profil.carte is not None and profil.carte.get('integree'):
        besoin = math.ceil(besoin + e['memoire_video_go'])
    return besoin


def _option(cle, profil, n):
    """Tout ce qu'il faut savoir d'un modèle sur ce PC : matériel, variante, téléchargement, durée, place."""
    e = exigences(cle)
    m = modele.info(cle)
    variantes = list(m.get('vision') or {})
    textes = list(m.get('texte') or {})
    texte = 'fp16' if 'fp16' in textes else (textes[0] if textes else None)
    n_ref = n or NB_PHOTOS_TYPE
    donnees = n_ref * OCTETS_PAR_PHOTO + MARGE_DISQUE

    ram_carte = _assez(profil.ram, _besoin_memoire(e, 'carte', 'fp16', profil))
    ram_processeur = _assez(profil.ram, _besoin_memoire(e, 'processeur', 'fp16', profil))
    video_ok = profil.carte is not None and _memoire_video_ok(profil.carte, e['memoire_video_min_go'])
    debit_carte = _debit_carte(e, profil) if video_ok else None
    debits_proc = {v: _debit_processeur(e, profil, v) for v in ('fp16', 'fp32') if v in variantes}
    debits_proc = {v: d for v, d in debits_proc.items() if d}
    meilleur_proc = max(debits_proc.values(), default=None)

    if debit_carte and ram_carte and (not meilleur_proc or debit_carte >= meilleur_proc or not ram_processeur):
        materiel, debit, possible = 'carte', debit_carte, True
        vision = 'fp16' if 'fp16' in variantes else variantes[0]        # sur la carte, fp16 va aussi vite que fp32
    else:
        materiel, possible = 'processeur', ram_processeur
        vision = 'fp16' if 'fp16' in debits_proc or not debits_proc else 'fp32'
        if 'fp16' in debits_proc and 'fp32' in debits_proc:
            # Vision fp32 : environ 20 % plus rapide sur le processeur, mais 180 Mo de plus à télécharger pour « base »
            # et 0,3 Go de mémoire en plus. Seulement si ça vaut nettement le coup, si la mémoire suffit et si le disque
            # garde de la place (place inconnue : non).
            t16, t32 = _telechargement(cle, 'fp16', texte), _telechargement(cle, 'fp32', texte)
            d16, d32 = debits_proc['fp16'], debits_proc['fp32']
            gagne = n_ref / d16 - n_ref / d32
            place = (profil.libre is not None and t32 is not None
                     and profil.libre - t32 - donnees >= ESPACE_CONFORT)
            memoire = _assez(profil.ram, _besoin_memoire(e, 'processeur', 'fp32', profil))
            if d32 >= d16 * (1 + GAIN_FP32_MIN) and place and memoire and (
                    (t32 is not None and t16 is not None and t32 <= t16) or gagne >= TEMPS_GAGNE_FP32_MIN_S):
                vision = 'fp32'
        debit = debits_proc.get(vision)

    telechargement = _telechargement(cle, vision, texte)
    besoin_disque = (telechargement or 0) + donnees
    duree = int(round(n / debit)) if (n and debit) else (0 if n == 0 else None)
    return {
        'cle': cle,
        'nom': m.get('nom') or cle,
        'description': m.get('description'),
        'vision': vision,
        'texte': texte,
        'materiel': materiel,
        'indice_carte': _entier(profil.carte.get('indice')) if materiel == 'carte' else None,   # JSON sûr
        'fils': profil.coeurs,
        'telechargement_octets': telechargement,
        'debit_estime': round(debit, 1) if debit else None,
        'duree_estimee_s': duree,
        'duree_texte': formater_duree(duree),
        'possible': bool(possible),
        'provisoire': bool(e.get('a_confirmer')),
        'raisons': [],
        'alertes': [],
        'bloquant': False,
        'raison_blocage': None,
        # usage interne, retiré avant de rendre le résultat
        '_exigences': e,
        '_video_ok': video_ok,
        '_ram_carte': ram_carte,
        '_besoin_ram': _besoin_memoire(e, materiel, vision, profil),
        '_besoin_disque': besoin_disque,
        '_tient_disque': profil.libre is None or profil.libre >= besoin_disque,
        '_duree_ref': n_ref / debit if debit else None,
    }


def _modele_voulu(options, profil, n):
    """Le modèle que les règles conseillent sur ce PC, avant de vérifier la mémoire et le disque : (clé, motif).

    - carte graphique DirectX 12 dédiée d'au moins 6 Go et 16 Go de mémoire vive : « precis » s'il analyse les
      photos (n, ou 10 000) en moins de 30 minutes, sinon le modèle standard ;
    - autre carte graphique utilisable (petite carte, puce intégrée récente) : le modèle standard ;
    - pas de carte utilisable : le standard avec au moins 6 cœurs et 8 Go de mémoire vive, sinon « rapide ».
    « tres_precis » n'est jamais conseillé d'office : son gain est faible (+0,7 point) pour 2,3 Go à télécharger.
    """
    standard = modele.MODELE_DEFAUT if modele.MODELE_DEFAUT in options else next(iter(options))
    if options[standard]['materiel'] == 'carte':           # la carte est utilisable et plus rapide que le processeur
        c = profil.carte
        dediee = _nombre(c.get('memoire_dediee_go'))
        grosse = (not c.get('integree') and dediee is not None and _assez(dediee, CARTE_PRECIS_GO)
                  and profil.ram is not None and _assez(profil.ram, MEMOIRE_PRECIS_GO))
        p = options.get('precis')
        if grosse and p is not None and p['materiel'] == 'carte':
            if p['_duree_ref'] is not None and p['_duree_ref'] < DUREE_RAPIDE_S:
                return 'precis', 'grosse_carte'
            return standard, 'trop_de_photos'
        return standard, 'carte'
    petit = ((profil.coeurs is not None and profil.coeurs_effectifs < COEURS_BASE)
             or (profil.ram is not None and not _assez(profil.ram, MEMOIRE_BASE_GO)))
    if petit and 'rapide' in options:
        return 'rapide', 'petit'
    return standard, 'processeur'


def _choisir(options, profil, n):
    """Applique les règles, puis les interdits : jamais un modèle qui ne tient pas en mémoire, ni un téléchargement
    qui ne tient pas sur le disque. Renvoie (clé, motif, écarté) ; écarté = (clé, 'memoire' | 'disque') du modèle
    voulu quand un plus léger le remplace, sinon None. Si rien ne tient, le modèle voulu, avec le motif 'impossible'
    (les alertes bloquantes disent pourquoi)."""
    voulu, motif = _modele_voulu(options, profil, n)

    def tient(c):
        return options[c]['possible'] and options[c]['_tient_disque']
    if tient(voulu):
        return voulu, motif, None
    plus_legers = [c for c in options if _precision(c) < _precision(voulu) and tient(c)]
    if plus_legers:
        return plus_legers[-1], 'repli', (voulu, 'memoire' if not options[voulu]['possible'] else 'disque')
    return voulu, 'impossible', None


def _phrase_materiel(o, profil, n):
    """Première phrase : ce qui fera l'analyse, et combien de temps elle prendra si l'on connaît le nombre de photos.
    « Votre ordinateur a une carte graphique AMD Radeon RX 6600 XT : l'analyse de vos 7 100 photos prendra environ
    2 minutes. »"""
    vos = f"l'analyse de vos {_nombre_fr(n)} photos" if n and o['duree_texte'] else None
    if o['materiel'] == 'carte':
        c = profil.carte
        nom = c.get('nom')
        if c.get('integree'):
            debut = 'Votre ordinateur utilisera sa puce graphique intégrée' + (f' ({nom})' if nom else '')
            suite = f"{vos} prendra {o['duree_texte']}" if vos else "l'analyse de vos photos se fera à une vitesse correcte"
        else:
            debut = 'Votre ordinateur a une carte graphique' + (f' {nom}' if nom else '')
            if vos:
                suite = f"{vos} prendra {o['duree_texte']}"
            elif c.get('classe') == 'carte_entree':
                suite = "l'analyse de vos photos se fera à bonne vitesse"
            else:
                suite = "l'analyse de vos photos sera rapide"
        return f'{debut} : {suite}.'
    proc = _court(profil.proc_nom)
    detail = f' ({proc}, {profil.coeurs} cœurs)' if proc and profil.coeurs else (f' ({proc})' if proc else '')
    if vos:
        suite = f"{vos} se fera avec le processeur{detail} et prendra {o['duree_texte']}"
    else:
        suite = f"l'analyse se fera avec le processeur{detail}, plus lentement"
    if profil.carte is not None and not o['_video_ok']:
        return f"La carte graphique n'a pas assez de mémoire pour ce modèle : {suite}."
    if profil.carte is not None and not o['_ram_carte']:
        return f"Votre ordinateur n'a pas assez de mémoire vive pour utiliser sa carte graphique avec ce modèle : {suite}."
    if profil.carte is not None:
        return f'La puce graphique de votre ordinateur ne serait pas plus rapide : {suite}.'
    return f"Votre ordinateur n'a pas de carte graphique utilisable : {suite}."


def _raisons(o, profil, n, motif, options, ecarte=None):
    if motif == 'impossible':
        return ["Pour l'instant, aucun modèle ne peut être installé sur cet ordinateur."]   # alertes : pourquoi
    r = [_phrase_materiel(o, profil, n)]
    nom = f"« {o['nom']} »"
    ram = _go_fr(profil.ram) if profil.ram is not None else None
    if motif == 'grosse_carte':
        video = _go_fr(_nombre(profil.carte.get('memoire_dediee_go')) or 0)
        r.append(f"Avec {ram} de mémoire vive et {video} de mémoire sur la carte graphique, votre ordinateur peut "
                 f"utiliser le modèle {nom}, qui trouve mieux vos photos.")
    elif motif == 'trop_de_photos':
        p = options['precis']
        combien = f'vos {_nombre_fr(n)} photos' if n else f'{_nombre_fr(NB_PHOTOS_TYPE)} photos'
        r.append(f"Le modèle « {p['nom']} » trouverait mieux vos photos, mais il mettrait "
                 f"{formater_duree(p['_duree_ref'])} à analyser {combien} : le modèle {nom} est plus raisonnable.")
    elif motif == 'petit':
        if profil.ram is not None and not _assez(profil.ram, MEMOIRE_BASE_GO):
            r.append(f'Avec {ram} de mémoire vive, le modèle {nom}, plus léger, est le plus sûr.')
        else:
            r.append(f'Sans carte graphique, le modèle {nom} analysera vos photos bien plus vite sur ce processeur.')
    elif motif == 'repli':
        autre, cause = ecarte
        nom_autre = f"« {options[autre]['nom']} »"
        if cause == 'disque':
            r.append(f"Le modèle {nom_autre} aurait été conseillé, mais il n'y a pas assez de place pour lui sur le "
                     f"disque {profil.lecteur or 'des données'}. Le modèle {nom} en prend moins.")
        else:
            r.append(f"Le modèle {nom_autre} aurait été conseillé, mais il demande plus de mémoire vive que votre "
                     f"ordinateur n'en a. Le modèle {nom} est plus léger.")
    elif motif == 'alternative':
        if o.get('description'):
            r.append(f"Modèle {nom} : {o['description']}")
    elif any(_precision(c) > _precision(o['cle']) for c in options):
        r.append(f'Le modèle {nom} est le meilleur équilibre entre précision et vitesse pour votre ordinateur.')
    else:
        r.append(f'Le modèle {nom} convient bien à votre ordinateur.')
    if not (n and o['duree_texte']) and o['debit_estime']:
        d = o['debit_estime']
        r.append(f'Vitesse estimée : environ {_decimal_fr(d, 0 if d >= 10 else 1)} photos par seconde.')
    t = o['telechargement_octets']
    if t == 0:
        r.append('Le modèle est déjà installé : rien à télécharger.')
    elif t:
        r.append(f'Le modèle se télécharge une seule fois : {formater_taille(t)}.')
    if o['materiel'] == 'processeur' and o['vision'] == 'fp32':
        d = o['_exigences']['debits']
        gain = int(round((d['processeur_fp32'] / d['processeur_fp16'] - 1) * 100))
        r.append(f"Version complète du modèle : un peu plus lourde à télécharger, mais l'analyse sur le processeur "
                 f'est environ {gain} % plus rapide.')
    return r


def _alertes(o, profil):
    """Liste de (bloquante, texte), les bloquantes d'abord."""
    al = []
    e = o['_exigences']
    lecteur = profil.lecteur or 'des données'
    if profil.libre is not None:
        besoin = o['_besoin_disque']
        if profil.libre < besoin:
            al.append((True, f"Il n'y a pas assez de place sur le disque {lecteur} (il faut {formater_taille(besoin)}, "
                             f'il en reste {formater_taille(profil.libre)}). Libérez de la place, puis recommencez.'))
        elif profil.libre - besoin < ESPACE_CONFORT:
            al.append((False, f'Il reste peu de place sur le disque {lecteur} ({formater_taille(profil.libre)} libres).'))
    conseillee = _besoin_memoire(e, o['materiel'], o['vision'], profil, 'memoire_conseillee_go')
    if not o['possible']:
        al.append((True, f"Ce modèle demande au moins {_go_fr(o['_besoin_ram'])} de mémoire vive ; votre ordinateur "
                         f'en a {_go_fr(profil.ram)}.'))
    elif profil.ram is not None and not _assez(profil.ram, conseillee):
        al.append((False, f'Votre ordinateur a peu de mémoire vive ({_go_fr(profil.ram)}) : fermez les autres '
                          f"programmes pendant l'analyse."))
    elif profil.ram_dispo is not None and profil.ram_dispo < e['memoire_moteur_go'].get(
            _usage(o['materiel'], o['vision']), 0):
        al.append((False, f'Il ne reste que {_go_fr(profil.ram_dispo)} de mémoire libre : fermez les programmes '
                          f"inutiles avant de lancer l'analyse."))
    if profil.sur_batterie:
        al.append((False, "Votre ordinateur portable fonctionne sur batterie : branchez-le pendant l'analyse."))
    if profil.compatible is False:
        al.append((False, 'Votre version de Windows est ancienne : faites les mises à jour de Windows avant '
                          "d'analyser vos photos."))
    if o['materiel'] == 'processeur' and profil.cartes_connues and not profil.carte_materielle:
        al.append((False, "Aucune carte graphique n'a été reconnue (pilote manquant ?) : l'analyse se fera avec le "
                          'processeur, plus lentement.'))
    elif o['materiel'] == 'processeur' and profil.carte_dx12 and not profil.directml:
        al.append((False, "Le calcul sur la carte graphique n'est pas disponible dans cette installation : l'analyse "
                          'se fera avec le processeur.'))
    if profil.architecture == 'ARM64':
        al.append((False, "Votre ordinateur a un processeur de type ARM : l'analyse y sera plus lente."))
    if o['duree_estimee_s'] and o['duree_estimee_s'] > DUREE_LONGUE_S:
        al.append((False, f"L'analyse sera longue ({o['duree_texte']}) : laissez l'ordinateur allumé et branché."))
    return sorted(al, key=lambda x: not x[0])


def _fois(rapport):
    return _decimal_fr(rapport, 1 if rapport < 3 else 0)        # « 2,5 fois », « 12 fois »


def _compromis(o, choix, n):
    """Une phrase sur ce que change cet autre choix par rapport au modèle conseillé."""
    if not o['possible']:
        return f"Impossible sur cet ordinateur : il faut au moins {_go_fr(o['_besoin_ram'])} de mémoire vive."
    if not o['_tient_disque']:
        return (f"Impossible pour l'instant : il faut {formater_taille(o['_besoin_disque'])} de place libre sur le "
                f'disque.')
    ecart = _precision(o['cle']) - _precision(choix['cle'])
    if ecart > 1:
        texte, mieux = 'Plus précis', True
    elif ecart > 0:
        texte, mieux = 'À peine plus précis', True
    elif ecart == 0:
        texte, mieux = 'Aussi précis', None
    else:
        texte, mieux = 'Moins précis', False
    a, b = o['debit_estime'], choix['debit_estime']
    if a and b:
        rapport = b / a                                            # > 1 : cette alternative est plus lente
        if rapport >= 1.3:
            texte += f"{', mais' if mieux is not False else ', et'} l'analyse est environ {_fois(rapport)} fois plus longue"
        elif rapport <= 1 / 1.3:
            texte += f"{', et' if mieux else ', mais'} l'analyse est environ {_fois(1 / rapport)} fois plus rapide"
        elif mieux is False:
            texte += ', et pas plus rapide sur cet ordinateur'
    if n and o['duree_texte']:
        texte += f" ({o['duree_texte']} pour vos {_nombre_fr(n)} photos)"
    t = o['telechargement_octets']
    if t and t > TELECHARGEMENT_RAISONNABLE:
        texte += f", et c'est un gros téléchargement ({formater_taille(t)})"
    return texte + '.'


def _finaliser(o, profil, n, motif, options, ecarte=None):
    o['raisons'] = _raisons(o, profil, n, motif, options, ecarte)
    alertes = _alertes(o, profil)
    o['alertes'] = [t for _, t in alertes]
    bloquantes = [t for b, t in alertes if b]
    o['bloquant'] = bool(bloquantes)
    o['raison_blocage'] = bloquantes[0] if bloquantes else None
    return o


def _nettoyer(o):
    return {k: v for k, v in o.items() if not k.startswith('_')}


def recommander(analyse, nb_photos=None):
    """Modèle conseillé pour ce PC, avec la variante, le matériel de calcul, le téléchargement, la durée estimée de
    l'analyse de nb_photos photos, des raisons et des alertes en français simple, et les autres choix possibles
    (« alternatives », avec un mot sur le compromis). Ne lève jamais d'exception."""
    try:
        return _recommander(analyse, nb_photos)
    except Exception:
        _journal.warning('matériel : conseil impossible, repli sur le modèle standard', exc_info=True)
    try:
        return _conseil_de_secours(analyse, nb_photos)
    except Exception:                        # analyse impossible à lire, même pour le secours (objet hostile…)
        _journal.warning('matériel : analyse illisible, conseil standard', exc_info=True)
        return _conseil_de_secours(None, None)


def _recommander(analyse, nb_photos):
    a = _dict(analyse)
    n = _entier(nb_photos)
    if n is None:
        n = _entier(a.get('nb_photos'))
    profil = _profil(a)
    options = {}
    for cle in _cles_proposees():
        try:
            options[cle] = _option(cle, profil, n)
        except Exception:
            _journal.debug('matériel : modèle %r ignoré', cle, exc_info=True)
    if not options:
        return _conseil_de_secours(a, n)
    cle, motif, ecarte = _choisir(options, profil, n)
    choix = _finaliser(options[cle], profil, n, motif, options, ecarte)
    alternatives = []
    for autre, o in options.items():
        if autre == cle:
            continue
        o = _finaliser(o, profil, n, 'alternative', options)
        o['compromis'] = _compromis(o, choix, n)
        alternatives.append(_nettoyer(o))
    resultat = _nettoyer(choix)
    resultat['nb_photos'] = n
    resultat['alternatives'] = alternatives
    return resultat


def _conseil_de_secours(analyse, nb_photos):
    """Conseil minimal quand l'analyse est inutilisable : le modèle standard, la carte graphique si DirectML
    existe (le moteur se replie de lui-même sur le processeur si elle refuse)."""
    try:
        cle = modele.MODELE_DEFAUT
        nom = modele.info(cle).get('nom') or cle
    except Exception:
        cle, nom = 'base', 'Standard'
    dml = _dict(analyse).get('directml') is True
    return {
        'cle': cle, 'nom': nom, 'description': None, 'vision': 'fp16', 'texte': 'fp16',
        'materiel': 'carte' if dml else 'processeur', 'indice_carte': 0 if dml else None, 'fils': None,
        'telechargement_octets': _telechargement(cle, 'fp16', 'fp16'), 'debit_estime': None,
        'duree_estimee_s': None, 'duree_texte': None, 'possible': True, 'provisoire': True,
        'raisons': [f"Le matériel n'a pas pu être analysé en détail : le modèle « {nom} » convient à la plupart des "
                    f'ordinateurs.'],
        'alertes': [], 'bloquant': False, 'raison_blocage': None, 'nb_photos': _entier(nb_photos),
        'alternatives': [],
    }


# ================================================================ mesure réelle et durées

def mesurer(moteur, n=32, lot=16, duree_max=20.0):
    """Débit réel (photos/s) du moteur sur des images synthétiques à sa taille (moteur.taille_image), modèle seul :
    sans lecture ni préparation des photos (estimer_duree() les ajoute). À appeler après le téléchargement.

    Un lot de préchauffage n'est pas compté (DirectML compile ses calculs au premier passage). La mesure s'arrête
    au bout de duree_max secondes si le moteur est très lent. None si la mesure échoue."""
    try:
        import numpy as np
        taille = int(moteur.taille_image)
        n = max(1, int(n))
        lot = max(1, min(int(lot), n))
        images = np.random.default_rng(0).uniform(-1.0, 1.0, (lot, 3, taille, taille)).astype(np.float32)
        carte = 'carte' in str(getattr(moteur, 'materiel', ''))
        moteur.vecteurs_images(images if carte else images[:min(2, lot)])   # même forme que la mesure pour DirectML
        faites = 0
        debut = time.perf_counter()
        while faites < n:
            moteur.vecteurs_images(images)
            faites += lot
            if time.perf_counter() - debut > duree_max:
                break
        ecoule = time.perf_counter() - debut
        return round(faites / ecoule, 2) if ecoule > 0 else None
    except Exception:
        _journal.debug('matériel : mesure impossible', exc_info=True)
        return None


def estimer_duree(analyse, debit_modele, nb_photos, materiel='carte'):
    """Durée (s) de l'analyse de nb_photos à partir d'un débit mesuré par mesurer() : ajoute la préparation des
    photos par le processeur, qui plafonne le débit. materiel : 'carte' ou 'processeur' (ou moteur.materiel).
    None si le calcul est impossible."""
    try:
        d, n = _nombre(debit_modele), _entier(nb_photos)
        if not d or d <= 0 or n is None:
            return None
        sur = 'processeur' if 'processeur' in str(materiel) else 'carte'
        total = _avec_decodage(d, sur, _profil(analyse))
        return int(round(n / total)) if total else None
    except Exception:
        return None


def formater_duree(secondes):
    """« moins d'une minute », « environ 2 minutes », « environ 1 heure 15 », « environ 5 heures »...
    None si la durée est inconnue."""
    s = _nombre(secondes)
    if s is None or s < 0:
        return None
    if s < 50:
        return "moins d'une minute"
    minutes = s / 60
    if minutes < 10:
        m = max(1, int(minutes + 0.5))
        return f"environ {m} minute{'s' if m > 1 else ''}"
    if minutes < 57.5:
        return f"environ {5 * int(minutes / 5 + 0.5)} minutes"
    heures = s / 3600
    if heures < 3:
        quarts = int(heures * 4 + 0.5)
        h, reste = divmod(quarts, 4)
        return f"environ {h} heure{'s' if h > 1 else ''}" + (f" {reste * 15}" if reste else '')
    if heures < 47.5:
        return f"environ {int(heures + 0.5)} heures"
    return f"environ {int(heures / 24 + 0.5)} jours"


def formater_taille(octets):
    """« 749 Mo », « 1,7 Go » (unités de Windows : 1 Go = 1 024 Mo). None si la taille est inconnue."""
    o = _nombre(octets)
    if o is None or o < 0:
        return None
    if o < MO:
        return 'moins de 1 Mo'
    if o < GO:
        return f'{_nombre_fr(int(o / MO + 0.5))} Mo'
    return _go_fr(o / GO)
