"""Processus d'essai dans un Job Object (Windows), bibliothèque standard seulement.

Règle des essais : un essai n'arrête QUE les processus qu'il a lancés lui-même.
- Chaque processus est créé SUSPENDU, mis dans un Job Object à l'essai (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), puis
  relancé : lui et tous ses enfants (le vrai Python d'un environnement virtuel, WebView2…) sont dans le job.
- Arrêt normal : /api/quitter, ou WM_CLOSE sur la fenêtre d'essai. Filet : Job.terminer() (TerminateJobObject) ou
  Job.fermer() (KILL_ON_JOB_CLOSE). JAMAIS d'arrêt par identifiant de processus retrouvé après coup, ni par « PID
  parent », ni par « /T » de l'outil d'arrêt de Windows, ni par nom d'image : Windows réutilise les identifiants.
- Les processus du job se listent par le job lui-même (Job.pids()), jamais par un parcours des PID parents.

Règle des MyPhotos d'essai (relecture de la 0.4.2) : un MyPhotos lancé par un essai depuis les sources ne prend jamais
le vrai mutex (celui qu'attend l'installateur : une vraie mise à jour en cours abandonnerait) et ne range jamais le vrai
dossier des mises à jour (%LOCALAPPDATA%\\MyPhotos\\mises-a-jour : son démarrage y efface les installateurs) : isoler()
lui donne les siens, et popen() (comme outils_fenetre.lancer) refuse un environnement de MyPhotos qui ne l'est pas.
"""
import ctypes
import os
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path

CREATE_SUSPENDED = 0x00000004
CREATE_NO_WINDOW = 0x08000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
STILL_ACTIVE = 259

