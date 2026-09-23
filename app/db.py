"""إدارة الاتصال بقاعدة البيانات."""
import sqlite3

import click
from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf8"))


@click.command("init-db")
def init_db_command():
    """إنشاء جداول قاعدة البيانات (يحذف البيانات الموجودة)."""
    init_db()
    click.echo("تم إنشاء قاعدة البيانات.")


@click.command("seed-demo")
def seed_demo_command():
    """تهيئة قاعدة البيانات وتعبئتها ببيانات تجريبية."""
    from .seed import seed_demo

    init_db()
    seed_demo(get_db())
    click.echo("تم تحميل البيانات التجريبية.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_demo_command)
