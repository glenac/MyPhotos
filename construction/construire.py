"""Construit MyPhotos.exe : un seul fichier, sans console, avec PyInstaller.

    construction\\construire.ps1                                  -> dist\\MyPhotos.exe
    construction\\construire.ps1 --essai                          -> dist\\MyPhotos-essai.exe
    construction\\construire.ps1 --sortie <dossier> [--essai]     -> <dossier>\\MyPhotos.exe (hors de dist\\)
    construction\\construire.ps1 --dossier                        -> dist\\MyPhotos\\MyPhotos.exe + _internal\\
    construction\\construire.ps1 --dossier --sortie <dossier>     -> <dossier>\\MyPhotos\\ (hors de dist\\)
    construction\\construire.ps1 --paquet [--cache <dossier>]     -> dist\\MyPhotos-<version>-windows.zip
    construction\\construire.ps1 --installateur [--cache <dossier>] [--iscc <ISCC.exe>] [--sortie <dossier>]
                                                                  -> dist\\MyPhotos-<version>-installation.exe
    construction\\construire.ps1 --manifeste [--sortie <dossier>]  -> dist\\MyPhotos-mise-a-jour.json (signé)
    .venv-build\\Scripts\\python construction\\construire.py [...]          (la même chose, sans PowerShell)

--essai construit le programme d'essai (construction\\essai_exe.py) avec les mêmes réglages et le même
contenu que MyPhotos.exe : il fait un vrai calcul sur la carte graphique, vérifie d'où viennent les DLL et
écrit un journal. Utile après une mise à jour de Python, d'onnxruntime ou de PyInstaller.

--sortie construit dans un autre dossier (exécutable et fichiers de travail), sans toucher à dist\\ ni à build\\ :
pour essayer une construction sans déranger celle qui sera publiée. Avec --installateur, --sortie désigne le dossier
qui tient la version dossier déjà construite (<dossier>\\MyPhotos\\) : licences et installateur y sont écrits.

--dossier construit la version « dossier » de PyInstaller (onedir), celle que pose l'installateur : le même
contenu que MyPhotos.exe, mais déjà décompressé (dist\\MyPhotos\\MyPhotos.exe et ses fichiers dans _internal\\).
Elle démarre sans extraction dans %TEMP% et attire moins de fausses alertes d'antivirus. À la racine de
dist\\MyPhotos\\, seulement MyPhotos.exe et _internal\\ : c'est contrôlé (controler_dossier).

--installateur ne reconstruit rien : à partir du dist\\MyPhotos\\ déjà construit (et essayé), il rassemble les
licences comme --paquet (plus celle d'Inno Setup, dans licences\\InnoSetup\\), puis compile construction\\MyPhotos.iss avec Inno Setup 6 (ISCC.exe : --iscc, sinon
%MYPHOTOS_ISCC%, sinon les emplacements habituels), contrôle la ressource de version de l'installateur et écrit
dist\\MyPhotos-<version>-installation.exe et son empreinte SHA-256.

--manifeste (après --installateur, depuis la 0.4.0) écrit dist\\MyPhotos-mise-a-jour.json : le manifeste de la
mise à jour (version, taille et empreinte de l'installateur, nouveautés de construction\\nouveautes.json), signé
avec la clé privée du projet (construction\\cle_signature.py, jamais dans le dépôt), puis relu avec les règles de
MyPhotos et la clé publique qu'il contient. Ce fichier est publié avec chaque version : c'est lui que les MyPhotos
installés lisent pour proposer la mise à jour (myphotos/mise_a_jour.py).

La fenêtre de MyPhotos (myphotos/fenetre.py) : pywebview et pythonnet, avec leurs DLL et assemblies .NET
(Microsoft.Web.WebView2.Core et .WinForms, WebView2Loader.dll pour x64, arm64 et x86, Python.Runtime.dll,
ClrLoader.dll), réunis par les hooks de pyinstaller-hooks-contrib (webview, clr, clr_loader) et celui de
pywebview. Les trois WebView2Loader.dll restent : pywebview ajoute leurs trois dossiers au PATH en s'important
(sans eux, l'exécutable s'arrête au démarrage). Depuis la 0.4.0, tkinter et Tcl/Tk ne sont plus embarqués : la
fenêtre « Choisir un dossier » des replis (Edge, navigateur) est celle de Windows, par COM (myphotos/dialogue.py).

--paquet ne reconstruit rien : il prépare la publication du dist\\MyPhotos.exe déjà construit (et essayé).
Il rassemble les textes de licence que demande THIRD_PARTY_NOTICES.md (section « Ce que chaque publication
doit contenir ») dans dist\\licences\\, puis écrit l'archive publiée et les empreintes SHA-256 :
dist\\MyPhotos-<version>-windows.zip (MyPhotos.exe, LICENSE, THIRD_PARTY_NOTICES.md, le mode d'emploi en PDF
et licences\\), dist\\MyPhotos-<version>-windows.zip.sha256 et dist\\MyPhotos.exe.sha256. Les fichiers absents
de l'environnement de construction (licences de DirectML et de WebView2, dans leurs paquets NuGet officiels ;
licences de tokenizers et de proxy_tools ; notices de quelques bibliothèques Rust) sont téléchargés une fois, contrôlés par leur empreinte, et gardés dans
un cache hors du dépôt : --cache, sinon %MYPHOTOS_CACHE_CONSTRUCTION%, sinon
%LOCALAPPDATA%\\MyPhotos-construction\\cache.

Environnement de construction, une seule fois (Python 3.14, Windows 64 bits) :

    python -m venv .venv-build
    .venv-build\\Scripts\\python -m pip install -r requirements-build.txt

Le moteur de reconnaissance n'est jamais embarqué : MyPhotos le télécharge au premier lancement (ou le
recopie depuis un dossier « modele » posé à côté de l'exécutable). Le fichier .spec et la ressource de
version sont générés à chaque fois dans build\\ ; à la fin, le contenu de l'exécutable est contrôlé.
"""
import ast
import base64
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
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
    (ICONE, 'construction'),                            # icône de la fenêtre MyPhotos (et de « Choisir un dossier »)
    (RACINE / 'myphotos' / 'lieux.bin', 'myphotos'),    # lieux (0.5.0) : GeoNames, préparé par outils/preparer_lieux.py
]

# Fenêtre de MyPhotos : modules que pywebview choisit à l'exécution (guilib), et pythonnet.
CACHES = ['webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr']

