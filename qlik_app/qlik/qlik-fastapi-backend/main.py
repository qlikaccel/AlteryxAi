import logging
import re
import io
import sys
from collections import defaultdict, deque
from typing import Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import xml.etree.ElementTree as ET
from groq import Groq
from dotenv import load_dotenv
import os
import requests
from app.api.v1.endpoints.migration import router as migration_router

load_dotenv()

# ─── LOGGING FIX: Force output to stdout, no buffering ──────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)   # force stdout so uvicorn shows it
    ],
    force=True   # override any existing logging config
)
logger = logging.getLogger(__name__)

app = FastAPI()
app.include_router(migration_router)

# ─── CORS ───────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── LLM Configuration ───────────────────────────────────────────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def _safe_text(element: Optional[ET.Element], *tag_names: str) -> str:
    if element is None:
        return ""
    for tag_name in tag_names:
        child = element.find(tag_name)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _connection_attr(element: ET.Element, *keys: str) -> str:
    for key in keys:
        value = element.get(key)
        if value:
            return value.strip()
    return ""


def _normalize_filter_expression(expr: str) -> str:
    expr = expr.strip()
    expr = re.sub(r"\bAND\b", "and", expr, flags=re.IGNORECASE)
    expr = re.sub(r"\bOR\b", "or", expr, flags=re.IGNORECASE)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"\s*=\s*", " = ", expr)
    expr = re.sub(r"\s*>\s*", " > ", expr)
    expr = re.sub(r"\s*<\s*", " < ", expr)
    expr = re.sub(r"\s*>=\s*", " >= ", expr)
    expr = re.sub(r"\s*<=\s*", " <= ", expr)
    expr = expr.replace("[", "[").replace("]", "]")
    expr = re.sub(r"\bIIF\(", "if ", expr, flags=re.IGNORECASE)
    expr = expr.replace(",", ", ")
    return expr


