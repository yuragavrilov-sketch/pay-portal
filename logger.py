"""
Настройка Python-логирования для приложения.

Два канала:
  - файл  logs/app.log  (RotatingFileHandler, 10 MB × 5 файлов)
  - консоль             (StreamHandler)

Уровень задаётся переменной окружения LOG_LEVEL (default: INFO).
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR  = os.path.join(os.path.dirname(__file__), 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'app.log')
LOG_FMT  = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
DATE_FMT = '%Y-%m-%d %H:%M:%S'


def setup_logging(app):
    """Подключает обработчики к Flask-приложению и корневому логгеру."""
    os.makedirs(LOG_DIR, exist_ok=True)

    level = getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)

    formatter = logging.Formatter(LOG_FMT, datefmt=DATE_FMT)

    # Файловый обработчик
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8',
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    # Консольный обработчик
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Применяем к Flask-логгеру и корневому
    for log in (app.logger, logging.getLogger('sqlalchemy.engine'), logging.getLogger()):
        log.handlers.clear()
        log.addHandler(file_handler)
        log.addHandler(console_handler)
        log.setLevel(level)

    # SQLAlchemy — только WARNING чтобы не засорять лог
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

    app.logger.info('Logging initialized. Level=%s File=%s', logging.getLevelName(level), LOG_FILE)
