from app.app import create_app

import atexit
import os

from app.agents.gateway import start_runtime

app = create_app()

if __name__ == "__main__":
    # O runtime pertence a este processo. use_reloader=False evita duplicação.
    enabled = os.environ.get("TCC_SPADE_ENABLED", "1") == "1"
    runtime = start_runtime(app) if enabled else None
    if runtime:
        atexit.register(runtime.stop)
    try:
        app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1", use_reloader=False)
    finally:
        if runtime:
            runtime.stop()
