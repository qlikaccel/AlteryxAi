import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const OUT_DIR = path.resolve("C:/Project/Alteryx_Update/AlteryxAi/separate_repos/alteryx_complex_accelerator_code/docs/ppt");
const OUT_FILE = path.join(OUT_DIR, "target_aware_alteryx_accelerator_swimlane.pptx");
await fs.mkdir(OUT_DIR, { recursive: true });

const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });

deck.theme.colorScheme = {
  name: "TargetAware",
  themeColors: {
    bg1: "#F7FAFC",
    bg2: "#EAF2F8",
    tx1: "#102033",
    tx2: "#475569",
    accent1: "#1D4ED8",
    accent2: "#14B8A6",
    accent3: "#F59E0B",
    accent4: "#7C3AED",
    accent5: "#EF4444",
    accent6: "#0F172A",
  },
};

const W = 1280;
const H = 720;
const FONT = { title: "Poppins", body: "Lato" };
const COLORS = {
  ink: "#102033",
  muted: "#64748B",
  blue: "#1D4ED8",
  teal: "#14B8A6",
  amber: "#F59E0B",
  purple: "#7C3AED",
  red: "#EF4444",
  bg: "#F7FAFC",
  panel: "#FFFFFF",
  line: "#D8E2EF",
  slate: "#0F172A",
};

function addText(slide, text, left, top, width, height, opts = {}) {
  const shape = slide.shapes.add({
    geometry: "rect",
    position: { left, top, width, height },
    fill: opts.fill ?? "#FFFFFF00",
    line: { fill: "#FFFFFF00", width: 0 },
  });
  shape.text = text;
  shape.text.typeface = opts.typeface || FONT.body;
  shape.text.fontSize = opts.fontSize ?? 24;
  shape.text.color = opts.color || COLORS.ink;
  shape.text.bold = opts.bold || false;
  shape.text.alignment = opts.align || "left";
  shape.text.verticalAlignment = opts.valign || "top";
  shape.text.insets = opts.insets || { left: 0, right: 0, top: 0, bottom: 0 };
  if (opts.autoFit) shape.text.autoFit = opts.autoFit;
  return shape;
}

function addTitle(slide, title, subtitle = "") {
  addText(slide, title, 58, 38, 920, 54, {
    typeface: FONT.title,
    fontSize: 34,
    bold: true,
    color: COLORS.ink,
    autoFit: "shrinkText",
  });
  if (subtitle) {
    addText(slide, subtitle, 60, 92, 1040, 34, {
      fontSize: 17,
      color: COLORS.muted,
      autoFit: "shrinkText",
    });
  }
  slide.shapes.add({
    geometry: "rect",
    position: { left: 58, top: 132, width: 1164, height: 2 },
    fill: COLORS.line,
    line: { fill: COLORS.line, width: 0 },
  });
}

function addFooter(slide, index) {
  addText(slide, `Target-aware Alteryx Accelerator | ${index}`, 58, 678, 420, 20, {
    fontSize: 11,
    color: "#7890A8",
  });
}

function card(slide, left, top, width, height, opts = {}) {
  return slide.shapes.add({
    geometry: "roundRect",
    adjustmentList: [{ name: "adj", formula: "val 9000" }],
    position: { left, top, width, height },
    fill: opts.fill || COLORS.panel,
    line: { fill: opts.line || COLORS.line, width: opts.lineWidth ?? 1.2 },
  });
}

function pill(slide, text, left, top, width, fill, color = COLORS.ink) {
  const s = slide.shapes.add({
    geometry: "roundRect",
    adjustmentList: [{ name: "adj", formula: "val 50000" }],
    position: { left, top, width, height: 32 },
    fill,
    line: { fill: fill, width: 0 },
  });
  s.text = text;
  s.text.typeface = FONT.body;
  s.text.fontSize = 13;
  s.text.bold = true;
  s.text.color = color;
  s.text.alignment = "center";
  s.text.verticalAlignment = "middle";
  s.text.insets = { left: 8, right: 8, top: 3, bottom: 3 };
  return s;
}

