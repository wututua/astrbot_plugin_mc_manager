@echo off
setlocal
cd /d "%~dp0"

rem Set your actual server JAR name and keep your existing memory settings.
set "MC_JAVA=java"
set "MC_JAR=server.jar"
set "MC_XMS=2G"
set "MC_XMX=4G"

if not exist "%MC_JAR%" (
    echo Server JAR not found: "%MC_JAR%"
    pause
    exit /b 1
)
if not exist "%~dp0astrbot-spark-agent.jar" (
    echo Agent JAR not found: "%~dp0astrbot-spark-agent.jar"
    pause
    exit /b 1
)

:restart
"%MC_JAVA%" "-javaagent:%~dp0astrbot-spark-agent.jar" -Xms%MC_XMS% -Xmx%MC_XMX% -jar "%MC_JAR%" nogui
echo Server stopped. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto restart
