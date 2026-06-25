from django.contrib.auth.models import User
import random
import uuid
import pyotp
from cryptography.fernet import Fernet
from exchange.settings import FERNET_KEY


def generate_hex_uuid():
    return uuid.uuid4().hex

def generate_encrypted_totp_secret():
    secret = pyotp.random_base32()

    fernet = Fernet(FERNET_KEY)
    encrypted_secret = fernet.encrypt(secret.encode())
    return secret, encrypted_secret

def verify_totp(user_totp_secret, totp):
    return pyotp.TOTP(user_totp_secret).verify(totp, valid_window=1)