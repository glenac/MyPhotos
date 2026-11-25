"""Réglage des seuils des doublons (myphotos/doublons.py) sur des images PUBLIQUES, jamais sur des photos personnelles.

    python -I outils/calibrer_doublons.py --images C:\\chemin\\images --travail C:\\chemin\\travail
    python -I outils/calibrer_doublons.py ... --images C:\\chemin\\images\\autres   plusieurs dossiers publics
    python -I outils/calibrer_doublons.py ... --materiel auto              moteur sur la carte graphique si possible
    python -I outils/calibrer_doublons.py ... --moteur rapide              un autre moteur (défaut : base)
    python -I outils/calibrer_doublons.py ... --reconstruire               refait les variantes et les index
    python -I outils/calibrer_doublons.py --travail ... --perf 10000,50000 durées sur des données synthétiques
    python -I outils/calibrer_doublons.py ... --markdown                   tableaux au format Markdown

Chaque image d'un dossier --images est une « origine » (deux fichiers qui portent le même titre dans un
manifeste.json ou un CREDITS.txt voisin, ou le même contenu, sont la même origine). La catégorie d'une image est son
sous-dossier (chien/03.jpg), sinon son nom sans numéro (chien-3.jpg) : deux photos différentes de la même catégorie
sont les cas négatifs les plus difficiles (deux chiens, deux plages).

L'outil fabrique dans --travail deux petites photothèques, indexées par le vrai moteur :
- « retouches » : chaque origine (avec une date EXIF, la même pour toutes : la date ne peut donc rien départager),
  sa copie exacte (une sur trois), et ses retouches : envoi WhatsApp (1600 px, qualité 70, sans EXIF),
  réduction de moitié, recompression (qualités 60 et 75), recadrages de 3 et 10 %, PNG, EXIF retiré (pixels
  identiques), bordure, luminosité +10 % et -10 %, rotation de 2° redressée ;
- « rafales » : par origine, trois cadres décalés (de 3 à 6 %) dont deux avec une autre exposition (± 12-15 %),
  plus deux cas plus durs : un sujet qui a bougé (un morceau de l'image déplacé) et un texte ajouté. Aucun ne doit
  être pris pour une retouche ; tous devraient être « proches ».
Puis il mesure chaque niveau : précision et rappel, paire par paire et groupe par groupe (calcul complet de
myphotos.doublons), et les marges de chaque seuil. Les nombres vont dans --travail/resultats_doublons.json.
"""
import argparse
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np                                     # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps   # noqa: E402

from myphotos import config, doublons, modele          # noqa: E402
from myphotos.base import Base                         # noqa: E402
from myphotos.indexation import Indexeur               # noqa: E402

DATE_EXIF = '2024:07:14 15:30:00'
RETOUCHES = ('whatsapp', 'reduite50', 'q60', 'q75', 'recadrage3', 'recadrage10', 'png', 'sans_exif', 'bordure',
             'lum+10', 'lum-10', 'rotation2')
RAFALES = ('cadre_a', 'cadre_b', 'cadre_c', 'mouvement', 'texte')
TEXTES = ('Joyeux anniversaire !', 'Vacances 2024', 'Bonne année', 'Souvenir de famille', 'Le grand jour')


# ---------------------------------------------------------------- sources

def _identites(dossier):
    """{chemin relatif en minuscules : titre} lus dans manifeste.json (dossier ou parent) et CREDITS.txt."""
    ident = {}
    for m in (dossier / 'manifeste.json', dossier.parent / 'manifeste.json'):
        if m.is_file():
            try:
                for e in json.loads(m.read_text(encoding='utf-8')):
                    if isinstance(e, dict) and e.get('fichier') and e.get('titre'):
                        ident[e['fichier'].replace('\\', '/').lower()] = e['titre']
            except (ValueError, OSError):
                pass
    c = dossier / 'CREDITS.txt'
    if c.is_file():
        for ligne in c.read_text(encoding='utf-8', errors='replace').splitlines():
            morceaux = ligne.split('\t')
            if len(morceaux) >= 2 and '.' in morceaux[0]:
                ident[morceaux[0].strip().lower()] = morceaux[1].strip()
    return {k: re.sub(r'[\s_]+', ' ', v.strip().lower()) for k, v in ident.items()}


def lister_sources(dossiers):
    sources = []
    for d in dossiers:
        d = Path(d)
        ident = _identites(d)
        for p in sorted(d.rglob('*')):
            if not p.is_file() or p.suffix.lower() not in config.EXTENSIONS:
                continue
            rel = p.relative_to(d).as_posix()
            cat = p.parent.name if p.parent != d else re.sub(r'[-_ ]?\d+$', '', p.stem)
            sources.append({'chemin': p, 'categorie': cat.lower(), 'identite': ident.get(rel.lower()),
                            'octets': p.read_bytes()})
    return sources


