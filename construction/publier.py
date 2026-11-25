"""Publie une version de MyPhotos sur GitHub, en une commande, dans l'ordre sûr.

    construction\\publier.ps1 --essai     tout jusqu'aux contrôles, rien n'est envoyé (étiquette, Release)
    construction\\publier.ps1             publication réelle

Avant : la version est dans myphotos/config.py, ses nouveautés dans construction/nouveautes.json, ses notes dans
dist\\NOTES-<version>.md (écrites à la main ; les repères {SHA_INST}, {SHA_EXE}, {SHA_ZIP}, {SHA_JSON}, {TAILLE_INST},
{TAILLE_EXE}, {TAILLE_ZIP}, {DATE_DEFENDER}, {DATE_DEFENDER_EN} sont remplis ici), tout est commité et envoyé sur
main, et la clé privée de signature est sur ce PC (construction/cle_signature.py).

Étapes :
1. contrôles : branche main propre et identique à origin/main, étiquette v<version> absente (ici et sur GitHub),
   nouveautés et notes présentes, clé privée qui correspond à MyPhotos, gh connecté ;
2. construction : MyPhotos.exe, version dossier, paquet (zip, licences), installateur, manifeste signé ;
3. contrôles des fichiers : empreintes .sha256, manifeste relu avec la clé publique de MyPhotos et conforme à
   l'installateur, analyse Microsoft Defender des quatre fichiers ; notes complétées (tailles, empreintes) ;
4. (sauf --essai) étiquette annotée v<version> envoyée, Release créée en BROUILLON avec ses 8 fichiers (le manifeste
   doit y être avant qu'elle devienne la dernière version), tailles vérifiées, puis publiée comme « latest » ;
5. (sauf --essai) vérification en ligne : fichiers retéléchargés et comparés, manifeste lu par MyPhotos lui-même
   (mise_a_jour.chercher) sur releases/latest.

Arrêt au premier problème, avec ce qu'il faut faire. Rien n'est jamais forcé (pas de --force, pas d'écrasement).
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
ICI = RACINE / 'construction'
DIST = RACINE / 'dist'
DEPOT = 'glenac/MyPhotos'
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(ICI))

FICHIERS = ('MyPhotos-{v}-installation.exe', 'MyPhotos.exe', 'MyPhotos-{v}-windows.zip', 'MyPhotos-mise-a-jour.json')
REPERES = ('{SHA_INST}', '{SHA_EXE}', '{SHA_ZIP}', '{SHA_JSON}', '{TAILLE_INST}', '{TAILLE_EXE}', '{TAILLE_ZIP}',
           '{DATE_DEFENDER}', '{DATE_DEFENDER_EN}')


class Arret(SystemExit):
    pass


def etape(texte):
    print(f'\n=== {texte}', flush=True)


def lancer(*commande, capture=True, cwd=RACINE):
    r = subprocess.run([str(c) for c in commande], cwd=str(cwd), capture_output=capture, text=True,
                       encoding='utf-8', errors='replace')
    if r.returncode != 0:
        sortie = ((r.stdout or '') + (r.stderr or '')).strip()[-2000:] if capture else ''
        raise Arret(f'ARRÊT : {" ".join(map(str, commande[:4]))}… a échoué (code {r.returncode})\n{sortie}')
    return (r.stdout or '').strip() if capture else ''


def sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def noms(version):
    return [f.format(v=version) for f in FICHIERS]


def completer_notes(texte, version, dist=DIST, date=None):
    """Remplit les repères des notes (tailles en Mo décimaux, empreintes, date de l'analyse Defender)."""
    inst, exe, zip_, manifeste = (dist / n for n in noms(version))
    date = date or time.strftime('%Y-%m-%d')
    a, m, j = date.split('-')
    valeurs = {'{SHA_INST}': sha256(inst), '{SHA_EXE}': sha256(exe), '{SHA_ZIP}': sha256(zip_),
               '{SHA_JSON}': sha256(manifeste),
               '{TAILLE_INST}': str(round(inst.stat().st_size / 1e6)), '{TAILLE_EXE}': str(round(exe.stat().st_size / 1e6)),
               '{TAILLE_ZIP}': str(round(zip_.stat().st_size / 1e6)),
               '{DATE_DEFENDER}': f'analyse du {j}/{m}/{a} des quatre fichiers',
               '{DATE_DEFENDER_EN}': f'scan of the four files, {date}'}
    for k, v in valeurs.items():
        texte = texte.replace(k, v)
    reste = re.findall(r'\{[A-Z_]+\}', texte)
    if reste:
        raise Arret(f'ARRÊT : repères inconnus dans les notes : {sorted(set(reste))}')
    return texte


def controles_prealables(version, essai):
    from myphotos import config
    etape(f'Contrôles avant de publier MyPhotos {version}' + (' (essai : rien ne sera envoyé)' if essai else ''))
    branche = lancer('git', 'rev-parse', '--abbrev-ref', 'HEAD')
    if branche != 'main':
        raise Arret(f'ARRÊT : on publie depuis main (branche actuelle : {branche})')
    if lancer('git', 'status', '--porcelain'):
        raise Arret('ARRÊT : des fichiers ne sont pas commités')
    lancer('git', 'fetch', '--quiet', 'origin', 'main')
    if lancer('git', 'rev-parse', 'HEAD') != lancer('git', 'rev-parse', 'origin/main'):
        if not essai:
            raise Arret('ARRÊT : main n\'est pas identique à origin/main (envoyer d\'abord, ou récupérer)')
        print('Attention : main n\'est pas encore envoyé sur GitHub (permis en essai seulement)')
    etiquette = f'v{version}'
    if lancer('git', 'tag', '--list', etiquette):
        raise Arret(f'ARRÊT : l\'étiquette {etiquette} existe déjà ici')
    if lancer('git', 'ls-remote', '--tags', 'origin', f'refs/tags/{etiquette}'):
        raise Arret(f'ARRÊT : l\'étiquette {etiquette} existe déjà sur GitHub')
    if lancer('git', 'branch', '--list', etiquette):
        raise Arret(f'ARRÊT : une branche porte le nom {etiquette} (ambiguë avec l\'étiquette) : la renommer')
    import construire
    construire.nouveautes(version)                   # arrêt si absentes
    notes = DIST / f'NOTES-{version}.md'
    if not notes.is_file():
        raise Arret(f'ARRÊT : {notes} est absent (notes de version à écrire, avec les repères {{SHA_INST}}…)')
    import cle_signature
    cle_signature.verifier_correspondance(cle_signature.lire_cle_privee())
    if not essai:
        lancer('gh', 'auth', 'status')
    print(f'OK : main = origin/main ({lancer("git", "rev-parse", "--short", "HEAD")}), {etiquette} libre, '
          f'nouveautés et notes présentes, clé de signature conforme ({config.VERSION})')
    return notes


def construire_tout():
    etape('Construction (exe, version dossier, paquet, installateur, manifeste signé)')
    script = ICI / 'construire.py'
    for options in ([], ['--dossier'], ['--paquet'], ['--installateur'], ['--manifeste']):
        print('construire.py', *options, flush=True)
        lancer(sys.executable, script, *options, capture=False)


def controles_fichiers(version):
    from myphotos import mise_a_jour
    etape('Contrôles des fichiers')
    for n in noms(version):
        f = DIST / n
        attendue = (DIST / (n + '.sha256')).read_text(encoding='ascii').split()[0]
        if sha256(f) != attendue:
            raise Arret(f'ARRÊT : {n} ne correspond pas à son .sha256')
        print(f'{n} : {f.stat().st_size:,} octets'.replace(',', ' '))
    info = mise_a_jour.lire_manifeste((DIST / 'MyPhotos-mise-a-jour.json').read_bytes())
    if info['version'] != version or not mise_a_jour.conforme(DIST / noms(version)[0], info):
        raise Arret('ARRÊT : le manifeste ne correspond pas à l\'installateur')
    print('manifeste : signature vérifiée avec la clé de MyPhotos, conforme à l\'installateur')
    mpcmd = Path(r'C:\Program Files\Windows Defender\MpCmdRun.exe')
    if not mpcmd.is_file():
        raise Arret('ARRÊT : Microsoft Defender (MpCmdRun.exe) introuvable')
    for n in noms(version):
        r = subprocess.run([str(mpcmd), '-Scan', '-ScanType', '3', '-File', str(DIST / n), '-DisableRemediation'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode != 0 or 'found no threats' not in (r.stdout or ''):
            raise Arret(f'ARRÊT : Defender signale {n} (code {r.returncode}) : ne rien publier\n{r.stdout[-800:]}')
    print('Defender : aucune menace dans les quatre fichiers')


def publier(version, notes_finales):
    etiquette = f'v{version}'
    etape(f'Étiquette {etiquette} et Release en brouillon')
    lancer('git', 'tag', '-a', etiquette, '-m', f'MyPhotos {version}')
    lancer('git', 'push', 'origin', f'refs/tags/{etiquette}:refs/tags/{etiquette}')
    fichiers = [p for n in noms(version) for p in (DIST / n, DIST / (n + '.sha256'))]
    lancer('gh', 'release', 'create', etiquette, '-R', DEPOT, '--draft', '--verify-tag', '--title', f'MyPhotos {version}',
           '--notes-file', notes_finales, *fichiers)
    vus = json.loads(lancer('gh', 'release', 'view', etiquette, '-R', DEPOT, '--json', 'isDraft,assets'))
    tailles = {a['name']: a['size'] for a in vus['assets']}
    for p in fichiers:
        if tailles.get(p.name) != p.stat().st_size:
            raise Arret(f'ARRÊT : {p.name} mal envoyé (brouillon gardé, rien de publié) : {tailles.get(p.name)}')
    print(f'brouillon complet : {len(tailles)} fichiers')
    etape('Publication')
    lancer('gh', 'release', 'edit', etiquette, '-R', DEPOT, '--draft=false', '--latest')
    derniere = lancer('gh', 'api', f'repos/{DEPOT}/releases/latest', '--jq', '.tag_name')
    if derniere != etiquette:
        raise Arret(f'ARRÊT : la dernière version sur GitHub est {derniere}, pas {etiquette}')
    print(f'publiée : https://github.com/{DEPOT}/releases/tag/{etiquette}')


def verifier_en_ligne(version):
    from myphotos import mise_a_jour
    etiquette = f'v{version}'
    etape('Vérification en ligne')
    with tempfile.TemporaryDirectory(prefix='myphotos-verif-') as d:
        lancer('gh', 'release', 'download', etiquette, '-R', DEPOT, '--dir', d)
        for n in noms(version):
            if sha256(Path(d) / n) != sha256(DIST / n):
                raise Arret(f'ARRÊT : {n} téléchargé depuis GitHub diffère du fichier construit')
    print('fichiers retéléchargés : identiques')
    for essai in range(6):                           # le lien « latest » peut mettre quelques secondes à suivre
        info = mise_a_jour.chercher()
        if info and info['version'] == version:
            break
        time.sleep(10)
    else:
        raise Arret('ARRÊT : releases/latest ne donne pas encore le bon manifeste')
    print(f'manifeste lu par MyPhotos sur releases/latest : {info["version"]}, signature vérifiée')


def main(argv):
    from myphotos import config
    essai = '--essai' in argv
    version = config.VERSION
    notes = controles_prealables(version, essai)
    construire_tout()
    controles_fichiers(version)
    finales = DIST / f'NOTES-{version}.final.md'
    finales.write_text(completer_notes(notes.read_text(encoding='utf-8'), version), encoding='utf-8', newline='\n')
    print(f'notes complétées : {finales}')
    if essai:
        etape('Essai terminé : rien n\'a été envoyé')
        return 0
    publier(version, finales)
    verifier_en_ligne(version)
    etape(f'MyPhotos {version} publiée et vérifiée')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Arret as e:
        print(e, file=sys.stderr)
        sys.exit(1)
