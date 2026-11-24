# Construire MyPhotos.exe

MyPhotos.exe est un seul fichier, sans console, fabriqué avec PyInstaller à partir de `lancer.py`.

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
  (code de sortie 0 si tout va bien).

## Publier

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

Trois sortes de fichiers ne sont pas dans l'environnement de construction. Ils sont téléchargés une fois, contrôlés
par leur empreinte, puis gardés dans un cache **hors du dépôt** (`--cache <dossier>`, sinon la variable
`MYPHOTOS_CACHE_CONSTRUCTION`, sinon `%LOCALAPPDATA%\MyPhotos-construction\cache`) :

- la licence de DirectML : paquet NuGet officiel `Microsoft.AI.DirectML` 1.15.4 (202 Mo), contrôlé par
  l'empreinte SHA-512 que publie nuget.org. Le script vérifie aussi que la `DirectML.dll` embarquée est celle de
  ce paquet : même version, même code (seule la signature numérique diffère) ;
- la licence de tokenizers (Apache-2.0), absente de sa roue : fichier `LICENSE` de son dépôt, au commit de
  l'étiquette v0.23.3, contrôlé par son empreinte SHA-256 ;
- les fichiers de licence des bibliothèques Rust de tokenizers qui demandent leurs propres mentions (licence MIT
  seule, BSD-2-Clause, Unicode-3.0) : pris dans leur paquet crates.io, contrôlé par l'empreinte SHA-256 de
  l'inventaire de la roue (`sboms`). Les autres sont couvertes par la licence Apache-2.0 jointe.

Après une mise à jour de tokenizers ou d'onnxruntime, le script s'arrête et indique quoi mettre à jour
(`TOKENIZERS`, `DIRECTML` dans `construire.py`, inventaire de `THIRD_PARTY_NOTICES.md`).

## Ce qui est embarqué

- Python 3.14, le paquet `myphotos`, `page\` et `construction\MyPhotos.ico`, qui sert d'icône à la
  fenêtre « Choisir un dossier ».
- onnxruntime-directml 1.24.4 : le module Python, `DirectML.dll` et `onnxruntime_providers_shared.dll`.
  Les fournisseurs disponibles sont `DmlExecutionProvider` (carte graphique) et `CPUExecutionProvider`.
- numpy (avec OpenBLAS), Pillow, tokenizers, Tcl/Tk 9 pour tkinter, OpenSSL pour le téléchargement en
  HTTPS, et SQLite.
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

## Taille

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
  avertissement. Les antivirus signalent parfois à tort les exécutables PyInstaller en un seul fichier.
