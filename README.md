<<<<<<< HEAD
# AlteryxAi
=======
# AlteryxAI Context Engineering Accelerator

Full accelerator copy with next-generation context-engineering enhancements.

This repo was seeded from the working accelerator so the existing login, discovery, upload, summary, publish, and report flows remain available while the context-engineered migration and validation approach is tested.

## Direction

- Use deterministic parsing, graph analysis, conversion rules, schema extraction, and reconciliation wherever possible.
- Use probabilistic models only for complex workflow or macro interpretation, remediation guidance, and human-readable validation explanations.
- Continue Hugging Face for BRD generation and executive summaries.
- Use Anthropic first, with OpenAI fallback, for complex workflow and macro reasoning.
- Treat LLM output as advisory or transform-specific. Validation verdicts must come from deterministic metrics.

## Core Modules

- `backend/app/services/ai_context.py`
  Builds a versioned, hashed migration context from workflow metadata, conversion steps, source schemas, policies, and validation requirements.

- `backend/app/services/llm_routing.py`
  Decides whether a workflow should remain deterministic, use LLM guidance only, or require LLM-assisted remediation.

- `backend/app/services/reconciliation_engine.py`
  Profiles source and target datasets and compares row count, columns, not-null counts, min/max, sum, and average metrics.

- `frontend/src/components/ReconciliationDashboard`
  Experimental UI for showing validation status, accuracy score, failed checks, and advisory investigation notes.

## Intended Flow

```text
Alteryx workflow/package
  -> deterministic parser
  -> graph/schema/lineage extraction
  -> migration context builder
  -> rule-based conversion where supported
  -> LLM only for complex nodes/macros
  -> deterministic target generation
  -> reconciliation metrics
  -> validation report + optional LLM explanation
```

## LLM Strategy

LLMs should receive a compact, structured context pack, not a loose prompt. The context pack includes:

- workflow graph facts
- selected/skipped nodes
- sources and schemas
- conversion steps
- unresolved constructs
- target platform rules
- validation contract
- deterministic fallback notes

The model should return strict JSON remediation plans, not final unvalidated migration truth.

## Run The Experimental App

Backend:

```powershell
cd C:\Project\Alteryx_Update\AlteryxAi\separate_repos\alteryx_context_engineering_accelerator\backend
$py='C:\Users\Ram V\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py -m pip install -r requirements.txt
& $py -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Frontend:

```powershell
cd C:\Project\Alteryx_Update\AlteryxAi\separate_repos\alteryx_context_engineering_accelerator\frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

The copied backend `.env` follows the previous accelerator structure. Put real provider keys in `backend\.env` when you want Anthropic, OpenAI, or Hugging Face calls enabled.

The reconciliation dashboard is available after publish through the `View checks` button, or directly at `/reconciliation` after selecting/uploading a workflow.
>>>>>>> f19848a7fc7bad1b7e7679688ea3b79fcaf90cf4
