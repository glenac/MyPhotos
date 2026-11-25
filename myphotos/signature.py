"""Signature des mises à jour de MyPhotos : ECDSA P-256 avec SHA-256, par la cryptographie de Windows (CNG, bcrypt.dll).

Chaque version publiée est accompagnée d'un manifeste (version, nom, taille et empreinte de l'installateur) signé avec
la clé privée du projet. Cette clé reste sur le PC qui publie, jamais dans le dépôt ni sur GitHub. MyPhotos contient
la clé publique (config.CLE_MISE_A_JOUR) et refuse toute mise à jour dont le manifeste n'est pas signé par elle : un
compte GitHub volé ne suffit donc pas à envoyer un faux MyPhotos.

Rien à installer : bcrypt.dll fait partie de Windows (Vista et suivants). Hors de Windows, verifier() lève
SignatureIndisponible (la mise à jour en un clic est alors simplement indisponible).

Formats :
- clé publique : 64 octets, X puis Y (grand-boutiste), comme dans le blob ECCPUBLICBLOB de CNG sans son en-tête ;
- clé privée : 96 octets, X, Y puis d (blob ECCPRIVATEBLOB sans son en-tête) ;
- signature : 64 octets, r puis s.
Ce qui est signé n'est jamais le texte nu : un préfixe propre à MyPhotos (DOMAINE) le précède, pour qu'une signature
faite pour autre chose ne puisse pas servir ici.
"""
import ctypes
import hashlib
import os
from ctypes import wintypes

DOMAINE = b'MyPhotos mise a jour v1\n'
TAILLE_CLE = 32                                  # P-256 : coordonnées et scalaire de 32 octets
_MAGIE_PUBLIQUE = 0x31534345                     # BCRYPT_ECDSA_PUBLIC_P256_MAGIC (« ECS1 »)
_MAGIE_PRIVEE = 0x32534345                       # BCRYPT_ECDSA_PRIVATE_P256_MAGIC (« ECS2 »)
_STATUS_INVALID_SIGNATURE = 0xC000A000


class SignatureIndisponible(Exception):
    """Vérification impossible sur ce système (hors Windows, ou bcrypt.dll absent)."""


_bcrypt = None


def _api():
    global _bcrypt
    if _bcrypt is not None:
        return _bcrypt
    if os.name != 'nt':
        raise SignatureIndisponible('la vérification des signatures demande Windows')
    try:
        b = ctypes.WinDLL('bcrypt.dll')
    except OSError as e:
        raise SignatureIndisponible(f'bcrypt.dll introuvable ({e})') from None
    H = wintypes.HANDLE
    P = ctypes.c_void_p
    U = wintypes.ULONG
    for nom, args in (
            ('BCryptOpenAlgorithmProvider', (ctypes.POINTER(H), wintypes.LPCWSTR, wintypes.LPCWSTR, U)),
            ('BCryptCloseAlgorithmProvider', (H, U)),
            ('BCryptGenerateKeyPair', (H, ctypes.POINTER(H), U, U)),
            ('BCryptFinalizeKeyPair', (H, U)),
            ('BCryptExportKey', (H, H, wintypes.LPCWSTR, P, U, ctypes.POINTER(U), U)),
            ('BCryptImportKeyPair', (H, H, wintypes.LPCWSTR, ctypes.POINTER(H), P, U, U)),
            ('BCryptSignHash', (H, P, P, U, P, U, ctypes.POINTER(U), U)),
            ('BCryptVerifySignature', (H, P, P, U, P, U, U)),
            ('BCryptDestroyKey', (H,))):
        f = getattr(b, nom)
        f.argtypes = args
        f.restype = ctypes.c_long                # NTSTATUS
    _bcrypt = b
    return b


def _verifier_statut(statut, quoi):
    if statut != 0:
        raise OSError(f'{quoi} : erreur CNG 0x{statut & 0xFFFFFFFF:08X}')


