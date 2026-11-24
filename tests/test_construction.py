"""Essais de construction/construire.py (préparation de la publication, --paquet), sans réseau ni PyInstaller.

    .venv\\Scripts\\python.exe -m unittest tests.test_construction

Les téléchargements passent par des adresses file:// ; un paquet crates.io est fabriqué sur place, avec des
membres piégés (chemin qui remonte, deux-points, lien, autre racine) qui ne doivent jamais être recopiés.
"""
import base64
import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / 'construction'))

import construire  # noqa: E402
from myphotos import config  # noqa: E402


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


if __name__ == '__main__':
    unittest.main()
