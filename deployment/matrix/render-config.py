#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


root = Path(__file__).resolve().parent
homeserver_template = (root / "config" / "homeserver.yaml.example").read_text(encoding="utf-8")
homeserver_replacements = {
    "__DB_PASSWORD__": os.environ["ABC4RD_MATRIX_DB_PASSWORD"],
    "__OIDC_CLIENT_SECRET__": os.environ["ABC4RD_MATRIX_OIDC_CLIENT_SECRET"],
    "__MACAROON_SECRET__": os.environ["ABC4RD_MATRIX_MACAROON_SECRET"],
    "__FORM_SECRET__": os.environ["ABC4RD_MATRIX_FORM_SECRET"],
}
livekit_template = (root / "config" / "livekit.yaml.example").read_text(encoding="utf-8")
livekit_replacements = {
    "__LIVEKIT_KEY__": os.environ["ABC4RD_LIVEKIT_KEY"],
    "__LIVEKIT_SECRET__": os.environ["ABC4RD_LIVEKIT_SECRET"],
}


def render(template: str, replacements: dict[str, str], target: Path) -> None:
    for placeholder, value in replacements.items():
        if not value or "\n" in value or "\r" in value or '"' in value:
            raise SystemExit(f"unsafe or empty value for {placeholder}")
        template = template.replace(placeholder, value)
    if "__" in template:
        raise SystemExit(f"unresolved placeholder in {target.name}")
    target.write_text(template, encoding="utf-8")
    target.chmod(0o640)


render(
    homeserver_template,
    homeserver_replacements,
    root / "config" / "homeserver.yaml",
)
render(
    livekit_template,
    livekit_replacements,
    root / "config" / "livekit.yaml",
)
