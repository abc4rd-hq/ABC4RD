from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import qrcode
from flask import Flask, Response, abort, jsonify, make_response, redirect, render_template, request, send_file, url_for
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


DATA_PATH = Path(os.environ.get("ABC4RD_PORTAL_DATA", "/app/data/state.json"))
VERIFY_BASE_URL = os.environ.get("ABC4RD_VERIFY_BASE_URL", "https://verify.abc4rd.org").rstrip("/")
SIGNING_KEY = os.environ.get("ABC4RD_CERTIFICATE_SIGNING_KEY", "")
CORE_URL = os.environ.get("ABC4RD_CORE_URL", "http://abc4rd-academy-core:8080").rstrip("/")
LIBRARY_ASSET_REF = "abc4rd-library:pilot-0001:v0.1"
LIBRARY_COURSE_REF = "course-v1:ABC4RD+0001+2026"

app = Flask(__name__)
app.config.update(JSON_AS_ASCII=False)


@app.after_request
def security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    return response


def _load_state() -> dict[str, Any]:
    try:
        with DATA_PATH.open("r", encoding="utf-8") as stream:
            state = json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"generated_at": None, "participants": []}
    if not isinstance(state, dict) or not isinstance(state.get("participants"), list):
        return {"generated_at": None, "participants": []}
    return state


def _decode_access_token() -> dict[str, Any]:
    token = request.headers.get("X-Forwarded-Access-Token", "")
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_participant(claims: dict[str, Any] | None = None) -> dict[str, Any] | None:
    claims = claims if claims is not None else _decode_access_token()
    subject = claims.get("sub")
    username = claims.get("preferred_username")
    state = _load_state()
    for participant in state["participants"]:
        if subject and participant.get("keycloak_subject") == subject:
            return participant
        if username and participant.get("username") == username:
            return participant
    return None


def _is_portal_admin(claims: dict[str, Any]) -> bool:
    realm_access = claims.get("realm_access")
    if not isinstance(realm_access, dict):
        return False
    roles = realm_access.get("roles")
    return isinstance(roles, list) and "abc4rd-admin" in roles


def _certificate_signature(certificate_id: str) -> str:
    if not SIGNING_KEY:
        raise RuntimeError("certificate signing key is not configured")
    return hmac.new(SIGNING_KEY.encode("utf-8"), certificate_id.encode("ascii"), hashlib.sha256).hexdigest()


def _certificate_verify_url(certificate_id: str) -> str:
    return f"{VERIFY_BASE_URL}/c/{certificate_id}?sig={_certificate_signature(certificate_id)}"


def _find_certificate(certificate_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for participant in _load_state()["participants"]:
        for course in participant.get("courses", []):
            certificate = course.get("certificate")
            if isinstance(certificate, dict) and certificate.get("id") == certificate_id:
                return participant, course
    return None


def _library_content_sha256() -> str:
    template = Path(app.root_path) / (app.template_folder or "templates") / "reader.html"
    return hashlib.sha256(template.read_bytes()).hexdigest()


def _has_library_access(participant: dict[str, Any]) -> bool:
    return any(
        course.get("course_ref") == LIBRARY_COURSE_REF
        for course in participant.get("courses", [])
        if isinstance(course, dict)
    )


def _record_library_access(participant: dict[str, Any]) -> None:
    abc4rd_id = str(participant["abc4rd_id"])
    access_day = datetime.now(timezone.utc).date().isoformat()
    body = {
        "event_type": "library.reader.accessed",
        "aggregate_type": "library_asset",
        "aggregate_ref": LIBRARY_ASSET_REF,
        "source": "abc4rd-portal",
        "actor_type": "PARTICIPANT",
        "actor_ref": abc4rd_id,
        "payload": {"abc4rd_id": abc4rd_id, "access_day": access_day},
    }
    encoded = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    key_material = f"{abc4rd_id}:{LIBRARY_ASSET_REF}:{access_day}".encode("utf-8")
    event_key = "portal-library-" + hashlib.sha256(key_material).hexdigest()
    core_request = urlrequest.Request(
        f"{CORE_URL}/v1/events",
        data=encoded,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": event_key,
        },
    )
    try:
        with urlrequest.urlopen(core_request, timeout=3) as response:
            response.read()
    except (OSError, urlerror.URLError, urlerror.HTTPError) as exc:
        app.logger.warning("library access audit unavailable: %s", type(exc).__name__)


