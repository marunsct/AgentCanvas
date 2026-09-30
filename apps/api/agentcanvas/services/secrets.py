"""Project secrets (M5-T6): encrypted at rest, never returned by the API, resolved only when a run needs them."""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Secret
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id

NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _fernet(secret_key: str) -> Fernet:
    # AGENTCANVAS_SECRET_KEY -> 32-byte Fernet key (KMS envelope encryption replaces this in M8)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))


class SecretService:
    def __init__(self, session: AsyncSession, secret_key: str):
        self.s = session
        self.f = _fernet(secret_key)

    async def names(self, project_id: str) -> list[Secret]:
        q = select(Secret).where(Secret.project_id == project_id).order_by(Secret.name)
        return list((await self.s.scalars(q)).all())

    async def put(self, project_id: str, name: str, value: str, user_id: str | None) -> None:
        if not NAME.match(name):
            raise InvalidInput("secret names are UPPER_SNAKE_CASE, e.g. OPENAI_API_KEY")
        if not value:
            raise InvalidInput("a secret needs a value")
        row = await self.s.scalar(select(Secret).where(Secret.project_id == project_id, Secret.name == name))
        token = self.f.encrypt(value.encode())
        if row is None:
            self.s.add(Secret(id=new_id("sec"), project_id=project_id, name=name, ciphertext=token, created_by=user_id))
        else:
            row.ciphertext = token
        await self.s.commit()

    async def delete(self, project_id: str, name: str) -> None:
        res: Any = await self.s.execute(delete(Secret).where(Secret.project_id == project_id, Secret.name == name))
        if not res.rowcount:
            raise NotFound(f"secret '{name}' not found")
        await self.s.commit()

    async def values(self, project_id: str | None = None) -> dict[str, dict[str, str]]:
        """Decrypted values per project (for the runtime resolver only)."""
        q = select(Secret) if project_id is None else select(Secret).where(Secret.project_id == project_id)
        out: dict[str, dict[str, str]] = {}
        for row in (await self.s.scalars(q)).all():
            try:
                out.setdefault(row.project_id, {})[row.name] = self.f.decrypt(row.ciphertext).decode()
            except InvalidToken:  # encrypted with another key: unusable, never crash startup
                continue
        return out


def redact(text: str, values: list[str]) -> str:
    for v in values:
        text = text.replace(v, "***")
    return text
