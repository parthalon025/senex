@echo off
title senex -- Nightly Audit
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python -m senex audit --nightly
