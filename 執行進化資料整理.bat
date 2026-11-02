@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo ================================================
echo PokeRole 3.0 - 進化資料整理工具
echo ================================================
echo.

if not exist "tools\enrich_pokemon_evolutions.py" (
  echo [錯誤] 找不到 tools\enrich_pokemon_evolutions.py
  echo 請確認本 BAT 放在 PokeRole3.0 專案根目錄，且 tools 資料夾存在。
  goto :failed
)
if not exist "site\data.js" (
  echo [錯誤] 找不到 site\data.js
  echo 請確認本 BAT 放在 PokeRole3.0 專案根目錄，且 site\data.js 存在。
  goto :failed
)

set "PYRUN="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYRUN=py -3"
)
if not defined PYRUN (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYRUN=python"
  )
)
if not defined PYRUN (
  echo [錯誤] 找不到 Python 3.10 或更新版本。
  echo 本工具不能使用 Python 3.6。請安裝 64 位元 Python 3.11 或 3.12，安裝時勾選 Add Python to PATH。
  goto :failed
)

echo [INFO] 使用的 Python：
%PYRUN% --version

echo.
echo [INFO] 備份原始 site\data.js 到 Windows 暫存資料夾...
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%i"
copy /Y "site\data.js" "%TEMP%\PokeRole3_data_before_evolution_%STAMP%.js" >nul
if errorlevel 1 (
  echo [錯誤] 無法備份 site\data.js，已停止以免覆蓋原始資料。
  goto :failed
)
echo [OK] 備份位置：%TEMP%\PokeRole3_data_before_evolution_%STAMP%.js

echo.
echo [INFO] 開始下載 PokeAPI CSV 並整理進化關係...
echo 需要可連上網際網路；請不要關閉此視窗。
echo.
%PYRUN% tools\enrich_pokemon_evolutions.py --input site\data.js
if errorlevel 1 (
  echo.
  echo [錯誤] 進化資料整理失敗。請查看上方第一個錯誤訊息。
  goto :failed
)

if not exist "site\evolution-audit.json" (
  echo [錯誤] 程式結束但沒有產生 site\evolution-audit.json。
  goto :failed
)

echo.
echo ================================================
echo [完成] 進化資料已寫入 site\data.js
echo [完成] 稽核報告：site\evolution-audit.json
echo ================================================
echo.
echo 下一步：使用 GitHub Desktop 檢查變更，Commit 並 Push 到 main。
echo Push 後再到 GitHub Actions 確認 Deploy web app to GitHub Pages 成功。
goto :end

:failed
echo.
echo 請將此視窗中的錯誤訊息貼給 ChatGPT，以便排查。

:end
pause
endlocal