function connector(slide, from, to, color = COLORS.blue) {
  slide.shapes.add({
    geometry: "connector",
    kind: "straight",
    from,
    fromIdx: 3,
    to,
    toIdx: 1,
    line: { style: "solid", fill: color, width: 2.2 },
    head: { type: "arrow", width: "med", length: "med" },
  });
}

function rightArrow(slide, left, top, width, color = COLORS.blue) {
  const arrow = slide.shapes.add({
    geometry: "rightArrow",
    position: { left, top, width, height: 22 },
    fill: color,
    line: { fill: color, width: 0 },
  });
  return arrow;
}

function arrowBetween(slide, from, to, color = COLORS.blue) {
  const start = from.position.left + from.position.width + 10;
  const end = to.position.left - 10;
  const y = from.position.top + from.position.height / 2 - 11;
  if (end > start) {
    rightArrow(slide, start, y, end - start, color);
  }
}

function processNode(slide, label, left, top, width, color, small = false) {
  const s = card(slide, left, top, width, small ? 58 : 72, {
    fill: "#FFFFFF",
    line: color,
    lineWidth: 2,
  });
  s.text = label;
  s.text.typeface = FONT.body;
  s.text.fontSize = small ? 14 : 15;
  s.text.bold = true;
  s.text.color = COLORS.ink;
  s.text.alignment = "center";
  s.text.verticalAlignment = "middle";
  s.text.insets = { left: 10, right: 10, top: 6, bottom: 6 };
  s.text.autoFit = "shrinkText";
  return s;
}

function bulletList(slide, items, left, top, width, lineHeight = 34) {
  items.forEach((item, idx) => {
    slide.shapes.add({
      geometry: "ellipse",
      position: { left, top: top + idx * lineHeight + 7, width: 8, height: 8 },
      fill: item.color || COLORS.blue,
      line: { fill: item.color || COLORS.blue, width: 0 },
    });
    addText(slide, item.text, left + 20, top + idx * lineHeight, width - 20, 26, {
      fontSize: 17,
      color: COLORS.ink,
      autoFit: "shrinkText",
    });
  });
}

// Slide 1
{
  const slide = deck.slides.add();
  slide.background.fill = COLORS.bg;
  slide.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: W, height: H }, fill: "#F7FAFC", line: { fill: "#F7FAFC", width: 0 } });
  slide.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: 20, height: H }, fill: COLORS.blue, line: { fill: COLORS.blue, width: 0 } });
  addText(slide, "Target-Aware Alteryx Migration Accelerator", 72, 72, 960, 96, {
    typeface: FONT.title,
    fontSize: 42,
    bold: true,
    color: COLORS.ink,
  });
  addText(slide, "A management view of how complex Alteryx workflows can route to Power Query, dbt, Dataform, BigQuery SQL, Python notebooks, or API orchestration before Power BI visualization.", 74, 172, 900, 82, {
    fontSize: 21,
    color: COLORS.muted,
  });
  const c1 = card(slide, 74, 310, 330, 150, { fill: "#EFF6FF", line: "#BFDBFE" });
  const c2 = card(slide, 442, 310, 330, 150, { fill: "#ECFDF5", line: "#A7F3D0" });
  const c3 = card(slide, 810, 310, 330, 150, { fill: "#FFF7ED", line: "#FED7AA" });
  [
    [c1, "Current", "Simple/mid workflows convert to Power Query and publish to Power BI."],
    [c2, "Expanded", "SQL-friendly workflows route to dbt, Dataform, or BigQuery."],
    [c3, "Complex", "Macros, Python, and REST flows route to remediation and orchestration."],
  ].forEach(([shape, title, body]) => {
    addText(slide, title, shape.position.left + 22, shape.position.top + 20, 250, 34, { fontSize: 22, bold: true, color: COLORS.ink });
    addText(slide, body, shape.position.left + 22, shape.position.top + 62, 270, 58, { fontSize: 16, color: COLORS.muted });
  });
  pill(slide, "Management decision: support target-aware conversion, not only Power BI publishing", 74, 520, 720, "#DBEAFE", "#1E40AF");
  addFooter(slide, 1);
}

