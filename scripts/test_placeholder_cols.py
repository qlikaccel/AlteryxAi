import sys
sys.path.insert(0, r"d:\Qlik_dev07\QlikAi\qlik_app\qlik\qlik-fastapi-backend")

from mquery_converter import MQueryConverter

converter = MQueryConverter()

# Test placeholder column generation
test_tables = ["Departments", "Roles", "Salary", "Shift", "Dates", "Employees"]

for table_name in test_tables:
    cols = converter._generate_placeholder_columns(table_name)
    print(f"\n{table_name}:")
    print(f"  Generated {len(cols)} columns:")
    for col in cols:
        print(f"    {col}")
