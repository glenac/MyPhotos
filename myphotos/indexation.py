"""Indexation en arrière-plan : inventaire des dossiers suivis, puis analyse des photos.

Inventaire : liste les images (sans suivre liens ni jonctions), repère les nouvelles, les modifiées
et les disparues. Un dossier injoignable (disque débranché, Freebox éteinte) garde ses photos.

Analyse : pour chaque photo, miniature (360 px), date de prise de vue, position GPS éventuelle et
vecteur du modèle ; les plus récentes d'abord, écrites par lots. Une analyse interrompue reprend où
elle en était. Les photos ne sont jamais modifiées, seulement lues.
"""
import ctypes
import io
import logging
import os
import re
import threading
import time
import unicodedata
import warnings
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
from PIL import Image, ImageFile, UnidentifiedImageError

from . import config, modele
from .base import chemin_affiche, chemin_systeme, cle_chemin, dedans, enregistrable, iso_depuis_ns, maintenant

journal = logging.getLogger(__name__)

# Une photo à moitié copiée ou un peu abîmée s'affiche quand même, comme dans une visionneuse.
ImageFile.LOAD_TRUNCATED_IMAGES = True
warnings.filterwarnings('ignore', category=Image.DecompressionBombWarning)

LOT = 16                       # images envoyées ensemble au modèle
AVANCE = 3 * LOT               # images décodées d'avance pendant que le modèle travaille
ECRITURE_N = 64                # écriture en base toutes les 64 photos…
ECRITURE_S = 2.0               # … ou toutes les 2 secondes
COTE_MINIATURE = 360
QUALITE_MINIATURE = 80
PAQUET_INVENTAIRE = 500
ECHECS_AVANT_CONTROLE = 3      # erreurs de lecture d'affilée avant de vérifier que le dossier répond

# Formats lus (ceux de config.EXTENSIONS), quel que soit le nom du fichier. Pillow en connaît d'autres, dont EPS, qui
# lance Ghostscript, un programme extérieur : un « .jpg » qui serait en fait un PostScript ne doit pas y arriver.
Image.init()
FORMATS = tuple(f for f in ('JPEG', 'PNG', 'WEBP', 'BMP', 'DIB', 'GIF', 'TIFF') if f in Image.OPEN)
# Préparées à part (voir _ouvrir_a_part) : les images non JPEG de plus de GRANDE_IMAGE pixels, que Pillow ne sait
# décoder qu'en entier (PNG de 178 Mpx : 3,4 Go pour une seule image, mesuré le 24/11/2026), et les niveaux de gris
# sur 16 ou 32 bits, que Pillow écrête à 255 (une numérisation 16 bits deviendrait toute blanche).
GRANDE_IMAGE = 16_000_000
COTE_DECODAGE = 448            # comme modele.ouvrir_image : le plus petit côté reste d'au moins 448 px
_MODES_PROFONDS = frozenset({'I', 'I;16', 'I;16B', 'I;16L', 'I;16N', 'F'})
_UNE_A_LA_FOIS = threading.Semaphore(1)
_SENS_EXIF = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180, 4: Image.Transpose.FLIP_TOP_BOTTOM,
              5: Image.Transpose.TRANSPOSE, 6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
              8: Image.Transpose.ROTATE_90}

PHASES = ('attente', 'inventaire', 'analyse', 'pause', 'termine', 'erreur')

# Attributs Windows (stat().st_file_attributes) et types de liens (st_reparse_tag).
_CACHE, _SYSTEME, _LIEN = 0x2, 0x4, 0x400
_BALISES_LIENS = {0xA000000C, 0xA0000003}          # lien symbolique, jonction
# Dossiers jamais parcourus (comparés en minuscules) ; « .xxx » et « $xxx » sont aussi ignorés.
NOMS_IGNORES = {'$recycle.bin', 'system volume information', 'recycler', 'recycled', 'lost+found',
                '@eadir', '#recycle', '#snapshot', '@recycle', 'node_modules', '__pycache__'}
# À la racine d'un disque seulement : Windows et les programmes, pleins d'images qui ne sont pas des photos.
NOMS_SYSTEME_RACINE = {'windows', 'windows.old', 'program files', 'program files (x86)', 'programdata',
                       'perflogs', 'recovery', 'msocache', 'intel', 'amd', 'nvidia', 'drivers', 'boot', 'efi'}
# Ce que Windows (ou un Mac) range de lui-même dans un dossier, sans que ce soit du contenu : un dossier qui ne contient
# que cela est vide (voir dossier_vide).
NOMS_SANS_CONTENU = {'desktop.ini', 'thumbs.db', 'ehthumbs.db', 'ehthumbs_vista.db', '.ds_store'}


# Sous-dossiers que l'assistant propose de laisser de côté (décochés d'office) : captures d'écran et fonds d'écran.
# Noms comparés sans accents, sans casse, apostrophes et tirets ramenés à des espaces (voir nature_sous_dossier).
# Sources (recherche du 24/11/2026) : Windows 10 et 11 enregistrent Win + Impr. écran dans Images\Screenshots, affiché
# « Captures d'écran » en français (dossier spécial, nom localisé par desktop.ini) ; OneDrive peut y enregistrer les
# captures sous un vrai dossier « Captures d'écran » ; la Xbox Game Bar range les siennes dans « Captures » ; les fonds
# d'écran (Bing Wallpaper, Windows à la une, téléchargés) se rangent souvent dans « Wallpapers » ou « Fonds d'écran ».
NOMS_CAPTURES = {'screenshots', 'screenshot', 'screen shots', 'captures d ecran', 'capture d ecran', 'captures ecran',
                 'captures', 'captures decran', 'capture decran', 'impressions d ecran', 'impression d ecran'}
NOMS_FONDS = {'wallpapers', 'wallpaper', 'fonds d ecran', 'fond d ecran', 'fonds ecran', 'fond ecran',
              'fonds decran', 'fond decran', 'bing wallpaper', 'bing wallpapers', 'bingwallpaper', 'windows spotlight',
              'spotlight', 'windows a la une', 'a la une', 'arriere plans', 'arriere plan', 'backgrounds',
              'desktop backgrounds', 'lock screen', 'ecran de verrouillage'}