// Slide 2
{
  const slide = deck.slides.add();
  slide.background.fill = COLORS.bg;
  addTitle(slide, "Proposed Target-Aware Flow", "The Accelerator assesses workflow complexity first, then generates the right target artifact.");
  const n1 = processNode(slide, "Upload\n.yxmd / .yxmc / .yxzp", 52, 250, 145, COLORS.blue);
  const n2 = processNode(slide, "Parse\nWorkflow", 230, 250, 130, COLORS.blue);
  const n3 = processNode(slide, "Assess\nComplexity", 392, 250, 140, COLORS.blue);
  const n4 = processNode(slide, "Target\nRecommendation", 566, 250, 165, COLORS.purple);
  const n5 = processNode(slide, "Validate", 1012, 250, 118, COLORS.teal);
  const n6 = processNode(slide, "Power BI consumes\ncurated tables/views", 1138, 230, 120, COLORS.teal, true);
  [arrowBetween(slide, n1, n2), arrowBetween(slide, n2, n3), arrowBetween(slide, n3, n4)];
  const targetLabels = [
    ["Power Query /\nPower BI Dataset", COLORS.blue],
    ["dbt\nProject", COLORS.teal],
    ["Dataform\nProject", COLORS.teal],
    ["BigQuery SQL\nScripts", COLORS.teal],
    ["Python /\nNotebook Plan", COLORS.amber],
    ["API /\nOrchestration", COLORS.red],
  ];
  const targetShapes = targetLabels.map(([label, color], i) => processNode(slide, label, 782, 142 + i * 78, 180, color, true));
  targetShapes.forEach((target) => {
    connector(slide, n4, target, COLORS.purple);
    connector(slide, target, n5, COLORS.teal);
  });
  arrowBetween(slide, n5, n6, COLORS.teal);
  addFooter(slide, 2);
}

// Slide 3
{
  const slide = deck.slides.add();
  slide.background.fill = COLORS.bg;
  addTitle(slide, "Swimlane: Roles And Responsibilities", "Shows where the user, accelerator, LLM layer, target platforms, and Power BI consumption fit.");
  const lanes = [
    ["Business / Migration User", "#E0F2FE"],
    ["Alteryx Accelerator", "#ECFDF5"],
    ["LLM-Assisted Layer", "#F5F3FF"],
    ["Target Platforms", "#FFF7ED"],
    ["BI Consumption", "#FEE2E2"],
  ];
  const top = 150;
  const laneH = 92;
  lanes.forEach(([name, fill], i) => {
    card(slide, 48, top + i * laneH, 1184, laneH - 12, { fill, line: "#CBD5E1" });
    addText(slide, name, 66, top + i * laneH + 28, 210, 26, { fontSize: 16, bold: true, color: COLORS.ink, autoFit: "shrinkText" });
  });
  const items = [
    [300, top + 22, "Upload\npackage", COLORS.blue],
    [470, top + 22, "Review\nassessment", COLORS.blue],
    [640, top + 22, "Approve\ntarget", COLORS.blue],
    [820, top + 22, "Review\nartifacts", COLORS.blue],
    [1000, top + 22, "Approve\ndeploy", COLORS.blue],
    [270, top + laneH + 22, "Parse", COLORS.teal],
    [420, top + laneH + 22, "Graph", COLORS.teal],
    [570, top + laneH + 22, "Classify", COLORS.teal],
    [730, top + laneH + 22, "Recommend", COLORS.teal],
    [900, top + laneH + 22, "Generate", COLORS.teal],
    [1060, top + laneH + 22, "Validate", COLORS.teal],
    [410, top + laneH * 2 + 22, "Expression\nmapping", COLORS.purple],
    [640, top + laneH * 2 + 22, "Macro / API /\nPython guidance", COLORS.purple],
    [890, top + laneH * 2 + 22, "BRD /\nreports", COLORS.purple],
    [330, top + laneH * 3 + 22, "Power\nQuery", COLORS.amber],
    [480, top + laneH * 3 + 22, "dbt", COLORS.amber],
    [630, top + laneH * 3 + 22, "Dataform", COLORS.amber],
    [780, top + laneH * 3 + 22, "BQ SQL", COLORS.amber],
    [930, top + laneH * 3 + 22, "Notebook /\nPipeline", COLORS.amber],
    [480, top + laneH * 4 + 22, "Curated\ntables/views", COLORS.red],
    [720, top + laneH * 4 + 22, "Semantic\nmodel", COLORS.red],
    [960, top + laneH * 4 + 22, "Power BI\nreports", COLORS.red],
  ].map(([x, y, label, color]) => processNode(slide, label, x, y, 120, color, true));
  for (let i = 0; i < items.length - 1; i += 1) {
    if (i < 4 || (i >= 5 && i < 10) || (i >= 18 && i < 20)) arrowBetween(slide, items[i], items[i + 1], "#64748B");
  }
  addFooter(slide, 3);
}

