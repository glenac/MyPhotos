#!/usr/bin/env python3
r"""Garde-fou de publication du dépôt MyPhotos.

Avant que des fichiers partent sur GitHub, ce script cherche :

  a) des données personnelles et des secrets : chemins de profil
     (C:\Users\<nom>\, /Users/<nom>/, /home/<nom>/), chemins de lecteur vers un
     dossier de photos, adresses e-mail, jetons et clés d'API, clés privées,
     mots de passe écrits en clair, identifiants dans une URL, adresses IP ;
  b) les mots interdits propres à l'utilisateur, lus dans
     .git/info/publication-interdits.txt. Ce fichier reste sur la machine :
     git ne publie jamais le contenu du dossier .git ;
  c) les fichiers à ne pas publier : plus de 2 Mo, bases de données, modèles,
     archives, exécutables, fichiers de clés, et les images ou vidéos absentes
     de .publication-autorises.

Utilisation, depuis le dépôt :

    python outils/verifier_publication.py               tout ce qui serait publié
    python outils/verifier_publication.py --index       ce qui va être commité
    python outils/verifier_publication.py --historique  tous les commits (avant un premier push)

Code de retour : 0 sans alerte, 1 avec au moins une alerte, 2 en cas d'erreur.
Les valeurs trouvées sont toujours masquées dans le rapport.

Le crochet .githooks/pre-commit lance le mode --index avant chaque commit.
Pour l'activer, une fois par clone :  git config core.hooksPath .githooks

Faux positifs :
  - une ligne qui contient le marqueur « publication: ok » est ignorée ;
  - .publication-autorises (publié, donc SANS donnée personnelle) contient :
      * les fichiers autorisés malgré leur type (images, vidéos…), un motif glob
        par ligne, par exemple docs/captures/*.png (« * » traverse les dossiers) ;
      * des valeurs d'exemple génériques, une par ligne, après « exemple: ».
    Ce fichier ne lève jamais un mot interdit, ni la limite de 2 Mo.

Format de .git/info/publication-interdits.txt : un motif par ligne, « # » en
début de ligne pour un commentaire. La comparaison ignore la casse et les
accents, et ne distingue pas « \ » de « / ». Préfixes possibles :
« mot: » (mot entier seulement), « re: » (expression régulière, appliquée au
texte mis en minuscules, sans accents, avec « / » comme seul séparateur) et
« ! » (expression autorisée, retirée du texte avant la recherche : par exemple
le nom d'un photographe public qui contient un prénom interdit).

Bibliothèque standard uniquement, Python 3.9 ou plus récent.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

LIMITE_TAILLE = 2 * 1024 * 1024      # au-delà : « fichier trop gros »
LIMITE_LECTURE = 8 * 1024 * 1024     # au-delà, le contenu n'est pas analysé (déjà signalé)
FICHIER_AUTORISES = '.publication-autorises'
CHEMIN_INTERDITS = 'info/publication-interdits.txt'   # relatif au dossier .git

MARQUEUR = re.compile(r'publication\s*:\s*ok', re.IGNORECASE)

# Catégories d'alerte.
CAT_PROFIL = 'chemin de profil'
CAT_PHOTOS = 'dossier de photos'
CAT_EMAIL = 'e-mail'
CAT_SECRET = 'secret'
CAT_MDP = 'mot de passe'
CAT_URL = 'identifiants dans une URL'
CAT_IP = 'adresse IP'
CAT_INTERDIT = 'mot interdit'
CAT_TAILLE = 'fichier trop gros'
CAT_BASE = 'base de données'
CAT_MODELE = 'modèle ou vecteurs'
CAT_ARCHIVE = 'archive'
CAT_EXE = 'exécutable'
CAT_CLES = 'fichier de clés'
CAT_MEDIA = 'image ou vidéo non autorisée'

# --- Types de fichiers -------------------------------------------------------

EXT_BASES = {'.db', '.db3', '.sqlite', '.sqlite3', '.db-wal', '.db-shm', '.db-journal',
             '.sqlite-wal', '.sqlite-shm', '.sqlite3-wal', '.sqlite3-shm', '.mdb', '.accdb'}
EXT_MODELES = {'.onnx', '.onnx_data', '.ort', '.pt', '.pth', '.ckpt', '.safetensors', '.gguf',
               '.tflite', '.h5', '.pb', '.npy', '.npz', '.faiss'}
EXT_ARCHIVES = {'.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.tbz2', '.xz', '.txz',
                '.zst', '.lz4', '.cab', '.iso', '.whl'}
EXT_EXECUTABLES = {'.exe', '.msi', '.msix', '.dll', '.pyd', '.so', '.dylib'}
EXT_CLES = {'.pem', '.key', '.p12', '.pfx', '.kdbx', '.ppk', '.jks', '.keystore'}
NOMS_CLES = {'.env', '.netrc', '_netrc', '.pypirc', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519',
             'credentials.json', 'secrets.json', 'sftp.json',
             'cle-signature-privee.txt'}             # clé de signature des mises à jour de MyPhotos
ENV_EXEMPLES = {'.env.example', '.env.exemple', '.env.sample', '.env.modele', '.env.template'}
EXT_MEDIAS = {'.png', '.jpg', '.jpeg', '.jpe', '.jfif', '.gif', '.bmp', '.dib', '.tif', '.tiff',
              '.webp', '.heic', '.heif', '.avif', '.ico', '.icns', '.svg', '.psd', '.xcf',
              '.raw', '.dng', '.cr2', '.cr3', '.nef', '.arw', '.orf', '.rw2', '.raf', '.srw', '.pef',
              '.mp4', '.m4v', '.mov', '.avi', '.mkv', '.wmv', '.webm', '.3gp', '.mts', '.m2ts',
              '.mpg', '.mpeg'}

# Pour la règle « mot de passe » : dans du code, seule une valeur entre guillemets
# compte (ailleurs, « mdp = saisie » est une variable) ; dans de la prose, une valeur
# sans guillemets doit contenir un chiffre ou un symbole.
EXT_CODE = {'.py', '.pyw', '.pyi', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.vue', '.html',
            '.htm', '.css', '.ps1', '.psm1', '.c', '.h', '.cpp', '.hpp', '.cs', '.java', '.kt',
            '.go', '.rs', '.rb', '.php', '.swift'}
EXT_PROSE = {'.md', '.markdown', '.rst', '.txt', '.adoc'}

# --- Motifs génériques -------------------------------------------------------

# Profil Windows : C:\Users\<nom>, aussi échappé (« \\ »), en « / », ou /c/Users/<nom> (Git Bash).
# Un nom suivi d'un séparateur peut contenir des espaces ; en fin de chemin, non.
RE_PROFIL_WINDOWS = re.compile(
    r'(?<![A-Za-z0-9_])(?P<debut>(?:[A-Za-z]:|/[A-Za-z](?=/))[\\/]+'
    r'(?:Users|Documents and Settings)[\\/]+)'
    r'(?:(?P<nom>[^\\/\r\n"\'<>|:*?`,;()=]+?)(?=[\\/])|(?P<nom_fin>[^\\/\s"\'<>|:*?`,;()=\]}]+))',
    re.IGNORECASE)
# Profil macOS ou Linux : /Users/nom/ ou /home/nom/.
RE_PROFIL_UNIX = re.compile(
    r'(?<![\w.~-])(?P<debut>/(?:Users|home)/)(?P<nom>[^/\s"\'<>|:*?`,;)\]}]+)(?=/)')
NOMS_GENERIQUES = {
    'public', 'default', 'default user', 'defaultuser0', 'all users', 'shared', 'guest', 'invite',
    'user', 'users', 'username', 'utilisateur', 'nom', 'name', 'votre_nom', 'votrenom', 'moi', 'me',
    'you', 'vous', 'toi', 'example', 'exemple', 'test', 'runner', 'admin', 'administrateur',
    'administrator', 'xxx'}

# Chemin de lecteur (E:\…, E:/…, /e/… de Git Bash), découpé ensuite en dossiers.
RE_CHEMIN_LECTEUR = re.compile(
    r'(?<![A-Za-z0-9_])(?:[A-Za-z]:|/[A-Za-z](?=/))'
    r'(?:[\\/]+(?:(?!\s+(?:[A-Za-z]:|/[A-Za-z]/))[^\\/\r\n"\'<>|:*?`])*)+')
RE_PREFIXE_LECTEUR = re.compile(r'(?:[A-Za-z]:|/[A-Za-z])[\\/]+')
# Nom de dossier de photos, comparé en minuscules et sans accents.
RE_MOT_PHOTO = re.compile(
    r'(?:(?:mes|my|nos|our)\s+)?'
    r'(?:images?|photos?|pictures?|fotos?|bilder|immagini|imagenes|phototheque|dcim|pellicule'
    r'|camera roll|saved pictures)(?![a-z0-9]|\.[a-z0-9])')

RE_EMAIL = re.compile(
    r'(?<![\w.%+-])(?P<local>[A-Za-z0-9][A-Za-z0-9._%+-]*)@'
    r'(?P<domaine>(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,24})(?![\w-])')
DOMAINES_EXEMPLES = {'example.com', 'example.org', 'example.net',
                     'exemple.com', 'exemple.fr', 'exemple.org', 'exemple.net'}
TLD_RESERVES = {'example', 'test', 'invalid', 'localhost', 'local'}
TLD_FICHIERS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'avif', 'ico', 'css', 'js', 'json',
                'map', 'html', 'htm'}
HOTES_GIT = {'github.com', 'gitlab.com', 'bitbucket.org', 'codeberg.org', 'ssh.dev.azure.com'}

SECRETS = [
    ('clé Anthropic', re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}')),
    ('clé OpenAI', re.compile(r'(?<![A-Za-z0-9_-])sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}')),
    ('jeton GitHub', re.compile(r'(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,}')),
    ('jeton GitHub', re.compile(r'(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{30,}')),
    ('clé AWS', re.compile(r'(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])')),
    ('clé privée', re.compile(r'-----BEGIN[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----')),
    # Fichier écrit par construction/cle_signature.py (sa première ligne ; jamais dans le code qui l'écrit).
    ('clé privée', re.compile(r'^# MyPhotos : clé PRIVÉE de signature')),
    ('jeton Slack', re.compile(r'(?<![A-Za-z0-9])xox[abeoprs]-[A-Za-z0-9-]{10,}')),
    ('clé Google', re.compile(r'(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}')),
    ('jeton Hugging Face', re.compile(r'(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}')),
]

RE_MDP = re.compile(
    r'(?<![A-Za-z0-9])(?P<cle>password|passwd|passphrase|pwd|mot[ _-]?de[ _-]?passe|mdp)'
    r'(?![A-Za-z0-9])["\']?\s*(?:=>|:=|=|:)\s*'
    r'(?P<val>"[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s"\',;)\]}]+)',
    re.IGNORECASE)
VALEURS_VIDES = {'none', 'null', 'nil', 'undefined', 'true', 'false', 'str', 'string', 'bytes',
                 'required', 'optional', 'todo', 'changeme', 'secret', 'password', 'passwd', 'pass',
                 'pwd', 'mdp', 'motdepasse', 'mot_de_passe', 'mot de passe', 'your_password',
                 'votre_mot_de_passe'}

RE_URL_IDENTIFIANTS = re.compile(
    r'(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://'
    r'(?P<user>[^/\s:@"\'<>]+):(?P<mdp>[^/\s@"\'<>]+)@')

_OCTET = r'(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)'
RE_IP = re.compile(r'(?<![\w.])(' + _OCTET + r'(?:\.' + _OCTET + r'){3})(?!\w|\.\d)')
IP_DOCUMENTATION = ((192, 0, 2), (198, 51, 100), (203, 0, 113))   # RFC 5737


# --- Outils ------------------------------------------------------------------

def sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize('NFKD', texte)
    return ''.join(c for c in decompose if not unicodedata.combining(c))


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, « \\ » et « / » confondus, espaces resserrés."""
    texte = sans_accents(texte).casefold()
    texte = re.sub(r'[\\/]+', '/', texte)
    return re.sub(r'\s+', ' ', texte)


