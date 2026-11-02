"""Flask entrypoint. Run from the backend2 folder:  python app.py"""
import logging
import os

from dotenv import load_dotenv
from flask import Flask

from api.jobs import JobManager
from api.routes import routes

load_dotenv()
logger = logging.getLogger("agentforge.app")


def create_app(job_manager: JobManager | None = None) -> Flask:
    app = Flask(__name__)
    app.config["JOB_MANAGER"] = job_manager or JobManager(
        os.getenv("OUTPUT_DIR", "generated_projects_api"))

    try:
        from flask_cors import CORS

        origins = os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://localhost:5174,"
            "http://127.0.0.1:5173,http://127.0.0.1:5174",
        ).split(",")
        CORS(app, origins=[o.strip() for o in origins if o.strip()])
    except ImportError:
        logger.warning("flask-cors not installed; CORS disabled.")

    app.register_blueprint(routes)
    return app


app = create_app() if os.getenv("CREATE_APP_ON_IMPORT", "1") == "1" else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    (app or create_app()).run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
