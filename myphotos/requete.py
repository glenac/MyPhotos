"""Comprendre une recherche tapée en français (0.5.0) : les DATES, les LIEUX et les MOTS.

« chien sur la plage en Bretagne l'été 2022 » donne les mots « chien sur la plage » (cherchés par le moteur, comme
avant), la période « été 2022 » (1er juin au 22 septembre 2022), le lieu que l'appelant reconnaît dans « Bretagne », et
deux puces que la page affiche au-dessus des résultats ; un × sur une puce renvoie son texte parmi les mots (`ignorer`).

Ce module ne connaît aucun lieu et ne lit rien (ni base, ni fichier, ni réseau) : bibliothèque standard seulement
(numpy dans `FiltreDates.masque`, importé à la demande). Il ne journalise jamais la recherche.

Interface
- Periode(debut, fin, libelle) : datetime.date, fin comprise ; libelle : « Noël 2019 », « été 2022 », « mars 2021 ».
- Analyse(mots, periodes, lieux, puces) :
  mots : ce qui reste pour la recherche par mots, '' s'il ne reste rien (la page montre alors, par date, les photos
    des dates et des lieux) ;
  periodes : une photo convient si sa date est dans L'UNE d'elles ; liste vide : pas de filtre de date ;
  lieux : les objets renvoyés par chercher_lieu, dans l'ordre du texte ;
  puces : [{'type': 'date' | 'lieu', 'libelle': str, 'texte': str}] dans l'ordre du texte ; texte = ce qui a été
    retiré de la recherche, tel que tapé (« en Bretagne », « l'été 2022 ») : la page le renvoie dans `ignorer`.
- analyser(texte, aujourd_hui, chercher_lieu=None, ignorer=()) -> Analyse
  aujourd_hui : date (ou datetime) des dates relatives et de l'année en cours.
  chercher_lieu(nom_normalise, indices) -> objet ou None ; l'appelant décide (il sait quels lieux ont des photos) ;
    indices = {'preposition': bool, 'majuscule': bool, 'mots': int}, plus 'debut' (le groupe ouvre la recherche : sa
    majuscule ne prouve rien), 'texte' (le groupe tel que tapé) et 'article' (un article entre la préposition et le
    nom, « à la plage », ou une préposition contractée, « au port »). Une exception, ou une liste vide, vaut « non ».
  ignorer : textes des puces retirées : ces mots ne sont plus lus ni comme date ni comme lieu.
- normaliser(texte) : la règle de lieux.normaliser (celle de lieux.py sert pour chercher_lieu quand il existe).
- FiltreDates(periodes) (`in`, masque numpy, clause SQL), contient(), intervalles() : appliquer les périodes.

Choix. Le pire défaut serait un filtre faux qui vide les résultats : dans le doute, le texte reste un mot.

Généralités
- Une date ou un lieu reconnus sortent des mots avec leur introduction (« en 2019 », « à Noël », « en Bretagne ») ; un
  petit mot laissé seul à côté (« photos de », « chien et ») part aussi ; s'il ne reste que des mots sans contenu
  (« photos », « mes », « de »…), mots = ''. Une recherche sans date ni lieu ressort inchangée.
- Plusieurs dates : l'une OU l'autre (« 2019 et 2020 »). Mais une fête, un mois ou une saison SANS année se combine
  avec les années tapées ailleurs : « Noël chez mamie en 2019 » = Noël 2019, « Noël l'an dernier », « Noël années 90 »
  (les Noëls de 1990 à 1999), « en août il y a 3 ans » (le mois d'août le plus proche d'il y a 3 ans). Sans année :
  tous les ans, de 1900 à l'an prochain, une période par année (128 en 2026), la forme la plus simple pour
  l'intégration ; FiltreDates les applique en O(log n) par photo, ou en une clause SQL.
- Années : 4 chiffres, de 1900 à l'année en cours (« 1500 », « 2045 » restent des mots) ; jointes à une fête, un mois,
  une saison ou un jour, jusqu'à l'an prochain (Nouvel An, appareil mal réglé). Deux chiffres seulement jointes
  (« mars 21 », « été 98 », « mai 68 ») : jusqu'à l'an prochain 2000 + n, sinon 1900 + n ; jamais quand ils
  commencent un jour (« Noël 25 décembre », « réveillon 31 décembre » : Noël et le 25 décembre, tous les ans) ni
  devant une quantité (« été 30 degrés », « été 30° », « août 15 jours », « Noël 10 ans »). Une année suivie d'une
  unité (« 2000 euros », « 1500 m »), ou précédée de « n° » ou d'un nom de modèle (« Peugeot 2008 », « Windows
  2000 », « chambre 2019 »), reste un mot ; « en 2008 » reste une année. « bonne année 2020 » : l'année 2020, et les
  mots « bonne année » (des vœux). Une année hors limites jointe à une date (« Noël 2045 », « 14 juillet 1789 »)
  laisse toute l'expression parmi les mots.
- Nombres : « 2 chiens », « 3 ans » (« enfant de 3 ans »), « 12 », « 100 ans » restent des mots ; « il y a 3 ans » est
  une date. « 14/07 » sans année non plus (score, fraction).

Mois
- « mars 2021 », « en mars 2021 », « mars 21 », « 07/2019 », « 2019-07 » : le mois entier ; « mars et avril 2021 »,
  « de mars à mai 2021 », « entre mars et mai » aussi.
- Sans année, un mois n'est une date qu'avec « en », « au mois de », « début », « mi », « fin », « courant »,
  « photos de » ou « dernier » : « en août » = tous les mois d'août ; « il fait beau en mai » : « en mai » est une
  date. Seul, il reste un mot : « mars » (planète), « Avril », « Juillet », « Mai » (prénoms), « giboulées de mars ».
  Avec une année, c'est une date même avec une majuscule (« Mars 2021 »). Abréviations (« sept », « déc ») seulement
  avec un jour ou une année : « sept » est aussi un nombre.
- début, mi, fin : 1er au 10, 10 au 20, 20 à la fin du mois ; d'une année : janvier-avril, mai-août,
  septembre-décembre. Les bornes se recouvrent exprès.

Saisons
- Union des saisons météorologiques (mois entiers) et astronomiques (équinoxes, solstices), pour ne jamais écarter une
  photo que quelqu'un rangerait dans la saison : printemps 1er mars - 20 juin ; été 1er juin - 22 septembre (grandes
  vacances et début septembre compris) ; automne 1er septembre - 20 décembre ; hiver 1er décembre - 20 mars (vacances
  de ski de février et début mars comprises).
- « hiver 2020 » est ambigu (Météo-France dit « l'hiver 2020 » de décembre 2019 à février 2020 ; on peut aussi
  comprendre décembre 2020 à février 2021) : les deux hivers qui touchent 2020. « hiver 2019-2020 » : un seul.
- Sans année, une saison n'est une date qu'avec « en », « au », « l' », « le », « les », « pendant », « cet »,
  « photos de » ou « dernier » : « l'été » = tous les étés (avec l'article, c'est le nom, jamais le verbe). Seule,
  elle reste un mot : « été » (« a été », « avait été » : après un auxiliaire, jamais une saison), « l'été indien »,
  « Printemps » (le magasin), « robe d'été », « soirée d'été ». Suivie de « de » et d'autre chose qu'une date, elle
  nomme autre chose (« le printemps de Bourges », « l'été de mes 10 ans ») : des mots ; « l'été de 2019 » = été 2019.
- « cet été » : le dernier commencé. « l'été dernier » : le dernier fini, et, s'il a commencé cette année, celui
  d'avant aussi (en novembre, « l'été dernier » peut vouloir dire les deux). Même règle pour « Noël dernier »,
  « août dernier »…

Fêtes (bornes comprises)
- Noël : 24 au 26 décembre (réveillon, jour de Noël, lendemain fêté de l'autre côté de la famille) ; réveillon,
  veille, nuit, soir de Noël : 24-25 ; jour ou matin de Noël : 25 ; lendemain de Noël : 26.
- Saint-Sylvestre, réveillon du Nouvel An : 31 décembre - 1er janvier (la nuit est à cheval sur deux années) ;
  « Saint-Sylvestre 2019 » = 31/12/2019 - 1/1/2020. « Nouvel An 2020 » est ambigu (la nuit qui ouvre 2020, ou celle
  qui le ferme) : les deux. « réveillon » seul : les deux réveillons. Fêtes de fin d'année : 20 décembre - 2 janvier.
- Pâques (date exacte, calendrier grégorien) : samedi au lundi (chasse aux œufs le dimanche ou le lundi) ; dimanche
  et lundi de Pâques : ce jour-là. Ascension : jeudi au dimanche (le pont). Pentecôte : samedi au lundi.
- 14 juillet, fête nationale : 13 au 15 juillet (bals du 13, feu d'artifice du 14 qui finit après minuit), avec ou
  sans année (« 14 juillet 2019 » : la fête de 2019). 1er mai, 15 août, Assomption : ce jour-là. Toussaint :
  31 octobre - 2 novembre (le 1er, puis le jour des morts). Halloween : 30 octobre - 1er novembre (soirées, nuit du
  31). Saint-Valentin : 13-15 février (dîner souvent déplacé d'un jour). Fête de la musique : 21-22 juin.
- Vacances scolaires (françaises, avec de la marge) : de Noël 19 décembre - 4 janvier ; de la Toussaint 17 octobre -
  5 novembre ; d'hiver, de février, de ski 3 février - 12 mars ; de Pâques, de printemps : Pâques - 14 jours (au plus
  tard le 4 avril) au 10 mai (depuis 2016, elles ne suivent plus Pâques) ; grandes vacances, d'été : 25 juin -
  5 septembre.
- Une fête seule (« Noël », « Pâques ») est une date, retirée des mots : toutes les photos de ces jours-là, même
  sans sapin ni œufs. Complément d'un nom, elle reste un mot (« sapin de Noël », « marché de Noël », « fête de Noël »
  de l'école, mi-décembre, « œufs de Pâques », « île de Pâques », « Champ de Mars ») ; une année qui suit reste une
  date (« marché de Noël 2019 » : l'année 2019, et les mots « marché de Noël »). Sauf après « photos », « souvenirs »,
  « images »… (« photos de Noël 2019 » = Noël 2019) ou un mot de temps (« samedi de Pâques », « nuit de la
  Saint-Sylvestre », « week-end de la Toussaint » : la fête). « père Noël » reste un mot. « Ascension » n'est une date
  qu'avec une majuscule, « l' » ou une année, et jamais devant « du/de » (« ascension du mont Blanc »). « Nouvel An
  chinois » reste un mot. « rue du 14 juillet » aussi.

Dates relatives (par rapport à aujourd_hui)
- aujourd'hui (ce matin, ce soir…), hier, avant-hier, cette nuit (hier et aujourd'hui) ; cette semaine, la semaine
  dernière : lundi au dimanche ; ce week-end : le dernier commencé ; le week-end dernier : le dernier fini ; ce
  mois-ci, le mois dernier : mois entiers ; cette année, l'an dernier : années entières (qui se combinent avec une
  fête : « Noël l'an dernier »). « lundi dernier » : le dernier lundi avant aujourd'hui. « ce soir-là », « cette
  année-là », « cet été-là » (« là » avec l'accent, ou collé par un tiret) parlent d'un autre jour : des mots.
- « il y a N jours » : ce jour-là, à N/7 jours près (une approximation grandit avec la distance) ; « il y a N
  semaines » : la semaine (lundi-dimanche) de ce jour-là, et 3 jours autour ; « il y a N mois » : le mois de ce
  jour-là, et 15 jours autour ; « il y a N ans » : l'année de ce jour-là ET 3 mois autour (le 26/11/2026, « il y a
  2 ans » = 1er janvier 2024 au 26 février 2025) : on pense « en 2024 », mais en janvier on vise souvent les fêtes de
  l'année d'avant. Avec une fête : l'occurrence la plus proche de ce jour (« Noël il y a 2 ans » le 10/01/2027 =
  Noël 2024). « il y a quelques jours » : 1 à 10 jours ; semaines : 1 à 8 ; mois : 1 à 9 ; années : 2 à 8.
- « depuis » (2019, hier, 3 mois, mars 2021, Noël…) : jusqu'à aujourd'hui ; « avant », « après », « jusqu'en » une
  date ou une année ; « vers 2010 » : 2009 à 2011. « avant Noël », « après Pâques » (sans année) restent des mots.
  Le futur (« l'été prochain », « demain ») n'est pas une date.

Dates précises
- « le 3 mai 2020 », « 1er janvier 2020 », « 14/07/2019 », « 14.07.19 », « 2019-07-14 » : ce jour-là (toujours
  jour/mois, jamais mois/jour). Une date impossible (« 31/02/2019 », « 29 février 2019 ») n'est pas une date. Sans
  année (« le 3 mai ») : ce jour, tous les ans (le 29 février : les années bissextiles). « du 3 au 10 mai 2020 »,
  « du 28 décembre au 3 janvier 2020 », « 3-10 mai », « 3 et 4 mai 2020 » : la plage, ou les deux jours.

Lieux
- Après les dates, sur les mots qui restent : groupes de 6 mots au plus (et non 4 : « Saintes-Maries-de-la-Mer »,
  « Saint-Jean-Pied-de-Port », « L'Isle-sur-la-Sorgue », « Provence-Alpes-Côte d'Azur » en ont 5), les plus longs
  d'abord, de gauche à droite, sans chevauchement ; jamais à travers une virgule, ni avec un chiffre, ni un groupe qui
  commence par une préposition ou finit par un petit mot ; jamais un mot outil, ni un mois, une saison, une fête ou un
  jour de la semaine seuls (« Avril » le prénom ne devient pas la commune d'Avril).
- Préposition (« à », « en », « au », « aux », « dans », « vers », « près de », « à côté de », « autour de », « du côté
  de », avec ou sans le/la/les/l') : retirée avec le lieu. « au Mans », « aux Sables-d'Olonne » : si le nom seul n'est
  pas un lieu, « le mans », « les sables d olonne » sont essayés. « à la plage » n'est pas un lieu si chercher_lieu dit
  non (« la plage » puis « plage », avec preposition=True).
- majuscule : la première lettre du groupe, ou de son premier mot après l'article (« la Baule »), est une capitale ;
  jamais pour une recherche tapée tout en capitales.
- Libellé de la puce : attribut ou clé « libelle », sinon « nom », de l'objet (ou de son premier élément pour une
  liste), sinon le texte tapé.

Retirer une puce (`ignorer`)
- Le texte est cherché mot à mot, sans accents ni majuscules ; ses mots ne sont plus ni date ni lieu et ne combinent
  plus rien (« Noël 2019 » retiré : plus de date ; « en 2019 » retiré de « Noël chez mamie en 2019 » : tous les Noëls).
"""
import calendar
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

