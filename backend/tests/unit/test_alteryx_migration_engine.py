from app.services.alteryx_migration_engine import _alteryx_filter_to_sql, _formula_to_sql


def test_formula_to_sql_translates_tonumber_to_safe_cast_numeric():
    assert _formula_to_sql("ToNumber([MetricA]) + ToNumber([MetricB])") == (
        "safe_cast(`MetricA` as numeric) + safe_cast(`MetricB` as numeric)"
    )


def test_formula_to_sql_translates_tostring_to_cast_string():
    assert _formula_to_sql("ToString([MetricA])") == "cast(`MetricA` as string)"


def test_filter_to_sql_translates_tonumber_in_filter():
    assert _alteryx_filter_to_sql("ToNumber([Quantity]) >= 10") == "safe_cast(`Quantity` as numeric) >= 10"
