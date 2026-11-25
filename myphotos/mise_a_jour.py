"""Mise à jour proposée de MyPhotos : nouvelle version publiée sur GitHub, installée en un clic.

Seulement avec l'accord de l'utilisateur (question de l'assistant, interrupteur dans « Réglages »), MyPhotos lit une
fois par jour le petit fichier MyPhotos-mise-a-jour.json de la dernière version publiée
(github.com/glenac/MyPhotos/releases/latest/download/…). Rien d'autre n'est envoyé : ni photo, ni recherche, ni
identifiant ; l'en-tête User-Agent ne porte pas même la version.

Ce fichier est une enveloppe : {"format": 1, "manifeste": <base64>, "signature": <base64>}. Le manifeste (JSON) donne
la version, et le nom, la taille et l'empreinte SHA-256 de l'installateur ; il est signé avec la clé privée du projet,
gardée hors du dépôt (construction/cle_signature.py), et vérifié avec la clé publique contenue dans MyPhotos
(signature.py). Tant que la signature n'est pas vérifiée, rien du manifeste n'est lu. Ensuite :
- seule une version plus récente que celle qui tourne est proposée (pas de retour en arrière forcé) ;
- l'adresse de l'installateur est construite ici, à partir de la version signée, jamais lue dans le fichier ;
- l'installateur téléchargé doit avoir exactement la taille et l'empreinte du manifeste, contrôlées encore une fois
  juste avant de le lancer.

Installation : seulement pour la version installée (unins000.exe à côté de MyPhotos.exe). L'installateur est lancé
en silence avec /MISEAJOUR : il attend que MyPhotos se soit fermé (son mutex), installe par-dessus en gardant les
données, puis relance MyPhotos (construction/MyPhotos.iss). MyPhotos.exe seul et les sources n'ont qu'un lien vers la
page de la version.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config, modele, signature

DEPOT = 'glenac/MyPhotos'
NOM_MANIFESTE = 'MyPhotos-mise-a-jour.json'
URL_MANIFESTE = f'https://github.com/{DEPOT}/releases/latest/download/{NOM_MANIFESTE}'
AGENT = 'MyPhotos/1 (logiciel local)'               # le même que pour le moteur : ni version, ni identifiant
DOSSIER = 'mises-a-jour'                             # dans config.DONNEES
TAILLE_MAX_MANIFESTE = 64 * 1024
TAILLE_MIN_INSTALLATEUR = 1 << 20
TAILLE_MAX_INSTALLATEUR = 1 << 30
NOUVEAUTES_MAX = 8
NOUVEAUTE_LONGUEUR_MAX = 300
_VERSION = re.compile(r'(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_DATE = re.compile(r'20[0-9]{2}-[01][0-9]-[0-3][0-9]')
_INSTALLATEUR = re.compile(r'MyPhotos-((?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3}))'
                           r'-installation\.exe(\.part)?')


class Refusee(Exception):
    """Manifeste refusé : signature absente ou fausse, contenu invalide. Rien n'est proposé à l'utilisateur."""


class Indisponible(Exception):
    """Vérification ou téléchargement impossible pour l'instant (pas d'Internet, GitHub ne répond pas…)."""


# ---------------------------------------------------------------- versions et adresses

def version_tuple(v):
    m = _VERSION.fullmatch(v) if isinstance(v, str) else None
    if not m:
        raise ValueError(f'version invalide : {v!r}')
    return tuple(int(x) for x in m.groups())


def plus_recente(a, b):
    """True si la version a est strictement plus récente que b."""
    return version_tuple(a) > version_tuple(b)


def nom_installateur(version):
    return f'MyPhotos-{version}-installation.exe'


def url_installateur(version):
    version_tuple(version)
    return f'https://github.com/{DEPOT}/releases/download/v{version}/{nom_installateur(version)}'


def url_notes(version):
    version_tuple(version)
    return f'https://github.com/{DEPOT}/releases/tag/v{version}'


# ---------------------------------------------------------------- manifeste

def _texte_propre(s):
    """Ligne de nouveautés affichable : texte sans caractères de contrôle, longueur bornée."""
    if not isinstance(s, str):
        raise Refusee('nouveautés invalides')
    s = ''.join(c for c in s if c >= ' ' and c != '\x7f').strip()
    if not s or len(s) > NOUVEAUTE_LONGUEUR_MAX:
        raise Refusee('nouveautés invalides')
    return s


def lire_manifeste(brut, cle=None):
    """Contenu vérifié du fichier MyPhotos-mise-a-jour.json : {version, nom, taille, sha256, nouveautes, date,
    url, notes}. Refusee si la signature n'est pas celle du projet ou si le contenu est invalide."""
    if not isinstance(brut, (bytes, bytearray)) or len(brut) > TAILLE_MAX_MANIFESTE:
        raise Refusee('manifeste absent ou trop gros')
    try:
        enveloppe = json.loads(bytes(brut).decode('utf-8'))
        if not isinstance(enveloppe, dict):
            raise ValueError('enveloppe')
        texte = base64.b64decode(enveloppe['manifeste'], validate=True)
        sig = base64.b64decode(enveloppe['signature'], validate=True)
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, RecursionError):
        raise Refusee('manifeste illisible') from None
    if enveloppe.get('format') != 1:
        raise Refusee('format de manifeste inconnu')
    if not signature.verifier(texte, sig, cle if cle is not None else config.cle_mise_a_jour()):
        raise Refusee('signature invalide')
    # Signé par le projet : on peut le lire, mais on le contrôle quand même entièrement.
    try:
        m = json.loads(texte.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise Refusee('manifeste signé illisible') from None
    if not isinstance(m, dict) or m.get('application') != 'MyPhotos':
        raise Refusee('manifeste d\'une autre application')
    version = m.get('version')
    try:
        version_tuple(version)
    except ValueError:
        raise Refusee('version invalide') from None
    inst = m.get('installateur')
    if not isinstance(inst, dict) or inst.get('nom') != nom_installateur(version):
        raise Refusee('installateur invalide')
    taille, empreinte = inst.get('taille'), inst.get('sha256')
    if (type(taille) is not int or not TAILLE_MIN_INSTALLATEUR <= taille <= TAILLE_MAX_INSTALLATEUR
            or not isinstance(empreinte, str) or not _SHA256.fullmatch(empreinte)):
        raise Refusee('installateur invalide')
    nouveautes = m.get('nouveautes', [])
    if not isinstance(nouveautes, list) or len(nouveautes) > NOUVEAUTES_MAX:
        raise Refusee('nouveautés invalides')
    date = m.get('date')
    if date is not None and not (isinstance(date, str) and _DATE.fullmatch(date)):
        raise Refusee('date invalide')
    return {'version': version, 'nom': nom_installateur(version), 'taille': taille, 'sha256': empreinte,
            'nouveautes': [_texte_propre(n) for n in nouveautes], 'date': date,
            'url': url_installateur(version), 'notes': url_notes(version)}


def faire_manifeste(version, installateur, cle_privee, nouveautes=(), date=None):
    """Côté publication (construire.py --paquet) : enveloppe signée, en octets, pour l'installateur donné."""
    installateur = Path(installateur)
    if installateur.name != nom_installateur(version):
        raise ValueError(f'installateur inattendu : {installateur.name}')
    m = {'application': 'MyPhotos', 'version': version, 'date': date or time.strftime('%Y-%m-%d'),
         'installateur': {'nom': installateur.name, 'taille': installateur.stat().st_size,
                          'sha256': modele._sha256(installateur)},
         'nouveautes': list(nouveautes)}
    texte = json.dumps(m, ensure_ascii=False, indent=1).encode('utf-8')
    enveloppe = {'format': 1, 'manifeste': base64.b64encode(texte).decode('ascii'),
                 'signature': base64.b64encode(signature.signer(texte, cle_privee)).decode('ascii')}
    brut = (json.dumps(enveloppe, indent=1) + '\n').encode('ascii')
    lire_manifeste(brut, cle_privee[:64])           # contrôle immédiat, avec les règles de MyPhotos
    return brut


# ---------------------------------------------------------------- réseau : https seulement

class _HttpsSeulement(urllib.request.HTTPRedirectHandler):
    """Les redirections de GitHub (vers son stockage) restent en https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).lower().startswith('https://'):
            raise urllib.error.URLError('redirection hors https refusée')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OUVREUR = urllib.request.build_opener(_HttpsSeulement)


def ouvrir(req, timeout=60):
    """Comme urllib.request.urlopen, en https seulement (adresse de départ et redirections)."""
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    if not url.lower().startswith('https://'):
        raise urllib.error.URLError('adresse hors https refusée')
    return _OUVREUR.open(req, timeout=timeout)


def chercher(delai=20):
    """Lit le manifeste de la dernière version publiée. Renvoie le manifeste vérifié, ou None si la dernière version
    publiée n'en a pas (versions d'avant la 0.4.0). Indisponible si GitHub ne répond pas ; Refusee si le fichier
    n'est pas signé par le projet."""
    req = urllib.request.Request(URL_MANIFESTE, headers={'User-Agent': AGENT, 'Accept': 'application/octet-stream'})
    try:
        with ouvrir(req, timeout=delai) as r:
            brut = r.read(TAILLE_MAX_MANIFESTE + 1)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code == 404:
            return None
        raise Indisponible(f'GitHub a répondu {code}') from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Indisponible(str(getattr(e, 'reason', e)) or e.__class__.__name__) from None
    return lire_manifeste(brut)


