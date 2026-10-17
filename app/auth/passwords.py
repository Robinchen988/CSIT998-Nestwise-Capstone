import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2 ** 14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f'scrypt${N}${R}${P}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split('$')
        if scheme != 'scrypt':
            return False
        got = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, bytes.fromhex(digest))


DUMMY_HASH = hash_password(secrets.token_hex(16))
