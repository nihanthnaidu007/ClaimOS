"""Email delivery tests (F1): pre-written templates, both drivers, FNOL hook.

AC-1.1 — the SmtpEmailDriver renders both MIME parts from the pre-written
templates and sends over a real SMTP conversation (a fake SMTP server speaking
the protocol on localhost; stdlib-only, since Python 3.12 removed smtpd and
the audit pins "no new dependencies"). The console driver's structured log
event is asserted byte-identical — field set and values — so dev/CI behavior
cannot drift.

AC-1.2 — FNOL with a contact email delivers the access-code email; claims
without an email skip delivery silently (no error, no driver call).
"""

import asyncio
import email
import email.policy
import socket
import threading
from datetime import datetime, timezone

import pytest
from starlette.testclient import TestClient

import server
from app.config import settings
from app.notifications import emails
from app.notifications.provider import (
    ConsoleDriver,
    DisabledDriver,
    Notification,
    SmtpEmailDriver,
    get_driver,
)
from app.notifications.templates import (
    render_access_code_email,
    render_email,
    render_milestone_email,
    render_reply_notice_email,
)

VALID_CLAIM = {
    "policyNumber": "AUTO-2024-001847",
    "holderName": "Sam Riviera",
    "incidentDate": "2026-09-17",
    "incidentType": "theft",
    "claimedAmount": 1500.0,
    "description": "Bike stolen from the courtyard outside my building overnight.",
    "contactEmail": "sam@example.com",
    "documentText": "",
}


def _run(coro):
    return asyncio.run(coro)


# ============ Fake SMTP server (stdlib-only, one connection at a time) ============


