"""Index de MyPhotos : SQLite en mode WAL, une connexion par fil.

Deux fichiers, pour que l'index reste léger :
- index.db      : réglages (meta), dossiers suivis, photos (métadonnées et vecteur) ;
- miniatures.db : vignettes JPEG, rattachée à chaque connexion sous le nom « mini ».

La base ne touche jamais aux photos : elle retient seulement ce qu'on en sait. Les écritures passent
une à une (verrou) dans des transactions groupées ; grâce au WAL, les lectures (recherche, miniatures)
ne sont jamais bloquées par l'indexation.

Colonnes de photos :
- taille, modifie : taille en octets et date de modification (st_mtime_ns) : un changement = à refaire ;
- date_prise (EXIF) et date_fichier (date de modification), en ISO local 'AAAA-MM-JJTHH:MM:SS' ;
- largeur, hauteur : dimensions d'origine, dans le sens d'affichage ;
- vecteur : float16 normalisé ; modele : clé du modèle qui l'a calculé (renseignés ensemble) ;
- erreur : pourquoi la photo n'est pas analysée ('trop petite', 'illisible : …') ;
- vu : date à laquelle la photo a été trouvée pour la première fois ;
- miniature : 1 si sa vignette est dans miniatures.db.

Table exclusions (0.3.0) : sous-dossiers d'un dossier suivi que MyPhotos ne regarde pas (captures d'écran, fonds
d'écran…). Rien n'est supprimé du disque : leurs photos sont seulement oubliées, et l'inventaire ne les parcourt plus.
Table à part, créée à l'ouverture si elle manque : une base 0.2.0 est reprise sans perte (user_version reste 1 ; une
0.2.0 rouverte sur une base 0.3.0 ignore simplement cette table).

Colonne dossiers.volume (0.4.0) : numéro de série du volume (disque, clé USB, partage) où se trouvait chaque dossier
suivi la dernière fois qu'il a été vu (identite_volume). Si un autre disque prend la lettre d'un disque débranché, le
numéro diffère : le dossier est inaccessible, ses photos ne sont pas « supprimées ». Ajoutée à l'ouverture si elle
manque, de la même façon : NULL (inconnu) pour une base 0.3.0, appris au tour suivant ; une 0.3.0 ou une 0.2.0 rouverte
sur une base 0.4.0 ne lit que les colonnes qu'elle connaît et ajoute ses dossiers sans numéro.

Table lieux_photos (0.5.0) : le lieu de chaque photo qui a une position GPS valide (lieux.py, sans Internet) : idents
de sa ville, de son département, de sa région et de son pays (None quand il n'y en a pas : photo prise en mer…),
distance à la ville en km, et version des données de lieux qui l'a calculé (refait quand elle change). Même règle que
les exclusions : créée à l'ouverture si elle manque, sans changer user_version ; une 0.4.x l'ignore. Les lignes d'une
photo oubliée partent avec elle, dans la même transaction ; celles qu'une 0.4.x aurait laissées (elle ne connaît pas la
table) sont ignorées par les lectures, qui passent par photos, et effacées par reparer(). Avec elle, un index partiel
de photos (photos_gps : position et miniature des photos qui en ont une) évite de relire toute la table, vecteurs
compris, pour trouver les photos à localiser.
"""
import ctypes
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

import numpy as np

VERSION_SCHEMA = 1
PAQUET = 500                          # longueur maximale d'une liste « IN (…) »

WINDOWS = os.name == 'nt'
_PREFIXE = '\\\\?\\'                  # \\?\ : chemins longs et noms inhabituels sous Windows
_PREFIXE_UNC = '\\\\?\\UNC\\'

_SCHEMA_INDEX = (
    "CREATE TABLE IF NOT EXISTS meta (cle TEXT PRIMARY KEY, valeur)",
    """CREATE TABLE IF NOT EXISTS dossiers (
        id         INTEGER PRIMARY KEY,
        chemin     TEXT NOT NULL UNIQUE,
        ajoute     TEXT NOT NULL,
        disponible INTEGER NOT NULL DEFAULT 1,
        vu         TEXT)""",
    """CREATE TABLE IF NOT EXISTS photos (
        id           INTEGER PRIMARY KEY,
        chemin       TEXT NOT NULL UNIQUE,
        dossier_id   INTEGER NOT NULL,
        taille       INTEGER NOT NULL,
        modifie      INTEGER NOT NULL,
        largeur      INTEGER,
        hauteur      INTEGER,
        date_prise   TEXT,
        date_fichier TEXT,
        lat          REAL,
        lon          REAL,
        vecteur      BLOB,
        modele       TEXT,
        erreur       TEXT,
        vu           TEXT,
        miniature    INTEGER NOT NULL DEFAULT 0)""",
    # Comptages sans lire les vecteurs (index couvrant).
    "CREATE INDEX IF NOT EXISTS photos_etat ON photos(dossier_id, modele, miniature, erreur)",
    # Affichage par date et file d'attente de l'analyse (plus récentes d'abord) : index partiels.
    "CREATE INDEX IF NOT EXISTS photos_date ON photos(COALESCE(date_prise, date_fichier) DESC, id DESC) "
    "WHERE miniature = 1",
    "CREATE INDEX IF NOT EXISTS photos_a_faire ON photos(COALESCE(date_prise, date_fichier) DESC, id DESC) "
    "WHERE modele IS NULL AND erreur IS NULL",
    "INSERT OR IGNORE INTO meta(cle, valeur) VALUES ('generation', 0)",
)
_SCHEMA_EXCLUSIONS = (
    """CREATE TABLE IF NOT EXISTS exclusions (
        id         INTEGER PRIMARY KEY,
        dossier_id INTEGER NOT NULL,
        chemin     TEXT NOT NULL UNIQUE,
        ajoute     TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS exclusions_dossier ON exclusions(dossier_id)",
)
# 0.5.0 : lieux des photos (voir l'en-tête), et index partiel des photos qui ont une position.
_SCHEMA_LIEUX = (
    """CREATE TABLE IF NOT EXISTS lieux_photos (
        photo_id    INTEGER PRIMARY KEY,
        ville       TEXT,
        departement TEXT,
        region      TEXT,
        pays        TEXT,
        distance    REAL,
        version     TEXT)""",
    "CREATE INDEX IF NOT EXISTS lieux_photos_ville ON lieux_photos(ville) WHERE ville IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS lieux_photos_departement ON lieux_photos(departement) WHERE departement IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS lieux_photos_region ON lieux_photos(region) WHERE region IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS lieux_photos_pays ON lieux_photos(pays) WHERE pays IS NOT NULL",
    # erreur est toujours NULL dans cet index, mais SQLite ne le déduit pas de la condition : sans elle, chaque photo
    # serait relue dans la table (vecteur compris) pour la vérifier.
    "CREATE INDEX IF NOT EXISTS photos_gps ON photos(lat, lon, miniature, erreur) WHERE lat IS NOT NULL "
    "AND erreur IS NULL",
)
_COLONNES_LIEU = ('ville', 'departement', 'region', 'pays')
_TYPES_LIEU = {'ville': 'ville', 'departement': 'departement', 'region': 'region', 'pays': 'pays',
               'v': 'ville', 'd': 'departement', 'r': 'region', 'p': 'pays'}
# Position GPS utilisable, comme lieux.coordonnees_valides : dans les bornes, et pas (0, 0) à 0,01° près (« Null
# Island » : appareil sans vraie position). SQLite n'enregistre jamais NaN (il le change en NULL).
_GPS_VALIDE = ('{p}lat IS NOT NULL AND {p}lon IS NOT NULL AND {p}lat BETWEEN -90 AND 90 AND {p}lon BETWEEN -180 AND 180 '
               'AND NOT (ABS({p}lat) < 0.01 AND ABS({p}lon) < 0.01)')
# Tables de doublons.py (créées par lui, dans index.db) : une photo oubliée y est effacée aussi.
_TABLES_DOUBLONS = ('doublons_empreintes', 'doublons_paires')
_SCHEMA_MINIATURES = (
    "CREATE TABLE IF NOT EXISTS mini.miniatures (id INTEGER PRIMARY KEY, jpeg BLOB NOT NULL)",
)

_COLONNES_PHOTO = ('p.id, p.chemin, p.dossier_id, p.taille, p.modifie, p.largeur, p.hauteur, p.date_prise, '
                   'p.date_fichier, p.lat, p.lon, p.modele, p.erreur, p.vu, p.miniature, d.chemin, d.disponible, '
                   'd.volume')
_COLONNES_DOSSIER = 'id, chemin, ajoute, disponible, vu, volume'

_ABIMEE = (11, 26)                    # SQLITE_CORRUPT, SQLITE_NOTADB : fichier qui n'est plus une base lisible


class DossierRefuse(ValueError):
    """Dossier impossible à ajouter ; le message, en français, peut s'afficher tel quel."""


def enregistrable(texte):
    """Faux pour un nom que Windows accepte mais qui n'est pas de l'Unicode valide (moitié de caractère laissée seule
    par un vieux logiciel ou un disque abîmé) : SQLite ne peut pas l'enregistrer."""
    try:
        texte.encode('utf-8')
        return True
    except UnicodeEncodeError:
        return False


def _abimee(e):
    """L'erreur dit-elle que le fichier n'est pas, ou plus, une base SQLite ?"""
    code = getattr(e, 'sqlite_errorcode', None)
    return isinstance(e, sqlite3.DatabaseError) and isinstance(code, int) and (code & 0xFF) in _ABIMEE


def _lisible(chemin):
    """Vrai si le fichier est une base SQLite lisible. Une base occupée par un autre programme est lisible."""
    c = None
    try:
        c = sqlite3.connect(chemin, timeout=5)
        return c.execute('PRAGMA quick_check(1)').fetchone()[0] == 'ok'
    except sqlite3.DatabaseError as e:
        return not _abimee(e)
    finally:
        if c is not None:
            c.close()


# ---------------------------------------------------------------- dates et chemins

def maintenant():
    """Date et heure locales, en ISO à la seconde."""
    return datetime.now().isoformat(timespec='seconds')


def iso_depuis_ns(ns):
    """st_mtime_ns -> 'AAAA-MM-JJTHH:MM:SS' (heure locale), ou None si la date est inexploitable."""
    try:
        return datetime.fromtimestamp(ns / 1e9).isoformat(timespec='seconds')
    except (OverflowError, OSError, ValueError):
        return None


def chemin_systeme(chemin):
    """Chemin à donner au système pour ouvrir ou lister : sous Windows, préfixe \\\\?\\ qui lève la limite
    des 260 caractères et accepte les noms finissant par un point ou une espace. Le chemin doit être absolu."""
    p = os.fspath(chemin)
    if not WINDOWS or p.startswith(_PREFIXE):
        return p
    if p.startswith('\\\\'):
        return _PREFIXE_UNC + p[2:]
    if len(p) >= 3 and p[1] == ':' and p[2] == '\\':
        return _PREFIXE + p
    return p


def chemin_affiche(chemin):
    """Inverse de chemin_systeme : chemin habituel, tel qu'on le montre et qu'on l'enregistre."""
    p = os.fspath(chemin)
    if p.startswith(_PREFIXE_UNC):
        return '\\\\' + p[len(_PREFIXE_UNC):]
    if p.startswith(_PREFIXE):
        return p[len(_PREFIXE):]
    return p


def cle_chemin(chemin):
    """Clé de comparaison de deux chemins (Windows ne distingue pas majuscules et minuscules)."""
    return os.path.normcase(chemin_affiche(chemin))


def identite_volume(chemin):
    """Numéro de série du volume (disque, clé USB, partage réseau) où se trouve ce chemin, ou None si on ne peut pas le
    lire. Sous Windows, st_dev est ce numéro ; seuls ses 32 bits bas sont gardés, ceux que montre la commande « vol » :
    selon les versions de Windows et de Python, st_dev en a 32 ou 64, et le numéro retenu ne doit pas changer pour
    autant."""
    try:
        numero = os.stat(chemin_systeme(chemin)).st_dev & 0xFFFFFFFF
    except (OSError, ValueError):
        return None
    return numero or None


def identite_fichier(chemin):
    """(volume, numéro de fichier) d'un élément existant, liens et jonctions suivis : deux écritures du même dossier
    (nom court 8.3, jonction, préfixe \\\\?\\) donnent la même identité. None si Windows ne la donne pas (certains
    partages réseau) ou si l'élément est introuvable."""
    try:
        st = os.stat(chemin_systeme(chemin))
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino) if st.st_dev and st.st_ino else None