ANNEE_MIN = 1900          # première année reconnue ; les fêtes et mois sans année vont de là à l'an prochain
LONGUEUR_MAX = 200        # caractères lus, comme recherche.LONGUEUR_MAX
MOTS_LIEU_MAX = 6         # mots d'un nom de lieu essayé d'un bloc


@dataclass
class Periode:
    """Période de dates, bornes comprises."""
    debut: date
    fin: date
    libelle: str


@dataclass
class Analyse:
    """Résultat d'analyser() ; voir l'en-tête du module."""
    mots: str
    periodes: list = field(default_factory=list)
    lieux: list = field(default_factory=list)
    puces: list = field(default_factory=list)


# ---------------------------------------------------------------- normalisation (règle de lieux.normaliser)

_LETTRES = str.maketrans({'œ': 'oe', 'æ': 'ae', 'ø': 'o', 'ł': 'l', 'đ': 'd', 'ð': 'd', 'þ': 'th', 'ı': 'i',
                          'ħ': 'h', 'ŧ': 't', 'ŀ': 'l', 'ĸ': 'k'})
_COMBINANTS = re.compile('[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]+')
_SEPARATEURS = re.compile('[\\W_ʰ-˿]+')
_ABREGES = {'st': 'saint', 'ste': 'sainte'}


def normaliser(texte):
    """Comme lieux.normaliser : minuscules, sans accents, apostrophes, tirets et autres signes changés en espaces,
    espaces simples, « st » et « ste » écrits « saint » et « sainte » (« St-Malo » -> « saint malo »)."""
    t = unicodedata.normalize('NFKD', str(texte).casefold())
    t = _COMBINANTS.sub('', t).translate(_LETTRES)
    return ' '.join(_ABREGES.get(m, m) for m in _SEPARATEURS.sub(' ', t).split())


_NORMALISER_LIEU = []


def _nom_lieu(texte):
    """Nom passé à chercher_lieu : lieux.normaliser si lieux.py existe (même règle, mais la sienne fait foi)."""
    if not _NORMALISER_LIEU:
        try:
            from .lieux import normaliser as f
        except Exception:                      # pas de lieux.py (ou illisible) : la copie ci-dessus
            f = normaliser
        _NORMALISER_LIEU.append(f)
    return _NORMALISER_LIEU[0](texte)


def _forme(mot):
    """Forme comparable d'un mot déjà découpé : minuscules, sans accents, « st » -> « saint »."""
    t = _COMBINANTS.sub('', unicodedata.normalize('NFKD', mot.casefold())).translate(_LETTRES)
    return _ABREGES.get(t, t)


# ---------------------------------------------------------------- découpage en mots

_TIRETS = '-‐‑‒–—―−'
_CLASSE_TIRETS = re.escape(_TIRETS)            # dans une classe de caractères : « - » échappé, pas une plage
# Un nombre composé (« 14/07/2019 », « 2018-2020 », « 3-10 »), un mot (lettres et chiffres), ou un tiret isolé entre
# deux espaces (« 2018 - 2020 ») ; tout le reste sépare (espaces, apostrophes, tirets dans un mot, ponctuation).
_JETONS = re.compile('[0-9]+(?:[./' + _CLASSE_TIRETS + '][0-9]+)+|[^\\W_]+|(?<=\\s)[' + _CLASSE_TIRETS + '](?=\\s)')
_SEP_NOMBRE = re.compile('[./' + _CLASSE_TIRETS + ']')
_COUPURE = re.compile('[,;:!?()\\[\\]{}"«»“”/+&|•·…]')    # entre deux mots : pas dans un même nom de lieu
_ESPACES = re.compile(r'\s+')


class _Jeton:
    __slots__ = ('debut', 'fin', 'brut', 'n', 'nombre', 'parties', 'maj', 'coupure')


def _jetons(texte):
    res = []
    fin = 0
    for m in _JETONS.finditer(texte):
        x = _Jeton()
        x.debut, x.fin, x.brut = m.start(), m.end(), m.group()
        x.coupure = _COUPURE.search(texte, fin, m.start()) is not None
        x.parties = x.nombre = None
        b = x.brut
        if len(b) == 1 and b in _TIRETS:
            x.n = '-'
        elif b[0].isascii() and b[0].isdigit() and _SEP_NOMBRE.search(b):
            x.parties = _SEP_NOMBRE.split(b)
            x.n = _SEP_NOMBRE.sub(lambda s: '-' if s.group() in _TIRETS else s.group(), b)
        else:
            x.n = _forme(b)
            if x.n.isascii() and x.n.isdigit():
                x.nombre = int(x.n)
        x.maj = b[:1].isupper()
        res.append(x)
        fin = m.end()
    return res


# ---------------------------------------------------------------- vocabulaire (formes normalisées)

_MOIS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8,
         'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12}
_MOIS_ABREGES = {'janv': 1, 'fev': 2, 'fevr': 2, 'avr': 4, 'juil': 7, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12}
_NOMS_MOIS = (None, 'janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
              'novembre', 'décembre')
_SAISONS = {'printemps': 'printemps', 'ete': 'ete', 'etes': 'ete', 'automne': 'automne', 'automnes': 'automne',
            'hiver': 'hiver', 'hivers': 'hiver'}
_JOURS_SEMAINE = {'lundi': 0, 'mardi': 1, 'mercredi': 2, 'jeudi': 3, 'vendredi': 4, 'samedi': 5, 'dimanche': 6}
_DERNIER = {'dernier', 'derniere', 'derniers', 'dernieres', 'passe', 'passee', 'passes', 'passees'}
_PROCHAIN = {'prochain', 'prochaine', 'prochains', 'prochaines', 'suivant', 'suivante'}
_NOMBRES = {'un': 1, 'une': 1, 'deux': 2, 'trois': 3, 'quatre': 4, 'cinq': 5, 'six': 6, 'sept': 7, 'huit': 8,
            'neuf': 9, 'dix': 10, 'onze': 11, 'douze': 12, 'treize': 13, 'quatorze': 14, 'quinze': 15, 'seize': 16,
            'vingt': 20, 'trente': 30, 'quarante': 40, 'cinquante': 50, 'soixante': 60, 'cent': 100}
_UNITES_TEMPS = {'jour': 'j', 'jours': 'j', 'semaine': 's', 'semaines': 's', 'mois': 'm', 'an': 'a', 'ans': 'a',
                 'annee': 'a', 'annees': 'a'}
# Après une année seule, ces mots en font une quantité (« 2000 euros », « 1500 m d'altitude »).
_UNITES = {'euro', 'euros', 'eur', 'e', 'dollar', 'dollars', 'usd', 'km', 'm', 'cm', 'mm', 'metre', 'metres',
           'kilometre', 'kilometres', 'kg', 'g', 'gramme', 'grammes', 'tonne', 'tonnes', 't', 'l', 'litre', 'litres',
           'ml', 'cl', 'habitant', 'habitants', 'personne', 'personnes', 'gens', 'photo', 'photos', 'piece', 'pieces',
           'an', 'ans', 'jour', 'jours', 'h', 'heure', 'heures', 'min', 'minute', 'minutes', 'cv', 'chevaux', 'point',
           'points', 'pts', 'page', 'pages', 'mot', 'mots', 'pixel', 'pixels', 'px', 'ko', 'mo', 'go', 'w', 'watts',
           'v', 'volts', 'pas', 'tour', 'tours', 'fois', 'mille', 'exemplaires', 'places', 'marches', 'metres'}
# Après un nombre de deux chiffres, ces mots en font une quantité, pas une année (« été 30 degrés », « Noël 10 ans »,
# « août 15 jours », « Pâques 10 enfants ») ; sans les lettres seules qui sont aussi des élisions (l', m', t'…).
_QUANTITES = (_UNITES | set(_UNITES_TEMPS) | {
    'degre', 'degres', 'enfant', 'enfants', 'adulte', 'adultes', 'invite', 'invites', 'convive', 'convives', 'ami',
    'amis', 'copain', 'copains', 'copine', 'copines', 'cousin', 'cousins', 'cousine', 'cousines', 'bougie', 'bougies',
    'eleve', 'eleves', 'participant', 'participants', 'nuit', 'nuits', 'centimetre', 'centimetres'}) - set('letmgvwsdnj')
_SIGNES_QUANTITE = set('°%€$£')                  # « été 30° », « soldes 50 % », « 20 € »
# Devant ces mots, un nombre de 4 chiffres est un numéro ou un modèle, pas une année (« Peugeot 2008 »).
_AVANT_NUMERO = {'n', 'no', 'numero', 'num', 'peugeot', 'renault', 'citroen', 'windows', 'office', 'iphone', 'ipad',
                 'galaxy', 'samsung', 'nokia', 'boeing', 'airbus', 'model', 'modele', 'version', 'serie', 'reference',
                 'ref', 'chambre', 'bus', 'ligne', 'vol', 'tgv', 'train', 'route', 'porte', 'code', 'fifa', 'xbox',
                 'playstation', 'canon', 'nikon', 'sony'}
