@echo off
REM Clean build artifacts (PyInstaller and Nuitka)

echo Cleaning build artifacts...

REM PyInstaller artifacts
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM Nuitka artifacts
if exist "v9k_image_util.build" rmdir /s /q "v9k_image_util.build"
if exist "v9k_image_util.dist" rmdir /s /q "v9k_image_util.dist"
if exist "v9k_image_util.onefile-build" rmdir /s /q "v9k_image_util.onefile-build"

REM Python cache
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "*.pyc" del /q "*.pyc"

echo Done.