class FakeSMTPServer:
    """Just enough SMTP for smtplib: greeting, EHLO, MAIL, RCPT, DATA, QUIT.
    Captures each message's raw DATA payload."""

    def __init__(self):
        self.messages: list[bytes] = []
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self.port = self._socket.getsockname()[1]
        self._socket.listen(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._socket.close()  # unblocks accept()
        self._thread.join(timeout=5)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except OSError:
                return
            try:
                self._handle(conn)
            finally:
                conn.close()

    def _handle(self, conn):
        conn.settimeout(5)
        reader = conn.makefile("rb")
        conn.sendall(b"220 claimos-fake ready\r\n")
        while True:
            line = reader.readline()
            if not line:
                return
            verb = line.decode(errors="replace").strip().upper()
            if verb.startswith("EHLO") or verb.startswith("HELO"):
                conn.sendall(b"250-claimos-fake\r\n250 OK\r\n")
            elif verb.startswith("MAIL FROM") or verb.startswith("RCPT TO"):
                conn.sendall(b"250 OK\r\n")
            elif verb == "DATA":
                conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                body: list[bytes] = []
                while True:
                    data_line = reader.readline()
                    if not data_line or data_line.strip() == b".":
                        break
                    body.append(data_line)
                self.messages.append(b"".join(body))
                conn.sendall(b"250 OK\r\n")
            elif verb == "QUIT":
                conn.sendall(b"221 Bye\r\n")
                return
            else:
                conn.sendall(b"250 OK\r\n")


@pytest.fixture
def fake_smtp(monkeypatch):
    server_ = FakeSMTPServer()
    server_.start()
    monkeypatch.setattr(settings, "email_provider", "smtp")
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", server_.port)
    monkeypatch.setattr(settings, "smtp_from", "no-reply@claimos.test")
    monkeypatch.setattr(settings, "smtp_use_tls", False)  # fake server speaks plain SMTP
    monkeypatch.setattr(settings, "smtp_ssl", False)
    monkeypatch.setattr(settings, "smtp_username", "")
    yield server_
    server_.stop()


def _single_message(fake_server) -> email.message.EmailMessage:
    assert len(fake_server.messages) == 1, fake_server.messages
    return email.message_from_bytes(
        fake_server.messages[0], policy=email.policy.default
    )


def _parts(message: email.message.EmailMessage) -> dict[str, str]:
    return {
        part.get_content_type(): part.get_content()
        for part in message.walk()
        if part.get_content_type() in ("text/plain", "text/html")
    }


# ============ Templates (pure) ============


@pytest.mark.parametrize(
    "milestone", ["submitted", "documents_received", "decision_ready", "payout_recorded"]
)
def test_every_milestone_renders_both_formats(milestone):
    rendered = render_milestone_email(milestone, "CLM-20260917-100")
    assert "CLM-20260917-100" in rendered.subject
    assert "CLM-20260917-100" in rendered.text
    assert "CLM-20260917-100" in rendered.html
    assert rendered.text != rendered.html  # two formats, one message
    assert "<html" in rendered.html


def test_unknown_template_key_raises():
    with pytest.raises(ValueError, match="unknown email template"):
        render_milestone_email("not_a_milestone", "CLM-20260917-100")


def test_access_code_email_carries_the_code():
    rendered = render_access_code_email("CLM-20260917-100", "Sarah Chen", "tok_abc-123_XYZ")
    assert rendered.subject == "Your ClaimOS access code for claim CLM-20260917-100"
    assert "tok_abc-123_XYZ" in rendered.text
    assert "tok_abc-123_XYZ" in rendered.html
    assert "Hi Sarah," in rendered.text


def test_access_code_email_escapes_hostile_names_in_html():
    rendered = render_access_code_email("CLM-20260917-100", "<script>alert(1)</script>", "code-123")
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    # The plain-text part is not HTML — the raw name is fine there.
    assert "<script>alert(1)</script>" in rendered.text


def test_reply_notice_email_truncates_and_escapes_snippet():
    rendered = render_reply_notice_email("CLM-20260917-100", "Adjuster Ada", "<b>bold</b> " + "x" * 400)
    assert "<b>bold</b>" not in rendered.html
    assert "&lt;b&gt;" in rendered.html
    snippet_start = rendered.text.index('"') + 1
    snippet = rendered.text[snippet_start : rendered.text.index('"', snippet_start)]
    assert len(snippet) == 280  # truncated preview, never the full body
    assert rendered.subject == "New message about your claim CLM-20260917-100"


def test_reply_notice_email_falls_back_to_team_sender():
    rendered = render_reply_notice_email("CLM-20260917-100", "  ", "hello")
    assert "The claims team" in rendered.text


def test_render_email_dispatches_by_key():
    assert render_email("submitted", claim_number="CLM-1") == render_milestone_email("submitted", "CLM-1")
    assert render_email("access_code", claim_number="CLM-1", access_code="c1") == render_access_code_email("CLM-1", "", "c1")
    assert render_email("reply_notice", claim_number="CLM-1", snippet="hi") == render_reply_notice_email("CLM-1", "", "hi")


# ============ AC-1.1: both drivers ============


def _notification(**overrides) -> Notification:
    now = datetime.now(timezone.utc).isoformat()
    defaults = dict(
        id="ntf_test_1",
        claim_id="CLM-20260917-100",
        recipient_email="sam@example.com",
        milestone="submitted",
        title="Claim submitted",
        body="Your claim CLM-20260917-100 has been received.",
        created_at=now,
    )
    defaults.update(overrides)
    return Notification(**defaults)


def test_smtp_driver_sends_milestone_over_real_smtp(fake_smtp):
    _run(SmtpEmailDriver().send(_notification()))

    message = _single_message(fake_smtp)
    assert message["To"] == "sam@example.com"
    assert message["From"] == "no-reply@claimos.test"
    assert message["Subject"] == "Claim CLM-20260917-100 received"
    parts = _parts(message)
    assert "CLM-20260917-100" in parts["text/plain"]
    assert "CLM-20260917-100" in parts["text/html"]


def test_smtp_driver_sends_access_code_template(fake_smtp):
    notification = _notification(
        milestone="access_code",
        template_vars={"holder_name": "Sarah Chen", "access_code": "tok_abc-123_XYZ"},
    )
    _run(SmtpEmailDriver().send(notification))

    message = _single_message(fake_smtp)
    assert message["Subject"] == "Your ClaimOS access code for claim CLM-20260917-100"
    parts = _parts(message)
    assert "tok_abc-123_XYZ" in parts["text/plain"]
    assert "tok_abc-123_XYZ" in parts["text/html"]


def test_smtp_driver_raises_when_server_is_down():
    # Reserve a port, then release it: nothing listens there.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    monkey_target = settings
    monkey_target.smtp_host = "127.0.0.1"
    monkey_target.smtp_port = dead_port
    monkey_target.smtp_from = "no-reply@claimos.test"
    monkey_target.smtp_use_tls = False
    monkey_target.smtp_ssl = False
    try:
        with pytest.raises(Exception):
            _run(SmtpEmailDriver().send(_notification()))
    finally:
        monkey_target.smtp_host = ""
        monkey_target.smtp_port = 587
        monkey_target.smtp_from = ""
        monkey_target.smtp_use_tls = True


def test_console_driver_log_event_is_byte_identical():
    """AC-1.1 regression: the default console driver's structured event keeps
    its exact field set and values — dev/CI output cannot drift silently."""
    from structlog.testing import capture_logs

    notification = _notification()
    with capture_logs() as captured:
        _run(ConsoleDriver().send(notification))

    assert len(captured) == 1
    # capture_logs injects its own log_level key; the driver's exact field set
    # and values must be byte-identical — nothing added, nothing renamed.
    event = {k: v for k, v in captured[0].items() if k != "log_level"}
    assert event == {
        "event": "notification_dispatched",
        "notification_id": "ntf_test_1",
        "claim_id": "CLM-20260917-100",
        "recipient_email": "sam@example.com",
        "milestone": "submitted",
        "title": "Claim submitted",
        "body": "Your claim CLM-20260917-100 has been received.",
        "driver": "console",
    }


def test_get_driver_resolves_by_config(monkeypatch):
    monkeypatch.setattr(settings, "email_disabled", False)
    monkeypatch.setattr(settings, "email_provider", "console")
    assert isinstance(get_driver(), ConsoleDriver)

    monkeypatch.setattr(settings, "email_provider", "smtp")
    assert isinstance(get_driver(), SmtpEmailDriver)

    monkeypatch.setattr(settings, "email_disabled", True)
    assert isinstance(get_driver(), DisabledDriver)


def test_disabled_driver_drops_silently():
    from structlog.testing import capture_logs

    with capture_logs() as captured:
        _run(DisabledDriver().send(_notification()))
    assert captured[0]["event"] == "notification_dropped"


# ============ AC-1.2: FNOL access-code email ============


class FakeDriver:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, notification: Notification) -> None:
        if self.fail:
            raise RuntimeError("driver down")
        self.sent.append(notification)


