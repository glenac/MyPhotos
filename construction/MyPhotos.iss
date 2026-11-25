; MyPhotos.iss : installateur Windows de MyPhotos (Inno Setup 6.7), pour l'utilisateur seul, sans droits
; d'administrateur, dans %LOCALAPPDATA%\Programs\MyPhotos.
;
; Il pose la version « dossier » de PyInstaller (dist\MyPhotos\MyPhotos.exe + _internal\), déjà construite et
; essayée, avec LICENSE, THIRD_PARTY_NOTICES.md, le mode d'emploi en PDF et les licences des composants
; (dist\licences\). D'ordinaire, c'est construire.py qui le compile :
;
;   construction\construire.ps1 --dossier          -> dist\MyPhotos\  (à essayer)
;   construction\construire.ps1 --installateur     -> dist\MyPhotos-<version>-installation.exe (+ .sha256)
;
; À la main : ISCC.exe /DMyAppVersion=<version> construction\MyPhotos.iss
;   /DRacine=<dépôt> /DDossierApp=<dist\MyPhotos> /DDossierLicences=<dist\licences> /DSortie=<dist>
;   (facultatifs : par défaut, les emplacements du dépôt, à partir de ce fichier rangé dans construction\)
;
; Installation silencieuse   : MyPhotos-<version>-installation.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
;                              [/MERGETASKS="!desktopicon"] (sans icône sur le Bureau)
;                              [/NOICONS] (sans raccourci dans le menu Démarrer)
; Désinstallation silencieuse : "%LOCALAPPDATA%\Programs\MyPhotos\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES
;                              garde l'analyse des photos et le moteur ; /SUPPRIMERDONNEES les supprime aussi.
;
; Ne jamais changer AppId : c'est lui qui relie les versions entre elles (mise à jour par-dessus,
; désinstallation). AppMutex doit rester le nom du mutex créé par MyPhotos au démarrage (config.MUTEX, dans
; myphotos/config.py ; tests/test_construction.py vérifie qu'ils sont égaux).
;
; Depuis la 0.3.0, MyPhotos s'ouvre dans sa propre fenêtre (pywebview et WebView2) : fermer la fenêtre arrête
; MyPhotos et libère le mutex. Le profil WebView2 est rangé dans le dossier de données (webview2\), jamais à côté
; de MyPhotos.exe ; celui du repli sur Edge aussi (edge\).

#if VER < EncodeVer(6, 7, 0)
  #error Inno Setup 6.7 ou plus récent est nécessaire pour compiler MyPhotos.iss
#endif

#ifndef MyAppVersion
  #error Version manquante : ISCC /DMyAppVersion=<version de MyPhotos> MyPhotos.iss
#endif
#ifndef Racine
  #define Racine AddBackslash(SourcePath) + ".."
#endif
#ifndef DossierApp
  #define DossierApp Racine + "\dist\MyPhotos"
#endif
#ifndef DossierLicences
  #define DossierLicences Racine + "\dist\licences"
#endif
#ifndef Sortie
  #define Sortie Racine + "\dist"
#endif
#ifndef Icone
  #define Icone Racine + "\construction\MyPhotos.ico"
#endif
; Longueur du plus long chemin posé sous {app}, y compris le nom provisoire (17 caractères) sous lequel Inno Setup
; copie chaque fichier à côté de lui (0.3.0 : 99 caractères, pour un texte de licence de NumPy de 89 caractères dans
; _internal). Sans chemins longs, Windows refuse au-delà de 259 caractères : un dossier d'installation trop long
; (/DIR) est refusé avant toute copie. construire.py --installateur la calcule et la passe à ISCC. (Essai adverse :
; sans le nom provisoire, un dossier de 160 caractères était accepté puis la copie échouait au milieu.)
#ifndef CheminLePlusLong
  #define CheminLePlusLong "100"
#endif

#define AppExe DossierApp + "\MyPhotos.exe"
#if !FileExists(AppExe) || !DirExists(DossierApp + "\_internal")
  #error MyPhotos.exe ou _internal absent de DossierApp : construisez d'abord la version dossier (construire.ps1 --dossier)
#endif
#if GetStringFileInfo(AppExe, "ProductVersion") != MyAppVersion
  #error La version de MyPhotos.exe (ressource de version) ne correspond pas à MyAppVersion
#endif
#if !FileExists(DossierLicences + "\LISEZMOI.txt")
  #error Licences des composants absentes de DossierLicences (construire.ps1 --installateur les rassemble)
#endif
; Version numérique (a.b.c.d) de l'installateur : celle de MyPhotos.exe.
#define MyAppVersionNum GetVersionNumbersString(AppExe)

; Identifiants permanents. Le mutex est créé par MyPhotos (0.3.0 et suivantes) pendant qu'il tourne.
; Seul l'essai réel de tests/test_construction.py les remplace (/DMyAppId=…, /DMyAppMutex=…), pour ne jamais
; toucher à une vraie installation de MyPhotos sur le PC d'essai.
#ifndef MyAppId
#define MyAppId "25DE0724-F7B4-4956-8238-1E33E16ABFED"
#endif
#ifndef MyAppMutex
#define MyAppMutex "MyPhotos-25DE0724-F7B4-4956-8238-1E33E16ABFED"
#endif

[Setup]
AppId={{{#MyAppId}}
AppName=MyPhotos
AppVersion={#MyAppVersion}
AppVerName=MyPhotos {#MyAppVersion}
AppPublisher=glenac
AppPublisherURL=https://github.com/glenac/MyPhotos
AppSupportURL=https://github.com/glenac/MyPhotos/issues
AppUpdatesURL=https://github.com/glenac/MyPhotos/releases
AppCopyright=Copyright (c) 2026 glenac (MIT)
VersionInfoVersion={#MyAppVersionNum}
VersionInfoProductVersion={#MyAppVersionNum}
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoCompany=glenac
VersionInfoDescription=Installation de MyPhotos
VersionInfoProductName=MyPhotos
VersionInfoCopyright=Copyright (c) 2026 glenac (MIT)
VersionInfoOriginalFileName=MyPhotos-{#MyAppVersion}-installation.exe
; Pour l'utilisateur seul, jamais de droits d'administrateur : {autopf} = %LOCALAPPDATA%\Programs.
PrivilegesRequired=lowest
DefaultDirName={autopf}\MyPhotos
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 version 1809, comme MyPhotos.exe (README).
MinVersion=10.0.17763
; Assistant réduit pour des novices : la page des raccourcis, puis l'installation, puis la fin.
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
; Sans page « Dossier du menu Démarrer », mais /NOICONS doit pouvoir éviter le raccourci du menu Démarrer.
AllowNoIcons=yes
DisableReadyPage=yes
WizardStyle=modern
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage
SetupIconFile={#Icone}
UninstallDisplayIcon={app}\MyPhotos.exe
UninstallDisplayName=MyPhotos
; MyPhotos ouvert : Setup et Uninstall demandent de le fermer (mutex) ; à défaut, le Gestionnaire de
; redémarrage de Windows ferme ce qui retient les fichiers à remplacer (sans relancer après).
AppMutex={#MyAppMutex}
SetupMutex=MyPhotos-installation-{#MyAppId}
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
OutputDir={#Sortie}
OutputBaseFilename=MyPhotos-{#MyAppVersion}-installation

[Languages]
; L'anglais d'abord : c'est la langue retenue quand Windows n'est ni en français ni en anglais.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Messages]
; Boîtes de message : lignes courtes, coupées à la main (%n), pour ne pas dépendre de la coupure automatique.
english.WizardSelectTasks=Shortcuts
english.SelectTasksDesc=Where would you like to find MyPhotos?
english.SelectTasksLabel2=[name] will be added to the Start menu. It is installed for you only and needs no administrator rights. Your photos stay where they are.%n%nClick Install to continue.
english.FinishedLabel=[name] is installed. You will find it in the Start menu and, if you asked for it, on the desktop.
english.SetupAppRunningError=MyPhotos is open.%n%nClose MyPhotos, then click OK.%nOr click Cancel to install later.
english.UninstallAppRunningError=MyPhotos is open.%n%nClose MyPhotos, then click OK.%nOr click Cancel to uninstall later.
english.ConfirmUninstall=Do you want to uninstall %1?%nYour photos will not be touched.
french.WizardSelectTasks=Raccourcis
french.SelectTasksDesc=Où voulez-vous retrouver MyPhotos ?
french.SelectTasksLabel2=[name] sera ajouté au menu Démarrer. Il s'installe pour vous seul, sans droits d'administrateur. Vos photos restent où elles sont.%n%nCliquez sur Installer pour continuer.
french.FinishedLabel=[name] est installé. Vous le retrouverez dans le menu Démarrer et, si vous l'avez demandé, sur le Bureau.
french.SetupAppRunningError=MyPhotos est ouvert.%n%nFermez MyPhotos, puis cliquez sur OK.%nOu cliquez sur Annuler pour installer plus tard.
french.UninstallAppRunningError=MyPhotos est ouvert.%n%nFermez MyPhotos, puis cliquez sur OK.%nOu cliquez sur Annuler pour désinstaller plus tard.
french.ConfirmUninstall=Voulez-vous désinstaller %1 ?%nVos photos ne seront pas touchées.

[CustomMessages]
english.CreateDesktopIcon=Put a MyPhotos icon on the &desktop
english.LancerMyPhotos=Start MyPhotos
english.CommentaireRaccourci=Find your photos by typing what they show
english.QuestionDonnees=Also delete the analysis of your photos%nand the downloaded engine (%1)?%n%nYour photos are never touched.
english.DonneesPasToutes=Some MyPhotos files could not be deleted.%nClose MyPhotos, then delete this folder yourself:%n%n%1
english.DossierOccupe=The folder %1 already contains another program. Install MyPhotos in another folder.
english.DossierTropLong=The path of the folder %1 is too long for MyPhotos. Install MyPhotos in a folder with a shorter path.
english.TailleGo=%1 GB
english.TailleMo=%1 MB
english.TailleKo=%1 KB
english.Virgule=.
french.CreateDesktopIcon=Mettre une icône MyPhotos sur le &Bureau
french.LancerMyPhotos=Lancer MyPhotos
french.CommentaireRaccourci=Retrouvez vos photos en tapant ce qu'elles montrent
french.QuestionDonnees=Supprimer aussi l'analyse de vos photos%net le moteur téléchargé (%1) ?%n%nVos photos ne sont jamais touchées.
french.DonneesPasToutes=Certains fichiers de MyPhotos n'ont pas pu être supprimés.%nFermez MyPhotos, puis supprimez vous-même ce dossier :%n%n%1
french.DossierOccupe=Le dossier %1 contient déjà un autre programme. Installez MyPhotos dans un autre dossier.
french.DossierTropLong=Le chemin du dossier %1 est trop long pour MyPhotos. Installez MyPhotos dans un dossier au chemin plus court.
french.TailleGo=%1 Go
french.TailleMo=%1 Mo
french.TailleKo=%1 Ko
french.Virgule=,

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"

[InstallDelete]
; Mise à jour : les fichiers de l'ancienne version, même ceux que la nouvelle n'a plus, sont retirés avant
; la copie (MyPhotos est fermé à ce moment-là), mais SEULEMENT dans le dossier d'une installation de MyPhotos
; (InstallationDeMyPhotos). Un dossier existant choisi par /DIR garde ses propres « licences » et « _internal »
; (essai de la 0.3.0 : /DIR vers un dossier de l'utilisateur vidait son sous-dossier « licences »).
Type: filesandordirs; Name: "{app}\_internal"; Check: InstallationDeMyPhotos
Type: filesandordirs; Name: "{app}\licences"; Check: InstallationDeMyPhotos

[Files]
Source: "{#DossierApp}\MyPhotos.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DossierApp}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Racine}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Racine}\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Racine}\docs\MyPhotos_mode_emploi.pdf"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DossierLicences}\*"; DestDir: "{app}\licences"; Excludes: ".cree-par-construire"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Un seul raccourci, sans dossier, dans le menu Démarrer ; /NOICONS l'évite (installations automatiques).
Name: "{autoprograms}\MyPhotos"; Filename: "{app}\MyPhotos.exe"; WorkingDir: "{app}"; Comment: "{cm:CommentaireRaccourci}"; Check: not WizardNoIcons
Name: "{autodesktop}\MyPhotos"; Filename: "{app}\MyPhotos.exe"; WorkingDir: "{app}"; Comment: "{cm:CommentaireRaccourci}"; Tasks: desktopicon

[Run]
Filename: "{app}\MyPhotos.exe"; WorkingDir: "{app}"; Description: "{cm:LancerMyPhotos}"; Flags: nowait postinstall skipifsilent
; Mise à jour lancée par MyPhotos lui-même (/MISEAJOUR, myphotos/mise_a_jour.py) : MyPhotos est rouvert à la fin,
; même en silence.
Filename: "{app}\MyPhotos.exe"; WorkingDir: "{app}"; Flags: nowait; Check: ParametrePresent('/MISEAJOUR')

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
; Par précaution : le dossier que WebView2 crée à côté de l'exécutable quand on ne lui en donne pas (MyPhotos lui
; donne toujours DONNEES\webview2 ; essai de la 0.3.0 : rien n'est écrit à côté de MyPhotos.exe).
Type: filesandordirs; Name: "{app}\MyPhotos.exe.WebView2"
Type: dirifempty; Name: "{app}"

[Code]
{ ------------------------------------------------------------------------------------------------------------
  Désinstallation et données de MyPhotos.

  MyPhotos range l'analyse des photos (index.db), les miniatures (miniatures.db), son journal, le moteur
  téléchargé (modele\), la place de sa fenêtre (fenetre.json) et les profils de la fenêtre (webview2\ pour
  WebView2, edge\ pour le repli sur Edge) dans son dossier de données : %LOCALAPPDATA%\MyPhotos, ou
  MYPHOTOS_DOSSIER s'il est défini (comme myphotos/config.py ; MYPHOTOS_MODELES n'est jamais touché). Une fois
  le programme désinstallé, on propose de supprimer ces données, Non par défaut. Garde-fous :
  - chemin complet, sans « .. » ; ni racine de disque, ni dossier de l'utilisateur ou du système (ni l'un de
    leurs parents) : profil, AppData, LOCALAPPDATA, Bureau, Documents, Images, Vidéos, Musique, OneDrive,
    TEMP, Windows, Program Files, ProgramData, dossier d'installation ;
  - le dossier contient index.db ou le marqueur de MyPhotos, et n'est pas lui-même un lien ;
  - seuls les éléments que MyPhotos y crée (NomDeMyPhotos, noms exacts) sont supprimés : tout autre fichier est
    gardé, et le dossier avec ; aucun lien (jonction, lien symbolique) n'est jamais suivi (DelTree retire le lien
    lui-même sans descendre dedans) ;
  - les dossiers modele\, webview2\ et edge\ ne partent que si leur contenu est bien celui de MyPhotos
    (ContenuDeMyPhotos) : des photos rangées par l'utilisateur dans un « modele » ou un « Edge » restent.
  Les dossiers protégés sont reconnus par leur chemin écrit, puis par leur identité (volume et numéro de fichier,
  comme os.stat : st_dev, st_ino) : un autre chemin vers le même dossier (nom court 8.3, partage \\localhost\C$,
  jonction parente) est reconnu aussi (0.4.0 ; en 0.3.0, seul le chemin écrit comptait).
  Désinstallation silencieuse : rien n'est demandé et les données sont gardées, sauf avec /SUPPRIMERDONNEES.

  Mise à jour lancée par MyPhotos (/MISEAJOUR) : InitializeSetup attend que MyPhotos se soit fermé, puis [Run] le
  rouvre à la fin.
  ------------------------------------------------------------------------------------------------------------ }

const
  MARQUEUR = 'myphotos-donnees.txt';
  CSIDL_MYMUSIC = $000D;
  CSIDL_MYVIDEO = $000E;
  CSIDL_MYPICTURES = $0027;
  FILE_SHARE_TOUT = 7;                       { lecture, écriture et suppression : n'empêche personne }
  OPEN_EXISTING = 3;
  FILE_FLAG_BACKUP_SEMANTICS = $02000000;    { nécessaire pour ouvrir un dossier }

type
  TIdentite = record
    Volume, Haut, Bas: DWORD;
  end;
  { BY_HANDLE_FILE_INFORMATION }
  TInfosFichier = record
    Attributs: DWORD;
    Creation1, Creation2, Acces1, Acces2, Ecriture1, Ecriture2: DWORD;
    Volume: DWORD;
    TailleHaut, TailleBas, Liens, IndexHaut, IndexBas: DWORD;
  end;

function CreateFileW(Nom: String; Acces, Partage: DWORD; Securite: DWORD; Disposition, Drapeaux: DWORD;
  Modele: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';     { publication: ok (fonction de Windows, pas une adresse) }
function GetFileInformationByHandle(Fichier: THandle; var Infos: TInfosFichier): BOOL;
  external 'GetFileInformationByHandle@kernel32.dll stdcall';     { publication: ok }
function CloseHandle(Objet: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';     { publication: ok }

{ Vrai si ce nom, à la racine du dossier de données, est un élément que MyPhotos crée. Noms exacts : un fichier de
  l'utilisateur au nom voisin (« Journal.log.txt », « index.db.sauvegarde ») est gardé. À compléter si MyPhotos
  range un jour autre chose à cet endroit (sinon cet élément est simplement gardé, et le dossier avec).
  tests/test_construction.py relève les noms d'une instance neuve (fenêtre WebView2, repli Edge) et les compare
  à cette liste.
  - modele\ : moteur téléchargé (config.MODELES) ;
  - index.db, miniatures.db, leurs fichiers de SQLite (-wal, -shm, -journal) et leurs copies abîmées mises de
    côté (« index.db.abimee-AAAAMMJJ-HHMMSS », avec -wal et -shm : base.py, _ecarter_abimes) ;
  - journal.log et ses copies (journal.log.1, .2 : serveur.JOURNAL_COPIES) ;
  - fenetre.json (et fenetre.json.part pendant son écriture) : taille et place de la fenêtre ;
  - webview2\ : profil WebView2 de la fenêtre (mode privé : effacé à la fermeture, reste après un plantage) ;
  - edge\ : profil d'Edge en mode application (repli quand WebView2 manque).
  Les trois dossiers ne sont supprimés que si leur contenu est bien celui de MyPhotos (ContenuDeMyPhotos). }
function NomDeMyPhotos(const Nom: String): Boolean;
var
  N: String;
begin
  N := Lowercase(Nom);
  Result := (N = 'modele') or (N = MARQUEUR)
    or (N = 'index.db') or (N = 'index.db-wal') or (N = 'index.db-shm') or (N = 'index.db-journal')
    or WildcardMatch(N, 'index.db.abimee-*')
    or (N = 'miniatures.db') or (N = 'miniatures.db-wal') or (N = 'miniatures.db-shm')
    or (N = 'miniatures.db-journal') or WildcardMatch(N, 'miniatures.db.abimee-*')
    or (N = 'journal.log') or (N = 'journal.log.1') or (N = 'journal.log.2')
    or (N = 'fenetre.json') or (N = 'fenetre.json.part')
    or (N = 'webview2') or (N = 'edge');
end;

function ParametrePresent(const Nom: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), Nom) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

{ Mise à jour lancée par MyPhotos (/MISEAJOUR, myphotos/mise_a_jour.py) : MyPhotos se ferme juste après avoir
  lancé l'installateur. On attend ici qu'il ait fini (son mutex disparaît), 60 secondes au plus, avant la
  vérification AppMutex d'Inno Setup, qui sinon abandonnerait en silence (/SUPPRESSMSGBOXES). Essai du 25/11/2026
  (Inno Setup 6.7.4) : InitializeSetup passe bien avant cette vérification. }
function InitializeSetup(): Boolean;
var
  i: Integer;
begin
  if ParametrePresent('/MISEAJOUR') then
  begin
    i := 0;
    while CheckForMutexes('{#MyAppMutex}') and (i < 240) do
    begin
      Sleep(250);
      i := i + 1;
    end;
    Log(Format('Mise à jour lancée par MyPhotos : %d ms d''attente de sa fermeture.', [i * 250]));
  end;
  Result := True;
end;

{ Identité d'un dossier (volume, numéro de fichier), en suivant les jonctions comme Windows : deux chemins écrits
  différemment vers le même dossier ont la même identité. False si le dossier n'existe pas ou ne s'ouvre pas. }
function Identite(const Chemin: String; var Id: TIdentite): Boolean;
var
  H: THandle;
  I: TInfosFichier;
begin
  Result := False;
  H := CreateFileW(Chemin, 0, FILE_SHARE_TOUT, 0, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, 0);
  if (H = 0) or (H = THandle(-1)) then
    Exit;
  try
    if GetFileInformationByHandle(H, I) then
    begin
      Id.Volume := I.Volume;
      Id.Haut := I.IndexHaut;
      Id.Bas := I.IndexBas;
      Result := True;
    end;
  finally
    CloseHandle(H);
  end;
end;

{ Comme ParentOuEgal, par identité : vrai si le dossier d'identité IdDossier est Protege ou l'un de ses parents.
  On remonte les parents de Protege (écrit normalement) et on compare chacun au dossier de données, quel que soit
  le chemin par lequel celui-ci a été donné (C:\PROGRA~1\…, \\localhost\C$\Users\…, jonction). }
function ParentOuEgalIdentite(const Protege: String; const IdDossier: TIdentite): Boolean;
var
  P, Parent: String;
  Id: TIdentite;
  n: Integer;
begin
  Result := False;
  if Protege = '' then
    Exit;
  P := RemoveBackslashUnlessRoot(Protege);
  for n := 1 to 64 do
  begin
    if Identite(P, Id) and (Id.Volume = IdDossier.Volume) and (Id.Haut = IdDossier.Haut)
       and (Id.Bas = IdDossier.Bas) then
    begin
      Result := True;
      Exit;
    end;
    Parent := RemoveBackslashUnlessRoot(ExtractFileDir(P));
    if (Parent = '') or (CompareText(Parent, P) = 0) then
      Exit;
    P := Parent;
  end;
end;

{ « X:\... » ou « \\serveur\partage\... » : jamais un chemin relatif, ni « \\?\ » ou « \\.\ ». }
function CheminComplet(const S: String): Boolean;
begin
  Result := ((Length(S) >= 3) and (S[2] = ':') and ((S[3] = '\') or (S[3] = '/'))
             and (((S[1] >= 'A') and (S[1] <= 'Z')) or ((S[1] >= 'a') and (S[1] <= 'z'))))
         or ((Length(S) >= 5) and (Copy(S, 1, 2) = '\\') and (S[3] <> '?') and (S[3] <> '.'));
end;

function EstRacine(const Dossier: String): Boolean;
begin
  Result := (RemoveBackslash(Dossier) = '')
         or PathSame(RemoveBackslash(Dossier), RemoveBackslash(ExtractFileDrive(Dossier)));
end;

{ Vrai si Dossier est Protege ou l'un de ses parents. }
function ParentOuEgal(const Dossier, Protege: String): Boolean;
begin
  Result := (Protege <> '') and PathStartsWith(AddBackslash(Protege), AddBackslash(Dossier), True);
end;

procedure Ajouter(var Liste: TArrayOfString; const Chemin: String);
var
  n: Integer;
begin
  if Chemin = '' then
    Exit;
  n := GetArrayLength(Liste);
  SetArrayLength(Liste, n + 1);
  Liste[n] := RemoveBackslashUnlessRoot(ExpandFileName(Chemin));
end;

function DossierProtege(const Dossier: String; var Lequel: String): Boolean;
var
  Liste: TArrayOfString;
  i: Integer;
  IdDossier: TIdentite;
begin
  Ajouter(Liste, GetEnv('USERPROFILE'));
  Ajouter(Liste, ExpandConstant('{localappdata}'));
  Ajouter(Liste, ExpandConstant('{userappdata}'));
  Ajouter(Liste, ExpandConstant('{userpf}'));
  Ajouter(Liste, ExpandConstant('{userdesktop}'));
  Ajouter(Liste, ExpandConstant('{userdocs}'));
  Ajouter(Liste, GetShellFolderByCSIDL(CSIDL_MYPICTURES, False));
  Ajouter(Liste, GetShellFolderByCSIDL(CSIDL_MYVIDEO, False));
  Ajouter(Liste, GetShellFolderByCSIDL(CSIDL_MYMUSIC, False));
  Ajouter(Liste, GetEnv('OneDrive'));
  Ajouter(Liste, GetEnv('TEMP'));
  Ajouter(Liste, ExpandConstant('{win}'));
  Ajouter(Liste, ExpandConstant('{sys}'));
  Ajouter(Liste, ExpandConstant('{commonpf32}'));
  Ajouter(Liste, ExpandConstant('{commonpf64}'));
  Ajouter(Liste, ExpandConstant('{commonappdata}'));
  Ajouter(Liste, ExpandConstant('{app}'));
  Result := False;
  for i := 0 to GetArrayLength(Liste) - 1 do
    if ParentOuEgal(Dossier, Liste[i]) then
    begin
      Lequel := Liste[i];
      Result := True;
      Exit;
    end;
  { Même dossier écrit autrement (nom court 8.3, \\localhost\C$, jonction) : par identité. }
  if Identite(Dossier, IdDossier) then
    for i := 0 to GetArrayLength(Liste) - 1 do
      if ParentOuEgalIdentite(Liste[i], IdDossier) then
      begin
        Lequel := Liste[i];
        Result := True;
        Exit;
      end;
end;

{ Attributs de l'élément lui-même (sans le suivre si c'est un lien) ; -1 s'il n'existe pas. }
function Attributs(const Chemin: String): Int64;
var
  R: TFindRec;
begin
  Result := -1;
  if FindFirst(Chemin, R) then
  try
    Result := R.Attributes;
  finally
    FindClose(R);
  end;
end;

function FichierOrdinaire(const Chemin: String): Boolean;
var
  A: Int64;
begin
  A := Attributs(Chemin);
  Result := (A >= 0) and ((A and (FILE_ATTRIBUTE_DIRECTORY or FILE_ATTRIBUTE_REPARSE_POINT)) = 0);
end;

{ Fichiers du moteur dans modele\<clé>\ (myphotos/modele.py et serveur.py) : tokenizer.json, onnx\*.onnx, leurs
  témoins .ok, et .part (téléchargement) ou .copie (recopie sans Internet) quand c'est interrompu. }
function FichierDuMoteur(const Nom: String): Boolean;
var
  E: String;
begin
  E := Lowercase(ExtractFileExt(Nom));
  Result := (E = '.onnx') or (E = '.json') or (E = '.ok') or (E = '.part') or (E = '.copie');
end;

{ Vrai si l'arbre ne contient que des fichiers du moteur : ni lien, ni aucun autre fichier (photo, document…). }
function SeulementLeMoteur(const Dossier: String): Boolean;
var
  R: TFindRec;
begin
  Result := True;
  if FindFirst(Dossier + '\*', R) then
  try
    repeat
      if (R.Name <> '.') and (R.Name <> '..') then
      begin
        if (R.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
          Result := False
        else if (R.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          Result := SeulementLeMoteur(Dossier + '\' + R.Name)
        else
          Result := FichierDuMoteur(R.Name);
      end;
    until (not Result) or (not FindNext(R));
  finally
    FindClose(R);
  end;
end;

{ Vrai si le dossier ne contient que Nom, un sous-dossier ordinaire (ou rien du tout). }
function SeulementLeSousDossier(const Dossier, Nom: String): Boolean;
var
  R: TFindRec;
begin
  Result := True;
  if FindFirst(Dossier + '\*', R) then
  try
    repeat
      if (R.Name <> '.') and (R.Name <> '..') then
        Result := (CompareText(R.Name, Nom) = 0) and ((R.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0)
                  and ((R.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0);
    until (not Result) or (not FindNext(R));
  finally
    FindClose(R);
  end;
end;

{ Un dossier au nom de MyPhotos (modele, webview2, edge) n'est supprimé que si son contenu est bien le sien. Sinon,
  il appartient à l'utilisateur (des photos rangées dans un « modele », un « Edge »…) : il est gardé, sans rien
  dire (comme tout autre fichier du dossier de données). Un lien n'est jamais examiné ici : DelTree le retire
  sans descendre dedans.
  - modele\ : seulement des fichiers du moteur (SeulementLeMoteur) ;
  - webview2\ : WebView2 n'y crée que EBWebView\ ;
  - edge\ : un profil d'Edge commence par son fichier « Local State » ;
  - vide : supprimé. }
function DossierVide(const Dossier: String): Boolean;
begin
  Result := SeulementLeSousDossier(Dossier, '');      { aucun élément ne s'appelle '' : vrai seulement s'il est vide }
end;

function ContenuDeMyPhotos(const Chemin, Nom: String): Boolean;
var
  N: String;
begin
  N := Lowercase(Nom);
  if N = 'modele' then
    Result := SeulementLeMoteur(Chemin)
  else if N = 'webview2' then
    Result := SeulementLeSousDossier(Chemin, 'EBWebView')
  else if N = 'edge' then
    Result := FichierOrdinaire(Chemin + '\Local State') or DossierVide(Chemin)
  else
    Result := DossierVide(Chemin);                     { autre nom de MyPhotos en dossier : seulement s'il est vide }
end;

{ Dossier de données de MyPhotos s'il peut être proposé à la suppression, sinon '' et la raison. }
function DossierDonnees(var Raison: String): String;
var
  S, Lequel: String;
  A: Int64;
begin
  Result := '';
  S := GetEnv('MYPHOTOS_DOSSIER');
  if S = '' then
    S := ExpandConstant('{localappdata}\MyPhotos')
  else if not CheminComplet(S) then
  begin
    Raison := 'MYPHOTOS_DOSSIER n''est pas un chemin complet : ' + S;
    Exit;
  end;
  if PathHasInvalidCharacters(S, True) then
  begin
    Raison := 'chemin refusé (caractère interdit ou « .. ») : ' + S;
    Exit;
  end;
  S := RemoveBackslashUnlessRoot(ExpandFileName(S));
  if EstRacine(S) then
  begin
    Raison := 'racine de disque refusée : ' + S;
    Exit;
  end;
  if DossierProtege(S, Lequel) then
  begin
    Raison := 'dossier protégé refusé : ' + S + ' (' + Lequel + ')';
    Exit;
  end;
  A := Attributs(S);
  if A < 0 then
  begin
    Raison := 'dossier absent : ' + S;
    Exit;
  end;
  if (A and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
  begin
    Raison := 'lien (jonction ou lien symbolique) refusé, jamais suivi : ' + S;
    Exit;
  end;
  if (A and FILE_ATTRIBUTE_DIRECTORY) = 0 then
  begin
    Raison := 'ce n''est pas un dossier : ' + S;
    Exit;
  end;
  if not (FichierOrdinaire(S + '\index.db') or FichierOrdinaire(S + '\' + MARQUEUR)) then
  begin
    Raison := 'ni index.db ni marqueur de MyPhotos : ' + S;
    Exit;
  end;
  Result := S;
end;

{ Taille d'un dossier, sans jamais suivre un lien. }
function TailleArbre(const Dossier: String): Int64;
var
  R: TFindRec;
  T: Int64;
begin
  Result := 0;
  if FindFirst(Dossier + '\*', R) then
  try
    repeat
      if (R.Name <> '.') and (R.Name <> '..') and ((R.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) then
      begin
        if (R.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          Result := Result + TailleArbre(Dossier + '\' + R.Name)
        else
        begin
          T := R.SizeHigh;
          Result := Result + T * 65536 * 65536 + R.SizeLow;
        end;
      end;
    until not FindNext(R);
  finally
    FindClose(R);
  end;
end;

{ Taille des seuls éléments de MyPhotos (ce qui serait supprimé). }
function TailleDonnees(const Dossier: String): Int64;
var
  R: TFindRec;
  T: Int64;
begin
  Result := 0;
  if FindFirst(Dossier + '\*', R) then
  try
    repeat
      if (R.Name <> '.') and (R.Name <> '..') and NomDeMyPhotos(R.Name)
         and ((R.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) = 0) then
      begin
        if (R.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          if ContenuDeMyPhotos(Dossier + '\' + R.Name, R.Name) then
            Result := Result + TailleArbre(Dossier + '\' + R.Name);
        end
        else
        begin
          T := R.SizeHigh;
          Result := Result + T * 65536 * 65536 + R.SizeLow;
        end;
      end;
    until not FindNext(R);
  finally
    FindClose(R);
  end;
end;

{ Attention : dans [Code], une ligne qui commence par « [ » est lue comme un nom de section. }
function TailleLisible(Octets: Int64): String;
var
  Dixiemes: Int64;
  Nombre: String;
begin
  if Octets >= 1073741824 then
  begin
    Dixiemes := (Octets * 10 + 536870912) div 1073741824;
    Nombre := IntToStr(Dixiemes div 10) + CustomMessage('Virgule') + IntToStr(Dixiemes mod 10);
    Result := FmtMessage(CustomMessage('TailleGo'), [Nombre]);
  end
  else if Octets >= 1048576 then
    Result := FmtMessage(CustomMessage('TailleMo'), [IntToStr((Octets + 524288) div 1048576)])
  else
    Result := FmtMessage(CustomMessage('TailleKo'), [IntToStr((Octets + 1023) div 1024)]);
end;

{ Supprime les éléments de MyPhotos du dossier, puis le dossier s'il est vide. Faux si l'un d'eux résiste. }
function SupprimerDonnees(const Dossier: String): Boolean;
var
  R: TFindRec;
  Noms: TArrayOfString;
  i, n: Integer;
  Chemin: String;
  A: Int64;
begin
  Result := True;
  n := 0;
  if FindFirst(Dossier + '\*', R) then
  try
    repeat
      if (R.Name <> '.') and (R.Name <> '..') and NomDeMyPhotos(R.Name) then
      begin
        SetArrayLength(Noms, n + 1);
        Noms[n] := R.Name;
        n := n + 1;
      end;
    until not FindNext(R);
  finally
    FindClose(R);
  end;
  for i := 0 to n - 1 do
  begin
    Chemin := Dossier + '\' + Noms[i];
    A := Attributs(Chemin);
    if A < 0 then
      Continue;
    if (A and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
    begin
      if ((A and FILE_ATTRIBUTE_REPARSE_POINT) = 0) and not ContenuDeMyPhotos(Chemin, Noms[i]) then
      begin
        Log('Données de MyPhotos : gardé, ce n''est pas le contenu de MyPhotos : ' + Chemin);
        Continue;
      end;
      { dossier de MyPhotos, ou lien vers un dossier : DelTree retire un lien sans jamais descendre dedans }
      if not DelTree(Chemin, True, True, True) then
        Result := False;
    end
    else if not DeleteFile(Chemin) then
      Result := False;
    Log('Données de MyPhotos : ' + Chemin + ' -> ' + IntToStr(Attributs(Chemin)));
  end;
  if RemoveDir(Dossier) then
    Log('Données de MyPhotos : dossier supprimé : ' + Dossier)
  else
    Log('Données de MyPhotos : dossier gardé (il contient autre chose) : ' + Dossier);
end;

procedure ProposerSuppressionDonnees();
var
  Dossier, Raison: String;
  Taille: Int64;
  Supprimer: Boolean;
begin
  Dossier := DossierDonnees(Raison);
  if Dossier = '' then
  begin
    Log('Données de MyPhotos : rien à proposer (' + Raison + ')');
    Exit;
  end;
  Taille := TailleDonnees(Dossier);
  Log('Données de MyPhotos : ' + Dossier + ', ' + IntToStr(Taille) + ' octets');
  if ParametrePresent('/SUPPRIMERDONNEES') then
  begin
    Log('Données de MyPhotos : suppression demandée par /SUPPRIMERDONNEES');
    Supprimer := True;
  end
  else if UninstallSilent then
  begin
    Log('Données de MyPhotos : désinstallation silencieuse, données gardées');
    Supprimer := False;
  end
  else
    Supprimer := MsgBox(FmtMessage(CustomMessage('QuestionDonnees'), [TailleLisible(Taille)]),
                        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  if not Supprimer then
  begin
    Log('Données de MyPhotos : gardées');
    Exit;
  end;
  if SupprimerDonnees(Dossier) then
    Log('Données de MyPhotos : supprimées')
  else
  begin
    Log('Données de MyPhotos : suppression incomplète');
    SuppressibleMsgBox(FmtMessage(CustomMessage('DonneesPasToutes'), [Dossier]), mbInformation, MB_OK, IDOK);
  end;
end;

{ Cible d'un raccourci .lnk, lue sans le modifier ('' si illisible). }
function CibleRaccourci(const Lien: String): String;
var
  Shell, Raccourci: Variant;
begin
  Result := '';
  try
    Shell := CreateOleObject('WScript.Shell');
    Raccourci := Shell.CreateShortcut(Lien);
    Result := Raccourci.TargetPath;
  except
    Log('Raccourci illisible : ' + Lien);
  end;
end;

{ MyPhotos peut aussi créer ses raccourcis lui-même (Réglages, myphotos/raccourci.py), aux mêmes endroits que
  l'installateur : ceux-là ne sont pas dans le journal de désinstallation. Ils sont retirés s'ils pointent vers
  le MyPhotos.exe désinstallé, et seulement dans ce cas. }
procedure RetirerRaccourci(const Lien: String);
begin
  if not FileExists(Lien) then
    Exit;
  if PathSame(CibleRaccourci(Lien), ExpandConstant('{app}\MyPhotos.exe')) then
  begin
    if DeleteFile(Lien) then
      Log('Raccourci retiré : ' + Lien)
    else
      Log('Raccourci impossible à retirer : ' + Lien);
  end
  else
    Log('Raccourci gardé (il désigne un autre programme) : ' + Lien);
end;

{ Valeur de « /NOM=valeur » sur la ligne de commande ('' si absent). }
function ValeurParametre(const Nom: String): String;
var
  i: Integer;
begin
  Result := '';
  for i := 1 to ParamCount do
    if CompareText(Copy(ParamStr(i), 1, Length(Nom) + 1), Nom + '=') = 0 then
      Result := Copy(ParamStr(i), Length(Nom) + 2, MaxInt);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Essai: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { Essais seulement : /RACCOURCISDANS=<dossier> cherche les raccourcis là, jamais sur le vrai Bureau. }
    Essai := ValeurParametre('/RACCOURCISDANS');
    if Essai <> '' then
      RetirerRaccourci(AddBackslash(Essai) + 'MyPhotos.lnk')
    else
    begin
      RetirerRaccourci(ExpandConstant('{userdesktop}\MyPhotos.lnk'));
      RetirerRaccourci(ExpandConstant('{userprograms}\MyPhotos.lnk'));
    end;
    ProposerSuppressionDonnees();
  end;
end;

{ ------------------------------------------------------------------------------------------------------------
  Installation. }

{ Check de [InstallDelete] : vrai si le dossier d'installation est celui d'une installation de MyPhotos, celle que
  Windows connaît (dossier précédent) ou un dossier qui contient déjà MyPhotos.exe et son _internal : ses dossiers
  _internal et licences sont alors ceux de l'ancienne version, et partent avant la copie. Sinon (dossier existant
  choisi par /DIR), un « licences » ou un « _internal » appartient à quelqu'un d'autre : il reste.
  Évaluée une seule fois, avant toute suppression (PrepareToInstall) : la première entrée de [InstallDelete]
  retire _internal, et la seconde ne doit pas en conclure que ce n'est plus un dossier de MyPhotos.
  Attention : pas d'accolade fermante dans un commentaire entre accolades (elle le termine). }
var
  DossierDeMyPhotosEvalue: Boolean;
  DossierDeMyPhotos: Boolean;

function InstallationDeMyPhotos(): Boolean;
var
  App: String;
begin
  if not DossierDeMyPhotosEvalue then
  begin
    App := RemoveBackslash(ExpandConstant('{app}'));
    DossierDeMyPhotos := PathSame(RemoveBackslash(WizardForm.PrevAppDir), App)
                         or (FileExists(App + '\MyPhotos.exe') and DirExists(App + '\_internal'));
    DossierDeMyPhotosEvalue := True;
    if DossierDeMyPhotos then
      Log('Dossier d''une installation de MyPhotos (anciens fichiers retirés) : ' + App)
    else
      Log('Dossier qui n''est pas une installation de MyPhotos (rien n''y est retiré) : ' + App);
  end;
  Result := DossierDeMyPhotos;
end;

{ Ne jamais vider le « _internal » d'un autre programme : un dossier qui en contient un sans MyPhotos.exe
  n'est accepté que s'il s'agit de l'installation précédente de MyPhotos. Ni de dossier au chemin trop long :
  la copie s'arrêterait au milieu (« chemin d'accès introuvable »), et tout serait annulé. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  App: String;
begin
  Result := '';
  App := RemoveBackslash(ExpandConstant('{app}'));
  if Length(App) + 1 + {#CheminLePlusLong} > 259 then
  begin
    Result := FmtMessage(CustomMessage('DossierTropLong'), [App]);
    Exit;
  end;
  if DirExists(App + '\_internal') and not FileExists(App + '\MyPhotos.exe')
     and not PathSame(RemoveBackslash(WizardForm.PrevAppDir), App) then
  begin
    Result := FmtMessage(CustomMessage('DossierOccupe'), [App]);
    Exit;
  end;
  InstallationDeMyPhotos();                    { fixée ici, avant [InstallDelete] }
end;

{ Sans page « Prêt à installer » : le bouton de la page des raccourcis s'appelle « Installer ». }
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall)
  else if CurPageID = wpFinished then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonFinish)
  else
    WizardForm.NextButton.Caption := SetupMessage(msgButtonNext);
end;
