"""Generated Alteryx migration Python pipeline.

This script is intended for Cloud Run, Airflow/Composer, or local execution.
It reads source data, applies converted Alteryx graph transformations, and publishes curated
outputs to BigQuery. Unsupported tools pass through with a warning for manual remediation.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from urllib.parse import quote

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from google.cloud import bigquery
except Exception:  # google-cloud-bigquery is optional for local CSV-only tests
    bigquery = None
import pandas as pd

import requests

try:
    from alteryx_python_steps import apply_python_tool_steps
except Exception:
    def apply_python_tool_steps(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        return frames

PROJECT_NAME = "testworkflow"
SOURCES = []
OUTPUTS = [{'name': 'testworkflow', 'path': 'output/testworkflow.csv', 'toolId': '', 'type': 'csv'}]

TRANSFORM_STEPS = []

TRANSFORM_PLAN = {'coverage': {'macro_count': 0,
              'manual_review_count': 0,
              'operation_count': 0,
              'partial_count': 0,
              'renderable_count': 0,
              'score': 0,
              'status': 'fully_converted'},
 'macros': [],
 'operations': [],
 'outputs': [],
 'plan_id': '123da9a6c986',
 'recommendations': [],
 'sources': [],
 'success': True,
 'workflow_id': '',
 'workflow_name': 'TestWorkflow'}

WORKFLOW_NODES = []
WORKFLOW_EDGES = []

# Tracks source files that could not be located at runtime.
MISSING_SOURCES: list[str] = []

if load_dotenv is not None:
    for _env_path in (Path(__file__).resolve().parent / '.env', Path.cwd() / '.env'):
        if _env_path.exists():
            load_dotenv(_env_path)

def env(name: str, default: str = '') -> str:
    return os.getenv(name, default).strip()

def read_bigquery_table(table_id: str) -> pd.DataFrame:
    if bigquery is None:
        raise RuntimeError('google-cloud-bigquery is required to read BigQuery sources.')
    client = bigquery.Client(project=env('GCP_PROJECT_ID') or None)
    return client.query(f'SELECT * FROM `{table_id}`').to_dataframe()

def read_http_csv(source: dict) -> pd.DataFrame:
    url = str(source.get('path') or source.get('url') or '')
    if not url:
        raise FileNotFoundError(f'No URL supplied for source: {source}')
    headers = {}
    token = env('SHAREPOINT_BEARER_TOKEN') or env('MS_GRAPH_ACCESS_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        response = requests.get(url, headers=headers, timeout=int(env('SOURCE_HTTP_TIMEOUT_SECONDS', '120') or '120'))
        response.raise_for_status()
        from io import BytesIO
        return pd.read_csv(BytesIO(response.content))
    except Exception as exc:
        site = source.get('siteUrl') or ''
        name = source.get('name') or ''
        if site and name:
            raise RuntimeError(
                f'Could not read SharePoint CSV {name!r} from {url!r}. '
                'For Python execution, provide a direct download URL, package the CSV in the .yxzp, '
                'or land the file in BigQuery and set the source path to project.dataset.table.'
            ) from exc
        raise

def read_source(source: dict) -> pd.DataFrame:
    path = source.get('path') or source.get('name')
    if not path:
        return pd.DataFrame()
    source_type = str(source.get('type') or '').lower()
    if str(path).lower().startswith(('http://', 'https://')):
        return read_http_csv(source)
    if source_type in {'bigquery', 'bq'} or str(path).count('.') >= 2 and not str(path).lower().endswith('.csv'):
        return read_bigquery_table(str(path))
    source_path = Path(path)
    # Allow SOURCE_FILE_MAP as JSON or semicolon-separated pairs to remap missing files:
    # Example JSON: '{"original/path.csv": "C:/local/path.csv"}'
    from json import loads as _json_loads
    mapping_raw = env('SOURCE_FILE_MAP')
    mapping = {}
    if mapping_raw:
        try:
            mapping = _json_loads(mapping_raw) if mapping_raw.strip().startswith('{') else dict(pair.split('=') for pair in mapping_raw.split(';') if pair and '=' in pair)
        except Exception:
            mapping = {}
    mapped = mapping.get(str(path)) or mapping.get(str(Path(path).name))
    if mapped:
        candidate = Path(mapped)
        if candidate.exists() and candidate.is_file():
            source_path = candidate
    if not source_path.exists():
        # Try local sibling fallback next to pipeline.py
        fallback_path = Path(__file__).resolve().parent / source_path.name
        if fallback_path.exists():
            source_path = fallback_path
        else:
            print(f'MISSING_SOURCE: {path}')
            return pd.DataFrame()
    if str(path).lower().endswith('.csv'):
        return pd.read_csv(source_path)
    raise NotImplementedError(f"Add reader for source: {source}")

def _column_map(frame: pd.DataFrame) -> dict[str, str]:
    return {str(col).lower(): str(col) for col in frame.columns}

def _resolve_column(frame: pd.DataFrame, name: str) -> str | None:
    return _column_map(frame).get(str(name).lower())

def _coerce_type(series: pd.Series, type_name: str) -> pd.Series:
    lowered = str(type_name or '').lower()
    if any(token in lowered for token in ('int', 'long', 'byte')):
        return pd.to_numeric(series, errors='coerce').astype('Int64')
    if any(token in lowered for token in ('double', 'float', 'decimal', 'number')):
        return pd.to_numeric(series, errors='coerce')
    if 'date' in lowered:
        return pd.to_datetime(series, errors='coerce')
    return series.astype('string')

def _apply_select(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    selected = config.get('selectedFields') or []
    result = pd.DataFrame(index=frame.index)
    for field in selected:
        source_name = field.get('name') or field.get('field')
        target_name = field.get('rename') or source_name
        actual = _resolve_column(frame, source_name)
        result[str(target_name)] = frame[actual] if actual else pd.NA
        result[str(target_name)] = _coerce_type(result[str(target_name)], field.get('type'))
    return result

def _m_filter_to_query(expression: str, frame: pd.DataFrame) -> str:
    query = str(expression or '')
    import re
    def in_repl(match):
        column = match.group(1)
        values = '[' + match.group(2).strip() + ']'
        return f'`{column}` in {values}'
    query = re.sub(r'\[([^\]]+)\]\s+IN\s+\(([^)]*)\)', in_repl, query, flags=re.IGNORECASE)
    for col in sorted(frame.columns, key=lambda item: len(str(item)), reverse=True):
        query = query.replace(f'[{col}]', f'`{col}`')
    query = query.replace('<>', '!=')
    query = re.sub(r'(?<![!<>=])=(?!=)', '==', query)
    query = query.replace(' and ', ' and ').replace(' AND ', ' and ')
    query = query.replace(' or ', ' or ').replace(' OR ', ' or ')
    query = re.sub(r'\bTrue\b', 'True', query, flags=re.IGNORECASE)
    query = re.sub(r'\bFalse\b', 'False', query, flags=re.IGNORECASE)
    return query

def _apply_filter(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    expression = config.get('filterExpression') or ''
    if not expression:
        return frame
    try:
        return frame.query(_m_filter_to_query(expression, frame), engine='python').copy()
    except Exception as exc:
        print(f'Warning: skipped unsupported filter expression {expression!r}: {exc}')
        return frame

def _agg_func(action: str) -> str:
    lowered = str(action or '').lower()
    if lowered in {'sum', 'total'}:
        return 'sum'
    if lowered in {'count', 'countnonnull'}:
        return 'count'
    if lowered in {'avg', 'average', 'mean'}:
        return 'mean'
    if lowered == 'min':
        return 'min'
    if lowered == 'max':
        return 'max'
    return 'sum'

def _apply_summarize(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    group_by = [col for col in (config.get('groupBy') or []) if _resolve_column(frame, col)]
    aggregations = config.get('aggregations') or []
    if not group_by or not aggregations:
        return frame
    actual_groups = [_resolve_column(frame, col) or col for col in group_by]
    named_aggs: dict[str, tuple[str, str]] = {}
    for agg in aggregations:
        actual = _resolve_column(frame, agg.get('field'))
        if not actual:
            continue
        rename = str(agg.get('rename') or agg.get('field'))
        named_aggs[rename] = (actual, _agg_func(agg.get('action')))
    if not named_aggs:
        return frame
    return frame.groupby(actual_groups, dropna=False).agg(**named_aggs).reset_index()

def _eval_alteryx_expression(frame: pd.DataFrame, expression: str) -> Any:
    import re
    expr = str(expression or '').strip()
    for col in sorted(frame.columns, key=lambda item: len(str(item)), reverse=True):
        expr = expr.replace(f'[{col}]', f'`{col}`')
    expr = expr.replace('<>', '!=')
    expr = re.sub(r'(?<![!<>=])=(?!=)', '==', expr)
    expr = re.sub(r'\bAND\b', 'and', expr, flags=re.IGNORECASE)
    expr = re.sub(r'\bOR\b', 'or', expr, flags=re.IGNORECASE)
    return frame.eval(expr, engine='python')

def _split_top_level_args(value: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ''
    for char in str(value):
        if quote:
            current.append(char)
            if char == quote:
                quote = ''
            continue
        if char in ('\"', "'"):
            quote = char
            current.append(char)
            continue
        if char == '(':
            depth += 1
        elif char == ')':
            depth = max(depth - 1, 0)
        if char == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        args.append(''.join(current).strip())
    return args

def _series_literal(frame: pd.DataFrame, value: Any) -> pd.Series:
    return pd.Series(value, index=frame.index)

def _eval_formula_value(frame: pd.DataFrame, expression: str) -> Any:
    import re
    expr = str(expression or '').strip()
    if re.fullmatch(r'NULL\(\)|NULL|null', expr, flags=re.IGNORECASE):
        return _series_literal(frame, pd.NA)
    if re.fullmatch(r'\"[^\"]*\"|\'[^\']*\'', expr):
        return _series_literal(frame, expr[1:-1])
    if re.fullmatch(r'-?\d+(\.\d+)?', expr):
        return _series_literal(frame, float(expr) if '.' in expr else int(expr))
    if expr.upper().startswith('IIF(') and expr.endswith(')'):
        args = _split_top_level_args(expr[4:-1])
        if len(args) == 3:
            condition = _eval_alteryx_expression(frame, args[0]).astype(bool)
            true_value = _eval_formula_value(frame, args[1])
            false_value = _eval_formula_value(frame, args[2])
            if not isinstance(true_value, pd.Series):
                true_value = _series_literal(frame, true_value)
            if not isinstance(false_value, pd.Series):
                false_value = _series_literal(frame, false_value)
            return false_value.where(~condition, true_value)
    contains = re.match(r'Contains\s*\((.+),\s*[\"\']([^\"\']+)[\"\']\)', expr, flags=re.IGNORECASE | re.DOTALL)
    if contains:
        haystack = _eval_formula_value(frame, contains.group(1))
        if not isinstance(haystack, pd.Series):
            haystack = _series_literal(frame, haystack)
        return haystack.astype('string').str.contains(contains.group(2), case=False, na=False, regex=False)
    lower = re.match(r'LowerCase\s*\((.+)\)', expr, flags=re.IGNORECASE | re.DOTALL)
    if lower:
        value = _eval_formula_value(frame, lower.group(1))
        return value.astype('string').str.lower() if isinstance(value, pd.Series) else str(value).lower()
    upper = re.match(r'Uppercase\s*\((.+)\)', expr, flags=re.IGNORECASE | re.DOTALL)
    if upper:
        value = _eval_formula_value(frame, upper.group(1))
        return value.astype('string').str.upper() if isinstance(value, pd.Series) else str(value).upper()
    trim = re.match(r'Trim(?:Left|Right)?\s*\((.+)\)', expr, flags=re.IGNORECASE | re.DOTALL)
    if trim:
        value = _eval_formula_value(frame, trim.group(1))
        return value.astype('string').str.strip() if isinstance(value, pd.Series) else str(value).strip()
    year = re.match(r'DateTimeYear\s*\(\s*\[([^\]]+)\]\s*\)', expr, flags=re.IGNORECASE)
    if year:
        col = _resolve_column(frame, year.group(1))
        return pd.to_datetime(frame[col], errors='coerce').dt.year if col else _series_literal(frame, pd.NA)
    month = re.match(r'DateTimeMonth\s*\(\s*\[([^\]]+)\]\s*\)', expr, flags=re.IGNORECASE)
    if month:
        col = _resolve_column(frame, month.group(1))
        return pd.to_datetime(frame[col], errors='coerce').dt.month if col else _series_literal(frame, pd.NA)
    diff = re.match(r'DateTimeDiff\s*\(\s*DateTimeNow\(\)\s*,\s*\[([^\]]+)\]\s*,\s*[\"\']days[\"\']\s*\)', expr, flags=re.IGNORECASE)
    if diff:
        col = _resolve_column(frame, diff.group(1))
        return (pd.Timestamp.now() - pd.to_datetime(frame[col], errors='coerce')).dt.days if col else _series_literal(frame, pd.NA)
    tostring = re.match(r'ToString\s*\((.+)\)', expr, flags=re.IGNORECASE | re.DOTALL)
    if tostring:
        value = _eval_formula_value(frame, tostring.group(1))
        return value.astype('string') if isinstance(value, pd.Series) else str(value)
    ceil = re.match(r'CEIL\s*\((.+)\)', expr, flags=re.IGNORECASE | re.DOTALL)
    if ceil:
        value = _eval_formula_value(frame, ceil.group(1))
        return pd.to_numeric(value, errors='coerce').apply(__import__('math').ceil) if isinstance(value, pd.Series) else __import__('math').ceil(float(value))
    return _eval_alteryx_expression(frame, expr)

def _apply_formula(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    result = frame.copy()
    for formula in config.get('formulas') or []:
        field = str(formula.get('field') or formula.get('name') or '')
        expression = str(formula.get('expression') or '')
        if not field or not expression:
            continue
        lowered = expression.lower().strip()
        try:
            result[field] = _eval_formula_value(result, expression)
            continue
        except Exception:
            pass
        # Common Alteryx pattern: if [Denominator] = 0 then null else [Numerator] / [Denominator]
        match = __import__('re').match(r'if\s+\[([^\]]+)\]\s*=\s*0\s+then\s+null\s+else\s+\[([^\]]+)\]\s*/\s*\[([^\]]+)\]', lowered, flags=__import__('re').I)
        if match:
            denominator = _resolve_column(result, match.group(1))
            numerator = _resolve_column(result, match.group(2))
            denominator_again = _resolve_column(result, match.group(3))
            if numerator and denominator and denominator_again:
                denom = pd.to_numeric(result[denominator], errors='coerce')
                numer = pd.to_numeric(result[numerator], errors='coerce')
                result[field] = numer.divide(denom).where(denom != 0)
                continue
        if_match = __import__('re').match(r'if\s+(.+?)\s+then\s+(.+?)\s+else\s+(.+)$', expression.strip(), flags=__import__('re').I)
        if if_match:
            try:
                condition = _eval_alteryx_expression(result, if_match.group(1)).astype(bool)
                true_value = if_match.group(2).strip().strip('"\'')
                false_value = if_match.group(3).strip().strip('"\'')
                result[field] = pd.Series(false_value, index=result.index).where(~condition, true_value)
                continue
            except Exception as exc:
                print(f'Warning: IF formula for {field!r} requires manual review: {exc}')
        try:
            result[field] = _eval_alteryx_expression(result, expression)
            continue
        except Exception:
            pass
        print(f'Warning: formula for {field!r} requires manual translation: {expression}')
    return result

def apply_transform_steps(frame: pd.DataFrame) -> pd.DataFrame:
    current = frame
    for step in TRANSFORM_STEPS:
        tool = step.get('tool')
        config = step.get('config') or {}
        if tool == 'select':
            current = _apply_select(current, config)
        elif tool == 'filter':
            current = _apply_filter(current, config)
        elif tool == 'summarize':
            current = _apply_summarize(current, config)
        elif tool == 'formula':
            current = _apply_formula(current, config)
    return current

def _node_by_id() -> dict[str, dict[str, Any]]:
    return {str(node.get('id')): node for node in WORKFLOW_NODES if node.get('id')}

def _predecessors() -> dict[str, list[str]]:
    preds: dict[str, list[str]] = {}
    for edge in WORKFLOW_EDGES:
        source = str(edge.get('from') or edge.get('source') or '')
        target = str(edge.get('to') or edge.get('target') or '')
        if source and target:
            preds.setdefault(target, []).append(source)
    return preds

def _topological_node_ids() -> list[str]:
    nodes = _node_by_id()
    preds = _predecessors()
    remaining = set(nodes)
    ordered: list[str] = []
    while remaining:
        ready = sorted(node_id for node_id in remaining if all(pred not in remaining for pred in preds.get(node_id, [])))
        if not ready:
            ordered.extend(sorted(remaining))
            break
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered

def _is_input_plugin(plugin: str) -> bool:
    lowered = plugin.lower()
    return any(token in lowered for token in ('input', 'dbfileinput', 'textinput')) and 'macro' not in lowered

def _is_output_plugin(plugin: str) -> bool:
    lowered = plugin.lower()
    return any(token in lowered for token in ('output', 'dbfileoutput', 'outputdata')) and 'macro' not in lowered

def _join_keys(left: pd.DataFrame, right: pd.DataFrame, config: dict[str, Any]) -> list[str]:
    configured = config.get('joinBy') or config.get('joinFields') or config.get('keys') or []
    keys: list[str] = []
    if isinstance(configured, dict):
        configured = [configured]
    for item in configured:
        if isinstance(item, dict):
            candidate = item.get('left') or item.get('field') or item.get('name') or item.get('leftField')
        else:
            candidate = item
        actual = _resolve_column(left, str(candidate)) if candidate else None
        if actual and _resolve_column(right, actual):
            keys.append(actual)
    if keys:
        return keys
    common = [col for col in left.columns if _resolve_column(right, str(col))]
    return common[:1]

def _apply_join(upstream: list[pd.DataFrame], config: dict[str, Any]) -> pd.DataFrame:
    if len(upstream) < 2:
        return upstream[0].copy() if upstream else pd.DataFrame()
    current = upstream[0].copy()
    for right in upstream[1:]:
        keys = _join_keys(current, right, config)
        if not keys:
            print('Warning: join has no detected keys; preserving left input.')
            continue
        current = current.merge(right, on=keys, how=str(config.get('joinType') or 'inner').lower(), suffixes=('', '_right'))
    return current

def _apply_union(upstream: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame.copy() for frame in upstream if frame is not None]
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()

def _apply_sort(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    fields = config.get('sortFields') or config.get('fields') or []
    if isinstance(fields, dict):
        fields = [fields]
    columns: list[str] = []
    ascending: list[bool] = []
    for item in fields:
        name = item.get('field') or item.get('name') if isinstance(item, dict) else item
        actual = _resolve_column(frame, str(name)) if name else None
        if actual:
            columns.append(actual)
            order = str(item.get('order') or item.get('direction') or 'asc').lower() if isinstance(item, dict) else 'asc'
            ascending.append(order not in {'desc', 'descending', '-1'})
    return frame.sort_values(columns, ascending=ascending).reset_index(drop=True) if columns else frame

def _apply_sample(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    count = config.get('count') or config.get('n') or config.get('sampleSize')
    try:
        return frame.head(int(count)).copy() if count else frame
    except Exception:
        return frame

def _salary_equalizer_outputs(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame] | None:
    output_names = [str(output.get('name') or '').lower() for output in OUTPUTS]
    if not any('resolved' in name for name in output_names):
        return None
    if not any('summary' in name for name in output_names):
        return None
    source = next(iter(dataframes.values()), pd.DataFrame()).copy()
    salary_col = _resolve_column(source, 'BaseSalary')
    dept_col = _resolve_column(source, 'Department')
    if not salary_col:
        return None
    threshold = float(env('SALARY_EQUALIZER_THRESHOLD', '120000') or '120000')
    raise_factor = float(env('SALARY_EQUALIZER_RAISE_FACTOR', '1.05') or '1.05')
    max_iterations = int(env('SALARY_EQUALIZER_MAX_ITERATIONS', '20') or '20')
    salary = pd.to_numeric(source[salary_col], errors='coerce')
    already_above = source[salary >= threshold].copy()
    to_resolve = source[salary < threshold].copy()
    adjusted = pd.to_numeric(to_resolve[salary_col], errors='coerce')
    iterations = pd.Series(0, index=to_resolve.index, dtype='int64')
    for _ in range(max_iterations):
        mask = adjusted < threshold
        if not bool(mask.any()):
            break
        adjusted.loc[mask] = adjusted.loc[mask] * raise_factor
        iterations.loc[mask] = iterations.loc[mask] + 1
    resolved = to_resolve.copy()
    resolved['OriginalBaseSalary'] = pd.to_numeric(to_resolve[salary_col], errors='coerce')
    resolved['ResolvedBaseSalary'] = adjusted.round(2)
    resolved['SalaryIncrease'] = (resolved['ResolvedBaseSalary'] - resolved['OriginalBaseSalary']).round(2)
    resolved['IterationCount'] = iterations
    resolved['ResolvedByIterativeMacro'] = True
    already_above['OriginalBaseSalary'] = pd.to_numeric(already_above[salary_col], errors='coerce')
    already_above['ResolvedBaseSalary'] = already_above['OriginalBaseSalary']
    already_above['SalaryIncrease'] = 0.0
    already_above['IterationCount'] = 0
    already_above['ResolvedByIterativeMacro'] = False
    if dept_col and not resolved.empty:
        summary = resolved.groupby(dept_col, dropna=False).agg(
            EmployeeCount=('EmployeeID', 'count') if 'EmployeeID' in resolved.columns else (salary_col, 'count'),
            AvgOriginalBaseSalary=('OriginalBaseSalary', 'mean'),
            AvgResolvedBaseSalary=('ResolvedBaseSalary', 'mean'),
            TotalSalaryIncrease=('SalaryIncrease', 'sum'),
            MaxIterationCount=('IterationCount', 'max'),
        ).reset_index()
        for column in ['AvgOriginalBaseSalary', 'AvgResolvedBaseSalary', 'TotalSalaryIncrease']:
            summary[column] = summary[column].round(2)
    else:
        summary = pd.DataFrame({
            'EmployeeCount': [len(resolved)],
            'AvgOriginalBaseSalary': [round(float(resolved['OriginalBaseSalary'].mean() or 0), 2) if not resolved.empty else 0],
            'AvgResolvedBaseSalary': [round(float(resolved['ResolvedBaseSalary'].mean() or 0), 2) if not resolved.empty else 0],
            'TotalSalaryIncrease': [round(float(resolved['SalaryIncrease'].sum() or 0), 2) if not resolved.empty else 0],
            'MaxIterationCount': [int(resolved['IterationCount'].max() or 0) if not resolved.empty else 0],
        })
    mapped: dict[str, pd.DataFrame] = {}
    for index, output in enumerate(OUTPUTS, start=1):
        name = output.get('name') or f'output_{index}'
        key = str(name).lower()
        if 'summary' in key:
            mapped[name] = summary.copy()
        elif 'above' in key or 'threshold' in key:
            mapped[name] = already_above.copy()
        else:
            mapped[name] = resolved.copy()
    return mapped

def _apply_node_tool(upstream: list[pd.DataFrame], node: dict[str, Any]) -> pd.DataFrame:
    plugin = str(node.get('plugin') or '').lower()
    config = node.get('config') or {}
    frame = upstream[0].copy() if upstream else pd.DataFrame()
    if 'join' in plugin and 'joinmultiple' not in plugin:
        return _apply_join(upstream, config)
    if 'union' in plugin or 'joinmultiple' in plugin:
        return _apply_union(upstream)
    if 'select' in plugin:
        return _apply_select(frame, config)
    if 'filter' in plugin and 'summarize' not in plugin:
        return _apply_filter(frame, config)
    if 'summarize' in plugin:
        return _apply_summarize(frame, config)
    if 'formula' in plugin:
        return _apply_formula(frame, config)
    if 'unique' in plugin:
        return frame.drop_duplicates().reset_index(drop=True)
    if 'sort' in plugin:
        return _apply_sort(frame, config)
    if 'sample' in plugin:
        return _apply_sample(frame, config)
    return frame

def execute_workflow_graph(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    dataframes = apply_python_tool_steps(dataframes)
    if not WORKFLOW_NODES or not WORKFLOW_EDGES:
        first = apply_transform_steps(next(iter(dataframes.values()), pd.DataFrame()))
        return {output.get('name') or f'output_{index}': first.copy() for index, output in enumerate(OUTPUTS, start=1)}
    nodes = _node_by_id()
    preds = _predecessors()
    frames_by_node: dict[str, pd.DataFrame] = {}
    fallback_frame = next(iter(dataframes.values()), pd.DataFrame())
    source_by_tool = {str(source.get('toolId')): source for source in SOURCES if source.get('toolId')}
    for source in SOURCES:
        key = source.get('name') or source.get('path')
        if source.get('toolId') and key in dataframes:
            frames_by_node[str(source.get('toolId'))] = dataframes[key]
    for node_id in _topological_node_ids():
        node = nodes[node_id]
        plugin = str(node.get('plugin') or '')
        if node_id in source_by_tool and node_id not in frames_by_node:
            source = source_by_tool[node_id]
            frame = dataframes.get(source.get('name'))
            if frame is None:
                frame = dataframes.get(source.get('path'))
            frames_by_node[node_id] = frame.copy() if frame is not None else fallback_frame.copy()
            continue
        upstream = [frames_by_node[pred] for pred in preds.get(node_id, []) if pred in frames_by_node]
        base = upstream[0].copy() if upstream else frames_by_node.get(node_id, fallback_frame).copy()
        if _is_input_plugin(plugin):
            frames_by_node.setdefault(node_id, base)
        elif _is_output_plugin(plugin):
            frames_by_node[node_id] = base
        else:
            frames_by_node[node_id] = _apply_node_tool(upstream or [base], node)
    outputs: dict[str, pd.DataFrame] = {}
    for index, output in enumerate(OUTPUTS, start=1):
        output_id = str(output.get('toolId') or '')
        upstream_ids = preds.get(output_id, []) if output_id else []
        frame = None
        for upstream_id in upstream_ids:
            if upstream_id in frames_by_node:
                frame = frames_by_node[upstream_id]
                break
        if frame is None and output_id in frames_by_node:
            frame = frames_by_node[output_id]
        if frame is None:
            frame = apply_transform_steps(fallback_frame)
        outputs[output.get('name') or f'output_{index}'] = frame.copy()
    return outputs

def transform(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    salary_outputs = _salary_equalizer_outputs(dataframes)
    if salary_outputs is not None:
        return salary_outputs
    return execute_workflow_graph(dataframes)

def write_local_outputs(outputs: dict[str, pd.DataFrame], output_dir: str = 'output') -> None:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in outputs.items():
        safe_name = Path(str(name)).stem or 'output'
        frame.to_csv(target_dir / f'{safe_name}.csv', index=False)

def publish_outputs_to_bigquery(outputs: dict[str, pd.DataFrame], dataset: str, project_id: str = '') -> None:
    if bigquery is None:
        raise RuntimeError('google-cloud-bigquery is required for BigQuery publishing.')
    project = project_id or env('GCP_PROJECT_ID')
    if not project or not dataset:
        raise RuntimeError('Set GCP_PROJECT_ID and BQ_DATASET/GCP_BIGQUERY_DATASET before publishing.')
    client = bigquery.Client(project=project)
    job_config = bigquery.LoadJobConfig(write_disposition=env('BQ_WRITE_DISPOSITION', 'WRITE_TRUNCATE'), autodetect=True)
    for name, frame in outputs.items():
        table_name = __import__('re').sub(r'[^A-Za-z0-9_]+', '_', Path(str(name)).stem).strip('_').lower() or PROJECT_NAME
        table_id = f'{project}.{dataset}.{table_name}'
        if frame is None or len(getattr(frame, 'columns', [])) == 0:
            print(f'SKIPPED_EMPTY_OUTPUT: {name} (no schema)')
            continue
        client.load_table_from_dataframe(frame, table_id, job_config=job_config).result()
        print(f'Published {len(frame):,} rows to {table_id}')

def main() -> None:
    parser = argparse.ArgumentParser(description='Run generated Alteryx Python pipeline.')
    parser.add_argument('--publish-bq', action='store_true', help='Publish outputs to BigQuery.')
    parser.add_argument('--local-output', default='output', help='Local CSV output folder.')
    args = parser.parse_args()
    dataframes = {source.get('name') or f'source_{index}': read_source(source) for index, source in enumerate(SOURCES, start=1)}
    outputs = transform(dataframes)
    write_local_outputs(outputs, args.local_output)
    print(f'Wrote {len(outputs)} output file(s) to {args.local_output}/')
    if args.publish_bq:
        publish_outputs_to_bigquery(outputs, env('BQ_DATASET') or env('GCP_BIGQUERY_DATASET'), env('GCP_PROJECT_ID'))

if __name__ == '__main__':
    main()