def masquer(valeur: str, visibles: int = 1) -> str:
    """Garde le premier caractère et remplace le reste par des étoiles (8 au plus)."""
    valeur = valeur.strip()
    if not valeur:
        return ''
    visibles = min(visibles, max(len(valeur) - 3, 0))
    return valeur[:visibles] + '*' * max(3, min(len(valeur) - visibles, 8))


def nettoyer(texte: str) -> str:
    return re.sub(r'[\x00-\x1f\x7f]', '?', texte)


def decoder(donnees: bytes) -> Optional[str]:
    """Texte du fichier, ou None s'il est binaire."""
    if donnees.startswith((b'\xff\xfe', b'\xfe\xff')):
        return donnees.decode('utf-16', errors='replace')
    if b'\x00' in donnees[:8192]:
        return None
    try:
        return donnees.decode('utf-8-sig')
    except UnicodeDecodeError:
        return donnees.decode('cp1252', errors='replace')


def genre_fichier(chemin: str) -> str:
    ext = os.path.splitext(chemin.casefold())[1]
    if ext in EXT_CODE:
        return 'code'
    if ext in EXT_PROSE:
        return 'prose'
    return 'config'


def type_interdit(chemin: str) -> Optional[str]:
    """Catégorie d'un fichier qu'on ne publie pas sans autorisation, sinon None."""
    nom = chemin.rsplit('/', 1)[-1].casefold()
    ext = os.path.splitext(nom)[1]
    if ext in EXT_BASES:
        return CAT_BASE
    if ext in EXT_MODELES or nom.endswith('.onnx.data'):
        return CAT_MODELE
    if ext in EXT_ARCHIVES:
        return CAT_ARCHIVE
    if ext in EXT_EXECUTABLES:
        return CAT_EXE
    if ext in EXT_CLES or nom in NOMS_CLES or (nom.startswith('.env.') and nom not in ENV_EXEMPLES):
        return CAT_CLES
    if ext in EXT_MEDIAS:
        return CAT_MEDIA
    return None


