"""Doublons : photos identiques, retouchées, ou prises au même instant.

Trois niveaux de groupes :
- « identiques » : même contenu de fichier (SHA-256, calculé seulement pour les photos de même taille en octets) ;
- « retouchees » : la même photo redimensionnée, recompressée (envoi WhatsApp…), légèrement recadrée ou redressée,
  convertie (PNG), sans EXIF, avec une bordure, un peu plus claire ou plus sombre ;
- « proches » : rafales, plusieurs prises du même instant. On veut souvent les garder : rien n'y est coché d'office.

Méthode, sans relire les photos (sauf le SHA-256 des fichiers de même taille) :
1. empreinte perceptuelle (pHash, 64 bits) de chaque miniature, gardée dans index.db (table doublons_empreintes),
   refaite seulement si la photo change ;
2. paires candidates, par produits de matrices par blocs : empreintes quasi identiques (distance de Hamming), ou
   vecteurs du moteur voisins avec des empreintes pas trop éloignées et des dates compatibles ; au plus quelques-unes
   par photo, les plus ressemblantes ;
3. vérification de chaque candidate sur les deux miniatures (128 px) : on cherche comment poser l'une sur l'autre
   (échelle, légère rotation, décalage, lumière ; méthode de Lucas et Kanade). « Retouchée » si l'une tient tout
   entière dans l'autre et que rien ne diffère, bloc par bloc (un contour adouci par la réduction est toléré, un trait
   nouveau sur une surface lisse ne l'est pas) ; « proche » si elles se superposent en grande partie (cadre décalé,
   autre exposition, sujet qui bouge) ou si les vecteurs sont très voisins. Chaque vérification est gardée (table
   doublons_paires) ; deux photos déjà réunies comme retouches ne sont pas comparées ;
4. regroupement : les identiques d'abord ; leurs copies écartées, les retouchées (de proche en proche) ; puis les
   proches, autour de la meilleure photo de chaque série (pas de chaîne sans fin).

Les seuils viennent de outils/calibrer_doublons.py (images publiques) ; chiffres et choix dans outils/DOUBLONS.md.
"""
import functools
import hashlib
import io
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
from PIL import Image

from .base import chemin_systeme, maintenant

journal = logging.getLogger(__name__)

# Version des empreintes et des vérifications : la changer fait tout recalculer.
VERSION = 2

TYPES = ('identiques', 'retouchees', 'proches')

# ---------------------------------------------------------------- réglages
# Mesurés le 24/11/2026 sur des images publiques (outils/calibrer_doublons.py, résumé dans outils/DOUBLONS.md), avec le
# moteur « base ». Les autres moteurs prennent les valeurs de repli (cosinus non mesurés ; la vérification sur les
# miniatures, qui décide seule des retouches, ne dépend pas du moteur).

# Paires à vérifier sur les miniatures :
HAMMING_CANDIDAT = 12           # - empreintes à au plus 12 bits l'une de l'autre (toujours vérifiées) ;
COSINUS_CANDIDAT = {'base': 0.86}   # - vecteurs voisins (cosinus) …
COSINUS_CANDIDAT_REPLI = 0.86
HAMMING_RETOUCHE_MAX = 26       #   … et empreintes à au plus 26 bits (aucune retouche mesurée au-delà) ;
VOISINES_MAX = 12               # - au plus les 12 plus ressemblantes de chaque photo (série de 100 photos : 1 200
                                #   vérifications au lieu de 4 950).
FENETRE_PROCHES_S = 1800        # Deux dates de prise de vue connues à plus de 30 min l'une de l'autre : ni la même photo
                                # (une copie garde sa date, ou la perd), ni « du même instant ». Non mesuré (le jeu
                                # public n'a pas de dates) : choix prudent.
HAMMING_IDENTIQUE = 8           # même taille et miniatures voisines : on compare les fichiers octet par octet
CONTRASTE_MIN = 6.0             # image presque unie (écart type des gris < 6 sur 255) : identiques seulement

# Retouchée : l'une des miniatures, posée sur l'autre, doit
DEBORD_MAX = 0.015              # … y tenir entièrement (dépasser d'au plus 1,5 % de sa largeur ou de sa hauteur),
RECOUVRE_MIN = 0.97             # … être comparée sur au moins 97 % de sa surface,
COUVERTURE_MIN = 0.55           # … couvrir au moins 55 % de l'autre (recadrage léger seulement),
ROTATION_MAX = 4.0              # … n'être tournée que d'au plus 4 degrés,
NCC_MIN = 0.90                  # … lui ressembler (corrélation des gris d'au moins 0,90),
RELIEF_MAX = 0.6                # … et nulle part en différer. Dans chacun des 8 × 8 blocs : écart des gris rapporté au
DIFFERENTS_MAX = 0.15           #     relief local d'au plus 0,6 en moyenne, et au plus 15 % de pixels nettement
SEUIL_DIFFERENT = 0.8           #     différents (écart > 0,8 fois le relief) : un trait nouveau sur une surface lisse
ECART_BLOC_MAX = 30.0           #     (texte ajouté, sujet qui a bougé) compte, pas un contour un peu adouci ; écart
                                #     moyen d'au plus 30 niveaux sur 255 (garde-fou).

# Proches (rien n'y est proposé à la suppression) : miniatures superposables sur une grande partie de leur surface
# (cadre un peu décalé, autre exposition, sujet qui bouge), ou vecteurs très voisins.
NCC_PROCHE = 0.93               # corrélation des gris d'au moins 0,93 (photos différentes : 0,88 au plus) …
RECOUVRE_PROCHE = 0.6           # … sur au moins 60 % de la surface ;
COSINUS_PROCHE = {'base': 0.96}     # ou cosinus d'au moins 0,96 (deux gâteaux différents vont jusqu'à 0,96 : plus bas,
COSINUS_PROCHE_REPLI = 0.97     # trop de photos différentes seraient réunies).

# Copies évidentes, évitées quand on choisit la photo à garder (comparées sans accents ni majuscules).
_COPIES = re.compile(r'\bcopie\b|\bcopy\b|whatsapp|\benvoye|\(\d{1,2}\)')

# Vérification : côté (px) des niveaux de gris comparés, nombre de blocs par côté pour l'écart local.
COTE_VERIF = 128
BLOCS = 8
MARGE_MESURE = 4                           # px (sur 128) laissés de côté au bord de la photo contenue
ITERATIONS = ((8, 1), (3, 2), (2, 2))      # Lucas-Kanade à 32, 64 et 128 px : (passages au plus, 1 point sur n)
QUANTILES = 17                             # correspondance des lumières : nombre de quantiles
RELIEF_PLANCHER = 8.0                      # relief minimal (niveaux par pixel) : bruit des surfaces lisses
_ECHELLES = np.linspace(1.0, 0.70, 7)      # part de la largeur du contenant couverte par le contenu
NCC_GROSSIER_MIN = 0.65                    # en dessous, à basse définition : rien à voir, inutile d'affiner (vraies
                                           # retouches : 0,72 au moins ; rafales superposables : 0,63 une fois sur 1 600)
NCC_LK_MIN = 0.7                           # idem, une fois superposées au mieux à basse définition

BLOC_VECTEURS = 2048                       # produits de vecteurs par blocs de 2048 × 8192
BLOC_COLONNES = 8192
BLOC_EMPREINTES = 2048                     # distances de Hamming, de même
LOT_ECRITURE = 500
FILS_EMPREINTES = 4

_MASQUE64 = (1 << 64) - 1

# Étapes, dans l'ordre, et leur part approximative de la durée d'un premier calcul.
PHASES = ('attente', 'empreintes', 'identiques', 'comparaison', 'verification', 'regroupement', 'termine',
          'annule', 'erreur')
_POIDS = {'empreintes': 0.45, 'identiques': 0.1, 'comparaison': 0.2, 'verification': 0.2, 'regroupement': 0.05}
_MESSAGES = {'attente': 'Pas encore lancé.', 'empreintes': 'Lecture des miniatures…',
             'identiques': 'Comparaison des fichiers de même taille…', 'comparaison': 'Recherche des ressemblances…',
             'verification': 'Vérification des photos qui se ressemblent…', 'regroupement': 'Regroupement…',
             'termine': 'Terminé.', 'annule': 'Recherche des doublons interrompue.',
             'erreur': 'La recherche des doublons a échoué.'}

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS doublons_meta (cle TEXT PRIMARY KEY, valeur)",
    # Une ligne par photo examinée. taille et modifie : ceux de la photo quand la ligne a été faite (un changement
    # = à refaire) ; phash : empreinte de la miniature (entier signé), NULL sans miniature ; contraste : écart type
    # des gris (0-255) ; provisoire : faite alors que la photo attendait son analyse (miniature peut-être périmée) ;
    # sha256 : contenu du fichier, calculé seulement quand une autre photo a la même taille.
    """CREATE TABLE IF NOT EXISTS doublons_empreintes (
        photo_id   INTEGER PRIMARY KEY,
        taille     INTEGER NOT NULL,
        modifie    INTEGER NOT NULL,
        phash      INTEGER,
        contraste  REAL,
        provisoire INTEGER NOT NULL DEFAULT 0,
        sha256     BLOB)""",
    # Vérifications déjà faites (a < b) : mesures en JSON, ou 'null' si les miniatures n'ont rien à voir.
    """CREATE TABLE IF NOT EXISTS doublons_paires (
        a      INTEGER NOT NULL,
        b      INTEGER NOT NULL,
        mesure TEXT NOT NULL,
        PRIMARY KEY (a, b)) WITHOUT ROWID""",
)


