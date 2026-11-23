"""Évaluation de la recherche, pour chacun des quatre moteurs, sur un jeu d'images PUBLIQUES étiquetées.

Jeu d'essai : vignettes de Wikimedia Commons, étiquetées par leurs données structurées « dépeint » (P180).
Aucune photo personnelle n'est utilisée ni regardée.

    python -I outils/evaluation.py --dossier D:\\chemin\\eval         télécharge ce qui manque, puis évalue
    python -I outils/evaluation.py --dossier ... --moteurs base      un seul moteur (par défaut : les quatre)
    python -I outils/evaluation.py --dossier ... --suggestions       seuils suggérés au lieu des réglages en service
    python -I outils/evaluation.py --dossier ... --reconstruire      nouvelle sélection d'images (API Commons)
    python -I outils/evaluation.py --dossier ... --planches          planches-contact pour vérifier les étiquettes
    python -I outils/evaluation.py --dossier ... --markdown          tableaux au format Markdown

La liste figée des images (titres, auteurs, licences, étiquettes P180) est dans evaluation_jeu.json, à côté de
ce script : relancer l'évaluation retélécharge exactement les mêmes images. Les réponses de l'API sont gardées
dans <dossier>/cache_api : une construction interrompue reprend là où elle s'était arrêtée.

Mesures, pour chaque moteur, chaque catégorie et chaque gabarit de requête (« {q} », « une photo de {q} »…) :
- AP : précision moyenne du classement (1 = toutes les bonnes photos avant toutes les autres) ;
- P@10 : part de bonnes photos parmi les 10 premières ;
- précision et rappel quand on ne garde que les photos dont la probabilité dépasse un seuil ;
- fausses détections des requêtes « absentes » (girafe, astronaute… : rien de tel dans le jeu) ;
- robustesse de la requête (pluriel, accents, majuscules, plusieurs mots) et repli MINIMUM_RESULTATS.
Le détail va dans <dossier>/rapport_<moteur>.txt, la synthèse à l'écran, les nombres dans resultats.json.
Les seuils évalués sont ceux en service (myphotos/recherche.py, REGLAGES), sauf --suggestions.
"""
import argparse
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np                       # noqa: E402

from myphotos import modele, recherche    # noqa: E402

UA = 'MyPhotos-essai/1 (logiciel local)'
API_COMMONS = 'https://commons.wikimedia.org/w/api.php'
API_WIKIDATA = 'https://www.wikidata.org/w/api.php'
PAUSE_API = 1.5            # secondes entre deux requêtes à l'API (une seule à la fois)
PAUSE_IMAGE = 2.0          # secondes entre deux vignettes (le serveur d'images limite vite le débit : HTTP 429)
LARGEUR = 640              # vignette demandée (le serveur renvoie la taille standard au-dessus : 960 px)

JEU = Path(__file__).resolve().parent / 'evaluation_jeu.json'
DOSSIER_DEFAUT = Path(os.environ.get('MYPHOTOS_EVAL') or Path(tempfile.gettempdir()) / 'myphotos-evaluation')

# ---------------------------------------------------------------- catégories
# cle : nom du dossier ; fr : requête de référence ; genre : pour « une photo d'un / d'une » ;
# en : requête anglaise (comparaison seulement) ; q : éléments Wikidata « dépeint » (le premier sert à chercher,
# les suivants sont des équivalents) ; recherche : (langue, terme) pour wbsearchentities ;
# photo=False : pas de filtre « œuvre d'art / photo ancienne » (captures d'écran, mèmes) ;
# parents : catégories qui contiennent celle-ci (une rose est une fleur) ; tous : toutes les valeurs de q exigées.
CATEGORIES = [
    dict(cle='chien', fr='chien', genre='m', en='dog', q=['Q144'],
         variantes={'pluriel': 'chiens', 'majuscule': 'Chien', 'capitales': 'CHIEN'}),
    dict(cle='chat', fr='chat', genre='m', en='cat', q=['Q146'], recherche=('en', 'house cat'),
         variantes={'pluriel': 'chats'}),
    dict(cle='plage', fr='plage', genre='f', en='beach', q=['Q40080'],
         variantes={'pluriel': 'plages', 'majuscule': 'Plage'}),
    dict(cle='mer', fr='mer', genre='f', en='sea', q=['Q165'], recherche=('fr', 'mer'),
         variantes={'article': 'la mer', 'deux mots': 'bord de mer'}),
    dict(cle='montagne', fr='montagne', genre='f', en='mountain', q=['Q8502'],
         variantes={'pluriel': 'montagnes'}),
    dict(cle='neige', fr='neige', genre='f', en='snow', q=['Q7561'],
         variantes={'deux mots': 'paysage enneigé'}),
    dict(cle='coucher_soleil', fr='coucher de soleil', genre='m', en='sunset', q=['Q166564'],
         variantes={'pluriel': 'couchers de soleil', 'synonyme': 'soleil couchant'}),
    dict(cle='voiture', fr='voiture', genre='f', en='car', q=['Q1420'],
         variantes={'pluriel': 'voitures', 'synonyme': 'auto'}),
    dict(cle='velo', fr='vélo', genre='m', en='bicycle', q=['Q11442'],
         variantes={'sans accent': 'velo', 'pluriel': 'vélos', 'synonyme': 'bicyclette'}),
    dict(cle='fleur', fr='fleur', genre='f', en='flower', q=['Q506'],
         variantes={'pluriel': 'fleurs'}),
    dict(cle='rose', fr='rose', genre='f', en='rose', q=['Q34687'], parents=['fleur'],
         variantes={'pluriel': 'roses'}),
    dict(cle='champignon', fr='champignon', genre='m', en='mushroom', q=['Q83093'],
         variantes={'pluriel': 'champignons'}),
    dict(cle='gateau', fr='gâteau', genre='m', en='cake', q=['Q13276'],
         variantes={'sans accent': 'gateau', 'pluriel': 'gâteaux', 'pluriel sans accent': 'gateaux'}),
    dict(cle='pizza', fr='pizza', genre='f', en='pizza', q=['Q177'],
         variantes={'pluriel': 'pizzas'}),
    dict(cle='basket', fr='basket-ball', genre='m', en='basketball', q=['Q5372'],
         variantes={'abrégé': 'basket', 'sans tiret': 'basketball', 'deux mots': 'match de basket'}),
    dict(cle='bateau', fr='bateau', genre='m', en='boat', q=['Q35872'],
         variantes={'pluriel': 'bateaux'}),
    dict(cle='cheval', fr='cheval', genre='m', en='horse', q=['Q726'],
         variantes={'pluriel': 'chevaux'}),
    dict(cle='enfant', fr='enfant', genre='m', en='child', q=['Q7569'],
         variantes={'pluriel': 'enfants'}),
    dict(cle='sapin_noel', fr='sapin de Noël', genre='m', en='Christmas tree', q=['Q47128'],
         variantes={'sans accent': 'sapin de noel', 'synonyme': 'arbre de Noël', 'court': 'sapin'}),
    dict(cle='outil', fr='outil', genre='m', en='tool', q=['Q39546', 'Q2578402', 'Q1327701', 'Q25294'],
         recherche=('en', 'tool'), variantes={'pluriel': 'outils', 'synonyme': 'bricolage'}),
    dict(cle='capture_ecran', fr="capture d'écran", genre='f', en='screenshot', q=['Q208594'], photo=False,
         variantes={'sans accent': "capture d'ecran", 'pluriel': "captures d'écran", 'anglicisme': 'screenshot'}),
    dict(cle='chantier', fr='chantier', genre='m', en='construction site', q=['Q360418'],
         variantes={'synonyme': 'travaux', 'pluriel': 'chantiers'}),
    dict(cle='meme', fr='mème', genre='m', en='internet meme', q=['Q2927074'], photo=False,
         recherche=('en', 'internet meme'), variantes={'sans accent': 'meme', 'pluriel': 'mèmes'}),
    dict(cle='rue', fr='rue', genre='f', en='street', q=['Q79007'],
         variantes={'pluriel': 'rues'}),
]
# Requêtes de deux mots ou plus : jeux plus petits, évalués à part (robustesse), pas pour régler les seuils.
COMBINEES = [
    dict(cle='gateau_anniversaire', fr="gâteau d'anniversaire", genre='m', en='birthday cake', q=['Q1195276'],
         parents=['gateau'], nombre=10, variantes={'sans accent': "gateau d'anniversaire"}),
    dict(cle='chien_plage', fr='chien sur la plage', genre='m', en='dog on the beach', q=['Q144', 'Q40080'],
         tous=True, parents=['chien', 'plage'], nombre=10,
         variantes={'pluriel': 'chiens sur la plage', 'télégraphique': 'chien plage'}),
]
TOUTES = CATEGORIES + COMBINEES
PAR_CLE = {c['cle']: c for c in TOUTES}
NOMBRE = 12                # images par catégorie simple

# Fichiers écartés après examen des planches-contact (sujet invisible, œuvre d'art, doublon…).
EXCLUS = {
    'File:Grindstone - Male (PSF).png': "dessin au trait, pas une photo",  # outil/08
    'File:Kraemer-1568.png': "gravure sur bois de 1568, pas une photo",  # outil/06
}

