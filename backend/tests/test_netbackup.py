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
    assert any(a["severity"] == "INFO" and "media servers (HTTP 404" in a["message"] for a in payload["alerts"])


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


# ── Media servers (réponse réelle de NetBackup 10.5, cmmkp-nba-master01, abrégée) ──
MEDIA_SERVERS = {"data": [
    {"type": "mediaServer", "id": "cmmkp-nba-master01", "attributes": {
        "hostName": "cmmkp-nba-master01", "isMachineUp": True, "machineType": 3,
        "status": {"name": "ACTIVE_FOR_DISK"}, "emmServerName": "cmmkp-nba-master01",
        "osType": "Linux", "osReleaseVersion": "8.10", "nbuVersion": "10.5.0.1"}},
    {"type": "mediaServer", "id": "cmmkp-nba-media01", "attributes": {
        "hostName": "cmmkp-nba-media01", "isMachineUp": True, "machineType": 1,
        "status": {"name": "ACTIVE_FOR_TAPE_AND_DISK"}, "emmServerName": "cmmkp-nba-master01",
        "osType": "Linux", "osReleaseVersion": "8.10", "nbuVersion": "10.5.0.1"}},
    {"type": "mediaServer", "id": "netbk01", "attributes": {
        "hostName": "netbk01", "isMachineUp": False, "machineType": 1, "status": {"name": "OFFLINE"},
        "emmServerName": "cmmkp-nba-master01", "osType": "Linux", "nbuVersion": "8.0"}},
    {"type": "mediaServer", "id": "netbk02", "attributes": {
        "hostName": "netbk02", "isMachineUp": True, "machineType": 1, "status": {"name": "OFFLINE"},
        "emmServerName": "cmmkp-nba-master01", "osType": "Linux", "nbuVersion": "8.0"}},
]}


class VersionedFake(Fake):
    """La liste n'est renvoyée qu'en version 12.0 de l'API (en 4.0 : liste vide, comme constaté)."""

    def send(self, request, **kw):
        resp = super().send(request, **kw)
        if request.path_url.startswith("/netbackup/config/media-servers"):
            self.ms_accept = request.headers.get("Accept")
            body = MEDIA_SERVERS if "version=12.0" in (self.ms_accept or "") else {"data": []}
            import json as _json
            resp.status_code, resp._content = 200, _json.dumps(body).encode()
        return resp


def test_media_servers_list(monkeypatch):
    fake = VersionedFake({("POST", "/netbackup/login"): (201, {"token": "t"}),
                          ("GET", "/netbackup/admin/jobs"): (200, {"data": []})})
    payload = _run(NetBackupCollector("n", "NBU", _cfg(port=1556, username="ldap:ADCM\\svc")), fake, monkeypatch)
    assert "version=12.0" in fake.ms_accept
    ms = payload["media_servers"]
    assert [m["name"] for m in ms] == ["cmmkp-nba-master01", "cmmkp-nba-media01", "netbk01", "netbk02"]
    assert ms[0] == {"name": "cmmkp-nba-master01", "role": "Primaire", "status_code": "ACTIVE_FOR_DISK",
                     "status": "Actif (disque)", "active": True, "reachable": True, "nbu_version": "10.5.0.1", "os": "Linux 8.10"}
    assert ms[1]["status"] == "Actif (bande et disque)" and ms[1]["role"] == "Media server"
    assert ms[3]["reachable"] is True and ms[3]["active"] is False   # joignable mais désactivé
    m = payload["metrics"]
    assert (m["media_servers_total"], m["media_servers_online"]) == (4, 2)
    alert = next(a for a in payload["alerts"] if a["component"] == "MediaServer")
    assert alert["message"] == "Media servers hors ligne (2/4) : netbk01, netbk02"
    assert not any("media servers" in a["message"] for a in payload["alerts"] if a["severity"] == "INFO")


def test_media_servers_fallback_when_version_unknown(monkeypatch):
    calls = []

    class Old(Fake):
        def send(self, request, **kw):
            resp = super().send(request, **kw)
            if request.path_url.startswith("/netbackup/config/media-servers"):
                calls.append(request.headers.get("Accept"))
                import json as _json
                ok = "12.0" not in request.headers.get("Accept", "")
                resp.status_code = 200 if ok else 406
                resp._content = _json.dumps(MEDIA_SERVERS if ok else {"errorMessage": "unsupported version"}).encode()
            return resp
    payload = _run(NetBackupCollector("n", "NBU", _cfg(port=1556, username="svc")),
                   Old({("POST", "/netbackup/login"): (201, {"token": "t"}), ("GET", "/netbackup/admin/jobs"): (200, {"data": []})}),
                   monkeypatch)
    assert len(calls) == 2 and payload["metrics"]["media_servers_total"] == 4


def test_jobs_filter_date_is_unquoted(monkeypatch):
    """Régression : NetBackup 10.5 rejette « startTime ge '…' » (HTTP 400, code 9401)."""
    from urllib.parse import parse_qs, urlsplit
    filters = []

    class OData(Fake):
        def send(self, request, **kw):
            if request.path_url.startswith("/netbackup/admin/jobs"):
                f = parse_qs(urlsplit(request.url).query)["filter"][0]
                filters.append(f)
                if "'" in f:
                    self.routes[("GET", "/netbackup/admin/jobs")] = (400, {"errorCode": 9401, "errorMessage": "The OData filter criteria is invalid."})
                else:
                    self.routes[("GET", "/netbackup/admin/jobs")] = (200, JOBS)
            return super().send(request, **kw)
    payload = _run(NetBackupCollector("n", "NBU", _cfg(port=1556, username="svc")),
                   OData({("POST", "/netbackup/login"): (201, {"token": "t"})}), monkeypatch)
    assert payload["status"] != "OFFLINE", payload["alerts"]
    assert len(filters) == 1 and "'" not in filters[0] and filters[0].startswith("startTime ge 20")
    assert payload["metrics"]["jobs_total"] == 4