class Annule(InterruptedError):
    """Recherche des doublons interrompue (arret levé)."""


# ---------------------------------------------------------------- petites fonctions

def _signe(v):
    """Entier non signé sur 64 bits -> entier signé (ce que SQLite sait enregistrer)."""
    return v - (1 << 64) if v >= 1 << 63 else v


def distance(h1, h2):
    """Distance de Hamming entre deux empreintes (nombre de bits différents, 0 à 64)."""
    return ((h1 ^ h2) & _MASQUE64).bit_count()


def _sans_accents(texte):
    return ''.join(c for c in unicodedata.normalize('NFD', texte) if unicodedata.category(c) != 'Mn').lower()


def motif_copie(chemin):
    """Ce qui fait d'un chemin une copie évidente (« WhatsApp », « - Copie », « (1) »…), ou None."""
    m = _COPIES.search(_sans_accents(chemin or ''))
    return m.group(0) if m else None


def _secondes(date_iso):
    """'AAAA-MM-JJTHH:MM:SS' -> secondes (pour comparer deux dates), ou None."""
    if not date_iso:
        return None
    try:
        return datetime.fromisoformat(date_iso).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def octets_lisibles(n):
    """1234567 -> '1,2 Mo'."""
    for unite, f in (('Go', 1e9), ('Mo', 1e6), ('Ko', 1e3)):
        if n >= f:
            return f'{n / f:.1f} {unite}'.replace('.', ',')
    return f'{n} octets'


# ---------------------------------------------------------------- images

_N = 32
_k = np.arange(_N)
_DCT = np.cos(np.pi * (2 * _k[None, :] + 1) * _k[:, None] / (2 * _N)).astype(np.float32)
del _k