# Étiquettes P180 manifestement incomplètes ou fausses, corrigées après examen visuel des « fausses » images
# les mieux classées. Chaque correction est signalée dans le rapport.
REQUALIFICATIONS = {
    'File:Porto Covo Outubro 2014-3.jpg':  # plage/03
        dict(ajouter=['mer'], motif='la mer est bien visible (seule « plage » est étiquetée)'),
    'File:Strandkörbe in Sellin.jpg':  # plage/07
        dict(ajouter=['mer'], motif='la mer est bien visible'),
    'File:Parasols, Evening, Beach, Rincon de la Victoria, Andalusia, Spain.jpg':  # plage/10
        dict(ajouter=['mer'], motif='la mer est bien visible'),
    'File:Porto Covo March 2010-2.jpg':  # plage/11
        dict(ajouter=['mer'], motif='la mer est bien visible'),
    'File:Sunset 2007-1.jpg':  # coucher_soleil/02
        dict(ajouter=['mer'], motif='coucher de soleil sur la mer'),
    'File:Juan Griego Sunset.jpg':  # coucher_soleil/06
        dict(ajouter=['mer', 'bateau'], motif='bateaux sur la mer au coucher du soleil'),
    'File:Fischerboot vor Bodø.jpg':  # bateau/10
        dict(ajouter=['mer'], motif='bateau en pleine mer'),
    'File:Muelle de Sopot, Polonia, 2013-05-22, DD 20.jpg':  # mer/09
        dict(ajouter=['plage'], motif='grande plage de sable en vue aérienne'),
    'File:Bow shipwreck Nea Artaki Evia Greece.jpg':  # mer/03
        dict(ajouter=['bateau'], motif='épave de bateau'),
    'File:Rostock asv2018-05 img63 Warnemuende port.jpg':  # coucher_soleil/04
        dict(ajouter=['bateau'], motif='paquebot'),
    'File:Birka June 2013.jpg':  # mer/11
        dict(ajouter=['bateau'], motif='paquebot'),
    'File:Bridge and deck of Gullbritt.jpg':  # voiture/09
        dict(ajouter=['bateau'], motif="pont d'un bac chargé de voitures"),
    'File:Leg Rowing Fisherman Inle Lake Myanmar.jpg':  # coucher_soleil/11
        dict(ajouter=['bateau'], motif='pêcheur debout sur sa barque'),
    "File:Iceland's Mid-Atlantic Ridge during snow.jpg":  # neige/02
        dict(ajouter=['montagne'], motif='paysage de montagne enneigé'),
    'File:Sella group panorama.jpg':  # neige/12
        dict(ajouter=['montagne'], motif='paysage de montagne enneigé'),
    'File:Mt Herschel, Antarctica, Jan 2006.jpg':  # mer/01
        dict(ajouter=['neige', 'montagne'], motif="montagnes enneigées au bord de l'eau"),
    'File:Angelus Hut in the winter, Nelson Lakes National Park, New Zealand.jpg':  # montagne/09
        dict(ajouter=['neige'], motif='refuge de montagne sous la neige'),
    'File:Horse-drawn sleighs 2012 G1.jpg':  # cheval/11
        dict(ajouter=['neige'], motif='traîneau à cheval dans la neige'),
    'File:Strandkorb bei Sonnenaufgang.jpg':  # plage/05
        dict(ajouter=['coucher_soleil'], motif="lever de soleil, indiscernible d'un coucher de soleil"),
    'File:Январское побережье Японского моря в Хасанском районе.jpg':  # mer/04
        dict(ajouter=['coucher_soleil'], motif="soleil posé sur l'horizon"),
    'File:Dülmen, Umland -- 2013 -- 2066.jpg':  # cheval/08
        dict(ajouter=['coucher_soleil'], motif="soleil posé sur l'horizon"),
    'File:African boy transporting fodder by bicycle edit.jpg':  # velo/10
        dict(ajouter=['enfant'], motif='enfant à vélo'),
    'File:Hupiti japo mrefu.jpg':  # basket/05
        dict(ajouter=['enfant'], motif='enfants qui jouent au basket'),
    'File:2023-01-07 Jugendfeuerwehr Tauberbischofsheim sammelt Weihnachtsbäume im Stadtgebiet 6.jpg':  # sapin_noel/09
        dict(ajouter=['voiture', 'rue'], motif='rue avec une voiture garée'),
    'File:Saliwangan Sabah Diesel-lokomotive-6105-01.jpg':  # montagne/10
        dict(retirer=['montagne'], motif='locomotive, aucune montagne visible'),
    'File:Autumn Oak - Broadhall Way - Stevenage.jpg':  # chien/07
        dict(retirer=['chien'], motif="chien invisible à la taille d'analyse (quelques pixels)"),
    "File:Drapeau de l'Algérie à la WikiConvention 2023.jpg":  # gateau/08
        dict(retirer=['gateau'], motif='aucun gâteau visible'),
    'File:2026-04-08 ALBA Berlin gegen Unicaja Málaga (Basketball Champions League 2025-26) by Sandro Halank–055.jpg':  # basket/10
        dict(retirer=['basket'], motif='portrait, rien qui évoque le basket-ball'),
    'File:Arch of Santa Rosa in Viterbo.JPG':  # rose/08
        dict(retirer=['rose', 'fleur'], motif='façade sculptée, aucune rose visible'),
    'File:Macropus giganteus - Brunkerville.jpg':  # enfant/06
        dict(retirer=['enfant'], motif='kangourou et son petit'),
    'File:MONDAY-Meme Dsc5436.png':  # meme/04
        dict(ajouter=['chat'], motif='chat bien visible dans le mème'),
    'File:Children in Raisen district, MP, India.jpg':  # enfant/12
        dict(ajouter=['chien'], motif='la fillette tient un chiot dans ses bras'),
    'File:Novascotiannoutaja Karhuluodossa 2.jpg':  # chien/09
        dict(ajouter=['mer', 'plage', 'chien_plage'], motif='chien sur une plage, vagues de la mer'),
    'File:Dhanushkodi seashore3.jpg':  # chien_plage/08
        dict(ajouter=['mer'], motif='chiens au bord de la mer'),
    'File:Doggy takes his bath (3106063943).jpg':  # chien_plage/02
        dict(ajouter=['mer'], motif='chien qui se baigne dans la mer'),
    'File:SL Ussangoda NP asv2020-01 img06.jpg':  # plage/06
        dict(ajouter=['mer'], motif='la mer est bien visible'),
    'File:Honeymoon Bay Sunset 2.jpg':  # coucher_soleil/12
        dict(ajouter=['mer'], motif='coucher de soleil sur la mer'),
    'File:Nørre Vorupør Coast one third sky 2012-11-18.jpg':  # mer/12
        dict(ajouter=['plage'], motif='côte avec une plage de sable'),
    'File:2024 Kłodzko, ul. Wojska Polskiego (3).jpg':  # rue/07
        dict(ajouter=['voiture'], motif='rue bordée de voitures garées'),
    'File:Narimanov, Baku (P1090201).jpg':  # rue/10
        dict(ajouter=['voiture'], motif='rue bordée de voitures garées'),
    'File:Sara - Perro Pastor 01.jpg':  # chien/05
        dict(ajouter=['rue'], motif='chien couché dans une rue'),
    'File:Paris - A waitress making a Phone call - 4588.jpg':  # velo/06
        dict(ajouter=['rue'], motif="terrasse de café sur le trottoir d'une rue"),
    'File:BeachDog.JPG':  # chien_plage/06
        dict(ajouter=['mer'], motif='la mer est bien visible'),
    'File:Wet dog on Formby beach - geograph.org.uk - 266591.jpg':  # chien_plage/09
        dict(ajouter=['mer'], motif='la mer est visible au loin'),
    'File:Vivien - Cendres et poussieres 1902 (page 1 crop).jpg':  # capture_ecran/12
        dict(retirer=['capture_ecran'], motif="page de livre numérisée, pas une capture d'écran"),
}

# Indices d'une image qui n'est pas une photo (tableau, gravure, jouet, scan…), cherchés dans le titre,
# le nom de l'objet et les catégories Commons. « Taken with » (appareil photo) est au contraire bon signe.
NON_PHOTO = re.compile(
    r'\b(paintings?|peintures?|drawings?|dessins?|engravings?|gravures?|illustrations?|lithograph\w*|woodcuts?|'
    r'etchings?|sculptures?|statues?|google art project|postcards?|cartes? postales?|posters?|affiches?|stamps?|'
    r'timbres?|coats? of arms|heraldry|mosaics?|frescos?|frescoes|tapestr\w*|manuscripts?|cartoons?|comics?|'
    r'figurines?|toys?|black and white photographs?|monochrome|bundesarchiv|diagrams?|logos?|clip ?art|'
    r'renders?|x-rays?|microscop\w*|herbarium|specimens?|museums?|musée|exhibits?|scans?|scanned|'
    r'svg|icons?|maquettes?|models? of)\b')


# ---------------------------------------------------------------- accès réseau (une requête à la fois)

_derniere = [0.0]


def _http(url, binaire=False, pause=PAUSE_API, essais=6):
    """GET poli : pause entre deux requêtes, reprise sur 429/5xx en respectant Retry-After."""
    for essai in range(essais):
        attente = pause - (time.monotonic() - _derniere[0])
        if attente > 0:
            time.sleep(attente)
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                contenu = r.read()
            _derniere[0] = time.monotonic()
            return contenu if binaire else json.loads(contenu)
        except urllib.error.HTTPError as e:
            _derniere[0] = time.monotonic()
            if e.code not in (429, 500, 502, 503, 504) or essai == essais - 1:
                raise
            ra = e.headers.get('Retry-After') or ''
            sommeil = min(600, max(5, int(ra) if ra.isdigit() else 30 * (essai + 1)))
            print(f'    (serveur occupé, HTTP {e.code} : pause {sommeil} s)', flush=True)
            time.sleep(sommeil)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            _derniere[0] = time.monotonic()
            if essai == essais - 1:
                raise
            print(f'    (réseau : {e} ; nouvel essai)', flush=True)
            time.sleep(5 * (essai + 1))


_CACHE_API = [None]        # dossier où garder les réponses de l'API : une construction interrompue repart de là