// Slide 4
{
  const slide = deck.slides.add();
  slide.background.fill = COLORS.bg;
  addTitle(slide, "UI Wireframe: Target Recommendation Screen", "Management-facing view that explains why a workflow should route to a specific target.");
  card(slide, 60, 156, 1160, 500, { fill: "#FFFFFF", line: "#CBD5E1" });
  addText(slide, "Recommended Target: Dataform / BigQuery SQL", 92, 186, 620, 32, { fontSize: 26, bold: true, color: COLORS.ink });
  addText(slide, "Reason: Source and target are BigQuery; transformation logic is mostly SQL-friendly. REST and Python sections require separate remediation artifacts.", 92, 224, 860, 42, { fontSize: 16, color: COLORS.muted });
  const headers = ["Target Option", "Fit", "Reason"];
  const rows = [
    ["Power Query / Power BI", "Medium", "Good for simple direct BI outputs"],
    ["dbt", "High", "Strong fit for SQL warehouse transformation"],
    ["Dataform", "High", "Best fit for BigQuery-native development"],
    ["BigQuery SQL Scripts", "High", "Direct BQ deployment option"],
    ["Python Notebook", "Required", "Python/Jupyter tool detected"],
    ["Pipeline / API Ingestion", "Required", "Bearer-token REST flow detected"],
  ];
  const x = 92, y = 302, col = [330, 160, 560], rowH = 42;
  headers.forEach((h, i) => {
    card(slide, x + col.slice(0, i).reduce((a, b) => a + b, 0), y, col[i], rowH, { fill: "#EFF6FF", line: "#BFDBFE" });
    addText(slide, h, x + col.slice(0, i).reduce((a, b) => a + b, 0) + 12, y + 11, col[i] - 24, 20, { fontSize: 14, bold: true, color: "#1E40AF" });
  });
  rows.forEach((r, ri) => {
    r.forEach((cell, ci) => {
      const left = x + col.slice(0, ci).reduce((a, b) => a + b, 0);
      card(slide, left, y + rowH * (ri + 1), col[ci], rowH, { fill: "#FFFFFF", line: "#E2E8F0" });
      addText(slide, cell, left + 12, y + rowH * (ri + 1) + 10, col[ci] - 24, 20, {
        fontSize: 13,
        color: ci === 1 && cell === "High" ? COLORS.teal : ci === 1 && cell === "Required" ? COLORS.red : COLORS.ink,
        bold: ci === 1,
        autoFit: "shrinkText",
      });
    });
  });
  pill(slide, "Accept Recommendation", 92, 592, 190, "#DBEAFE", "#1E40AF");
  pill(slide, "Choose Different Target", 298, 592, 220, "#F1F5F9", "#334155");
  pill(slide, "Export Assessment", 534, 592, 180, "#ECFDF5", "#047857");
  addFooter(slide, 4);
}

