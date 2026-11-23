"""Outil de développement : télécharge les fichiers d'un modèle (toutes ses variantes par défaut).

    python outils/telecharger_tout.py                 -> modèle par défaut, tous ses fichiers
    python outils/telecharger_tout.py base onnx/text_model_fp16.onnx
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from myphotos import modele  # noqa: E402

cle = sys.argv[1] if len(sys.argv) > 1 else modele.MODELE_DEFAUT
noms = sys.argv[2:] or list(modele.info(cle)['fichiers'])
etat = {'p': -1, 't0': time.monotonic()}


def progression(fait, total, nom):
    p = int(fait * 100 / total) if total else 100
    if p >= etat['p'] + 5 or fait == total:
        etat['p'] = p
        debit = fait / max(time.monotonic() - etat['t0'], 0.1) / 1e6
        print(f'{p:3d} %  {fait / 1e6:8.1f} / {total / 1e6:.1f} Mo  {debit:5.1f} Mo/s  {nom}', flush=True)


t = time.monotonic()
modele.telecharger(cle, noms, progression)
print('termine en', round(time.monotonic() - t), 's ; manquants :', modele.manquants(cle, noms))
