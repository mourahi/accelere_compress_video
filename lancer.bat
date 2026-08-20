@echo off
chcp 65001 >nul
title Compress ^& Accélère
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python est introuvable. Installez Python 3 depuis https://www.python.org/downloads/
    echo Cochez "Add python.exe to PATH" pendant l'installation.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement...
    python -m venv .venv
    if errorlevel 1 (
        echo Impossible de creer l'environnement virtuel.
        pause
        exit /b 1
    )
)

echo Verification des dependances...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo Installation des dependances echouee.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" app.py
if errorlevel 1 pause
