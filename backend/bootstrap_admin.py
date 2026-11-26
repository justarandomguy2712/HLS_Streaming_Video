"""Tạo SQL bootstrap D1 chứa password hash, đọc mật khẩu bằng prompt ẩn."""

import argparse
import hashlib
import re
import secrets
import sys
from getpass import getpass
from pathlib import Path
from uuid import uuid4


def bootstrap_sql(username: str, password: str) -> str:
    name = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", name):
        raise ValueError("Username cần 3–40 ký tự ASCII hợp lệ.")
    if not 12 <= len(password) or len(password.encode()) > 256:
        raise ValueError("Mật khẩu cần ít nhất 12 ký tự và tối đa 256 byte UTF-8.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=5, dklen=32, maxmem=64 * 1024**2)
    encoded = f"scrypt${salt.hex()}${digest.hex()}"
    return (
        "INSERT INTO users(id,username,password_hash,role) "
        f"SELECT '{uuid4()}','{name}','{encoded}','ADMIN' "
        "WHERE NOT EXISTS (SELECT 1 FROM users WHERE role='ADMIN');\n"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        password = getpass("Mật khẩu Admin: ")
        if password != getpass("Nhập lại: "):
            raise ValueError("Mật khẩu nhập lại không khớp.")
        sql = bootstrap_sql(args.username, password)
        with args.out.open("x", encoding="utf-8") as file:
            file.write(sql)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print("Đã tạo SQL bootstrap. Chạy trên D1 và giữ tệp này ngoài Git.")


if __name__ == "__main__":
    main()