# Modules jamais importés par MyPhotos, mais que PyInstaller embarquerait en suivant les dépendances.
EXCLUS = [
    # outils d'onnxruntime (conversion, quantification, transformers) : seule l'inférence sert
    'onnxruntime.backend', 'onnxruntime.datasets', 'onnxruntime.quantization', 'onnxruntime.tools',
    'onnxruntime.training', 'onnxruntime.transformers',
    # paquets installés avec onnxruntime et tokenizers, inutiles à l'exécution (typing_extensions, lui, sert à
    # pywebview)
    'sympy', 'mpmath', 'google.protobuf', 'flatbuffers', 'packaging', 'huggingface_hub', 'hf_xet', 'fsspec',
    'filelock', 'tqdm', 'yaml', 'httpx2', 'httpcore2', 'anyio', 'h11', 'idna', 'click', 'colorama',
    'truststore',
    # pywebview : moteurs des autres systèmes (macOS, Linux, Qt, CEF, Android), jamais chargés sous Windows avec
    # WebView2, et leurs bibliothèques
    'webview.platforms.cocoa', 'webview.platforms.gtk', 'webview.platforms.qt', 'webview.platforms.cef',
    'webview.platforms.android', 'qtpy', 'gi', 'cefpython3', 'objc', 'AppKit', 'Foundation', 'WebKit', 'jnius',
    'cryptography',
    # cffi : sa partie « compilation » (distutils, setuptools), jamais utilisée par clr_loader (mode ABI) ; sans
    # cette exclusion, PyInstaller échoue (« distutils already imported as ExcludedModule »)
    'cffi._shimmed_dist_utils', 'cffi.setuptools_ext', 'cffi.verifier', 'cffi.recompiler', 'cffi.ffiplatform',
    'cffi.pkgconfig', 'cffi.vengine_cpy', 'cffi.vengine_gen',
    # Pillow : AVIF (7,5 Mo, format non lu par MyPhotos), polices, Qt et Tk
    'PIL._avif', 'PIL.AvifImagePlugin', 'PIL._imagingft', 'PIL.ImageQt', 'PIL.ImageTk', 'PIL._imagingtk',
    # tkinter, Tcl/Tk 9 et libtommath (0.4.0) : « Choisir un dossier » passe par la fenêtre de Windows (COM)
    'tkinter', '_tkinter',
    # bibliothèque standard : aide, tests, outils de développement
    'pydoc', 'pydoc_data', 'test', 'idlelib', 'turtle', 'turtledemo', 'xmlrpc', 'ensurepip', 'venv', 'lib2to3',
    'distutils', 'setuptools', 'pkg_resources', 'pip',
    # gros paquets qui ne doivent jamais arriver là par erreur
    'torch', 'onnx', 'matplotlib', 'scipy', 'pandas', 'IPython', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
]

# Fichiers retirés après l'analyse (binaires et données), par leur nom. onnxruntime.dll est l'interface C
# d'onnxruntime : le module Python (onnxruntime_pybind11_state.pyd) contient déjà tout le moteur et ne la charge pas
# (20 Mo de gagnés). WebBrowserInterop.x86.dll (MSHTML, 32 bits) et pywebview-android.jar ne servent jamais ici.
RETIRES = {'onnxruntime.dll', 'webbrowserinterop.x86.dll', 'pywebview-android.jar',
           # Tcl/Tk (0.4.0) : jamais embarqués, même si une dépendance les tirait encore (contrôlé à la fin)
           '_tkinter.pyd', 'tcl90.dll', 'tcl9tk90.dll', 'libtommath.dll'}
# … et par leur dossier : la version 32 bits de ClrLoader.dll.
RETIRES_DOSSIERS = ['clr_loader\\ffi\\dlls\\x86\\']

# Ce qui doit être dans l'exécutable : sans eux, pas de carte graphique, pas de fenêtre, pas de page ou pas de
# recherche.
OBLIGATOIRES = [
    'onnxruntime\\capi\\onnxruntime_pybind11_state.pyd',
    'onnxruntime\\capi\\DirectML.dll',                  # carte graphique (DirectML), chargée à la demande
    'onnxruntime\\capi\\onnxruntime_providers_shared.dll',
    'tokenizers\\tokenizers.pyd',
    'PIL\\_imaging.cp314-win_amd64.pyd',
    'PIL\\_webp.cp314-win_amd64.pyd',
    '_ssl.pyd', 'libssl-3.dll', 'libcrypto-3.dll',      # téléchargement du moteur en HTTPS
    '_sqlite3.pyd', 'sqlite3.dll',
    # fenêtre de MyPhotos : WebView2 (assemblies .NET et chargeur), pythonnet, clr_loader, cffi
    'webview\\lib\\Microsoft.Web.WebView2.Core.dll',
    'webview\\lib\\Microsoft.Web.WebView2.WinForms.dll',
    'webview\\lib\\runtimes\\win-x64\\native\\WebView2Loader.dll',
    'webview\\lib\\runtimes\\win-arm64\\native\\WebView2Loader.dll',
    'webview\\lib\\runtimes\\win-x86\\native\\WebView2Loader.dll',
    'pythonnet\\runtime\\Python.Runtime.dll',
    'clr_loader\\ffi\\dlls\\amd64\\ClrLoader.dll',
    '_cffi_backend.cp314-win_amd64.pyd',
    'page\\index.html',
    'construction\\MyPhotos.ico',
    'myphotos\\lieux.bin',                              # lieux des photos (0.5.0), sans Internet
]


def retire(nom):
    """Vrai si ce fichier de l'analyse (nom dans l'exécutable) est retiré : voir RETIRES et RETIRES_DOSSIERS."""
    n = nom.replace('/', '\\').lower()
    return n.rsplit('\\', 1)[-1] in RETIRES or any(n.startswith(d) for d in RETIRES_DOSSIERS)

SPEC_ANALYSE = '''\
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
RETIRES = {retires!r}
RETIRES_DOSSIERS = {dossiers!r}


def garder(element):
    n = element[0].replace('/', '\\\\').lower()
    return n.rsplit('\\\\', 1)[-1] not in RETIRES and not any(n.startswith(d) for d in RETIRES_DOSSIERS)


a.binaries = [b for b in a.binaries if garder(b)]
a.datas = [d for d in a.datas if garder(d)]
pyz = PYZ(a.pure)
'''

# Un seul fichier : tout est dans MyPhotos.exe, extrait dans %TEMP%\\_MEI… à chaque lancement.
SPEC = SPEC_ANALYSE + '''\
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

# Version « dossier » (onedir), posée par l'installateur : même analyse, mais MyPhotos.exe ne contient que le chargeur
# et le code Python ; les DLL, les modules compilés et les données sont posés à côté, dans _internal\\
# (sys._MEIPASS = config.CODE).
INTERNE = '_internal'
SPEC_DOSSIER = SPEC_ANALYSE + '''\
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name={nom!r},
    console=False,
    upx=False,
    strip=False,
    debug=False,
    icon=[{icone!r}],
    version={version!r},
    contents_directory=''' + repr(INTERNE) + ''',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name={nom!r},
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


