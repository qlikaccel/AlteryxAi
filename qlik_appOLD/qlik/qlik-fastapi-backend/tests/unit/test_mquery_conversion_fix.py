from loadscript_parser import LoadScriptParser
from mquery_converter import MQueryConverter
from migration_api import _apply_row_aware_cardinality_from_rows
from relationship_service import resolve_relationships_unified
from powerbi_publisher import _Publisher


def test_dates_derived_columns_generate_valid_m():
    converter = MQueryConverter()
    table = {
        "name": "Dates",
        "source_type": "csv",
        "source_path": "dim_date.csv",
        "fields": [
            {"name": "date_id", "alias": "date_id", "expression": "date_id", "type": "string"},
            {"name": "date_id", "alias": "full_date", "expression": "Date(Date#(date_id, 'YYYYMMDD'))", "type": "date"},
            {"name": "date_id", "alias": "year", "expression": "Year(Date#(date_id, 'YYYYMMDD'))", "type": "number"},
            {"name": "date_id", "alias": "month", "expression": "Month(Date#(date_id, 'YYYYMMDD'))", "type": "number"},
            {"name": "date_id", "alias": "day", "expression": "Day(Date#(date_id, 'YYYYMMDD'))", "type": "number"},
            {"name": "date_id", "alias": "quarter", "expression": "'Q' & Ceil(Month(Date#(date_id, 'YYYYMMDD'))/3)", "type": "string"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert '"full_date"' in m_expression
    assert 'Date.Year(Date.FromText([date_id], [Format = "yyyyMMdd"]))' in m_expression
    assert 'Date.Month(Date.FromText([date_id], [Format = "yyyyMMdd"]))' in m_expression
    assert 'Date.Day(Date.FromText([date_id], [Format = "yyyyMMdd"]))' in m_expression
    assert '"Q" & Text.From(Number.RoundUp(Date.Month(Date.FromText([date_id], [Format = "yyyyMMdd"]))/3))' in m_expression
    assert 'Removed original full_date' not in m_expression
    assert 'Replaced full_date' not in m_expression


def test_resident_if_literals_stay_as_strings():
    converter = MQueryConverter()
    table = {
        "name": "Final_Activity",
        "source_type": "resident",
        "source_path": "Activity",
        "fields": [
            {"name": "*", "alias": "*", "expression": "*", "type": "wildcard"},
            {"name": "hours_worked", "alias": "work_type", "expression": "If(hours_worked > 8, 'Overtime', 'Normal')", "type": "string"},
            {"name": "hours_worked", "alias": "productivity_flag", "expression": "If(hours_worked >= 8, 1, 0)", "type": "number"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert 'then "Overtime" else "Normal"' in m_expression
    assert '[Overtime]' not in m_expression
    assert '[Normal]' not in m_expression


def test_resident_arithmetic_column_added_before_typing():
    converter = MQueryConverter()
    table = {
        "name": "Final_Activity",
        "source_type": "resident",
        "source_path": "Activity",
        "fields": [
            {"name": "*", "alias": "*", "expression": "*", "type": "wildcard"},
            {"name": "hours_worked", "alias": "productivity_score", "expression": "hours_worked * 10", "type": "number"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert '{"hours_worked", type number}' in m_expression
    assert '"productivity_score",\n        each [hours_worked] * 10' in m_expression
    assert 'Removed original productivity_score' not in m_expression
    assert 'Replaced productivity_score' not in m_expression


def test_resident_groupby_types_numeric_source_columns_before_aggregation():
    script = r'''
Final_Activity:
LOAD employee_id, activity_id, hours_worked
FROM [lib://DataFiles/fact.csv]
(txt, utf8, embedded labels, delimiter is ',');

Employee_Summary:
LOAD employee_id,
     Sum(hours_worked) as total_hours,
     Count(activity_id) as total_days,
     Avg(hours_worked) as avg_hours
RESIDENT Final_Activity
GROUP BY employee_id;
'''

    parser = LoadScriptParser(script)
    parsed = parser.parse(
        qlik_fields_map={
            "Final_Activity": ["employee_id", "activity_id", "hours_worked"],
            "Employee_Summary": ["employee_id", "total_hours", "total_days", "avg_hours"],
        }
    )
    table = next(table for table in parsed["details"]["tables"] if table["name"] == "Employee_Summary")

    converter = MQueryConverter()
    m_expression = converter.convert_one(
        table,
        base_path="https://example.sharepoint.com",
        all_tables_list=parsed["details"]["tables"],
        qlik_fields_map={
            "Final_Activity": ["employee_id", "activity_id", "hours_worked"],
            "Employee_Summary": ["employee_id", "total_hours", "total_days", "avg_hours"],
        },
    )

    assert '{"hours_worked", type number}' in m_expression
    assert m_expression.index('{"hours_worked", type number}') < m_expression.index('List.Sum([hours_worked])')


def test_csv_if_conditions_type_numeric_source_columns_before_derived_fields():
    converter = MQueryConverter()
    table = {
        "name": "Final_Activity",
        "source_type": "csv",
        "source_path": "fact.csv",
        "fields": [
            {"name": "*", "alias": "*", "expression": "*", "type": "wildcard"},
            {"name": "hours_worked", "alias": "work_type", "expression": "If(hours_worked > 8, 'Overtime', 'Normal')", "type": "string"},
            {"name": "hours_worked", "alias": "productivity_flag", "expression": "If(hours_worked >= 8, 1, 0)", "type": "number"},
            {"name": "hours_worked", "alias": "productivity_score", "expression": "hours_worked * 10", "type": "number"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert '{"hours_worked", type number}' in m_expression
    assert m_expression.index('{"hours_worked", type number}') < m_expression.index('"work_type"')
    assert m_expression.index('{"hours_worked", type number}') < m_expression.index('"productivity_score"')


def test_qualified_passthrough_aliases_do_not_generate_dotted_columns_or_fake_derived_steps():
    converter = MQueryConverter()
    table = {
        "name": "Model_Master",
        "source_type": "csv",
        "source_path": "model_master.csv",
        "fields": [
            {"name": "ModelID", "alias": "Model_Master.ModelID", "expression": "ModelID", "type": "string"},
            {"name": "ModelName", "alias": "ModelName", "expression": "[ModelName]", "type": "string"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert '{"ModelID", type text}' in m_expression
    assert 'Model_Master.ModelID' not in m_expression
    assert 'ModelName__derived' not in m_expression
    assert 'Removed original ModelName' not in m_expression


def test_bim_one_to_one_relationship_uses_both_directions():
    publisher = _Publisher(workspace_id="workspace", access_token="token")
    tables_m = [
        {
            "name": "Vehicle_Fact_MASTER",
            "source_type": "csv",
            "m_expression": "let Source = #table(type table [VIN = text], {}) in Source",
            "fields": [{"name": "VIN", "alias": "VIN", "type": "string"}],
        },
        {
            "name": "VIN_Details",
            "source_type": "csv",
            "m_expression": "let Source = #table(type table [VIN = text], {}) in Source",
            "fields": [{"name": "VIN", "alias": "VIN", "type": "string"}],
        },
    ]
    relationships = [
        {
            "fromTable": "Vehicle_Fact_MASTER",
            "fromColumn": "VIN",
            "toTable": "VIN_Details",
            "toColumn": "VIN",
            "cardinality": "OneToOne",
            "crossFilteringBehavior": "Single",
        }
    ]

    bim_json = publisher._build_bim("Demo", tables_m, relationships, "")

    assert '"fromCardinality": "one"' in bim_json
    assert '"toCardinality": "one"' in bim_json
    assert '"crossFilteringBehavior": "bothDirections"' in bim_json


def test_bim_relationship_maps_sanitized_keys_back_to_hyphenated_columns():
    publisher = _Publisher(workspace_id="workspace", access_token="token")
    tables_m = [
        {
            "name": "Dealer_Master",
            "source_type": "csv",
            "m_expression": 'let Source = #table(type table [#"DealerID-ServiceID" = text], {}) in Source',
            "fields": [{"name": "DealerID-ServiceID", "alias": "DealerID-ServiceID", "type": "string"}],
        },
        {
            "name": "Service_History",
            "source_type": "csv",
            "m_expression": 'let Source = #table(type table [#"DealerID-ServiceID" = text], {}) in Source',
            "fields": [{"name": "DealerID-ServiceID", "alias": "DealerID-ServiceID", "type": "string"}],
        },
    ]
    relationships = [
        {
            "fromTable": "Service_History",
            "fromColumn": "DealerID_ServiceID",
            "toTable": "Dealer_Master",
            "toColumn": "DealerID_ServiceID",
            "cardinality": "ManyToOne",
            "crossFilteringBehavior": "Single",
        }
    ]

    bim_json = publisher._build_bim("Demo", tables_m, relationships, "")

    assert '"name": "Service_History_DealerID-ServiceID_Dealer_Master_DealerID-ServiceID"' in bim_json
    assert '"fromColumn": "DealerID-ServiceID"' in bim_json
    assert '"toColumn": "DealerID-ServiceID"' in bim_json


def test_relationship_fallback_avoids_measure_fields_like_service_cost():
    tables_m = [
        {
            "name": "Vehicle_Fact_MASTER",
            "fields": [
                {"name": "VIN", "alias": "VIN"},
                {"name": "ServiceDate", "alias": "ServiceDate"},
                {"name": "ServiceType", "alias": "ServiceType"},
                {"name": "ServiceCost", "alias": "ServiceCost"},
            ],
        },
        {
            "name": "Service_History",
            "fields": [
                {"name": "VehicleID", "alias": "VehicleID"},
                {"name": "ServiceDate", "alias": "ServiceDate"},
                {"name": "ServiceType", "alias": "ServiceType"},
                {"name": "ServiceCost", "alias": "ServiceCost"},
            ],
        },
    ]

    relationships = resolve_relationships_unified(tables_m)

    rel = next(
        (r for r in relationships if {r["fromTable"], r["toTable"]} == {"Vehicle_Fact_MASTER", "Service_History"}),
        None,
    )

    assert rel is not None
    assert "ServiceCost" not in {rel["fromColumn"], rel["toColumn"]}
    assert "ServiceDate" not in {rel["fromColumn"], rel["toColumn"]}


def test_inline_csv_row_aware_cardinality_marks_duplicate_variant_name_as_many_to_many():
    relationships = [
        {
            "fromTable": "Vehicle_Fact_MASTER",
            "fromColumn": "VariantName",
            "toTable": "Variant_Master",
            "toColumn": "VariantName",
            "cardinality": "ManyToOne",
            "crossFilteringBehavior": "Single",
        }
    ]

    adjusted = _apply_row_aware_cardinality_from_rows(
        relationships,
        {
            "Vehicle_Fact_MASTER": [
                {"VariantName": "XLT"},
                {"VariantName": "XLT"},
                {"VariantName": "Lariat"},
            ],
            "Variant_Master": [
                {"VariantName": "XLT", "VariantID": "2"},
                {"VariantName": "XLT", "VariantID": "11"},
                {"VariantName": "Lariat", "VariantID": "3"},
                {"VariantName": "Lariat", "VariantID": "29"},
            ],
        },
    )

    assert adjusted[0]["cardinality"] == "ManyToMany"
    assert adjusted[0]["crossFilteringBehavior"] == "Both"


def test_resident_groupby_keeps_expression_source_columns():
    script = r'''
Final_Activity:
LOAD employee_id, activity_id, hours_worked
FROM [lib://DataFiles/fact.csv]
(txt, utf8, embedded labels, delimiter is ',');

Employee_Summary:
LOAD employee_id,
     Sum(hours_worked) as total_hours,
     Count(activity_id) as total_days,
     Avg(hours_worked) as avg_hours
RESIDENT Final_Activity
GROUP BY employee_id;
'''

    parser = LoadScriptParser(script)
    parsed = parser.parse(
        qlik_fields_map={
            "Final_Activity": ["employee_id", "activity_id", "hours_worked"],
            "Employee_Summary": ["employee_id", "total_hours", "total_days", "avg_hours"],
        }
    )
    table = next(table for table in parsed["details"]["tables"] if table["name"] == "Employee_Summary")

    converter = MQueryConverter()
    m_expression = converter.convert_one(
        table,
        base_path="https://example.sharepoint.com",
        all_tables_list=parsed["details"]["tables"],
        qlik_fields_map={
            "Final_Activity": ["employee_id", "activity_id", "hours_worked"],
            "Employee_Summary": ["employee_id", "total_hours", "total_days", "avg_hours"],
        },
    )

    assert 'Table.SelectColumns(Final_Activity,' in m_expression
    assert '{"employee_id", "hours_worked", "activity_id"}' in m_expression
    assert 'List.Sum([hours_worked])' in m_expression
    assert 'List.Count([activity_id])' in m_expression


def test_explicit_schema_step_infers_numeric_hour_columns_from_qlik_fields_map():
    converter = MQueryConverter()
    converter.all_tables_list = {
        "Employee_Summary": {
            "options": {
                "qlik_columns": ["employee_id", "total_hours", "avg_hours", "total_days"]
            }
        }
    }
    converter.qlik_fields_map = {
        "Employee_Summary": ["employee_id", "total_hours", "avg_hours", "total_days"]
    }

    schema_step, final_step = converter._build_explicit_schema_step("Employee_Summary", "Headers")

    assert '{"employee_id", type text}' in schema_step
    assert '{"total_hours", type number}' in schema_step
    assert '{"avg_hours", type number}' in schema_step
    assert '{"total_days", Int64.Type}' in schema_step
    assert final_step == "TypedTable"


def test_resident_schema_injection_preserves_numeric_types_for_hour_fields():
    converter = MQueryConverter()
    table = {
        "name": "Employee_Summary",
        "source_type": "resident",
        "source_path": "Final_Activity",
        "fields": [
            {"name": "*", "alias": "*", "expression": "*", "type": "wildcard"},
        ],
        "options": {
            "qlik_columns": ["employee_id", "total_hours", "avg_hours", "total_days"]
        },
    }

    m_expression = converter.convert_one(
        table,
        base_path="https://example.sharepoint.com",
        all_tables_list=[table],
        qlik_fields_map={
            "Employee_Summary": ["employee_id", "total_hours", "avg_hours", "total_days"]
        },
    )

    assert '{"total_hours", type number}' in m_expression
    assert '{"avg_hours", type number}' in m_expression
    assert '{"total_days", Int64.Type}' in m_expression


def test_alias_preserving_transform_is_emitted():
    converter = MQueryConverter()
    table = {
        "name": "Employees",
        "source_type": "csv",
        "source_path": "dim_employee.csv",
        "fields": [
            {"name": "employee_id", "alias": None, "expression": "employee_id", "type": "string"},
            {"name": "employee_name", "alias": "employee_name", "expression": "Upper(Trim(employee_name))", "type": "string"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert 'Text.Upper(Text.Trim([employee_name]))' in m_expression
    assert '"employee_name__derived"' in m_expression
    assert 'Table.RemoveColumns(' in m_expression
    assert 'Table.RenameColumns(' in m_expression
    assert 'Table.AddColumn(\n        TypedTable,\n        "employee_name"' not in m_expression


def test_parser_retains_join_helper_and_attaches_join_to_target():
    script = r'''
Employees:
LOAD employee_id, employee_name
FROM [lib://DataFiles/dim_employee.csv]
(txt, utf8, embedded labels, delimiter is ',');

Final_Activity:
LOAD employee_id, hours_worked
FROM [lib://DataFiles/fact.csv]
(txt, utf8, embedded labels, delimiter is ',');

Employee_Summary:
LOAD employee_id, Sum(hours_worked) as total_hours
RESIDENT Final_Activity
GROUP BY employee_id;

LEFT JOIN (Employees)
LOAD employee_id, total_hours, If(total_hours > 100, 'High', 'Low') as performance_category
RESIDENT Employee_Summary;
DROP TABLE Employee_Summary;
'''

    parser = LoadScriptParser(script)
    parsed = parser.parse(
        qlik_fields_map={
            "Employees": ["employee_id", "employee_name", "total_hours", "performance_category"],
            "Final_Activity": ["employee_id", "hours_worked"],
            "Employee_Summary": ["employee_id", "total_hours"],
        }
    )

    tables_by_name = {table["name"]: table for table in parsed["details"]["tables"]}

    assert "Employee_Summary" in tables_by_name
    assert tables_by_name["Employee_Summary"]["options"].get("is_helper_table") is True
    assert len(tables_by_name["Employees"]["options"].get("post_join_loads", [])) == 1


def test_deferred_join_emits_aggregation_and_join_steps():
    script = r'''
Employees:
LOAD employee_id, employee_name
FROM [lib://DataFiles/dim_employee.csv]
(txt, utf8, embedded labels, delimiter is ',');

Final_Activity:
LOAD employee_id, hours_worked
FROM [lib://DataFiles/fact.csv]
(txt, utf8, embedded labels, delimiter is ',');

Employee_Summary:
LOAD employee_id, Sum(hours_worked) as total_hours
RESIDENT Final_Activity
GROUP BY employee_id;

LEFT JOIN (Employees)
LOAD employee_id, total_hours, If(total_hours > 100, 'High', 'Low') as performance_category
RESIDENT Employee_Summary;
DROP TABLE Employee_Summary;
'''

    qlik_fields_map = {
        "Employees": ["employee_id", "employee_name", "total_hours", "performance_category"],
        "Final_Activity": ["employee_id", "hours_worked"],
        "Employee_Summary": ["employee_id", "total_hours"],
    }
    parser = LoadScriptParser(script)
    parsed = parser.parse(qlik_fields_map=qlik_fields_map)
    tables = parsed["details"]["tables"]
    employees = next(table for table in tables if table["name"] == "Employees")

    converter = MQueryConverter()
    m_expression = converter.convert_one(
        employees,
        base_path="https://example.sharepoint.com",
        all_tables_list=tables,
        qlik_fields_map=qlik_fields_map,
    )

    assert 'Table.Group(' in m_expression
    assert 'Table.NestedJoin(' in m_expression
    assert 'performance_category' in m_expression
    assert 'if [total_hours] > 100 then "High" else "Low"' in m_expression
    assert 'each Sum([hours_worked])' not in m_expression
    assert 'each Count([activity_id])' not in m_expression


def test_date_parts_are_added_not_replaced_when_source_only_has_date_id():
    converter = MQueryConverter()
    table = {
        "name": "Dates",
        "source_type": "csv",
        "source_path": "dim_date.csv",
        "fields": [
            {"name": "date_id", "alias": "date_id", "expression": "date_id", "type": "string"},
            {"name": "date_id", "alias": "full_date", "expression": "Date(Date#(date_id, 'YYYYMMDD'))", "type": "date"},
            {"name": "date_id", "alias": "year", "expression": "Year(Date#(date_id, 'YYYYMMDD'))", "type": "number"},
            {"name": "date_id", "alias": "month", "expression": "Month(Date#(date_id, 'YYYYMMDD'))", "type": "number"},
            {"name": "date_id", "alias": "day", "expression": "Day(Date#(date_id, 'YYYYMMDD'))", "type": "number"},
        ],
        "options": {},
    }

    m_expression = converter.convert_one(table, base_path="https://example.sharepoint.com")

    assert '"Added year" = Table.AddColumn(' in m_expression
    assert '"Added month" = Table.AddColumn(' in m_expression
    assert '"Added day" = Table.AddColumn(' in m_expression
    assert 'Removed original year' not in m_expression
    assert 'Removed original month' not in m_expression
    assert 'Removed original day' not in m_expression


def test_publish_path_dates_do_not_replace_function_named_columns():
    script = r'''
Dates:
LOAD
    date_id,
    Date(Date#(date_id,'YYYYMMDD')) as full_date,
    Year(Date#(date_id,'YYYYMMDD')) as year,
    Month(Date#(date_id,'YYYYMMDD')) as month,
    Day(Date#(date_id,'YYYYMMDD')) as day,
    'Q' & Ceil(Month(Date#(date_id,'YYYYMMDD'))/3) as quarter
FROM [lib://DataFiles/dim_date.csv]
(txt, utf8, embedded labels, delimiter is ',');
'''

    qlik_fields_map = {
        'Dates': ['date_id', 'full_date', 'year', 'month', 'day', 'quarter']
    }

    parsed = LoadScriptParser(script).parse(qlik_fields_map=qlik_fields_map)
    table = next(table for table in parsed['details']['tables'] if table['name'] == 'Dates')

    m_expression = MQueryConverter().convert_one(
        table,
        base_path='https://example.sharepoint.com',
        all_tables_list=parsed['details']['tables'],
        qlik_fields_map=qlik_fields_map,
    )

    assert '"Added year" = Table.AddColumn(' in m_expression
    assert '"Added month" = Table.AddColumn(' in m_expression
    assert '"Added day" = Table.AddColumn(' in m_expression
    assert 'Removed original year' not in m_expression
    assert 'Removed original month' not in m_expression
    assert 'Removed original day' not in m_expression


def test_deferred_join_uses_resident_helper_not_fake_sharepoint_file():
    script = r'''
Employees:
LOAD employee_id, employee_name
FROM [lib://DataFiles/dim_employee.csv]
(txt, utf8, embedded labels, delimiter is ',');

Final_Activity:
LOAD employee_id, activity_id, hours_worked
FROM [lib://DataFiles/fact.csv]
(txt, utf8, embedded labels, delimiter is ',');

Employee_Summary:
LOAD employee_id, Sum(hours_worked) as total_hours, Count(activity_id) as total_days
RESIDENT Final_Activity
GROUP BY employee_id;

LEFT JOIN (Employees)
LOAD employee_id, total_hours, total_days
RESIDENT Employee_Summary;
DROP TABLE Employee_Summary;
'''

    qlik_fields_map = {
        "Employees": ["employee_id", "employee_name", "total_hours", "total_days"],
        "Final_Activity": ["employee_id", "activity_id", "hours_worked"],
        "Employee_Summary": ["employee_id", "total_hours", "total_days"],
    }

    parser = LoadScriptParser(script)
    parsed = parser.parse(qlik_fields_map=qlik_fields_map)
    tables = parsed["details"]["tables"]
    employees = next(table for table in tables if table["name"] == "Employees")

    converter = MQueryConverter()
    m_expression = converter.convert_one(
        employees,
        base_path="https://example.sharepoint.com",
        all_tables_list=tables,
        qlik_fields_map=qlik_fields_map,
    )

    assert 'HelperSource_Employee_Summary =' in m_expression
    assert 'JoinSource_Employees_1 =' in m_expression
    assert 'File Employee_Summary not found in SharePoint' not in m_expression
    assert 'Text.EndsWith(Text.Lower([Name]), "employee_summary")' not in m_expression


def test_roles_relationship_is_oriented_many_to_one_from_employees():
    tables_m = [
        {
            "name": "Employees",
            "fields": [
                {"name": "employee_id", "alias": "employee_id"},
                {"name": "role_id", "alias": "role_id"},
            ],
        },
        {
            "name": "Roles",
            "fields": [
                {"name": "role_id", "alias": "role_id"},
                {"name": "role_name", "alias": "role_name"},
            ],
        },
    ]

    relationships = resolve_relationships_unified(tables_m)

    rel = next(
        r for r in relationships
        if {r["fromTable"], r["toTable"]} == {"Employees", "Roles"}
        and {r["fromColumn"], r["toColumn"]} == {"role_id"}
    )

    assert rel["fromTable"] == "Employees"
    assert rel["fromColumn"] == "role_id"
    assert rel["toTable"] == "Roles"
    assert rel["toColumn"] == "role_id"