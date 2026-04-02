


import axios from "axios";

// Use environment variable for production (set by Render), fallback to localhost for dev
const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
// const BASE_URL = import.meta.env.VITE_API_URL || "https://qlikai-app-ltmrv.ondigitalocean.app"

// Helper to get auth headers from sessionStorage
const getAuthHeaders = () => ({
  "x-api-key": sessionStorage.getItem("qlik_api_key") || "",
  "x-tenant-url": sessionStorage.getItem("tenant_url") || "",
});

// Convert FastAPI response → simple format
const mapApps = (data: any[]) =>
  (data || [])
    .map((a: any) => ({
      id: a.attributes?.id || a.id,
      name: a.attributes?.name || a.name,
      lastModifiedDate:
        a.attributes?.modifiedDate || a.attributes?.lastReloadTime || a.modifiedDate || a.lastReloadTime,
    }))
    .filter((a: any) => Boolean(a.id && a.name));

// Test browser login
export const testBrowserLogin = async (tenantUrl: string) => {
  const res = await axios.get(`${BASE_URL}`, {
    params: { tenant_url: tenantUrl },
  });
  return res.data;
};

// Validate login
export const validateLogin = async (
  tenantUrl: string,
  connectAsUser: boolean,
  username: string,
  password: string
) => {
  const res = await axios.post(`${BASE_URL}/validate-login`, {
    tenant_url: tenantUrl,
    connect_as_user: connectAsUser,
    username,
    password,
  });

  return res.data;
};

// Validate SharePoint URL - STRICT validation
export const validateSharePointUrl = async (sharePointUrl: string) => {
  const res = await axios.post(`${BASE_URL}/validate-sharepoint-url`, {
    sharepoint_url: sharePointUrl,
  });
  return res.data;
};

// Fetch apps
export const fetchApps = async (tenantUrl: string) => {
  const params: any = {};
  if (tenantUrl) {
    params.tenant_url = tenantUrl;
  }

  try {
    const res = await axios.get(`${BASE_URL}/applications`, {
      params,
      headers: getAuthHeaders()
    });
    return mapApps(res.data || []);
  } catch (error: any) {
    const detail =
      error?.response?.data?.detail ||
      error?.response?.data?.message ||
      error?.message ||
      "Failed to fetch applications";
    throw new Error(detail);
  }
};

// Fetch tables
export const fetchTables = async (appId: string, tenantUrl?: string) => {
  const params: any = {};
  if (tenantUrl) {
    params.tenant_url = tenantUrl;
  }

  try {
    const res = await axios.get(
      `${BASE_URL}/applications/${appId}/tables`,
      { params, headers: getAuthHeaders() }
    );
    return res.data.tables || [];
  } catch (error: any) {
    if (error.response?.status === 500) {
      return [];
    }
    throw error;
  }
};

// ✅ CLEAN TABLE DATA (Only standard endpoint)
export const fetchTableData = async (
  appId: string,
  table_Name: string,
  limit?: number,
  offset?: number
) => {
  console.log("🔍 Fetching table data from API...", { table: table_Name, limit, offset });

  try {
    const url = `${BASE_URL}/applications/${appId}/table/${table_Name}/data`;
    const params: any = {};
    if (limit !== undefined) params.limit = limit;
    if (offset !== undefined) params.offset = offset;

    const res = await axios.get(url, { params, headers: getAuthHeaders() });

    if (res.data && res.data.success === false) {
      throw new Error(res.data.error || "Failed to fetch data");
    }

    // Support both shapes: { rows: [...] } or direct array
    if (res.data && res.data.rows) {
      const rows = res.data.rows;
      const firstRow = Array.isArray(rows) && rows.length > 0 ? rows[0] : null;
      const hasPlaceholder = firstRow
        ? Object.values(firstRow).some(
            (v) => typeof v === "string" && v.toLowerCase().includes("not accessible")
          )
        : false;

      if (!hasPlaceholder) return rows;
    }
    if (Array.isArray(res.data)) return res.data;

    throw new Error("Primary table-data endpoint returned no usable rows");
  } catch (error: any) {
    console.warn("⚠️ Primary table-data endpoint failed, trying enhanced endpoint:", error.response?.data || error.message);

    try {
      const enhancedUrl = `${BASE_URL}/applications/${appId}/table/${table_Name}/data/enhanced`;
      const params: any = {};
      if (limit !== undefined) params.limit = limit;
      if (offset !== undefined) params.offset = offset;

      const enhancedRes = await axios.get(enhancedUrl, { params, headers: getAuthHeaders() });
      if (enhancedRes.data && enhancedRes.data.success === false) {
        throw new Error(enhancedRes.data.error || "Enhanced endpoint failed");
      }

      if (enhancedRes.data && enhancedRes.data.rows) return enhancedRes.data.rows;
      if (Array.isArray(enhancedRes.data)) return enhancedRes.data;
    } catch (enhancedError: any) {
      console.error("❌ Failed to fetch table data from both endpoints:", enhancedError.response?.data || enhancedError.message);
      const errorMessage =
        enhancedError.response?.data?.detail ||
        error.response?.data?.detail ||
        enhancedError.message ||
        error.message ||
        "Could not fetch table data";
      throw new Error(`Could not fetch table "${table_Name}". Error: ${errorMessage}`);
    }

    return [];
  }
};