def construire(essai=False, sortie=None, dossier=False):
    """Construit l'exécutable dans dist\\ (fichiers de travail dans build\\), ou entièrement dans `sortie`.
    dossier=True : version « dossier » (<dist>\\MyPhotos\\MyPhotos.exe + _internal\\)."""
    version_pyi = verifier_environnement()
    version = version_myphotos()
    nom = 'MyPhotos-essai' if essai else 'MyPhotos'
    entree = ICI / 'essai_exe.py' if essai else RACINE / 'lancer.py'
    dist = Path(sortie).resolve() if sortie else DIST
    travail = (dist / 'build' if sortie else BUILD) / (f'{nom}-dossier' if dossier else nom)   # un .spec chacun
    travail.mkdir(parents=True, exist_ok=True)
    print(f'{nom}.exe {version}{" (version dossier)" if dossier else ""} : PyInstaller {version_pyi}, '
          f'Python {sys.version.split()[0]}')

    rc = travail / 'version.txt'
    rc.write_text(VERSION_RC.format(nombres=nombres_version(version), version=version, nom=nom,
                                    nom_exe=f'{nom}.exe', auteur=AUTEUR, copyright=COPYRIGHT), encoding='utf-8')
    spec = travail / f'{nom}.spec'
    spec.write_text((SPEC_DOSSIER if dossier else SPEC).format(entree=str(entree), racine=str(RACINE), nom=nom, icone=str(ICONE),
                                version=str(rc), datas=[(str(s), d) for s, d in DONNEES],
                                # l'essai importe ses modules par leur nom : on lui donne tout le paquet myphotos
                                caches=(['myphotos.serveur'] if essai else []) + CACHES,
                                exclus=EXCLUS, retires=sorted(RETIRES), dossiers=RETIRES_DOSSIERS),
                    encoding='utf-8')

    debut = time.monotonic()
    journal = travail / 'pyinstaller.log'
    with open(journal, 'w', encoding='utf-8') as sortie_pyi:
        r = subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
                            '--distpath', str(dist), '--workpath', str(travail), str(spec)],
                           cwd=RACINE, stdout=sortie_pyi, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print(journal.read_text(encoding='utf-8', errors='replace')[-4000:])
        raise SystemExit(f'Échec de PyInstaller (code {r.returncode}) : voir {journal}')
    if dossier:
        exe = dist / nom / f'{nom}.exe'
        print(f'construit en {time.monotonic() - debut:.0f} s : {exe}')
        controler_dossier(exe.parent, version, nom)
        return exe
    exe = dist / f'{nom}.exe'
    print(f'construit en {time.monotonic() - debut:.0f} s : {exe}')
    controler(exe, version)
    return exe


def controler(exe, version):
    """Relit l'exécutable : fichiers indispensables présents, onnxruntime.dll absente, ressource de version."""
    from PyInstaller.archive.readers import CArchiveReader
    toc = CArchiveReader(str(exe)).toc
    noms = {n.replace('/', '\\').lower() for n in toc}
    manquants = [n for n in OBLIGATOIRES if n.lower() not in noms]
    en_trop = sorted(n for n in noms if retire(n))
    if manquants or en_trop:
        raise SystemExit(f'Contenu inattendu : manquants {manquants}, en trop {en_trop}')
    infos = _controler_version(exe, version)
    taille = exe.stat().st_size
    decompresse = sum(e[2] for e in toc.values())
    print(f'contenu vérifié : {len(toc)} éléments, dont DirectML.dll et onnxruntime_providers_shared.dll')
    print(f'version : {infos.get("FileDescription")} {infos.get("FileVersion")}, {infos.get("LegalCopyright")}')
    octets = f'{taille:,}'.replace(',', ' ')
    print(f'taille : {taille / 1e6:.1f} Mo ({octets} octets), {decompresse / 1e6:.0f} Mo une fois extrait')


def _controler_version(exe, version):
    """Ressource de version de l'exécutable : celle de MyPhotos {version}. Rend ses textes."""
    infos = lire_version(exe)
    attendu = {'FileDescription': 'MyPhotos', 'ProductName': 'MyPhotos', 'FileVersion': version,
               'ProductVersion': version, 'LegalCopyright': COPYRIGHT}
    ecarts = {k: infos.get(k) for k, v in attendu.items() if infos.get(k) != v}
    if ecarts:
        raise SystemExit(f'Ressource de version inattendue : {ecarts}')
    return infos


def controler_dossier(dossier, version, nom='MyPhotos'):
    """Relit la version « dossier » : à la racine, seulement <nom>.exe et _internal\\ ; fichiers indispensables
    présents, fichiers retirés absents (onnxruntime.dll…) ; ressource de version de l'exécutable. Rend la taille."""
    dossier = Path(dossier)
    exe = dossier / f'{nom}.exe'
    interne = dossier / INTERNE
    if not exe.is_file() or not interne.is_dir():
        raise SystemExit(f'{dossier} : {nom}.exe ou {INTERNE}\\ absent')
    racine = sorted(p.name for p in dossier.iterdir())
    if racine != sorted([f'{nom}.exe', INTERNE]):
        raise SystemExit(f'{dossier} : contenu inattendu à la racine : {racine}')
    fichiers = [p for p in interne.rglob('*') if p.is_file()]
    noms = {p.relative_to(interne).as_posix().replace('/', '\\').lower() for p in fichiers}
    manquants = [n for n in OBLIGATOIRES if n.lower() not in noms]
    en_trop = sorted(n for n in noms if retire(n))
    if manquants or en_trop:
        raise SystemExit(f'Contenu inattendu : manquants {manquants}, en trop {en_trop}')
    infos = _controler_version(exe, version)
    taille = exe.stat().st_size + sum(p.stat().st_size for p in fichiers)
    print(f'contenu vérifié : {nom}.exe ({exe.stat().st_size / 1e6:.1f} Mo) et {len(fichiers)} fichiers dans '
          f'{INTERNE}\\, dont DirectML.dll, WebView2Loader.dll et Python.Runtime.dll')
    print(f'version : {infos.get("FileDescription")} {infos.get("FileVersion")}, {infos.get("LegalCopyright")}')
    print(f'taille : {taille / 1e6:.1f} Mo ({len(fichiers) + 1} fichiers)')
    return taille


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


# ---------------------------------------------------------------- paquet de publication (--paquet)

MODE_EMPLOI = RACINE / 'docs' / 'MyPhotos_mode_emploi.pdf'
AGENT_HTTP = 'MyPhotos/1 (construction)'
MARQUE = '.cree-par-construire'          # dist\licences n'est refait que s'il porte cette marque (jamais dans le zip)

# DirectML.dll est livrée dans onnxruntime-directml, mais sa licence n'est que dans le paquet NuGet officiel.
DIRECTML = {
    'version': '1.15.4',
    'url': 'https://www.nuget.org/api/v2/package/Microsoft.AI.DirectML/1.15.4',
    'fichier': 'Microsoft.AI.DirectML.1.15.4.nupkg',
    # Empreinte publiée par nuget.org (« packageHash » du catalogue NuGet : SHA-512 en base64).
    'sha512': '/edn9WkEq8kP1T9l2HKckYq39uPF4ezdR5kI/AK0U1zysIYPerKsubcx1suAm3LD1dTQKFP7j16gIqR7xE7yhQ==',
    'dll': 'bin/x64-win/DirectML.dll',
    'textes': ('LICENSE.txt', 'ThirdPartyNotices.txt'),
}

# La roue de tokenizers ne contient aucun texte de licence : LICENSE du dépôt, au commit de l'étiquette v0.23.3.
TOKENIZERS = {
    'version': '0.23.3',
    'url': 'https://raw.githubusercontent.com/huggingface/tokenizers/'
           '301277072263fb3830eb66eec7e47adadf76f1dc/LICENSE',
    'fichier': 'tokenizers-0.23.3-LICENSE',
    'sha256': 'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4',
}
# Bibliothèques Rust de tokenizers (inventaire « sboms » de la roue) : celles dont la licence n'offre ni Apache-2.0
# (texte joint), ni Unlicense, ni BSL-1.0, ou qui ajoutent une licence (« AND »), ont leurs propres mentions de droits
# d'auteur : leurs fichiers de licence sont pris dans le paquet publié sur crates.io, contrôlé par l'empreinte SHA-256
# que donne l'inventaire.
CRATES_URL = 'https://static.crates.io/crates/{nom}/{nom}-{version}.crate'