class _Algo:
    """Fournisseur ECDSA P-256, refermé à la sortie du bloc with."""

    def __enter__(self):
        self.b = _api()
        self.h = wintypes.HANDLE()
        _verifier_statut(self.b.BCryptOpenAlgorithmProvider(ctypes.byref(self.h), 'ECDSA_P256', None, 0),
                         'ouverture de ECDSA_P256')
        return self

    def __exit__(self, *_):
        self.b.BCryptCloseAlgorithmProvider(self.h, 0)

    def importer(self, type_blob, blob):
        cle = wintypes.HANDLE()
        tampon = ctypes.create_string_buffer(blob, len(blob))
        _verifier_statut(self.b.BCryptImportKeyPair(self.h, None, type_blob, ctypes.byref(cle), tampon, len(blob), 0),
                         'clé refusée')
        return cle


def _blob(magie, octets):
    return magie.to_bytes(4, 'little') + TAILLE_CLE.to_bytes(4, 'little') + octets


def _condense(donnees):
    return hashlib.sha256(DOMAINE + donnees).digest()


def verifier(donnees, signature, cle_publique):
    """True si signature (64 octets) est une signature valide de donnees par cle_publique (64 octets), False sinon.

    Une signature ou une clé de mauvaise taille donne False (jamais d'exception pour une donnée venue d'Internet) ;
    SignatureIndisponible seulement si ce système ne sait pas vérifier.
    """
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 2 * TAILLE_CLE:
        return False
    if not isinstance(cle_publique, (bytes, bytearray)) or len(cle_publique) != 2 * TAILLE_CLE:
        return False
    with _Algo() as a:
        try:
            cle = a.importer('ECCPUBLICBLOB', _blob(_MAGIE_PUBLIQUE, bytes(cle_publique)))
        except OSError:
            return False
        try:
            h = _condense(bytes(donnees))
            sig = bytes(signature)
            statut = a.b.BCryptVerifySignature(cle, None, ctypes.create_string_buffer(h, len(h)), len(h),
                                               ctypes.create_string_buffer(sig, len(sig)), len(sig), 0)
            return statut == 0
        finally:
            a.b.BCryptDestroyKey(cle)


# ---------------------------------------------------------------- côté publication (construction/), jamais à l'usage

def nouvelle_cle():
    """Nouvelle paire de clés : (privée 96 octets, publique 64 octets)."""
    with _Algo() as a:
        cle = wintypes.HANDLE()
        _verifier_statut(a.b.BCryptGenerateKeyPair(a.h, ctypes.byref(cle), 256, 0), 'génération de la clé')
        try:
            _verifier_statut(a.b.BCryptFinalizeKeyPair(cle, 0), 'génération de la clé')
            taille = wintypes.ULONG()
            tampon = ctypes.create_string_buffer(8 + 3 * TAILLE_CLE)
            _verifier_statut(a.b.BCryptExportKey(cle, None, 'ECCPRIVATEBLOB', tampon, len(tampon),
                                                 ctypes.byref(taille), 0), 'export de la clé')
            brut = tampon.raw[:taille.value]
        finally:
            a.b.BCryptDestroyKey(cle)
    if len(brut) != 8 + 3 * TAILLE_CLE or int.from_bytes(brut[:4], 'little') != _MAGIE_PRIVEE:
        raise OSError('clé générée inattendue')
    privee = brut[8:]
    return privee, privee[:2 * TAILLE_CLE]


def signer(donnees, cle_privee):
    """Signature (64 octets) de donnees par cle_privee (96 octets : X, Y, d)."""
    if len(cle_privee) != 3 * TAILLE_CLE:
        raise ValueError('clé privée de signature : 96 octets attendus')
    with _Algo() as a:
        cle = a.importer('ECCPRIVATEBLOB', _blob(_MAGIE_PRIVEE, bytes(cle_privee)))
        try:
            h = _condense(bytes(donnees))
            sortie = ctypes.create_string_buffer(2 * TAILLE_CLE)
            taille = wintypes.ULONG()
            _verifier_statut(a.b.BCryptSignHash(cle, None, ctypes.create_string_buffer(h, len(h)), len(h), sortie,
                                                len(sortie), ctypes.byref(taille), 0), 'signature')
            return sortie.raw[:taille.value]
        finally:
            a.b.BCryptDestroyKey(cle)
