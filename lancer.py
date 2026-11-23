"""Point d'entrée de MyPhotos.exe (PyInstaller). « python lancer.py » fait comme « python -m myphotos »."""
import os
import sys


def _flux_absents():
    """Exécutable sans console : sys.stdout et sys.stderr valent None, et un simple print() planterait."""
    for nom in ('stdout', 'stderr'):
        if getattr(sys, nom) is None:
            setattr(sys, nom, open(os.devnull, 'w', encoding='utf-8'))


if __name__ == '__main__':
    _flux_absents()
    from myphotos.serveur import main
    sys.exit(main())