# Fenêtre de MyPhotos. Les DLL WebView2 livrées par pywebview (webview\lib) sont celles du paquet NuGet officiel
# Microsoft.Web.WebView2, dont la licence n'est que dans ce paquet : elles lui sont comparées octet pour octet avant
# d'en prendre les textes.
WEBVIEW2 = {
    'version': '1.0.3856.49',
    'url': 'https://www.nuget.org/api/v2/package/Microsoft.Web.WebView2/1.0.3856.49',
    'fichier': 'Microsoft.Web.WebView2.1.0.3856.49.nupkg',
    # « packageHash » du catalogue NuGet (catalog0/data/2026.03.16.04.51.21/microsoft.web.webview2.1.0.3856.49.json).
    'sha512': '3b8jtjfw2idN3BCur90m8FV1y5hWl8Va3K0IfArcCzQAD35d8LFTtM33wTeAinW/DZB/JGnoLg4vbX22oRfU+w==',
    'dll': {                            # chemin sous webview\lib de pywebview -> chemin dans le paquet NuGet
        'Microsoft.Web.WebView2.Core.dll': 'lib/net462/Microsoft.Web.WebView2.Core.dll',
        'Microsoft.Web.WebView2.WinForms.dll': 'lib/net462/Microsoft.Web.WebView2.WinForms.dll',
        'runtimes/win-x64/native/WebView2Loader.dll': 'runtimes/win-x64/native/WebView2Loader.dll',
        'runtimes/win-arm64/native/WebView2Loader.dll': 'runtimes/win-arm64/native/WebView2Loader.dll',
        'runtimes/win-x86/native/WebView2Loader.dll': 'runtimes/win-x86/native/WebView2Loader.dll',
    },
    'textes': ('LICENSE.txt', 'NOTICE.txt'),
}
# proxy_tools (tiré par pywebview) n'est publié qu'en sources, sans fichier de licence : LICENSE.txt de son dépôt, au
# dernier commit (06/05/2014). PyPI indique MIT, ce fichier une licence BSD : THIRD_PARTY_NOTICES.md cite les deux.
PROXY_TOOLS = {
    'version': '0.1.0',
    'url': 'https://raw.githubusercontent.com/jtushman/proxy_tools/db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6/LICENSE.txt',
    'fichier': 'proxy_tools-0.1.0-LICENSE.txt',
    'sha256': 'a428fb8a2e762af3eb0a6edbbb88e9b42ccfee80fd9b423958bcacf9b9abbfe4',
}
# Paquets Python de la fenêtre dont les métadonnées contiennent les textes de licence (<dist-info>\licenses\),
# avec le sous-dossier de dist\licences qui les reçoit.
LICENCES_FENETRE = [('pywebview', 'pywebview'), ('pythonnet', 'pythonnet'), ('clr_loader', 'clr_loader'),
                    ('cffi', 'cffi'), ('pycparser', 'pycparser'), ('bottle', 'bottle'),
                    ('typing_extensions', 'typing_extensions')]
# license.txt d'Inno Setup 6.7.4 (dossier d'installation ; identique à https://jrsoftware.org/files/is/license.txt le
# 24/11/2026). Joint aux licences de l'installateur, qui pose aussi le désinstallateur unins000.exe à côté de MyPhotos.
INNO_LICENCE_SHA256 = '2e5346868c2a18434489824e11d65c3031620f792fefc415d05f19cd441abf5c'
LICENCES_SUFFISANTES = {'Apache-2.0', 'Apache-2.0 WITH LLVM-exception', 'Unlicense', 'BSL-1.0'}
RE_FICHIER_LICENCE = re.compile(r'(?i)(licen[cs]e|copying|copyright|notice|unlicense|authors)([-._][A-Za-z0-9._-]*)?')

# Composants dont les versions doivent être celles de requirements*.txt ET celles citées dans THIRD_PARTY_NOTICES.md,
# y compris, depuis la 0.3.0, ceux de la fenêtre (pywebview et ses dépendances ; WebView2 : voir WEBVIEW2).
PAQUETS_LICENCES = ['onnxruntime-directml', 'numpy', 'pillow', 'tokenizers', 'pyinstaller',
                    'pywebview', 'pythonnet', 'clr_loader', 'cffi', 'pycparser', 'proxy_tools', 'bottle',
                    'typing_extensions']


def _cle_paquet(nom):
    return re.sub(r'[-_.]+', '-', nom).lower()


def dossier_cache(choisi=None):
    """Cache des téléchargements de --paquet, hors du dépôt."""
    if choisi:
        return Path(choisi).resolve()
    if os.environ.get('MYPHOTOS_CACHE_CONSTRUCTION'):
        return Path(os.environ['MYPHOTOS_CACHE_CONSTRUCTION']).resolve()
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'MyPhotos-construction' / 'cache'


def empreinte(chemin, algo='sha256'):
    h = hashlib.new(algo)
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h


def depuis_cache(cache, nom, url, algo, attendu):
    """Le fichier `nom` du cache, téléchargé depuis `url` s'il manque ; son empreinte doit valoir `attendu`
    (hexadécimal, ou base64 pour un SHA-512 de NuGet). Un fichier du cache abîmé est téléchargé de nouveau."""
    def bon(p):
        h = empreinte(p, algo)
        return attendu in (h.hexdigest(), base64.b64encode(h.digest()).decode('ascii'))

    chemin = cache / nom
    if chemin.is_file() and bon(chemin):
        return chemin
    chemin.parent.mkdir(parents=True, exist_ok=True)
    partiel = chemin.with_name(chemin.name + '.part')
    print(f'téléchargement : {url}')
    requete = urllib.request.Request(url, headers={'User-Agent': AGENT_HTTP})
    with urllib.request.urlopen(requete, timeout=120) as r, open(partiel, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)
    if not bon(partiel):
        raise SystemExit(f'{url} : empreinte {algo} inattendue. Fichier gardé pour examen : {partiel}')
    os.replace(partiel, chemin)
    return chemin


