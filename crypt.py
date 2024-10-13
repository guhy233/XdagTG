from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import base64
import hashlib

def aes_encrypt(key, text):
    key = key.encode('utf-8')
    if len(key) > 32:
        key = key[:32]
    elif len(key) < 32:
        key = key.ljust(32, b'\0')

    iv = b'1145141919810000'
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded_data = padder.update(text.encode('utf-8')) + padder.finalize()

    encryptor = cipher.encryptor()
    encrypted_text = encryptor.update(padded_data) + encryptor.finalize()

    return base64.b64encode(encrypted_text).decode('utf-8')

def aes_decrypt(key, cdtext):
    key = key.encode('utf-8')
    if len(key) > 32:
        key = key[:32]
    elif len(key) < 32:
        key = key.ljust(32, b'\0')

    iv = b'1145141919810000'
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())

    encrypted_text = base64.b64decode(cdtext)

    decryptor = cipher.decryptor()
    padded_text = decryptor.update(encrypted_text) + decryptor.finalize()

    unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
    text = unpadder.update(padded_text) + unpadder.finalize()

    return text.decode('utf-8')

def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