def chemin_reel(chemin):
    """Emplacement réel d'un dossier existant, en chemin habituel : noms longs (pas « PROGRA~1 »), liens et jonctions
    résolus (os.path.realpath, par GetFinalPathNameByHandleW). None si Windows ne le donne pas."""
    try:
        return chemin_affiche(os.path.realpath(chemin_systeme(chemin), strict=True))
    except (OSError, ValueError):
        return None


def nom_long(chemin):
    """Chemin d'un élément existant écrit avec ses noms longs (« VACANC~1 » -> « Vacances d'été 2021 »), par
    GetLongPathNameW : chaque nom est développé là où il est, sans traverser aucune jonction (contrairement à
    chemin_reel, qui les résout). None si Windows ne le donne pas, ou ailleurs que sous Windows."""
    if not WINDOWS:
        return None
    try:
        fonction = ctypes.WinDLL('kernel32', use_last_error=True).GetLongPathNameW
        fonction.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        fonction.restype = ctypes.c_uint32
        tampon = ctypes.create_unicode_buffer(32768)
        n = fonction(chemin_systeme(chemin), tampon, len(tampon))
    except (OSError, ValueError, AttributeError):
        return None
    return chemin_affiche(tampon.value) if 0 < n < len(tampon) else None


def sous_le_dossier(chemin, racine):
    """Le dossier `chemin` vu depuis le dossier suivi `racine`, quand son emplacement réel est sous celui de `racine` :
    racine suivie des vrais noms des dossiers en dessous. Sert pour un chemin saisi par un nom court (8.3) ou à travers
    une jonction, que l'inventaire ne voit jamais (il ne connaît que les noms longs et ne suit aucune jonction). None
    sinon. Comparaison élément par élément, sans os.path.relpath, qui retirerait le point final de « Vacances. »."""
    reel, reelle = chemin_reel(chemin), chemin_reel(racine)
    if not reel or not reelle:
        return None
    pc, pr = reel.rstrip(os.sep).split(os.sep), reelle.rstrip(os.sep).split(os.sep)
    if len(pc) <= len(pr) or [os.path.normcase(x) for x in pc[:len(pr)]] != [os.path.normcase(x) for x in pr]:
        return None
    return os.path.join(racine, *pc[len(pr):])


def dedans(enfant, parent):
    """Vrai si `enfant` est situé sous `parent` (deux clés de cle_chemin, distinctes)."""
    prefixe = parent if parent.endswith(os.sep) else parent + os.sep
    return enfant != parent and enfant.startswith(prefixe)


def _absolu(p):
    """Vrai pour « E:\\… » ou « \\\\serveur\\partage… » (pas « E:dossier », relatif au dossier courant de E:)."""
    lecteur, reste = os.path.splitdrive(p)
    if not WINDOWS:
        return os.path.isabs(p)
    return bool(lecteur) and reste[:1] in ('\\', '/')


def _lecteur_majuscule(p):
    lecteur, reste = os.path.splitdrive(p)
    return lecteur.upper() + reste if len(lecteur) == 2 and lecteur[1] == ':' else p


def normaliser_chemin(chemin):
    """Chemin absolu et propre : « e:\\Photos\\ », « "E:\\Photos" » et « E:/Photos » donnent « E:\\Photos ».

    Un chemin absolu qui désigne TEL QUEL un dossier existant (celui de la fenêtre « Choisir un dossier ») est gardé
    exactement, même s'il finit par un point ou une espace (« Vacances. » : un AUTRE dossier que « Vacances ») :
    seulement os.path.normpath, purement textuel. Sinon (texte saisi), le chemin est nettoyé comme avant (espaces,
    guillemets, puis os.path.abspath, qui retire aussi les points et espaces finaux)."""
    brut = chemin_affiche(os.fspath(chemin))
    if brut and _absolu(brut):
        exact = os.path.normpath(brut)
        try:
            if os.path.isdir(chemin_systeme(exact)):
                return _lecteur_majuscule(exact)
        except (OSError, ValueError):
            pass
    p = brut.strip().strip('"').strip()
    if not p:
        raise DossierRefuse('Aucun dossier indiqué.')
    if WINDOWS and len(p) == 2 and p[1] == ':':
        p += '\\'                                     # « E: » = la racine du disque, pas son dossier courant
    p = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
    return _lecteur_majuscule(p)


def casse_reelle(chemin):
    """Remet chaque élément du chemin dans l'écriture du disque (« e:\\photos » -> « E:\\Photos »)."""
    if not WINDOWS:
        return chemin
    lecteur, reste = os.path.splitdrive(chemin)
    morceaux = [m for m in reste.split(os.sep) if m]
    courant = lecteur + os.sep
    for i, m in enumerate(morceaux):
        reel = None
        try:
            with os.scandir(chemin_systeme(courant)) as it:
                for e in it:
                    if e.name == m:
                        reel = m
                        break
                    if reel is None and e.name.lower() == m.lower():
                        reel = e.name
        except OSError:
            return os.path.join(courant, *morceaux[i:])
        courant = os.path.join(courant, reel or m)
    return courant


# ---------------------------------------------------------------- base

class _Connexion:
    """Connexion d'un fil, fermée proprement quand le fil se termine."""
    __slots__ = ('c',)

    def __init__(self, c):
        self.c = c

    def __del__(self):
        try:
            self.c.close()
        except Exception:
            pass