def verifier_inventaire():
    """Les versions installées sont celles de requirements*.txt, et THIRD_PARTY_NOTICES.md les cite : sinon
    l'inventaire des licences est à refaire avant de publier."""
    figees = {}
    for nom in ('requirements.txt', 'requirements-build.txt'):
        for ligne in (RACINE / nom).read_text(encoding='utf-8').splitlines():
            m = re.match(r'\s*([A-Za-z0-9_.-]+)\s*==\s*([^\s#;]+)', ligne)
            if m:
                figees[_cle_paquet(m.group(1))] = m.group(2)
    notices = (RACINE / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    ecarts, versions = [], {}
    for nom in PAQUETS_LICENCES:
        v = versions[nom] = importlib.metadata.version(nom)
        if figees.get(_cle_paquet(nom)) != v:
            ecarts.append(f'{nom} {v} installé, {figees.get(_cle_paquet(nom))} dans requirements')
        if v not in notices:
            ecarts.append(f'{nom} {v} : version absente de THIRD_PARTY_NOTICES.md')
    versions['python'] = '.'.join(map(str, sys.version_info[:3]))
    for nom, v in (('Python', versions['python']), ('DirectML', DIRECTML['version']),
                   ('WebView2', WEBVIEW2['version'])):
        if v not in notices:
            ecarts.append(f'{nom} {v} : version absente de THIRD_PARTY_NOTICES.md')
    if versions['tokenizers'] != TOKENIZERS['version']:
        ecarts.append(f'tokenizers {versions["tokenizers"]} : mettre à jour TOKENIZERS dans construire.py')
    if ecarts:
        raise SystemExit('Inventaire des licences à refaire (THIRD_PARTY_NOTICES.md) :\n  ' + '\n  '.join(ecarts))
    return versions


def _version_et_code(octets):
    """(version de fichier « a.b.c.d », SHA-256 de l'image PE sans somme de contrôle ni signature Authenticode)."""
    import pefile
    pe = pefile.PE(data=octets, fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
    f = pe.VS_FIXEDFILEINFO[0]
    version = f'{f.FileVersionMS >> 16}.{f.FileVersionMS & 0xFFFF}.{f.FileVersionLS >> 16}.{f.FileVersionLS & 0xFFFF}'
    signature = pe.OPTIONAL_HEADER.DATA_DIRECTORY[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_SECURITY']]
    somme = pe.OPTIONAL_HEADER.get_file_offset() + 64
    entree = signature.get_file_offset()
    fin = signature.VirtualAddress if signature.VirtualAddress and signature.Size else len(octets)
    if not somme < entree < fin <= len(octets):
        raise SystemExit('DirectML.dll : en-tête PE inattendu')
    h = hashlib.sha256()
    for debut, arret in ((0, somme), (somme + 4, entree), (entree + 8, fin)):
        h.update(octets[debut:arret])
    pe.close()
    return version, h.hexdigest()


def licence_directml(cache):
    """Textes de licence du paquet NuGet officiel, après avoir vérifié que la DirectML.dll embarquée est bien la
    sienne : même version, même code (seule la signature numérique peut différer)."""
    nupkg = depuis_cache(cache, DIRECTML['fichier'], DIRECTML['url'], 'sha512', DIRECTML['sha512'])
    with zipfile.ZipFile(nupkg) as z:
        du_paquet = z.read(DIRECTML['dll'])
        textes = {n: z.read(n) for n in DIRECTML['textes']}
    embarquee = Path(importlib.metadata.distribution('onnxruntime-directml')
                     .locate_file('onnxruntime/capi/DirectML.dll')).read_bytes()
    (va, ca), (vb, cb) = _version_et_code(du_paquet), _version_et_code(embarquee)
    if va != vb or not va.startswith(DIRECTML['version'] + '.'):
        raise SystemExit(f'DirectML.dll : version {vb} embarquée, {va} dans le paquet NuGet {DIRECTML["version"]}')
    if ca != cb:
        raise SystemExit(f'DirectML.dll embarquée ({vb}) : code différent de celle du paquet NuGet')
    print(f'DirectML.dll {vb} : même code que dans le paquet NuGet officiel {DIRECTML["version"]}')
    return textes


def licence_webview2(cache):
    """Textes de licence du paquet NuGet officiel Microsoft.Web.WebView2, après avoir vérifié que les DLL WebView2
    livrées par pywebview (et embarquées dans MyPhotos) sont exactement les siennes."""
    nupkg = depuis_cache(cache, WEBVIEW2['fichier'], WEBVIEW2['url'], 'sha512', WEBVIEW2['sha512'])
    lib = Path(importlib.metadata.distribution('pywebview').locate_file('webview/lib'))
    with zipfile.ZipFile(nupkg) as z:
        for embarquee, du_paquet in WEBVIEW2['dll'].items():
            if not (lib / embarquee).is_file() or (lib / embarquee).read_bytes() != z.read(du_paquet):
                raise SystemExit(f'webview\\lib\\{embarquee} (pywebview) : différente de {du_paquet} du paquet NuGet '
                                 f'Microsoft.Web.WebView2 {WEBVIEW2["version"]}')
        textes = {n: z.read(n) for n in WEBVIEW2['textes']}
    print(f'DLL WebView2 de pywebview : identiques au paquet NuGet officiel {WEBVIEW2["version"]}')
    return textes


def licence_inno(iscc, dossier):
    """Copie license.txt d'Inno Setup (à côté d'ISCC.exe), contrôlé par son empreinte, dans dossier\\InnoSetup\\."""
    source = Path(iscc).parent / 'license.txt'
    if not source.is_file():
        raise SystemExit(f'{source} est absent : la licence d\'Inno Setup doit accompagner l\'installateur')
    if empreinte(source).hexdigest() != INNO_LICENCE_SHA256:
        raise SystemExit(f'{source} : texte différent de celui d\'Inno Setup {INNO_SETUP}. Relisez-le, comparez-le à '
                         'https://jrsoftware.org/files/is/license.txt, puis mettez à jour INNO_LICENCE_SHA256 et '
                         'THIRD_PARTY_NOTICES.md.')
    _copier(source, dossier / 'InnoSetup' / 'license.txt')


def _crate_a_noter(expression):
    """Vrai si la licence de cette bibliothèque Rust demande ses propres mentions (MIT seule, BSD, « AND »…)."""
    e = ' '.join((expression or '').split())
    if not e or ' AND ' in e:
        return True
    return not any(choix.strip('() ') in LICENCES_SUFFISANTES for choix in re.split(r'\s+OR\s+', e))


def _chemin_sur(morceaux):
    """Vrai si ces éléments de chemin (lus dans une archive) sont de simples noms, sans danger sous Windows."""
    return all(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._-]*', x) and x.split('.')[0].upper() not in NOMS_RESERVES
               for x in morceaux)


NOMS_RESERVES = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}


def licences_crates(cache, sbom, dossier):
    """Fichiers de licence des bibliothèques Rust à noter, lus dans leur paquet crates.io, qui n'est jamais extrait :
    seuls ses fichiers de licence sont recopiés, y compris ceux du code tiers qu'il embarque (Oniguruma, compilé
    dans onig_sys). Renvoie {nom@version: [fichiers]}."""
    notes = {}
    for c in sbom.get('components') or []:
        nom, version = c.get('name'), c.get('version')
        expression = ' | '.join(x.get('expression') or (x.get('license') or {}).get('id') or ''
                                for x in c.get('licenses') or [])
        if not _crate_a_noter(expression) or not str(c.get('purl', '')).startswith('pkg:cargo/'):
            continue
        if not (re.fullmatch(r'[A-Za-z0-9_-]+', nom or '') and re.fullmatch(r'[A-Za-z0-9.+-]+', version or '')):
            raise SystemExit(f'inventaire de tokenizers : nom de bibliothèque inattendu {nom!r} {version!r}')
        sha = next((h.get('content') for h in c.get('hashes') or [] if h.get('alg') == 'SHA-256'), None)
        if not sha:
            raise SystemExit(f'inventaire de tokenizers : pas d\'empreinte pour {nom} {version}')
        paquet = depuis_cache(cache, f'crates/{nom}-{version}.crate', CRATES_URL.format(nom=nom, version=version),
                              'sha256', sha)
        cible = dossier / f'{nom}-{version}'
        trouves = []
        with tarfile.open(paquet, 'r:gz') as tar:
            for m in tar.getmembers():
                morceaux = m.name.split('/')
                if (m.isfile() and len(morceaux) >= 2 and morceaux[0] == f'{nom}-{version}'
                        and RE_FICHIER_LICENCE.fullmatch(morceaux[-1]) and _chemin_sur(morceaux[1:])):
                    fichier = cible.joinpath(*morceaux[1:])
                    fichier.parent.mkdir(parents=True, exist_ok=True)
                    fichier.write_bytes(tar.extractfile(m).read())
                    trouves.append('/'.join(morceaux[1:]))
        if not trouves:
            raise SystemExit(f'{nom} {version} : aucun fichier de licence dans son paquet crates.io')
        notes[f'{nom}@{version}'] = sorted(trouves)
    return notes


def _copier(source, cible):
    cible.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, cible)


