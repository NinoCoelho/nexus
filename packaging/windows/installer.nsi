; Nexus Windows Installer (NSIS 3.x)
; -------------------------------------
; Builds a setup.exe from the dist\Nexus folder produced by build.ps1.
;
; Usage (from repo root after build.ps1 completes):
;   makensis /DPRODUCT_VERSION=0.6.2 packaging\windows\installer.nsi
;
; Output: dist\Nexus-Setup-0.6.2.exe

!include "MUI2.nsh"
!include "FileFunc.nsh"

; ── Product metadata ────────────────────────────────────────────────────────
!define PRODUCT_NAME      "Nexus"
!define PRODUCT_PUBLISHER "Nino Coelho"
!define PRODUCT_EXE       "Nexus.exe"
!define PRODUCT_REG_KEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "0.0.0"
!endif

!ifndef SOURCE_DIR
  !define SOURCE_DIR "dist\Nexus"
!endif

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "dist\Nexus-Setup-${PRODUCT_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_REG_KEY}" "InstallLocation"

RequestExecutionLevel admin

; ── Modern UI pages ─────────────────────────────────────────────────────────
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ── Installer section ───────────────────────────────────────────────────────
Section "Install" SecInstall
  SetOutPath $INSTDIR

  ; Copy the entire bundle
  File /r "${SOURCE_DIR}\*.*"

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; Registry entries for Add/Remove Programs
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "DisplayName"     "${PRODUCT_NAME}"
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "DisplayVersion"  "${PRODUCT_VERSION}"
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "Publisher"        "${PRODUCT_PUBLISHER}"
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "InstallLocation"  "$INSTDIR"
  WriteRegStr HKLM "${PRODUCT_REG_KEY}" "UninstallString"  '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKLM "${PRODUCT_REG_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${PRODUCT_REG_KEY}" "NoRepair" 1

  ; Estimate size for ARP
  ${GetSize} "$INSTDIR" "/S=0K" $0
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${PRODUCT_REG_KEY}" "EstimatedSize" "$0"

  ; Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortcut  "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" \
                  "$INSTDIR\${PRODUCT_EXE}" "" "$INSTDIR\${PRODUCT_EXE}" 0
  CreateShortcut  "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk" \
                  "$INSTDIR\uninstall.exe"

  ; Desktop shortcut
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" \
                 "$INSTDIR\${PRODUCT_EXE}" "" "$INSTDIR\${PRODUCT_EXE}" 0
SectionEnd

; ── Uninstaller section ────────────────────────────────────────────────────
Section "Uninstall" SecUninstall
  ; Remove shortcuts
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${PRODUCT_NAME}"

  ; Remove application files
  RMDir /r "$INSTDIR"

  ; Remove registry entries
  DeleteRegKey HKLM "${PRODUCT_REG_KEY}"
SectionEnd
