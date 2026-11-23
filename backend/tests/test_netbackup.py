"""NetBackup : format de connexion, jeton sans « Bearer », erreurs lisibles, aucune valeur inventée."""
from app.services.netbackup_collector import NetBackupCollector
from test_auth_suspension import Fake, _cfg, _run

CERT_ERROR = {"errorCode": 8963, "errorMessage": "The host name that you have specified to connect to the NetBackup web server is not present in the NetBackup web server certificate."}
JOBS = {"data": [
    {"attributes": {"state": "DONE", "status": 0, "policyName": "P1", "kiloBytesWritten": 1024 ** 3}},
    {"attributes": {"state": "DONE", "status": 1, "policyName": "P2"}},           # succès partiel
    {"attributes": {"state": "DONE", "status": 156, "policyName": "P3"}},         # échec
    {"attributes": {"state": "ACTIVE", "policyName": "P4"}},                      # en cours, pas de statut
]}


def _nbu(monkeypatch, routes, user="ADCM\\svc_noc"):
    fake = Fake(routes)
    payload = _run(NetBackupCollector("n", "NBU", _cfg(host="nbu-primary.adcm.orangecm", port=1556, username=user)), fake, monkeypatch)
    return payload, fake


def test_ad_account_login_and_raw_token(monkeypatch):
    payload, fake = _nbu(monkeypatch, {("POST", "/netbackup/login"): (201, {"token": "jwt-abc"}),
                                       ("GET", "/netbackup/admin/jobs"): (200, JOBS)})
    login = fake.calls[0]
    assert login[2] == {"userName": "svc_noc", "password": "faux", "domainType": "NT", "domainName": "ADCM"}
    m = payload["metrics"]
    assert (m["jobs_total"], m["jobs_failed"], m["jobs_active"]) == (4, 1, 1)
    assert m["backup_success_rate"] == round(2 / 3 * 100, 2)     # sur les jobs terminés seulement
    assert "media_servers_total" not in m                         # illisible (404) : vide, pas 0
    assert any(a["severity"] == "INFO" and "media servers (HTTP 404)" in a["message"] for a in payload["alerts"])


def test_local_account_uses_unixpwd(monkeypatch):
    _, fake = _nbu(monkeypatch, {("POST", "/netbackup/login"): (201, {"token": "t"})}, user="svc_noc")
    assert fake.calls[0][2]["domainType"] == "unixpwd" and fake.calls[0][2]["domainName"] == "nbu-primary.adcm.orangecm"


def test_token_sent_without_bearer(monkeypatch):
    seen = []

    class Spy(Fake):
        def send(self, request, **kw):
            if request.path_url.startswith("/netbackup/admin/jobs"):
                seen.append(request.headers.get("Authorization"))
            return super().send(request, **kw)
    from test_auth_suspension import _run as run
    run(NetBackupCollector("n", "NBU", _cfg(port=1556, username="ADCM\\svc")),
        Spy({("POST", "/netbackup/login"): (201, {"token": "jwt-abc"}), ("GET", "/netbackup/admin/jobs"): (200, JOBS)}), monkeypatch)
    assert seen == ["jwt-abc"]


def test_netbackup_error_message_is_shown(monkeypatch):
    payload, _ = _nbu(monkeypatch, {("POST", "/netbackup/login"): (400, CERT_ERROR)})
    msg = payload["alerts"][0]["message"]
    assert "not present in the NetBackup web server certificate" in msg and "code 8963" in msg
    assert "auth_failed" not in payload            # erreur d'adresse, pas d'identifiants


def test_refused_credentials_suspend(monkeypatch):
    payload, _ = _nbu(monkeypatch, {("POST", "/netbackup/login"): (401, {"errorCode": 8, "errorMessage": "Invalid credentials"})})
    assert payload["auth_failed"] is True


def test_no_jobs_means_unknown_rate_not_100(monkeypatch):
    payload, _ = _nbu(monkeypatch, {("POST", "/netbackup/login"): (201, {"token": "t"}),
                                    ("GET", "/netbackup/admin/jobs"): (200, {"data": []})})
    assert "backup_success_rate" not in payload["metrics"] and payload["metrics"]["jobs_total"] == 0


def test_account_formats():
    parse = NetBackupCollector.parse_account
    assert parse("svc", "nbu") == ("unixpwd", "nbu", "svc")
    assert parse("ADCM\\svc", "nbu") == ("NT", "ADCM", "svc")
    assert parse("ldap:adcm.orangecm\\svc", "nbu") == ("ldap", "adcm.orangecm", "svc")
    assert parse("LDAP:adcm.orangecm\\svc", "nbu") == ("ldap", "adcm.orangecm", "svc")
    assert parse("vx:NBU_DOMAIN\\svc", "nbu") == ("vx", "NBU_DOMAIN", "svc")
    import pytest
    with pytest.raises(ValueError):
        parse("kerberos:X\\svc", "nbu")
