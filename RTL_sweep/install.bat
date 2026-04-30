@echo off
REM RTL Sweep Pro — convenience installer for Windows.
setlocal
cd /d "%~dp0"

echo ==^> Creating virtual environment in .venv
python -m venv .venv
call .venv\Scripts\activate.bat

echo ==^> Upgrading pip
python -m pip install --upgrade pip

echo ==^> Installing requirements
pip install -r requirements.txt

echo.
echo RTL Sweep Pro is installed.
echo Activate the venv with:    .venv\Scripts\activate.bat
echo Then run:                  python main.py
echo Or, without hardware:      python main.py --mock
endlocal
