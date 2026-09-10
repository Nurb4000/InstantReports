"""Integration tests for API routes."""
import pytest
from httpx import AsyncClient


class TestAuthRoutes:
    """Test authentication routes."""

    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient, test_user):
        """Should login successfully with correct credentials."""
        response = await client.post(
            "/auth/login",
            data={"email": "user@example.com", "password": "password123"},
            follow_redirects=False,
        )
        
        assert response.status_code == 302
        assert "access_token" in response.cookies

    @pytest.mark.asyncio
    async def test_login_failure(self, client: AsyncClient):
        """Should fail login with incorrect credentials."""
        response = await client.post(
            "/auth/login",
            data={"email": "wrong@example.com", "password": "wrongpass"},
        )
        
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_logout(self, auth_client: AsyncClient):
        """Should logout successfully."""
        response = await auth_client.get("/auth/logout")
        
        assert response.status_code == 302
        assert "access_token" not in response.cookies


class TestDesignerRoutes:
    """Test designer routes."""

    @pytest.mark.asyncio
    async def test_list_reports(self, auth_client: AsyncClient):
        """Should list reports for authenticated user."""
        response = await auth_client.get("/designer/reports")
        
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_create_report(self, auth_client: AsyncClient):
        """Should create a new report."""
        response = await auth_client.post(
            "/designer/reports",
            data={"name": "Test Report", "description": "A test report"},
        )
        
        assert response.status_code == 200 or response.status_code == 303

    @pytest.mark.asyncio
    async def test_edit_report(self, auth_client: AsyncClient):
        """Should edit an existing report."""
        # First create a report
        create_response = await auth_client.post(
            "/designer/reports",
            data={"name": "Edit Test"},
        )
        
        if create_response.status_code in [200, 303]:
            # Get the report ID from the response (303 redirect -> Location header; 200 -> JSON body)
            if create_response.status_code == 303:
                report_id = create_response.headers.get("location", "").split("/")[-1]
            else:
                report_id = create_response.json().get("id")
            
            # Edit the report (update route redirects to the report view)
            response = await auth_client.post(
                f"/designer/reports/{report_id}",
                data={"name": "Updated Name"},
            )
            
            assert response.status_code in [200, 303]

    @pytest.mark.asyncio
    async def test_admin_sees_all_reports(self, auth_client, db_session):
        """Admins must see every report in the system, not just ones they created."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        other = User(
            id=uuid.uuid4(), email="other@example.com", name="Other",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add(other)
        await db_session.commit()

        foreign = Report(id=uuid.uuid4(), name="Foreign Created Report", created_by=other.id)
        db_session.add(foreign)
        await db_session.commit()

        listing = await auth_client.get("/designer/reports")
        assert listing.status_code == 200
        assert "Foreign Created Report" in listing.text

    @pytest.mark.asyncio
    async def test_designer_sees_only_own_reports(self, client, db_session):
        """Non-admins only see reports they created; others stay hidden."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        designer = User(
            id=uuid.uuid4(), email="dev@example.com", name="Dev",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add(designer)
        await db_session.commit()

        colleague = User(
            id=uuid.uuid4(), email="colleague@example.com", name="Colleague",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add(colleague)
        await db_session.commit()

        not_mine = Report(id=uuid.uuid4(), name="Not Mine Report", created_by=colleague.id)
        db_session.add(not_mine)
        await db_session.commit()

        login = await client.post(
            "/auth/login",
            data={"email": "dev@example.com", "password": "pw"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        client.headers["Cookie"] = f"access_token={login.cookies.get('access_token')}"

        listing = await client.get("/designer/reports")
        assert listing.status_code == 200
        assert "Not Mine Report" not in listing.text

    @pytest.mark.asyncio
    async def test_designer_index_scopes_to_own_reports(self, client, db_session):
        """The designer landing page must scope non-admins to their own reports,
        matching list_reports. Without this a designer sees every report in the
        system, leaking other teams' report names/IDs."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        dev = User(
            id=uuid.uuid4(), email="dev2@example.com", name="Dev2",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        colleague = User(
            id=uuid.uuid4(), email="colleague2@example.com", name="Colleague2",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        for u in (dev, colleague):
            db_session.add(u)
        await db_session.commit()

        foreign = Report(id=uuid.uuid4(), name="Colleague Report", created_by=colleague.id)
        db_session.add(foreign)
        await db_session.commit()

        login = await client.post(
            "/auth/login",
            data={"email": "dev2@example.com", "password": "pw"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        client.headers["Cookie"] = f"access_token={login.cookies.get('access_token')}"

        index = await client.get("/designer/")
        assert index.status_code == 200
        assert "Colleague Report" not in index.text

    @pytest.mark.asyncio
    async def test_report_list_defaults_to_active(self, client, db_session):
        """The list must default to ACTIVE reports only; inactive reports stay
        hidden unless the user explicitly asks for All/Inactive."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        dev = User(
            id=uuid.uuid4(), email="dev-active@example.com", name="DevActive",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add(dev)
        await db_session.commit()

        active = Report(id=uuid.uuid4(), name="Active Report", created_by=dev.id, is_active=True)
        inactive = Report(id=uuid.uuid4(), name="Inactive Report", created_by=dev.id, is_active=False)
        db_session.add_all([active, inactive])
        await db_session.commit()

        login = await client.post(
            "/auth/login",
            data={"email": "dev-active@example.com", "password": "pw"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        client.headers["Cookie"] = f"access_token={login.cookies.get('access_token')}"

        listing = await client.get("/designer/reports")
        assert listing.status_code == 200
        assert "Active Report" in listing.text
        assert "Inactive Report" not in listing.text

    @pytest.mark.asyncio
    async def test_report_list_all_filter_shows_inactive(self, client, db_session):
        """An explicit 'All' status filter must show both active and inactive."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        dev = User(
            id=uuid.uuid4(), email="dev-all@example.com", name="DevAll",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add(dev)
        await db_session.commit()

        active = Report(id=uuid.uuid4(), name="Active All", created_by=dev.id, is_active=True)
        inactive = Report(id=uuid.uuid4(), name="Inactive All", created_by=dev.id, is_active=False)
        db_session.add_all([active, inactive])
        await db_session.commit()

        login = await client.post(
            "/auth/login",
            data={"email": "dev-all@example.com", "password": "pw"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        client.headers["Cookie"] = f"access_token={login.cookies.get('access_token')}"

        listing = await client.get("/designer/reports", params={"status_filter": ""})
        assert listing.status_code == 200
        assert "Active All" in listing.text
        assert "Inactive All" in listing.text

    @pytest.mark.asyncio
    async def test_report_list_inactive_filter(self, client, db_session):
        """An explicit 'inactive' status filter shows only inactive reports."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        dev = User(
            id=uuid.uuid4(), email="dev-in@example.com", name="DevIn",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add(dev)
        await db_session.commit()

        active = Report(id=uuid.uuid4(), name="Active In", created_by=dev.id, is_active=True)
        inactive = Report(id=uuid.uuid4(), name="Inactive In", created_by=dev.id, is_active=False)
        db_session.add_all([active, inactive])
        await db_session.commit()

        login = await client.post(
            "/auth/login",
            data={"email": "dev-in@example.com", "password": "pw"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        client.headers["Cookie"] = f"access_token={login.cookies.get('access_token')}"

        listing = await client.get("/designer/reports", params={"status_filter": "inactive"})
        assert listing.status_code == 200
        assert "Inactive In" in listing.text
        assert "Active In" not in listing.text

    @pytest.mark.asyncio
    async def test_non_admin_filter_keeps_ownership_scope(self, client, db_session):
        """THE core filter bug: a non-admin who applies any filter must NOT see
        other users' reports. Previously ownership scoping was dropped whenever a
        filter was present, producing 'random' result sets."""
        import uuid

        from app.auth import hash_password
        from app.models.report import Report
        from app.models.user import AuthSource, User, UserRole

        dev = User(
            id=uuid.uuid4(), email="dev-scope@example.com", name="DevScope",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        colleague = User(
            id=uuid.uuid4(), email="colleague-scope@example.com", name="ColleagueScope",
            password_hash=hash_password("pw"), role=UserRole.DESIGNER,
            auth_source=AuthSource.LOCAL, is_active=True,
        )
        db_session.add_all([dev, colleague])
        await db_session.commit()

        my_active = Report(id=uuid.uuid4(), name="My Scope Report", created_by=dev.id, is_active=True)
        foreign = Report(id=uuid.uuid4(), name="Foreign Scope Report", created_by=colleague.id, is_active=True)
        db_session.add_all([my_active, foreign])
        await db_session.commit()

        login = await client.post(
            "/auth/login",
            data={"email": "dev-scope@example.com", "password": "pw"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        client.headers["Cookie"] = f"access_token={login.cookies.get('access_token')}"

        # Filter by status/search/sort — none of these may widen scope.
        listing = await client.get(
            "/designer/reports",
            params={"search": "Report", "status_filter": "active", "sort_by": "updated_at"},
        )
        assert listing.status_code == 200
        assert "My Scope Report" in listing.text
        assert "Foreign Scope Report" not in listing.text

        # Even an explicit 'All' status filter must stay scoped to own reports.
        listing_all = await client.get("/designer/reports", params={"status_filter": ""})
        assert listing_all.status_code == 200
        assert "My Scope Report" in listing_all.text
        assert "Foreign Scope Report" not in listing_all.text


class TestVersionRoutes:
    """Test version history routes."""

    @pytest.mark.asyncio
    async def test_list_versions(self, auth_client: AsyncClient):
        """Should list versions for a report."""
        # Create a report first
        create_response = await auth_client.post(
            "/designer/reports",
            data={"name": "Version Test"},
        )
        
        if create_response.status_code in [200, 303]:
            if create_response.status_code == 303:
                report_id = create_response.headers.get("location", "").split("/")[-1]
            else:
                report_id = create_response.json().get("id")
            
            response = await auth_client.get(f"/designer/reports/{report_id}/versions")
            
            assert response.status_code == 200


class TestAdminRoutes:
    """Test admin routes."""

    @pytest.mark.asyncio
    async def test_list_users(self, auth_client: AsyncClient):
        """Should list users for admin."""
        response = await auth_client.get("/admin/api/users")
        
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_create_user(self, auth_client: AsyncClient):
        """Should create a new user."""
        response = await auth_client.post(
            "/admin/api/users",
            data={
                "name": "New User",
                "email": "new@example.com",
                "password": "password123",
                "role": "viewer"
            },
        )
        
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_list_schedules(self, auth_client: AsyncClient):
        """Should list schedules."""
        response = await auth_client.get("/admin/api/schedules")
        
        assert response.status_code == 200


class TestAPIDocs:
    """Test API documentation endpoints."""

    @pytest.mark.asyncio
    async def test_openapi_schema(self, client: AsyncClient):
        """Should return OpenAPI schema."""
        response = await client.get("/openapi.json")
        
        assert response.status_code == 200
        assert "info" in response.json()
        assert "paths" in response.json()

    @pytest.mark.asyncio
    async def test_swagger_ui(self, client: AsyncClient):
        """Should serve Swagger UI."""
        response = await client.get("/docs")
        
        assert response.status_code == 200
        assert "swagger" in response.text.lower() or "redoc" in response.text.lower()

    @pytest.mark.asyncio
    async def test_health_check(self, client: AsyncClient):
        """Should return health status."""
        response = await client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
