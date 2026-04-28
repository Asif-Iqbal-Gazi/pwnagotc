import logging
import os
import base64
import threading  # FIX B5: replaced _thread with threading
import secrets
import json
from functools import wraps

import flask

# https://stackoverflow.com/questions/14888799/disable-console-messages-in-flask-server
logging.getLogger("werkzeug").setLevel(logging.ERROR)
os.environ["WERKZEUG_RUN_MAIN"] = "false"

import pwnagotchi
import pwnagotchi.ui.web as web
from pwnagotchi import plugins

from flask import send_file
from flask import Response
from flask import request
from flask import jsonify
from flask import abort
from flask import redirect
from flask import render_template, render_template_string


class Handler:
    def __init__(self, config, agent, app):
        self._config = config
        self._agent = agent
        self._app = app

        # Dynamic theme CSS route
        self._app.add_url_rule("/css/theme.css", "dynamic_theme", self.dynamic_theme)

        self._app.add_url_rule("/", "index", self.with_auth(self.index))
        self._app.add_url_rule("/ui", "ui", self.with_auth(self.ui))

        self._app.add_url_rule(
            "/shutdown", "shutdown", self.with_auth(self.shutdown), methods=["POST"]
        )
        self._app.add_url_rule(
            "/reboot", "reboot", self.with_auth(self.reboot), methods=["POST"]
        )
        self._app.add_url_rule(
            "/restart", "restart", self.with_auth(self.restart), methods=["POST"]
        )

        # inbox
        self._app.add_url_rule("/inbox", "inbox", self.with_auth(self.inbox))
        self._app.add_url_rule(
            "/inbox/profile", "inbox_profile", self.with_auth(self.inbox_profile)
        )
        self._app.add_url_rule(
            "/inbox/peers", "inbox_peers", self.with_auth(self.inbox_peers)
        )
        self._app.add_url_rule(
            "/inbox/<id>", "show_message", self.with_auth(self.show_message)
        )
        self._app.add_url_rule(
            "/inbox/<id>/<mark>", "mark_message", self.with_auth(self.mark_message)
        )
        self._app.add_url_rule(
            "/inbox/new", "new_message", self.with_auth(self.new_message)
        )
        self._app.add_url_rule(
            "/inbox/send",
            "send_message",
            self.with_auth(self.send_message),
            methods=["POST"],
        )

        # plugins
        plugins_with_auth = self.with_auth(self.plugins)
        self._app.add_url_rule('/plugins', 'plugins', plugins_with_auth, strict_slashes=False,
                               defaults={'name': None, 'subpath': None})
        self._app.add_url_rule('/plugins/<name>', 'plugins', plugins_with_auth, strict_slashes=False,
                               methods=['GET', 'POST'], defaults={'subpath': None})
        self._app.add_url_rule('/plugins/<name>/<path:subpath>', 'plugins', plugins_with_auth, methods=['GET', 'POST'])

    def _check_creds(self, u, p):
        # trying to be timing attack safe
        return secrets.compare_digest(
            u, self._config["username"]
        ) and secrets.compare_digest(p, self._config["password"])

    def with_auth(self, f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not self._config["auth"]:
                return f(*args, **kwargs)
            else:
                auth = request.authorization
                if (
                    not auth
                    or not auth.username
                    or not auth.password
                    or not self._check_creds(auth.username, auth.password)
                ):
                    return Response(
                        "Unauthorized",
                        401,
                        {"WWW-Authenticate": 'Basic realm="Unauthorized"'},
                    )
                return f(*args, **kwargs)

        return wrapper

    def index(self):
        return render_template(
            "index.html",
            title=pwnagotchi.name(),
            other_mode="AUTO" if self._agent.mode == "manual" else "MANU",
            fingerprint=self._agent.fingerprint(),
        )

    def inbox(self):
        return render_template(
            "inbox.html",
            name=pwnagotchi.name(),
            page=1,
            error="Mesh networking (pwngrid) has been removed.",
            inbox={"pages": 1, "records": 0, "messages": []},
        )

    def inbox_profile(self):
        return render_template(
            "profile.html",
            name=pwnagotchi.name(),
            fingerprint=self._agent.fingerprint(),
            data="{}",
            error="Mesh networking (pwngrid) has been removed.",
        )

    def inbox_peers(self):
        return render_template(
            "peers.html",
            name=pwnagotchi.name(),
            peers={},
            error="Mesh networking (pwngrid) has been removed.",
        )

    def show_message(self, id):
        return render_template(
            "message.html",
            name=pwnagotchi.name(),
            error="Mesh networking (pwngrid) has been removed.",
            message={},
        )

    def new_message(self):
        return render_template("new_message.html", to="")

    def send_message(self):
        return jsonify({"error": "Mesh networking (pwngrid) has been removed."})

    def mark_message(self, id, mark):
        return redirect("/inbox")

    def plugins(self, name, subpath):
        if name is None:
            # Determine which plugins are from the default folder
            default_plugins = set()
            default_path = os.path.join(
                os.path.dirname(os.path.realpath(plugins.__file__)), "default"
            )
            for plugin_name, plugin_path in plugins.database.items():
                if plugin_path.startswith(default_path):
                    default_plugins.add(plugin_name)
            return render_template(
                "plugins.html",
                loaded=plugins.loaded,
                database=plugins.database,
                default_plugins=default_plugins,
            )

        if name == "toggle" and request.method == "POST":
            checked = True if "enabled" in request.form else False
            return (
                "success"
                if plugins.toggle_plugin(request.form["plugin"], checked)
                else "failed"
            )

        if name == "upgrade" and request.method == "POST":
            logging.info(f"Upgrading plugin: {request.form['plugin']}")
            os.system(
                f"pwnagotchi plugins update && pwnagotchi plugins upgrade {request.form['plugin']}"
            )
            return redirect("/plugins")

        if (
            name in plugins.loaded
            and plugins.loaded[name] is not None
            and hasattr(plugins.loaded[name], "on_webhook")
        ):
            try:
                return plugins.loaded[name].on_webhook(subpath, request)
            except Exception:
                abort(500)
        else:
            abort(404)

    # serve a message and shuts down the unit
    def shutdown(self):
        try:
            return render_template(
                "status.html",
                title=pwnagotchi.name(),
                go_back_after=60,
                message="Shutting down ...",
            )
        finally:
            # FIX B5: replaced _thread.start_new_thread with threading.Thread
            threading.Thread(target=pwnagotchi.shutdown, daemon=True).start()

    # serve a message and reboot the unit
    def reboot(self):
        try:
            return render_template(
                "status.html",
                title=pwnagotchi.name(),
                go_back_after=60,
                message="Rebooting ...",
            )
        finally:
            # FIX B5: replaced _thread.start_new_thread with threading.Thread
            threading.Thread(target=pwnagotchi.reboot, daemon=True).start()

    # serve a message and restart the unit in the other mode
    def restart(self):
        mode = request.form["mode"]
        if mode not in ("AUTO", "MANU"):
            mode = "MANU"

        try:
            return render_template(
                "status.html",
                title=pwnagotchi.name(),
                go_back_after=30,
                message="Restarting in %s mode ..." % mode,
            )
        finally:
            # FIX B5: replaced _thread.start_new_thread with threading.Thread
            threading.Thread(
                target=pwnagotchi.restart, args=(mode,), daemon=True
            ).start()

    # serve dynamic CSS with accent color from config
    def dynamic_theme(self):
        """Generate CSS accent RGB variables from config [ui.web.theme] section"""
        # Get RGB values from already-loaded config, fallback to default green
        r = self._config.get("theme", {}).get("accent_r", 76)
        g = self._config.get("theme", {}).get("accent_g", 175)
        b = self._config.get("theme", {}).get("accent_b", 80)

        css = f":root {{\n  --accent: rgb({r}, {g}, {b});\n  --accent-r: {r};\n  --accent-g: {g};\n  --accent-b: {b};\n}}"
        return Response(css, mimetype="text/css")

    # serve the PNG file with the display image
    def ui(self):
        with web.frame_lock:
            return send_file(web.frame_path, mimetype="image/png")
