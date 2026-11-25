# Construire MyPhotos.exe et son installateur

MyPhotos.exe est un seul fichier, sans console, fabriqué avec PyInstaller à partir de `lancer.py`. La version
« dossier » (le même contenu, déjà décompressé) est posée par l'installateur, compilé avec Inno Setup 6.

## Construire

Sous Windows 64 bits, avec Python 3.14. Préparer l'environnement de construction une seule fois :

```
python -m venv .venv-build
.venv-build\Scripts\python -m pip install -r requirements-build.txt
```

Puis construire (une demi-minute) :

```
construction\construire.ps1
```

Le résultat est `dist\MyPhotos.exe`. Le script écrit le fichier `.spec` et la ressource de version dans
`build\`, lance PyInstaller, puis relit l'exécutable : fichiers indispensables présents (dont
`DirectML.dll`), `onnxruntime.dll` absente, version et copyright corrects. Au moindre écart, il s'arrête
en erreur.

- `construction\icone.py` redessine `MyPhotos.ico` et le favicon de `page\index.html`.
  `--apercu planche.png` produit une planche de toutes les tailles, agrandies, sur fond clair et sombre.
- `construction\construire.ps1 --essai` fabrique `dist\MyPhotos-essai.exe`, le programme d'essai
  (`essai_exe.py`), avec le même contenu que MyPhotos.exe. Il fait un vrai calcul sur la carte graphique
  et sur le processeur, vérifie d'où viennent les DLL et écrit un journal. À utiliser après une mise à
  jour de Python, d'onnxruntime ou de PyInstaller :
  `MyPhotos-essai.exe --modele <moteur>\base\onnx\vision_model_fp16.onnx --journal essai.log`
  (code de sortie 0 si tout va bien). Depuis la 0.3.0, il charge aussi la pile de la fenêtre (pythonnet,
  pywebview, WebView2), sans ouvrir de fenêtre.
- `construction\construire.ps1 --sortie <dossier>` (avec ou sans `--essai`) construit dans un autre
  dossier, exécutable et fichiers de travail, sans toucher à `dist\` ni à `build\` : pour essayer une
  construction sans déranger celle qui sera publiée.
- `construction\construire.ps1 --dossier` construit la version « dossier » de PyInstaller (onedir) :
  `dist\MyPhotos\MyPhotos.exe` et ses fichiers dans `dist\MyPhotos\_internal\` (une vingtaine de secondes,
  fichiers de travail dans `build\MyPhotos-dossier\`). Même analyse, mêmes exclusions et mêmes fichiers retirés
  que MyPhotos.exe. Le contrôle est le même, plus un : à la racine, rien d'autre que `MyPhotos.exe` et
  `_internal\` (une DLL posée à côté de l'exécutable par un hook, ou un profil WebView2, arrêteraient la
  construction). Avec `--sortie <dossier>` : `<dossier>\MyPhotos\`.
- Piloter la fenêtre de `MyPhotos.exe` par le protocole DevTools (essai réel, jamais pour l'utilisateur) : il faut
  les TROIS à la fois, `MYPHOTOS_DEBUG=1`, l'argument `--essai-debogage` sur la ligne de commande, et
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>`, par exemple
  `MyPhotos.exe --essai-debogage` lancé avec ces deux variables (et `MYPHOTOS_PORT`, `MYPHOTOS_DOSSIER` d'essai).
  Sans l'argument, l'exe ignore `MYPHOTOS_DEBUG` et retire les variables `WEBVIEW2_*` : une variable
  d'environnement persistante n'ouvre jamais de port de débogage ni les outils de développement.

## Publier

**En une commande** (depuis la 0.4.1) : `construction\publier.ps1 --essai`, puis `construction\publier.ps1`.
Il contrôle que main est propre et envoyée, que l'étiquette est libre, que les nouveautés
(`construction\nouveautes.json`) et les notes (`dist\NOTES-<version>.md`, écrites à la main avec les repères
`{SHA_INST}`, `{SHA_EXE}`, `{SHA_ZIP}`, `{SHA_JSON}`, `{TAILLE_INST}`, `{TAILLE_EXE}`, `{TAILLE_ZIP}`,
`{DATE_DEFENDER}`, `{DATE_DEFENDER_EN}`) existent et que la clé de signature correspond ; construit tout (exe,
dossier, paquet, installateur, manifeste) ; contrôle les empreintes, le manifeste et l'analyse Defender ; puis
(sans `--essai`) pose l'étiquette, crée la Release en brouillon avec ses 8 fichiers, la publie comme dernière
version et vérifie en ligne (fichiers retéléchargés, manifeste lu par MyPhotos). Le détail des étapes suit.

Une fois `dist\MyPhotos.exe` construit **et essayé**, préparer ce qui sera publié :

```
construction\construire.ps1 --paquet
```

Cette étape ne reconstruit rien : elle relit l'exécutable (contenu, version), vérifie que les versions installées
sont celles de `requirements*.txt` et de `THIRD_PARTY_NOTICES.md`, puis écrit dans `dist\` :

- `licences\` : les textes de licence que demande `THIRD_PARTY_NOTICES.md` (section « Ce que chaque publication
  doit contenir »), un sous-dossier par composant, présentés par `licences\LISEZMOI.txt` ;
- `MyPhotos-<version>-windows.zip` : `MyPhotos.exe`, `LICENSE`, `THIRD_PARTY_NOTICES.md`,
  `MyPhotos_mode_emploi.pdf` et `licences\` ;
- `MyPhotos-<version>-windows.zip.sha256` et `MyPhotos.exe.sha256`, au format de `sha256sum`.

Quelques fichiers ne sont pas dans l'environnement de construction. Ils sont téléchargés une fois, contrôlés
par leur empreinte, puis gardés dans un cache **hors du dépôt** (`--cache <dossier>`, sinon la variable
`MYPHOTOS_CACHE_CONSTRUCTION`, sinon `%LOCALAPPDATA%\MyPhotos-construction\cache`) :

- la licence de DirectML : paquet NuGet officiel `Microsoft.AI.DirectML` 1.15.4 (202 Mo), contrôlé par
  l'empreinte SHA-512 que publie nuget.org. Le script vérifie aussi que la `DirectML.dll` embarquée est celle de
  ce paquet : même version, même code (seule la signature numérique diffère) ;
- la licence de tokenizers (Apache-2.0), absente de sa roue : fichier `LICENSE` de son dépôt, au commit de
  l'étiquette v0.23.3, contrôlé par son empreinte SHA-256 ;
- les fichiers de licence des bibliothèques Rust de tokenizers qui demandent leurs propres mentions (licence MIT
  seule, BSD-2-Clause, Unicode-3.0) : pris dans leur paquet crates.io, contrôlé par l'empreinte SHA-256 de
  l'inventaire de la roue (`sboms`). Les autres sont couvertes par la licence Apache-2.0 jointe ;
- la licence des DLL WebView2 que livre pywebview (`Microsoft.Web.WebView2.Core.dll`, `.WinForms.dll`,
  `WebView2Loader.dll` x64, arm64 et x86) : `LICENSE.txt` et `NOTICE.txt` du paquet NuGet officiel
  `Microsoft.Web.WebView2` 1.0.3856.49 (9 Mo), contrôlé par l'empreinte SHA-512 que publie nuget.org.
  Le script vérifie que les cinq DLL livrées par pywebview sont identiques, octet pour octet, à celles du paquet ;
- la licence de proxy_tools 0.1.0 (publié en sources seulement, sans fichier de licence) : `LICENSE.txt` de
  son dépôt, au dernier commit (`db43f1e`), contrôlé par son empreinte SHA-256.

Les autres composants de la fenêtre (pywebview, pythonnet, clr_loader, cffi, pycparser, bottle,
typing_extensions) ont leurs textes dans les métadonnées de leur paquet (`<dist-info>\licenses\`).
`--installateur` ajoute `licences\InnoSetup\license.txt`, pris à côté d'`ISCC.exe` et contrôlé par son
empreinte (`INNO_LICENCE_SHA256`) : une autre version d'Inno Setup arrête la construction tant que le texte
n'a pas été relu.

Après une mise à jour de tokenizers ou d'onnxruntime, le script s'arrête et indique quoi mettre à jour
(`TOKENIZERS`, `DIRECTML`, `WEBVIEW2`, `PROXY_TOOLS` dans `construire.py`, inventaire de
`THIRD_PARTY_NOTICES.md`).

## L'installateur

`MyPhotos-<version>-installation.exe` installe la version « dossier » pour l'utilisateur seul, sans droits
d'administrateur, dans `%LOCALAPPDATA%\Programs\MyPhotos` : raccourci dans le menu Démarrer, icône sur le Bureau
(cochée d'office), entrée dans « Applications installées », assistant en français ou en anglais selon Windows.
Le script est `construction\MyPhotos.iss`, en UTF-8 sans BOM, fins de ligne LF.

**Inno Setup 6.7.4**, une seule fois, pour l'utilisateur seul (lien donné par https://jrsoftware.org/isdl.php) :

```
innosetup-6.7.4.exe /CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS="!fileassoc,!desktopicon" /DIR="%LOCALAPPDATA%\Programs\Inno Setup 6"
```

Avant de le lancer, vérifier le fichier téléchargé (10 604 576 octets) : SHA-256
`a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c` (`Get-FileHash`), et signature valide de
« Pyrsys B.V. » (`Get-AuthenticodeSignature`). `ISCC.exe` est ensuite dans `%LOCALAPPDATA%\Programs\Inno Setup 6`.

Une fois `dist\MyPhotos\` construit (`--dossier`) **et essayé** :

```
construction\construire.ps1 --installateur [--cache <dossier>] [--iscc <chemin de ISCC.exe>] [--sortie <dossier>]
```

Cette étape ne reconstruit rien. Elle relit la version dossier (contenu, version), refait `dist\licences\` comme
`--paquet`, compile `MyPhotos.iss` (une vingtaine de secondes, journal dans `build\installateur-iscc.log`),
contrôle la ressource de version de l'installateur (Inno Setup complète ses textes par des espaces : ils sont
retirés avant de comparer), puis écrit `dist\MyPhotos-<version>-installation.exe` et son `.sha256`. `ISCC.exe` est
cherché dans `--iscc`, sinon `MYPHOTOS_ISCC`, sinon `%LOCALAPPDATA%\Programs\Inno Setup 6`, puis dans les deux
`Program Files`. La version d'Inno Setup est lue dans le journal d'ISCC (ses fichiers ont une ressource de
version 0.0.0.0) : une autre version que 6.7.4 est signalée. Avec `--sortie <dossier>`, la version dossier est
prise dans `<dossier>\MyPhotos\`, et licences, journal et installateur sont écrits dans ce dossier.

À la main, l'équivalent est :

```
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DMyAppVersion=<version> [/DDossierApp=…] [/DDossierLicences=…] [/DSortie=…] [/DCheminLePlusLong=…] construction\MyPhotos.iss
```

Les chemins ont pour valeurs par défaut ceux du dépôt. La compilation s'arrête si `MyPhotos.exe` ou `_internal\`
manquent, si la version de `MyPhotos.exe` n'est pas `MyAppVersion`, ou si `licences\LISEZMOI.txt` manque.
`CheminLePlusLong` (calculé par `construire.py` : 99 caractères en 0.3.0, nom provisoire d'Inno Setup compris) sert
à refuser un dossier d'installation au chemin trop long (au-delà de 259 caractères en tout, Windows refuserait la
copie).

L'installateur contient `MyPhotos.exe`, `_internal\`, `LICENSE`, `THIRD_PARTY_NOTICES.md`,
`MyPhotos_mode_emploi.pdf` et `licences\` : les mêmes textes que l'archive `.zip`. Il est compressé en LZMA2
(compression solide).

Ce qui ne change jamais :

- `AppId` `{25DE0724-F7B4-4956-8238-1E33E16ABFED}` : il relie les versions entre elles (mise à jour par-dessus,
  désinstallation) ;
- `AppMutex` `MyPhotos-25DE0724-F7B4-4956-8238-1E33E16ABFED`, égal à `config.MUTEX` : MyPhotos crée ce mutex au
  démarrage et le garde jusqu'à l'arrêt. Tant qu'il existe, l'installateur et le désinstallateur demandent de
  fermer MyPhotos (en silence : code de sortie 1, rien n'est touché). `tests/test_construction.py` vérifie qu'ils
  sont égaux.

Ce que fait l'installateur :

- **Mise à jour.** Relancé sur un PC où MyPhotos est installé, il reprend le même dossier, retire d'abord
  `_internal\` et `licences\` de l'ancienne version (fichiers qu'elle seule avait), puis copie la nouvelle. Il ne les
  retire QUE dans le dossier d'une installation de MyPhotos (le dossier précédent, ou un dossier qui contient déjà
  `MyPhotos.exe` et `_internal\`) : un dossier existant choisi par `/DIR` garde ses propres `licences\`.
- **Dossier occupé.** Un dossier qui contient un `_internal\` sans `MyPhotos.exe`, et qui n'est pas l'installation
  précédente, est refusé : l'installateur ne vide jamais le dossier d'un autre programme. Un chemin trop long
  aussi. En silence, code de sortie 7.
- **Désinstallation.** Le programme est retiré, puis les raccourcis `MyPhotos.lnk` du Bureau et du menu Démarrer
  créés depuis les Réglages de MyPhotos, s'ils désignent le `MyPhotos.exe` désinstallé. Une question propose
  ensuite de supprimer les données (`%LOCALAPPDATA%\MyPhotos`, ou `MYPHOTOS_DOSSIER`), **Non** par défaut, avec
  leur taille. En silence, les données sont gardées, sauf avec `/SUPPRIMERDONNEES`.
- **Garde-fous de la suppression des données.** Chemin complet, sans `..` ; ni racine de disque, ni dossier de
  l'utilisateur ou du système (profil, AppData, Bureau, Documents, Images, Vidéos, Musique, OneDrive, TEMP,
  Windows, Program Files, ProgramData, dossier d'installation), ni l'un de leurs parents ; le dossier contient
  `index.db` (ou le marqueur `myphotos-donnees.txt`) et n'est pas lui-même un lien. Seuls les éléments que
  MyPhotos crée à la racine des données sont supprimés, par leur nom exact (`NomDeMyPhotos` : `modele\`,
  `index.db` et `miniatures.db` avec leurs fichiers SQLite et leurs copies `.abimee-…`, `journal.log`, `.1`, `.2`,
  `fenetre.json` et `.part`, `webview2\`, `edge\`) ; tout autre fichier est gardé (`Journal.log.txt`,
  `index.db.sauvegarde`…), et le dossier avec. Les dossiers `modele\`, `webview2\` et `edge\` ne partent que si
  leur contenu est celui de MyPhotos (`ContenuDeMyPhotos` : fichiers du moteur seulement ; `EBWebView\` seulement ;
  profil d'Edge avec son `Local State`) : des photos rangées là par l'utilisateur restent, avec le dossier. Un lien
  (jonction, lien symbolique) n'est jamais suivi : il est retiré lui-même, sa cible reste. `MYPHOTOS_MODELES` n'est
  jamais touché. Si MyPhotos range un jour autre chose à la racine de ses données, ou un autre type de fichier dans
  `modele\`, l'ajouter à `NomDeMyPhotos` ou à `FichierDuMoteur` : `tests/test_construction.py` le vérifie. Limite
  connue : les dossiers protégés sont comparés par leur chemin écrit ; un autre chemin vers le même dossier (nom
  court 8.3, partage `\\localhost\C$`) n'est pas reconnu, et seuls les noms ci-dessus y seraient supprimés.

Commandes silencieuses (administrateurs) : voir le README, « Installer sans questions, sur plusieurs PC ».
Toujours passer `/NORESTART`. Pour les essais : `/NOICONS /TASKS=""` (aucun raccourci) et `/DIR=<dossier d'essai>` ;
`/RACCOURCISDANS=<dossier>` fait chercher au désinstallateur les raccourcis créés par MyPhotos dans ce dossier,
jamais sur le vrai Bureau. L'essai réel du dépôt, à la demande : `MYPHOTOS_ESSAI_INSTALLATEUR=1` (voir
`tests/test_construction.py`).