def _convert_formula_expression(expression: str) -> str:
    text = expression.strip()
    if not text:
        return text
    iif_match = re.match(r"^IIF\((.+),(.+),(.+)\)$", text, flags=re.IGNORECASE)
    if iif_match:
        condition = iif_match.group(1).strip()
        true_expr = iif_match.group(2).strip()
        false_expr = iif_match.group(3).strip()
        text = f"if {condition} then {true_expr} else {false_expr}"
    # Normalize reserved literals to Power Query syntax
    text = re.sub(r"\bNULL\b", "null", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTRUE\b", "true", text, flags=re.IGNORECASE)
    text = re.sub(r"\bFALSE\b", "false", text, flags=re.IGNORECASE)
    return text


def parse_yxmd(xml_content: str) -> List[Dict]:
    logger.info("=== parse_yxmd START ===")
    root = ET.fromstring(xml_content)
    nodes_by_id: Dict[str, Dict] = {}
    default_order: List[str] = []

    for node in root.findall('.//Node'):
        node_id = node.get('ToolID') or node.get('ToolId') or node.get('Id') or node.get('id')
        if not node_id:
            continue
        gui = node.find('.//GuiSettings') or node.find('GuiSettings')
        tool_name = ''
        if gui is not None:
            tool_name = gui.get('Tool') or gui.get('Plugin') or gui.get('Name') or ''
            if not tool_name:
                tool_name = _safe_text(gui, 'Tool', 'Plugin', 'Name')
        config = node.find('.//Configuration') or node.find('Configuration')
        nodes_by_id[node_id] = {
            'tool_id': node_id,
            'tool_name': tool_name,
            'config': config,
            'element': node,
        }
        default_order.append(node_id)
        logger.info("  Found node: id=%s tool=%s", node_id, tool_name)

    connections = defaultdict(list)
    in_degree = {node_id: 0 for node_id in nodes_by_id}
    for conn in root.findall('.//Connection'):
        source = _connection_attr(conn, 'Source', 'source', 'From', 'from')
        target = _connection_attr(conn, 'Target', 'target', 'To', 'to')
        if source and target and source in nodes_by_id and target in nodes_by_id:
            connections[source].append(target)
            in_degree[target] += 1

    queue = deque([node_id for node_id, deg in in_degree.items() if deg == 0])
    ordered_ids: List[str] = []
    seen = set(queue)

    while queue:
        node_id = queue.popleft()
        ordered_ids.append(node_id)
        for target in connections.get(node_id, []):
            in_degree[target] -= 1
            if in_degree[target] == 0 and target not in seen:
                queue.append(target)
                seen.add(target)

    for node_id in default_order:
        if node_id not in seen:
            ordered_ids.append(node_id)
            seen.add(node_id)

    ordered_nodes = [nodes_by_id[node_id] for node_id in ordered_ids if node_id in nodes_by_id]
    logger.info("=== parse_yxmd END — total nodes: %d ===", len(ordered_nodes))
    return ordered_nodes


def _extract_column_references(expr: str) -> List[str]:
    refs: List[str] = []
    if not expr:
        return refs
    for match in re.finditer(r'\[([^\]]+)\]|\b([A-Za-z_][A-Za-z0-9_]*)\b', expr):
        col = match.group(1) or match.group(2)
        if not col:
            continue
        col_lower = col.lower()
        if col_lower in {
            'if', 'then', 'else', 'and', 'or', 'not', 'null', 'true', 'false',
            'each', 'and', 'or', 'not', 'text', 'number', 'date', 'time',
            'datetime', 'int64', 'type', 'table', 'list', 'sum', 'count',
            'average', 'min', 'max', 'ceil', 'floor', 'round'
        }:
            continue
        if re.fullmatch(r'[0-9]+', col):
            continue
        refs.append(col)
    return refs


def _find_numeric_columns(nodes: List[Dict]) -> List[str]:
    numeric_cols: set[str] = set()

    def _remove_string_literals(expr: str) -> str:
        return re.sub(r'"[^"]*"|\'[^\']*\'', '', expr)

    def _capture_numeric_comparisons(expr: str) -> List[str]:
        cols = []
        expr_clean = _remove_string_literals(expr)
        cols += re.findall(r'\[?([A-Za-z_][A-Za-z0-9_]*)\]?\s*(?:>=|<=|>|<|=)\s*[-+]?[0-9]+(?:\.[0-9]+)?', expr_clean)
        cols += re.findall(r'[-+]?[0-9]+(?:\.[0-9]+)?\s*(?:>=|<=|>|<|=)\s*\[?([A-Za-z_][A-Za-z0-9_]*)\]?', expr_clean)
        return cols

    for node in nodes:
        tool = node.get('tool_name', '') or ''
        lower_tool = tool.lower()
        config = node.get('config')

        if 'filter' in lower_tool and 'summarize' not in lower_tool:
            expr = _safe_text(config, 'Expression', 'Filter', 'Condition', 'ConditionExpression')
            if expr:
                numeric_cols.update(_capture_numeric_comparisons(expr))

        elif 'formula' in lower_tool or 'calculatedfield' in lower_tool:
            if config is not None:
                for ff in config.findall('.//FormulaField'):
                    expr = ff.get('expression') or _safe_text(ff, 'Expression', 'Value')
                    if expr:
                        expr_clean = _remove_string_literals(expr)
                        if '&' not in expr_clean and re.search(r'[\+\-\*\/]', expr_clean):
                            numeric_cols.update(_extract_column_references(expr_clean))
                        if re.search(r'\b(?:Ceil|Floor|Round|Abs|Int|Number)\b', expr_clean, re.IGNORECASE):
                            numeric_cols.update(_extract_column_references(expr_clean))

        elif 'summarize' in lower_tool:
            if config is not None:
                for sf in config.findall('.//SummarizeField'):
                    action = sf.get('action', '')
                    field = sf.get('field') or _safe_text(sf, 'Field')
                    if action in ('Sum', 'Average', 'Min', 'Max') and field:
                        numeric_cols.add(field)

    return sorted(set(col for col in numeric_cols if col))


def rule_based_convert(nodes: List[Dict]) -> str:
    logger.info("=== rule_based_convert START ===")
    numeric_columns = _find_numeric_columns(nodes)
    steps: List[str] = []
    step_names: List[str] = []

    def add_step(step_name: str, expression: str) -> None:
        steps.append(f'    {step_name} = {expression},')
        step_names.append(step_name)
        logger.info("  ADD STEP: %s", step_name)

    def previous_step() -> str:
        return step_names[-1] if step_names else 'Source'

    for node in nodes:
        tool = node.get('tool_name', '')
        config = node.get('config')
        lower_tool = tool.lower() if tool else ''
        logger.info("Processing tool: '%s'", tool)

        # ── INPUT ──────────────────────────────────────────────────────────
        if 'dbfileinput' in lower_tool or 'textinput' in lower_tool or 'input' in lower_tool:
            file_path = _safe_text(config, 'File', 'FilePath', 'Filename', 'Text')
            if not file_path:
                file_path = 'YourFile.csv'
            logger.info("  Input file path: %s", file_path)

            # Step 1: Source
            add_step('Source',
                f'Csv.Document(File.Contents("{file_path}"), '
                f'[Delimiter=",", Encoding=1252, QuoteStyle=QuoteStyle.None])')

            # Step 2: PromoteHeaders  ← FIX: was missing
            add_step('PromotedHeaders',
                'Table.PromoteHeaders(Source, [PromoteAllScalars=true])')

            # Step 3: ChangedTypes  ← FIX: was missing
            add_step('ChangedTypes',
                'Table.TransformColumnTypes(PromotedHeaders, '
                'List.Transform(Table.ColumnNames(PromotedHeaders), '
                'each {_, type text}))')
            if numeric_columns:
                typed_pairs = ', '.join(
                    f'{{"{col}", type number}}' for col in numeric_columns
                )
                add_step('TypedColumns',
                    f'let cols = Table.ColumnNames(ChangedTypes), ops = {{{typed_pairs}}} in Table.TransformColumnTypes(ChangedTypes, List.Select(ops, each List.Contains(cols, _{{0}})))')
        # ── SELECT ─────────────────────────────────────────────────────────
        elif 'select' in lower_tool or 'alteryxselect' in lower_tool:
            fields = []
            if config is not None:
                for sf in config.findall('.//SelectField'):
                    selected = sf.get('selected', 'True').lower() == 'true'
                    field_name = sf.get('field') or _safe_text(sf, 'Field')
                    if selected and field_name:
                        fields.append(f'"{field_name}"')
            if not fields:
                logger.warning("  SELECT tool had no fields — skipping")
                continue
            # MissingField.UseNull  ← FIX: prevents column-not-found crash
            add_step('SelectedFields',
                f'Table.SelectColumns({previous_step()}, '
                f'{{{", ".join(fields)}}}, MissingField.UseNull)')

        # ── FILTER ─────────────────────────────────────────────────────────
        elif 'filter' in lower_tool and 'summarize' not in lower_tool:
            expr = _safe_text(config, 'Expression', 'Filter', 'Condition', 'ConditionExpression')
            expr = _normalize_filter_expression(expr)
            if not expr:
                logger.warning("  FILTER tool had empty expression — skipping")
                continue
            add_step('FilteredData',
                f'Table.SelectRows({previous_step()}, each {expr})')

        # ── FORMULA ────────────────────────────────────────────────────────
        elif 'formula' in lower_tool or 'calculatedfield' in lower_tool:
            if config is None:
                continue
            for ff in config.findall('.//FormulaField'):
                field = ff.get('field') or _safe_text(ff, 'Field', 'Name')
                expr = ff.get('expression') or _safe_text(ff, 'Expression', 'Value')
                if not field or not expr:
                    continue
                expr = _convert_formula_expression(expr)
                # Divide-by-zero guard  ← FIX
                if '/' in expr:
                    divisor_match = re.search(r'/\s*\[(\w+)\]', expr)
                    if divisor_match:
                        divisor = divisor_match.group(1)
                        expr = f'if [{divisor}] = 0 then null else {expr}'
                add_step(f'Calculated_{field}',
                    f'Table.AddColumn({previous_step()}, "{field}", each {expr}, type number)')

        # ── SUMMARIZE ──────────────────────────────────────────────────────
        elif 'summarize' in lower_tool:
            group_fields: List[str] = []
            agg_fields: List[str] = []
            if config is not None:
                for sf in config.findall('.//SummarizeField'):
                    action = sf.get('action', '')
                    field = sf.get('field') or _safe_text(sf, 'Field')
                    rename = sf.get('rename') or field
                    if action == 'GroupBy' and field:
                        group_fields.append(f'"{field}"')
                    elif field and rename:
                        if action == 'Sum':
                            agg_fields.append(f'{{"{rename}", each List.Sum([{field}]), type number}}')
                        elif action == 'Count':
                            agg_fields.append(f'{{"{rename}", each Table.RowCount(_), Int64.Type}}')
                        elif action == 'Average':
                            agg_fields.append(f'{{"{rename}", each List.Average([{field}]), type number}}')
                        elif action == 'Min':
                            agg_fields.append(f'{{"{rename}", each List.Min([{field}]), type number}}')
                        elif action == 'Max':
                            agg_fields.append(f'{{"{rename}", each List.Max([{field}]), type number}}')
            group_clause = f'{{{", ".join(group_fields)}}}' if group_fields else '{}'
            agg_clause = f'{{{", ".join(agg_fields)}}}' if agg_fields else '{}'
            add_step('GroupedData',
                f'Table.Group({previous_step()}, {group_clause}, {agg_clause})')

        # ── SORT ───────────────────────────────────────────────────────────
        elif 'sort' in lower_tool:
            sort_field = _safe_text(config, 'SortField', 'Field', 'Column')
            sort_order = _safe_text(config, 'Order', 'Direction', 'SortOrder')
            if not sort_field:
                continue
            m_order = 'Order.Descending' if sort_order.lower().startswith('desc') else 'Order.Ascending'
            add_step('SortedData',
                f'Table.Sort({previous_step()}, {{{{"{sort_field}", {m_order}}}}})')

        # ── OUTPUT / BROWSE — skip ─────────────────────────────────────────
        elif 'output' in lower_tool or 'browse' in lower_tool:
            logger.info("  Skipping output/browse node: %s", tool)

    if not steps:
        logger.warning("No steps generated! Returning minimal query.")
        return 'let\n    Source = "No steps generated"\nin\n    Source'

    steps[-1] = steps[-1].rstrip(',')
    last_step = step_names[-1]
    mquery = 'let\n' + '\n'.join(steps) + f'\nin\n    {last_step}'
    mquery = _sanitize_m_query_output(mquery)
    logger.info("=== rule_based_convert END ===\n%s", mquery)
    return mquery


# ════════════════════════════════════════════════════════════════════════════
# LLM-DRIVEN CONVERTER (GROQ)
# ════════════════════════════════════════════════════════════════════════════

def _build_llm_prompt(xml_content: str) -> str:
    return f"""You are an expert in Alteryx workflows and Power BI Power Query M language.

Convert the given Alteryx .yxmd XML into valid M-Query code for Power BI Desktop.

STRICT RULES — FOLLOW ALL:

RULE 1 - Source step must be exactly:
  Source = Csv.Document(File.Contents("YourFile.csv"), [Delimiter=",", Encoding=1252, QuoteStyle=QuoteStyle.None])

RULE 2 - Second step MUST always be PromoteHeaders:
  PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])

RULE 3 - Third step MUST always be ChangedTypes (assign type text to all columns initially):
  ChangedTypes = Table.TransformColumnTypes(PromotedHeaders, List.Transform(Table.ColumnNames(PromotedHeaders), each {{_, type text}}))

RULE 4 - ALL steps after Source must reference PromotedHeaders or ChangedTypes — NEVER reference Source directly.

RULE 5 - Table.SelectColumns must always include MissingField.UseNull as third argument:
  Table.SelectColumns(ChangedTypes, {{"Col1", "Col2"}}, MissingField.UseNull)

RULE 6 - Table.Group aggregations must always specify output type:
  Table.Group(prev, {{"GroupCol"}}, {{{{"Total", each List.Sum([Amount]), type number}}}} )

RULE 7 - Table.AddColumn for division must always guard against divide-by-zero:
  Table.AddColumn(prev, "Avg", each if [TotalQty] = 0 then null else [TotalSales]/[TotalQty], type number)

RULE 8 - Every step must reference the exact previous step variable name.

RULE 9 - Use lowercase null, true, and false. Return ONLY valid M-Query. No explanation, no markdown, no backticks.

RULE 10 - The final "in" clause must return the LAST step variable name only.

Alteryx workflow XML:
{xml_content}

Return only valid M-Query starting with "let" and ending with the "in" clause."""


def _sanitize_m_query_output(m_query: str) -> str:
    sanitized = re.sub(r"\bNULL\b", "null", m_query, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bTRUE\b", "true", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bFALSE\b", "false", sanitized, flags=re.IGNORECASE)
    return sanitized


def groq_llm_convert(xml_content: str) -> str:
    if not groq_client:
        raise RuntimeError("Groq provider is not configured")

    logger.info("=== groq_llm_convert START ===")
    prompt = _build_llm_prompt(xml_content)
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=2048,
    )
    if not hasattr(response, 'choices') or not response.choices:
        raise RuntimeError('No completion choices returned from Groq LLM')
    content = response.choices[0].message.content
    content = _sanitize_m_query_output(content)
    logger.info("Groq LLM raw response (first 300 chars): %s", content[:300] if content else "EMPTY")
    if not content or 'let' not in content.lower():
        raise RuntimeError('Groq returned invalid M Query output')
    logger.info("=== groq_llm_convert END ===")
    return content