def nom_generique(nom: str) -> bool:
    n = sans_accents(nom).strip().casefold()
    return (not n or n[0] in '<{[(%$~*.' or '...' in n or '\u2026' in n
            or n in NOMS_GENERIQUES)


def email_autorise(local: str, domaine: str) -> bool:
    local, domaine = local.casefold(), domaine.casefold()
    if domaine == 'users.noreply.github.com' or domaine.endswith('.users.noreply.github.com'):
        return True
    if local.startswith(('noreply', 'no-reply', 'no_reply')):
        return True
    if domaine in DOMAINES_EXEMPLES or any(domaine.endswith('.' + d) for d in DOMAINES_EXEMPLES):
        return True
    tld = domaine.rsplit('.', 1)[-1]
    if tld in TLD_RESERVES or tld in TLD_FICHIERS:
        return True
    return local == 'git' and domaine in HOTES_GIT    # adresse SSH d'un dépôt, pas un e-mail


def valeur_factice(valeur: str) -> bool:
    """Valeur vide, générique ou de remplissage (***, xxxx, <mot de passe>, $VAR…)."""
    v = valeur.strip()
    return (len(v) < 3 or v.casefold() in VALEURS_VIDES or v[0] in '<{[($%@*'
            or len(set(v.casefold())) == 1)


