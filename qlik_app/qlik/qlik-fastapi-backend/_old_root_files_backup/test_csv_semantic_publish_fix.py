from migration_api import _build_inline_text_m_expression
from powerbi_publisher import _is_file_based_source


def test_inline_csv_m_expression_forces_text_types():
    headers = ["salary_band_id", "salary_range"]
    rows = [{"salary_band_id": "S1", "salary_range": "0-3L"}]

    m_expr = _build_inline_text_m_expression(headers, rows)

    assert 'Table.FromRecords' in m_expr
    assert 'Table.TransformColumnTypes' in m_expr
    assert '{"salary_band_id", type text}' in m_expr
    assert '{"salary_range", type text}' in m_expr
    assert 'salary_band_id = "S1"' in m_expr


def test_inline_csv_source_is_treated_as_file_based():
    assert _is_file_based_source("inline_csv", "let Source = Table.FromRecords({}) in Source") is True