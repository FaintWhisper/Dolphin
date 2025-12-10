@echo off
echo Building Tame executable...
echo.

REM Kill any running Tame processes
taskkill /F /IM Tame.exe >nul 2>&1
if %errorlevel% equ 0 (
    echo Stopped running Tame process.
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
pyinstaller --clean Tame.spec

echo.
if exist dist\Tame.exe (
    echo ================================================
    echo Build successful!
    echo Executable location: dist\Tame.exe
    echo ================================================
) else (
    echo Build failed! Check the error messages above.
)
echo.
pause
