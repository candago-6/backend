import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# In a real production environment, the key should be provided via environment variable.
# It MUST be present.
SECRET_KEY = os.getenv("ENCRYPTION_KEY")

if not SECRET_KEY:
    raise ValueError("CRITICAL: ENCRYPTION_KEY environment variable not set.")

fernet = Fernet(SECRET_KEY.encode())


def encrypt_data(data: str) -> str:
    """Encrypts a string data."""
    if not data:
        return data
    return fernet.encrypt(data.encode()).decode()


def decrypt_data(encrypted_data: str) -> str:
    """Decrypts an encrypted string. Returns original if not encrypted or error."""
    if not encrypted_data or not encrypted_data.startswith("gAAAA"):
        return encrypted_data
    try:
        return fernet.decrypt(encrypted_data.encode()).decode()
    except Exception:
        return encrypted_data
