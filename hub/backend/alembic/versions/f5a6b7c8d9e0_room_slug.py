"""room_slug: add rooms.slug MQTT identity column

Revision ID: f5a6b7c8d9e0
Revises: e3f4a5b6c7d8
Create Date: 2026-05-20 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f5a6b7c8d9e0"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

# Inline copy of hub.backend.slug — migrations must stay self-contained.
_TRANSLIT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "h",
    "ґ": "g",
    "д": "d",
    "е": "e",
    "є": "ie",
    "ж": "zh",
    "з": "z",
    "и": "y",
    "і": "i",
    "ї": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ь": "",
    "ю": "iu",
    "я": "ia",
}


def _slugify(name: str) -> str:
    chars: list[str] = []
    for ch in name.strip().lower():
        if ch in _TRANSLIT:
            chars.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            chars.append(ch)
        else:
            chars.append("_")
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")[:60] or "room"


def upgrade() -> None:
    # Nullable first so existing rows survive the ADD COLUMN.
    op.add_column("rooms", sa.Column("slug", sa.String(64), nullable=True))

    # Backfill: transliterate each room name to a unique slug.
    conn = op.get_bind()
    result = conn.execute(sa.text('SELECT id, name FROM rooms ORDER BY "order"'))
    taken: set[str] = set()
    for row in result:
        base = _slugify(str(row[1]))
        slug = base
        n = 2
        while slug in taken:
            slug = f"{base}_{n}"
            n += 1
        taken.add(slug)
        conn.execute(
            sa.text("UPDATE rooms SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": row[0]},
        )

    op.alter_column("rooms", "slug", nullable=False)
    op.create_unique_constraint("uq_rooms_slug", "rooms", ["slug"])


def downgrade() -> None:
    op.drop_constraint("uq_rooms_slug", "rooms", type_="unique")
    op.drop_column("rooms", "slug")