def _register_fonts() -> tuple[str, str]:
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    pdfmetrics.registerFont(TTFont("ABC4RD-Regular", regular))
    pdfmetrics.registerFont(TTFont("ABC4RD-Bold", bold))
    return "ABC4RD-Regular", "ABC4RD-Bold"


def _certificate_pdf(participant: dict[str, Any], course: dict[str, Any]) -> bytes:
    certificate = course["certificate"]
    verify_url = _certificate_verify_url(certificate["id"])
    output = io.BytesIO()
    width, height = landscape(A4)
    regular, bold = _register_fonts()
    pdf = canvas.Canvas(output, pagesize=(width, height), pageCompression=1)
    pdf.setTitle(f"ABC4RD Certificate {certificate['id']}")
    pdf.setAuthor("ABC4RD Academy")

    pdf.setFillColor(colors.HexColor("#081225"))
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#12213d"))
    pdf.roundRect(34, 34, width - 68, height - 68, 20, stroke=0, fill=1)
    pdf.setStrokeColor(colors.HexColor("#7c3aed"))
    pdf.setLineWidth(3)
    pdf.roundRect(48, 48, width - 96, height - 96, 16, stroke=1, fill=0)

    pdf.setFillColor(colors.HexColor("#a78bfa"))
    pdf.setFont(bold, 16)
    pdf.drawString(78, height - 95, "ABC4RD ACADEMY")
    pdf.setFillColor(colors.white)
    heading = "СЕРТИФИКАТ ТЕХНИЧЕСКОГО ПИЛОТА"
    heading_size = 32
    while pdfmetrics.stringWidth(heading, bold, heading_size) > width - 156 and heading_size > 20:
        heading_size -= 1
    pdf.setFont(bold, heading_size)
    pdf.drawString(78, height - 155, heading)
    pdf.setFillColor(colors.HexColor("#b9c5dc"))
    pdf.setFont(regular, 14)
    pdf.drawString(78, height - 195, "Подтверждает проверяемое завершение учебного маршрута")

    pdf.setFillColor(colors.white)
    pdf.setFont(bold, 27)
    pdf.drawString(78, height - 260, participant.get("display_label", "ABC4RD Participant"))
    pdf.setFillColor(colors.HexColor("#d9e2f2"))
    pdf.setFont(regular, 15)
    title = course.get("title") or course.get("course_ref", "Course")
    if len(title) > 72:
        title = title[:69] + "..."
    pdf.drawString(78, height - 300, title)
    pdf.setFont(bold, 15)
    pdf.setFillColor(colors.HexColor("#34d399"))
    pdf.drawString(78, height - 340, f"Результат: {course.get('grade_percent', 0):g}% - пройдено")

    qr = qrcode.make(verify_url)
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    from reportlab.lib.utils import ImageReader

    pdf.drawImage(ImageReader(qr_buffer), width - 235, 120, 140, 140, mask="auto")
    pdf.setFillColor(colors.HexColor("#b9c5dc"))
    pdf.setFont(regular, 8)
    pdf.drawCentredString(width - 165, 106, "Проверка подлинности по QR-коду")

    issued_at = certificate.get("issued_at", "")
    pdf.setFillColor(colors.HexColor("#8da0bd"))
    pdf.setFont(regular, 9)
    pdf.drawString(78, 82, f"ID: {certificate['id']}")
    pdf.drawString(78, 66, f"Выдан: {issued_at}")
    pdf.drawRightString(width - 78, 66, "Не является государственным дипломом")
    pdf.showPage()
    pdf.save()
    return output.getvalue()