def mdp_plausible(brute: str, genre: str) -> bool:
    entre_guillemets = brute[:1] in '"\''
    valeur = brute.strip('"\'') if entre_guillemets else brute
    if valeur_factice(valeur):
        return False
    if not entre_guillemets:
        if genre == 'code' or '(' in valeur or valeur.casefold().startswith(('os.', 'env', 'process.')):
            return False
        if genre == 'prose' and valeur.isalpha():
            return False
    return True


def ip_autorisee(ip: str, ligne: str, debut: int) -> bool:
    octets = tuple(int(x) for x in ip.split('.'))
    if octets[0] in (127, 255) or ip == '0.0.0.0' or octets[:3] in IP_DOCUMENTATION:
        return True
    return 'vers' in ligne[max(0, debut - 30):debut].casefold()    # numéro de version


# --- Listes locales ----------------------------------------------------------

class MotsInterdits:
    """Motifs de .git/info/publication-interdits.txt (jamais affichés en clair)."""

    def __init__(self, motifs=(), present: bool = False, avertissements=(), exceptions=()):
        self.motifs = list(motifs)            # (numéro de ligne, sous-chaîne ou regex)
        self.present = present
        self.avertissements = list(avertissements)
        self.exceptions = sorted(exceptions, key=len, reverse=True)

    @classmethod
    def depuis_texte(cls, texte: str, present: bool = True) -> 'MotsInterdits':
        motifs, avertissements, exceptions = [], [], []
        for numero, ligne in enumerate(texte.split('\n'), 1):
            ligne = ligne.strip()
            if not ligne or ligne.startswith('#'):
                continue
            if ligne.startswith('!'):
                exception = normaliser(ligne[1:]).strip()
                if exception:
                    exceptions.append(exception)
                continue
            if ligne.startswith('re:'):
                try:
                    motifs.append((numero, re.compile(ligne[3:].strip(), re.IGNORECASE)))
                except re.error:
                    avertissements.append(f'ligne {numero} : expression régulière invalide, ignorée')
                continue
            entier = ligne.startswith('mot:')
            motif = normaliser(ligne[4:] if entier else ligne).strip()
            if len(motif) < (2 if entier else 3):
                avertissements.append(f'ligne {numero} : motif trop court, ignoré')
            elif entier:
                motifs.append((numero, re.compile(r'(?<![^\W_])' + re.escape(motif) + r'(?![^\W_])')))
            else:
                motifs.append((numero, motif))
        return cls(motifs, present, avertissements, exceptions)

    @classmethod
    def lire(cls, chemin: Optional[str]) -> 'MotsInterdits':
        if not chemin or not os.path.isfile(chemin):
            return cls()
        with open(chemin, 'rb') as fichier:
            return cls.depuis_texte(decoder(fichier.read()) or '')

    def chercher(self, texte: str) -> List[Tuple[int, str]]:
        if not self.motifs:
            return []
        normal = normaliser(texte)
        for exception in self.exceptions:
            normal = normal.replace(exception, ' ')
        trouves = []
        for numero, motif in self.motifs:
            if isinstance(motif, str):
                position = normal.find(motif)
                if position >= 0:
                    trouves.append((numero, normal[position:position + len(motif)]))
            else:
                m = motif.search(normal)
                if m:
                    trouves.append((numero, m.group(0)))
        return trouves