def _api(base, **p):
    """Requête à l'API MediaWiki, suites (« continue ») comprises ; renvoie la liste des réponses."""
    import hashlib
    p.update(format='json', formatversion='2')
    reponses = []
    suite = {}
    occupe = 0
    while True:
        url = base + '?' + urllib.parse.urlencode({**p, **suite})
        memo = _CACHE_API[0] / (hashlib.sha1(url.encode()).hexdigest() + '.json') if _CACHE_API[0] else None
        if memo and memo.is_file():
            d = json.loads(memo.read_text(encoding='utf-8'))
        else:
            d = _http(url)
            if memo and 'error' not in d:
                memo.parent.mkdir(parents=True, exist_ok=True)
                memo.write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')
        if 'error' in d:
            info = str(d['error'].get('info', d['error']))
            # moteur de recherche saturé (« too busy ») ou limite de débit : on attend et on recommence
            if occupe < 6 and ('busy' in info.lower() or d['error'].get('code') in ('ratelimited', 'maxlag')):
                occupe += 1
                print(f'    (API occupée : pause {20 * occupe} s)', flush=True)
                time.sleep(20 * occupe)
                continue
            raise RuntimeError(f'API : {info}')
        reponses.append(d)
        if 'continue' not in d or p.get('list') == 'search':
            return reponses
        suite = d['continue']


# ---------------------------------------------------------------- construction du jeu

def verifier_wikidata(categories):
    """Résout chaque élément par wbsearchentities et vérifie son libellé ; renvoie {Q: {en, fr}}."""
    for c in categories:
        if c.get('tous'):
            continue                                   # éléments déjà vérifiés par les catégories simples
        langue, terme = c.get('recherche', ('en', c['en']))
        d = _api(API_WIKIDATA, action='wbsearchentities', search=terme, language=langue, uselang=langue,
                 type='item', limit=10)[0]
        trouves = {r['id']: r.get('label', '') for r in d.get('search', [])}
        q = c['q'][0]
        if q not in trouves:
            raise RuntimeError(f'{c["cle"]} : {q} absent des résultats de wbsearchentities pour « {terme} »')
        print(f'  {c["cle"]:<20} {q:<9} « {terme} » -> « {trouves[q]} »')
    ids = sorted({q for c in categories for q in c['q']})
    libelles = {}
    for i in range(0, len(ids), 50):
        d = _api(API_WIKIDATA, action='wbgetentities', ids='|'.join(ids[i:i + 50]), props='labels',
                 languages='fr|en|mul')[0]
        for q, ent in d['entities'].items():
            lab = ent.get('labels', {})
            mul = lab.get('mul', {}).get('value', '')          # libellé commun à toutes les langues (taxons…)
            libelles[q] = {k: lab.get(k, {}).get('value', '') or mul for k in ('en', 'fr')}
    for c in categories:
        for q in c['q']:
            print(f'  {c["cle"]:<20} {q:<9} en « {libelles[q]["en"]} » / fr « {libelles[q]["fr"]} »')
    return libelles


def _texte(v):
    """Valeur extmetadata (souvent du HTML) -> texte simple."""
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', v or ''))).strip()


def _annee(v):
    m = re.search(r'\b(1[89]\d\d|20\d\d)\b', _texte(v))
    return int(m.group(1)) if m else None


def _infos_images(titres):
    """imageinfo (vignette 640 px, taille, auteur, licence, catégories) pour au plus 50 titres."""
    infos = {}
    for d in _api(API_COMMONS, action='query', prop='imageinfo', titles='|'.join(titres),
                  iiprop='url|extmetadata|size|mime', iiurlwidth=LARGEUR, iiextmetadatalanguage='fr',
                  iiextmetadatafilter='Artist|LicenseShortName|LicenseUrl|DateTimeOriginal|Categories|ObjectName'):
        for page in d.get('query', {}).get('pages', []):
            if page.get('imageinfo'):
                infos[page['title']] = (page['pageid'], page['imageinfo'][0])
    return infos


def _depeint(pageids):
    """Valeurs P180 (dépeint) et leur rang, pour au plus 50 fichiers : {pageid: [(Q, rang), …]}."""
    sortie = {}
    d = _api(API_COMMONS, action='wbgetentities', ids='|'.join(f'M{p}' for p in pageids), props='claims')[0]
    for mid, ent in d.get('entities', {}).items():
        decl = ent.get('statements') or ent.get('claims') or {}
        if not isinstance(decl, dict):
            decl = {}
        valeurs = []
        for s in decl.get('P180', []):
            snak = s.get('mainsnak', {})
            if snak.get('snaktype') == 'value' and s.get('rank') != 'deprecated':
                valeurs.append((snak['datavalue']['value']['id'], s.get('rank', 'normal')))
        sortie[int(mid[1:])] = valeurs
    return sortie


def _souche(titre):
    """Titre sans chiffres ni ponctuation : repère les séries d'un même auteur (« Chat 2010-1 », « Chat 2010-2 »)."""
    return re.sub(r'[^a-z]+', '', titre.lower().rsplit('.', 1)[0])[:20]


def _candidats(c, q_recherche, deja, page):
    """Une page de résultats de recherche -> candidats filtrés, avec leurs métadonnées."""
    qs = c['q'] if c.get('tous') else [q_recherche]
    srsearch = ' '.join(f'haswbstatement:P180={q}' for q in qs) + ' filetype:bitmap'
    d = _api(API_COMMONS, action='query', list='search', srnamespace=6, srlimit=100, sroffset=100 * page,
             srprop='', srsearch=srsearch)[0]
    titres = [r['title'] for r in d.get('query', {}).get('search', [])]
    rang = {t: 100 * page + i for i, t in enumerate(titres)}
    titres = [t for t in titres if t not in deja and t not in EXCLUS
              and t.lower().rsplit('.', 1)[-1] in ('jpg', 'jpeg', 'png', 'webp')
              and not (c.get('photo', True) and NON_PHOTO.search(t.lower()))]
    retenus = []
    for i in range(0, len(titres), 50):
        infos = _infos_images(titres[i:i + 50])
        bons = {}
        for t, (pageid, ii) in infos.items():
            em = ii.get('extmetadata', {})
            cats = _texte(em.get('Categories', {}).get('value', '')).lower()
            nom = _texte(em.get('ObjectName', {}).get('value', '')).lower()
            larg, haut = ii.get('width', 0), ii.get('height', 0)
            if ii.get('mime') not in ('image/jpeg', 'image/png', 'image/webp') or not ii.get('thumburl'):
                continue
            if min(larg, haut) < (400 if c.get('photo', True) else 300) or not 0.42 <= larg / haut <= 2.4:
                continue
            annee = _annee(em.get('DateTimeOriginal', {}).get('value', ''))
            if c.get('photo', True):
                if NON_PHOTO.search(cats) or NON_PHOTO.search(nom) or (annee and annee < 1990):
                    continue
            bons[t] = dict(pageid=pageid, ii=ii, cats=cats, annee=annee)
        if not bons:
            continue
        depeint = _depeint([b['pageid'] for b in bons.values()])
        for t, b in bons.items():
            valeurs = depeint.get(b['pageid'], [])
            ids = [v for v, _ in valeurs]
            if not all(q in ids for q in qs):
                continue                               # index de recherche en retard sur les données
            em = b['ii'].get('extmetadata', {})
            retenus.append(dict(
                titre=t, rang=rang[t], depeint=ids,
                saillant=any(r == 'preferred' for v, r in valeurs if v in qs),
                appareil='taken with' in b['cats'],
                auteur=_texte(em.get('Artist', {}).get('value', ''))[:120] or 'inconnu',
                licence=_texte(em.get('LicenseShortName', {}).get('value', '')),
                licence_url=_texte(em.get('LicenseUrl', {}).get('value', '')),
                annee=b['annee'], largeur=b['ii'].get('width'), hauteur=b['ii'].get('height'),
                url=b['ii']['thumburl'], page=b['ii'].get('descriptionurl', '')))
    return retenus, len(d.get('query', {}).get('search', [])) == 100


