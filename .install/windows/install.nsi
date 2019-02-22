!include MUI2.nsh
!include nsDialogs.nsh
!include LogicLib.nsh
!include CPUFeatures.nsh
!include MultiDetailPrint.nsi

# Installer names and locations
OutFile "faceswap_setup_x64.exe"
Name "Faceswap"
InstallDir $PROFILE\faceswap

# Download sites
!define wwwGit "https://github.com/git-for-windows/git/releases/download/v2.20.1.windows.1/Git-2.20.1-64-bit.exe"
!define wwwConda "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
!define wwwRepo "https://github.com/deepfakes/faceswap.git"


# Faceswap Specific
!define envName "faceswap"
!define flagsSetup "setup.py --installer"

# Install cli flags
!define flagsConda "/S /RegisterPython=0 /AddToPath=0 /D=$Profile\MiniConda3"
!define flagsGit "/SILENT /NORESTART /NOCANCEL /SP /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS"
!define flagsRepo "--depth 1"
!define flagsEnv "-y -n ${envName} python=3.6"

# Dlib Wheel prefix
!define prefixDlib "dlib-19.16.99-cp36-cp36m-win_amd64"
!define dlibFinalName "dlib-19.16.99-cp36-cp36m-win_amd64.whl" # Dlib Wheel MUST have this name before installing
!define cudaDlib "_cuda90"
!define avxDlib "_avx"
!define sseDlib "_sse4"
!define noneDlib "_none"


# Folders
Var dirTemp
Var dirMiniconda
Var dirAnaconda
Var dirConda

# Items to Install
Var InstallGit
Var InstallConda
Var dlibWhl

# Misc
Var gitInf
Var InstallFailed
Var lblPos
Var hasAVX
Var hasSSE4
Var noNvidia
Var ctlCondaText
Var ctlCondaButton
Var Log

# Modern UI2
!define MUI_COMPONENTSPAGE_NODESC
!define MUI_ABORTWARNING

# Install Location Page
!define MUI_ICON "fs_logo_32.ico"
!define MUI_PAGE_HEADER_TEXT "Faceswap.py Installer"
!define MUI_PAGE_HEADER_SUBTEXT "Install Location"
!define MUI_DIRECTORYPAGE_TEXT_DESTINATION "Select Destination Folder:"
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE VerifyInstallDir
!insertmacro MUI_PAGE_DIRECTORY

# Install Prerequisites Page
Page custom pgPrereqCreate pgPrereqLeave

# Install Faceswap Page
!define MUI_PAGE_HEADER_SUBTEXT "Installing Faceswap..."
!insertmacro MUI_PAGE_INSTFILES

# Set language (or Modern UI doesn't work)
!insertmacro MUI_LANGUAGE "English"

# Init
Function .onInit
    # It's better to put stuff in $pluginsdir, $temp is shared
    InitPluginsDir
    StrCpy $dirTemp "$pluginsdir\faceswap\temp"
    StrCpy $dirMiniconda "$PROFILE\Miniconda3"
    StrCpy $dirAnaconda "$PROFILE\Anaconda3"
    StrCpy $gitInf "$dirTemp\git_install.inf"
    SetOutPath "$dirTemp"
    File *.whl
    File git_install.inf
    Call CheckPrerequisites
FunctionEnd

Function VerifyInstallDir
    # Check install folder does not already exist
    IfFileExists  $INSTDIR 0 +3
    MessageBox MB_OK "Destination directory exists. Please select an alternative location"
    Abort
FunctionEnd

Function Abort
    MessageBox MB_OK "Some applications failed to install. Process Aborted. Check Details."
    Abort "Install Aborted"
FunctionEnd

Function pgPrereqCreate
    !insertmacro  MUI_HEADER_TEXT "Faceswap.py Installer" "Customize Install"

    nsDialogs::Create 1018
    Pop $0

    ${If} $0 == error
        Abort
    ${EndIf}

    StrCpy $lblPos 14
    # Info Installing applications
    ${NSD_CreateGroupBox} 5% 5% 90% 35% "The following applications will be installed"
    Pop $0

        ${If} $InstallGit == 1
            ${NSD_CreateLabel} 10% $lblPos% 80% 14u "Git for Windows"
            Pop $0
            intOp $lblPos $lblPos + 7
        ${EndIf}

        ${If} $InstallConda == 1
            ${NSD_CreateLabel} 10% $lblPos% 80% 14u "MiniConda 3"
            Pop $0
            intOp $lblPos $lblPos + 7
        ${EndIf}
        ${NSD_CreateLabel} 10% $lblPos% 80% 14u "Faceswap"

        StrCpy $lblPos 47
    # Info Custom Options
    ${NSD_CreateGroupBox} 5% 40% 90% 60% "Custom Items"
    Pop $0

        ${NSD_CreateCheckBox} 10% $lblPos% 80% 16u " IMPORTANT! Check here if you do NOT have an NVIDIA graphics card"
        Pop $noNvidia
        intOp $lblPos $lblPos + 18

        ${If} $InstallConda == 1
            ${NSD_CreateLabel} 10% $lblPos% 80% 24u "Anaconda or Miniconda is required but could not be detected.$\nIf you have Conda installed on your system please specify the location below, otherwise leave blank:"
            Pop $0
            intOp $lblPos $lblPos + 20

            ${NSD_CreateText} 10% $lblPos% 70% 12u ""
            Pop $ctlCondaText

            ${NSD_CreateButton} 80% $lblPos% 7% 12u "..."
            Pop $ctlCondaButton
            ${NSD_OnClick} $ctlCondaButton fnc_hCtl_test_DirRequest1_Click
        ${EndIf}

    nsDialogs::Show