class Autorisations:
    """.publication-autorises : fichiers autorisés malgré leur type, valeurs d'exemple."""

    def __init__(self, fichiers=(), exemples=()):
        self.fichiers = [motif.casefold() for motif in fichiers]
        self.exemples = {normaliser(valeur).strip().rstrip('/') for valeur in exemples}
        self.exemples.discard('')

    @classmethod
    def depuis_texte(cls, texte: str) -> 'Autorisations':
        fichiers, exemples = [], []
        for ligne in texte.split('\n'):
            ligne = ligne.strip()
            if not ligne or ligne.startswith('#'):
                continue
            exemple = re.match(r'exemple\s*:', ligne, re.IGNORECASE)
            if exemple:
                exemples.append(ligne[exemple.end():].strip())
            else:
                fichiers.append(ligne.replace('\\', '/').lstrip('/'))
        return cls(fichiers, exemples)

    @classmethod
    def lire(cls, chemin: str) -> 'Autorisations':
        if not os.path.isfile(chemin):
            return cls()
        with open(chemin, 'rb') as fichier:
            return cls.depuis_texte(decoder(fichier.read()) or '')

    def fichier_autorise(self, chemin: str) -> bool:
        chemin = chemin.casefold()
        return any(fnmatch.fnmatchcase(chemin, motif) for motif in self.fichiers)

    def est_exemple(self, valeur: str) -> bool:
        if not self.exemples:
            return False
        valeur = normaliser(valeur).strip().rstrip('/')
        return any(valeur == e or valeur.startswith(e + '/') for e in self.exemples)


# --- Analyse -----------------------------------------------------------------

@dataclass(frozen=True)
class Alerte:
    lieu: str          # « fichier:ligne », « fichier », « commit … »
    categorie: str
    extrait: str       # toujours masqué

    def __str__(self) -> str:
        return f'{self.lieu}  [{self.categorie}]  {nettoyer(self.extrait)}'