def _licences_du_paquet(nom, sous_dossier='licenses'):
    """[(chemin relatif sous <dist-info>/<sous_dossier>/, chemin réel)] d'un paquet installé."""
    d = importlib.metadata.distribution(nom)
    res = [(Path(*f.parts[2:]), Path(d.locate_file(f))) for f in d.files or []
           if len(f.parts) > 2 and f.parts[0].endswith('.dist-info') and f.parts[1] == sous_dossier]
    if not res:
        raise SystemExit(f'{nom} : aucun fichier dans {sous_dossier}/ de ses métadonnées')
    return res


def rassembler_licences(dossier, cache, versions, iscc=None):
    """Remplit `dossier` : un sous-dossier par composant embarqué, plus LISEZMOI.txt qui les présente. Avec `iscc`
    (installateur), ajoute la licence d'Inno Setup."""
    ort = importlib.metadata.distribution('onnxruntime-directml')
    for n in ('LICENSE', 'ThirdPartyNotices.txt', 'Privacy.md'):
        _copier(Path(ort.locate_file(f'onnxruntime/{n}')), dossier / 'onnxruntime' / n)
    for n, octets in licence_directml(cache).items():
        (dossier / 'DirectML').mkdir(parents=True, exist_ok=True)
        (dossier / 'DirectML' / n).write_bytes(octets)
    for paquet, sous in (('numpy', 'numpy'), ('pillow', 'Pillow'), ('pyinstaller', 'PyInstaller')):
        for relatif, source in _licences_du_paquet(paquet):
            _copier(source, dossier / sous / relatif)
    python = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python.is_file():
        raise SystemExit(f'{python} introuvable : la licence de Python doit accompagner l\'exécutable')
    _copier(python, dossier / 'Python' / 'LICENSE.txt')

    # fenêtre de MyPhotos : pywebview et ses dépendances, DLL WebView2
    for paquet, sous in LICENCES_FENETRE:
        for relatif, source in _licences_du_paquet(paquet):
            _copier(source, dossier / sous / relatif)
    _copier(depuis_cache(cache, PROXY_TOOLS['fichier'], PROXY_TOOLS['url'], 'sha256', PROXY_TOOLS['sha256']),
            dossier / 'proxy_tools' / 'LICENSE.txt')
    for n, octets in licence_webview2(cache).items():
        (dossier / 'WebView2').mkdir(parents=True, exist_ok=True)
        (dossier / 'WebView2' / n).write_bytes(octets)
    if iscc:
        licence_inno(iscc, dossier)

    tok = dossier / 'tokenizers'
    _copier(depuis_cache(cache, TOKENIZERS['fichier'], TOKENIZERS['url'], 'sha256', TOKENIZERS['sha256']),
            tok / 'LICENSE')
    sboms = _licences_du_paquet('tokenizers', 'sboms')
    for relatif, source in sboms:
        _copier(source, tok / relatif.name)
    sbom = json.loads(sboms[0][1].read_text(encoding='utf-8'))
    notes = licences_crates(cache, sbom, tok / 'crates')
    lignes = [f'Bibliothèques compilées dans tokenizers.pyd (tokenizers {versions["tokenizers"]}), d\'après '
              f'{sboms[0][0].name}.',
              'Celles qui proposent la licence Apache-2.0 sont couvertes par le fichier LICENSE ; celles qui proposent '
              'Unlicense ou BSL-1.0 ne demandent aucune mention ;',
              'les autres ont leurs fichiers de licence dans crates\\<nom>-<version>\\.', '']
    for c in sorted(sbom.get('components') or [], key=lambda c: (c.get('name') or '').lower()):
        expression = ' | '.join(x.get('expression') or (x.get('license') or {}).get('id') or ''
                                for x in c.get('licenses') or [])
        cle = f'{c.get("name")}@{c.get("version")}'
        note = f'   crates\\{c.get("name")}-{c.get("version")}\\' if cle in notes else ''
        lignes.append(f'{c.get("name", "?"):28} {c.get("version", "?"):12} {" ".join(expression.split())}{note}')
    (tok / 'COMPOSANTS.txt').write_text('\n'.join(lignes) + '\n', encoding='utf-8')

    titre = f'Licences des composants de MyPhotos.exe {version_myphotos()}'
    (dossier / 'LISEZMOI.txt').write_text(LISEZMOI_LICENCES.format(
        titre=titre, souligne='=' * len(titre), ort=versions['onnxruntime-directml'], directml=DIRECTML['version'],
        numpy=versions['numpy'], pillow=versions['pillow'], tokenizers=versions['tokenizers'],
        python=versions['python'], pyinstaller=versions['pyinstaller'], crates=len(notes),
        pywebview=versions['pywebview'], webview2=WEBVIEW2['version'], pythonnet=versions['pythonnet'],
        clr_loader=versions['clr_loader'], cffi=versions['cffi'], pycparser=versions['pycparser'],
        proxy_tools=versions['proxy_tools'], bottle=versions['bottle'],
        typing_extensions=versions['typing_extensions'],
        inno=LISEZMOI_INNO.format(inno=INNO_SETUP) if iscc else ''), encoding='utf-8')
    return notes


LISEZMOI_LICENCES = '''\
{titre}
{souligne}

MyPhotos est publié sous licence MIT (fichier LICENSE, à côté de ce dossier). MyPhotos.exe embarque aussi les
composants ci-dessous, chacun sous sa propre licence. THIRD_PARTY_NOTICES.md (à côté de ce dossier) explique,
pour chacun, sa licence et ce qu'elle impose ; ce dossier contient leurs textes, tels que leurs auteurs les
fournissent.

onnxruntime\\   ONNX Runtime {ort} (paquet onnxruntime-directml) : LICENSE, ThirdPartyNotices.txt, Privacy.md
DirectML\\      DirectML {directml} (DirectML.dll) : LICENSE.txt et ThirdPartyNotices.txt du paquet NuGet officiel
               Microsoft.AI.DirectML {directml}
numpy\\         NumPy {numpy}, avec OpenBLAS : LICENSE.txt et les licences de ses parties
Pillow\\        Pillow {pillow} : LICENSE, qui reprend celles des bibliothèques d'images livrées avec lui
tokenizers\\    tokenizers {tokenizers} (Hugging Face) : LICENSE (Apache-2.0), l'inventaire de ses bibliothèques Rust
               (tokenizers-python.cyclonedx.json, résumé dans COMPOSANTS.txt) et, dans crates\\, les licences
               propres de {crates} d'entre elles
Python\\        Python {python} : LICENSE.txt, qui reprend aussi les licences d'OpenSSL, bzip2, libffi, Zstandard et
               les conditions du runtime Microsoft Visual C++ (et celles de Tcl/Tk, que MyPhotos n'embarque plus)
PyInstaller\\   chargeur de PyInstaller {pyinstaller} : COPYING.txt (GPL avec exception pour le chargeur)

La fenêtre de MyPhotos :
pywebview\\     pywebview {pywebview} : LICENSE (BSD-3-Clause), qui couvre aussi WebBrowserInterop.x64.dll
WebView2\\      Microsoft.Web.WebView2.Core.dll, Microsoft.Web.WebView2.WinForms.dll et WebView2Loader.dll, livrées
               par pywebview : LICENSE.txt et NOTICE.txt du paquet NuGet officiel Microsoft.Web.WebView2 {webview2}
pythonnet\\     pythonnet {pythonnet} (Python.Runtime.dll) : LICENSE (MIT) et AUTHORS.md
clr_loader\\    clr_loader {clr_loader} (ClrLoader.dll) : LICENSE (MIT)
cffi\\          cffi {cffi} : LICENSE (MIT-0)
pycparser\\     pycparser {pycparser} : LICENSE (BSD-3-Clause)
proxy_tools\\   proxy_tools {proxy_tools} : LICENSE.txt de son dépôt (licence BSD ; la fiche PyPI indique MIT)
bottle\\        bottle {bottle} : LICENSE (MIT)
typing_extensions\\  typing_extensions {typing_extensions} : LICENSE (PSF-2.0)
{inno}
English: this folder holds the license texts of the third-party components bundled in MyPhotos.exe.
See THIRD_PARTY_NOTICES.md for what each license requires.
'''

