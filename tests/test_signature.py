"""Tests de myphotos/signature.py : signature des mises à jour (ECDSA P-256 par la cryptographie de Windows).

    .venv\\Scripts\\python.exe -m unittest tests.test_signature -v

Aucune clé du projet n'est utilisée : chaque essai fabrique ses propres clés.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from myphotos import config, signature  # noqa: E402


@unittest.skipUnless(os.name == 'nt', 'CNG (bcrypt.dll) : Windows seulement')
class Signature(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.privee, cls.publique = signature.nouvelle_cle()
        cls.texte = '{"application": "MyPhotos", "version": "0.4.0"}'.encode()
        cls.sig = signature.signer(cls.texte, cls.privee)

    def test_tailles(self):
        self.assertEqual((len(self.privee), len(self.publique), len(self.sig)), (96, 64, 64))
        self.assertEqual(self.privee[:64], self.publique)

    def test_signature_valide(self):
        self.assertTrue(signature.verifier(self.texte, self.sig, self.publique))
        self.assertTrue(signature.verifier(bytearray(self.texte), bytearray(self.sig), bytearray(self.publique)))

    def test_texte_modifie(self):
        self.assertFalse(signature.verifier(self.texte + b' ', self.sig, self.publique))
        self.assertFalse(signature.verifier(self.texte.replace(b'0.4.0', b'9.9.9'), self.sig, self.publique))

    def test_signature_abimee(self):
        for i in (0, 31, 32, 63):
            abimee = bytearray(self.sig)
            abimee[i] ^= 1
            self.assertFalse(signature.verifier(self.texte, bytes(abimee), self.publique), i)

    def test_autre_cle(self):
        _, autre = signature.nouvelle_cle()
        self.assertFalse(signature.verifier(self.texte, self.sig, autre))

    def test_donnees_invalides_sans_exception(self):
        for sig, cle in ((self.sig[:63], self.publique), (self.sig + b'\0', self.publique), (b'', self.publique),
                         (None, self.publique), ('texte', self.publique), (self.sig, self.publique[:63]),
                         (self.sig, b'\x01' * 64), (self.sig, b'\0' * 64), (b'\0' * 64, self.publique),
                         (b'\xff' * 64, self.publique)):
            self.assertFalse(signature.verifier(self.texte, sig, cle))

    def test_prefixe_propre_a_myphotos(self):
        # Une signature ECDSA faite sur le texte nu (sans le préfixe de MyPhotos) n'est pas acceptée.
        with mock.patch.object(signature, 'DOMAINE', b''):
            brut = signature.signer(self.texte, self.privee)
            self.assertTrue(signature.verifier(self.texte, brut, self.publique))
        self.assertFalse(signature.verifier(self.texte, brut, self.publique))

    def test_signatures_differentes_meme_texte(self):
        self.assertNotEqual(signature.signer(self.texte, self.privee), self.sig)

    def test_cle_privee_de_mauvaise_taille(self):
        with self.assertRaises(ValueError):
            signature.signer(self.texte, self.privee[:95])

    def test_cle_publique_du_projet(self):
        # MyPhotos embarque une clé publique de 64 octets (X puis Y), valide : elle s'importe sans erreur.
        cle = config.cle_mise_a_jour()
        self.assertEqual(len(cle), 64)
        self.assertFalse(signature.verifier(self.texte, self.sig, cle))   # signé par une autre clé : refusé


class HorsWindows(unittest.TestCase):

    def test_indisponible_hors_windows(self):
        if os.name == 'nt':
            self.skipTest('Windows : bcrypt.dll présent')
        with self.assertRaises(signature.SignatureIndisponible):
            signature.verifier(b'x', b'\0' * 64, b'\0' * 64)


if __name__ == '__main__':
    unittest.main()
