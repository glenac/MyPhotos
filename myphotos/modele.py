"""Moteur de reconnaissance : SigLIP 2 (Google, licence Apache-2.0), en ONNX (exports onnx-community).

Le modèle transforme une photo, ou un texte, en un vecteur de nombres. Une photo et un texte qui parlent
de la même chose ont des vecteurs proches : c'est ce qui permet de chercher « chien ».

Choix (23/11/2026, détails dans PASSATION.md) :
- famille SigLIP 2 : meilleur compromis qualité en français / vitesse / licence d'après le banc d'essai
  d'Immich (Crossmodal-3600) ; licence Apache-2.0 qui autorise la redistribution et la vente ;
- variantes fp16 (demi-précision) : identiques à fp32 à 0,99999 près, deux fois plus légères ;
- variantes int8 écartées : vecteurs trop abîmés (fidélité 0,76 en vision, 0,95 en texte).
"""
import hashlib
import os
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import config

URL = 'https://huggingface.co/{depot}/resolve/{revision}/{nom}'
LONGUEUR_TEXTE = 64          # texte en minuscules, terminé par <eos>, complété par <pad> jusqu'à 64 jetons

# Modèles proposés. Chaque entrée est figée (révision + empreintes SHA-256 relevées sur l'API Hugging Face).
# echelle = exp(logit_scale) et biais = logit_bias du modèle d'origine (google/...), lus dans model.safetensors :
# probabilité qu'une photo corresponde au texte = sigmoïde(cosinus * echelle + biais).
MODELES_DISPO = {
    'base': {
        'nom': 'Standard',
        'description': 'Rapide et léger : convient à tous les PC.',
        'depot': 'onnx-community/siglip2-base-patch16-224-ONNX',
        'revision': 'ba1f3b0843f24bc5417d38e19c37b287d719b2f4',
        'origine': 'google/siglip2-base-patch16-224@75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2',
        'licence': 'Apache-2.0',
        'taille_image': 224,
        'dimension': 768,
        'echelle': 112.66890226978423,       # exp(4.724453449249268)
        'biais': -16.771724700927734,
        'rappel_fr': 82.91,                  # Immich, Crossmodal-3600, français
        'fichiers': {
            'tokenizer.json':              (34363039,   'cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322'),
            'onnx/vision_model_fp16.onnx': (186039516,  'a1959f7bd3993a607e48839f6d01e25b876fe76afda301b028b78eef68aabd95'),
            'onnx/vision_model.onnx':      (371807752,  'c0573e3f4140c3a7c4e9cc5912bd6b26a033b46a6a8e8af26cbea262b163bcad'),
            'onnx/text_model_fp16.onnx':   (564862230,  '711da56ada0a4aa11c7dd3320df741081a3cae4f0ae1b5e5c6d5b294738d0eb0'),
            'onnx/text_model.onnx':        (1129469657, 'baf12d941beabafafb14f7b4adb38dc15be18681b964a84410ec53d9d65e6293'),
        },
        'vision': {'fp16': 'onnx/vision_model_fp16.onnx', 'fp32': 'onnx/vision_model.onnx'},
        'texte': {'fp16': 'onnx/text_model_fp16.onnx', 'fp32': 'onnx/text_model.onnx'},
    },
    # Paliers relevés et mesurés le 23/11/2026 (RX 6600 XT / Ryzen 5 5600X, photos/s du modèle seul, hors décodage) :
    #   rapide  carte ~310, processeur ~26 ; base carte ~92, processeur ~7 ;
    #   precis  carte ~20,  processeur ~1,5 ; tres_precis carte ~15, processeur ~1,3.
    # Tous distinguent chien / voiture / chat / vélo (11 contrôles sur 11).
    'rapide': {
        'nom': 'Rapide',
        'description': 'Pour un PC ancien ou sans carte graphique : beaucoup plus rapide, un peu moins précis.',
        'depot': 'onnx-community/siglip2-base-patch32-256-ONNX',
        'revision': '7efccb86b5b6601bfe9e14326e1c11b40b68d44c',
        'origine': 'google/siglip2-base-patch32-256@94dffa8cb1179de3e03f091dbc3917e5d5a9ae84',
        'licence': 'Apache-2.0',
        'taille_image': 256,
        'dimension': 768,
        'echelle': 111.8325729708764,        # exp(4.717002868652344)
        'biais': -16.766876220703125,
        'rappel_fr': 80.23,
        'fichiers': {
            'tokenizer.json':              (34363039,   'cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322'),
            'onnx/vision_model_fp16.onnx': (189374263,  '44fcd984cf4ac4baba5b4776192c0b141a120c6981df49732c681d4fe8ddc718'),
            'onnx/vision_model.onnx':      (378478689,  '0ddb7507cfcb54c2cd503029949add9dc6586de67164bb69c2c09588cdcc9719'),
            'onnx/text_model_fp16.onnx':   (564862230,  '3f8149272095410332433ca84f2c99611ed4679429eff43fbf3983ea15664ef3'),
            'onnx/text_model.onnx':        (1129469657, 'e8950ceb970ea302be3559055df99af8a1c5e1bf5a25e34d4ed0069340a3c8aa'),
        },
        'vision': {'fp16': 'onnx/vision_model_fp16.onnx', 'fp32': 'onnx/vision_model.onnx'},
        'texte': {'fp16': 'onnx/text_model_fp16.onnx', 'fp32': 'onnx/text_model.onnx'},
    },
    'precis': {
        'nom': 'Précis',
        'description': 'Trouve mieux les photos. Pour un PC récent avec une carte graphique.',
        'depot': 'onnx-community/siglip2-large-patch16-256-ONNX',
        'revision': 'c03fb63ddd5fe363212cbd08110f92a35250db6a',
        'origine': 'google/siglip2-large-patch16-256@787800c8990e6f058423089178e718139608408c',
        'licence': 'Apache-2.0',
        'taille_image': 256,
        'dimension': 1024,
        'echelle': 108.0497142449256,        # exp(4.682591438293457)
        'biais': -16.347614288330078,
        'rappel_fr': 84.97,
        'fichiers': {
            'tokenizer.json':              (34363039,   'cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322'),
            'onnx/vision_model_fp16.onnx': (632433061,  'f7f719385d406bde52e638069d04fc82453deff9df6aad69426375a78450fd1a'),
            'onnx/vision_model.onnx':      (1264345246, 'a68762da4fab7335558d4713c77e42d8abe23dcd48d38b5dbe1c00765c07ca56'),
            'onnx/text_model_fp16.onnx':   (1131647648, 'bf7195836ddeb5beea9277e689ffa8142cbdbb375e2e4b699487f50234884280'),
        },
        # texte fp32 absent : découpé en text_model.onnx + text_model.onnx_data (plus de 2 Go).
        'vision': {'fp16': 'onnx/vision_model_fp16.onnx', 'fp32': 'onnx/vision_model.onnx'},
        'texte': {'fp16': 'onnx/text_model_fp16.onnx'},
    },
    'tres_precis': {
        'nom': 'Très précis',
        'description': 'Le plus fin. Pour un PC puissant avec une bonne carte graphique ; long à télécharger.',
        'depot': 'onnx-community/siglip2-so400m-patch16-256-ONNX',
        'revision': '7881d8f282cbd7cdeac1aca6bed493a36d58e713',
        'origine': 'google/siglip2-so400m-patch16-256@e8708ab72d125807e45b36fb7d4e0aacbb59f379',
        'licence': 'Apache-2.0',
        'taille_image': 256,
        'dimension': 1152,
        'echelle': 109.88121823456089,       # exp(4.699399948120117)
        'biais': -15.932409286499023,
        'rappel_fr': 85.67,
        'fichiers': {
            'tokenizer.json':              (34363039,   'cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322'),
            'onnx/vision_model_fp16.onnx': (856359102,  'f700a9ffcaa976d663ffd8f3eaff249db42fc22cc70d2aa5e29cc4da5e42b5ad'),
            'onnx/vision_model.onnx':      (1712134973, '2415065fbb48adfbc675ce65d8ed8eec057a8317fcdb2144032e84f645e59709'),
            'onnx/text_model_fp16.onnx':   (1416135143, '8193c15f0721d51a3af48f2900fc9cace4c4f746310f4366853cf046e9da9e23'),
        },
        'vision': {'fp16': 'onnx/vision_model_fp16.onnx', 'fp32': 'onnx/vision_model.onnx'},
        'texte': {'fp16': 'onnx/text_model_fp16.onnx'},
    },
}
MODELE_DEFAUT = 'base'

