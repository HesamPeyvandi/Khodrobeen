from flask import Flask

from app.config import settings
from app.web.routes import auth, dashboard, health, users


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = settings.flask_secret_key

    app.register_blueprint(health.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(users.bp)

    return app