# ---------------------------------------------------------------- téléchargement et installation

PLACE_MINIMALE = 600 * 1024 * 1024       # installateur (40 Mo) + nouvelle version (115 Mo) + marge, sur le disque


def dossier(racine=None):
    """Dossier des installateurs téléchargés : config.MISES_A_JOUR (profil de l'utilisateur), ou <racine>\\mises-a-jour
    pour les essais."""
    return Path(racine) / DOSSIER if racine else config.MISES_A_JOUR


def _lien(p):
    """True si p est un lien symbolique ou une jonction (jamais suivi pour un installateur)."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return bool(getattr(st, 'st_file_attributes', 0) & 0x400) or os.path.islink(p)     # REPARSE_POINT


def place_suffisante():
    """Assez de place pour télécharger puis installer (disque de MyPhotos et disque du dossier des mises à jour) ?
    Une installation silencieuse qui manque de place laisserait MyPhotos à moitié remplacé."""
    for p in (config.EXE_DOSSIER, config.MISES_A_JOUR):
        while not p.exists() and p.parent != p:
            p = p.parent
        try:
            if shutil.disk_usage(p).free < PLACE_MINIMALE:
                return False
        except OSError:
            continue
    return True


def conforme(chemin, info):
    """True si le fichier a exactement la taille et l'empreinte du manifeste vérifié."""
    try:
        return Path(chemin).stat().st_size == info['taille'] and modele._sha256(chemin) == info['sha256']
    except OSError:
        return False