def _gris(jpeg, cote):
    """Miniature (octets JPEG) -> image en niveaux de gris, plus grand côté réduit à `cote` px au plus.
    Le JPEG n'est décodé qu'en partie : la luminance seule, directement à l'échelle utile."""
    im = Image.open(io.BytesIO(jpeg))
    try:
        im.draft('L', (cote, cote))
    except Exception:
        pass
    if im.mode != 'L':
        if im.mode in ('RGBA', 'LA', 'PA', 'P'):
            im = im.convert('RGBA')
            fond = Image.new('RGBA', im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(fond, im)
        im = im.convert('L')
    w, h = im.size
    r = cote / max(w, h)
    if r < 1:
        im = im.resize((max(1, round(w * r)), max(1, round(h * r))), Image.Resampling.BOX)
    return im


def empreinte(jpeg):
    """(phash, contraste) d'une miniature : pHash 64 bits (entier signé) et écart type des gris (0-255).
    Lève une exception si la miniature est illisible."""
    a = np.asarray(_gris(jpeg, 2 * _N).resize((_N, _N), Image.Resampling.BOX), dtype=np.float32)
    d = (_DCT @ a @ _DCT.T)[:8, :8].ravel()
    bits = d > np.median(d[1:])
    return _signe(int.from_bytes(np.packbits(bits, bitorder='little').tobytes(), 'little')), round(float(a.std()), 2)


def _flou(a):
    """Flou binomial 5 × 5 (proche d'un flou gaussien de rayon 1 px), bords répétés."""
    h, w = a.shape
    p = np.pad(a, 2, mode='edge')
    t = (p[:, 0:w] + p[:, 4:w + 4]) * 0.0625 + (p[:, 1:w + 1] + p[:, 3:w + 3]) * 0.25 + p[:, 2:w + 2] * 0.375
    return (t[0:h] + t[4:h + 4]) * 0.0625 + (t[1:h + 1] + t[3:h + 3]) * 0.25 + t[2:h + 2] * 0.375


def _reduire(a, w, h):
    return np.array(Image.fromarray(a, 'F').resize((max(1, w), max(1, h)), Image.Resampling.BOX), dtype=np.float32)


class _Niveau:
    """Une définition de la pyramide : gris flouté, et le même avec ses gradients (pour l'échantillonnage) ;
    gabarits : versions réduites, centrées et leur norme, gardées pour la recherche grossière."""
    __slots__ = ('f', 'pile', 'h', 'w', 'gabarits')

    def __init__(self, a):
        self.f = _flou(a)
        gy, gx = np.gradient(self.f)
        self.pile = np.stack([self.f, gx, gy], axis=-1).reshape(-1, 3)
        self.h, self.w = a.shape
        self.gabarits = {}

    def gabarit(self, w, h):
        """L'image réduite à w × h, moins sa moyenne, et sa norme."""
        g = self.gabarits.get((w, h))
        if g is None:
            t = _reduire(self.f, w, h).ravel()
            t -= t.mean()
            g = self.gabarits[(w, h)] = (t, float(np.sqrt(np.dot(t, t))))
        return g


def preparer_verification(jpeg):
    """Miniature -> pyramide [fin (COTE_VERIF px), moitié, quart] pour la vérification."""
    a = np.asarray(_gris(jpeg, COTE_VERIF), dtype=np.float32)
    niveaux = [a]
    for _ in range(2):
        h, w = niveaux[-1].shape
        niveaux.append(_reduire(niveaux[-1], round(w / 2), round(h / 2)))
    return [_Niveau(n) for n in niveaux]


def _echantillonner(niv, X, Y, gris_seul=False):
    """Valeurs (gris, gradient x, gradient y) du niveau aux points (X, Y) (positifs, dans l'image), interpolées :
    tableau (n, 3) ; gris_seul : le gris seulement, tableau (n,)."""
    xi = X.astype(np.intp)                  # X >= 0 : troncature = partie entière
    yi = Y.astype(np.intp)
    fx = X - xi
    fy = Y - yi
    np.minimum(xi, niv.w - 2, out=xi)
    np.minimum(yi, niv.h - 2, out=yi)
    i = yi * niv.w + xi
    if gris_seul:
        p = niv.f.ravel()
    else:
        p = niv.pile
        fx = fx[:, None]
        fy = fy[:, None]
    a = p.take(i, axis=0)
    b = p.take(i + 1, axis=0)
    c = p.take(i + niv.w, axis=0)
    d = p.take(i + niv.w + 1, axis=0)
    ab = a + fx * (b - a)
    return ab + fy * (c + fx * (d - c) - ab)


def _grossier(niv_p, niv_q):
    """Q posé dans P à basse définition, sans rotation : meilleure place (ncc, lam, cx, cy), ou None.
    lam : pixels de P par pixel de Q ; (cx, cy) : centre de Q dans P."""
    P = niv_p.f
    hp, wp = P.shape
    hq, wq = niv_q.h, niv_q.w
    meilleur = None
    for s in _ECHELLES:
        wi = int(round(s * wp))
        hi = int(round(wi * hq / wq))
        if hi > hp or hi < round(_ECHELLES[-1] * hp) or wi < 6 or hi < 6:
            continue
        t, nt = niv_q.gabarit(wi, hi)
        if nt < 1e-3:
            continue
        fen = np.lib.stride_tricks.sliding_window_view(P, (hi, wi))
        ny, nx = fen.shape[:2]
        m = np.ascontiguousarray(fen).reshape(ny * nx, hi * wi)
        n = hi * wi
        s1 = m.sum(axis=1)
        var = np.einsum('ij,ij->i', m, m) - s1 * s1 / n
        ncc = (m @ t) / (np.sqrt(np.maximum(var, 1e-3)) * nt)
        k = int(np.argmax(ncc))
        if meilleur is None or ncc[k] > meilleur[0]:
            y, x = divmod(k, nx)
            meilleur = (float(ncc[k]), wi / wq, x + (wi - 1) / 2, y + (hi - 1) / 2)
    return meilleur


@functools.lru_cache(maxsize=256)
def _grille(hq, wq, pas, marge=0):
    """Points d'une image hq × wq (un sur `pas`, sans les `marge` px du bord) : écarts au centre (du, dv) et indices
    (iv, iu). Les miniatures ont peu de formats différents : gardée d'une fois sur l'autre (tableaux en lecture seule)."""
    v, u = np.mgrid[marge:hq - marge:pas, marge:wq - marge:pas]
    r = ((u - (wq - 1) / 2).ravel().astype(np.float32), (v - (hq - 1) / 2).ravel().astype(np.float32),
         v.ravel(), u.ravel())
    for x in r:
        x.flags.writeable = False
    return r


def _lk(P, Q, prm, iterations, pas):
    """Affine Q sur P (échelle, rotation, décalage, gain et biais de lumière) par Gauss-Newton (Lucas-Kanade).
    prm = [lam, theta, cx, cy, gain, biais] dans les pixels de ce niveau. Renvoie (prm, corrélation des gris
    superposés au dernier passage), ou None si la méthode diverge."""
    du, dv, iv, iu = _grille(Q.h, Q.w, pas)
    q = Q.f[iv, iu]
    lam, th, cx, cy, al, be = prm
    correlation = 0.0
    for it in range(iterations):
        c, s = math.cos(th), math.sin(th)
        a1 = c * du - s * dv                   # dérivées de X et de Y par rapport à lam
        a2 = s * du + c * dv
        X = cx + lam * a1
        Y = cy + lam * a2
        m = (X >= 0) & (X <= P.w - 1) & (Y >= 0) & (Y <= P.h - 1)
        dedans = int(np.count_nonzero(m))
        if dedans < 30:
            return None
        if dedans < len(m):                    # (tous dedans, cas courant : pas de copie)
            X, Y, a1, a2, qm = X[m], Y[m], a1[m], a2[m], q[m]
        else:
            qm = q
        e = _echantillonner(P, X, Y)
        p, gx, gy = e[:, 0], e[:, 1], e[:, 2]
        if al is None:                     # premier passage : gain et biais par moindres carrés
            vp = float(p.var())
            al = float(np.cov(p, qm, bias=True)[0, 1] / vp) if vp > 1e-6 else 1.0
            be = float(qm.mean() - al * p.mean())
        r = al * p + be - qm
        gxa = al * gx
        gya = al * gy
        J = np.empty((len(p), 6), np.float64)
        J[:, 0] = gxa * a1 + gya * a2
        J[:, 1] = lam * (gya * a1 - gxa * a2)
        J[:, 2] = gxa
        J[:, 3] = gya
        J[:, 4] = p
        J[:, 5] = 1.0
        H = J.T @ J
        try:
            d = np.linalg.solve(H + np.diag(np.diag(H) * 1e-3 + 1e-9), -(J.T @ r))
        except np.linalg.LinAlgError:
            return None
        lam += float(d[0])
        th += float(d[1])
        cx += float(d[2])
        cy += float(d[3])
        al += float(d[4])
        be += float(d[5])
        if not (0.3 < lam < 3.0 and abs(th) < 0.35 and math.isfinite(cx) and math.isfinite(cy)):
            return None
        fini = abs(d[2]) < 0.05 and abs(d[3]) < 0.05 and abs(d[0]) * max(Q.w, Q.h) < 0.05
        if fini or it == iterations - 1:
            sp, sq = float(p.std()), float(qm.std())
            correlation = float(((p - p.mean()) * (qm - qm.mean())).mean() / (sp * sq)) if sp * sq > 1e-6 else 0.0
        if fini:
            break
    return [lam, th, cx, cy, al, be], correlation


def _mesurer(P, Q, prm):
    """Mesures de la superposition de Q sur P (niveau fin) : voir verifier(). Les écarts bloc par bloc ne sont
    mesurés que si Q tient dans P (sinon ce n'est pas une retouche, inutile de les calculer)."""
    lam, th, cx, cy, gain = prm[0], prm[1], prm[2], prm[3], abs(prm[4] or 1.0)
    c, s = math.cos(th), math.sin(th)
    # Débord : les coins de Q, posés dans P, en dehors de P (part de la largeur ou de la hauteur de P).
    xs, ys = [], []
    for a, b in ((-Q.w / 2, -Q.h / 2), (Q.w / 2, -Q.h / 2), (-Q.w / 2, Q.h / 2), (Q.w / 2, Q.h / 2)):
        xs.append(cx + lam * (c * a - s * b))
        ys.append(cy + lam * (s * a + c * b))
    debord = max(0.0, (-0.5 - min(xs)) / P.w, (max(xs) - P.w + 0.5) / P.w, (-0.5 - min(ys)) / P.h,
                 (max(ys) - P.h + 0.5) / P.h)
    complet = debord <= DEBORD_MAX
    marge = MARGE_MESURE                    # bords de Q exclus (flou, rééchantillonnage, bordure ajoutée)
    du, dv, iv, iu = _grille(Q.h, Q.w, 1 if complet else 2, marge)
    X = cx + lam * (c * du - s * dv)
    Y = cy + lam * (s * du + c * dv)
    m = (X >= -0.5) & (X <= P.w - 0.5) & (Y >= -0.5) & (Y <= P.h - 0.5)
    recouvre = float(np.count_nonzero(m)) / max(1, len(m))
    res = {'debord': round(float(debord), 4), 'recouvre': round(recouvre, 4),
           'couverture': round(float(min(1.0, lam * lam * Q.w * Q.h / (P.w * P.h))), 4),
           'rotation': round(math.degrees(float(th)), 2), 'echelle': round(float(lam), 4),
           'emprise': round(float(max(abs(lam * Q.w / P.w - 1), abs(lam * Q.h / P.h - 1))), 4)}
    if np.count_nonzero(m) < 100:
        return dict(res, ncc=0.0)
    Xm = np.clip(X[m], 0, P.w - 1)
    Ym = np.clip(Y[m], 0, P.h - 1)
    k = iv[m] * Q.w + iu[m]
    q = Q.pile[k, 0]
    if not complet:
        p = _echantillonner(P, Xm, Ym, gris_seul=True)
        sp, sq = float(p.std()), float(q.std())
        ncc = float(((p - p.mean()) * (q - q.mean())).mean() / (sp * sq)) if sp > 1e-3 and sq > 1e-3 else 0.0
        return dict(res, ncc=round(ncc, 4))
    e = _echantillonner(P, Xm, Ym)
    p = e[:, 0]
    sp, sq = float(p.std()), float(q.std())
    ncc = float(((p - p.mean()) * (q - q.mean())).mean() / (sp * sq)) if sp > 1e-3 and sq > 1e-3 else 0.0
    # Lumière : correspondance des quantiles (toute retouche monotone : luminosité, contraste, exposition).
    niv = np.linspace(0.0, 1.0, QUANTILES)
    pas = max(1, len(p) // 4000)
    qp = np.maximum.accumulate(np.quantile(p[::pas], niv) + np.arange(QUANTILES) * 1e-3)
    qq = np.quantile(q[::pas], niv)
    ecart = np.abs(np.interp(p, qp, qq) - q)
    # Écart rapporté au relief local : un contour un peu décalé (rééchantillonnage) compte peu, un trait nouveau sur
    # une surface lisse (texte ajouté, sujet qui a bougé) compte beaucoup.
    relief = np.hypot(Q.pile[k, 1], Q.pile[k, 2]) + gain * lam * np.hypot(e[:, 1], e[:, 2]) + RELIEF_PLANCHER
    relatif = ecart / relief
    # Moyennes par bloc de Q (BLOCS × BLOCS), blocs à moitié vides exclus.
    bi = np.minimum((iv[m] * BLOCS) // Q.h, BLOCS - 1) * BLOCS + np.minimum((iu[m] * BLOCS) // Q.w, BLOCS - 1)
    nombre = np.bincount(bi, minlength=BLOCS * BLOCS)
    plein = nombre >= 0.5 * (Q.h - 2 * marge) * (Q.w - 2 * marge) / (BLOCS * BLOCS)
    if not np.any(plein):
        plein = nombre > 0

    def pire(valeurs):
        return float((np.bincount(bi, weights=valeurs, minlength=BLOCS * BLOCS)[plein] / nombre[plein]).max())
    res.update(ncc=round(ncc, 4), ecart_max=round(pire(ecart), 2), ecart_moyen=round(float(ecart.mean()), 2),
               relief_max=round(pire(relatif), 4),
               differents_max=round(pire((relatif > SEUIL_DIFFERENT).astype(np.float32)), 4))
    return res


def verifier(pa, pb):
    """Pose l'une des deux miniatures sur l'autre (pyramides de preparer_verification) et mesure l'accord.

    Renvoie None si elles n'ont rien à voir, sinon un dict : sens (0 : b posée dans a, 1 : a dans b), echelle (pixels
    de l'une par pixel de l'autre), rotation (degrés), emprise (écart de cadrage, 0 = même cadre), debord (part qui
    dépasse de l'autre), recouvre (part de la posée effectivement comparée), couverture (part de l'autre couverte),
    grossier et ncc (corrélation des gris, -1 à 1, à basse définition puis à 128 px). Si l'une tient dans l'autre
    (debord <= DEBORD_MAX), aussi, lumière corrigée et bloc par bloc (8 × 8) : ecart_max (écart moyen des gris du bloc
    le plus différent, 0-255), ecart_moyen, relief_max (écart rapporté au relief local) et differents_max (part de
    pixels nettement différents).
    """
    essais = []
    for sens, (P, Q) in enumerate(((pa, pb), (pb, pa))):
        g = _grossier(P[2], Q[2])
        if g is not None:
            essais.append((g[0], sens, g))
    essais.sort(key=lambda e: -e[0])
    if not essais or essais[0][0] < NCC_GROSSIER_MIN:
        return None
    meilleure = None
    for ncc0, sens, (_, lam, cx, cy) in essais:
        if meilleure is not None:
            if ncc0 < NCC_GROSSIER_MIN or ncc0 < essais[0][0] - 0.15:
                break
            if meilleure['ncc'] >= NCC_MIN and meilleure['debord'] > DEBORD_MAX + meilleure['emprise']:
                break                          # cadre décalé, pas seulement agrandi : dans l'autre sens aussi
        P, Q = (pa, pb) if sens == 0 else (pb, pa)
        prm = [lam, 0.0, cx, cy, None, None]
        for n, (iters, pas) in zip((2, 1, 0), ITERATIONS):
            if n < 2:                          # passage au niveau plus fin : coordonnées doublées
                f = P[n].w / P[n + 1].w
                prm[2] = (prm[2] + 0.5) * f - 0.5
                prm[3] = (prm[3] + 0.5) * P[n].h / P[n + 1].h - 0.5
                prm[0] *= (P[n].w / P[n + 1].w) / (Q[n].w / Q[n + 1].w)
            r = _lk(P[n], Q[n], prm, iters, pas)
            if r is None or (n == 2 and r[1] < NCC_LK_MIN):
                prm = None                     # diverge, ou rien à voir une fois superposées
                break
            prm = r[0]
        if prm is None:
            continue
        m = _mesurer(P[0], Q[0], prm)
        m['sens'] = sens
        m['grossier'] = round(ncc0, 4)
        # Préférence : une superposition où l'une tient dans l'autre, puis la meilleure corrélation.
        if meilleure is None or ((m['debord'] <= DEBORD_MAX, m['ncc']) > (meilleure['debord'] <= DEBORD_MAX,
                                                                          meilleure['ncc'])):
            meilleure = m
        if meilleure['debord'] <= DEBORD_MAX and meilleure['ncc'] >= NCC_MIN:
            break
    return meilleure


def est_retouchee(m):
    """Vrai si les mesures de verifier() disent : la même photo (recadrée, redimensionnée, éclaircie…)."""
    return (m is not None and m['debord'] <= DEBORD_MAX and m['recouvre'] >= RECOUVRE_MIN
            and m['couverture'] >= COUVERTURE_MIN and abs(m['rotation']) <= ROTATION_MAX
            and m['ncc'] >= NCC_MIN and m.get('relief_max', 9.0) <= RELIEF_MAX
            and m.get('differents_max', 1.0) <= DIFFERENTS_MAX and m.get('ecart_max', 255.0) <= ECART_BLOC_MAX)


def superposables(m):
    """Vrai si les miniatures coïncident sur une grande partie de leur surface (même scène, cadre décalé)."""
    return m is not None and m['ncc'] >= NCC_PROCHE and m['recouvre'] >= RECOUVRE_PROCHE


# ---------------------------------------------------------------- choix de la photo à garder

TOLERANCE_DEFINITION = 0.97     # deux définitions à moins de 3 % l'une de l'autre comptent comme égales
FORMATS = {'.jpg': 0, '.jpeg': 0, '.jfif': 0, '.heic': 0, '.heif': 0, '.tif': 1, '.tiff': 1, '.webp': 1,
           '.png': 2, '.bmp': 2, '.gif': 2}
NOMS_FORMATS = {'.png': 'PNG', '.bmp': 'BMP', '.gif': 'GIF', '.tif': 'TIFF', '.tiff': 'TIFF', '.webp': 'WebP'}


def _pixels(p):
    return (p['largeur'] or 0) * (p['hauteur'] or 0)


def definition_utile(p):
    """Pixels de la photo elle-même : sa définition, moins un cadre ou une bordure ajoutés autour (p['utile'], posé
    par _marquer_cadres)."""
    u = p.get('utile')
    return _pixels(p) if u is None else min(u, _pixels(p))


def rang_format(chemin):
    """0 : format d'appareil photo (JPEG, HEIC) ; 1 : TIFF, WebP ; 2 : PNG, BMP, GIF (conversions, captures)."""
    return FORMATS.get(os.path.splitext(str(chemin or ''))[1].lower(), 1)


def _cle_qualite(p, plafond=None):
    """Ordre de préférence : la plus grande définition utile (à 3 % près de `plafond`, la plus grande du groupe : à
    égalité), pas une copie évidente, avec sa date de prise de vue (EXIF), au format d'origine (JPEG plutôt qu'une
    conversion PNG), la plus grande image, le plus gros fichier, la prise de vue la plus ancienne (dates inconnues à
    la fin), puis la première trouvée."""
    d = definition_utile(p)
    rang_def = -plafond if plafond and d >= TOLERANCE_DEFINITION * plafond else -d
    return (rang_def, 1 if p['copie'] else 0, 0 if p['date_prise'] else 1, rang_format(p.get('chemin')),
            -_pixels(p), -p['taille'], p['date_prise'] or '~', p['date_fichier'] or '~', p['id'])


def _critere(meilleure, autre):
    """Premier critère qui fait préférer `meilleure` à `autre`."""
    dm, da = definition_utile(meilleure), definition_utile(autre)
    if da < TOLERANCE_DEFINITION * dm:
        # Même nombre de pixels (ou plus) mais une partie n'est qu'un cadre ajouté autour de la photo.
        return 'cadre' if _pixels(autre) >= TOLERANCE_DEFINITION * _pixels(meilleure) else 'definition'
    if autre['copie'] and not meilleure['copie']:
        return 'copie'
    if meilleure['date_prise'] and not autre['date_prise']:
        return 'exif'
    if rang_format(meilleure.get('chemin')) < rang_format(autre.get('chemin')):
        return 'format'
    if _pixels(meilleure) > _pixels(autre):
        return 'definition'
    if meilleure['taille'] > autre['taille']:
        return 'taille'
    if (meilleure['date_prise'] or '~') < (autre['date_prise'] or '~'):
        return 'date'
    return 'egal'


def _marquer_cadres(photos, paires, mesures):
    """Photo entourée d'un cadre ou d'une bordure (retouchée : l'autre photo tient dans une partie seulement) : sa
    définition utile est celle de la partie couverte (largeur × hauteur × couverture). Seulement quand elle n'a pas de
    date de prise de vue et que la photo contenue en a une : un recadrage (l'original contient le recadrage, et c'est
    lui qui a sa date) ne fait jamais perdre sa définition à l'original."""
    for (a, b, _, _), m in zip(paires, mesures):
        if not isinstance(m, dict) or not est_retouchee(m) or m.get('couverture') is None:
            continue
        contenante, contenue = (a, b) if m.get('sens') == 0 else (b, a)
        pc, pd = photos.get(contenante), photos.get(contenue)
        if pc is None or pd is None or pc['date_prise'] or not pd['date_prise']:
            continue
        utile = _pixels(pc) * float(m['couverture'])
        if utile < TOLERANCE_DEFINITION * _pixels(pc):
            pc['utile'] = min(pc.get('utile') or utile, utile)


def _definition(p):
    return f"{p['largeur']} × {p['hauteur']}" if p['largeur'] and p['hauteur'] else 'définition inconnue'


def _raison_meilleure(type_, critere, p):
    if type_ == 'proches':
        return {'definition': f'La plus grande de la série ({_definition(p)}).',
                'copie': 'La meilleure de la série : les autres semblent être des copies.',
                'taille': 'La meilleure de la série : le fichier le plus détaillé.',
                'date': 'La meilleure de la série : la première prise.'}.get(critere, 'La meilleure de la série.')
    return {'definition': f'À garder : la plus grande ({_definition(p)}).',
            'copie': "À garder : l'original, les autres semblent être des copies.",
            'cadre': "À garder : l'originale, sans cadre ajouté autour.",
            'exif': "À garder : l'originale, avec sa date de prise de vue.",
            'format': "À garder : l'originale, dans le format de l'appareil photo.",
            'taille': 'À garder : le fichier le plus détaillé.',
            'date': 'À garder : la plus ancienne.'}.get(critere, 'À garder : trouvée en premier.')


def _raison_autre(type_, critere, p, meilleure):
    if type_ == 'proches':
        return 'Prise au même moment, ou presque.'
    if type_ == 'identiques':
        return 'Copie exacte de la photo à garder.'
    if critere == 'definition':
        return f'Plus petite : {_definition(p)} au lieu de {_definition(meilleure)}.'
    if critere == 'copie':
        return f'Même photo, qui semble être une copie (« {p["copie"]} »).'
    if critere == 'cadre':
        return 'Même photo, entourée d’un cadre ou d’une bordure ajoutés après coup.'
    if critere == 'exif':
        return 'Même photo, sans date de prise de vue : sans doute une copie convertie ou retouchée.'
    if critere == 'format':
        nom = NOMS_FORMATS.get(os.path.splitext(str(p.get('chemin') or ''))[1].lower())
        return f'Même photo, convertie en {nom}.' if nom else 'Même photo, convertie dans un autre format.'
    if critere == 'taille':
        return (f'Même photo, fichier plus léger ({octets_lisibles(p["taille"])} au lieu de '
                f'{octets_lisibles(meilleure["taille"])}).')
    if critere == 'date':
        return 'Même photo, enregistrée plus tard.'
    return 'Même photo.'


def former_groupe(type_, photos, similarite=None):
    """Groupe prêt à afficher : photos de la meilleure à la moins bonne, avec garder, meilleure, critere et raison.
    Pour « proches », toutes sont gardées (rien n'est coché d'office)."""
    plafond = max((definition_utile(p) for p in photos), default=0)
    rangees = sorted(photos, key=lambda p: _cle_qualite(p, plafond))
    best = rangees[0]
    critere0 = _critere(best, rangees[1]) if len(rangees) > 1 else 'egal'
    sortie = []
    for i, p in enumerate(rangees):
        if i == 0:
            garder, critere, raison = True, critere0, _raison_meilleure(type_, critere0, p)
        else:
            critere = _critere(best, p)
            garder = type_ == 'proches'
            raison = _raison_autre(type_, critere, p, best)
        sortie.append({'id': p['id'], 'garder': garder, 'meilleure': i == 0, 'critere': critere, 'raison': raison,
                       'nom': p['nom'], 'dossier': p['dossier'], 'taille': p['taille'], 'largeur': p['largeur'],
                       'hauteur': p['hauteur'], 'date': p['date_prise'] or p['date_fichier']})
    gain = sum(p['taille'] for p in sortie if not p['garder'])
    g = {'id': f'{type_[0]}{min(p["id"] for p in photos)}', 'type': type_, 'photos': sortie, 'gain_octets': gain}
    if type_ == 'proches':
        g['gain_possible_octets'] = sum(p['taille'] for p in sortie[1:])
    if similarite is not None:
        g['similarite'] = round(float(similarite), 4)
    return g


# ---------------------------------------------------------------- regroupements

class _Ensembles:
    """Union-find (ensembles disjoints)."""

    def __init__(self):
        self.parent = {}

    def trouver(self, x):
        racine = x
        while self.parent.get(racine, racine) != racine:
            racine = self.parent[racine]
        while x != racine:
            x, self.parent[x] = self.parent.get(x, x), racine
        return racine

    def unir(self, a, b):
        ra, rb = self.trouver(a), self.trouver(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)
            return True
        return False

    def groupes(self, elements):
        g = {}
        for e in elements:
            g.setdefault(self.trouver(e), []).append(e)
        return [v for v in g.values() if len(v) > 1]


_SAUTEE = object()                   # paire non comparée : ses deux photos étaient déjà réunies comme retouches


def _meilleures(a, b, score, k):
    """Masque des paires (a[i], b[i]) qui sont parmi les k meilleures (score décroissant) de l'une de leurs photos."""
    n = len(a)
    bout = np.concatenate([a, b])
    note = np.concatenate([score, score])
    ordre = np.lexsort((-note, bout))                   # par photo, puis du meilleur score au moins bon
    trie = bout[ordre]
    debuts = np.flatnonzero(np.r_[True, trie[1:] != trie[:-1]])
    rang = np.arange(len(trie)) - np.repeat(debuts, np.diff(np.r_[debuts, len(trie)]))
    garde = np.zeros(n, dtype=bool)
    garde[(ordre % n)[rang < k]] = True
    return garde


def _series(voisins, ordre):
    """Groupes « proches » sans chaîne : la meilleure photo restante prend ses voisines directes encore libres."""
    pris = set()
    series = []
    for x in ordre:
        if x in pris or x not in voisins:
            continue
        libres = [y for y in voisins[x] if y not in pris]
        if libres:
            pris.add(x)
            pris.update(libres)
            series.append([x] + sorted(libres))
    return series


# ---------------------------------------------------------------- moteur des doublons

class _Connexion:
    __slots__ = ('c',)

    def __init__(self, c):
        self.c = c

    def __del__(self):
        try:
            self.c.close()
        except Exception:
            pass


class Doublons:
    """Recherche des doublons parmi les photos de la base.

    base : base.Base ; cle_modele : moteur dont on compare les vecteurs (le même que la recherche). Sûr depuis
    plusieurs fils : une connexion SQLite par fil ; un seul calcul à la fois (un second appel attend le premier).

    Utilisation directe : calculer(progression, arret) -> résultat (voir ce nom). En arrière-plan : demarrer(),
    etat(), resultat(), annuler(), attendre().
    """

    def __init__(self, base, cle_modele):
        self.base = base
        self.cle_modele = cle_modele
        self._local = threading.local()
        self._connexions = []
        self._verrou_connexions = threading.Lock()
        self._verrou = threading.Lock()           # état
        self._un_calcul = threading.Lock()
        self._fil = None
        self._arret = threading.Event()
        self._resultat = None
        self._etat = {'phase': 'attente', 'fait': 0, 'total': 0, 'debut': None, 'duree_s': None, 'erreur': None}
        self._t0 = None
        self._rappel = None
        self._rappel_t = 0.0
        self._arret_calcul = None
        self._fermee = False
        self._creer_tables()

    # ------------------------------------------------ connexions

    def _cnx(self):
        h = getattr(self._local, 'h', None)
        if h is not None:
            return h.c
        if self._fermee:
            raise RuntimeError('doublons : fermé')
        c = sqlite3.connect(self.base.chemin_index, timeout=30, isolation_level=None, check_same_thread=False)
        try:
            c.execute('PRAGMA synchronous = NORMAL')
            c.execute('PRAGMA temp_store = MEMORY')
        except BaseException:
            c.close()
            raise
        h = _Connexion(c)
        self._local.h = h
        with self._verrou_connexions:
            self._connexions = [(f, x) for f, x in self._connexions if f.is_alive()]
            self._connexions.append((threading.current_thread(), h))
        return c

    def _ecrire(self, sql, lignes=None):
        """Écriture groupée dans une transaction courte (les lectures de la base ne sont pas bloquées : WAL)."""
        c = self._cnx()
        c.execute('BEGIN IMMEDIATE')
        try:
            if lignes is None:
                c.execute(sql)
            else:
                c.executemany(sql, lignes)
        except BaseException:
            c.execute('ROLLBACK')
            raise
        c.execute('COMMIT')

    def _creer_tables(self):
        c = self._cnx()
        c.execute('BEGIN IMMEDIATE')
        try:
            for sql in _SCHEMA:
                c.execute(sql)
            v = c.execute("SELECT valeur FROM doublons_meta WHERE cle = 'version'").fetchone()
            if v is None or v[0] != VERSION:
                c.execute('DELETE FROM doublons_empreintes')
                c.execute('DELETE FROM doublons_paires')
                c.execute("INSERT INTO doublons_meta(cle, valeur) VALUES ('version', ?) "
                          'ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur', (VERSION,))
        except BaseException:
            c.execute('ROLLBACK')
            raise
        c.execute('COMMIT')

    def fermer(self):
        """Ferme les connexions (à l'arrêt du programme, après annuler() et attendre())."""
        self._fermee = True
        with self._verrou_connexions:
            liste, self._connexions = self._connexions, []
        for _, h in liste:
            try:
                h.c.close()
            except Exception:
                pass
        self._local = threading.local()

    def oublier(self):
        """Efface empreintes et vérifications gardées (tout sera recalculé). Les photos ne sont pas touchées."""
        with self._un_calcul:
            self._ecrire('DELETE FROM doublons_empreintes')
            self._ecrire('DELETE FROM doublons_paires')
            with self._verrou:
                self._resultat = None

    # ------------------------------------------------ arrière-plan

    def demarrer(self):
        """Lance calculer() dans un fil d'arrière-plan. Sans effet s'il tourne déjà. Renvoie le fil."""
        with self._verrou:
            if self._fil is not None and self._fil.is_alive():
                return self._fil
            self._arret.clear()
            self._etat.update(phase='empreintes', fait=0, total=0, debut=maintenant(), duree_s=None, erreur=None)
            self._fil = threading.Thread(target=self._executer, name='myphotos-doublons', daemon=True)
            self._fil.start()
            return self._fil

    def _executer(self):
        try:
            self.calculer(arret=self._arret)
        except Annule:
            pass
        except Exception:
            journal.exception('doublons : échec du calcul')

    def annuler(self):
        """Demande l'arrêt du calcul lancé par demarrer() (il s'arrête en moins d'une seconde ou deux)."""
        self._arret.set()

    def attendre(self, timeout=None):
        """Attend la fin du calcul lancé par demarrer(). Vrai s'il est fini (ou s'il n'y en a pas)."""
        fil = self._fil
        if fil is None:
            return True
        fil.join(timeout)
        return not fil.is_alive()

    @property
    def en_cours(self):
        return self._un_calcul.locked()

    def resultat(self):
        """Dernier résultat complet de calculer(), ou None."""
        with self._verrou:
            return self._resultat

    def etat(self):
        """{phase, en_cours, fait, total, avancement (0-1), message, debut, duree_s, erreur, dernier}.

        phase : une de PHASES ; fait / total : avancée de l'étape en cours ; dernier : résumé du dernier résultat
        ({quand, duree_s, groupes: {identiques, retouchees, proches}, photos_en_trop, total_gain_octets}) ou None.
        """
        with self._verrou:
            e = dict(self._etat)
            r = self._resultat
            t0 = self._t0
        e['en_cours'] = self.en_cours or e['phase'] in _POIDS
        if e['en_cours'] and t0 is not None:
            e['duree_s'] = round(time.perf_counter() - t0, 1)
        e['message'] = _MESSAGES.get(e['phase'], '')
        if e['phase'] == 'erreur' and e.get('erreur'):
            e['message'] = f"{_MESSAGES['erreur']} ({e['erreur']})"
        av = 0.0
        if e['phase'] in _POIDS:
            for ph in PHASES[1:6]:
                if ph == e['phase']:
                    av += _POIDS[ph] * (e['fait'] / e['total'] if e['total'] else 0.0)
                    break
                av += _POIDS[ph]
        elif e['phase'] == 'termine':
            av = 1.0
        e['avancement'] = round(min(1.0, av), 3)
        e['dernier'] = None if r is None else {
            'quand': r['quand'], 'duree_s': r['duree_s'], 'groupes': dict(r['comptes']['groupes']),
            'photos_en_trop': r['comptes']['photos_en_trop'], 'total_gain_octets': r['total_gain_octets']}
        return e

    def _phase(self, phase, fait=0, total=0):
        with self._verrou:
            self._etat.update(phase=phase, fait=fait, total=total)
        if self._rappel is not None:
            try:
                self._rappel(phase, fait, total)
            except Exception:
                journal.exception('doublons : rappel de progression')

    def _avancer(self, fait, total=None):
        with self._verrou:
            self._etat['fait'] = fait
            if total is not None:
                self._etat['total'] = total
            phase = self._etat['phase']
            total = self._etat['total']
        t = time.monotonic()
        if self._rappel is not None and (t - self._rappel_t >= 0.2 or fait >= total):
            self._rappel_t = t
            try:
                self._rappel(phase, fait, total)
            except Exception:
                journal.exception('doublons : rappel de progression')

    def _verifier_arret(self):
        if self._arret_calcul is not None and self._arret_calcul.is_set():
            raise Annule('recherche des doublons interrompue')

    # ------------------------------------------------ calcul

    def calculer(self, progression=None, arret=None):
        """Cherche les doublons. Peut durer : à lancer dans un fil (ou via demarrer()).

        progression(phase, fait, total) : appelée à chaque étape et au plus 5 fois par seconde ; arret
        (threading.Event) : interrompt le calcul (lève Annule, ce qui est déjà calculé est gardé pour la suite).

        Renvoie {groupes, total_gain_octets, duree_s, quand, modele, comptes} :
        - groupes : [{id, type ('identiques' | 'retouchees' | 'proches'), photos, gain_octets, similarite?,
          gain_possible_octets (proches seulement)}], photos de la meilleure à la moins bonne :
          {id, garder, meilleure, critere, raison, nom, dossier, taille, largeur, hauteur, date}.
          garder=False : proposée à la suppression (jamais dans « proches ») ; critere : ce qui la départage de la
          meilleure ('definition', 'cadre', 'copie', 'exif', 'format', 'taille', 'date', 'egal') ; raison : phrase à
          afficher ;
        - total_gain_octets : place libérée en supprimant toutes les photos proposées ;
        - comptes : {photos (examinées), sans_miniature, sans_vecteur, unies, illisibles, groupes: {par type},
          photos_en_trop, paires_verifiees, paires_calculees}.
        Une photo proposée à la suppression n'apparaît que dans un seul groupe ; une photo gardée peut apparaître
        dans plusieurs (l'original de ses copies exactes, puis de ses retouches, puis d'une série).
        """
        with self._un_calcul:
            self._rappel = progression
            self._rappel_t = 0.0
            self._arret_calcul = arret
            with self._verrou:
                self._t0 = time.perf_counter()
                self._etat.update(debut=maintenant(), duree_s=None, erreur=None)
            try:
                res = self._calculer()
            except Annule:
                with self._verrou:
                    self._etat.update(phase='annule', duree_s=round(time.perf_counter() - self._t0, 1))
                raise
            except Exception as e:
                with self._verrou:
                    self._etat.update(phase='erreur', erreur=str(e) or e.__class__.__name__,
                                      duree_s=round(time.perf_counter() - self._t0, 1))
                raise
            finally:
                self._rappel = None
                self._arret_calcul = None
            with self._verrou:
                self._resultat = res
                self._etat.update(phase='termine', fait=0, total=0, duree_s=res['duree_s'])
            if progression is not None:
                try:
                    progression('termine', 0, 0)
                except Exception:
                    journal.exception('doublons : rappel de progression')
            return res

    def _calculer(self):
        t0 = time.perf_counter()
        comptes = {'photos': 0, 'sans_miniature': 0, 'sans_vecteur': 0, 'unies': 0, 'illisibles': 0,
                   'paires_verifiees': 0, 'paires_calculees': 0, 'paires_directes': 0}
        self._phase('empreintes')
        photos = self._lire_photos()
        comptes['photos'] = len(photos)
        self._nettoyer()
        empreintes = self._empreintes(photos, comptes)
        self._verifier_arret()

        # 1. Identiques.
        self._phase('identiques')
        identiques = self._identiques(photos, empreintes, comptes)
        ecartees = set()
        groupes = []
        for membres in identiques:
            g = former_groupe('identiques', [photos[i] for i in membres])
            groupes.append(g)
            ecartees.update(p['id'] for p in g['photos'] if not p['garder'])

        # 2. Paires à examiner parmi les photos restantes qui ont une miniature assez contrastée.
        self._phase('comparaison')
        restantes = [i for i, e in empreintes.items()
                     if i not in ecartees and e['phash'] is not None and i in photos]
        comptes['sans_miniature'] = sum(1 for i in photos if i not in empreintes or empreintes[i]['phash'] is None)
        unies = {i for i in restantes if (empreintes[i]['contraste'] or 0.0) < CONTRASTE_MIN}
        comptes['unies'] = len(unies)
        restantes = [i for i in restantes if i not in unies]
        a_verifier, directes = self._candidates(restantes, empreintes, photos, comptes)
        comptes['paires_directes'] = len(directes)
        self._verifier_arret()

        # 3. Vérification sur les miniatures (résultats gardés d'une fois sur l'autre) ; les retouchées se réunissent
        # au fur et à mesure (deux photos déjà réunies ne sont pas comparées).
        self._phase('verification', 0, len(a_verifier))
        mesures, retouches = self._verifications(a_verifier, comptes)
        self._verifier_arret()
        _marquer_cadres(photos, a_verifier, mesures)      # cadre ou bordure ajoutés : définition utile réduite

        # 4. Regroupement.
        self._phase('regroupement')
        cos_proche = COSINUS_PROCHE.get(self.cle_modele, COSINUS_PROCHE_REPLI)
        proches = []
        for (a, b, _, cs), m in zip(a_verifier, mesures):
            if m is _SAUTEE or est_retouchee(m):
                continue
            if superposables(m) or (cs is not None and cs >= cos_proche):
                ta, tb = _secondes(photos[a]['date_prise']), _secondes(photos[b]['date_prise'])
                if ta is None or tb is None or abs(ta - tb) <= FENETRE_PROCHES_S:
                    proches.append((a, b))
        proches.extend((a, b) for a, b, _, _ in directes)
        rep = {}
        for membres in retouches.groupes(restantes):
            g = former_groupe('retouchees', [photos[i] for i in membres])
            groupes.append(g)
            garde = g['photos'][0]['id']
            for p in g['photos']:
                rep[p['id']] = garde
        voisins = {}
        for a, b in proches:
            a, b = rep.get(a, a), rep.get(b, b)
            if a != b:
                voisins.setdefault(a, set()).add(b)
                voisins.setdefault(b, set()).add(a)
        ordre = sorted(voisins, key=lambda i: _cle_qualite(photos[i]))
        for serie in _series(voisins, ordre):
            groupes.append(former_groupe('proches', [photos[i] for i in serie]))

        rang = {t: k for k, t in enumerate(TYPES)}
        groupes.sort(key=lambda g: (rang[g['type']], -g['gain_octets'],
                                    -len(g['photos']), g['photos'][0]['date'] or '', g['id']))
        par_type = {t: sum(1 for g in groupes if g['type'] == t) for t in TYPES}
        comptes['groupes'] = par_type
        comptes['photos_en_trop'] = sum(1 for g in groupes for p in g['photos'] if not p['garder'])
        total = sum(g['gain_octets'] for g in groupes)
        return {'groupes': groupes, 'total_gain_octets': total, 'duree_s': round(time.perf_counter() - t0, 2),
                'quand': maintenant(), 'modele': self.cle_modele, 'comptes': comptes}

    # ------------------------------------------------ étapes

    def _lire_photos(self):
        """Photos examinées : lisibles (sans erreur), dans un dossier joignable. {id: fiche}."""
        lignes = self._cnx().execute(
            'SELECT p.id, p.chemin, p.taille, p.modifie, p.largeur, p.hauteur, p.date_prise, p.date_fichier, '
            'p.modele IS NOT NULL, p.miniature, d.chemin FROM photos p JOIN dossiers d ON d.id = p.dossier_id '
            'WHERE p.erreur IS NULL AND d.disponible = 1').fetchall()
        photos = {}
        for pid, ch, t, mo, w, h, dp, df, analysee, mini, dossier in lignes:
            photos[pid] = {'id': pid, 'chemin': ch, 'nom': os.path.basename(ch), 'dossier': dossier, 'taille': t,
                           'modifie': mo,
                           'largeur': w, 'hauteur': h, 'date_prise': dp, 'date_fichier': df,
                           'analysee': bool(analysee), 'miniature': bool(mini), 'copie': motif_copie(ch)}
        return photos

    def _nettoyer(self):
        """Oublie empreintes et vérifications des photos disparues de la base."""
        self._ecrire('DELETE FROM doublons_empreintes WHERE photo_id NOT IN (SELECT id FROM photos)')
        self._ecrire('DELETE FROM doublons_paires WHERE a NOT IN (SELECT id FROM photos) '
                     'OR b NOT IN (SELECT id FROM photos)')

    def _empreintes(self, photos, comptes):
        """Empreintes à jour de toutes les photos examinées : {id: {phash, contraste, sha256}}. Celles qui manquent
        ou sont périmées sont calculées sur les miniatures (plusieurs fils) et enregistrées."""
        connues = {}
        for pid, t, mo, ph, co, prov, sha in self._cnx().execute(
                'SELECT photo_id, taille, modifie, phash, contraste, provisoire, sha256 FROM doublons_empreintes'):
            connues[pid] = (t, mo, ph, co, prov, sha)
        resultat = {}
        a_faire = []
        perimees = []
        for pid, p in photos.items():
            e = connues.get(pid)
            if e is not None and (e[0], e[1]) != (p['taille'], p['modifie']):
                perimees.append(pid)               # fichier changé : empreinte et SHA à refaire
                e = None
            if e is not None and not p['miniature']:
                resultat[pid] = {'phash': None, 'contraste': None, 'sha256': e[5]}   # miniature retirée entre-temps
                continue
            if e is not None and (e[2] is None or (e[4] and p['analysee'])):
                a_faire.append(pid)                # miniature apparue, ou refaite par l'analyse
                resultat[pid] = {'phash': None, 'contraste': None, 'sha256': e[5]}
                continue
            if e is None:
                resultat[pid] = {'phash': None, 'contraste': None, 'sha256': None}
                if p['miniature']:
                    a_faire.append(pid)
                continue
            resultat[pid] = {'phash': e[2], 'contraste': e[3], 'sha256': e[5]}
        if perimees:
            for i in range(0, len(perimees), LOT_ECRITURE):
                lot = perimees[i:i + LOT_ECRITURE]
                marques = ','.join('?' * len(lot))
                c = self._cnx()
                c.execute('BEGIN IMMEDIATE')
                try:
                    c.execute(f'DELETE FROM doublons_empreintes WHERE photo_id IN ({marques})', lot)
                    c.execute(f'DELETE FROM doublons_paires WHERE a IN ({marques}) OR b IN ({marques})', lot + lot)
                except BaseException:
                    c.execute('ROLLBACK')
                    raise
                c.execute('COMMIT')
        self._phase('empreintes', 0, len(a_faire))
        if a_faire:
            self._calculer_empreintes(a_faire, photos, resultat, comptes)
        return resultat

    def _calculer_empreintes(self, ids, photos, resultat, comptes):
        base = self.base

        def une(pid):
            try:
                jpeg = base.miniature(pid)
                return pid, (empreinte(jpeg) if jpeg else None)
            except RuntimeError:
                raise                               # base fermée
            except Exception:
                return pid, None

        fait = 0
        lot = []
        refaites = []
        with ThreadPoolExecutor(FILS_EMPREINTES, thread_name_prefix='myphotos-empreintes') as pool:
            for debut in range(0, len(ids), LOT_ECRITURE):
                self._verifier_arret()
                for pid, r in pool.map(une, ids[debut:debut + LOT_ECRITURE]):
                    p = photos[pid]
                    e = resultat[pid]
                    if r is None:
                        comptes['illisibles'] += 1
                        e['phash'] = e['contraste'] = None
                    else:
                        e['phash'], e['contraste'] = r
                    lot.append((pid, p['taille'], p['modifie'], e['phash'], e['contraste'],
                                0 if p['analysee'] else 1, e['sha256']))
                    refaites.append(pid)
                fait += len(ids[debut:debut + LOT_ECRITURE])
                self._enregistrer_empreintes(lot, refaites)
                lot, refaites = [], []
                self._avancer(fait)

    def _enregistrer_empreintes(self, lot, refaites):
        if not lot:
            return
        c = self._cnx()
        c.execute('BEGIN IMMEDIATE')
        try:
            c.executemany('INSERT OR REPLACE INTO doublons_empreintes(photo_id, taille, modifie, phash, contraste, '
                          'provisoire, sha256) VALUES (?, ?, ?, ?, ?, ?, ?)', lot)
            for i in range(0, len(refaites), LOT_ECRITURE):
                part = refaites[i:i + LOT_ECRITURE]
                marques = ','.join('?' * len(part))
                c.execute(f'DELETE FROM doublons_paires WHERE a IN ({marques}) OR b IN ({marques})', part + part)
        except BaseException:
            c.execute('ROLLBACK')
            raise
        c.execute('COMMIT')

    def _identiques(self, photos, empreintes, comptes):
        """Groupes de fichiers identiques (listes d'identifiants) : même taille, miniatures voisines (si connues),
        puis même SHA-256 (calculé au besoin, gardé)."""
        par_taille = {}
        for pid, p in photos.items():
            par_taille.setdefault(p['taille'], []).append(pid)
        candidats = []
        for t, ids in par_taille.items():
            if len(ids) < 2 or t <= 0:
                continue
            for i, a in enumerate(ids):
                ha = empreintes[a]['phash']
                for b in ids[i + 1:]:
                    hb = empreintes[b]['phash']
                    if ha is None or hb is None or distance(ha, hb) <= HAMMING_IDENTIQUE:
                        candidats.append(a)
                        candidats.append(b)
        candidats = list(dict.fromkeys(candidats))
        a_lire = [i for i in candidats if empreintes[i]['sha256'] is None]
        self._phase('identiques', 0, len(a_lire))
        lus = []
        for k, pid in enumerate(a_lire):
            if k % 8 == 0:
                self._verifier_arret()
            sha = self._sha256(photos[pid]['chemin'])
            if sha is None:
                comptes['illisibles'] += 1
                continue
            empreintes[pid]['sha256'] = sha
            lus.append((sha, pid, photos[pid]['taille'], photos[pid]['modifie']))
            if len(lus) >= 50:
                self._enregistrer_sha(lus)
                lus = []
            self._avancer(k + 1)
        self._enregistrer_sha(lus)
        par_sha = {}
        for pid in candidats:
            sha = empreintes[pid]['sha256']
            if sha is not None:
                par_sha.setdefault((photos[pid]['taille'], sha), []).append(pid)
        return [sorted(v) for v in par_sha.values() if len(v) > 1]

    def _sha256(self, chemin):
        h = hashlib.sha256()
        try:
            with open(chemin_systeme(chemin), 'rb') as f:
                while True:
                    bloc = f.read(1 << 20)
                    if not bloc:
                        break
                    h.update(bloc)
        except OSError:
            return None
        return h.digest()

    def _enregistrer_sha(self, lus):
        if not lus:
            return
        c = self._cnx()
        c.execute('BEGIN IMMEDIATE')
        try:
            for sha, pid, t, mo in lus:
                r = c.execute('UPDATE doublons_empreintes SET sha256 = ? WHERE photo_id = ? AND taille = ? '
                              'AND modifie = ?', (sha, pid, t, mo))
                if not r.rowcount:
                    c.execute('INSERT OR IGNORE INTO doublons_empreintes(photo_id, taille, modifie, provisoire, '
                              'sha256) VALUES (?, ?, ?, 1, ?)', (pid, t, mo, sha))
        except BaseException:
            c.execute('ROLLBACK')
            raise
        c.execute('COMMIT')

    def _candidates(self, ids, empreintes, photos, comptes):
        """Paires à examiner parmi `ids` (photos qui ont une empreinte assez contrastée).

        Renvoie (a_verifier, directes), listes de (a, b, hamming, cosinus ou None) avec a < b :
        - a_verifier : empreintes à au plus HAMMING_CANDIDAT bits (quasi identiques, toujours) ; ou vecteurs voisins
          (COSINUS_CANDIDAT) avec des empreintes à au plus HAMMING_RETOUCHE_MAX bits et des dates compatibles, au plus
          VOISINES_MAX par photo (les plus ressemblantes). Les quasi identiques d'abord ;
        - directes : « proches » par les vecteurs seuls (COSINUS_PROCHE, dates compatibles), sans vérification.
        Tout est fait par blocs, en tableaux : la place prise ne dépend pas du nombre de paires voisines.
        """
        ids = np.array(sorted(ids), dtype=np.int64)
        n = len(ids)
        liste = ids.tolist()
        vid, mat, _ = self.base.vecteurs(self.cle_modele)
        self._verifier_arret()
        if n < 2:
            comptes['sans_vecteur'] = n - int(np.isin(vid, ids).sum()) if n else 0
            return [], []
        h = np.array([empreintes[i]['phash'] for i in liste], dtype=np.int64).view(np.uint64)
        dates = np.array([_secondes(photos[i]['date_prise']) for i in liste], dtype=np.float64)
        # Vecteurs rangés comme ids : pos[k] = rang dans ids de la k-ième ligne de mat ; ligne[i] = l'inverse (-1 : aucun).
        pos = np.searchsorted(ids, vid)
        dedans = (pos < n) & (ids[np.minimum(pos, n - 1)] == vid) if len(vid) else np.zeros(0, bool)
        if not dedans.all():                       # (copie évitée dans le cas courant : toutes dedans)
            mat = mat[dedans]
            pos = pos[dedans]
        ligne = np.full(n, -1, np.int64)
        ligne[pos] = np.arange(len(pos))
        nv = len(pos)
        comptes['sans_vecteur'] = n - nv
        c_cand = COSINUS_CANDIDAT.get(self.cle_modele, COSINUS_CANDIDAT_REPLI)
        c_proche = COSINUS_PROCHE.get(self.cle_modele, COSINUS_PROCHE_REPLI)

        def compatibles(a, b):
            da, db = dates[a], dates[b]
            return np.isnan(da) | np.isnan(db) | (np.abs(da - db) <= FENETRE_PROCHES_S)

        blocs_v = [(i, j) for i in range(0, nv, BLOC_VECTEURS)
                   for j in range(i - i % BLOC_COLONNES, nv, BLOC_COLONNES)] if nv > 1 else []
        blocs_h = [(i, j) for i in range(0, n, BLOC_EMPREINTES) for j in range(i - i % BLOC_COLONNES, n, BLOC_COLONNES)]
        self._phase('comparaison', 0, len(blocs_v) + len(blocs_h))
        fait = 0
        pa, pb = [], []
        for i, j in blocs_v:
            self._verifier_arret()
            s = mat[i:i + BLOC_VECTEURS] @ mat[j:j + BLOC_COLONNES].T
            ii, jj = np.nonzero(s >= c_cand)
            garde = ii + i < jj + j
            ii, jj = ii[garde], jj[garde]
            a, b = pos[ii + i], pos[jj + j]
            ok = compatibles(a, b) & ((np.bitwise_count(h[a] ^ h[b]) <= HAMMING_RETOUCHE_MAX) | (s[ii, jj] >= c_proche))
            pa.append(a[ok])
            pb.append(b[ok])
            fait += 1
            self._avancer(fait)
        # Empreintes : chaque bit devient +1 ou -1 ; le produit de deux empreintes vaut alors 64 - 2 × leur distance de
        # Hamming, exactement. Un produit de matrices (comme pour les vecteurs) va bien plus vite que bit à bit.
        bits = np.unpackbits(h.view(np.uint8).reshape(n, 8), axis=1, bitorder='little').astype(np.float32) * 2 - 1
        seuil_h = 64 - 2 * HAMMING_CANDIDAT - 0.5
        for i, j in blocs_h:
            self._verifier_arret()
            s = bits[i:i + BLOC_EMPREINTES] @ bits[j:j + BLOC_COLONNES].T
            ii, jj = np.nonzero(s >= seuil_h)
            garde = ii + i < jj + j
            pa.append(ii[garde] + i)
            pb.append(jj[garde] + j)
            fait += 1
            self._avancer(fait)
        if not pa:
            return [], []
        a, b = np.concatenate(pa).astype(np.int64), np.concatenate(pb).astype(np.int64)
        cle = np.unique(np.minimum(a, b) * n + np.maximum(a, b))
        a, b = cle // n, cle % n
        ham = np.bitwise_count(h[a] ^ h[b]).astype(np.int64)
        cos = np.full(len(a), np.nan, np.float32)
        avec = np.flatnonzero((ligne[a] >= 0) & (ligne[b] >= 0))
        for d in range(0, len(avec), 65536):
            k = avec[d:d + 65536]
            cos[k] = np.einsum('ij,ij->i', mat[ligne[a[k]]], mat[ligne[b[k]]])
        temps = compatibles(a, b)
        quasi = ham <= HAMMING_CANDIDAT
        with np.errstate(invalid='ignore'):
            voisines = (cos >= c_cand) & (ham <= HAMMING_RETOUCHE_MAX) & temps & ~quasi
            verif = quasi.copy()
            autres = np.flatnonzero(voisines)
            if len(autres):
                verif[autres[_meilleures(a[autres], b[autres], cos[autres], VOISINES_MAX)]] = True
            directes = ~verif & temps & (cos >= c_proche)

        def sortie(masque, ordre):
            k = ordre[masque[ordre]]
            return [(liste[x], liste[y], hh, None if cc != cc else cc)
                    for x, y, hh, cc in zip(a[k].tolist(), b[k].tolist(), ham[k].tolist(), cos[k].tolist())]
        ordre = np.lexsort((b, a, ~quasi))                # quasi identiques d'abord, puis par photo
        return sortie(verif, ordre), sortie(directes, ordre)

    def _verifications(self, paires, comptes):
        """Mesures de verifier() pour chaque paire, dans l'ordre : gardées en base, calculées si elles manquent. Les
        retouchées se réunissent au fur et à mesure ; deux photos déjà réunies ne sont pas comparées (_SAUTEE).
        Renvoie (mesures, ensembles des retouchées)."""
        c = self._cnx()
        connues = {}
        cles = sorted((a, b) for a, b, _, _ in paires)
        for i in range(0, len(cles), LOT_ECRITURE):
            lot = cles[i:i + LOT_ECRITURE]
            voulues = set(lot)
            for a, b, m in c.execute('SELECT a, b, mesure FROM doublons_paires WHERE a BETWEEN ? AND ?',
                                     (lot[0][0], lot[-1][0])):
                if (a, b) in voulues:
                    connues[(a, b)] = m
        pyramides = OrderedDict()

        def pyramide(pid):
            p = pyramides.get(pid)
            if p is None:
                jpeg = self.base.miniature(pid)
                try:
                    p = preparer_verification(jpeg) if jpeg else False
                except Exception:
                    p = False
                pyramides[pid] = p
                if len(pyramides) > 256:
                    pyramides.popitem(last=False)
            else:
                pyramides.move_to_end(pid)
            return p

        retouches = _Ensembles()
        mesures = []
        nouvelles = []
        calculees = 0
        for k, (a, b, _, _) in enumerate(paires):
            if k % 16 == 0:
                self._verifier_arret()
            if retouches.trouver(a) == retouches.trouver(b):
                mesures.append(_SAUTEE)
            else:
                brut = connues.get((a, b))
                if brut is not None:
                    m = json.loads(brut)
                else:
                    pa, pb = pyramide(a), pyramide(b)
                    m = verifier(pa, pb) if pa and pb else None
                    nouvelles.append((a, b, json.dumps(m, separators=(',', ':'))))
                    calculees += 1
                    if len(nouvelles) >= LOT_ECRITURE:
                        self._ecrire('INSERT OR REPLACE INTO doublons_paires(a, b, mesure) VALUES (?, ?, ?)', nouvelles)
                        nouvelles = []
                if est_retouchee(m):
                    retouches.unir(a, b)
                mesures.append(m)
            self._avancer(k + 1)
        if nouvelles:
            self._ecrire('INSERT OR REPLACE INTO doublons_paires(a, b, mesure) VALUES (?, ?, ?)', nouvelles)
        comptes['paires_verifiees'] = len(paires)
        comptes['paires_calculees'] = calculees
        return mesures, retouches