# Débuts de noms qui suffisent (« Fonds d'écran Windows », « Wallpapers HD », « Screenshots 2023 »…).
DEBUTS_CAPTURES = ('screenshot', 'captures d ecran', 'capture d ecran')
DEBUTS_FONDS = ('wallpaper', 'fonds d ecran', 'fond d ecran', 'bing wallpaper')
EXAMEN_SOUS_DOSSIERS = 12          # l'assistant montre au plus les 12 plus gros sous-dossiers…
EXAMEN_PROFONDEUR = 3              # … et les captures ou fonds d'écran trouvés jusqu'à 3 niveaux sous le dossier


def _nom_simple(nom):
    """« Fonds d’écran » -> « fonds d ecran » : sans accents ni casse, ponctuation ramenée à une espace."""
    s = re.sub(r"['‘’ʼ`´]", ' ', nom)          # apostrophes droites ou typographiques
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii').lower()
    s = re.sub(r"[^a-z0-9]+", ' ', s)
    return s.strip()


def nature_sous_dossier(nom):
    """'captures', 'fonds' ou None : le nom d'un dossier dit-il qu'il contient des captures d'écran ou des fonds
    d'écran (et donc pas de vraies photos) ?"""
    n = _nom_simple(nom)
    if not n:
        return None
    if n in NOMS_CAPTURES or n.startswith(DEBUTS_CAPTURES):
        return 'captures'
    if n in NOMS_FONDS or n.startswith(DEBUTS_FONDS):
        return 'fonds'
    return None


class MoteurIndisponible(RuntimeError):
    """Le modèle de reconnaissance ne peut pas être chargé ou ne répond plus (message affichable)."""


# ---------------------------------------------------------------- inventaire

def _racine_de_disque(chemin):
    lecteur, reste = os.path.splitdrive(chemin_affiche(chemin))
    return bool(lecteur) and reste in ('', '\\', '/')


def _sans_contenu(e):
    """Élément d'un dossier qui ne compte pas comme du contenu : desktop.ini, Thumbs.db… (NOMS_SANS_CONTENU), ou élément
    caché ou système (« System Volume Information », corbeille), que l'inventaire ne regarde jamais."""
    if e.name.lower() in NOMS_SANS_CONTENU:
        return True
    try:
        return bool(getattr(e.stat(follow_symlinks=False), 'st_file_attributes', 0) & (_CACHE | _SYSTEME))
    except OSError:
        return False


def dossier_vide(chemin):
    """Vrai si le dossier ne contient rien, ou seulement ce que Windows y range de lui-même (_sans_contenu) : un dossier
    Images vidé, ou que OneDrive n'a pas encore rempli, garde son desktop.ini. Lève OSError s'il est injoignable."""
    with os.scandir(chemin_systeme(chemin)) as it:
        return all(_sans_contenu(e) for e in it)


class Parcours:
    """Liste les images sous un dossier : itération -> (chemin, taille, modifie_ns).

    Ne suit ni les liens symboliques ni les jonctions (pas de boucle possible) ; ignore les dossiers
    cachés, système et ceux de NOMS_IGNORES. Lève OSError si le dossier lui-même est injoignable.
    Les sous-dossiers illisibles sont notés : leurs photos connues ne sont pas considérées disparues.
    Un nom qui n'est pas de l'Unicode valide (base.enregistrable) est sauté et compté dans `sautes` : impossible à
    enregistrer, il arrêtait toute l'indexation.
    """

    def __init__(self, racine, extensions=None, exclus=()):
        self.racine = racine
        self.extensions = extensions or config.EXTENSIONS
        # Sous-dossiers exclus (clés de cle_chemin) : jamais parcourus.
        self.exclus = frozenset(exclus)
        self.echecs = []           # clés (cle_chemin) des sous-dossiers illisibles, terminées par os.sep
        self.vide = False          # le dossier ne contient rien, ou seulement desktop.ini et autres (dossier_vide)
        self.courant = racine      # dossier en cours de lecture (pour l'affichage)
        self.sautes = 0            # fichiers et dossiers au nom impossible à enregistrer

    def sous_echec(self, chemin):
        k = cle_chemin(chemin)
        return any(k.startswith(e) for e in self.echecs)

    def __iter__(self):
        pile = [chemin_systeme(self.racine)]
        premier = True
        while pile:
            d = pile.pop()
            try:
                with os.scandir(d) as it:
                    entrees = list(it)
            except OSError:
                if premier:
                    raise
                k = cle_chemin(d)
                self.echecs.append(k if k.endswith(os.sep) else k + os.sep)
                continue
            if premier:
                self.vide, premier = all(_sans_contenu(e) for e in entrees), False
            self.courant = chemin_affiche(d)
            racine_disque = _racine_de_disque(d)
            for e in entrees:
                if not enregistrable(e.name):
                    self.sautes += 1
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not self._dossier_ignore(e, racine_disque) and not (
                                self.exclus and cle_chemin(e.path) in self.exclus):
                            pile.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        r = self._image(e)
                        if r:
                            yield r
                except OSError:
                    continue

    @staticmethod
    def _dossier_ignore(e, racine_disque):
        nom = e.name
        if nom[:1] in ('.', '$'):
            return True
        n = nom.lower()
        if n in NOMS_IGNORES or (racine_disque and n in NOMS_SYSTEME_RACINE):
            return True
        if e.is_symlink() or e.is_junction():
            return True
        st = e.stat(follow_symlinks=False)
        attributs = getattr(st, 'st_file_attributes', 0)
        if attributs & (_CACHE | _SYSTEME):
            return True
        return bool(attributs & _LIEN) and getattr(st, 'st_reparse_tag', 0) in _BALISES_LIENS

    def _image(self, e):
        nom = e.name
        if nom[:1] == '.':                       # « ._IMG_0001.JPG » des Mac, fichiers cachés à la Unix
            return None
        if os.path.splitext(nom)[1].lower() not in self.extensions or e.is_symlink():
            return None
        st = e.stat(follow_symlinks=False)
        if getattr(st, 'st_file_attributes', 0) & (_CACHE | _SYSTEME):
            return None
        return chemin_affiche(e.path), st.st_size, st.st_mtime_ns