_VOEUX = {'bonne', 'heureuse', 'nouvelle', 'belle', 'excellente', 'meilleure'}     # « bonne année 2020 » : des vœux
# Petits mots qui introduisent une date ; ils partent avec elle (« photos de l'été 2022 », « lors de la Toussaint »).
_INTRO = {'en', 'au', 'aux', 'a', 'pour', 'pendant', 'durant', 'lors', 'le', 'la', 'les', 'l', 'du', 'de', 'des',
          'd', 'dans'}
_ARTICLES = {'le', 'la', 'les', 'l'}
_DE = {'de', 'du', 'd', 'des'}
# « photos de Noël » est une date ; « sapin de Noël », un nom (voir _Lecture.complement).
_NEUTRES = {'photo', 'photos', 'image', 'images', 'cliche', 'cliches', 'souvenir', 'souvenirs', 'video', 'videos',
            'album', 'albums', 'selfie', 'selfies', 'diaporama'}
_TETES_FETES = {'jour', 'jours', 'journee', 'nuit', 'nuits', 'soir', 'soiree', 'soirees', 'matin', 'matinee', 'midi',
                'veille', 'lendemain', 'end', 'weekend', 'pont', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi',
                'samedi', 'dimanche', 'periode', 'moment', 'semaine', 'fetes', 'vacances', 'debut', 'fin', 'milieu',
                'temps', 'epoque'}
_TETES_PARTIES = {'debut', 'fin', 'milieu', 'mi', 'courant', 'mois', 'periode'}
_PAS_TETE = _INTRO | {'depuis', 'avant', 'apres', 'vers', 'partir', 'jusqu', 'entre', 'et', 'ou'}
_VOIES = {'rue', 'avenue', 'av', 'boulevard', 'bd', 'place', 'quai', 'square', 'allee', 'impasse', 'chemin', 'route',
          'cours', 'rond', 'stade', 'lycee', 'college', 'ecole', 'parc', 'jardin', 'gare', 'station', 'metro',
          'residence', 'cite', 'hopital', 'esplanade', 'passage'}
_AUXILIAIRES = {'a', 'as', 'ai', 'avait', 'avais', 'avaient', 'avions', 'aviez', 'ont', 'avons', 'avez', 'aura',
                'auras', 'aurai', 'aurons', 'aurez', 'auront', 'aurait', 'aurais', 'auraient', 'avoir', 'ayant', 'eut',
                'eu', 'j'}
_PARTIES = {'debut': 'debut', 'fin': 'fin', 'mi': 'mi', 'milieu': 'mi', 'courant': None}
_FORTS_MOIS = {'en', 'pendant', 'durant', 'lors'}
_FORTS_SAISON = {'en', 'au', 'l', 'le', 'les', 'pendant', 'durant', 'lors'}
_NOUVEL_AN_AUTRES = {'chinois', 'lunaire', 'juif', 'vietnamien', 'tamoul', 'persan', 'iranien', 'berbere', 'tibetain',
                     'coreen', 'thai', 'orthodoxe', 'hindou', 'musulman', 'islamique', 'japonais'}
_DECENNIES_MOTS = {('dix',): 10, ('vingt',): 20, ('trente',): 30, ('quarante',): 40, ('cinquante',): 50,
                   ('soixante',): 60, ('soixante', 'dix'): 70, ('septante',): 70, ('quatre', 'vingt'): 80,
                   ('quatre', 'vingts'): 80, ('huitante',): 80, ('octante',): 80, ('quatre', 'vingt', 'dix'): 90,
                   ('nonante',): 90, ('deux', 'mille'): 2000, ('deux', 'mille', 'dix'): 2010}

# ---------------------------------------------------------------- calendrier