class Verificateur:
    def __init__(self, interdits: Optional[MotsInterdits] = None,
                 autorises: Optional[Autorisations] = None):
        self.interdits = interdits or MotsInterdits()
        self.autorises = autorises or Autorisations()

    def analyser_ligne(self, ligne: str, lieu: str, genre: str = 'config') -> List[Alerte]:
        if not ligne or MARQUEUR.search(ligne):
            return []
        alertes: List[Alerte] = []

        def signaler(categorie: str, brut: str, extrait: str) -> None:
            if self.autorises.est_exemple(brut):
                return
            alerte = Alerte(lieu, categorie, extrait)
            if alerte not in alertes:
                alertes.append(alerte)

        for m in RE_PROFIL_WINDOWS.finditer(ligne):
            nom = m.group('nom') or m.group('nom_fin') or ''
            if not nom_generique(nom):
                signaler(CAT_PROFIL, m.group(0), m.group('debut') + masquer(nom))
        for m in RE_PROFIL_UNIX.finditer(ligne):
            if not nom_generique(m.group('nom')):
                signaler(CAT_PROFIL, m.group(0), m.group('debut') + masquer(m.group('nom')) + '/')

        for m in RE_CHEMIN_LECTEUR.finditer(ligne):
            texte = m.group(0)
            prefixe = RE_PREFIXE_LECTEUR.match(texte)
            for dossier in re.finditer(r'[^\\/]+', texte[2:]):
                mot = RE_MOT_PHOTO.match(sans_accents(dossier.group(0)).casefold())
                if mot:
                    brut = texte[:2 + dossier.start() + len(mot.group(0))]
                    debut = prefixe.group(0) if prefixe else brut[:3]
                    signaler(CAT_PHOTOS, brut, debut + masquer(brut[len(debut):]))
                    break

        urls = []                     # « ftp://<nom>:<mdp>@hôte » n'est pas un e-mail
        for m in RE_URL_IDENTIFIANTS.finditer(ligne):
            urls.append(m.span())
            if not valeur_factice(m.group('mdp')):
                signaler(CAT_URL, m.group(0), masquer(m.group('user')) + ':********@')

        for m in RE_EMAIL.finditer(ligne):
            if any(debut <= m.start() < fin for debut, fin in urls):
                continue
            if not email_autorise(m.group('local'), m.group('domaine')):
                domaine, _, tld = m.group('domaine').rpartition('.')
                signaler(CAT_EMAIL, m.group(0),
                         f"{masquer(m.group('local'))}@{masquer(domaine)}.{tld}")

        for nom, motif in SECRETS:
            for m in motif.finditer(ligne):
                valeur = m.group(0)
                if nom == 'clé privée':
                    signaler(f'{CAT_SECRET} ({nom})', valeur, valeur)
                elif len(set(re.sub(r'[^A-Za-z0-9]', '', valeur[4:]))) >= 5:   # pas « ghp_xxxx… »
                    signaler(f'{CAT_SECRET} ({nom})', valeur,
                             f'{valeur[:4]}******** ({len(valeur)} caractères)')

        for m in RE_MDP.finditer(ligne):
            if mdp_plausible(m.group('val'), genre):
                signaler(CAT_MDP, m.group('val').strip('"\''), f"{m.group('cle')} = ********")

        for m in RE_IP.finditer(ligne):
            ip = m.group(1)
            if not ip_autorisee(ip, ligne, m.start()):
                signaler(CAT_IP, ip, ip.split('.')[0] + '.***.***.***')

        for numero, extrait in self.interdits.chercher(ligne):
            # Jamais levé par .publication-autorises : seul « publication: ok » le fait.
            alertes.append(Alerte(lieu, f'{CAT_INTERDIT} #{numero}', masquer(extrait)))
        return alertes

    def analyser_contenu(self, donnees: bytes, chemin: str, prefixe: str = '') -> List[Alerte]:
        texte = decoder(donnees)
        if texte is None:
            return []
        genre = genre_fichier(chemin)
        alertes: List[Alerte] = []
        for numero, ligne in enumerate(texte.split('\n'), 1):
            alertes.extend(self.analyser_ligne(ligne.rstrip('\r'), f'{prefixe}{chemin}:{numero}', genre))
        return alertes

    def verifier_nom(self, chemin: str, lieu: Optional[str] = None) -> List[Alerte]:
        return [Alerte(lieu or chemin, f'{CAT_INTERDIT} #{numero} (dans le nom)', masquer(extrait))
                for numero, extrait in self.interdits.chercher(chemin)]

    def verifier_fichier(self, chemin: str, taille: int, lieu: Optional[str] = None) -> List[Alerte]:
        lieu = lieu or chemin
        alertes = self.verifier_nom(chemin, lieu)
        if taille > LIMITE_TAILLE:
            alertes.append(Alerte(lieu, CAT_TAILLE, f'{taille / (1024 * 1024):.1f} Mo'))
        categorie = type_interdit(chemin)
        if categorie and not self.autorises.fichier_autorise(chemin):
            extension = os.path.splitext(chemin)[1] or chemin.rsplit('/', 1)[-1]
            alertes.append(Alerte(lieu, categorie, extension))
        return alertes


# --- Git ---------------------------------------------------------------------

class ErreurGit(Exception):
    pass


def git(racine: str, *arguments: str, entree: Optional[bytes] = None) -> bytes:
    commande = ['git', '-c', 'core.quotepath=false', *arguments]
    try:
        resultat = subprocess.run(commande, cwd=racine, input=entree,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as erreur:
        raise ErreurGit(f'git introuvable ({erreur})') from erreur
    if resultat.returncode != 0:
        message = resultat.stderr.decode('utf-8', 'replace').strip()
        raise ErreurGit(f"git {' '.join(arguments[:2])} : {message or resultat.returncode}")
    return resultat.stdout


def liste_z(sortie: bytes) -> List[str]:
    """Chemins d'une sortie git « -z », sans doublon, dans l'ordre."""
    chemins = (brut.decode('utf-8', 'replace') for brut in sortie.split(b'\x00'))
    return list(dict.fromkeys(chemin for chemin in chemins if chemin))


def infos_objets(racine: str, noms: Sequence[str]) -> Dict[str, Tuple[str, str, int]]:
    """{nom: (sha, type, taille)} pour des noms d'objets (« :chemin » = version indexée)."""
    if not noms:
        return {}
    entree = ''.join(nom + '\n' for nom in noms).encode('utf-8')
    sortie = git(racine, 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)',
                 entree=entree)
    infos = {}
    for nom, ligne in zip(noms, sortie.decode('utf-8', 'replace').split('\n')):
        morceaux = ligne.strip().split(' ')
        if len(morceaux) == 3 and morceaux[2].isdigit() and not ligne.endswith((' missing', ' ambiguous')):
            infos[nom] = (morceaux[0], morceaux[1], int(morceaux[2]))
    return infos


