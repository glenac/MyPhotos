"""Construit MyPhotos.exe : un seul fichier, sans console, avec PyInstaller.

    construction\\construire.ps1                                  -> dist\\MyPhotos.exe
    construction\\construire.ps1 --essai                          -> dist\\MyPhotos-essai.exe
    .venv-build\\Scripts\\python construction\\construire.py [--essai]       (la même chose, sans PowerShell)

--essai construit le programme d'essai (construction\\essai_exe.py) avec les mêmes réglages et le même
contenu que MyPhotos.exe : il fait un vrai calcul sur la carte graphique, vérifie d'où viennent les DLL et
écrit un journal. Utile après une mise à jour de Python, d'onnxruntime ou de PyInstaller.

Environnement de construction, une seule fois (Python 3.14, Windows 64 bits) :

    python -m venv .venv-build
    .venv-build\\Scripts\\python -m pip install -r requirements-build.txt

Le moteur de reconnaissance n'est jamais embarqué : MyPhotos le télécharge au premier lancement (ou le
recopie depuis un dossier « modele » posé à côté de l'exécutable). Le fichier .spec et la ressource de
version sont générés à chaque fois dans build\\ ; à la fin, le contenu de l'exécutable est contrôlé.
"""
import ast
import re
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ICI = RACINE / 'construction'
BUILD = RACINE / 'build'
DIST = RACINE / 'dist'
ICONE = ICI / 'MyPhotos.ico'
AUTEUR = 'glenac'
COPYRIGHT = 'Copyright (c) 2026 glenac (MIT)'

# Fichiers lus par le code à côté de lui (config.CODE = dossier d'extraction de l'exécutable).
DONNEES = [
    (RACINE / 'page', 'page'),                          # la page unique
    (ICONE, 'construction'),                            # icône de la fenêtre « Choisir un dossier » (tkinter)
]

# Modules jamais importés par MyPhotos, mais que PyInstaller embarquerait en suivant les dépendances.
EXCLUS = [
    # outils d'onnxruntime (conversion, quantification, transformers) : seule l'inférence sert
    'onnxruntime.backend', 'onnxruntime.datasets', 'onnxruntime.quantization', 'onnxruntime.tools',
    'onnxruntime.training', 'onnxruntime.transformers',
    # paquets installés avec onnxruntime et tokenizers, inutiles à l'exécution
    'sympy', 'mpmath', 'google.protobuf', 'flatbuffers', 'packaging', 'huggingface_hub', 'hf_xet', 'fsspec',
    'filelock', 'tqdm', 'yaml', 'httpx2', 'httpcore2', 'anyio', 'h11', 'idna', 'click', 'colorama',
    'truststore', 'typing_extensions',
    # Pillow : AVIF (7,5 Mo, format non lu par MyPhotos), polices, Qt et Tk
    'PIL._avif', 'PIL.AvifImagePlugin', 'PIL._imagingft', 'PIL.ImageQt', 'PIL.ImageTk', 'PIL._imagingtk',
    # bibliothèque standard : aide, tests, outils de développement
    'pydoc', 'pydoc_data', 'test', 'idlelib', 'turtle', 'turtledemo', 'xmlrpc', 'ensurepip', 'venv', 'lib2to3',
    'distutils', 'setuptools', 'pkg_resources', 'pip',
    # gros paquets qui ne doivent jamais arriver là par erreur
    'torch', 'onnx', 'matplotlib', 'scipy', 'pandas', 'IPython', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
]

# Binaires retirés après l'analyse. onnxruntime.dll est l'interface C d'onnxruntime : le module Python
# (onnxruntime_pybind11_state.pyd) contient déjà tout le moteur et ne la charge pas (20 Mo de gagnés).
RETIRES = {'onnxruntime.dll'}

# Ce qui doit être dans l'exécutable : sans eux, pas de carte graphique, pas de page ou pas de recherche.
OBLIGATOIRES = [
    'onnxruntime\\capi\\onnxruntime_pybind11_state.pyd',
    'onnxruntime\\capi\\DirectML.dll',                  # carte graphique (DirectML), chargée à la demande
    'onnxruntime\\capi\\onnxruntime_providers_shared.dll',
    'tokenizers\\tokenizers.pyd',
    'PIL\\_imaging.cp314-win_amd64.pyd',
    'PIL\\_webp.cp314-win_amd64.pyd',
    '_tkinter.pyd', 'tcl90.dll', 'tcl9tk90.dll',
    '_ssl.pyd', 'libssl-3.dll', 'libcrypto-3.dll',      # téléchargement du moteur en HTTPS
    '_sqlite3.pyd', 'sqlite3.dll',
    'page\\index.html',
    'construction\\MyPhotos.ico',
]

SPEC = '''\
# Généré par construction/construire.py à chaque construction : ne pas modifier à la main.
import os

a = Analysis(
    [{entree!r}],
    pathex=[{racine!r}],
    datas={datas!r},
    hiddenimports={caches!r},
    excludes={exclus!r},
    noarchive=False,
    optimize=0,
)
a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in {retires!r}]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name={nom!r},
    console=False,
    upx=False,
    strip=False,
    debug=False,
    icon=[{icone!r}],
    version={version!r},
)
'''

VERSION_RC = '''\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nombres}, prodvers={nombres}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', {auteur!r}),
      StringStruct('FileDescription', 'MyPhotos'),
      StringStruct('FileVersion', {version!r}),
      StringStruct('InternalName', {nom!r}),
      StringStruct('LegalCopyright', {copyright!r}),
      StringStruct('OriginalFilename', {nom_exe!r}),
      StringStruct('ProductName', 'MyPhotos'),
      StringStruct('ProductVersion', {version!r})])]),
    VarFileInfo([VarStruct('Translation', [0x040C, 1200])])
  ]
)
'''


