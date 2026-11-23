"""Dessine l'icône de MyPhotos : une photo (ciel, soleil, montagnes) et une loupe posée dessus.

    .venv-build\\Scripts\\python construction\\icone.py                      (icône + favicon de la page)
    .venv-build\\Scripts\\python construction\\icone.py --apercu planche.png  (planche agrandie, pour juger)

Produit construction\\MyPhotos.ico (16, 24, 32, 48, 64 et 256 px, chacune enregistrée en PNG) et met la
même icône (PNG de 32 px en data: URI) dans la balise <link rel="icon"> de page\\index.html.

Chaque taille est dessinée à part, sur-échantillonnée puis réduite : bords de la photo calés sur les
pixels, traits jamais plus fins qu'un pixel et demi, soleil dessiné au pixel près en 16 et 24 px, et les
détails (dégradé du ciel, reflet du verre) réservés aux tailles où ils se voient. La planche (--apercu)
montre chaque taille agrandie sans lissage, sur fond clair et sur fond sombre, puis à sa vraie taille.
"""
import base64
import io
import math
import re
import struct
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

TAILLES = (16, 24, 32, 48, 64, 256)
ICI = Path(__file__).resolve().parent
ICO = ICI / 'MyPhotos.ico'
PAGE = ICI.parent / 'page' / 'index.html'

CIEL_HAUT = (25, 118, 230)
CIEL_BAS = (110, 190, 250)
CIEL_UNI = (52, 150, 240)          # petites tailles : ciel uni
SOLEIL = (255, 196, 30)
MONTAGNE = (120, 200, 110)         # montagne du fond, claire
COLLINE = (30, 140, 75)            # colline du devant, foncée
LOUPE = (240, 84, 34)              # cerclage : orange vif, lisible sur fond clair comme sur fond sombre
MANCHE = (200, 58, 18)
VERRE = (220, 238, 253)            # bleu très pâle : le reflet blanc s'y voit
VERRE_PETIT = (235, 246, 254)      # petites tailles : presque blanc, la lentille se lit mieux
REFLET = (255, 255, 255)


def disque(x0, y0, n):
    """Pixels d'un petit disque dessiné au pixel près : carré n x n sans ses quatre coins."""
    return [(x, y) for x in range(x0, x0 + n) for y in range(y0, y0 + n)
            if not (x in (x0, x0 + n - 1) and y in (y0, y0 + n - 1))]


def geometrie(t):
    """Positions et épaisseurs, en pixels de l'icône finale (t x t). Les petites tailles sont calées à la main :
    pentes à 45°, soleil au pixel près, loupe centrée sur un coin de pixel, sans écart autour."""
    if t == 16:
        g = dict(photo=(0, 1, 13, 12), rayon=2.0, soleil=disque(1, 2, 4),
                 montagne=[(0, 11), (5, 6), (10, 11), (10, 12), (0, 12)],
                 colline=[(0, 10), (2, 8), (6, 12), (0, 12)],
                 loupe=(10.0, 10.0, 4.0), cerclage=1.75, manche=2.6, fin=14.6, ecart=0.0)
    elif t == 24:
        g = dict(photo=(0, 2, 20, 18), rayon=3.0, soleil=disque(3, 4, 5),
                 montagne=[(0, 18), (0, 17), (8, 9), (17, 18)],
                 colline=[(0, 15), (3, 12), (9, 18), (0, 18)],
                 loupe=(15.0, 15.0, 6.0), cerclage=2.25, manche=3.6, fin=21.8, ecart=0.0)
    else:                                                  # dessin de référence en 256 px, mis à l'échelle
        f = t / 256
        g = dict(photo=(round(6 * f), round(26 * f), round(212 * f), round(196 * f)), rayon=30 * f,
                 soleil=(56 * f, 72 * f, 21 * f),
                 montagne=[(x * f, y * f) for x, y in ((6, 162), (110, 90), (212, 168), (212, 196), (6, 196))],
                 colline=[(x * f, y * f) for x, y in ((6, 148), (50, 124), (134, 196), (6, 196))],
                 loupe=(170 * f, 170 * f, 62 * f),
                 cerclage=max(2.5, 17 * f), manche=max(3.8, 30 * f), fin=240 * f, ecart=max(1.0, 9 * f))
    g['degrade'] = t >= 48
    g['reflet'] = t >= 32
    g['verre'] = VERRE if t >= 48 else VERRE_PETIT
    return g