def telecharger(info, progression=None, arret=None, racine=None, essais=3):
    """Télécharge l'installateur du manifeste vérifié (reprise d'un fichier interrompu), contrôle taille et
    empreinte, et renvoie son chemin. Indisponible en cas d'échec ; InterruptedError si arret est levé."""
    d = dossier(racine)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / info['nom']
    partiel = d / (info['nom'] + '.part')
    if any(_lien(p) for p in (d, dest, partiel)):
        raise Indisponible('dossier des mises à jour inattendu (lien)')
    if dest.exists() and conforme(dest, info):
        if progression:
            progression(info['taille'], info['taille'])
        return dest
    for essai in range(1, essais + 1):
        try:
            modele._telecharger_un(info['url'], partiel, info['taille'],
                                   lambda n: progression and progression(n, info['taille']), arret,
                                   ouvrir=ouvrir, agent=AGENT)
            if modele._sha256(partiel) != info['sha256']:
                partiel.unlink(missing_ok=True)
                raise IOError('installateur abîmé (empreinte inattendue)')
            os.replace(partiel, dest)
            return dest
        except InterruptedError:
            raise
        except Exception as e:
            if essai == essais:
                raise Indisponible(str(e) or e.__class__.__name__) from None
            time.sleep(2 * essai)