@pytest.fixture
def email_driver(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(emails, "get_driver", lambda: driver)
    return driver

@pytest.fixture
def client(patched_mongo):
    with TestClient(server.app) as test_client:
        yield test_client


def _submit(client, headers, **overrides):
    payload = {**VALID_CLAIM, **overrides}
    return client.post("/api/claims", json=payload, headers=headers)


def test_fnol_with_email_delivers_the_access_code(client, patched_mongo, email_driver, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = _submit(client, headers)

    assert response.status_code == 200, response.text
    assert response.json()["accessCode"]
    assert len(email_driver.sent) == 1
    sent = email_driver.sent[0]
    assert sent.milestone == "access_code"
    assert sent.recipient_email == "sam@example.com"
    assert sent.template_vars["access_code"] == response.json()["accessCode"]
    assert sent.template_vars["holder_name"] == "Sam Riviera"


def test_fnol_without_email_skips_delivery_silently(client, patched_mongo, email_driver, make_authenticated_user):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    response = _submit(client, headers, contactEmail="")

    assert response.status_code == 200, response.text
    # The code is still minted and returned once in the response — there is
    # just no email address to deliver it to, and that is not an error.
    assert response.json()["accessCode"]
    assert email_driver.sent == []


def test_fnol_survives_a_failing_email_driver(client, patched_mongo, make_authenticated_user, monkeypatch):
    headers, _, _ = make_authenticated_user(client, role="adjuster")
    monkeypatch.setattr(emails, "get_driver", lambda: FakeDriver(fail=True))

    response = _submit(client, headers)

    # Delivery degradation: the submission (and its event write) must succeed
    # even when the driver is down.
    assert response.status_code == 200, response.text
    assert response.json()["accessCode"]

