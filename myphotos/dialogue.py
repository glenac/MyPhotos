"""Fenêtre « Choisir un dossier » de Windows (IFileOpenDialog), pour les replis : Edge en mode application et le
navigateur (0.4.0 ; avant : tkinter, Tcl/Tk embarqués pour cette seule fenêtre).

La boîte de dialogue de Windows (« Common Item Dialog ») est appelée par COM, avec ctypes, comme raccourci.py le
fait pour IShellLinkW : FOS_PICKFOLDERS (un dossier, pas un fichier), FOS_FORCEFILESYSTEM (un vrai dossier du
disque : ni « Ce PC », ni bibliothèque, ni appareil photo branché en MTP), FOS_PATHMUSTEXIST, FOS_NOCHANGEDIR (le
dossier courant de MyPhotos ne bouge pas) et FOS_DONTADDTORECENT (les « Éléments récents » de Windows ne sont pas
touchés). Elle s'ouvre dans le dossier proposé (SetFolder), avec son propre identifiant (SetClientGuid) : sa taille
est retenue à part de celle des autres programmes.

Dans un fil COM STA qui lui est réservé (ouvrir_devant) : la page, dans Edge ou le navigateur, a le clavier ; une
fenêtre d'un autre programme ne peut pas lui passer devant. La fenêtre de Windows appartient donc à une petite
fenêtre hôte de MyPhotos, invisible (transparente, 1 × 1 pixel), toujours au-dessus et mise au premier plan, posée
au milieu de la fenêtre de MyPhotos (Edge) quand on la connaît, sinon de l'écran : elle s'ouvre devant, au bon
endroit, et la barre des tâches montre « MyPhotos » tant qu'elle est ouverte. La fenêtre d'Edge (un autre
processus) n'est jamais prise comme propriétaire : Windows lierait les deux files de messages, et Edge resterait
désactivé si MyPhotos s'arrêtait pendant le choix.

Dans la fenêtre de MyPhotos (WebView2), fenetre.ChoixDossierFenetre ouvre la même boîte de Windows, par Windows
Forms, sur le fil de la fenêtre, qui en est la propriétaire.
"""
import ctypes
import logging
from ctypes import wintypes

from . import config
from .raccourci import _GUID, _guid, _liberer, _methode, _ole32, _verifier

journal = logging.getLogger(__name__)

NOM = 'MyPhotos'
CLSID_FILEOPENDIALOG = '{DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7}'
IID_IFILEOPENDIALOG = '{D57C7288-D4AD-4768-BE02-9D969532D960}'
IID_ISHELLITEM = '{43826D1E-E718-42EE-BC55-A1E261C37BFE}'
CLIENT = '{6F1C7A52-6B0E-4F43-9C8B-2D1B5E0A7D31}'      # identifiant de la fenêtre de MyPhotos (taille retenue à part)
CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED, COINIT_DISABLE_OLE1DDE = 0x2, 0x4
FOS_NOCHANGEDIR, FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM = 0x8, 0x20, 0x40
FOS_PATHMUSTEXIST, FOS_DONTADDTORECENT = 0x800, 0x2000000
OPTIONS = FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR | FOS_DONTADDTORECENT
SIGDN_FILESYSPATH = 0x80058000
ANNULE = 0x800704C7                                    # HRESULT_FROM_WIN32(ERROR_CANCELLED) : « Annuler », Échap, croix

# Tables virtuelles (shobjidl_core.h). IFileOpenDialog : IUnknown (0-2), IModalWindow::Show (3), puis IFileDialog.
SHOW, SET_OPTIONS, GET_OPTIONS, SET_FOLDER, SET_TITLE, GET_RESULT, SET_CLIENT_GUID = 3, 9, 10, 12, 17, 20, 24
GET_DISPLAY_NAME = 5                                   # IShellItem