def contenus_objets(racine: str, shas: Sequence[str]) -> Iterator[Tuple[str, bytes]]:
    if not shas:
        return
    sortie = git(racine, 'cat-file', '--batch', entree=''.join(s + '\n' for s in shas).encode())
    position = 0
    for sha in shas:
        fin_entete = sortie.index(b'\n', position)
        morceaux = sortie[position:fin_entete].split(b' ')
        position = fin_entete + 1
        if len(morceaux) != 3 or not morceaux[2].isdigit():
            continue
        taille = int(morceaux[2])
        yield sha, sortie[position:position + taille]
        position += taille + 1


def verifier_arbre(racine: str, verif: Verificateur) -> Tuple[List[Alerte], int]:
    """Fichiers suivis et nouveaux fichiers non ignorés, tels qu'ils sont sur le disque."""
    chemins = liste_z(git(racine, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'))
    alertes: List[Alerte] = []
    nombre = 0
    for chemin in chemins:
        complet = os.path.join(racine, chemin)
        if os.path.islink(complet) or not os.path.isfile(complet):
            continue
        nombre += 1
        taille = os.path.getsize(complet)
        alertes.extend(verif.verifier_fichier(chemin, taille))
        if taille <= LIMITE_LECTURE:
            with open(complet, 'rb') as fichier:
                alertes.extend(verif.analyser_contenu(fichier.read(), chemin))
    return alertes, nombre


def verifier_index(racine: str, verif: Verificateur) -> Tuple[List[Alerte], int]:
    """Version indexée (celle qui sera commitée) des fichiers ajoutés ou modifiés."""
    chemins = liste_z(git(racine, 'diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR'))
    infos = infos_objets(racine, [':' + chemin for chemin in chemins])
    alertes: List[Alerte] = []
    a_lire: Dict[str, str] = {}
    for chemin in chemins:
        info = infos.get(':' + chemin)
        if not info or info[1] != 'blob':
            continue
        alertes.extend(verif.verifier_fichier(chemin, info[2]))
        if info[2] <= LIMITE_LECTURE:
            a_lire.setdefault(info[0], chemin)
    for sha, donnees in contenus_objets(racine, list(a_lire)):
        alertes.extend(verif.analyser_contenu(donnees, a_lire[sha]))
    return alertes, len(infos)


def _chemin_diff(texte: str) -> Optional[str]:
    texte = texte.rstrip('\t')
    if texte.startswith('"') and texte.endswith('"'):
        echappements = {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}
        texte = re.sub(r'\\([0-7]{3}|.)',
                       lambda m: chr(int(m.group(1), 8)) if len(m.group(1)) == 3
                       else echappements.get(m.group(1), m.group(1)), texte[1:-1])
    if texte == '/dev/null':
        return None
    return texte[2:] if texte.startswith('b/') else texte


RE_ENTETE_BLOC = re.compile(r'@@+ -\d+(?:,\d+)? \+(\d+)')


def verifier_historique(racine: str, verif: Verificateur) -> Tuple[List[Alerte], int]:
    """Lignes ajoutées par chaque commit, auteurs, messages et fichiers de tout l'historique."""
    if not git(racine, 'rev-list', '--all', '--max-count=1').strip():
        return [], 0
    alertes: List[Alerte] = []
    nombre = int(git(racine, 'rev-list', '--all', '--count').strip() or 0)

    # 1. Auteurs, committeurs et messages.
    vus = set()
    brut = git(racine, 'log', '--all', '--format=%H%x00%an <%ae>%x00%cn <%ce>%x00%B%x1e')
    for bloc in brut.decode('utf-8', 'replace').split('\x1e'):
        champs = bloc.strip('\n').split('\x00')
        if len(champs) < 4:
            continue
        court = champs[0][:8]
        for role, identite in (('auteur', champs[1]), ('committeur', champs[2])):
            for alerte in verif.analyser_ligne(identite, f'commit {court} ({role})'):
                cle = (role, alerte.categorie, alerte.extrait)
                if cle not in vus:                 # une fois par identité, pas par commit
                    vus.add(cle)
                    alertes.append(alerte)
        for numero, ligne in enumerate(champs[3].split('\n'), 1):
            alertes.extend(verif.analyser_ligne(ligne, f'commit {court} (message):{numero}', 'prose'))

    # 2. Lignes ajoutées.
    commande = ['git', '-c', 'core.quotepath=false', 'log', '--all', '-p', '--no-color',
                '--no-ext-diff', '--no-textconv', '--src-prefix=a/', '--dst-prefix=b/',
                '--format=%x00%H']
    with subprocess.Popen(commande, cwd=racine, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as processus:
        commit, fichier, dans_bloc, numero = '', None, False, 0
        for octets in processus.stdout:
            ligne = octets.decode('utf-8', 'replace').rstrip('\r\n')
            if ligne.startswith('\x00'):
                commit, fichier, dans_bloc = ligne[1:9], None, False
            elif ligne.startswith('diff --git '):
                fichier, dans_bloc = None, False
            elif ligne.startswith('@@'):
                m = RE_ENTETE_BLOC.match(ligne)
                numero, dans_bloc = (int(m.group(1)) if m else 1), True
            elif not dans_bloc:
                if ligne.startswith('+++ '):
                    fichier = _chemin_diff(ligne[4:])
            elif ligne.startswith('+'):
                if fichier:
                    alertes.extend(verif.analyser_ligne(
                        ligne[1:], f'{commit}:{fichier}:{numero}', genre_fichier(fichier)))
                numero += 1
            elif ligne.startswith(' ') or not ligne:
                numero += 1
    if processus.returncode not in (0, None):
        raise ErreurGit('git log -p a échoué')

    # 3. Fichiers et dossiers de tout l'historique (taille, type, nom).
    objets = []
    for ligne in git(racine, 'rev-list', '--objects', '--all').decode('utf-8', 'replace').split('\n'):
        sha, _, chemin = ligne.strip().partition(' ')
        if chemin:
            objets.append((sha, chemin))
    infos = infos_objets(racine, [sha for sha, _ in objets])
    for sha, chemin in objets:
        info = infos.get(sha)
        lieu = f'{chemin} (objet {sha[:8]})'
        if info and info[1] == 'blob':
            alertes.extend(verif.verifier_fichier(chemin, info[2], lieu))
        elif info and info[1] == 'tree':
            alertes.extend(verif.verifier_nom(chemin, lieu))
    return alertes, nombre


# --- Programme ---------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    analyseur = argparse.ArgumentParser(
        prog='verifier_publication.py',
        description='Garde-fou de publication : données personnelles, secrets et gros fichiers.')
    mode = analyseur.add_mutually_exclusive_group()
    mode.add_argument('--index', action='store_true',
                      help='vérifie la version indexée des fichiers ajoutés ou modifiés '
                           '(crochet pre-commit)')
    mode.add_argument('--historique', action='store_true',
                      help='vérifie tous les commits (git log -p --all), avant un premier push')
    analyseur.add_argument('-C', '--depot', default='.',
                           help='dossier du dépôt (par défaut : le dossier courant)')
    arguments = analyseur.parse_args(argv)

    try:
        racine = git(arguments.depot, 'rev-parse', '--show-toplevel').decode('utf-8').strip()
        chemin_interdits = git(racine, 'rev-parse', '--git-path', CHEMIN_INTERDITS).decode('utf-8').strip()
        interdits = MotsInterdits.lire(os.path.join(racine, chemin_interdits))
        verif = Verificateur(interdits, Autorisations.lire(os.path.join(racine, FICHIER_AUTORISES)))
        if arguments.index:
            alertes, nombre, objet = *verifier_index(racine, verif), 'fichier(s) indexé(s)'
        elif arguments.historique:
            alertes, nombre, objet = *verifier_historique(racine, verif), 'commit(s)'
        else:
            alertes, nombre, objet = *verifier_arbre(racine, verif), 'fichier(s)'
    except (ErreurGit, OSError) as erreur:
        print(f'Garde-fou de publication : erreur : {erreur}', file=sys.stderr)
        return 2

    if not interdits.present:
        print('Attention : .git/info/publication-interdits.txt est absent : '
              'les mots interdits personnels ne sont pas vérifiés.', file=sys.stderr)
    for avertissement in interdits.avertissements:
        print(f'Attention : publication-interdits.txt, {avertissement}.', file=sys.stderr)

    resume = f'{nombre} {objet} vérifié(s), {len(interdits.motifs)} mot(s) interdit(s) local(aux)'
    if not alertes:
        print(f'Garde-fou de publication : aucune alerte ({resume}).')
        return 0
    print(f'Garde-fou de publication : {len(alertes)} alerte(s) ({resume}).\n')
    for alerte in alertes:
        print(f'  {alerte}')
    print('\nPublication bloquée. Retirez ces données (ou ces fichiers) avant de publier.'
          '\nFaux positif : ajoutez « publication: ok » sur la ligne, ou déclarez le fichier'
          '\nou la valeur d\'exemple dans .publication-autorises (sans donnée personnelle).')
    if any(a.categorie.startswith(CAT_INTERDIT) for a in alertes):
        print('« mot interdit #N » : motif de la ligne N de .git/info/publication-interdits.txt.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
