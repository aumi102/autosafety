"""
Tests for SQL safety validator.
"""

from app.services.sql_safety import ValidationResult, enforce_limit, validate_sql


class TestValidateSqlAllowed:
    """Tests for allowed SQL queries."""

    def test_simple_select_allowed(self):
        result = validate_sql("SELECT * FROM vehicles")
        assert result.valid is True
        assert result.reason is None

    def test_select_with_where(self):
        result = validate_sql("SELECT make, model FROM vehicles WHERE model_year = 2022")
        assert result.valid is True

    def test_select_with_limit(self):
        result = validate_sql("SELECT * FROM complaints LIMIT 50")
        assert result.valid is True

    def test_select_with_join(self):
        result = validate_sql(
            "SELECT v.make, v.model, c.summary FROM vehicles v "
            "JOIN complaints c ON c.vehicle_id = v.id "
            "WHERE v.model_year = 2022"
        )
        assert result.valid is True

    def test_select_with_aggregate(self):
        result = validate_sql(
            "SELECT component_id, COUNT(*) as count FROM complaints GROUP BY component_id"
        )
        assert result.valid is True

    def test_with_cte_allowed(self):
        result = validate_sql(
            "WITH recent AS (SELECT * FROM complaints WHERE received_date > '2024-01-01') "
            "SELECT * FROM recent"
        )
        assert result.valid is True

    def test_select_lowercase_allowed(self):
        result = validate_sql("select * from vehicles")
        assert result.valid is True

    def test_select_with_subquery(self):
        result = validate_sql(
            "SELECT * FROM vehicles WHERE id IN (SELECT vehicle_id FROM complaints)"
        )
        assert result.valid is True

    def test_explain_allowed(self):
        result = validate_sql("EXPLAIN SELECT * FROM vehicles")
        assert result.valid is True


class TestValidateSqlBlocked:
    """Tests for blocked SQL operations."""

    def test_drop_blocked(self):
        result = validate_sql("DROP TABLE vehicles")
        assert result.valid is False
        assert result.reason is not None and "DROP" in result.reason

    def test_delete_blocked(self):
        result = validate_sql("DELETE FROM vehicles WHERE id = '123'")
        assert result.valid is False
        assert result.reason is not None and "DELETE" in result.reason

    def test_update_blocked(self):
        result = validate_sql("UPDATE vehicles SET make = 'Ford' WHERE id = '123'")
        assert result.valid is False
        assert result.reason is not None and "UPDATE" in result.reason

    def test_insert_blocked(self):
        result = validate_sql("INSERT INTO vehicles (make, model) VALUES ('Ford', 'F-150')")
        assert result.valid is False
        # Validator hits one of INSERT or INTO first (set iteration order)
        assert result.reason is not None
        assert "INSERT" in result.reason or "INTO" in result.reason

    def test_alter_blocked(self):
        result = validate_sql("ALTER TABLE vehicles ADD COLUMN test TEXT")
        assert result.valid is False
        assert result.reason is not None and "ALTER" in result.reason

    def test_truncate_blocked(self):
        result = validate_sql("TRUNCATE TABLE vehicles")
        assert result.valid is False
        assert result.reason is not None and "TRUNCATE" in result.reason

    def test_create_blocked(self):
        result = validate_sql("CREATE TABLE test (id INT)")
        assert result.valid is False
        assert result.reason is not None and "CREATE" in result.reason

    def test_grant_blocked(self):
        result = validate_sql("GRANT SELECT ON vehicles TO public")
        assert result.valid is False
        assert result.reason is not None and "GRANT" in result.reason

    def test_revoke_blocked(self):
        result = validate_sql("REVOKE SELECT ON vehicles FROM public")
        assert result.valid is False
        assert result.reason is not None and "REVOKE" in result.reason

    def test_copy_blocked(self):
        result = validate_sql("COPY vehicles TO '/tmp/file.csv'")
        assert result.valid is False
        assert result.reason is not None and "COPY" in result.reason


class TestValidateSqlInjection:
    """Tests for SQL injection attempts."""

    def test_multiple_statements_blocked(self):
        result = validate_sql("SELECT * FROM vehicles; DROP TABLE vehicles;")
        assert result.valid is False
        assert result.reason is not None and ";" in result.reason

    def test_sql_comment_blocked(self):
        result = validate_sql("SELECT * FROM vehicles -- DROP TABLE")
        assert result.valid is False
        assert result.reason is not None and "--" in result.reason

    def test_block_comment_blocked(self):
        result = validate_sql("SELECT * FROM vehicles /* DROP */")
        assert result.valid is False

    def test_semicolon_injection_blocked(self):
        # Even single semicolon can be used for injection
        result = validate_sql("SELECT * FROM vehicles;")
        assert result.valid is False

    def test_lowercase_drop_blocked(self):
        result = validate_sql("drop table vehicles")
        assert result.valid is False

    def test_lowercase_delete_blocked(self):
        result = validate_sql("delete from vehicles")
        assert result.valid is False

    def test_lowercase_update_blocked(self):
        result = validate_sql("update vehicles set make = 'test'")
        assert result.valid is False

    def test_lowercase_insert_blocked(self):
        result = validate_sql("insert into vehicles (make) values ('test')")
        assert result.valid is False

    def test_xp_blocked(self):
        result = validate_sql("SELECT * FROM vehicles; EXEC xp_cmdshell 'dir')")
        assert result.valid is False


class TestEnforceLimit:
    """Tests for LIMIT enforcement."""

    def test_add_limit_to_query_without(self):
        query = "SELECT * FROM vehicles"
        result = enforce_limit(query)
        assert "LIMIT 100" in result

    def test_keep_existing_limit(self):
        query = "SELECT * FROM vehicles LIMIT 50"
        result = enforce_limit(query)
        assert "LIMIT 50" in result

    def test_reduce_excessive_limit(self):
        query = "SELECT * FROM vehicles LIMIT 1000"
        result = enforce_limit(query)
        assert "LIMIT 500" in result

    def test_respect_hard_limit(self):
        query = "SELECT * FROM complaints LIMIT 9999"
        result = enforce_limit(query)
        assert "LIMIT 500" in result

    def test_custom_default_limit(self):
        query = "SELECT * FROM vehicles"
        result = enforce_limit(query, default_limit=25)
        assert "LIMIT 25" in result


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_bool_true(self):
        result = ValidationResult(valid=True, query="SELECT 1")
        assert bool(result) is True

    def test_bool_false(self):
        result = ValidationResult(valid=False, query="DROP TABLE", reason="blocked")
        assert bool(result) is False

    def test_empty_query_invalid(self):
        result = validate_sql("")
        assert result.valid is False
        assert result.reason is not None and "Empty" in result.reason

    def test_whitespace_only_invalid(self):
        result = validate_sql("   ")
        assert result.valid is False