# Fenêtre hôte.
WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_LAYERED = 0x8, 0x80000
LWA_ALPHA = 0x2
SW_SHOWNA = 8
WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CYSMICON, SM_CXICON, SM_CYICON = 49, 50, 11, 12
SPI_GETWORKAREA = 0x0030
MONITOR_DEFAULTTONEAREST = 2
DPI_PAR_ECRAN_V2 = -4                                  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2

_DLL = {}


def _dll(nom):
    """Instance privée d'une DLL : nos argtypes ne touchent pas ceux des autres modules."""
    if nom not in _DLL:
        d = ctypes.WinDLL(nom, use_last_error=True)
        v, u, i, b, w = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, wintypes.BOOL, ctypes.c_wchar_p
        signatures = {
            'user32': {'CreateWindowExW': ([u, w, w, u, i, i, i, i, v, v, v, v], v), 'DestroyWindow': ([v], b),
                       'SetLayeredWindowAttributes': ([v, u, ctypes.c_ubyte, u], b), 'ShowWindow': ([v, i], b),
                       'SendMessageW': ([v, u, wintypes.WPARAM, wintypes.LPARAM], wintypes.LPARAM),
                       'LoadImageW': ([v, w, u, i, i, u], v), 'DestroyIcon': ([v], b),
                       'GetSystemMetrics': ([i], i),
                       'SystemParametersInfoW': ([u, u, ctypes.POINTER(wintypes.RECT), u], b),
                       'GetForegroundWindow': ([], v), 'GetWindowThreadProcessId': ([v, v], u),
                       'AttachThreadInput': ([u, u, b], b), 'SetForegroundWindow': ([v], b),
                       'BringWindowToTop': ([v], b), 'SetThreadDpiAwarenessContext': ([v], v),
                       'MonitorFromPoint': ([wintypes.POINT, u], v),
                       'GetMonitorInfoW': ([v, ctypes.POINTER(_MONITORINFO)], b)},
            'kernel32': {'GetCurrentThreadId': ([], u)},
            'shell32': {'SHCreateItemFromParsingName': ([w, v, ctypes.POINTER(_GUID), ctypes.POINTER(v)],
                                                        ctypes.c_long)},
            'ole32': {'CoInitializeEx': ([v, u], ctypes.c_long), 'CoUninitialize': ([], None)},
        }[nom]
        for fonction, (arguments, retour) in signatures.items():
            try:
                f = getattr(d, fonction)
            except AttributeError:                  # Windows trop ancien pour cette fonction
                continue
            f.argtypes, f.restype = arguments, retour
        _DLL[nom] = d
    return _DLL[nom]


# ---------------------------------------------------------------- la boîte de dialogue (COM)

def _hr(valeur):
    return valeur & 0xFFFFFFFF


def _element(chemin):
    """IShellItem du dossier `chemin`, ou None (absent, débranché, chemin que Windows ne sait pas lire)."""
    element = ctypes.c_void_p()
    try:
        hr = _dll('shell32').SHCreateItemFromParsingName(chemin, None, ctypes.byref(_guid(IID_ISHELLITEM)),
                                                         ctypes.byref(element))
    except (OSError, ValueError):
        return None
    return element if hr >= 0 and element.value else None


def _creer():
    dialogue = ctypes.c_void_p()
    _verifier(_ole32().CoCreateInstance(ctypes.byref(_guid(CLSID_FILEOPENDIALOG)), None, CLSCTX_INPROC_SERVER,
                                        ctypes.byref(_guid(IID_IFILEOPENDIALOG)), ctypes.byref(dialogue)),
              'FileOpenDialog')
    return dialogue


def _options(dialogue):
    options = wintypes.DWORD()
    _verifier(_methode(dialogue, GET_OPTIONS, ctypes.POINTER(wintypes.DWORD))(ctypes.byref(options)), 'GetOptions')
    return options.value


