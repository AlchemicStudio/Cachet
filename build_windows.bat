@echo off
REM ===================================================================
REM  Build of both WINDOWS executables, on a REAL Windows machine:
REM    dist\signApp.exe       (windowed: double-click -> GUI)
REM    dist\signApp-cli.exe   (console: batch signing/stamping)
REM
REM  Prerequisites: Python 3.12, 3.13 or 3.14 (64-bit) installed, with
REM  the "tcl/tk and IDLE" option checked (tkinter), and "py" or "python"
REM  in the PATH. Double-click this file or run it from cmd.
REM ===================================================================
setlocal
cd /d "%~dp0"

if not exist venv (
  echo ^>^> Creating the venv...
  py -3 -m venv venv || python -m venv venv
  if errorlevel 1 (
    echo ERROR: unable to create the venv. Is 64-bit Python installed?
    pause & exit /b 1
  )
)

call venv\Scripts\activate.bat

echo ^>^> Installing dependencies (runtime + build)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 ( echo pip ERROR. & pause & exit /b 1 )

echo ^>^> Checking tkinter (required by the windowed binary)...
python -c "import tkinter, _tkinter; print('   tkinter OK')" || echo    WARNING: tkinter missing -^> reinstall Python with "tcl/tk and IDLE" checked.

echo ^>^> PyInstaller...
python -m PyInstaller --noconfirm --clean signApp.spec
if errorlevel 1 ( echo PyInstaller ERROR. & pause & exit /b 1 )

echo.
echo === Produced executables (dist\) ===
dir dist
echo.
echo Reminder: eID mode requires the Belgian eID middleware installed + reader + card.
pause
