@echo off
chcp 65001 >nul
echo ==========================================
echo   CPU Usage Track - 打包脚本
echo ==========================================
echo.

REM 检查 Python 环境
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 并添加到 PATH
    pause
    exit /b 1
)

REM 安装/升级 PyInstaller
echo [1/3] 安装 PyInstaller ...
pip install pyinstaller -q
if errorlevel 1 (
    echo [错误] PyInstaller 安装失败
    pause
    exit /b 1
)

REM 安装项目依赖
echo [2/3] 安装项目依赖 ...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

REM 打包
echo [3/3] 开始打包 ...
pyinstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "CPUUsageTrack" ^
    --hidden-import "pyqtgraph" ^
    --hidden-import "psutil" ^
    --hidden-import "PyQt5" ^
    --hidden-import "PyQt5.QtWidgets" ^
    --hidden-import "PyQt5.QtCore" ^
    --hidden-import "PyQt5.QtGui" ^
    --hidden-import "PyQt5.sip" ^
    --collect-data "pyqtgraph" ^
    main.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上方日志
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   打包完成！
echo   输出路径: dist\CPUUsageTrack.exe
echo ==========================================
echo.
pause
