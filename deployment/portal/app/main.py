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

import qrcode
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


DATA_PATH = Path(os.environ.get("ABC4RD_PORTAL_DATA", "/app/data/state.json"))
VERIFY_BASE_URL = os.environ.get("ABC4RD_VERIFY_BASE_URL", "https://verify.abc4rd.org").rstrip("/")
SIGNING_KEY = os.environ.get("ABC4RD_CERTIFICATE_SIGNING_KEY", "")

app = Flask(__name__)
app.config.update(JSON_AS_ASCII=False)


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


def _current_participant() -> dict[str, Any] | None:
    claims = _decode_access_token()
    subject = claims.get("sub")
    username = claims.get("preferred_username")
    state = _load_state()
    for participant in state["participants"]:
        if subject and participant.get("keycloak_subject") == subject:
            return participant
        if username and participant.get("username") == username:
            return participant
    return None


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
@app.get("/library")
@app.get("/messages")
@app.get("/certificates")
def dashboard() -> str:
    participant = _current_participant()
    return render_template(
        "dashboard.html",
        participant=participant,
        active=request.path.strip("/") or "home",
        academy_url="https://learn.abc4rd.org",
        messenger_url="https://chat.abc4rd.org",
    )


@app.get("/api/me")
def api_me() -> Response:
    participant = _current_participant()
    if participant is None:
        return jsonify({"status": "pending_sync"}), 404
    return jsonify(participant)


@app.get("/library/pilot-0001")
def library_reader() -> str:
    participant = _current_participant()
    return render_template("reader.html", participant=participant)


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
