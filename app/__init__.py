"""نظام مراقبة الاعتمادات والمصروفات الحكومية."""
import os

from flask import Flask

from . import db, services


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
        DATABASE=os.path.join(app.instance_path, "budget.sqlite"),
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    app.jinja_env.filters["money"] = services.format_amount
    app.jinja_env.globals.update(
        STATUS_LABELS=services.STATUS_LABELS,
        ALERT_LABELS=services.ALERT_LABELS,
        WARNING_THRESHOLD=services.WARNING_THRESHOLD,
    )

    from .routes import bp

    app.register_blueprint(bp)
    return app
