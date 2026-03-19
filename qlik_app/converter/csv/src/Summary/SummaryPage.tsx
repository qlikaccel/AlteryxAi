
import "./SummaryPage.css";
import { useEffect, useState, useMemo, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchTables, fetchTableData, fetchTableDataSimple, exportTableAsCSV, fetchLoadScript, parseLoadScript } from "../api/qlikApi";
import Csvicon from "../assets/Csvicon.png";
import { useWizard } from "../context/WizardContext";
import SchemaModal from "../components/SchemaModal/SchemaModal";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
import Paper from "@mui/material/Paper";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TableSortLabel from "@mui/material/TableSortLabel";

type TableInfo = string | { name: string; [key: string]: any };
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
  const [summary, setSummary] = useState<any>(null);
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [totalRows, setTotalRows] = useState<number>(0); // server-reported total for selected table

  // Maximum rows we'll request from the backend in a single call (matches backend limit)
  // Raised to match backend cap so large tables (e.g. 12k+) are fully retrieved
  const SERVER_FETCH_MAX = 200000;

  // Relationship / star-schema helpers
  const [mainTable, setMainTable] = useState<string | null>(null); // detected hub table
  const [relations, setRelations] = useState<Record<string, string[]>>({}); // name -> related table names
  const [isSchemaModalOpen, setIsSchemaModalOpen] = useState(false);
  const [] = useState(false); // ER Diagram modal
  const [activeTab, setActiveTab] = useState<"summary" | "mquery" | "er">("summary");

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
  const [dataSourcePath, setDataSourcePath] = useState<string>("https://sorimtechnologies.sharepoint.com");

  // LoadScript type detection and URL validation
  const [isCsvLoadscript, setIsCsvLoadscript] = useState<boolean>(false);
  const [isValidUrl, setIsValidUrl] = useState<boolean>(true);
  const [urlValidationError, setUrlValidationError] = useState<string>("");

  // AI Executive Summary
  const [aiSummaryBullets, setAiSummaryBullets] = useState<string[]>([]);
  const [aiSummaryLoading, setAiSummaryLoading] = useState(false);
  const [_aiSummaryError, setAiSummaryError] = useState<string>("");

  // URL autocomplete history
  // (removed - not needed for testing)

  // Helper: Detect if loadscript is CSV-based or inline
  const detectCsvLoadscript = (script: string): boolean => {
    if (!script) return false;
    const lower = script.toLowerCase();
    // If it has INLINE keyword, it's definitely inline (not CSV)
    if (/inline\s*\[/.test(lower)) return false;
    // CSV/QVD-based if it has FROM with file paths/protocols
    return /from\s+[\[\']?(?:lib:\/\/|file:\/\/|https?:\/\/|\/|[a-z]:[\\\/]).*?(?:\.csv|\.qvd|\.xlsx?|\.txt|\])/i.test(lower);
  };

  // Helper: Validate ONLY SharePoint URLs - STRICT validation
  const validateSharePointUrl = (url: string): { isValid: boolean; error?: string } => {
    if (!url || url.trim().length === 0) {
      return { isValid: false, error: "URL cannot be empty" };
    }

    const trimmed = url.trim();

    if (!trimmed.toLowerCase().startsWith("https://")) {
      return { isValid: false, error: "❌ Must start with https://" };
    }

    if (trimmed.toLowerCase().startsWith("http://") && !trimmed.toLowerCase().startsWith("https://")) {
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

    // Updated regex to capture company name before .sharepoint.com
    const sharepointMatch = trimmed.match(/https:\/\/([a-z0-9\-]+)\.sharepoint\.com/i);
    if (!sharepointMatch || !sharepointMatch[1]) {
      return { isValid: false, error: "❌ Missing company name - Should be: https://COMPANYNAME.sharepoint.com" };
    }

    const companyName = sharepointMatch[1];

    if (companyName.length === 0) {
      return { isValid: false, error: "❌ Invalid company name - Should be: https://COMPANYNAME.sharepoint.com" };
    }

    return { isValid: true };
  };


  // Helper: build relation graph from `tables` (uses fields when available)
  const buildRelations = (tableList: TableInfo[]) => {
    const map: Record<string, Set<string>> = {};

    const normalizeFields = (t: TableInfo): Set<string> => {
      const out = new Set<string>();
      if (!t || typeof t === "string") return out;

      const raw = (t as any).fields || (t as any).columns || [];
      for (const f of (raw || [])) {
        if (!f) continue;
        if (typeof f === "string") {
          out.add(f.toLowerCase());
          continue;
        }

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

    // Two tables are related if they share at least one field name (case-insensitive)
    for (let i = 0; i < names.length; i++) {
      for (let j = i + 1; j < names.length; j++) {
        const a = names[i];
        const b = names[j];
        const setA = fieldSets[a] || new Set();
        const setB = fieldSets[b] || new Set();
        let shared = 0;
        for (const f of setA) {
          if (setB.has(f)) {
            shared++;
            break;
          }
        }
        if (shared > 0) {
          map[a].add(b);
          map[b].add(a);
        }
      }
    }

    const out: Record<string, string[]> = {};
    for (const k of Object.keys(map)) {
      out[k] = Array.from(map[k]);
    }
    return out;
  };

  // Helper: check whether two tables share at least one field (case-insensitive)
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
    for (const f of fieldsB) {
      if (setA.has(f)) return true;
    }
    return false;
  };


  // Track page load start time
  useEffect(() => {
    pageStartTimeRef.current = Date.now();
  }, []);

  // Save activeTab to sessionStorage so Stepper can read it and hide Export step if needed
  useEffect(() => {
    sessionStorage.setItem("summaryActiveTab", activeTab);
  }, [activeTab]);

  // Load URL history from localStorage on component mount
  // (removed - not needed for testing)

  // Validate hardcoded SharePoint URL on component mount
  useEffect(() => {
    const validation = validateSharePointUrl(dataSourcePath);
    setIsValidUrl(validation.isValid);
    setUrlValidationError(validation.error || "");
  }, []);

  // Data-table controls
  const [tableQuery, setTableQuery] = useState<string>("");
  const [pageSize, setPageSize] = useState<number>(10);
  const [currentPage, setCurrentPage] = useState<number>(1);
  // Search for table list on the left panel
  const [tableListQuery, setTableListQuery] = useState<string>("");

  // Derived pagination lists with search + sorting
  const [orderBy, setOrderBy] = useState<string>("");
  const [order, setOrder] = useState<"asc" | "desc">("asc");

  const processedRows = useMemo(() => {
    let out = rows || [];

    // search filter
    if (tableQuery.trim()) {
      const q = tableQuery.toLowerCase();
      out = out.filter((r) =>
        Object.values(r).some((v) =>
          String(v ?? "").toLowerCase().includes(q)
        )
      );
    }

    // sorting
    if (orderBy) {
      out = out.slice().sort((a: any, b: any) => {
        const va = a[orderBy];
        const vb = b[orderBy];
        if (va === vb) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === "number" && typeof vb === "number") {
          return order === "asc" ? va - vb : vb - va;
        }
        const sa = String(va).toLowerCase();
        const sb = String(vb).toLowerCase();
        if (sa < sb) return order === "asc" ? -1 : 1;
        if (sa > sb) return order === "asc" ? 1 : -1;
        return 0;
      });
    }

    return out;
  }, [rows, tableQuery, orderBy, order]);

  // For server-side paging use `totalRows` reported by backend; fall back to local data length
  const totalEntries = totalRows && totalRows > 0 ? totalRows : processedRows.length;
  const totalPages = Math.max(1, Math.ceil(totalEntries / pageSize));
  const current = Math.min(currentPage, totalPages);
  const startIndex = totalEntries ? (current - 1) * pageSize : 0;
  const endIndex = Math.min(startIndex + pageSize, totalEntries);
  const visibleRows = processedRows;

  const handleRequestSort = (property: string) => {
    if (orderBy === property) {
      setOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setOrderBy(property);
      setOrder("asc");
    }
  };

  const pageNumbers = useMemo<(number | string)[]>(() => {
    const nums: (number | string)[] = [];
    const max = totalPages;
    const cur = current;
    if (max <= 7) {
      for (let i = 1; i <= max; i++) nums.push(i);
    } else {
      nums.push(1);
      if (cur > 3) nums.push("...");
      const start = Math.max(2, cur - 1);
      const end = Math.min(max - 1, cur + 1);
      for (let i = start; i <= end; i++) nums.push(i);
      if (cur < max - 2) nums.push("...");
      nums.push(max);
    }
    return nums;
  }, [totalPages, current]);

  useEffect(() => {
    setCurrentPage(1);
  }, [pageSize, tableQuery]);

  // Server-side paging: fetch the selected page when page/size/table changes
  useEffect(() => {
    if (!selectedTable) return;

    const loadPage = async () => {
      setTableLoading(true);
      try {
        const offset = (currentPage - 1) * pageSize;
        const data = await fetchTableData(appId, selectedTable, pageSize, offset);
        setRows(data || []);
      } catch (e) {
        console.error("❌ Failed to load page:", e);
      } finally {
        setTableLoading(false);
      }
    };

    loadPage();
  }, [currentPage, pageSize, selectedTable, appId]);

  // Filter the left-side table list when the user types in the table search box
  useEffect(() => {
    if (!tableListQuery) {
      setFilteredTables((tables || []).slice());
      return;
    }

    const q = tableListQuery.toLowerCase();
    const filtered = (tables || [])
      .filter((t) => {
        const name = typeof t === "string" ? t : t?.name || "";
        return String(name).toLowerCase().includes(q);
      })
      .slice();
    setFilteredTables(filtered);
  }, [tableListQuery, tables]);

  // 1 → GET APP ID FROM NAVIGATION STATE
  useEffect(() => {
    const state = location.state as any;
    const passedAppId = state?.appId || sessionStorage.getItem("appSelected");

    if (!passedAppId) {
      alert("No app selected. Please go back and select an app.");
      navigate("/apps");
      return;
    }

    setAppId(passedAppId);
  }, [location, navigate]);

  // 2 → LOAD TABLE LIST
  const { stopTimer, startTimer, getLastElapsed } = useWizard();

  useEffect(() => {
    if (!appId) return;

    if (sessionStorage.getItem("lastTimerTarget") !== "/summary") {
      startTimer?.("/summary");
    }

    fetchTables(appId)
      .then((data) => {
        const cleaned = (data || []).filter((t: any) => {
          const name = typeof t === 'string' ? t : t?.name || '';
          if (!name) return false;
          return !name.toLowerCase().startsWith('@syn');
        });

        const getTimestamp = (t: any) => {
          if (!t || typeof t === 'string') return 0;
          const candidates = ['added_timestamp','created','createdAt','created_at','createdDate','modifiedDate','lastModifiedDate','lastReloadTime','lastReload'];
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
          const tx = getTimestamp(x);
          const ty = getTimestamp(y);
          if (tx !== ty) return ty - tx;

          const xi = (typeof x === 'string') ? false : !!x.is_new;
          const yi = (typeof y === 'string') ? false : !!y.is_new;
          if (xi && !yi) return -1;
          if (!xi && yi) return 1;

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
          if (/\b(master|fact|main)\b/i.test(n) && degreeOf(n) > 0) {
            detectedMain = n;
            break;
          }
        }

        if (!detectedMain) {
          let bestName: string | null = null;
          let bestDeg = -1;
          for (const t of sorted) {
            const n = nameOf(t);
            const deg = degreeOf(n);
            if (deg > bestDeg) {
              bestDeg = deg;
              bestName = n;
            } else if (deg === bestDeg && deg > 0) {
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
          if (degree > 0 || isExplicit) {
            setMainTable(detectedMain);
          } else {
            setMainTable(null);
          }
        } else {
          setMainTable(null);
        }

        setFilteredTables(sorted);
        console.log("All tables fetched (sorted by recency, @syn filtered). Detected main:", detectedMain, "relations:", rel);

        if (detectedMain) {
          loadData(detectedMain);
        } else if (sorted && sorted.length > 0) {
          const firstTableName = typeof sorted[0] === "string" ? sorted[0] : sorted[0]?.name;
          if (firstTableName) loadData(firstTableName);
        }
      })
      .catch(() => {})
      .finally(() => {
        setLoading(false);
      });
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
      if (total > SERVER_FETCH_MAX) {
        console.warn(`Table ${tableName} contains ${total} rows — UI will page on demand (server capped at ${SERVER_FETCH_MAX})`);
      }

      const data = await fetchTableData(appId, tableName, firstPageLimit, 0);
      setRows(data || []);
      setCurrentPage(1);

      try {
        sessionStorage.setItem("selectedTable", tableName);
        sessionStorage.setItem("selectedRows", JSON.stringify(data || []));
      } catch (e) {
        // ignore storage errors
      }

      const { generateSummaryFromData } = await import("../api/qlikApi");
      const summary = generateSummaryFromData(data, tableName);
      setSummary(summary);

      fetchAiSummary(data, tableName);

      try {
        console.log("📍 Auto-fetching LoadScript for table:", tableName);
        const scriptResult = await fetchLoadScript(appId, tableName);

        if (scriptResult.status === "success" || scriptResult.status === "partial_success") {
          const script = scriptResult.loadscript || "";
          setLoadscript(script);

          const isCsv = detectCsvLoadscript(script);
          setIsCsvLoadscript(isCsv);
          if (isCsv) {
            const validation = validateSharePointUrl(dataSourcePath);
            setIsValidUrl(validation.isValid);
          }

          if (script) {
            try {
              const parseResult = await parseLoadScript(script);
              if (parseResult.status === "success") {
                setParsedScript(parseResult);
                console.log("✅ LoadScript auto-parsed for table:", tableName);
              }
            } catch (parseError) {
              console.warn("Auto-parse failed, keeping raw loadscript", parseError);
            }
          }

          console.log("✅ LoadScript auto-loaded for table:", tableName);
        }
      } catch (scriptError) {
        console.warn("⚠️ Could not auto-fetch LoadScript:", scriptError);
      }

    } catch (e) {
      console.error("❌ Error loading table data:", e);

      const errorMessage = e instanceof Error ? e.message : String(e);
      alert(
        `Failed to load table "${tableName}".\n\n` +
        `Error: ${errorMessage}\n\n` +
        `Suggestions:\n` +
        `1. Verify the table name is correct\n` +
        `2. Ensure the app has been reloaded with the latest data in QlikCloud\n` +
        `3. Check the backend is running (http://127.0.0.1:8000)`
      );
    } finally {
      setTableLoading(false);

      const tableElapsed = stopTimer?.(`/summary/data/${tableName}`);
      if (tableElapsed) {
        console.debug(`Table ${tableName} load time:`, tableElapsed);
        setPageLoadTime(tableElapsed);
      } else {
        const navElapsed = getLastElapsed?.("/summary");
        if (navElapsed) {
          setPageLoadTime(navElapsed);
        } else if (pageStartTimeRef.current) {
          const totalTime = Date.now() - pageStartTimeRef.current;
          setPageLoadTime(formatElapsed(totalTime));
        }
      }
    }
  };

  // CSV DOWNLOAD
  const downloadCSV = async () => {
    if (!rows.length && !totalRows) {
      alert("No data");
      return;
    }

    if (totalRows && totalRows > rows.length) {
      try {
        const csv = await exportTableAsCSV(appId, selectedTable);
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${selectedTable || "data"}.csv`;
        a.click();
        window.URL.revokeObjectURL(url);
        return;
      } catch (e) {
        console.error("Export failed, falling back to page CSV:", e);
      }
    }

    const headers = Object.keys(rows[0] || {});
    const csv = [
      headers.join(","),
      ...rows.map((r) => headers.map((h) => `"${r[h] ?? ""}"`).join(",")),
    ].join("\n");

    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = window.URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = `${selectedTable || "data"}.csv`;
    a.click();
    window.URL.revokeObjectURL(url);
  };

  // ✅ CONVERT TO MQUERY from parsed loadscript
  const handleConvertToMQuery = async () => {
    if (!loadscript) {
      setLoadscriptError("No LoadScript available");
      return;
    }

    if (!selectedTable) {
      setLoadscriptError("Please select a table first");
      return;
    }

    try {
      setConvertingToMquery(true);
      setLoadscriptError("");
      setMquery("");

      console.log("📍 Converting LoadScript to M Query for table:", selectedTable);

      let scriptToConvert = parsedScript;
      if (!scriptToConvert) {
        console.log("Parsing LoadScript first...");
        const parseResult = await parseLoadScript(loadscript);
        if (parseResult.status === "success") {
          scriptToConvert = parseResult;
          setParsedScript(parseResult);
        } else {
          throw new Error("Failed to parse LoadScript");
        }
      }

      const apiBase = window.location.hostname.includes('localhost') || window.location.hostname === '127.0.0.1'
        ? 'http://127.0.0.1:8000'
        : 'https://qliksense-stuv.onrender.com';
      const convertResponse = await fetch(`${apiBase}/api/migration/convert-to-mquery`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          parsed_script_json: JSON.stringify(scriptToConvert),
          table_name: "",
          base_path: dataSourcePath.trim() || "",
        }),
      });
      if (!convertResponse.ok) {
        throw new Error(`Failed to convert to M Query: ${convertResponse.status}`);
      }
      const convertResult = await convertResponse.json();

      if (convertResult.status !== "success" && convertResult.status !== "partial_success") {
        throw new Error(convertResult.message || "Failed to convert to M Query");
      }

      const finalMQuery = convertResult.m_query || "";
      setMquery(finalMQuery);
      console.log(
        "✅ Converted to M Query successfully —",
        convertResult.statistics?.total_tables_converted ?? "?",
        "table(s)"
      );
    } catch (error: any) {
      const errorMsg = error.message || "Failed to convert to M Query";
      setLoadscriptError(errorMsg);
      console.error("❌ Error converting to M Query:", error);
    } finally {
      setConvertingToMquery(false);
    }
  };

  const fetchAiSummary = async (tableRows: any[], tableName: string) => {
    if (!tableRows || tableRows.length === 0) return;
    setAiSummaryLoading(true);
    setAiSummaryError("");
    setAiSummaryBullets([]);
    try {
      const apiBase = window.location.hostname.includes('localhost') || window.location.hostname === '127.0.0.1'
        ? 'http://127.0.0.1:8000'
        : 'https://qliksense-stuv.onrender.com';
      const res = await fetch(`${apiBase}/chat/summary-hf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ table_name: tableName, data: tableRows.slice(0, 500) }),
      });
      const rawText = await res.text();
      let result: any = {};
      try { result = rawText ? JSON.parse(rawText) : {}; } catch {
        setAiSummaryError(`Server error: ${rawText.slice(0, 200) || "Empty response"}`);
        return;
      }
      if (!res.ok || !result.success) {
        setAiSummaryError(result.detail || result.error || `HTTP ${res.status}`);
        return;
      }
      setAiSummaryBullets(result.bullets || []);
    } catch (e: any) {
      setAiSummaryError(e.message || "Error calling AI summary");
    } finally {
      setAiSummaryLoading(false);
    }
  };

  const handlePublishMQuery = async () => {
    if (!mquery && !loadscript) {
      setPublishStatus("error");
      setPublishMessage("No M Query available. Click 'Convert to MQuery' first.");
      return;
    }

    try {
      setPublishingMQuery(true);

      let masterRowCount = totalRows || (rows?.length || 0);
      let relatedTables: string[] = [];
      let relatedTablesCount = 1;

      if (selectedTable && relations[selectedTable]) {
        relatedTables = relations[selectedTable].slice();
        relatedTablesCount = 1 + relatedTables.length;
      } else if (mainTable && relations[mainTable]) {
        relatedTables = relations[mainTable].slice();
        relatedTablesCount = 1 + relatedTables.length;
      }

      let totalRowsAllTables = masterRowCount;
      for (const relTable of relatedTables) {
        try {
          const relMeta = await fetchTableDataSimple(appId, relTable).catch(() => null);
          const relRowCount = relMeta?.row_count || relMeta?.rowCount || relMeta?.no_of_rows || 0;
          totalRowsAllTables += relRowCount;
          console.log(`📊 Related table "${relTable}": ${relRowCount} rows`);
        } catch (e) {
          console.warn(`Failed to fetch row count for related table: ${relTable}`, e);
        }
      }

      console.log(`📊 Master table "${selectedTable}": ${masterRowCount} rows`);
      console.log(`📊 Total rows (master + all related): ${totalRowsAllTables} rows`);

      if (isCsvLoadscript && isValidUrl && dataSourcePath.trim()) {
        // URL validation passed
      }

      sessionStorage.setItem("publishMethod", "M_QUERY");
      sessionStorage.setItem("exportComplete", "true");

      navigate("/publish", {
        state: {
          appId: appId,
          selectedTable: selectedTable,
          publishMethod: "M_QUERY",
          showWorkflow: true,
          tableCount: relatedTablesCount,
          totalRows: totalRowsAllTables,
          rowCount: totalRowsAllTables,
          columns: rows && rows.length > 0 ? Object.keys(rows[0]) : [],
          mqueryData: {
            dataset_name: selectedTable || "Qlik_Dataset",
            combined_mquery: mquery || "",
            raw_script: mquery ? "" : loadscript,
            data_source_path: dataSourcePath?.trim() || "",
          },
        },
      });
    } catch (error: any) {
      setPublishStatus("error");
      setPublishMessage(`Failed to prepare publishing: ${error.message || "Unknown error"}`);
      setPublishingMQuery(false);
    }
  };

  // Compute master table per-prefix
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
      if (fordExplicit) {
        map.set(prefix, fordExplicit.name);
        return;
      }

      const explicit = candidates.find((c: any) => /fact|master|main/i.test(c.name));
      if (explicit) {
        map.set(prefix, explicit.name);
        return;
      }

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
    if (mainTable && relations && relations[mainTable]) {
      return relations[mainTable].includes(name);
    }

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
        const aRel = relSet.has(an);
        const bRel = relSet.has(bn);
        if (aRel && !bRel) return -1;
        if (!aRel && bRel) return 1;

        const aMaster = isMasterTable(an);
        const bMaster = isMasterTable(bn);
        if (aMaster && !bMaster) return -1;
        if (!aMaster && bMaster) return 1;

        return an.localeCompare(bn);
      });
      return arr;
    }

    arr.sort((a, b) => {
      const an = typeof a === 'string' ? a : a?.name || '';
      const bn = typeof b === 'string' ? b : b?.name || '';
      const aMaster = isMasterTable(an);
      const bMaster = isMasterTable(bn);
      if (aMaster && !bMaster) return -1;
      if (!aMaster && bMaster) return 1;
      return an.localeCompare(bn);
    });
    return arr;
  }, [filteredTables, masterMap, mainTable, relations]);

  const isSelectionMaster = !!(selectedTable && isMasterTable(selectedTable));
  const isExportAllowed = Boolean(selectedTable && (isSelectionMaster || !isRelatedTable(selectedTable)));

  // Helper: prepare export payload and navigate to /publish
  const prepareAndNavigateToExport = async (tableToExport?: string) => {
    try {
      stopTimer?.("/summary");
      sessionStorage.setItem("summaryComplete", "true");
      startTimer?.("/publish");

      const sel = tableToExport || selectedTable || (sessionStorage.getItem("selectedTable") || "");
      if (!sel) {
        alert("No table selected for export.");
        return;
      }

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
          setSelectedTable(sel);
          setRows(masterRows);
          const { generateSummaryFromData } = await import("../api/qlikApi");
          setSummary(generateSummaryFromData(masterRows, sel));
        } catch (e) {
          console.warn("Failed to load table prior to publish:", e);
          alert("Failed to load table data for publish. See console for details.");
          setTableLoading(false);
          return;
        } finally {
          setTableLoading(false);
        }
      } else {
        try {
          const meta = await fetchTableDataSimple(appId, sel).catch(() => null);
          masterRowCount = meta?.row_count || meta?.rowCount || meta?.no_of_rows || (masterRows || []).length;
        } catch (e) {
          masterRowCount = (masterRows || []).length;
        }
      }

      const prefix = sel && sel.includes("_") ? sel.split("_")[0] : null;
      const candidateNames = (tables || []).map((t) => (typeof t === "string" ? t : t?.name)).filter(Boolean) as string[];
      let related: string[] = [];
      if (mainTable && sel === mainTable && relations && relations[mainTable]) {
        related = relations[mainTable].slice();
      } else if (prefix) {
        related = candidateNames
          .filter(n => n.startsWith(prefix + "_") && n !== sel)
          .filter(n => shareFields(sel, n));
      }

      const headers = masterRows.length > 0 ? Object.keys(masterRows[0]) : [];
      const csv = [
        headers.join(","),
        ...masterRows.map((r: any) => headers.map((h) => `"${r[h] ?? ""}"`).join(",")),
      ].join("\n");

      const cols = headers;
      const daxLines = [] as string[];
      daxLines.push(`-- DAX export skeleton for table: ${sel}`);
      daxLines.push(`-- Columns:`);
      cols.forEach((c) => daxLines.push(`-- ${c}`));
      daxLines.push(`\n-- Sample measure`);
      daxLines.push(`[${sel} Count] = COUNTROWS('${sel}')`);
      const daxContent = daxLines.join("\n");

      if (isCsvLoadscript && isValidUrl && dataSourcePath.trim()) {
        // URL validation passed
      }

      if (!related || related.length === 0) {
        navigate("/export", {
          state: {
            appId,
            appName: location.state?.appName || sessionStorage.getItem("appName") || appId,
            selectedTable: sel,
            rows: masterRows || [],
            totalRows: masterRowCount,
            totalTablesCount: 1,
            exportOptions: { combined: true, separate: false },
            csvPayloads: { migration_csv: csv },
            daxPayloads: { migration_dax: daxContent },
          },
        });
        return;
      }

      setTableLoading(true);
      const selectedData: any[] = [];
      const csvPayloads: Record<string, string> = { migration_csv_0: csv };
      const daxPayloads: Record<string, string> = { migration_dax: daxContent };

      selectedData.push({
        name: sel,
        data: { name: sel, rows: masterRows || [], summary },
        actualRowCount: masterRowCount
      });

      for (let idx = 0; idx < related.length; idx++) {
        const relName = related[idx];
        try {
          const relMeta = await fetchTableDataSimple(appId, relName).catch(() => null);
          const relTotal = relMeta?.row_count || relMeta?.rowCount || relMeta?.no_of_rows || 0;
          const relLimit = relTotal > 0 ? Math.min(relTotal, SERVER_FETCH_MAX) : SERVER_FETCH_MAX;
          const relRows = await fetchTableData(appId, relName, relLimit);
          const { generateSummaryFromData } = await import("../api/qlikApi");
          const relSummary = generateSummaryFromData(relRows, relName);

          selectedData.push({
            name: relName,
            data: { name: relName, rows: relRows, summary: relSummary },
            actualRowCount: relTotal
          });

          const relHeaders = relRows.length > 0 ? Object.keys(relRows[0]) : [];
          const relCsv = [
            relHeaders.join(","),
            ...relRows.map((r: any) => relHeaders.map((h) => `"${r[h] ?? ""}"`).join(",")),
          ].join("\n");
          csvPayloads[`migration_csv_${idx + 1}`] = relCsv;
        } catch (e) {
          console.warn("Failed to load related table:", relName, e);
        }
      }

      setTableLoading(false);

      const totalAllRows = selectedData.reduce((sum, table) => sum + (table.actualRowCount || table.data?.rows?.length || 0), 0);
      const totalTablesCount = selectedData.length;

      navigate("/export", {
        state: {
          appId,
          appName: location.state?.appName || sessionStorage.getItem("appName") || appId,
          selectedTables: selectedData,
          totalRows: totalAllRows,
          totalTablesCount: totalTablesCount,
          exportOptions: { combined: true, separate: true },
          csvPayloads,
          daxPayloads,
        },
      });
    } catch (err) {
      setTableLoading(false);
      console.error(err);
      alert("Failed to prepare related tables for export. See console for details.");
    }
  };


  if (loading) {
    return (
      <LoadingOverlay
        isVisible={loading}
        message="Loading tables from QlikSense..."
      />
    );
  }

  return (
    <div className="summary-layout">
      {/* LEFT – TABLE NAMES */}
      <div className="left-panel">
        <div className="panel-header">
          <h3 className="title">Tables {`(${tables.length})`}</h3>
          {mainTable && (
            <div style={{ marginTop: 6, fontSize: 12, color: '#444' }}>
              Detected main table: <strong style={{ color: '#0b3a66' }}>{mainTable}</strong>
            </div>
          )}
        </div>

        {/* Table list search */}
        <div className="table-search">
          <input
            type="search"
            placeholder="Search tables..."
            value={tableListQuery}
            onChange={(e) => setTableListQuery(e.target.value)}
            className="table-search-input"
          />
        </div>

        {tables.length === 0 && (
          <p className="no-tables">No tables found</p>
        )}

        {sortedFilteredTables.map((t, i) => {
          const tableName = typeof t === "string" ? t : t?.name;
          const isNew = typeof t === "string" ? false : t?.is_new;
          if (!tableName) return null;

          const master = isMasterTable(tableName);
          const related = isRelatedTable(tableName);
          const cls = `${tableName === selectedTable ? "table-item active" : "table-item"}${master ? " master-row" : ""}${related && !master ? " related-row" : ""}`;

          return (
            <div
              key={i}
              className={cls}
              onClick={() => loadData(tableName)}
              title={master ? "Master table — click to export master + its related tables" : related ? "Related table — preview only — export disabled (select master to export)" : "Click to preview table"}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{tableName}</span>
              </span>

              {/* Inline export button for standalone or master tables (hidden for related-only tables) */}
              {!related && (
                <button
                  className="inline-export"
                  title={master ? "Export master + related tables" : "Export this standalone table"}
                  onClick={(e) => { e.stopPropagation(); prepareAndNavigateToExport(tableName); }}
                >
                  {/* <img src={exportImg} alt="Export" style={{ width: 16 }} /> */}
                </button>
              )}

              {isNew && <span className="new-badge">NEW</span>}
            </div>
          );
        })}
      </div>

      {/* RIGHT – SUMMARY + DATA */}
      <div className="right-panel">
        {!selectedTable && (
          <div className="empty">
            <p>👈 Select a table on the left to view its data</p>
          </div>
        )}

        {selectedTable && (
          <>
            {/* HEADER WITH TITLE + TABS ON LEFT, TIME ON RIGHT */}
            <div className="header">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%", gap: "20px" }}>
                {/* LEFT: Title + Master Badge + Tabs */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
                  <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: '#1f2937', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {selectedTable}
                    {isSelectionMaster && <span className="master-indicator" style={{ marginLeft: '0px' }}>master</span>}
                  </h2>

                  {/* MODERN TAB BAR */}
                  <div style={{
                    display: "flex",
                    gap: "14px",
                    borderBottom: "none",
                    paddingBottom: "0px",
                    marginLeft: '12px',
                    borderRadius: '6px',
                    padding: '4px',
                  }}>
                    <button
                      onClick={() => setActiveTab("summary")}
                      style={{
                        padding: '8px 14px',
                        fontSize: '13px',
                        fontWeight: activeTab === "summary" ? 600 : 500,
                        color: activeTab === "summary" ? '#fff' : '#6b7280',
                        backgroundColor: activeTab === "summary" ? '#667eea' : 'transparent',
                        border: 'none',
                        borderBottom: '3px solid #938d8d',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        transition: 'all 0.3s ease',
                      }}
                      onMouseEnter={(e) => {
                        if (activeTab !== "summary") {
                          e.currentTarget.style.backgroundColor = 'rgba(102, 126, 234, 0.1)';
                          e.currentTarget.style.color = '#4f46e5';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (activeTab !== "summary") {
                          e.currentTarget.style.backgroundColor = 'transparent';
                          e.currentTarget.style.color = '#6b7280';
                        }
                      }}
                    >
                      📊 Summary
                    </button>

                    <button
                      onClick={() => setActiveTab("mquery")}
                      style={{
                        padding: '8px 14px',
                        fontSize: '13px',
                        fontWeight: activeTab === "mquery" ? 600 : 500,
                        color: activeTab === "mquery" ? '#fff' : '#6b7280',
                        backgroundColor: activeTab === "mquery" ? '#059669' : 'transparent',
                        border: 'none',
                        borderRadius: '4px',
                        borderBottom: '3px solid #938d8d',
                        cursor: 'pointer',
                        transition: 'all 0.3s ease',
                      }}
                      onMouseEnter={(e) => {
                        if (activeTab !== "mquery") {
                          e.currentTarget.style.backgroundColor = 'rgba(5, 150, 105, 0.1)';
                          e.currentTarget.style.color = '#069669';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (activeTab !== "mquery") {
                          e.currentTarget.style.backgroundColor = 'transparent';
                          e.currentTarget.style.color = '#6b7280';
                        }
                      }}
                    >
                      📋 Query
                    </button>

                    {/* Schema Button → opens SchemaModal */}
                    {/* <button
                      onClick={() => setIsSchemaModalOpen(true)}
                      style={{
                        padding: '8px 14px',
                        fontSize: '13px',
                        fontWeight: 500,
                        color: '#6b7280',
                        backgroundColor: 'transparent',
                        border: 'none',
                        borderRadius: '4px',
                        borderBottom: '3px solid #938d8d',
                        cursor: 'pointer',
                        transition: 'all 0.3s ease',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.backgroundColor = 'rgba(245, 158, 11, 0.1)';
                        e.currentTarget.style.color = '#f59e0b';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.backgroundColor = 'transparent';
                        e.currentTarget.style.color = '#6b7280';
                      }}
                    >
                      🔷 Schema
                    </button> */}

                    {/* ER Diagram Tab → displays as tab content */}
                    <button
                      onClick={() => setActiveTab("er")}
                      style={{
                        padding: '8px 14px',
                        fontSize: '13px',
                        fontWeight: activeTab === "er" ? 600 : 500,
                        color: activeTab === "er" ? '#fff' : '#6b7280',
                        backgroundColor: activeTab === "er" ? '#7c3aed' : 'transparent',
                        border: 'none',
                        borderRadius: '4px',
                        borderBottom: '3px solid #938d8d',
                        cursor: 'pointer',
                        transition: 'all 0.3s ease',
                      }}
                      onMouseEnter={(e) => {
                        if (activeTab !== "er") {
                          e.currentTarget.style.backgroundColor = 'rgba(124, 58, 237, 0.1)';
                          e.currentTarget.style.color = '#7c3aed';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (activeTab !== "er") {
                          e.currentTarget.style.backgroundColor = 'transparent';
                          e.currentTarget.style.color = '#6b7280';
                        }
                      }}
                    >
                      🔷 ER Diagram
                    </button>
                  </div>
                </div>

                {/* RIGHT: Analysis Time only */}
                {pageLoadTime && (
                  <div className="timer-badge">Analysis Time: {pageLoadTime}</div>
                )}
              </div>
            </div>

            {/* ===== TAB CONTENT ===== */}
            <div className="tabs-content">
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

                    {loadscriptError && (
                      <div className="error-message">
                        ⚠️ {loadscriptError}
                      </div>
                    )}

                    <div className="loadscript-displays">
                      {/* First Display: LoadScript */}
                      <div className="display-section loadscript-display">
                        <h4>Qlik LoadScript</h4>
                        {!loadscript && (
                          <div className="empty-display">Select a table to auto-load its LoadScript</div>
                        )}
                        {loadscript && (
                          <>
                            <div className="script-content">
                              <pre>{loadscript}</pre>
                            </div>
                            {/* DataSourcePath input — for CSV/QVD file sources only */}
                            {isCsvLoadscript && (
                            <div style={{
                              display: "flex",
                              flexDirection: "column",
                              gap: "4px",
                              marginBottom: "10px",
                              padding: "10px 12px",
                              background: isValidUrl ? "#ecfdf5" : "#fef2f2",
                              borderRadius: "6px",
                              border: isValidUrl ? "1px solid #86efac" : "1px solid #fecaca",
                              position: "relative",
                            }}>
                              <label style={{ fontSize: "12px", fontWeight: 600, color: "#0369a1" }}>
                                📁 Data Source Path <span style={{ fontWeight: 400, color: "#64748b" }}>(required for CSV)</span>
                              </label>
                              <div style={{ position: "relative" }}>
                                <input
                                  type="text"
                                  // value={dataSourcePath}
                                  value="https://sorimtechnologies.sharepoint.com"
                                  onChange={() => {
                                    const val = "https://sorimtechnologies.sharepoint.com";
                                    console.log("Data source path input changed:", val);
                                    setDataSourcePath(val);
                                    const validation = validateSharePointUrl(val);
                                    setIsValidUrl(validation.isValid);
                                    setUrlValidationError(validation.error || "");
                                  }}
                                  placeholder="e.g. https://company.sharepoint.com/Shared Documents/Data/"
                                  style={{
                                    padding: "6px 10px",
                                    fontSize: "12px",
                                    fontFamily: "monospace",
                                    border: isValidUrl ? "1px solid #86efac" : "1px solid #fecaca",
                                    transition: "border-color 0.3s ease",
                                    borderRadius: "4px",
                                    outline: "none",
                                    width: "100%",
                                    boxSizing: "border-box" as const,
                                    background: "#fff",
                                  }}
                                />

                                {dataSourcePath && (
                                  <span style={{ fontSize: "11px", color: isValidUrl ? "#059669" : "#dc2626", fontWeight: 500 }}>
                                    {isValidUrl ? "✅ Valid SharePoint URL" : (urlValidationError || "❌ Invalid SharePoint URL")}
                                  </span>
                                )}
                                <span style={{ fontSize: "11px", color: "#64748b", lineHeight: "1.4" }}>
                                  SharePoint URL only. Format: https://companyname.sharepoint.com
                                </span>
                              </div>
                            </div>
                            )}

                            {!isCsvLoadscript && (
                              <div style={{
                                display: "flex",
                                flexDirection: "column",
                                gap: "4px",
                                marginBottom: "10px",
                                padding: "8px 12px",
                                background: "#f0fdf4",
                                borderRadius: "6px",
                                border: "1px solid #bbf7d0",
                              }}>
                                <span style={{ fontSize: "11px", color: "#059669", fontWeight: 500 }}>
                                  ℹ️ Inline LoadScript - No URL required
                                </span>
                              </div>
                            )}

                            <button
                              onClick={handleConvertToMQuery}
                              disabled={convertingToMquery || !selectedTable || (isCsvLoadscript && !isValidUrl)}
                              className="convert-btn"
                              title={
                                !selectedTable ? "Please select a table first" :
                                isCsvLoadscript && !isValidUrl ? "Please enter a valid data source URL for CSV loadscript" :
                                "Convert to M Query"
                              }
                              style={{
                                opacity: (isCsvLoadscript && !isValidUrl) ? 0.5 : 1,
                                cursor: (isCsvLoadscript && !isValidUrl) ? "not-allowed" : "pointer",
                              }}
                            >
                              {convertingToMquery ? "⏳ Converting..." : "🔄 Convert to MQuery"}
                            </button>
                          </>
                        )}
                      </div>

                      {/* Second Display: MQuery */}
                      <div className="display-section mquery-display">
                        <h4>Generated M Query</h4>
                        {!mquery && !convertingToMquery && (
                          <div className="empty-display">M Query will appear here after conversion</div>
                        )}
                        {mquery && (
                          <>
                            <div className="script-content">
                              <pre>{mquery}</pre>
                            </div>
                            {/* Download and Publish buttons side by side */}
                            <div className="mquery-button-group">
                              <button
                                onClick={() => {
                                  const element = document.createElement("a");
                                  const file = new Blob([mquery], { type: "text/plain" });
                                  element.href = URL.createObjectURL(file);
                                  element.download = `${selectedTable || "query"}_mquery.m`;
                                  document.body.appendChild(element);
                                  element.click();
                                  document.body.removeChild(element);
                                }}
                                className="download-btn"
                                title="Download M Query"
                              >
                                ⬇️ Download M Query
                              </button>
                              <button
                                onClick={handlePublishMQuery}
                                disabled={publishingMQuery}
                                className="publish-pbi-btn"
                                title="Publish Push dataset to Power BI Service (schema only — no file data)"
                              >
                                {publishingMQuery ? (
                                  <>
                                    <span className="publish-spinner" />
                                    Publishing…
                                  </>
                                ) : (
                                  <>
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                                      <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                                      <path d="M2 17l10 5 10-5"/>
                                      <path d="M2 12l10 5 10-5"/>
                                    </svg>
                                    Publish MQuery to PowerBI
                                  </>
                                )}
                              </button>
                            </div>

                            {publishStatus !== "idle" && publishMessage && (
                              <div
                                className={`publish-status-msg ${publishStatus === "success" ? "publish-success" : "publish-error"}`}
                              >
                                {publishMessage}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* ER DIAGRAM TAB */}
              {activeTab === "er" && (
                <div className="tab-content er-tab">
                  <div className="summary-div er-diagram-div">
                    <ERDiagramModal
                      tables={tables}
                      relations={relations}
                      mainTable={mainTable}
                      onClose={() => {}}
                      isModal={false}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* ===== SEPARATE DIV FOR TABLE ===== */}
            <div className="data-section">

              {tableLoading && <p>Loading data…</p>}

              {!tableLoading && (
                <>
                  {rows.length > 0 ? (
                    <>
                      {/* Top controls: page length + search */}
                      <div className="data-controls">
                        <div className="length">
                          <label>
                            <select
                              value={pageSize}
                              onChange={(e) => setPageSize(parseInt(e.target.value, 10))}
                            >
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
                            <input
                              type="search"
                              value={tableQuery}
                              onChange={(e) => setTableQuery(e.target.value)}
                              placeholder="Search..."
                            />
                          </label>
                          <button
                            className="csv-btn"
                            disabled={!rows.length}
                            onClick={downloadCSV}
                          >
                            <img src={Csvicon} alt="csv " className="btn-icon" />
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
                                    <TableSortLabel
                                      active={orderBy === k}
                                      direction={orderBy === k ? order : 'asc'}
                                      onClick={() => handleRequestSort(k)}
                                    >
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

                      {/* Pagination */}
                      <div className="pagination-bar">
                        <button
                          className="page-btn"
                          disabled={current === 1}
                          onClick={() => setCurrentPage(current - 1)}
                        >
                          Previous
                        </button>
                        {pageNumbers.map((p, idx) =>
                          typeof p === "number" ? (
                            <button
                              key={idx}
                              className={`page-btn ${p === current ? "active" : ""}`}
                              onClick={() => setCurrentPage(p)}
                            >
                              {p}
                            </button>
                          ) : (
                            <span key={idx} className="ellipsis">…</span>
                          )
                        )}
                        <button
                          className="page-btn"
                          disabled={current === totalPages}
                          onClick={() => setCurrentPage(current + 1)}
                        >
                          Next
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="no-data-placeholder" style={{ padding: 20 }}>
                      <p style={{ margin: 0, color: '#444' }}>No rows available for this table — preview not available.</p>
                      <p style={{ marginTop: 8, color: '#666' }}>You can still export this table; clicking <strong>Continue to Export</strong> will attempt to load the table data.</p>
                    </div>
                  )}

                  {/* BOTTOM RIGHT BUTTON - Export (single table or auto-include related tables for master) */}
                  {activeTab === "summary" && (
                  <div className="bottom-actions">
                    <button
                      className="continue-export-btn"
                      disabled={!isExportAllowed || tableLoading}
                      onClick={() => prepareAndNavigateToExport()}
                      title={!selectedTable ? "Select a table first" : isRelatedTable(selectedTable) ? "Select master table or standalone table to export" : "Proceed to Export page"}
                    >
                      Continue to Export
                    </button>
                  </div>
                  )}
                </>
              )}

            </div>

          </>
        )}
      </div>

      {/* Schema Modal */}
      <SchemaModal
        isOpen={isSchemaModalOpen}
        onClose={() => setIsSchemaModalOpen(false)}
        appId={appId}
        masterTable={mainTable || selectedTable}
        tables={tables}
      />

    </div>
  );
}


// ================= ER DIAGRAM MODAL =================
// Shows a mermaid erDiagram popup matching the Qlik Sense ER structure.
// Relationship direction: fact/history tables are many-side pointing
// to dimension/master tables as one-side. This matches Power BI model view.

interface ERDiagramModalProps {
  tables: any[];
  relations: Record<string, string[]>;
  mainTable: string | null;
  onClose: () => void;
  isModal?: boolean;
}

function ERDiagramModal({ tables, relations, mainTable, onClose, isModal = true }: ERDiagramModalProps) {
  const ref = useRef<HTMLDivElement>(null);

  const factKeywords  = ["fact", "history", "transaction", "detail", "sales", "order"];
  const dimKeywords   = ["master", "dim", "lookup", "ref", "details"];

  const isFact = (name: string) => factKeywords.some(k => name.toLowerCase().includes(k));
  const isDim  = (name: string) => dimKeywords.some(k => name.toLowerCase().includes(k));

  const buildErSource = (): string => {
    let src = "erDiagram\n";

    // Entity definitions — limit fields to avoid overflow
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

    // Relationships with correct direction
    // many-side (fact/history or mainTable) --> one-side (dim/master)
    const seen = new Set<string>();
    for (const [a, toList] of Object.entries(relations)) {
      for (const b of toList) {
        const key = [a, b].sort().join("|");
        if (seen.has(key)) continue;
        seen.add(key);

        const aIsFact = isFact(a) || a === mainTable;
        const bIsFact = isFact(b) || b === mainTable;
        const aIsDim  = isDim(a);
        const bIsDim  = isDim(b);

        let many: string, one: string;
        if (aIsDim && !bIsDim)       { one = a; many = b; }
        else if (bIsDim && !aIsDim)  { one = b; many = a; }
        else if (aIsFact && !bIsFact){ many = a; one = b; }
        else if (bIsFact && !aIsFact){ many = b; one = a; }
        else                          { many = a; one = b; }

        src += `  ${many} }o--|| ${one} : ""\n`;
      }
    }
    return src;
  };

  useEffect(() => {
    if (!ref.current) return;
    ref.current.innerHTML = '<p style="color:#888;font-size:13px;padding:16px">Rendering diagram...</p>';

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
        const { svg } = await mermaid.render("er-modal-svg-" + Date.now(), buildErSource());
        if (ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        console.error("ER render error:", e);
        if (ref.current) ref.current.innerHTML = '<p style="color:#e11d48;font-size:13px;padding:16px">Could not render diagram. Check console for details.</p>';
      }
    });
  }, [tables, relations]);

  return (
    <>
      {isModal ? (
        <div
          onClick={onClose}
          style={{
            position: "fixed", inset: 0, zIndex: 9999,
            background: "rgba(0,0,0,0.5)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "#fff", borderRadius: 12,
              width: "min(90vw, 900px)", maxHeight: "85vh",
              display: "flex", flexDirection: "column",
              boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
            }}
          >
            {/* Header */}
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "16px 20px", borderBottom: "1px solid #e5e7eb",
            }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: "#111827" }}>
                  ER Diagram — from Qlik Sense
                </h3>
                <p style={{ margin: "2px 0 0", fontSize: 12, color: "#6b7280" }}>
                  Entity relationships detected from the Qlik associative model
                </p>
              </div>
              <button
                onClick={onClose}
                style={{
                  background: "none", border: "none", cursor: "pointer",
                  fontSize: 20, color: "#9ca3af", lineHeight: 1, padding: "4px 8px",
                  borderRadius: 4,
                }}
              >
                ✕
              </button>
            </div>

            {/* Diagram */}
            <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
              <div ref={ref} style={{ fontSize: 12 }} />
            </div>

            {/* Footer note */}
            <div style={{
              padding: "10px 20px", borderTop: "1px solid #e5e7eb",
              fontSize: 11, color: "#9ca3af",
            }}>
              Arrows show many-to-one direction. Qlik associative joins on shared field names.
            </div>
          </div>
        </div>
      ) : (
        <div style={{ width: "100%", padding: "20px", fontSize: 12 }}>
          <div ref={ref} />
        </div>
      )}
    </>
  );
}


// ================= SUMMARY REPORT COMPONENT =================
import React from "react";

interface SummaryReportProps {
  summary: any;
  rows: Row[];
  aiBullets?: string[];
  aiLoading?: boolean;
}

// Pie Chart Component
const PieChart: React.FC<{ data: Record<string, number>; title: string }> = ({ data, title }) => {
  const entries = Object.entries(data)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8);

  const total = entries.reduce((sum, [_, val]) => sum + val, 0);

  const colors = [
    "#FF6B6B",
    "#4ECDC4",
    "#45B7D1",
    "#FFA07A",
    "#98D8C8",
    "#F7DC6F",
    "#BB8FCE",
    "#85C1E2",
  ];

  let currentAngle = 0;
  const slices = entries.map(([label, value], i) => {
    const percentage = (value / total) * 100;
    const sliceAngle = (percentage / 100) * 360;
    const startAngle = currentAngle;
    const endAngle = currentAngle + sliceAngle;

    const startRad = (startAngle - 90) * (Math.PI / 180);
    const endRad = (endAngle - 90) * (Math.PI / 180);

    const x1 = 100 + 80 * Math.cos(startRad);
    const y1 = 100 + 80 * Math.sin(startRad);
    const x2 = 100 + 80 * Math.cos(endRad);
    const y2 = 100 + 80 * Math.sin(endRad);

    const largeArc = sliceAngle > 180 ? 1 : 0;

    const pathData = [
      `M 100 100`,
      `L ${x1} ${y1}`,
      `A 80 80 0 ${largeArc} 1 ${x2} ${y2}`,
      `Z`,
    ].join(" ");

    const labelAngle = (startAngle + endAngle) / 2;
    const labelRad = (labelAngle - 90) * (Math.PI / 180);
    const labelX = 100 + 50 * Math.cos(labelRad);
    const labelY = 100 + 50 * Math.sin(labelRad);

    currentAngle = endAngle;

    return {
      pathData,
      color: colors[i % colors.length],
      label,
      percentage,
      value,
      labelX,
      labelY,
    };
  });

  return (
    <div className="pie-chart-container">
      <div className="pie-chart-content">
        <div className="pie-chart-left">
          {title && <h4>{title}</h4>}
          <svg viewBox="0 0 200 200" className="pie-svg">
            {slices.map((slice, i) => (
              <g key={i}>
                <path d={slice.pathData} fill={slice.color} stroke="white" strokeWidth="2" />
                {slice.percentage > 8 && (
                  <text
                    x={slice.labelX}
                    y={slice.labelY}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="pie-label"
                  >
                    {slice.percentage.toFixed(0)}%
                  </text>
                )}
              </g>
            ))}
          </svg>
        </div>
        <div className="pie-legend">
          {slices.map((slice, i) => (
            <div key={i} className="legend-item">
              <span className="legend-color" style={{ backgroundColor: slice.color }}></span>
              <span className="legend-text">
                {slice.label.substring(0, 20)}: {slice.percentage.toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export const SummaryReport: React.FC<SummaryReportProps> = ({
  summary,
  rows,
  aiBullets = [],
  aiLoading = false,
}) => {
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
          if (cityCount[strValue] > topCityCount) {
            topCityCount = cityCount[strValue];
          }
        }
      }
    });
  });

  return (
    <div className="summary-report">
      {/* Analytics Container - Pie Chart and Summary Side by Side */}
      <div className="analytics-container">
        {/* Left: Chart Section */}
        {Object.keys(allCategoricalCounts).length > 0 && (
          <div className="chart-section">
            <PieChart data={allCategoricalCounts} title="" />
          </div>
        )}

        {/* Right: AI Summary Section */}
        <div className="hf-summary-section">
          <h4>Executive Summary</h4>
          <ul className="hf-summary-content">
            {aiLoading ? (
              <li style={{ color: "#6366f1" }}>⏳ Generating AI insights...</li>
            ) : aiBullets.length > 0 ? (
              aiBullets.map((point, idx) => (
                <li key={idx}>{point.replace(/^[-•]\s*/, "")}</li>
              ))
            ) : null}
          </ul>
        </div>
      </div>
    </div>
  );
};