"""
Encryption utilities for securing sensitive data at rest.
Uses Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256).

To generate a new key, run:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Then set it as the ENCRYPTION_KEY environment variable.
"""

import os
import logging

logger = logging.getLogger(__name__)

_fernet = None
_initialized = False


def _get_fernet():
    """Lazy-initialize the Fernet cipher. Returns None if key is not configured."""
    global _fernet, _initialized
    if _initialized:
        return _fernet

    _initialized = True
    key = os.environ.get('ENCRYPTION_KEY')

    if not key:
        logger.warning(
            "ENCRYPTION_KEY not set. Tokens will be stored in plain text. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return None

    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        logger.info("Encryption initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize encryption: {e}. Tokens will be stored in plain text.")
        _fernet = None

    return _fernet


def encrypt_token(token: str) -> str:
    """
    Encrypt a token for safe storage.
    Returns the encrypted string, or the original token if encryption is unavailable.
    """
    if not token:
        return token

    fernet = _get_fernet()
    if fernet is None:
        return token

    try:
        return fernet.encrypt(token.encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return token


def decrypt_token(encrypted_token: str) -> str:
    """
    Decrypt a stored token.
    Returns the decrypted string, or the original value if decryption fails
    (handles tokens that were stored before encryption was enabled).
    """
    if not encrypted_token:
        return encrypted_token

    fernet = _get_fernet()
    if fernet is None:
        return encrypted_token

    try:
        return fernet.decrypt(encrypted_token.encode('utf-8')).decode('utf-8')
    except Exception:
        # Token was likely stored in plain text before encryption was enabled.
        # Return as-is so existing configs don't break.
        logger.debug("Token could not be decrypted — treating as plain text (pre-encryption data).")
        return encrypted_token