# Variantes par défaut. fp16 partout : sur la carte graphique, même vitesse que fp32 ; sur le processeur,
# 20 % plus lent en vision mais deux fois moins à télécharger. Réglables pour les essais.
VISION_DEFAUT = os.environ.get('MYPHOTOS_VISION') or 'fp16'
TEXTE_DEFAUT = os.environ.get('MYPHOTOS_TEXTE') or 'fp16'


def info(cle=None):
    return MODELES_DISPO[cle or MODELE_DEFAUT]


def dossier_modele(cle=None, racine=None):
    return Path(racine or config.MODELES) / (cle or MODELE_DEFAUT)


# ---------------------------------------------------------------- fichiers du modèle

def fichier_present(cle, nom, racine=None):
    """Présent et vérifié : taille exacte et empreinte déjà contrôlée (fichier .ok à côté)."""
    taille, empreinte = info(cle)['fichiers'][nom]
    p = dossier_modele(cle, racine) / nom
    ok = p.with_name(p.name + '.ok')
    try:
        return p.is_file() and p.stat().st_size == taille and ok.read_text(encoding='ascii').strip() == empreinte
    except OSError:
        return False


def fichiers_necessaires(cle=None, vision=None, texte=None):
    m = info(cle)
    return ['tokenizer.json', m['vision'][vision or VISION_DEFAUT], m['texte'][texte or TEXTE_DEFAUT]]


