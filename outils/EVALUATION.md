# Évaluation de la recherche : gabarit et seuils de chaque moteur

Mesures du 23/11/2026, faites avec `outils/evaluation.py` sur un jeu d'images **publiques** (aucune photo
personnelle). Les réglages retenus sont dans `myphotos/recherche.py`, table `REGLAGES`.

## En bref

| Moteur | Gabarit | `seuil_sur` | `seuil_peut_etre` | `minimum` |
|---|---|---:|---:|---:|
| rapide | `{q}` | 0,0005 | 0,0001 | 0 |
| base | `{q}` | 0,0008 | 0,0002 | 0 |
| precis | `{q}` | 0,0005 | 0,00015 | 0 |
| tres_precis | `{q}` | 0,0006 | 0,00015 | 0 |

- **Gabarit** : la requête telle quelle (`{q}`) est la meilleure formulation en français pour les quatre moteurs.
  « une photo de {q} » fait perdre 4 à 5 points de précision moyenne (AP), « photo de {q} » 2 à 5.
- **Résultats sûrs** : 93 à 94 % de bonnes photos, qui représentent 68 à 75 % de toutes les bonnes photos.
- **Avec les « moins sûres »** : 84 à 90 % des bonnes photos sont montrées.
- **Repli `minimum`** : il n'apporte rien (voir plus bas), il est désactivé partout.
- Les seuils sont **très bas** parce que le modèle est très prudent : une bonne photo a une probabilité médiane
  de 0,001 à 0,003 seulement. L'ancien réglage (`SEUIL_SUR = 0,05`) n'aurait gardé que 3 à 11 % des bonnes photos.
- Sur ce jeu, les deux gros moteurs ne trient pas mieux que `base` (même AP, à 0,01 près).

## Le jeu d'essai