FunctionEnd


; onClick handler for DirRequest Button $hCtl_test_DirRequest1_Btn
Function fnc_hCtl_test_DirRequest1_Click
	Pop $R0
	${If} $R0 == $ctlCondaButton
		${NSD_GetText} $ctlCondaText $R0
		nsDialogs::SelectFolderDialog /NOUNLOAD "" "$R0"
		Pop $R0
		${If} "$R0" != "error"
			${NSD_SetText} $ctlCondaText "$R0"
		${EndIf}
	${EndIf}
FunctionEnd

Function pgPrereqLeave
    Call CheckCustomCondaPath
    ${NSD_GetState} $noNvidia $noNvidia
FunctionEnd

Function CheckCustomCondaPath
    ${NSD_GetText} $ctlCondaText $2
    ${If} $2 != ""
        nsExec::ExecToStack "$2\Scripts\conda.exe -V"
        pop $0
        pop $1
        ${If} $0 == 0
            StrCpy $InstallConda 0
            StrCpy $dirConda "$2"
            StrCpy $Log "$log(check) Custom Conda found: $1$\n"
        ${Else}
            StrCpy $Log "$log(error) Custom Conda not found at: $2. Installing MiniConda$\n"
        ${EndIf}
    ${EndIf}
FunctionEnd

Function CheckPrerequisites
    #Git
        nsExec::ExecToStack "git --version"
        pop $0
        pop $1
        ${If} $0 == 0
            StrCpy $Log "$log(check) Git installed: $1"
        ${Else}
            StrCpy $InstallGit 1
        ${EndIf}

    # Conda
        # miniconda
        nsExec::ExecToStack "$dirMiniconda\Scripts\conda.exe -V"
        pop $0
        pop $1

        # anaconda
        nsExec::ExecToStack "$dirAnaconda\Scripts\conda.exe -V"
        pop $2
        pop $3

        ${If} $0 == 0
            StrCpy $dirConda "$dirMiniconda"
            StrCpy $Log "$log(check) MiniConda installed: $1"
        ${Else}
            ${If} $2 == 0
                StrCpy $dirConda "$dirAnaconda"
                StrCpy $Log "$log(check) AnaConda installed: $0"
            ${Else}
                StrCpy $InstallConda 1
            ${EndIf}
        ${EndIf}

    # CPU Capabilities
        ${If} ${CPUSupports} "AVX2"
        ${OrIf} ${CPUSupports} "AVX1"
            StrCpy $Log "$log(check) CPU Supports AVX Instructions$\n"
            StrCpy $hasAVX 1
        ${EndIf}
        ${If} ${CPUSupports} "SSE4.2"
        ${OrIf} ${CPUSupports} "SSE4"
            StrCpy $Log "$log(check) CPU Supports SSE4 Instructions$\n"
            StrCpy $hasSSE4 1
        ${EndIf}

    StrCpy $Log "$Log(check) Completed check for installed applications$\n"
FunctionEnd

Section Install
    Push $Log
    Call MultiDetailPrint
    Call InstallPrerequisites
    Call CloneRepo
    Call SetEnvironment
    Call InstallDlib
    Call SetupFaceSwap
    Call DesktopShortcut
SectionEnd