def dessiner(t):
    """L'icône en t x t pixels (RGBA)."""
    k = max(4, 512 // t)                                   # sur-échantillonnage, puis réduction par moyenne
    s = t * k
    g = geometrie(t)

    def P(v):                                              # pixel final -> pixel dessiné
        return v * k

    # --- la photo : ciel, soleil et montagnes dans un rectangle arrondi
    px0, py0, px1, py1 = (P(v) for v in g['photo'])
    im = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if g['degrade']:
        for y in range(py0, py1):
            a = (y - py0) / max(1, py1 - py0 - 1)
            d.line([(px0, y), (px1, y)], fill=tuple(round(h * (1 - a) + b * a) for h, b in zip(CIEL_HAUT, CIEL_BAS)))
    else:
        d.rectangle((px0, py0, px1, py1), fill=CIEL_UNI)
    if isinstance(g['soleil'], list):                      # petites tailles : au pixel près, sans frange verdâtre
        for x, y in g['soleil']:
            d.rectangle((P(x), P(y), P(x + 1) - 1, P(y + 1) - 1), fill=SOLEIL)
    else:
        sx, sy, sr = (P(v) for v in g['soleil'])
        d.ellipse((sx - sr, sy - sr, sx + sr, sy + sr), fill=SOLEIL)
    d.polygon([(P(x), P(y)) for x, y in g['montagne']], fill=MONTAGNE)
    d.polygon([(P(x), P(y)) for x, y in g['colline']], fill=COLLINE)
    masque = Image.new('L', (s, s), 0)
    ImageDraw.Draw(masque).rounded_rectangle((px0, py0, px1 - 1, py1 - 1), radius=P(g['rayon']), fill=255)
    im.putalpha(ImageChops.multiply(im.getchannel('A'), masque))

    # --- la loupe : d'abord découpée dans la photo avec un petit écart, puis dessinée
    cx, cy, r = (P(v) for v in g['loupe'])
    ep, em, ec = P(g['cerclage']), P(g['manche']), P(g['ecart'])
    bout = (P(g['fin']), P(g['fin']))                      # bout du manche, sur la diagonale
    angle = math.atan2(bout[1] - cy, bout[0] - cx)
    dx, dy = math.cos(angle), math.sin(angle)
    debut = (cx + dx * (r - ep / 2), cy + dy * (r - ep / 2))

    def manche(dessin, epaisseur, couleur):                # barre en biais, bout arrondi
        nx, ny = -dy * epaisseur / 2, dx * epaisseur / 2
        dessin.polygon([(debut[0] + nx, debut[1] + ny), (bout[0] + nx, bout[1] + ny),
                        (bout[0] - nx, bout[1] - ny), (debut[0] - nx, debut[1] - ny)], fill=couleur)
        dessin.ellipse((bout[0] - epaisseur / 2, bout[1] - epaisseur / 2,
                        bout[0] + epaisseur / 2, bout[1] + epaisseur / 2), fill=couleur)

    if ec:
        trou = Image.new('L', (s, s), 0)
        dt = ImageDraw.Draw(trou)
        dt.ellipse((cx - r - ec, cy - r - ec, cx + r + ec, cy + r + ec), fill=255)
        manche(dt, em + 2 * ec, 255)
        im.putalpha(ImageChops.subtract(im.getchannel('A'), trou))
        d = ImageDraw.Draw(im)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=g['verre'])
    manche(d, em, MANCHE)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LOUPE, width=round(ep))
    if g['reflet']:
        rr = r - ep * 1.75
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), start=195, end=255, fill=REFLET,
              width=round(max(P(1.0), ep * 0.55)))
    return im.resize((t, t), Image.Resampling.BOX)


