# Doublons : méthode, seuils et mesures

Moteur de détection des doublons de MyPhotos (`myphotos/doublons.py`), utilisé par l'écran « Doublons » de la page
(calcul en arrière-plan dans le serveur, sans le moteur de reconnaissance : voir `docs/ARCHITECTURE.md`). Seuils réglés le 24/11/2026
avec `outils/calibrer_doublons.py`, sur des images **publiques** uniquement : aucune photo personnelle n'a été utilisée
ni regardée. Moteur de vecteurs mesuré : `base` (celui par défaut).

## En bref

| Niveau | Ce qu'on y trouve | Proposées à la suppression | Précision mesurée | Rappel mesuré |
|---|---|---|---:|---:|
| `identiques` | fichiers identiques octet par octet | toutes sauf une | 100 % (SHA-256) | 100 % (les 111 copies, et 3 doublons déjà dans le jeu public) |
| `retouchees` | même photo réduite, recompressée, recadrée, redressée, convertie, sans EXIF, avec bordure, éclaircie ou assombrie | toutes sauf une | 100 % (0 photo différente sur 1,44 million de paires) | 99,6 % (4 098 / 4 114) |
| `proches` | rafales, prises du même instant | **aucune** | 99,8 % (rafales) | 93 % des paires d'une rafale dans la même série |

- **Deux photos différentes n'ont jamais été réunies** dans « identiques » ou « retouchees » : 0 sur 1 437 228 paires
  de photos différentes (dont 51 454 de même catégorie : deux chiens, deux plages, deux gâteaux…). Les plus
  ressemblantes restent loin des seuils (marges plus bas).
- Une **rafale** (cadre décalé, autre exposition) n'a jamais été prise pour une retouche : 0 sur 999 paires.
- Cas plus durs, hors cahier des charges : un **texte ajouté** sur la photo est pris pour une retouche 5 fois sur 333,
  un **sujet qui a bougé** 1 fois sur 333 (les autres vont dans « proches »).
- Durées, sur une photothèque synthétique riche en rafales : **10 000 photos, 41 s la première fois puis 1,2 s ;
  50 000 photos, 4 min 13 s puis 15 s**. La première fois, presque tout le temps va à la vérification des paires qui
  se ressemblent (~4,7 ms chacune), gardée pour les fois suivantes.

## Ce que fait le moteur

