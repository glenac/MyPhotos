"""Outils de l'essai réel de la fenêtre (tests/test_fenetre.py, MYPHOTOS_ESSAI_FENETRE=1), bibliothèque standard
seulement.

- Bureau Windows invisible (CreateDesktop) : MyPhotos y est lancé (CreateProcess, lpDesktop), ses fenêtres
  n'apparaissent jamais sur l'écran de l'utilisateur et ne lui prennent pas le clavier. Les opérations sur ces
  fenêtres passent par un fil attaché à ce bureau (SetThreadDesktop) : EnumWindows, PostMessage, ShowWindow, IsIconic.
- Client minimal du protocole DevTools (CDP) : WebSocket texte, appels, touches du clavier, capture de la page.
- Processus lancés DANS un Job Object de l'essai (tests/processus_essai.py) : on les liste par le job, et le filet
  d'arrêt est TerminateJobObject de ce job. Jamais d'arrêt par identifiant retrouvé, par PID parent ni « /T » de l'outil d'arrêt de Windows.
"""
import base64
import ctypes
import http.client
import json
import os
import queue
import socket
import struct
import subprocess
import threading
import time
import urllib.parse
from ctypes import wintypes

GENERIC_ALL = 0x10000000
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_SUSPENDED = 0x00000004
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_CLOSE = 0x0010
SW_MINIMIZE = 6

if os.name == 'nt':
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    u32 = ctypes.WinDLL('user32', use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
                    ('lpTitle', wintypes.LPWSTR), ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                    ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD), ('dwXCountChars', wintypes.DWORD),
                    ('dwYCountChars', wintypes.DWORD), ('dwFillAttribute', wintypes.DWORD),
                    ('dwFlags', wintypes.DWORD), ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                    ('lpReserved2', ctypes.c_void_p), ('hStdInput', wintypes.HANDLE),
                    ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE), ('dwProcessId', wintypes.DWORD),
                    ('dwThreadId', wintypes.DWORD)]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', ctypes.c_wchar * 260)]

    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u32.CreateDesktopW.restype = wintypes.HANDLE
    u32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_void_p]
    u32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    u32.CloseDesktop.argtypes = [wintypes.HANDLE]
    u32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
    u32.EnumChildWindows.argtypes = [wintypes.HWND, EnumProc, wintypes.LPARAM]
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u32.IsIconic.argtypes = [wintypes.HWND]
    u32.IsWindowVisible.argtypes = [wintypes.HWND]
    u32.IsWindowEnabled.argtypes = [wintypes.HWND]
    u32.GetWindow.restype = wintypes.HWND
    u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    u32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    u32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                   wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


# ---------------------------------------------------------------- bureau invisible et processus

def creer_bureau(nom):
    h = u32.CreateDesktopW(nom, None, None, 0, GENERIC_ALL, None)
    if not h:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def fermer_bureau(h):
    if h:
        u32.CloseDesktop(h)


