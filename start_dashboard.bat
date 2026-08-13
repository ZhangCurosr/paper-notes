@echo off
rem ============================================================
rem  MinerU API Dashboard 一键启动（Windows 双击运行）
rem  首次运行会提示输入 API key 并保存到用户目录，之后免输入
rem  自动打开浏览器 http://127.0.0.1:8901
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0.."

set CFG=%USERPROFILE%\.mineru_dashboard\config.json

if exist "%CFG%" (
    echo 使用已保存配置 %CFG%
) else (
    echo [首次运行] 未检测到配置，接下来会请你输入 API key
    echo   admin key：查看全局统计 / user key：只看自己的任务
)

python scripts/local_dashboard.py --config "%CFG%"

echo.
echo Dashboard 已退出。按任意键关闭窗口...
pause >nul
