; L'installateur de Prisme (Inno Setup 6) : construire.py le compile.
;
; Deux usages, choisis a la premiere page :
;   - Installer sur ce PC : dans le dossier de l'utilisateur (pas besoin
;     d'etre administrateur, et la mise a jour automatique peut remplacer le
;     programme), raccourcis menu Demarrer et bureau, desinstallation dans les
;     Parametres de Windows.
;   - Version portable : le dossier seul, ou l'on veut (une cle USB), avec un
;     dossier « cache » a cote du programme -- l'index, les vignettes et les
;     reglages voyagent avec lui (config.py, `_chosen_cache`). Ni raccourci ni
;     trace dans Windows.
;
; La version arrive de construire.py : ISCC /DAppVersion=1.1.7 prisme.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1D2A7C-3B84-4C5E-9A41-2E7B5D0C8F13}
AppName=Prisme
AppVersion={#AppVersion}
AppVerName=Prisme {#AppVersion}
AppPublisher=Prisme
DefaultDirName={localappdata}\Programs\Prisme
DefaultGroupName=Prisme
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Prisme-{#AppVersion}-installation
SetupIconFile=..\prisme.ico
UninstallDisplayIcon={app}\Prisme.exe
UninstallDisplayName=Prisme
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=yes
RestartApplications=no
; Portable : rien a desinstaller, rien dans Windows.
Uninstallable=not IsPortable
CreateUninstallRegKey=not IsPortable

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "bureau"; Description: "Créer un raccourci sur le bureau"; Check: not IsPortable

[Files]
Source: "..\dist\Prisme\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
; Le dossier qui rend Prisme portable : ses donnees y vivent.
Name: "{app}\cache"; Check: IsPortable

[Icons]
Name: "{autoprograms}\Prisme"; Filename: "{app}\Prisme.exe"; Check: not IsPortable
Name: "{autodesktop}\Prisme"; Filename: "{app}\Prisme.exe"; Tasks: bureau; Check: not IsPortable

[Run]
Filename: "{app}\Prisme.exe"; Description: "Lancer Prisme maintenant"; Flags: nowait postinstall skipifsilent

[Code]
var
  ModePage: TInputOptionWizardPage;
  InstalledDir, PortableDir: String;

function IsPortable: Boolean;
begin
  // /PORTABLE=1 en ligne de commande : pour une installation sans fenetre.
  Result := (ExpandConstant('{param:PORTABLE|0}') = '1')
            or ((ModePage <> nil) and (ModePage.SelectedValueIndex = 1));
end;

procedure InitializeWizard;
begin
  ModePage := CreateInputOptionPage(wpWelcome,
    'Installation ou version portable',
    'Comment voulez-vous utiliser Prisme ?',
    'Les deux sont le même programme, aussi rapide l''un que l''autre. ' +
    'Gardez Prisme sur le disque de l''ordinateur (pas sur un NAS) : il démarre plus vite.',
    True, False);
  ModePage.Add('Installer sur ce PC (recommandé) — raccourcis, mise à jour automatique, ' +
               'désinstallation dans les Paramètres de Windows');
  ModePage.Add('Version portable — un dossier autonome (clé USB, autre disque) : ' +
               'index, vignettes et réglages restent dedans');
  ModePage.SelectedValueIndex := 0;
  InstalledDir := ExpandConstant('{localappdata}\Programs\Prisme');
  PortableDir := ExpandConstant('{userdocs}\Prisme portable');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ModePage.ID then
  begin
    // Le dossier propose suit le choix, tant qu'on ne l'a pas change soi-meme.
    if IsPortable and (WizardForm.DirEdit.Text = InstalledDir) then
      WizardForm.DirEdit.Text := PortableDir
    else if (not IsPortable) and (WizardForm.DirEdit.Text = PortableDir) then
      WizardForm.DirEdit.Text := InstalledDir;
  end;
end;
