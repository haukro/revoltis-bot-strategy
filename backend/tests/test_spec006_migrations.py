from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def test_spec006_apply_sql_never_delegates_to_legacy_order_first_apply():
    sql = (MIGRATIONS / "014_spec006_fence_apply.sql").read_text(encoding="utf-8")
    assert "create or replace function public.paper_spec006_apply_fill" in sql.lower()
    assert "public.paper_apply_fill(" not in sql


def test_spec006_exit_attempt_sql_binds_remaining_locked_base_qty():
    sql = (MIGRATIONS / "027_spec006_exit_attempt_locked_base_qty.sql").read_text(encoding="utf-8")
    assert "remaining_base_qty" in sql
    assert "v_pos.quantity" in sql
