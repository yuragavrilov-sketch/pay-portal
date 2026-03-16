@echo off
chcp 65001 >nul
echo === Git pull ===
git pull
if errorlevel 1 (
    echo ОШИБКА: git pull завершился с ошибкой
    pause
    exit /b 1
)

echo.
echo === Сборка фронтенда ===
cd frontend
call npm install
call npx vite build
if errorlevel 1 (
    echo ОШИБКА: сборка завершилась с ошибкой
    cd ..
    pause
    exit /b 1
)
cd ..

echo.
echo === Готово ===
pause