if os.name == 'nt':
    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)

    class _BASIC(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ('a', 'b', 'c', 'd', 'e', 'f')]

    class _ETENDU(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _BASIC), ('IoInfo', _IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ThreadID', wintypes.DWORD),
                    ('th32OwnerProcessID', wintypes.DWORD), ('tpBasePri', ctypes.c_long),
                    ('tpDeltaPri', ctypes.c_long), ('dwFlags', wintypes.DWORD)]

    for _nom, _args, _ret in (
            ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
             wintypes.BOOL),
            ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ('TerminateJobObject', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ('QueryInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            ('TerminateProcess', [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ('ResumeThread', [wintypes.HANDLE], wintypes.DWORD),
            ('OpenThread', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ('CreateToolhelp32Snapshot', [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
            ('Thread32First', [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)], wintypes.BOOL),
            ('Thread32Next', [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)], wintypes.BOOL),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL)):
        _f = getattr(_k32, _nom)
        _f.argtypes, _f.restype = _args, _ret


class Job:
    """Job Object de l'essai : tout processus qu'il lance y entre, avec ses enfants."""

    def __init__(self):
        self.h = _k32.CreateJobObjectW(None, None)
        if not self.h:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ETENDU()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _k32.SetInformationJobObject(self.h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            e = ctypes.get_last_error()
            self.fermer()
            raise ctypes.WinError(e)

    def ajouter(self, hprocessus):
        """Met un processus SUSPENDU dans le job ; en cas d'échec, l'arrête par SON handle et lève l'erreur."""
        if not _k32.AssignProcessToJobObject(self.h, hprocessus):
            e = ctypes.get_last_error()
            _k32.TerminateProcess(hprocessus, 1)
            raise ctypes.WinError(e)

    def pids(self):
        """Identifiants des processus vivants du job (lus par le job, jamais par les PID parents)."""
        if not self.h:
            return []
        n = 1024
        tampon = (ctypes.c_ubyte * (8 + 8 * n))()
        if not _k32.QueryInformationJobObject(self.h, 3, tampon, ctypes.sizeof(tampon), None):
            raise ctypes.WinError(ctypes.get_last_error())
        nb = ctypes.cast(tampon, ctypes.POINTER(wintypes.DWORD))[1]
        liste = ctypes.cast(ctypes.addressof(tampon) + 8, ctypes.POINTER(ctypes.c_size_t))
        return [int(liste[i]) for i in range(nb)]

    def attendre_vide(self, delai):
        fin = time.monotonic() + delai
        while time.monotonic() < fin:
            if not self.pids():
                return True
            time.sleep(0.2)
        return not self.pids()

    def terminer(self):
        """Filet : arrête les processus de CE job, et seulement eux."""
        if self.h:
            _k32.TerminateJobObject(self.h, 1)

    def fermer(self):
        if self.h:
            _k32.CloseHandle(self.h)             # KILL_ON_JOB_CLOSE : rien du job ne survit
            self.h = None


def reprendre(pid):
    """Relance le fil principal d'un processus créé suspendu (son handle est tenu : l'identifiant ne peut pas avoir
    été réutilisé)."""
    h = _k32.CreateToolhelp32Snapshot(0x4, 0)                          # TH32CS_SNAPTHREAD
    if not h or h == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    repris = 0
    try:
        e = _THREADENTRY32()
        e.dwSize = ctypes.sizeof(e)
        ok = _k32.Thread32First(h, ctypes.byref(e))
        while ok:
            if e.th32OwnerProcessID == pid:
                ht = _k32.OpenThread(0x0002, False, e.th32ThreadID)      # THREAD_SUSPEND_RESUME
                if ht:
                    _k32.ResumeThread(ht)
                    _k32.CloseHandle(ht)
                    repris += 1
            ok = _k32.Thread32Next(h, ctypes.byref(e))
    finally:
        _k32.CloseHandle(h)
    if not repris:
        raise OSError(f'processus {pid} : aucun fil à relancer')


def isoler(env, donnees=None):
    """Environnement (dict, modifié et rendu) d'un MyPhotos d'essai : un mutex à lui (MYPHOTOS_ESSAI_MUTEX, nom tiré au
    hasard) et un dossier des mises à jour dans ses données d'essai (MYPHOTOS_ESSAI_MISES_A_JOUR ; données :
    `donnees`, sinon MYPHOTOS_DOSSIER de env)."""
    donnees = Path(donnees or env['MYPHOTOS_DOSSIER'])
    env['MYPHOTOS_ESSAI_MUTEX'] = f'MyPhotos-essai-{uuid.uuid4()}'
    env['MYPHOTOS_ESSAI_MISES_A_JOUR'] = str(donnees / 'mises-a-jour')
    return env


def verifier_isole(env):
    """Refuse (ValueError) l'environnement d'un MyPhotos d'essai (MYPHOTOS_DOSSIER, ou None : celui de l'essai) qui
    n'est pas passé par isoler()."""
    env = os.environ if env is None else env
    if env.get('MYPHOTOS_DOSSIER') and not (env.get('MYPHOTOS_ESSAI_MUTEX') and env.get('MYPHOTOS_ESSAI_MISES_A_JOUR')):
        raise ValueError("MyPhotos d'essai non isolé : il prendrait le vrai mutex et rangerait le vrai dossier des "
                         "mises à jour (processus_essai.isoler)")


def popen(job, arguments, **kw):
    """subprocess.Popen dans le job : créé suspendu, mis dans le job, puis relancé. proc.job = job. L'environnement
    d'un MyPhotos d'essai doit être passé par isoler()."""
    verifier_isole(kw.get('env'))
    kw['creationflags'] = kw.get('creationflags', 0) | CREATE_SUSPENDED
    proc = subprocess.Popen(arguments, **kw)
    try:
        job.ajouter(int(proc._handle))
        reprendre(proc.pid)
    except Exception:
        job.terminer()
        proc.wait(30)
        raise
    proc.job = job
    return proc


def arreter(proc, delai=30):
    """Arrêt d'un processus lancé par popen() : par SON job (TerminateJobObject), puis job fermé."""
    job = getattr(proc, 'job', None)
    if job is None:
        raise ValueError('processus lancé hors d\'un job d\'essai : arrêt refusé')
    if proc.poll() is None:
        job.terminer()
        proc.wait(timeout=delai)
    job.fermer()