def version_myphotos():
    """VERSION de myphotos/config.py, lue sans importer le paquet."""
    arbre = ast.parse((RACINE / 'myphotos' / 'config.py').read_text(encoding='utf-8'))
    for noeud in arbre.body:
        if isinstance(noeud, ast.Assign) and any(getattr(c, 'id', None) == 'VERSION' for c in noeud.targets):
            return ast.literal_eval(noeud.value)
    raise SystemExit('VERSION introuvable dans myphotos/config.py')


def nombres_version(version):
    """'0.1.0' -> (0, 1, 0, 0) ; un suffixe (« 0.2.0b1 ») est ignoré."""
    n = [int(x) for x in re.findall(r'\d+', version.split('-')[0])[:4]]
    return tuple(n + [0] * (4 - len(n)))


def verifier_environnement():
    if sys.platform != 'win32' or sys.maxsize < 2 ** 32:
        raise SystemExit('MyPhotos.exe se construit sous Windows, avec un Python 64 bits.')
    if sys.version_info[:2] != (3, 14):
        print(f'Attention : Python {sys.version.split()[0]} (MyPhotos est essayé avec Python 3.14).')
    try:
        import PyInstaller
    except ImportError:
        raise SystemExit('PyInstaller est absent. Préparez l\'environnement de construction :\n'
                         '    python -m venv .venv-build\n'
                         '    .venv-build\\Scripts\\python -m pip install -r requirements-build.txt')
    if not ICONE.is_file():
        raise SystemExit(f'{ICONE} est absente : lancez d\'abord construction\\icone.py')
    return PyInstaller.__version__


def construire(essai=False):
    version_pyi = verifier_environnement()
    version = version_myphotos()
    nom = 'MyPhotos-essai' if essai else 'MyPhotos'
    entree = ICI / 'essai_exe.py' if essai else RACINE / 'lancer.py'
    travail = BUILD / nom
    travail.mkdir(parents=True, exist_ok=True)
    print(f'{nom}.exe {version} : PyInstaller {version_pyi}, Python {sys.version.split()[0]}')

    rc = travail / 'version.txt'
    rc.write_text(VERSION_RC.format(nombres=nombres_version(version), version=version, nom=nom,
                                    nom_exe=f'{nom}.exe', auteur=AUTEUR, copyright=COPYRIGHT), encoding='utf-8')
    spec = travail / f'{nom}.spec'
    spec.write_text(SPEC.format(entree=str(entree), racine=str(RACINE), nom=nom, icone=str(ICONE),
                                version=str(rc), datas=[(str(s), d) for s, d in DONNEES],
                                # l'essai importe ses modules par leur nom : on lui donne tout le paquet myphotos
                                caches=['myphotos.serveur'] if essai else [],
                                exclus=EXCLUS, retires=RETIRES), encoding='utf-8')

    debut = time.monotonic()
    journal = travail / 'pyinstaller.log'
    with open(journal, 'w', encoding='utf-8') as sortie:
        r = subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
                            '--distpath', str(DIST), '--workpath', str(travail), str(spec)],
                           cwd=RACINE, stdout=sortie, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(journal.read_text(encoding='utf-8', errors='replace')[-4000:])
        raise SystemExit(f'Échec de PyInstaller (code {r.returncode}) : voir {journal}')
    exe = DIST / f'{nom}.exe'
    print(f'construit en {time.monotonic() - debut:.0f} s : {exe}')
    controler(exe, version)
    return exe


def controler(exe, version):
    """Relit l'exécutable : fichiers indispensables présents, onnxruntime.dll absente, ressource de version."""
    from PyInstaller.archive.readers import CArchiveReader
    toc = CArchiveReader(str(exe)).toc
    noms = {n.replace('/', '\\').lower() for n in toc}
    manquants = [n for n in OBLIGATOIRES if n.lower() not in noms]
    en_trop = sorted(n for n in noms if n.rsplit('\\', 1)[-1] in RETIRES)
    if manquants or en_trop:
        raise SystemExit(f'Contenu inattendu : manquants {manquants}, en trop {en_trop}')
    infos = lire_version(exe)
    attendu = {'FileDescription': 'MyPhotos', 'ProductName': 'MyPhotos', 'FileVersion': version,
               'ProductVersion': version, 'LegalCopyright': COPYRIGHT}
    ecarts = {k: infos.get(k) for k, v in attendu.items() if infos.get(k) != v}
    if ecarts:
        raise SystemExit(f'Ressource de version inattendue : {ecarts}')
    taille = exe.stat().st_size
    decompresse = sum(e[2] for e in toc.values())
    print(f'contenu vérifié : {len(toc)} éléments, dont DirectML.dll et onnxruntime_providers_shared.dll')
    print(f'version : {infos.get("FileDescription")} {infos.get("FileVersion")}, {infos.get("LegalCopyright")}')
    octets = f'{taille:,}'.replace(',', ' ')
    print(f'taille : {taille / 1e6:.1f} Mo ({octets} octets), {decompresse / 1e6:.0f} Mo une fois extrait')


def lire_version(exe):
    import pefile
    pe = pefile.PE(str(exe), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
    infos = {}
    for fi in getattr(pe, 'FileInfo', None) or []:
        for bloc in fi:
            for table in getattr(bloc, 'StringTable', []):
                infos.update({k.decode(): v.decode() for k, v in table.entries.items()})
    pe.close()
    return infos


if __name__ == '__main__':
    construire(essai='--essai' in sys.argv[1:])