def examiner_dossier(racine, limite_s=4.0, exclus=()):
    """Ce que l'assistant montre avant d'ajouter un dossier : ses plus gros sous-dossiers, avec leur nombre de photos
    (sans en ouvrir aucune, en limite_s secondes au plus, comme le comptage du premier lancement), et les sous-dossiers
    de captures d'écran ou de fonds d'écran (nature_sous_dossier), jusqu'à EXAMEN_PROFONDEUR niveaux.

    Renvoie {'total', 'complet', 'sous_dossiers': [{chemin, nom, photos, nature}]} : les EXAMEN_SOUS_DOSSIERS plus
    gros du premier niveau (au moins une photo), puis les captures et fonds d'écran plus profonds. Sans comptage
    complet, les nombres sont des minimums. Lève OSError si le dossier est injoignable."""
    parcours = Parcours(racine, exclus=exclus)
    debut = time.monotonic()
    base = chemin_affiche(racine).rstrip('\\/')
    longueur = len(base) + 1
    premiers, reconnus = {}, {}                  # nom (1er niveau) -> photos ; parties (tuple) -> [nature, photos]
    total, complet = 0, True
    for chemin, _taille, _modifie in parcours:
        total += 1
        parties = chemin[longueur:].split(os.sep)[:-1]
        if parties:
            premiers[parties[0]] = premiers.get(parties[0], 0) + 1
            for i, nom in enumerate(parties[:EXAMEN_PROFONDEUR]):
                cle = tuple(parties[:i + 1])
                r = reconnus.get(cle)
                if r is None:
                    nature = nature_sous_dossier(nom)
                    r = reconnus[cle] = [nature, 0]
                if r[0]:
                    r[1] += 1
                    break                       # le plus haut dossier reconnu compte seul
        if total % 256 == 0 and time.monotonic() - debut > limite_s:
            complet = False
            break
    sous = []
    for nom, n in sorted(premiers.items(), key=lambda x: (-x[1], x[0].lower()))[:EXAMEN_SOUS_DOSSIERS]:
        sous.append({'chemin': base + os.sep + nom, 'nom': nom, 'photos': n,
                     'nature': nature_sous_dossier(nom)})
    deja = {cle_chemin(s['chemin']) for s in sous}
    for parties, (nature, n) in sorted(reconnus.items(), key=lambda x: (-x[1][1], x[0])):
        if not nature or not n:
            continue
        chemin = base + os.sep + os.sep.join(parties)
        k = cle_chemin(chemin)
        # Déjà montré, ou dans un dossier reconnu montré (qui sera décoché d'office) : rien à ajouter.
        if k in deja or any(dedans(k, cle_chemin(s['chemin'])) and s['nature'] for s in sous):
            continue
        sous.append({'chemin': chemin, 'nom': os.path.join(*parties), 'photos': n, 'nature': nature})
        deja.add(k)
    return {'total': total, 'complet': complet, 'sous_dossiers': sous}


# ---------------------------------------------------------------- lecture d'une photo

_DATE = re.compile(r'(\d{4})[:\-/.](\d{1,2})[:\-/.](\d{1,2})(?:[ T]+(\d{1,2})[:.h](\d{1,2})(?:[:.](\d{1,2}))?)?')


def lire_date_exif(valeur):
    """'AAAA:MM:JJ HH:MM:SS' (EXIF) -> 'AAAA-MM-JJTHH:MM:SS' ; None si absente ou absurde."""
    if isinstance(valeur, bytes):
        valeur = valeur.decode('ascii', 'ignore')
    if not isinstance(valeur, str):
        return None
    m = _DATE.match(valeur.replace('\x00', '').strip())
    if not m:
        return None
    an, mois, jour, h, mi, s = (int(x) if x else 0 for x in m.groups())
    if not 1900 <= an <= datetime.now().year + 1:
        return None                              # 0000:00:00, appareil jamais réglé…
    try:
        d = datetime(an, mois, jour, h, mi, s)
    except ValueError:
        return None
    if d > datetime.now() + timedelta(days=2):
        return None
    return d.isoformat(timespec='seconds')


def lire_gps(exif):
    """(lat, lon) en degrés décimaux, ou (None, None)."""
    try:
        g = exif.get_ifd(0x8825)
    except Exception:
        return None, None
    if not g:
        return None, None

    def degres(valeur, ref):
        if valeur is None:
            return None
        parts = list(valeur) if isinstance(valeur, (tuple, list)) else [valeur]
        parts = [float(x) for x in parts[:3]] + [0.0] * (3 - min(3, len(parts)))
        v = parts[0] + parts[1] / 60 + parts[2] / 3600
        if isinstance(ref, bytes):
            ref = ref.decode('ascii', 'ignore')
        if str(ref or '').strip('\x00 ').upper() in ('S', 'W', 'O'):
            v = -v
        return v

    try:
        lat, lon = degres(g.get(2), g.get(1)), degres(g.get(4), g.get(3))
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None
    if lat is None or lon is None or not (np.isfinite(lat) and np.isfinite(lon)):
        return None, None
    if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None, None
    return round(lat, 7), round(lon, 7)


def lire_metadonnees(im):
    """(date_prise, lat, lon) d'une image ouverte (en-tête seulement)."""
    try:
        if im.format == 'PNG' and 'exif' not in im.info:
            return None, None, None              # sinon Pillow décoderait toute l'image pour rien
        exif = im.getexif()
    except Exception:
        return None, None, None
    date = None
    try:
        ifd = exif.get_ifd(0x8769)
        date = lire_date_exif(ifd.get(36867)) or lire_date_exif(ifd.get(36868))   # prise de vue, numérisation
    except Exception:
        pass
    if date is None:
        date = lire_date_exif(exif.get(306))     # DateTime : date d'enregistrement du fichier par l'appareil
    lat, lon = lire_gps(exif)
    return date, lat, lon