LISEZMOI_INNO = '''
L'installateur :
InnoSetup\\     Inno Setup {inno} : license.txt. Le programme d'installation et le désinstallateur (unins000.exe)
               sont faits avec Inno Setup ; MyPhotos.exe n'en contient rien.
'''


def dossier_refait(dossier):
    """Vide `dossier` s'il a été créé par --paquet (marque), sinon refuse : rien d'autre n'est jamais effacé."""
    if dossier.exists():
        if not (dossier / MARQUE).is_file():
            raise SystemExit(f'{dossier} existe mais n\'a pas été créé par construire.py --paquet : '
                             'déplacez-le ailleurs, puis relancez.')
        shutil.rmtree(dossier)
    dossier.mkdir(parents=True)
    (dossier / MARQUE).write_text('Refait à chaque « construire.py --paquet ».\n', encoding='utf-8')


def ecrire_empreinte(fichier):
    """Écrit <fichier>.sha256 au format de sha256sum (« empreinte  nom ») et renvoie l'empreinte."""
    h = empreinte(fichier).hexdigest()
    fichier.with_name(fichier.name + '.sha256').write_text(f'{h}  {fichier.name}\n', encoding='ascii', newline='\n')
    return h


def preparer_licences(cache=None, dist=DIST, iscc=None):
    """Refait <dist>\\licences\\ (textes de licence des composants embarqués), pour --paquet et --installateur.
    Rend (dossier, fichiers, notes)."""
    for f in (RACINE / 'LICENSE', RACINE / 'THIRD_PARTY_NOTICES.md', MODE_EMPLOI):
        if not f.is_file():
            raise SystemExit(f'{f} est absent')
    versions = verifier_inventaire()
    cache = dossier_cache(cache)
    print(f'cache des téléchargements : {cache}')
    licences = Path(dist) / 'licences'
    dossier_refait(licences)
    notes = rassembler_licences(licences, cache, versions, iscc)      # iscc : plus la licence d'Inno Setup
    fichiers = sorted(p for p in licences.rglob('*') if p.is_file() and p.name != MARQUE)
    print(f'licences : {len(fichiers)} fichiers dans {licences} (dont {len(notes)} bibliothèques Rust notées)')
    return licences, fichiers, notes