def paques(annee):
    """Dimanche de Pâques de cette année (calendrier grégorien ; algorithme de Meeus, Jones et Butcher)."""
    a, b, c = annee % 19, annee // 100, annee % 100
    d, e = b // 4, b % 4
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lettre = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lettre) // 451
    n = h + lettre - 7 * m + 114
    return date(annee, n // 31, n % 31 + 1)


def _jour(a, m, j):
    """date(a, m, j), ou None si elle n'existe pas (29 février d'une année ordinaire, 31 avril…)."""
    if not 1 <= a <= 9999:
        return None
    try:
        return date(a, m, j)
    except ValueError:
        return None


def _dernier_jour(a, m):
    return date(a, m, calendar.monthrange(a, m)[1])


def _moins_mois(d, n):
    """Même jour n mois plus tôt, ramené à la fin du mois (31 mars - 1 mois = 28 ou 29 février)."""
    m = d.month - 1 - n
    a, m = d.year + m // 12, m % 12 + 1
    if a < 1:
        return None
    return date(a, m, min(d.day, calendar.monthrange(a, m)[1]))


def _moins_ans(d, n):
    """Même jour n ans plus tôt (le 29 février devient le 28)."""
    a = d.year - n
    if a < 1:
        return None
    return date(a, d.month, min(d.day, calendar.monthrange(a, d.month)[1]))


def _lundi(d):
    return d - timedelta(days=d.weekday())


def _jour_fr(d, annee=True):
    """« 1er mai 2020 », « 14 juillet » (sans l'année)."""
    s = f"{'1er' if d.day == 1 else d.day} {_NOMS_MOIS[d.month]}"
    return f'{s} {d.year}' if annee else s


def _plage_fr(d, f):
    """« 25 novembre 2026 », « 16 au 22 novembre 2026 », « 28 décembre 2019 au 3 janvier 2020 »."""
    if d == f:
        return _jour_fr(d)
    if d.year != f.year:
        return f'{_jour_fr(d)} au {_jour_fr(f)}'
    if d.month != f.month:
        return f'{_jour_fr(d, False)} au {_jour_fr(f)}'
    return f"{'1er' if d.day == 1 else d.day} au {_jour_fr(f)}"


def _mois_fr(a, m):
    return f'{_NOMS_MOIS[m]} {a}'


def _annees_fr(intervalles):
    """[(2019, 2019), (2020, 2020)] -> « 2019 et 2020 » ; [(2015, 2019)] -> « 2015 à 2019 »."""
    morceaux = [str(a) if a == b else f'{a} à {b}' for a, b in intervalles]
    return morceaux[0] if len(morceaux) == 1 else ', '.join(morceaux[:-1]) + ' et ' + morceaux[-1]


# ---------------------------------------------------------------- ce qui revient chaque année

class _Regle:
    """Fête, mois, saison ou jour qui revient chaque année. occ(a) : [(debut, fin)] des occurrences qui commencent
    l'année a. chevauche : « hiver 2020 », « Nouvel An 2020 » désignent les occurrences qui TOUCHENT 2020 (deux),
    les autres celles qui y commencent."""
    __slots__ = ('nom', 'occ', 'chevauche', 'nommer')

    def __init__(self, nom, occ, chevauche=False, nommer=None):
        self.nom, self.occ, self.chevauche, self.nommer = nom, occ, chevauche, nommer

    def libelle(self, debut):
        return self.nommer(debut) if self.nommer else f'{self.nom} {debut.year}'


def _fixe(m1, j1, m2, j2):
    """Du j1/m1 au j2/m2 (de l'année suivante si j2/m2 vient avant) ; rien les années où l'un n'existe pas."""
    suivante = (m2, j2) < (m1, j1)

    def occ(a):
        d, f = _jour(a, m1, j1), _jour(a + suivante, m2, j2)
        return [(d, f)] if d and f else []
    return occ


def _autour_de_paques(avant, apres):
    def occ(a):
        p = paques(a)
        return [(p + timedelta(days=avant), p + timedelta(days=apres))]
    return occ


def _vacances_printemps(a):
    p = paques(a)
    return [(min(p - timedelta(days=14), date(a, 4, 4)), date(a, 5, 10))]


def _reveillons(a):
    return [(date(a, 12, 24), date(a, 12, 25)), (date(a, 12, 31), date(a + 1, 1, 1))]


def _deux_annees(nom):
    return lambda d: f'{nom} {d.year}-{d.year + 1}'


_FETES = {
    'noel': _Regle('Noël', _fixe(12, 24, 12, 26)),
    'reveillon_noel': _Regle('réveillon de Noël', _fixe(12, 24, 12, 25)),
    'jour_noel': _Regle('jour de Noël', _fixe(12, 25, 12, 25)),
    'lendemain_noel': _Regle('lendemain de Noël', _fixe(12, 26, 12, 26)),
    'sylvestre': _Regle('Saint-Sylvestre', _fixe(12, 31, 1, 1)),
    'nouvel_an': _Regle('Nouvel An', _fixe(12, 31, 1, 1), True, lambda d: f'Nouvel An {d.year + 1}'),
    'reveillon': _Regle('réveillons', _reveillons,
                        nommer=lambda d: f"réveillon {'de Noël' if d.day == 24 else 'du Nouvel An'} {d.year}"),
    'fetes_fin': _Regle("fêtes de fin d'année", _fixe(12, 20, 1, 2), nommer=_deux_annees("fêtes de fin d'année")),
    'paques': _Regle('Pâques', _autour_de_paques(-1, 1)),
    'dimanche_paques': _Regle('dimanche de Pâques', _autour_de_paques(0, 0)),
    'lundi_paques': _Regle('lundi de Pâques', _autour_de_paques(1, 1)),
    'ascension': _Regle('Ascension', _autour_de_paques(39, 42)),
    'pentecote': _Regle('Pentecôte', _autour_de_paques(48, 50)),
    'lundi_pentecote': _Regle('lundi de Pentecôte', _autour_de_paques(50, 50)),
    'quatorze': _Regle('14 juillet', _fixe(7, 13, 7, 15)),
    'travail': _Regle('1er mai', _fixe(5, 1, 5, 1)),
    'assomption': _Regle('15 août', _fixe(8, 15, 8, 15)),
    'toussaint': _Regle('Toussaint', _fixe(10, 31, 11, 2)),
    'halloween': _Regle('Halloween', _fixe(10, 30, 11, 1)),
    'valentin': _Regle('Saint-Valentin', _fixe(2, 13, 2, 15)),
    'musique': _Regle('fête de la musique', _fixe(6, 21, 6, 22)),
    'vacances_noel': _Regle('vacances de Noël', _fixe(12, 19, 1, 4), nommer=_deux_annees('vacances de Noël')),
    'vacances_toussaint': _Regle('vacances de la Toussaint', _fixe(10, 17, 11, 5)),
    'vacances_hiver': _Regle("vacances d'hiver", _fixe(2, 3, 3, 12)),
    'vacances_printemps': _Regle('vacances de printemps', _vacances_printemps),
    'grandes_vacances': _Regle('grandes vacances', _fixe(6, 25, 9, 5)),
}

# Noms des fêtes, en mots normalisés ; le plus long l'emporte (« lundi de Pâques » avant « Pâques »).
_NOMS_FETES = {
    ('noel',): 'noel', ('reveillon', 'de', 'noel'): 'reveillon_noel', ('veille', 'de', 'noel'): 'reveillon_noel',
    ('nuit', 'de', 'noel'): 'reveillon_noel', ('soir', 'de', 'noel'): 'reveillon_noel',
    ('soiree', 'de', 'noel'): 'reveillon_noel', ('jour', 'de', 'noel'): 'jour_noel',
    ('matin', 'de', 'noel'): 'jour_noel', ('lendemain', 'de', 'noel'): 'lendemain_noel',
    ('saint', 'sylvestre'): 'sylvestre', ('reveillon', 'de', 'la', 'saint', 'sylvestre'): 'sylvestre',
    ('reveillon', 'du', 'nouvel', 'an'): 'sylvestre', ('soiree', 'du', 'nouvel', 'an'): 'sylvestre',
    ('soir', 'du', 'nouvel', 'an'): 'sylvestre', ('nuit', 'du', 'nouvel', 'an'): 'sylvestre',
    ('reveillon', 'du', '31'): 'sylvestre',
    ('nouvel', 'an'): 'nouvel_an', ('jour', 'de', 'l', 'an'): 'nouvel_an', ('premier', 'de', 'l', 'an'): 'nouvel_an',
    ('reveillon',): 'reveillon', ('reveillons',): 'reveillon',
    ('fetes', 'de', 'fin', 'd', 'annee'): 'fetes_fin', ('fetes', 'de', 'fin', 'de', 'l', 'annee'): 'fetes_fin',
    ('paques',): 'paques', ('dimanche', 'de', 'paques'): 'dimanche_paques', ('lundi', 'de', 'paques'): 'lundi_paques',
    ('ascension',): 'ascension', ('pentecote',): 'pentecote', ('lundi', 'de', 'pentecote'): 'lundi_pentecote',
    ('lundi', 'de', 'la', 'pentecote'): 'lundi_pentecote',
    ('fete', 'nationale'): 'quatorze', ('fete', 'du', 'travail'): 'travail', ('assomption',): 'assomption',
    ('toussaint',): 'toussaint', ('halloween',): 'halloween', ('saint', 'valentin'): 'valentin',
    ('fete', 'de', 'la', 'musique'): 'musique',
    ('vacances', 'de', 'noel'): 'vacances_noel', ('vacances', 'de', 'la', 'toussaint'): 'vacances_toussaint',
    ('vacances', 'de', 'toussaint'): 'vacances_toussaint', ('vacances', 'd', 'automne'): 'vacances_toussaint',
    ('vacances', 'de', 'fevrier'): 'vacances_hiver', ('vacances', 'd', 'hiver'): 'vacances_hiver',
    ('vacances', 'de', 'ski'): 'vacances_hiver', ('vacances', 'de', 'paques'): 'vacances_printemps',
    ('vacances', 'de', 'printemps'): 'vacances_printemps', ('grandes', 'vacances'): 'grandes_vacances',
    ('vacances', 'd', 'ete'): 'grandes_vacances',
}
_FETES_PAR_MOT = {}
for _suite_, _cle_ in sorted(_NOMS_FETES.items(), key=lambda x: -len(x[0])):
    _FETES_PAR_MOT.setdefault(_suite_[0], []).append((_suite_, _cle_))
# Un de ces mots seul n'est jamais essayé comme lieu (« Avril », « Pâques », « Toussaint »).
_MOTS_DATES = (set(_MOIS) | set(_SAISONS) | set(_JOURS_SEMAINE) | {s[0] for s in _NOMS_FETES if len(s) == 1}
               | {'hier', 'aujourd', 'aujourdhui', 'hui', 'demain', 'veille', 'lendemain', 'matin', 'soir', 'nuit',
                  'midi', 'semaine', 'mois', 'annee', 'annees', 'an', 'ans', 'jour', 'jours', 'week', 'end',
                  'weekend', 'saint'})

_SAISONS_REGLES = {
    'printemps': _Regle('printemps', _fixe(3, 1, 6, 20)),
    'ete': _Regle('été', _fixe(6, 1, 9, 22)),
    'automne': _Regle('automne', _fixe(9, 1, 12, 20)),
    'hiver': _Regle('hiver', _fixe(12, 1, 3, 20), True, _deux_annees('hiver')),
}


_PARTIES_FR = {None: '{}', 'debut': 'début {}', 'mi': 'mi-{}', 'fin': 'fin {}'}


def _regle_mois(m, partie=None):
    """Le mois m, ou son début (1-10), son milieu (10-20), sa fin (20-dernier jour)."""
    nom = _PARTIES_FR[partie].format(_NOMS_MOIS[m])

    def occ(a):
        fin = calendar.monthrange(a, m)[1]
        j1, j2 = {None: (1, fin), 'debut': (1, 10), 'mi': (10, 20), 'fin': (20, fin)}[partie]
        return [(date(a, m, j1), date(a, m, j2))]
    return _Regle(nom, occ)


def _regle_mois_plage(m1, m2):
    """Du début du mois m1 à la fin du mois m2 (de l'année suivante si m2 vient avant m1)."""
    def occ(a):
        a2 = a + (m2 < m1)
        return [(date(a, m1, 1), _dernier_jour(a2, m2))] if a2 <= 9999 else []
    return _Regle(f'{_NOMS_MOIS[m1]} à {_NOMS_MOIS[m2]}', occ)


def _union(regles, nom):
    """Une règle qui réunit les occurrences de plusieurs (« en mars et avril »)."""
    if len(regles) == 1:
        return regles[0]
    noms = {}                                   # début d'une occurrence -> son libellé (règle d'une seule analyse)

    def occ(a):
        res = []
        for r in regles:
            for d, f in r.occ(a):
                noms[d] = r.libelle(d)
                res.append((d, f))
        return res
    return _Regle(nom, occ, any(r.chevauche for r in regles), lambda d: noms.get(d) or f'{nom} {d.year}')


def _occurrences(regle, a1, a2):
    res = []
    for a in range(max(a1, 1), min(a2, 9998) + 1):
        for d, f in regle.occ(a):
            res.append((d, f, regle.libelle(d)))
    return res


def _dans_annees(regle, a1, a2):
    """Occurrences « en a1 … a2 » : qui y commencent, ou qui y touchent pour une règle qui chevauche."""
    if not regle.chevauche:
        return _occurrences(regle, a1, a2)
    lo, hi = date(a1, 1, 1), date(a2, 12, 31)
    return [o for o in _occurrences(regle, a1 - 1, a2) if o[0] <= hi and o[1] >= lo]


def _toutes(regle, auj):
    return _occurrences(regle, ANNEE_MIN, auj.year + 1)


def _voisines(regle, jour):
    return sorted(_occurrences(regle, jour.year - 2, jour.year + 1))


def _courante(regle, auj):
    """« cet été », « ce Noël » : la dernière occurrence commencée."""
    occ = [o for o in _voisines(regle, auj) if o[0] <= auj]
    return occ[-1:]


def _derniere(regle, auj):
    """« l'été dernier » : la dernière occurrence finie ; si elle a commencé cette année, celle d'avant aussi."""
    occ = [o for o in _voisines(regle, auj) if o[1] < auj]
    if not occ:
        return []
    return occ[-2:] if occ[-1][0].year == auj.year else occ[-1:]


def _proche(regle, jour):
    """L'occurrence la plus proche de ce jour (« Noël il y a 2 ans »)."""
    occ = _voisines(regle, jour)

    def ecart(o):
        return 0 if o[0] <= jour <= o[1] else min(abs((o[0] - jour).days), abs((o[1] - jour).days))
    return [min(occ, key=ecart)] if occ else []


class _Expr:
    """Une date trouvée dans le texte, mots i à j (exclu). Une seule de ces formes :
    per : périodes fixes [(debut, fin, libelle)] ; regle : revient chaque année, à combiner ou à étendre à toutes
    les années ; annees : années [(a1, a2)] qui peuvent préciser une règle (seules : leurs périodes, si rien n'est à
    préciser) ; ancre : jour d'« il y a N ans » (idem, avec seules) ; verrou : texte à laisser parmi les mots."""
    __slots__ = ('i', 'j', 'libelle', 'per', 'regle', 'annees', 'seules', 'ancre', 'verrou')

    def __init__(self, i, j, libelle, per=None, regle=None, annees=None, seules=None, ancre=None, verrou=False):
        self.i, self.j, self.libelle = i, j, libelle
        self.per, self.regle, self.annees, self.seules, self.ancre, self.verrou = (per, regle, annees, seules,
                                                                                    ancre, verrou)


_RELATIFS = {
    ('aujourd', 'hui'): 'aujourdhui', ('aujourdhui',): 'aujourdhui', ('hier',): 'hier', ('avant', 'hier'): 'avant_hier',
    ('ce', 'matin'): 'aujourdhui', ('ce', 'midi'): 'aujourdhui', ('cet', 'apres', 'midi'): 'aujourdhui',
    ('ce', 'soir'): 'aujourdhui', ('cette', 'nuit'): 'cette_nuit',
    ('cette', 'semaine'): 'semaine', ('cette', 'semaine', 'ci'): 'semaine',
    ('semaine', 'derniere'): 'semaine_d', ('semaine', 'passee'): 'semaine_d',
    ('ce', 'week', 'end'): 'weekend', ('ce', 'weekend'): 'weekend',
    ('week', 'end', 'dernier'): 'weekend_d', ('weekend', 'dernier'): 'weekend_d',
    ('week', 'end', 'passe'): 'weekend_d', ('weekend', 'passe'): 'weekend_d',
    ('ce', 'mois', 'ci'): 'mois', ('ce', 'mois'): 'mois', ('mois', 'dernier'): 'mois_d', ('mois', 'passe'): 'mois_d',
    ('cette', 'annee'): 'annee', ('cette', 'annee', 'ci'): 'annee', ('an', 'dernier'): 'annee_d',
    ('an', 'passe'): 'annee_d', ('annee', 'derniere'): 'annee_d', ('annee', 'passee'): 'annee_d',
}
_RELATIFS_PAR_MOT = {}
for _suite_, _cle_ in sorted(_RELATIFS.items(), key=lambda x: -len(x[0])):
    _RELATIFS_PAR_MOT.setdefault(_suite_[0], []).append((_suite_, _cle_))
_MODIFS = ((('a', 'partir', 'de'), 'depuis'), (('a', 'partir', 'du'), 'depuis'), (('a', 'partir', 'd'), 'depuis'),
           (('jusqu', 'en'), 'jusque'), (('jusqu', 'au'), 'jusque'), (('jusqu', 'a'), 'jusque'),
           (('depuis',), 'depuis'), (('avant',), 'avant'), (('apres',), 'apres'), (('vers',), 'vers'))
_MOTS_MODIFS = {s[0] for s, _x in _MODIFS}
_MODIFS_FR = {'depuis': ('depuis', 'depuis le'), 'apres': ('après', 'après le'), 'avant': ('avant', 'avant le'),
              'jusque': ("jusqu'en", "jusqu'au")}
_UN_JOUR = timedelta(days=1)


class _Lecture:
    """Une recherche découpée en mots, et ce qu'on y a reconnu. etat[k] : 0 libre, 1 verrouillé (texte d'une puce
    retirée, ou à garder parmi les mots), 2 date, 3 lieu."""

    def __init__(self, texte, auj):
        self.t, self.auj = texte, auj
        self.j = _jetons(texte)
        self.n = [x.n for x in self.j]
        self.N = len(self.j)
        self.etat = [0] * self.N
        self.capitales = not any(c.islower() for c in texte)       # « PLAGE EN BRETAGNE » : majuscules muettes
        self._noyaux = (self.relatif, self.numerique, self.jours, self.fete, self.mois, self.saison, self.decennie,
                        self.annee_seule, self.modifie)

    # ------------------------------------------------ accès aux mots

    def mot(self, k):
        """Forme normalisée du mot k s'il est libre, sinon None : un mot déjà pris fait barrière."""
        return self.n[k] if 0 <= k < self.N and self.etat[k] == 0 else None

    def suite(self, k, seq):
        if k < 0 or k + len(seq) > self.N:
            return False
        for x, w in enumerate(seq):
            if self.etat[k + x] or self.n[k + x] != w:
                return False
        return True

    def texte(self, i, j):
        return self.t[self.j[i].debut:self.j[j - 1].fin]

    def verrouiller(self, ignorer):
        """Les textes des puces retirées redeviennent des mots, ni date ni lieu."""
        if not ignorer:
            return
        if isinstance(ignorer, str):
            ignorer = (ignorer,)
        for txt in ignorer:
            if not isinstance(txt, str):
                continue
            seq = [x.n for x in _jetons(unicodedata.normalize('NFC', txt))]
            for s in range(self.N - len(seq) + 1) if seq else ():
                if self.n[s:s + len(seq)] == seq:
                    for k in range(s, s + len(seq)):
                        self.etat[k] = 1

    # ------------------------------------------------ nombres, années, jours, mois

    def annee(self, k, maxi):
        w = self.mot(k)
        if w is None or len(w) != 4:
            return None
        v = self.j[k].nombre
        return v if v is not None and ANNEE_MIN <= v <= maxi else None

    def siecle(self, v):
        """Année sur deux chiffres : jusqu'à l'an prochain dans les années 2000, sinon dans les années 1900."""
        return 2000 + v if v <= (self.auj.year + 1) % 100 else 1900 + v

    def annee_jointe(self, k):
        """Année jointe à un jour ou un mois : 4 chiffres jusqu'à l'an prochain, ou 2 chiffres. (année, suite)."""
        y = self.annee(k, self.auj.year + 1)
        if y is not None:
            return y, k + 1
        w = self.mot(k)
        if w is not None and len(w) == 2 and self.j[k].nombre is not None and not self.jour_ou_quantite(k):
            y = self.siecle(self.j[k].nombre)
            if ANNEE_MIN <= y <= self.auj.year + 1:
                return y, k + 1
        return None, k

    def jour_ou_quantite(self, k):
        """Le nombre de deux chiffres du mot k est-il un jour ou une quantité, plutôt qu'une année ? Un jour s'il
        commence une date (« Noël 25 décembre », « Noël 24 au 26 décembre ») ; une quantité devant une unité ou un
        signe (« été 30 degrés », « été 30° », « août 15 jours », « Noël 10 ans », « Pâques 10 enfants »)."""
        suite = self.t[self.j[k].fin:self.j[k].fin + 2].lstrip()
        if suite[:1] in _SIGNES_QUANTITE:
            return True
        if k + 1 < self.N and self.n[k + 1] in _QUANTITES:
            return True
        return self.jours(k, ()) is not None

    def annee_hors(self, k):
        """Le mot k ressemble à une année (4 chiffres, 1000 à 2999) mais n'est pas permis (« Noël 2045 »,
        « 14 juillet 1789 ») : toute l'expression reste alors des mots."""
        w = self.mot(k)
        return (w is not None and len(w) == 4 and self.j[k].nombre is not None and 1000 <= self.j[k].nombre < 3000
                and self.annee(k, self.auj.year + 1) is None)

    def jour_num(self, k):
        w = self.mot(k)
        if w in ('1er', '1ere', 'premier'):
            return 1
        if w is not None and len(w) <= 2 and self.j[k].nombre is not None and 1 <= self.j[k].nombre <= 31:
            return self.j[k].nombre
        return None

    def mois_num(self, k, abreges=True):
        w = self.mot(k)
        if w is None:
            return None
        return _MOIS.get(w) or (_MOIS_ABREGES.get(w) if abreges else None)

    def nombre(self, k):
        """Nombre en chiffres ou en lettres (« deux », « quinze »), ou 'quelques' : (valeur, suite)."""
        w = self.mot(k)
        if w is None:
            return None, k
        if self.j[k].nombre is not None and len(w) <= 4:
            return self.j[k].nombre, k + 1
        if w == 'quelques':
            return w, k + 1
        v = _NOMBRES.get(w)
        return (v, k + 1) if v is not None else (None, k)

    def plage_composee(self, k, maxi):
        """Jeton « 2019-2020 », « 2019/20 » : (a1, a2), ou None."""
        p = self.j[k].parties if self.mot(k) is not None else None
        if not p or len(p) != 2 or len(p[0]) != 4 or len(p[1]) not in (2, 4):
            return None
        a1 = int(p[0])
        a2 = int(p[1]) if len(p[1]) == 4 else a1 // 100 * 100 + int(p[1])
        if len(p[1]) == 4:
            a1, a2 = min(a1, a2), max(a1, a2)
        if a2 <= a1 or a1 < ANNEE_MIN or a2 > maxi:
            return None
        return a1, a2

    def suite_annees(self, k, maxi, deux_chiffres=False):
        """Années jointes : « 2019 », « 2019 et 2020 », « 2015 à 2019 », « de 2015 à 2019 », « entre 2015 et 2019 »,
        « 2019-2020 », et « 98 » si deux_chiffres. ([(a1, a2)], suite) ou (None, k)."""
        w = self.mot(k)
        if w is None:
            return None, k
        if w in ('de', 'entre'):
            a1, a2 = self.annee(k + 1, maxi), self.annee(k + 3, maxi)
            if a1 and a2 and self.mot(k + 2) in (('a', 'au') if w == 'de' else ('et',)):
                return [(min(a1, a2), max(a1, a2))], k + 4
            if w == 'de' and a1 and self.mot(k + 2) not in ('a', 'au', '-'):     # « l'été de 2019 »
                return [(a1, a1)], k + 2
            return None, k
        pc = self.plage_composee(k, maxi)
        if pc:
            return [pc], k + 1
        a = self.annee(k, maxi)
        if a is None:
            if deux_chiffres and len(w) == 2:
                y, k2 = self.annee_jointe(k)
                if y is not None:
                    return [(y, y)], k2
            return None, k
        if self.mot(k + 1) in ('a', 'au', '-'):
            a2 = self.annee(k + 2, maxi)
            if a2:
                return [(min(a, a2), max(a, a2))], k + 3
        res, k = [(a, a)], k + 1
        while True:
            a2 = self.annee(k, maxi)
            if a2:
                res.append((a2, a2))
                k += 1
                continue
            if self.mot(k) in ('et', 'ou') and self.annee(k + 1, maxi):
                a2 = self.annee(k + 1, maxi)
                res.append((a2, a2))
                k += 2
                continue
            return res, k

    def complement(self, q, prolonge):
        """Le nom de date du mot q complète-t-il un nom (« sapin de Noël », « robe d'été ») ? None si oui. Sinon
        (début, fort) : le début recule sur un mot de temps de `prolonge` (« samedi de Pâques ») ; fort après
        « photos de » ou un mot de temps."""
        k = q - 1
        if k >= 0 and self.n[k] in _ARTICLES:
            k -= 1
        if k < 0 or self.n[k] not in _DE:
            return q, False
        h = k - 1
        if h < 0 or self.etat[h] >= 2:
            return q, False
        tete = self.n[h]
        if tete in _NEUTRES:
            return q, True
        if tete in _PAS_TETE:
            return q, False
        if tete in prolonge:
            if all(self.etat[x] == 0 for x in range(h, q)):
                if tete == 'end' and h > 0 and self.n[h - 1] == 'week' and self.etat[h - 1] == 0:
                    h -= 1
                return h, True
            return q, True
        if self.j[h].nombre is not None or not tete.isalpha():
            return q, False
        return None

    def la_demonstratif(self, k):
        """Le mot k est le « là » de « ce soir-là » : écrit « là », ou « la » collé par un tiret."""
        if not 0 < k < self.N or self.n[k] != 'la':
            return False
        x = self.j[k]
        return x.brut.casefold() == 'là' or any(c in _TIRETS for c in self.t[self.j[k - 1].fin:x.debut])

    def voie(self, p):
        """« rue du 14 juillet », « avenue du 8 mai 1945 » : un nom de rue, pas une date."""
        return p >= 2 and self.n[p - 1] in _DE and self.n[p - 2] in _VOIES

    # ------------------------------------------------ expressions

    def periode(self, i, j, d, f, lib=None):
        lib = lib or _plage_fr(d, f)
        return _Expr(i, j, lib, per=[(d, f, lib)])

    def expr_annees(self, i, j, intervalles, lib=None, seules=None):
        lib = lib or _annees_fr(intervalles)
        if seules is None:
            seules = [(date(a1, 1, 1), date(a2, 12, 31), lib) for a1, a2 in intervalles]
        return _Expr(i, j, lib, annees=intervalles, seules=seules)

    def expr_regle(self, i, j, regle, annees, mode=None):
        if annees:
            per = [o for a1, a2 in annees for o in _dans_annees(regle, a1, a2)]
            return _Expr(i, j, f'{regle.nom} {_annees_fr(annees)}', per=per) if per else None
        if mode:
            per = _courante(regle, self.auj) if mode == 'courante' else _derniere(regle, self.auj)
            return _Expr(i, j, ' ou '.join(o[2] for o in reversed(per)), per=per) if per else None
        return _Expr(i, j, regle.nom, regle=regle)

    # ------------------------------------------------ lecture des dates

    def dates(self):
        """Toutes les dates, de gauche à droite, la plus longue à chaque mot."""
        exprs, i = [], 0
        while i < self.N:
            e = None
            if self.etat[i] == 0:
                try:
                    e = self.date_en(i)
                except Exception:                        # date hors du calendrier, ou défaut : pas une date ici
                    if _STRICT:
                        raise
                    e = None
            if e is None:
                i += 1
                continue
            if self.la_demonstratif(e.j) and any(self.n[k] in ('ce', 'cet', 'cette') for k in range(e.i, e.j)):
                e = _Expr(e.i, e.j + 1, '', verrou=True)     # « ce soir-là », « cette année-là » : pas aujourd'hui
            for k in range(e.i, e.j):
                self.etat[k] = 1 if e.verrou else 2
            if not e.verrou:
                exprs.append(e)
            i = e.j
        return exprs

    def date_en(self, i, modifier=True):
        """La plus longue date qui commence au mot i, après 0 à 3 petits mots d'introduction (« lors de la »)."""
        meilleure, intro, k = None, (), i
        noyaux = self._noyaux if modifier else self._noyaux[:-1]
        while True:
            for f in noyaux:
                e = f(k, intro)
                if e is not None and (meilleure is None or e.j > meilleure.j):
                    e.i = min(e.i, i)
                    meilleure = e
            w = self.mot(k)
            if len(intro) == 3 or w not in _INTRO:
                return meilleure
            intro += (w,)
            k += 1

    def relatif(self, p, intro):
        w = self.mot(p)
        if w is None:
            return None
        a = self.auj
        for seq, cle in _RELATIFS_PAR_MOT.get(w, ()):
            if self.suite(p, seq):
                return self.expr_relative(cle, p, p + len(seq))
        if w in _JOURS_SEMAINE and self.mot(p + 1) in _DERNIER:
            d = a - timedelta(days=(a.weekday() - _JOURS_SEMAINE[w]) % 7 or 7)
            return self.periode(p, p + 2, d, d, f'{w} {_jour_fr(d)}')
        if w == 'il' and self.suite(p, ('il', 'y', 'a')):
            v, k = self.nombre(p + 3)
            u = _UNITES_TEMPS.get(self.mot(k)) if v is not None else None
            return self.il_y_a(p, k + 1, v, u) if u else None
        if w == 'depuis':
            v, k = self.nombre(p + 1)
            u = _UNITES_TEMPS.get(self.mot(k)) if isinstance(v, int) and v > 0 else None
            if u:
                d = {'j': lambda: a - timedelta(days=v), 's': lambda: a - timedelta(days=7 * v),
                     'm': lambda: _moins_mois(a, v), 'a': lambda: _moins_ans(a, v)}[u]()
                if d is not None and d.year >= ANNEE_MIN:
                    return self.periode(p, k + 1, d, a, f'depuis le {_jour_fr(d)}')
        return None

    def expr_relative(self, cle, i, j):
        a = self.auj
        if cle in ('aujourdhui', 'hier', 'avant_hier'):
            d = a - timedelta(days={'aujourdhui': 0, 'hier': 1, 'avant_hier': 2}[cle])
            return self.periode(i, j, d, d)
        if cle == 'cette_nuit':
            return self.periode(i, j, a - _UN_JOUR, a)
        if cle in ('semaine', 'semaine_d'):
            lun = _lundi(a) - timedelta(days=7 * (cle == 'semaine_d'))
            return self.periode(i, j, lun, lun + timedelta(days=6))
        if cle in ('weekend', 'weekend_d'):
            sam = _lundi(a) + timedelta(days=5)
            if cle == 'weekend_d' or a.weekday() < 5:
                sam -= timedelta(days=7)
            return self.periode(i, j, sam, sam + _UN_JOUR)
        if cle == 'mois':
            return self.periode(i, j, a.replace(day=1), _dernier_jour(a.year, a.month), _mois_fr(a.year, a.month))
        if cle == 'mois_d':
            f = a.replace(day=1) - _UN_JOUR
            return self.periode(i, j, f.replace(day=1), f, _mois_fr(f.year, f.month))
        y = a.year - (cle == 'annee_d')
        return self.expr_annees(i, j, [(y, y)])

    def il_y_a(self, i, j, v, u):
        """« il y a 3 jours », « il y a 2 ans », « il y a quelques mois » (voir l'en-tête du module)."""
        a = self.auj
        if v == 'quelques':
            if u == 'a':
                return self.expr_annees(i, j, [(a.year - 8, a.year - 2)])
            d, f = {'j': (a - timedelta(days=10), a - _UN_JOUR), 's': (a - timedelta(days=56), a - timedelta(days=7)),
                    'm': (_moins_mois(a, 9), _moins_mois(a, 1))}[u]
            return self.periode(i, j, d, f)
        if v < 1:
            return None
        if u == 'j':
            c, marge = a - timedelta(days=v), timedelta(days=v // 7)
            d, f, lib = c - marge, c + marge, (f'vers le {_jour_fr(c)}' if v >= 7 else _jour_fr(c))
        elif u == 's':
            c = a - timedelta(days=7 * v)
            lun = _lundi(c)
            d, f = min(c - timedelta(days=3), lun), max(c + timedelta(days=3), lun + timedelta(days=6))
            lib = f'vers le {_jour_fr(c)}'
        elif u == 'm':
            c = _moins_mois(a, v)
            if c is None:
                return None
            d = min(c.replace(day=1), c - timedelta(days=15))
            f = max(_dernier_jour(c.year, c.month), c + timedelta(days=15))
            lib = f'vers {_mois_fr(c.year, c.month)}'
        else:
            c = _moins_ans(a, v)
            if c is None or c.year < ANNEE_MIN:
                return None
            d = min(date(c.year, 1, 1), c - timedelta(days=92))
            f = max(date(c.year, 12, 31), c + timedelta(days=92))
            lib = f'vers {c.year}'
            return _Expr(i, j, lib, seules=[(d, f, lib)], ancre=c)
        return self.periode(i, j, d, f, lib) if d.year >= ANNEE_MIN else None

    def numerique(self, p, intro):
        """« 14/07/2019 », « 14.07.19 », « 2019-07-14 », « 07/2019 », « 2019-07 », « 2018-2020 », « 2018 - 2020 »."""
        if self.mot(p) is None:
            return None
        a = self.auj
        ps = self.j[p].parties
        if ps is None:
            a1 = self.annee(p, a.year)
            a2 = self.annee(p + 2, a.year) if a1 and self.mot(p + 1) == '-' else None
            return self.expr_annees(p, p + 3, [(min(a1, a2), max(a1, a2))]) if a2 else None
        lg, v, maxi = [len(s) for s in ps], [int(s) for s in ps], a.year + 1
        if len(ps) == 3:
            if lg[0] == 4 and lg[1] <= 2 and lg[2] <= 2:
                y, m, d = v
            elif lg[0] <= 2 and lg[1] <= 2 and lg[2] in (2, 4):
                d, m, y = v
                y = self.siecle(y) if lg[2] == 2 else y
            else:
                return None
            jour = _jour(y, m, d) if ANNEE_MIN <= y <= maxi and 1 <= m <= 12 else None
            return self.periode(p, p + 1, jour, jour) if jour else None
        if len(ps) != 2:
            return None
        if lg[0] == 4:
            pc = self.plage_composee(p, a.year)
            if pc:
                return self.expr_annees(p, p + 1, [pc])
            y, m = v
        elif lg[1] == 4 and lg[0] <= 2:
            m, y = v
        else:
            return None
        if lg[0] + lg[1] > 6 or not (1 <= m <= 12 and ANNEE_MIN <= y <= maxi):
            return None
        return self.periode(p, p + 1, date(y, m, 1), _dernier_jour(y, m), _mois_fr(y, m))

    def jours(self, p, intro):
        """« le 3 mai 2020 », « 1er janvier », « 14 juillet », « du 3 au 10 mai 2020 », « 3-10 mai », « 3 et 4 mai »."""
        w = self.mot(p)
        if w is None:
            return None
        ps = self.j[p].parties
        if ps is not None:
            if len(ps) != 2 or max(len(s) for s in ps) > 2:
                return None
            d1, d2, m = int(ps[0]), int(ps[1]), self.mois_num(p + 1)
            if not m or not 1 <= d1 < d2 <= 31:
                return None
            y, k = self.annee_jointe(p + 2)
            return self.expr_jours(p, k, d1, m, None, d2, m, y)
        d1 = self.jour_num(p)
        if d1 is None:
            return None
        k, y1 = p + 1, None
        m1 = self.mois_num(k)
        if m1:
            y1, k = self.annee_jointe(k + 1)
            if y1 is None and self.annee_hors(k):
                return _Expr(p, k + 1, '', verrou=True)
        if m1 and self.voie(p):
            return _Expr(p, k, '', verrou=True)
        c = self.mot(k)
        if c in ('au', 'a', '-', 'et'):
            d2 = self.jour_num(k + 1)
            m2 = self.mois_num(k + 2) if d2 else None
            if m2 and not self.voie(p):
                y2, k2 = self.annee_jointe(k + 3)
                e = self.expr_jours(p, k2, d1, m1 or m2, y1, d2, m2, y2, c == 'et')
                if e is not None:
                    return e
        if not m1:
            return None
        if (d1, m1) == (14, 7):
            return self.expr_regle(p, k, _FETES['quatorze'], [(y1, y1)] if y1 else None)
        e = self.expr_jours(p, k, d1, m1, y1, d1, m1, y1)
        return e if e is not None else _Expr(p, k, '', verrou=True)     # « 29 février 2019 » : reste des mots

    def expr_jours(self, i, j, d1, m1, y1, d2, m2, y2, liste=False):
        if y1 is None and y2 is None:                    # tous les ans
            if _jour(2000, m1, d1) is None or _jour(2000, m2, d2) is None:
                return None
            j1, j2 = _jour_fr(date(2000, m1, d1), False), _jour_fr(date(2000, m2, d2), False)
            if liste:
                r = _union([_Regle(j1, _fixe(m1, d1, m1, d1)), _Regle(j2, _fixe(m2, d2, m2, d2))], f'{j1} et {j2}')
            elif (m1, d1) == (m2, d2):
                r = _Regle(j1, _fixe(m1, d1, m1, d1))
            else:
                debut = j1.split()[0] if m1 == m2 else j1
                r = _Regle(f'{debut} au {j2}', _fixe(m1, d1, m2, d2))
            return _Expr(i, j, r.nom, regle=r)
        y2 = y2 or y1
        y1 = y1 or (y2 - 1 if (m1, d1) > (m2, d2) else y2)
        d, f = _jour(y1, m1, d1), _jour(y2, m2, d2)
        if d is None or f is None or f < d:
            return None
        if liste and d != f:
            lib = (f"{'1er' if d.day == 1 else d.day} et {_jour_fr(f)}" if (d.year, d.month) == (f.year, f.month)
                   else f'{_jour_fr(d, d.year != f.year)} et {_jour_fr(f)}')
            return _Expr(i, j, lib, per=[(d, d, _jour_fr(d)), (f, f, _jour_fr(f))])
        return self.periode(i, j, d, f)

    def fete(self, p, intro):
        w = self.mot(p)
        if w is None:
            return None
        q, mode = (p + 1, 'courante') if w in ('ce', 'cet', 'cette') else (p, None)
        cle = None
        for seq, c in _FETES_PAR_MOT.get(self.mot(q), ()):
            if self.suite(q, seq):
                cle, k = c, q + len(seq)
                break
        if cle is None:
            return None
        debut = p
        if mode is None:
            r = self.complement(q, _TETES_FETES)
            if r is None:
                return None
            debut = r[0]
        avant = self.n[q - 1] if q > 0 else None
        if cle == 'noel' and avant in ('pere', 'papa', 'mere'):
            return None
        if cle == 'nouvel_an' and self.mot(k) in _NOUVEL_AN_AUTRES:
            return None
        regle = _FETES[cle]
        annees, k2 = self.suite_annees(k, self.auj.year + 1, deux_chiffres=True)
        if cle == 'ascension' and k == q + 1:            # « ascension du mont Blanc » : une montée
            if self.mot(k) in _DE or not (annees or avant == 'l' or (self.j[q].maj and not self.capitales)):
                return None
        if annees:
            return self.expr_regle(debut, k2, regle, annees)
        suivant = self.mot(k)
        if suivant in _DERNIER:
            return self.expr_regle(debut, k + 1, regle, None, 'derniere')
        if suivant in _PROCHAIN or self.annee_hors(k):
            return _Expr(debut, k + 1, '', verrou=True)
        return self.expr_regle(debut, k, regle, None, mode)

    def mois(self, p, intro):
        w = self.mot(p)
        if w is None:
            return None
        q, partie, fort = p, None, not _FORTS_MOIS.isdisjoint(intro)
        if w in _PARTIES:                                # « début août », « fin de mars 2019 », « courant mai »
            q = p + 1 + (self.mot(p + 1) in ('de', 'd'))
            if self.mois_num(q, False) is None:
                return None
            partie, fort = _PARTIES[w], True
        elif w == 'mois' and self.mot(p + 1) in ('de', 'd'):
            q, fort = p + 2, True
        elif w == 'entre':
            m1, m2 = self.mois_num(p + 1, False), self.mois_num(p + 3, False)
            return self.plage_mois(p, p + 4, m1, None, m2) if m1 and m2 and self.mot(p + 2) == 'et' else None
        m = self.mois_num(q)
        if m is None:
            return None
        debut = p
        if q == p:
            r = self.complement(q, _TETES_PARTIES)
            if r is None:
                return None
            debut, fort = r[0], fort or r[1]
        k = q + 1
        if partie is None:                               # « mars à mai 2021 », « novembre 2019 à février 2020 »
            y1 = self.annee(k, self.auj.year + 1)
            k1 = k + 1 if y1 else k
            m2 = self.mois_num(k1 + 1, False) if self.mot(k1) in ('a', 'au') else None
            if m2:
                e = self.plage_mois(debut, k1 + 2, m, y1, m2)
                if e is not None:
                    return e
        mois = [m]
        while partie is None:                            # « mars et avril 2021 »
            m2 = self.mois_num(k, False)
            if m2:
                mois.append(m2)
                k += 1
            elif self.mot(k) in ('et', 'ou') and self.mois_num(k + 1, False):
                mois.append(self.mois_num(k + 1, False))
                k += 2
            else:
                break
        regles = [_regle_mois(x, partie) for x in mois]
        regle = _union(regles, ' et '.join(r.nom for r in regles))
        annees, k2 = self.suite_annees(k, self.auj.year + 1, deux_chiffres=len(mois) == 1)
        if annees:
            return self.expr_regle(debut, k2, regle, annees)
        suivant = self.mot(k)
        if suivant in _DERNIER:
            return self.expr_regle(debut, k + 1, regle, None, 'derniere')
        if self.annee_hors(k):                           # « mai 2045 » : des mots
            return _Expr(debut, k + 1, '', verrou=True)
        # Sans année : seulement avec « en », « début »…, en toutes lettres, et jamais « sept ans ».
        if (not fort or suivant in _PROCHAIN or suivant in _UNITES_TEMPS or suivant in _UNITES
                or any(self.n[x] in _MOIS_ABREGES for x in range(q, k))):
            return None
        return _Expr(debut, k, regle.nom, regle=regle)

    def plage_mois(self, i, j, m1, y1, m2):
        """« de mars à mai 2021 », « entre mars et mai », « novembre 2019 à février 2020 »."""
        y2, j2 = self.annee_jointe(j)
        if y2 is None and y1 is not None:
            y2 = y1 + (m2 < m1)
        if y2 is None:
            r = _regle_mois_plage(m1, m2)
            return _Expr(i, j, r.nom, regle=r)
        y1 = y1 or y2 - (m1 > m2)
        if (y1, m1) > (y2, m2) or y1 < ANNEE_MIN:
            return None
        lib = (f'{_NOMS_MOIS[m1]} à {_mois_fr(y2, m2)}' if y1 == y2
               else f'{_mois_fr(y1, m1)} à {_mois_fr(y2, m2)}')
        return self.periode(i, j2, date(y1, m1, 1), _dernier_jour(y2, m2), lib)

    def saison(self, p, intro):
        w = self.mot(p)
        if w is None:
            return None
        q, mode, fort = p, None, not _FORTS_SAISON.isdisjoint(intro)
        if w in ('ce', 'cet', 'cette'):
            q, mode = p + 1, 'courante'
        elif w in ('debut', 'fin', 'milieu'):           # « fin de l'été 2019 » : l'été
            q = p + 1 + (self.mot(p + 1) in ('de', 'du', 'd'))
            q += self.mot(q) in _ARTICLES
            fort = True
        s = _SAISONS.get(self.mot(q))
        if s is None:
            return None
        if s == 'ete' and ((q > 0 and self.n[q - 1] in _AUXILIAIRES) or self.mot(q + 1) == 'indien'):
            return None                                  # « a été », « avait été », « l'été indien »
        debut = p
        if q == p:
            r = self.complement(q, _TETES_PARTIES)
            if r is None:
                return None
            debut, fort = r[0], fort or r[1]
        k, saisons = q + 1, [s]
        while True:                                      # « été et automne 2020 »
            if _SAISONS.get(self.mot(k)):
                saisons.append(_SAISONS[self.mot(k)])
                k += 1
            elif self.mot(k) in ('et', 'ou') and _SAISONS.get(self.mot(k + 1)):
                saisons.append(_SAISONS[self.mot(k + 1)])
                k += 2
            else:
                break
        regles = [_SAISONS_REGLES[x] for x in saisons]
        regle = _union(regles, ' et '.join(r.nom for r in regles))
        if mode:
            return self.expr_regle(debut, k, regle, None, mode)
        annees, k2 = self.suite_annees(k, self.auj.year + 1, deux_chiffres=True)
        if annees:
            if saisons == ['hiver'] and len(annees) == 1 and annees[0][1] == annees[0][0] + 1:
                a1 = annees[0][0]                        # « hiver 2019-2020 » : un seul hiver
                return _Expr(debut, k2, f'hiver {a1}-{a1 + 1}', per=_occurrences(regle, a1, a1))
            return self.expr_regle(debut, k2, regle, annees)
        suivant = self.mot(k)
        if suivant in _DERNIER:
            return self.expr_regle(debut, k + 1, regle, None, 'derniere')
        if suivant in _PROCHAIN or self.annee_hors(k):
            return _Expr(debut, k + 1, '', verrou=True)
        if not fort:
            return None
        if suivant in _DE and self.date_en(k + 1, modifier=False) is None:
            return None                  # « le printemps de Bourges », « l'été de mes 10 ans » : un nom, pas la saison
        return _Expr(debut, k, regle.nom, regle=regle)

    def decennie(self, p, intro):
        """« années 90 », « années 1990 », « années 2000 », « années soixante », « début des années 2000 »."""
        w = self.mot(p)
        if w is None:
            return None
        q, partie = p, None
        if w in ('debut', 'fin', 'milieu'):
            partie, q = w, p + 1 + (self.mot(p + 1) in ('des', 'de'))
        if self.mot(q) != 'annees':
            return None
        k, decs = q + 1, None
        x = self.mot(k)
        if x is not None and self.j[k].nombre is not None:
            v = self.j[k].nombre
            if len(x) == 4 and v % 10 == 0:
                decs = [v]
            elif len(x) == 2 and v % 10 == 0:
                decs = [1900 + v] if v >= 30 else [1900 + v, 2000 + v]    # « années 20 » : 1920 ou 2020
            k += 1
        else:
            for lg in (3, 2, 1):
                seq = tuple(self.n[k:k + lg])
                if len(seq) == lg and seq in _DECENNIES_MOTS and self.suite(k, seq):
                    v = _DECENNIES_MOTS[seq]
                    decs = [v] if v >= 1000 else ([1900 + v] if v >= 30 else [1900 + v, 2000 + v])
                    k += lg
                    break
        decs = [d for d in decs or () if ANNEE_MIN <= d <= self.auj.year]
        if not decs:
            return None
        a, b = {None: (0, 9), 'debut': (0, 3), 'milieu': (3, 6), 'fin': (6, 9)}[partie]
        lib = ' ou '.join(f'années {d}' for d in decs)
        if partie:
            lib = f"{ {'debut': 'début', 'milieu': 'milieu', 'fin': 'fin'}[partie] } des {lib}"
        return self.expr_annees(p, k, [(d + a, d + b) for d in decs], lib)

    def annee_seule(self, p, intro):
        """« 2019 », « en 2019 », « année 2019 », « l'an 2000 », « 2018 à 2020 », « entre 2018 et 2020 »,
        « début 2019 », « mi-2019 », « fin 2019 », « courant 2019 »."""
        w = self.mot(p)
        if w is None:
            return None
        a = self.auj
        if w == 'entre':
            a1, a2 = self.annee(p + 1, a.year), self.annee(p + 3, a.year)
            if a1 and a2 and self.mot(p + 2) == 'et':
                return self.expr_annees(p, p + 4, [(min(a1, a2), max(a1, a2))])
            return None
        if w in _PARTIES:
            k = p + 1
            k += self.mot(k) in ('de', 'd')
            k += self.mot(k) == 'l'
            k += self.mot(k) == 'annee'
            y = self.annee(k, a.year)
            if y is None:
                return None
            if w == 'courant':
                return self.expr_annees(p, k + 1, [(y, y)])
            m1, m2 = {'debut': (1, 4), 'mi': (5, 8), 'milieu': (5, 8), 'fin': (9, 12)}[w]
            lib = {'debut': f'début {y}', 'mi': f'mi-{y}', 'milieu': f'mi-{y}', 'fin': f'fin {y}'}[w]
            return self.periode(p, k + 1, date(y, m1, 1), _dernier_jour(y, m2), lib)
        k, indice = (p, False)
        if w in ('annee', 'annees', 'an') and not (p > 0 and self.n[p - 1] in _VOEUX):   # « bonne année 2020 »
            k, indice = p + 1, True
        y = self.annee(k, a.year)
        if y is None:
            x = self.mot(k)
            if indice and x is not None and len(x) == 2 and self.j[k].nombre is not None:   # « année 98 »
                y = self.siecle(self.j[k].nombre)
                if ANNEE_MIN <= y <= a.year:
                    return self.expr_annees(p, k + 1, [(y, y)])
            return None
        if self.mot(k + 1) in ('a', 'au', '-'):
            y2 = self.annee(k + 2, a.year)
            if y2:
                return self.expr_annees(p, k + 3, [(min(y, y2), max(y, y2))])
        if not indice and ((k + 1 < self.N and self.n[k + 1] in _UNITES)
                           or (not intro and p > 0 and self.n[p - 1] in _AVANT_NUMERO)):
            return None                                  # « 2000 euros », « n° 2019 », « Peugeot 2008 »
        return self.expr_annees(p, k + 1, [(y, y)])

    def modifie(self, p, intro):
        """« depuis 2019 », « avant 2010 », « après l'été 2022 », « jusqu'en 2010 », « vers 2010 », « à partir de »."""
        if self.mot(p) not in _MOTS_MODIFS:
            return None
        for seq, sorte in _MODIFS:
            if self.suite(p, seq):
                k = p + len(seq)
                e = self.date_en(k, modifier=False)
                return self.appliquer(sorte, p, e) if e is not None and e.i == k else None
        return None

    def appliquer(self, sorte, p, e):
        a = self.auj
        if e.verrou:                                     # « avant l'été prochain »
            return None
        if e.regle is not None:
            if sorte == 'vers':                          # « vers Noël » : Noël
                e.i = p
                return e
            if sorte == 'depuis':                        # « depuis Noël » : depuis le dernier Noël commencé
                occ = _courante(e.regle, a)
                return self.periode(p, e.j, occ[0][0], a, f'depuis {occ[0][2]}') if occ else None
            return _Expr(p, e.j, '', verrou=True)        # « avant Noël » : jusqu'où ? reste un mot
        if e.annees is not None:
            a1, a2 = min(x[0] for x in e.annees), max(x[1] for x in e.annees)
            if sorte == 'vers':
                ivs = [(max(ANNEE_MIN, a1 - 1), min(a.year, a2 + 1))]
                return self.expr_annees(p, e.j, ivs, f'vers {e.libelle}')
            ivs = {'depuis': (a1, a.year), 'apres': (a2 + 1, a.year), 'avant': (ANNEE_MIN, a1 - 1),
                   'jusque': (ANNEE_MIN, a2)}[sorte]
            if ivs[0] > ivs[1]:
                return None
            lib = f'{_MODIFS_FR[sorte][0]} {e.libelle}'
            seules = [(date(ivs[0], 1, 1), a if sorte in ('depuis', 'apres') else date(ivs[1], 12, 31), lib)]
            return self.expr_annees(p, e.j, [ivs], lib, seules)
        if sorte == 'vers':
            e.i = p
            return e
        per = e.per if e.per is not None else e.seules
        d, f = min(x[0] for x in per), max(x[1] for x in per)
        d2, f2 = {'depuis': (d, a), 'apres': (f + _UN_JOUR, a), 'avant': (date(ANNEE_MIN, 1, 1), d - _UN_JOUR),
                  'jusque': (date(ANNEE_MIN, 1, 1), f)}[sorte]
        if f2 < d2:
            return None
        jour = d if sorte in ('depuis', 'avant') else f
        lib = (f'{_MODIFS_FR[sorte][1]} {_jour_fr(jour)}' if d == f or sorte in ('avant', 'apres')
               else f'{_MODIFS_FR[sorte][0]} {e.libelle}')          # « après le 22 septembre 2022 »
        return self.periode(p, e.j, d2, f2, lib)

    # ------------------------------------------------ combinaison

    def resoudre(self, exprs):
        """Périodes [(debut, fin, libelle)] : les règles sans année prennent les années tapées ailleurs, sinon
        toutes les années ; les années seules ne comptent pour elles-mêmes que s'il n'y a rien à préciser."""
        regles = [e for e in exprs if e.regle is not None]
        precisions = [e for e in exprs if e.annees is not None or e.ancre is not None]
        per = [o for e in exprs if e.per for o in e.per]
        if regles and precisions:
            combinees = []
            for e in regles:
                for q in precisions:
                    if q.ancre is not None:
                        combinees += _proche(e.regle, q.ancre)
                    else:
                        combinees += [o for a1, a2 in q.annees for o in _dans_annees(e.regle, a1, a2)]
            # Rien dans ces années (« 29 février » et « en 2019 ») : les années seules, plutôt qu'aucune photo.
            per += combinees or [o for q in precisions for o in q.seules]
        elif regles:
            for e in regles:
                per += _toutes(e.regle, self.auj)
                e.libelle += ' (chaque année)'
        else:
            per += [o for q in precisions for o in q.seules]
        return per

    # ------------------------------------------------ lieux

    def lieux(self, chercher):
        """Groupes de mots libres, les plus longs d'abord, de gauche à droite : [(debut, fin, objet, texte)]."""
        trouves = []
        for lg in range(min(MOTS_LIEU_MAX, self.N), 0, -1):
            for s in range(self.N - lg + 1):
                e = s + lg
                if not self.groupe_possible(s, e):
                    continue
                deb, prep, art = self.preposition(s)
                texte = self.texte(s, e)
                nom = _nom_lieu(texte)
                if not nom:
                    continue
                # article : entre la préposition et le nom (« à la plage »), ou dans la préposition (« au port ») ;
                # un nom qui est aussi un mot courant est alors plutôt ce mot-là (choix de l'appelant).
                indices = {'preposition': prep is not None, 'majuscule': self.majuscule(s, e), 'mots': lg,
                           'debut': s == 0, 'texte': texte,
                           'article': art is not None or (prep is not None and prep[-1] in ('au', 'aux', 'du', 'des'))}
                lieu = _appeler(chercher, nom, indices)
                if lieu is None and prep and art is None and prep[-1] in ('au', 'aux', 'du', 'des'):
                    article = 'les ' if prep[-1] in ('aux', 'des') else 'le '      # « au Mans » : « le mans »
                    lieu = _appeler(chercher, article + nom, indices)
                if lieu is None:
                    continue
                for k in range(deb, e):
                    self.etat[k] = 3
                trouves.append((deb, e, lieu, texte))
        trouves.sort(key=lambda x: x[0])
        return trouves

    def groupe_possible(self, s, e):
        for k in range(s, e):
            if self.etat[k] or self.n[k] == '-' or any(c.isdigit() for c in self.n[k]):
                return False
            if k > s and self.j[k].coupure:
                return False
        if self.n[s] in _OUTILS or self.n[e - 1] in _OUTILS_FIN:
            return False
        return e - s > 1 or (len(self.n[s]) > 1 and self.n[s] not in _NON_LIEUX)

    def preposition(self, s):
        """(début, préposition, article) : ce qui part avec le lieu (« près de la »), ou (s, None, None)."""
        k, art = s - 1, None
        if k >= 0 and self.etat[k] == 0 and self.n[k] in _ARTICLES and self.n[s] not in _ARTICLES:
            art, k = k, k - 1
        for seq in _PREPOSITIONS:
            d = k - len(seq) + 1
            if self.suite(d, seq):
                return d, seq, art
        return s, None, None

    def majuscule(self, s, e):
        if self.capitales:
            return False
        k = s
        while k < e - 1 and self.n[k] in _ARTICLES:
            k += 1
        return self.j[s].maj or self.j[k].maj

    # ------------------------------------------------ mots

    def mots(self):
        """Le texte sans les dates ni les lieux, ni les petits mots qu'ils laissent seuls ; '' s'il ne reste que des
        mots sans contenu (« photos de »)."""
        ote = [x >= 2 for x in self.etat]
        if not any(ote):
            return self.t
        n, N = self.n, self.N
        for k in range(N - 2, -1, -1):                   # « photos de [Noël] », « chien et [2019] »
            if not ote[k] and self.etat[k] == 0 and n[k] in _LIANTS and ote[k + 1]:
                ote[k] = True
        k = 0
        while k < N and ote[k]:
            k += 1
        while 0 < k < N and not ote[k] and self.etat[k] == 0 and n[k] in _LIANTS:   # « [Noël] et chien »
            ote[k] = True
            k += 1
        gardes = [k for k in range(N) if not ote[k]]
        if all(n[k] in _REMPLISSAGE for k in gardes):
            return ''
        morceaux, k = [], 0
        while k < N:
            if ote[k]:
                k += 1
                continue
            d = k
            while k + 1 < N and not ote[k + 1]:
                k += 1
            morceaux.append(self.t[self.j[d].debut:self.j[k].fin])
            k += 1
        return _BORDS.sub('', ' '.join(morceaux))


_PREPOSITIONS = sorted([('a',), ('en',), ('au',), ('aux',), ('dans',), ('vers',)]
                       + [(x, y) for x in ('pres', 'autour') for y in ('de', 'du', 'des', 'd')]
                       + [(x, 'cote', y) for x in ('a', 'du') for y in ('de', 'du', 'des', 'd')],
                       key=len, reverse=True)
_OUTILS = {'a', 'en', 'au', 'aux', 'dans', 'vers', 'pres', 'de', 'du', 'des', 'd', 'sur', 'sous', 'chez', 'pour',
           'par', 'avec', 'sans', 'et', 'ou', 'entre', 'depuis', 'pendant', 'durant', 'cote', 'autour', 'il', 'y',
           'un', 'une', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes', 'son', 'sa', 'ses', 'notre', 'nos', 'votre', 'vos',
           'leur', 'leurs', 'ce', 'cet', 'cette', 'ces', 'qui', 'que', 'quoi', 'dont', 'est', 'sont', 'c', 'qu', 'j',
           'n', 's', 'm', 't'}
_OUTILS_FIN = _OUTILS | _ARTICLES
_NON_LIEUX = _OUTILS_FIN | _MOTS_DATES
_LIANTS = {'de', 'du', 'des', 'd', 'en', 'a', 'au', 'aux', 'pendant', 'durant', 'lors', 'pour', 'le', 'la', 'les', 'l',
           'et', 'ou', 'dans', 'vers', 'depuis', 'entre', 'ce', 'cet', 'cette'}
_REMPLISSAGE = _LIANTS | {'photo', 'photos', 'image', 'images', 'cliche', 'cliches', 'souvenir', 'souvenirs', 'mes',
                          'nos', 'tes', 'vos', 'ma', 'mon', 'toutes', 'tous', 'tout', 'une', 'un', 'prises', 'prise',
                          'pris', 'faites', 'fait', 'faite', 'datant', 'datees', 'datee', 'video', 'videos', 'album',
                          'albums', 'selfie', 'selfies', 'voir', 'montre', 'montrer', 'moi', 'afficher', 'cherche',
                          'chercher', 'trouver', 'retrouver', 'y', 'il', 'diaporama'}
_BORDS = re.compile('^[\\s,;:.!?…/&+' + _CLASSE_TIRETS + ']+|[\\s,;:!?…/&+' + _CLASSE_TIRETS + ']+$')
_STRICT = False             # essais : une erreur de lecture d'une date remonte au lieu d'être ignorée


def _appeler(chercher, nom, indices):
    """chercher_lieu(nom, indices) ; une exception ou une réponse vide valent « pas un lieu »."""
    try:
        lieu = chercher(nom, dict(indices))
    except Exception:
        if _STRICT:
            raise
        return None
    if lieu is None or (isinstance(lieu, (list, tuple, set, frozenset, dict, str)) and not lieu):
        return None
    return lieu


def _libelle_lieu(objet, texte):
    """Nom à montrer sur la puce : attribut ou clé « libelle », sinon « nom », sinon le texte tapé."""
    x = objet[0] if isinstance(objet, (list, tuple)) and objet else objet
    if isinstance(x, str):
        return x
    for cle in ('libelle', 'nom'):
        v = x.get(cle) if isinstance(x, dict) else getattr(x, cle, None)
        if isinstance(v, str) and v:
            return v
    return texte


def _preparer(texte):
    """Forme Unicode composée, espaces réduits, 200 caractères au plus (comme recherche.normaliser_requete)."""
    if texte is None:
        return ''
    t = _ESPACES.sub(' ', unicodedata.normalize('NFC', str(texte))).strip()
    return t[:LONGUEUR_MAX].rstrip()


def analyser(texte, aujourd_hui, chercher_lieu=None, ignorer=()):
    """Sépare les dates, les lieux et les mots de la recherche `texte` (voir l'en-tête du module)."""
    if isinstance(aujourd_hui, datetime):
        aujourd_hui = aujourd_hui.date()
    if not isinstance(aujourd_hui, date) or not ANNEE_MIN <= aujourd_hui.year <= 9990:
        raise ValueError('aujourd_hui : une date (datetime.date) entre 1900 et 9990')
    t = _preparer(texte)
    if not t:
        return Analyse('')
    lec = _Lecture(t, aujourd_hui)
    lec.verrouiller(ignorer)
    if _STRICT:
        exprs = lec.dates()
    else:
        try:
            exprs = lec.dates()
        except Exception:                                # jamais d'erreur pour une recherche : pas de date
            lec.etat = [1 if x == 1 else 0 for x in lec.etat]
            exprs = []
    trouves = lec.lieux(chercher_lieu) if chercher_lieu is not None else []
    vues, periodes = set(), []
    for d, f, lib in sorted(lec.resoudre(exprs), key=lambda o: (o[0], o[1])):
        if (d, f) not in vues:
            vues.add((d, f))
            periodes.append(Periode(d, f, lib))
    puces = [(e.i, {'type': 'date', 'libelle': e.libelle, 'texte': lec.texte(e.i, e.j)}) for e in exprs]
    puces += [(i, {'type': 'lieu', 'libelle': _libelle_lieu(objet, txt), 'texte': lec.texte(i, j)})
              for i, j, objet, txt in trouves]
    puces.sort(key=lambda x: x[0])
    return Analyse(lec.mots(), periodes, [objet for _i, _j, objet, _t in trouves], [p for _i, p in puces])


# ---------------------------------------------------------------- appliquer les périodes aux photos

def intervalles(periodes):
    """Périodes (Periode ou (debut, fin)) fusionnées et triées : [(debut, fin)] disjoints, ni contigus."""
    iv = sorted((p.debut, p.fin) if isinstance(p, Periode) else (p[0], p[1]) for p in periodes or ())
    res = []
    for d, f in iv:
        if res and d <= res[-1][1] + _UN_JOUR:
            if f > res[-1][1]:
                res[-1] = (res[-1][0], f)
        else:
            res.append((d, f))
    return res


def _en_date(quand):
    if isinstance(quand, datetime):
        return quand.date()
    if isinstance(quand, date):
        return quand
    if quand is None:
        return None
    try:
        return date.fromisoformat(str(quand)[:10])       # 'AAAA-MM-JJTHH:MM:SS' de l'index, datetime64
    except ValueError:
        return None


class FiltreDates:
    """Les périodes d'une Analyse prêtes à trier des photos. Sans période, tout passe : pas de filtre de date.

    `jour in filtre` (date, datetime, texte ISO de l'index ; None ou illisible : non), en O(log n) ;
    masque(dates) : tableau numpy de booléens pour un tableau datetime64 (NaT : non), celui de base.vecteurs ;
    sql(colonne) : (clause, paramètres) pour une colonne au format de l'index ('AAAA-MM-JJTHH:MM:SS')."""

    def __init__(self, periodes):
        self.intervalles = intervalles(periodes)
        self._debuts = [d for d, _f in self.intervalles]
        self._fins = [f for _d, f in self.intervalles]

    def __bool__(self):
        return bool(self.intervalles)

    def __contains__(self, quand):
        if not self.intervalles:
            return True
        d = _en_date(quand)
        if d is None:
            return False
        k = bisect_right(self._debuts, d) - 1
        return k >= 0 and d <= self._fins[k]

    def masque(self, dates):
        import numpy as np
        jours = np.asarray(dates).astype('datetime64[D]')
        if not self.intervalles:
            return np.ones(jours.shape, bool)
        debuts = np.array(self._debuts, dtype='datetime64[D]')
        fins = np.array(self._fins, dtype='datetime64[D]')
        k = np.searchsorted(debuts, jours, side='right') - 1
        return (k >= 0) & ~np.isnat(jours) & (jours <= fins[np.maximum(k, 0)])

    def sql(self, colonne):
        if not self.intervalles:
            return '1', []
        params = []
        for d, f in self.intervalles:
            params += [d.isoformat(), (f + _UN_JOUR).isoformat()]
        return '(' + ' OR '.join([f'({colonne} >= ? AND {colonne} < ?)'] * len(self.intervalles)) + ')', params


def contient(periodes, quand):
    """La date `quand` est-elle dans l'une des périodes ? (Sans période : oui, pas de filtre.)"""
    return quand in FiltreDates(periodes)