Function InstallPrerequisites
    # GIT
        ${If} $InstallGit == 1
            DetailPrint "Downloading Git..."
            inetc::get /caption "Downloading Git..." /canceltext "Cancel" ${wwwGit} "git_installer.exe" /end
            Pop $0 # return value = exit code, "OK" means OK
            ${If} $0 == "OK"
                DetailPrint "Installing Git..."
                SetDetailsPrint listonly
                ExecWait "$dirTemp\git_installer.exe ${flagsGit} /LOADINF=$\"$gitInf$\"" $0
                SetDetailsPrint both
                ${If} $0 != 0
                    DetailPrint "Error Installing Git"
                    StrCpy $InstallFailed 1
                ${EndIf}
            ${Else}
                DetailPrint "Error Downloading Git"
                StrCpy $InstallFailed 1
            ${EndIf}
        ${EndIf}

    # CONDA
        ${If} $InstallConda == 1
            DetailPrint "Downloading Miniconda3..."
            inetc::get /caption "Downloading Miniconda3." /canceltext "Cancel" ${wwwConda} "Miniconda3.exe" /end
            Pop $0
            ${If} $0 == "OK"
                DetailPrint "Installing Miniconda3. This will take a few minutes..."
                SetDetailsPrint listonly
                ExecWait "$dirTemp\Miniconda3.exe ${flagsConda}" $0
                StrCpy $dirConda "$dirMiniconda"
                SetDetailsPrint both
                ${If} $0 != 0
                    DetailPrint "Error Installing Miniconda3"
                    StrCpy $InstallFailed 1
                ${EndIf}
            ${Else}
                DetailPrint "Error Downloading Miniconda3"
                StrCpy $InstallFailed 1
            ${EndIf}
        ${EndIf}

    ${If} $InstallFailed == 1
        Call Abort
    ${Else}
        DetailPrint "All Prerequisites installed."
    ${EndIf}
FunctionEnd

Function CloneRepo
    DetailPrint "Downloading Faceswap..."
    SetDetailsPrint listonly
    ExecWait "$PROGRAMFILES64\git\bin\git.exe clone ${flagsRepo} ${wwwRepo} $INSTDIR" $0
    SetDetailsPrint both
    ${If} $0 != 0
        DetailPrint "Error Downloading Faceswap"
        Call Abort
    ${EndIf}
FunctionEnd

Function SetEnvironment
    DetailPrint "Creating Conda Virtual Environment..."
    IfFileExists  "$dirConda\envs\faceswap" DeleteEnv CreateEnv
        DeleteEnv:
            SetDetailsPrint listonly
            ExecWait "$dirConda\scripts\activate.bat && conda env remove -y -n ${envName} && conda deactivate" $0
            SetDetailsPrint both
            ${If} $0 != 0
                DetailPrint "Error deleting Conda Virtual Environment"
                Call Abort
            ${EndIf}

    CreateEnv:
        SetDetailsPrint listonly
        ExecWait "$dirConda\scripts\activate.bat && conda create ${flagsEnv} && conda deactivate" $0
        SetDetailsPrint both
        ${If} $0 != 0
            DetailPrint "Error Creating Conda Virtual Environment"
            Call Abort
        ${EndIf}
FunctionEnd

Function InstallDlib
    DetailPrint "Installing Dlib..."
    StrCpy $dlibWhl ${prefixDlib}

    ${If} $noNvidia != 1
        StrCpy $dlibWhl "$dlibWhl${cudaDlib}"
    ${EndIf}

    ${If} $hasAVX == 1
        StrCpy $dlibWhl "$dlibWhl${avxDlib}"
    ${ElseIf} $hasSSE4 == 1
        StrCpy $dlibWhl "$dlibWhl${sseDlib}"
    ${Else}
        StrCpy $dlibWhl "$dlibWhl${noneDlib}"
    ${EndIf}

    StrCpy $dlibWhl "$dlibWhl.whl"
    Rename  $dirTemp\$dlibWhl  $dirTemp\${dlibFinalName}

    SetDetailsPrint listonly
    ExecWait "$dirConda\scripts\activate.bat && conda activate ${envName} && pip install $dirTemp\${dlibFinalName} &&  conda deactivate" $0
    SetDetailsPrint both
    ${If} $0 != 0
        DetailPrint "Error Installing Dlib"
        Call Abort
    ${EndIf}

FunctionEnd

Function SetupFaceSwap
    DetailPrint "Setting up FaceSwap Environment"
    StrCpy $0 "${flagsSetup}"
    ${If} $noNvidia != 1
        StrCpy $0 "$0 --gpu"
    ${EndIf}

    SetDetailsPrint listonly
    ExecWait "$dirConda\scripts\activate.bat && conda activate ${envName} && python $INSTDIR\$0 && conda deactivate" $0
    SetDetailsPrint both
    ${If} $0 != 0
        DetailPrint "Error Setting up Faceswap"
        Call Abort
    ${EndIf}
FunctionEnd

Function DesktopShortcut
    DetailPrint "Creating Desktop Shortcut"
    SetOutPath "$INSTDIR"
    StrCpy $0 "faceswap_win_launcher.bat"
    FileOpen $9 "$INSTDIR\$0" w
    FileWrite $9 "$dirConda\scripts\activate.bat && conda activate ${envName} && python $INSTDIR/faceswap.py gui$\r$\n"
    FileClose $9
    CreateShortCut "$DESKTOP\${envName}.lnk" "$INSTDIR\$0" "" "$INSTDIR\.install\windows\fs_logo_32.ico"
FunctionEnd