def paquet(cache=None):
    """Prépare la publication du dist\\MyPhotos.exe déjà construit : licences, archive .zip, empreintes."""
    version = version_myphotos()
    exe = DIST / 'MyPhotos.exe'
    if not exe.is_file():
        raise SystemExit(f'{exe} est absent : construisez-le (construction\\construire.ps1) et essayez-le d\'abord.')
    verifier_environnement()
    controler(exe, version)                     # contenu et ressource de version : bien le MyPhotos.exe {version}
    licences, fichiers, notes = preparer_licences(cache)

    archive = DIST / f'MyPhotos-{version}-windows.zip'
    contenu = [(exe, 'MyPhotos.exe'), (RACINE / 'LICENSE', 'LICENSE'),
               (RACINE / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md'), (MODE_EMPLOI, MODE_EMPLOI.name)]
    contenu += [(p, 'licences/' + p.relative_to(licences).as_posix()) for p in fichiers]
    partiel = archive.with_name(archive.name + '.part')
    with zipfile.ZipFile(partiel, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for source, nom in contenu:
            info = zipfile.ZipInfo.from_file(source, nom)
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(source, 'rb') as f:
                z.writestr(info, f.read(), compresslevel=9)
    with zipfile.ZipFile(partiel) as z:
        if z.testzip() is not None or z.read('MyPhotos.exe') != exe.read_bytes():
            raise SystemExit(f'{partiel} : archive illisible')
    os.replace(partiel, archive)

    for f in (exe, archive):
        octets = f'{f.stat().st_size:,}'.replace(',', ' ')
        print(f'{f.name} : {octets} octets, SHA-256 {ecrire_empreinte(f)}')
    return archive


# ---------------------------------------------------------------- installateur (--installateur)

ISS = ICI / 'MyPhotos.iss'
INNO_SETUP = '6.7.4'                     # version d'Inno Setup essayée (THIRD_PARTY_NOTICES.md, LISEZMOI.md)


def trouver_iscc(choisi=None):
    """ISCC.exe d'Inno Setup 6 : --iscc, sinon %MYPHOTOS_ISCC%, sinon les emplacements d'installation habituels
    (pour l'utilisateur seul, puis pour tous)."""
    impose = choisi or os.environ.get('MYPHOTOS_ISCC')
    if impose:
        if not Path(impose).is_file():
            raise SystemExit(f'ISCC.exe introuvable : {impose}')
        return Path(impose)
    for base, sous in (('LOCALAPPDATA', 'Programs\\Inno Setup 6'), ('ProgramFiles(x86)', 'Inno Setup 6'),
                       ('ProgramFiles', 'Inno Setup 6')):
        if os.environ.get(base) and (Path(os.environ[base]) / sous / 'ISCC.exe').is_file():
            return Path(os.environ[base]) / sous / 'ISCC.exe'
    raise SystemExit('Inno Setup 6 est absent (ISCC.exe). Installez-le pour l\'utilisateur seul, sans droits '
                     'd\'administrateur :\n    innosetup-6.x.y.exe /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES '
                     '/NORESTART /MERGETASKS="!fileassoc,!desktopicon"\nou indiquez-le : --iscc <chemin de ISCC.exe>')


# Inno Setup copie chaque fichier sous un nom provisoire posé à côté de lui (« is-XXXXXXXXXX.tmp », 17 caractères),
# puis le renomme : c'est ce nom, dans le dossier le plus profond, qui atteint d'abord la limite des 259 caractères.
# Essai adverse de la 0.3.0 (plus long fichier : 89 caractères, dans un dossier de 81) : un dossier d'installation de
# 160 caractères était accepté (160 + 1 + 89 = 250) mais la copie s'arrêtait au milieu (« chemin d'accès
# introuvable », code 5, tout annulé) ; 159 passait (159 + 1 + 81 + 1 + 17 = 259).
PROVISOIRE_INNO = len('is-XXXXXXXXXX.tmp')


def _longueur_posee(chemin):
    """Ce qu'un fichier posé par Inno Setup demande sous le dossier d'installation : son chemin, et celui de son nom
    provisoire."""
    parent = os.path.dirname(chemin)
    return max(len(chemin), (len(parent) + 1 if parent else 0) + PROVISOIRE_INNO)


def chemin_le_plus_long(dossier, licences):
    """Longueur du plus long chemin que l'installateur pose sous son dossier d'installation (version dossier et
    licences\\), noms provisoires d'Inno Setup compris : MyPhotos.iss refuse un dossier d'installation pour lequel un
    chemin dépasserait 259 caractères."""
    chemins = [str(p.relative_to(dossier)) for p in Path(dossier).rglob('*') if p.is_file()]
    chemins += ['licences\\' + str(p.relative_to(licences)) for p in Path(licences).rglob('*')
                if p.is_file() and p.name != MARQUE]
    return max(_longueur_posee(c) for c in chemins)


def version_iscc(journal):
    """Version d'Inno Setup lue dans le journal d'ISCC (ses fichiers ont une ressource de version 0.0.0.0) ; une
    autre version que INNO_SETUP est signalée, sans arrêter."""
    m = re.search(r'Compiler engine version: Inno Setup (\S+)',
                  Path(journal).read_text(encoding='utf-8', errors='replace'))
    v = m.group(1) if m else '?'
    if v != INNO_SETUP:
        print(f'Attention : Inno Setup {v} (MyPhotos.iss est essayé avec Inno Setup {INNO_SETUP}).')
    return v


def installateur(cache=None, iscc=None, sortie=None):
    """Compile l'installateur à partir de la version dossier déjà construite (et essayée) : <dist>\\MyPhotos\\
    (dist\\, ou `sortie`). Licences, ISCC, ressource de version, empreinte."""
    version = version_myphotos()
    dist = Path(sortie).resolve() if sortie else DIST
    dossier = dist / 'MyPhotos'
    if not (dossier / 'MyPhotos.exe').is_file():
        raise SystemExit(f'{dossier}\\MyPhotos.exe est absent : construisez la version dossier '
                         '(construction\\construire.ps1 --dossier) et essayez-la d\'abord.')
    if not ISS.is_file():
        raise SystemExit(f'{ISS} est absent')
    verifier_environnement()
    iscc = trouver_iscc(iscc)
    print(f'Inno Setup : {iscc}')
    controler_dossier(dossier, version)         # bien le MyPhotos {version}, complet, rien d'autre à la racine
    licences, _, _ = preparer_licences(cache, dist, iscc)
    cible = dist / f'MyPhotos-{version}-installation.exe'
    for f in (cible, cible.with_name(cible.name + '.sha256')):
        f.unlink(missing_ok=True)
    commande = [str(iscc), f'/DMyAppVersion={version}', f'/DRacine={RACINE}', f'/DDossierApp={dossier}',
                f'/DDossierLicences={licences}', f'/DSortie={dist}',
                f'/DCheminLePlusLong={chemin_le_plus_long(dossier, licences)}', str(ISS)]
    print('compilation :', subprocess.list2cmdline(commande))
    debut = time.monotonic()
    journal = (dist / 'build' if sortie else BUILD) / 'installateur-iscc.log'
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, 'wb') as f:
        r = subprocess.run(commande, cwd=ICI, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode != 0 or not cible.is_file():
        print(journal.read_text(encoding='utf-8', errors='replace')[-4000:])
        raise SystemExit(f'Échec d\'Inno Setup (code {r.returncode}) : voir {journal}')
    print(f'Inno Setup {version_iscc(journal)}')
    # Inno Setup complète les textes de la ressource de version par des espaces (champs de taille fixe).
    infos = {k: v.strip() for k, v in lire_version(cible).items()}
    if infos.get('ProductVersion') != version or infos.get('ProductName') != 'MyPhotos':
        raise SystemExit(f'{cible.name} : ressource de version inattendue {infos}')
    octets = f'{cible.stat().st_size:,}'.replace(',', ' ')
    print(f'compilé en {time.monotonic() - debut:.0f} s : {cible}')
    print(f'{cible.name} : {octets} octets, SHA-256 {ecrire_empreinte(cible)}')
    return cible


# ---------------------------------------------------------------- manifeste de mise à jour (--manifeste)

NOUVEAUTES = ICI / 'nouveautes.json'


def nouveautes(version):
    """Les lignes « Nouveautés » de cette version (construction/nouveautes.json), telles que la page les affichera."""
    try:
        toutes = json.loads(NOUVEAUTES.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise SystemExit(f'{NOUVEAUTES} illisible : {e}') from None
    lignes = toutes.get(version) if isinstance(toutes, dict) else None
    if not isinstance(lignes, list) or not lignes:
        raise SystemExit(f'{NOUVEAUTES} : aucune nouveauté pour la version {version}')
    return lignes


def manifeste(sortie=None, cle_privee=None):
    """Écrit <dist>\\MyPhotos-mise-a-jour.json pour l'installateur déjà construit, signé, puis relu comme MyPhotos le
    lira (clé publique de config.py). cle_privee : pour les essais seulement ; sinon la clé du projet."""
    if str(RACINE) not in sys.path:
        sys.path.insert(0, str(RACINE))
    from myphotos import config, mise_a_jour
    version = version_myphotos()
    dist = Path(sortie).resolve() if sortie else DIST
    inst = dist / mise_a_jour.nom_installateur(version)
    if not inst.is_file():
        raise SystemExit(f'{inst} est absent : construisez d\'abord l\'installateur (--installateur).')
    if cle_privee is None:
        sys.path.insert(0, str(ICI))
        import cle_signature
        cle_privee = cle_signature.lire_cle_privee()
        cle_signature.verifier_correspondance(cle_privee)
        publique = config.cle_mise_a_jour()
    else:
        publique = cle_privee[:64]
    brut = mise_a_jour.faire_manifeste(version, inst, cle_privee, nouveautes(version))
    info = mise_a_jour.lire_manifeste(brut, cle=publique)
    if (info['version'], info['taille'], info['sha256']) != (version, inst.stat().st_size,
                                                             empreinte(inst).hexdigest()):
        raise SystemExit('manifeste incohérent avec l\'installateur')
    cible = dist / mise_a_jour.NOM_MANIFESTE
    cible.write_bytes(brut)
    octets = f'{info["taille"]:,}'.replace(',', ' ')
    print(f'{cible.name} : MyPhotos {version}, installateur de {octets} octets, SHA-256 {info["sha256"]}')
    print(f'{cible.name} : SHA-256 {ecrire_empreinte(cible)}')
    return cible


def _valeur(argv, option):
    """Valeur de `option` dans argv (None si l'option est absente)."""
    if option not in argv:
        return None
    i = argv.index(option)
    if i + 1 >= len(argv) or argv[i + 1].startswith('--'):
        raise SystemExit(f'{option} demande une valeur')
    return argv[i + 1]


def main(argv):
    if '--paquet' in argv:
        paquet(_valeur(argv, '--cache'))
    elif '--installateur' in argv:
        installateur(_valeur(argv, '--cache'), _valeur(argv, '--iscc'), _valeur(argv, '--sortie'))
    elif '--manifeste' in argv:
        manifeste(_valeur(argv, '--sortie'))
    else:
        construire(essai='--essai' in argv, sortie=_valeur(argv, '--sortie'), dossier='--dossier' in argv)


if __name__ == '__main__':
    main(sys.argv[1:])
