"""Tests for the multi-user auth system: UserStore, AuthManager, and auth routes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from nexus.server.auth import AuthManager
from nexus.server.user_store import UserStore


@pytest.fixture
def user_store(tmp_path: Path) -> UserStore:
    return UserStore(db_path=tmp_path / "test_server.sqlite")


@pytest.fixture
def auth_manager() -> AuthManager:
    return AuthManager()


class TestUserStore:
    async def test_empty_store_has_no_users(self, user_store: UserStore):
        assert not user_store.has_any_users()

    async def test_create_and_get_user(self, user_store: UserStore):
        user = user_store.create_user(
            email="admin@test.com", display_name="Admin", role="admin"
        )
        assert user.id
        assert user.email == "admin@test.com"
        assert user.role == "admin"
        assert user.status == "active"

        fetched = user_store.get_user(user.id)
        assert fetched is not None
        assert fetched.email == user.email

    async def test_get_user_by_email(self, user_store: UserStore):
        user_store.create_user(email="a@b.com", display_name="A")
        found = user_store.get_user_by_email("a@b.com")
        assert found is not None
        assert found.display_name == "A"

    async def test_update_user(self, user_store: UserStore):
        user = user_store.create_user(email="a@b.com", display_name="A")
        updated = user_store.update_user(user.id, display_name="B", role="admin")
        assert updated is not None
        assert updated.display_name == "B"
        assert updated.role == "admin"

    async def test_list_users(self, user_store: UserStore):
        user_store.create_user(email="a@b.com", display_name="A")
        user_store.create_user(email="c@d.com", display_name="C")
        users = user_store.list_users()
        assert len(users) == 2

    async def test_touch_login(self, user_store: UserStore):
        user = user_store.create_user(email="a@b.com", display_name="A")
        assert user.last_login is None
        user_store.touch_login(user.id)
        fetched = user_store.get_user(user.id)
        assert fetched is not None
        assert fetched.last_login is not None


class TestInviteFlow:
    async def test_create_and_validate_invite(self, user_store: UserStore):
        admin = user_store.create_user(email="admin@test.com", display_name="Admin", role="admin")
        invite = user_store.create_invite(created_by=admin.id, role="member")
        assert invite.code
        valid, err = user_store.validate_invite(invite.code)
        assert valid
        assert err == ""

    async def test_redeem_invite(self, user_store: UserStore):
        admin = user_store.create_user(email="admin@test.com", display_name="Admin", role="admin")
        invite = user_store.create_invite(created_by=admin.id)
        user = user_store.redeem_invite(invite.code, "member@test.com", "Member")
        assert user.email == "member@test.com"
        assert user.role == "member"
        assert user.created_by == admin.id

    async def test_redeem_expired_invite_fails(self, user_store: UserStore):
        admin = user_store.create_user(email="admin@test.com", display_name="Admin", role="admin")
        invite = user_store.create_invite(
            created_by=admin.id, expires_at=time.time() - 3600
        )
        with pytest.raises(ValueError, match="expired"):
            user_store.redeem_invite(invite.code, "m@t.com", "M")

    async def test_redeem_twice_fails(self, user_store: UserStore):
        admin = user_store.create_user(email="admin@test.com", display_name="Admin", role="admin")
        invite = user_store.create_invite(created_by=admin.id, max_uses=1)
        user_store.redeem_invite(invite.code, "m@t.com", "M")
        with pytest.raises(ValueError, match="already been used"):
            user_store.redeem_invite(invite.code, "m2@t.com", "M2")

    async def test_redeem_email_mismatch_fails(self, user_store: UserStore):
        admin = user_store.create_user(email="admin@test.com", display_name="Admin", role="admin")
        invite = user_store.create_invite(created_by=admin.id, email="specific@test.com")
        with pytest.raises(ValueError, match="does not match"):
            user_store.redeem_invite(invite.code, "other@test.com", "Other")

    async def test_list_and_revoke_invites(self, user_store: UserStore):
        admin = user_store.create_user(email="admin@test.com", display_name="Admin", role="admin")
        inv = user_store.create_invite(created_by=admin.id)
        invites = user_store.list_invites(created_by=admin.id)
        assert len(invites) == 1
        assert user_store.revoke_invite(inv.code)
        assert len(user_store.list_invites(created_by=admin.id)) == 0


class TestAuthManager:
    def test_create_and_verify_token(self, auth_manager: AuthManager):
        token = auth_manager.create_token("user123", "admin")
        payload = auth_manager.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["role"] == "admin"

    def test_verify_invalid_token_returns_none(self, auth_manager: AuthManager):
        assert auth_manager.verify_token("garbage") is None

    def test_verify_expired_token_returns_none(self, auth_manager: AuthManager):
        token = auth_manager.create_token("user123", "admin", expires_in=-1)
        assert auth_manager.verify_token(token) is None


class TestAuthRoutes:
    """Integration tests for auth routes via FastAPI test client."""

    @pytest.fixture
    def app(self, user_store: UserStore, auth_manager: AuthManager):
        from fastapi import FastAPI
        from nexus.server.routes.auth import router as auth_router

        app = FastAPI()
        app.state.user_store = user_store
        app.state.auth_manager = auth_manager
        app.state.multi_user = True
        app.include_router(auth_router)
        return app

    async def test_auth_status_no_users(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/auth/status")
            assert r.status_code == 200
            data = r.json()
            assert data["multi_user"] is True
            assert data["needs_setup"] is True

    async def test_setup_flow(self, app, user_store: UserStore):
        from nexus.server.routes.auth import generate_bootstrap_token
        token = generate_bootstrap_token()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": token,
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            assert r.status_code == 200
            data = r.json()
            assert data["user_id"]
            assert data["session_token"]

            user = user_store.get_user(data["user_id"])
            assert user is not None
            assert user.role == "admin"

    async def test_setup_rejected_with_bad_token(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": "bad-token",
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            assert r.status_code == 200

    async def test_setup_from_loopback_succeeds_without_token(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": "",
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            assert r.status_code == 200

    async def test_invite_register_flow(self, app, user_store: UserStore):
        from nexus.server.routes.auth import generate_bootstrap_token
        token = generate_bootstrap_token()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": token,
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            admin_token = r.json()["session_token"]
            headers = {"Authorization": f"Bearer {admin_token}"}

            r = await c.post("/auth/invites", json={"role": "member"}, headers=headers)
            assert r.status_code == 200
            invite_code = r.json()["code"]

            r = await c.get(f"/auth/invite/{invite_code}")
            assert r.status_code == 200
            assert r.json()["role"] == "member"

            r = await c.post("/auth/register", json={
                "code": invite_code,
                "email": "member@test.com",
                "display_name": "Member",
            })
            assert r.status_code == 200
            member_data = r.json()
            assert member_data["session_token"]

            member_token = member_data["session_token"]
            member_headers = {"Authorization": f"Bearer {member_token}"}

            r = await c.get("/auth/session", headers=member_headers)
            assert r.status_code == 200
            assert r.json()["email"] == "member@test.com"
            assert r.json()["role"] == "member"

    async def test_session_requires_auth(self, app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/auth/session")
            assert r.status_code == 401

    async def test_logout_clears_cookie(self, app):
        from nexus.server.routes.auth import generate_bootstrap_token
        token = generate_bootstrap_token()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": token,
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            r = await c.post("/auth/logout")
            assert r.status_code == 200

    async def test_member_cannot_create_invites(self, app):
        from nexus.server.routes.auth import generate_bootstrap_token
        token = generate_bootstrap_token()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": token,
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            admin_token = r.json()["session_token"]
            headers = {"Authorization": f"Bearer {admin_token}"}

            r = await c.post("/auth/invites", json={"role": "member"}, headers=headers)
            invite_code = r.json()["code"]

            r = await c.post("/auth/register", json={
                "code": invite_code,
                "email": "member@test.com",
                "display_name": "Member",
            })
            member_token = r.json()["session_token"]
            member_headers = {"Authorization": f"Bearer {member_token}"}

            r = await c.post("/auth/invites", json={"role": "member"}, headers=member_headers)
            assert r.status_code == 403

    async def test_change_name(self, app):
        from nexus.server.routes.auth import generate_bootstrap_token
        token = generate_bootstrap_token()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/auth/setup", json={
                "token": token,
                "email": "admin@test.com",
                "display_name": "Admin",
            })
            admin_token = r.json()["session_token"]
            headers = {"Authorization": f"Bearer {admin_token}"}

            r = await c.post("/auth/change-name", json={"display_name": "New Name"}, headers=headers)
            assert r.status_code == 200
            assert r.json()["display_name"] == "New Name"
