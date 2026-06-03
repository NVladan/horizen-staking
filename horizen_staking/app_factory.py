"""Flask application factory."""

from __future__ import annotations

import os
import secrets

from flask import Flask, g

from .api import api_bp
from .blockchain.compiler import ensure_compiled
from .config import Config, _to_bool, config as default_config
from .context import AppContext
from .logging_config import configure_logging
from .web import web_bp

# Placeholder/example secrets that must never sign real admin sessions.
_WEAK_SECRETS = {
    "", "dev-secret-change-me", "change-me-in-production", "change-me", "local-demo",
}


def _csp(nonce: str) -> str:
    """Strict CSP: scripts only from self + a per-request nonce + the ethers CDN
    (no script 'unsafe-inline', so injected inline handlers can't run). The inline
    theme-bootstrap script carries the nonce. style 'unsafe-inline' is kept for the
    handful of inline style attributes (style injection is far lower risk)."""
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://api.fontshare.com; "
        "font-src 'self' https://fonts.gstatic.com https://api.fontshare.com; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
    )


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or default_config
    log = configure_logging(cfg)
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # The admin gate is only as strong as the cookie signing key — never run a
    # gated console with a known/placeholder secret.
    secret = cfg.flask_secret_key
    if secret in _WEAK_SECRETS:
        if cfg.admin_address:
            raise RuntimeError(
                "FLASK_SECRET_KEY is unset or a known placeholder, but ADMIN_ADDRESS "
                "is set — the admin session cookie would be forgeable. Set a strong key: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        secret = secrets.token_hex(32)  # ephemeral key for ungated/dev use
    app.config["SECRET_KEY"] = secret

    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_to_bool(os.getenv("SESSION_COOKIE_SECURE")),
    )
    app.config["CTX"] = AppContext(cfg)

    # Pre-compile contract artifacts once at startup so no HTTP request ever
    # triggers a solc download / compile. (No-op if build/ is already present;
    # a missing toolchain is surfaced later by load_artifact, not at boot.)
    try:
        ensure_compiled()
    except Exception:  # noqa: BLE001
        pass

    @app.before_request
    def _csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _inject_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", _csp(getattr(g, "csp_nonce", "")))
        if app.config.get("SESSION_COOKIE_SECURE"):
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return resp

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

    log.info(
        "Horizen staking app ready — chain=%s deployed=%s gated=%s debug=%s",
        cfg.chain_id, cfg.is_deployed, bool(cfg.admin_address), cfg.debug,
    )
    return app
