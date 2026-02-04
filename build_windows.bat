@echo off
REM ############################################################################
REM PowerTrader AI - Windows Build Script
REM
REM This script builds all PowerTrader AI components for Windows using PyInstaller.
REM It creates standalone executables that can be distributed to users.
REM
REM Components built:
REM   - PowerTrader_AI.exe (Main GUI)
REM   - pt_thinker.exe (AI signal processor)
REM   - pt_trader.exe (Trading engine)
REM
REM Requirements:
REM   - Python 3.12+
REM   - PyInstaller 6.18.0+
REM   - All dependencies from requirements.txt installed
REM
REM Usage:
REM   build_windows.bat [options]
REM
REM Options:
REM   --clean    Remove build and dist directories before building
REM   --help     Show this help message
REM
REM Author: PowerTrader AI Contributors
REM License: Apache 2.0
REM ############################################################################

setlocal enabledelayedexpansion

REM Configuration
set PROJECT_ROOT=%~dp0
set BUILD_DIR=%PROJECT_ROOT%build
set DIST_DIR=%PROJECT_ROOT%dist

REM Components to build
set COMPONENTS=pt_hub pt_thinker pt_trader

REM Parse arguments
set CLEAN_BUILD=0
set SHOW_HELP=0

:parse_args
if "%~1"=="" goto end_parse_args
if /i "%~1"=="--clean" set CLEAN_BUILD=1
if /i "%~1"=="--help" set SHOW_HELP=1
if /i "%~1"=="-h" set SHOW_HELP=1
shift
goto parse_args
:end_parse_args

REM Show help if requested
if %SHOW_HELP%==1 (
    echo.
    echo PowerTrader AI - Windows Build Script
    echo.
    echo Usage: build_windows.bat [options]
    echo.
    echo Options:
    echo   --clean    Remove build and dist directories before building
    echo   --help     Show this help message
    echo.
    exit /b 0
)

REM Print banner
echo ================================================================
echo   PowerTrader AI - Windows Build Script
echo ================================================================
echo   Platform: Windows
echo.

REM Clean if requested
if %CLEAN_BUILD%==1 (
    echo ================================================================
    echo   Cleaning Build Directories
    echo ================================================================
    if exist "%BUILD_DIR%" (
        echo   Removing %BUILD_DIR%...
        rmdir /s /q "%BUILD_DIR%"
        echo   [OK] Build directory removed
    ) else (
        echo   Build directory does not exist
    )

    if exist "%DIST_DIR%" (
        echo   Removing %DIST_DIR%...
        rmdir /s /q "%DIST_DIR%"
        echo   [OK] Dist directory removed
    ) else (
        echo   Dist directory does not exist
    )
    echo.
)

REM Check requirements
echo ================================================================
echo   Checking Requirements
echo ================================================================

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python not found. Please install Python 3.12 or higher.
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo   [OK] Python %PYTHON_VERSION% found

REM Check PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] PyInstaller not found. Please install: pip install pyinstaller
    exit /b 1
)

for /f %%i in ('python -c "import PyInstaller; print(PyInstaller.__version__)"') do set PYINSTALLER_VERSION=%%i
echo   [OK] PyInstaller %PYINSTALLER_VERSION% found

REM Check spec files
set ALL_SPECS_FOUND=1
for %%c in (%COMPONENTS%) do (
    if not exist "%PROJECT_ROOT%%%c.spec" (
        echo   [ERROR] Spec file not found: %%c.spec
        set ALL_SPECS_FOUND=0
    ) else (
        echo   [OK] Found %%c.spec
    )
)

if %ALL_SPECS_FOUND%==0 (
    echo.
    echo   [ERROR] Missing spec files. Cannot proceed with build.
    exit /b 1
)

echo.

REM Build all components
echo ================================================================
echo   Building All Components
echo ================================================================
echo.

set BUILD_FAILED=0

for %%c in (%COMPONENTS%) do (
    echo ----------------------------------------------------------------
    echo   Building %%c
    echo ----------------------------------------------------------------
    echo   Building with PyInstaller...

    pyinstaller --clean --noconfirm "%PROJECT_ROOT%%%c.spec"

    if errorlevel 1 (
        echo   [ERROR] Failed to build %%c
        set BUILD_FAILED=1
    ) else (
        echo   [OK] %%c built successfully

        REM Check output for pt_hub (creates folder in onedir mode)
        if "%%c"=="pt_hub" (
            if exist "%DIST_DIR%\PowerTrader_AI" (
                echo   Output: PowerTrader_AI\
            )
        ) else (
            if exist "%DIST_DIR%\%%c" (
                echo   Output: %%c\
            )
        )
    )
    echo.
)

REM Show summary
echo ================================================================
echo   Build Summary
echo ================================================================
echo.
echo Build directory: %BUILD_DIR%
echo Distribution directory: %DIST_DIR%
echo.
echo Built components:

REM Check each component
if exist "%DIST_DIR%\PowerTrader_AI" (
    echo   [OK] PowerTrader_AI\
) else (
    echo   [ERROR] PowerTrader_AI\ not found
    set BUILD_FAILED=1
)

for %%c in (pt_thinker pt_trader) do (
    if exist "%DIST_DIR%\%%c" (
        echo   [OK] %%c\
    ) else (
        echo   [ERROR] %%c\ not found
        set BUILD_FAILED=1
    )
)

echo.

REM Exit with appropriate code
if %BUILD_FAILED%==1 (
    echo [ERROR] Build failed with errors
    exit /b 1
) else (
    echo [OK] Build completed successfully!
    exit /b 0
)
