@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo    销售准入文件管理系统  启动中...
echo ============================================
echo.

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if defined PYEXE goto RUN
if exist "C:\Python314\python.exe" set "PYEXE=C:\Python314\python.exe"
if defined PYEXE goto RUN
where python >nul 2>nul && set "PYEXE=python"
if defined PYEXE goto RUN
goto NOPY

:RUN
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

:NOPY
echo [错误] 没有找到 Python，请先安装 Python 3 后重试。
echo 下载地址： https://www.python.org/downloads/
pause
exit /b
