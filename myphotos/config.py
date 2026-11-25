"""Réglages communs : version, port, emplacement des données."""
import os
import sys
from pathlib import Path

VERSION = '0.4.0'
NOM = 'MyPhotos'

# Mutex nommé tenu par MyPhotos tant qu'il tourne : l'installateur (Inno Setup, AppMutex) s'en sert. Ne change jamais
# (même identifiant que l'AppId de l'installateur). Seul l'essai réel de l'installateur (tests/test_construction.py)
# en donne un autre (MYPHOTOS_ESSAI_MUTEX), avec son propre AppId : il ne se mêle jamais d'une vraie installation.
MUTEX = os.environ.get('MYPHOTOS_ESSAI_MUTEX') or 'MyPhotos-25DE0724-F7B4-4956-8238-1E33E16ABFED'

# Clé publique des mises à jour (ECDSA P-256, X puis Y, base64) : MyPhotos n'accepte une mise à jour que si son
# manifeste est signé par la clé privée correspondante, gardée hors du dépôt (construction/cle_signature.py).
CLE_MISE_A_JOUR = 'n3rsWcz5ecQU4/bOtPaedogKBbOm0ct4bOil0vsJL/eUq57UHbzLzXZoEOKcmqL/3QJhJULpNWBoRjub7dCPAA=='


def cle_mise_a_jour():
    import base64
    return base64.b64decode(CLE_MISE_A_JOUR)


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
# Jeton d'accès au serveur local, tiré à chaque lancement (serveur.creer_jeton) : dans les données, privées au compte
# Windows ; une relance le lit pour rouvrir la page. Jamais dans le journal.
JETON = DONNEES / 'jeton'

# Formats lus en version 1 (HEIC et vidéos : plus tard).
EXTENSIONS = {'.jpg', '.jpeg', '.jfif', '.png', '.webp', '.bmp', '.gif', '.tif', '.tiff'}

# Une image plus petite que ça est une icône ou un émoji, pas une photo.
COTE_MIN = 96
