from conftest import ADMIN, login, make_user


def test_api_requires_authentication(client):
    for path in ("/api/metrics/realtime", "/api/inventory/devices", "/api/inventory/settings",
                 "/api/reports/capacity/data", "/api/alerts/active", "/api/reports/apps"):
        assert client.get(path).status_code == 401, path


def test_health_is_public(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_login_and_me(client):
    headers = login(client, *ADMIN)
    me = client.get("/api/auth/me", headers=headers).json()
    assert me["username"] == "admin" and me["role"] == "admin"


def test_wrong_password_is_rejected_then_locked(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    # 6e essai : verrouillé même avec le bon mot de passe
    res = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN[1]})
    assert res.status_code == 429


def test_password_spraying_from_one_ip_is_blocked(client):
    """Quelques essais sur beaucoup de comptes depuis une même adresse : bloqué après 20 échecs."""
    headers = {"X-Real-IP": "10.9.9.9"}
    for i in range(20):  # 2 essais sur 10 comptes : aucun compte n'atteint son propre seuil
        res = client.post("/api/auth/login", json={"username": f"compte{i // 2}", "password": "Ete2026!"}, headers=headers)
        assert res.status_code == 401
    res = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN[1]}, headers=headers)
    assert res.status_code == 429  # même le bon mot de passe, depuis cette adresse
    # Une autre adresse n'est pas affectée.
    ok = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN[1]}, headers={"X-Real-IP": "10.9.9.10"})
    assert ok.status_code == 200


def test_unknown_user_gets_same_error(client):
    res = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Identifiant ou mot de passe incorrect."


def test_invalid_token_rejected(client):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_viewer_cannot_write(client, admin_headers):
    viewer = make_user(client, admin_headers, "lecteur", "viewer")
    assert client.get("/api/inventory/devices", headers=viewer).status_code == 200
    assert client.put("/api/inventory/settings/smtp_server", headers=viewer, json={"value": "evil.example"}).status_code == 403
    assert client.post("/api/inventory/devices", headers=viewer, json={}).status_code in (403, 422)
    assert client.get("/api/reports/capacity/csv", headers=viewer).status_code == 403
    assert client.get("/api/auth/users", headers=viewer).status_code == 403


def test_operator_can_export_but_not_admin(client, admin_headers):
    op = make_user(client, admin_headers, "operateur", "operator")
    assert client.get("/api/reports/capacity/csv", headers=op).status_code == 200
    assert client.put("/api/inventory/settings/polling_interval", headers=op, json={"value": "60"}).status_code == 403


def test_password_policy(client, admin_headers):
    res = client.post("/api/auth/users", headers=admin_headers, json={"username": "faible", "password": "short", "role": "viewer"})
    assert res.status_code == 400


def test_password_change_revokes_old_tokens(client, admin_headers):
    op = make_user(client, admin_headers, "op2", "operator")
    res = client.post("/api/auth/me/password", headers=op,
                      json={"current_password": "User-Passw0rd!x", "new_password": "New-Passw0rd!xyz"})
    assert res.status_code == 204
    assert client.get("/api/auth/me", headers=op).status_code == 401


def test_last_admin_cannot_be_demoted(client, admin_headers):
    me = client.get("/api/auth/me", headers=admin_headers).json()
    res = client.put(f"/api/auth/users/{me['id']}", headers=admin_headers, json={"role": "viewer"})
    assert res.status_code == 400


def test_audit_log_records_actions(client, admin_headers):
    client.put("/api/inventory/settings/polling_interval", headers=admin_headers, json={"value": "120"})
    events = client.get("/api/auth/audit", headers=admin_headers).json()
    assert any(e["action"] == "setting.updated" and e["target"] == "polling_interval" for e in events)
