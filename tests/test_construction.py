"""Essais de construction/construire.py (préparation de la publication, --paquet, --dossier, --installateur) et de
construction/MyPhotos.iss, sans réseau ni PyInstaller.

    .venv\\Scripts\\python.exe -m unittest tests.test_construction

Les téléchargements passent par des adresses file:// ; un paquet crates.io est fabriqué sur place, avec des
membres piégés (chemin qui remonte, deux-points, lien, autre racine) qui ne doivent jamais être recopiés.

Essai réel de l'installateur, à la demande (Windows, Inno Setup 6 installé, version dossier déjà construite) :

    set MYPHOTOS_ESSAI_INSTALLATEUR=1
    set MYPHOTOS_ESSAI_DOSSIER_APP=C:\\chemin\\MyPhotos      (défaut : dist\\MyPhotos, de construire.ps1 --dossier)
    set MYPHOTOS_ESSAI_UI=C:\\chemin\\essai-ui                (dossier de travail ; défaut : %TEMP%\\myphotos-essai-ui)

Il compile l'installateur dans ce dossier de travail, installe en silence DANS ce dossier (/DIR, sans raccourci),
puis vérifie le mutex, la mise à jour par-dessus et les deux désinstallations. Tous les processus tournent dans un
Job Object de l'essai, sur un bureau Windows invisible, avec TEMP dans le dossier de travail. L'entrée de
« Applications installées » (HKCU) n'existe que pendant l'essai.
"""
import base64
import ctypes
import fnmatch
import hashlib
import io
import os
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / 'construction'))
sys.path.insert(0, str(RACINE / 'tests'))

import construire  # noqa: E402
from myphotos import config, fenetre  # noqa: E402

ISS = RACINE / 'construction' / 'MyPhotos.iss'
APP_ID = '25DE0724-F7B4-4956-8238-1E33E16ABFED'
CLE_DESINSTALLATION = rf'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{APP_ID}}}_is1'


def section(texte, nom):
    """Lignes d'une section du .iss ([Setup], [Code]…), jusqu'à la section suivante."""
    debut = texte.index(f'\n[{nom}]\n') + len(nom) + 3
    m = re.search(r'^\[[A-Za-z]+\]$', texte[debut:], re.M)
    return texte[debut:debut + m.start()] if m and nom != 'Code' else texte[debut:]


def nom_de_myphotos():
    """La fonction NomDeMyPhotos du .iss, traduite en Python : (nom) -> vrai si le désinstallateur le supprime."""
    texte = ISS.read_text(encoding='utf-8')
    corps = texte[texte.index('function NomDeMyPhotos'):]
    corps = corps[corps.index('begin'):corps.index('end;')]
    exacts = set(re.findall(r"\(N = '([^']+)'\)", corps))
    if '(N = MARQUEUR)' in corps:
        exacts.add(re.search(r"MARQUEUR = '([^']+)'", texte).group(1))
    motifs = re.findall(r"WildcardMatch\(N, '([^']+)'\)", corps)
    return lambda nom: nom.lower() in exacts or any(fnmatch.fnmatchcase(nom.lower(), m) for m in motifs)


def mutex_existe():
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    k32.OpenMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenMutexW(0x00100000, False, config.MUTEX)                    # SYNCHRONIZE
    if h:
        k32.CloseHandle(h)
    return bool(h)


def port_libre(port):
    with socket.socket() as s:
        return s.connect_ex(('127.0.0.1', port)) != 0


def crate(nom, version, membres):
    """Paquet .crate (tar.gz) : membres = [(chemin, contenu bytes, ou None pour un lien symbolique)]."""
    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode='w:gz') as tar:
        for chemin, contenu in membres:
            info = tarfile.TarInfo(chemin)
            if contenu is None:
                info.type, info.linkname = tarfile.SYMTYPE, '../../ailleurs/LICENSE'
                tar.addfile(info)
            else:
                info.size = len(contenu)
                tar.addfile(info, io.BytesIO(contenu))
    return tampon.getvalue()


class TestVersion(unittest.TestCase):
    def test_version_lue_sans_importer(self):
        self.assertEqual(construire.version_myphotos(), config.VERSION)
        self.assertEqual(construire.nombres_version('0.2.0'), (0, 2, 0, 0))


class TestFenetre(unittest.TestCase):
    """Ce que la fenêtre de MyPhotos (pywebview, pythonnet, WebView2) demande à la construction."""

    def test_fichiers_retires(self):
        for nom in ('onnxruntime\\capi\\onnxruntime.dll', 'webview\\lib\\WebBrowserInterop.x86.dll',
                    'webview/lib/pywebview-android.jar', 'clr_loader\\ffi\\dlls\\x86\\ClrLoader.dll'):
            self.assertTrue(construire.retire(nom), nom)
        # Les trois WebView2Loader.dll restent : pywebview ajoute leurs dossiers au PATH en s'important.
        for nom in ('webview\\lib\\runtimes\\win-x86\\native\\WebView2Loader.dll',
                    'webview\\lib\\runtimes\\win-arm64\\native\\WebView2Loader.dll',
                    'clr_loader\\ffi\\dlls\\amd64\\ClrLoader.dll', 'webview\\lib\\WebBrowserInterop.x64.dll'):
            self.assertFalse(construire.retire(nom), nom)
        for nom in construire.OBLIGATOIRES:
            self.assertFalse(construire.retire(nom), nom)

    def test_spec(self):
        """Le .spec généré se compile, et son tri des fichiers est celui de construire.retire()."""
        texte = construire.SPEC.format(entree='lancer.py', racine='.', nom='MyPhotos', icone='x.ico',
                                       version='version.txt', datas=[], caches=construire.CACHES,
                                       exclus=construire.EXCLUS, retires=sorted(construire.RETIRES),
                                       dossiers=construire.RETIRES_DOSSIERS)
        compile(texte, 'MyPhotos.spec', 'exec')
        espace = {}
        exec(texte[texte.index('RETIRES ='):texte.index('a.binaries =')], espace)
        for nom in ('clr_loader\\ffi\\dlls\\x86\\ClrLoader.dll', 'clr_loader/ffi/dlls/amd64/ClrLoader.dll',
                    'webview\\lib\\runtimes\\win-x86\\native\\WebView2Loader.dll', 'onnxruntime\\capi\\onnxruntime.dll',
                    'webview\\lib\\pywebview-android.jar', 'page\\index.html'):
            self.assertEqual(espace['garder']((nom, 'source', 'BINARY')), not construire.retire(nom), nom)
        self.assertIn("'webview.platforms.winforms'", texte)
        # pywebview a besoin de typing_extensions ; tkinter sert encore aux replis (Edge, navigateur).
        for necessaire in ('typing_extensions', 'tkinter', '_tkinter', 'webview', 'clr', 'bottle', 'proxy_tools'):
            self.assertNotIn(necessaire, construire.EXCLUS)


