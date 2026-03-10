"""
Генерация Fernet-ключа для шифрования паролей в БД.

Запуск:
    python generate_key.py

Скопируйте вывод в .env:
    FERNET_KEY=<ключ>
"""
from cryptography.fernet import Fernet

key = Fernet.generate_key().decode()
print(f"FERNET_KEY={key}")
print()
print("Добавьте строку выше в файл .env")
print("ВАЖНО: сохраните ключ — без него расшифровка паролей невозможна.")