À publier, en plus de `MyPhotos.exe` et de l'archive `.zip` : `MyPhotos-<version>-installation.exe` et son
`.sha256`, puis, depuis la 0.4.0, `MyPhotos-mise-a-jour.json` et son `.sha256` (voir plus bas).

## Le manifeste de mise à jour (0.4.0)

Les MyPhotos installés lisent, avec l'accord de l'utilisateur, le fichier `MyPhotos-mise-a-jour.json` de la
dernière Release (`releases/latest/download/…`) pour proposer la nouvelle version (`myphotos/mise_a_jour.py`).
Il se fait après l'installateur, dont il donne la taille et l'empreinte :

```
construction\construire.ps1 --manifeste [--sortie <dossier>]
```

Les nouveautés affichées par MyPhotos viennent de `construction\nouveautes.json` (une entrée par version, 8 lignes
au plus, 300 caractères au plus chacune, sans caractère de contrôle) : la construction s'arrête si la version n'y
est pas. Le manifeste est signé avec la **clé privée du projet**, puis relu avec les règles de MyPhotos et la clé
publique qu'il contient (`config.CLE_MISE_A_JOUR`) : une clé qui ne correspond pas arrête tout.

**La clé privée** n'est jamais dans le dépôt ni sur GitHub : `%APPDATA%\MyPhotos-publication\cle-signature-privee.txt`
(ou le fichier désigné par `MYPHOTOS_CLE_SIGNATURE`). Elle a été créée une fois, le 25/11/2026, par
`construction\cle_signature.py --creer`, qui refuse d'en remplacer une ; `--verifier` dit si celle de ce PC
correspond à MyPhotos. En garder une copie hors du PC (clé USB rangée). Perdue, plus aucune mise à jour en un
clic : il faudrait publier une version avec une nouvelle clé publique, que chacun installerait à la main. Pour
publier depuis un autre PC, y recopier ce fichier au même endroit.

