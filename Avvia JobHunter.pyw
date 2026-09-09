"""Double-click launcher for the local dashboard, with no terminal window on Windows."""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.request
import webbrowser

logger = logging.getLogger(__name__)


def main():
    """Prefer the project environment, open the pipeline and provide a safe server stop button."""
    root = Path(__file__).resolve().parent
    python = root/'.venv/Scripts/pythonw.exe'
    if python.exists() and Path(sys.executable).resolve() != python.resolve():
        subprocess.Popen([str(python), str(Path(__file__).resolve())], cwd=root,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return
    logging.basicConfig(filename=root/'dashboard.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    window.title('JobHunter')
    window.geometry('460x270')
    window.resizable(False, False)
    from jobhunter.workspace import Archive, settings
    from jobhunter.dashboard import create_server
    cfg = settings()
    database = root/cfg['database']
    base = f"http://127.0.0.1:{cfg['port']}"
    url = base+'/?view=pipeline'
    try:
        server = create_server(database, cfg, cfg['port'])
    except OSError:
        try:
            with urllib.request.urlopen(base+'/api/bootstrap', timeout=3) as response:
                data = json.load(response)
            if 'token' not in data or 'sources' not in data:
                raise ValueError('Porta occupata')
            webbrowser.open(url)
            window.destroy()
            return
        except Exception:
            logger.exception('Dashboard launch failed')
            messagebox.showerror('JobHunter', 'La porta della dashboard è occupata. Chiudi il servizio che la usa oppure modifica la porta in config/app.json.', parent=window)
            window.destroy()
            return
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tk.Label(window, text='JobHunter è attivo', font=('Segoe UI', 15, 'bold')).pack(pady=(24, 10))
    tk.Label(window, text='Usa la pagina Pipeline per avviare e seguire i passaggi.\nPuoi ridurre questa finestra a icona durante il lavoro.', wraplength=420).pack(pady=6)
    tk.Button(window, text='Apri la pipeline', command=lambda: webbrowser.open(url)).pack(pady=8)

    def running_step():
        """Report in-flight work without ever blocking the close: an unverifiable state counts as idle."""
        try:
            archive = Archive(database)
            try:
                return bool(archive.db.execute("SELECT 1 FROM pipeline_jobs WHERE status='running' AND pid=?", (os.getpid(),)).fetchone())
            finally:
                archive.close()
        except Exception:
            logger.exception('Cannot verify server activity before closing')
            return False

    def stop():
        """Always close. A failed check must warn, never trap the user in a window with no way out."""
        idle = server.operation_lock.acquire(blocking=False)
        if (not idle or running_step()) and not messagebox.askokcancel(
                'JobHunter', 'Un passaggio è ancora in esecuzione.\n\nChiudere adesso lo interrompe: i risultati già salvati restano, '
                'il lavoro in corso no. Il passaggio verrà segnato come interrotto e potrai riprenderlo.\n\nChiudere comunque?',
                icon='warning', parent=window):
            if idle:
                server.operation_lock.release()
            return
        logger.info('Dashboard closed from the launcher')
        # Shut down in the background, then leave for certain: a stuck request must not keep the window alive.
        threading.Thread(target=server.shutdown, daemon=True).start()
        window.destroy()
        logging.shutdown()
        os._exit(0)

    tk.Button(window, text='Ferma e chiudi', command=stop).pack(pady=4)
    tk.Label(window, text='«Ferma e chiudi» termina sempre il server, anche se un passaggio è in corso.',
             wraplength=420, fg='#4d5b6c').pack(pady=(2, 0))
    window.protocol('WM_DELETE_WINDOW', stop)
    webbrowser.open(url)
    window.mainloop()


if __name__ == '__main__':
    main()