1. **Empreinte** de chaque miniature (celles que l'analyse a déjà faites : les photos ne sont pas relues) : pHash sur
   64 bits (transformée en cosinus discrète d'une version 32 × 32 en gris), et écart type des gris. Gardée dans
   `index.db` (table `doublons_empreintes`), refaite seulement si la taille ou la date du fichier changent.
2. **Identiques** : seules les photos de **même taille en octets** (et dont les miniatures se ressemblent, quand elles
   en ont) sont lues, pour leur SHA-256, gardé lui aussi. Fonctionne aussi pour les photos pas encore analysées.
3. **Paires candidates**, par produits de matrices par blocs (aucune boucle sur les paires) :
   - empreintes à au plus 12 bits l'une de l'autre (le produit de deux empreintes écrites en ±1 vaut 64 − 2 × leur
     distance de Hamming) ;
   - ou vecteurs du moteur voisins (cosinus ≥ 0,86), **et** empreintes à au plus 26 bits, **et** dates de prise de
     vue compatibles (moins de 30 min d'écart, ou date inconnue) ; au plus les 12 plus ressemblantes de chaque photo.
4. **Vérification** de chaque candidate sur les deux miniatures réduites à 128 px : recherche grossière (32 px, sept
   échelles, dans les deux sens), puis Lucas-Kanade sur trois niveaux (échelle, légère rotation, décalage, gain et
   biais de lumière). Mesures : débord (l'une tient-elle dans l'autre ?), corrélation des gris, puis, bloc par bloc
   (8 × 8) et lumière corrigée (correspondance des quantiles) : écart rapporté au **relief local** et part de pixels
   **nettement différents**. Un contour adouci par une réduction compte peu ; un trait nouveau sur une surface lisse
   (texte, sujet déplacé) compte beaucoup. Chaque vérification est gardée (table `doublons_paires`) ; deux photos
   déjà réunies comme retouches ne sont plus comparées.
5. **Regroupement** : identiques d'abord ; puis, leurs copies écartées, retouchées (de proche en proche) ; puis, les
   retouches écartées, proches, en séries autour de la meilleure photo (chaque photo d'une série ressemble
   directement à la meilleure : pas de chaîne de proche en proche).

**Photo à garder** : la plus grande définition ; à définition égale, pas une copie évidente (« copie », « copy »,
« WhatsApp », « Envoyé », « (1) » dans le nom ou le dossier) ; puis le plus gros fichier ; puis la prise de vue la plus
ancienne ; puis la première trouvée. Les autres sont proposées à la suppression (`garder: false`), sauf dans
« proches », où aucune ne l'est. Une photo proposée n'apparaît que dans un seul groupe.

Photos laissées de côté : en erreur (illisibles, trop petites), dans un dossier injoignable (disque débranché : la
copie gardée serait peut-être inaccessible), et les images presque unies (écart type des gris < 6 sur 255 : photos
noires, ciel uni), comparées seulement octet par octet.

## Le jeu d'essai

- **340 images publiques** de Wikimedia Commons (les 308 du jeu de `outils/evaluation.py`, 26 catégories, et 32 autres
  qui servent aux essais de l'interface), soit **333 origines** : deux fichiers qui portent le même titre Commons, ou
  qui sont identiques, sont la même photo.
- **Photothèque « retouches »** (4 447 photos) : chaque origine avec une date EXIF (la même pour toutes : la date ne
  peut rien départager), une copie exacte sur trois (« - Copie »), et 12 retouches : envoi WhatsApp (1 600 px au plus,
  qualité 70, sans EXIF, dans un dossier « WhatsApp Images »), réduction de moitié, recompression (qualités 60 et 75),
  recadrages de 3 et 10 % (décentrés), PNG, EXIF retiré (pixels identiques), bordure de 4 % (blanche ou noire),
  luminosité +10 % et −10 %, rotation de 2° redressée (recadrée sans coins vides).
- **Photothèque « rafales »** (1 665 photos) : par origine, trois cadres décalés de 3 à 6 % (largeur ou hauteur), dont
  deux avec une autre exposition (+12 à 15 %, −12 à 14 %), plus deux cas durs : un sujet qui a bougé (un morceau de
  22 × 28 % de l'image déplacé de 5 %) et un texte ajouté (légende blanche cerclée de noir, 8 % de la hauteur).
- **Photos différentes** : toutes les paires d'origines différentes des deux photothèques (1 437 228), dont celles de
  même catégorie (51 454), les plus difficiles.
- Analyse par le vrai moteur (`base`, sur le processeur), comme dans MyPhotos ; vecteurs et miniatures identiques à
  ceux d'une vraie photothèque.

## Résultats

### Retouches

Rappel paire par paire (l'original contre la retouche, une seule vérification), puis dans le calcul complet (la
retouche est-elle dans le même groupe que l'original, directement ou par une autre retouche ?) :

| Retouche | Hamming (min / méd. / max) | Cosinus (min / médiane) | Paire par paire | Calcul complet |
|---|---|---|---:|---:|
| copie exacte | 0 / 0 / 0 | 1,000 / 1,000 | 111 / 111 | 111 / 111 (identiques) |
| envoi WhatsApp | 0 / 0 / 12 | 0,939 / 0,964 | 333 / 333 | 333 / 333 |
| réduite de moitié | 0 / 0 / 10 | 0,883 / 0,919 | 333 / 333 | 333 / 333 |
| qualité 60 | 0 / 0 / 2 | 0,922 / 0,981 | 333 / 333 | 333 / 333 |
| qualité 75 | 0 / 0 / 2 | 0,958 / 0,988 | 333 / 333 | 333 / 333 |
| PNG | 0 / 0 / 4 | 0,996 / 0,999 | 333 / 333 | 333 / 333 |
| EXIF retiré | 0 / 0 / 2 | 0,981 / 1,000 | 333 / 333 | 333 / 333 |
| luminosité −10 % | 0 / 0 / 4 | 0,968 / 0,992 | 333 / 333 | 333 / 333 |
| luminosité +10 % | 0 / 0 / 6 | 0,961 / 0,991 | 312 / 333 | 329 / 333 |
| recadrage 3 % | 0 / 4 / 26 | 0,896 / 0,962 | 329 / 333 | 331 / 333 |
| recadrage 10 % | 4 / 14 / 26 | 0,843 / 0,950 | 329 / 333 | 331 / 333 |
| bordure 4 % | 4 / 14 / 26 | 0,847 / 0,933 | 324 / 333 | 330 / 333 |
| rotation 2° | 2 / 6 / 20 | 0,896 / 0,957 | 328 / 333 | 328 / 333 |

- Précision du calcul complet : 333 groupes « retouchees », **25 850 paires, toutes justes**, aucun groupe mélangé.
- Les retouches manquées (16 sur 4 114) ne sont pas perdues pour autant : 12 se retrouvent dans « proches ».
- Luminosité +10 % : là où le ciel sature (blanc pur), le relief disparaît et quelques pixels paraissent « nettement
  différents ». Relever la tolérance rattraperait ces cas, mais laisserait passer 3 à 5 fois plus de textes ajoutés :
  choix prudent.
- Photo gardée : l'original dans les 114 groupes d'identiques (la copie « - Copie » est proposée). Pour les retouches,
  la règle (définition, puis taille du fichier) garde souvent la version avec bordure (plus de pixels) ou le PNG (plus
  lourd) : c'est la règle demandée, voir « Limites ».

### Rafales et cas durs

| Paires | Prises pour une retouche | Superposables (vérification seule) | Calcul complet : même série « proches » |
|---|---:|---:|---:|
| cadres décalés (rafale) | 0 / 999 | 990 / 999 | 927 / 999 |
| sujet qui a bougé | 1 / 333 | 296 / 333 | 894 / 999 (toutes les paires de la scène) |
| texte ajouté | 5 / 333 | 305 / 333 | 590 / 999 (le texte change le sens : vecteurs moins voisins) |

- Précision des séries « proches » : **2 581 paires justes sur 2 586** (99,8 %) ; un seul groupe mélangé (deux photos
  de gâteaux de la même série Commons, presque identiques).
- Dans la photothèque « retouches », 11 séries « proches » dont 2 mélangent deux photos différentes (deux gâteaux, deux
  captures d'écran du même logiciel) : rien n'y est proposé à la suppression.
- Une série réunit la meilleure photo et ses voisines directes : quand les cinq photos d'une scène ne se ressemblent
  pas toutes deux à deux, la scène peut faire deux séries (d'où 927 / 999).

### Photos différentes

- 1 437 228 paires, dont 3 866 assez voisines pour être vérifiées : **0 prise pour une retouche, 0 superposable**.
- Les plus trompeuses, quand l'une semble tenir dans l'autre (187 paires) : corrélation 0,881 au plus (seuil des
  retouches 0,90), écart rapporté au relief 1,65 au moins (seuil 0,6), part de pixels nettement différents 0,65 au moins
  (seuil 0,15), écart moyen 29,7 au moins (garde-fou 30). À 95 %, les vraies retouches restent sous 0,41 (relief) et
  0,10 (pixels différents), sauf la luminosité +10 % (0,52 et 0,21 : ciel saturé).
- Cosinus : 99,9 % des paires de photos différentes sous 0,86 ; maximum 0,964 (deux gâteaux). Distance des empreintes :
  1 % sous 22 bits, minimum 8.

### Proches par les vecteurs seuls

Rafales (999 paires) contre toutes les paires de photos différentes, sans l'aide des dates :

| Cosinus ≥ | Rappel | Photos différentes réunies | Précision |
|---:|---:|---:|---:|
| 0,90 | 99,5 % | 327 | 75 % |
| 0,93 | 98,5 % | 79 | 93 % |
| 0,95 | 96,2 % | 25 | 97,5 % |
| **0,96** | 92,4 % | 6 | 99,4 % |
| 0,97 | 83,7 % | 0 | 100 % |

Les vecteurs seuls ne suffisent pas : deux photos de gâteaux différents vont jusqu'à 0,96. D'où le choix : « proches »
surtout par la **superposition des miniatures** (990 / 999 rafales, 0 photo différente), et par les vecteurs seuls à
partir de 0,96. Dans une vraie photothèque, la fenêtre de 30 min sur les dates de prise de vue écarte en plus deux
photos semblables de jours différents.

## Choix des seuils

| Réglage | Valeur | Pourquoi |
|---|---:|---|
| `HAMMING_CANDIDAT` | 12 bits | quasi-identiques toujours vérifiés ; 15 paires de photos différentes sur 1,44 million |
| `COSINUS_CANDIDAT` | 0,86 | avec `HAMMING_RETOUCHE_MAX` : laisse 1 bordure, 1 recadrage, 4 rafales et 1 texte sur 5 779 paires vraies ; 244 paires de photos différentes à vérifier au lieu de 924 |
| `HAMMING_RETOUCHE_MAX` | 26 bits | aucune retouche mesurée au-delà ; écarte 70 % des photos différentes voisines par les vecteurs |
| `VOISINES_MAX` | 12 | borne le travail dans les longues séries (100 photos : 1 200 vérifications au lieu de 4 950) |
| `FENETRE_PROCHES_S` | 30 min | non mesuré (pas de dates dans le jeu public) : choix prudent |
| `NCC_GROSSIER_MIN` | 0,65 | sortie rapide ; vraies retouches ≥ 0,72, rafales superposables ≥ 0,63 (une sur 1 600) |
| `DEBORD_MAX` | 1,5 % | une rafale décalée de 3 % ou plus déborde nettement |
| `NCC_MIN` | 0,90 | vraies retouches ≥ 0,94 sauf alignements ratés ; photos différentes ≤ 0,881 |
| `RELIEF_MAX` | 0,6 | vraies retouches : 0,41 à 95 % (0,52 pour +10 % de luminosité) ; photos différentes ≥ 1,65 ; textes et sujets déplacés : médiane 1,1 |
| `DIFFERENTS_MAX` | 15 % | vraies retouches : 10 % à 95 % (21 % pour +10 % de luminosité) ; photos différentes ≥ 65 % ; textes : 23 % à 5 %, sujets déplacés : 25 % à 5 % |
| `ECART_BLOC_MAX` | 30 / 255 | garde-fou (photos différentes ≥ 29,7) |
| `COUVERTURE_MIN`, `ROTATION_MAX` | 55 %, 4° | « légèrement » recadrée ou redressée |
| `NCC_PROCHE`, `RECOUVRE_PROCHE` | 0,93 sur 60 % | 990 / 999 rafales ; photos différentes ≤ 0,881 |
| `COSINUS_PROCHE` | 0,96 | 6 paires de photos différentes sur 1,44 million (sans dates) |
| `CONTRASTE_MIN` | 6 / 255 | images presque unies : leurs empreintes n'ont pas de sens |

Les autres moteurs (`rapide`, `precis`, `tres_precis`) prennent les valeurs de repli (`COSINUS_CANDIDAT_REPLI` 0,86,
`COSINUS_PROCHE_REPLI` 0,97, plus prudent) : leurs cosinus n'ont pas été mesurés. La vérification sur les miniatures,
qui décide seule des retouches, ne dépend pas du moteur.

## Durées

Mesurées avec `outils/calibrer_doublons.py --perf 10000,50000`, sur un processeur de bureau à 6 cœurs (Ryzen 5
5600X), un fil de calcul (les fils n'aident pas : le verrou global de Python les ralentit). Photothèque synthétique
organisée comme celle d'une famille : des « événements » de 20 à 120 photos (même lieu, vecteurs voisins, cosinus
de 0,63 à 0,89 ; dates sur deux heures, 30 % sans date), environ 22 % des photos en rafales de 3 à 6, 3 % de retouches
(copie réduite, sans date), 1 % de copies exactes (vrais fichiers). Soit 0,77 paire à vérifier par photo.

| Photos | Premier calcul | dont empreintes | dont comparaisons | dont vérifications | Relance (rien de changé) |
|---:|---:|---:|---:|---:|---:|
| 10 000 | 41 s | 3,8 s | 0,9 s | 36 s (7 643 paires) | **1,2 s** |
| 50 000 | 4 min 13 s | 22,6 s | 12,9 s | 3 min 37 s (47 172 paires) | **15 s** |

- **Relance** : empreintes et vérifications sont relues dans `index.db` ; il reste les produits de matrices (vecteurs
  et empreintes de toutes les paires : 0,8 s pour 10 000 photos, 12,6 s pour 50 000) et le regroupement.
- **Premier calcul** : ~4,7 ms par paire vérifiée (décodage des miniatures compris). Une recherche interrompue garde
  ce qui est fait (écrit par lots de 500) et repart de là ; ensuite, seules les photos nouvelles ou modifiées coûtent.
- Mémoire : les vecteurs de 50 000 photos (150 Mo en float32) et des blocs de 2 048 × 8 192 (64 Mo).

## Limites connues

- **Première recherche** : plus longue que visé (41 s pour 10 000 photos, 4 min pour 50 000, au lieu de quelques
  secondes et d'une minute) dès que la photothèque compte beaucoup de rafales et de quasi-doublons : chaque paire
  candidate demande ~4,7 ms de vérification. À lancer en arrière-plan, avec la progression ; les fois suivantes
  restent dans les objectifs (1,2 s et 15 s).
- **Textes et petits détails** : la vérification se fait à 128 px. Une légende très fine sur un fond clair, ou deux
  captures d'écran qui ne diffèrent que par quelques mots, peuvent passer pour la même photo (5 textes ajoutés sur 333).
- **Non détectés comme retouches** : rotation d'un quart de tour, image retournée (miroir), recadrage de plus d'un
  tiers, montage ou collage. Ces photos peuvent tout de même arriver dans « proches » si leurs vecteurs sont très voisins.
- **Photo gardée** : la règle demandée (définition, puis taille du fichier) garde une conversion PNG plutôt que le
  JPEG d'origine (même définition, fichier plus lourd), et la version avec bordure plutôt que l'originale. Préférer à
  taille égale de définition la photo qui a gardé sa date de prise de vue serait une amélioration simple.
- **Copies dont la date EXIF a été changée** (fuseau horaire corrigé) : trouvées seulement si leurs empreintes sont
  quasi identiques (12 bits), pas par les vecteurs.
- **Miniature périmée** : entre la modification d'un fichier et sa nouvelle analyse, son empreinte vient de l'ancienne
  miniature (elle est refaite dès l'analyse). Les identiques, eux, se fondent toujours sur le fichier.

## Refaire les mesures

```
python -I outils/calibrer_doublons.py --images C:\chemin\images --images C:\chemin\images\autres --travail C:\chemin\travail
python -I outils/calibrer_doublons.py --travail C:\chemin\travail --perf 10000,50000
```

La première commande fabrique les deux photothèques (une fois), les fait analyser par le vrai moteur (une fois :
environ 15 min sur le processeur), puis mesure tout ; les nombres vont dans `resultats_doublons.json` et toutes les
mesures paire par paire dans `mesures_base.json`. `--materiel auto` utilise la carte graphique, `--moteur` un autre
moteur, `--limite 12` fait un essai rapide sur 12 origines.
