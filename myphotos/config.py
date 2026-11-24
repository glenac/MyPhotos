"""Réglages communs : version, port, emplacement des données."""
import os
import sys
from pathlib import Path

VERSION = '0.2.0'
NOM = 'MyPhotos'

HOTE = '127.0.0.1'
PORT = int(os.environ.get('MYPHOTOS_PORT') or 7796)

# Exécutable autonome (PyInstaller) ou lancement depuis les sources.
GELE = bool(getattr(sys, 'frozen', False))
CODE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
EXE_DOSSIER = Path(sys.executable).resolve().parent if GELE else CODE

# Données : base, miniatures et modèle. Jamais à côté des photos, qui ne sont que lues.
if os.environ.get('MYPHOTOS_DOSSIER'):
    DONNEES = Path(os.environ['MYPHOTOS_DOSSIER'])
elif GELE:
    DONNEES = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'MyPhotos'
else:
    DONNEES = CODE / 'donnees'

# Modèles : dans les données, sauf réglage contraire (tests : réutiliser un modèle déjà téléchargé).
MODELES = Path(os.environ['MYPHOTOS_MODELES']) if os.environ.get('MYPHOTOS_MODELES') else DONNEES / 'modele'
# Installation sans Internet : un dossier « modele » posé à côté de l'exécutable est recopié au premier lancement.
MODELE_A_COTE = EXE_DOSSIER / 'modele'
BASE = DONNEES / 'index.db'
MINIATURES = DONNEES / 'miniatures.db'
JOURNAL = DONNEES / 'journal.log'

# Formats lus en version 1 (HEIC et vidéos : plus tard).
EXTENSIONS = {'.jpg', '.jpeg', '.jfif', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff'}

# Une image plus petite que ça est une icône ou un émoji, pas une photo.
COTE_MIN = 96
