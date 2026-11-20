@echo off
REM Pull the latest NAIA / NCAA / NJCAA / NCCAA data from GitHub and overwrite the local "26-27 College Data" files.
REM   pull_from_github.bat              -> pull all 4
REM   pull_from_github.bat --dry-run    -> only show what would change
REM   pull_from_github.bat --backup     -> keep old files in _pull_backups
set PY=D:\Apps\Python\python.exe
if not exist "%PY%" set PY=python
"%PY%" "%~dp0pull_from_github.py" %*
pause