class TestLicencesRust(unittest.TestCase):
    def test_licences_qui_demandent_leurs_mentions(self):
        a_noter = ['MIT', 'BSD-2-Clause', '(MIT OR Apache-2.0) AND Unicode-3.0', '', None, 'MIT | Apache-2.0']
        couvertes = ['MIT OR Apache-2.0', 'Apache-2.0  OR  MIT', 'Apache-2.0', 'Unlicense OR MIT',
                     'Apache-2.0 WITH LLVM-exception', 'Apache-2.0 OR BSL-1.0', 'BSD-2-Clause OR Apache-2.0 OR MIT',
                     'Apache-2.0 OR MIT OR Zlib']
        for e in a_noter:
            self.assertTrue(construire._crate_a_noter(e), e)
        for e in couvertes:
            self.assertFalse(construire._crate_a_noter(e), e)

    def test_chemins_sans_danger(self):
        for ok in (['LICENSE'], ['oniguruma', 'COPYING'], ['LICENSE-MIT.txt']):
            self.assertTrue(construire._chemin_sur(ok), ok)
        for mauvais in (['..', 'LICENSE'], ['.', 'LICENSE'], ['a:b'], ['LICENSE:flux'], ['CON'], ['nul.txt'],
                        ['com1'], ['.cache', 'LICENSE'], ['a\\b'], [''], ['dossier ', 'LICENSE']):
            self.assertFalse(construire._chemin_sur(mauvais), mauvais)

    def test_paquet_crates_piege(self):
        """Seuls les fichiers de licence sûrs, à la racine du paquet ou dans un code embarqué, sont recopiés ; une
        bibliothèque couverte par Apache-2.0 n'est jamais téléchargée."""
        donnees = crate('piege', '1.2.3', [
            ('piege-1.2.3/LICENSE-MIT', b'Copyright (c) Exemple\nMIT\n'),
            ('piege-1.2.3/vendor/COPYING', b'BSD-2-Clause\n'),
            ('piege-1.2.3/../evasion/LICENSE', b'NON'),
            ('piege-1.2.3/a:b/LICENSE', b'NON'),
            ('autre-1.0.0/LICENSE', b'NON'),
            ('piege-1.2.3/src/lib.rs', b'fn main() {}'),
            ('piege-1.2.3/LICENSE-LIEN', None),
        ])
        sbom = {'components': [
            {'name': 'piege', 'version': '1.2.3', 'licenses': [{'expression': 'MIT'}], 'purl': 'pkg:cargo/piege@1.2.3',
             'hashes': [{'alg': 'SHA-256', 'content': hashlib.sha256(donnees).hexdigest()}]},
            {'name': 'couverte', 'version': '1.0.0', 'licenses': [{'expression': 'MIT OR Apache-2.0'}],
             'purl': 'pkg:cargo/couverte@1.0.0', 'hashes': [{'alg': 'SHA-256', 'content': '0' * 64}]},
        ]}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'cache' / 'crates').mkdir(parents=True)
            (d / 'cache' / 'crates' / 'piege-1.2.3.crate').write_bytes(donnees)
            source = d / 'source'
            source.mkdir()
            (source / 'piege' / 'piege-1.2.3.crate').parent.mkdir()
            (source / 'piege' / 'piege-1.2.3.crate').write_bytes(donnees)
            # Le cache sert d'abord ; une adresse locale garantit qu'aucun téléchargement réel n'aurait lieu.
            with mock.patch.object(construire, 'CRATES_URL', source.as_uri() + '/{nom}/{nom}-{version}.crate'), \
                    mock.patch('builtins.print'):
                notes = construire.licences_crates(d / 'cache', sbom, d / 'sortie')
                self.assertEqual(notes, {'piege@1.2.3': ['LICENSE-MIT', 'vendor/COPYING']})
                ecrits = sorted(p.relative_to(d / 'sortie').as_posix() for p in (d / 'sortie').rglob('*')
                                if p.is_file())
                self.assertEqual(ecrits, ['piege-1.2.3/LICENSE-MIT', 'piege-1.2.3/vendor/COPYING'])
                self.assertFalse((d / 'evasion').exists())
                self.assertFalse((d / 'cache' / 'crates' / 'couverte-1.0.0.crate').exists())

                # Paquet absent du cache : téléchargé (ici depuis l'adresse locale), puis contrôlé.
                (d / 'cache' / 'crates' / 'piege-1.2.3.crate').unlink()
                notes = construire.licences_crates(d / 'cache', sbom, d / 'sortie2')
                self.assertEqual(notes, {'piege@1.2.3': ['LICENSE-MIT', 'vendor/COPYING']})

                # Paquet sans aucun fichier de licence : arrêt.
                vide = crate('vide', '0.1.0', [('vide-0.1.0/src/lib.rs', b'')])
                (d / 'cache' / 'crates' / 'vide-0.1.0.crate').write_bytes(vide)
                sbom_vide = {'components': [{'name': 'vide', 'version': '0.1.0', 'licenses': [{'expression': 'MIT'}],
                                             'purl': 'pkg:cargo/vide@0.1.0', 'hashes': [
                                                 {'alg': 'SHA-256', 'content': hashlib.sha256(vide).hexdigest()}]}]}
                with self.assertRaises(SystemExit):
                    construire.licences_crates(d / 'cache', sbom_vide, d / 'sortie3')