def installation_possible():
    """(True, '') pour la version installée de MyPhotos ; sinon (False, raison) : lien vers la page seulement."""
    if os.name != 'nt':
        return False, 'hors Windows'
    if not config.GELE:
        return False, 'depuis les sources'
    if not (config.EXE_DOSSIER / 'unins000.exe').is_file():
        return False, 'MyPhotos.exe sans installation'
    return True, ''


def commande_installation(chemin, journal_installation):
    """Ligne de commande de l'installateur : silencieux (fenêtre d'avancement seule), /MISEAJOUR pour attendre la
    fermeture de MyPhotos puis le relancer. Les choix de la première installation (dossier, icône) sont repris."""
    return [str(chemin), '/SP-', '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/MISEAJOUR',
            f'/LOG={journal_installation}']


def lancer_installation(chemin, info):
    """Contrôle une dernière fois l'installateur, puis le lance, détaché de MyPhotos. À l'appelant d'arrêter MyPhotos
    tout de suite après : l'installateur attend sa fermeture. Renvoie le PID de l'installateur."""
    possible, raison = installation_possible()
    if not possible:
        raise Indisponible(raison)
    chemin = Path(chemin)
    if _lien(chemin) or _lien(chemin.parent) or not conforme(chemin, info):
        raise Indisponible('installateur abîmé (empreinte inattendue)')
    if not place_suffisante():
        raise Indisponible('pas assez de place sur le disque')
    journal_installation = chemin.with_name(f'installation-{info["version"]}.log')
    commande = commande_installation(chemin, journal_installation)
    # L'installateur, puis le MyPhotos qu'il rouvre, héritent de cet environnement : sans les variables que le
    # chargeur de PyInstaller pose pour CE MyPhotos (le nouveau doit démarrer comme lancé à la main).
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('_PYI_') and k.upper() != '_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    drapeaux = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Hors du Job Object éventuel de MyPhotos : l'installateur doit lui survivre.
        p = subprocess.Popen(commande, cwd=str(chemin.parent), close_fds=True, env=env,
                             creationflags=drapeaux | 0x01000000)            # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        p = subprocess.Popen(commande, cwd=str(chemin.parent), close_fds=True, env=env, creationflags=drapeaux)
    return p.pid


def nettoyer(racine=None, maintenant=None):
    """Range le dossier des mises à jour : installateurs d'une version déjà installée (ou plus ancienne), fichiers
    partiels de plus de 7 jours, journaux d'installation de plus de 90 jours. Ne touche qu'aux noms qu'il reconnaît."""
    d = dossier(racine)
    if not d.is_dir():
        return []
    maintenant = maintenant or time.time()
    retires = []
    for p in d.iterdir():
        try:
            if not p.is_file() or p.is_symlink():
                continue
            m = _INSTALLATEUR.fullmatch(p.name)
            age = maintenant - p.stat().st_mtime
            if m:
                vieux = not plus_recente(m.group(1), config.VERSION)
                if vieux or (m.group(2) and age > 7 * 86400):
                    p.unlink()
                    retires.append(p.name)
            elif re.fullmatch(r'installation-[0-9.]+\.log', p.name) and age > 90 * 86400:
                p.unlink()
                retires.append(p.name)
        except OSError:
            continue
    return retires


# ---------------------------------------------------------------- la mise à jour vue du serveur

class Refus(Exception):
    """Action impossible maintenant ; message en français simple pour la page, code HTTP."""

    def __init__(self, message, code=409):
        super().__init__(message)
        self.code = code