def faire_miniature(im):
    """Image RVB -> JPEG dont le plus grand côté fait au plus 360 px."""
    w, h = im.size
    r = COTE_MINIATURE / max(w, h)
    if r < 1:
        im = im.resize((max(1, round(w * r)), max(1, round(h * r))), Image.Resampling.LANCZOS, reducing_gap=3.0)
    tampon = io.BytesIO()
    im.save(tampon, 'JPEG', quality=QUALITE_MINIATURE, optimize=True)
    return tampon.getvalue()


def _passagere(e):
    """Erreur de lecture passagère (disque débranché, réseau coupé, fichier verrouillé) : à réessayer plus tard,
    pas à retenir. Les erreurs de Pillow (format inconnu, données abîmées) n'ont pas de numéro d'erreur."""
    if isinstance(e, UnidentifiedImageError):
        return False
    if isinstance(e, MemoryError):
        return True
    return isinstance(e, OSError) and bool(e.errno or getattr(e, 'winerror', None))


def _message_erreur(e):
    if isinstance(e, Image.DecompressionBombError):
        return 'illisible : image trop grande'
    if isinstance(e, UnidentifiedImageError):
        return 'illisible : format non reconnu'
    return 'illisible : fichier abîmé'


def _a_part(im0):
    """Image que modele.ouvrir_image traiterait mal (voir GRANDE_IMAGE)."""
    return im0.mode in _MODES_PROFONDS or (im0.format not in ('JPEG', 'MPO')
                                          and im0.width * im0.height > GRANDE_IMAGE)