def taille_telechargement(cle=None, vision=None, texte=None, racine=None):
    """Octets restant à télécharger pour ce modèle et ces variantes."""
    m = info(cle)
    return sum(m['fichiers'][n][0] for n in fichiers_necessaires(cle, vision, texte)
               if not fichier_present(cle, n, racine))


def manquants(cle=None, noms=None, racine=None):
    return [n for n in (noms or fichiers_necessaires(cle)) if not fichier_present(cle, n, racine)]


def telecharger(cle, noms, progression=None, arret=None, racine=None, essais=4):
    """Télécharge depuis Hugging Face (version figée), reprend un fichier interrompu, vérifie le SHA-256.

    progression(fait, total, nom) est appelée régulièrement ; arret (threading.Event) interrompt proprement
    (InterruptedError). Lève une exception si un fichier ne peut pas être obtenu intact.
    """
    m = info(cle)
    noms = [n for n in noms if not fichier_present(cle, n, racine)]
    total = sum(m['fichiers'][n][0] for n in noms)
    deja = 0
    for nom in noms:
        taille, empreinte = m['fichiers'][nom]
        dest = dossier_modele(cle, racine) / nom
        dest.parent.mkdir(parents=True, exist_ok=True)
        partiel = dest.with_name(dest.name + '.part')
        url = URL.format(depot=m['depot'], revision=m['revision'], nom=nom)
        for essai in range(1, essais + 1):
            try:
                _telecharger_un(url, partiel, taille, lambda n, d=deja: progression and progression(d + n, total, nom), arret)
                h = _sha256(partiel)
                if h != empreinte:
                    partiel.unlink(missing_ok=True)
                    raise IOError(f'{nom} : empreinte inattendue, fichier abîmé ({h[:12]}…)')
                os.replace(partiel, dest)
                dest.with_name(dest.name + '.ok').write_text(empreinte, encoding='ascii')
                break
            except InterruptedError:
                raise
            except Exception:
                if essai == essais:
                    raise
                time.sleep(2 * essai)
        deja += taille
    if progression:
        progression(total, total, '')


def _telecharger_un(url, partiel, taille, rappel, arret):
    debut = partiel.stat().st_size if partiel.exists() else 0
    if debut > taille:
        partiel.unlink()
        debut = 0
    if debut == taille:
        rappel(taille)
        return
    req = urllib.request.Request(url, headers={'User-Agent': 'MyPhotos/1 (logiciel local)'})
    if debut:
        req.add_header('Range', f'bytes={debut}-')
    with urllib.request.urlopen(req, timeout=60) as r:
        if debut and r.status != 206:            # le serveur ne sait pas reprendre : on repart de zéro
            debut = 0
        fait = debut
        dernier = 0.0
        with open(partiel, 'ab' if debut else 'wb') as f:
            while True:
                if arret is not None and arret.is_set():
                    raise InterruptedError('téléchargement interrompu')
                bloc = r.read(1 << 20)
                if not bloc:
                    break
                f.write(bloc)
                fait += len(bloc)
                if time.monotonic() - dernier > 0.25:
                    rappel(fait)
                    dernier = time.monotonic()
    if partiel.stat().st_size != taille:
        raise IOError(f'téléchargement incomplet ({partiel.stat().st_size} / {taille} octets)')
    rappel(taille)