def regrouper_origines(sources):
    """Origines : listes de sources qui sont la même photo (même titre ou même contenu)."""
    import hashlib
    parent = list(range(len(sources)))

    def trouver(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    vus = {}
    for i, s in enumerate(sources):
        for cle in (('t', s['identite']), ('h', hashlib.sha256(s['octets']).hexdigest())):
            if cle[1] is None:
                continue
            if cle in vus:
                parent[trouver(i)] = trouver(vus[cle])
            else:
                vus[cle] = i
    groupes = {}
    for i in range(len(sources)):
        groupes.setdefault(trouver(i), []).append(sources[i])
    # La plus grande d'abord : c'est d'elle que partent les variantes.
    return [sorted(g, key=lambda s: -len(s['octets'])) for g in groupes.values()]


# ---------------------------------------------------------------- variantes

def _sans_app1_exif(octets):
    """JPEG sans ses segments EXIF (APP1 « Exif »), pixels intacts."""
    if octets[:2] != b'\xff\xd8':
        return octets
    sortie = bytearray(octets[:2])
    i = 2
    while i + 4 <= len(octets) and octets[i] == 0xFF:
        marqueur = octets[i + 1]
        if marqueur == 0xDA:                               # début des données : le reste tel quel
            break
        longueur = int.from_bytes(octets[i + 2:i + 4], 'big')
        segment = octets[i:i + 2 + longueur]
        if not (marqueur == 0xE1 and segment[4:10] == b'Exif\x00\x00'):
            sortie += segment
        i += 2 + longueur
    sortie += octets[i:]
    return bytes(sortie)


def _exif():
    e = Image.Exif()
    e[0x010F] = 'Essai'
    e.get_ifd(0x8769)[36867] = DATE_EXIF
    return e.tobytes()


def _avec_exif(octets):
    """JPEG avec notre EXIF (date commune) à la place du sien, pixels intacts."""
    nu = _sans_app1_exif(octets)
    donnees = _exif()
    return nu[:2] + b'\xff\xe1' + (len(donnees) + 2).to_bytes(2, 'big') + donnees + nu[2:]


def _jpeg(im, qualite, exif=False):
    t = io.BytesIO()
    im.save(t, 'JPEG', quality=qualite, exif=_exif() if exif else b'')
    return t.getvalue()


def _png(im):
    t = io.BytesIO()
    im.save(t, 'PNG')
    return t.getvalue()


def _recadrer(im, part, rnd):
    w, h = im.size
    gx, gy = rnd.uniform(0, 1), rnd.uniform(0, 1)
    dx, dy = round(w * part), round(h * part)
    return im.crop((round(dx * gx), round(dy * gy), w - (dx - round(dx * gx)), h - (dy - round(dy * gy))))


def _rotation(im, degres):
    """Rotation, puis le plus grand rectangle du même format sans coins vides (comme « redresser »)."""
    w, h = im.size
    r = im.rotate(degres, resample=Image.Resampling.BICUBIC, expand=False)
    a = abs(np.radians(degres))
    f = 1.0 / (np.cos(a) + np.sin(a) * max(w / h, h / w))
    cw, ch = round(w * f), round(h * f)
    return r.crop(((w - cw) // 2, (h - ch) // 2, (w - cw) // 2 + cw, (h - ch) // 2 + ch))


def retouches(src, rnd):
    """{nom: octets} des retouches d'une origine (src : octets JPEG ou autre)."""
    im = modele.vers_rvb(ImageOps.exif_transpose(Image.open(io.BytesIO(src))))
    w, h = im.size
    v = {}
    cote = max(w, h)
    cible = min(1600, round(cote * 0.75))
    r = cible / cote
    v['whatsapp'] = ('jpg', _jpeg(im.resize((round(w * r), round(h * r)), Image.Resampling.LANCZOS), 70))
    v['reduite50'] = ('jpg', _jpeg(im.resize((w // 2, h // 2), Image.Resampling.LANCZOS), 85))
    v['q60'] = ('jpg', _jpeg(im, 60))
    v['q75'] = ('jpg', _jpeg(im, 75, exif=True))
    v['recadrage3'] = ('jpg', _jpeg(_recadrer(im, 0.03, rnd), 90, exif=True))
    v['recadrage10'] = ('jpg', _jpeg(_recadrer(im, 0.10, rnd), 90, exif=True))
    v['png'] = ('png', _png(im))
    v['sans_exif'] = ('jpg', _sans_app1_exif(src)) if src[:2] == b'\xff\xd8' else ('jpg', _jpeg(im, 92))
    b = round(0.04 * cote)
    v['bordure'] = ('jpg', _jpeg(ImageOps.expand(im, border=b, fill=rnd.choice(((255, 255, 255), (0, 0, 0)))), 90,
                                 exif=True))
    v['lum+10'] = ('jpg', _jpeg(ImageEnhance.Brightness(im).enhance(1.10), 90, exif=True))
    v['lum-10'] = ('jpg', _jpeg(ImageEnhance.Brightness(im).enhance(0.90), 90, exif=True))
    v['rotation2'] = ('jpg', _jpeg(_rotation(im, rnd.choice((-2.0, 2.0))), 90, exif=True))
    return v


def _texture(a):
    return float(a.std())


def rafales(src, rnd):
    """{nom: octets} : trois cadres décalés d'une même scène (deux avec une autre exposition), un sujet qui a bougé,
    un texte ajouté."""
    im = modele.vers_rvb(ImageOps.exif_transpose(Image.open(io.BytesIO(src))))
    w, h = im.size
    horizontal = rnd.random() < 0.6

    def cadre(debut, fin):
        if horizontal:
            return im.crop((round(w * debut), round(h * 0.01), round(w * fin), round(h * 0.99)))
        return im.crop((round(w * 0.01), round(h * debut), round(w * 0.99), round(h * fin)))
    a = cadre(0.00, 0.92)
    v = {'cadre_a': ('jpg', _jpeg(a, 90)),
         'cadre_b': ('jpg', _jpeg(ImageEnhance.Brightness(cadre(0.06, 0.98)).enhance(rnd.uniform(1.12, 1.15)), 90)),
         'cadre_c': ('jpg', _jpeg(ImageEnhance.Brightness(cadre(0.03, 0.95)).enhance(rnd.uniform(0.86, 0.88)), 90))}
    # Sujet qui a bougé : le morceau le plus détaillé parmi quelques essais, déplacé de 5 % de la largeur.
    aw, ah = a.size
    bw, bh = round(aw * 0.22), round(ah * 0.28)
    gris = np.asarray(a.convert('L'), dtype=np.float32)
    meilleur = None
    for _ in range(12):
        x, y = rnd.randrange(aw // 10, aw - bw - aw // 10), rnd.randrange(ah // 10, ah - bh - ah // 10)
        t = _texture(gris[y:y + bh, x:x + bw])
        if meilleur is None or t > meilleur[0]:
            meilleur = (t, x, y)
    _, x, y = meilleur
    m = a.copy()
    m.paste(a.crop((x, y, x + bw, y + bh)), (x + round(aw * 0.05), y + round(ah * 0.02)))
    v['mouvement'] = ('jpg', _jpeg(m, 90))
    # Texte ajouté (photo légendée, mème) : deux photos différentes au sens du contenu.
    t = a.copy()
    d = ImageDraw.Draw(t)
    police = ImageFont.load_default(size=max(14, round(ah * 0.08)))
    texte = rnd.choice(TEXTES)
    tw = d.textlength(texte, font=police)
    d.text(((aw - tw) / 2, ah * 0.84), texte, font=police, fill=(255, 255, 255), stroke_width=max(1, ah // 120),
           stroke_fill=(0, 0, 0))
    v['texte'] = ('jpg', _jpeg(t, 90))
    return v


def fabriquer(origines, travail, graine=7):
    """Écrit les deux photothèques et leur vérité (verite.json). Ne refait rien si elle existe déjà."""
    verite_f = travail / 'verite.json'
    if verite_f.is_file():
        return json.loads(verite_f.read_text(encoding='utf-8'))
    rnd = random.Random(graine)
    verite = {'retouches': {}, 'rafales': {}}
    for k, origine in enumerate(origines):
        cat = re.sub(r'[^a-z0-9]+', '_', origine[0]['categorie']).strip('_') or 'divers'
        src = origine[0]['octets']
        if src[:2] == b'\xff\xd8':
            src = _avec_exif(src)
        nom = f'{k:04d}'
        # Retouches : l'origine (et ses autres fichiers), une copie exacte sur trois, les retouches.
        fichiers = {f'{cat}/{nom}.jpg': ('original', src)}
        for j, autre in enumerate(origine[1:]):
            fichiers[f'{cat}/{nom}_autre{j}.jpg'] = ('autre_fichier', autre['octets'])
        if k % 3 == 0:
            fichiers[f'{cat}/{nom} - Copie.jpg'] = ('copie', src)
        for v, (ext, octets) in retouches(src, rnd).items():
            dossier = 'WhatsApp Images' if v == 'whatsapp' else cat
            fichiers[f'{dossier}/{cat}_{nom}_{v}.{ext}'] = (v, octets)
        for rel, (v, octets) in fichiers.items():
            p = travail / 'retouches' / 'photos' / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(octets)
            verite['retouches'][rel.lower()] = {'origine': k, 'variante': v, 'categorie': cat}
        for v, (ext, octets) in rafales(src, rnd).items():
            rel = f'{cat}/{nom}_{v}.{ext}'
            p = travail / 'rafales' / 'photos' / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(octets)
            verite['rafales'][rel.lower()] = {'origine': k, 'variante': v, 'categorie': cat}
        if k % 50 == 0:
            print(f'  variantes : {k + 1} / {len(origines)} origines', flush=True)
    verite_f.write_text(json.dumps(verite, ensure_ascii=False), encoding='utf-8')
    return verite


TOURNEES = {'r90': (Image.Transpose.ROTATE_90,), 'r180': (Image.Transpose.ROTATE_180,),
            'r270': (Image.Transpose.ROTATE_270,), 'miroir': (Image.Transpose.FLIP_LEFT_RIGHT,),
            'miroir_r90': (Image.Transpose.TRANSPOSE,)}


def _symetrique(im, decalage=0.0):
    """La moitié gauche de l'image et son miroir (image symétrique), puis un cadre de 94 % de la largeur, décalé de
    `decalage` (part de la largeur) : deux cadres décalés de 3 % sont une rafale, pas une copie, même en miroir."""
    w, h = im.size
    g = im.crop((0, 0, w // 2, h))
    s = Image.new('RGB', (2 * (w // 2), h))
    s.paste(g, (0, 0))
    s.paste(g.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (w // 2, 0))
    cw = round(s.width * 0.94)
    x = round(s.width * decalage)
    return s.crop((x, 0, x + cw, h))


def _ciel(rnd, w=1200, h=800):
    """Ciel généré : dégradé vertical, nuages doux (bruit très flou), grain léger."""
    haut = np.array([rnd.uniform(40, 140), rnd.uniform(90, 180), rnd.uniform(170, 250)])
    bas = np.array([rnd.uniform(170, 250), rnd.uniform(170, 240), rnd.uniform(160, 250)])
    y = np.linspace(0, 1, h)[:, None, None]
    a = haut * (1 - y) + bas * y + np.zeros((h, w, 3))
    g = np.random.default_rng(rnd.randrange(1 << 30))
    nuages = Image.fromarray((g.random((6, 9)) * 255).astype(np.uint8)).resize((w, h), Image.Resampling.BICUBIC)
    n = np.asarray(nuages, np.float32)[:, :, None] / 255.0
    a = a * (1 - 0.5 * n) + 250 * 0.5 * n + g.normal(0, 3, (h, w, 3))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _texture_generee(rnd, w=1200, h=800):
    """Texture générée (sable, herbe, gravier) : bruit à plusieurs échelles, teinte au hasard, sans sujet."""
    g = np.random.default_rng(rnd.randrange(1 << 30))
    a = np.zeros((h, w), np.float32)
    for k, poids in ((8, 0.5), (32, 0.3), (128, 0.2), (w, 0.15)):
        b = Image.fromarray((g.random((max(2, h * k // w), k)) * 255).astype(np.uint8)).resize(
            (w, h), Image.Resampling.BICUBIC)
        a += poids * np.asarray(b, np.float32)
    teinte = np.array([rnd.uniform(0.6, 1.2), rnd.uniform(0.6, 1.2), rnd.uniform(0.4, 1.0)])
    return Image.fromarray(np.clip(a[:, :, None] * teinte, 0, 255).astype(np.uint8))


def fabriquer_tournees(origines, travail, graine=13):
    """Photothèque « tournees » (0.4.1) et sa vérité (verite_tournees.json) :
    - chaque origine publique (avec une date EXIF) et ses copies tournées ou en miroir : quart de tour (r90, r270),
      demi-tour, miroir, miroir et quart de tour ; un quart de tour envoyé par WhatsApp (1 600 px, qualité 70, sans
      EXIF), un miroir recadré de 3 %, un quart de tour recadré de 10 % ;
    - des images symétriques (une origine sur deux : moitié gauche et son miroir) et leur rafale (cadre décalé de
      3 %, symétrique elle aussi : son miroir est elle-même, pas la première) ;
    - 80 ciels et 80 textures générés (dégradés, nuages, sable, herbe : presque pareils dans tous les sens), avec
      une copie en miroir ou tournée pour un sur quatre.
    Chaque image symétrique, chaque rafale, chaque ciel et chaque texture est une origine à part : aucune ne doit être
    réunie à une autre comme retouche."""
    verite_f = travail / 'verite_tournees.json'
    if verite_f.is_file():
        return json.loads(verite_f.read_text(encoding='utf-8'))
    rnd = random.Random(graine)
    verite = {}
    racine = travail / 'tournees' / 'photos'

    def poser(rel, octets, origine, variante, cat):
        p = racine / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(octets)
        verite[rel.lower()] = {'origine': origine, 'variante': variante, 'categorie': cat}
    for k, origine in enumerate(origines):
        cat = re.sub(r'[^a-z0-9]+', '_', origine[0]['categorie']).strip('_') or 'divers'
        src = origine[0]['octets']
        if src[:2] == b'\xff\xd8':
            src = _avec_exif(src)
        im = modele.vers_rvb(ImageOps.exif_transpose(Image.open(io.BytesIO(src))))
        poser(f'{cat}/{k:04d}.jpg', src, k, 'original', cat)
        for v, ops in TOURNEES.items():
            poser(f'{cat}/{k:04d}_{v}.jpg', _jpeg(im.transpose(*ops), 90, exif=True), k, v, cat)
        r90 = im.transpose(Image.Transpose.ROTATE_90)
        w, h = r90.size
        r = min(1600, round(max(w, h) * 0.75)) / max(w, h)
        poser(f'WhatsApp Images/{cat}_{k:04d}_r90_whatsapp.jpg',
              _jpeg(r90.resize((round(w * r), round(h * r)), Image.Resampling.LANCZOS), 70), k, 'r90_whatsapp', cat)
        poser(f'{cat}/{k:04d}_miroir_recadrage3.jpg',
              _jpeg(_recadrer(im.transpose(Image.Transpose.FLIP_LEFT_RIGHT), 0.03, rnd), 90, exif=True), k,
              'miroir_recadrage3', cat)
        poser(f'{cat}/{k:04d}_r270_recadrage10.jpg',
              _jpeg(_recadrer(im.transpose(Image.Transpose.ROTATE_270), 0.10, rnd), 90, exif=True), k,
              'r270_recadrage10', cat)
        if k % 2 == 0:
            poser(f'symetriques/{k:04d}_sym.jpg', _jpeg(_symetrique(im, 0.0), 90), 100000 + k, 'original', 'symetrique')
            poser(f'symetriques/{k:04d}_sym_rafale.jpg', _jpeg(_symetrique(im, 0.03), 90), 200000 + k, 'original',
                  'symetrique')
        if k % 50 == 0:
            print(f'  tournées : {k + 1} / {len(origines)} origines', flush=True)
    for k in range(80):
        for nom, fabrique in (('ciel', _ciel), ('texture', _texture_generee)):
            im = fabrique(rnd)
            o = (300000 if nom == 'ciel' else 400000) + k
            poser(f'{nom}s/{nom}_{k:03d}.jpg', _jpeg(im, 90), o, 'original', nom)
            if k % 4 == 0:
                v = ('miroir', 'r90', 'r180', 'r270')[(k // 4) % 4]
                poser(f'{nom}s/{nom}_{k:03d}_{v}.jpg', _jpeg(im.transpose(*TOURNEES[v]), 90), o, v, nom)
    verite_f.write_text(json.dumps(verite, ensure_ascii=False), encoding='utf-8')
    return verite


def mesurer_tournees(travail, cle, materiel_choisi, origines):
    """Copies tournées ou en miroir : rappel par variante et photos différentes réunies, avant (recherche des copies
    tournées coupée, comme la 0.4.0) et après, sur la photothèque « tournees » ; puis, s'ils sont là, sur les
    photothèques « retouches » et « rafales » (pas de photo différente réunie de plus, durées)."""
    verite = fabriquer_tournees(origines, travail)
    sortie = {}
    mondes = [('tournees', verite)]
    v_f = travail / 'verite.json'
    if v_f.is_file():
        v = json.loads(v_f.read_text(encoding='utf-8'))
        mondes += [(m, v[m]) for m in ('retouches', 'rafales') if (travail / m / 'donnees' / 'index.db').is_file()]
    for monde, ver in mondes:
        base = indexer(travail / monde, cle, materiel_choisi)
        racine = Path(base.dossiers()[0]['chemin'])
        for nom, actif in (('avant', False), ('apres', True)):
            doublons.CHERCHER_TOURNEES = actif
            t0 = time.perf_counter()
            fin, res = bout_en_bout(base, cle, ver, racine)
            fin['rappels'] = rappels(res, base, ver, racine, 'rafales' if monde == 'rafales' else 'retouches')
            sortie[f'{monde}_{nom}'] = fin
            r = fin['retouchees']
            print(f"{monde} {nom} : {fin['duree_s']} s (relance {fin['duree_relance_s']} s), retouchées "
                  f"{r['groupes']} groupes, {r['paires_justes']}/{r['paires']} paires justes, impurs {r['n_impurs']} "
                  f"{r['impurs'][:3]} ; tournées vérifiées {fin['comptes'].get('paires_tournees')} ; "
                  f"{time.perf_counter() - t0:.0f} s", flush=True)
            for var, s in sorted(fin['rappels'].items()):
                print('    ', var, s)
        doublons.CHERCHER_TOURNEES = True
        base.fermer()
    return sortie


# ---------------------------------------------------------------- index

def indexer(monde, cle, materiel_choisi):
    """Base de la photothèque `monde`, analysée par le vrai moteur (reprend une analyse interrompue)."""
    base = Base(monde / 'donnees' / 'index.db', monde / 'donnees' / 'miniatures.db')
    if not base.dossiers():
        base.ajouter_dossier(str(monde / 'photos'))
    c = base.compter()
    if c['a_faire'] == 0 and c['total']:
        return base

    def fabrique(materiel='auto'):
        return modele.Moteur(cle, materiel=materiel_choisi if materiel == 'auto' else materiel, charger_texte=False)
    ix = Indexeur(base, fabrique, cle)
    t0 = time.time()
    ix.relancer()
    while not ix.attendre(10):
        e = ix.etat()
        print(f'  analyse {monde.name} : {e["faites"]} / {e["total"]} ({e["debit"]} photos/s)', flush=True)
    ix.arreter()
    print(f'  analyse {monde.name} : finie en {time.time() - t0:.0f} s', flush=True)
    return base


def fiches(base, verite, racine, cle):
    """Photos analysées de la base : ids, vérité, empreintes, vecteurs (alignés)."""
    lignes = base._cnx().execute('SELECT id, chemin FROM photos WHERE miniature = 1 ORDER BY id').fetchall()
    vid, mat, _ = base.vecteurs(cle)
    ligne = {int(i): k for k, i in enumerate(vid.tolist())}
    ids, info, hs, cs, vecs = [], [], [], [], []
    for pid, ch in lignes:
        rel = Path(ch).relative_to(racine).as_posix().lower()
        if rel not in verite or pid not in ligne:
            continue
        h, c = doublons.empreinte(base.miniature(pid))
        ids.append(pid)
        info.append(verite[rel])
        hs.append(h)
        cs.append(c)
        vecs.append(mat[ligne[pid]])
    return {'ids': np.array(ids), 'info': info, 'phash': np.array(hs, np.int64).view(np.uint64),
            'contraste': np.array(cs), 'vec': np.array(vecs, np.float32),
            'origine': np.array([i['origine'] for i in info]), 'categorie': [i['categorie'] for i in info],
            'variante': [i['variante'] for i in info]}


# ---------------------------------------------------------------- mesures paire par paire

def _q(x, qs=(0, 0.01, 0.05, 0.5, 0.95, 0.99, 1)):
    x = np.asarray(x, dtype=np.float64)
    return [round(float(np.quantile(x, q)), 4) for q in qs] if len(x) else []


def paires_negatives(f, selection):
    """Paires (i, j) d'origines différentes parmi les photos sélectionnées (masque), avec hamming et cosinus."""
    sel = np.flatnonzero(selection)
    if len(sel) < 2:
        vide = np.zeros(0, np.intp)
        return vide, vide, np.zeros(0, np.int32), np.zeros(0, np.float32), np.zeros(0, bool)
    vec = f['vec'][sel]
    cos_m = vec @ vec.T
    h = f['phash'][sel]
    ham_m = np.bitwise_count(h[:, None] ^ h[None, :])
    a, b = np.triu_indices(len(sel), 1)
    diff = f['origine'][sel][a] != f['origine'][sel][b]
    a, b = a[diff], b[diff]
    cats = {c: k for k, c in enumerate(sorted(set(f['categorie'])))}
    cat = np.array([cats[c] for c in f['categorie']])[sel]
    return sel[a], sel[b], ham_m[a, b].astype(np.int32), cos_m[a, b], cat[a] == cat[b]


def pyramides(base, ids):
    return {int(i): doublons.preparer_verification(base.miniature(int(i))) for i in ids}


def analyser_monde(base, f, nom, positives_de, negatives_originales=True):
    """Mesures paire par paire d'une photothèque. positives_de(i, j) -> étiquette ou None."""
    n = len(f['ids'])
    pyr = {}

    def pyramide(k):
        if k not in pyr:
            pyr[k] = doublons.preparer_verification(base.miniature(int(f['ids'][k])))
        return pyr[k]
    # Positives : même origine.
    pos = []
    par_origine = {}
    for k in range(n):
        par_origine.setdefault(int(f['origine'][k]), []).append(k)
    for membres in par_origine.values():
        for x in range(len(membres)):
            for y in range(x + 1, len(membres)):
                i, j = membres[x], membres[y]
                etiquette = positives_de(i, j)
                if etiquette is None:
                    continue
                ham = int(np.bitwise_count(f['phash'][i] ^ f['phash'][j]))
                cos = float(f['vec'][i] @ f['vec'][j])
                m = doublons.verifier(pyramide(i), pyramide(j))
                pos.append({'etiquette': etiquette, 'hamming': ham, 'cosinus': cos, 'mesure': m})
    # Négatives : toutes (les originales seules pour les retouches : leurs variantes répéteraient les mêmes paires),
    # vérifiées si assez voisines (vecteurs ou empreintes).
    selection = (np.array([v == 'original' for v in f['variante']]) if negatives_originales
                 else np.ones(n, dtype=bool))
    ii, jj, ham, cos, meme_cat = paires_negatives(f, selection)
    voisines = np.flatnonzero((cos >= 0.80) | (ham <= 16))
    neg_verif = []
    for k in voisines.tolist():
        m = doublons.verifier(pyramide(int(ii[k])), pyramide(int(jj[k])))
        neg_verif.append({'hamming': int(ham[k]), 'cosinus': float(cos[k]), 'meme_categorie': bool(meme_cat[k]),
                          'mesure': m, 'a': f['info'][int(ii[k])], 'b': f['info'][int(jj[k])]})
    return {'nom': nom, 'positives': pos, 'negatives': {'hamming': ham, 'cosinus': cos, 'meme_categorie': meme_cat},
            'negatives_verifiees': neg_verif}


def resumer_paires(a_ret, a_raf):
    """Tableaux de synthèse paire par paire, à partir des analyses des deux photothèques."""
    r = {}
    lignes = []
    for etiquette in sorted({p['etiquette'] for p in a_ret['positives']} | {p['etiquette'] for p in a_raf['positives']},
                            key=lambda e: (e.startswith('rafale'), e)):
        ps = [p for p in a_ret['positives'] + a_raf['positives'] if p['etiquette'] == etiquette]
        ret = sum(1 for p in ps if doublons.est_retouchee(p['mesure']))
        sup = sum(1 for p in ps if doublons.superposables(p['mesure']) and not doublons.est_retouchee(p['mesure']))
        lignes.append({'etiquette': etiquette, 'n': len(ps),
                       'hamming': _q([p['hamming'] for p in ps], (0, 0.5, 1)),
                       'cosinus': _q([p['cosinus'] for p in ps], (0, 0.05, 0.5)),
                       'retouchee': ret, 'superposables': sup})
    r['positives'] = lignes
    for nom, a in (('retouches', a_ret), ('rafales', a_raf)):
        neg = a['negatives']
        mc = neg['meme_categorie']
        r[f'negatives_{nom}'] = {
            'n': int(len(neg['cosinus'])), 'n_meme_categorie': int(mc.sum()),
            'cosinus_tous': _q(neg['cosinus'], (0.5, 0.99, 0.999, 1)),
            'cosinus_meme_categorie': _q(neg['cosinus'][mc], (0.5, 0.99, 1)) if mc.any() else [],
            'hamming_min': int(neg['hamming'].min()) if len(neg['hamming']) else None,
            'hamming_1pc': float(np.quantile(neg['hamming'], 0.01)) if len(neg['hamming']) else None,
            'verifiees': len(a['negatives_verifiees']),
            'prises_pour_retouche': [dict(v, mesure=v['mesure']) for v in a['negatives_verifiees']
                                     if doublons.est_retouchee(v['mesure'])],
            'superposables': sum(1 for v in a['negatives_verifiees'] if doublons.superposables(v['mesure'])),
            'plus_proches': sorted(({'cosinus': round(v['cosinus'], 4), 'hamming': v['hamming'],
                                     'a': v['a']['categorie'], 'b': v['b']['categorie'],
                                     'ncc': (v['mesure'] or {}).get('ncc'), 'debord': (v['mesure'] or {}).get('debord'),
                                     'ecart_max': (v['mesure'] or {}).get('ecart_max')}
                                    for v in a['negatives_verifiees']), key=lambda d: -d['cosinus'])[:8]}
    # Marges de la règle « retouchée » : les valeurs limites des vraies retouches et des négatives les plus proches.
    def contenue(m):
        return m is not None and m['debord'] <= doublons.DEBORD_MAX and 'ecart_max' in m
    vraies = [p['mesure'] for p in a_ret['positives'] if p['mesure'] is not None]
    fausses = [v['mesure'] for a in (a_ret, a_raf) for v in a['negatives_verifiees'] if contenue(v['mesure'])]
    rafs = [p['mesure'] for p in a_raf['positives'] if p['mesure'] is not None]
    r['marges'] = {
        'retouches_ncc_min': min((m['ncc'] for m in vraies), default=None),
        'retouches_ecart_max_max': max((m['ecart_max'] for m in vraies if contenue(m)), default=None),
        'retouches_debord_max': max((m['debord'] for m in vraies), default=None),
        'retouches_couverture_min': min((m['couverture'] for m in vraies), default=None),
        'negatives_contenues_ncc_max': max((m['ncc'] for m in fausses), default=None),
        'negatives_contenues_ecart_max_min': min((m['ecart_max'] for m in fausses), default=None),
        'rafales_debord_min': min((m['debord'] for m in rafs), default=None),
        'rafales_contenues_ecart_max_min': min((m['ecart_max'] for m in rafs if contenue(m)), default=None),
    }
    # Statistiques de la vérification, pour les superpositions « contenues » (l'une tient dans l'autre) : ce sont
    # elles qui départagent une retouche d'un sujet qui a bougé, d'un texte ajouté ou d'une autre photo.
    cles = ('ncc', 'ecart_max', 'relief_max', 'differents_max')
    familles = {}
    for p in a_ret['positives'] + a_raf['positives']:
        familles.setdefault(p['etiquette'], []).append(p['mesure'])
    familles['negatives'] = [v['mesure'] for a in (a_ret, a_raf) for v in a['negatives_verifiees']]
    stats = {}
    for nom, ms in familles.items():
        contenues = [m for m in ms if m is not None and m['debord'] <= doublons.DEBORD_MAX
                     and m['recouvre'] >= doublons.RECOUVRE_MIN and 'ecart_max' in m]
        stats[nom] = {'n': len(ms), 'contenues': len(contenues)}
        for c in cles:
            stats[nom][c] = _q([m.get(c, 0.0) for m in contenues], (0, 0.05, 0.5, 0.95, 1))
    r['statistiques'] = stats
    # Seuil de cosinus des « proches » : précision et rappel paire par paire (sans l'aide des dates).
    pos_cos = np.array([p['cosinus'] for p in a_raf['positives'] if p['etiquette'] == 'rafale'])
    neg_cos = np.concatenate([a_ret['negatives']['cosinus'], a_raf['negatives']['cosinus']])
    balayage = []
    for s in (0.85, 0.88, 0.90, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97):
        vp = int((pos_cos >= s).sum())
        fp = int((neg_cos >= s).sum())
        balayage.append({'seuil': s, 'rappel': round(vp / max(1, len(pos_cos)), 4),
                         'precision': round(vp / max(1, vp + fp), 4), 'fausses': fp})
    r['cosinus_proches'] = balayage
    pos_h = np.array([p['hamming'] for p in a_raf['positives'] if p['etiquette'] == 'rafale'])
    neg_h = np.concatenate([a_ret['negatives']['hamming'], a_raf['negatives']['hamming']])
    r['hamming_proches'] = [{'seuil': s, 'rappel': round(float((pos_h <= s).mean()), 4) if len(pos_h) else None,
                             'fausses': int((neg_h <= s).sum())} for s in (6, 8, 10, 12, 14)]
    return r


# ---------------------------------------------------------------- calcul complet

def bout_en_bout(base, cle, verite, racine):
    """Calcul complet (myphotos.doublons) et comparaison des groupes à la vérité."""
    d = doublons.Doublons(base, cle)
    d.oublier()
    t0 = time.perf_counter()
    res = d.calculer()
    duree = time.perf_counter() - t0
    t0 = time.perf_counter()
    d.calculer()
    duree2 = time.perf_counter() - t0
    d.fermer()
    chemins = dict(base._cnx().execute('SELECT id, chemin FROM photos').fetchall())

    def info(pid):
        return verite.get(Path(chemins[pid]).relative_to(racine).as_posix().lower())
    sortie = {'duree_s': round(duree, 2), 'duree_relance_s': round(duree2, 2), 'comptes': res['comptes']}
    for t in doublons.TYPES:
        gs = [g for g in res['groupes'] if g['type'] == t]
        paires = bonnes = 0
        impurs = []
        garde_original = 0
        for g in gs:
            infos = [info(p['id']) for p in g['photos']]
            origines = [i['origine'] for i in infos]
            for x in range(len(origines)):
                for y in range(x + 1, len(origines)):
                    paires += 1
                    bonnes += origines[x] == origines[y]
            if len(set(origines)) > 1:
                impurs.append([f"{i['categorie']}/{i['origine']}:{i['variante']}" for i in infos])
            if infos[0]['variante'] in ('original', 'autre_fichier', 'sans_exif'):
                garde_original += 1
        sortie[t] = {'groupes': len(gs), 'paires': paires, 'paires_justes': bonnes,
                     'precision': round(bonnes / paires, 4) if paires else None, 'impurs': impurs[:10],
                     'n_impurs': len(impurs), 'garde_original': garde_original,
                     'gain_octets': sum(g['gain_octets'] for g in gs)}
    return sortie, res


def rappels(res, base, verite, racine, monde):
    """Rappel de chaque niveau : part des paires vraies (même origine) réunies dans un groupe du bon niveau (ou d'un
    niveau plus prudent pour les retouches)."""
    chemins = dict(base._cnx().execute('SELECT id, chemin FROM photos').fetchall())
    info = {pid: verite.get(Path(ch).relative_to(racine).as_posix().lower()) for pid, ch in chemins.items()}
    rep = {}                                    # photo proposée à la suppression -> photo gardée de son groupe
    series = {}                                 # photo -> numéros de ses groupes « proches »
    for k, g in enumerate(res['groupes']):
        if g['type'] == 'proches':
            for p in g['photos']:
                series.setdefault(p['id'], set()).add(k)
        else:
            for p in g['photos'][1:]:
                rep[p['id']] = g['photos'][0]['id']

    def garde(pid):
        vus = set()
        while pid in rep and pid not in vus:
            vus.add(pid)
            pid = rep[pid]
        return pid

    def meme_serie(a, b):
        return bool(series.get(garde(a), set()) & series.get(garde(b), set()))
    par_origine = {}
    for pid, i in info.items():
        if i is not None:
            par_origine.setdefault(i['origine'], []).append(pid)
    par_variante = {}
    for membres in par_origine.values():
        if monde == 'retouches':
            orig = [m for m in membres if info[m]['variante'] == 'original']
            for m in membres:
                if not orig or m == orig[0]:
                    continue
                reunies = garde(m) == garde(orig[0])
                s = par_variante.setdefault(info[m]['variante'], {'n': 0, 'reunies': 0, 'proches_seulement': 0})
                s['n'] += 1
                s['reunies'] += reunies
                s['proches_seulement'] += (not reunies) and meme_serie(m, orig[0])
        else:
            for x in range(len(membres)):
                for y in range(x + 1, len(membres)):
                    a, b = membres[x], membres[y]
                    speciales = sorted({info[a]['variante'], info[b]['variante']} - {'cadre_a', 'cadre_b', 'cadre_c'})
                    s = par_variante.setdefault('+'.join(speciales) or 'rafale',
                                                {'n': 0, 'proches': 0, 'retouchees': 0})
                    s['n'] += 1
                    if garde(a) == garde(b):
                        s['retouchees'] += 1
                    elif meme_serie(a, b):
                        s['proches'] += 1
    return par_variante


# ---------------------------------------------------------------- performance (données synthétiques)

def _image_synthetique(rnd, fond, w=360, h=270):
    """Miniature dessinée : le fond de l'événement (même lieu), plus des formes propres à la photo."""
    im = Image.fromarray(fond).resize((w, h), Image.Resampling.BICUBIC)
    d = ImageDraw.Draw(im)
    for _ in range(int(rnd.integers(4, 10))):
        x, y = int(rnd.integers(0, w)), int(rnd.integers(0, h))
        r = int(rnd.integers(8, 70))
        c = tuple(int(v) for v in rnd.integers(0, 256, 3))
        if rnd.random() < 0.5:
            d.ellipse((x - r, y - r, x + r, y + r), fill=c)
        else:
            d.rectangle((x - r, y - r // 2, x + r, y + r // 2), fill=c)
    return im


def _jpeg80(im):
    t = io.BytesIO()
    im.save(t, 'JPEG', quality=80)
    return t.getvalue()


def base_synthetique(dossier, n, cle, graine=11):
    """Base de n photos fictives, organisée comme une photothèque de famille : des « événements » de 20 à 120 photos
    (même lieu : fond commun ; vecteurs voisins, cosinus de 0,63 à 0,89 entre deux photos ; dates sur deux heures,
    événements espacés de 1 à 20 jours), dont 6 % de rafales de 3 à 6 (même dessin décalé, autre exposition, cosinus
    ~0,97, quelques secondes d'écart), 3 % de retouches (copie réduite, sans date) et 1 % de copies exactes (vrais
    fichiers, pour le SHA-256). 30 % des photos n'ont pas de date de prise de vue."""
    dossier.mkdir(parents=True, exist_ok=True)
    base = Base(dossier / 'index.db', dossier / 'miniatures.db')
    if base.compter()['total'] >= n:
        return base
    rnd = np.random.default_rng(graine)
    dim = modele.info(cle)['dimension']
    photos_dir = dossier / 'photos'
    photos_dir.mkdir(exist_ok=True)
    d = base.ajouter_dossier(str(photos_dir))

    def unitaire():
        v = rnd.standard_normal(dim).astype(np.float32)
        return v / np.linalg.norm(v)

    def iso(t):
        return None if t is None else time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(t))
    lignes, analyses = [], []
    k = 0
    t0 = time.time()
    t_evt = time.mktime((2015, 1, 1, 10, 0, 0, 0, 0, -1))
    reste_evt = 0
    while k < n:
        if reste_evt <= 0:                      # nouvel événement
            reste_evt = int(rnd.integers(20, 121))
            centre = unitaire()
            fond = (rnd.random((3, 4, 3)) * 255).astype(np.uint8)
            t_evt += float(rnd.uniform(1, 20)) * 86400
        tirage = rnd.random()
        im = _image_synthetique(rnd, fond)
        v0 = centre + unitaire() * float(rnd.uniform(0.35, 0.75))
        date = None if rnd.random() < 0.3 else t_evt + float(rnd.uniform(0, 7200))
        if tirage < 0.06:                       # rafale
            membres = []
            for f in range(int(rnd.integers(3, 7))):
                dx, dy = int(rnd.integers(-12, 13)), int(rnd.integers(-8, 9))
                fr = ImageEnhance.Brightness(im.transform(im.size, Image.Transform.AFFINE, (1, 0, dx, 0, 1, dy),
                                                          resample=Image.Resampling.BILINEAR)).enhance(
                    float(rnd.uniform(0.9, 1.1)))
                membres.append((_jpeg80(fr), v0 + unitaire() * 0.2, None if date is None else date + 0.4 * f))
        elif tirage < 0.09:                     # retouche : la même, réduite et recompressée, sans date
            petite = im.resize((240, 180), Image.Resampling.LANCZOS)
            t = io.BytesIO()
            petite.save(t, 'JPEG', quality=65)
            membres = [(_jpeg80(im), v0, date), (_jpeg80(Image.open(t).resize((360, 270))), v0 + unitaire() * 0.1, None)]
        else:
            membres = [(_jpeg80(im), v0, date)]
        identique = tirage > 0.99
        for mini, v, quand in membres:
            if k >= n:
                break
            chemin = photos_dir / f'p{k:06d}.jpg'
            taille = int(rnd.integers(1_500_000, 6_000_000))
            if identique:
                chemin.write_bytes(mini)
                taille = len(mini)
            lignes.append((str(chemin), taille, 1, None))
            analyses.append({'taille': taille, 'modifie': 1, 'largeur': 4000, 'hauteur': 3000, 'date_prise': iso(quand),
                             'lat': None, 'lon': None, 'miniature': mini,
                             'vecteur': (v / np.linalg.norm(v)).astype(np.float16).tobytes()})
            k += 1
            reste_evt -= 1
            if identique:
                chemin2 = photos_dir / f'p{k:06d}.jpg'
                chemin2.write_bytes(mini)
                lignes.append((str(chemin2), taille, 1, None))
                analyses.append(dict(analyses[-1]))
                k += 1
                reste_evt -= 1
        if len(lignes) >= 2000 or k >= n:
            base.ajouter_photos(d['id'], lignes)
            ids = {ch: pid for ch, (pid, _, _) in base.connus(d['id']).items()}
            for (ch, *_), a in zip(lignes, analyses):
                a['id'] = ids[ch]
            base.enregistrer(analyses, [], cle)
            lignes, analyses = [], []
            print(f'  base synthétique : {k} / {n} ({time.time() - t0:.0f} s)', flush=True)
    return base


def mesurer_perf(travail, tailles, cle):
    sortie = {}
    for n in tailles:
        dossier = travail / f'perf_{n}'
        base = base_synthetique(dossier, n, cle)
        d = doublons.Doublons(base, cle)
        d.oublier()
        etapes = {}
        debut = {}

        def suivre(phase, fait, total):
            t = time.perf_counter()
            if phase not in debut:
                debut[phase] = t
            etapes[phase] = t - debut[phase]
        t0 = time.perf_counter()
        r1 = d.calculer(progression=suivre)
        t1 = time.perf_counter() - t0
        etapes1 = {k: round(v, 2) for k, v in etapes.items()}
        etapes.clear()
        debut.clear()
        t0 = time.perf_counter()
        r2 = d.calculer(progression=suivre)
        t2 = time.perf_counter() - t0
        d.fermer()
        base.fermer()
        sortie[n] = {'premier_calcul_s': round(t1, 1), 'relance_s': round(t2, 1), 'etapes_premier': etapes1,
                     'etapes_relance': {k: round(v, 2) for k, v in etapes.items()},
                     'paires_verifiees': r1['comptes']['paires_verifiees'],
                     'paires_calculees': r1['comptes']['paires_calculees'],
                     'paires_directes': r1['comptes']['paires_directes'],
                     'groupes': r1['comptes']['groupes'], 'groupes_relance': r2['comptes']['groupes']}
        print(f'  {n} photos : premier calcul {t1:.1f} s, relance {t2:.1f} s, {sortie[n]}', flush=True)
    return sortie


# ---------------------------------------------------------------- rapport

def _md(lignes, entetes):
    s = '| ' + ' | '.join(entetes) + ' |\n|' + '|'.join('---' for _ in entetes) + '|\n'
    for l in lignes:
        s += '| ' + ' | '.join(str(x) for x in l) + ' |\n'
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--images', action='append', default=[], help="dossier d'images publiques (plusieurs possibles)")
    ap.add_argument('--travail', default=str(Path(tempfile.gettempdir()) / 'myphotos-doublons'))
    ap.add_argument('--moteur', default='base')
    ap.add_argument('--materiel', default='processeur', choices=('processeur', 'auto'))
    ap.add_argument('--reconstruire', action='store_true')
    ap.add_argument('--perf', default='')
    ap.add_argument('--markdown', action='store_true')
    ap.add_argument('--limite', type=int, default=0, help="nombre d'origines au plus (essai rapide)")
    ap.add_argument('--tournees', action='store_true',
                    help='copies tournées ou en miroir seulement (photothèque « tournees », avant / après)')
    a = ap.parse_args()
    travail = Path(a.travail)
    resultats_f = travail / 'resultats_doublons.json'
    resultats = json.loads(resultats_f.read_text(encoding='utf-8')) if resultats_f.is_file() else {}
    if a.images and a.tournees:
        travail.mkdir(parents=True, exist_ok=True)
        origines = regrouper_origines(lister_sources(a.images))
        if a.limite:
            origines = origines[::max(1, len(origines) // a.limite)][:a.limite]
        resultats.setdefault(a.moteur, {})['tournees'] = mesurer_tournees(travail, a.moteur, a.materiel, origines)
    elif a.images:
        if a.reconstruire and travail.exists():
            for p in ('retouches', 'rafales', 'verite.json'):
                q = travail / p
                if q.is_dir():
                    shutil.rmtree(q)
                elif q.exists():
                    q.unlink()
        travail.mkdir(parents=True, exist_ok=True)
        sources = lister_sources(a.images)
        origines = regrouper_origines(sources)
        if a.limite:
            origines = origines[::max(1, len(origines) // a.limite)][:a.limite]
        print(f'{len(sources)} images, {len(origines)} origines', flush=True)
        verite = fabriquer(origines, travail)
        rapport = {'sources': len(sources), 'origines': len(origines), 'moteur': a.moteur,
                   'seuils': {k: getattr(doublons, k) for k in (
                       'HAMMING_CANDIDAT', 'COSINUS_CANDIDAT', 'HAMMING_IDENTIQUE', 'CONTRASTE_MIN', 'DEBORD_MAX',
                       'RECOUVRE_MIN', 'COUVERTURE_MIN', 'ROTATION_MAX', 'NCC_MIN', 'ECART_BLOC_MAX', 'COSINUS_PROCHE',
                       'NCC_PROCHE', 'RECOUVRE_PROCHE', 'FENETRE_PROCHES_S', 'HAMMING_RETOUCHE_MAX', 'VOISINES_MAX',
                       'RELIEF_MAX', 'DIFFERENTS_MAX', 'SEUIL_DIFFERENT')}}
        analyses = {}
        for monde in ('retouches', 'rafales'):                 # d'abord les deux analyses (longues)
            indexer(travail / monde, a.moteur, a.materiel).fermer()
        for monde in ('retouches', 'rafales'):
            racine = travail / monde / 'photos'
            base = indexer(travail / monde, a.moteur, a.materiel)
            f = fiches(base, verite[monde], racine, a.moteur)
            print(f'{monde} : {len(f["ids"])} photos analysées', flush=True)
            if monde == 'retouches':
                def etiquette(i, j, f=f):
                    vi, vj = f['variante'][i], f['variante'][j]
                    if 'original' not in (vi, vj):
                        return None
                    autre = vj if vi == 'original' else vi
                    return 'identique' if autre == 'copie' else autre
                analyses[monde] = analyser_monde(base, f, monde, etiquette)
            else:
                def etiquette(i, j, f=f):
                    vi, vj = f['variante'][i], f['variante'][j]
                    if vi.startswith('cadre') and vj.startswith('cadre'):
                        return 'rafale'
                    autre = [v for v in (vi, vj) if not v.startswith('cadre')]
                    if len(autre) == 1 and 'cadre_a' in (vi, vj):
                        return 'rafale_' + autre[0]
                    return None
                analyses[monde] = analyser_monde(base, f, monde, etiquette, negatives_originales=False)
            t0 = time.perf_counter()
            fin, res = bout_en_bout(base, a.moteur, verite[monde], racine)
            fin['rappels'] = rappels(res, base, verite[monde], racine, monde)
            rapport[f'bout_en_bout_{monde}'] = fin
            print(f'{monde} : calcul complet en {time.perf_counter() - t0:.1f} s', flush=True)
            base.fermer()
        # Toutes les mesures paire par paire, pour essayer d'autres règles sans tout recalculer.
        (travail / f'mesures_{a.moteur}.json').write_text(json.dumps(
            {m: {'positives': x['positives'], 'negatives_verifiees': x['negatives_verifiees'],
                 'negatives_cosinus': x['negatives']['cosinus'].tolist(),
                 'negatives_hamming': x['negatives']['hamming'].tolist(),
                 'negatives_meme_categorie': x['negatives']['meme_categorie'].tolist()}
             for m, x in analyses.items()}, ensure_ascii=False, default=float), encoding='utf-8')
        (travail / f'bout_en_bout_{a.moteur}.json').write_text(json.dumps(
            {k: v for k, v in rapport.items() if k.startswith('bout_en_bout')}, ensure_ascii=False, default=float),
            encoding='utf-8')
        rapport['paires'] = resumer_paires(analyses['retouches'], analyses['rafales'])
        resultats[a.moteur] = rapport
        afficher(rapport, a.markdown)
    if a.perf:
        tailles = [int(x) for x in a.perf.split(',') if x.strip()]
        resultats['perf'] = mesurer_perf(travail, tailles, a.moteur)
    travail.mkdir(parents=True, exist_ok=True)
    resultats_f.write_text(json.dumps(resultats, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print(f'Résultats : {resultats_f}')


def afficher(r, markdown=False):
    p = r['paires']
    print(f"\n=== Moteur {r['moteur']} : {r['origines']} origines ({r['sources']} images)")
    print('\nPaires positives (même origine) : distance des empreintes [min, médiane, max], cosinus [min, 5 %, '
          'médiane], prises pour une retouche, superposables :')
    lignes = [(l['etiquette'], l['n'], l['hamming'], l['cosinus'], l['retouchee'], l['superposables'])
              for l in p['positives']]
    if markdown:
        print(_md(lignes, ('variante', 'paires', 'hamming', 'cosinus', 'retouchée', 'superposable')))
    else:
        for l in lignes:
            print('  ', *l)
    for nom in ('retouches', 'rafales'):
        n = p[f'negatives_{nom}']
        print(f"\nNégatives ({nom}) : {n['n']} paires ({n['n_meme_categorie']} de même catégorie) ; cosinus "
              f"médiane/99 %/99,9 %/max {n['cosinus_tous']} ; même catégorie médiane/99 %/max "
              f"{n['cosinus_meme_categorie']} ; hamming min {n['hamming_min']} (1 % : {n['hamming_1pc']}) ; "
              f"vérifiées {n['verifiees']}, prises pour une retouche {len(n['prises_pour_retouche'])}, "
              f"superposables {n['superposables']}")
        for v in n['plus_proches'][:5]:
            print('   ', v)
    print('\nMarges :', p['marges'])
    print('\nSuperpositions contenues : [min, 5 %, médiane, 95 %, max] de ncc, ecart_max, relief_max, differents_max')
    for nom, s in sorted(p['statistiques'].items()):
        print(f"  {nom:18s} {s['contenues']:5d}/{s['n']:<5d} ncc {s['ncc']} ecart {s['ecart_max']} "
              f"relief {s['relief_max']} différents {s['differents_max']}")
    print('\nCosinus « proches » (rafales contre toutes les négatives, sans les dates) :')
    for b in p['cosinus_proches']:
        print('  ', b)
    print('Hamming « proches » :', p['hamming_proches'])
    for monde in ('retouches', 'rafales'):
        b = r[f'bout_en_bout_{monde}']
        print(f"\nCalcul complet ({monde}) : {b['duree_s']} s (relance {b['duree_relance_s']} s) ; {b['comptes']}")
        for t in doublons.TYPES:
            x = b[t]
            print(f"  {t} : {x['groupes']} groupes, précision des paires {x['precision']} "
                  f"({x['paires_justes']}/{x['paires']}), impurs {x['n_impurs']}, garde l'original {x['garde_original']}")
            for g in x['impurs'][:5]:
                print('     impur :', g)
        print('  rappels :')
        for v, s in sorted(b['rappels'].items()):
            print('    ', v, s)


if __name__ == '__main__':
    main()
