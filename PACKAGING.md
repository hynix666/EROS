# Desktop packaging notes (untested paths, stated as such)

The shipped shape is service-style: Postgres (compose or native), the
Python LIL (uvicorn) serving the built UI, Ollama alongside. That is the
verified configuration.

Two wrapper paths if a single "app" artifact is wanted later:

1. **Tauri v2 shell** — point a Tauri window at http://127.0.0.1:8000 and
   manage the uvicorn process as a sidecar (`tauri.conf.json` >
   `bundle.externalBin`, packaging the backend with PyInstaller
   `--onefile`). Postgres remains external (compose) — bundling a
   database inside a desktop bundle is how RPO guarantees die.
2. **PyInstaller service** — `pyinstaller -n eros-api backend/eros/lil/app.py`
   plus an OS service unit (systemd / launchd / NSSM on Windows).

Neither wrapper has been executed in this build; both are sketches, not
claims. The README's per-OS quickstart is the supported path.
