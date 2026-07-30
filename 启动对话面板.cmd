@echo off
setlocal
cd /d "%~dp0"
title Codex Conversation Archive Viewer

where python >nul 2>nul
if errorlevel 1 goto use_py_launcher
python --version >nul 2>nul
if errorlevel 1 goto use_py_launcher

python conversation_viewer.py
if errorlevel 1 pause
goto end

:use_py_launcher
where py >nul 2>nul
if errorlevel 1 goto python_missing

py -3 conversation_viewer.py
if errorlevel 1 pause
goto end

:python_missing
echo Python 3 was not found. Please install Python 3 and try again.
pause

:end
endlocal