def _paquets(valeurs, n=PAQUET):
    valeurs = list(valeurs)
    for i in range(0, len(valeurs), n):
        yield valeurs[i:i + n]


class Base:
    """Index des photos (voir l'en-tête du module). Utilisable depuis plusieurs fils à la fois.

    Un fichier qui n'est plus une base lisible (disque abîmé, fichier écrasé) est mis de côté à l'ouverture, renommé
    « index.db.abimee-AAAAMMJJ-HHMMSS » sans rien effacer, puis recréé vide : `ecartes` donne les nouveaux noms.
    Sans cela, MyPhotos refuserait de démarrer à chaque lancement.
    """

    def __init__(self, chemin_index, chemin_miniatures):
        self.chemin_index = os.path.abspath(os.fspath(chemin_index))
        self.chemin_miniatures = os.path.abspath(os.fspath(chemin_miniatures))
        for p in (self.chemin_index, self.chemin_miniatures):
            os.makedirs(os.path.dirname(p), exist_ok=True)
        self._local = threading.local()
        self._connexions = []                         # pour fermer() ; nettoyée des fils terminés
        self._verrou_connexions = threading.Lock()
        self._ecriture = threading.RLock()            # une écriture à la fois dans ce programme
        self._fermee = False
        self.ecartes = []
        # Numéro des lieux (0.5.0) : change à chaque écriture de lieux_photos par ce programme (enregistrer_lieux), que
        # la génération ne suit pas ; numéro des oublis : à chaque photo retirée ou cachée par ce programme. Tous deux
        # changent APRÈS le COMMIT (une lecture faite avant ne peut pas être gardée sous le nouveau numéro) ; ils
        # disent quand relire lieux_presents() et les listes filtrées (serveur.Application).
        self.numero_lieux = 0
        self.numero_oublis = 0
        try:
            with self._ecriture:
                self._migrer(self._cnx())
        except sqlite3.DatabaseError as e:
            if not _abimee(e):
                raise
            self._oublier_connexions()
            self.ecartes = self._ecarter_abimes()
            if not self.ecartes:
                raise
            with self._ecriture:
                self._migrer(self._cnx())

    def _ecarter_abimes(self):
        """Renomme (journaux WAL compris) ceux des deux fichiers qui ne sont plus une base lisible ; ils seront
        recréés vides. Renvoie les nouveaux noms des bases mises de côté."""
        quand = datetime.now().strftime('%Y%m%d-%H%M%S')
        ecartes = []
        for p in (self.chemin_index, self.chemin_miniatures):
            if not os.path.exists(p) or _lisible(p):
                continue
            for suffixe in ('', '-wal', '-shm'):
                if os.path.exists(p + suffixe):
                    os.replace(p + suffixe, f'{p}.abimee-{quand}{suffixe}')
            ecartes.append(f'{p}.abimee-{quand}')
        return ecartes

    # ------------------------------------------------ connexions et transactions

    def _cnx(self):
        h = getattr(self._local, 'h', None)
        if h is not None:
            return h.c
        if self._fermee:
            raise RuntimeError('base fermée')
        c = sqlite3.connect(self.chemin_index, timeout=30, isolation_level=None, check_same_thread=False)
        try:
            # auto_vacuum avant le WAL, et sur une base neuve seulement : sur une base existante il est sans effet,
            # mais il demande le verrou d'écriture. Chaque connexion neuve (chaque fil du serveur) attendait alors la
            # fin de l'écriture en cours, et MyPhotos ne démarrait plus si un autre programme écrivait dans la base.
            if not c.execute('PRAGMA page_count').fetchone()[0]:
                c.execute('PRAGMA auto_vacuum = INCREMENTAL')
            c.execute('PRAGMA journal_mode = WAL').fetchall()
            c.execute('PRAGMA synchronous = NORMAL')
            c.execute('PRAGMA temp_store = MEMORY')
            c.execute('ATTACH DATABASE ? AS mini', (self.chemin_miniatures,))
            if not c.execute('PRAGMA mini.page_count').fetchone()[0]:
                c.execute('PRAGMA mini.auto_vacuum = INCREMENTAL')
            c.execute('PRAGMA mini.journal_mode = WAL').fetchall()
            c.execute('PRAGMA mini.synchronous = NORMAL')
        except BaseException:
            c.close()                                 # sinon le fichier reste ouvert (et impossible à renommer)
            raise
        h = _Connexion(c)
        self._local.h = h
        with self._verrou_connexions:
            self._connexions = [(f, x) for f, x in self._connexions if f.is_alive()]
            self._connexions.append((threading.current_thread(), h))
        return c

    @contextmanager
    def _transaction(self):
        """Transaction d'écriture : tout ou rien, une seule à la fois. Une photo retirée ou cachée dedans (_oubli) fait
        changer numero_oublis, après le COMMIT."""
        with self._ecriture:
            c = self._cnx()
            c.execute('BEGIN IMMEDIATE')
            self._local.oubli = False
            try:
                yield c
            except BaseException:
                self._local.oubli = False
                c.execute('ROLLBACK')
                raise
            c.execute('COMMIT')
            if self._local.oubli:
                self._local.oubli = False
                self.numero_oublis += 1

    def _oubli(self):
        """Dans une transaction : des photos sont retirées ou cachées (numero_oublis changera après le COMMIT)."""
        self._local.oubli = True

    @contextmanager
    def _lecture(self):
        """Plusieurs lectures cohérentes entre elles (même instantané)."""
        c = self._cnx()
        c.execute('BEGIN')
        try:
            yield c
        finally:
            c.execute('COMMIT')

    def fermer(self):
        """Ferme toutes les connexions (à l'arrêt du programme, une fois l'indexation arrêtée)."""
        self._fermee = True
        self._oublier_connexions()

    def _oublier_connexions(self):
        with self._verrou_connexions:
            liste, self._connexions = self._connexions, []
        for _, h in liste:
            try:
                h.c.close()
            except Exception:
                pass
        self._local = threading.local()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fermer()

    def _migrer(self, c):
        """Crée ou met à jour les tables (PRAGMA user_version = version du schéma)."""
        for schema, tables in (('main', _SCHEMA_INDEX), ('mini', _SCHEMA_MINIATURES)):
            version = c.execute(f'PRAGMA {schema}.user_version').fetchone()[0]
            if version > VERSION_SCHEMA:
                raise RuntimeError('Cet index a été créé par une version plus récente de MyPhotos.')
            if version < 1:
                c.execute('BEGIN IMMEDIATE')
                try:
                    for sql in tables:
                        c.execute(sql)
                    c.execute(f'PRAGMA {schema}.user_version = 1')
                except BaseException:
                    c.execute('ROLLBACK')
                    raise
                c.execute('COMMIT')
            # Migrations futures : « if version < 2: … » ici, dans une transaction.
        # 0.3.0 : table des sous-dossiers exclus. Créée seulement si elle manque : sur une base à jour, aucune écriture
        # (pas de verrou d'écriture demandé à chaque ouverture).
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'exclusions'").fetchone():
            c.execute('BEGIN IMMEDIATE')
            try:
                for sql in _SCHEMA_EXCLUSIONS:
                    c.execute(sql)
            except BaseException:
                c.execute('ROLLBACK')
                raise
            c.execute('COMMIT')
        # 0.4.0 : numéro de volume des dossiers suivis (voir l'en-tête), même principe ; revérifié sous le verrou.
        if not self._a_colonne(c, 'dossiers', 'volume'):
            c.execute('BEGIN IMMEDIATE')
            try:
                if not self._a_colonne(c, 'dossiers', 'volume'):
                    c.execute('ALTER TABLE dossiers ADD COLUMN volume INTEGER')
            except BaseException:
                c.execute('ROLLBACK')
                raise
            c.execute('COMMIT')
        # 0.5.0 : lieux des photos et index des positions (voir l'en-tête), même principe. L'index de photos se construit
        # une seule fois, à la première ouverture par une 0.5 (0,2 s pour 10 000 photos).
        if self._schema_lieux_manquant(c):
            c.execute('BEGIN IMMEDIATE')
            try:
                for sql in _SCHEMA_LIEUX:
                    c.execute(sql)
            except BaseException:
                c.execute('ROLLBACK')
                raise
            c.execute('COMMIT')

    @staticmethod
    def _schema_lieux_manquant(c):
        noms = {n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE name IN ('lieux_photos', 'photos_gps')")}
        return len(noms) < 2

    @staticmethod
    def _a_colonne(c, table, colonne):
        return any(l[1] == colonne for l in c.execute(f'PRAGMA table_info({table})'))

    @staticmethod
    def _nouvelle_generation(c):
        c.execute("UPDATE meta SET valeur = valeur + 1 WHERE cle = 'generation'")

    def _liberer_place(self):
        """Rend au disque la place des lignes effacées (auto_vacuum incrémental)."""
        c = self._cnx()
        with self._ecriture:
            for schema in ('main', 'mini'):
                try:
                    c.execute(f'PRAGMA {schema}.incremental_vacuum').fetchall()
                except sqlite3.Error:
                    pass

    # ------------------------------------------------ réglages

    def meta_get(self, cle, defaut=None):
        r = self._cnx().execute('SELECT valeur FROM meta WHERE cle = ?', (cle,)).fetchone()
        return defaut if r is None else r[0]

    def meta_set(self, cle, valeur):
        with self._transaction() as c:
            c.execute('INSERT INTO meta(cle, valeur) VALUES (?, ?) '
                      'ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur', (cle, valeur))

    def generation(self):
        """Compteur qui change à chaque modification des vecteurs (la recherche s'en sert pour se recharger)."""
        r = self._cnx().execute("SELECT valeur FROM meta WHERE cle = 'generation'").fetchone()
        return int(r[0]) if r and r[0] is not None else 0

    # ------------------------------------------------ dossiers suivis

    @staticmethod
    def _stats_dossiers(c):
        return {d: (n, a or 0, e or 0) for d, n, a, e in c.execute(
            'SELECT dossier_id, COUNT(*), SUM(modele IS NOT NULL), SUM(erreur IS NOT NULL) '
            'FROM photos GROUP BY dossier_id')}

    @staticmethod
    def _dossier_dict(ligne, stats):
        did, chemin, ajoute, disponible, vu, volume = ligne
        n, a, e = stats.get(did, (0, 0, 0))
        return {'id': did, 'chemin': chemin, 'ajoute': ajoute, 'disponible': bool(disponible), 'vu': vu,
                'volume': volume, 'photos': n, 'analysees': a, 'erreurs': e, 'a_faire': n - a - e}

    def dossiers(self):
        """Dossiers suivis, par ordre alphabétique, avec leurs comptes de photos (et 'volume' : numéro du volume où
        chacun a été vu la dernière fois, None si inconnu)."""
        with self._lecture() as c:
            stats = self._stats_dossiers(c)
            lignes = c.execute(f'SELECT {_COLONNES_DOSSIER} FROM dossiers '
                               'ORDER BY chemin COLLATE NOCASE').fetchall()
        return [self._dossier_dict(l, stats) for l in lignes]

    def dossier(self, dossier_id):
        with self._lecture() as c:
            l = c.execute(f'SELECT {_COLONNES_DOSSIER} FROM dossiers WHERE id = ?',
                          (dossier_id,)).fetchone()
            stats = self._stats_dossiers(c) if l else {}
        return self._dossier_dict(l, stats) if l else None

    def ajouter_dossier(self, chemin):
        """Suit un nouveau dossier. Lève DossierRefuse (message clair) s'il est introuvable, déjà suivi ou
        situé dans un dossier suivi. S'il contient des dossiers suivis, il les remplace et garde leurs photos.

        Renvoie le dossier (comme dossiers()) avec en plus 'remplaces' : chemins des dossiers absorbés.
        L'inventaire n'est pas lancé ici : appeler ensuite Indexeur.relancer().
        """
        p = self.verifier_dossier(chemin)
        volume = identite_volume(p)
        with self._transaction() as c:
            remplaces = self._place_du_dossier(c, p)
            nid = c.execute('INSERT INTO dossiers(chemin, ajoute, disponible, volume) VALUES (?, ?, 1, ?)',
                            (p, maintenant(), volume)).lastrowid
            for did, _ in remplaces:
                c.execute('UPDATE photos SET dossier_id = ? WHERE dossier_id = ?', (nid, did))
                # Les sous-dossiers exclus d'un dossier absorbé restent exclus du nouveau.
                c.execute('UPDATE exclusions SET dossier_id = ? WHERE dossier_id = ?', (nid, did))
                c.execute('DELETE FROM dossiers WHERE id = ?', (did,))
        d = self.dossier(nid)
        d['remplaces'] = [ch for _, ch in remplaces]
        return d

    def verifier_dossier(self, chemin):
        """Chemin propre d'un dossier qu'on peut ajouter (mêmes contrôles qu'ajouter_dossier, sans rien enregistrer).
        Lève DossierRefuse avec un message clair sinon."""
        p = normaliser_chemin(chemin)
        if not enregistrable(p):
            raise DossierRefuse('Le nom de ce dossier contient un caractère que MyPhotos ne sait pas enregistrer : '
                                'renommez-le, puis recommencez.')
        if not os.path.isdir(chemin_systeme(p)):
            raise DossierRefuse(f'Ce dossier est introuvable : « {p} ».')
        # 0.4.1 : noms longs (« VACANC~1 » -> « Vacances d'été 2021 »), sans traverser de jonction : c'est ainsi que
        # l'inventaire le verra, et qu'il est comparé aux autres.
        p = casse_reelle(nom_long(p) or p)
        with self._lecture() as c:
            self._place_du_dossier(c, p)
        return p

    @staticmethod
    def _place_du_dossier(c, p):
        """Dossiers suivis que le nouveau dossier `p` contient [(id, chemin)]. DossierRefuse s'il est déjà suivi,
        dans un dossier suivi, ou dans un sous-dossier exclu : un dossier suivi ne peut donc jamais se trouver dans un
        sous-dossier exclu (le message dit quoi faire).

        Depuis la 0.4.1, la comparaison se fait aussi sur l'emplacement réel (chemin_reel : noms longs, jonctions
        résolues) et sur l'identité du dossier (identite_fichier), pour les dossiers suivis joignables : le même
        dossier écrit autrement (nom court, jonction, préfixe \\\\?\\) est reconnu. Un dossier vu à travers une jonction
        est situé comme l'inventaire du dossier suivi le verrait (sous_le_dossier), puis comparé aux exclusions comme
        l'inventaire les applique (écriture enregistrée et noms longs, jamais à travers une jonction). Un dossier qui
        contient un dossier suivi sous une autre écriture est refusé (retirer d'abord l'autre) : l'absorber laisserait
        ses exclusions sous l'ancienne écriture."""
        k = cle_chemin(p)
        reel = chemin_reel(p)
        kr = cle_chemin(reel) if reel else None
        identite = identite_fichier(p)
        remplaces = []
        for did, ch, dispo in c.execute('SELECT id, chemin, disponible FROM dossiers').fetchall():
            ke = cle_chemin(ch)
            r = chemin_reel(ch) if dispo else None      # (un dossier injoignable n'est pas interrogé : partage éteint)
            ker = cle_chemin(r) if r else None
            if ke == k or (kr and ker == kr) or (identite and dispo and identite_fichier(ch) == identite):
                raise DossierRefuse(f'Ce dossier est déjà dans MyPhotos : « {ch} ».')
            dans_texte = dedans(k, ke)
            if dans_texte or (kr and ker and dedans(kr, ker)):
                vu = p if dans_texte else (sous_le_dossier(p, ch) or p)
                cles = {cle_chemin(vu), cle_chemin(nom_long(vu) or vu)}
                for (ex,) in c.execute('SELECT chemin FROM exclusions WHERE dossier_id = ?', (did,)).fetchall():
                    exclues = {cle_chemin(ex), cle_chemin(nom_long(ex) or ex)}
                    if any(a == b or dedans(a, b) for a in cles for b in exclues):
                        raise DossierRefuse(
                            f'Ce dossier se trouve dans « {ex} », que vous avez exclu de MyPhotos. Pour voir ses '
                            'photos, choisissez « Ne plus exclure » pour ce sous-dossier, dans les Réglages.')
                raise DossierRefuse(f'Ce dossier fait déjà partie de « {ch} », qui est dans MyPhotos.')
            if dedans(ke, k):
                remplaces.append((did, ch))
            elif kr and ker and dedans(ker, kr):
                raise DossierRefuse(f'Ce dossier contient « {ch} », déjà dans MyPhotos sous une autre écriture (nom '
                                    f'court ou raccourci de dossier). Retirez d\'abord « {ch} » dans les Réglages, '
                                    'puis recommencez.')
        return remplaces

    def retirer_dossier(self, dossier_id):
        """Oublie un dossier, ses photos et leurs miniatures. Les fichiers ne sont JAMAIS touchés.
        Renvoie {'id', 'chemin', 'photos': nombre oublié}, ou None si le dossier n'existe pas."""
        with self._transaction() as c:
            l = c.execute('SELECT chemin FROM dossiers WHERE id = ?', (dossier_id,)).fetchone()
            if l is None:
                return None
            n = c.execute('SELECT COUNT(*) FROM photos WHERE dossier_id = ?', (dossier_id,)).fetchone()[0]
            ids = [i for (i,) in c.execute('SELECT id FROM photos WHERE dossier_id = ?', (dossier_id,))]
            self._effacer_photos(c, ids)
            c.execute('DELETE FROM exclusions WHERE dossier_id = ?', (dossier_id,))
            c.execute('DELETE FROM dossiers WHERE id = ?', (dossier_id,))
            self._nouvelle_generation(c)
        self._liberer_place()
        return {'id': dossier_id, 'chemin': l[0], 'photos': n}

    # ------------------------------------------------ sous-dossiers exclus

    def exclusions(self, dossier_id=None):
        """Sous-dossiers exclus [{id, dossier_id, chemin, ajoute}], d'un dossier ou de tous, par ordre alphabétique."""
        sql, args = 'SELECT id, dossier_id, chemin, ajoute FROM exclusions', ()
        if dossier_id is not None:
            sql, args = sql + ' WHERE dossier_id = ?', (dossier_id,)
        return [{'id': i, 'dossier_id': d, 'chemin': ch, 'ajoute': a}
                for i, d, ch, a in self._cnx().execute(sql + ' ORDER BY chemin COLLATE NOCASE', args)]

    def ajouter_exclusion(self, dossier_id, chemin):
        """Exclut un sous-dossier d'un dossier suivi : ses photos sont oubliées tout de suite (base, miniatures,
        empreintes des doublons) et l'inventaire ne le parcourt plus. Les fichiers ne sont JAMAIS touchés.

        Le chemin se compare comme partout (normaliser_chemin, casse_reelle, cle_chemin : « Vacances. » reste
        « Vacances. »). Saisi par un nom court (8.3) ou à travers une jonction, il est d'abord ramené au vrai dossier vu
        depuis le dossier suivi (sous_le_dossier), le seul chemin que l'inventaire connaît : sinon l'exclusion restait
        sans effet. Lève DossierRefuse (message clair) s'il n'est pas DANS le dossier suivi, s'il est déjà exclu ou
        introuvable. Les exclusions qu'il contient sont remplacées par lui. Renvoie {id, dossier_id, chemin, dossier,
        photos (nombre oublié), remplacees (chemins)}."""
        p = normaliser_chemin(chemin)
        if not enregistrable(p):
            raise DossierRefuse('Le nom de ce dossier contient un caractère que MyPhotos ne sait pas enregistrer.')
        if not os.path.isdir(chemin_systeme(p)):
            raise DossierRefuse(f'Ce dossier est introuvable : « {p} ».')
        p = casse_reelle(p)
        l = self._cnx().execute('SELECT chemin FROM dossiers WHERE id = ?', (dossier_id,)).fetchone()
        reel = sous_le_dossier(p, l[0]) if l else None
        if reel and enregistrable(reel):
            p = reel
        k = cle_chemin(p)
        with self._transaction() as c:
            l = c.execute('SELECT chemin FROM dossiers WHERE id = ?', (dossier_id,)).fetchone()
            if l is None:
                raise DossierRefuse('Ce dossier ne fait plus partie de MyPhotos.')
            racine = l[0]
            kd = cle_chemin(racine)
            if k == kd:
                raise DossierRefuse(f'Vous avez choisi « {racine} » lui-même. Pour ne plus voir ses photos, utilisez '
                                    '« Retirer ». Pour en exclure une partie, choisissez un dossier qui se trouve '
                                    'dedans.')
            if not dedans(k, kd):
                raise DossierRefuse(f'Ce dossier n\'est pas dans « {racine} » : choisissez un dossier qui se trouve à '
                                    'l\'intérieur.')
            remplacees = []
            for eid, ex in c.execute('SELECT id, chemin FROM exclusions WHERE dossier_id = ?',
                                     (dossier_id,)).fetchall():
                kx = cle_chemin(ex)
                if kx == k:
                    raise DossierRefuse(f'Ce dossier est déjà exclu : « {ex} ».')
                if dedans(k, kx):
                    raise DossierRefuse(f'Ce dossier est déjà exclu, avec « {ex} » qui le contient.')
                if dedans(kx, k):
                    remplacees.append((eid, ex))
            for eid, _ in remplacees:
                c.execute('DELETE FROM exclusions WHERE id = ?', (eid,))
            nid = c.execute('INSERT INTO exclusions(dossier_id, chemin, ajoute) VALUES (?, ?, ?)',
                            (dossier_id, p, maintenant())).lastrowid
            ids = [pid for pid, ch in c.execute('SELECT id, chemin FROM photos WHERE dossier_id = ?',
                                                (dossier_id,)).fetchall()
                   if dedans(cle_chemin(ch), k)]
            self._effacer_photos(c, ids)
        if ids:
            self._liberer_place()
        return {'id': nid, 'dossier_id': dossier_id, 'chemin': p, 'dossier': racine, 'photos': len(ids),
                'remplacees': [ex for _, ex in remplacees]}

    def retirer_exclusion(self, exclusion_id):
        """« Ne plus exclure » : renvoie {id, dossier_id, chemin}, ou None si l'exclusion n'existe pas. L'inventaire
        de son dossier est à relancer (Indexeur.relancer) pour retrouver ses photos."""
        with self._transaction() as c:
            l = c.execute('SELECT dossier_id, chemin FROM exclusions WHERE id = ?', (exclusion_id,)).fetchone()
            if l is None:
                return None
            c.execute('DELETE FROM exclusions WHERE id = ?', (exclusion_id,))
        return {'id': exclusion_id, 'dossier_id': l[0], 'chemin': l[1]}

    def _effacer_photos(self, c, ids):
        """Dans une transaction : efface des photos de la base, leurs miniatures, leurs empreintes de doublons et leurs
        lieux."""
        if not ids:
            return
        tables = {n for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        doublons = tables.issuperset(_TABLES_DOUBLONS)
        lieux = 'lieux_photos' in tables
        for paquet in _paquets(ids):
            marques = ','.join('?' * len(paquet))
            c.execute(f'DELETE FROM mini.miniatures WHERE id IN ({marques})', paquet)
            c.execute(f'DELETE FROM photos WHERE id IN ({marques})', paquet)
            if doublons:
                c.execute(f'DELETE FROM doublons_empreintes WHERE photo_id IN ({marques})', paquet)
                c.execute(f'DELETE FROM doublons_paires WHERE a IN ({marques}) OR b IN ({marques})', paquet + paquet)
            if lieux:
                c.execute(f'DELETE FROM lieux_photos WHERE photo_id IN ({marques})', paquet)
        self._nouvelle_generation(c)
        self._oubli()

    # ------------------------------------------------ photos

    @staticmethod
    def _photo_dict(l):
        (pid, chemin, did, taille, modifie, largeur, hauteur, date_prise, date_fichier, lat, lon, mod, err, vu,
         mini, dossier, dispo, volume) = l
        return {'id': pid, 'chemin': chemin, 'nom': os.path.basename(chemin), 'dossier_id': did,
                'dossier': dossier, 'disponible': bool(dispo), 'volume': volume, 'taille': taille, 'modifie': modifie,
                'largeur': largeur, 'hauteur': hauteur, 'date_prise': date_prise, 'date_fichier': date_fichier,
                'date': date_prise or date_fichier, 'lat': lat, 'lon': lon, 'modele': mod,
                'analysee': mod is not None, 'erreur': err, 'vu': vu, 'miniature': bool(mini)}

    def photo(self, photo_id):
        """Tout ce qu'on sait d'une photo (sans son vecteur), ou None."""
        l = self._cnx().execute(f'SELECT {_COLONNES_PHOTO} FROM photos p LEFT JOIN dossiers d '
                                'ON d.id = p.dossier_id WHERE p.id = ?', (photo_id,)).fetchone()
        return self._photo_dict(l) if l else None

    def photos(self, ids):
        """Comme photo(), pour une liste d'identifiants ; même ordre, les inconnus sont omis."""
        ids = [int(i) for i in ids]
        trouves = {}
        c = self._cnx()
        for paquet in _paquets(ids):
            marques = ','.join('?' * len(paquet))
            for l in c.execute(f'SELECT {_COLONNES_PHOTO} FROM photos p LEFT JOIN dossiers d '
                               f'ON d.id = p.dossier_id WHERE p.id IN ({marques})', paquet):
                trouves[l[0]] = self._photo_dict(l)
        return [trouves[i] for i in ids if i in trouves]

    def miniature(self, photo_id):
        """Vignette JPEG (plus grand côté 360 px), ou None."""
        l = self._cnx().execute('SELECT jpeg FROM mini.miniatures WHERE id = ?', (photo_id,)).fetchone()
        return l[0] if l else None

    def compter(self):
        """{total, analysees, a_faire, erreurs, indisponibles, en_attente}.

        total = analysees + a_faire + erreurs. indisponibles : photos d'un dossier injoignable (disque
        débranché…) ; en_attente : celles d'entre elles qui restent à analyser (comprises dans a_faire).
        """
        with self._lecture() as c:
            stats = self._stats_dossiers(c)
            dispo = dict(c.execute('SELECT id, disponible FROM dossiers').fetchall())
        total = analysees = erreurs = indisponibles = en_attente = 0
        for did, (n, a, e) in stats.items():
            total += n
            analysees += a
            erreurs += e
            if not dispo.get(did, 0):
                indisponibles += n
                en_attente += n - a - e
        return {'total': total, 'analysees': analysees, 'a_faire': total - analysees - erreurs,
                'erreurs': erreurs, 'indisponibles': indisponibles, 'en_attente': en_attente}

    def photos_par_date(self, debut=0, n=200, dossier_id=None):
        """Photos qui ont une miniature, les plus récentes d'abord (date de prise de vue, sinon du fichier).

        Renvoie {'total', 'debut', 'photos': [{id, date, largeur, hauteur}]}.
        """
        debut, n = max(0, int(debut)), max(0, int(n))
        filtre, args = 'miniature = 1', ()
        if dossier_id is not None:
            filtre, args = 'miniature = 1 AND dossier_id = ?', (dossier_id,)
        with self._lecture() as c:
            total = c.execute(f'SELECT COUNT(*) FROM photos WHERE {filtre}', args).fetchone()[0]
            lignes = c.execute(f'SELECT id, COALESCE(date_prise, date_fichier), largeur, hauteur FROM photos '
                               f'WHERE {filtre} ORDER BY COALESCE(date_prise, date_fichier) DESC, id DESC '
                               'LIMIT ? OFFSET ?', args + (n, debut)).fetchall()
        return {'total': total, 'debut': debut,
                'photos': [{'id': i, 'date': d, 'largeur': w, 'hauteur': h} for i, d, w, h in lignes]}

    def vecteurs(self, modele):
        """Vecteurs calculés par ce modèle : (ids int64 (n,), matrice float32 (n, dim) normalisée,
        dates datetime64[s] (n,), NaT si inconnue). Les plus récentes d'abord."""
        with self._lecture() as c:
            n = c.execute('SELECT COUNT(*) FROM photos WHERE modele = ?', (modele,)).fetchone()[0]
            ids = np.empty(n, np.int64)
            dates = [None] * n
            mat = None
            k = 0
            for pid, blob, date in c.execute(
                    'SELECT id, vecteur, COALESCE(date_prise, date_fichier) FROM photos WHERE modele = ? '
                    'ORDER BY COALESCE(date_prise, date_fichier) DESC, id DESC', (modele,)):
                if blob is None or k >= n:
                    continue
                if mat is None:
                    mat = np.empty((n, len(blob) // 2), np.float16)
                if len(blob) != 2 * mat.shape[1]:
                    continue                          # vecteur d'une autre taille : ignoré
                mat[k] = np.frombuffer(blob, np.float16)
                ids[k] = pid
                dates[k] = date
                k += 1
        if mat is None:
            return np.empty(0, np.int64), np.empty((0, 0), np.float32), np.empty(0, 'datetime64[s]')
        m = mat[:k].astype(np.float32)
        m /= np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)   # corrige l'arrondi float16
        return ids[:k], m, np.array(dates[:k], dtype='datetime64[s]')

    def tailles(self):
        """Place occupée sur le disque, en octets : {'index', 'miniatures'} (journaux WAL compris)."""
        def taille(p):
            return sum(os.path.getsize(p + s) for s in ('', '-wal') if os.path.exists(p + s))
        return {'index': taille(self.chemin_index), 'miniatures': taille(self.chemin_miniatures)}

    # ------------------------------------------------ pour l'indexeur

    def connus(self, dossier_id):
        """{chemin: (id, taille, modifie)} des photos connues d'un dossier."""
        return {ch: (pid, t, m) for pid, ch, t, m in self._cnx().execute(
            'SELECT id, chemin, taille, modifie FROM photos WHERE dossier_id = ?', (dossier_id,))}

    def ajouter_photos(self, dossier_id, lignes):
        """Nouvelles photos [(chemin, taille, modifie, date_fichier)]. Renvoie le nombre ajouté.

        Chaque photo reçoit un identifiant jamais donné auparavant. Laissé à lui-même, SQLite redonne le plus haut
        identifiant libéré (photo oubliée, fiche vierge d'un déplacement) : une sélection restée dans la page, un groupe
        de doublons ou une miniature en cache désignaient alors une AUTRE photo (et « Mettre à la Corbeille » l'aurait
        envoyée à la Corbeille). Le plus haut identifiant donné est gardé dans meta (photo_id_max) : une 0.3.0 l'ignore,
        et une base 0.3.0, qui n'en a pas, repart du plus haut identifiant présent."""
        quand = maintenant()
        with self._transaction() as c:
            if not c.execute('SELECT 1 FROM dossiers WHERE id = ?', (dossier_id,)).fetchone():
                return 0                              # dossier retiré entre-temps
            # Sous-dossier exclu pendant que l'inventaire était en route : ses photos ne reviennent pas.
            exclus = [cle_chemin(ex) for (ex,) in c.execute('SELECT chemin FROM exclusions WHERE dossier_id = ?',
                                                            (dossier_id,))]
            if exclus:
                lignes = [x for x in lignes if not any(dedans(cle_chemin(x[0]), kx) for kx in exclus)]
            if not lignes:
                return 0
            m = c.execute("SELECT valeur FROM meta WHERE cle = 'photo_id_max'").fetchone()
            plancher = max(int(m[0] or 0) if m else 0,
                           c.execute('SELECT COALESCE(MAX(id), 0) FROM photos').fetchone()[0],
                           c.execute('SELECT COALESCE(MAX(id), 0) FROM mini.miniatures').fetchone()[0])
            avant = c.total_changes
            c.executemany('INSERT INTO photos(id, chemin, dossier_id, taille, modifie, date_fichier, vu) '
                          'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chemin) DO NOTHING',
                          [(plancher + 1 + i, ch, dossier_id, t, m, df, quand)
                           for i, (ch, t, m, df) in enumerate(lignes)])
            ajoutees = c.total_changes - avant
            c.execute("INSERT INTO meta(cle, valeur) VALUES ('photo_id_max', ?) "
                      'ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur', (plancher + len(lignes),))
            return ajoutees

    def marquer_modifiees(self, lignes):
        """Fichiers changés [(id, taille, modifie, date_fichier)] : à analyser de nouveau. La miniature reste
        affichée jusqu'à la nouvelle analyse."""
        with self._transaction() as c:
            c.executemany('UPDATE photos SET taille = ?, modifie = ?, date_fichier = ?, vecteur = NULL, '
                          'modele = NULL, erreur = NULL WHERE id = ?',
                          [(t, m, df, pid) for pid, t, m, df in lignes])
            self._nouvelle_generation(c)

    def oublier_photos(self, ids):
        """Photos disparues du disque : on les oublie (base, miniatures, empreintes des doublons)."""
        ids = [int(i) for i in ids]
        with self._transaction() as c:
            self._effacer_photos(c, ids)

    def deplacer_photos(self, paires):
        """Photos déplacées ou renommées [(ancien_id, nouveau_chemin)] : l'ancienne fiche (analyse, miniature,
        identifiant) prend le nouveau chemin ; la fiche vierge créée par l'inventaire est supprimée."""
        with self._transaction() as c:
            for ancien, chemin in paires:
                l = c.execute('SELECT id, dossier_id, date_fichier FROM photos WHERE chemin = ?', (chemin,)).fetchone()
                if l is None or l[0] == ancien:
                    continue
                c.execute('DELETE FROM mini.miniatures WHERE id = ?', (l[0],))
                c.execute('DELETE FROM lieux_photos WHERE photo_id = ?', (l[0],))
                c.execute('DELETE FROM photos WHERE id = ?', (l[0],))
                c.execute('UPDATE photos SET chemin = ?, dossier_id = ?, date_fichier = ? WHERE id = ?',
                          (chemin, l[1], l[2], ancien))
            self._nouvelle_generation(c)
            self._oubli()

    def marquer_dossier(self, dossier_id, disponible, vu=None, volume=None):
        """Dossier joignable ou non (disque débranché, partage réseau éteint) ; ses photos restent connues. vu et volume
        (numéro du volume où il vient d'être vu) ne changent que s'ils sont donnés."""
        with self._transaction() as c:
            c.execute('UPDATE dossiers SET disponible = ?, vu = COALESCE(?, vu), volume = COALESCE(?, volume) '
                      'WHERE id = ?', (int(bool(disponible)), vu, volume, dossier_id))

    def a_analyser(self):
        """Photos à analyser dans les dossiers joignables, les plus récentes d'abord :
        [(id, chemin, taille, modifie, dossier_id, chemin_du_dossier)]."""
        return self._cnx().execute(
            'SELECT p.id, p.chemin, p.taille, p.modifie, p.dossier_id, d.chemin '
            'FROM photos p JOIN dossiers d ON d.id = p.dossier_id '
            'WHERE p.modele IS NULL AND p.erreur IS NULL AND d.disponible = 1 '
            'ORDER BY COALESCE(p.date_prise, p.date_fichier) DESC, p.id DESC').fetchall()

    def enregistrer(self, analyses, erreurs, modele):
        """Écrit un lot dans une transaction.

        analyses : [{id, taille, modifie, largeur, hauteur, date_prise, lat, lon, vecteur (octets float16),
        miniature (JPEG)}] ; erreurs : [{id, taille, modifie, erreur, largeur, hauteur}].
        Une photo modifiée ou retirée entre-temps (taille/date différentes, fiche disparue) est ignorée, et tout le lot
        l'est si un autre moteur a été adopté entre-temps (voir adopter_modele) : ses vecteurs ne serviraient à rien.
        """
        ecrites = 0
        with self._transaction() as c:
            actuel = c.execute("SELECT valeur FROM meta WHERE cle = 'modele'").fetchone()
            if actuel is not None and actuel[0] is not None and actuel[0] != modele:
                return 0
            for a in analyses:
                # Lieu calculé pour une autre position (fichier modifié) : refait (Base.a_localiser).
                c.execute('DELETE FROM lieux_photos WHERE photo_id = ? AND EXISTS (SELECT 1 FROM photos WHERE id = ? '
                          'AND taille = ? AND modifie = ? AND (lat IS NOT ? OR lon IS NOT ?))',
                          (a['id'], a['id'], a['taille'], a['modifie'], a['lat'], a['lon']))
                r = c.execute('UPDATE photos SET largeur = ?, hauteur = ?, date_prise = ?, lat = ?, lon = ?, '
                              'vecteur = ?, modele = ?, erreur = NULL, miniature = 1 '
                              'WHERE id = ? AND taille = ? AND modifie = ?',
                              (a['largeur'], a['hauteur'], a['date_prise'], a['lat'], a['lon'], a['vecteur'],
                               modele, a['id'], a['taille'], a['modifie']))
                if r.rowcount:
                    c.execute('INSERT OR REPLACE INTO mini.miniatures(id, jpeg) VALUES (?, ?)',
                              (a['id'], a['miniature']))
                    ecrites += 1
            for e in erreurs:
                r = c.execute('UPDATE photos SET erreur = ?, largeur = ?, hauteur = ?, vecteur = NULL, '
                              'modele = NULL, miniature = 0 WHERE id = ? AND taille = ? AND modifie = ?',
                              (e['erreur'], e.get('largeur'), e.get('hauteur'), e['id'], e['taille'], e['modifie']))
                if r.rowcount:
                    c.execute('DELETE FROM mini.miniatures WHERE id = ?', (e['id'],))
                    c.execute('DELETE FROM lieux_photos WHERE photo_id = ?', (e['id'],))
                    ecrites += 1
            if analyses or erreurs:
                self._nouvelle_generation(c)
        return ecrites

    def effacer_vecteurs(self, sauf=None):
        """Vecteurs à refaire (le modèle a changé) : efface ceux qui ne viennent pas du modèle `sauf`.
        Les miniatures et les dates restent. Renvoie le nombre de photos concernées."""
        with self._transaction() as c:
            if sauf is None:
                n = c.execute('UPDATE photos SET vecteur = NULL, modele = NULL WHERE modele IS NOT NULL').rowcount
            else:
                n = c.execute('UPDATE photos SET vecteur = NULL, modele = NULL '
                              'WHERE modele IS NOT NULL AND modele <> ?', (sauf,)).rowcount
            self._nouvelle_generation(c)
        return n

    def adopter_modele(self, cle):
        """Le modèle `cle` devient celui de l'index, d'un seul coup : les vecteurs des autres modèles sont effacés (pas
        les miniatures) et enregistrer() refusera désormais les leurs. Un ancien indexeur qui finit son lot après un
        changement de moteur ne laisse donc pas de photos analysées par un moteur qui ne sert plus (jamais refaites,
        introuvables par la recherche). Renvoie le nombre de vecteurs effacés."""
        with self._transaction() as c:
            n = c.execute('UPDATE photos SET vecteur = NULL, modele = NULL '
                          'WHERE modele IS NOT NULL AND modele <> ?', (cle,)).rowcount
            c.execute("INSERT INTO meta(cle, valeur) VALUES ('modele', ?) "
                      'ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur', (cle,))
            if n:
                self._nouvelle_generation(c)
        return n

    def effacer_erreurs(self):
        """Redonne une chance aux photos en erreur (elles seront analysées de nouveau)."""
        with self._transaction() as c:
            return c.execute('UPDATE photos SET erreur = NULL WHERE erreur IS NOT NULL').rowcount

    # ------------------------------------------------ lieux des photos (0.5.0)

    def a_localiser(self, limite=1000, version=None):
        """Photos à localiser [(id, lat, lon)], au plus `limite` : celles qui ont une position GPS valide (voir
        _GPS_VALIDE) et pas de lieu, ou un lieu calculé avec une autre version des données (version : celle des
        données de lieux.py, par défaut celle de lieux.lieux() ; sans données, rien à faire). Lues dans l'index
        photos_gps, sans toucher aux vecteurs."""
        if version is None:
            from .lieux import lieux
            version = lieux().version
        if not version:
            return []
        return self._cnx().execute(
            'SELECT p.id, p.lat, p.lon FROM photos p LEFT JOIN lieux_photos l ON l.photo_id = p.id '
            f'WHERE p.lat IS NOT NULL AND p.erreur IS NULL AND {_GPS_VALIDE.format(p="p.")} '
            'AND (l.photo_id IS NULL OR l.version IS NOT ?) ORDER BY p.id LIMIT ?',
            (version, max(0, int(limite)))).fetchall()

    def enregistrer_lieux(self, lignes):
        """Écrit des lieux, d'un coup : lignes [(photo_id, ville, departement, region, pays, distance, version)] ou
        dictionnaires de ces clés (idents de lieux.py, None quand il n'y en a pas). Une photo oubliée entre-temps est
        ignorée. Renvoie le nombre de lignes écrites."""
        valeurs = []
        for l in lignes:
            if isinstance(l, dict):
                l = (l['photo_id'], l.get('ville'), l.get('departement'), l.get('region'), l.get('pays'),
                     l.get('distance'), l.get('version'))
            pid, ville, dep, region, pays, distance, version = l
            valeurs.append((int(pid), ville, dep, region, pays, None if distance is None else float(distance),
                            version, int(pid)))
        if not valeurs:
            return 0
        with self._transaction() as c:
            avant = c.total_changes
            c.executemany('INSERT OR REPLACE INTO lieux_photos(photo_id, ville, departement, region, pays, distance, '
                          'version) SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM photos WHERE id = ?)',
                          valeurs)
            n = c.total_changes - avant
        # Après le COMMIT (relecture de la 0.5.0) : une recherche qui relit lieux_presents() pendant l'écriture
        # gardait l'instantané d'avant sous le nouveau numéro, et la Bretagne n'était plus reconnue jusqu'au
        # changement suivant de l'index.
        self.numero_lieux += 1
        return n

    @staticmethod
    def _colonne_lieu(endroit):
        """(colonne de lieux_photos, ident) pour un Endroit de lieux.py, ou un ident ('v:…', 'd:…', 'r:…', 'p:…')."""
        ident = getattr(endroit, 'ident', endroit)
        genre = getattr(endroit, 'type', None) or (ident[0] if isinstance(ident, str) and ident[1:2] == ':' else None)
        colonne = _TYPES_LIEU.get(genre)
        if colonne is None or not isinstance(ident, str):
            raise ValueError(f'lieu inconnu : {endroit!r}')
        return colonne, ident

    def ids_par_lieu(self, endroit):
        """Identifiants des photos prises dans ce lieu (Endroit de lieux.py ou son ident) : selon son type, celles de
        la ville, du département, de la région ou du pays. Seulement les photos affichées (qui ont une miniature), les
        plus récentes d'abord, comme photos_par_date. Mesuré sur 100 000 photos : 1 ms pour une ville, 50 ms pour la
        France entière (70 000 photos). ValueError si ce n'est ni un Endroit ni un ident."""
        colonne, ident = self._colonne_lieu(endroit)
        with self._lecture() as c:
            ids = [i for (i,) in c.execute(f'SELECT photo_id FROM lieux_photos WHERE {colonne} = ?', (ident,))]
            if len(ids) <= PAQUET * 4:
                # Peu de photos : leur date, une par une (relire les fiches coûte moins que tout l'index des dates).
                dates = {}
                for paquet in _paquets(ids):
                    marques = ','.join('?' * len(paquet))
                    dates.update(c.execute(f'SELECT id, COALESCE(date_prise, date_fichier) FROM photos '
                                           f'WHERE miniature = 1 AND id IN ({marques})', paquet))
                return sorted(dates, key=lambda i: (dates[i] or '', i), reverse=True)
            voulus = set(ids)
            return [i for (i,) in c.execute('SELECT id FROM photos WHERE miniature = 1 '
                                            'ORDER BY COALESCE(date_prise, date_fichier) DESC, id DESC')
                    if i in voulus]

    def _sous_requete_lieux(self, idents):
        """(« SELECT photo_id FROM lieux_photos WHERE … », paramètres) des photos prises dans l'un de ces lieux."""
        branches, params = [], []
        for ident in idents:
            colonne, ident = self._colonne_lieu(ident)
            branches.append(f'SELECT photo_id FROM lieux_photos WHERE {colonne} = ?')
            params.append(ident)
        return ' UNION '.join(branches), params

    def ids_des_lieux(self, idents):
        """Identifiants des photos prises dans l'un de ces lieux (idents de lieux.py : « v:… », « d:… », « r:… »,
        « p:… »), sans ordre ; [] sans ident. ValueError pour un ident illisible."""
        idents = list(idents)
        if not idents:
            return []
        sql, params = self._sous_requete_lieux(idents)
        return [i for (i,) in self._cnx().execute(sql, params)]

    def liste_filtree(self, intervalles=None, idents=None):
        """Photos affichées (qui ont une miniature) de ces dates et de ces lieux (0.5.0) : [(id, date)], les plus
        récentes d'abord, dans l'ordre de photos_par_date (date de prise de vue, sinon du fichier, en heure locale
        comme dans l'index).

        intervalles : [(debut, fin)] de datetime.date, fins comprises (requete.intervalles) ; None : pas de filtre de
        date ; [] : aucune photo. idents : lieux de lieux.py (une photo prise dans l'un d'eux) ; None : pas de filtre de
        lieu. Une requête par intervalle, dans l'index photos_date : une seule requête avec tous les intervalles (128
        pour « Noël », un par année) prenait 0,5 s sur 100 000 photos, contre 1 ms ainsi (mesuré)."""
        dates = 'COALESCE(date_prise, date_fichier)'
        with self._lecture() as c:
            voulus = None
            if idents is not None:
                idents = list(idents)
                if not idents:
                    return []
                sql, params = self._sous_requete_lieux(idents)
                voulus = {i for (i,) in c.execute(sql, params)}
                if not voulus:
                    return []
            if intervalles is not None:
                trouvees = {}
                for d, f in intervalles:
                    for i, quand in c.execute(f'SELECT id, {dates} FROM photos WHERE miniature = 1 AND {dates} >= ? '
                                              f'AND {dates} < ?', (d.isoformat(), (f + timedelta(days=1)).isoformat())):
                        if voulus is None or i in voulus:
                            trouvees[i] = quand
                lignes = list(trouvees.items())
            elif len(voulus) <= PAQUET * 4:
                # Peu de photos : leurs fiches, une à une (moins cher que tout l'index des dates).
                lignes = []
                for paquet in _paquets(sorted(voulus)):
                    lignes += c.execute(f'SELECT id, {dates} FROM photos WHERE miniature = 1 AND id IN '
                                        f'({",".join("?" * len(paquet))})', paquet).fetchall()
            else:
                # Beaucoup de photos (un pays) : tout l'index des dates, déjà dans l'ordre.
                return [(i, quand) for i, quand in c.execute(
                    f'SELECT id, {dates} FROM photos WHERE miniature = 1 ORDER BY {dates} DESC, id DESC') if i in voulus]
        lignes.sort(key=lambda x: (x[1] or '', x[0]), reverse=True)
        return lignes

    def dates_presentes(self, intervalles):
        """Au moins une photo affichée prise dans l'un de ces intervalles [(debut, fin)] de datetime.date (fins
        comprises) ? Une requête LIMIT 1 par intervalle dans l'index photos_date, jusqu'à la première trouvée."""
        dates = 'COALESCE(date_prise, date_fichier)'
        with self._lecture() as c:
            for d, f in intervalles:
                if c.execute(f'SELECT 1 FROM photos WHERE miniature = 1 AND {dates} >= ? AND {dates} < ? LIMIT 1',
                             (d.isoformat(), (f + timedelta(days=1)).isoformat())).fetchone():
                    return True
        return False

    def page_filtree(self, liste, debut=0, n=200):
        """Une page d'une liste_filtree : {'total', 'debut', 'photos': [{id, date, largeur, hauteur}]} ; une photo
        oubliée entre-temps est omise de la page."""
        debut, n = max(0, int(debut)), max(0, int(n))
        page = liste[debut:debut + n]
        tailles = {}
        c = self._cnx()
        for paquet in _paquets([i for i, _d in page]):
            tailles.update((i, (w, h)) for i, w, h in c.execute(
                f'SELECT id, largeur, hauteur FROM photos WHERE id IN ({",".join("?" * len(paquet))})', paquet))
        return {'total': len(liste), 'debut': debut,
                'photos': [{'id': i, 'date': d, 'largeur': tailles[i][0], 'hauteur': tailles[i][1]}
                           for i, d in page if i in tailles]}

    def photos_filtrees(self, intervalles=None, idents=None, debut=0, n=200):
        """Comme photos_par_date, limitées à des dates et à des lieux (voir liste_filtree) : {'total', 'debut',
        'photos': [{id, date, largeur, hauteur}]}."""
        return self.page_filtree(self.liste_filtree(intervalles, idents), debut, n)

    def photos_du_jour(self, jours, avant):
        """« Ce jour-là » (0.5.0) : photos affichées prises un de ces jours de l'année (jours : textes « MM-JJ »),
        avant la date `avant` (texte ISO « AAAA-MM-JJ », exclue), date de prise de vue sinon du fichier : [{id, date,
        largeur, hauteur}], dans l'ordre chronologique. Les dates sont lues dans l'index photos_date (sans relire les
        fiches) ; seules les photos retenues sont relues."""
        jours = [str(j) for j in jours]
        if not jours:
            return []
        marques = ','.join('?' * len(jours))
        with self._lecture() as c:
            trouvees = c.execute(
                'SELECT id, COALESCE(date_prise, date_fichier) FROM photos WHERE miniature = 1 '
                f'AND substr(COALESCE(date_prise, date_fichier), 6, 5) IN ({marques}) '
                'AND COALESCE(date_prise, date_fichier) < ? ORDER BY COALESCE(date_prise, date_fichier) DESC, id DESC',
                jours + [str(avant)]).fetchall()
            tailles = {}
            for paquet in _paquets([i for i, _d in trouvees]):
                tailles.update((i, (w, h)) for i, w, h in c.execute(
                    f'SELECT id, largeur, hauteur FROM photos WHERE id IN ({",".join("?" * len(paquet))})', paquet))
        return [{'id': i, 'date': d, 'largeur': tailles.get(i, (None, None))[0],
                 'hauteur': tailles.get(i, (None, None))[1]} for i, d in reversed(trouvees)]

    def lieux_presents(self):
        """{ident: nombre de photos} des villes, départements, régions et pays où des photos (affichées) ont été
        prises. Les photos affichées qui ont une position se lisent dans l'index photos_gps (0,2 s pour 70 000 photos
        localisées)."""
        branches = ' UNION ALL '.join(f'SELECT {c}, COUNT(*) FROM v WHERE {c} IS NOT NULL GROUP BY {c}'
                                      for c in _COLONNES_LIEU)
        return dict(self._cnx().execute(
            'WITH v AS MATERIALIZED (SELECT ville, departement, region, pays FROM lieux_photos WHERE photo_id IN '
            '(SELECT id FROM photos WHERE lat IS NOT NULL AND erreur IS NULL AND miniature = 1)) ' + branches))

    def lieu_photo(self, photo_id):
        """{ville, departement, region, pays, distance, version} d'une photo, ou None si elle n'a pas (encore) de
        lieu."""
        l = self._cnx().execute('SELECT ville, departement, region, pays, distance, version FROM lieux_photos '
                                'WHERE photo_id = ?', (int(photo_id),)).fetchone()
        return dict(zip(('ville', 'departement', 'region', 'pays', 'distance', 'version'), l)) if l else None

    def sans_position(self):
        """Nombre de photos analysées (affichées, sans erreur) qui n'ont pas de position GPS valide."""
        with self._lecture() as c:
            toutes = c.execute('SELECT COUNT(*) FROM photos WHERE miniature = 1 AND erreur IS NULL').fetchone()[0]
            avec = c.execute('SELECT COUNT(*) FROM photos WHERE lat IS NOT NULL AND erreur IS NULL AND miniature = 1 '
                             f'AND {_GPS_VALIDE.format(p="")}').fetchone()[0]
        return toutes - avec

    def reparer(self):
        """Remet d'accord les deux fichiers : miniatures orphelines, drapeaux faux. En WAL, une transaction n'est pas
        atomique d'un fichier à l'autre (coupure de courant pendant l'écriture) ; et miniatures.db peut être perdue ou
        abîmée (mise de côté à l'ouverture). Une photo analysée sans miniature n'apparaîtrait plus dans la frise et ne
        serait jamais refaite : elle repart à l'analyse, qui refait sa miniature."""
        with self._transaction() as c:
            n = c.execute('DELETE FROM mini.miniatures WHERE id NOT IN (SELECT id FROM main.photos)').rowcount
            n += c.execute('UPDATE photos SET miniature = 0 WHERE miniature = 1 '
                           'AND id NOT IN (SELECT id FROM mini.miniatures)').rowcount
            # Lieux de photos oubliées par une 0.4.x, qui ne connaît pas la table (0.5.0).
            n += c.execute('DELETE FROM lieux_photos WHERE photo_id NOT IN (SELECT id FROM photos)').rowcount
            refaites = c.execute('UPDATE photos SET vecteur = NULL, modele = NULL '
                                 'WHERE modele IS NOT NULL AND miniature = 0').rowcount
            if refaites:
                self._nouvelle_generation(c)
        return n + refaites
