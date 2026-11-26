@echo off
rem Klik dua kali file ini untuk menjalankan Tukang Catat.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Instalasi pertama kali, mohon tunggu beberapa menit...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" run.py
pause
exit /b

:error
echo.
echo Gagal menyiapkan Tukang Catat. Pastikan Python 3.10+ sudah terpasang.
pause