class Gestion:
    """Accord de l'utilisateur, vérification une fois par jour, report, téléchargement puis installation.

    Indépendante du serveur : il lui donne la lecture et l'écriture de ses réglages (table meta), sa fonction
    d'arrêt et la liste des travaux en cours. Les fonctions réseau et système sont remplaçables (essais).
    Réglages gardés : maj_auto ('oui' / 'non' / absent = pas encore demandé), maj_derniere (heure de la dernière
    vérification réussie), maj_reportee ({version, jusqua} : « Plus tard »), maj_lancee ({de, vers, quand} : une
    installation lancée, dont la page dit le résultat au lancement suivant).
    """

    INTERVALLE = 24 * 3600                       # une vérification par jour au plus, sauf demande de l'utilisateur
    PREMIERE_ATTENTE = 60                        # après le lancement : laisser MyPhotos démarrer d'abord
    REPORT = 24 * 3600                           # « Plus tard » : le bandeau revient le lendemain

    def __init__(self, meta_get, meta_set, arreter, travaux, journal, chercher=chercher, telecharger=telecharger,
                 lancer=lancer_installation, possible=installation_possible, racine=None, place=place_suffisante):
        self._meta_get, self._meta_set = meta_get, meta_set
        self._arreter, self._travaux, self._journal = arreter, travaux, journal
        self._chercher, self._telecharger, self._lancer, self._possible = chercher, telecharger, lancer, possible
        self._place = place
        self._racine = racine
        self._verrou = threading.Lock()
        self._fin = threading.Event()
        self._arret_telechargement = threading.Event()
        self._fil = None
        self.etat = 'inconnu'        # inconnu, verification, a_jour, disponible, telechargement, installation, erreur
        self.info = None             # manifeste vérifié de la version proposée
        self.message = None
        self.fait = self.total = 0
        self.apres = self._bilan_lance()

    # -------- réglages

    def accord(self):
        v = self._meta_get('maj_auto')
        return v if v in ('oui', 'non') else None

    def regler(self, oui):
        self._meta_set('maj_auto', 'oui' if oui else 'non')
        self._journal.info('vérification des nouvelles versions : %s', 'oui' if oui else 'non')
        if oui:
            self.verifier(manuel=False)
        return self.resume()

    def _json(self, cle):
        try:
            d = json.loads(self._meta_get(cle) or '')
            return d if isinstance(d, dict) else None
        except (ValueError, TypeError):
            return None

    def _bilan_lance(self):
        """Une installation a été lancée par le MyPhotos précédent : a-t-elle abouti ?"""
        d = self._json('maj_lancee')
        if not d:
            return None
        try:
            vers = d.get('vers')
            if vers == config.VERSION:
                return {'reussie': True, 'version': vers}
            if plus_recente(vers, config.VERSION):
                return {'reussie': False, 'version': vers}
        except ValueError:
            pass
        self._meta_set('maj_lancee', None)               # plus ancien que la version qui tourne : sans objet
        return None

    def bilan_vu(self):
        self.apres = None
        self._meta_set('maj_lancee', None)
        return {'ok': True}

    def reporter(self):
        with self._verrou:
            if self.info is None:
                raise Refus('Aucune nouvelle version à reporter.')
            self._meta_set('maj_reportee', json.dumps({'version': self.info['version'],
                                                       'jusqua': time.time() + self.REPORT}))
        return self.resume()

    def _reportee(self):
        d = self._json('maj_reportee')
        return bool(d and self.info and d.get('version') == self.info['version']
                    and isinstance(d.get('jusqua'), (int, float)) and d['jusqua'] > time.time())

    # -------- vérification

    def verifier(self, manuel=True):
        """Lance une vérification en arrière-plan (manuel : bouton « Rechercher une mise à jour », permis même sans
        accord, puisque c'est l'utilisateur qui le demande). Sans effet si une vérification ou une installation est
        déjà en cours."""
        with self._verrou:
            if self.etat in ('verification', 'telechargement', 'installation'):
                return self.resume_verrouille()
            self.etat = 'verification'
            self.message = None
            self._fil = threading.Thread(target=self._verifier, args=(manuel,), name='myphotos-mise-a-jour',
                                         daemon=True)
            self._fil.start()
            return self.resume_verrouille()

    def _meta_sur(self, cle, valeur):
        """Écrit un réglage sans jamais interrompre la mise à jour (base verrouillée, disque plein) : journalisé."""
        try:
            self._meta_set(cle, valeur)
            return True
        except Exception:
            self._journal.exception('réglage %s de la mise à jour non enregistré', cle)
            return False

    def _verifier(self, manuel):
        """Fil de vérification. Ne change l'état que s'il vaut encore « verification » (rien ne doit écraser un
        téléchargement ou une installation commencés entre-temps)."""
        resultat, info, erreur = 'ok', None, None
        try:
            info = self._chercher()
        except Indisponible as e:
            resultat, erreur = 'indisponible', e
            self._journal.info('vérification des nouvelles versions impossible : %s', e)
        except Refusee as e:
            resultat, erreur = 'refusee', e
            self._journal.warning('fichier de mise à jour refusé : %s', e)
        except Exception as e:
            resultat, erreur = 'refusee', e
            self._journal.exception('vérification des nouvelles versions')
        maintenant = str(time.time())
        self._meta_sur('maj_essai', maintenant)
        self._meta_sur('maj_echecs', str(self._echecs() + 1) if resultat == 'indisponible' else '0')
        if resultat == 'ok':
            self._meta_sur('maj_derniere', maintenant)
        with self._verrou:
            if self.etat != 'verification':
                return
            if resultat == 'indisponible':
                if self.info is not None:                 # une version déjà connue reste proposée
                    self.etat, self.message = 'disponible', None
                else:
                    self.etat = 'erreur' if manuel else 'inconnu'
                    self.message = ('MyPhotos n’a pas pu joindre GitHub pour savoir s’il existe une nouvelle '
                                    'version. Vérifiez votre connexion à Internet, puis réessayez.') if manuel else None
            elif resultat == 'refusee':
                self.etat, self.info = ('erreur' if manuel else 'inconnu'), None
                self.message = (('Le fichier de mise à jour publié n’a pas pu être vérifié : MyPhotos ne l’utilise '
                                 'pas. Réessayez plus tard.') if isinstance(erreur, Refusee)
                                else 'La vérification a échoué. Réessayez plus tard.') if manuel else None
            elif info is not None and plus_recente(info['version'], config.VERSION):
                if self.info is None or self.info['version'] != info['version']:
                    self._journal.info('nouvelle version disponible : %s', info['version'])
                self.info, self.etat, self.message = info, 'disponible', None
            else:
                self.info, self.etat, self.message = None, 'a_jour', None

    def _nombre(self, cle):
        try:
            return float(self._meta_get(cle) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _derniere(self):
        return self._nombre('maj_derniere')

    def _echecs(self):
        return int(self._nombre('maj_echecs'))

    # Après un échec de réseau (pas d'Internet, GitHub ne répond pas), on réessaie au bout de 1 h, 6 h, puis 24 h.
    ATTENTES_ECHEC = (3600, 6 * 3600, 24 * 3600)

    def due(self, maintenant=None):
        """Vérification automatique à faire maintenant ? Une fois par jour au plus après une vérification réussie ou
        une réponse de GitHub (même refusée) ; plus tôt seulement après un échec de réseau, de plus en plus espacé."""
        maintenant = time.time() if maintenant is None else maintenant
        if self.accord() != 'oui' or self.etat in ('verification', 'telechargement', 'installation'):
            return False
        if 0 <= maintenant - self._derniere() < self.INTERVALLE:
            return False
        echecs = self._echecs()
        attente = self.ATTENTES_ECHEC[min(echecs, len(self.ATTENTES_ECHEC)) - 1] if echecs else self.INTERVALLE
        return not 0 <= maintenant - self._nombre('maj_essai') < attente

    def veiller(self):
        """Fil de veille : une vérification par jour au plus, si l'utilisateur l'a accepté."""
        if self._fin.wait(self.PREMIERE_ATTENTE):
            return
        while True:
            if self.due():
                self.verifier(manuel=False)
            if self._fin.wait(15 * 60):
                return

    def demarrer(self):
        try:
            nettoyer(self._racine)
        except OSError:
            pass
        threading.Thread(target=self.veiller, name='myphotos-veille-mise-a-jour', daemon=True).start()

    def fermer(self):
        self._fin.set()
        self._arret_telechargement.set()

    # -------- installation

    def installer(self):
        with self._verrou:
            if self.etat in ('telechargement', 'installation'):
                return self.resume_verrouille()
            if self.etat == 'verification':
                raise Refus('MyPhotos est en train de chercher une nouvelle version : réessayez dans un instant.')
            if self.info is None:
                raise Refus('Aucune nouvelle version à installer.')
            possible, _ = self._possible()
            if not possible:
                raise Refus('Cette copie de MyPhotos ne peut pas se mettre à jour toute seule : téléchargez la '
                            'nouvelle version sur sa page.')
            if 'copie' in self._travaux():
                raise Refus('Une copie de photos est en cours : attendez qu’elle soit finie, puis recommencez.')
            if self._fin.is_set():
                raise Refus('MyPhotos est en train de s’arrêter.')
            if not self._place():
                raise Refus('Il n’y a pas assez de place sur le disque pour installer la nouvelle version : '
                            'libérez au moins 600 Mo, puis recommencez.')
            self.etat, self.message, self.fait, self.total = 'telechargement', None, 0, self.info['taille']
            self._arret_telechargement.clear()
            self._fil = threading.Thread(target=self._installer, args=(self.info,), name='myphotos-installation',
                                         daemon=True)
            self._fil.start()
            return self.resume_verrouille()

    def _progression(self, fait, total):
        self.fait, self.total = int(fait), int(total)

    def _installer(self, info):
        lance = False
        try:
            chemin = self._telecharger(info, self._progression, self._arret_telechargement, self._racine)
            with self._verrou:
                # « Annuler » cliqué à la fin du téléchargement, MyPhotos qui s'arrête, ou copie commencée entre-temps :
                # on ne lance rien.
                if self._fin.is_set() or self._arret_telechargement.is_set():
                    self.etat = 'disponible'
                    return
                if 'copie' in self._travaux():
                    self.etat = 'erreur'
                    self.message = ('Une copie de photos a commencé pendant le téléchargement : la mise à jour attend '
                                    'qu’elle soit finie. Cliquez ensuite sur « Réessayer ».')
                    return
                self.etat = 'installation'
            self._meta_sur('maj_lancee', json.dumps({'de': config.VERSION, 'vers': info['version'],
                                                     'quand': time.time()}))
            pid = self._lancer(chemin, info)
            lance = True
            self._journal.info('installation de MyPhotos %s lancée (installateur PID %s) : MyPhotos s’arrête',
                               info['version'], pid)
        except InterruptedError:
            with self._verrou:
                self.etat = 'disponible'
            return
        except Exception as e:
            if isinstance(e, Indisponible):
                self._journal.warning('mise à jour vers %s impossible : %s', info['version'], e)
            else:
                self._journal.exception('mise à jour vers %s', info['version'])
            with self._verrou:
                self.etat = 'erreur'
                self.message = ('La nouvelle version n’a pas pu être téléchargée ou vérifiée. Vérifiez votre '
                                'connexion à Internet et la place libre sur le disque, puis réessayez.')
            return
        finally:
            if not lance:
                self._meta_sur('maj_lancee', None)
                with self._verrou:
                    if self.etat in ('telechargement', 'installation'):     # jamais d'état figé
                        self.etat = 'erreur'
                        self.message = self.message or 'La mise à jour n’a pas pu commencer. Réessayez plus tard.'
        self._arreter()                                   # l'installateur attend la fermeture de MyPhotos

    def annuler(self):
        with self._verrou:
            if self.etat == 'telechargement':
                self._arret_telechargement.set()
            return self.resume_verrouille()

    # -------- pour /api/etat

    def resume(self):
        with self._verrou:
            return self.resume_verrouille()

    def resume_verrouille(self):
        possible, raison = self._possible()
        i = self.info
        return {
            'accord': self.accord(),
            'etat': self.etat,
            'version': i['version'] if i else None,
            'date': i['date'] if i else None,
            'nouveautes': list(i['nouveautes']) if i else [],
            'notes': i['notes'] if i else None,
            'taille': i['taille'] if i else None,
            'fait': self.fait, 'total': self.total,
            'pourcent': round(100 * self.fait / self.total, 1) if self.total else 0.0,
            'message': self.message,
            'possible': possible, 'raison': raison,
            'reportee': self._reportee(),
            'derniere': self._derniere() or None,
            'apres': self.apres,
        }
