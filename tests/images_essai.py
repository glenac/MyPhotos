"""Images d'essai générées par Pillow (jamais de vraies photos) : formats, EXIF et cas limites.

Utilisé par test_coeur.py et test_modele_reel.py.
"""
import ctypes
import os
import random
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw                       # noqa: E402

from myphotos.base import chemin_systeme             # noqa: E402

CACHE, SYSTEME = 0x2, 0x4
ROUGE, VERT, BLEU = (210, 25, 25), (25, 190, 40), (30, 50, 210)
DATE_EXIF = '2021:07:14 15:30:00'
GPS = {1: 'N', 2: (48.0, 51.0, 24.45), 3: 'E', 4: (2.0, 21.0, 3.6)}      # 48,8568 N  2,3510 E


def attribut(chemin, valeur):
    """Pose un attribut Windows (caché, système) sur un fichier ou un dossier."""
    if os.name == 'nt':
        ctypes.windll.kernel32.SetFileAttributesW(chemin_systeme(str(chemin)), valeur)


def date_fichier(chemin, quand):
    os.utime(chemin_systeme(str(chemin)), (quand, quand))


def jpeg(chemin, taille=(400, 300), couleur=ROUGE, date=None, date_simple=None, orientation=None, gps=None,
         quand=None, bruit=False):
    """JPEG uni (petit carré clair dans un coin), avec les EXIF demandés."""
    chemin = Path(chemin)
    os.makedirs(chemin_systeme(str(chemin.parent)), exist_ok=True)
    im = Image.new('RGB', taille, couleur)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, taille[0] // 10, taille[1] // 10), fill=(240, 240, 240))
    if bruit:                                           # du détail, pour qu'un fichier tronqué le soit vraiment
        rnd = random.Random(7)
        for _ in range(400):
            x, y = rnd.randrange(taille[0]), rnd.randrange(taille[1])
            d.ellipse((x, y, x + 12, y + 12), fill=tuple(rnd.randrange(256) for _ in range(3)))
    exif = Image.Exif()
    if orientation:
        exif[0x0112] = orientation
    if date:
        exif.get_ifd(0x8769)[36867] = date
    if date_simple:
        exif[306] = date_simple
    if gps:
        exif.get_ifd(0x8825).update(gps)
    im.save(chemin_systeme(str(chemin)), 'JPEG', quality=90, exif=exif.tobytes())
    if quand:
        date_fichier(chemin, quand)
    return chemin


def creer_jeu(racine, serie=20):
    """Crée le jeu d'essai sous `racine`. Renvoie un dict : chemins utiles et nombres attendus."""
    racine = Path(racine)
    os.makedirs(racine, exist_ok=True)
    t = time.time() - 86400 * 30                        # dates de fichier : il y a un mois, puis de plus en plus récentes
    c = {}
    c['exif'] = jpeg(racine / 'vacances' / 'rouge_exif.jpg', couleur=ROUGE, date=DATE_EXIF, orientation=6, quand=t)
    c['datetime'] = jpeg(racine / 'vacances' / 'vert_datetime.jpg', (300, 200), VERT,
                         date_simple='2019:01:02 03:04:05', quand=t + 1)
    c['gps'] = jpeg(racine / 'vacances' / 'bleu_gps.jpg', (320, 240), BLEU, gps=GPS, quand=t + 2)
    c['absurde'] = jpeg(racine / 'vacances' / 'date_absurde.jpg', (320, 240), (120, 120, 120),
                        date='0000:00:00 00:00:00', quand=t + 3)
    # PNG transparent : moitié gauche bleue, moitié droite transparente (doit devenir blanche).
    png = Image.new('RGBA', (200, 150), (0, 0, 0, 0))
    png.paste((30, 50, 210, 255), (0, 0, 100, 150))
    c['png'] = racine / 'transparent.png'
    png.save(c['png'])
    date_fichier(c['png'], t + 4)
    c['icone'] = racine / 'icone.png'
    Image.new('RGB', (50, 50), ROUGE).save(c['icone'])
    c['casse'] = racine / 'casse.jpg'
    c['casse'].write_bytes(bytes(random.Random(3).randrange(256) for _ in range(6000)))
    # JPEG tronqué aux deux tiers (copie interrompue) : doit rester lisible.
    c['tronquee'] = jpeg(racine / 'tronquee.jpg', (400, 300), VERT, bruit=True, quand=t + 5)
    donnees = c['tronquee'].read_bytes()
    c['tronquee'].write_bytes(donnees[:len(donnees) * 2 // 3])
    date_fichier(c['tronquee'], t + 5)
    c['unicode'] = jpeg(racine / 'été à Noël – 東京 🎄.jpg', (300, 300), ROUGE, quand=t + 6)
    c['grand'] = jpeg(racine / 'grand.jpg', (2000, 1500), BLEU, quand=t + 7)
    longue = racine / 'long' / ('d' * 60) / ('e' * 60) / ('f' * 60) / ('g' * 60) / 'photo au chemin très long.jpg'
    c['long'] = jpeg(longue, (300, 200), VERT, quand=t + 8)
    for i in range(serie):
        couleur = (ROUGE, VERT, BLEU)[i % 3]
        c[f'serie{i}'] = jpeg(racine / 'serie' / f'img_{i:02d}.jpg', (240, 180), couleur, quand=t + 100 + i)
    # À ignorer : dossiers cachés, système, corbeille, vignettes de NAS, fichier caché, autre extension.
    jpeg(racine / '.cache' / 'point.jpg')
    jpeg(racine / 'Cache' / 'cachee.jpg')
    attribut(racine / 'Cache', CACHE)
    jpeg(racine / 'Systeme' / 'systeme.jpg')
    attribut(racine / 'Systeme', SYSTEME)
    jpeg(racine / '$RECYCLE.BIN' / 'poubelle.jpg')
    jpeg(racine / '@eaDir' / 'vignette_nas.jpg')
    jpeg(racine / 'fichier_cache.jpg')
    attribut(racine / 'fichier_cache.jpg', CACHE)
    (racine / 'notes.txt').write_text('pas une image', encoding='utf-8')
    (racine / '._mac.jpg').write_bytes(b'\x00\x05\x16\x07')
    # Boucle : jonction qui pointe vers la racine (ne doit pas être suivie).
    c['jonction'] = None
    if os.name == 'nt':
        try:
            import _winapi
            _winapi.CreateJunction(str(racine), str(racine / 'boucle'))
            c['jonction'] = racine / 'boucle'
        except OSError:
            pass
    c['symlien'] = None
    try:
        os.symlink(str(racine / 'serie'), str(racine / 'lien_serie'), target_is_directory=True)
        c['symlien'] = racine / 'lien_serie'
    except (OSError, NotImplementedError):
        pass                                            # droits insuffisants : essai sauté
    # Photos connues (analysées ou en erreur) : 4 « vacances », png, icône, cassé, tronqué, unicode, grand, long.
    c['attendues'] = 11 + serie
    c['erreurs'] = 2                                    # icône (trop petite) et fichier cassé
    return c


def nettoyer(dossier):
    """Efface un dossier temporaire d'essai (chemins longs compris, jonctions non suivies)."""
    dossier = Path(dossier)
    for p in (dossier / 'boucle', dossier / 'lien_serie', dossier / 'photos' / 'boucle', dossier / 'photos' / 'lien_serie'):
        try:
            if p.is_junction():
                os.rmdir(p)                             # retire le lien seul, pas ce qu'il désigne
            elif p.is_symlink():
                os.unlink(p)
        except OSError:
            pass
    shutil.rmtree(chemin_systeme(str(dossier)), ignore_errors=True)
