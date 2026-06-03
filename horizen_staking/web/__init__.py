"""HTML page blueprint. Serves the single-page staking UI shell.

All live data is fetched client-side from the JSON API, so these routes stay
trivially simple.
"""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, session

web_bp = Blueprint("web", __name__)


@web_bp.get("/")
def index():
    cfg = current_app.config["CTX"].cfg
    return render_template("index.html", deployed=cfg.is_deployed)


@web_bp.get("/admin")
def admin():
    """Browser-based deploy/fund console (signs with the operator's wallet).

    SIWE-gated: the wizard is only rendered to a session authenticated as the
    configured ADMIN_ADDRESS. Everyone else gets the sign-in gate.
    """
    cfg = current_app.config["CTX"].cfg
    if cfg.admin_address:
        authed = bool(session.get("admin"))
    else:
        authed = cfg.admin_open  # no ADMIN_ADDRESS: open only with explicit ADMIN_OPEN
    return render_template("admin.html", authed=authed, admin_address=cfg.admin_address)
