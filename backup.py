#!/usr/bin/env python3
import os
import re
import asyncio
import subprocess
import aiomysql
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler
import logging
import hashlib

from telethon import TelegramClient
from telethon.errors import FloodWaitError

# --------------------
# Configuration
# --------------------

load_dotenv()

DEBUG = bool(os.getenv("APP_DEBUG", "False").lower() in ("true", "1", "t", "yes", "y", "on", "enable", "enabled"))

# --------------------
# Logging
# --------------------

LOG_FILE = os.getenv("LOG_FILE", "backup.log")

# Clear existing handlers set by basicConfig
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

log_level = logging.DEBUG if DEBUG else logging.INFO
log_format = "%(asctime)s [%(levelname)s] %(message)s"
formatter = logging.Formatter(log_format)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_handler.setFormatter(formatter)

# Rotating file handler
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5 MB per file
    backupCount=7,             # keep 7 old log files
    encoding="utf-8"
)
file_handler.setLevel(log_level)
file_handler.setFormatter(formatter)

# Configure root logger with both handlers
logging.basicConfig(level=log_level, handlers=[console_handler, file_handler])
logging.info("Logging initialized.")

# Telegram
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))          # from https://my.telegram.org
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))        # group or user ID

# MySQL
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USERNAME = os.getenv("MYSQL_USERNAME", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "password")
MYSQL_WORLD_DB = os.getenv("MYSQL_WORLD_DB", "acore_world")
MYSQL_CHARACTERS_DB = os.getenv("MYSQL_CHAR_DB", "acore_characters")
MYSQL_AUTH_DB = os.getenv("MYSQL_AUTH_DB", "acore_auth")

# Archive options
ZIP_PASSWORD = os.getenv("ZIP_PASSWORD", "db_backup_password")
ZIP_NAME = f"db_backup_{datetime.now():%Y%m%d}.7z"
ZIP_BINARY = os.getenv("ZIP_BINARY", "7z")  # path to 7z binary, e.g. /usr/bin/7z

# Session name and directory
SESSION_NAME = os.getenv("SESSION_NAME", "azerothcore_backup")
SESSION_DIR = os.getenv("SESSION_DIR", "/sessions")

# Path working directory (optional)
WORKDIR = Path.cwd()

# --------------------
# Helpers
# --------------------

async def dump_database(db_name: str) -> str:
    """Async dump of a MySQL database using aiomysql."""
    output_file = str(WORKDIR / f"{db_name}.sql")
    logging.info(f"Starting dump for database: {db_name}")

    conn = await aiomysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USERNAME,
        password=MYSQL_PASSWORD,
        db=db_name,
        autocommit=True
    )
    cursor = await conn.cursor()

    await cursor.execute("SHOW TABLES")
    tables = [row[0] for row in await cursor.fetchall()]

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"-- Database dump for {db_name}\n\n")
        for table in tables:
            # schema
            await cursor.execute(f"SHOW CREATE TABLE `{table}`")
            create_stmt = (await cursor.fetchone())[1]
            f.write(f"\n-- Table structure for `{table}`\n")
            f.write(f"DROP TABLE IF EXISTS `{table}`;\n")
            f.write(f"{create_stmt};\n\n")

            # data
            await cursor.execute(f"SELECT * FROM `{table}`")
            rows = await cursor.fetchall()
            if rows:
                columns = [desc[0] for desc in cursor.description]
                for row in rows:
                    values = []
                    for value in row:
                        if value is None:
                            values.append("NULL")
                        elif isinstance(value, (int, float)):
                            values.append(str(value))
                        else:
                            values.append("'" + str(value).replace("'", "''") + "'")
                    insert_stmt = (
                        f"INSERT INTO `{table}` ({', '.join(columns)}) "
                        f"VALUES ({', '.join(values)});\n"
                    )
                    f.write(insert_stmt)

    await cursor.close()
    conn.close()
    logging.info(f"Finished dump for database: {db_name} -> {output_file}")
    return output_file