// Temporary AI summary using table meta
export const fetchAISummary = async (appId: string) => {
  const tables = await fetchTables(appId);

  let text = `App contains ${tables.length} tables\n\n`;

  tables.forEach((t: any) => {
    text += `• ${t.name} (${t.fields?.length || 0} fields)\n`;
  });

  return text;
};

// Backend vehicle summary
export const fetchVehicleSummary = async (
  appId: string,
  tableName: string
) => {
  const url = `${BASE_URL}/vehicle-summary`;

  try {
    const res = await axios.get(url, {
      params: {
        app_id: appId,
        table_name: tableName,
      },
    });

    return res.data.summary;
  } catch (e) {
    console.warn(
      "⚠️ Backend summary generation failed, continuing without summary"
    );
    return null;
  }
};

// Generate summary from table data locally
export const generateSummaryFromData = (
  rows: any[],
  tableName: string
): any => {
  if (!rows || rows.length === 0) {
    return { table: tableName, totalRows: 0 };
  }

  const summary: any = {
    table: tableName,
    totalRows: rows.length,
    columns: Object.keys(rows[0]),
    columnCount: Object.keys(rows[0]).length,
    numericAnalysis: {},
    categoryCounts: {},
  };

  const firstRow = rows[0];

  for (const key in firstRow) {
    const values: number[] = [];
    const textValues: Set<string> = new Set();

    rows.forEach((row: any) => {
      const val = row[key];

      if (
        val &&
        typeof val === "string" &&
        val.includes("not accessible")
      ) {
        return;
      }

      if (val !== null && val !== undefined && val !== "") {
        const strVal = String(val).replace(/,/g, "").trim();

        if (!isNaN(Number(strVal)) && strVal !== "") {
          values.push(Number(strVal));
        } else {
          textValues.add(strVal);
        }
      }
    });

    if (values.length > 0) {
      summary.numericAnalysis[key] = {
        min: Math.min(...values),
        max: Math.max(...values),
        avg:
          Math.round(
            (values.reduce((a, b) => a + b, 0) / values.length) * 100
          ) / 100,
        count: values.length,
      };
    }

    if (textValues.size > 0 && textValues.size <= 20) {
      const counts: any = {};

      rows.forEach((row: any) => {
        const val = String(row[key]);
        if (!val.includes("not accessible")) {
          counts[val] = (counts[val] || 0) + 1;
        }
      });

      const sorted = Object.entries(counts)
        .sort((a: any, b: any) => b[1] - a[1])
        .slice(0, 5);

      if (sorted.length > 0) {
        summary.categoryCounts[key] = Object.fromEntries(sorted);
      }
    }
  }

  return summary;
};

// Health check
export const connectQlik = async () => {
  try {
    const res = await axios.get(`${BASE_URL}/health`);
    return res.data;
  } catch (err) {
    throw new Error("Cannot connect to FastAPI backend");
  }
};

// Simple table structure endpoint
export const fetchTableDataSimple = async (
  appId: string,
  table_Name: string
) => {
  const url = `${BASE_URL}/applications/${appId}/table/${table_Name}/data/simple`;

  const res = await axios.get(url);

  if (res.data.success === false) {
    throw new Error(res.data.error || "Failed to fetch table data");
  }

  return res.data;
};

// Export CSV
export const exportTableAsCSV = async (
  appId: string,
  table_Name: string
) => {
  const url = `${BASE_URL}/applications/${appId}/table/${table_Name}/export/csv`;

  const res = await axios.get(url, {
    responseType: "text",
  });

  return res.data;
};

// Download CSV file
export const downloadCSVFile = (
  csvContent: string,
  fileName: string
) => {
  const blob = new Blob([csvContent], {
    type: "text/csv;charset=utf-8;",
  });

  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);

  link.setAttribute("href", url);
  link.setAttribute("download", fileName);
  link.style.visibility = "hidden";

  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
};

