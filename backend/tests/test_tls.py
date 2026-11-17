"""Vérification TLS / SSH des équipements — serveur HTTPS local avec certificat auto-signé."""
import datetime as dt
import hashlib
import http.server
import ipaddress
import ssl
import threading

import paramiko
import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.services import tls


@pytest.fixture(scope="module")
def https_server(tmp_path_factory):
    """Serveur HTTPS sur 127.0.0.1, certificat auto-signé pour « localhost » / 127.0.0.1."""
    d = tmp_path_factory.mktemp("tls")
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                         x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    cert_pem, key_pem = d / "cert.pem", d / "key.pem"
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_pem, key_pem)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    yield {"port": srv.server_address[1], "fingerprint": fp, "ca": str(cert_pem)}
    srv.shutdown()


def _get(policy, port, host="localhost"):
    return tls.http_session(policy).get(f"https://{host}:{port}/", timeout=5)


def test_fingerprint_normalisation():
    fp = "AB:" * 31 + "CD"
    assert tls.normalize_fingerprint(fp) == ("ab" * 31) + "cd"
    assert tls.normalize_fingerprint("") is None
    with pytest.raises(ValueError):
        tls.normalize_fingerprint("1234")


def test_verify_mode_rejects_self_signed(https_server, monkeypatch):
    monkeypatch.delenv("DEVICE_CA_BUNDLE", raising=False)
    with pytest.raises(requests.exceptions.SSLError):
        _get(tls.TlsPolicy("verify"), https_server["port"])


def test_verify_mode_accepts_internal_ca(https_server, monkeypatch):
    monkeypatch.setenv("DEVICE_CA_BUNDLE", https_server["ca"])
    assert _get(tls.TlsPolicy("verify"), https_server["port"]).text == "ok"


def test_pinned_fingerprint_accepts_only_the_right_certificate(https_server):
    ok = tls.TlsPolicy("pin", https_server["fingerprint"])
    assert _get(ok, https_server["port"], host="127.0.0.1").text == "ok"  # IP sans nom : l'empreinte suffit
    with pytest.raises(requests.exceptions.SSLError):
        _get(tls.TlsPolicy("pin", "0" * 64), https_server["port"])


def test_pin_mode_requires_a_fingerprint():
    with pytest.raises(ValueError):
        tls.http_session(tls.TlsPolicy("pin", None))


def test_insecure_mode_still_connects(https_server):
    assert _get(tls.TlsPolicy("insecure"), https_server["port"]).text == "ok"


def test_inspect_reads_certificate(https_server, monkeypatch):
    monkeypatch.delenv("DEVICE_CA_BUNDLE", raising=False)
    info = tls.inspect_tls("127.0.0.1", https_server["port"])
    assert info["sha256"] == https_server["fingerprint"]
    assert info["self_signed"] is True and info["trusted"] is False and info["expired"] is False
    assert "localhost" in info["subject_alt_names"]


def test_vsphere_kwargs():
    assert tls.vsphere_connect_kwargs(tls.TlsPolicy("verify"))["sslContext"].verify_mode == ssl.CERT_REQUIRED
    pinned = tls.vsphere_connect_kwargs(tls.TlsPolicy("pin", "a" * 64))
    assert pinned["thumbprint"] == "a" * 64 and pinned["sslContext"].check_hostname is True
    assert tls.vsphere_connect_kwargs(tls.TlsPolicy("insecure"))["sslContext"].verify_mode == ssl.CERT_NONE


def test_ssh_host_key_pinning():
    good = paramiko.RSAKey.generate(1024)
    other = paramiko.RSAKey.generate(1024)
    policy = tls.ssh_host_key_policy(tls.TlsPolicy("pin", tls.ssh_key_fingerprint(good)))
    policy.missing_host_key(None, "veritas", good)  # accepté
    with pytest.raises(paramiko.SSHException):
        policy.missing_host_key(None, "veritas", other)
    with pytest.raises(ValueError):
        tls.ssh_host_key_policy(tls.TlsPolicy("verify", None))
    assert isinstance(tls.ssh_host_key_policy(tls.TlsPolicy("insecure")), paramiko.AutoAddPolicy)


# ── API d'inventaire ────────────────────────────────────────────────────────
BASE = {"display_name": "Unity", "hostname_ip": "unity.example.local", "device_type": "DELL_UNITY",
        "port": 443, "username": "svc", "password": "x"}


def test_api_tls_rules(client, admin_headers):
    post = lambda **kw: client.post("/api/inventory/devices", headers=admin_headers, json={**BASE, **kw})
    assert post(tls_mode="pin").status_code == 400                     # empreinte manquante
    assert post(tls_mode="pin", tls_fingerprint="xyz").status_code == 400
    r = post(tls_mode="pin", tls_fingerprint="AB:" * 31 + "CD")
    assert r.status_code == 201 and r.json()["tls_fingerprint"] == "ab" * 31 + "cd"

    d = client.post("/api/inventory/devices", headers=admin_headers,
                    json={**BASE, "hostname_ip": "other.example.local"}).json()
    assert d["tls_mode"] == "verify"                                     # défaut des nouveaux équipements
    upd = client.put(f"/api/inventory/devices/{d['id']}", headers=admin_headers, json={"tls_mode": "insecure"})
    assert upd.json()["tls_mode"] == "insecure"


def test_api_ssh_device_needs_host_key(client, admin_headers):
    ver = {**BASE, "device_type": "VERITAS_ACCESS", "hostname_ip": "va.example.local", "port": 22}
    assert client.post("/api/inventory/devices", headers=admin_headers, json=ver).status_code == 400
    r = client.post("/api/inventory/devices", headers=admin_headers, json={**ver, "tls_fingerprint": "c" * 64})
    assert r.status_code == 201 and r.json()["tls_mode"] == "pin"


def test_api_inspect_endpoint(client, admin_headers, https_server):
    r = client.post("/api/inventory/tls/inspect", headers=admin_headers,
                    json={"hostname_ip": "127.0.0.1", "port": https_server["port"], "device_type": "DELL_UNITY"})
    assert r.status_code == 200 and r.json()["sha256"] == https_server["fingerprint"]
    viewer_denied = client.post("/api/inventory/tls/inspect", json={"hostname_ip": "127.0.0.1", "port": 1, "device_type": "DELL_UNITY"})
    assert viewer_denied.status_code == 401