def lancer(arguments, bureau, dossier, env, job):
    """Lance `arguments` sur le bureau « WinSta0\\<bureau> », avec l'environnement `env` (dict), DANS le Job Object
    `job` (processus_essai.Job) : créé suspendu, mis dans le job, puis relancé. Rend (pid, handle)."""
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    nom = ctypes.create_unicode_buffer('WinSta0\\' + bureau)
    si.lpDesktop = ctypes.cast(nom, wintypes.LPWSTR)
    pi = PROCESS_INFORMATION()
    ligne = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    bloc = ''.join(f'{k}={v}\0' for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + '\0'
    tampon = ctypes.create_unicode_buffer(bloc, len(bloc))
    ok = k32.CreateProcessW(None, ligne, None, None, False,
                            CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED,
                            ctypes.cast(tampon, ctypes.c_void_p), dossier, ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        job.ajouter(pi.hProcess)                 # en cas d'échec : arrêté par SON handle, erreur levée
        k32.ResumeThread(pi.hThread)
    except Exception:
        k32.CloseHandle(pi.hProcess)
        raise
    finally:
        k32.CloseHandle(pi.hThread)
    return pi.dwProcessId, pi.hProcess


def code_sortie(h):
    c = wintypes.DWORD()
    k32.GetExitCodeProcess(h, ctypes.byref(c))
    return None if c.value == STILL_ACTIVE else c.value


def attendre_processus(h, delai_s):
    return k32.WaitForSingleObject(h, int(delai_s * 1000)) == 0


def fermer_handle(h):
    if h:
        k32.CloseHandle(h)


def processus():
    """{pid: (pid du parent, nom)} de tous les processus."""
    h = k32.CreateToolhelp32Snapshot(0x2, 0)
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    res = {}
    try:
        if k32.Process32FirstW(h, ctypes.byref(e)):
            while True:
                res[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile)
                if not k32.Process32NextW(h, ctypes.byref(e)):
                    break
    finally:
        k32.CloseHandle(h)
    return res


def du_job(job):
    """[(pid, nom)] des processus vivants du Job Object de l'essai (lus par le job, jamais par les PID parents)."""
    noms = processus()
    return [(p, noms.get(p, (0, '?'))[1]) for p in job.pids()]


class FilBureau:
    """Fil attaché au bureau invisible : toutes les opérations sur ses fenêtres passent par lui."""

    def __init__(self, hbureau):
        self._q = queue.Queue()
        self._hbureau = hbureau
        self._pret = threading.Event()
        self._erreur = None
        threading.Thread(target=self._boucle, name='essai-bureau', daemon=True).start()
        self._pret.wait(5)
        if self._erreur:
            raise self._erreur

    def _boucle(self):
        if not u32.SetThreadDesktop(self._hbureau):
            self._erreur = ctypes.WinError(ctypes.get_last_error())
            self._pret.set()
            return
        self._pret.set()
        while True:
            f, r = self._q.get()
            try:
                r.put((True, f()))
            except Exception as e:
                r.put((False, e))

    def faire(self, f):
        r = queue.Queue()
        self._q.put((f, r))
        ok, v = r.get(timeout=60)
        if not ok:
            raise v
        return v


def conscience_dpi(hwnd):
    """DPI d'une fenêtre : 0 ignoré, 1 « système », 2 « par écran » (celle de MyPhotos : 2)."""
    return u32.GetAwarenessFromDpiAwarenessContext(u32.GetWindowDpiAwarenessContext(hwnd))


def _texte(fonction, hwnd):
    b = ctypes.create_unicode_buffer(2048)
    fonction(hwnd, b, 2048)
    return b.value


def textes(hwnd):
    """(à appeler depuis FilBureau) Textes des fenêtres enfants : message et boutons d'une boîte de dialogue."""
    res = []

    @EnumProc
    def rappel(h, _):
        t = _texte(u32.GetWindowTextW, h)
        if t:
            res.append(t)
        return True
    u32.EnumChildWindows(hwnd, rappel, 0)
    return '\n'.join(res)


def fenetres(pids):
    """(à appeler depuis FilBureau) Fenêtres de premier niveau de ces processus : [{hwnd, pid, classe, titre…}]."""
    res = []

    @EnumProc
    def rappel(hwnd, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value in pids:
            res.append({'hwnd': hwnd, 'pid': p.value, 'classe': _texte(u32.GetClassNameW, hwnd),
                        'titre': _texte(u32.GetWindowTextW, hwnd), 'visible': bool(u32.IsWindowVisible(hwnd)),
                        'proprietaire': u32.GetWindow(hwnd, 4) or 0})        # GW_OWNER
        return True
    u32.EnumWindows(rappel, 0)
    return res


# ---------------------------------------------------------------- HTTP et protocole DevTools

def requete_json(port, chemin, corps=None, delai=30):
    """GET (ou POST JSON comme la page) vers MyPhotos : (statut, réponse)."""
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=delai)
    entetes = {'Host': f'127.0.0.1:{port}'}
    brut = None
    if corps is not None:
        brut = json.dumps(corps).encode('utf-8')
        entetes.update({'Content-Type': 'application/json', 'X-MyPhotos': '1', 'Origin': f'http://127.0.0.1:{port}',
                        'Sec-Fetch-Site': 'same-origin'})
    try:
        c.request('POST' if corps is not None else 'GET', chemin, body=brut, headers=entetes)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b'null')
    finally:
        c.close()


def cibles(port, delai=2):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=delai)
    try:
        c.request('GET', '/json/list')
        return json.loads(c.getresponse().read())
    finally:
        c.close()


