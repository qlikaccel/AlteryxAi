import "./SummaryPage.css";
import { useEffect, useState, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchTables, fetchTableData, fetchTableDataSimple, exportTableAsCSV, fetchLoadScript, parseLoadScript } from "../api/qlikApi";
import Csvicon from "../assets/Csvicon.png";
import { useWizard } from "../context/WizardContext";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
import Paper from "@mui/material/Paper";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";

type TableInfo = string | { name: string;[key: string]: any };
type Row = Record<string, any>;



export default function SummaryPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const pageStartTimeRef = useRef<number | null>(null);

  const [appId, setAppId] = useState<string>("");
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [filteredTables, setFilteredTables] = useState<TableInfo[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>("");
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [tableLoading, setTableLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [summary, setSummary] = useState<any>(null);
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [totalRows, setTotalRows] = useState<number>(0);

  const SERVER_FETCH_MAX = 200000;

  // Relationship / star-schema helpers
  const [mainTable, setMainTable] = useState<string | null>(null);
  const [relations, setRelations] = useState<Record<string, string[]>>({});

  // ── TABS ──────────────────────────────────────────────────────────────────
  // "sourceTypes" | "summary" | "mquery" | "er"
  // "er" replaces the old Schema modal button.
  // ──────────────────────────────────────────────────────────────────────────
  const [activeTab, setActiveTab] = useState<"sourceTypes" | "summary" | "mquery" | "er">("sourceTypes");
  const [sourceTypesTab, setSourceTypesTab] = useState<"database" | "scripts" | "csv" | null>(null);

  // LoadScript and MQuery Display States
  const [loadscript, setLoadscript] = useState<string>("");
  const [parsedScript, setParsedScript] = useState<any>(null);
  const [mquery, setMquery] = useState<string>("");
  const [convertingToMquery, setConvertingToMquery] = useState(false);
  const [loadscriptError, setLoadscriptError] = useState<string>("");

  // Publish MQuery to Power BI states
  const [publishingMQuery, setPublishingMQuery] = useState(false);
  const [publishStatus, setPublishStatus] = useState<"idle" | "success" | "error">("idle");
  const [publishMessage, setPublishMessage] = useState<string>("");
  const [dataSourcePath, setDataSourcePath] = useState<string>("");

  const DEFAULT_SHAREPOINT_URL = "https://sorimtechnologies.sharepoint.com";

  // LoadScript type detection and URL validation
  const [isCsvLoadscript, setIsCsvLoadscript] = useState<boolean>(false);
  const [isValidUrl, setIsValidUrl] = useState<boolean>(false);
  const [urlValidationError, setUrlValidationError] = useState<string>("");

  // AI Executive Summary
  const [aiSummaryBullets, setAiSummaryBullets] = useState<string[]>([]);
  const [aiSummaryLoading, setAiSummaryLoading] = useState(false);
  const [_aiSummaryError, setAiSummaryError] = useState<string>("");

  // URL autocomplete history
  const [urlHistory, setUrlHistory] = useState<string[]>([]);
  const [showUrlSuggestions, setShowUrlSuggestions] = useState(false);
  const [filteredSuggestions, setFilteredSuggestions] = useState<string[]>([]);

  // Helper: Detect if loadscript is CSV-based or inline
  const detectCsvLoadscript = (script: string): boolean => {
    if (!script) return false;
    const lower = script.toLowerCase();
    if (/inline\s*\[/.test(lower)) return false;
    return /from\s+[\[\']?(?:lib:\/\/|file:\/\/|https?:\/\/|\/|[a-z]:[\\\/]).*?(?:\.csv|\.qvd|\.xlsx?|\.txt|\])/i.test(lower);
  };

  // Helper: Validate ONLY SharePoint URLs
  const validateSharePointUrl = (url: string): { isValid: boolean; error?: string } => {
    if (!url || url.trim().length === 0) {
      return { isValid: false, error: "URL cannot be empty" };
    }
    const trimmed = url.trim();
    if (!trimmed.toLowerCase().startsWith("https://")) {
      return { isValid: false, error: "❌ Must start with https://" };
    }
    if (trimmed.toLowerCase().startsWith("http://")) {
      return { isValid: false, error: "❌ Must use HTTPS (not HTTP). Use: https://" };
    }
    const hasSharePointDomain = trimmed.toLowerCase().includes(".sharepoint.com");
    if (!hasSharePointDomain) {
      if (!trimmed.includes(".com")) {
        return { isValid: false, error: "❌ Missing .com - Should end with .sharepoint.com" };
      }
      if (!trimmed.toLowerCase().includes("sharepoint")) {
        return { isValid: false, error: "❌ Missing 'sharepoint' - Should be: https://COMPANYNAME.sharepoint.com" };
      }
      return { isValid: false, error: "❌ Invalid format. Should be: https://COMPANYNAME.sharepoint.com" };
    }
    const sharepointMatch = trimmed.match(/https:\/\/([^.]+)\.sharepoint\.com/i);
    if (!sharepointMatch || !sharepointMatch[1]) {
      return { isValid: false, error: "❌ Missing company name - Should be: https://COMPANYNAME.sharepoint.com" };
    }
    const companyName = sharepointMatch[1];
    if (companyName.length === 0 || !/[a-z0-9]/i.test(companyName)) {
      return { isValid: false, error: "❌ Invalid company name - Should be: https://COMPANYNAME.sharepoint.com" };
    }
    return { isValid: true };
  };

  // Helper: build relation graph from `tables`
  const buildRelations = (tableList: TableInfo[]) => {
    const map: Record<string, Set<string>> = {};

    const normalizeFields = (t: TableInfo): Set<string> => {
      const out = new Set<string>();
      if (!t || typeof t === "string") return out;
      const raw = (t as any).fields || (t as any).columns || [];
      for (const f of (raw || [])) {
        if (!f) continue;
        if (typeof f === "string") { out.add(f.toLowerCase()); continue; }
        const fname = (f.name || f.qName || f.field || f.key || "").toString();
        if (fname) out.add(fname.toLowerCase());
      }
      return out;
    };

    const names = (tableList || []).map((t) => (typeof t === 'string' ? t : t?.name || '')).filter(Boolean);
    const fieldSets: Record<string, Set<string>> = {};
    for (const t of tableList) {
      const name = typeof t === 'string' ? t : t?.name || '';
      if (!name) continue;
      fieldSets[name] = normalizeFields(t);
      map[name] = new Set();
    }

    for (let i = 0; i < names.length; i++) {
      for (let j = i + 1; j < names.length; j++) {
        const a = names[i];
        const b = names[j];
        const setA = fieldSets[a] || new Set();
        const setB = fieldSets[b] || new Set();
        let shared = 0;
        for (const f of setA) {
          if (setB.has(f)) { shared++; break; }
        }
        if (shared > 0) { map[a].add(b); map[b].add(a); }
      }
    }

    const out: Record<string, string[]> = {};
    for (const k of Object.keys(map)) { out[k] = Array.from(map[k]); }
    return out;
  };

  // Helper: check whether two tables share at least one field
  const shareFields = (aName: string, bName: string) => {
    if (!aName || !bName) return false;
    const find = (n: string) => (tables || []).find(t => (typeof t === 'string' ? t : t?.name) === n) as any;
    const a = find(aName);
    const b = find(bName);
    const getNames = (tbl: any) => {
      const raw = tbl && typeof tbl !== 'string' ? (tbl.fields || tbl.columns || []) : [];
      return (raw || []).map((x: any) => (typeof x === 'string' ? x : (x.name || x.qName || String(x))).toLowerCase());
    };
    const fieldsA = getNames(a);
    const fieldsB = getNames(b);
    if (!fieldsA.length || !fieldsB.length) return false;
    const setA = new Set(fieldsA);
    for (const f of fieldsB) { if (setA.has(f)) return true; }
    return false;
  };

  // Track page load start time
  useEffect(() => {
    pageStartTimeRef.current = Date.now();
  }, []);

  // Save activeTab to sessionStorage
  useEffect(() => {
    sessionStorage.setItem("summaryActiveTab", activeTab);
  }, [activeTab]);

  // When user navigates to the M Query tab, pre-fill the SharePoint URL
  useEffect(() => {
    if (activeTab !== "mquery") return;
    const defaultUrl = DEFAULT_SHAREPOINT_URL;
    setDataSourcePath(defaultUrl);
    const validation = validateSharePointUrl(defaultUrl);
    setIsValidUrl(validation.isValid);
    setUrlValidationError(validation.error || "");
  }, [activeTab]);

  // Load URL history from localStorage
  useEffect(() => {
    const stored = localStorage.getItem("sharepoint_url_history");
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed)) setUrlHistory(parsed);
      } catch (e) { }
    }
  }, []);

  // Data-table controls
  const [tableQuery, setTableQuery] = useState<string>("");
  const [pageSize, setPageSize] = useState<number>(10);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [tableListQuery, setTableListQuery] = useState<string>("");
  const [orderBy, setOrderBy] = useState<string>("");
  const [order, setOrder] = useState<"asc" | "desc">("asc");

  const processedRows = useMemo(() => {
    let out = rows || [];
    if (tableQuery.trim()) {
      const q = tableQuery.toLowerCase();
      out = out.filter((r) => Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(q)));
    }
    if (orderBy) {
      out = out.slice().sort((a: any, b: any) => {
        const va = a[orderBy]; const vb = b[orderBy];
        if (va === vb) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === "number" && typeof vb === "number") return order === "asc" ? va - vb : vb - va;
        const sa = String(va).toLowerCase(); const sb = String(vb).toLowerCase();
        if (sa < sb) return order === "asc" ? -1 : 1;
        if (sa > sb) return order === "asc" ? 1 : -1;
        return 0;
      });
    }
    return out;
  }, [rows, tableQuery, orderBy, order]);

  const totalEntries = totalRows && totalRows > 0 ? totalRows : processedRows.length;
  const totalPages = Math.max(1, Math.ceil(totalEntries / pageSize));
  const current = Math.min(currentPage, totalPages);
  const startIndex = totalEntries ? (current - 1) * pageSize : 0;
  const endIndex = Math.min(startIndex + pageSize, totalEntries);
  const visibleRows = processedRows;

  const handleRequestSort = (property: string) => {
    if (orderBy === property) { setOrder((o) => (o === "asc" ? "desc" : "asc")); }
    else { setOrderBy(property); setOrder("asc"); }
  };

  const pageNumbers = useMemo<(number | string)[]>(() => {
    const nums: (number | string)[] = [];
    const max = totalPages; const cur = current;
    if (max <= 7) { for (let i = 1; i <= max; i++) nums.push(i); }
    else {
      nums.push(1);
      if (cur > 3) nums.push("...");
      const start = Math.max(2, cur - 1); const end = Math.min(max - 1, cur + 1);
      for (let i = start; i <= end; i++) nums.push(i);
      if (cur < max - 2) nums.push("...");
      nums.push(max);
    }
    return nums;
  }, [totalPages, current]);

  useEffect(() => { setCurrentPage(1); }, [pageSize, tableQuery]);

  useEffect(() => {
    if (!selectedTable) return;
    const loadPage = async () => {
      setTableLoading(true);
      try {
        const offset = (currentPage - 1) * pageSize;
        const data = await fetchTableData(appId, selectedTable, pageSize, offset);
        setRows(data || []);
      } catch (e) { console.error("❌ Failed to load page:", e); }
      finally { setTableLoading(false); }
    };
    loadPage();
  }, [currentPage, pageSize, selectedTable, appId]);

  useEffect(() => {
    if (!tableListQuery) { setFilteredTables((tables || []).slice()); return; }
    const q = tableListQuery.toLowerCase();
    const filtered = (tables || []).filter((t) => {
      const name = typeof t === "string" ? t : t?.name || "";
      return String(name).toLowerCase().includes(q);
    }).slice();
    setFilteredTables(filtered);
  }, [tableListQuery, tables]);

  // 1 → GET APP ID
  useEffect(() => {
    const state = location.state as any;
    const passedAppId = state?.appId || sessionStorage.getItem("appSelected");
    if (!passedAppId) { alert("No app selected. Please go back and select an app."); navigate("/apps"); return; }
    setAppId(passedAppId);
  }, [location, navigate]);

  // 2 → LOAD TABLE LIST
  const { stopTimer, startTimer, getLastElapsed } = useWizard();

  useEffect(() => {
    if (!appId) return;
    if (sessionStorage.getItem("lastTimerTarget") !== "/summary") startTimer?.("/summary");

    fetchTables(appId)
      .then((data) => {
        const cleaned = (data || []).filter((t: any) => {
          const name = typeof t === 'string' ? t : t?.name || '';
          if (!name) return false;
          return !name.toLowerCase().startsWith('@syn');
        });

        const getTimestamp = (t: any) => {
          if (!t || typeof t === 'string') return 0;
          const candidates = ['added_timestamp', 'created', 'createdAt', 'created_at', 'createdDate', 'modifiedDate', 'lastModifiedDate', 'lastReloadTime', 'lastReload'];
          for (const k of candidates) {
            const v = t[k];
            if (v) {
              const asNum = typeof v === 'number' ? v : Number(v);
              if (!isNaN(asNum) && asNum > 0) return asNum;
              const parsed = Date.parse(String(v));
              if (!isNaN(parsed)) return parsed;
            }
          }
          return 0;
        };

        const sorted = (cleaned || []).slice().sort((x: any, y: any) => {
          const tx = getTimestamp(x); const ty = getTimestamp(y);
          if (tx !== ty) return ty - tx;
          const xi = (typeof x === 'string') ? false : !!x.is_new;
          const yi = (typeof y === 'string') ? false : !!y.is_new;
          if (xi && !yi) return -1; if (!xi && yi) return 1;
          const nx = typeof x === 'string' ? x : x?.name || '';
          const ny = typeof y === 'string' ? y : y?.name || '';
          return String(nx).localeCompare(String(ny), undefined, { sensitivity: 'base' });
        });

        setTables(sorted);
        const rel = buildRelations(sorted);
        setRelations(rel);

        const degreeOf = (n: string) => (rel[n] || []).length || 0;
        const nameOf = (t: any) => (typeof t === 'string' ? t : t?.name || '');
        let detectedMain: string | null = null;

        for (const t of sorted) {
          const n = nameOf(t);
          if (!n) continue;
          if (/\b(master|fact|main)\b/i.test(n) && degreeOf(n) > 0) { detectedMain = n; break; }
        }

        if (!detectedMain) {
          let bestName: string | null = null; let bestDeg = -1;
          for (const t of sorted) {
            const n = nameOf(t); const deg = degreeOf(n);
            if (deg > bestDeg) { bestDeg = deg; bestName = n; }
            else if (deg === bestDeg && deg > 0) {
              const fcount = typeof t === 'string' ? 0 : (t?.fields || []).length || 0;
              const found = sorted.find((s: TableInfo) => nameOf(s) === bestName);
              const currentFcount = typeof found === 'string' ? 0 : ((found as any)?.fields || []).length || 0;
              if (fcount > currentFcount) bestName = n;
            }
          }
          if (bestName && bestDeg > 0) detectedMain = bestName;
        }

        if (!detectedMain) {
          const explicit = sorted.find((t: any) => /\b(master|fact|main)\b/i.test(nameOf(t)));
          if (explicit) detectedMain = nameOf(explicit);
        }

        if (detectedMain) {
          const degree = (rel[detectedMain] || []).length || 0;
          const isExplicit = /\b(master|fact|main)\b/i.test(detectedMain);
          if (degree > 0 || isExplicit) setMainTable(detectedMain);
          else setMainTable(null);
        } else { setMainTable(null); }

        setFilteredTables(sorted);
        console.log("All tables fetched. Detected main:", detectedMain, "relations:", rel);

        if (detectedMain) loadData(detectedMain);
        else if (sorted && sorted.length > 0) {
          const firstTableName = typeof sorted[0] === "string" ? sorted[0] : sorted[0]?.name;
          if (firstTableName) loadData(firstTableName);
        }
      })
      .catch(() => { })
      .finally(() => { setLoading(false); });
  }, [appId, stopTimer, startTimer]);

  // 3 → LOAD DATA FOR SELECTED TABLE
  const formatElapsed = (msTotal: number) => {
    const minutes = Math.floor(msTotal / 60000);
    const seconds = Math.floor((msTotal % 60000) / 1000);
    const centis = Math.floor((msTotal % 1000) / 10);
    const pad = (n: number, width = 2) => String(n).padStart(width, "0");
    return `${pad(minutes)}m : ${pad(seconds)}s : ${pad(centis)}ms`;
  };

  const loadData = async (tableName: string) => {
    if (!tableName || tableName === selectedTable) return;
    setSelectedTable(tableName);
    setTableLoading(true);
    setRows([]);
    setSummary(null);
    setAiSummaryBullets([]);
    setAiSummaryError("");
    startTimer?.(`/summary/data/${tableName}`);

    try {
      const meta = await fetchTableDataSimple(appId, tableName).catch(() => null);
      const total = meta?.row_count || meta?.rowCount || meta?.no_of_rows || 0;
      setTotalRows(total || 0);

      const firstPageLimit = Math.min(pageSize, total > 0 ? total : pageSize);
      if (total > SERVER_FETCH_MAX) console.warn(`Table ${tableName} has ${total} rows — paging on demand`);

      const data = await fetchTableData(appId, tableName, firstPageLimit, 0);
      setRows(data || []);
      setCurrentPage(1);

      try {
        sessionStorage.setItem("selectedTable", tableName);
        sessionStorage.setItem("selectedRows", JSON.stringify(data || []));
      } catch (e) { }

      const { generateSummaryFromData } = await import("../api/qlikApi");
      const summary = generateSummaryFromData(data, tableName);
      setSummary(summary);

      fetchAiSummary(data, tableName);

      try {
        const scriptResult = await fetchLoadScript(appId, tableName);
        if (scriptResult.status === "success" || scriptResult.status === "partial_success") {
          const script = scriptResult.loadscript || "";
          setLoadscript(script);
          const isCsv = detectCsvLoadscript(script);
          setIsCsvLoadscript(isCsv);
          if (isCsv) {
            setDataSourcePath(DEFAULT_SHAREPOINT_URL);
            const validation = validateSharePointUrl(DEFAULT_SHAREPOINT_URL);
            setIsValidUrl(validation.isValid);
            setUrlValidationError(validation.error || "");
          }
          if (script) {
            try {
              const parseResult = await parseLoadScript(script);
              if (parseResult.status === "success") { setParsedScript(parseResult); }
            } catch (parseError) { console.warn("Auto-parse failed", parseError); }
          }
        }
      } catch (scriptError) { console.warn("⚠️ Could not auto-fetch LoadScript:", scriptError); }

    } catch (e) {
      console.error("❌ Error loading table data:", e);
      const errorMessage = e instanceof Error ? e.message : String(e);
      alert(`Failed to load table "${tableName}".\n\nError: ${errorMessage}\n\nSuggestions:\n1. Verify the table name\n2. Ensure the app has been reloaded\n3. Check the backend is running`);
    } finally {
      setTableLoading(false);
      const tableElapsed = stopTimer?.(`/summary/data/${tableName}`);
      if (tableElapsed) { setPageLoadTime(tableElapsed); }
      else {
        const navElapsed = getLastElapsed?.("/summary");
        if (navElapsed) setPageLoadTime(navElapsed);
        else if (pageStartTimeRef.current) setPageLoadTime(formatElapsed(Date.now() - pageStartTimeRef.current));
      }
    }
  };

  // CSV DOWNLOAD
  const downloadCSV = async () => {
    if (!rows.length && !totalRows) { alert("No data"); return; }
    if (totalRows && totalRows > rows.length) {
      try {
        const csv = await exportTableAsCSV(appId, selectedTable);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = `${selectedTable || "data"}.csv`; a.click();
        window.URL.revokeObjectURL(url);
        return;
      } catch (e) { console.error("Export failed, falling back to page CSV:", e); }
    }
    const headers = Object.keys(rows[0] || {});
    const csv = [headers.join(","), ...rows.map((r) => headers.map((h) => `"${r[h] ?? ""}"`).join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${selectedTable || "data"}.csv`; a.click();
    window.URL.revokeObjectURL(url);
  };

  // CONVERT TO MQUERY
  const handleConvertToMQuery = async () => {
    if (!loadscript) { setLoadscriptError("No LoadScript available"); return; }
    if (!selectedTable) { setLoadscriptError("Please select a table first"); return; }
    try {
      setConvertingToMquery(true); setLoadscriptError(""); setMquery("");
      let scriptToConvert = parsedScript;
      if (!scriptToConvert) {
        const parseResult = await parseLoadScript(loadscript);
        if (parseResult.status === "success") { scriptToConvert = parseResult; setParsedScript(parseResult); }
        else throw new Error("Failed to parse LoadScript");
      }
      //const apiBase = window.location.hostname.includes('localhost') || window.location.hostname === '127.0.0.1'
      //  ? 'http://127.0.0.1:8000' : 'https://qlikai-app-ltmrv.ondigitalocean.app';
      const apiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const convertResponse = await fetch(`${apiBase}/api/migration/convert-to-mquery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parsed_script_json: JSON.stringify(scriptToConvert), table_name: "", base_path: dataSourcePath.trim() || "" }),
      });
      if (!convertResponse.ok) throw new Error(`Failed to convert to M Query: ${convertResponse.status}`);
      const convertResult = await convertResponse.json();
      if (convertResult.status !== "success" && convertResult.status !== "partial_success") throw new Error(convertResult.message || "Failed to convert to M Query");
      setMquery(convertResult.m_query || "");
    } catch (error: any) {
      setLoadscriptError(error.message || "Failed to convert to M Query");
    } finally { setConvertingToMquery(false); }
  };

  const fetchAiSummary = async (tableRows: any[], tableName: string) => {
    if (!tableRows || tableRows.length === 0) return;
    setAiSummaryLoading(true); setAiSummaryError(""); setAiSummaryBullets([]);
    
    try {
      const apiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const res = await fetch(`${apiBase}/chat/summary-hf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ table_name: tableName, data: tableRows.slice(0, 500) }),
      });
      const rawText = await res.text();
      let result: any = {};
      try { result = rawText ? JSON.parse(rawText) : {}; } catch {
        setAiSummaryError(`Server error: ${rawText.slice(0, 200) || "Empty response"}`); return;
      }
      if (!res.ok || !result.success) { setAiSummaryError(result.detail || result.error || `HTTP ${res.status}`); return; }
      setAiSummaryBullets(result.bullets || []);
    } catch (e: any) { setAiSummaryError(e.message || "Error calling AI summary"); }
    finally { setAiSummaryLoading(false); }
  };

  const handlePublishMQuery = async () => {
    if (!mquery && !loadscript) { setPublishStatus("error"); setPublishMessage("No M Query available. Click 'Convert to MQuery' first."); return; }
    try {
      setPublishingMQuery(true);
      let masterRowCount = totalRows || (rows?.length || 0);
      let relatedTables: string[] = [];
      let relatedTablesCount = 1;
      if (selectedTable && relations[selectedTable]) { relatedTables = relations[selectedTable].slice(); relatedTablesCount = 1 + relatedTables.length; }
      else if (mainTable && relations[mainTable]) { relatedTables = relations[mainTable].slice(); relatedTablesCount = 1 + relatedTables.length; }
      let totalRowsAllTables = masterRowCount;
      for (const relTable of relatedTables) {
        try {
          const relMeta = await fetchTableDataSimple(appId, relTable).catch(() => null);
          totalRowsAllTables += relMeta?.row_count || relMeta?.rowCount || relMeta?.no_of_rows || 0;
        } catch (e) { }
      }
      if (isCsvLoadscript && isValidUrl && dataSourcePath.trim()) {
        const updatedHistory = [dataSourcePath, ...urlHistory.filter(url => url !== dataSourcePath)].slice(0, 10);
        setUrlHistory(updatedHistory);
        localStorage.setItem("sharepoint_url_history", JSON.stringify(updatedHistory));
      }
      sessionStorage.setItem("publishMethod", "M_QUERY");
      sessionStorage.setItem("exportComplete", "true");
      navigate("/publish", {
        state: {
          appId, selectedTable, publishMethod: "M_QUERY", showWorkflow: true,
          tableCount: relatedTablesCount, totalRows: totalRowsAllTables, rowCount: totalRowsAllTables,
          columns: rows && rows.length > 0 ? Object.keys(rows[0]) : [],
          mqueryData: { dataset_name: selectedTable || "Qlik_Dataset", combined_mquery: mquery || "", raw_script: mquery ? "" : loadscript, data_source_path: dataSourcePath?.trim() || "" },
        },
      });
    } catch (error: any) {
      setPublishStatus("error");
      setPublishMessage(`Failed to prepare publishing: ${error.message || "Unknown error"}`);
      setPublishingMQuery(false);
    }
  };

  const masterMap = useMemo(() => {
    const map = new Map<string, string>();
    const groups: Record<string, any[]> = {};
    (tables || []).forEach((t) => {
      const name = typeof t === "string" ? t : t?.name || "";
      if (!name) return;
      const prefix = name.includes("_") ? name.split("_")[0] : "__noprefix__";
      groups[prefix] = groups[prefix] || [];
      groups[prefix].push(t);
    });
    Object.keys(groups).forEach((prefix) => {
      const group = groups[prefix];
      if (!group || group.length <= 1) return;
      const candidates = group.map((g: any) => {
        const name = typeof g === "string" ? g : g?.name || "";
        const fields = typeof g === "string" ? 0 : (g?.fields || []).length || 0;
        return { name, fields };
      });
      const fordExplicit = candidates.find((c: any) => c.name.toLowerCase() === 'ford_vehicle_fact');
      if (fordExplicit) { map.set(prefix, fordExplicit.name); return; }
      const explicit = candidates.find((c: any) => /fact|master|main/i.test(c.name));
      if (explicit) { map.set(prefix, explicit.name); return; }
      candidates.sort((a: any, b: any) => (b.fields || 0) - (a.fields || 0));
      map.set(prefix, candidates[0].name);
    });
    return map;
  }, [tables]);

  const isMasterTable = (name: string) => {
    if (!name) return false;
    if (mainTable) return name === mainTable || name.toLowerCase() === "ford_vehicle_fact";
    const lower = name.toLowerCase();
    if (lower === "ford_vehicle_fact") return true;
    const prefix = name.includes("_") ? name.split("_")[0] : null;
    if (!prefix) return false;
    return masterMap.get(prefix) === name;
  };

  const isRelatedTable = (name: string) => {
    if (!name) return false;
    if (mainTable && relations && relations[mainTable]) return relations[mainTable].includes(name);
    const prefix = name.includes("_") ? name.split("_")[0] : null;
    if (!prefix) return false;
    const master = masterMap.get(prefix);
    if (!master || master === name) return false;
    if (shareFields(master, name)) return true;
    return false;
  };

  const sortedFilteredTables = useMemo(() => {
    const arr = (filteredTables || []).slice();
    if (mainTable) {
      arr.sort((a, b) => {
        const an = typeof a === 'string' ? a : a?.name || '';
        const bn = typeof b === 'string' ? b : b?.name || '';
        if (an === mainTable && bn !== mainTable) return -1;
        if (bn === mainTable && an !== mainTable) return 1;
        const relSet = new Set(relations[mainTable] || []);
        const aRel = relSet.has(an); const bRel = relSet.has(bn);
        if (aRel && !bRel) return -1; if (!aRel && bRel) return 1;
        const aMaster = isMasterTable(an); const bMaster = isMasterTable(bn);
        if (aMaster && !bMaster) return -1; if (!aMaster && bMaster) return 1;
        return an.localeCompare(bn);
      });
      return arr;
    }
    arr.sort((a, b) => {
      const an = typeof a === 'string' ? a : a?.name || '';
      const bn = typeof b === 'string' ? b : b?.name || '';
      const aMaster = isMasterTable(an); const bMaster = isMasterTable(bn);
      if (aMaster && !bMaster) return -1; if (!aMaster && bMaster) return 1;
      return an.localeCompare(bn);
    });
    return arr;
  }, [filteredTables, masterMap, mainTable, relations]);

  const isSelectionMaster = !!(selectedTable && isMasterTable(selectedTable));
  const isExportAllowed = Boolean(selectedTable && (isSelectionMaster || !isRelatedTable(selectedTable)));

  const prepareAndNavigateToExport = async (tableToExport?: string) => {
    setExporting(true);
    try {
      stopTimer?.("/summary");
      sessionStorage.setItem("summaryComplete", "true");
      startTimer?.("/publish");
      const sel = tableToExport || selectedTable || (sessionStorage.getItem("selectedTable") || "");
      if (!sel) { setExporting(false); alert("No table selected for export."); return; }

      let masterRows = rows;
      let masterRowCount = (rows || []).length;

      if ((tableToExport && tableToExport !== selectedTable) || (!masterRows || masterRows.length === 0)) {
        try {
          setTableLoading(true);
          const meta = await fetchTableDataSimple(appId, sel).catch(() => null);
          masterRowCount = meta?.row_count || meta?.rowCount || meta?.no_of_rows || 0;
          const loadLimit = masterRowCount > 0 ? Math.min(masterRowCount, SERVER_FETCH_MAX) : SERVER_FETCH_MAX;
          const loaded = await fetchTableData(appId, sel, loadLimit);
          masterRows = loaded || [];
          setSelectedTable(sel); setRows(masterRows);
          const { generateSummaryFromData } = await import("../api/qlikApi");
          setSummary(generateSummaryFromData(masterRows, sel));
        } catch (e) {
          console.warn("Failed to load table prior to publish:", e);
          alert("Failed to load table data for publish. See console for details.");
          setTableLoading(false);
          setExporting(false);
          return;
        } finally { setTableLoading(false); }
      } else {
        try {
          const meta = await fetchTableDataSimple(appId, sel).catch(() => null);
          masterRowCount = meta?.row_count || meta?.rowCount || meta?.no_of_rows || (masterRows || []).length;
        } catch (e) { masterRowCount = (masterRows || []).length; }
      }

      const prefix = sel && sel.includes("_") ? sel.split("_")[0] : null;
      const candidateNames = (tables || []).map((t) => (typeof t === "string" ? t : t?.name)).filter(Boolean) as string[];
      let related: string[] = [];
      if (mainTable && sel === mainTable && relations && relations[mainTable]) related = relations[mainTable].slice();
      else if (prefix) related = candidateNames.filter(n => n.startsWith(prefix + "_") && n !== sel).filter(n => shareFields(sel, n));

      const headers = masterRows.length > 0 ? Object.keys(masterRows[0]) : [];
      const csv = [headers.join(","), ...masterRows.map((r: any) => headers.map((h) => `"${r[h] ?? ""}"`).join(","))].join("\n");
      const cols = headers;
      const daxLines = [] as string[];
      daxLines.push(`-- DAX export skeleton for table: ${sel}`);
      daxLines.push(`-- Columns:`);
      cols.forEach((c) => daxLines.push(`-- ${c}`));
      daxLines.push(`\n-- Sample measure`);
      daxLines.push(`[${sel} Count] = COUNTROWS('${sel}')`);
      const daxContent = daxLines.join("\n");

      if (isCsvLoadscript && isValidUrl && dataSourcePath.trim()) {
        const updatedHistory = [dataSourcePath, ...urlHistory.filter(url => url !== dataSourcePath)].slice(0, 10);
        setUrlHistory(updatedHistory);
        localStorage.setItem("sharepoint_url_history", JSON.stringify(updatedHistory));
      }

      if (!related || related.length === 0) {
        navigate("/export", {
          state: {
            appId, appName: location.state?.appName || sessionStorage.getItem("appName") || appId,
            selectedTable: sel, rows: masterRows || [], totalRows: masterRowCount, totalTablesCount: 1,
            exportOptions: { combined: true, separate: false },
            csvPayloads: { migration_csv: csv }, daxPayloads: { migration_dax: daxContent },
          },
        });
        return;
      }

      setTableLoading(true);
      const selectedData: any[] = [];
      const csvPayloads: Record<string, string> = { migration_csv_0: csv };
      const daxPayloads: Record<string, string> = { migration_dax: daxContent };
      selectedData.push({ name: sel, data: { name: sel, rows: masterRows || [], summary }, actualRowCount: masterRowCount });

      for (let idx = 0; idx < related.length; idx++) {
        const relName = related[idx];
        try {
          const relMeta = await fetchTableDataSimple(appId, relName).catch(() => null);
          const relTotal = relMeta?.row_count || relMeta?.rowCount || relMeta?.no_of_rows || 0;
          const relLimit = relTotal > 0 ? Math.min(relTotal, SERVER_FETCH_MAX) : SERVER_FETCH_MAX;
          const relRows = await fetchTableData(appId, relName, relLimit);
          const { generateSummaryFromData } = await import("../api/qlikApi");
          const relSummary = generateSummaryFromData(relRows, relName);
          selectedData.push({ name: relName, data: { name: relName, rows: relRows, summary: relSummary }, actualRowCount: relTotal });
          const relHeaders = relRows.length > 0 ? Object.keys(relRows[0]) : [];
          const relCsv = [relHeaders.join(","), ...relRows.map((r: any) => relHeaders.map((h) => `"${r[h] ?? ""}"`).join(","))].join("\n");
          csvPayloads[`migration_csv_${idx + 1}`] = relCsv;
        } catch (e) { console.warn("Failed to load related table:", relName, e); }
      }

      setTableLoading(false);
      const totalAllRows = selectedData.reduce((sum, table) => sum + (table.actualRowCount || table.data?.rows?.length || 0), 0);
      navigate("/export", {
        state: {
          appId, appName: location.state?.appName || sessionStorage.getItem("appName") || appId,
          selectedTables: selectedData, totalRows: totalAllRows, totalTablesCount: selectedData.length,
          exportOptions: { combined: true, separate: true }, csvPayloads, daxPayloads,
        },
      });
    } catch (err) {
      setTableLoading(false);
      setExporting(false);
      console.error(err);
      alert("Failed to prepare related tables for export. See console for details.");
    }
  };


  if (exporting) {
    return <LoadingOverlay isVisible={exporting} message="Exporting your data..." />;
  }

  if (loading) {
    return <LoadingOverlay isVisible={loading} message="Loading tables from QlikSense..." />;
  }

  return (
    <div className="summary-layout">
      {/* LEFT – TABLE NAMES */}
      <div className="left-panel">
        <div className="panel-header">
          <h3 className="title">Tables {`(${tables.length})`}</h3>
          {/* {mainTable && (
            <div style={{ marginTop: 6, fontSize: 12, color: '#444' }}>
              Detected main table: <strong style={{ color: '#0b3a66' }}>{mainTable}</strong>
            </div>
          )} */}
        </div>

        <div className="table-search">
          <input
            type="search"
            placeholder="Search tables..."
            value={tableListQuery}
            onChange={(e) => setTableListQuery(e.target.value)}
            className="table-search-input"
          />
        </div>

        {tables.length === 0 && <p className="no-tables">No tables found</p>}

        {sortedFilteredTables.map((t, i) => {
          const tableName = typeof t === "string" ? t : t?.name;
          const isNew = typeof t === "string" ? false : t?.is_new;
          if (!tableName) return null;
          const master = isMasterTable(tableName);
          const related = isRelatedTable(tableName);
          const cls = `${tableName === selectedTable ? "table-item active" : "table-item"}${master ? " master-row" : ""}${related && !master ? " related-row" : ""}`;
          return (
            <div
              key={i} className={cls} onClick={() => loadData(tableName)}
              title={master ? "Master table — click to export master + its related tables" : related ? "Related table — preview only" : "Click to preview table"}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{tableName}</span>
              </span>
              {!related && (
                <button
                  className="inline-export"
                  title={master ? "Export master + related tables" : "Export this standalone table"}
                  onClick={(e) => { e.stopPropagation(); prepareAndNavigateToExport(tableName); }}
                />
              )}
              {isNew && <span className="new-badge">NEW</span>}
            </div>
          );
        })}
      </div>

      {/* RIGHT – CONTENT */}
      <div className="right-panel">
        {!selectedTable && (
          <div className="empty"><p>👈 Select a table on the left to view its data</p></div>
        )}

        {selectedTable && (
          <>
            {/* HEADER */}
            <div className="header">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%", gap: "20px" }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                  <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: '#1f2937', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {selectedTable}
                    {isSelectionMaster && <span className="master-indicator" style={{ marginLeft: '0px' }}>master</span>}
                  </h2>

                  {/* ── TAB BAR: Source Types · Summary · ER Diagram ── */}
                  <div style={{ display: "flex", gap: "14px", borderBottom: "none", marginLeft: '12px', borderRadius: '6px', padding: '4px' }}>

                    {/* Source Types Tab */}
                    <button
                      type="button"
                      className={`tab-button tab-button--sourceTypes ${activeTab === "sourceTypes" ? "active" : ""}`}
                      onClick={() => setActiveTab("sourceTypes")}
                    >
                      🗂️ Source Types
                    </button>

                    {/* Summary Tab */}
                    <button
                      type="button"
                      className={`tab-button tab-button--summary ${activeTab === "summary" ? "active" : ""}`}
                      onClick={() => setActiveTab("summary")}
                    >
                      📊 Summary
                    </button>

                    {/* ER Diagram Tab — replaces old Schema modal button */}
                    <button
                      type="button"
                      className={`tab-button tab-button--er ${activeTab === "er" ? "active" : ""}`}
                      onClick={() => setActiveTab("er")}
                    >
                      🔷 ER Diagram
                    </button>
                  </div>
                </div>

                {pageLoadTime && <div className="timer-badge">Analysis Time: {pageLoadTime}</div>}
              </div>
            </div>

            {/* ===== TAB CONTENT ===== */}
            <div className="tabs-content">

              {/* SOURCE TYPES TAB */}
              {activeTab === "sourceTypes" && (
                <div className="tab-content source-types-tab">
                  <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', justifyContent: 'space-between', marginBottom: 16 }}>

                    {/* Database card */}
                    <div
                      className="source-card"
                      onClick={() => setSourceTypesTab('database')}
                      style={{
                        flex: '1 1 260px', minWidth: 260,
                        border: sourceTypesTab === 'database' ? '2px solid #0ea5e9' : '1px solid #e5e7eb',
                        borderRadius: 12, padding: 16, cursor: 'pointer',
                        background: sourceTypesTab === 'database' ? 'rgba(14,165,233,0.08)' : '#fff',
                        boxShadow: sourceTypesTab === 'database' ? '0 8px 20px rgba(14,165,233,0.12)' : '0 4px 12px rgba(0,0,0,0.04)',
                        transition: 'all 0.2s ease', position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <span style={{ width: 18, height: 18, borderRadius: '50%', border: sourceTypesTab === 'database' ? '2px solid #0ea5e9' : '2px solid #cbd5e1', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: sourceTypesTab === 'database' ? '#0ea5e9' : 'transparent' }}>
                            {sourceTypesTab === 'database' && <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#fff' }} />}
                          </span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <div style={{ width: 36, height: 36, borderRadius: 10, background: '#e0f2fe', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>🗄️</div>
                            <div>
                              <div style={{ fontSize: 16, fontWeight: 700, color: '#1f2937' }}>Database</div>
                              <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>Direct ODBC/JDBC connection</div>
                            </div>
                          </div>
                        </div>
                      </div>
                      <p style={{ marginTop: 12, fontSize: 13, color: '#475569', lineHeight: 1.5 }}>
                        Connect directly to the source database via ODBC/JDBC. Schema is inferred automatically. Best for live systems where data lives in SQL Server, Oracle, or Snowflake.
                      </p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#f3f4f6', fontSize: 11, fontWeight: 600, color: '#1f2937' }}>ODBC / JDBC</span>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#f3f4f6', fontSize: 11, fontWeight: 600, color: '#1f2937' }}>Live schema</span>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#f3f4f6', fontSize: 11, fontWeight: 600, color: '#1f2937' }}>SQL Server • Oracle</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }} />
                    </div>

                    {/* Scripts card */}
                    <div
                      className="source-card"
                      onClick={() => setSourceTypesTab('scripts')}
                      style={{
                        flex: '1 1 260px', minWidth: 260,
                        border: sourceTypesTab === 'scripts' ? '2px solid #14b8a6' : '1px solid #e5e7eb',
                        borderRadius: 12, padding: 16, cursor: 'pointer',
                        background: sourceTypesTab === 'scripts' ? 'rgba(20,184,166,0.08)' : '#fff',
                        boxShadow: sourceTypesTab === 'scripts' ? '0 8px 20px rgba(20,184,166,0.12)' : '0 4px 12px rgba(0,0,0,0.04)',
                        transition: 'all 0.2s ease', position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <span style={{ width: 18, height: 18, borderRadius: '50%', border: sourceTypesTab === 'scripts' ? '2px solid #14b8a6' : '2px solid #cbd5e1', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: sourceTypesTab === 'scripts' ? '#14b8a6' : 'transparent' }}>
                            {sourceTypesTab === 'scripts' && <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#fff' }} />}
                          </span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <div style={{ width: 36, height: 36, borderRadius: 10, background: '#dcfce7', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>📜</div>
                            <div>
                              <div style={{ fontSize: 16, fontWeight: 700, color: '#1f2937' }}>Scripts</div>
                              <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>Qlik Load Script → M-Query</div>
                            </div>
                          </div>
                        </div>
                      </div>
                      <p style={{ marginTop: 12, fontSize: 13, color: '#475569', lineHeight: 1.5 }}>
                        Parse the Qlik Load Script from your application. Transforms APPLYMAP, INLINE, and RESIDENT tables into Power Query M-code. Full schema and relationship preservation.
                      </p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#fde68a', fontSize: 11, fontWeight: 600, color: '#92400e' }}>M-QUERY</span>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#fde68a', fontSize: 11, fontWeight: 600, color: '#92400e' }}>XMLA</span>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#fde68a', fontSize: 11, fontWeight: 600, color: '#92400e' }}>RELATIONSHIPS</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
                        <button className="continue-export-btn" onClick={() => { setActiveTab('mquery'); setSourceTypesTab('scripts'); }}>
                          Go to Scripts
                        </button>
                      </div>
                    </div>

                    {/* Export CSV card */}
                    <div
                      className="source-card"
                      onClick={() => setSourceTypesTab('csv')}
                      style={{
                        flex: '1 1 260px', minWidth: 260,
                        border: sourceTypesTab === 'csv' ? '2px solid #f97316' : '1px solid #e5e7eb',
                        borderRadius: 12, padding: 16, cursor: 'pointer',
                        background: sourceTypesTab === 'csv' ? 'rgba(251,146,60,0.08)' : '#fff',
                        boxShadow: sourceTypesTab === 'csv' ? '0 8px 20px rgba(249,115,22,0.12)' : '0 4px 12px rgba(0,0,0,0.04)',
                        transition: 'all 0.2s ease', position: 'relative', display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <span style={{ width: 18, height: 18, borderRadius: '50%', border: sourceTypesTab === 'csv' ? '2px solid #f97316' : '2px solid #cbd5e1', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: sourceTypesTab === 'csv' ? '#f97316' : 'transparent' }}>
                            {sourceTypesTab === 'csv' && <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#fff' }} />}
                          </span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                            <div style={{ width: 36, height: 36, borderRadius: 10, background: '#ffedd5', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18 }}>📦</div>
                            <div>
                              <div style={{ fontSize: 16, fontWeight: 700, color: '#1f2937' }}>Export CSV</div>
                              <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>Data export via REST API</div>
                            </div>
                          </div>
                        </div>
                      </div>
                      <p style={{ marginTop: 12, fontSize: 13, color: '#475569', lineHeight: 1.5 }}>
                        Export all table data as CSV and push to Power BI as a push dataset via REST API. Works on any Power BI license. Ideal for flat tables without complex transformations.
                      </p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#dbeafe', fontSize: 11, fontWeight: 600, color: '#1e40af' }}>ANY LICENSE</span>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#dbeafe', fontSize: 11, fontWeight: 600, color: '#1e40af' }}>REST API</span>
                        <span style={{ padding: '4px 10px', borderRadius: 999, background: '#dbeafe', fontSize: 11, fontWeight: 600, color: '#1e40af' }}>FAST DEPLOY</span>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
                        <button
                          className="continue-export-btn"
                          disabled={!isExportAllowed || tableLoading}
                          onClick={() => prepareAndNavigateToExport()}
                          style={{ padding: '10px 14px', fontWeight: 600 }}
                        >
                          Go to Export
                        </button>
                      </div>
                    </div>
                  </div>

                  {!sourceTypesTab && (
                    <div style={{ padding: 14, borderRadius: 10, background: '#f1f5f9', border: '1px solid #e2e8f0', color: '#475569' }}>
                      Select a source type above to continue.
                    </div>
                  )}
                </div>
              )}

              {/* SUMMARY TAB */}
              {activeTab === "summary" && (
                <div className="tab-content summary-tab">
                  <div className="summary-div pie-chart-div">
                    <SummaryReport summary={summary} rows={rows} aiBullets={aiSummaryBullets} aiLoading={aiSummaryLoading} />
                  </div>
                </div>
              )}

              {/* MQUERY TAB */}
              {activeTab === "mquery" && (
                <div className="tab-content mquery-tab">
                  <div className="summary-div loadscript-div">
                    <div className="loadscript-header">
                      <h3>LoadScript Conversion - {selectedTable}</h3>
                    </div>

                    {loadscriptError && <div className="error-message">⚠️ {loadscriptError}</div>}

                    <div className="loadscript-displays">
                      <div className="display-section loadscript-display">
                        <h4>Qlik LoadScript</h4>
                        {!loadscript && <div className="empty-display">Select a table to auto-load its LoadScript</div>}
                        {loadscript && (
                          <>
                            <div className="script-content"><pre>{loadscript}</pre></div>

                            {isCsvLoadscript && (
                              <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginBottom: "10px", padding: "10px 12px", background: isValidUrl ? "#ecfdf5" : "#fef2f2", borderRadius: "6px", border: isValidUrl ? "1px solid #86efac" : "1px solid #fecaca", position: "relative" }}>
                                <label style={{ fontSize: "12px", fontWeight: 600, color: "#0369a1" }}>
                                  📁 Data Source Path <span style={{ fontWeight: 400, color: "#64748b" }}>(required for CSV)</span>
                                </label>
                                <div style={{ position: "relative" }}>
                                  <input
                                    type="text"
                                    value={dataSourcePath}
                                    onChange={(e) => {
                                      const val = e.target.value;
                                      setDataSourcePath(val);
                                      const validation = validateSharePointUrl(val);
                                      setIsValidUrl(validation.isValid);
                                      setUrlValidationError(validation.error || "");
                                      if (val.trim().length > 0) {
                                        const filtered = urlHistory.filter(url => url.toLowerCase().includes(val.toLowerCase()));
                                        setFilteredSuggestions(filtered);
                                        setShowUrlSuggestions(filtered.length > 0);
                                      } else { setShowUrlSuggestions(false); }
                                    }}
                                    onFocus={() => { if (urlHistory.length > 0) { setFilteredSuggestions(urlHistory); setShowUrlSuggestions(true); } }}
                                    onBlur={() => { setTimeout(() => setShowUrlSuggestions(false), 200); }}
                                    placeholder="e.g. https://company.sharepoint.com/Shared Documents/Data/"
                                    style={{ padding: "6px 10px", fontSize: "12px", fontFamily: "monospace", border: isValidUrl ? "1px solid #86efac" : "1px solid #fecaca", transition: "border-color 0.3s ease", borderRadius: "4px", outline: "none", width: "100%", boxSizing: "border-box" as const, background: "#fff" }}
                                  />
                                  {showUrlSuggestions && filteredSuggestions.length > 0 && (
                                    <div style={{ position: "absolute", top: "100%", left: 0, right: 0, backgroundColor: "#fff", border: "1px solid #ddd", borderTop: "none", borderRadius: "0 0 4px 4px", maxHeight: "200px", overflowY: "auto", zIndex: 10, boxShadow: "0 2px 8px rgba(0,0,0,0.1)" }}>
                                      {filteredSuggestions.map((url, idx) => (
                                        <div key={idx}
                                          onClick={() => { setDataSourcePath(url); const v = validateSharePointUrl(url); setIsValidUrl(v.isValid); setUrlValidationError(v.error || ""); setShowUrlSuggestions(false); }}
                                          style={{ padding: "8px 10px", borderBottom: idx < filteredSuggestions.length - 1 ? "1px solid #eee" : "none", cursor: "pointer", fontSize: "12px", fontFamily: "monospace", backgroundColor: "#f9f9f9" }}
                                          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = "#f0f0f0")}
                                          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = "#f9f9f9")}
                                        >{url}</div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                                {dataSourcePath && (
                                  <span style={{ fontSize: "11px", color: isValidUrl ? "#059669" : "#dc2626", fontWeight: 500 }}>
                                    {isValidUrl ? "✅ Valid SharePoint URL" : (urlValidationError || "❌ Invalid SharePoint URL")}
                                  </span>
                                )}
                                <span style={{ fontSize: "11px", color: "#64748b", lineHeight: "1.4" }}>
                                  SharePoint URL only. Format: https://companyname.sharepoint.com
                                </span>
                              </div>
                            )}

                            {!isCsvLoadscript && (
                              <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginBottom: "10px", padding: "8px 12px", background: "#f0fdf4", borderRadius: "6px", border: "1px solid #bbf7d0" }}>
                                <span style={{ fontSize: "11px", color: "#059669", fontWeight: 500 }}>ℹ️ Inline LoadScript - No URL required</span>
                              </div>
                            )}

                            <button
                              onClick={handleConvertToMQuery}
                              disabled={convertingToMquery || !selectedTable || (isCsvLoadscript && !isValidUrl)}
                              className="convert-btn"
                              style={{ opacity: (isCsvLoadscript && !isValidUrl) ? 0.5 : 1, cursor: (isCsvLoadscript && !isValidUrl) ? "not-allowed" : "pointer" }}
                            >
                              {convertingToMquery ? "⏳ Converting..." : "🔄 Convert to MQuery"}
                            </button>
                          </>
                        )}
                      </div>

                      <div className="display-section mquery-display">
                        <h4>Generated M Query</h4>
                        {!mquery && !convertingToMquery && <div className="empty-display">M Query will appear here after conversion</div>}
                        {mquery && (
                          <>
                            <div className="script-content"><pre>{mquery}</pre></div>
                            <div className="mquery-button-group">
                              <button
                                onClick={() => { const element = document.createElement("a"); const file = new Blob([mquery], { type: "text/plain" }); element.href = URL.createObjectURL(file); element.download = `${selectedTable || "query"}_mquery.m`; document.body.appendChild(element); element.click(); document.body.removeChild(element); }}
                                className="download-btn"
                              >
                                ⬇️ Download M Query
                              </button>
                              <button onClick={handlePublishMQuery} disabled={publishingMQuery} className="publish-pbi-btn">
                                {publishingMQuery ? (<><span className="publish-spinner" />Publishing…</>) : (
                                  <><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}><path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" /></svg>Publish MQuery to PowerBI</>
                                )}
                              </button>
                            </div>
                            {publishStatus !== "idle" && publishMessage && (
                              <div className={`publish-status-msg ${publishStatus === "success" ? "publish-success" : "publish-error"}`}>{publishMessage}</div>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* ── ER DIAGRAM TAB ──────────────────────────────────────────
                  Replaces the old SchemaModal popup.
                  Renders an inline Mermaid erDiagram inside the tab panel.
              ─────────────────────────────────────────────────────────────── */}
              {activeTab === "er" && (
                <div className="tab-content er-tab">
                  <div className="summary-div er-diagram-div">
                    <ERDiagramView
                      tables={tables}
                      relations={relations}
                      mainTable={mainTable}
                    />
                  </div>
                </div>
              )}

            </div>

            {/* DATA TABLE SECTION */}
            <div className="data-section">
              {tableLoading && <p>Loading data…</p>}
              {!tableLoading && (
                <>
                  {rows.length > 0 ? (
                    <>
                      <div className="data-controls">
                        <div className="length">
                          <label>
                            <select value={pageSize} onChange={(e) => setPageSize(parseInt(e.target.value, 10))}>
                              <option value={10}>10</option>
                              <option value={25}>25</option>
                              <option value={50}>50</option>
                              <option value={100}>100</option>
                            </select>
                            records per page
                          </label>
                        </div>
                        <div className="searchfilter">
                          <label className="lable-search">
                            Search:
                            <input type="search" value={tableQuery} onChange={(e) => setTableQuery(e.target.value)} placeholder="Search..." />
                          </label>
                          <button className="csv-btn" disabled={!rows.length} onClick={downloadCSV}>
                            <img src={Csvicon} alt="csv" className="btn-icon" />
                          </button>
                        </div>
                      </div>

                      <div className="table-wrapper">
                        <TableContainer component={Paper}>
                          <Table size="small">
                            <TableHead>
                              <TableRow>
                                {rows[0] && Object.keys(rows[0]).map((k) => (
                                  <TableCell key={k} sortDirection={orderBy === k ? order : false}>
                                    <TableSortLabel active={orderBy === k} direction={orderBy === k ? order : 'asc'} onClick={() => handleRequestSort(k)}>
                                      {k}
                                    </TableSortLabel>
                                  </TableCell>
                                ))}
                              </TableRow>
                            </TableHead>
                            <TableBody>
                              {visibleRows.map((r, i) => (
                                <TableRow key={i} hover>
                                  {Object.keys(rows[0]).map((k, j) => (
                                    <TableCell key={j}>{String(r[k] ?? "")}</TableCell>
                                  ))}
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </TableContainer>
                        <div className="table-footer">
                          {`Showing ${totalEntries ? startIndex + 1 : 0} to ${endIndex} of ${totalEntries} entries`}
                        </div>
                      </div>

                      <div className="pagination-bar">
                        <button className="page-btn" disabled={current === 1} onClick={() => setCurrentPage(current - 1)}>Previous</button>
                        {pageNumbers.map((p, idx) =>
                          typeof p === "number" ? (
                            <button key={idx} className={`page-btn ${p === current ? "active" : ""}`} onClick={() => setCurrentPage(p)}>{p}</button>
                          ) : (
                            <span key={idx} className="ellipsis">…</span>
                          )
                        )}
                        <button className="page-btn" disabled={current === totalPages} onClick={() => setCurrentPage(current + 1)}>Next</button>
                      </div>
                    </>
                  ) : (
                    <div className="no-data-placeholder" style={{ padding: 20 }}>
                      <p style={{ margin: 0, color: '#444' }}>No rows available for this table — preview not available.</p>
                      <p style={{ marginTop: 8, color: '#666' }}>You can still export this table; clicking <strong>Continue to Export</strong> will attempt to load the table data.</p>
                    </div>
                  )}

                  {/* "Continue to Export" is intentionally hidden on Summary tab to match UX requirement */}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}


// =============================================================================
// ER DIAGRAM VIEW (inline tab — uses Mermaid, no modal)
// Replaces the old SchemaModal popup.
// Relationship direction: fact/history/mainTable = many-side → dim/master = one-side
// =============================================================================

interface ERDiagramViewProps {
  tables: any[];
  relations: Record<string, string[]>;
  mainTable: string | null;
}

function ERDiagramView({ tables, relations, mainTable }: ERDiagramViewProps) {
  const ref = useRef<HTMLDivElement>(null);

  const factKeywords = ["fact", "history", "transaction", "detail", "sales", "order"];
  const dimKeywords = ["master", "dim", "lookup", "ref", "details"];

  const isFact = (name: string) => factKeywords.some(k => name.toLowerCase().includes(k));
  const isDim = (name: string) => dimKeywords.some(k => name.toLowerCase().includes(k));

  const buildErSource = (): string => {
    let src = "erDiagram\n";

    // Entity definitions — limit to 10 fields per table to avoid SVG overflow
    for (const t of tables) {
      const name = typeof t === "string" ? t : t?.name;
      if (!name) continue;
      const fields = (t.fields || t.columns || []).slice(0, 10);
      src += `  ${name} {\n`;
      for (const f of fields) {
        const fname = (typeof f === "string" ? f : f?.name || "")
          .replace(/[^a-zA-Z0-9_]/g, "_");
        if (fname) src += `    string ${fname}\n`;
      }
      src += "  }\n";
    }

    // Relationships with correct many-to-one direction
    const seen = new Set<string>();
    for (const [a, toList] of Object.entries(relations)) {
      for (const b of toList) {
        const key = [a, b].sort().join("|");
        if (seen.has(key)) continue;
        seen.add(key);

        const aIsDim = isDim(a);
        const bIsDim = isDim(b);
        const aIsFact = isFact(a) || a === mainTable;
        const bIsFact = isFact(b) || b === mainTable;

        let many: string, one: string;
        if (aIsDim && !bIsDim) { one = a; many = b; }
        else if (bIsDim && !aIsDim) { one = b; many = a; }
        else if (aIsFact && !bIsFact) { many = a; one = b; }
        else if (bIsFact && !aIsFact) { many = b; one = a; }
        else { many = a; one = b; }

        src += `  ${many} }o--|| ${one} : ""\n`;
      }
    }
    return src;
  };

  useEffect(() => {
    if (!ref.current) return;
    ref.current.innerHTML = '<p style="color:#888;font-size:13px;padding:16px">⏳ Rendering ER Diagram...</p>';

    import("mermaid").then(async (mod: any) => {
      const mermaid = mod.default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "base",
        fontFamily: "inherit",
        themeVariables: {
          fontSize: "12px",
          lineColor: "#73726c",
          textColor: "#3d3d3a",
          primaryColor: "#ede9fe",
          primaryBorderColor: "#7c3aed",
          primaryTextColor: "#3d3d3a",
        },
      });
      try {
        const { svg } = await mermaid.render("er-inline-" + Date.now(), buildErSource());
        if (ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        console.error("ER render error:", e);
        if (ref.current)
          ref.current.innerHTML = '<p style="color:#e11d48;font-size:13px;padding:16px">⚠️ Could not render diagram. Check console for details.</p>';
      }
    });
  }, [tables, relations]);

  const hasRelations = Object.keys(relations).some(k => (relations[k] || []).length > 0);

  if (!tables || tables.length === 0) {
    return (
      <div style={{ padding: 24, color: '#6b7280', fontSize: 14 }}>
        No tables loaded yet. Select a table from the left panel.
      </div>
    );
  }

  return (
    <div style={{ width: "100%", padding: "16px 0" }}>
      {/* Header */}
      <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: '#111827' }}>
            ER Diagram — from Qlik Sense
          </h3>
          <p style={{ margin: "3px 0 0", fontSize: 12, color: '#6b7280' }}>
            Entity relationships detected from the Qlik associative model
            {mainTable && <> · Main table: <strong style={{ color: '#7c3aed' }}>{mainTable}</strong></>}
          </p>
        </div>
        {!hasRelations && (
          <span style={{ fontSize: 11, color: '#f59e0b', background: '#fef3c7', padding: '4px 10px', borderRadius: 999, fontWeight: 600 }}>
            ⚠️ No relationships detected
          </span>
        )}
      </div>

      {/* Mermaid diagram */}
      <div
        ref={ref}
        style={{
          fontSize: 12,
          background: '#faf9ff',
          border: '1px solid #e5e7eb',
          borderRadius: 10,
          padding: 16,
          minHeight: 200,
          overflowX: 'auto',
        }}
      />

      {/* Footer note */}
      <p style={{ marginTop: 10, fontSize: 11, color: '#9ca3af' }}>
        Arrows show many-to-one direction. Qlik associative joins on shared field names.
      </p>
    </div>
  );
}


// =============================================================================
// SUMMARY REPORT COMPONENT
// =============================================================================
import React from "react";

interface SummaryReportProps {
  summary: any;
  rows: Row[];
  aiBullets?: string[];
  aiLoading?: boolean;
}

const PieChart: React.FC<{ data: Record<string, number>; title: string }> = ({ data, title }) => {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]).slice(0, 8);
  const total = entries.reduce((sum, [, val]) => sum + val, 0);
  const colors = ["#FF4E50", "#FC913A", "#F9D62E", "#1DC8CD", "#7B68EE", "#EE4B9E", "#00C851", "#2196F3"];

  const cx = 100, cy = 100, R = 95;

  let angle = -90;
  const slices = entries.map(([label, value], i) => {
    const pct = value / total;
    const sweep = pct * 360;
    const startRad = (angle - 90) * Math.PI / 180;
    const endRad = (angle + sweep - 90) * Math.PI / 180;
    const x1 = cx + R * Math.cos(startRad), y1 = cy + R * Math.sin(startRad);
    const x2 = cx + R * Math.cos(endRad), y2 = cy + R * Math.sin(endRad);
    const largeArc = sweep > 180 ? 1 : 0;
    const midRad = ((angle + sweep / 2) - 90) * Math.PI / 180;
    const lx = cx + R * 0.62 * Math.cos(midRad);
    const ly = cy + R * 0.62 * Math.sin(midRad);
    const tipX = cx + (R + 38) * Math.cos(midRad);
    const tipY = cy + (R + 38) * Math.sin(midRad);
    const pathData = `M${cx} ${cy} L${x1} ${y1} A${R} ${R} 0 ${largeArc} 1 ${x2} ${y2}Z`;
    angle += sweep;
    return { pathData, label, value, pct, lx, ly, tipX, tipY, midRad, color: colors[i % colors.length], delay: i * 0.06 };
  });

  return (
    <div className="pie-chart-container">
      <style>{`
        @keyframes slicePop {
          from { opacity: 0; transform: scale(0.5); }
          to   { opacity: 1; transform: scale(1); }
        }
        @keyframes pieFadeUp {
          from { opacity: 0; transform: translateY(5px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .pie-slice-g {
          cursor: pointer;
          transform-origin: 100px 100px;
          animation: slicePop 0.5s cubic-bezier(.34,1.56,.64,1) both;
        }
        .pie-slice-g path {
          transition: opacity 0.18s;
        }
        .pie-slice-g:hover path { opacity: 0.82; }
        .pie-callout-line {
          opacity: 0;
          transition: opacity 0.2s;
        }
        .pie-callout-tip {
          opacity: 0;
          transition: opacity 0.2s;
        }
        .pie-slice-g:hover .pie-callout-line { opacity: 1; }
        .pie-slice-g:hover .pie-callout-tip  { opacity: 1; }
        .pie-legend-animated {
          animation: pieFadeUp 0.4s ease both;
        }
      `}</style>

      <div className="pie-chart-content">
        <div className="pie-chart-left">
          {title && <h4>{title}</h4>}
          <svg viewBox="0 0 200 200" className="pie-svg" style={{ overflow: "visible" }}>
            {slices.map((slice, i) => (
              <g
                key={i}
                className="pie-slice-g"
                style={{ animationDelay: `${slice.delay}s` } as React.CSSProperties}
              >
                <path
                  d={slice.pathData}
                  fill={slice.color}
                  stroke="#fff"
                  strokeWidth="2.5"
                />
                {/* percentage label inside slice */}
                {slice.pct > 0.09 && (
                  <text
                    x={slice.lx} y={slice.ly}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fill="#fff"
                    fontSize={11}
                    fontWeight={700}
                    fontFamily="sans-serif"
                  >
                    {(slice.pct * 100).toFixed(0)}%
                  </text>
                )}
                {/* hover callout line */}
                {/* <line
                  className="pie-callout-line"
                  x1={slice.lx} y1={slice.ly}
                  x2={slice.tipX} y2={slice.tipY}
                  stroke={slice.color}
                  strokeWidth="1.5"
                /> */}
                {/* hover callout tip label */}
                {/* <text
                  className="pie-callout-tip"
                  x={slice.tipX + (Math.cos(slice.midRad) > 0 ? 5 : -5)}
                  y={slice.tipY}
                  textAnchor={Math.cos(slice.midRad) > 0 ? "start" : "end"}
                  dominantBaseline="central"
                  fill={slice.color}
                  fontSize={10}
                  fontWeight={600}
                  fontFamily="sans-serif"
                >
                  {(slice.pct * 100).toFixed(1)}%
                </text> */}
              </g>
            ))}
          </svg>
        </div>

        <div className="pie-legend">
          {slices.map((slice, i) => (
            <div
              key={i}
              className="legend-item pie-legend-animated"
              style={{ animationDelay: `${i * 0.07 + 0.3}s` } as React.CSSProperties}
            >
              <span className="legend-color" style={{ backgroundColor: slice.color }} />
              <span className="legend-text">
                {slice.label.substring(0, 20)}: {(slice.pct * 100).toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export const SummaryReport: React.FC<SummaryReportProps> = ({ summary, rows, aiBullets = [], aiLoading = false }) => {
  if (!summary && rows.length === 0) return null;
  const allCategoricalCounts: Record<string, number> = {};
  let topCityCount = 0;
  const cityCount: Record<string, number> = {};
  rows.forEach((row) => {
    Object.entries(row).forEach(([key, value]) => {
      if (key.toLowerCase().includes('id')) return;
      const num = Number(value);
      if (isNaN(num) || num === null || num === 0) {
        const strValue = String(value);
        const label = `${key}: ${strValue}`;
        allCategoricalCounts[label] = (allCategoricalCounts[label] || 0) + 1;
        if (key.toLowerCase().includes('city')) {
          cityCount[strValue] = (cityCount[strValue] || 0) + 1;
          if (cityCount[strValue] > topCityCount) topCityCount = cityCount[strValue];
        }
      }
    });
  });
  return (
    <div className="summary-report">
      <div className="analytics-container">
        {Object.keys(allCategoricalCounts).length > 0 && (
          <div className="chart-section">
            <PieChart data={allCategoricalCounts} title="" />
          </div>
        )}
        <div className="hf-summary-section">
          <h4>Executive Summary</h4>
          <ul className="hf-summary-content">
            {aiLoading ? (
              <li style={{ color: "#6366f1" }}>⏳ Generating AI insights...</li>
            ) : aiBullets.length > 0 ? (
              aiBullets.map((point, idx) => <li key={idx}>{point.replace(/^[-•]\s*/, "")}</li>)
            ) : null}
          </ul>
        </div>
      </div>
    </div>
  );
};
