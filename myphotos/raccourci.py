"""Raccourcis de MyPhotos.exe : « MyPhotos » sur le Bureau et dans le menu Démarrer (Réglages de la page).

Les fichiers MyPhotos.lnk sont écrits par l'interface IShellLink de Windows (objet COM ShellLink, appelé par ctypes),
dans les dossiers de l'utilisateur : ni installation, ni droits d'administrateur, ni PowerShell, ni cmd. Ils
pointent vers l'exécutable en cours, avec son icône. Aucun identifiant d'application (AppUserModelID) n'y est
écrit : MyPhotos.exe n'en pose pas non plus, si bien que Windows regroupe sa fenêtre, ses raccourcis et un
épinglage fait depuis l'Explorateur sous le même bouton de la barre des tâches (identifiant tiré du chemin).

Depuis les sources (python -m myphotos), pas de raccourci : il pointerait vers python.exe.
"""
import ctypes
import os
import sys
import tempfile
import uuid
from ctypes import wintypes
from pathlib import Path

from . import config

NOM_LIEN = 'MyPhotos.lnk'
DESCRIPTION = 'MyPhotos : retrouvez vos photos en tapant des mots'
FOLDERID_DESKTOP = '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}'
FOLDERID_PROGRAMS = '{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}'     # menu Démarrer > Programmes, de l'utilisateur
CLSID_SHELLLINK = '{00021401-0000-0000-C000-000000000046}'
IID_ISHELLLINKW = '{000214F9-0000-0000-C000-000000000046}'
IID_IPERSISTFILE = '{0000010B-0000-0000-C000-000000000046}'
CLSCTX_INPROC_SERVER = 1
MAX_CHEMIN = 32768


class RaccourciRefuse(Exception):
    """Raccourci impossible ici ; le message, en français simple, s'affiche dans la page."""


class _GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                ('Data4', ctypes.c_ubyte * 8)]


def _guid(texte):
    return _GUID.from_buffer_copy(uuid.UUID(texte).bytes_le)


def _dll(nom):
    d = ctypes.WinDLL(nom, use_last_error=True)
    return d


_OLE32 = _SHELL32 = None


def _ole32():
    global _OLE32
    if _OLE32 is None:
        d = _dll('ole32')
        d.CoInitializeEx.argtypes, d.CoInitializeEx.restype = [ctypes.c_void_p, wintypes.DWORD], ctypes.c_long
        d.CoUninitialize.argtypes, d.CoUninitialize.restype = [], None
        d.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_GUID),
                                       ctypes.POINTER(ctypes.c_void_p)]
        d.CoCreateInstance.restype = ctypes.c_long
        d.CoTaskMemFree.argtypes, d.CoTaskMemFree.restype = [ctypes.c_void_p], None
        _OLE32 = d
    return _OLE32


def _shell32():
    global _SHELL32
    if _SHELL32 is None:
        d = _dll('shell32')
        d.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                                           ctypes.POINTER(ctypes.c_void_p)]
        d.SHGetKnownFolderPath.restype = ctypes.c_long
        _SHELL32 = d
    return _SHELL32


def possible():
    """Vrai si MyPhotos peut créer ses raccourcis : l'exécutable, sous Windows."""
    return os.name == 'nt' and config.GELE


def dossier_connu(folderid):
    """Dossier connu de Windows (Bureau, menu Démarrer…), redirigé vers OneDrive le cas échéant ; None sinon."""
    sortie = ctypes.c_void_p()
    if _shell32().SHGetKnownFolderPath(ctypes.byref(_guid(folderid)), 0, None, ctypes.byref(sortie)) != 0:
        return None
    try:
        return ctypes.wstring_at(sortie.value) if sortie.value else None
    finally:
        _ole32().CoTaskMemFree(sortie)


def emplacements(bureau=None, programmes=None):
    """{'bureau': …\\MyPhotos.lnk, 'menu': …\\MyPhotos.lnk} : là où les raccourcis sont écrits."""
    res = {}
    for cle, dossier, folderid in (('bureau', bureau, FOLDERID_DESKTOP), ('menu', programmes, FOLDERID_PROGRAMS)):
        d = dossier if dossier is not None else dossier_connu(folderid)
        if d:
            res[cle] = Path(d) / NOM_LIEN
    return res


def _dans_un_dossier_temporaire(chemin):
    """Vrai si l'exécutable tourne depuis un dossier temporaire (archive .zip ouverte sans l'extraire…)."""
    p = os.path.normcase(os.path.abspath(chemin))
    for t in {tempfile.gettempdir(), os.environ.get('TEMP') or '', os.environ.get('TMP') or ''}:
        if t:
            t = os.path.normcase(os.path.abspath(t))
            if p == t or p.startswith(t.rstrip('\\/') + os.sep):
                return True
    return False


def verifier_cible(exe):
    """Refus clair si le raccourci ne doit pas viser cet exécutable."""
    if not possible():
        raise RaccourciRefuse('Le raccourci se crée depuis MyPhotos.exe.')
    if not exe or not os.path.isfile(exe):
        raise RaccourciRefuse('MyPhotos.exe est introuvable.')
    if _dans_un_dossier_temporaire(exe):
        raise RaccourciRefuse('MyPhotos est lancé depuis un dossier temporaire (archive .zip ouverte sans '
                              'l\'extraire ?). Copiez d\'abord MyPhotos.exe dans un dossier à vous, par exemple '
                              'Documents, lancez-le de là, puis créez le raccourci.')