// Slide 5
{
  const slide = deck.slides.add();
  slide.background.fill = COLORS.bg;
  addTitle(slide, "Decision Matrix For Target Selection", "The target depends on data location, workflow complexity, production needs, and remediation requirements.");
  const headers = ["Scenario", "Recommended Target", "Why"];
  const rows = [
    ["Simple file-to-dashboard workflow", "Power Query / Power BI", "Fastest route; existing path supports simple/mid workflows"],
    ["BigQuery source and target", "Dataform / BQ SQL", "Aligns with BQ-native governance and pipelines"],
    ["SQL warehouse transformation", "dbt", "Production-grade SQL models, lineage, tests"],
    ["REST API bearer token", "Ingestion pipeline", "Token handling should happen before BI transformation"],
    ["Python/Jupyter tool", "Python notebook", "Preserve custom code and ML/data science logic"],
    ["Batch / iterative macro", "Flow redesign", "Requires orchestration or recursive logic"],
  ];
  const x = 60, y = 160, col = [355, 270, 520], rowH = 58;
  headers.forEach((h, i) => {
    const left = x + col.slice(0, i).reduce((a, b) => a + b, 0);
    card(slide, left, y, col[i], 44, { fill: "#0F172A", line: "#0F172A" });
    addText(slide, h, left + 14, y + 12, col[i] - 28, 18, { fontSize: 14, bold: true, color: "#FFFFFF" });
  });
  rows.forEach((r, ri) => {
    r.forEach((cell, ci) => {
      const left = x + col.slice(0, ci).reduce((a, b) => a + b, 0);
      card(slide, left, y + 44 + rowH * ri, col[ci], rowH, { fill: ri % 2 ? "#F8FAFC" : "#FFFFFF", line: "#E2E8F0" });
      addText(slide, cell, left + 14, y + 44 + rowH * ri + 13, col[ci] - 28, 30, {
        fontSize: 14,
        color: ci === 1 ? COLORS.blue : COLORS.ink,
        bold: ci === 1,
        autoFit: "shrinkText",
      });
    });
  });
  addFooter(slide, 5);
}

// Slide 6
{
  const slide = deck.slides.add();
  slide.background.fill = COLORS.bg;
  addTitle(slide, "Support Required To Deliver", "The ask covers environment access, representative workflows, LLM capability, and specialized resources.");
  const panels = [
    ["Environment & Data", [
      "VDI access to build/test in client context",
      "Representative workflows that mimic actual complexity",
      "Expected Alteryx outputs for reconciliation",
      "BigQuery, dbt/Dataform, Power BI sandbox access",
    ], COLORS.blue],
    ["LLM & Platform", [
      "Approved GPT / Claude Sonnet / DigitalOcean model access",
      "Large context and structured JSON output",
      "Prompt caching and token/cost monitoring",
      "Secure handling of workflow metadata and secrets",
    ], COLORS.purple],
    ["People", [
      "Senior Python backend developer",
      "Python + ML/LLM engineer",
      "SQL/dbt/Dataform/BigQuery engineer",
      "UI designer and React frontend developer",
    ], COLORS.teal],
  ];
  panels.forEach(([title, items, color], idx) => {
    const left = 62 + idx * 400;
    card(slide, left, 170, 350, 420, { fill: "#FFFFFF", line: color, lineWidth: 2 });
    pill(slide, title, left + 24, 196, 220, `${color}22`, color);
    bulletList(slide, items.map((text) => ({ text, color })), left + 28, 258, 300, 62);
  });
  addText(slide, "Recommended next step: secure VDI + representative workflow samples, then validate Phase 1 assessment and target recommendation before building generators.", 70, 625, 1110, 34, {
    fontSize: 17,
    bold: true,
    color: COLORS.ink,
    autoFit: "shrinkText",
  });
  addFooter(slide, 6);
}

const pptx = await PresentationFile.exportPptx(deck);
await pptx.save(OUT_FILE);
console.log(OUT_FILE);
