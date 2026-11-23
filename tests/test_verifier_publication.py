"""Tests du garde-fou de publication (outils/verifier_publication.py).

Toutes les données sont fausses. Les faux secrets, e-mails et chemins sont
assemblés à l'exécution : ce fichier ne déclenche donc pas lui-même le
garde-fou quand il est commité (voir test_le_garde_fou_ne_se_declenche_pas_lui_meme).
"""
import contextlib
import importlib.util
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / 'outils' / 'verifier_publication.py'


def _charger():
    spec = importlib.util.spec_from_file_location('verifier_publication', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vp = _charger()

# Fausses valeurs, assemblées à l'exécution.
CORPS = 'Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3hJ5'          # 36 caractères au hasard
FAUX_NOM = 'zorg' + 'lub'
FAUX_EMAIL = 'jean.' + FAUX_NOM + '@' + 'fournisseur.fr'
JETON = 'gh' + 'p_' + CORPS
FAUX_SECRETS = [
    ('clé Anthropic', 'sk-' + 'ant-' + 'api03-' + CORPS),
    ('clé OpenAI', 'sk-' + 'proj-' + CORPS),
    ('jeton GitHub', JETON),
    ('jeton GitHub', 'gh' + 'o_' + CORPS),
    ('jeton GitHub', 'github' + '_pat_' + '11AAAAAAA0' + CORPS),
    ('clé AWS', 'AK' + 'IA' + 'Z7Q4K2M9W3X8B5N1'),
    ('clé privée', '-----BEGIN ' + 'OPENSSH PRIVATE KEY-----'),
    ('jeton Slack', 'xo' + 'xb-' + '123456789012-' + 'AbCdEfGhIjKl'),
    ('clé Google', 'AI' + 'za' + 'Sy' + CORPS[:33]),
    ('jeton Hugging Face', 'h' + 'f_' + CORPS[:34]),
]


def categories(alertes):
    return [a.categorie for a in alertes]


class TestMotifsGeneriques(unittest.TestCase):
    def setUp(self):
        self.verif = vp.Verificateur()

    def alertes(self, ligne, genre='config'):
        return self.verif.analyser_ligne(ligne, 'f:1', genre)

    def test_chemins_de_profil(self):
        for ligne in ('C:' + '\\Users\\' + FAUX_NOM + '\\Documents',
                      '"C:' + '\\\\Users\\\\' + FAUX_NOM + '\\\\AppData"',      # échappé (JSON)
                      'c:' + '/users/' + FAUX_NOM,
                      'cd /c' + '/Users/' + FAUX_NOM + '/projets',             # Git Bash
                      'cat /home' + '/' + FAUX_NOM + '/.ssh/config',
                      'open /Users' + '/' + FAUX_NOM + '/Desktop'):
            with self.subTest(ligne=ligne):
                alertes = self.alertes(ligne)
                self.assertEqual([vp.CAT_PROFIL], categories(alertes))
                self.assertNotIn(FAUX_NOM, alertes[0].extrait)

    def test_profils_generiques_ignores(self):
        for ligne in ('C:\\Users\\<nom>\\Documents', 'C:\\Users\\Public\\Documents',
                      '%USERPROFILE%\\Documents', 'C:\\Users\\%USERNAME%\\x',
                      '/home/runner/work', '/api/users/42'):
            with self.subTest(ligne=ligne):
                self.assertEqual([], self.alertes(ligne))

    def test_dossiers_de_photos(self):
        for ligne in ('F:' + '\\Images\\2019\\a.jpg', 'G:' + '/Mes photos/Vacances',
                      'cd /f' + '/Photos/2020', 'H:' + '\\Sauvegarde\\Pictures',
                      'dossier = r"F:' + '\\Phototh\u00e8que"'):
            with self.subTest(ligne=ligne):
                self.assertEqual([vp.CAT_PHOTOS], categories(self.alertes(ligne)))
        for ligne in ('D:\\projet\\images.py', 'Q:\\inexistant\\MyPhotos', 'D:\\Photoshop\\x',
                      'https://exemple.org/images/x.png', 'src="/static/photos/a.png"'):
            with self.subTest(ligne=ligne):
                self.assertEqual([], self.alertes(ligne))

    def test_emails(self):
        alertes = self.alertes('Contact : ' + FAUX_EMAIL)
        self.assertEqual([vp.CAT_EMAIL], categories(alertes))
        self.assertNotIn(FAUX_NOM, alertes[0].extrait)
        for ligne in ('12345+essai@users.noreply.github.com', 'noreply@fournisseur.fr',
                      'contact@example.com', 'vous@exemple.fr', 'git@github.com:glenac/MyPhotos.git',
                      'icone@2x.png'):
            with self.subTest(ligne=ligne):
                self.assertEqual([], self.alertes(ligne))

    def test_secrets(self):
        for nom, valeur in FAUX_SECRETS:
            with self.subTest(nom=nom):
                alertes = self.alertes('cle = "' + valeur + '"', 'code')
                self.assertEqual([f'{vp.CAT_SECRET} ({nom})'], categories(alertes))
                if nom != 'clé privée':
                    self.assertNotIn(valeur[6:], alertes[0].extrait)
        self.assertEqual([], self.alertes('cle = "' + 'gh' + 'p_' + 'x' * 36 + '"'))

    def test_mots_de_passe(self):
        signales = [('pass' + 'word = "' + 'Tr0ub4dor&3' + '"', 'code'),
                    ('"pass' + 'word": "' + 's3cr3t!' + '"', 'config'),
                    ('pass' + 'word: azerty', 'config'),
                    ('Le mot de ' + 'passe : hunter22', 'prose')]
        for ligne, genre in signales:
            with self.subTest(ligne=ligne):
                alertes = self.alertes(ligne, genre)
                self.assertEqual([vp.CAT_MDP], categories(alertes))
                self.assertNotIn('Tr0ub', alertes[0].extrait)
        ignores = [('mot_de_' + 'passe = saisie_utilisateur', 'code'),
                   ('pass' + 'word = ""', 'code'),
                   ('pass' + 'word = os.environ["MDP"]', 'code'),
                   ('pass' + 'word = "<votre mot de passe>"', 'code'),
                   ('pass' + 'word: str = None', 'code'),
                   ('Aucun mot de ' + 'passe : MyPhotos reste local.', 'prose'),
                   ('pass' + 'word = ********', 'config')]
        for ligne, genre in ignores:
            with self.subTest(ligne=ligne):
                self.assertEqual([], self.alertes(ligne, genre))

    def test_identifiants_dans_une_url(self):
        alertes = self.alertes('ftp://' + 'alice:s3cr3t' + '@ftp.fournisseur.fr/www')
        self.assertEqual([vp.CAT_URL], categories(alertes))
        self.assertNotIn('s3cr3t', alertes[0].extrait)
        self.assertEqual([], self.alertes('https://user:password@exemple.net'))

    def test_adresses_ip(self):
        self.assertEqual([vp.CAT_IP], categories(self.alertes('hote = "192.168.' + '1.20"')))
        self.assertEqual([vp.CAT_IP], categories(self.alertes('http://10.0.' + '0.5:8080/')))
        for ligne in ('127.0.0.1', '0.0.0.0', '127.0.0.2', 'FileVersion 1.0.0.0', '192.0.2.10',
                      'version 1.2.3.4.5', 'masque 255.255.255.0'):
            with self.subTest(ligne=ligne):
                self.assertEqual([], self.alertes(ligne))

    def test_marqueur_publication_ok(self):
        self.assertEqual([], self.alertes(FAUX_EMAIL + '  # publication: ok'))
        self.assertEqual([], self.alertes(JETON + '  <!-- Publication : OK -->'))

    def test_valeur_d_exemple(self):
        verif = vp.Verificateur(autorises=vp.Autorisations(exemples=['F:' + '\\Images']))
        self.assertEqual([], verif.analyser_ligne('F:' + '/images/2019', 'f:1'))
        self.assertEqual([vp.CAT_PHOTOS], categories(verif.analyser_ligne('G:' + '\\Images', 'f:1')))

    def test_masquage(self):
        self.assertEqual('s********', vp.masquer('secret1234'))
        self.assertEqual('***', vp.masquer('abc'))
        self.assertEqual('', vp.masquer(''))


class TestMotsInterdits(unittest.TestCase):
    FICHIER = '\n'.join(['# commentaire', 'Zorgl\u00fcb', 'mot:K\u00e9o', 're:b[i1]dule\\d+',
                         '!K\u00e9o Studio-Photo', 'F:\\Quux Corp', 'ab', ''])

    def setUp(self):
        self.interdits = vp.MotsInterdits.depuis_texte(self.FICHIER)
        self.verif = vp.Verificateur(self.interdits)

    def numeros(self, texte):
        return [numero for numero, _ in self.interdits.chercher(texte)]

    def test_lecture(self):
        self.assertEqual(4, len(self.interdits.motifs))
        self.assertEqual(1, len(self.interdits.avertissements))       # « ab » : trop court

    def test_casse_accents_et_separateurs(self):
        self.assertEqual([2], self.numeros('Merci à ZORGLUB'))
        self.assertEqual([2], self.numeros('zorgl\u00fcb'))
        self.assertEqual([6], self.numeros('f:/quux corp/a.txt'))
        self.assertEqual([6], self.numeros('"F:\\\\QUUX  Corp\\\\b"'))

    def test_mot_entier_regex_et_exception(self):
        self.assertEqual([3], self.numeros('Keo est là'))
        self.assertEqual([3], self.numeros('keo_sauvegarde'))
        self.assertEqual([], self.numeros('Kéops'))
        self.assertEqual([], self.numeros('crédit : Kéo Studio-Photo'))
        self.assertEqual([4], self.numeros('B1DULE42'))

    def test_alerte_masquee_et_jamais_levee_par_les_exemples(self):
        verif = vp.Verificateur(self.interdits, vp.Autorisations(exemples=['Zorgl\u00fcb']))
        alertes = verif.analyser_ligne('auteur = "Zorglub"', 'f:3', 'code')
        self.assertEqual([f'{vp.CAT_INTERDIT} #2'], categories(alertes))
        self.assertNotIn('orglub', alertes[0].extrait.casefold())
        self.assertEqual([], verif.analyser_ligne('auteur = "Zorglub"  # publication: ok', 'f:3'))

    def test_nom_de_fichier(self):
        alertes = self.verif.verifier_fichier('photos/Zorglub/a.txt', 10)
        self.assertEqual([f'{vp.CAT_INTERDIT} #2 (dans le nom)'], categories(alertes))

    def test_fichier_absent(self):
        interdits = vp.MotsInterdits.lire(str(RACINE / 'inexistant.txt'))
        self.assertFalse(interdits.present)
        self.assertEqual([], interdits.chercher('Zorglub'))


class TestFichiers(unittest.TestCase):
    def setUp(self):
        autorises = vp.Autorisations.depuis_texte('# images\ndocs/captures/*.png\nconstruction/MyPhotos.ico\n'
                                                  'exemple: D:\\Photos\n')
        self.verif = vp.Verificateur(autorises=autorises)

    def categories(self, chemin, taille=10):
        return categories(self.verif.verifier_fichier(chemin, taille))

    def test_types_interdits(self):
        attendus = {'index.db': vp.CAT_BASE, 'donnees/miniatures.sqlite3': vp.CAT_BASE,
                    'Thumbs.db': vp.CAT_BASE, 'moteur/model.onnx': vp.CAT_MODELE,
                    'moteur/model.onnx.data': vp.CAT_MODELE, 'vecteurs.npy': vp.CAT_MODELE,
                    'sauvegarde.zip': vp.CAT_ARCHIVE, 'dist/MyPhotos.exe': vp.CAT_EXE,
                    '.env': vp.CAT_CLES, 'cle.pem': vp.CAT_CLES, 'sftp.json': vp.CAT_CLES,
                    'photo.JPG': vp.CAT_MEDIA, 'docs/video.mp4': vp.CAT_MEDIA,
                    'docs/schema.svg': vp.CAT_MEDIA}
        for chemin, categorie in attendus.items():
            with self.subTest(chemin=chemin):
                self.assertEqual([categorie], self.categories(chemin))
        for chemin in ('.env.example', 'myphotos/base.py', 'README.md', 'page/index.html'):
            with self.subTest(chemin=chemin):
                self.assertEqual([], self.categories(chemin))

    def test_images_autorisees(self):
        self.assertEqual([], self.categories('docs/captures/accueil.png'))
        self.assertEqual([], self.categories('construction/MyPhotos.ico'))
        self.assertEqual([vp.CAT_MEDIA], self.categories('docs/autre/accueil.png'))

    def test_taille(self):
        self.assertEqual([], self.categories('jeu.json', vp.LIMITE_TAILLE))
        self.assertEqual([vp.CAT_TAILLE], self.categories('jeu.json', vp.LIMITE_TAILLE + 1))
        # L'autorisation d'une image ne lève pas la limite de taille.
        self.assertEqual([vp.CAT_TAILLE], self.categories('docs/captures/a.png', 3 * vp.LIMITE_TAILLE))

    def test_contenu_binaire_ignore(self):
        self.assertEqual([], self.verif.analyser_contenu(b'\x00\x01' + FAUX_EMAIL.encode(), 'a.bin'))
        texte = ('ligne 1\r\n' + FAUX_EMAIL + '\r\n').encode('utf-16')
        self.assertEqual(['a.txt:2'], [a.lieu for a in self.verif.analyser_contenu(texte, 'a.txt')])

    def test_le_garde_fou_ne_se_declenche_pas_lui_meme(self):
        verif = vp.Verificateur(autorises=vp.Autorisations.lire(str(RACINE / '.publication-autorises')))
        for chemin in (SCRIPT, Path(__file__), RACINE / '.githooks' / 'pre-commit',
                       RACINE / '.publication-autorises'):
            if chemin.is_file():
                with self.subTest(fichier=chemin.name):
                    alertes = verif.analyser_contenu(chemin.read_bytes(), chemin.name)
                    self.assertEqual([], [str(a) for a in alertes])


def _supprimer(dossier):
    def reessayer(fonction, chemin, _erreur):
        os.chmod(chemin, stat.S_IWRITE)          # objets git en lecture seule sous Windows
        fonction(chemin)
    if sys.version_info >= (3, 12):
        shutil.rmtree(dossier, onexc=reessayer)
    else:
        shutil.rmtree(dossier, onerror=reessayer)


@unittest.skipUnless(shutil.which('git'), 'git introuvable')
class TestDepotGit(unittest.TestCase):
    """Dépôts git jetables, isolés de la configuration git de la machine."""

    def setUp(self):
        self.dossier = tempfile.mkdtemp(prefix='verif-publication-')
        self.depot = os.path.join(self.dossier, 'depot')
        os.mkdir(self.depot)
        config_vide = os.path.join(self.dossier, 'gitconfig')
        open(config_vide, 'w').close()
        self.environnement = mock.patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': config_vide, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CEILING_DIRECTORIES': self.dossier})
        self.environnement.start()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Essai')
        self.git('config', 'user.email', 'essai@example.com')

    def tearDown(self):
        self.environnement.stop()
        _supprimer(self.dossier)

    def git(self, *arguments):
        subprocess.run(['git', *arguments], cwd=self.depot, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def ecrire(self, chemin, contenu):
        complet = os.path.join(self.depot, chemin)
        os.makedirs(os.path.dirname(complet), exist_ok=True)
        if isinstance(contenu, str):
            contenu = contenu.encode('utf-8')
        with open(complet, 'wb') as fichier:
            fichier.write(contenu)

    def verifier(self, *options):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(io.StringIO()):
            code = vp.main([*options, '-C', self.depot])
        return code, sortie.getvalue()

    def test_arbre_avec_fichier_non_suivi(self):
        self.ecrire('a.py', "print('bonjour')\n")
        self.assertEqual(0, self.verifier()[0])
        self.ecrire('notes.txt', 'Contact : ' + FAUX_EMAIL + '\n')
        code, sortie = self.verifier()
        self.assertEqual(1, code)
        self.assertIn('notes.txt:1  [e-mail]', sortie)
        self.assertNotIn(FAUX_NOM, sortie)

    def test_index_lit_la_version_indexee(self):
        self.ecrire('a.py', "print('bonjour')\n")
        self.ecrire('b.py', 'cle = "' + JETON + '"\n')
        self.git('add', 'a.py')
        self.assertEqual(0, self.verifier('--index')[0])          # b.py n'est pas indexé
        self.git('add', 'b.py')
        self.ecrire('b.py', 'cle = None\n')                       # corrigé sur le disque seulement
        code, sortie = self.verifier('--index')
        self.assertEqual(1, code)
        self.assertIn('b.py:1  [secret (jeton GitHub)]', sortie)
        self.assertNotIn(CORPS, sortie)

    def test_mot_interdit_local(self):
        with open(os.path.join(self.depot, '.git', 'info', 'publication-interdits.txt'), 'w',
                  encoding='utf-8') as fichier:
            fichier.write('# essai\nZorgl\u00fcb\n')
        self.ecrire('lisezmoi.md', 'Merci à ZORGLUB.\n')
        self.git('add', 'lisezmoi.md')
        code, sortie = self.verifier('--index')
        self.assertEqual(1, code)
        self.assertIn('lisezmoi.md:1  [mot interdit #2]', sortie)
        self.assertNotIn('orglub', sortie.casefold())

    def test_fichiers_interdits_et_images(self):
        self.ecrire('gros.txt', 'a' * (vp.LIMITE_TAILLE + 10))
        self.ecrire('photo.jpg', b'\xff\xd8\xff\xe0' + bytes(100))
        self.ecrire('index.db', b'SQLite format 3\x00')
        self.git('add', '-A')
        code, sortie = self.verifier('--index')
        self.assertEqual(1, code)
        for attendu in ('gros.txt  [fichier trop gros]', 'photo.jpg  [image ou vidéo non autorisée]',
                        'index.db  [base de données]'):
            self.assertIn(attendu, sortie)
        self.ecrire('.publication-autorises', '# essai\nphoto.jpg\n')
        self.assertNotIn('photo.jpg  [', self.verifier('--index')[1])

    def test_historique(self):
        self.ecrire('config.py', 'cle = "' + JETON + '"\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'ajout')
        self.ecrire('config.py', 'cle = None\n')
        self.git('commit', '-q', '-a', '-m', 'retrait')
        self.assertEqual(0, self.verifier()[0])                   # l'arbre actuel est propre
        code, sortie = self.verifier('--historique')
        self.assertEqual(1, code)
        self.assertRegex(sortie, r'[0-9a-f]{8}:config\.py:1  \[secret \(jeton GitHub\)\]')
        self.assertNotIn(CORPS, sortie)

    def test_historique_auteur_message_et_fichiers(self):
        self.ecrire('a.txt', 'bonjour\n')
        self.ecrire('sauvegarde.zip', b'PK\x03\x04')
        self.git('add', '-A')
        self.git('commit', '-q', '--author', 'Jean Essai <' + FAUX_EMAIL + '>',
                 '-m', 'Copie depuis C:' + '\\Users\\' + FAUX_NOM + '\\Bureau')
        self.git('rm', '-q', 'sauvegarde.zip')
        self.git('commit', '-q', '-m', 'retrait')
        code, sortie = self.verifier('--historique')
        self.assertEqual(1, code)
        self.assertIn('(auteur)  [e-mail]', sortie)
        self.assertIn('(message):1  [chemin de profil]', sortie)
        self.assertIn('sauvegarde.zip (objet ', sortie)
        self.assertNotIn(FAUX_NOM, sortie.casefold())

    def test_historique_sans_commit(self):
        self.assertEqual(0, self.verifier('--historique')[0])

    def test_ligne_de_commande(self):
        self.ecrire('a.txt', FAUX_EMAIL + '\n')
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        resultat = subprocess.run([sys.executable, str(SCRIPT)], cwd=self.depot, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(1, resultat.returncode)
        self.assertIn('[e-mail]', resultat.stdout.decode('utf-8'))
        resultat = subprocess.run([sys.executable, str(SCRIPT), '-C', self.dossier], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(2, resultat.returncode)                  # pas un dépôt git


if __name__ == '__main__':
    unittest.main()