class TestCache(unittest.TestCase):
    def test_telechargement_controle(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            source = d / 'paquet.bin'
            source.write_bytes(b'contenu officiel')
            sha256 = hashlib.sha256(b'contenu officiel').hexdigest()
            sha512 = base64.b64encode(hashlib.sha512(b'contenu officiel').digest()).decode('ascii')
            cache = d / 'cache'
            with mock.patch('builtins.print'):
                p = construire.depuis_cache(cache, 'a/paquet.bin', source.as_uri(), 'sha256', sha256)
                self.assertEqual(p.read_bytes(), b'contenu officiel')
                # Empreinte SHA-512 en base64, comme la publie NuGet.
                self.assertEqual(construire.depuis_cache(cache, 'b.bin', source.as_uri(), 'sha512', sha512).read_bytes(),
                                 b'contenu officiel')
                # Fichier du cache abîmé : téléchargé de nouveau.
                p.write_bytes(b'abime')
                self.assertEqual(construire.depuis_cache(cache, 'a/paquet.bin', source.as_uri(), 'sha256',
                                                         sha256).read_bytes(), b'contenu officiel')
                # Empreinte fausse : arrêt, rien dans le cache sous le nom attendu.
                with self.assertRaises(SystemExit):
                    construire.depuis_cache(cache, 'c.bin', source.as_uri(), 'sha256', '0' * 64)
            self.assertFalse((cache / 'c.bin').exists())
            self.assertTrue((cache / 'c.bin.part').exists())

    def test_dossier_du_cache(self):
        with mock.patch.dict('os.environ', {'MYPHOTOS_CACHE_CONSTRUCTION': '', 'LOCALAPPDATA': str(RACINE / 'x')}):
            self.assertEqual(construire.dossier_cache(), RACINE / 'x' / 'MyPhotos-construction' / 'cache')
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(construire.dossier_cache(d), Path(d).resolve())


class TestLicencesFenetre(unittest.TestCase):
    """Licences des composants de la fenêtre (0.3.0) : inventaire, DLL WebView2 comparées au paquet NuGet officiel,
    proxy_tools, Inno Setup, et ce que rassembler_licences écrit."""

    def test_inventaire_cite_les_versions_figees(self):
        """Chaque composant de PAQUETS_LICENCES a, dans THIRD_PARTY_NOTICES.md, la version de requirements*.txt."""
        figees = {}
        for nom in ('requirements.txt', 'requirements-build.txt'):
            for ligne in (RACINE / nom).read_text(encoding='utf-8').splitlines():
                m = re.match(r'\s*([A-Za-z0-9_.-]+)\s*==\s*([^\s#;]+)', ligne)
                if m:
                    figees[construire._cle_paquet(m.group(1))] = m.group(2)
        notices = (RACINE / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        for nom in construire.PAQUETS_LICENCES:
            self.assertIn(construire._cle_paquet(nom), figees, nom)
            self.assertIn(figees[construire._cle_paquet(nom)], notices, nom)
        for v in (construire.WEBVIEW2['version'], construire.DIRECTML['version'], construire.INNO_SETUP,
                  construire.PROXY_TOOLS['version']):
            self.assertIn(v, notices)
        self.assertEqual(figees['proxy-tools'], construire.PROXY_TOOLS['version'])
        # Tous les paquets de la fenêtre ont leur dossier de licences (proxy_tools : texte téléchargé).
        self.assertEqual({construire._cle_paquet(p) for p, _ in construire.LICENCES_FENETRE} | {'proxy-tools'},
                         {construire._cle_paquet(p) for p in construire.PAQUETS_LICENCES[5:]})

    def test_webview2_couvre_les_dll_embarquees(self):
        """Chaque DLL WebView2 obligatoire de l'exe est comparée au paquet NuGet."""
        obligatoires = {n[len('webview\\lib\\'):].replace('\\', '/') for n in construire.OBLIGATOIRES
                        if n.startswith('webview\\lib\\')}
        self.assertEqual(obligatoires, set(construire.WEBVIEW2['dll']))
        self.assertEqual(construire.WEBVIEW2['textes'], ('LICENSE.txt', 'NOTICE.txt'))

    def _paquet_webview2(self, d, change=None):
        """Faux paquet NuGet fait des DLL de pywebview installé (une peut être changée) ; rend (url, sha512)."""
        import importlib.metadata
        lib = Path(importlib.metadata.distribution('pywebview').locate_file('webview/lib'))
        nupkg = Path(d) / 'source' / 'wv2.nupkg'
        nupkg.parent.mkdir(parents=True, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(nupkg, 'w') as z:
            for embarquee, du_paquet in construire.WEBVIEW2['dll'].items():
                octets = (lib / embarquee).read_bytes()
                z.writestr(du_paquet, octets + b'x' if embarquee == change else octets)
            z.writestr('LICENSE.txt', b'Copyright (C) Microsoft Corporation.')
            z.writestr('NOTICE.txt', b'NOTICES AND INFORMATION')
        sha = base64.b64encode(hashlib.sha512(nupkg.read_bytes()).digest()).decode('ascii')
        return nupkg.as_uri(), sha

    def test_licence_webview2(self):
        with tempfile.TemporaryDirectory() as d:
            url, sha = self._paquet_webview2(d)
            faux = dict(construire.WEBVIEW2, url=url, sha512=sha)
            with mock.patch.object(construire, 'WEBVIEW2', faux), mock.patch('builtins.print'):
                textes = construire.licence_webview2(Path(d) / 'cache')
            self.assertEqual(textes, {'LICENSE.txt': b'Copyright (C) Microsoft Corporation.',
                                      'NOTICE.txt': b'NOTICES AND INFORMATION'})

            # Une DLL de pywebview différente de celle du paquet : arrêt.
            url, sha = self._paquet_webview2(Path(d) / 'b', change='runtimes/win-arm64/native/WebView2Loader.dll')
            faux = dict(construire.WEBVIEW2, url=url, sha512=sha, fichier='autre.nupkg')
            with mock.patch.object(construire, 'WEBVIEW2', faux), mock.patch('builtins.print'), \
                    self.assertRaises(SystemExit) as e:
                construire.licence_webview2(Path(d) / 'cache')
            self.assertIn('win-arm64', str(e.exception))

    def test_licence_inno(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'Inno').mkdir()
            (d / 'Inno' / 'license.txt').write_bytes(b'Inno Setup License\n')
            bonne = hashlib.sha256(b'Inno Setup License\n').hexdigest()
            with mock.patch.object(construire, 'INNO_LICENCE_SHA256', bonne):
                construire.licence_inno(d / 'Inno' / 'ISCC.exe', d / 'licences')
            self.assertEqual((d / 'licences' / 'InnoSetup' / 'license.txt').read_bytes(), b'Inno Setup License\n')
            with self.assertRaises(SystemExit):                       # autre texte (autre version) : arrêt
                construire.licence_inno(d / 'Inno' / 'ISCC.exe', d / 'licences2')
            with self.assertRaises(SystemExit):                       # absent : arrêt
                construire.licence_inno(d / 'ailleurs' / 'ISCC.exe', d / 'licences3')

    def test_rassembler_licences(self):
        """Sans réseau (téléchargements et licences NuGet simulés) : un dossier par composant, LISEZMOI.txt qui les
        cite, et la licence d'Inno Setup seulement pour l'installateur."""
        import importlib.metadata
        versions = {n: importlib.metadata.version(n) for n in construire.PAQUETS_LICENCES if n != 'pyinstaller'}
        versions['pyinstaller'] = '6.22.3'
        versions['python'] = '3.14.10'
        reel = construire._licences_du_paquet

        def licences_du_paquet(nom, sous='licenses'):
            if nom == 'pyinstaller':                                  # absent de .venv (seulement dans .venv-build)
                return [(Path('COPYING.txt'), RACINE / 'LICENSE')]
            return reel(nom, sous)

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            telecharge = d / 'telecharge.txt'
            telecharge.write_text('texte', encoding='utf-8')
            (d / 'Inno').mkdir()
            (d / 'Inno' / 'license.txt').write_bytes(b'Inno Setup License\n')
            with mock.patch.object(construire, 'depuis_cache', return_value=telecharge), \
                    mock.patch.object(construire, 'licence_directml', return_value={'LICENSE.txt': b'd'}), \
                    mock.patch.object(construire, 'licence_webview2',
                                      return_value={'LICENSE.txt': b'w', 'NOTICE.txt': b'n'}), \
                    mock.patch.object(construire, 'licences_crates', return_value={}), \
                    mock.patch.object(construire, '_licences_du_paquet', side_effect=licences_du_paquet), \
                    mock.patch.object(construire, 'INNO_LICENCE_SHA256',
                                      hashlib.sha256(b'Inno Setup License\n').hexdigest()):
                construire.rassembler_licences(d / 'zip', d, versions)
                construire.rassembler_licences(d / 'inst', d, versions, iscc=d / 'Inno' / 'ISCC.exe')
            for sous in ('pywebview/LICENSE', 'pythonnet/LICENSE', 'pythonnet/AUTHORS.md', 'clr_loader/LICENSE',
                         'cffi/LICENSE', 'pycparser/LICENSE', 'bottle/LICENSE', 'typing_extensions/LICENSE',
                         'proxy_tools/LICENSE.txt', 'WebView2/LICENSE.txt', 'WebView2/NOTICE.txt',
                         'DirectML/LICENSE.txt', 'tokenizers/LICENSE', 'Python/LICENSE.txt'):
                self.assertTrue((d / 'zip' / sous).is_file(), sous)
            self.assertFalse((d / 'zip' / 'InnoSetup').exists())
            self.assertTrue((d / 'inst' / 'InnoSetup' / 'license.txt').is_file())
            lisezmoi = (d / 'zip' / 'LISEZMOI.txt').read_text(encoding='utf-8')
            for texte in ('pywebview ' + versions['pywebview'], 'Microsoft.Web.WebView2 ' +
                          construire.WEBVIEW2['version'], 'proxy_tools ' + versions['proxy_tools'],
                          'typing_extensions ' + versions['typing_extensions']):
                self.assertIn(texte, lisezmoi)
            self.assertNotIn('Inno Setup', lisezmoi)
            self.assertNotIn('{', lisezmoi)
            self.assertIn('Inno Setup ' + construire.INNO_SETUP,
                          (d / 'inst' / 'LISEZMOI.txt').read_text(encoding='utf-8'))


class TestSorties(unittest.TestCase):
    def test_dossier_refait_seulement_s_il_est_marque(self):
        with tempfile.TemporaryDirectory() as d:
            etranger = Path(d) / 'licences'
            etranger.mkdir()
            (etranger / 'a-garder.txt').write_text('x', encoding='utf-8')
            with self.assertRaises(SystemExit):
                construire.dossier_refait(etranger)
            self.assertTrue((etranger / 'a-garder.txt').exists())          # rien d'effacé

            mien = Path(d) / 'mien'
            construire.dossier_refait(mien)
            (mien / 'ancien.txt').write_text('x', encoding='utf-8')
            construire.dossier_refait(mien)
            self.assertEqual(sorted(p.name for p in mien.iterdir()), [construire.MARQUE])

    def test_fichier_sha256(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'MyPhotos.exe'
            f.write_bytes(b'MZ essai')
            h = construire.ecrire_empreinte(f)
            self.assertEqual(h, hashlib.sha256(b'MZ essai').hexdigest())
            self.assertEqual((Path(d) / 'MyPhotos.exe.sha256').read_bytes(), f'{h}  MyPhotos.exe\n'.encode('ascii'))


class TestVersionDossier(unittest.TestCase):
    """--dossier : version « dossier » (MyPhotos.exe + _internal\\), celle que pose l'installateur."""

    def test_spec_dossier(self):
        valeurs = dict(entree='lancer.py', racine='.', nom='MyPhotos', icone='x.ico', version='version.txt', datas=[],
                       caches=construire.CACHES, exclus=construire.EXCLUS, retires=sorted(construire.RETIRES),
                       dossiers=construire.RETIRES_DOSSIERS)
        texte = construire.SPEC_DOSSIER.format(**valeurs)
        compile(texte, 'MyPhotos.spec', 'exec')
        self.assertIn('exclude_binaries=True', texte)
        self.assertIn("contents_directory='_internal'", texte)
        self.assertIn('coll = COLLECT(', texte)
        # Même analyse et même tri des fichiers que l'exécutable en un seul fichier.
        un_fichier = construire.SPEC.format(**valeurs)
        self.assertEqual(texte[:texte.index('exe = EXE(')], un_fichier[:un_fichier.index('exe = EXE(')])

    def arbre(self, d, en_plus=(), sans=None):
        d = Path(d) / 'MyPhotos'
        (d / '_internal').mkdir(parents=True)
        (d / 'MyPhotos.exe').write_bytes(b'MZ')
        for n in list(construire.OBLIGATOIRES) + list(en_plus):
            if n != sans:
                f = d / '_internal' / n
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b'x')
        return d

    def test_controler_dossier(self):
        infos = {'FileDescription': 'MyPhotos', 'ProductName': 'MyPhotos', 'FileVersion': '0.3.0',
                 'ProductVersion': '0.3.0', 'LegalCopyright': construire.COPYRIGHT}
        with mock.patch.object(construire, 'lire_version', return_value=infos), mock.patch('builtins.print'):
            with tempfile.TemporaryDirectory() as d:
                self.assertGreater(construire.controler_dossier(self.arbre(d), '0.3.0'), 0)
                with self.assertRaisesRegex(SystemExit, 'Ressource de version'):
                    construire.controler_dossier(Path(d) / 'MyPhotos', '0.3.1')
                # Rien d'autre à la racine : ni profil WebView2, ni DLL posée à côté de l'exécutable.
                for etranger in ('MyPhotos.exe.WebView2', 'python314.dll'):
                    (Path(d) / 'MyPhotos' / etranger).mkdir()
                    with self.assertRaisesRegex(SystemExit, 'racine'):
                        construire.controler_dossier(Path(d) / 'MyPhotos', '0.3.0')
                    (Path(d) / 'MyPhotos' / etranger).rmdir()
            with tempfile.TemporaryDirectory() as d:
                manque = 'webview\\lib\\runtimes\\win-x64\\native\\WebView2Loader.dll'
                with self.assertRaisesRegex(SystemExit, 'manquants'):
                    construire.controler_dossier(self.arbre(d, sans=manque), '0.3.0')
            for retire in ('onnxruntime\\capi\\onnxruntime.dll', 'clr_loader\\ffi\\dlls\\x86\\ClrLoader.dll'):
                with tempfile.TemporaryDirectory() as d:
                    with self.assertRaisesRegex(SystemExit, 'en trop'):
                        construire.controler_dossier(self.arbre(d, en_plus=[retire]), '0.3.0')
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(SystemExit, 'absent'):
                    construire.controler_dossier(Path(d), '0.3.0')

    def test_options(self):
        with mock.patch.object(construire, 'construire') as c:
            construire.main(['--dossier', '--sortie', 'ailleurs'])
            c.assert_called_once_with(essai=False, sortie='ailleurs', dossier=True)
        with mock.patch.object(construire, 'construire') as c:
            construire.main([])
            c.assert_called_once_with(essai=False, sortie=None, dossier=False)
        with mock.patch.object(construire, 'installateur') as i:
            construire.main(['--installateur', '--iscc', 'ISCC.exe', '--sortie', 'ailleurs', '--cache', 'c'])
            i.assert_called_once_with('c', 'ISCC.exe', 'ailleurs')
        with mock.patch.object(construire, 'paquet') as p:
            construire.main(['--paquet'])
            p.assert_called_once_with(None)
        for mauvais in (['--sortie'], ['--dossier', '--sortie', '--essai'], ['--installateur', '--iscc']):
            with mock.patch.object(construire, 'construire'), mock.patch.object(construire, 'installateur'):
                with self.assertRaises(SystemExit, msg=mauvais):
                    construire.main(mauvais)


class TestInstallateur(unittest.TestCase):
    """construction/MyPhotos.iss et --installateur, sans Inno Setup."""

    @classmethod
    def setUpClass(cls):
        cls.brut = ISS.read_bytes()
        cls.texte = cls.brut.decode('utf-8')

    def test_encodage(self):
        self.assertFalse(self.brut.startswith(b'\xef\xbb\xbf'))       # UTF-8 sans BOM (ISCC 6.7 le lit ainsi)
        self.assertNotIn(b'\r\n', self.brut)                          # fins de ligne LF (.gitattributes)

    def test_invariants(self):
        setup = section(self.texte, 'Setup')
        self.assertIn(f'#define MyAppId "{APP_ID}"', self.texte)       # ne change jamais
        self.assertIn('AppId={{{#MyAppId}}', setup.splitlines())
        mutex = re.search(r'^#define MyAppMutex "([^"]+)"$', self.texte, re.M).group(1)
        self.assertEqual(mutex, config.MUTEX)                          # celui que crée MyPhotos
        self.assertEqual(mutex, f'MyPhotos-{APP_ID}')
        lignes = setup.splitlines()
        for attendu in ('AppMutex={#MyAppMutex}', 'PrivilegesRequired=lowest',
                        'OutputBaseFilename=MyPhotos-{#MyAppVersion}-installation', 'DefaultDirName={autopf}\\MyPhotos',
                        'AllowNoIcons=yes', 'UsePreviousAppDir=yes'):
            self.assertIn(attendu, lignes)
        self.assertFalse([x for x in lignes if x.startswith('PrivilegesRequiredOverridesAllowed')])
        # Le désinstallateur garde les données en silence ; la suppression passe par la question ou par
        # /SUPPRIMERDONNEES, et seulement pour les noms de NomDeMyPhotos.
        self.assertIn("ParametrePresent('/SUPPRIMERDONNEES')", self.texte)
        self.assertIn('MB_YESNO or MB_DEFBUTTON2', self.texte)

    def test_code_sans_ligne_qui_commence_par_un_crochet(self):
        """ISCC lit toute ligne de [Code] qui commence par « [ » comme un nom de section (« Invalid section tag »)."""
        code = section(self.texte, 'Code')
        self.assertGreater(len(code.splitlines()), 100)
        fautives = [x for x in code.splitlines() if x.lstrip().startswith('[')]
        self.assertEqual(fautives, [])

    def test_noms_de_myphotos(self):
        """Tout ce que MyPhotos range à la racine de ses données est supprimable ; rien d'autre, pas même un fichier
        de l'utilisateur au nom voisin (essai adverse de la 0.3.0 : « Journal.log.txt » partait avec journal.log.*)."""
        est = nom_de_myphotos()
        crees = [config.BASE.name, config.MINIATURES.name, config.JOURNAL.name, 'modele',
                 fenetre.PLACEMENT, fenetre.PLACEMENT + '.part', fenetre.WEBVIEW2_DOSSIER, fenetre.EDGE_DOSSIER,
                 config.JETON.name, config.JETON.name + '.part']
        crees += [config.BASE.name + s for s in ('-wal', '-shm', '-journal')]
        crees += [config.MINIATURES.name + s for s in ('-wal', '-shm', '-journal')]
        # Bases abîmées mises de côté (base.py, _ecarter_abimes), journaux WAL compris.
        self.assertIn("f'{p}.abimee-{quand}{suffixe}'", (RACINE / 'myphotos' / 'base.py').read_text(encoding='utf-8'))
        for b in (config.BASE.name, config.MINIATURES.name):
            crees += [f'{b}.abimee-20261124-101010{s}' for s in ('', '-wal', '-shm')]
        # Copies du journal : serveur.JOURNAL_COPIES (RotatingFileHandler).
        copies = int(re.search(r'^JOURNAL_COPIES = (\d+)', (RACINE / 'myphotos' / 'serveur.py').read_text(
            encoding='utf-8'), re.M).group(1))
        crees += [f'{config.JOURNAL.name}.{i}' for i in range(1, copies + 1)] + ['INDEX.DB', 'Edge']
        # modele\ : config.MODELES vaut DONNEES / 'modele' sans MYPHOTOS_MODELES (les essais le définissent).
        self.assertIn("else DONNEES / 'modele'", (RACINE / 'myphotos' / 'config.py').read_text(encoding='utf-8'))
        for nom in crees:
            self.assertTrue(est(nom), nom)
        for nom in ('a-garder.txt', 'Images', 'photos', 'index.dbx', 'index', 'desktop.ini', 'webview2.txt',
                    'fenetre', 'journal', 'modeles', 'MyPhotos.exe', 'edge2', 'Journal.log.txt',
                    f'journal.log.{copies + 1}', 'index.db.sauvegarde', 'index.db-copie', 'miniatures.db.old',
                    'fenetre.json.bak', 'jeton.txt', 'jetons'):
            self.assertFalse(est(nom), nom)

    def test_installdelete_seulement_dans_un_dossier_de_myphotos(self):
        """[InstallDelete] ne vide « licences » et « _internal » que dans le dossier d'une installation de MyPhotos
        (essai adverse de la 0.3.0 : /DIR vers un dossier de l'utilisateur vidait son sous-dossier « licences »).
        La décision est prise une fois, avant toute suppression (PrepareToInstall)."""
        lignes = [x for x in section(self.texte, 'InstallDelete').splitlines() if x and not x.startswith(';')]
        self.assertEqual(len(lignes), 2)
        for x in lignes:
            self.assertTrue(x.endswith('; Check: InstallationDeMyPhotos'), x)
        code = section(self.texte, 'Code')
        fonction = code[code.index('function InstallationDeMyPhotos'):code.index('function PrepareToInstall')]
        self.assertIn('if not DossierDeMyPhotosEvalue then', fonction)
        self.assertIn("FileExists(App + '\\MyPhotos.exe') and DirExists(App + '\\_internal')", fonction)
        preparer = code[code.index('function PrepareToInstall'):]
        self.assertIn('InstallationDeMyPhotos();', preparer[:preparer.index('\nend;')])

    def test_contenu_du_moteur(self):
        """modele\\ ne part que s'il ne contient que des fichiers du moteur : chaque fichier que MyPhotos y pose
        (manifestes de modele.py, témoins .ok, .part d'un téléchargement, .copie d'une recopie) est reconnu ; une
        photo ou un document de l'utilisateur ne l'est pas, et le dossier reste."""
        from myphotos import modele
        code = section(self.texte, 'Code')
        corps = code[code.index('function FichierDuMoteur'):]
        corps = corps[:corps.index('end;')]
        extensions = set(re.findall(r"\(E = '(\.[a-z_]+)'\)", corps))
        self.assertTrue(extensions)

        def du_moteur(nom):
            return os.path.splitext(nom)[1].lower() in extensions
        noms = set()
        for cle, m in modele.MODELES_DISPO.items():
            for nom in m['fichiers']:
                noms.update({nom, nom + '.ok', nom + '.part', nom + '.copie'})
        self.assertGreater(len(noms), 8)
        for nom in noms:
            self.assertTrue(du_moteur(os.path.basename(nom)), nom)
        self.assertIn("dest.with_name(dest.name + '.copie')", (RACINE / 'myphotos' / 'serveur.py').read_text(
            encoding='utf-8'))
        for nom in ('IMG_0001.JPG', 'photo.jpeg', 'Vacances.png', 'modele.dotx', 'lettre.docx', 'notes.txt', 'x.bin',
                    'sans-extension', 'video.mp4'):
            self.assertFalse(du_moteur(nom), nom)
        # webview2\ : seulement EBWebView ; edge\ : un profil d'Edge (« Local State ») ; vide : supprimé.
        contenu = code[code.index('function ContenuDeMyPhotos'):]
        contenu = contenu[:contenu.index('\nend;')]
        self.assertIn("SeulementLeSousDossier(Chemin, 'EBWebView')", contenu)
        self.assertIn("FichierOrdinaire(Chemin + '\\Local State')", contenu)
        supprimer = code[code.index('function SupprimerDonnees'):]
        self.assertIn('not ContenuDeMyPhotos(Chemin, Noms[i])', supprimer[:supprimer.index('\nend;')])

    @unittest.skipUnless(os.name == 'nt', 'Windows')
    def test_noms_d_une_instance_neuve(self):
        """Vrai serveur d'essai (MYPHOTOS_SANS_PAGE, données vides, aucun dossier proposé), dans un Job Object :
        tout ce qu'il crée à la racine de ses données est dans NomDeMyPhotos. (Fenêtre : fenetre.json,
        webview2\\ et edge\\, vérifiés plus haut par leurs constantes, et par l'essai réel de l'installateur.)"""
        import outils_fenetre as O
        import processus_essai
        port = int(os.environ.get('MYPHOTOS_ESSAI_PORT_NOMS') or 7946)
        if mutex_existe():
            self.skipTest('une MyPhotos 0.3 (ou plus) tourne déjà sur cet ordinateur')
        if not port_libre(port):
            self.skipTest(f'port {port} déjà pris')
        base = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
        donnees = base / f'essai-noms-{time.strftime("%Y%m%d-%H%M%S")}'
        donnees.mkdir(parents=True)
        O.donnees_du_port(port, donnees)                 # jeton d'accès lu dans ses données
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYPHOTOS_', 'WEBVIEW2_'))}
        env.update(MYPHOTOS_PORT=str(port), MYPHOTOS_DOSSIER=str(donnees), MYPHOTOS_SANS_PAGE='1',
                   PYTHONIOENCODING='utf-8')            # sans MYPHOTOS_MODELES : modele\ dans les données
        code = ('import sys; import myphotos.serveur as s; s.dossiers_proposes = lambda: []; '
                's.dossier_images = lambda: None; sys.exit(s.main())')
        p = processus_essai.popen(processus_essai.Job(), [sys.executable, '-c', code], cwd=str(RACINE), env=env,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            self.assertTrue(O.attendre(lambda: O.requete_json(port, '/api/etat')[0] == 200, 60), 'serveur absent')
            pendant = sorted(x.name for x in donnees.iterdir())
            self.assertEqual(O.requete_json(port, '/api/quitter', {})[0], 200)
            self.assertEqual(p.wait(60), 0)
        finally:
            processus_essai.arreter(p)
        apres = sorted(x.name for x in donnees.iterdir())
        self.assertIn('index.db', pendant)
        est = nom_de_myphotos()
        self.assertEqual([n for n in set(pendant) | set(apres) if not est(n)], [])

    def test_chemin_le_plus_long(self):
        """Le nom provisoire sous lequel Inno Setup copie chaque fichier (« is-XXXXXXXXXX.tmp », à côté de lui) est
        compté : dans un dossier profond, c'est lui qui atteint la limite des 259 caractères, pas le fichier (essai
        adverse de la 0.3.0 : un dossier d'installation accepté échouait au milieu de la copie)."""
        self.assertEqual(construire.PROVISOIRE_INNO, 17)
        with tempfile.TemporaryDirectory() as d:
            app, lic = Path(d) / 'app', Path(d) / 'lic'
            profond = app / '_internal' / 'abcdefghij' / 'klmnopqrst' / 'uvwxyzabcd'
            profond.mkdir(parents=True)
            (profond / 'e').write_bytes(b'x')                                      # 44 caractères, dossier de 42
            (app / '_internal' / ('n' * 40 + '.txt')).write_bytes(b'x')            # 54 caractères
            (app / 'MyPhotos.exe').write_bytes(b'MZ')
            lic.mkdir()
            (lic / 'LISEZMOI.txt').write_text('x', encoding='utf-8')
            (lic / construire.MARQUE).write_text('x', encoding='utf-8')
            self.assertEqual(construire.chemin_le_plus_long(app, lic), 42 + 1 + 17)
        self.assertIn('Length(App) + 1 + {#CheminLePlusLong} > 259', self.texte)

    def test_trouver_iscc(self):
        with tempfile.TemporaryDirectory() as d:
            iscc = Path(d) / 'Programs' / 'Inno Setup 6' / 'ISCC.exe'
            iscc.parent.mkdir(parents=True)
            iscc.write_bytes(b'MZ')
            vide = {'LOCALAPPDATA': str(Path(d) / 'rien'), 'ProgramFiles': str(Path(d) / 'rien'),
                    'ProgramFiles(x86)': str(Path(d) / 'rien'), 'MYPHOTOS_ISCC': ''}
            with mock.patch.dict('os.environ', dict(vide, LOCALAPPDATA=d)):
                self.assertEqual(construire.trouver_iscc(), iscc)                      # pour l'utilisateur seul
            with mock.patch.dict('os.environ', dict(vide, MYPHOTOS_ISCC=str(iscc))):
                self.assertEqual(construire.trouver_iscc(), iscc)
            with mock.patch.dict('os.environ', vide):
                self.assertEqual(construire.trouver_iscc(str(iscc)), iscc)
                with self.assertRaisesRegex(SystemExit, 'Inno Setup 6 est absent'):
                    construire.trouver_iscc()
                with self.assertRaisesRegex(SystemExit, 'introuvable'):
                    construire.trouver_iscc(str(Path(d) / 'absent.exe'))

    def test_version_iscc(self):
        with tempfile.TemporaryDirectory() as d:
            j = Path(d) / 'iscc.log'
            j.write_text('Inno Setup 6 Command-Line Compiler\nCompiler engine version: Inno Setup 6.7.4\n',
                         encoding='utf-8')
            self.assertEqual(construire.version_iscc(j), construire.INNO_SETUP)
            j.write_text('Compiler engine version: Inno Setup 7.1.1\n', encoding='utf-8')
            with mock.patch('builtins.print') as p:
                self.assertEqual(construire.version_iscc(j), '7.1.1')
            self.assertIn('Attention', p.call_args[0][0])

    def test_installateur_sans_version_dossier(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(SystemExit, '--dossier'):
                construire.installateur(sortie=d)


# ---------------------------------------------------------------- essai réel de l'installateur (à la demande)

def _cle_desinstallation():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CLE_DESINSTALLATION) as k:
            return winreg.QueryValueEx(k, 'InstallLocation')[0]
    except FileNotFoundError:
        return None


@unittest.skipUnless(os.environ.get('MYPHOTOS_ESSAI_INSTALLATEUR') == '1' and os.name == 'nt',
                     'essai réel de l\'installateur : MYPHOTOS_ESSAI_INSTALLATEUR=1 (Windows, Inno Setup 6)')
class TestInstallateurReel(unittest.TestCase):
    """Compilation, installation silencieuse dans un dossier d'essai, mutex, mise à jour, désinstallations."""

    @classmethod
    def setUpClass(cls):
        import outils_fenetre as O
        import processus_essai
        cls.O, cls.P = O, processus_essai
        try:
            cls.iscc = construire.trouver_iscc()
        except SystemExit:
            raise unittest.SkipTest('Inno Setup 6 absent (ISCC.exe)')
        cls.app = Path(os.environ.get('MYPHOTOS_ESSAI_DOSSIER_APP') or RACINE / 'dist' / 'MyPhotos')
        if not (cls.app / 'MyPhotos.exe').is_file():
            raise unittest.SkipTest(f'version dossier absente : {cls.app} (construire.ps1 --dossier)')
        if _cle_desinstallation() is not None:
            raise unittest.SkipTest('MyPhotos est installé sur cet ordinateur : essai annulé (dossier précédent)')
        if mutex_existe():
            raise unittest.SkipTest('une MyPhotos 0.3 (ou plus) tourne déjà sur cet ordinateur')
        base = Path(os.environ.get('MYPHOTOS_ESSAI_UI') or Path(tempfile.gettempdir()) / 'myphotos-essai-ui')
        cls.d = base / f'inst-{time.strftime("%H%M%S")}'         # court : chemins de _internal jusqu'à ~90
        cls.temp = cls.d / 'temp'
        cls.temp.mkdir(parents=True)
        cls.inst = cls.d / 'i' / 'MyPhotos'
        licences = cls.d / 'licences'
        licences.mkdir()
        (licences / 'LISEZMOI.txt').write_text('essai\n', encoding='utf-8')
        cls.installeur = cls.d / f'MyPhotos-{config.VERSION}-installation.exe'
        journal = cls.d / 'iscc.log'
        cls.long = construire.chemin_le_plus_long(cls.app, licences)
        if len(str(cls.d / 'logiciels')) + 1 + cls.long > 259:
            raise unittest.SkipTest(f'MYPHOTOS_ESSAI_UI trop long pour l\'essai réel : {base} (les chemins installés '
                                    'dépasseraient 259 caractères)')
        with open(journal, 'wb') as f:
            r = subprocess.run([str(cls.iscc), f'/DMyAppVersion={config.VERSION}', f'/DDossierApp={cls.app}',
                                f'/DDossierLicences={licences}', f'/DSortie={cls.d}',
                                f'/DCheminLePlusLong={cls.long}', str(ISS)],
                               cwd=str(ISS.parent), stdout=f, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert r.returncode == 0 and cls.installeur.is_file(), journal.read_text(encoding='utf-8', errors='replace')
        cls.bureau = f'MyPhotosInstallateur{os.getpid()}'
        cls.hbureau = O.creer_bureau(cls.bureau)
        cls.raccourcis_avant = cls.raccourcis()

    @staticmethod
    def raccourcis():
        """Raccourcis MyPhotos.lnk du Bureau et du menu Démarrer de l'utilisateur (lus, jamais écrits)."""
        from myphotos import raccourci
        return [str(p) for p in raccourci.emplacements().values() if Path(p).exists()]

    @classmethod
    def tearDownClass(cls):
        cls.O.fermer_bureau(cls.hbureau)

    def executer(self, arguments, **env_plus):
        """Installateur ou désinstallateur, sur le bureau invisible, dans un Job Object à lui : code de sortie."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('MYPHOTOS_', 'WEBVIEW2_'))}
        env.update(TEMP=str(self.temp), TMP=str(self.temp), **{k: str(v) for k, v in env_plus.items()})
        job = self.P.Job()
        pid, h = self.O.lancer([str(a) for a in arguments], self.bureau, str(self.d), env, job)
        try:
            self.assertTrue(self.O.attendre_processus(h, 120), arguments)
            self.assertTrue(job.attendre_vide(60), arguments)
            return self.O.code_sortie(h)
        finally:
            job.terminer()                       # filet : seulement les processus de CE job
            job.fermer()
            self.O.fermer_handle(h)

    def installer(self, *plus):
        return self.executer([self.installeur, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS',
                              '/TASKS=', f'/DIR={self.inst}', *plus])

    def desinstaller(self, donnees, *plus):
        return self.executer([self.inst / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', *plus],
                             MYPHOTOS_DOSSIER=donnees)

    def test_cycle_complet(self):
        try:
            self._cycle()
        finally:
            if _cle_desinstallation() is not None and (self.inst / 'unins000.exe').exists():
                self.desinstaller(self.d / 'aucune')
            self.assertIsNone(_cle_desinstallation())
            self.assertEqual(self.raccourcis(), self.raccourcis_avant)

    def _cycle(self):
        # Dossier d'installation au chemin trop long : refusé avant toute copie (code 7 : préparation refusée).
        trop_long = self.d / ('x' * (259 - self.long - len(str(self.d))))
        self.assertGreater(len(str(trop_long)) + 1 + self.long, 259)
        self.assertEqual(self.executer([self.installeur, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                        '/NOICONS', '/TASKS=', f'/DIR={trop_long}']), 7)
        self.assertFalse(trop_long.exists())
        self.assertIsNone(_cle_desinstallation())

        # Juste à la limite acceptée, l'installation va au bout (essai adverse de la 0.3.0 : le nom provisoire sous
        # lequel Inno Setup copie chaque fichier n'était pas compté ; la copie échouait au milieu, code 5).
        limite = self.d / ('y' * (257 - self.long - len(str(self.d))))
        self.assertEqual(len(str(limite)) + 1 + self.long, 259)
        self.assertEqual(self.executer([self.installeur, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                        '/NOICONS', '/TASKS=', f'/DIR={limite}']), 0)
        self.assertTrue((limite / 'MyPhotos.exe').is_file())
        self.assertEqual(self.executer([limite / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                                       MYPHOTOS_DOSSIER=self.d / 'aucune'), 0)
        self.assertIsNone(_cle_desinstallation())

        # Dossier existant de l'utilisateur choisi par /DIR : son sous-dossier « licences » n'est jamais vidé, ni à
        # l'installation ni à la désinstallation (essai adverse de la 0.3.0 : [InstallDelete] le vidait).
        logiciels = self.d / 'logiciels'
        (logiciels / 'licences').mkdir(parents=True)
        (logiciels / 'licences' / 'cle.txt').write_text('clé de licence de l\'utilisateur', encoding='utf-8')
        (logiciels / 'notes.txt').write_text('notes', encoding='utf-8')
        self.assertEqual(self.executer([self.installeur, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOICONS',
                                        '/TASKS=', f'/DIR={logiciels}']), 0)
        self.assertTrue((logiciels / 'MyPhotos.exe').is_file())
        self.assertTrue((logiciels / 'licences' / 'cle.txt').is_file())
        self.assertEqual(self.executer([logiciels / 'unins000.exe', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                                       MYPHOTOS_DOSSIER=self.d / 'aucune'), 0)
        self.assertEqual(sorted(p.relative_to(logiciels).as_posix() for p in logiciels.rglob('*')),
                         ['licences', 'licences/cle.txt', 'notes.txt'])
        self.assertIsNone(_cle_desinstallation())

        # Installation : dans le dossier d'essai, sans raccourci, entrée de « Applications installées ».
        self.assertEqual(self.installer(), 0)
        self.assertEqual(Path(_cle_desinstallation()), self.inst)
        racine = sorted(p.name for p in self.inst.iterdir())
        self.assertEqual(racine, sorted(['LICENSE', 'MyPhotos.exe', 'MyPhotos_mode_emploi.pdf',
                                         'THIRD_PARTY_NOTICES.md', '_internal', 'licences', 'unins000.dat',
                                         'unins000.exe']))
        self.assertEqual(self.raccourcis(), self.raccourcis_avant)

        # MyPhotos ouvert (mutex config.MUTEX tenu) : installation et désinstallation refusées, rien n'est touché.
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.CreateMutexW(None, False, config.MUTEX)
        try:
            avant = (self.inst / 'MyPhotos.exe').stat().st_mtime_ns
            self.assertNotEqual(self.installer(), 0)
            self.assertNotEqual(self.desinstaller(self.d / 'aucune'), 0)
            self.assertEqual((self.inst / 'MyPhotos.exe').stat().st_mtime_ns, avant)
            self.assertIsNotNone(_cle_desinstallation())
        finally:
            k32.CloseHandle(h)

        # Mise à jour par-dessus : les fichiers de l'ancienne version sont retirés.
        (self.inst / '_internal' / 'ancien.txt').write_text('x', encoding='utf-8')
        (self.inst / 'licences' / 'ancienne-licence.txt').write_text('x', encoding='utf-8')
        self.assertEqual(self.executer([self.installeur, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                        '/NOICONS', '/TASKS=']), 0)            # sans /DIR : dossier repris
        self.assertFalse((self.inst / '_internal' / 'ancien.txt').exists())
        self.assertFalse((self.inst / 'licences' / 'ancienne-licence.txt').exists())
        self.assertEqual(Path(_cle_desinstallation()), self.inst)

        # Désinstallation silencieuse : données gardées. Contenu réaliste : moteur, profils WebView2 et Edge, base
        # abîmée mise de côté ; et des fichiers de l'utilisateur aux noms voisins.
        donnees = self.d / 'donnees'
        donnees.mkdir()
        noms = ['index.db', 'index.db-wal', 'miniatures.db', 'journal.log', 'journal.log.1', 'fenetre.json',
                'index.db.abimee-20261124-101010', 'a-garder.txt', 'Journal.log.txt', 'index.db.sauvegarde']
        for n in noms:
            (donnees / n).write_text('x', encoding='utf-8')
        (donnees / 'modele' / 'base' / 'onnx').mkdir(parents=True)
        for n in ('onnx/vision_model_fp16.onnx', 'onnx/vision_model_fp16.onnx.ok', 'tokenizer.json',
                  'tokenizer.json.ok', 'onnx/text_model_fp16.onnx.part'):
            (donnees / 'modele' / 'base' / n).write_bytes(b'x')
        (donnees / 'webview2' / 'EBWebView' / 'Default').mkdir(parents=True)
        (donnees / 'webview2' / 'EBWebView' / 'Default' / 'f.bin').write_bytes(b'x')
        (donnees / 'edge' / 'Default' / 'Extensions').mkdir(parents=True)
        (donnees / 'edge' / 'Local State').write_text('{}', encoding='utf-8')
        (donnees / 'edge' / 'Default' / 'Extensions' / '128.png').write_bytes(b'x')
        tout = sorted(p.name for p in donnees.iterdir())
        self.assertEqual(self.desinstaller(donnees), 0)
        self.assertIsNone(_cle_desinstallation())
        self.assertFalse(self.inst.exists())
        self.assertEqual(sorted(p.name for p in donnees.iterdir()), tout)

        # Dossier protégé (ici TEMP) : rien n'est supprimé, même avec /SUPPRIMERDONNEES.
        (self.temp / 'index.db').write_text('x', encoding='utf-8')
        self.assertEqual(self.installer(), 0)
        self.assertEqual(self.desinstaller(self.temp, '/SUPPRIMERDONNEES'), 0)
        self.assertTrue((self.temp / 'index.db').exists())

        # /SUPPRIMERDONNEES : seuls les éléments de MyPhotos partent ; le reste, et donc le dossier, restent.
        self.assertEqual(self.installer(), 0)
        self.assertEqual(self.desinstaller(donnees, '/SUPPRIMERDONNEES'), 0)
        self.assertEqual(sorted(p.name for p in donnees.iterdir()),
                         ['Journal.log.txt', 'a-garder.txt', 'index.db.sauvegarde'])
        self.assertIsNone(_cle_desinstallation())

        # Un dossier au nom de MyPhotos (modele, Edge, WebView2) qui contient autre chose, comme des photos rangées
        # là par l'utilisateur, reste en entier (essai adverse de la 0.3.0 : il partait, photos comprises).
        partage = self.d / 'partage'
        (partage / 'modele' / 'base' / 'onnx').mkdir(parents=True)
        (partage / 'modele' / 'base' / 'onnx' / 'vision_model_fp16.onnx').write_bytes(b'x')
        (partage / 'modele' / 'Mannequin').mkdir()
        (partage / 'modele' / 'Mannequin' / 'IMG_0001.JPG').write_bytes(b'\xff\xd8\xff')
        (partage / 'Edge').mkdir()
        (partage / 'Edge' / 'vacances.jpg').write_bytes(b'\xff\xd8\xff')
        (partage / 'WebView2').mkdir()
        (partage / 'WebView2' / 'notes.txt').write_text('x', encoding='utf-8')
        (partage / 'index.db').write_text('x', encoding='utf-8')
        self.assertEqual(self.installer(), 0)
        self.assertEqual(self.desinstaller(partage, '/SUPPRIMERDONNEES'), 0)
        self.assertEqual(sorted(p.relative_to(partage).as_posix() for p in partage.rglob('*') if p.is_file()),
                         ['Edge/vacances.jpg', 'WebView2/notes.txt', 'modele/Mannequin/IMG_0001.JPG',
                          'modele/base/onnx/vision_model_fp16.onnx'])
        self.assertIsNone(_cle_desinstallation())


if __name__ == '__main__':
    unittest.main()
