"""Banc d'essai : vitesse et fidélité des variantes du modèle, sur un échantillon de photos.

Ne regarde pas les photos : ne compare que des nombres (vecteurs, temps).
    python outils/banc_essai.py <dossier de photos> 96
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np                       # noqa: E402
from PIL import Image, ImageOps          # noqa: E402

from myphotos import config, modele   # noqa: E402

if len(sys.argv) < 2:
    sys.exit('usage : python outils/banc_essai.py <dossier de photos> [nombre]')
dossier = Path(sys.argv[1])
n = int(sys.argv[2]) if len(sys.argv) > 2 else 96

photos = sorted(p for p in dossier.rglob('*') if p.suffix.lower() in config.EXTENSIONS)
photos = photos[::max(1, len(photos) // n)][:n]
print(f'{len(photos)} photos prises dans {dossier}')


def memoire_mo():
    try:
        import ctypes
        import ctypes.wintypes as w

        class PMC(ctypes.Structure):
            _fields_ = [('cb', w.DWORD), ('PageFaultCount', w.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                        ('WorkingSetSize', ctypes.c_size_t), ('a', ctypes.c_size_t), ('b', ctypes.c_size_t),
                        ('c', ctypes.c_size_t), ('d', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                        ('PeakPagefileUsage', ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL('kernel32')
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL('psapi')
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), w.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 2**20
    except Exception:
        return float('nan')


# ---------------------------------------------------------------- décodage
t = time.perf_counter()
plein = [modele.pixels(modele.vers_rvb(ImageOps.exif_transpose(Image.open(p)))) for p in photos]
t_plein = (time.perf_counter() - t) / len(photos) * 1000
t = time.perf_counter()
reduit = [modele.pixels(modele.ouvrir_image(p)) for p in photos]
t_reduit = (time.perf_counter() - t) / len(photos) * 1000
print(f'décodage : complet {t_plein:.1f} ms/photo, réduit (draft) {t_reduit:.1f} ms/photo')
lot_plein = np.stack(plein)
lot_reduit = np.stack(reduit)


def executer(mot, lot, taille=16):
    sortie = []
    mot.vecteurs_images(lot[:2])                       # préchauffage
    t = time.perf_counter()
    for i in range(0, len(lot), taille):
        sortie.append(mot.vecteurs_images(lot[i:i + taille]))
    return np.concatenate(sortie), len(lot) / (time.perf_counter() - t)


def cos(a, b):
    c = np.sum(a * b, axis=1)
    return f'moy {c.mean():.4f} min {c.min():.4f}'


# ---------------------------------------------------------------- vision
ref = None
res = {}
for variante in ('fp32', 'fp16'):
    for materiel, fils in (('processeur', 6), ('processeur', 3), ('carte', None)):
        if materiel == 'processeur' and fils == 3 and variante != 'fp32':
            continue
        try:
            m0 = memoire_mo()
            mot = modele.Moteur('base', vision=variante, materiel=materiel, fils=fils, charger_texte=False)
            v, debit = executer(mot, lot_reduit)
            m1 = memoire_mo()
            if ref is None:
                ref = v
            etiquette = f'{variante} {mot.materiel}' + (f' {fils} fils' if mot.materiel == 'processeur' else '')
            res[etiquette] = v
            print(f'vision {etiquette:<28} {debit:6.1f} photos/s   fidélité/fp32 : {cos(v, ref)}   mémoire +{m1 - m0:.0f} Mo')
            del mot
        except Exception as e:
            print(f'vision {variante} {materiel} : ÉCHEC {type(e).__name__} {str(e)[:200]}')

# effet du décodage réduit sur les vecteurs (fp32 processeur)
mot = modele.Moteur('base', vision='fp32', materiel='processeur', fils=6, charger_texte=False)
v_plein, _ = executer(mot, lot_plein)
print(f'décodage réduit vs complet (fp32) : {cos(ref, v_plein)}')
del mot

# ---------------------------------------------------------------- texte
requetes = ['chien', 'chat', 'plage', 'voiture', 'gâteau d\'anniversaire', 'coucher de soleil', 'neige',
            'montagne', 'fleurs', 'capture d\'écran', 'basket', 'bricolage', 'champignon', 'mer', 'enfant',
            'repas au restaurant', 'texte', 'nuit', 'vélo', 'rose']
txt = {}
for variante in ('fp32', 'fp16'):
    m0 = memoire_mo()
    mot = modele.Moteur('base', texte=variante, charger_vision=False)
    mot.vecteurs_textes(['préchauffage'])
    t = time.perf_counter()
    txt[variante] = np.concatenate([mot.vecteurs_textes([q]) for q in requetes])
    duree = (time.perf_counter() - t) / len(requetes) * 1000
    print(f'texte {variante} : {duree:.0f} ms/requête, mémoire +{memoire_mo() - m0:.0f} Mo')
    del mot
print(f'texte fp16 vs fp32 : {cos(txt["fp16"], txt["fp32"])}')


# ---------------------------------------------------------------- classements : mêmes 10 premières photos ?
def top(vimg, vtxt, k=10):
    return [set(np.argsort(-(vimg @ q))[:k]) for q in vtxt]


base = top(ref, txt['fp32'])
for etiquette, v in res.items():
    for tv in ('fp32', 'fp16'):
        commun = np.mean([len(a & b) / 10 for a, b in zip(base, top(v, txt[tv]))])
        print(f'10 premières communes avec fp32/fp32 : vision {etiquette:<28} texte {tv} : {commun * 100:5.1f} %')