def _preparer(dialogue, titre, initial):
    """Réglages, avant Show : un dossier du disque, titre, identifiant, dossier proposé (s'il existe encore)."""
    _verifier(_methode(dialogue, SET_OPTIONS, wintypes.DWORD)(_options(dialogue) | OPTIONS), 'SetOptions')
    _verifier(_methode(dialogue, SET_CLIENT_GUID, ctypes.POINTER(_GUID))(ctypes.byref(_guid(CLIENT))),
              'SetClientGuid')
    _verifier(_methode(dialogue, SET_TITLE, ctypes.c_wchar_p)(titre), 'SetTitle')
    if initial:
        dossier = _element(initial)
        if dossier is not None:
            try:
                _methode(dialogue, SET_FOLDER, ctypes.c_void_p)(dossier)     # sans lui : le dernier dossier vu
            finally:
                _liberer(dossier)


def verifier(titre='MyPhotos', initial=None):
    """Crée et règle la fenêtre sans l'afficher, puis la libère : essais et construction/essai_exe.py (la classe
    COM, ses interfaces et l'ordre de leurs méthodes). Rend les options lues après réglage. Fil COM STA exigé."""
    dialogue = _creer()
    try:
        _preparer(dialogue, titre, initial)
        return _options(dialogue)
    finally:
        _liberer(dialogue)


def choisir_dossier(titre, initial=None, proprietaire=None):
    """Ouvre la fenêtre « Choisir un dossier » de Windows et attend : chemin du dossier choisi, ou None (annulé).
    À appeler dans un fil COM STA (ouvrir_devant s'en charge). proprietaire : HWND à qui elle appartient (elle
    s'ouvre devant lui et le bloque tant qu'elle est ouverte), ou None. OSError si Windows refuse."""
    dialogue = _creer()
    try:
        _preparer(dialogue, titre, initial)
        hr = _methode(dialogue, SHOW, ctypes.c_void_p)(proprietaire)
        if _hr(hr) == ANNULE:
            return None
        _verifier(hr, 'Show')
        element = ctypes.c_void_p()
        _verifier(_methode(dialogue, GET_RESULT, ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(element)),
                  'GetResult')
        try:
            texte = ctypes.c_void_p()
            _verifier(_methode(element, GET_DISPLAY_NAME, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(
                SIGDN_FILESYSPATH, ctypes.byref(texte)), 'GetDisplayName')
            try:
                chemin = ctypes.wstring_at(texte.value) if texte.value else ''
            finally:
                _ole32().CoTaskMemFree(texte)
        finally:
            _liberer(element)
        return chemin or None
    finally:
        _liberer(dialogue)


# ---------------------------------------------------------------- devant la page : fil STA et fenêtre hôte

class _MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD)]


def zone_de_travail(x, y):
    """(gauche, haut, droite, bas) de la zone de travail (sans la barre des tâches) de l'écran le plus proche de ce
    point."""
    u32 = _dll('user32')
    infos = _MONITORINFO()
    infos.cbSize = ctypes.sizeof(infos)
    ecran = u32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    if ecran and u32.GetMonitorInfoW(ecran, ctypes.byref(infos)):
        r = infos.rcWork
    else:
        r = wintypes.RECT()
        if not u32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            r = wintypes.RECT(0, 0, u32.GetSystemMetrics(0), u32.GetSystemMetrics(1))
    return r.left, r.top, r.right, r.bottom


