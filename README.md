# MyPhotos

**Retrouvez vos photos en tapant ce qu'elles montrent.** Tapez « chien » : toutes vos photos de chiens apparaissent. MyPhotos fonctionne entièrement sur votre ordinateur Windows ; vos photos ne sont envoyées nulle part.

![Recherche « chien » dans MyPhotos : les photos de chiens s'affichent](docs/captures/recherche.png)

*English version below: [In English](#in-english).*

## À quoi sert MyPhotos ?

Vous avez des milliers de photos et vous ne retrouvez jamais celle du gâteau d'anniversaire ? MyPhotos regarde une fois chacune de vos photos pour reconnaître ce qu'elle montre. Ensuite, il suffit de décrire ce que vous cherchez, avec vos mots :

- « chien », « chat », « cheval » ;
- « plage », « neige », « coucher de soleil », « montagne » ;
- « gâteau d'anniversaire », « sapin de Noël », « pizza » ;
- « voiture », « vélo », « bateau » ;
- « chien sur la plage », « match de basket-ball » ;
- « capture d'écran », « chantier ».

Inutile d'avoir rangé, renommé ou étiqueté vos photos : MyPhotos comprend leur contenu, à la manière de Google Photos, mais sans rien envoyer sur Internet. Sans recherche, il affiche toutes vos photos, des plus récentes aux plus anciennes, mois par mois. Il permet aussi de cocher des photos pour les copier ou les mettre à la Corbeille, et de repérer vos photos en double ([Les doublons](#les-doublons)).

## Vos photos restent chez vous

- **Tout est local.** La reconnaissance et la recherche se font sur votre ordinateur, avec sa carte graphique ou son processeur. MyPhotos s'affiche dans votre navigateur, mais à une adresse locale (`http://127.0.0.1:7796`), inaccessible depuis Internet comme depuis les autres appareils de votre réseau.
- **Aucun compte**, aucune inscription, aucune publicité, aucune télémétrie : MyPhotos ne collecte rien et n'envoie rien.
- **Un seul téléchargement** : le moteur de reconnaissance, au premier lancement, depuis Hugging Face. C'est une version figée, dont chaque fichier est contrôlé par son empreinte SHA-256. Ensuite, MyPhotos n'a plus besoin d'Internet.
- **Vos photos ne sont jamais modifiées.** MyPhotos ne les supprime que si vous le lui demandez, après confirmation, et alors vers la **Corbeille de Windows**, d'où vous pouvez les restaurer : jamais de suppression définitive (un disque sans Corbeille, comme une clé USB ou un disque réseau, est refusé). Ce qu'il retient de vos photos (miniatures, dates, description chiffrée de chaque photo) reste dans son propre dossier, sur votre ordinateur.

À savoir : le moteur tourne grâce à ONNX Runtime et DirectML, deux bibliothèques de Microsoft. Leurs versions officielles peuvent émettre des événements de diagnostic Windows, que Windows ne transmet à Microsoft que si vous l'y avez autorisé (Paramètres > Confidentialité et sécurité > Diagnostics et commentaires). Détails dans [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Configuration requise

| | Minimum | Conseillé |
|---|---|---|
| Système | Windows 10 64 bits, version 1809 ou plus récente | Windows 11 |
| Mémoire vive | 4 Go (moteurs Rapide et Standard) | 8 Go ; 16 Go pour le moteur Précis |
| Carte graphique | facultative | compatible DirectX 12 ; 6 Go de mémoire pour le moteur Précis |
| Disque | le moteur (de 749 Mo à 2,1 Go), plus environ 35 Ko par photo | |
| Internet | une seule fois, pour télécharger le moteur | |
| Navigateur | Microsoft Edge (fourni avec Windows) ou un autre navigateur récent | |

Sans carte graphique utilisable, le processeur fait tout le travail : c'est plus lent, mais cela fonctionne.

Formats reconnus : JPEG (`.jpg`, `.jpeg`, `.jfif`), PNG, WebP, BMP, GIF et TIFF. Pas encore : HEIC (iPhone), vidéos, formats RAW. Les images de moins de 96 pixels de côté (icônes, émojis) sont ignorées.

## Installation

1. Téléchargez **MyPhotos.exe** (un seul fichier d'environ 48 Mo) depuis la page [Releases](https://github.com/glenac/MyPhotos/releases) du projet.
2. Rangez-le où vous voulez, par exemple dans vos Documents : il n'y a rien à installer.
3. Double-cliquez sur MyPhotos.exe.

**Avertissement de Windows.** Au premier lancement, Windows peut afficher « Windows a protégé votre ordinateur » (Microsoft Defender SmartScreen). Ce message apparaît pour les logiciels récents qui n'ont pas de signature numérique payante, ce qui est le cas de MyPhotos. Cliquez sur **Informations complémentaires**, puis sur **Exécuter quand même**.

## Premier lancement

MyPhotos s'ouvre dans votre navigateur et vous guide en trois étapes : **Votre ordinateur**, **Vos photos**, **Analyse**.

![Étape « Votre ordinateur » : MyPhotos décrit le PC et conseille un moteur](docs/captures/premier-lancement.png)

1. **Votre ordinateur.** MyPhotos examine votre PC (système, processeur, mémoire, carte graphique, place libre) et vous conseille un moteur de reconnaissance, marqué « Conseillé pour cet ordinateur ». Il annonce la taille du téléchargement, la durée prévue de l'analyse et ce qui fera le travail : « la carte graphique » ou « le processeur ». Cliquez sur **Télécharger** (la taille est indiquée sur le bouton), ou sur **Autres choix** pour prendre un autre moteur. Dès que le téléchargement a commencé, vous pouvez cliquer sur **Continuer** : il se poursuit pendant que vous choisissez vos photos.
2. **Vos photos.** Choisissez les dossiers où se trouvent vos photos. MyPhotos propose votre dossier **Images** (et **Images de OneDrive** s'il existe), avec le nombre approximatif de photos qu'il contient : cliquez sur **Ajouter**. Pour un autre dossier, un disque externe par exemple, cliquez sur **Ajouter un dossier…**, ou tapez son emplacement sous « Ou tapez l'emplacement du dossier ». Les dossiers retenus apparaissent sous « Dossiers choisis ». Cliquez ensuite sur **Continuer**.
3. **Analyse.** MyPhotos regarde chacune de vos photos une seule fois, en commençant par les plus récentes. Une barre de progression indique où il en est et le temps restant. Vous pouvez cliquer tout de suite sur **Commencer à chercher** : les résultats s'enrichissent au fur et à mesure.

L'analyse continue en arrière-plan. Le bandeau en haut de la page permet de la mettre en **Pause** puis de la **Reprendre**. Si vous arrêtez MyPhotos, elle reprendra où elle en était au lancement suivant.

## Chercher dans vos photos

- Tapez ce que vous cherchez dans la barre « Que cherchez-vous ? », puis appuyez sur Entrée ou cliquez sur **Chercher**. Les boutons « Essayez : » proposent des exemples. La touche `/` place le curseur dans la barre de recherche.
- Les photos **sûres** s'affichent en premier. En dessous, la rubrique **Moins sûres** regroupe les photos qui « correspondent peut-être à votre recherche ».
- Peu de résultats ? Le bouton **Voir les photos les plus proches** montre, 60 par 60, celles qui s'approchent le plus de votre recherche, dans la rubrique **Moins probables**.
- Cliquez sur une photo pour l'ouvrir dans la **visionneuse**.

![La visionneuse de MyPhotos](docs/captures/visionneuse.jpg)

Dans la visionneuse, les flèches (à l'écran, ou ← et → au clavier) passent d'une photo à l'autre. En bas s'affichent la date de la photo et son dossier. **Ouvrir avec la visionneuse Windows** ouvre la photo dans le logiciel habituel de Windows, **Afficher dans le dossier** ouvre l'Explorateur sur la photo, et **Fermer** (ou la touche Échap) ramène aux résultats. **Copier**, **Copier dans un dossier…** et **Supprimer** agissent sur la photo affichée (voir ci-dessous).

Le bouton **Réglages**, en haut à droite, regroupe :

- **Vos dossiers de photos** : ajouter un dossier (**Ajouter un dossier…**) ou en retirer un (**Retirer**). Retirer un dossier ne supprime aucune photo : MyPhotos cesse simplement de s'en occuper.
- **Analyse des photos** : MyPhotos repère seul les photos ajoutées, modifiées ou supprimées, à chaque démarrage puis toutes les heures. **Chercher les nouvelles photos maintenant** le fait sur-le-champ.
- **Moteur de reconnaissance** : le moteur utilisé, et le bouton **Changer de moteur**.
- **Votre ordinateur** : ce que MyPhotos sait de votre PC.
- **Arrêter MyPhotos**.

MyPhotos s'arrête aussi tout seul environ trois minutes après la fermeture de sa page. Pour le retrouver, double-cliquez de nouveau sur MyPhotos.exe ; s'il tourne encore, sa page s'ouvre simplement.

## Choisir, copier, supprimer

Comme dans Google Photos, vous pouvez cocher des photos pour agir sur plusieurs à la fois :

- au survol d'une miniature, un rond apparaît en haut à gauche : cliquez dessus pour cocher la photo (sur un écran tactile, faites un appui long) ;
- dès qu'une photo est cochée, un clic sur une miniature la coche ou la décoche, et Maj+clic coche toute une suite ; le rond à côté d'un mois coche tout le mois ;
- Ctrl+A coche tout ce qui est affiché, Échap annule la sélection.

Une barre remplace alors l'en-tête. Elle indique le nombre de photos cochées et propose :

- **Copier** (Ctrl+C) : les photos vont dans le presse-papiers de Windows ; collez-les avec Ctrl+V dans un dossier, un e-mail, un document ;
- **Copier dans un dossier…** : une fenêtre de Windows demande où les copier. Rien n'est jamais écrasé : une photo dont le nom est déjà pris est copiée sous le nom « photo (2).jpg ». Les dates sont gardées, et une copie interrompue ne laisse aucun fichier à moitié écrit ;
- **Supprimer** (Suppr) : après confirmation, les photos partent dans la **Corbeille de Windows**, d'où vous pouvez les restaurer tant qu'elle n'est pas vidée. MyPhotos ne supprime jamais définitivement : sur un disque sans Corbeille (clé USB, disque réseau), il refuse. Il refuse aussi une photo qui a changé depuis son analyse.

La visionneuse propose les mêmes actions pour la photo affichée.

## Les doublons

![L'écran « Doublons » : copies identiques et copies retouchées, la photo à garder et les copies cochées](docs/captures/doublons.png)

Le bouton **Doublons**, en haut à droite, ouvre l'écran des photos en double. Cliquez sur **Chercher les doublons** : MyPhotos compare vos photos entre elles, sur votre ordinateur, et vous pouvez continuer à chercher pendant ce temps. Il les range en trois rubriques :

- **Copies identiques** : le même fichier, enregistré plusieurs fois ;
- **Copies retouchées** : la même photo, réduite, recompressée (envoyée par WhatsApp…), légèrement recadrée ou redressée, convertie en PNG, éclaircie ou assombrie, ou entourée d'une bordure ;
- **Photos très proches (rafales)** : plusieurs prises du même instant. Cette rubrique est repliée et rien n'y est coché : à vous de choisir.

Chaque photo s'affiche avec sa définition, sa taille, sa date et son dossier. Dans chaque groupe, MyPhotos indique la photo **À garder** (la plus grande ; à définition égale, l'original plutôt qu'une copie évidente comme « IMG_0001 - Copie.jpg », puis le fichier le plus détaillé) et coche les autres. Vérifiez, cochez ou décochez (un clic sur une miniature l'ouvre en grand), puis cliquez sur **Supprimer les copies cochées** : le bouton indique la place qu'elles occupent. Après confirmation, elles partent dans la Corbeille de Windows ; la place est libérée quand vous la videz. MyPhotos refuse de mettre à la Corbeille toutes les photos d'un groupe de copies : il ne vous en resterait aucune.

La première recherche prend un peu de temps : environ 40 secondes pour 10 000 photos sur le PC d'essai, avec une photothèque riche en rafales. MyPhotos garde ce qu'il a déjà comparé : les recherches suivantes ne prennent que quelques secondes. Quand l'analyse ajoute des photos, ou quand vous en supprimez, la liste se met à jour d'elle-même. Les photos pas encore analysées ne sont comparées qu'à l'identique. Limites : une photo retournée en miroir, tournée d'un quart de tour ou très recadrée n'est pas reconnue comme une copie. Détails et mesures : [outils/DOUBLONS.md](outils/DOUBLONS.md).

## Le moteur de reconnaissance

MyPhotos propose quatre moteurs, tous tirés de **SigLIP 2**, un modèle de Google publié sous licence libre Apache-2.0, dans les exports ONNX d'onnx-community, en demi-précision (fp16). Un moteur plus précis trouve mieux vos photos, mais il est plus long à télécharger et à faire travailler.

| Moteur | Téléchargement¹ | Carte graphique² | Processeur² | Rappel en français³ | Pour qui ? |
|---|---:|---:|---:|---:|---|
| Rapide | 752 Mo | 310 photos/s | 26 photos/s | 80,2 % | PC ancien ou sans carte graphique |
| Standard | 749 Mo | 92 photos/s | 7 photos/s | 82,9 % | tous les PC |
| Précis | 1,7 Go | 20 photos/s | 1,5 photo/s | 85,0 % | PC récent avec une carte graphique |
| Très précis | 2,1 Go | 15 photos/s | 1,3 photo/s | 85,7 % | PC puissant avec une bonne carte graphique |

¹ Tailles affichées par MyPhotos, qui compte comme l'Explorateur de Windows (1 Go = 1 024 Mo). En millions d'octets : 789, 785, 1 798 et 2 307.

² Débit du moteur seul, mesuré sur un processeur Ryzen 5 5600X et une carte Radeon RX 6600 XT. Lors d'une première lecture, un disque dur ordinaire limite l'analyse à environ 10 photos par seconde.

³ Banc d'essai multilingue publié par le projet Immich (jeu Crossmodal-3600), recherches en français.

**Le moteur conseillé.** MyPhotos choisit selon votre matériel :

- carte graphique DirectX 12 dédiée d'au moins 6 Go et 16 Go de mémoire vive : **Précis**, si l'analyse de vos photos prend moins d'une demi-heure, sinon **Standard** ;
- autre carte graphique utilisable (petite carte, puce graphique intégrée récente) : **Standard** ;
- pas de carte graphique utilisable : **Standard** avec au moins 6 cœurs et 8 Go de mémoire vive, sinon **Rapide**.

Il vérifie ensuite que la mémoire et la place libre suffisent. **Très précis** n'est jamais conseillé d'office : il ne gagne que 0,7 point sur Précis, pour un téléchargement plus lourd. Il reste proposé sous « Autres choix ».

**La qualité des résultats.** Sur un jeu d'essai de 306 images publiques réparties en 24 catégories, 93 à 94 % des photos « sûres » sont de bonnes réponses, selon le moteur ; elles représentent 68 à 75 % de toutes les bonnes réponses. En comptant les « moins sûres », 84 à 90 % des bonnes réponses sont trouvées. Les détails sont dans [outils/EVALUATION.md](outils/EVALUATION.md).

**Changer de moteur.** Dans les Réglages, cliquez sur **Changer de moteur** : la fenêtre « Changer de moteur de reconnaissance » présente les quatre moteurs. Cliquez sur **Choisir ce moteur**, puis confirmez. MyPhotos télécharge le moteur s'il n'est pas déjà sur l'ordinateur, puis analyse de nouveau toutes vos photos. Pendant ce temps, vous pouvez continuer à chercher, mais les résultats restent incomplets jusqu'à la fin de l'analyse.

**Sans Internet.** Pour un PC hors ligne, posez à côté de MyPhotos.exe un dossier `modele` qui contient le moteur, rangé comme MyPhotos le range lui-même : le plus simple est de copier le dossier `%LOCALAPPDATA%\MyPhotos\modele` d'un PC où le moteur a déjà été téléchargé. Quand vous cliquez sur **Télécharger**, MyPhotos recopie ce dossier au lieu de passer par Internet, après avoir contrôlé la taille et l'empreinte SHA-256 de chaque fichier. Les sous-dossiers s'appellent `rapide`, `base` (Standard), `precis` et `tres_precis`.

## Questions fréquentes

### Où sont mes données ?

Dans le dossier `%LOCALAPPDATA%\MyPhotos` (copiez ce chemin dans la barre d'adresse de l'Explorateur) :

- `index.db` : vos dossiers, ce que MyPhotos sait de chaque photo (date, dimensions, position GPS si l'appareil l'a enregistrée, description chiffrée calculée par le moteur) et les empreintes qui servent à repérer les doublons ;
- `miniatures.db` : les miniatures affichées dans les résultats ;
- `modele\` : le ou les moteurs téléchargés ;
- `journal.log` : le journal de fonctionnement, utile en cas de problème.

MyPhotos n'écrit rien dans vos dossiers de photos, sauf les copies que vous lui demandez (**Copier dans un dossier…**). À chaque lancement, MyPhotos.exe se décompresse aussi dans un dossier temporaire (`%TEMP%\_MEI…`), effacé à l'arrêt.

### Comment désinstaller MyPhotos ?

1. Dans les Réglages, cliquez sur **Arrêter MyPhotos**.
2. Supprimez MyPhotos.exe.
3. Supprimez le dossier `%LOCALAPPDATA%\MyPhotos`.

MyPhotos n'installe rien d'autre : ni service, ni entrée dans le registre, ni démarrage automatique. Vos photos restent intactes.

### Mes photos sont-elles modifiées ?

Non. MyPhotos ne modifie jamais vos photos : il ne les déplace pas et ne les renomme pas. Il ne les supprime que si vous le lui demandez (**Supprimer**, ou **Supprimer les copies cochées** dans l'écran Doublons), après confirmation, et alors vers la Corbeille de Windows, jamais définitivement. Retirer un dossier de MyPhotos ne supprime aucune photo.

### J'ai supprimé une photo par erreur.

Ouvrez la Corbeille de Windows (icône du Bureau), faites un clic droit sur la photo, puis **Restaurer** : elle retrouve sa place. MyPhotos la retrouve à son prochain passage, ou tout de suite avec **Chercher les nouvelles photos maintenant**, dans les Réglages.

### Puis-je chercher avec plusieurs mots ?

Oui, et c'est souvent plus efficace : décrivez la scène, par exemple « chien sur la plage » ou « gâteau d'anniversaire ». MyPhotos comprend la phrase entière ; il n'y a pas d'opérateurs ET ou OU à connaître. Sur le jeu d'essai, « chien sur la plage » trouve les 12 photos de chiens sur une plage, toutes classées « sûres », alors que « chien » seul les mêle à toutes les autres photos de chiens. Majuscules, pluriels et accents ne changent presque rien : « Chiens » ou « gateau » fonctionnent aussi.

### Dans quelle langue taper mes recherches ?

En français de préférence : c'est la langue de référence de MyPhotos, et ses seuils ont été réglés avec des recherches en français. SigLIP 2 est un modèle multilingue : l'anglais donne des résultats comparables sur le jeu d'essai, et d'autres langues fonctionnent aussi, sans avoir été mesurées ici. Évitez les mots à double sens : « basket » seul désigne aussi des chaussures, « basket-ball » est plus sûr.

### Que veut dire « Moins sûres » ?

Pour chaque photo, le moteur estime la probabilité qu'elle corresponde à votre recherche. Au-dessus d'un premier seuil, la photo est « sûre » ; un peu en dessous, elle est rangée dans « Moins sûres ». Les deux seuils ont été réglés pour chaque moteur sur le jeu d'essai.

### Mes photos sont sur un disque externe ou sur le réseau.

Ajoutez le dossier comme les autres. Si le disque est débranché ou le dossier injoignable, MyPhotos garde ce qu'il en sait et reprend quand le dossier revient.

## Développement

Il faut Windows 64 bits et Python 3.14.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m myphotos
```

`python -m myphotos` (ou `python lancer.py`, le point d'entrée de l'exécutable) lance le serveur local puis ouvre la page. Depuis les sources, les données vont dans `donnees\`, ignoré par git ; MyPhotos.exe les range dans `%LOCALAPPDATA%\MyPhotos`.

| Variable d'environnement | Effet |
|---|---|
| `MYPHOTOS_DOSSIER` | dossier des données (index, miniatures, journal) |
| `MYPHOTOS_MODELES` | dossier des moteurs, pour réutiliser un moteur déjà téléchargé |
| `MYPHOTOS_PORT` | port local (7796 par défaut) |
| `MYPHOTOS_SANS_PAGE=1` | n'ouvre pas le navigateur |
| `MYPHOTOS_INACTIVITE_S` | délai d'arrêt automatique sans page ouverte (180 s par défaut) |
| `MYPHOTOS_TELECHARGEMENT_FACTICE=1` | simule le téléchargement du moteur, pour essayer l'interface |

L'API locale n'accepte les requêtes POST qu'avec l'en-tête `X-MyPhotos: 1` (protection contre les autres sites ouverts dans le navigateur). `POST /api/quitter` arrête proprement le serveur.

**Tests** (199 essais) :

```powershell
.venv\Scripts\python.exe -m unittest tests.test_coeur tests.test_materiel tests.test_serveur tests.test_verifier_publication tests.test_doublons tests.test_construction
```

`test_coeur` travaille avec un moteur factice et des images générées. `test_serveur` lance un vrai serveur sur un port d'essai et réutilise les moteurs déjà téléchargés (`donnees\modele`, ou `MYPHOTOS_MODELES`) ; son essai de bout en bout demande un dossier d'images publiques (`MYPHOTOS_ESSAI_PHOTOS`). `test_doublons` essaie la recherche des doublons sur des images générées. `test_construction` essaie, sans réseau, la préparation de la publication (`construire.py --paquet`). Le presse-papiers, la fenêtre « Choisir un dossier » et la Corbeille sont simulés ; leurs vrais appels à Windows ne sont essayés qu'à la demande (`MYPHOTOS_ESSAI_WINDOWS=1`). Aucun essai n'utilise de photo personnelle.

**Construction de MyPhotos.exe** (un seul fichier, sans console, avec PyInstaller, dans un environnement séparé) :

```powershell
python -m venv .venv-build
.venv-build\Scripts\python -m pip install -r requirements-build.txt
construction\construire.ps1
```

Le résultat est `dist\MyPhotos.exe`. `construction\construire.ps1 --essai` construit le programme d'essai (`construction/essai_exe.py`), qui vérifie la chaîne de construction après une mise à jour de Python, d'ONNX Runtime ou de PyInstaller. Une fois MyPhotos.exe essayé, `construction\construire.ps1 --paquet` prépare la publication : l'archive `MyPhotos-<version>-windows.zip` (avec le mode d'emploi et les licences des composants) et les empreintes SHA-256. Détails : [construction/LISEZMOI.md](construction/LISEZMOI.md).

**Outils** : `outils/evaluation.py` mesure la qualité de la recherche et règle les seuils (résumé dans `outils/EVALUATION.md`) ; `outils/banc_essai.py` compare la vitesse et la fidélité des variantes d'un moteur ; `outils/telecharger_tout.py` télécharge tous les fichiers d'un moteur ; `outils/calibrer_doublons.py` règle les seuils des doublons sur des images publiques (résumé dans `outils/DOUBLONS.md`) ; `outils/verifier_publication.py` vérifie qu'aucune donnée personnelle n'entre dans le dépôt.

## Architecture

Le flux : **matériel → moteur → analyse → recherche**. Au premier lancement, MyPhotos examine le PC et conseille un moteur ; il le télécharge, puis analyse les photos en arrière-plan : chaque photo devient une miniature et un vecteur de nombres. Une recherche transforme le texte en vecteur du même espace et le compare à ceux de toutes les photos. Les doublons se cherchent sur les miniatures et les vecteurs déjà en base, sans relire les photos (sauf pour comparer octet par octet deux fichiers de même taille).

| Fichier | Rôle |
|---|---|
| `page/index.html` | l'interface : une page unique, sans aucune ressource externe |
| `myphotos/serveur.py` | serveur local sur 127.0.0.1, API JSON, assistant de premier lancement, téléchargement du moteur, copie, Corbeille, recherche des doublons en arrière-plan |
| `myphotos/materiel.py` | analyse du PC et conseil du moteur |
| `myphotos/modele.py` | catalogue des moteurs, téléchargement contrôlé, calcul avec ONNX Runtime (DirectML ou processeur) |
| `myphotos/indexation.py` | inventaire des dossiers et analyse des photos en arrière-plan |
| `myphotos/base.py` | index SQLite et miniatures |
| `myphotos/recherche.py` | recherche par mots, seuils « sûres » et « moins sûres » |
| `myphotos/doublons.py` | doublons : copies identiques, copies retouchées, rafales |
| `myphotos/config.py` | version, port, emplacement des données |

Les choix techniques et leurs mesures sont expliqués dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Un mode d'emploi d'une page, à imprimer, est disponible en [PDF](docs/MyPhotos_mode_emploi.pdf).

## Crédits et licences

- **MyPhotos** : licence MIT, © 2026 glenac. Voir [LICENSE](LICENSE).
- **Moteur de reconnaissance** : SigLIP 2, © Google, licence Apache-2.0, dans les exports ONNX publiés par onnx-community sur Hugging Face. MyPhotos le télécharge depuis Hugging Face ; il n'est pas inclus dans MyPhotos.exe.
- **Composants embarqués dans MyPhotos.exe** : Python, ONNX Runtime, DirectML, NumPy, Pillow, tokenizers, Tcl/Tk et le chargeur de PyInstaller, chacun sous sa propre licence. Voir [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Choix des moteurs** : banc d'essai multilingue du projet Immich (jeu Crossmodal-3600).
- **Captures d'écran** : photos de Wikimedia Commons versées dans le domaine public (CC0). Voir [docs/captures/CREDITS.md](docs/captures/CREDITS.md).

## Feuille de route

Des idées pour les versions suivantes, sans date promise :

- **Dates et lieux** : chercher « Noël 2019 » ou « vacances en Bretagne ». MyPhotos relève déjà la date de prise de vue et la position GPS des photos pendant l'analyse.
- **Visages** : regrouper les photos d'une même personne, toujours sans rien envoyer.
- **Vidéos** : les retrouver comme les photos.
- **HEIC** : lire les photos des iPhone.

---

## In English

**Find your photos by typing what they show.** Type "dog" (or, preferably, the French « chien ») and every photo with a dog appears. MyPhotos runs entirely on your Windows PC: your photos are never uploaded anywhere. The interface is in French.

**Privacy.** Recognition and search run locally, on your graphics card or processor. The interface is a page served at `http://127.0.0.1:7796`, unreachable from the Internet or from your local network. No account, no ads, no telemetry from MyPhotos. The only download is the recognition engine, once, from Hugging Face (pinned revision, every file checked against its SHA-256 hash). Your photos are never modified. MyPhotos only deletes them when you ask it to, after confirmation, and then to the Windows Recycle Bin, never permanently (drives without a Recycle Bin, such as USB sticks or network drives, are refused). Note: ONNX Runtime and DirectML, the Microsoft libraries that run the engine, may emit Windows diagnostic events, which Windows only sends to Microsoft if your diagnostic data settings allow it (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).

**Requirements.** Windows 10 64-bit (version 1809 or later) or Windows 11; 4 GB of RAM (8 GB recommended, 16 GB for the "Précis" engine); an optional DirectX 12 graphics card; the engine (749 MB to 2.1 GB) plus about 35 KB per photo of disk space; Internet access once. Supported formats: JPEG, PNG, WebP, BMP, GIF, TIFF (no HEIC, video or RAW yet).

**Installation.** Download **MyPhotos.exe** from the [Releases](https://github.com/glenac/MyPhotos/releases) page and double-click it; there is nothing to install. If Microsoft Defender SmartScreen shows "Windows protected your PC", click **More info**, then **Run anyway** (MyPhotos is not signed with a paid certificate).

**First run.** A three-step assistant opens in your browser: « Votre ordinateur » (MyPhotos describes your PC and recommends an engine; click « Télécharger »), « Vos photos » (pick your photo folders with « Ajouter » or « Ajouter un dossier… »), and « Analyse » (each photo is analysed once; you can start searching right away with « Commencer à chercher »).

**Search.** Type what you are looking for and press Enter; "sure" results come first, then « Moins sûres » (less sure). When a search finds few results, « Voir les photos les plus proches » shows the closest photos, 60 at a time.

**Select, copy, delete.** Tick photos as in Google Photos: hover a thumbnail and click the circle (long-press on a touch screen), Shift+click for a range, Ctrl+A for everything shown, Esc to clear. « Copier » (Ctrl+C) puts the files on the Windows clipboard; « Copier dans un dossier… » copies them to a folder you pick, never overwriting anything; « Supprimer » (Del) sends them to the Recycle Bin after confirmation. The viewer offers the same actions.

**Duplicates.** The « Doublons » screen finds exact copies, edited copies (resized, recompressed by WhatsApp, slightly cropped or straightened, converted, brightened…) and bursts. Each photo is shown with its resolution, size, date and folder; MyPhotos marks the one to keep (« À garder ») and ticks the others, but ticks nothing in bursts. « Supprimer les copies cochées » sends the ticked photos to the Recycle Bin after confirmation, and never all the copies of a photo. The first run takes about 40 s for 10,000 photos on the test PC; later runs take seconds. Details: [outils/DOUBLONS.md](outils/DOUBLONS.md) (in French).

**Engines.** Four engines, all based on Google's SigLIP 2 (Apache-2.0), using onnx-community's fp16 ONNX exports:

| Engine | Download | Graphics card | Processor | French recall (Immich, XM3600) |
|---|---:|---:|---:|---:|
| Rapide (fast) | 752 MB | 310 photos/s | 26 photos/s | 80.2% |
| Standard | 749 MB | 92 photos/s | 7 photos/s | 82.9% |
| Précis (accurate) | 1.7 GB | 20 photos/s | 1.5 photos/s | 85.0% |
| Très précis (most accurate) | 2.1 GB | 15 photos/s | 1.3 photos/s | 85.7% |

Speeds were measured for the engine alone on a Ryzen 5 5600X with a Radeon RX 6600 XT; a regular hard disk limits a first pass to about 10 photos/s. On 306 public test images, 93 to 94% of the "sure" results are correct ([outils/EVALUATION.md](outils/EVALUATION.md)). You can switch engines in « Réglages » > « Changer de moteur ».

**FAQ.** Data lives in `%LOCALAPPDATA%\MyPhotos` (index, thumbnails, engine, log). To uninstall, click « Arrêter MyPhotos » in « Réglages », then delete MyPhotos.exe and that folder; nothing else is installed. Multi-word queries work well ("chien sur la plage"). French is the reference language; English gives comparable results.

**Development.** Python 3.14 on 64-bit Windows: `python -m venv .venv`, `.venv\Scripts\python -m pip install -r requirements.txt`, then `.venv\Scripts\python -m myphotos`. Tests: `.venv\Scripts\python.exe -m unittest tests.test_coeur tests.test_materiel tests.test_serveur tests.test_verifier_publication tests.test_doublons tests.test_construction`. The executable is built with `construction\construire.ps1` (see `construction/LISEZMOI.md`, in French). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (in French).

**License.** MyPhotos is released under the MIT license, © 2026 glenac. Third-party components and their licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
