@echo off
python -m venv .venv
call .venv\Scripts\activate
python.exe -m pip install --upgrade pip
pip install -r requirements.txt
pip install python-multipart
echo Setup complete.