def selectionner(c, deja):
    """Choisit les images d'une catégorie : sujet saillant d'abord, peu d'objets dépeints, vraie photo,
    au plus deux images par auteur et une par série."""
    nombre = c.get('nombre', NOMBRE)
    candidats = []
    for q in (c['q'][:1] if c.get('tous') else c['q']):
        for page in range(3):
            nouveaux, encore = _candidats(c, q, deja | {x['titre'] for x in candidats}, page)
            candidats += nouveaux
            if len(candidats) >= 4 * nombre or not encore:
                break
        if len(candidats) >= 4 * nombre:
            break
    candidats.sort(key=lambda x: (not x['saillant'], min(len(x['depeint']), 6) // 2, not x['appareil'], x['rang']))
    choisis, auteurs, souches = [], {}, set()
    for x in candidats:
        a = x['auteur'].lower()
        if auteurs.get(a, 0) >= 2 or _souche(x['titre']) in souches:
            continue
        choisis.append(x)
        auteurs[a] = auteurs.get(a, 0) + 1
        souches.add(_souche(x['titre']))
        if len(choisis) == nombre:
            break
    if len(choisis) < nombre:
        print(f'  ! {c["cle"]} : seulement {len(choisis)} images sur {nombre}')
    return choisis


def etiquettes_depeint(depeint, cle_source):
    """Catégories auxquelles appartient une image, d'après sa catégorie d'origine et ses valeurs P180."""
    etiq = {cle_source}
    for c in TOUTES:
        if c.get('tous'):
            if all(q in depeint for q in c['q']):
                etiq.add(c['cle'])
        elif any(q in depeint for q in c['q']):
            etiq.add(c['cle'])
    for cle in list(etiq):                             # une rose est une fleur, etc.
        etiq.update(PAR_CLE[cle].get('parents', []))
    return sorted(etiq)


def construire_jeu():
    """Interroge Wikidata et Commons et fige la sélection dans evaluation_jeu.json."""
    print('Vérification des éléments Wikidata (wbsearchentities)…')
    libelles = verifier_wikidata(TOUTES)
    images, deja = [], set()
    for c in TOUTES:
        print(f'Recherche Commons : {c["cle"]}…', flush=True)
        for i, x in enumerate(selectionner(c, deja)):
            deja.add(x['titre'])
            ext = Path(urllib.parse.urlparse(x['url']).path).suffix.lower() or '.jpg'
            images.append(dict(fichier=f'{c["cle"]}/{i + 1:02d}{".jpg" if ext == ".jpeg" else ext}',
                               categorie=c['cle'], titre=x['titre'], page=x['page'], url=x['url'],
                               auteur=x['auteur'], licence=x['licence'], licence_url=x['licence_url'],
                               annee=x['annee'], depeint=x['depeint'], saillant=x['saillant'],
                               etiquettes=etiquettes_depeint(x['depeint'], c['cle'])))
    jeu = dict(source='Wikimedia Commons, données structurées « dépeint » (P180)',
               date=time.strftime('%Y-%m-%d'), wikidata=libelles, images=images)
    JEU.write_text(json.dumps(jeu, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{len(images)} images retenues -> {JEU}')
    return jeu


def _vignette_standard(url, largeur=330):
    """Fichier original (upload.wikimedia.org, limité très tôt) -> sa vignette standard, en cache (thumb.wikimedia.org)."""
    m = re.match(r'https://upload\.wikimedia\.org/wikipedia/commons/([0-9a-f])/([0-9a-f]{2})/([^?]+)', url)
    if not m:
        return None
    a, ab, nom = m.groups()
    return f'https://thumb.wikimedia.org/wikipedia/commons/thumb/{a}/{ab}/{nom}/{largeur}px-{nom}'


def telecharger_images(jeu, dossier):
    """Télécharge les vignettes absentes et écrit le manifeste à côté des images."""
    from PIL import Image
    manquantes = [e for e in jeu['images'] if not (dossier / 'images' / e['fichier']).is_file()]
    # Les vignettes (thumb.wikimedia.org) d'abord : les fichiers originaux, servis sans cache, sont limités bien
    # plus tôt (HTTP 429) ; on les prend en dernier, sans les paramètres de suivi « utm_ » que l'API ajoute.
    manquantes.sort(key=lambda e: '/thumb/' not in e['url'])
    if manquantes:
        print(f'Téléchargement de {len(manquantes)} vignettes…', flush=True)
    for n, e in enumerate(manquantes, 1):
        dest = dossier / 'images' / e['fichier']
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = e['url'].split('?utm_')[0]
        contenu, erreur = None, None
        for u in [x for x in (_vignette_standard(url), url) if x]:
            try:
                contenu = _http(u, binaire=True, pause=PAUSE_IMAGE, essais=3 if u == url else 1)
                break
            except Exception as err:
                erreur = err
        if contenu is None:                            # fichier supprimé de Commons, etc. : on continue sans
            print(f'  ! {e["titre"]} : {erreur}')
            continue
        partiel = dest.with_name(dest.name + '.part')
        partiel.write_bytes(contenu)
        try:
            with Image.open(partiel) as im:
                im.verify()
        except Exception as err:
            print(f'  ! {e["titre"]} : image illisible ({err})')
            partiel.unlink()
            continue
        os.replace(partiel, dest)
        if n % 25 == 0:
            print(f'  {n} / {len(manquantes)}', flush=True)
    manifeste = [dict(fichier=e['fichier'], categorie=e['categorie'], titre=e['titre'], url=e['url'],
                      page=e['page'], auteur=e['auteur'], licence=e['licence'], licence_url=e['licence_url'],
                      depeint=e['depeint'], etiquettes=e['etiquettes']) for e in jeu['images']]
    (dossier / 'manifeste.json').write_text(json.dumps(manifeste, ensure_ascii=False, indent=1), encoding='utf-8')


# ---------------------------------------------------------------- planches-contact (contrôle visuel des étiquettes)

def planche(vignettes, chemin, colonnes=6, cote=170, titre=''):
    """vignettes : [(chemin image, légende)] -> une image JPEG en grille, légendes sous chaque vignette."""
    from PIL import Image, ImageDraw
    lignes = (len(vignettes) + colonnes - 1) // colonnes
    haut_titre = 22 if titre else 0
    toile = Image.new('RGB', (colonnes * cote, haut_titre + lignes * (cote + 28)), 'white')
    dessin = ImageDraw.Draw(toile)
    if titre:
        dessin.text((6, 4), titre, fill='black')
    for i, (p, legende) in enumerate(vignettes):
        x, y = (i % colonnes) * cote, haut_titre + (i // colonnes) * (cote + 28)
        try:
            im = modele.ouvrir_image(p)
            im.thumbnail((cote - 6, cote - 6))
            toile.paste(im, (x + (cote - im.width) // 2, y + (cote - im.height) // 2))
        except Exception:
            dessin.text((x + 6, y + 6), 'illisible', fill='red')
        for k, ligne in enumerate(legende.split('\n')[:2]):
            dessin.text((x + 4, y + cote + 1 + 13 * k), ligne[:28], fill='black')
    chemin.parent.mkdir(parents=True, exist_ok=True)
    toile.save(chemin, quality=85)


def planches_categories(jeu, dossier):
    sortie = dossier / 'planches'
    for c in TOUTES:
        elems = [e for e in jeu['images'] if e['categorie'] == c['cle']]
        vign = [(dossier / 'images' / e['fichier'], f'{Path(e["fichier"]).stem} ' + ','.join(
            x for x in e['etiquettes'] if x != c['cle'])) for e in elems]
        planche(vign, sortie / f'categorie_{c["cle"]}.jpg', colonnes=6, titre=f'{c["cle"]} ({c["fr"]})')
    print(f'Planches-contact : {sortie}')


# ---------------------------------------------------------------- étiquettes finales

def etiquettes_finales(e):
    """Étiquettes P180 recalculées, corrigées par REQUALIFICATIONS (le motif est rapporté à part)."""
    etiq = set(etiquettes_depeint(e['depeint'], e['categorie']))
    r = REQUALIFICATIONS.get(e['titre'])
    if r:
        etiq |= set(r.get('ajouter', []))
        etiq -= set(r.get('retirer', []))
        for cle in list(etiq):
            etiq.update(PAR_CLE[cle].get('parents', []))
    return etiq


# ---------------------------------------------------------------- gabarits de requête

MOTEURS = [m for m in ('rapide', 'base', 'precis', 'tres_precis') if m in modele.MODELES_DISPO]
GABARITS_FR = ['{q}', 'une photo de {q}', 'photo de {q}']
ANGLAIS = 'a photo of a {en}'                  # comparaison seulement : l'interface est en français
GABARITS = GABARITS_FR + [ANGLAIS]

# Concepts absents du jeu : tout ce qu'ils remontent est faux. Mesure directe des fausses détections d'une
# requête « sans réponse », et garde-fou du repli MINIMUM_RESULTATS.
IMPROBABLES = ['girafe', 'astronaute', 'dinosaure', 'volcan en éruption', 'crocodile', 'chameau', 'fusée',
               'gorille', "char d'assaut", 'igloo', 'éléphant', 'pingouin', 'zèbre', 'guitare', 'piano', 'tigre']


def nom_gabarit(g):
    return g.replace('{q}', 'q').replace('{en}', 'en')


def texte_requete(gabarit, c, q):
    """Texte donné au modèle, construit exactement comme dans l'application (recherche.appliquer_gabarit)."""
    if gabarit == ANGLAIS:
        return 'a photo of a ' + c['en']
    return recherche.appliquer_gabarit(gabarit, q)


class Textes:
    """Vecteurs de textes, calculés une seule fois par texte."""

    def __init__(self, moteur):
        self.moteur = moteur
        self.memo = {}

    def preparer(self, textes):
        nouveaux = sorted({t for t in textes if t not in self.memo})
        for i in range(0, len(nouveaux), 32):
            lot = nouveaux[i:i + 32]
            for t, v in zip(lot, self.moteur.vecteurs_textes(lot)):
                self.memo[t] = v

    def __call__(self, t):
        if t not in self.memo:
            self.preparer([t])
        return self.memo[t]


# ---------------------------------------------------------------- vecteurs des images

def elements(jeu, dossier):
    """Images du jeu présentes sur le disque et non écartées."""
    return [e for e in jeu['images'] if e['titre'] not in EXCLUS and (dossier / 'images' / e['fichier']).is_file()]


def vecteurs_images(elems, dossier, cle, vision='fp16'):
    """Vecteurs des images pour un moteur, en cache dans le dossier de travail (même liste = même cache)."""
    signature = json.dumps([[e['fichier'], (dossier / 'images' / e['fichier']).stat().st_size] for e in elems])
    cache = dossier / f'vecteurs_{cle}_{vision}.npz'
    if cache.is_file():
        z = np.load(cache, allow_pickle=False)
        if str(z['signature']) == signature:
            return z['v'], str(z['materiel'])
    mot = modele.Moteur(cle, vision=vision, materiel='carte', charger_texte=False)
    t0 = time.perf_counter()
    morceaux = []
    for i in range(0, len(elems), 16):
        lot = np.stack([modele.pixels(modele.ouvrir_image(dossier / 'images' / e['fichier']), mot.taille_image)
                        for e in elems[i:i + 16]])
        morceaux.append(mot.vecteurs_images(lot))
    v = np.concatenate(morceaux)
    materiel = mot.materiel
    del mot
    print(f'  {cle} : vecteurs de {len(elems)} images sur {materiel}, '
          f'{len(elems) / (time.perf_counter() - t0):.0f} images/s décodage compris', file=sys.stderr, flush=True)
    np.savez(cache, v=v, signature=np.array(signature), materiel=np.array(materiel))
    return v, materiel


# ---------------------------------------------------------------- mesures

GRILLE = np.unique(np.concatenate([np.geomspace(1e-7, 0.99, 1000), [
    1e-6, 2e-6, 5e-6, 1e-5, 2e-5, 5e-5, 1e-4, 2e-4, 3e-4, 5e-4, 7e-4, 1e-3, 1.5e-3, 2e-3, 3e-3, 5e-3, 7e-3,
    0.01, 0.015, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2, 0.3, 0.5]]))
AFFICHES = [1e-5, 1e-4, 3e-4, 1e-3, 2e-3, 3e-3, 5e-3, 0.01, 0.02, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5]


def precision_moyenne(scores, y):
    """AP : moyenne des précisions au rang de chaque bonne photo (1 = classement parfait)."""
    ordre = np.argsort(-scores, kind='stable')
    r = y[ordre].astype(np.float64)
    if r.sum() == 0:
        return float('nan')
    return float((np.cumsum(r) / np.arange(1, len(r) + 1) * r).sum() / r.sum())


def precision_a(scores, y, k=10):
    return float(y[np.argsort(-scores, kind='stable')[:k]].mean())


def courbes(P, Y, seuils):
    """Précision et rappel pour chaque seuil. P, Y : (images, requêtes). Renvoie des tableaux (seuils, …)."""
    garde = P[None, :, :] >= seuils[:, None, None]
    tp = (garde & Y[None]).sum(1)
    nres = garde.sum(1)
    npos = Y.sum(0)
    nneg = Y.shape[0] - npos
    prec = np.where(nres > 0, tp / np.maximum(nres, 1), np.nan)
    with np.errstate(invalid='ignore'):
        vides = (nres == 0).sum(1)
        macro_p = np.array([np.nanmean(l) if np.isfinite(l).any() else np.nan for l in prec])
    return dict(seuils=seuils, tp=tp, nres=nres, prec=prec, rap=tp / npos,
                macro_p=macro_p, macro_r=(tp / npos).mean(1),
                micro_p=tp.sum(1) / np.maximum(nres.sum(1), 1), micro_r=tp.sum(1) / npos.sum(),
                fpr=(nres - tp).sum(1) / nneg.sum(), vides=vides)


def point(P, Y, s):
    """Mesures à un seul seuil."""
    cs = courbes(P, Y, np.array([float(s)]))
    return {k: v[0] for k, v in cs.items() if k != 'seuils'}


def seuil_precision(cc, cible=0.9, minimum=20):
    """Plus bas seuil où la précision moyenne par requête atteint la cible et à partir duquel la précision globale
    reste >= cible pour tous les seuils plus hauts. Tout en haut, quand il reste moins de `minimum` résultats en
    tout, une seule image difficile ferait tout basculer : ces seuils-là ne comptent pas."""
    ok = (cc['nres'].sum(1) < minimum) | (cc['micro_p'] >= cible)
    env = np.logical_and.accumulate(ok[::-1])[::-1] & ~(cc['macro_p'] < cible)
    return float(cc['seuils'][np.argmax(env)]) if env.any() else float('nan')


def seuil_rappel(cc, cible):
    """Plus haut seuil qui garde un rappel moyen >= cible."""
    ok = np.where(cc['macro_r'] >= cible)[0]
    return float(cc['seuils'][ok.max()]) if len(ok) else float('nan')


def seuil_propre(p, y, cible=0.9):
    """Seuil le plus bas qui garde une précision >= cible pour cette requête, et le rappel obtenu."""
    ordre = np.argsort(-p, kind='stable')
    r = y[ordre]
    prec = np.cumsum(r) / np.arange(1, len(r) + 1)
    ok = np.where(prec >= cible)[0]
    if not len(ok):
        return None, 0.0
    k = ok.max()
    return float(p[ordre][k]), float(r[:k + 1].sum() / r.sum())


def scores_relatifs(C):
    """Cosinus (images, requêtes) -> écart à la masse de la collection, requête par requête, en écarts-types
    robustes : (cos - médiane) / (1,4826 × écart absolu médian). Même calcul que recherche.ecarts()."""
    med = np.median(C, axis=0)
    mad = np.median(np.abs(C - med), axis=0) * 1.4826
    return (C - med) / np.maximum(mad, 1e-6)


def repli(P, C, Y, peut, k, ecart):
    """Repli MINIMUM_RESULTATS : quand moins de k photos atteignent SEUIL_PEUT_ETRE, montrer quand même les
    meilleures jusqu'à k, pourvu (si ecart n'est pas None) que leur score se détache de la masse de la
    collection d'au moins `ecart` écarts-types robustes. Renvoie, par requête : (photos ajoutées, dont vraies)."""
    Z = scores_relatifs(C)
    ajout = np.zeros(P.shape[1], int)
    vrais = np.zeros(P.shape[1], int)
    for j in range(P.shape[1]):
        garde = P[:, j] >= peut
        if k and garde.sum() < k:
            for i in np.argsort(-C[:, j], kind='stable')[:k]:
                if not garde[i] and (ecart is None or Z[i, j] >= ecart):
                    ajout[j] += 1
                    vrais[j] += bool(Y[i, j])
    return ajout, vrais


def repli_rare(P, C, Y, peut, k, ecart):
    """Concept rare : pour chaque vraie image, collection = toutes les images sans l'objet + cette seule image.
    Renvoie (part des vraies images montrées sans repli, avec repli, fausses ajoutées par le repli, nombre de cas
    où il joue). Mesure ce que le repli MINIMUM_RESULTATS apporte quand l'objet n'apparaît qu'une fois."""
    sans = avec = ajouts = cas = total = 0
    for j in range(P.shape[1]):
        neg = np.flatnonzero(~Y[:, j])
        for i in np.flatnonzero(Y[:, j]):
            idx = np.append(neg, i)
            p, c = P[idx, j], C[idx, j]
            ok = p >= peut
            vu = bool(ok[-1])
            total += 1
            sans += vu
            if k and ok.sum() < k:
                cas += 1
                top = np.argsort(-c, kind='stable')[:k]
                if ecart is not None:
                    med = np.median(c)
                    z = (c[top] - med) / max(float(np.median(np.abs(c - med))) * 1.4826, 1e-6)
                    top = top[ok[top] | (z >= ecart)]
                vu = vu or (len(idx) - 1) in top
                ajouts += int(sum(1 for x in top if not ok[x] and x != len(idx) - 1))
            avec += vu
    return sans / total, avec / total, ajouts, cas


def arrondi(x):
    """Deux chiffres significatifs : un seuil lisible."""
    if not x or not np.isfinite(x):
        return x
    e = int(np.floor(np.log10(abs(x)))) - 1
    return round(x, -e)


# ---------------------------------------------------------------- présentation

def tableau(entetes, lignes, md=False):
    lignes = [[str(x) for x in l] for l in lignes]
    if md:
        print('| ' + ' | '.join(entetes) + ' |')
        print('|' + '|'.join('---' if i == 0 else '---:' for i in range(len(entetes))) + '|')
        for l in lignes:
            print('| ' + ' | '.join(l) + ' |')
        return
    larg = [max(len(str(h)), *(len(l[i]) for l in lignes)) for i, h in enumerate(entetes)]
    print('  '.join(str(h).ljust(larg[i]) if i == 0 else str(h).rjust(larg[i]) for i, h in enumerate(entetes)))
    for l in lignes:
        print('  '.join(x.ljust(larg[i]) if i == 0 else x.rjust(larg[i]) for i, x in enumerate(l)))


def f2(x):
    return '—' if x is None or not np.isfinite(x) else f'{x:.2f}'


def fpct(x):
    return '—' if x is None or not np.isfinite(x) else f'{100 * x:.0f} %'


def fprob(x):
    if x is None or not np.isfinite(x):
        return '—'
    return f'{x:.2g}' if x < 0.01 else f'{x:.3f}'


def titre(t):
    print('\n' + t + '\n' + '=' * len(t))


# ---------------------------------------------------------------- évaluation d'un moteur

def evaluer_moteur(cle, elems, etiq, dossier, md=False, gabarit_impose=None, suggestions=False):
    """Toutes les mesures d'un moteur. Le détail s'imprime (rapport) ; le résumé est renvoyé."""
    V, materiel = vecteurs_images(elems, dossier, cle)
    mot = modele.Moteur(cle, texte='fp16', charger_vision=False)
    textes = Textes(mot)
    simples = [c['cle'] for c in CATEGORIES]
    besoin = [texte_requete(g, c, c['fr']) for g in GABARITS for c in TOUTES]
    besoin += [texte_requete(g, c, v) for g in GABARITS_FR for c in TOUTES for v in c.get('variantes', {}).values()]
    besoin += [texte_requete(g, None, q) for g in GABARITS_FR for q in IMPROBABLES]
    t0 = time.perf_counter()
    textes.preparer(besoin)
    ms = (time.perf_counter() - t0) / max(1, len(textes.memo)) * 1000
    titre(f'Moteur « {cle} » : {len(elems)} images (vision : {materiel}), {len(textes.memo)} textes '
          f'({ms:.1f} ms/texte par lots de 32, texte : {mot.materiel_texte})')

    def y_de(k):
        return np.array([k in s for s in etiq])

    Y = np.stack([y_de(k) for k in simples], 1)
    R = {}

    def bloc(g):
        if g not in R:
            C = np.stack([V @ textes(texte_requete(g, PAR_CLE[k], PAR_CLE[k]['fr'])) for k in simples], 1)
            P = modele.probabilite(C, cle)
            cc = courbes(P, Y, GRILLE)
            sur90 = seuil_precision(cc)
            R[g] = dict(C=C, P=P, cc=cc, sur90=sur90, peut95=seuil_rappel(cc, 0.95), peut90=seuil_rappel(cc, 0.9),
                        ap=np.array([precision_moyenne(P[:, j], Y[:, j]) for j in range(len(simples))]),
                        p10=np.array([precision_a(P[:, j], Y[:, j]) for j in range(len(simples))]),
                        au90=point(P, Y, sur90) if np.isfinite(sur90) else None)
        return R[g]

    for g in GABARITS:
        bloc(g)

    def merite(g):
        return (round(float(np.mean(R[g]['ap'])), 2), float(R[g]['au90']['macro_r']) if R[g]['au90'] else 0.0)

    suggere = max(GABARITS_FR, key=merite)
    reg = None if suggestions else (getattr(recherche, 'REGLAGES', None) or {}).get(cle)
    g = gabarit_impose or (reg['gabarit'] if reg else suggere)
    b = bloc(g)
    P, C = b['P'], b['C']
    sur_sugg, peut_sugg = arrondi(b['sur90']), arrondi(b['peut95'])
    if reg and not gabarit_impose:
        sur, peut = reg['seuil_sur'], reg['seuil_peut_etre']
        k_min, ecart = reg.get('minimum', 0), reg.get('ecart_minimum')
        origine = 'réglages en service (myphotos/recherche.py)'
    else:
        sur, peut, k_min, ecart = sur_sugg, peut_sugg, 0, None
        origine = 'suggestion automatique'

    # --- gabarits
    titre(f'Gabarits : classement et seuil de précision 90 % ({len(simples)} catégories simples)')
    lignes = []
    for x in R:
        a = R[x]['au90']
        lignes.append([nom_gabarit(x), f2(np.mean(R[x]['ap'])), f2(np.mean(R[x]['p10'])), fprob(R[x]['sur90']),
                       fpct(a['macro_p']) if a else '—', fpct(a['macro_r']) if a else '—',
                       f'{1000 * a["fpr"]:.1f}' if a else '—', fprob(R[x]['peut95']), fprob(R[x]['peut90'])])
    tableau(['gabarit', 'AP moy.', 'P@10 moy.', 'seuil préc. 90 %', 'préc. moy.', 'rappel moy.', 'fausses /1000',
             'seuil rappel 95 %', 'seuil rappel 90 %'], lignes, md)
    print(f'Gabarit suggéré : « {suggere} » (meilleure AP moyenne à 0,01 près, puis meilleur rappel à 90 % de '
          f'précision). Analysé ci-dessous : « {g} » ; seuils : {origine}.')

    titre('AP par catégorie et par gabarit')
    lignes = [[f'{k} ({int(Y[:, j].sum())})'] + [f2(R[x]['ap'][j]) for x in R] for j, k in enumerate(simples)]
    lignes.append(['MOYENNE'] + [f2(np.mean(R[x]['ap'])) for x in R])
    tableau(['catégorie (vraies)'] + [nom_gabarit(x) for x in R], lignes, md)

    # --- distribution des probabilités
    titre(f'Distribution de modele.probabilite() — « {g} »')
    quant = [0.01, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99]
    pos, neg = P[Y], P[~Y]
    tableau(['', 'nombre'] + [f'q{int(q * 100)}' for q in quant],
            [['vraies', str(len(pos))] + [fprob(np.quantile(pos, q)) for q in quant],
             ['fausses', str(len(neg))] + [fprob(np.quantile(neg, q)) for q in quant]], md)
    bornes = [0, 1e-5, 1e-4, 1e-3, 3e-3, 0.01, 0.03, 0.1, 0.3, 1.0001]
    lignes = []
    for a, bb in zip(bornes[:-1], bornes[1:]):
        dp, dn = (pos >= a) & (pos < bb), (neg >= a) & (neg < bb)
        lignes.append([f'[{fprob(a)} ; {fprob(min(bb, 1))}[', fpct(dp.mean()), fpct(dn.mean()), str(int(dp.sum())),
                       str(int(dn.sum()))])
    print()
    tableau(['probabilité', 'vraies', 'fausses', 'vraies (nb)', 'fausses (nb)'], lignes, md)
    print('\nTous les gabarits : médiane vraies / médiane fausses ; q99 fausses')
    for x in R:
        Px = R[x]['P']
        print(f'  {nom_gabarit(x):<22} {fprob(np.median(Px[Y]))} / {fprob(np.median(Px[~Y]))} ; '
              f'{fprob(np.quantile(Px[~Y], 0.99))}')

    # --- seuils
    titre(f'Seuils — « {g} » (moyennes sur {len(simples)} catégories)')
    lignes = []
    for s in sorted(set(AFFICHES) | {sur, peut, sur_sugg, peut_sugg}):
        m = point(P, Y, s)
        repere = ' <- SÛR' if s == sur else (' <- PEUT-ÊTRE' if s == peut else '')
        lignes.append([fprob(s) + repere, fpct(m['macro_p']), fpct(m['micro_p']), fpct(m['macro_r']),
                       fpct(m['micro_r']), f'{1000 * m["fpr"]:.1f}', str(int(m['vides']))])
    tableau(['seuil', 'préc. moy.', 'préc. globale', 'rappel moy.', 'rappel global', 'fausses /1000',
             'requêtes vides'], lignes, md)
    print(f'Suggestion : SÛR {fprob(sur_sugg)} (précision >= 90 % au-dessus), PEUT-ÊTRE {fprob(peut_sugg)} '
          f'(rappel moyen >= 95 %). « fausses /1000 » : photos sans l\'objet qui passent, pour 1 000 photos sans '
          f'l\'objet.')

    titre(f'Par catégorie : SÛR >= {fprob(sur)}, PEUT-ÊTRE >= {fprob(peut)}')
    lignes = []
    for j, k in enumerate(simples):
        p, y = P[:, j], Y[:, j]
        rs, rp = p >= sur, p >= peut
        s90, r90 = seuil_propre(p, y)
        lignes.append([k, f2(b['ap'][j]), f2(b['p10'][j]), f'{(rs & y).sum()}/{rs.sum()}',
                       fpct((rs & y).sum() / rs.sum()) if rs.sum() else '—', fpct((rs & y).sum() / y.sum()),
                       f'{(rp & y).sum()}/{rp.sum()}', fpct((rp & y).sum() / rp.sum()) if rp.sum() else '—',
                       fpct((rp & y).sum() / y.sum()), fprob(np.median(p[y])), fprob(s90), fpct(r90)])
    tableau(['catégorie', 'AP', 'P@10', 'sûres bons/montrés', 'préc.', 'rappel', 'cumul bons/montrés', 'préc.',
             'rappel', 'médiane vraies', 'seuil propre 90 %', 'rappel propre'], lignes, md)

    # --- requêtes sans réponse et repli
    Ci = np.stack([V @ textes(texte_requete(g, None, q)) for q in IMPROBABLES], 1)
    Pi = modele.probabilite(Ci, cle)
    Yi = np.zeros(Pi.shape, bool)
    Zi = scores_relatifs(Ci)
    titre('Requêtes sans réponse dans le jeu (concepts absents) : tout résultat est faux')
    lignes = [[q, str(int((Pi[:, j] >= sur).sum())), str(int(((Pi[:, j] >= peut) & (Pi[:, j] < sur)).sum())),
               fprob(Pi[:, j].max()), f'{Zi[:, j].max():.1f}', elems[int(np.argmax(Pi[:, j]))]['fichier']]
              for j, q in enumerate(IMPROBABLES)]
    tableau(['requête', 'sûres', 'moins sûres', 'proba max', 'écart max', 'meilleure image'], lignes, md)

    Z = scores_relatifs(C)
    titre('Écart à la masse (écarts-types robustes) : vraies, fausses, meilleure image des requêtes absentes')
    zq = [0.05, 0.25, 0.5, 0.75, 0.95, 0.99]
    tableau(['', *[f'q{int(q * 100)}' for q in zq], 'max'],
            [['vraies'] + [f'{np.quantile(Z[Y], q):.1f}' for q in zq] + [f'{Z[Y].max():.1f}'],
             ['fausses'] + [f'{np.quantile(Z[~Y], q):.1f}' for q in zq] + [f'{Z[~Y].max():.1f}'],
             ['max des absentes'] + [f'{np.quantile(Zi.max(0), q):.1f}' for q in zq] + [f'{Zi.max():.1f}']], md)

    titre('Repli « au moins k résultats » (MINIMUM_RESULTATS) au seuil PEUT-ÊTRE, avec ou sans condition d\'écart')
    lignes = []
    for kk in (3, 5, 8):
        for ec in (None, 3, 4, 5, 6, 8, 10):
            a, v = repli(P, C, Y, peut, kk, ec)
            ai, _ = repli(Pi, Ci, Yi, peut, kk, ec)
            lignes.append([str(kk), 'aucune' if ec is None else f'>= {ec}', str(int((a > 0).sum())),
                           str(int(v.sum())), str(int((a - v).sum())), str(int((ai > 0).sum())), str(int(ai.sum()))])
    tableau(['k', 'écart', 'catégories repêchées', 'vraies ajoutées', 'fausses ajoutées', 'absentes repêchées',
             'fausses ajoutées (absentes)'], lignes, md)

    titre("Repli et concept rare : une seule photo de l'objet dans la collection (toutes les autres sans)")
    lignes = []
    for kk, ec in [(0, None), (3, None), (3, 4), (3, 5), (5, None), (5, 4), (5, 5), (5, 6), (8, 5)]:
        s0, s1, aj, cas = repli_rare(P, C, Y, peut, kk, ec)
        lignes.append([str(kk), 'aucune' if ec is None else f'>= {ec}', fpct(s0), fpct(s1), str(cas), str(aj)])
    tableau(['k', 'écart', 'photo trouvée sans repli', 'avec repli', 'cas où le repli joue', 'fausses ajoutées'],
            lignes, md)

    # --- erreurs
    titre(f'Fausses images les mieux classées, vraies les plus mal classées — « {g} »')
    for j, k in enumerate(simples):
        p, y = P[:, j], Y[:, j]
        ordre = np.argsort(-p, kind='stable')
        faux = [i for i in ordre if not y[i]][:5]
        vrais = [i for i in ordre[::-1] if y[i]][:3]
        print(f'  {k:<15} fausses : ' + ' ; '.join(f'{elems[i]["fichier"]} {fprob(p[i])}' for i in faux))
        print(f'  {"":<15} vraies mal classées : ' + ' ; '.join(
            f'{elems[i]["fichier"]} {fprob(p[i])} (rang {int(np.where(ordre == i)[0][0]) + 1})' for i in vrais))

    # --- robustesse
    titre(f'Robustesse de la requête — « {g} »')
    lignes, rob = [], []
    for c in TOUTES:
        y = y_de(c['cle'])
        t_ref = texte_requete(g, c, c['fr'])
        p_ref = modele.probabilite(V @ textes(t_ref), cle)
        ap_ref = precision_moyenne(p_ref, y)
        for nomv, q in c.get('variantes', {}).items():
            vq = textes(texte_requete(g, c, q))
            p = modele.probabilite(V @ vq, cle)
            ap = precision_moyenne(p, y)
            r_v, r_ref = float((p[y] >= sur).mean()), float((p_ref[y] >= sur).mean())
            rc_v, rc_ref = float((p[y] >= peut).mean()), float((p_ref[y] >= peut).mean())
            lignes.append([f'{c["cle"]} : {nomv}', f'« {q} »', f2(ap), f2(ap_ref), f'{ap - ap_ref:+.2f}',
                           fpct(r_v), fpct(r_ref), fpct(rc_v), fpct(rc_ref), str(int((p[~y] >= sur).sum())),
                           f'{float(vq @ textes(t_ref)):.3f}'])
            rob.append(dict(categorie=c['cle'], variante=nomv, requete=q, ap=ap, ap_ref=ap_ref, rappel_sur=r_v,
                            rappel_sur_ref=r_ref, rappel_cumul=rc_v, rappel_cumul_ref=rc_ref,
                            fausses_sures=int((p[~y] >= sur).sum()), cos_ref=float(vq @ textes(t_ref))))
    tableau(['catégorie : variante', 'requête', 'AP', 'AP réf.', 'écart', 'rappel sûres', 'réf.', 'rappel cumul',
             'réf.', 'fausses sûres', 'cos/réf.'], lignes, md)

    titre('Requêtes de plusieurs mots : le sous-ensemble visé (images qui ont les deux)')
    lignes, comb = [], []
    for c in COMBINEES:
        y = y_de(c['cle'])
        for q, cq in [(c['fr'], c['cle'])] + [(PAR_CLE[x]['fr'], x) for x in c.get('parents', [])]:
            p = modele.probabilite(V @ textes(texte_requete(g, PAR_CLE[cq], q)), cle)
            lignes.append([c['cle'], f'« {q} »', f2(precision_moyenne(p, y)), f2(precision_a(p, y, 10)),
                           f'{(p[y] >= sur).sum()}/{y.sum()}', str(int((p[~y] >= sur).sum()))])
            comb.append(dict(cible=c['cle'], requete=q, ap=precision_moyenne(p, y), p10=precision_a(p, y, 10)))
    tableau(['images visées', 'requête', 'AP', 'P@10', 'vraies sûres', 'autres sûres'], lignes, md)

    au_sur, au_peut = point(P, Y, sur), point(P, Y, peut)
    a_rep, v_rep = repli(P, C, Y, peut, k_min, ecart)
    ai_rep, _ = repli(Pi, Ci, Yi, peut, k_min, ecart)
    rare = repli_rare(P, C, Y, peut, k_min, ecart)

    def mesures(m):
        return dict(prec_moy=m['macro_p'], prec_globale=m['micro_p'], rappel_moy=m['macro_r'],
                    rappel_global=m['micro_r'], fausses_1000=1000 * m['fpr'], requetes_vides=int(m['vides']))

    return dict(
        cle=cle, materiel=materiel, materiel_texte=mot.materiel_texte, ms_texte=ms, images=len(elems),
        gabarit=g, gabarit_suggere=suggere, origine=origine, seuil_sur=sur, seuil_peut_etre=peut, minimum=k_min,
        ecart_minimum=ecart, suggestion=dict(seuil_sur=sur_sugg, seuil_peut_etre=peut_sugg),
        gabarits={nom_gabarit(x): dict(
            ap=float(np.mean(R[x]['ap'])), p10=float(np.mean(R[x]['p10'])), seuil_precision_90=R[x]['sur90'],
            rappel_a_90=float(R[x]['au90']['macro_r']) if R[x]['au90'] else None,
            fausses_1000_a_90=1000 * float(R[x]['au90']['fpr']) if R[x]['au90'] else None,
            seuil_rappel_95=R[x]['peut95'], mediane_vraies=float(np.median(R[x]['P'][Y])),
            mediane_fausses=float(np.median(R[x]['P'][~Y]))) for x in R},
        categories={k: dict(ap=float(b['ap'][j]), p10=float(b['p10'][j]), vraies=int(Y[:, j].sum()),
                            rappel_sur=float((P[Y[:, j], j] >= sur).mean()),
                            mediane_vraies=float(np.median(P[Y[:, j], j]))) for j, k in enumerate(simples)},
        sures=mesures(au_sur), cumul=mesures(au_peut),
        distribution=dict(vraies={f'q{int(q * 100)}': float(np.quantile(pos, q)) for q in quant},
                          fausses={f'q{int(q * 100)}': float(np.quantile(neg, q)) for q in quant}),
        absentes=dict(sures_par_requete=float(np.mean((Pi >= sur).sum(0))),
                      peut_etre_par_requete=float(np.mean(((Pi >= peut) & (Pi < sur)).sum(0))),
                      requetes_avec_sures=int(((Pi >= sur).sum(0) > 0).sum()), requetes=len(IMPROBABLES)),
        repli=dict(categories_repechees=int((a_rep > 0).sum()), vraies_ajoutees=int(v_rep.sum()),
                   fausses_ajoutees=int((a_rep - v_rep).sum()), absentes_repechees=int((ai_rep > 0).sum()),
                   fausses_ajoutees_absentes=int(ai_rep.sum()), rare_trouvees_sans_repli=rare[0],
                   rare_trouvees_avec_repli=rare[1], rare_fausses_ajoutees=rare[2], rare_cas=rare[3]),
        robustesse=rob, combinees=comb,
        _P=P, _Z=Z, _Y=Y)


# ---------------------------------------------------------------- synthèse des moteurs

def synthese(resultats, md=False):
    titre('Gabarits par moteur : AP moyenne / P@10 moyenne / rappel moyen au seuil de précision 90 %')
    noms = list(next(iter(resultats.values()))['gabarits'])
    lignes = []
    for cle, r in resultats.items():
        lignes.append([cle] + [f'{f2(r["gabarits"][n]["ap"])} / {f2(r["gabarits"][n]["p10"])} / '
                               f'{fpct(r["gabarits"][n]["rappel_a_90"] if r["gabarits"][n]["rappel_a_90"] is not None else float("nan"))}'
                               for n in noms])
    tableau(['moteur'] + noms, lignes, md)

    titre('Réglages et résultats par moteur (24 catégories simples)')
    lignes = []
    for cle, r in resultats.items():
        s, c, a = r['sures'], r['cumul'], r['absentes']
        lignes.append([cle, r['gabarit'], fprob(r['seuil_sur']), fpct(s['prec_moy']), fpct(s['rappel_moy']),
                       f'{s["fausses_1000"]:.1f}', fprob(r['seuil_peut_etre']), fpct(c['prec_moy']),
                       fpct(c['rappel_moy']), f'{c["fausses_1000"]:.1f}',
                       f'{a["sures_par_requete"]:.2f} / {a["peut_etre_par_requete"]:.2f}',
                       f'{r["minimum"]}' + (f' (écart {r["ecart_minimum"]})' if r['minimum'] else '')])
    tableau(['moteur', 'gabarit', 'SÛR', 'préc.', 'rappel', 'fausses /1000', 'PEUT-ÊTRE', 'préc. cumul',
             'rappel cumul', 'fausses /1000', 'absentes : sûres / moins sûres par requête', 'minimum'], lignes, md)
    print('Origine des seuils : ' + ' ; '.join(f'{k} = {r["origine"]}' for k, r in resultats.items()))
    print('Suggestions automatiques : ' + ' ; '.join(
        f'{k} « {r["gabarit_suggere"]} » SÛR {fprob(r["suggestion"]["seuil_sur"])} PEUT-ÊTRE '
        f'{fprob(r["suggestion"]["seuil_peut_etre"])}' for k, r in resultats.items()))

    titre('Distribution des probabilités (gabarit retenu) : vraies q10 / q50 / q90 ; fausses q50 / q99')
    lignes = [[cle, r['gabarit'], *(fprob(r['distribution']['vraies'][q]) for q in ('q10', 'q50', 'q90')),
               *(fprob(r['distribution']['fausses'][q]) for q in ('q50', 'q99'))] for cle, r in resultats.items()]
    tableau(['moteur', 'gabarit', 'vraies q10', 'q50', 'q90', 'fausses q50', 'q99'], lignes, md)

    titre('Robustesse (gabarit retenu) : écart moyen d\'AP et de rappel « sûres » par type de variante')
    types = sorted({x['variante'] for r in resultats.values() for x in r['robustesse']})
    lignes = []
    for t in types:
        l = [t, str(sum(1 for x in next(iter(resultats.values()))['robustesse'] if x['variante'] == t))]
        for r in resultats.values():
            xs = [x for x in r['robustesse'] if x['variante'] == t]
            l.append(f'{np.nanmean([x["ap"] - x["ap_ref"] for x in xs]):+.2f} / '
                     f'{100 * np.nanmean([x["rappel_sur"] - x["rappel_sur_ref"] for x in xs]):+.0f} pts')
        lignes.append(l)
    tableau(['variante', 'cas'] + list(resultats), lignes, md)

    titre('Par catégorie (gabarit retenu) : AP et rappel « sûres »')
    cats = list(next(iter(resultats.values()))['categories'])
    lignes = [[k] + [f'{f2(r["categories"][k]["ap"])} / {fpct(r["categories"][k]["rappel_sur"])}'
                     for r in resultats.values()] for k in cats]
    tableau(['catégorie'] + list(resultats), lignes, md)


def consensus_erreurs(resultats, elems, dossier, planches=False):
    """Fausses images que plusieurs moteurs classent haut : étiquette P180 sans doute incomplète.
    Classement commun = moyenne des écarts relatifs des moteurs. Écrit la liste (et les planches)."""
    simples = [c['cle'] for c in CATEGORIES]
    Z = np.mean([r['_Z'] for r in resultats.values()], axis=0)
    Y = next(iter(resultats.values()))['_Y']
    lignes = []
    for j, k in enumerate(simples):
        ordre = np.argsort(-Z[:, j], kind='stable')
        for i in [i for i in ordre if not Y[i, j]][:6]:
            sures = sum(1 for r in resultats.values() if r['_P'][i, j] >= r['seuil_sur'])
            lignes.append(f'{k:<15} F z={Z[i, j]:5.1f} sûre pour {sures}/{len(resultats)}  {elems[i]["fichier"]:<22} '
                          f'{elems[i]["titre"]}')
        for i in [i for i in ordre[::-1] if Y[i, j]][:3]:
            lignes.append(f'{k:<15} V z={Z[i, j]:5.1f} rang {int(np.where(ordre == i)[0][0]) + 1:>3}         '
                          f'{elems[i]["fichier"]:<22} {elems[i]["titre"]}')
    (dossier / 'erreurs_consensus.txt').write_text('\n'.join(lignes), encoding='utf-8')
    if planches:
        planches_erreurs(elems, Z, Y, simples, dossier / 'planches', 'consensus des moteurs (écart moyen)')


def planches_erreurs(elems, S, Y, cles, sortie, g):
    """Une ligne par catégorie : 6 fausses images les mieux classées (F), puis 3 vraies les plus mal classées (V)."""
    par_planche = 4
    for debut in range(0, len(cles), par_planche):
        vign = []
        for j in range(debut, min(debut + par_planche, len(cles))):
            s, y = S[:, j], Y[:, j]
            ordre = np.argsort(-s, kind='stable')
            faux = [i for i in ordre if not y[i]][:6]
            vrais = [i for i in ordre[::-1] if y[i]][:3][::-1]
            for i in faux:
                vign.append((sortie.parent / 'images' / elems[i]['fichier'], f'{cles[j]} F {s[i]:.1f}\n{elems[i]["fichier"]}'))
            for i in vrais:
                vign.append((sortie.parent / 'images' / elems[i]['fichier'], f'{cles[j]} V {s[i]:.1f}\n{elems[i]["fichier"]}'))
        planche(vign, sortie / f'erreurs_{debut // par_planche + 1}.jpg', colonnes=9, cote=150,
                titre=f'{g} : F = fausse bien classée, V = vraie mal classée')
    print(f'Planches des erreurs : {sortie}')


# ---------------------------------------------------------------- contrôle sur une vraie collection (nombres seulement)

def controle_collection(racine, cle, maximum=None):
    """Compte, sur une vraie collection, les photos qui passent les seuils en service pour ce moteur.
    Ne montre aucune image, n'affiche aucun nom de fichier et n'enregistre rien : seulement des nombres.

    Les requêtes absentes (girafe, astronaute…) estiment le nombre de fausses détections par requête
    sur des photos de famille, que le jeu Commons ne représente qu'en partie."""
    from concurrent.futures import ThreadPoolExecutor
    from myphotos import config
    reg = recherche.reglages(cle)
    chemins = sorted(p for p in Path(racine).rglob('*') if p.suffix.lower() in config.EXTENSIONS)
    if maximum and len(chemins) > maximum:
        chemins = chemins[::len(chemins) // maximum][:maximum]
    mot = modele.Moteur(cle, vision='fp16', materiel='carte', charger_texte=False)

    def charger(p):
        try:
            im = modele.ouvrir_image(p)
            return None if min(im.size) < config.COTE_MIN else modele.pixels(im, mot.taille_image)
        except Exception:
            return None

    t0 = time.perf_counter()
    morceaux = []
    with ThreadPoolExecutor(6) as ex:
        for i in range(0, len(chemins), 256):             # par paquets : la mémoire reste bornée
            lot = [a for a in ex.map(charger, chemins[i:i + 256]) if a is not None]
            for j in range(0, len(lot), 32):
                morceaux.append(mot.vecteurs_images(np.stack(lot[j:j + 32])))
    V = np.concatenate(morceaux)
    del mot
    print(f'{len(V)} photos lues sur {len(chemins)} en {time.perf_counter() - t0:.0f} s')
    textes = Textes(modele.Moteur(cle, texte='fp16', charger_vision=False))
    requetes = [c['fr'] for c in CATEGORIES] + IMPROBABLES
    lignes, fausses_sur, fausses_peut = [], [], []
    for q in requetes:
        p = modele.probabilite(V @ textes(recherche.appliquer_gabarit(reg['gabarit'], q)), cle)
        n_sur, n_peut = int((p >= reg['seuil_sur']).sum()), int(((p >= reg['seuil_peut_etre']) & (p < reg['seuil_sur'])).sum())
        if q in IMPROBABLES:
            fausses_sur.append(n_sur)
            fausses_peut.append(n_peut)
        lignes.append([('(absente) ' if q in IMPROBABLES else '') + q, str(n_sur), str(n_peut), fprob(p.max()),
                       fprob(np.sort(p)[-min(5, len(p))])])
    titre(f'Collection réelle, moteur {cle} : nombre de photos par requête (« {reg["gabarit"]} », '
          f'SÛR {fprob(reg["seuil_sur"])}, PEUT-ÊTRE {fprob(reg["seuil_peut_etre"])})')
    tableau(['requête', 'sûres', 'moins sûres', 'proba max', '5e proba'], lignes)
    print(f'Requêtes absentes : médiane {np.median(fausses_sur):.0f} fausse(s) « sûre(s) » '
          f'et {np.median(fausses_peut):.0f} « moins sûre(s) » par requête, pour {len(V)} photos.')


# ---------------------------------------------------------------- point d'entrée

def main():
    import contextlib
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dossier', type=Path, default=DOSSIER_DEFAUT, help='dossier de travail (images, résultats)')
    ap.add_argument('--reconstruire', action='store_true', help='nouvelle sélection d\'images via l\'API Commons')
    ap.add_argument('--planches', action='store_true', help='planches-contact (catégories et erreurs)')
    ap.add_argument('--sans-evaluation', action='store_true', help='seulement préparer le jeu')
    ap.add_argument('--sans-telechargement', action='store_true', help='évaluer les images déjà présentes')
    ap.add_argument('--markdown', action='store_true', help='tableaux au format Markdown')
    ap.add_argument('--moteurs', default=','.join(MOTEURS), help='moteurs à évaluer, séparés par des virgules')
    ap.add_argument('--gabarit', help='gabarit imposé à tous les moteurs (sinon : celui des réglages en service)')
    ap.add_argument('--suggestions', action='store_true',
                    help='ignorer les réglages en service et montrer les seuils suggérés automatiquement')
    ap.add_argument('--collection', type=Path,
                    help='vraie collection : seulement des NOMBRES de photos au-dessus des seuils (rien n\'est montré)')
    ap.add_argument('--collection-max', type=int, help='nombre maximal de photos lues dans la collection')
    args = ap.parse_args()

    dossier = args.dossier
    dossier.mkdir(parents=True, exist_ok=True)
    _CACHE_API[0] = dossier / 'cache_api'
    jeu = construire_jeu() if (args.reconstruire or not JEU.is_file()) else json.loads(JEU.read_text(encoding='utf-8'))
    if not args.sans_telechargement:
        telecharger_images(jeu, dossier)
    if args.planches:
        planches_categories(jeu, dossier)
    if args.sans_evaluation:
        return
    moteurs = [m.strip() for m in args.moteurs.split(',') if m.strip()]
    if args.collection:
        for cle in moteurs:
            controle_collection(args.collection, cle, args.collection_max)
        return

    elems = elements(jeu, dossier)
    etiq = [etiquettes_finales(e) for e in elems]
    titre('Requalifications appliquées (étiquettes P180 incomplètes ou fausses)')
    requalifiees = [(e, REQUALIFICATIONS[e['titre']]) for e in elems if e['titre'] in REQUALIFICATIONS]
    for e, r in requalifiees:
        modif = ' '.join([f'+{x}' for x in r.get('ajouter', [])] + [f'-{x}' for x in r.get('retirer', [])])
        print(f'  {e["fichier"]:<24} {modif:<24} {r.get("motif", "")}')
    print(f'{len(requalifiees)} image(s) requalifiée(s), {len(EXCLUS)} écartée(s) ; {len(elems)} images évaluées.')

    resultats = {}
    for cle in moteurs:
        rapport = dossier / f'rapport_{cle}.{"md" if args.markdown else "txt"}'
        with open(rapport, 'w', encoding='utf-8') as f, contextlib.redirect_stdout(f):
            resultats[cle] = evaluer_moteur(cle, elems, etiq, dossier, md=args.markdown, gabarit_impose=args.gabarit,
                                            suggestions=args.suggestions)
        print(f'{cle} : rapport détaillé dans {rapport}', file=sys.stderr, flush=True)
    synthese(resultats, md=args.markdown)
    consensus_erreurs(resultats, elems, dossier, planches=args.planches)
    sortie = dict(date=time.strftime('%Y-%m-%d'), images=len(elems),
                  requalifications={e['fichier']: r for e, r in requalifiees},
                  moteurs={k: {x: v for x, v in r.items() if not x.startswith('_')} for k, r in resultats.items()})
    (dossier / 'resultats.json').write_text(json.dumps(sortie, ensure_ascii=False, indent=1, default=float),
                                            encoding='utf-8')
    print(f'\nRésultats détaillés : {dossier / "resultats.json"} ; erreurs communes : {dossier / "erreurs_consensus.txt"}')


if __name__ == '__main__':
    main()