def openai_llm_convert(xml_content: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI provider is not configured")

    logger.info("=== openai_llm_convert START ===")
    prompt = _build_llm_prompt(xml_content)
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OpenAI API error {response.status_code}: {response.text}")
    data = response.json()
    choices = data.get("choices")
    if not choices:
        raise RuntimeError("No completion choices returned from OpenAI")
    content = choices[0].get("message", {}).get("content")
    content = _sanitize_m_query_output(content)
    logger.info("OpenAI raw response (first 300 chars): %s", (content or "EMPTY")[:300])
    if not content or 'let' not in content.lower():
        raise RuntimeError('OpenAI returned invalid M Query output')
    logger.info("=== openai_llm_convert END ===")
    return content


def llm_convert(xml_content: str) -> str:
    logger.info("=== llm_convert START ===")
    attempts = []

    if LLM_PROVIDER in ("auto", "groq"):
        try:
            return groq_llm_convert(xml_content)
        except Exception as ex:
            logger.error("Groq LLM failed: %s", ex)
            attempts.append(f"Groq failed: {ex}")
            if LLM_PROVIDER == "groq":
                raise

    if LLM_PROVIDER in ("auto", "openai"):
        try:
            return openai_llm_convert(xml_content)
        except Exception as ex:
            logger.error("OpenAI LLM failed: %s", ex)
            attempts.append(f"OpenAI failed: {ex}")
            if LLM_PROVIDER == "openai":
                raise

    raise RuntimeError(
        "LLM conversion failed. " + " ".join(attempts) +
        " Configure GROQ_API_KEY or OPENAI_API_KEY, or set LLM_PROVIDER to groq/openai/auto."
    )


# ════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@app.get("/")
def root():
    logger.info("GET / called")
    return {"message": "Alteryx to M-Query Converter API is running!"}


@app.post("/api/upload")
async def upload_csv(file: UploadFile = File(...), csv: UploadFile = File(...)):
    logger.info("POST /api/upload — yxmd=%s  csv=%s", file.filename, csv.filename)
    if not file.filename.lower().endswith(".yxmd"):
        raise HTTPException(status_code=400, detail="Please upload a .yxmd workflow file")
    if not csv.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a CSV file")
    try:
        await file.read()
        csv_bytes = await csv.read()
        csv_text = csv_bytes.decode("utf-8", errors="replace")
        dataframe = pd.read_csv(io.StringIO(csv_text))
        columns = [str(col) for col in dataframe.columns]
        data = dataframe.fillna("").to_dict(orient="records")
        logger.info("CSV loaded — columns: %s  rows: %d", columns, len(data))
        return {"data": data, "columns": columns, "total_records": len(data)}
    except Exception as e:
        logger.error("CSV parsing failed: %s", e)
        raise HTTPException(status_code=500, detail=f"CSV parsing failed: {str(e)}")


@app.post("/api/convert/rule-based")
async def convert_rule_based(file: UploadFile = File(...)):
    logger.info("POST /api/convert/rule-based — file=%s", file.filename)
    if not file.filename.endswith(".yxmd"):
        raise HTTPException(status_code=400, detail="Please upload a .yxmd file")
    content = await file.read()
    try:
        nodes = parse_yxmd(content.decode("utf-8"))
        m_query = rule_based_convert(nodes)
        logger.info("Rule-based conversion SUCCESS")
        return {
            "approach": "Rule-Based",
            "filename": file.filename,
            "node_count": len(nodes),
            "m_query": m_query
        }
    except Exception as e:
        logger.error("Rule-based conversion FAILED: %s", e)
        raise HTTPException(status_code=500, detail=f"Rule-based conversion failed: {str(e)}")


@app.post("/api/convert/llm")
async def convert_llm(file: UploadFile = File(...)):
    logger.info("POST /api/convert/llm — file=%s", file.filename)
    if not file.filename.endswith(".yxmd"):
        raise HTTPException(status_code=400, detail="Please upload a .yxmd file")
    content = await file.read()
    xml_content = content.decode("utf-8")
    try:
        m_query = llm_convert(xml_content)
        logger.info("LLM conversion SUCCESS")
        return {"approach": "LLM-Driven (Groq)", "filename": file.filename, "m_query": m_query}
    except Exception as llm_error:
        logger.error("LLM conversion FAILED: %s — trying rule-based fallback", llm_error)
        try:
            nodes = parse_yxmd(xml_content)
            fallback_query = rule_based_convert(nodes)
            logger.info("Fallback rule-based conversion SUCCESS")
            return {
                "approach": "LLM-Driven (fallback to Rule-Based)",
                "filename": file.filename,
                "m_query": fallback_query,
                "llm_error": str(llm_error),
            }
        except Exception as fallback_error:
            logger.error("Fallback ALSO failed: %s", fallback_error)
            raise HTTPException(
                status_code=500,
                detail=(
                    f"LLM conversion failed: {str(llm_error)}; "
                    f"Rule-based fallback also failed: {str(fallback_error)}"
                ),
            )


@app.post("/api/convert/both")
async def convert_both(file: UploadFile = File(...)):
    logger.info("POST /api/convert/both — file=%s", file.filename)
    if not file.filename.endswith(".yxmd"):
        raise HTTPException(status_code=400, detail="Please upload a .yxmd file")
    content = await file.read()
    xml_content = content.decode("utf-8")
    try:
        nodes = parse_yxmd(xml_content)
        rule_result = rule_based_convert(nodes)
        try:
            llm_result = llm_convert(xml_content)
            return {
                "filename": file.filename,
                "node_count": len(nodes),
                "rule_based": rule_result,
                "llm_driven": llm_result
            }
        except Exception as llm_error:
            logger.error("LLM in both-mode failed: %s", llm_error)
            return {
                "filename": file.filename,
                "node_count": len(nodes),
                "rule_based": rule_result,
                "llm_driven": None,
                "llm_error": str(llm_error),
            }
    except Exception as e:
        logger.error("Both-mode conversion FAILED: %s", e)
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")