def _vers_8_bits(im):
    """Niveaux de gris 'I' (32 bits) ou 'F' (flottants) -> 'L', en gardant les nuances : 16 bits (0-65 535) et
    flottants entre 0 et 1 sont ramenés à 0-255, comme le font les visionneuses."""
    a = np.nan_to_num(np.asarray(im, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    haut = float(a.max()) if a.size else 0.0
    if im.mode == 'F' and haut <= 1.0:
        plafond = 1.0
    elif haut <= 255:
        plafond = 255.0
    elif haut <= 65535:
        plafond = 65535.0
    else:
        plafond = haut
    return Image.fromarray(np.clip(a * (255.0 / plafond), 0, 255).astype(np.uint8))


def _ouvrir_a_part(f, cote=COTE_DECODAGE):
    """Comme modele.ouvrir_image (dans le bon sens, en RVB, plus petit côté >= cote), pour une image que
    Pillow décode en entier : une seule à la fois (4 fils x 3 Go, c'est trop pour un PC de 8 Go) et réduite dès le
    décodage fini, au lieu des trois ou quatre copies pleine taille du chemin habituel. modele.ouvrir_image passe
    aussi par ici (visionneuse : cote plus grand)."""
    with _UNE_A_LA_FOIS:
        im = Image.open(f, formats=FORMATS)
        try:
            sens = _SENS_EXIF.get(im.getexif().get(0x0112))
        except Exception:
            sens = None                          # informations EXIF abîmées : l'image reste telle quelle
        im.load()
        if im.mode.startswith('I;16'):
            im = im.convert('I')                 # 16 -> 32 bits, valeurs gardées, et réductible
        facteur = min(im.size) // max(1, cote)
        if facteur >= 2:
            if im.mode in ('P', 'PA', '1'):
                im = modele.vers_rvb(im)         # une moyenne d'indices de palette donnerait n'importe quelle couleur
            try:
                im = im.reduce(facteur)
            except ValueError:                   # autre mode que reduce() refuse : d'abord en couleurs
                im = modele.vers_rvb(im).reduce(facteur)
        if im.mode in ('I', 'F'):
            im = _vers_8_bits(im)
        if sens is not None:
            im = im.transpose(sens)
        return modele.vers_rvb(im)


def preparer(chemin, taille_modele):
    """Lit une photo. Ne lève jamais : renvoie un dict
    - réussite : largeur, hauteur, date_prise, lat, lon, miniature (JPEG), pixels ((3, t, t) float32) ;
    - échec    : erreur (message), passagere (bool), et largeur/hauteur si connues."""
    try:
        with open(chemin_systeme(chemin), 'rb') as f:
            im0 = Image.open(f, formats=FORMATS)     # en-tête seulement : format, dimensions d'origine et EXIF
            largeur, hauteur = im0.size
            date, lat, lon = lire_metadonnees(im0)
            a_part = _a_part(im0)
            del im0                              # (pas de close() : il fermerait aussi le fichier)
            if min(largeur, hauteur) < config.COTE_MIN:
                return {'erreur': 'trop petite', 'passagere': False, 'largeur': largeur, 'hauteur': hauteur}
            f.seek(0)
            im = _ouvrir_a_part(f) if a_part else modele.ouvrir_image(f)
            im.load()
            if (im.width >= im.height) != (largeur >= hauteur):
                largeur, hauteur = hauteur, largeur      # photo tournée par l'EXIF : sens d'affichage
            return {'largeur': largeur, 'hauteur': hauteur, 'date_prise': date, 'lat': lat, 'lon': lon,
                    'miniature': faire_miniature(im), 'pixels': modele.pixels(im, taille_modele)}
    except Exception as e:
        passagere = _passagere(e)
        journal.debug('lecture impossible (%s) : %s : %r', 'passagère' if passagere else 'définitive', chemin, e)
        return {'erreur': 'momentanément illisible' if passagere else _message_erreur(e), 'passagere': passagere}


# ---------------------------------------------------------------- priorité du processus

class _Priorite:
    """Abaisse la priorité du programme pendant l'analyse, pour que le PC reste fluide."""
    INFERIEURE, BASSE = 0x4000, 0x40           # BELOW_NORMAL_PRIORITY_CLASS, IDLE_PRIORITY_CLASS

    def __init__(self):
        self.k32 = None
        self.avant = None
        if os.name == 'nt':
            try:
                k32 = ctypes.WinDLL('kernel32', use_last_error=True)
                k32.GetCurrentProcess.restype = ctypes.c_void_p
                k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
                k32.GetPriorityClass.restype = ctypes.c_uint32
                k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                k32.SetPriorityClass.restype = ctypes.c_int
                self.k32 = k32
            except (OSError, AttributeError):
                self.k32 = None

    def actuelle(self):
        return self.k32.GetPriorityClass(self.k32.GetCurrentProcess()) if self.k32 else None

    def baisser(self):
        if self.k32 is None or self.avant is not None:
            return
        avant = self.actuelle()
        if not avant or avant in (self.INFERIEURE, self.BASSE):
            return                               # déjà basse : on n'y touche pas
        if self.k32.SetPriorityClass(self.k32.GetCurrentProcess(), self.INFERIEURE):
            self.avant = avant

    def retablir(self):
        if self.k32 is None or self.avant is None:
            return
        self.k32.SetPriorityClass(self.k32.GetCurrentProcess(), self.avant)
        self.avant = None


# ---------------------------------------------------------------- indexeur

def _nombre(n):
    return f'{n:,}'.replace(',', ' ')


def _photos(n):
    return f'{_nombre(n)} photo' + ('s' if n > 1 else '')


class Indexeur:
    """Indexe les dossiers suivis dans un fil d'arrière-plan.

    base : base.Base ; fabrique_moteur(materiel='auto'|'processeur') -> modele.Moteur, appelée au premier
    besoin (jamais au démarrage) ; cle_modele : clé du modèle, retenue avec chaque vecteur ;
    fils_decodage : fils de lecture des images (défaut min(4, cœurs / 2)) ;
    inventaire_auto_s : refaire l'inventaire tout seul toutes les N secondes une fois fini (None = non).
    """

    def __init__(self, base, fabrique_moteur, cle_modele, fils_decodage=None, inventaire_auto_s=None):
        self.base = base
        self.fabrique_moteur = fabrique_moteur
        self.cle_modele = cle_modele
        self.fils_decodage = max(1, int(fils_decodage or min(4, max(1, (os.cpu_count() or 2) // 2))))
        self.inventaire_auto_s = inventaire_auto_s
        self._moteur = None
        self._pool = None
        self._priorite = _Priorite()
        self._fil = None
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        self._en_marche = threading.Event()      # effacé = pause demandée
        self._en_marche.set()
        self._reveil = threading.Event()
        self._repos = threading.Event()          # levé quand le travail est fini (termine ou erreur)
        self._inventaire_demande = False
        # Inventaires demandés : numéro de la dernière demande, numéro couvert par le dernier inventaire complet, et
        # son bilan {numero, nouvelles, retirees, injoignables, dossiers, quand} (bouton « Actualiser »).
        self._demande = 0
        self._fait = 0
        self._en_traitement = 0                  # numéro de demande du passage en cours (une erreur ne vaut que pour lui)
        self._bilan = None
        self._bilan_courant = None
        self._cibles = set()                     # dossiers à revoir seulement (« Ne plus exclure ») ; vide = tous
        self._tous = False
        self._effacer_erreurs = False
        self._repare = False
        self._passees = set()                    # photos momentanément illisibles : pas avant le prochain passage
        self._etat = {'phase': 'attente', 'dossier_courant': None, 'materiel': None, 'message': None,
                      'trouvees': 0, 'traitees': 0}
        self._mesures = deque()                  # (instant, photos traitées depuis le début de l'analyse)
        self._comptes_cache = None
        self._comptes_t = 0.0

    # ------------------------------------------------ commandes

    def demarrer(self):
        """Lance le fil d'indexation (inventaire puis analyse). Sans effet s'il tourne déjà."""
        with self._verrou:
            if self._fil is not None and self._fil.is_alive():
                return
            self._arret.clear()
            self._repos.clear()
            self._fil = threading.Thread(target=self._executer, name='myphotos-indexeur', daemon=True)
            self._fil.start()

    def pause(self):
        """Met l'indexation en pause (à la fin du lot en cours, en moins d'une seconde ou deux)."""
        self._en_marche.clear()

    def reprendre(self):
        self._en_marche.set()

    def arreter(self, attendre=True):
        """Arrête le fil. Ce qui est déjà analysé est enregistré ; le reste le sera au prochain lancement."""
        self._arret.set()
        self._en_marche.set()
        self._reveil.set()
        fil = self._fil
        if attendre and fil is not None and fil is not threading.current_thread():
            fil.join(timeout=120)

    def relancer(self, erreurs=False, dossiers=None):
        """Refait un inventaire (après l'ajout ou le retrait d'un dossier, par exemple), puis analyse ce qui
        manque. erreurs=True redonne aussi une chance aux photos en erreur ; dossiers (identifiants) : ne revoir que
        ceux-là (sinon tous). Renvoie le numéro de la demande : elle est satisfaite quand etat()['inventaire_fait'] l'a
        atteint (le bilan de cet inventaire est alors dans etat()['bilan'])."""
        with self._verrou:                       # demande et réveil ensemble (voir _cycle)
            self._inventaire_demande = True
            self._effacer_erreurs = self._effacer_erreurs or bool(erreurs)
            if dossiers is None:
                self._tous = True
            else:
                self._cibles.update(int(d) for d in dossiers)
            self._demande += 1
            numero = self._demande
            self._repos.clear()
            self._reveil.set()
        self.demarrer()
        return numero

    def attendre(self, timeout=None):
        """Attend que le travail en cours soit fini (phase 'termine' ou 'erreur'). Vrai si c'est le cas."""
        return self._repos.wait(timeout)

    @property
    def actif(self):
        return self._fil is not None and self._fil.is_alive()

    def etat(self):
        """{phase, faites, total, a_faire, debit, eta_s, dossier_courant, erreurs, materiel, message,
        analysees, en_attente, trouvees, traitees}.

        faites = analysées + erreurs ; total = toutes les photos connues ; a_faire = à analyser dans les
        dossiers joignables (en_attente : à analyser mais dossier injoignable) ; debit en photos/s ;
        eta_s : secondes restantes estimées (None si inconnu) ; trouvees : images vues par l'inventaire en
        cours ; traitees : photos traitées depuis le début de l'analyse en cours.
        """
        with self._verrou:
            e = dict(self._etat)
            mesures = list(self._mesures)
            e['inventaire_demande'] = self._demande
            e['inventaire_fait'] = self._fait
            e['inventaire_en_cours'] = self._en_traitement
            e['bilan'] = dict(self._bilan) if self._bilan else None
        c = self._comptes()
        if not self._en_marche.is_set() and e['phase'] in ('inventaire', 'analyse'):
            e['phase'] = 'pause'
        a_faire = max(0, c['a_faire'] - c['en_attente'])
        debit = 0.0
        if e['phase'] == 'analyse' and len(mesures) >= 2 and mesures[-1][0] - mesures[0][0] >= 1.0:
            debit = (mesures[-1][1] - mesures[0][1]) / (mesures[-1][0] - mesures[0][0])
        e.update(total=c['total'], faites=c['analysees'] + c['erreurs'], a_faire=a_faire, erreurs=c['erreurs'],
                 analysees=c['analysees'], en_attente=c['en_attente'], debit=round(debit, 1),
                 eta_s=round(a_faire / debit) if debit > 0 else None)
        if not e.get('message'):
            e['message'] = self._message(e)
        return e

    # ------------------------------------------------ état

    def _changer(self, phase=None, **champs):
        """Met à jour l'état ; un changement de phase efface le message particulier (sauf s'il est donné)."""
        with self._verrou:
            if phase is not None:
                self._etat['phase'] = phase
                self._etat['message'] = None
            self._etat.update(champs)

    def _comptes(self):
        maintenant_s = time.monotonic()
        if self._comptes_cache is None or maintenant_s - self._comptes_t >= 1.0:
            try:
                self._comptes_cache = self.base.compter()
                self._comptes_t = maintenant_s
            except Exception:
                journal.exception('comptage impossible')
                if self._comptes_cache is None:
                    return {'total': 0, 'analysees': 0, 'a_faire': 0, 'erreurs': 0, 'indisponibles': 0,
                            'en_attente': 0}
        return self._comptes_cache

    def _comptes_perimes(self):
        self._comptes_t = 0.0

    @staticmethod
    def _message(e):
        phase = e['phase']
        if phase == 'attente':
            return 'En attente.'
        if phase == 'pause':
            return 'En pause.'
        if phase == 'inventaire':
            m = f'Recherche des photos dans « {e["dossier_courant"]} »…' if e.get('dossier_courant') \
                else 'Recherche des photos…'
            return m + (f' ({_nombre(e["trouvees"])} trouvées)' if e.get('trouvees') else '')
        if phase == 'analyse':
            return f'Analyse des photos : {_nombre(e["faites"])} sur {_nombre(e["total"])}.'
        if phase == 'termine':
            if not e['total']:
                return 'Aucune photo pour le moment.'
            morceaux = ['Toutes les photos sont analysées.' if not e['a_faire'] else
                        f'Analyse terminée ; {_photos(e["a_faire"])} n\'ont pas pu être lues pour le moment '
                        '(nouvel essai au prochain passage).']
            if e['en_attente']:
                morceaux.append(f'{_photos(e["en_attente"])} attendent que leur dossier soit de nouveau '
                                'accessible.')
            return ' '.join(morceaux)
        return 'Erreur.'

    # ------------------------------------------------ boucle du fil

    def _executer(self):
        try:
            while not self._arret.is_set():
                self._repos.clear()
                self._cycle()
                if self._arret.is_set():
                    break
                with self._verrou:
                    encore = self._inventaire_demande
                    if not encore:
                        # Sous le verrou : une relance arrivée juste avant ne peut plus être effacée par ce « repos »
                        # (attendre() rendait la main alors que le passage demandé n'avait pas eu lieu).
                        self._repos.set()
                if encore:
                    continue
                self._reveil.wait(self.inventaire_auto_s)
                self._reveil.clear()
        finally:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None
            self._priorite.retablir()
            self._moteur = None
            self._changer('attente', message='Arrêté.', dossier_courant=None)
            self._repos.set()

    def _interrompu(self):
        return self._arret.is_set() or self._inventaire_demande

    def _cycle(self):
        with self._verrou:
            self._inventaire_demande = False
            effacer, self._effacer_erreurs = self._effacer_erreurs, False
            numero = self._demande
            self._en_traitement = numero
            cibles = None if (self._tous or not self._cibles) else set(self._cibles)
            self._tous, self._cibles = False, set()
            # Ce passage répond aux relances déjà reçues : leur réveil, resté levé, en lançait un second pour rien
            # juste après la mise au repos (attendre() rendait la main, puis l'inventaire repartait).
            self._reveil.clear()
        self._passees.clear()
        try:
            self._attendre_reprise()
            if not self._repare:
                self.base.reparer()
                self._repare = True
            self._verifier_modele()
            if effacer:
                self.base.effacer_erreurs()
            self._inventaire(cibles)
            if not self._arret.is_set():
                with self._verrou:
                    self._fait = max(self._fait, numero)
                    self._bilan = dict(self._bilan_courant, numero=numero, quand=maintenant(),
                                   tous=cibles is None)
            if self._interrompu():
                return
            self._analyser()
            if self._interrompu():
                return
            # Le moteur est GARDÉ entre deux passages : avec DirectML, détruire une session pendant qu'une autre
            # vit encore (celle de la recherche) fait planter le processus (constaté le 23/11/2026).
            self._comptes_perimes()
            self._changer('termine', dossier_courant=None)
        except MoteurIndisponible as e:
            journal.warning('%s', e)
            self._moteur = None
            self._changer('erreur', message=str(e), dossier_courant=None)
        except Exception as e:
            journal.exception('indexation interrompue')
            self._moteur = None
            self._changer('erreur', message=f"L'indexation s'est arrêtée sur une erreur inattendue : {e}",
                          dossier_courant=None)
        finally:
            self._priorite.retablir()
            self._comptes_perimes()

    def _attendre_reprise(self, avant=None):
        """Si une pause est demandée : enregistre ce qui est prêt (avant), puis attend reprendre() ou l'arrêt."""
        if self._en_marche.is_set() or self._arret.is_set():
            return
        if avant:
            avant()
        self._priorite.retablir()
        with self._verrou:
            phase, message = self._etat['phase'], self._etat['message']
        self._changer('pause')
        while not self._en_marche.wait(0.25):
            if self._arret.is_set():
                break
        self._changer(phase, message=message)
        with self._verrou:
            self._mesures.clear()                # le débit repart de zéro après une pause

    def _verifier_modele(self):
        """Si le modèle a changé depuis la dernière fois, tous les vecteurs sont à refaire (pas les miniatures)."""
        if self.base.meta_get('modele') != self.cle_modele:
            n = self.base.adopter_modele(self.cle_modele)   # d'un seul coup (voir Base.adopter_modele)
            if n:
                journal.info('modèle changé (%s) : %d vecteurs à refaire', self.cle_modele, n)

    # ------------------------------------------------ inventaire

    def _inventaire(self, cibles=None):
        self._changer('inventaire', dossier_courant=None, trouvees=0)
        self._bilan_courant = {'nouvelles': 0, 'retirees': 0, 'injoignables': [], 'dossiers': 0}
        for d in self.base.dossiers():
            if cibles is not None and d['id'] not in cibles:
                continue
            if self._arret.is_set():
                return
            self._attendre_reprise()
            self._inventorier(d)
            self._comptes_perimes()

    def _inventorier(self, d):
        did, racine = d['id'], d['chemin']
        self._changer(dossier_courant=racine, trouvees=0)
        connus = self.base.connus(did)
        parcours = Parcours(racine, exclus={cle_chemin(x['chemin']) for x in self.base.exclusions(did)})
        bilan = self._bilan_courant if self._bilan_courant is not None else {
            'nouvelles': 0, 'retirees': 0, 'injoignables': [], 'dossiers': 0}
        bilan['dossiers'] += 1
        ajoutees = 0
        nouveaux, modifies, vus = [], [], set()
        par_empreinte = {}                       # (taille, modifie) -> nouveaux chemins (déplacements)
        trouvees = 0
        complet = False
        try:
            for chemin, taille, modifie in parcours:
                trouvees += 1
                info = connus.get(chemin)
                if info is None:
                    nouveaux.append((chemin, taille, modifie, iso_depuis_ns(modifie)))
                    par_empreinte.setdefault((taille, modifie), []).append(chemin)
                else:
                    vus.add(chemin)
                    if info[1] != taille or info[2] != modifie:
                        modifies.append((info[0], taille, modifie, iso_depuis_ns(modifie)))
                if len(nouveaux) >= PAQUET_INVENTAIRE:
                    ajoutees += self.base.ajouter_photos(did, nouveaux)
                    nouveaux = []
                if len(modifies) >= PAQUET_INVENTAIRE:
                    self.base.marquer_modifiees(modifies)
                    modifies = []
                if trouvees % 100 == 0:
                    self._changer(dossier_courant=parcours.courant, trouvees=trouvees)
                    if self._arret.is_set():
                        break
                    self._attendre_reprise()
            else:
                complet = True
        except OSError as e:
            journal.info('dossier injoignable, ses photos sont gardées : %s (%s)', racine, e)
            self.base.marquer_dossier(did, disponible=False)
            bilan['injoignables'].append(racine)
            bilan['nouvelles'] += ajoutees
            return
        if parcours.sautes:
            journal.info('%d nom(s) de fichier ou de dossier impossibles à enregistrer, ignorés, dans %s',
                         parcours.sautes, racine)
        if nouveaux:
            ajoutees += self.base.ajouter_photos(did, nouveaux)
        if modifies:
            self.base.marquer_modifiees(modifies)
        self._changer(trouvees=trouvees)
        if not complet:
            bilan['nouvelles'] += ajoutees
            return                               # arrêt demandé : rien n'est déclaré disparu
        if parcours.vide and connus:
            # Un partage réseau monté sans son disque paraît vide ; un dossier Images que OneDrive n'a pas encore rempli
            # ne contient que desktop.ini : prudence, on garde tout. (Des photos supprimées d'un dossier qui garde un
            # autre contenu, même un simple document ou un sous-dossier, sont bien retirées.)
            journal.info('dossier vide alors que des photos y étaient connues, considéré injoignable : %s', racine)
            self.base.marquer_dossier(did, disponible=False)
            bilan['injoignables'].append(racine)
            bilan['nouvelles'] += ajoutees
            return
        disparus = [(ch, info) for ch, info in connus.items() if ch not in vus and not parcours.sous_echec(ch)]
        if disparus:
            deplaces, oublies = [], []
            for ch, (pid, taille, modifie) in disparus:
                candidats = par_empreinte.get((taille, modifie))
                if candidats:                    # même taille et même date : déplacée ou renommée
                    nom = os.path.basename(ch).lower()
                    j = next((i for i, c in enumerate(candidats) if os.path.basename(c).lower() == nom), 0)
                    deplaces.append((pid, candidats.pop(j)))
                else:
                    oublies.append(pid)
            if deplaces:
                self.base.deplacer_photos(deplaces)
                ajoutees -= len(deplaces)        # une photo déplacée n'est ni nouvelle ni retirée
            if oublies:
                self.base.oublier_photos(oublies)
                bilan['retirees'] += len(oublies)
        bilan['nouvelles'] += max(0, ajoutees)
        self.base.marquer_dossier(did, disponible=True, vu=maintenant())

    # ------------------------------------------------ analyse

    def _charger_moteur(self, materiel):
        self._changer(message='Préparation du moteur de reconnaissance…')
        try:
            self._moteur = self.fabrique_moteur(materiel=materiel)
        except Exception as e:
            self._moteur = None
            raise MoteurIndisponible(f"Le moteur de reconnaissance n'a pas pu démarrer ({e}).") from e
        self._changer(message=None, materiel=getattr(self._moteur, 'materiel', None) or materiel)
        return self._moteur

    def _vecteurs_lot(self, lot):
        """Vecteurs d'un lot ; si la carte graphique échoue, on recrée le moteur sur le processeur."""
        try:
            return self._verifier_vecteurs(self._moteur.vecteurs_images(lot), len(lot))
        except Exception as e:
            if getattr(self._moteur, 'materiel', None) != 'carte graphique':
                raise
            journal.warning('la carte graphique a échoué (%r) : analyse sur le processeur', e)
            self._moteur = None
            self._charger_moteur('processeur')
            return self._verifier_vecteurs(self._moteur.vecteurs_images(lot), len(lot))

    @staticmethod
    def _verifier_vecteurs(v, n):
        v = np.asarray(v, dtype=np.float32)
        if v.ndim != 2 or v.shape[0] != n or not np.all(np.isfinite(v)):
            raise FloatingPointError(f'vecteurs invalides {v.shape}')
        return v

    def _vecteurs(self, pixels):
        """Liste de vecteurs (ou None pour une image que le modèle refuse)."""
        lot = np.stack(pixels)
        try:
            return list(self._vecteurs_lot(lot))
        except MoteurIndisponible:
            raise
        except Exception as e:
            journal.warning('lot refusé par le modèle (%r) : essai image par image', e)
        vecs = []
        for i in range(len(lot)):
            try:
                vecs.append(self._verifier_vecteurs(self._moteur.vecteurs_images(lot[i:i + 1]), 1)[0])
            except Exception:
                vecs.append(None)
        if all(v is None for v in vecs):
            raise MoteurIndisponible('Le moteur de reconnaissance ne répond plus correctement.')
        return vecs

    def _pool_decodage(self):
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self.fils_decodage, thread_name_prefix='myphotos-lecture')
        return self._pool

    def _analyser(self):
        file = deque(l for l in self.base.a_analyser() if l[0] not in self._passees)
        if not file:
            return
        self._changer('analyse', traitees=0)
        moteur = self._moteur if self._moteur is not None else self._charger_moteur('auto')
        taille = moteur.taille_image
        with self._verrou:
            self._mesures.clear()
            self._mesures.append((time.monotonic(), 0))
        self._priorite.baisser()
        pool = self._pool_decodage()
        en_vol = deque()                         # (ligne, future) dans l'ordre de la file
        lot = []                                 # (ligne, lecture) décodées, en attente du modèle
        analyses, erreurs = [], []               # prêtes à écrire
        injoignables = set()                     # dossiers devenus injoignables pendant l'analyse
        echecs_suivis = {}                       # dossier_id -> lectures passagères ratées d'affilée
        traitees = 0
        derniere = time.monotonic()

        def remplir():
            while len(en_vol) < AVANCE and file:
                ligne = file.popleft()
                if ligne[4] not in injoignables:
                    en_vol.append((ligne, pool.submit(preparer, ligne[1], taille)))

        def vectoriser():
            if not lot:
                return
            vecs = self._vecteurs([r['pixels'] for _, r in lot])
            for (ligne, r), v in zip(lot, vecs):
                pid, _, t, m = ligne[:4]
                if v is None:
                    erreurs.append({'id': pid, 'taille': t, 'modifie': m, 'erreur': 'analyse impossible',
                                    'largeur': r['largeur'], 'hauteur': r['hauteur']})
                else:
                    analyses.append({'id': pid, 'taille': t, 'modifie': m, 'largeur': r['largeur'],
                                     'hauteur': r['hauteur'], 'date_prise': r['date_prise'], 'lat': r['lat'],
                                     'lon': r['lon'], 'vecteur': v.astype(np.float16).tobytes(),
                                     'miniature': r['miniature']})
            lot.clear()

        def ecrire():
            nonlocal derniere, traitees
            if analyses or erreurs:
                self.base.enregistrer(analyses, erreurs, self.cle_modele)
                traitees += len(analyses) + len(erreurs)
                analyses.clear()
                erreurs.clear()
                maintenant_s = time.monotonic()
                with self._verrou:
                    self._mesures.append((maintenant_s, traitees))
                    while len(self._mesures) > 2 and maintenant_s - self._mesures[0][0] > 30:
                        self._mesures.popleft()
                    self._etat['traitees'] = traitees
                self._comptes_perimes()
            derniere = time.monotonic()

        def tout_ecrire():
            vectoriser()
            ecrire()

        remplir()
        try:
            while en_vol:
                if self._interrompu():
                    break
                if not self._en_marche.is_set():
                    self._attendre_reprise(avant=tout_ecrire)
                    self._priorite.baisser()
                    continue
                ligne, futur = en_vol.popleft()
                r = futur.result()
                remplir()
                pid, chemin, t, m, did, racine = ligne
                self._changer(dossier_courant=os.path.dirname(chemin))
                if 'erreur' not in r:
                    echecs_suivis.pop(did, None)
                    lot.append((ligne, r))
                    if len(lot) >= LOT:
                        vectoriser()
                elif r['passagere']:
                    self._passees.add(pid)
                    echecs_suivis[did] = echecs_suivis.get(did, 0) + 1
                    if echecs_suivis[did] >= ECHECS_AVANT_CONTROLE and did not in injoignables \
                            and not os.path.isdir(chemin_systeme(racine)):
                        journal.info('dossier devenu injoignable pendant l\'analyse : %s', racine)
                        injoignables.add(did)
                        self.base.marquer_dossier(did, disponible=False)
                else:
                    erreurs.append({'id': pid, 'taille': t, 'modifie': m, 'erreur': r['erreur'],
                                    'largeur': r.get('largeur'), 'hauteur': r.get('hauteur')})
                if len(analyses) + len(erreurs) >= ECRITURE_N or time.monotonic() - derniere >= ECRITURE_S:
                    ecrire()
            else:
                vectoriser()                     # fin de la file : le dernier lot, même incomplet
            ecrire()                             # en cas d'arrêt, le lot pas encore vectorisé sera refait
        finally:
            for _, futur in en_vol:
                futur.cancel()
            self._priorite.retablir()
