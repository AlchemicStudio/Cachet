@echo off
REM ===================================================================
REM  Build des deux executables WINDOWS, sur une VRAIE machine Windows :
REM    dist\signApp.exe       (fenetre : double-clic -> GUI)
REM    dist\signApp-cli.exe   (console : signature/tampon en lot)
REM
REM  Prerequis : Python 3.12, 3.13 ou 3.14 (64 bits) installe, avec
REM  l'option « tcl/tk and IDLE » cochee (tkinter), et « py » ou « python »
REM  dans le PATH. Double-cliquez ce fichier ou lancez-le depuis cmd.
REM ===================================================================
setlocal
cd /d "%~dp0"

if not exist venv (
  echo ^>^> Creation du venv...
  py -3 -m venv venv || python -m venv venv
  if errorlevel 1 (
    echo ERREUR : impossible de creer le venv. Python 64 bits est-il installe ?
    pause & exit /b 1
  )
)

call venv\Scripts\activate.bat

echo ^>^> Installation des dependances (runtime + build)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 ( echo ERREUR pip. & pause & exit /b 1 )

echo ^>^> Verification de tkinter (necessaire au binaire fenetre)...
python -c "import tkinter, _tkinter; print('   tkinter OK')" || echo    AVERTISSEMENT : tkinter absent -^> reinstallez Python en cochant « tcl/tk and IDLE ».

echo ^>^> PyInstaller...
python -m PyInstaller --noconfirm --clean signApp.spec
if errorlevel 1 ( echo ERREUR PyInstaller. & pause & exit /b 1 )

echo.
echo === Executables produits (dist\) ===
dir dist
echo.
echo Rappel : le mode eID exige le middleware eID belge installe + lecteur + carte.
pause