# ---------------------------------------------------------------- COM : IShellLinkW et IPersistFile

def _methode(objet, index, *arguments):
    """Méthode n° index de la table virtuelle d'une interface COM (premier argument : l'objet lui-même)."""
    table = ctypes.cast(objet, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)
    fonction = prototype(table[index])
    return lambda *valeurs: fonction(objet, *valeurs)


def _verifier(hr, quoi):
    if hr < 0:
        raise OSError(f'{quoi} : erreur {hr & 0xFFFFFFFF:#010x}')


class _Com:
    """COM initialisé pour le fil appelant le temps d'un bloc with (fil d'une requête HTTP)."""

    def __enter__(self):
        self.hr = _ole32().CoInitializeEx(None, 0x2)                  # COINIT_APARTMENTTHREADED
        return self

    def __exit__(self, *_):
        if self.hr in (0, 1):                                         # S_OK, S_FALSE : à défaire
            _ole32().CoUninitialize()


def _lien():
    """Objet ShellLink : (IShellLinkW, IPersistFile)."""
    lien = ctypes.c_void_p()
    _verifier(_ole32().CoCreateInstance(ctypes.byref(_guid(CLSID_SHELLLINK)), None, CLSCTX_INPROC_SERVER,
                                        ctypes.byref(_guid(IID_ISHELLLINKW)), ctypes.byref(lien)), 'ShellLink')
    fichier = ctypes.c_void_p()
    hr = _methode(lien, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(_guid(IID_IPERSISTFILE)), ctypes.byref(fichier))         # QueryInterface
    if hr < 0:
        _liberer(lien)
        _verifier(hr, 'IPersistFile')
    return lien, fichier


def _liberer(*objets):
    for o in objets:
        if o and o.value:
            _methode(o, 2)()                                          # Release


def creer_lien(lien, cible, dossier, icone, description=DESCRIPTION):
    """Écrit le raccourci `lien` (.lnk) vers `cible` ; remplace un raccourci du même nom."""
    lien = str(lien)
    w = ctypes.c_wchar_p
    with _Com():
        shell, fichier = _lien()
        try:
            _verifier(_methode(shell, 20, w)(cible), 'SetPath')
            _verifier(_methode(shell, 9, w)(dossier), 'SetWorkingDirectory')
            _verifier(_methode(shell, 17, w, ctypes.c_int)(icone, 0), 'SetIconLocation')
            _verifier(_methode(shell, 7, w)(description), 'SetDescription')
            _verifier(_methode(fichier, 6, w, wintypes.BOOL)(lien, True), 'Save')
        finally:
            _liberer(fichier, shell)


def lire_lien(lien):
    """(cible, dossier de travail, icône) d'un raccourci : pour vérifier ce qui a été écrit."""
    w = ctypes.c_wchar_p
    with _Com():
        shell, fichier = _lien()
        try:
            _verifier(_methode(fichier, 5, w, wintypes.DWORD)(str(lien), 0), 'Load')
            cible, dossier, icone = (ctypes.create_unicode_buffer(MAX_CHEMIN) for _ in range(3))
            indice = ctypes.c_int()
            _verifier(_methode(shell, 3, w, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)(
                cible, MAX_CHEMIN, None, 0x4), 'GetPath')                       # SLGP_RAWPATH
            _verifier(_methode(shell, 8, w, ctypes.c_int)(dossier, MAX_CHEMIN), 'GetWorkingDirectory')
            _verifier(_methode(shell, 16, w, ctypes.c_int, ctypes.POINTER(ctypes.c_int))(
                icone, MAX_CHEMIN, ctypes.byref(indice)), 'GetIconLocation')
            return cible.value, dossier.value, (icone.value, indice.value)
        finally:
            _liberer(fichier, shell)


def creer_raccourcis(exe=None, cibles=None):
    """Crée MyPhotos.lnk sur le Bureau et dans le menu Démarrer, vers l'exécutable en cours. Rend la liste de ce
    qui a été créé ('bureau', 'menu') ; RaccourciRefuse ou OSError sinon."""
    exe = exe or sys.executable
    verifier_cible(exe)
    exe = os.path.abspath(exe)
    cibles = emplacements() if cibles is None else cibles
    if not cibles:
        raise RaccourciRefuse('Windows n\'indique ni Bureau ni menu Démarrer pour cet utilisateur.')
    crees, erreur = [], None
    for cle in ('bureau', 'menu'):
        lien = cibles.get(cle)
        if lien is None:
            continue
        try:
            Path(lien).parent.mkdir(parents=True, exist_ok=True)
            creer_lien(lien, exe, os.path.dirname(exe), exe)
            crees.append(cle)
        except OSError as e:
            erreur = e
    if not crees:
        raise erreur or RaccourciRefuse('Aucun raccourci n\'a pu être créé.')
    return crees
