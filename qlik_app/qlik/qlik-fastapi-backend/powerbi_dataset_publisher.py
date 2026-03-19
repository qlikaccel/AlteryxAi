# powerbi_dataset_publisher.py - Cloud-Optimized REST Push + M Queries
import os
import json
from typing import Dict, Any, List
from msal import ConfidentialClientApplication
import requests
from pydantic import BaseModel

class PublisherConfig(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str
    workspace_id: str
    dataset_name: str

def get_powerbi_token(config: PublisherConfig) -> str:
    app = ConfidentialClientApplication(
        config.client_id, authority=f"https://login.microsoftonline.com/{config.tenant_id}",
        client_credential=config.client_secret
    )
    result = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
    if "access_token" not in result: raise ValueError(f"Auth failed: {result.get('error_description')}")
    return result["access_token"]  # [web:6]

def generate_cloud_m(table: Dict[str, Any]) -> str:
    """Generates cloud-friendly M query using Web.Contents for SharePoint CSVs"""
    filename = table['source_path'].split('/')[-1].replace('.qvd', '.csv')  # QVD → CSV fallback
    path = f"[DataSourcePath] & '/{filename}'"
    m_base = f"""
let
    FilePath = {path},
    Source = Csv.Document(Web.Contents(FilePath), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true])
in
    Promoted
"""
    # Add type transforms from fields
    types = {f['name']: f.get('type', 'text') for f in table.get('fields', [])}
    type_map = {"number": "type number", "date": "type date", "text": "type text"}
    typed = "Table.TransformColumnTypes(Promoted,{{" + ", ".join(f'"{k}", {type_map.get(v, "type text")}' for k,v in types.items()) + "}})"
    return m_base.replace("Promoted", typed, 1)  # [web:28]

def tables_to_push_schema(parse_result: Dict[str, Any]) -> Dict[str, Any]:
    dataset = {
        "name": parse_result.get("dataset_name", "QlikConvertedDataset"),
        "defaultMode": "Push",
        "parameters": [{"name": "DataSourcePath", "type": "string", "mode": "Required"}],
        "tables": []
    }
    for table in parse_result["tables"]:
        cols = [{"name": f["name"], "dataType": "string"} for f in table.get("fields", [])]  # Infer later
        dataset["tables"].append({"name": table["table_name"], "columns": cols})
    dataset["m_queries"] = {t["table_name"]: generate_cloud_m(t) for t in parse_result["tables"]}  # Bonus: Store M
    return dataset  # [web:20]






def resolve_ambiguous_relationships(relationships: List[Dict]) -> List[Dict]:
    """
    Detects ambiguous paths (two active routes between same table pair)
    and sets isActive: false on the redundant direct relationship.
    
    Rule: If TableA -> TableC exists directly AND via TableA -> TableB -> TableC,
    mark the direct TableA -> TableC as inactive.
    """
    direct_map = {}
    for i, rel in enumerate(relationships):
        key = (rel["fromTable"], rel["toTable"])
        direct_map[key] = i

    to_deactivate = set()

    for (from_table, to_table), rel_idx in direct_map.items():
        intermediaries = [t for (f, t) in direct_map if f == from_table and t != to_table]
        for mid in intermediaries:
            if (mid, to_table) in direct_map:
                to_deactivate.add(rel_idx)
                print(f"[RelationshipFix] Deactivating ambiguous: "
                      f"{from_table}.{relationships[rel_idx]['fromColumn']} -> "
                      f"{to_table}.{relationships[rel_idx]['toColumn']} "
                      f"(indirect path via {mid} exists)")

    result = []
    for i, rel in enumerate(relationships):
        r = dict(rel)
        if i in to_deactivate:
            r["isActive"] = False
        result.append(r)

    return result


def build_bim_relationships(inferred_relationships: List[Dict]) -> List[Dict]:
    """
    Converts inferred relationships into BIM-format and resolves ambiguity.
    Call this before building the final BIM payload.
    """
    bim_rels = []
    for rel in inferred_relationships:
        bim_rels.append({
            "name": f"{rel['fromTable']}_{rel['fromColumn']}_{rel['toTable']}",
            "fromTable": rel["fromTable"],
            "fromColumn": rel["fromColumn"],
            "toTable": rel["toTable"],
            "toColumn": rel["toColumn"],
            "isActive": True
        })

    return resolve_ambiguous_relationships(bim_rels)




# def publish_from_parse_result(parse_result: Dict[str, Any], config: PublisherConfig) -> Dict[str, Any]:
#     token = get_powerbi_token(config)
#     headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
#     schema = tables_to_push_schema(parse_result)


def publish_from_parse_result(parse_result: Dict[str, Any], config: PublisherConfig) -> Dict[str, Any]:
    token = get_powerbi_token(config)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    schema = tables_to_push_schema(parse_result)

    # ✅ Build relationships with ambiguity resolved BEFORE BIM payload
    raw_relationships = parse_result.get("relationships", [])
    resolved_relationships = build_bim_relationships(raw_relationships)

    # Inject into BIM payload
    schema["relationships"] = resolved_relationships

    # ... rest of your publish logic