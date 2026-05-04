import "./MigrationPage.css";
import { useNavigate } from "react-router-dom";

export default function MigrationPage() {
  const navigate = useNavigate();
  
  // Get data from sessionStorage
  const appName = sessionStorage.getItem("migration_appName") || sessionStorage.getItem("appName") || "Unknown";
  const tableName = sessionStorage.getItem("migration_selected_table") || "";
  const hasCSV = sessionStorage.getItem("migration_has_csv") === "true";
  const hasDAX = sessionStorage.getItem("migration_has_dax") === "true";
  const rowCount = Number(sessionStorage.getItem("migration_row_count") || "0");
  const columns = JSON.parse(sessionStorage.getItem("migration_columns") || "[]");

  // Show error if no data to migrate
  if (!hasCSV && !hasDAX) {
    return (
      <div className="wrap" style={{ maxWidth: "100vw" }}>
        <h2>🔐 Migration</h2>
        <div style={{
          marginTop: 20,
          padding: "20px",
          backgroundColor: "#fff3cd",
          borderRadius: "8px",
          border: "2px solid #ffc107"
        }}>
          <h3 style={{ marginTop: 0, color: "#856404" }}>⚠️ No Data to Migrate</h3>
          <p style={{ color: "#856404", marginBottom: "20px" }}>
            Please go back to the Publish page and select CSV and/or DAX format before migrating.
          </p>
          <button
            onClick={() => navigate("/publish")}
            style={{
              padding: "12px 24px",
              backgroundColor: "#ffc107",
              color: "#856404",
              border: "none",
              borderRadius: "6px",
              cursor: "pointer",
              fontSize: "14px",
              fontWeight: "bold"
            }}
          >
            ← Back to Publish
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="wrap" >
      <h2>📊 Review</h2>

      {/* 3 INFO BOXES */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: "20px",
        marginTop: "30px",
        marginBottom: "40px"
      }}>
        {/* Application */}
        <div style={{
          padding: "24px",
          borderRadius: "8px",
          backgroundColor: "#f5f5f5",
          border: "2px solid #e0e0e0",
          textAlign: "center",
          boxShadow: "0 2px 8px rgba(0,0,0,0.06)"
        }}>
          <div style={{ fontSize: "14px", color: "#999", marginBottom: "12px", fontWeight: "500", letterSpacing: "0.5px" }}>
            APPLICATION
          </div>
          <div style={{ fontSize: "20px", fontWeight: "bold", color: "#333", wordBreak: "break-word" }}>
            {appName}
          </div>
        </div>

        {/* Dataset Table */}
        <div style={{
          padding: "24px",
          borderRadius: "8px",
          backgroundColor: "#f5f5f5",
          border: "2px solid #e0e0e0",
          textAlign: "center",
          boxShadow: "0 2px 8px rgba(0,0,0,0.06)"
        }}>
          <div style={{ fontSize: "14px", color: "#999", marginBottom: "12px", fontWeight: "500", letterSpacing: "0.5px" }}>
            DATASET (TABLE)
          </div>
          <div style={{ fontSize: "20px", fontWeight: "bold", color: "#333", wordBreak: "break-word" }}>
            {tableName}
          </div>
        </div>

        {/* Rows & Columns */}
        <div style={{
          padding: "24px",
          borderRadius: "8px",
          backgroundColor: "#f5f5f5",
          border: "2px solid #e0e0e0",
          textAlign: "center",
          boxShadow: "0 2px 8px rgba(0,0,0,0.06)"
        }}>
          <div style={{ fontSize: "14px", color: "#999", marginBottom: "12px", fontWeight: "500", letterSpacing: "0.5px" }}>
            ROWS | COLUMNS
          </div>
          <div style={{ fontSize: "20px", fontWeight: "bold", color: "#333" }}>
            {rowCount} | {columns.length}
          </div>
        </div>
      </div>

      {/* PUBLISH BUTTON */}
      <div className="page-actions" >
        <button
          onClick={() => {
            sessionStorage.setItem("migrationComplete", "true");
            navigate("/publish");
          }}
          style={{
            padding: "16px 40px",
            backgroundColor: "#4f46e5",
            color: "white",
            border: "none",
            borderRadius: "8px",
            cursor: "pointer",
            fontSize: "16px",
            fontWeight: "bold",
            transition: "all 0.3s ease",
            boxShadow: "0 8px 20px rgba(79, 70, 229, 0.35)"
          }}
          onMouseOver={(e) => {
            e.currentTarget.style.backgroundColor = "#4339ca";
            e.currentTarget.style.boxShadow = "0 12px 28px rgba(79, 70, 229, 0.45)";
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.backgroundColor = "#4f46e5";
            e.currentTarget.style.boxShadow = "0 8px 20px rgba(79, 70, 229, 0.35)";
          }}
        >
          📤 Publish to Power BI
        </button>
      </div>
    </div>
  );
}