// ✅ DOWNLOAD M QUERY - Convert Qlik to PowerBI M Query
export const downloadMQuery = async (appId: string, tableName?: string) => {
  try {
    const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
    // const BASE_URL = import.meta.env.VITE_API_URL || "https://qlikai-app-ltmrv.ondigitalocean.app"

    console.log("📍 Fetching M Query for app:", appId, "table:", tableName);

    // Build URL with optional table parameter
    let url = `${BASE_URL}/api/migration/full-pipeline?app_id=${appId}`;
    if (tableName) {
      url += `&table_name=${encodeURIComponent(tableName)}`;
    }

    // Call the full pipeline endpoint
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Accept": "application/json",
      },
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to generate M Query");
    }

    const data = await response.json();

    if (!data.m_query) {
      throw new Error("No M Query generated");
    }

    // Download the M Query as a file
    const fileName = tableName
      ? `powerbi_query_${appId}_${tableName}.m`
      : `powerbi_query_${appId}.m`;

    const blob = new Blob([data.m_query], { type: "text/plain;charset=utf-8;" });
    const link = document.createElement("a");
    const url_obj = URL.createObjectURL(blob);

    link.setAttribute("href", url_obj);
    link.setAttribute("download", fileName);
    link.style.visibility = "hidden";

    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    console.log("✅ M Query downloaded successfully!");
    return data;
  } catch (error: any) {
    console.error("❌ Failed to download M Query:", error);
    alert(`Error: ${error.message}\n\nCheck browser console for details.`);
    throw error;
  }
};

// ✅ FETCH LOADSCRIPT - Get Qlik LoadScript from app (filtered by table)
export const fetchLoadScript = async (appId: string, tableName?: string) => {
  try {
    const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
    // const BASE_URL = import.meta.env.VITE_API_URL || "https://qlikai-app-ltmrv.ondigitalocean.app"

    // Get credentials from sessionStorage
    const apiKey = sessionStorage.getItem("qlik_api_key");
    const tenantUrl = sessionStorage.getItem("tenant_url");

    console.log("📍 Fetching LoadScript for app:", appId, "table:", tableName);
    console.log("   API Key available:", !!apiKey);

    let url = `${BASE_URL}/api/migration/fetch-loadscript?app_id=${encodeURIComponent(appId)}`;
    if (tableName) {
      url += `&table_name=${encodeURIComponent(tableName)}`;
    }
    if (apiKey) {
      url += `&api_key=${encodeURIComponent(apiKey)}`;
    }
    if (tenantUrl) {
      url += `&tenant_url=${encodeURIComponent(tenantUrl)}`;
    }

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch LoadScript");
    }

    const data = await response.json();
    console.log("✅ LoadScript fetched successfully!", data);
    return data;
  } catch (error: any) {
    console.error("❌ Failed to fetch LoadScript:", error);
    throw error;
  }
};

// ✅ PARSE LOADSCRIPT - Parse Qlik LoadScript
export const parseLoadScript = async (loadscript: string) => {
  try {
    const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
    // const BASE_URL = import.meta.env.VITE_API_URL || "https://qlikai-app-ltmrv.ondigitalocean.app"

    console.log("📍 Parsing LoadScript...");

    // Use POST with body instead of URL query param (LoadScript can be very large - thousands of chars)
    const url = `${BASE_URL}/api/migration/parse-loadscript`;

    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ loadscript }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to parse LoadScript");
    }

    const data = await response.json();
    console.log("✅ LoadScript parsed successfully!", data);
    return data;
  } catch (error: any) {
    console.error("❌ Failed to parse LoadScript:", error);
    throw error;
  }
};

// ✅ CONVERT TO MQUERY - Convert parsed Qlik LoadScript to M Query
// export const convertToMQuery = async (
//   parsedScriptJson: string,
//   tableName?: string
// ) => {
//   try {
//     const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
//     // const BASE_URL = import.meta.env.VITE_API_URL || "https://qlikai-app-ltmrv.ondigitalocean.app"

//     console.log("📍 Converting to M Query for table:", tableName);

//     let url = `${BASE_URL}/api/migration/convert-to-mquery?parsed_script_json=${encodeURIComponent(parsedScriptJson)}`;
//     if (tableName) {
//       url += `&table_name=${encodeURIComponent(tableName)}`;
//     }

//     const response = await fetch(url, {
//       method: "POST",
//       headers: {
//         "Content-Type": "application/json",
//       },
//     });

//     if (!response.ok) {
//       const error = await response.json();
//       throw new Error(error.detail || "Failed to convert to M Query");
//     }

//     const data = await response.json();
//     console.log("✅ Converted to M Query successfully!", data);
//     return data;
//   } catch (error: any) {
//     console.error("❌ Failed to convert to M Query:", error);
//     throw error;
//   }
// };





export const convertToMQuery = async (
  parsedScriptJson: string,
  tableName?: string,
  basePath?: string
) => {
  try {
    const BASE_URL = import.meta.env.VITE_API_URL || "https://qlikai-app-ltmrv.ondigitalocean.app";
 
    console.log("📍 Converting to M Query for table:", tableName);
 
    const response = await fetch(`${BASE_URL}/api/migration/convert-to-mquery`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parsed_script_json: parsedScriptJson,
        table_name: tableName || "",
        base_path: basePath || "",
      }),
    });
 
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to convert to M Query");
    }
 
    const data = await response.json();
    console.log("✅ Converted to M Query successfully!", data);
    return data;
  } catch (error: any) {
    console.error("❌ Failed to convert to M Query:", error);
    throw error;
  }
};