def create_7z_archive(archive_name: str, password: str, files: list[str]) -> str:
    """Create a password-protected 7z archive (no split)."""
    if not files:
        raise ValueError("No files provided")

    archive_path = Path(archive_name)

    # remove old archive if exists
    if archive_path.exists():
        archive_path.unlink()

    cmd = [
        str(ZIP_BINARY), "a", str(archive_path),
        *files,
        f"-p{password}",
        "-mhe=on",
        "-m0=lzma2",
        "-y"
    ]

    cmd_display = cmd.copy()
    cmd_display[cmd_display.index(f"-p{password}")] = "-pREDACTED"
    logging.info(f"Running command: {' '.join(cmd_display)}")

    try:
        res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logging.debug(f"7zz stdout: {res.stdout.decode(errors='ignore')}")
        logging.debug(f"7zz stderr: {res.stderr.decode(errors='ignore')}")
    except subprocess.CalledProcessError as e:
        logging.error(f"7zz failed: {e.stderr.decode(errors='ignore') if e.stderr else e}")
        raise

    logging.info(f"7z archive created: {archive_path}")
    return str(archive_path)


def cleanup_temp_files(*files):
    """Delete temporary files if they exist."""
    for file in files:
        try:
            os.remove(file)
            logging.debug(f"Removed temporary file: {file}")
        except FileNotFoundError:
            logging.warning(f"Temporary file not found: {file}")
        except IsADirectoryError:
            logging.warning(f"Expected file but found directory: {file}")
        except Exception as e:
            logging.warning(f"Could not remove {file}: {e}")

def human_readable_size(size_bytes: int, decimals: int = 2) -> str:
    """
    Converts a file size in bytes to a human-readable string.
    """
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    size = float(size_bytes)

    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1

    return f"{size:.{decimals}f} {units[index]}"


# --------------------
# Main backup flow
# --------------------

async def backup_and_send():

    # initializing telethon client
    logging.info("Making sure session directory exists...")
    os.makedirs(SESSION_DIR, exist_ok=True)
    SESSION_PATH = os.path.join(SESSION_DIR, SESSION_NAME)  # No ".session" here, Telethon adds it
    logging.info(f"Using session path: {SESSION_PATH}")
    logging.info("Starting Telegram client...")
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start()  # interactive login first run
    await client.send_message(CHAT_ID, f"Backup started at `{datetime.now():%Y-%m-%d %H:%M:%S}`", silent=True)

    dumps = {
        "world": MYSQL_WORLD_DB,
        "characters": MYSQL_CHARACTERS_DB,
        "auth": MYSQL_AUTH_DB,
    }

    logging.info("Starting database backup process...")
    tasks = [dump_database(db) for db in dumps.values()]
    dump_files = await asyncio.gather(*tasks)

    await asyncio.sleep(1)  # I/O safety delay

    archive_path = create_7z_archive(ZIP_NAME, ZIP_PASSWORD, dump_files)
    
    try:
        logging.info(f"Sending {archive_path} to chat {CHAT_ID}")
        file_size = os.path.getsize(archive_path)
        md5_hash = hashlib.md5(open(archive_path,'rb').read()).hexdigest()
        caption = (
            f"Database backup file name: `{Path(archive_path).name}`\n"
            f"Size: `{human_readable_size(file_size)}`\n"
            f"MD5: `{md5_hash}`"
        )

        # Progress callback: Telethon can automatically show progress via client.action
        async with client.action(CHAT_ID, 'document') as action:
            await client.send_file(
                CHAT_ID,
                archive_path,
                caption=caption,
                force_document=True,
                silent=True,
                progress_callback=action.progress
            )
        await client.send_message(CHAT_ID, f"Backup completed at `{datetime.now():%Y-%m-%d %H:%M:%S}`", silent=True)
    except FloodWaitError as e:
        logging.warning(f"Rate limited, must wait {e.seconds}s")
        await asyncio.sleep(e.seconds)
    finally:
        await client.disconnect()

    # cleanup
    cleanup_temp_files(*dump_files, archive_path)
    logging.info("Backup process completed and temporary files removed.")


if __name__ == "__main__":
    asyncio.run(backup_and_send())