- **308 images de Wikimedia Commons**, choisies par leurs données structurées « dépeint » (propriété P180 de
  Wikidata) : 24 catégories simples de 12 images (chien, chat, plage, mer, montagne, neige, coucher de soleil,
  voiture, vélo, fleur, rose, champignon, gâteau, pizza, basket-ball, bateau, cheval, enfant, sapin de Noël,
  outil, capture d'écran, chantier, mème, rue) et 2 requêtes de plusieurs mots de 10 images (« gâteau
  d'anniversaire », « chien sur la plage »).
- Vignettes de 960 px de large (330 px pour 11 petits fichiers dont l'original était refusé par le serveur).
- Sélection : vraies photos (pas de peinture, gravure, jouet, scan…), prises après 1990, sujet principal
  d'abord, au plus deux images par auteur et une par série.
- Licences libres : CC BY-SA (226), CC BY (36), CC0 ou domaine public (35), Art Libre (8), autres (3). Titre,
  auteur, licence et adresse de chaque image sont dans `outils/evaluation_jeu.json` ; les images elles-mêmes ne
  sont pas dans le dépôt, l'outil les télécharge.
- **Étiquettes** : une image compte pour toutes les catégories qu'elle « dépeint » (une plage qui dépeint aussi la
  mer compte pour les deux, une rose est une fleur).
- **16 requêtes « absentes »**, sans aucune réponse dans le jeu : girafe, astronaute, dinosaure, volcan en
  éruption, crocodile, chameau, fusée, gorille, char d'assaut, igloo, éléphant, pingouin, zèbre, guitare, piano,
  tigre. Tout ce qu'elles remontent est faux. Trois candidates ont été écartées parce que le jeu les contenait
  (un kangourou, un hélicoptère de sauvetage en mer, une photo sous-marine).

### Étiquettes requalifiées

Les étiquettes P180 sont souvent incomplètes. Les fausses images que les quatre moteurs classent le plus haut, et
les bonnes qu'ils classent le plus bas, ont été regardées une à une ; seuls les cas manifestes ont été corrigés.
La liste complète, avec le motif de chaque correction, est dans `evaluation.py` (`REQUALIFICATIONS`, `EXCLUS`) et
l'outil l'affiche à chaque lancement.

- **38 images complétées (43 étiquettes)** : la mer sur 14 photos (plages, couchers de soleil, chiens au bord de
  l'eau, un bateau au large) ; « bateau » sur 6 (paquebots, bac, épave, barques) ; montagne ou neige sur 5 scènes
  enneigées ; « coucher de soleil » sur 3 photos de soleil posé sur l'horizon (un lever de soleil ne se distingue
  pas d'un coucher) ; « plage » sur 3 photos de côte ; rue ou voiture sur 5 scènes de rue ; « enfant » sur 2
  photos ; un chat dans un mème ; un chiot dans les bras d'une fillette ; « chien sur la plage » sur un chien au
  bord des vagues.
- **7 images corrigées dans l'autre sens** : une locomotive étiquetée « montagne », un portrait d'entraîneur étiqueté
  « basket-ball », un kangourou et son petit étiquetés « enfant », une façade sans rose, une réception sans
  gâteau visible, un chien de quelques pixels, une page de livre numérisée étiquetée « capture d'écran ».
- **2 images écartées** : un dessin au trait et une gravure de 1568, passés à travers le filtre « vraie photo ».

Il reste donc **306 images**.

## Mesures

- **AP** (précision moyenne) : qualité du classement d'une requête, 1 = toutes les bonnes photos avant toutes
  les autres. **P@10** : part de bonnes photos parmi les 10 premières.
- **Précision** et **rappel** à un seuil : part de bonnes photos parmi celles qui passent le seuil, part des
  bonnes photos qui le passent. Moyennes sur les 24 catégories simples.
- **Fausses /1000** : photos sans l'objet qui passent le seuil, pour 1 000 photos sans l'objet. Contrairement à
  la précision, ce taux ne dépend pas de la proportion de photos qui contiennent l'objet.
- Dans ce jeu, chaque objet est présent sur 4 à 8 % des photos ; dans une vraie collection, c'est souvent moins.
  À seuil égal, la précision y est donc plus basse : c'est pourquoi les seuils retenus sont un peu au-dessus du
  point où la précision du jeu atteint tout juste 90 %.

## Résultats

### Gabarits

AP moyenne / P@10 moyenne / rappel moyen au plus bas seuil qui garde 90 % de précision.

| Moteur | `{q}` | `une photo de {q}` | `photo de {q}` | `a photo of a {en}` (anglais, comparaison) |
|---|---|---|---|---|
| rapide | **0,90** / 0,91 / 77 % | 0,85 / 0,85 / 28 % | 0,88 / 0,89 / 64 % | 0,91 / 0,91 / 76 % |
| base | **0,92** / 0,94 / 77 % | 0,87 / 0,91 / 55 % | 0,89 / 0,90 / 69 % | 0,93 / 0,93 / 75 % |
| precis | **0,91** / 0,92 / 69 % | 0,86 / 0,87 / 25 % | 0,86 / 0,87 / 36 % | 0,93 / 0,93 / 77 % |
| tres_precis | **0,92** / 0,93 / 68 % | 0,88 / 0,89 / 58 % | 0,88 / 0,89 / 61 % | 0,93 / 0,94 / 83 % |

La requête française nue fait presque aussi bien que la phrase anglaise classique ; les phrases françaises
« une photo de … » font moins bien partout, au classement comme au seuil. Quand un gabarit contient « de {q} »,
l'application élide devant une voyelle (« une photo d'enfant »), comme l'évaluation.

### Probabilités (`modele.probabilite`, gabarit `{q}`)

| Moteur | bonnes : 10 % | médiane | 90 % | fausses : médiane | 99 % |
|---|---:|---:|---:|---:|---:|
| rapide | 0,000059 | 0,0013 | 0,020 | 0,00000043 | 0,00014 |
| base | 0,00018 | 0,0027 | 0,037 | 0,00000074 | 0,00028 |
| precis | 0,000064 | 0,00099 | 0,029 | 0,00000021 | 0,00018 |
| tres_precis | 0,000061 | 0,0015 | 0,042 | 0,00000027 | 0,00018 |

Bonnes et fausses images sont bien séparées (un facteur 3 000 à 5 500 entre les médianes), mais à des niveaux
très bas : un seuil « intuitif » de 5 ou 10 % ne garderait presque rien.

### Seuils retenus

| Moteur | sûres : précision | rappel | fausses /1000 | + moins sûres : précision | rappel | fausses /1000 | requêtes absentes : sûres / moins sûres pour 1 000 photos |
|---|---:|---:|---:|---:|---:|---:|---:|
| rapide | 93 % | 70 % | 2,7 | 79 % | 87 % | 12,8 | 1,6 / 5,3 |
| base | 94 % | 75 % | 3,2 | 82 % | 90 % | 13,3 | 0,8 / 2,9 |
| precis | 93 % | 68 % | 3,3 | 84 % | 84 % | 11,5 | 1,2 / 2,5 |
| tres_precis | 93 % | 70 % | 4,0 | 85 % | 85 % | 12,3 | 0,6 / 2,0 |

Règles suivies :

- **`seuil_sur`** : à peine au-dessus du point où la précision moyenne et la précision globale franchissent
  90 %, là où la précision moyenne atteint environ 93 % pour quelques points de rappel en moins ; les requêtes
  absentes y donnent au plus 1 à 2 fausses photos « sûres » pour 1 000 photos. Par exemple pour `base` : 0,0006
  donnait 91 % de précision, 78 % de rappel et 1,2 fausse photo sûre pour 1 000 ; 0,0008 donne 94 %, 75 % et 0,8.
- **`seuil_peut_etre`** : le plus bas seuil qui laisse passer au plus une quinzaine de photos sans rapport pour
  1 000. Plus bas, on gagne peu de bonnes photos pour beaucoup de bruit (95 % de rappel coûterait 35 à 60
  fausses pour 1 000).

### Par catégorie (AP / part des bonnes photos parmi les « sûres »)

| Catégorie | rapide | base | precis | tres_precis |
|---|---|---|---|---|
| chien | 0,97 / 82 % | 0,98 / 77 % | 0,98 / 77 % | 0,98 / 82 % |
| chat | 0,98 / 100 % | 1,00 / 100 % | 0,97 / 86 % | 1,00 / 100 % |
| plage | 0,86 / 27 % | 0,88 / 58 % | 0,93 / 31 % | 0,91 / 23 % |
| mer | 0,89 / 50 % | 0,92 / 62 % | 0,88 / 50 % | 0,87 / 38 % |
| montagne | 0,85 / 45 % | 0,91 / 70 % | 0,77 / 35 % | 0,74 / 60 % |
| neige | 0,86 / 41 % | 0,85 / 35 % | 0,85 / 24 % | 0,94 / 24 % |
| coucher de soleil | 0,94 / 62 % | 0,96 / 69 % | 0,94 / 38 % | 0,92 / 31 % |
| voiture | 0,96 / 62 % | 0,98 / 62 % | 0,97 / 69 % | 0,99 / 69 % |
| vélo | 0,99 / 100 % | 1,00 / 92 % | 0,98 / 83 % | 0,97 / 92 % |
| fleur | 0,95 / 57 % | 0,92 / 65 % | 0,92 / 39 % | 0,89 / 48 % |
| rose | 0,91 / 82 % | 0,91 / 82 % | 0,90 / 73 % | 0,91 / 82 % |
| champignon | 1,00 / 100 % | 1,00 / 100 % | 1,00 / 100 % | 1,00 / 100 % |
| gâteau | 1,00 / 71 % | 1,00 / 86 % | 1,00 / 76 % | 1,00 / 81 % |
| pizza | 1,00 / 100 % | 1,00 / 100 % | 1,00 / 100 % | 1,00 / 100 % |
| basket-ball | 0,92 / 73 % | 1,00 / 73 % | 1,00 / 91 % | 0,97 / 82 % |
| bateau | 0,97 / 74 % | 0,98 / 84 % | 0,97 / 84 % | 0,97 / 84 % |
| cheval | 0,95 / 92 % | 0,96 / 100 % | 1,00 / 100 % | 1,00 / 100 % |
| enfant | 0,86 / 31 % | 0,94 / 31 % | 0,94 / 62 % | 0,95 / 62 % |
| sapin de Noël | 0,98 / 92 % | 0,95 / 92 % | 1,00 / 92 % | 0,99 / 92 % |
| outil | 0,76 / 30 % | 0,82 / 50 % | 0,85 / 40 % | 0,80 / 20 % |
| capture d'écran | 0,78 / 73 % | 0,77 / 82 % | 0,81 / 82 % | 0,82 / 73 % |
| chantier | 0,86 / 92 % | 0,90 / 92 % | 0,88 / 75 % | 0,99 / 92 % |
| mème | 0,55 / 50 % | 0,59 / 50 % | 0,50 / 50 % | 0,69 / 50 % |
| rue | 0,92 / 87 % | 0,94 / 93 % | 0,84 / 87 % | 0,87 / 93 % |

Les objets (animaux, plats, véhicules) sont très bien trouvés. Les paysages (plage, mer, neige, montagne) sont
bien classés mais ont des probabilités plus basses : une bonne partie de leurs photos tombe dans les « moins
sûres ». Les notions vagues (outil, mème, capture d'écran) sont les plus faibles.

### Robustesse de la requête

Écart d'AP par rapport à la requête de référence (même gabarit `{q}`), de `rapide` à `tres_precis` :

- **majuscules** (« Chien », « CHIEN ») : identique, le modèle met tout en minuscules ;
- **pluriel** (21 cas : « chiens », « chevaux »…) : de -0,01 à 0,00 ;
- **sans accents** (« gateau », « velo »…) : de +0,02 à +0,06 ;
- **synonymes** (« auto », « bicyclette », « travaux »…) : de -0,05 à -0,07 en moyenne ; « bricolage » pour
  outil perd de 0,23 à 0,28, « auto » et « bicyclette » ne perdent rien ;
- **deux mots** (« bord de mer », « paysage enneigé », « match de basket ») : de -0,05 à -0,09 ;
- **« basket » seul** au lieu de « basket-ball » : de -0,18 à -0,34 (en français, des baskets sont aussi des
  chaussures) ; « screenshot » au lieu de « capture d'écran » : de +0,07 à +0,19.

### Requêtes de plusieurs mots

| Images visées | Requête | AP base | AP tres_precis |
|---|---|---:|---:|
| chien sur la plage (12) | « chien sur la plage » | **1,00** | **1,00** |
| | « chien » | 0,43 | 0,43 |
| | « plage » | 0,28 | 0,34 |
| gâteau d'anniversaire (10) | « gâteau d'anniversaire » | **0,85** | **0,82** |
| | « gâteau » | 0,72 | 0,72 |

Une phrase précise trouve exactement ce qu'elle décrit : les 12 photos de chiens sur une plage sont toutes
« sûres » avec « chien sur la plage » (0 ou 1 autre photo).

### Requêtes absentes

Fausses photos « sûres » remontées par les 16 requêtes sans réponse : 8 pour `rapide` (6 requêtes), 4 pour
`base` (3 requêtes), 6 pour `precis` (4 requêtes), 3 pour `tres_precis` (2 requêtes). Ce sont presque toujours des
voisins proches : « tigre » trouve un chat tigré, « igloo » un refuge de montagne sous la neige, « char
d'assaut » des engins ou des véhicules ; seul `rapide` confond aussi girafe, zèbre et chameau avec des chiens sur
une plage.

### Repli `minimum` (MINIMUM_RESULTATS)

Le repli montrerait les k meilleures photos quand moins de k atteignent `seuil_peut_etre`, éventuellement à
condition qu'elles « se détachent » de la masse de la collection (écart à la médiane des scores, en écarts-types
robustes). Deux situations ont été simulées :

- **objet rare** : pour chaque bonne photo, une collection faite de toutes les photos sans l'objet plus cette
  seule photo ;
- **objet absent** : les 16 requêtes absentes.

| Moteur | photo rare trouvée sans repli | k = 5 sans condition | k = 5, écart ≥ 4 | k = 5, écart ≥ 5 |
|---|---:|---|---|---|
| rapide | 86 % | 90 % (+500 fausses) | 86 % (+31 fausses) | 86 % (0 fausse) |
| base | 90 % | 94 % (+631 fausses) | 90 % (+64 fausses) | 90 % (+21 fausses) |
| precis | 82 % | 90 % (+808 fausses) | 83 % (+64 fausses) | 82 % (0 fausse) |
| tres_precis | 84 % | 90 % (+812 fausses) | 84 % (+26 fausses) | 84 % (0 fausse) |

Sur les requêtes absentes, k = 5 sans condition ajoute 56 à 67 photos fausses (14 ou 15 requêtes sur 16) ; avec
un écart ≥ 5, aucune. Une bonne photo restée sous `seuil_peut_etre` ne se détache presque jamais nettement : quand
elle se détache, sa probabilité dépasse déjà le seuil. Le repli conditionnel ne repêche donc rien, et le repli sans
condition noie les résultats. **`minimum = 0` pour les quatre moteurs** ; le mécanisme (`minimum`,
`ecart_minimum`) reste disponible dans `REGLAGES`.

## Limites

- **Petit jeu** : 24 requêtes et 306 images. Une différence de 0,01 ou 0,02 d'AP n'est pas significative, et les
  seuils sont des ordres de grandeur, pas des valeurs au millième près.
- **Photos de Commons, pas de famille** : sujets nets et bien cadrés, peu de portraits, de groupes, d'intérieurs ou
  de photos floues. Le taux de fausses détections d'une vraie collection peut être différent. L'outil sait le
  vérifier sur une collection réelle sans rien montrer ni enregistrer, en ne comptant que des nombres
  (`--collection`).
- **Proportion** : dans une collection où l'objet est rare, la précision des « sûres » sera plus basse que les
  93-94 % mesurés ici (voir « fausses /1000 »).
- **Étiquettes** : les corrections reposent sur un examen visuel ; seuls les cas manifestes ont été corrigés, il
  en reste sûrement d'autres, plus discutables.
- **Notions vagues ou ambiguës** : « mème », « outil », « capture d'écran », ou des mots à double sens
  (« basket », « rose ») donnent des résultats nettement moins bons.
- Seule la vision fp16 a été évaluée (la fp32 donne les mêmes vecteurs à 0,99999 près).

## Refaire l'évaluation

```
python -I outils/evaluation.py --dossier D:\chemin\eval                  # les quatre moteurs, réglages en service
python -I outils/evaluation.py --dossier D:\chemin\eval --suggestions    # seuils proposés automatiquement
python -I outils/evaluation.py --dossier D:\chemin\eval --planches       # planches-contact pour vérifier
python -I outils/evaluation.py --dossier D:\chemin\eval --moteurs base   # un seul moteur
```

Le premier lancement télécharge les 308 vignettes (environ 65 Mo), une requête à la fois. Le serveur d'images de
Wikimedia limite vite le débit (HTTP 429) : l'outil attend ce qu'il demande, ce qui peut prendre plusieurs
minutes. Ensuite, l'évaluation des quatre moteurs prend environ une minute sur une carte graphique. Le détail de
chaque moteur va dans `rapport_<moteur>.txt`, les nombres dans `resultats.json`.
