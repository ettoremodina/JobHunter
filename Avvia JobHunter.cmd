@echo off
rem Avvio con doppio clic (regola PC-01): chiede all'hub di avviare l'app (se e' gia' accesa non cambia nulla), poi apre la dashboard.
rem Senza l'hub: python main.py serve (solo il server, porta di config/app.json). Avvia JobHunter.pyw apre invece la finestra con "Ferma e chiudi".
curl.exe -s -o NUL -m 60 -X POST http://127.0.0.1:8800/api/apps/jobhunter/start
start "" http://127.0.0.1:8898/#pipeline