def position_hote(zone, travail):
    """Coin de la fenêtre hôte. La fenêtre de Windows s'ouvre avec son coin haut gauche sur celui de sa
    propriétaire, et fait d'office la moitié de la zone de travail de l'écran (mesuré le 25/11/2026, Windows 11) :
    l'hôte est donc posé un quart de zone en haut à gauche du centre visé (la fenêtre de MyPhotos, zone, sinon
    l'écran), pour qu'elle s'ouvre au milieu. Windows la garde ensuite entière sur l'écran."""
    g, h, d, b = travail
    if zone and zone[2] > zone[0] and zone[3] > zone[1]:
        cx, cy = (zone[0] + zone[2]) // 2, (zone[1] + zone[3]) // 2
    else:
        cx, cy = (g + d) // 2, (h + b) // 2
    return max(g, min(cx - (d - g) // 4, d - 1)), max(h, min(cy - (b - h) // 4, b - 1))


def creer_hote(zone=None):
    """Fenêtre hôte : invisible (transparente, 1 × 1), toujours au-dessus, placée pour que la fenêtre de Windows
    s'ouvre au milieu de `zone` (fenêtre de MyPhotos) ou de l'écran principal ; titre et icône de MyPhotos (barre
    des tâches). Rend (hwnd, icônes à détruire après elle)."""
    u32 = _dll('user32')
    if zone and zone[2] > zone[0] and zone[3] > zone[1]:
        travail = zone_de_travail((zone[0] + zone[2]) // 2, (zone[1] + zone[3]) // 2)
    else:
        travail = zone_de_travail(0, 0)             # écran principal : (0, 0) est toujours à lui
    x, y = position_hote(zone, travail)
    hwnd = u32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOPMOST, 'STATIC', NOM, WS_POPUP, x, y, 1, 1, None, None,
                               None, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    u32.SetLayeredWindowAttributes(hwnd, 0, 0, LWA_ALPHA)
    icones = []
    icone = config.CODE / 'construction' / 'MyPhotos.ico'
    if icone.is_file():
        for taille, (cx, cy) in ((ICON_SMALL, (SM_CXSMICON, SM_CYSMICON)), (ICON_BIG, (SM_CXICON, SM_CYICON))):
            h = u32.LoadImageW(None, str(icone), IMAGE_ICON, u32.GetSystemMetrics(cx), u32.GetSystemMetrics(cy),
                               LR_LOADFROMFILE)
            if h:
                icones.append(h)
                u32.SendMessageW(hwnd, WM_SETICON, taille, h)
    u32.ShowWindow(hwnd, SW_SHOWNA)
    return hwnd, icones


def premier_plan(hwnd):
    """Met la fenêtre hôte au premier plan alors que c'est Edge ou le navigateur qui a le clavier : le temps de
    l'appel, le fil se rattache à celui de la fenêtre de devant (AttachThreadInput)."""
    u32 = _dll('user32')
    devant = u32.GetForegroundWindow()
    fil_devant = u32.GetWindowThreadProcessId(devant, None) if devant else 0
    moi = _dll('kernel32').GetCurrentThreadId()
    attache = bool(fil_devant and fil_devant != moi and u32.AttachThreadInput(moi, fil_devant, True))
    try:
        u32.BringWindowToTop(hwnd)
        return bool(u32.SetForegroundWindow(hwnd))
    finally:
        if attache:
            u32.AttachThreadInput(moi, fil_devant, False)


class Sta:
    """COM en « appartement à un seul fil » (STA, ce qu'exige la fenêtre de Windows) pour le fil appelant, le temps
    d'un bloc with. Un fil déjà en MTA : OSError (la fenêtre ne s'y ouvrirait pas correctement)."""

    def __enter__(self):
        self.hr = _dll('ole32').CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
        _verifier(self.hr, 'CoInitializeEx')
        return self

    def __exit__(self, *_):
        if self.hr >= 0:                              # S_OK, S_FALSE : à défaire
            _dll('ole32').CoUninitialize()


def ouvrir_devant(titre, initial=None, zone=None):
    """Ce que fait serveur.ChoixDossier, dans un fil qui lui est réservé : COM en STA pour ce fil, DPI « par écran »
    (fenêtre nette sur un écran agrandi), fenêtre hôte au premier plan, puis la fenêtre de Windows, qui lui
    appartient. Rend le chemin choisi, ou None."""
    with Sta():
        u32 = _dll('user32')
        if hasattr(u32, 'SetThreadDpiAwarenessContext'):
            u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(DPI_PAR_ECRAN_V2))
        hote, icones = creer_hote(zone)
        try:
            try:
                premier_plan(hote)
            except OSError:
                journal.debug('premier plan impossible', exc_info=True)    # elle s'ouvre quand même
            return choisir_dossier(titre, initial, hote)
        finally:
            u32.DestroyWindow(hote)
            for h in icones:
                u32.DestroyIcon(h)

