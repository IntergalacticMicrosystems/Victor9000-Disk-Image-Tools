@echo off
REM Build script for v9k_image_util.exe
REM Requires: pip install pyinstaller

echo Building v9k_image_util.exe...

REM Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller
)

REM Clean previous builds
if exist "dist\v9k_image_util.exe" del "dist\v9k_image_util.exe"
if exist "build\v9k_image_util" rmdir /s /q "build\v9k_image_util"

REM Build the executable
pyinstaller --onefile --console --clean --name v9k_image_util v9k_image_util.py

if exist "dist\v9k_image_util.exe" (
    echo.
    echo Build successful!
    echo Output: dist\v9k_image_util.exe
    echo.
    for %%A in ("dist\v9k_image_util.exe") do echo Size: %%~zA bytes
) else (
    echo.
    echo Build failed!
    exit /b 1
)