@app.get("/health")
def health() -> Response:
    state = _load_state()
    return jsonify({"status": "ok", "participants": len(state["participants"]), "generated_at": state.get("generated_at")})


@app.get("/")
def dashboard() -> str:
    claims = _decode_access_token()
    if _is_portal_admin(claims):
        return render_template(
            "admin.html",
            admin_name=claims.get("name") or claims.get("preferred_username") or "администратор",
            academy_url="https://learn.abc4rd.org",
            studio_url="https://studio.abc4rd.org",
            crm_url="https://crm.abc4rd.org",
            messenger_url="https://chat.abc4rd.org",
            identity_url="https://id.abc4rd.org/realms/abc4rd/account/",
            mail_admin_url="https://www.m43.online/admin/",
        )
    participant = _current_participant(claims)
    return render_template(
        "dashboard.html",
        participant=participant,
        active=request.path.strip("/") or "home",
        academy_url="https://learn.abc4rd.org",
        messenger_url="https://chat.abc4rd.org",
        identity_security_url="https://id.abc4rd.org/realms/abc4rd/account/#/security/signingin",
    )


@app.get("/library")
def library_home() -> Response:
    participant = _current_participant()
    if participant is None or not _has_library_access(participant):
        abort(403)
    return redirect(url_for("library_reader"))


@app.get("/messages")
def messages_home() -> Response:
    return redirect("https://chat.abc4rd.org")


@app.get("/certificates")
def certificates_home() -> Response:
    participant = _current_participant()
    if participant is None:
        abort(404)
    for course in participant.get("courses", []):
        certificate = course.get("certificate")
        if isinstance(certificate, dict) and certificate.get("id") and course.get("passed"):
            return redirect(url_for("certificate_pdf", certificate_id=certificate["id"]))
    abort(404)


@app.get("/api/me")
def api_me() -> Response:
    participant = _current_participant()
    if participant is None:
        return jsonify({"status": "pending_sync"}), 404
    return jsonify(participant)


@app.get("/mobile")
def mobile_setup() -> str:
    return render_template(
        "mobile.html",
        participant=_current_participant(),
        messenger_url="https://chat.abc4rd.org",
        homeserver_url="https://matrix.abc4rd.org",
        identity_security_url="https://id.abc4rd.org/realms/abc4rd/account/#/security/signingin",
    )


@app.get("/library/pilot-0001")
def library_reader() -> Response:
    participant = _current_participant()
    if participant is None or not _has_library_access(participant):
        abort(403)
    _record_library_access(participant)
    rendered = render_template(
        "reader.html",
        participant=participant,
        asset_ref=LIBRARY_ASSET_REF,
        content_sha256=_library_content_sha256(),
        accessed_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    response = make_response(rendered)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, noarchive, nosnippet"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/certificate/<certificate_id>.pdf")
def certificate_pdf(certificate_id: str):
    participant = _current_participant()
    if participant is None:
        abort(404)
    for course in participant.get("courses", []):
        certificate = course.get("certificate")
        if isinstance(certificate, dict) and certificate.get("id") == certificate_id and course.get("passed"):
            return send_file(
                io.BytesIO(_certificate_pdf(participant, course)),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=f"ABC4RD-{participant['display_label']}-{certificate_id}.pdf",
            )
    abort(404)


@app.get("/c/<certificate_id>")
def verify_certificate(certificate_id: str) -> str:
    signature = request.args.get("sig", "")
    expected = _certificate_signature(certificate_id)
    match = _find_certificate(certificate_id)
    valid = bool(match and hmac.compare_digest(signature, expected))
    participant, course = match if match else ({}, {})
    return render_template(
        "verify.html",
        valid=valid,
        certificate_id=certificate_id,
        participant=participant,
        course=course,
        checked_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    ), 200 if valid else 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