def attendre_page(port, prefixe, delai=90):
    """Cible « page » du port de débogage dont l'adresse commence par `prefixe`."""
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        try:
            for x in cibles(port):
                if x.get('type') == 'page' and x.get('url', '').startswith(prefixe):
                    return x
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise TimeoutError(f'aucune page {prefixe} sur le port de débogage {port}')


class CDP:
    """Connexion WebSocket à une page : appels (id), événements gardés, évaluation, touches, capture."""

    def __init__(self, url, delai=60):
        u = urllib.parse.urlsplit(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=delai)
        cle = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n'
                        f'Connection: Upgrade\r\nSec-WebSocket-Key: {cle}\r\nSec-WebSocket-Version: 13\r\n\r\n')
                       .encode())
        tampon = b''
        while b'\r\n\r\n' not in tampon:
            bloc = self.s.recv(4096)
            if not bloc:
                raise ConnectionError('poignée de main WebSocket refusée')
            tampon += bloc
        tete, self._reste = tampon.split(b'\r\n\r\n', 1)
        if b' 101 ' not in tete.split(b'\r\n')[0]:
            raise ConnectionError(tete[:200])
        self._n = 0
        self.evenements = []

    def _lire(self, k):
        while len(self._reste) < k:
            bloc = self.s.recv(1 << 20)
            if not bloc:
                raise ConnectionError('WebSocket fermé')
            self._reste += bloc
        d, self._reste = self._reste[:k], self._reste[k:]
        return d

    def _envoyer(self, texte):
        p = texte.encode()
        masque = os.urandom(4)
        n = len(p)
        if n < 126:
            tete = bytes([0x81, 0x80 | n])
        elif n < 65536:
            tete = bytes([0x81, 0x80 | 126]) + struct.pack('>H', n)
        else:
            tete = bytes([0x81, 0x80 | 127]) + struct.pack('>Q', n)
        self.s.sendall(tete + masque + bytes(b ^ masque[i % 4] for i, b in enumerate(p)))

    def _recevoir(self):
        morceaux = []
        while True:
            b0, b1 = self._lire(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._lire(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._lire(8))[0]
            d = self._lire(n)
            op = b0 & 0x0F
            if op == 0x8:
                raise ConnectionError('WebSocket fermé par le navigateur')
            if op in (0x9, 0xA):
                continue
            morceaux.append(d)
            if b0 & 0x80:
                return json.loads(b''.join(morceaux).decode('utf-8', 'replace'))

    def appel(self, methode, **params):
        self._n += 1
        ident = self._n
        self._envoyer(json.dumps({'id': ident, 'method': methode, 'params': params}))
        while True:
            m = self._recevoir()
            if m.get('id') == ident:
                if 'error' in m:
                    raise RuntimeError(f'{methode} : {m["error"]}')
                return m.get('result')
            if 'method' in m:
                self.evenements.append(m)

    def evaluer(self, expression, geste=False):
        r = self.appel('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True,
                       userGesture=geste)
        if 'exceptionDetails' in r:
            raise RuntimeError(f'JavaScript : {r["exceptionDetails"]}')
        return r['result'].get('value')

    def touche(self, key, code, vk, modificateurs=0, texte=None):
        """keyDown (rawKeyDown sans texte) puis keyUp. modificateurs : 1 Alt, 2 Ctrl, 4 Meta, 8 Maj."""
        bas = {'type': 'keyDown' if texte else 'rawKeyDown', 'key': key, 'code': code,
               'windowsVirtualKeyCode': vk, 'nativeVirtualKeyCode': vk, 'modifiers': modificateurs}
        if texte:
            bas.update(text=texte, unmodifiedText=texte)
        self.appel('Input.dispatchKeyEvent', **bas)
        self.appel('Input.dispatchKeyEvent', type='keyUp', key=key, code=code, windowsVirtualKeyCode=vk,
                   nativeVirtualKeyCode=vk, modifiers=modificateurs)

    def capture(self, chemin):
        """Capture de la page seule (jamais l'écran), en PNG."""
        png = self.appel('Page.captureScreenshot', format='png')
        with open(chemin, 'wb') as f:
            f.write(base64.b64decode(png['data']))

    def fermer(self):
        try:
            self.s.close()
        except OSError:
            pass


def attendre(condition, delai, pas=0.2):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        try:
            v = condition()
        except (OSError, RuntimeError, ValueError, http.client.HTTPException):
            v = None
        if v:
            return v
        time.sleep(pas)
    return None
