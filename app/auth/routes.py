import re
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import passwords, store

router = APIRouter(prefix='/api/auth')
COOKIE = 'nw_session'
USERNAME_RE = re.compile(r'^[A-Za-z0-9_一-鿿]{3,20}$')
EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
MAX_FAILS, WINDOW = 5, 600
_fails: dict[tuple[str, str], deque] = defaultdict(deque)


class RegisterBody(BaseModel):
    username: str
    email: str | None = None
    password: str


class LoginBody(BaseModel):
    login: str
    password: str


def _error(code: str, status: int, field: str | None = None):
    return JSONResponse({'error': code, 'field': field}, status_code=status)


def _public(user: dict) -> dict:
    return {'username': user['username'], 'email': user.get('email')}


def _set_cookie(request: Request, response: Response, token: str, expires) -> None:
    https = request.url.scheme == 'https' or request.headers.get('x-forwarded-proto') == 'https'
    response.set_cookie(COOKIE, token, max_age=store.SESSION_DAYS * 86400, expires=expires,
                        httponly=True, samesite='lax', secure=https, path='/')


def _client(request: Request) -> str:
    return request.headers.get('cf-connecting-ip') or (request.client.host if request.client else '?')


def _locked(key) -> bool:
    q = _fails[key]
    while q and q[0] < time.monotonic() - WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


@router.post('/register')
def register(body: RegisterBody, request: Request, response: Response):
    username = body.username.strip()
    email = (body.email or '').strip() or None
    if not USERNAME_RE.match(username):
        return _error('username_invalid', 400, 'username')
    if email and (len(email) > 254 or not EMAIL_RE.match(email)):
        return _error('email_invalid', 400, 'email')
    if not 8 <= len(body.password) <= 128:
        return _error('password_length', 400, 'password')
    if body.password.lower() == username.lower():
        return _error('password_is_username', 400, 'password')
    try:
        user = store.create_user(username, email, passwords.hash_password(body.password))
    except store.Taken as taken:
        field = taken.args[0]
        return _error(f'{field}_taken', 409, field)
    token, expires = store.create_session(user['id'])
    _set_cookie(request, response, token, expires)
    return {'user': _public(user)}


@router.post('/login')
def login(body: LoginBody, request: Request, response: Response):
    key = (_client(request), body.login.strip().lower())
    if _locked(key):
        return _error('too_many_attempts', 429)
    user = store.find_for_login(body.login.strip()) if body.login.strip() else None
    ok = passwords.verify_password(body.password, user['password_hash'] if user else passwords.DUMMY_HASH)
    if not (user and ok):
        _fails[key].append(time.monotonic())
        return _error('bad_credentials', 401)
    _fails.pop(key, None)
    token, expires = store.create_session(user['id'])
    _set_cookie(request, response, token, expires)
    return {'user': _public(user)}


@router.post('/logout')
def logout(request: Request, response: Response):
    store.delete_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@router.get('/me')
def me(request: Request):
    user = store.user_for_session(request.cookies.get(COOKIE))
    return {'user': _public(user) if user else None}
