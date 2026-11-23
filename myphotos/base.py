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
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

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
_SCHEMA_MINIATURES = (
    "CREATE TABLE IF NOT EXISTS mini.miniatures (id INTEGER PRIMARY KEY, jpeg BLOB NOT NULL)",
)

_COLONNES_PHOTO = ('p.id, p.chemin, p.dossier_id, p.taille, p.modifie, p.largeur, p.hauteur, p.date_prise, '
                   'p.date_fichier, p.lat, p.lon, p.modele, p.erreur, p.vu, p.miniature, d.chemin, d.disponible')


class DossierRefuse(ValueError):
    """Dossier impossible à ajouter ; le message, en français, peut s'afficher tel quel."""


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


def dedans(enfant, parent):
    """Vrai si `enfant` est situé sous `parent` (deux clés de cle_chemin, distinctes)."""
    prefixe = parent if parent.endswith(os.sep) else parent + os.sep
    return enfant != parent and enfant.startswith(prefixe)


def normaliser_chemin(chemin):
    """Chemin absolu et propre : « e:\\Photos\\ », « "E:\\Photos" » et « E:/Photos » donnent « E:\\Photos »."""
    p = chemin_affiche(os.fspath(chemin)).strip().strip('"').strip()
    if not p:
        raise DossierRefuse('Aucun dossier indiqué.')
    if WINDOWS and len(p) == 2 and p[1] == ':':
        p += '\\'                                     # « E: » = la racine du disque, pas son dossier courant
    p = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
    lecteur, reste = os.path.splitdrive(p)
    if len(lecteur) == 2 and lecteur[1] == ':':
        p = lecteur.upper() + reste
    return p


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
    """Index des photos (voir l'en-tête du module). Utilisable depuis plusieurs fils à la fois."""

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
        with self._ecriture:
            self._migrer(self._cnx())

    # ------------------------------------------------ connexions et transactions

    def _cnx(self):
        h = getattr(self._local, 'h', None)
        if h is not None:
            return h.c
        if self._fermee:
            raise RuntimeError('base fermée')
        c = sqlite3.connect(self.chemin_index, timeout=30, isolation_level=None, check_same_thread=False)
        # auto_vacuum avant le WAL : sans effet sur une base existante, actif sur une base neuve.
        c.execute('PRAGMA auto_vacuum = INCREMENTAL')
        c.execute('PRAGMA journal_mode = WAL').fetchall()
        c.execute('PRAGMA synchronous = NORMAL')
        c.execute('PRAGMA temp_store = MEMORY')
        c.execute('ATTACH DATABASE ? AS mini', (self.chemin_miniatures,))
        c.execute('PRAGMA mini.auto_vacuum = INCREMENTAL')
        c.execute('PRAGMA mini.journal_mode = WAL').fetchall()
        c.execute('PRAGMA mini.synchronous = NORMAL')
        h = _Connexion(c)
        self._local.h = h
        with self._verrou_connexions:
            self._connexions = [(f, x) for f, x in self._connexions if f.is_alive()]
            self._connexions.append((threading.current_thread(), h))
        return c

    @contextmanager
    def _transaction(self):
        """Transaction d'écriture : tout ou rien, une seule à la fois."""
        with self._ecriture:
            c = self._cnx()
            c.execute('BEGIN IMMEDIATE')
            try:
                yield c
            except BaseException:
                c.execute('ROLLBACK')
                raise
            c.execute('COMMIT')

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
        did, chemin, ajoute, disponible, vu = ligne
        n, a, e = stats.get(did, (0, 0, 0))
        return {'id': did, 'chemin': chemin, 'ajoute': ajoute, 'disponible': bool(disponible), 'vu': vu,
                'photos': n, 'analysees': a, 'erreurs': e, 'a_faire': n - a - e}

    def dossiers(self):
        """Dossiers suivis, par ordre alphabétique, avec leurs comptes de photos."""
        with self._lecture() as c:
            stats = self._stats_dossiers(c)
            lignes = c.execute('SELECT id, chemin, ajoute, disponible, vu FROM dossiers '
                               'ORDER BY chemin COLLATE NOCASE').fetchall()
        return [self._dossier_dict(l, stats) for l in lignes]

    def dossier(self, dossier_id):
        with self._lecture() as c:
            l = c.execute('SELECT id, chemin, ajoute, disponible, vu FROM dossiers WHERE id = ?',
                          (dossier_id,)).fetchone()
            stats = self._stats_dossiers(c) if l else {}
        return self._dossier_dict(l, stats) if l else None

    def ajouter_dossier(self, chemin):
        """Suit un nouveau dossier. Lève DossierRefuse (message clair) s'il est introuvable, déjà suivi ou
        situé dans un dossier suivi. S'il contient des dossiers suivis, il les remplace et garde leurs photos.

        Renvoie le dossier (comme dossiers()) avec en plus 'remplaces' : chemins des dossiers absorbés.
        L'inventaire n'est pas lancé ici : appeler ensuite Indexeur.relancer().
        """
        p = normaliser_chemin(chemin)
        if not os.path.isdir(chemin_systeme(p)):
            raise DossierRefuse(f'Ce dossier est introuvable : « {p} ».')
        p = casse_reelle(p)
        k = cle_chemin(p)
        with self._transaction() as c:
            remplaces = []
            for did, ch in c.execute('SELECT id, chemin FROM dossiers').fetchall():
                ke = cle_chemin(ch)
                if ke == k:
                    raise DossierRefuse(f'Ce dossier est déjà dans MyPhotos : « {ch} ».')
                if dedans(k, ke):
                    raise DossierRefuse(f'Ce dossier fait déjà partie de « {ch} », qui est dans MyPhotos.')
                if dedans(ke, k):
                    remplaces.append((did, ch))
            nid = c.execute('INSERT INTO dossiers(chemin, ajoute, disponible) VALUES (?, ?, 1)',
                            (p, maintenant())).lastrowid
            for did, _ in remplaces:
                c.execute('UPDATE photos SET dossier_id = ? WHERE dossier_id = ?', (nid, did))
                c.execute('DELETE FROM dossiers WHERE id = ?', (did,))
        d = self.dossier(nid)
        d['remplaces'] = [ch for _, ch in remplaces]
        return d

    def retirer_dossier(self, dossier_id):
        """Oublie un dossier, ses photos et leurs miniatures. Les fichiers ne sont JAMAIS touchés.
        Renvoie {'id', 'chemin', 'photos': nombre oublié}, ou None si le dossier n'existe pas."""
        with self._transaction() as c:
            l = c.execute('SELECT chemin FROM dossiers WHERE id = ?', (dossier_id,)).fetchone()
            if l is None:
                return None
            n = c.execute('SELECT COUNT(*) FROM photos WHERE dossier_id = ?', (dossier_id,)).fetchone()[0]
            c.execute('DELETE FROM mini.miniatures WHERE id IN (SELECT id FROM main.photos WHERE dossier_id = ?)',
                      (dossier_id,))
            c.execute('DELETE FROM photos WHERE dossier_id = ?', (dossier_id,))
            c.execute('DELETE FROM dossiers WHERE id = ?', (dossier_id,))
            self._nouvelle_generation(c)
        self._liberer_place()
        return {'id': dossier_id, 'chemin': l[0], 'photos': n}

    # ------------------------------------------------ photos

    @staticmethod
    def _photo_dict(l):
        (pid, chemin, did, taille, modifie, largeur, hauteur, date_prise, date_fichier, lat, lon, mod, err, vu,
         mini, dossier, dispo) = l
        return {'id': pid, 'chemin': chemin, 'nom': os.path.basename(chemin), 'dossier_id': did,
                'dossier': dossier, 'disponible': bool(dispo), 'taille': taille, 'modifie': modifie,
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
        """Nouvelles photos [(chemin, taille, modifie, date_fichier)]. Renvoie le nombre ajouté."""
        quand = maintenant()
        with self._transaction() as c:
            if not c.execute('SELECT 1 FROM dossiers WHERE id = ?', (dossier_id,)).fetchone():
                return 0                              # dossier retiré entre-temps
            avant = c.total_changes
            c.executemany('INSERT INTO photos(chemin, dossier_id, taille, modifie, date_fichier, vu) '
                          'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(chemin) DO NOTHING',
                          [(ch, dossier_id, t, m, df, quand) for ch, t, m, df in lignes])
            return c.total_changes - avant

    def marquer_modifiees(self, lignes):
        """Fichiers changés [(id, taille, modifie, date_fichier)] : à analyser de nouveau. La miniature reste
        affichée jusqu'à la nouvelle analyse."""
        with self._transaction() as c:
            c.executemany('UPDATE photos SET taille = ?, modifie = ?, date_fichier = ?, vecteur = NULL, '
                          'modele = NULL, erreur = NULL WHERE id = ?',
                          [(t, m, df, pid) for pid, t, m, df in lignes])
            self._nouvelle_generation(c)

    def oublier_photos(self, ids):
        """Photos disparues du disque : on les oublie (base et miniatures)."""
        with self._transaction() as c:
            for paquet in _paquets(ids):
                marques = ','.join('?' * len(paquet))
                c.execute(f'DELETE FROM mini.miniatures WHERE id IN ({marques})', paquet)
                c.execute(f'DELETE FROM photos WHERE id IN ({marques})', paquet)
            self._nouvelle_generation(c)

    def deplacer_photos(self, paires):
        """Photos déplacées ou renommées [(ancien_id, nouveau_chemin)] : l'ancienne fiche (analyse, miniature,
        identifiant) prend le nouveau chemin ; la fiche vierge créée par l'inventaire est supprimée."""
        with self._transaction() as c:
            for ancien, chemin in paires:
                l = c.execute('SELECT id, dossier_id, date_fichier FROM photos WHERE chemin = ?', (chemin,)).fetchone()
                if l is None or l[0] == ancien:
                    continue
                c.execute('DELETE FROM mini.miniatures WHERE id = ?', (l[0],))
                c.execute('DELETE FROM photos WHERE id = ?', (l[0],))
                c.execute('UPDATE photos SET chemin = ?, dossier_id = ?, date_fichier = ? WHERE id = ?',
                          (chemin, l[1], l[2], ancien))
            self._nouvelle_generation(c)

    def marquer_dossier(self, dossier_id, disponible, vu=None):
        """Dossier joignable ou non (disque débranché, partage réseau éteint) ; ses photos restent connues."""
        with self._transaction() as c:
            if vu is None:
                c.execute('UPDATE dossiers SET disponible = ? WHERE id = ?', (int(bool(disponible)), dossier_id))
            else:
                c.execute('UPDATE dossiers SET disponible = ?, vu = ? WHERE id = ?',
                          (int(bool(disponible)), vu, dossier_id))

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
        Une photo modifiée ou retirée entre-temps (taille/date différentes, fiche disparue) est ignorée.
        """
        ecrites = 0
        with self._transaction() as c:
            for a in analyses:
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

    def effacer_erreurs(self):
        """Redonne une chance aux photos en erreur (elles seront analysées de nouveau)."""
        with self._transaction() as c:
            return c.execute('UPDATE photos SET erreur = NULL WHERE erreur IS NOT NULL').rowcount

    def reparer(self):
        """Remet d'accord les deux fichiers après un arrêt brutal : miniatures orphelines, drapeaux faux."""
        with self._transaction() as c:
            n = c.execute('DELETE FROM mini.miniatures WHERE id NOT IN (SELECT id FROM main.photos)').rowcount
            n += c.execute('UPDATE photos SET miniature = 0 WHERE miniature = 1 '
                           'AND id NOT IN (SELECT id FROM mini.miniatures)').rowcount
        return n