# ---------------------------------------------------------------- fichiers

def ecrire_ico(images, chemin):
    """.ico écrit à la main : chaque taille dessinée à part y est enregistrée telle quelle, en PNG."""
    blocs = []
    for t in TAILLES:
        tampon = io.BytesIO()
        images[t].save(tampon, format='PNG', optimize=True)
        blocs.append(tampon.getvalue())
    repertoire, position = b'', 6 + 16 * len(TAILLES)
    for t, bloc in zip(TAILLES, blocs):                    # 256 s'écrit 0 dans l'en-tête
        repertoire += struct.pack('<BBBBHHII', t % 256, t % 256, 0, 0, 1, 32, len(bloc), position)
        position += len(bloc)
    Path(chemin).write_bytes(struct.pack('<HHH', 0, 1, len(TAILLES)) + repertoire + b''.join(blocs))


def verifier_ico(chemin):
    """Relit l'en-tête du .ico : tailles présentes, chacune en PNG."""
    data = Path(chemin).read_bytes()
    _, genre, n = struct.unpack_from('<HHH', data, 0)
    tailles = []
    for i in range(n):
        w, _, _, _, _, _, _, debut = struct.unpack_from('<BBBBHHII', data, 6 + 16 * i)
        if data[debut:debut + 8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError(f'image {i} : pas en PNG')
        tailles.append(w or 256)
    if genre != 1 or sorted(tailles) != sorted(TAILLES):
        raise ValueError(f'.ico inattendu : {tailles}')
    return sorted(tailles)


def favicon(image32):
    tampon = io.BytesIO()
    image32.save(tampon, format='PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(tampon.getvalue()).decode('ascii')


def mettre_favicon(uri, page=PAGE):
    """Remplace la balise <link rel="icon"> de la page (une seule, déjà présente). Vrai si la page a changé."""
    texte = page.read_bytes().decode('utf-8')               # fins de ligne gardées telles quelles
    balise = f'<link rel="icon" type="image/png" sizes="32x32" href="{uri}">'
    neuf, n = re.subn(r'<link rel="icon"[^>]*>', lambda _: balise, texte, count=1)
    if n != 1:
        raise ValueError(f'{page} : balise <link rel="icon"> introuvable')
    if neuf == texte:
        return False
    page.write_bytes(neuf.encode('utf-8'))
    return True


def apercu(images, chemin):
    """Planche : chaque taille agrandie sans lissage, sur fond clair puis sombre, puis à sa vraie taille."""
    zoom = {16: 16, 24: 10, 32: 8, 48: 5, 64: 4, 256: 1}
    case, bande = 272, 64
    clair, sombre = (243, 243, 243, 255), (32, 32, 32, 255)
    planche = Image.new('RGBA', (case * len(TAILLES), case * 2 + bande * 2), clair)
    dp = ImageDraw.Draw(planche)
    dp.rectangle((0, case, planche.width, case * 2 - 1), fill=sombre)
    dp.rectangle((0, case * 2 + bande, planche.width, planche.height), fill=sombre)
    for i, t in enumerate(TAILLES):
        grand = images[t].resize((t * zoom[t], t * zoom[t]), Image.Resampling.NEAREST)
        for j in range(2):
            planche.alpha_composite(grand, (i * case + (case - grand.width) // 2, j * case + (case - grand.height) // 2))
    x = 8
    for t in TAILLES[:-1]:                                 # vraie taille, comme dans la barre des tâches
        for j in range(2):
            planche.alpha_composite(images[t], (x, case * 2 + j * bande + (bande - t) // 2))
        x += t + 24
    planche.save(chemin)


def main(argv):
    images = {t: dessiner(t) for t in TAILLES}
    ecrire_ico(images, ICO)
    print('icône :', ICO, verifier_ico(ICO))
    if mettre_favicon(favicon(images[32])):
        print('favicon mis à jour :', PAGE)
    if '--apercu' in argv:
        apercu(images, argv[argv.index('--apercu') + 1])
        print('aperçu :', argv[argv.index('--apercu') + 1])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
