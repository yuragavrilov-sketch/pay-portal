"""Keycloak authentication & authorization helpers for Flask."""
import functools
import logging
import os

import jwt
import requests as http_requests
from flask import Blueprint, jsonify, request, g

log = logging.getLogger(__name__)

auth = Blueprint('auth', __name__, url_prefix='/api/auth')

# ---------------------------------------------------------------------------
# Keycloak configuration (read from env)
# ---------------------------------------------------------------------------
KEYCLOAK_URL = os.environ.get('KEYCLOAK_URL', 'http://localhost:8080')
KEYCLOAK_REALM = os.environ.get('KEYCLOAK_REALM', 'svcmgr')
KEYCLOAK_CLIENT_ID = os.environ.get('KEYCLOAK_CLIENT_ID', 'svcmgr-app')
KEYCLOAK_CLIENT_SECRET = os.environ.get('KEYCLOAK_CLIENT_SECRET', '')

_jwks_client = None


def _keycloak_openid_url():
    return f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect"


def _get_jwks_client():
    """Lazy-init JWKS client for token verification."""
    global _jwks_client
    if _jwks_client is None:
        jwks_uri = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs"
        _jwks_client = jwt.PyJWKClient(jwks_uri)
    return _jwks_client


def _decode_token(token: str) -> dict:
    """Decode and verify a Keycloak JWT access token."""
    jwks = _get_jwks_client()
    signing_key = jwks.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience="account",
        options={"verify_exp": True},
    )


def login_required(f):
    """Decorator: require a valid Keycloak JWT in Authorization header."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({"error": "Authorization required"}), 401
        token = auth_header.split(' ', 1)[1]
        try:
            payload = _decode_token(token)
            g.user = {
                'sub': payload.get('sub'),
                'username': payload.get('preferred_username', ''),
                'email': payload.get('email', ''),
                'name': payload.get('name', ''),
                'roles': payload.get('realm_access', {}).get('roles', []),
            }
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except Exception as e:
            log.warning("JWT verification failed: %s", e)
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return wrapper


def role_required(*roles):
    """Decorator: require one of the given Keycloak realm roles."""
    def decorator(f):
        @functools.wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            user_roles = g.user.get('roles', [])
            if not any(r in user_roles for r in roles):
                return jsonify({"error": "Insufficient permissions"}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Auth API endpoints
# ---------------------------------------------------------------------------

@auth.route('/login', methods=['POST'])
def do_login():
    """Authenticate user via Keycloak token endpoint (Resource Owner Password)."""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    token_url = f"{_keycloak_openid_url()}/token"
    payload = {
        'grant_type': 'password',
        'client_id': KEYCLOAK_CLIENT_ID,
        'username': username,
        'password': password,
    }
    if KEYCLOAK_CLIENT_SECRET:
        payload['client_secret'] = KEYCLOAK_CLIENT_SECRET

    try:
        resp = http_requests.post(token_url, data=payload, timeout=10)
    except http_requests.RequestException as e:
        log.error("Keycloak connection error: %s", e)
        return jsonify({"error": "Authentication service unavailable"}), 503

    if resp.status_code != 200:
        kc_error = resp.json().get('error_description', 'Invalid credentials')
        return jsonify({"error": kc_error}), 401

    tokens = resp.json()
    return jsonify({
        "access_token": tokens['access_token'],
        "refresh_token": tokens['refresh_token'],
        "expires_in": tokens['expires_in'],
        "refresh_expires_in": tokens['refresh_expires_in'],
    })


@auth.route('/refresh', methods=['POST'])
def do_refresh():
    """Refresh access token using a Keycloak refresh token."""
    data = request.get_json(silent=True) or {}
    refresh_token = data.get('refresh_token', '')

    if not refresh_token:
        return jsonify({"error": "refresh_token is required"}), 400

    token_url = f"{_keycloak_openid_url()}/token"
    payload = {
        'grant_type': 'refresh_token',
        'client_id': KEYCLOAK_CLIENT_ID,
        'refresh_token': refresh_token,
    }
    if KEYCLOAK_CLIENT_SECRET:
        payload['client_secret'] = KEYCLOAK_CLIENT_SECRET

    try:
        resp = http_requests.post(token_url, data=payload, timeout=10)
    except http_requests.RequestException as e:
        log.error("Keycloak refresh error: %s", e)
        return jsonify({"error": "Authentication service unavailable"}), 503

    if resp.status_code != 200:
        return jsonify({"error": "Refresh token expired or invalid"}), 401

    tokens = resp.json()
    return jsonify({
        "access_token": tokens['access_token'],
        "refresh_token": tokens['refresh_token'],
        "expires_in": tokens['expires_in'],
        "refresh_expires_in": tokens['refresh_expires_in'],
    })


@auth.route('/logout', methods=['POST'])
def do_logout():
    """Logout user — revoke tokens in Keycloak."""
    data = request.get_json(silent=True) or {}
    refresh_token = data.get('refresh_token', '')

    if refresh_token:
        logout_url = f"{_keycloak_openid_url()}/logout"
        payload = {
            'client_id': KEYCLOAK_CLIENT_ID,
            'refresh_token': refresh_token,
        }
        if KEYCLOAK_CLIENT_SECRET:
            payload['client_secret'] = KEYCLOAK_CLIENT_SECRET
        try:
            http_requests.post(logout_url, data=payload, timeout=5)
        except http_requests.RequestException:
            pass

    return jsonify({"ok": True})


@auth.route('/me', methods=['GET'])
@login_required
def me():
    """Return current user info from the JWT token."""
    return jsonify(g.user)
