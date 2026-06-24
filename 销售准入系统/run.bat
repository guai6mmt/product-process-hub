@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo    销售准入文件管理系统  启动中...
echo ============================================
echo.

REM ====== 自动检测失败时：去掉下面这行最前面的 REM，把路径换成你的 python.exe ======
REM set "PYEXE=D:\你的Python目录\python.exe"

REM 依次尝试候选项；每个都"真正跑一次 --version"，能成功才采用
REM （这样可以挡掉指向已失效路径的 py 启动器，例如 D:\Python\python.exe 已被删除的情况）
if not defined PYEXE call :try py
if not defined PYEXE call :try python
if not defined PYEXE call :try "C:\Python314\python.exe"
if not defined PYEXE call :try "C:\Python313\python.exe"
if not defined PYEXE call :try "C:\Python312\python.exe"
if not defined PYEXE call :try "C:\Python311\python.exe"
if not defined PYEXE call :try "C:\Python310\python.exe"
if not defined PYEXE call :try "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PYEXE call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE call :try "D:\Python\python.exe"
if not defined PYEXE goto NOPY

echo 使用 Python: %PYEXE%
"%PYEXE%" --version
echo.
echo 本窗口保持打开即表示服务运行中；关闭窗口 = 停止服务。
echo 浏览器若没自动弹出，请手动打开： http://localhost:8765
echo.
"%PYEXE%" app.py
echo.
echo === 程序已退出。若上方有红色报错，请截图发我 ===
pause
exit /b

:try
REM 只有真正能跑出版本号，才把它记为可用的 Python
"%~1" --version >nul 2>nul && set "PYEXE=%~1"
goto :eof

:NOPY
echo [错误] 没找到“能正常运行”的 Python。
echo.
echo 常见原因：py 启动器指向的 Python（如 D:\Python\python.exe）已被删除/移动。
echo.
echo 请在本机命令行运行下面两行，把结果发给技术支持：
echo     where python
echo     py --version
echo.
echo 解决办法（任选其一）：
echo   1) 安装 Python 3（内网请用离线安装包），安装时务必勾选 "Add python.exe to PATH"；
echo   2) 若机器上其实已有 python.exe，把它的完整路径填到本文件顶部的 set "PYEXE=..." 那行（记得去掉 REM）。
pause
exit /b