def _sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 22), b''):
            h.update(bloc)
    return h.hexdigest()


# ---------------------------------------------------------------- images

def ouvrir_image(p, cote=448):
    """Ouvre une image, dans le bon sens, en RVB. Pour un JPEG, ne décode qu'une version réduite :
    4,5 fois plus rapide, vecteurs identiques à 0,9998 près (banc d'essai du 23/11/2026).

    Le côté le plus court reste >= cote, ce qui suffit pour le modèle et pour la miniature.
    """
    im = Image.open(p)
    try:
        im.draft('RGB', (cote, cote))
    except Exception:
        pass
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass                                  # informations EXIF abîmées : on garde l'image telle quelle
    return vers_rvb(im)


def vers_rvb(im):
    if im.mode == 'RGB':
        return im
    if im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info):
        im = im.convert('RGBA')
        fond = Image.new('RGBA', im.size, (255, 255, 255, 255))
        return Image.alpha_composite(fond, im).convert('RGB')
    return im.convert('RGB')


def pixels(im, taille=224):
    """Image RVB -> tableau (3, taille, taille) float32, exactement comme le prétraitement de SigLIP
    (vérifié contre transformers : écart nul) : redimensionnement bilinéaire sans recadrage, [0,255] -> [-1,1]."""
    a = np.asarray(im.resize((taille, taille), Image.BILINEAR), dtype=np.float32)
    return (a * (1.0 / 127.5) - 1.0).transpose(2, 0, 1)


# ---------------------------------------------------------------- texte

class Tokeniseur:
    """Découpe le texte comme Siglip2Tokenizer (vérifié : 12 cas sur 12 identiques), sans dépendre de transformers."""

    def __init__(self, fichier):
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(str(fichier))
        self.tok.no_padding()
        self.tok.no_truncation()
        self.eos = self.tok.token_to_id('<eos>')
        self.pad = self.tok.token_to_id('<pad>')

    def encoder(self, textes):
        ids = np.full((len(textes), LONGUEUR_TEXTE), self.pad, dtype=np.int64)
        for i, t in enumerate(textes):
            e = self.tok.encode(t.lower(), add_special_tokens=False).ids[:LONGUEUR_TEXTE - 1] + [self.eos]
            ids[i, :len(e)] = e
        return ids


# ---------------------------------------------------------------- moteur

# ONNX Runtime 1.24 + DirectML : deux calculs simultanés sur la carte graphique (deux fils, deux sessions) font
# planter le processus sans message (violation d'accès, reproduit le 23/11/2026). Tous les calculs faits sur la
# carte passent donc un par un, pour tout le processus. Une recherche (11 ms) attend au plus la fin d'un lot.
_VERROU_CARTE = threading.Lock()


def _executer(session, sur_carte, sorties, entrees):
    if sur_carte:
        with _VERROU_CARTE:
            return session.run(sorties, entrees)
    return session.run(sorties, entrees)


