@echo off
echo Building Dolphin executable...
echo.

REM Kill any running Dolphin processes
taskkill /F /IM Dolphin.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo Stopped running Dolphin process.
    echo.
)

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Build with PyInstaller using spec file
pyinstaller --clean Dolphin.spec

echo.
if exist dist\Dolphin.exe (
    echo ================================================
    echo Build successful!
    echo Executable location: dist\Dolphin.exe
    echo ================================================
) else (
    echo Build failed! Check the error messages above.
)
echo.
