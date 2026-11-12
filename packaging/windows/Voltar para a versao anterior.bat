@echo off
rem issn_pc: volta para a versao anterior do port (saves e configuracoes ficam).
rem Goes back to the previous version of the port (saves and settings are kept).
rem "Voltar para a versao anterior.bat -Check" mostra o que faria, sem mudar nada.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rollback.ps1" %*
pause