def normaliser(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def probabilite(cosinus, cle=None):
    """Probabilité (0 à 1) que la photo corresponde au texte, calibrée par le modèle lui-même."""
    m = info(cle)
    x = np.asarray(cosinus, dtype=np.float64) * m['echelle'] + m['biais']
    return 1.0 / (1.0 + np.exp(-x))


class Moteur:
    """Les deux moitiés du modèle : vision (photos -> vecteurs) et texte (mots -> vecteur).

    materiel : 'auto' (carte graphique si possible, sinon processeur), 'carte' ou 'processeur'.
    materiel_texte : idem pour le texte. Sur la carte : 11 ms par requête et peu de mémoire vive ;
    sur le processeur : environ 90 ms (mesures du 23/11/2026, modèle base).
    """

    def __init__(self, cle=None, vision=None, texte=None, materiel='auto', fils=None, charger_texte=True,
                 charger_vision=True, racine=None, materiel_texte=None):
        import onnxruntime as ort
        self.ort = ort
        self.cle = cle or MODELE_DEFAUT
        self.info = info(self.cle)
        self.dossier = dossier_modele(self.cle, racine)
        self.nom_vision = vision or VISION_DEFAUT
        self.nom_texte = texte or TEXTE_DEFAUT
        self.taille_image = self.info['taille_image']
        self.fils = fils or max(1, (os.cpu_count() or 2) // 2)
        self.verrou_texte = threading.Lock()
        self.materiel = 'processeur'
        self.vision = self.texte = self.tokeniseur = None
        if charger_vision:
            self.vision = self._session(self.info['vision'][self.nom_vision], materiel)
            self.entree_vision = self.vision.get_inputs()[0].name
            self.type_vision = self.vision.get_inputs()[0].type
            self.sortie_vision = self._sortie(self.vision)
        self.materiel_texte = 'processeur'
        if charger_texte:
            avant = self.materiel
            self.texte = self._session(self.info['texte'][self.nom_texte], materiel_texte or materiel)
            if self.texte.get_providers()[0] == 'DmlExecutionProvider':
                self.materiel_texte = 'carte graphique'
            self.materiel = avant if charger_vision else self.materiel_texte
            self.entree_texte = self.texte.get_inputs()[0].name
            self.sortie_texte = self._sortie(self.texte)
            self.tokeniseur = Tokeniseur(self.dossier / 'tokenizer.json')

    def _options(self, nom, carte):
        ort = self.ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        # Bogue d'ONNX Runtime 1.24 : l'optimisation complète plante sur les modèles fp16 sur processeur
        # (SimplifiedLayerNormFusion). Le niveau BASIC les charge, sans perte et à la même vitesse.
        fp16 = '_fp16' in nom
        so.graph_optimization_level = (ort.GraphOptimizationLevel.ORT_ENABLE_BASIC if (fp16 and not carte)
                                       else ort.GraphOptimizationLevel.ORT_ENABLE_ALL)
        if fp16 and not carte:
            # Le processeur reconvertit les poids fp16 en fp32 à chaque appel ; avec la réserve mémoire
            # d'ONNX Runtime, ces copies restent : texte base 2,1 Go au lieu de 1,0 Go (mesuré le 23/11/2026).
            so.enable_cpu_mem_arena = False
        if carte:
            so.enable_mem_pattern = False            # exigences de DirectML
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        else:
            so.intra_op_num_threads = self.fils
        return so

    def _session(self, nom, materiel):
        ort = self.ort
        chemin = str(self.dossier / nom)
        if materiel in ('auto', 'carte') and 'DmlExecutionProvider' in ort.get_available_providers():
            try:
                s = ort.InferenceSession(chemin, sess_options=self._options(nom, True),
                                         providers=[('DmlExecutionProvider', {'device_id': 0}), 'CPUExecutionProvider'])
                if s.get_providers()[0] == 'DmlExecutionProvider':
                    self.materiel = 'carte graphique'
                    return s
            except Exception:
                pass                                 # pilote graphique récalcitrant : on passe au processeur
        return ort.InferenceSession(chemin, sess_options=self._options(nom, False), providers=['CPUExecutionProvider'])

    @staticmethod
    def _sortie(session):
        noms = [o.name for o in session.get_outputs()]
        return 'pooler_output' if 'pooler_output' in noms else noms[-1]

    def vecteurs_images(self, lot):
        """lot : tableau (n, 3, taille, taille) float32 -> vecteurs normalisés (n, dimension) float32."""
        entree = lot.astype(np.float16) if self.type_vision == 'tensor(float16)' else lot
        sortie = _executer(self.vision, self.materiel == 'carte graphique',
                           [self.sortie_vision], {self.entree_vision: entree})[0]
        v = normaliser(sortie)
        if not np.all(np.isfinite(v)):
            raise FloatingPointError('vecteurs invalides (pilote graphique ?)')
        return v

    def vecteurs_textes(self, textes):
        ids = self.tokeniseur.encoder(textes)
        with self.verrou_texte:
            sortie = _executer(self.texte, self.materiel_texte == 'carte graphique',
                               [self.sortie_texte], {self.entree_texte: ids})[0]
        return normaliser(sortie)
