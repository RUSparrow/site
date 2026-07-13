import subprocess
from pathlib import Path

from fastapi import FastAPI


DEPLOY_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"

app = FastAPI(title="Deploy Agent")


@app.post("/update")
def update():
    subprocess.Popen([str(DEPLOY_SCRIPT)], start_new_session=True)
    return {"status": "started", "message": "Обновление запущено"}
