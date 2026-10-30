from app.app import create_app

import atexit
import os
from pathlib import Path

from dotenv import load_dotenv

from app.agents.gateway import start_runtime

if __name__ == "__main__":
    # SPADE precisa das credenciais antes de app.run(), quando Flask as leria.
    # O caminho explícito evita carregar arquivos de outro diretório de trabalho.
    load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

app = create_app()

if __name__ == "__main__":
    # O runtime pertence a este processo. use_reloader=False evita duplicação.
    enabled = os.environ.get("TCC_SPADE_ENABLED", "1") == "1"
    runtime = start_runtime(app) if enabled else None
    if runtime:
        atexit.register(runtime.stop)
    try:
        app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", use_reloader=False,
                load_dotenv=False)
    finally:
        if runtime:
            runtime.stop()
