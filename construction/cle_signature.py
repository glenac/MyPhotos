"""Clé de signature des mises à jour de MyPhotos (côté publication seulement).

    .venv\\Scripts\\python.exe construction\\cle_signature.py --creer      (une seule fois : crée la clé du projet)
    .venv\\Scripts\\python.exe construction\\cle_signature.py --verifier   (la clé privée correspond-elle à MyPhotos ?)

La clé privée signe le manifeste de chaque version publiée ; MyPhotos ne contient que la clé publique
(config.CLE_MISE_A_JOUR) et refuse toute mise à jour qui n'est pas signée par cette clé.

La clé privée n'est JAMAIS dans le dépôt : par défaut %APPDATA%\\MyPhotos-publication\\cle-signature-privee.txt,
ou le fichier désigné par MYPHOTOS_CLE_SIGNATURE. En garder une copie hors de ce PC (clé USB rangée) : perdue, plus
aucune mise à jour en un clic n'est possible (il faudrait publier une version avec une nouvelle clé, que chacun
installerait à la main). Volée, quelqu'un qui aurait aussi le compte GitHub pourrait publier une fausse mise à jour.
"""
import base64
import os
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from myphotos import config, signature  # noqa: E402

ENTETE = '# MyPhotos : clé PRIVÉE de signature des mises à jour. Ne jamais publier, ne jamais mettre dans le dépôt.'


def chemin_cle():
    if os.environ.get('MYPHOTOS_CLE_SIGNATURE'):
        return Path(os.environ['MYPHOTOS_CLE_SIGNATURE'])
    return Path(os.environ.get('APPDATA') or Path.home()) / 'MyPhotos-publication' / 'cle-signature-privee.txt'


def _dans_le_depot(p):
    try:
        p.resolve().relative_to(RACINE.resolve())
        return True
    except ValueError:
        return False


def lire_cle_privee(p=None):
    p = Path(p) if p else chemin_cle()
    if _dans_le_depot(p):
        raise SystemExit(f'ARRÊT : la clé privée ne doit pas être dans le dépôt ({p})')
    lignes = [x.strip() for x in p.read_text(encoding='utf-8').splitlines() if x.strip() and not x.startswith('#')]
    if len(lignes) != 1:
        raise SystemExit(f'ARRÊT : fichier de clé illisible ({p})')
    cle = base64.b64decode(lignes[0], validate=True)
    if len(cle) != 96:
        raise SystemExit(f'ARRÊT : clé privée de {len(cle)} octets au lieu de 96 ({p})')
    return cle


def verifier_correspondance(privee):
    """ARRÊT si la clé privée ne correspond pas à la clé publique contenue dans MyPhotos."""
    essai = b'essai de correspondance des cles'
    if privee[:64] != config.cle_mise_a_jour() or not signature.verifier(
            essai, signature.signer(essai, privee), config.cle_mise_a_jour()):
        raise SystemExit('ARRÊT : cette clé privée ne correspond pas à la clé publique de MyPhotos '
                         '(config.CLE_MISE_A_JOUR) : les mises à jour seraient refusées')


def creer():
    p = chemin_cle()
    if p.exists():
        raise SystemExit(f'ARRÊT : une clé existe déjà ({p}) ; on ne la remplace jamais')
    if _dans_le_depot(p):
        raise SystemExit(f'ARRÊT : la clé privée ne doit pas être dans le dépôt ({p})')
    privee, publique = signature.nouvelle_cle()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'x', encoding='utf-8', newline='\n') as f:       # 'x' : jamais d'écrasement
        f.write(ENTETE + '\n' + base64.b64encode(privee).decode('ascii') + '\n')
    print(f'Clé privée écrite : {p}')
    print('À recopier dans myphotos/config.py :')
    print(f"CLE_MISE_A_JOUR = '{base64.b64encode(publique).decode('ascii')}'")


def main(args):
    if args == ['--creer']:
        creer()
    elif args == ['--verifier']:
        verifier_correspondance(lire_cle_privee())
        print(f'OK : {chemin_cle()} correspond à la clé publique de MyPhotos')
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