Après la publication, `MyPhotos-mise-a-jour.json` doit être joint à la Release **avant** qu'elle devienne la
dernière (« latest ») : sinon, pendant quelques minutes, les MyPhotos qui vérifient ne trouvent rien (sans
conséquence : ils réessaient le lendemain).

## Ce qui est embarqué

- Python 3.14, le paquet `myphotos`, `page\` et `construction\MyPhotos.ico`, qui sert d'icône à la
  fenêtre de MyPhotos et, dans les replis, à la fenêtre « Choisir un dossier » (barre des tâches).
- onnxruntime-directml 1.24.4 : le module Python, `DirectML.dll` et `onnxruntime_providers_shared.dll`.
  Les fournisseurs disponibles sont `DmlExecutionProvider` (carte graphique) et `CPUExecutionProvider`.
- numpy (avec OpenBLAS), Pillow, tokenizers, OpenSSL pour le téléchargement en HTTPS, et SQLite. Depuis la
  0.4.0, ni tkinter ni Tcl/Tk : la fenêtre « Choisir un dossier » des replis (Edge, navigateur) est celle de
  Windows, ouverte par COM (`myphotos/dialogue.py`) ; `construire.py` les exclut et le contrôle final vérifie
  que `_tkinter.pyd`, `tcl90.dll`, `tcl9tk90.dll` et `libtommath.dll` sont absents.
- La fenêtre (0.3.0) : pywebview 6.2.1 (moteurs Windows Forms et Edge Chromium seulement), les DLL WebView2
  1.0.3856.49, pythonnet 3.2.1 (`Python.Runtime.dll`), clr_loader 0.3.1 (`ClrLoader.dll` 64 bits), cffi 2.1.1
  (`_cffi_backend`), pycparser, proxy_tools, bottle et typing_extensions. Le runtime WebView2 et le
  .NET Framework viennent de Windows.
- La ressource de version contient FileDescription et ProductName « MyPhotos », la version lue dans
  `myphotos/config.py` et le copyright « Copyright (c) 2026 glenac (MIT) ». L'icône couvre les tailles
  16 à 256 px. UPX n'est pas utilisé.

Ce qui n'est **pas** embarqué :

- Le moteur de reconnaissance. MyPhotos le télécharge au premier lancement, ou le recopie depuis un
  dossier `modele` posé à côté de l'exécutable.
- `onnxruntime.dll` (20 Mo). C'est l'interface C d'onnxruntime, que le module Python ne charge pas.
- Les outils d'onnxruntime (quantification, transformers), sympy, protobuf, huggingface_hub, le format
  AVIF de Pillow (7,5 Mo) et les modules d'aide et de test de Python. La liste est dans `EXCLUS`, dans
  `construire.py`. Si MyPhotos se met à utiliser l'un d'eux, il faut l'en retirer.

## Taille et démarrage

Version dossier 0.3.0 : **119,7 Mo** en 101 fichiers (`MyPhotos.exe` 6,4 Mo et 100 fichiers dans `_internal\`) ;
installateur **39,4 Mo** (LZMA2, compression solide), moins que `MyPhotos.exe` seul (49,3 Mo). Une fois installée,
avec les licences et le désinstallateur, environ 125 Mo.

Démarrage jusqu'à la première réponse de la page (24/11/2026, 0.3.0, même machine, `MYPHOTOS_SANS_PAGE=1`,
bureau invisible, données copiées à chaque fois, 6 + 6 lancements alternés) :

- version dossier : **0,51 s** (0,506 à 0,515 s), un seul processus, rien d'extrait ; arrêt en 0,54 s ;
- un seul fichier : **1,57 s** (1,572 à 1,581 s), deux processus (chargeur et Python), extraction dans
  `%TEMP%\_MEI…` ; arrêt en 0,82 s.

Soit environ 1,06 s de gagnées à chaque lancement (trois fois plus rapide). La version installée affiche sa
fenêtre WebView2 2,2 s après le lancement (page servie à 1,8 s), avec une base existante.

Publication 0.3.0 (24/11/2026) : `MyPhotos.exe` 49 277 981 octets, `MyPhotos-0.3.0-windows.zip` 49 691 323 octets,
`MyPhotos-0.3.0-installation.exe` 39 441 585 octets. Essayés sur un bureau Windows invisible, tous les processus dans
un Job Object : fenêtre et page à 2,2 s, exécutable seul comme installé, recherche, photos les plus proches,
doublons, sélection, Actualiser, exclusion puis réinclusion, assistant, Copier dans un dossier avec la vraie fenêtre
de choix, fermeture en 1,5 à 2 s (code 0, aucun processus restant, `_MEI…` effacé), relance juste après la
fermeture, repli navigateur forcé, reprise d'une base 0.2.0, installation, mise à jour et désinstallations.
Analyse Windows Defender (`MpCmdRun -Scan -ScanType 3`, signatures 1.463.447.0) : aucune menace dans les trois
fichiers ni dans `dist\MyPhotos\`.

`MyPhotos.exe` 0.2.0 pèse **47,9 Mo** et occupe 117 Mo une fois extrait. À chaque lancement, il s'extrait
dans `%TEMP%\_MEI…` en 0,9 s environ ; ce dossier est effacé à l'arrêt.

Mesures du 24/11/2026 (0.2.0, Ryzen 5 5600X, Radeon RX 6600 XT), sur une base existante, l'exécutable
lancé sur un bureau Windows invisible (`MYPHOTOS_SANS_PAGE=1`) :

- La page répond en 1,0 s. Le moteur est prêt sur la carte graphique 5,8 à 6,2 s après le lancement,
  sans retarder la page (« Préparation… »). En 0.1.0, ce chargement retardait la page (1,6 à 6,5 s).
- 32 photos publiques analysées en 1,0 s, doublons en 0,5 s, arrêt par `POST /api/quitter` en 2 s :
  plus aucun processus, dossier `_MEI…` effacé.
- Une base faite par la 0.1.0 est reprise telle quelle (moteur, dossiers, analyse).
- Analyse Windows Defender (`MpCmdRun -Scan -ScanType 3`, signatures 1.463.447.0) : aucune menace, pour
  `MyPhotos.exe` comme pour l'archive `.zip`.

## Pièges connus

- **fp16 sur processeur.** Avec l'optimisation complète (`ORT_ENABLE_ALL`), ONNX Runtime 1.24 plante en
  chargeant un modèle fp16 sur processeur (fusion `SimplifiedLayerNormFusion`). MyPhotos utilise donc
  `ORT_ENABLE_BASIC` sur processeur. C'est sans perte et aussi rapide. Le niveau complet reste utilisé
  sur la carte graphique.
- **DirectML, origine de la DLL.** `DirectML.dll` doit venir du paquet onnxruntime : celle de
  `C:\Windows\System32` est plus ancienne. Le module Python la charge à la demande (chargement différé).
  Dans l'exécutable, c'est bien celle du paquet qui est chargée, depuis `_MEI…\onnxruntime\capi\` :
  c'est vérifié sur le processus en marche et par le programme d'essai. `d3d12.dll` vient de Windows.
- **DirectML, réglages.** Les sessions sur la carte graphique demandent `enable_mem_pattern = False` et
  `ORT_SEQUENTIAL`. ONNX Runtime 1.24.4 plante aussi dans deux cas : deux calculs simultanés depuis deux
  fils, et une session détruite pendant qu'une plus ancienne vit encore. MyPhotos passe les calculs un
  par un et garde son moteur. Ces contournements sont dans le code, pas dans la construction.
- **Pas de console.** `sys.stdout` et `sys.stderr` valent `None`. `lancer.py` les remplace, et MyPhotos
  ne lance aucun programme en mode console, qui ferait clignoter une fenêtre noire.
- **Programme d'essai.** Il importe ses modules par leur nom, ce que PyInstaller ne voit pas.
  `--essai` lui ajoute donc tout le paquet `myphotos` en import caché.
- **Exécutable non signé.** Au premier téléchargement, Windows SmartScreen peut afficher un
  avertissement, pour l'installateur comme pour l'exécutable. Les antivirus signalent parfois à tort les
  exécutables PyInstaller en un seul fichier ; la version dossier (installée) en attire moins.
- **Inno Setup, `[Code]`.** Une ligne dont le premier caractère utile est `[` (un tableau de `FmtMessage` passé à
  la ligne, par exemple) est lue comme un nom de section : « Invalid section tag ». Un essai le vérifie.
- **Inno Setup, `/NOICONS`.** Sans `AllowNoIcons=yes` et `Check: not WizardNoIcons` sur le raccourci du menu
  Démarrer, l'option n'a aucun effet.
- **Inno Setup, chemins longs.** Le plus long chemin de la version dossier fait 89 caractères (textes de licence
  de NumPy dans `_internal\`) : au-delà de 259 caractères en tout, la copie échouerait au milieu (« chemin d'accès
  introuvable ») et tout serait annulé. L'installateur refuse donc d'avance un dossier trop long. Inno Setup copie
  chaque fichier sous un nom provisoire posé à côté de lui (`is-XXXXXXXXXX.tmp`, 17 caractères) : c'est ce nom,
  dans le dossier le plus profond, qui compte (99 caractères en 0.3.0, et non 89 : avec 89, un dossier de 160
  caractères était accepté puis la copie échouait, code 5 ; essai adverse).
- **Inno Setup, `/VERYSILENT` sans `/NORESTART`** redémarre Windows sans demander si un redémarrage est
  nécessaire.
