import pandas as pd
import re
import io

class PowerBIProcessor:

    def __init__(self, csv_content, dax_content):
        self.csv_content = csv_content
        self.dax_content = dax_content

    # 1️⃣ Read CSV from content
    def load_csv(self):
        # Create a file-like object from the content
        csv_file = io.StringIO(self.csv_content.decode('utf-8'))
        df = pd.read_csv(csv_file)
        print("CSV Loaded:")
        print(df.head())
        return df

    # 2️⃣ Read DAX from content
    def load_dax(self):
        # DAX content is already a string
        dax = self.dax_content.decode('utf-8')
        print("DAX Loaded:")
        print(dax)
        return dax

    # 3️⃣ Extract Measures from DAX
    def parse_dax_measures(self, dax_text):
        measures = []

        for line in dax_text.splitlines():
            if "=" in line:
                name, expr = line.split("=", 1)
                measures.append({
                    "name": name.strip(),
                    "expression": expr.strip()
                })

        return measures

    # 4️⃣ Main Process
    def process(self):
        df = self.load_csv()
        dax = self.load_dax()
        measures = self.parse_dax_measures(dax)

        result = {
            "table_columns": list(df.columns),
            "rows": df.to_dict(orient="records"),
            "measures": measures
        }

        return result
