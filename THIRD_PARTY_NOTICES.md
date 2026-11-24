# Composants tiers et licences

Le code source de MyPhotos (ce dépôt) est publié sous licence MIT, © 2026 glenac : voir [LICENSE](LICENSE).

**MyPhotos.exe** est construit avec PyInstaller. En plus du code de MyPhotos, il embarque l'interpréteur Python et les bibliothèques listées ci-dessous, chacune sous sa propre licence. Ce document indique, pour chaque composant, sa licence, ce qu'elle impose à qui redistribue l'exécutable, et où trouver le texte complet. Il ne remplace pas ces textes, qui font foi.

Inventaire du 23/11/2026, établi à partir des fichiers de licence des paquets installés (versions figées de `requirements.txt`, Python 3.14.10), du script de construction `construction/construire.py` (listes `EXCLUS`, `RETIRES` et `OBLIGATOIRES`) et du contenu d'une construction d'essai. **À refaire à chaque changement de version d'un paquet, de Python ou de PyInstaller, et à chaque modification de ces listes.**

## En résumé

| Composant | Version | Licence | Dans MyPhotos.exe |
|---|---|---|---|
| [ONNX Runtime](#onnx-runtime-onnxruntime-directml) (paquet `onnxruntime-directml`) | 1.24.4 | MIT, plus les notices de ses composants | oui, sans `onnxruntime.dll` |
| [DirectML](#directml) (`DirectML.dll`) | 1.15.4 | licence Microsoft propriétaire, redistribuable sous conditions | oui |
| [NumPy](#numpy) | 2.5.5 | BSD-3-Clause (et 0BSD, MIT, Zlib, CC0-1.0 pour des parties) | oui |
| [OpenBLAS](#numpy) (livré avec NumPy) | 0.3.34 | BSD-3-Clause ; LAPACK : BSD-3-Clause-Open-MPI ; runtime GCC : GPL-3.0+ avec exception | oui |
| [Pillow](#pillow) | 13.0.0 | MIT-CMU | oui |
| [Bibliothèques livrées avec Pillow](#pillow) | voir plus bas | IJG, BSD, MIT, 0BSD, Zlib, licence libtiff | oui, en partie |
| [tokenizers](#tokenizers) (Hugging Face) | 0.23.3 | Apache-2.0, plus des bibliothèques Rust MIT ou Apache-2.0 | oui |
| [Python](#python) | 3.14.10 | PSF License 2.0 | oui |
| [Bibliothèques livrées avec Python](#bibliothèques-livrées-avec-python) | voir plus bas | Apache-2.0, MIT, BSD, domaine public, Zlib… | oui |
| [Tcl/Tk](#tcltk) (fenêtre « Choisir un dossier ») | 9.0.4 | licence Tcl/Tk (type BSD) | oui |
| [Runtime Microsoft Visual C++](#runtime-microsoft-visual-c) | 14.x | « Microsoft Distributable Code » | oui |
| [PyInstaller](#pyinstaller) (chargeur) | 6.22.3 | GPL-2.0-or-later avec exception pour le chargeur | oui, le chargeur seulement |
| [SigLIP 2](#le-moteur-de-reconnaissance-siglip-2) (Google) | 2025 | Apache-2.0 | **non** : téléchargé par l'utilisateur |
| [Exports ONNX d'onnx-community](#le-moteur-de-reconnaissance-siglip-2) | révisions figées | aucune licence propre déclarée ; dérivés de SigLIP 2 | **non** : téléchargés |

**Ne sont pas embarqués** (exclus par `construction/construire.py`) : `sympy`, `mpmath`, `flatbuffers`, `protobuf` et `packaging`, dépendances déclarées d'`onnxruntime-directml` mais inutiles à l'exécution ; `huggingface_hub` et ses dépendances, tirées par `tokenizers` (MyPhotos télécharge le moteur lui-même) ; les outils d'ONNX Runtime (quantification, conversion) ; les modules `_avif`, `_imagingft` et `_imagingtk` de Pillow. La bibliothèque `onnxruntime.dll` (interface C d'ONNX Runtime) est aussi retirée : le module Python contient déjà tout le moteur.

## Points d'attention pour une redistribution publique

Aucune licence n'interdit de publier MyPhotos.exe gratuitement sur GitHub. Il faut cependant savoir ceci :

1. **DirectML n'est pas un logiciel libre.** C'est un composant Microsoft propriétaire, que sa licence permet de redistribuer *à l'intérieur d'une application* pour Windows, mais pas seul. L'exécutable MyPhotos.exe n'est donc pas entièrement libre, même si son code source l'est. Voir [DirectML](#directml).
2. **Télémétrie Microsoft.** Les versions officielles d'ONNX Runtime pour Windows émettent par défaut des événements de diagnostic par le mécanisme TraceLogging de Windows (fichier `Privacy.md` d'ONNX Runtime) ; la licence de DirectML prévoit aussi une collecte de données (§ 2). Windows ne transmet ces événements à Microsoft que selon les réglages de diagnostic de l'utilisateur. MyPhotos lui-même n'envoie rien, mais son code n'appelle pas aujourd'hui `onnxruntime.disable_telemetry_events()`, la fonction d'ONNX Runtime qui coupe ces événements.
3. **Les textes de licence doivent accompagner l'exécutable.** Les licences MIT, BSD, Apache-2.0, Tcl/Tk, FTL, IJG, PSF, etc. demandent que les mentions de droits d'auteur et les textes de licence soient fournis avec le binaire. Le paquet `onnxruntime-directml` **ne contient pas** la licence de DirectML : il faut la prendre dans le paquet NuGet `Microsoft.AI.DirectML` 1.15.4. Voir [Ce que chaque publication doit contenir](#ce-que-chaque-publication-doit-contenir).
4. **Le runtime Visual C++** (« Microsoft Distributable Code ») est redistribuable à condition de ne pas en altérer les mentions, de ne pas laisser croire que le logiciel vient de Microsoft et de faire accepter aux utilisateurs des conditions qui protègent ce code au moins autant que celles de Microsoft. Voir [Runtime Microsoft Visual C++](#runtime-microsoft-visual-c).
5. **Aucun composant sous GPL n'impose sa licence à MyPhotos** :
   - le chargeur de PyInstaller bénéficie d'une exception explicite ;
   - le runtime GCC présent dans OpenBLAS relève de la « GCC Runtime Library Exception » ;
   - FreeType, distribué au choix sous FTL ou GPLv2, n'est pas embarqué ; s'il l'était, MyPhotos retiendrait la FTL ;
   - `libimagequant` (GPL-3.0) n'est pas compilé dans la roue de Pillow utilisée (`PIL.features.check('libimagequant')` renvoie `False`).
6. **Eigen**, compilé dans ONNX Runtime, est sous MPL-2.0 : qui redistribue le binaire doit indiquer où se procurer son code source (voir [ONNX Runtime](#onnx-runtime-onnxruntime-directml)).

## Ce que chaque publication doit contenir

À joindre à chaque Release. `construction\construire.ps1 --paquet` les rassemble, après avoir vérifié leurs versions et leurs empreintes, dans le dossier `licences\` de l'archive `MyPhotos-<version>-windows.zip`, publiée avec MyPhotos.exe (voir [construction/LISEZMOI.md](construction/LISEZMOI.md)) :

| Fichier | Où le prendre |
|---|---|
| `LICENSE` (MyPhotos, MIT) | racine du dépôt |
| `THIRD_PARTY_NOTICES.md` (ce fichier) | racine du dépôt |
| `onnxruntime/LICENSE`, `onnxruntime/ThirdPartyNotices.txt`, `onnxruntime/Privacy.md` | `site-packages` de l'environnement de construction |
| `LICENSE.txt` et `ThirdPartyNotices.txt` de DirectML | paquet NuGet [Microsoft.AI.DirectML 1.15.4](https://www.nuget.org/packages/Microsoft.AI.DirectML/1.15.4) (absents de la roue Python) |
| `numpy-2.5.5.dist-info/licenses/` (dossier entier) | `site-packages` |
| `pillow-13.0.0.dist-info/licenses/LICENSE` | `site-packages` |
| licence Apache-2.0 de tokenizers et notices de ses bibliothèques Rust | `LICENSE` du [dépôt huggingface/tokenizers](https://github.com/huggingface/tokenizers/blob/v0.23.3/LICENSE) (étiquette v0.23.3) ; liste dans `tokenizers-0.23.3.dist-info/sboms/` ; fichiers de licence des bibliothèques qui demandent leurs propres mentions, pris dans leur paquet [crates.io](https://crates.io) |
| `LICENSE.txt` de Python (comprend OpenSSL, bzip2, libffi, Zstandard, Tcl, Tk et les conditions Microsoft) | dossier d'installation de Python 3.14 |
| `COPYING.txt` de PyInstaller | `pyinstaller-6.22.3.dist-info/licenses/` |

### Mentions exigées dans la documentation

Ces phrases doivent figurer dans la documentation du produit. Elles sont reproduites ici telles que les licences les demandent :

- libjpeg-turbo (licence IJG, clause 2) : *This software is based in part on the work of the Independent JPEG Group.* (Ce logiciel repose en partie sur le travail de l'Independent JPEG Group.)
- FreeType (FTL), seulement si une construction embarque le module `_imagingft` de Pillow : *Portions of this software are copyright © The FreeType Project (https://freetype.org). All rights reserved.* (Ce logiciel repose en partie sur le travail de l'équipe FreeType.)

## ONNX Runtime (onnxruntime-directml)

Le moteur d'inférence qui fait tourner SigLIP 2, sur la carte graphique (DirectML) ou le processeur. Fichiers embarqués : `onnxruntime_pybind11_state.pyd` (le moteur complet), `onnxruntime_providers_shared.dll` et le code Python du paquet ; `onnxruntime.dll` est retiré à la construction.

- **Licence** : MIT, « Copyright (c) Microsoft Corporation ».
- **Obligations** : joindre la mention de droits d'auteur et le texte de la licence MIT ; joindre `ThirdPartyNotices.txt` (6 121 lignes), qui couvre les composants compilés dans la bibliothèque : ONNX, Protocol Buffers, Abseil, FlatBuffers, Eigen, Microsoft GSL, SafeInt, nlohmann/json, HowardHinnant/date, pybind11, etc. Eigen est sous **MPL-2.0** : il faut indiquer où obtenir son code source, publié sur [gitlab.com/libeigen/eigen](https://gitlab.com/libeigen/eigen) et référencé par les sources d'ONNX Runtime ; Microsoft propose aussi ces sources sur http://3rdpartysource.microsoft.com, comme l'indique l'en-tête de `ThirdPartyNotices.txt`.
- **Vie privée** : `Privacy.md` précise que la télémétrie TraceLogging est « turned ON by default in the official builds » pour Windows ; elle se coupe par l'API (`onnxruntime.disable_telemetry_events()` en Python).
- **Liens** : [github.com/microsoft/onnxruntime](https://github.com/microsoft/onnxruntime), [licence](https://github.com/microsoft/onnxruntime/blob/main/LICENSE), [Privacy.md](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md).

## DirectML

La couche de Microsoft qui fait calculer la carte graphique (toute carte compatible DirectX 12). Fichier embarqué : `DirectML.dll` (« DirectML Redistributable Library », version 1.15.4), livré dans le paquet `onnxruntime-directml`.

- **Licence** : « MICROSOFT SOFTWARE LICENSE TERMS – MICROSOFT DIRECTX MACHINE LEARNING (DIRECTML) », licence propriétaire. Texte complet dans le paquet NuGet [Microsoft.AI.DirectML 1.15.4](https://www.nuget.org/packages/Microsoft.AI.DirectML/1.15.4).
- **Conditions de redistribution**, citées mot pour mot :

  > 1\. INSTALLATION AND USE RIGHTS.
  >
  > a) General. Subject to the terms of this agreement, you may install and use any number of copies of the software, and solely for use on Windows and Xbox. You may copy and distribute the software (i.e. make available for third parties) in applications and services you develop in the build with Machine Learning tools and frameworks, and/or games that run on Windows and Xbox.
  >
  > […]
  >
  > c) Third Party Components. The software may include third party components with separate legal notices or governed by other agreements, as may be described in the ThirdPartyNotices file(s) accompanying the software.

  > 3\. SCOPE OF LICENSE. The software is licensed, not sold. Microsoft reserves all other rights. Unless applicable law gives you more rights despite this limitation, you will not (and have no right to):
  >
  > a) work around any technical limitations in the software that only allow you to use it in certain ways;
  >
  > b) reverse engineer, decompile or disassemble the software, or otherwise attempt to derive the source code for the software, except and to the extent required by third party licensing terms governing use of certain open source components that may be included in the software;
  >
  > c) remove, minimize, block, or modify any notices of Microsoft or its suppliers in the software;
  >
  > d) use the software in any way that is against the law or to create or propagate malware; or
  >
  > e) except as expressly stated in Section 1, share, publish, distribute, or lease the software, provide the software as a stand-alone offering for others to use, or transfer the software or this agreement to any third party.

  > 4\. EXPORT RESTRICTIONS. You must comply with all domestic and international export laws and regulations that apply to the software, which include restrictions on destinations, end users, and end use.

- **En clair** : MyPhotos peut redistribuer `DirectML.dll` parce qu'il s'agit d'une application d'apprentissage automatique pour Windows ; la DLL ne doit pas être proposée seule, ni modifiée, ni désassemblée, et ses mentions doivent rester intactes. La licence prévoit aussi une collecte de données (§ 2 a : « The software may collect information about you and your use of the software, and send that to Microsoft »).
- **Composants tiers de DirectML** (son `ThirdPartyNotices.txt`) : Microsoft GSL (MIT), la bibliothèque *half* de Christian Rau (MIT) et Zstandard (BSD).
- **Liens** : [Microsoft.AI.DirectML sur NuGet](https://www.nuget.org/packages/Microsoft.AI.DirectML/1.15.4), [github.com/microsoft/DirectML](https://github.com/microsoft/DirectML).

## NumPy

Calcul sur les vecteurs. Fichiers embarqués : les modules compilés de NumPy et `numpy.libs/` (OpenBLAS, runtime C++ de Microsoft).

- **Licence** : BSD-3-Clause, « Copyright (c) 2005-2025, NumPy Developers » ; certaines parties sous 0BSD, MIT, Zlib et CC0-1.0 (expression déclarée : `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`). Le dossier `licenses/` détaille pocketfft, lapack_lite, les générateurs aléatoires (MT19937, PCG64, Philox, SFC64, SplitMix64), libdivide, Highway, x86-simd-sort, SVML, dragon4 et pythoncapi-compat.
- **Composants livrés avec la roue** (section « This binary distribution of NumPy also bundles the following software » de son `LICENSE.txt`) :
  - **OpenBLAS** (`libscipy_openblas64_*.dll`, version 0.3.34) : BSD-3-Clause ;
  - **LAPACK**, inclus dans OpenBLAS : BSD-3-Clause-Open-MPI ;
  - **runtime GCC** (libgfortran), lié statiquement à OpenBLAS : GPL-3.0-or-later **avec la GCC Runtime Library Exception 3.1**, qui autorise sa redistribution dans un programme sous une autre licence.
- **Obligations** : reproduire les mentions de droits d'auteur, les conditions et l'avertissement BSD dans la documentation ou les fichiers fournis avec le binaire, donc joindre `LICENSE.txt` et le dossier `licenses/` de NumPy.
- **Liens** : [numpy.org](https://numpy.org), [licence](https://github.com/numpy/numpy/blob/main/LICENSE.txt), [OpenBLAS](https://github.com/OpenMathLib/OpenBLAS).

## Pillow

Lecture des photos et fabrication des miniatures.

- **Licence** : MIT-CMU (licence historique de PIL), « Copyright © 1997-2011 by Secret Labs AB, Copyright © 1995-2011 by Fredrik Lundh and contributors, Copyright © 2010 by Jeffrey 'Alex' Clark and contributors ».
- **Obligations** : la mention de droits d'auteur et le texte de permission doivent figurer dans la documentation ; joindre le fichier `LICENSE` de Pillow, qui reproduit aussi les licences des bibliothèques ci-dessous.
- **Bibliothèques livrées dans la roue Windows** (d'après ce fichier `LICENSE` et `PIL.features`). MyPhotos n'importe que `PIL.Image` et `PIL.ImageOps` : l'exécutable embarque les modules `_imaging`, `_imagingcms` et `_webp`. `construction/construire.py` exclut `_avif` (format AVIF, avec libavif, dav1d et libyuv), `_imagingft` (polices de caractères, avec FreeType, HarfBuzz, Brotli et libpng) et `_imagingtk`.

  | Bibliothèque | Version | Licence | Dans MyPhotos.exe |
  |---|---|---|---|
  | libjpeg-turbo | 3.2.0 | IJG, BSD-3-Clause, Zlib | oui ; phrase obligatoire, voir [Mentions exigées](#mentions-exigées-dans-la-documentation) |
  | zlib-ng | 2.3.3 | Zlib | oui |
  | libtiff | 4.7.2 | licence libtiff (type BSD) | oui ; mentions à garder dans la documentation |
  | XZ (liblzma) | 5.8.4 | 0BSD | oui, avec libtiff |
  | OpenJPEG | 2.5.4 | BSD-2-Clause | oui |
  | Little CMS | 2.19 | MIT | oui |
  | libwebp | 1.6.0 | BSD-3-Clause | oui |
  | libavif | 1.4.2 | BSD-2-Clause | non (avec dav1d, BSD-2-Clause, et libyuv, BSD-3-Clause) |
  | FreeType | 2.14.3 | FTL (ou GPLv2, au choix) | non ; si une construction l'embarque, MyPhotos retient la FTL et la mention devient obligatoire |
  | HarfBuzz | 14.5.0 | « Old MIT » | non |
  | Brotli | 1.2.0 | MIT | non |
  | libpng | 1.6.58 | PNG Reference Library License v2 | non |

  `libimagequant` (GPL-3.0), `raqm` et `fribidi` ne sont **pas** présents dans cette roue.
- **Liens** : [python-pillow.github.io](https://python-pillow.github.io), [licence](https://github.com/python-pillow/Pillow/blob/main/LICENSE).

## tokenizers

Découpe en jetons le texte tapé dans la recherche. Fichier embarqué : `tokenizers.pyd` (écrit en Rust).

- **Licence** : Apache-2.0, © Hugging Face.
- **Obligations** : fournir une copie de la licence Apache-2.0 ; ne pas utiliser les marques de Hugging Face. La roue ne contient aucun fichier de licence, seulement un inventaire CycloneDX (`sboms/tokenizers-python.cyclonedx.json`).
- **Bibliothèques Rust compilées dans le module** : 122 bibliothèques (« crates »), presque toutes sous `MIT OR Apache-2.0`. Dix-sept sont sous licence MIT seule, dont la mention de droits d'auteur doit accompagner le binaire : `castaway`, `compact_str`, `console`, `indicatif`, `mio`, `nom`, `onig`, `onig_sys`, `slab`, `strsim`, `tokio`, `unit-prefix`, `zmij`, et quatre qui ne servent qu'à la compilation (`darling`, `darling_core`, `darling_macro`, `tokio-macros`) ; `onig_sys` compile en plus la bibliothèque C Oniguruma (BSD-2-Clause). Autres exceptions : `aho-corasick`, `memchr`, `jiff` (`Unlicense OR MIT`) ; `macro_rules_attribute` (`Apache-2.0 OR MIT OR Zlib`) ; `numpy` pour Rust (`BSD-2-Clause`) ; `ryu` (`Apache-2.0 OR BSL-1.0`) ; `zerocopy` (`BSD-2-Clause OR Apache-2.0 OR MIT`) ; `unicode-ident` (`(MIT OR Apache-2.0) AND Unicode-3.0`, dont la mention Unicode doit accompagner le binaire).
- **Liens** : [github.com/huggingface/tokenizers](https://github.com/huggingface/tokenizers), [licence Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Python

L'interpréteur (`python314.dll`) et la bibliothèque standard, embarqués sans modification.

- **Licence** : PSF License Agreement for Python, version 2 (avec, pour l'historique, les licences BeOpen, CNRI et CWI).
- **Obligations** : garder la mention « Copyright (c) 2001 Python Software Foundation; All Rights Reserved » et le texte de la licence ; décrire les modifications apportées à Python s'il y en a. Il n'y en a pas : PyInstaller embarque Python tel quel, la bibliothèque standard étant précompilée.
- **Liens** : [python.org](https://www.python.org), [licences de Python 3.14 et de ses composants](https://docs.python.org/3.14/license.html).

### Bibliothèques livrées avec Python

Celles que MyPhotos.exe embarque, d'après une construction d'essai (versions livrées avec Python 3.14.10 pour Windows) :

| Bibliothèque | Version | Licence | Utilisée pour |
|---|---|---|---|
| OpenSSL (`libssl-3.dll`, `libcrypto-3.dll`) | 3.5.9 | Apache-2.0 | HTTPS du téléchargement du moteur, empreintes SHA-256 |
| SQLite (`sqlite3.dll`) | 3.50.4 | domaine public | l'index des photos |
| libffi (`libffi-8.dll`) | | MIT | `ctypes` (appels à Windows) |
| zlib-ng (`zlib1.dll`) | 2.2.4 | Zlib | compression |
| bzip2 | 1.0.8 | licence de type BSD | bibliothèque standard |
| XZ (liblzma) | | 0BSD | bibliothèque standard |
| Zstandard | 1.5.7 | BSD-3-Clause | bibliothèque standard |
| Expat | 2.9.0 | MIT | bibliothèque standard |
| mpdecimal | 4.0.0 | BSD-2-Clause | bibliothèque standard |
| HACL* | | MIT | fonctions de hachage intégrées |

Le fichier `LICENSE.txt` livré avec Python reproduit les licences d'OpenSSL, bzip2, libffi, Zstandard, Tcl et Tk ; la page [docs.python.org/3.14/license.html](https://docs.python.org/3.14/license.html) donne celles des autres.

## Tcl/Tk

Sert uniquement à ouvrir la fenêtre Windows « Choisissez un dossier de photos ». Fichiers embarqués : `tcl90.dll`, `tcl9tk90.dll`, `libtommath.dll` et les bibliothèques de scripts de Tcl/Tk.

- **Licence** : licence Tcl/Tk, de type BSD : « This software is copyrighted by the Regents of the University of California, Sun Microsystems, Inc., Scriptics Corporation, ActiveState Corporation and other parties. » (Tk ajoute Apple Inc.)
- **Obligations** : « provided that existing copyright notices are retained in all copies and that this notice is included verbatim in any distributions » : joindre le texte de la licence tel quel (il figure dans le `LICENSE.txt` de Python).
- **libtommath** (calcul sur grands entiers, requis par Tcl 9) : domaine public (« The LibTom license »).
- **Liens** : [licence Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html), [libtommath](https://github.com/libtom/libtommath).

## Runtime Microsoft Visual C++

Fichiers embarqués : `vcruntime140.dll` et `vcruntime140_1.dll` (livrés avec Python), `msvcp140.dll` (livré avec NumPy) et `msvcp140_1.dll` (copié par PyInstaller depuis Windows, comme dépendance des modules compilés).

- **Licence** : « Microsoft Distributable Code », redistribuable avec une application. Le `LICENSE.txt` de Python résume les conditions : « If you further distribute programs that include the Microsoft Distributable Code, you must comply with the restrictions on distribution specified by Microsoft. In particular, you must require distributors and external end users to agree to terms that protect the Microsoft Distributable Code at least as much as Microsoft's own requirements for the Distributable Code. » Il ne faut pas non plus altérer ses mentions, utiliser les marques de Microsoft dans le nom du programme, le faire tourner ailleurs que sur un système Microsoft ni l'inclure dans un programme malveillant.
- **Pour l'utilisateur** : en utilisant MyPhotos.exe, vous acceptez que ces composants Microsoft (runtime Visual C++, DirectML) restent soumis aux conditions de Microsoft rappelées dans ce document.
- **Lien** : [Redistribuer les fichiers Visual C++](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files).

## PyInstaller

Outil de construction : il réunit Python, les bibliothèques et MyPhotos en un seul exécutable. Seuls son **chargeur** (« bootloader ») et de petits modules d'exécution se retrouvent dans MyPhotos.exe.

- **Licence** : GPL-2.0-or-later, **avec l'exception du chargeur** :

  > In addition to the permissions in the GNU General Public License, the authors give you unlimited permission to link or embed compiled bootloader and related files into combinations with other programs, and to distribute those combinations without any restriction coming from the use of those files. (The General Public License restrictions do apply in other respects; for example, they cover modification of the files, and distribution when not linked into a combined executable.)

  Les « run-time hooks » et modules d'exécution embarqués (`PyInstaller/hooks/rthooks`, `PyInstaller/fake-modules`) sont sous Apache-2.0, comme les « run-time hooks » de `pyinstaller-hooks-contrib`.
- **Obligations** : aucune contrainte de la GPL sur MyPhotos.exe ; joindre `COPYING.txt` par courtoisie et pour les parties Apache-2.0. Le chargeur contient zlib (licence Zlib).
- **Liens** : [pyinstaller.org](https://pyinstaller.org), [COPYING.txt](https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt).

## Le moteur de reconnaissance (SigLIP 2)

**Il n'est pas dans MyPhotos.exe.** Au premier lancement, MyPhotos le télécharge directement depuis Hugging Face, sur l'ordinateur de l'utilisateur, à des révisions figées dont chaque fichier est contrôlé par son empreinte SHA-256. Le projet MyPhotos ne redistribue donc pas ces fichiers.

- **Modèles d'origine** : SigLIP 2 de Google, licence **Apache-2.0** ([article](https://arxiv.org/abs/2502.14786)) :
  - Rapide : [google/siglip2-base-patch32-256](https://huggingface.co/google/siglip2-base-patch32-256) ;
  - Standard : [google/siglip2-base-patch16-224](https://huggingface.co/google/siglip2-base-patch16-224) ;
  - Précis : [google/siglip2-large-patch16-256](https://huggingface.co/google/siglip2-large-patch16-256) ;
  - Très précis : [google/siglip2-so400m-patch16-256](https://huggingface.co/google/siglip2-so400m-patch16-256).
- **Fichiers téléchargés** : les exports ONNX publiés par onnx-community (`siglip2-base-patch32-256-ONNX`, `siglip2-base-patch16-224-ONNX`, `siglip2-large-patch16-256-ONNX`, `siglip2-so400m-patch16-256-ONNX`), par exemple [onnx-community/siglip2-base-patch16-224-ONNX](https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX). Ces dépôts ne déclarent pas de licence propre ; ce sont des conversions de format des modèles de Google, qui restent soumis à l'Apache-2.0. Les révisions et empreintes figées sont dans `myphotos/modele.py`.
- **Si vous redistribuez un dossier `modele`** (installation sans Internet) : joignez la licence Apache-2.0 et indiquez l'origine des fichiers (Google, conversion onnx-community).

## Composants propres à MyPhotos

Le code Python du paquet `myphotos`, la page `page/index.html` et l'icône (dessinée par `construction/icone.py`) relèvent de la licence MIT de MyPhotos. La page n'utilise aucune police, bibliothèque ni ressource externe.
