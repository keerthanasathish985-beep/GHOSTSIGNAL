import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowDownRight, ArrowLeft, ArrowRight, ArrowUpRight, Check, FileText,
  Globe2, Search, ShieldAlert, X, Zap, AlertTriangle, CheckCircle2, HelpCircle
} from "lucide-react";

type Page = "home" | "investigation" | "results" | "report" | "reports" | "method" | "about" | "analyser";
type Source = "GDELT News" | "Hacker News" | "Bluesky" | "Google Search" | "Amazon Reviews";
type ChartMode = "activity" | "sentiment";

// ---- Analyser types --------------------------------------------------------
interface SinglePrediction {
  label: string;
  probabilities: Record<string, number>;
  is_uncertain: boolean;
  model_available: boolean;
  caveat: string;
  error: string | null;
}
interface AnalyseResult {
  text_preview: string;
  sentiment: SinglePrediction;
  authenticity: SinglePrediction;
}
// ---- Investigation result types -------------------------------------------
interface SentimentBreakdown {
  total: number;
  positive: number;
  negative: number;
  uncertain: number;
  positive_pct: number;
}

interface InvestigationResult {
  investigation_id?: string;
  topic: string;
  query?: string;
  status?: "success" | "partial" | "empty" | "error";
  error?: string;
  sources: string[];
  metrics: {
    records_total: number;
    sources_count: number;
    news_count?: number;
    discussions_count?: number;
    hn_count?: number;
    bluesky_count?: number;
    positive_pct: number;
    positive_count?: number;
    negative_count?: number;
    uncertain_count?: number;
    news_positive?: number;
    news_negative?: number;
    discussions_positive?: number;
    discussions_negative?: number;
  };
  sentiment_stats?: {
    news: SentimentBreakdown;
    discussions: SentimentBreakdown;
    totals: SentimentBreakdown;
  };
  news_count?: number;
  discussions_count?: number;
  hn_count?: number;
  bluesky_count?: number;
  news_records?: any[];
  discussion_records?: any[];
  hn_records?: any[];
  bluesky_records?: any[];
  records?: any[];
  collector_errors?: Record<string, string>;
  sentiment: SinglePrediction;
  authenticity: SinglePrediction;
  timeline: { day: string; all: number; positive: number; negative: number }[];
}
type AnalyserStatus = "idle" | "loading" | "done" | "error";
type InvestigationStatus = "idle" | "running" | "done" | "error";

const sourceOptions: Source[] = ["GDELT News", "Hacker News", "Bluesky"];

const pageHash: Record<Page, string> = {
  home: "investigate", investigation: "investigation", results: "results", report: "report",
  reports: "reports", method: "method", about: "about", analyser: "analyser"
};

const stageNames = [
  ["Collecting source material", "Concurrently querying Hacker News and Bluesky discussions, then GDELT News."],
  ["Preparing the data", "Cleaning text, normalising schemas, and deduplicating records."],
  ["Analysing content", "Classifying records and identifying recurring narrative language."],
  ["Mapping relationships", "Comparing timing, similarity and available engagement signals."],
  ["Compiling findings", "Consolidating observations, evidence references and limitations."]
];

function pageFromHash(): Page {
  const hash = window.location.hash.replace("#", "").toLowerCase();
  const found = (Object.keys(pageHash) as Page[]).find(page => pageHash[page] === hash);
  return found ?? "home";
}

function Brand() {
  return <div className="brand" aria-label="GHOSTSIGNAL">GHOSTSIGNAL<span className="brand-dot"/></div>;
}

function Header({ page, topic, stage, investigationId, onNavigate, onNew }: {
  page: Page; topic: string; stage: number; investigationId?: string; onNavigate: (page: Page) => void; onNew: () => void;
}) {
  const isInvestigation = page === "investigation";
  const invId = investigationId || "GS-0001";
  const navItems: { label: string; page: Page }[] = [
    { label: "Investigate", page: "home" }, { label: "Analyser", page: "analyser" },
    { label: "Reports", page: "reports" }, { label: "Method", page: "method" },
    { label: "About", page: "about" }
  ];
  return <header className="header">
    <button className="brand-button" onClick={() => onNavigate("home")} aria-label="Go to GHOSTSIGNAL home"><Brand/></button>
    <AnimatePresence mode="wait" initial={false}>
      {isInvestigation ? <motion.div key="active-context" className="header-active" initial={{ opacity: 0, y: -7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={{ duration: .24 }}>
        <div className="header-context"><span className="header-slash">/</span><span>INVESTIGATION</span><span className="header-id">{invId}</span>{topic && <span className="header-topic">{topic}</span>}</div>
        <div className="header-live"><i/><span>{stageNames[stage]?.[0] ?? "Processing"}</span><b>0{stage + 1} / 05</b></div>
      </motion.div> : <motion.nav key="main-nav" className="nav" initial={{ opacity: 0, y: -7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={{ duration: .24 }}>
        {navItems.map(item => <button key={item.page} className={page === item.page ? "nav-link active" : "nav-link"} onClick={() => onNavigate(item.page)}>{item.label}</button>)}
      </motion.nav>}
    </AnimatePresence>
    {(page === "results" || page === "report") && <span className="header-result-context">/ {invId} · {page === "results" ? "RESULTS" : "REPORT"}</span>}
    {page !== "home" && <button className="new-button" onClick={onNew}>New investigation <ArrowUpRight size={15}/></button>}
  </header>;
}

function NetworkArt({ variant = "hero" }: { variant?: "hero" | "investigation" }) {
  if (variant === "hero") {
    const points = Array.from({ length: 22 }, (_, i) => {
      const angle = i * 2.399;
      const radius = 33 + (i % 7) * 15;
      return { x: 246 + Math.cos(angle) * radius, y: 204 + Math.sin(angle) * radius, color: i % 4 === 0 ? "#ed69b4" : "#8d55df" };
    });
    return <svg className="hero-network" viewBox="0 0 470 430" aria-hidden="true">
      <defs>
        <radialGradient id="heroGlow"><stop stopColor="#aa71e9" stopOpacity=".24"/><stop offset="1" stopColor="#aa71e9" stopOpacity="0"/></radialGradient>
        <linearGradient id="heroOrb" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#a96ee7"/><stop offset="1" stopColor="#ee83c6"/></linearGradient>
      </defs>
      <circle cx="246" cy="205" r="190" fill="url(#heroGlow)"/>
      {points.map((p, i) => <g key={i}>
        <motion.line x1="246" y1="204" x2={p.x} y2={p.y} stroke={p.color} strokeWidth="1.2" opacity=".65" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: .75, delay: i * .025 }}/>
        <motion.circle cx={p.x} cy={p.y} r={i % 4 === 0 ? 4 : 2.7} fill={p.color} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: [0.55, 1, .65] }} transition={{ delay: .2 + i * .025, duration: 1.4, repeat: Infinity, repeatType: "reverse" }}/>
      </g>)}
      <motion.circle cx="246" cy="204" r="53" fill="url(#heroOrb)" opacity=".15" animate={{ scale: [1, 1.13, 1] }} transition={{ duration: 4.2, repeat: Infinity }}/>
      <motion.circle cx="246" cy="204" r="28" fill="url(#heroOrb)" opacity=".28" animate={{ scale: [1, 1.1, 1] }} transition={{ duration: 3.1, repeat: Infinity }}/>
      <circle cx="246" cy="204" r="7" fill="#7f43c7"/>
      <path d="M24 325 C120 277 165 315 246 347 S374 400 450 340" fill="none" stroke="#9d59e3" strokeWidth="1.1" opacity=".7"/>
      <path d="M30 350 C120 302 166 338 250 370 S380 420 452 362" fill="none" stroke="#ed83c5" strokeWidth="1" opacity=".6"/>
      <g className="hero-crosses" stroke="#26231f" strokeWidth="1"><path d="M85 80h13m-6-6v13M385 270h13m-6-6v13"/></g>
    </svg>;
  }

  const clusters = [
    { id: "A", cx: 350, cy: 215, radius: 108, color: "#8952e5", count: 23 },
    { id: "B", cx: 660, cy: 112, radius: 85, color: "#ec68b5", count: 18 },
    { id: "C", cx: 650, cy: 365, radius: 76, color: "#f49a43", count: 15 }
  ];
  return <svg className="cluster-network" viewBox="0 0 1000 480" role="img" aria-label="Network visualisation of three illustrative content communities">
    <defs>
      <radialGradient id="networkWash"><stop stopColor="#e9cffa" stopOpacity=".78"/><stop offset="1" stopColor="#f2e6fb" stopOpacity="0"/></radialGradient>
      <linearGradient id="bridgeGradient"><stop stopColor="#8d55df"/><stop offset=".5" stopColor="#d58ecb"/><stop offset="1" stopColor="#f49a43"/></linearGradient>
    </defs>
    <ellipse cx="520" cy="240" rx="475" ry="245" fill="url(#networkWash)"/>
    <motion.path d="M440 218 C525 180 565 134 620 118" fill="none" stroke="url(#bridgeGradient)" strokeWidth="2" strokeDasharray="5 8" initial={{ pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: .65 }} transition={{ duration: 1.8, delay: .8 }}/>
    <motion.path d="M440 245 C520 295 568 341 615 360" fill="none" stroke="url(#bridgeGradient)" strokeWidth="2" strokeDasharray="5 8" initial={{ pathLength: 0, opacity: 0 }} animate={{ pathLength: 1, opacity: .65 }} transition={{ duration: 1.8, delay: 1.1 }}/>
    {clusters.map((cluster, clusterIndex) => {
      const nodes = Array.from({ length: cluster.count }, (_, i) => {
        const angle = i * 2.399;
        const radius = 22 + (i % 8) * (cluster.radius / 8);
        return { x: cluster.cx + Math.cos(angle) * radius, y: cluster.cy + Math.sin(angle) * radius, delay: clusterIndex * .26 + i * .025 };
      });
      return <g key={cluster.id}>
        {nodes.map((node, i) => <g key={`${cluster.id}-${i}`}>
          <motion.line x1={cluster.cx} y1={cluster.cy} x2={node.x} y2={node.y} stroke={cluster.color} strokeWidth="1.35" opacity=".57" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: .7, delay: node.delay }}/>
          <motion.circle cx={node.x} cy={node.y} r={i % 4 === 0 ? 4.6 : 3.6} fill={cluster.color} initial={{ scale: 0, opacity: 0 }} animate={{ scale: [0, 1, 1], opacity: [0, 1, .78] }} transition={{ duration: .55, delay: node.delay + .15, repeat: Infinity, repeatDelay: 5.5 }}/>
        </g>)}
        <motion.circle cx={cluster.cx} cy={cluster.cy} r="32" fill={cluster.color} opacity=".10" animate={{ scale: [1, 1.24, 1], opacity: [.12, .24, .12] }} transition={{ duration: 3.4, delay: clusterIndex * .6, repeat: Infinity }}/>
        <motion.circle cx={cluster.cx} cy={cluster.cy} r="18" fill={cluster.color} animate={{ scale: [1, 1.07, 1] }} transition={{ duration: 2.8, delay: clusterIndex * .55, repeat: Infinity }}/>
      </g>;
    })}
    <g className="network-crosshairs" stroke="#37313c" strokeWidth="1.15" opacity=".72">
      <path d="M210 270l-52 30M770 80l44-33M730 405l53 21"/><path d="M154 300h13m-6-7v13M807 47h13m-6-7v13M777 426h13m-6-7v13"/>
    </g>
  </svg>;
}

function WaveArt() {
  const paths = useMemo(() => Array.from({ length: 15 }, (_, i) => {
    const offset = i * 5.1;
    return `M0 ${168 + offset} C150 ${88 + offset * .48} 235 ${214 - offset * .22} 430 ${146 + offset * .58} S710 ${54 + offset * .67} 900 ${143 + offset * .52} S1110 ${225 - offset * .48} 1280 ${126 + offset * .55} S1460 ${72 + offset * .25} 1600 ${120 + offset * .48}`;
  }), []);
  return <div className="wave-stage">
    <svg className="wave-art" viewBox="0 0 1600 270" preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id="waveFill" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#d7a5f1" stopOpacity=".13"/><stop offset="1" stopColor="#c47be9" stopOpacity=".34"/></linearGradient></defs>
      <path d="M0 178 C150 105 235 228 430 160 S710 70 900 154 S1110 238 1280 138 S1460 87 1600 132 L1600 270 L0 270Z" fill="url(#waveFill)"/>
      {paths.map((d, i) => <motion.path key={i} d={d} fill="none" stroke="#a957e4" strokeWidth={i === 7 ? 1.8 : 1.25} opacity={.35 + i * .026} initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.8 + i * .055, delay: i * .045, ease: "easeOut" }} className="wave-line"/>)}
    </svg>
    <div className="wave-annotation wave-annotation-one"><span>mention<br/>spike</span><i/></div>
    <div className="wave-annotation wave-annotation-two"><span>coordinated<br/>activity?</span><i/></div>
    <div className="wave-annotation wave-annotation-three"><span>narrative<br/>shift</span><i/></div>
  </div>;
}

function Home({ onStart }: { onStart: (topic: string, selected: Source[]) => void }) {
  const [topic, setTopic] = useState("");
  const [selected, setSelected] = useState<Source[]>(["GDELT News", "Hacker News", "Bluesky"]);
  const [error, setError] = useState(false);
  const toggle = (source: Source) => setSelected(current => current.includes(source) ? current.filter(item => item !== source) : [...current, source]);
  const submit = () => topic.trim() && selected.length ? onStart(topic.trim(), selected) : setError(true);
  return <motion.main className="home-screen" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: .48 }}>
    <div className="screen-index"><span>01 /</span><b>Investigate</b></div>
    <section className="home-hero">
      <motion.div className="hero-copy" initial={{ opacity: 0, x: -25 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: .65, delay: .1 }}>
        <div className="hero-kicker">PUBLIC INFORMATION / NARRATIVE INTELLIGENCE</div>
        <h1>TRACE<br/>NARRATIVES<br/>ACROSS THE<br/>OPEN WEB<span className="purple-dot">.</span></h1>
        <p>A graph-based investigation platform for uncovering coordinated behaviour, misleading narratives and influence patterns in public online spaces.</p>
      </motion.div>
      <motion.div className="hero-network-wrap" initial={{ opacity: 0, scale: .88, x: 30 }} animate={{ opacity: 1, scale: 1, x: 0 }} transition={{ duration: .9, delay: .18 }}><NetworkArt/></motion.div>
    </section>
    <motion.section className="start-block" id="investigate" initial={{ opacity: 0, y: 22 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .38, duration: .5 }}>
      <div className="start-heading">Start a new investigation</div>
      <div className="query-wrap">
        <Search size={21}/>
        <input value={topic} onChange={event => { setTopic(event.target.value); setError(false); }} onKeyDown={event => event.key === "Enter" && submit()} placeholder="Enter a topic, product, brand, claim or URL..." aria-label="Investigation topic"/>
        {topic && <button className="clear-button" onClick={() => setTopic("")} aria-label="Clear query"><X size={15}/></button>}
        <motion.button className="query-submit" onClick={submit} aria-label="Begin investigation" whileHover={{ scale: 1.04 }} whileTap={{ scale: .94 }}><ArrowRight size={24}/></motion.button>
      </div>
      {error && <motion.div className="form-error" initial={{ opacity: 0, y: -5 }} animate={{ opacity: 1, y: 0 }}>Enter a subject and select at least one source.</motion.div>}
    </motion.section>
    <motion.section className="source-area" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: .55 }}>
      <span className="eyebrow">SOURCES</span>
      <div className="source-list">
        {sourceOptions.map(source => <button key={source} className={`source-chip ${selected.includes(source) ? "checked" : ""}`} onClick={() => toggle(source)}>
          <span className="check-square">{selected.includes(source) && <Check size={11}/>}</span>
          {source === "Bluesky" ? <span className="bsky-icon">🦋</span> : source === "Google Search" ? <Globe2 size={15}/> : <span className="amazon-a">a</span>}{source}
        </button>)}
        <span className="source-note">HN &amp; Bluesky run concurrently · GDELT News follows</span>
      </div>
    </motion.section>
    <div className="home-bottom"><WaveArt/></div>
  </motion.main>;
}

function Investigation({ topic, selected, stage, fetchError, onBack, onSkip }: {
  topic: string; selected: Source[]; stage: number; fetchError: string | null; onBack: () => void; onSkip: () => void;
}) {
  return <motion.main className="investigation-screen" initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }} transition={{ duration: .45 }}>
    <div className="screen-index"><span>02 /</span><b>Analyse</b></div>
    <div className="investigation-title">
      <div className="section-overline"><i/> INVESTIGATION IN PROGRESS</div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.h1
          key={stage}
          initial={{ opacity: 0, y: 22 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -18 }}
          transition={{ duration: .38, ease: "easeOut" }}
        >
          {stage === 0 && <><span className="investigation-heading-plain">FIRST,</span><span className="investigation-heading-plain">WE GATHER</span><span className="investigation-heading-accent">THE EVIDENCE.</span></>}
          {stage === 1 && <><span className="investigation-heading-plain">THEN, WE</span><span className="investigation-heading-plain">MAKE RECORDS</span><span className="investigation-heading-accent">COMPARABLE.</span></>}
          {stage === 2 && <><span className="investigation-heading-plain">WE BEGIN</span><span className="investigation-heading-plain">TO READ</span><span className="investigation-heading-accent">THE CONTENT.</span></>}
          {stage === 3 && <><span className="investigation-heading-plain">NOW, WE LOOK</span><span className="investigation-heading-plain">FOR</span><span className="investigation-heading-accent">PATTERNS.</span></>}
          {stage === 4 && <><span className="investigation-heading-plain">FINALLY,</span><span className="investigation-heading-plain">ASSEMBLING</span><span className="investigation-heading-accent">THE EVIDENCE.</span></>}
        </motion.h1>
      </AnimatePresence>
      <AnimatePresence mode="wait" initial={false}>
        <motion.p
          key={`desc-${stage}`}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: .32, delay: .1 }}
        >
          {stageNames[stage]?.[1] ?? "Processing signals in real time."}
        </motion.p>
      </AnimatePresence>
      <motion.div className="topic-sticker" initial={{ rotate: -9, y: -14, opacity: 0 }} animate={{ rotate: -5, y: 0, opacity: 1 }} transition={{ delay: .35, type: "spring", stiffness: 150 }}>“{topic}”</motion.div>
    </div>
    <section className="stage-list">
      {stageNames.map(([title, detail], i) => <motion.div key={title} className={`stage-item ${i < stage ? "complete" : i === stage ? "current" : "pending"}`} initial={{ opacity: 0, x: 15 }} animate={{ opacity: i <= stage ? 1 : .58, x: 0 }} transition={{ delay: .15 + i * .09, duration: .35 }}>
        <div className="stage-marker">{i < stage ? <Check size={13}/> : i === stage ? <motion.span className="stage-pulse" animate={{ scale: [1, 1.55, 1], opacity: [1, .35, 1] }} transition={{ duration: 1.5, repeat: Infinity }}/> : null}</div>
        <div className="stage-number">0{i + 1}</div>
        <div className="stage-copy"><b>{title}</b><span>{detail}</span></div>
        <div className="stage-status">{i < stage ? "✓ Complete" : i === stage ? "In progress" : "Pending"}</div>
      </motion.div>)}
    </section>
    {fetchError && (
      <motion.div className="investigation-fetch-error" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
        <AlertTriangle size={14}/> API error: {fetchError}
        <span>Make sure the Flask API is running: <code>python app.py</code></span>
      </motion.div>
    )}
    <motion.div className="cluster-area" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: .2, duration: .8 }}>
      <NetworkArt variant="investigation"/>
      <motion.div className="cluster-tag tag-a" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: .85 }}>Community A<br/><b>(2.4k accounts)</b></motion.div>
      <motion.div className="cluster-tag tag-b" initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 1.05 }}>Community B<br/><b>(1.1k accounts)</b></motion.div>
      <motion.div className="cluster-tag tag-c" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 1.2 }}>Community C<br/><b>(827 accounts)</b></motion.div>
      <div className="network-caption"><span>ILLUSTRATIVE RELATIONSHIP MAP</span><span>3 CLUSTERS / SELECTED RECORDS</span></div>
    </motion.div>
    <div className="investigation-footer">
      <span>SELECTED SOURCES · {selected.join(" / ")}</span>
      <button onClick={onBack}>Edit query</button>
      {fetchError && <button onClick={onSkip}>View results anyway <ArrowRight size={14}/></button>}
    </div>
  </motion.main>;
}

type TimelineEntry = { day: string; all: number; positive: number; negative: number };

function ActivityChart({ mode, timeline }: { mode: ChartMode; timeline: TimelineEntry[] }) {
  const primary = mode === "activity" ? "all" : "positive";
  // In sentiment mode scale Y off whichever is larger: positive or negative.
  const max = mode === "activity"
    ? Math.max(...timeline.map(d => d.all), 10)
    : Math.max(...timeline.map(d => Math.max(d.positive, d.negative)), 10);
  const x = (index: number) => 48 + index * (572 / Math.max(timeline.length - 1, 1));
  const y = (value: number) => 166 - (value / max) * 142;
  const pathFor = (key: keyof TimelineEntry) =>
    timeline.map((item, index) => `${index === 0 ? "M" : "L"} ${x(index)} ${y(item[key] as number)}`).join(" ");
  const areaFor = (key: keyof TimelineEntry) =>
    `${pathFor(key)} L ${x(timeline.length - 1)} 166 L ${x(0)} 166 Z`;
  // Peak marker always tracks the primary series for the current mode.
  const peakIdx = timeline.reduce((best, d, i) => d[primary] > timeline[best][primary] ? i : best, 0);
  return <svg className="activity-chart" viewBox="0 0 650 215" role="img" aria-label={mode === "activity" ? "Observed activity over time" : "Positive and negative activity over time"}>
    <defs>
      <linearGradient id="activityArea" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#9359e5" stopOpacity=".3"/><stop offset="1" stopColor="#9359e5" stopOpacity=".02"/></linearGradient>
      <linearGradient id="positiveArea" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#8c52e8" stopOpacity=".23"/><stop offset="1" stopColor="#8c52e8" stopOpacity=".01"/></linearGradient>
      <linearGradient id="negativeArea" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#ed69b5" stopOpacity=".22"/><stop offset="1" stopColor="#ed69b5" stopOpacity=".01"/></linearGradient>
    </defs>
    {[0, Math.round(max * 0.2), Math.round(max * 0.4), Math.round(max * 0.6), Math.round(max * 0.8), max].map(value => (
      <g key={value}><line x1="48" x2="620" y1={y(value)} y2={y(value)} stroke="#d9d2dc" strokeDasharray="3 6"/><text x="36" y={y(value) + 4} textAnchor="end">{value}</text></g>
    ))}
    <motion.path d={areaFor(primary)} fill={mode === "activity" ? "url(#activityArea)" : "url(#positiveArea)"} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: .7 }}/>
    <motion.path d={pathFor(primary)} fill="none" stroke={mode === "activity" ? "#7f42d2" : "#8952e5"} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: .9, ease: "easeOut" }}/>
    {mode === "sentiment" && <>
      <motion.path d={areaFor("negative")} fill="url(#negativeArea)" initial={{ opacity: 0 }} animate={{ opacity: 1 }}/>
      <motion.path d={pathFor("negative")} fill="none" stroke="#e56aac" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: .9, delay: .12 }}/>
    </>}
    <motion.line x1={x(peakIdx)} x2={x(peakIdx)} y1="12" y2="166" stroke="#8d55df" strokeDasharray="3 4" initial={{ opacity: 0 }} animate={{ opacity: .55 }} transition={{ delay: .6 }}/>
    <motion.circle cx={x(peakIdx)} cy={y(timeline[peakIdx][primary] as number)} r="5" fill={mode === "activity" ? "#7f42d2" : "#8952e5"} stroke="#fff" strokeWidth="2" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: .7, type: "spring" }}/>
    {timeline.map((item, index) => <text key={item.day} x={x(index)} y="192" textAnchor="middle" fontSize="8">{item.day}</text>)}
  </svg>;
}

function Results({ topic, invResult, onHome, onReport }: {
  topic: string;
  invResult: InvestigationResult | null;
  onHome: () => void;
  onReport: () => void;
}) {
  const [mode, setMode] = useState<ChartMode>("activity");

  // Fallback when backend unreachable
  const timeline = invResult?.timeline ?? [];
  const metrics  = invResult?.metrics;
  const sent     = invResult?.sentiment;
  const auth     = invResult?.authenticity;

  const sentPos  = sent?.probabilities?.Positive ?? 0;
  const sentNeg  = sent?.probabilities?.Negative ?? 0;
  const authHon  = auth?.probabilities?.HONEST   ?? 0;
  const authFake = auth?.probabilities?.FAKE      ?? 0;

  const sentLabel  = sent?.label  ?? "—";
  const authLabel  = auth?.label  ?? "—";
  const sentColor  = sentLabel === "Positive" ? "#22a06b" : sentLabel === "Negative" ? "#c8372d" : "#8d55df";
  const authColor  = authLabel === "HONEST"   ? "#22a06b" : authLabel === "FAKE"     ? "#c8372d" : "#8d55df";

  return <motion.main className="results-screen" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }} transition={{ duration: .42 }}>
    <div className="screen-index"><span>03 /</span><b>Understand</b></div>
    <section className="results-intro">
      <motion.div initial={{ opacity: 0, x: -18 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: .55 }}>
        <div className="section-overline"><i/> INVESTIGATION COMPLETE</div>
        <h1>WHAT<br/>WE FOUND<span className="purple-dot">.</span></h1>
      </motion.div>
      <motion.p initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .22 }}>
        A concise overview of the records examined and patterns observed for <b>{topic}</b>.
      </motion.p>
    </section>

    {invResult?.status === "error" && (
      <div className="investigation-fetch-error" style={{ marginBottom: "1.2rem", background: "#fef2f2", borderColor: "#ef4444", color: "#b91c1c" }}>
        <AlertTriangle size={15}/> <b>Collection Issue:</b> {invResult.error || "Failed to retrieve records from the selected sources."}
      </div>
    )}
    {invResult?.status === "partial" && (
      <div className="investigation-fetch-error" style={{ marginBottom: "1.2rem", background: "#fef3c7", borderColor: "#f59e0b", color: "#92400e" }}>
        <AlertTriangle size={15}/> <b>Partial Collection:</b> One of the sources encountered an issue. Available records from the active source are displayed below.
      </div>
    )}
    {invResult?.status === "empty" && (
      <div className="investigation-fetch-error" style={{ marginBottom: "1.2rem", background: "#eff6ff", borderColor: "#3b82f6", color: "#1e40af" }}>
        <CheckCircle2 size={15}/> <b>No Records Found:</b> All configured sources were queried but returned no matching records for "{topic}".
      </div>
    )}

    <section className="metrics">
      {[
        { value: metrics ? String(metrics.records_total) : "—", label: "Records analysed", className: "metric-yellow" },
        { value: invResult ? String(invResult.news_count ?? 0) : "—", label: "News (GDELT)", className: "metric-purple" },
        { value: invResult ? String(invResult.hn_count ?? 0) : "—", label: "Hacker News", className: "metric-pink" },
        { value: invResult ? String(invResult.bluesky_count ?? 0) : "—", label: "Bluesky", className: "metric-purple" },
        { value: metrics ? `${metrics.positive_pct}%` : "—", label: "Positive sentiment", className: "metric-yellow" },
      ].map((metric, index) => (
        <motion.div className={`metric ${metric.className}`} key={metric.label} initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: .12 + index * .09 }}>
          <strong>{metric.value}</strong><span>{metric.label}</span>
        </motion.div>
      ))}
    </section>

    {/* Activity chart — only shown when records exist */}
    {(metrics?.records_total ?? 0) > 0 && <section className="activity-section">
      <div className="chart-head">
        <div>
          <div className="section-overline"><i/> SIGNAL ACTIVITY</div>
          <b>Observed activity over time</b>
        </div>
        <div className="chart-switch">
          <button className={mode === "activity" ? "active" : ""} onClick={() => setMode("activity")}>Activity</button>
          <button className={mode === "sentiment" ? "active" : ""} onClick={() => setMode("sentiment")}>Positive vs Negative</button>
        </div>
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={mode} initial={{ opacity: 0, y: 7 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -5 }} transition={{ duration: .22 }}>
          {timeline.length > 0
            ? <ActivityChart mode={mode} timeline={timeline}/>
            : <div className="chart-unavailable">Timeline data unavailable — backend not connected.</div>
          }
        </motion.div>
      </AnimatePresence>
      {mode === "sentiment" && (
        <div className="chart-legend">
          <span><i className="legend-positive"/> Positive</span>
          <span><i className="legend-negative"/> Negative</span>
          <small>Derived from model probability scores</small>
        </div>
      )}
    </section>}

    {/* Model output cards — only shown when records exist */}
    {(metrics?.records_total ?? 0) > 0 && <section className="classification-section">
      <div className="classification-heading">
        <div className="section-overline"><i/> MODEL OUTPUT</div>
        <h2>Classifier<br/>signals.</h2>
        <p>Sentiment and authenticity signals derived from classified records.</p>
      </div>
      <div className="model-output-cards">
        <motion.div className="model-card" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: .08 }}>
          <div className="model-card-label">SENTIMENT</div>
          <div className="model-card-value" style={{ color: sentColor }}>{sentLabel.toUpperCase()}</div>
          {sentPos > 0 || sentNeg > 0 ? (
            <div className="model-card-bar-wrap">
              <div className="model-card-bar-row">
                <span>Positive</span>
                <div className="model-bar-track"><motion.div className="model-bar-fill model-bar-positive" initial={{ width: 0 }} whileInView={{ width: `${(sentPos * 100).toFixed(1)}%` }} viewport={{ once: true }} transition={{ duration: .9, delay: .15 }}/></div>
                <b>{(sentPos * 100).toFixed(1)}%</b>
              </div>
              <div className="model-card-bar-row">
                <span>Negative</span>
                <div className="model-bar-track"><motion.div className="model-bar-fill model-bar-negative" initial={{ width: 0 }} whileInView={{ width: `${(sentNeg * 100).toFixed(1)}%` }} viewport={{ once: true }} transition={{ duration: .9, delay: .22 }}/></div>
                <b>{(sentNeg * 100).toFixed(1)}%</b>
              </div>
              {invResult?.sentiment_stats && (
                <div style={{ marginTop: "6px", paddingTop: "6px", borderTop: "1px dashed var(--line)", fontSize: "9px", color: "var(--muted)", display: "flex", flexDirection: "column", gap: "3px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>News:</span>
                    <b>+{invResult.sentiment_stats.news.positive} / -{invResult.sentiment_stats.news.negative} ({invResult.sentiment_stats.news.positive_pct}% pos)</b>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span>Discussions:</span>
                    <b>+{invResult.sentiment_stats.discussions.positive} / -{invResult.sentiment_stats.discussions.negative} ({invResult.sentiment_stats.discussions.positive_pct}% pos)</b>
                  </div>
                </div>
              )}
            </div>
          ) : <p className="model-card-caveat">No classified records.</p>}
          {sent?.is_uncertain && <div className="analyser-uncertain"><HelpCircle size={11}/> Low confidence — treat as uncertain</div>}
          <p className="model-card-caveat">{sent?.caveat ?? "TF-IDF + Logistic Regression on collected records."}</p>
        </motion.div>

        <motion.div className="model-card" initial={{ opacity: 0, y: 12 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }} transition={{ delay: .18 }}>
          <div className="model-card-label">AUTHENTICITY</div>
          <div className="model-card-value" style={{ color: authColor }}>{authLabel.toUpperCase()}</div>
          {authHon > 0 || authFake > 0 ? (
            <div className="model-card-bar-wrap">
              <div className="model-card-bar-row">
                <span>Honest</span>
                <div className="model-bar-track"><motion.div className="model-bar-fill model-bar-honest" initial={{ width: 0 }} whileInView={{ width: `${(authHon * 100).toFixed(1)}%` }} viewport={{ once: true }} transition={{ duration: .9, delay: .2 }}/></div>
                <b>{(authHon * 100).toFixed(1)}%</b>
              </div>
              <div className="model-card-bar-row">
                <span>Fake</span>
                <div className="model-bar-track"><motion.div className="model-bar-fill model-bar-fake" initial={{ width: 0 }} whileInView={{ width: `${(authFake * 100).toFixed(1)}%` }} viewport={{ once: true }} transition={{ duration: .9, delay: .27 }}/></div>
                <b>{(authFake * 100).toFixed(1)}%</b>
              </div>
            </div>
          ) : <p className="model-card-caveat">No labelled records.</p>}
          {auth?.is_uncertain && <div className="analyser-uncertain"><HelpCircle size={11}/> Low confidence — treat as uncertain</div>}
          <p className="model-card-caveat">{auth?.caveat ?? "Reviewer-profile labels. FAKE does not prove an individual review is fraudulent."}</p>
        </motion.div>
      </div>
    </section>}

    <section className="observations">
      <div className="observations-title"><div className="section-overline"><i/> EVIDENCE NOTES</div><h2>Key observations</h2></div>
      <ul>
        <li><span>01</span>
          Collected{" "}
          <b>{invResult?.news_count ?? 0} news articles</b> (GDELT),{" "}
          <b>{invResult?.hn_count ?? 0} items</b> (Hacker News),{" "}
          <b>{invResult?.bluesky_count ?? 0} posts</b> (Bluesky),{" "}
          <b>{(invResult as any)?.devcommunity_count ?? 0} articles</b> (DEV Community),{" "}
          <b>{(invResult as any)?.stackexchange_count ?? 0} questions</b> (Stack Exchange),{" "}
          <b>{(invResult as any)?.discourse_count ?? 0} topics</b> (Discourse).
          Total: <b>{metrics?.records_total ?? 0} records</b>.
        </li>
        {(metrics?.records_total ?? 0) > 0 && <>
          <li><span>02</span>Overall classified sentiment is <b>{sentLabel}</b> with <b>{metrics ? metrics.positive_pct : 0}% positive</b> across {metrics?.records_total ?? 0} analysed records.</li>
          <li><span>03</span>Authenticity signal is <b>{authLabel}</b> ({(authHon * 100).toFixed(0)}% honest profile match) — reflects reviewer-associated patterns, not individual verified claims.</li>
          <li><span>04</span>The available evidence does not establish coordinated activity, sponsorship or common intent.</li>
        </>}
        {(metrics?.records_total ?? 0) === 0 && (
          <li><span>02</span>No records were collected across any active source for this query. The search may be too specific, sources may be unavailable, or the topic has no indexed public discussion.</li>
        )}
      </ul>
    </section>

    {invResult?.records && invResult.records.length > 0 && (
      <section className="observations" style={{ marginTop: "16px" }}>
        <div className="observations-title">
          <div className="section-overline"><i/> EVIDENCE SAMPLES</div>
          <h2>Collected items</h2>
          <p style={{ fontSize: "10px", color: "var(--muted)", margin: "4px 0 0" }}>
            Displaying {Math.min(invResult.records.length, 10)} of {invResult.records.length} retrieved items with attached sentiment signals.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "10px", width: "100%" }}>
          {invResult.records.slice(0, 10).map((rec: any, idx: number) => {
            const isPos = rec.predicted_label === "Positive";
            const isNeg = rec.predicted_label === "Negative";
            const badgeColor = isPos ? "#22a06b" : isNeg ? "#c8372d" : "#8d55df";
            const confPct = rec.confidence ? `${(rec.confidence * 100).toFixed(0)}%` : null;
            const isGdelt = rec.source === "gdelt";
            const itemType = rec.metadata?.item_type;
            const uniqueKey = rec.id ? `${rec.id}-${idx}` : `${rec.source || 'rec'}-${idx}`;
            const titleText = (rec.title || "").trim();
            const contentText = (rec.content || "").trim();
            const showContent = contentText && contentText !== titleText;

            return (
              <div key={uniqueKey} style={{ padding: "12px 14px", background: "#fff", border: "1px solid var(--line)", borderRadius: "3px", display: "flex", flexDirection: "column", gap: "6px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", fontSize: "9px", fontFamily: "var(--mono)", color: "var(--muted)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{ textTransform: "uppercase", fontWeight: 700, color: isGdelt ? "#7c3aed" : rec.source === "bluesky" ? "#0085ff" : "#ea580c" }}>
                      {isGdelt ? "GDELT News" : rec.source === "bluesky" ? "Bluesky" : "Hacker News"}
                    </span>
                    {itemType && (
                      <span style={{ fontSize: "8.5px", background: "#f3f4f6", padding: "1px 5px", borderRadius: "2px", textTransform: "capitalize", color: "#6b7280" }}>
                        {itemType}
                      </span>
                    )}
                  </div>
                  <span>{rec.published_at ? rec.published_at.substring(0, 10) : "Undated"}</span>
                </div>
                {titleText && (
                  <div style={{ fontSize: "12px", fontWeight: 600, color: "#171716", lineHeight: 1.4 }}>
                    {titleText}
                  </div>
                )}
                {showContent && (
                  <div style={{ fontSize: "11px", color: "#4b5563", lineHeight: 1.45, maxHeight: "4.5em", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {contentText.length > 220 ? contentText.substring(0, 220) + "…" : contentText}
                  </div>
                )}
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: "4px", fontSize: "9px", fontFamily: "var(--mono)" }}>
                  <span style={{ color: badgeColor, fontWeight: 600 }}>
                    {rec.predicted_label ? rec.predicted_label.toUpperCase() : "UNCLASSIFIED"}
                    {confPct && <span style={{ color: "var(--muted)", marginLeft: "5px" }}>({confPct})</span>}
                  </span>
                  {rec.url && (
                    <a href={rec.url} target="_blank" rel="noopener noreferrer" style={{ display: "flex", alignItems: "center", gap: "3px", color: "#4f46e5", textDecoration: "none" }}>
                      Source <ArrowUpRight size={11}/>
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    )}

    {invResult && (!invResult.records || invResult.records.length === 0) && (
      <section className="observations" style={{ marginTop: "16px" }}>
        <div className="observations-title">
          <div className="section-overline"><i/> EVIDENCE SAMPLES</div>
          <h2>Collected items</h2>
        </div>
        <div style={{ padding: "20px", background: "#f9fafb", border: "1px dashed var(--line)", borderRadius: "3px", textAlign: "center", color: "var(--muted)", fontSize: "11px", fontFamily: "var(--mono)" }}>
          NO RECORDS COLLECTED FOR "{topic}". Query returned 0 matching records across active sources.
        </div>
      </section>
    )}

    {!invResult && (
      <div className="results-disclaimer"><ShieldAlert size={14}/> No active investigation results. Start an investigation from the search bar to collect and analyse public signals.</div>
    )}
    <section className="results-actions">
      <button className="outline-action" onClick={onHome}><ArrowLeft size={14}/> New investigation</button>
      <button className="dark-action" onClick={onReport}>View full report <ArrowUpRight size={15}/></button>
    </section>
  </motion.main>;
}

// ---------------------------------------------------------------------------
// Report — full data-driven investigative report with both charts embedded
// ---------------------------------------------------------------------------
function Report({ topic, investigationId, invResult, onBack }: {
  topic: string;
  investigationId?: string;
  invResult: InvestigationResult | null;
  onBack: () => void;
}) {
  const invId   = investigationId || "GS-0001";
  const now     = new Date();
  const dateStr = now.toLocaleDateString("en-GB", { day: "2-digit", month: "long", year: "numeric" });

  const timeline  = invResult?.timeline ?? [];
  const metrics   = invResult?.metrics;
  const sent      = invResult?.sentiment;
  const auth      = invResult?.authenticity;
  const stats     = invResult?.sentiment_stats;
  const records   = invResult?.records ?? [];
  const sources   = invResult?.sources ?? [];
  const errors    = invResult?.collector_errors ?? {};

  const sentPos   = sent?.probabilities?.Positive ?? 0;
  const sentNeg   = sent?.probabilities?.Negative ?? 0;
  const authHon   = auth?.probabilities?.HONEST   ?? 0;
  const authFake  = auth?.probabilities?.FAKE      ?? 0;
  const sentLabel = sent?.label  ?? "—";
  const authLabel = auth?.label  ?? "—";
  const sentColor = sentLabel === "Positive" ? "#22a06b" : sentLabel === "Negative" ? "#c8372d" : "#8d55df";
  const authColor = authLabel === "HONEST"   ? "#22a06b" : authLabel === "FAKE"     ? "#c8372d" : "#8d55df";

  const totalRecords  = metrics?.records_total  ?? 0;
  const newsCount     = invResult?.news_count     ?? 0;
  const hnCount       = invResult?.hn_count       ?? 0;
  const bskyCount     = invResult?.bluesky_count  ?? 0;
  const posCount      = metrics?.positive_count   ?? 0;
  const negCount      = metrics?.negative_count   ?? 0;
  const uncCount      = metrics?.uncertain_count  ?? 0;
  const posPct        = metrics?.positive_pct     ?? 0;

  // Timeline peak day
  const peakDay = timeline.length
    ? timeline.reduce((best, d) => d.all > best.all ? d : best, timeline[0])
    : null;

  // Chronological record groups by date
  const byDate: Record<string, any[]> = {};
  for (const rec of records) {
    const day = rec.published_at ? rec.published_at.substring(0, 10) : "Undated";
    (byDate[day] = byDate[day] ?? []).push(rec);
  }
  const sortedDates = Object.keys(byDate).sort((a, b) => {
    if (a === "Undated") return 1;
    if (b === "Undated") return -1;
    return a < b ? -1 : a > b ? 1 : 0;
  });

  const sourceColor = (src: string) =>
    src === "gdelt" ? "#7c3aed" : src === "bluesky" ? "#0085ff" :
    src === "devcommunity" ? "#22a06b" : src === "stackexchange" ? "#f97316" :
    src === "discourse" ? "#ec4899" : "#ea580c";

  const sourceLabel = (src: string) =>
    src === "gdelt" ? "GDELT News" : src === "bluesky" ? "Bluesky" :
    src === "devcommunity" ? "DEV Community" : src === "stackexchange" ? "Stack Exchange" :
    src === "discourse" ? "Discourse" : "Hacker News";

  return (
    <motion.main
      className="report-screen info-page"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -12 }}
    >
      {/* ── Cover ───────────────────────────────────────────────── */}
      <article className="report-document">

        <div className="report-doc-head">
          <Brand/>
          <span>{invId} · {dateStr}</span>
        </div>

        <div className="report-cover-line">
          <span>INVESTIGATION REPORT</span>
          <span>{dateStr.toUpperCase()}</span>
        </div>
        <h2 style={{ marginTop: "10px" }}>{topic || "Untitled Investigation"}</h2>
        <p className="report-subtitle">
          Public-source narrative and sentiment analysis · {totalRecords} records
          across {sources.length || "multiple"} source{sources.length !== 1 ? "s" : ""}
        </p>

        {/* ── 01 Executive Summary ─────────────────────────────── */}
        <h3>01 / Executive Summary</h3>

        {invResult ? (() => {
          const devCount  = (invResult as any).devcommunity_count  ?? 0;
          const seCount   = (invResult as any).stackexchange_count ?? 0;
          const discCount = (invResult as any).discourse_count     ?? 0;

          // Build a per-source breakdown sentence only for sources that returned records
          const parts: string[] = [];
          if (newsCount > 0)  parts.push(`${newsCount} news article${newsCount !== 1 ? "s" : ""} from GDELT`);
          if (hnCount > 0)    parts.push(`${hnCount} item${hnCount !== 1 ? "s" : ""} from Hacker News`);
          if (bskyCount > 0)  parts.push(`${bskyCount} post${bskyCount !== 1 ? "s" : ""} from Bluesky`);
          if (devCount > 0)   parts.push(`${devCount} article${devCount !== 1 ? "s" : ""} from DEV Community`);
          if (seCount > 0)    parts.push(`${seCount} question${seCount !== 1 ? "s" : ""} from Stack Exchange`);
          if (discCount > 0)  parts.push(`${discCount} topic${discCount !== 1 ? "s" : ""} from Discourse`);

          const breakdown = parts.length === 0
            ? "No records were returned by any source."
            : parts.join("; ") + ".";

          return (
            <p>
              This investigation examined public online discourse relating to <b>"{topic}"</b>.
              A total of <b>{totalRecords} record{totalRecords !== 1 ? "s" : ""}</b> were collected
              and classified across all active sources: {breakdown}
              {totalRecords > 0 && <>
                {" "}Overall classified sentiment skews{" "}
                <b style={{ color: sentColor }}>{sentLabel}</b>{" "}
                ({posPct}% of classified records positive, based on {posCount} positive
                and {negCount} negative predictions
                {uncCount > 0 ? `; ${uncCount} records were uncertain or unclassifiable` : ""}).
                {" "}Authenticity profiling across labelled records returned a signal of{" "}
                <b style={{ color: authColor }}>{authLabel}</b>{" "}
                ({(authHon * 100).toFixed(0)}% honest-pattern match).
              </>}
              {totalRecords === 0 && <>
                {" "}No matching records were found. The query may be too specific, the topic may
                not have indexed public discussion, or all sources encountered errors.
              </>}
              {Object.keys(errors).length > 0 && (
                <> Collection was partial — the following sources encountered errors:{" "}
                {Object.entries(errors).map(([k]) => k).join(", ")}.</>
              )}
            </p>
          );
        })() : (
          <p>
            No investigation data is loaded. Navigate to the investigation screen, run a search, then
            return here to view the generated report.
          </p>
        )}

        {/* ── 02 Methodology ───────────────────────────────────── */}
        <h3>02 / Methodology &amp; Source Coverage</h3>
        <p>
          Records were collected using the GhostSignal automated pipeline. Each source was queried
          concurrently where rate limits permit. Hacker News and Bluesky ran in parallel (Batch 1);
          DEV Community, Stack Exchange and Discourse followed concurrently (Batch 2); GDELT news
          articles were retrieved sequentially to respect the API's one-request-per-five-seconds
          rate limit, with a maximum of two attempts per investigation.
        </p>
        <p>
          All records were normalised into the GHOSTSIGNAL unified schema: source identifier,
          canonical URL, publication timestamp (UTC), author where available, engagement counts
          (likes, comments, shares, views), and free-text content. Sentiment (Positive / Negative)
          and authenticity (HONEST / FAKE) labels were attached by a TF-IDF + Logistic Regression
          classifier trained on labelled review and discussion data.
        </p>

        {/* Source coverage table */}
        {invResult && (
          <div className="report-source-table">
            <div className="report-source-row report-source-header">
              <span>Source</span><span>Records</span><span>Status</span>
            </div>
            {[
              { key: "gdelt",         label: "GDELT News",      count: newsCount },
              { key: "hackernews",    label: "Hacker News",     count: hnCount },
              { key: "bluesky",       label: "Bluesky",         count: bskyCount },
              { key: "devcommunity",  label: "DEV Community",   count: (invResult as any).devcommunity_count ?? 0 },
              { key: "stackexchange", label: "Stack Exchange",  count: (invResult as any).stackexchange_count ?? 0 },
              { key: "discourse",     label: "Discourse",       count: (invResult as any).discourse_count ?? 0 },
            ].map(row => (
              <div className="report-source-row" key={row.key}>
                <span style={{ color: sourceColor(row.key), fontWeight: 600 }}>{row.label}</span>
                <span>{row.count}</span>
                <span style={{ color: errors[row.key] ? "#c8372d" : "#22a06b", fontSize: "9px", fontFamily: "var(--mono)" }}>
                  {errors[row.key] ? `ERROR — ${errors[row.key].substring(0, 60)}` : "OK"}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* ── 03 Signal Activity Chart ─────────────────────────── */}
        {totalRecords > 0 && <>
        <h3>03 / Signal Activity Over Time</h3>
        <p>
          The chart below plots the volume of records observed each day across the seven-day
          collection window. Records without a parseable publication date are distributed
          proportionally across the window.
          {peakDay && peakDay.all > 0 && <> The highest single-day volume was <b>{peakDay.all} record{peakDay.all !== 1 ? "s" : ""}</b> on <b>{peakDay.day}</b>.</>}
        </p>

        {timeline.length > 0 ? (
          <div className="report-chart-wrap">
            <div className="report-chart-label">ACTIVITY — ALL RECORDS</div>
            <ActivityChart mode="activity" timeline={timeline}/>
          </div>
        ) : (
          <div className="report-chart-empty">Timeline data unavailable — no records with parseable dates were collected.</div>
        )}

        {/* ── 04 Sentiment Over Time Chart ─────────────────────── */}
        <h3>04 / Sentiment Distribution Over Time</h3>
        <p>
          The chart below overlays positive (purple) and negative (pink) record counts per day.
          Records classified as uncertain are excluded from both series.
          {stats && <>
            {" "}News sentiment: <b>{stats.news.positive} positive</b>, <b>{stats.news.negative} negative</b>{" "}
            ({stats.news.positive_pct}% positive).
            Discussion sentiment: <b>{stats.discussions.positive} positive</b>, <b>{stats.discussions.negative} negative</b>{" "}
            ({stats.discussions.positive_pct}% positive).
          </>}
        </p>

        {timeline.length > 0 ? (
          <div className="report-chart-wrap">
            <div className="report-chart-label">SENTIMENT — POSITIVE vs NEGATIVE</div>
            <ActivityChart mode="sentiment" timeline={timeline}/>
            <div className="chart-legend" style={{ marginTop: "6px" }}>
              <span><i className="legend-positive"/> Positive</span>
              <span><i className="legend-negative"/> Negative</span>
            </div>
          </div>
        ) : (
          <div className="report-chart-empty">Sentiment timeline unavailable.</div>
        )}

        {/* ── 05 Classifier Signals ────────────────────────────── */}
        <h3>05 / Classifier Signals</h3>
        <p>
          Aggregate sentiment and authenticity scores are derived from the full set of classified
          records. Authenticity ratios are computed over records that received an authenticity label;
          records without product context are excluded from that denominator.
        </p>

        <div className="model-output-cards" style={{ marginTop: "10px" }}>
          <div className="model-card">
            <div className="model-card-label">SENTIMENT</div>
            <div className="model-card-value" style={{ color: sentColor }}>{sentLabel.toUpperCase()}</div>
            {sentPos > 0 || sentNeg > 0 ? (
              <div className="model-card-bar-wrap">
                <div className="model-card-bar-row">
                  <span>Positive</span>
                  <div className="model-bar-track">
                    <motion.div className="model-bar-fill model-bar-positive"
                      initial={{ width: 0 }} whileInView={{ width: `${(sentPos * 100).toFixed(1)}%` }}
                      viewport={{ once: true }} transition={{ duration: .9, delay: .1 }}/>
                  </div>
                  <b>{(sentPos * 100).toFixed(1)}%</b>
                </div>
                <div className="model-card-bar-row">
                  <span>Negative</span>
                  <div className="model-bar-track">
                    <motion.div className="model-bar-fill model-bar-negative"
                      initial={{ width: 0 }} whileInView={{ width: `${(sentNeg * 100).toFixed(1)}%` }}
                      viewport={{ once: true }} transition={{ duration: .9, delay: .2 }}/>
                  </div>
                  <b>{(sentNeg * 100).toFixed(1)}%</b>
                </div>
                {stats && (
                  <div style={{ marginTop: "6px", paddingTop: "6px", borderTop: "1px dashed var(--line)", fontSize: "9px", color: "var(--muted)", display: "flex", flexDirection: "column", gap: "3px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>News:</span>
                      <b>+{stats.news.positive} / −{stats.news.negative} ({stats.news.positive_pct}% pos)</b>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>Discussions:</span>
                      <b>+{stats.discussions.positive} / −{stats.discussions.negative} ({stats.discussions.positive_pct}% pos)</b>
                    </div>
                  </div>
                )}
              </div>
            ) : <p className="model-card-caveat">No classified records.</p>}
            {sent?.is_uncertain && <div className="analyser-uncertain"><HelpCircle size={11}/> Low confidence</div>}
            <p className="model-card-caveat">{sent?.caveat ?? "TF-IDF + Logistic Regression on collected records."}</p>
          </div>

          <div className="model-card">
            <div className="model-card-label">AUTHENTICITY</div>
            <div className="model-card-value" style={{ color: authColor }}>{authLabel.toUpperCase()}</div>
            {authHon > 0 || authFake > 0 ? (
              <div className="model-card-bar-wrap">
                <div className="model-card-bar-row">
                  <span>Honest</span>
                  <div className="model-bar-track">
                    <motion.div className="model-bar-fill model-bar-honest"
                      initial={{ width: 0 }} whileInView={{ width: `${(authHon * 100).toFixed(1)}%` }}
                      viewport={{ once: true }} transition={{ duration: .9, delay: .15 }}/>
                  </div>
                  <b>{(authHon * 100).toFixed(1)}%</b>
                </div>
                <div className="model-card-bar-row">
                  <span>Fake</span>
                  <div className="model-bar-track">
                    <motion.div className="model-bar-fill model-bar-fake"
                      initial={{ width: 0 }} whileInView={{ width: `${(authFake * 100).toFixed(1)}%` }}
                      viewport={{ once: true }} transition={{ duration: .9, delay: .25 }}/>
                  </div>
                  <b>{(authFake * 100).toFixed(1)}%</b>
                </div>
              </div>
            ) : <p className="model-card-caveat">No labelled records.</p>}
            {auth?.is_uncertain && <div className="analyser-uncertain"><HelpCircle size={11}/> Low confidence</div>}
            <p className="model-card-caveat">{auth?.caveat ?? "Reviewer-profile pattern signals."}</p>
          </div>
        </div>
        </>}

        {/* ── 06 Chronological Evidence ────────────────────────── */}
        <h3>06 / Chronological Evidence Log</h3>
        <p>
          All {totalRecords} collected records are listed below in ascending date order. Each entry
          includes the source, record type, classifier verdict, confidence score, and a direct URL
          to the original item.
          {totalRecords > 50 && <> Only records with a parseable publication date appear under their
          date heading; undated records follow at the end.</>}
        </p>

        {sortedDates.length === 0 && (
          <div className="report-chart-empty">No records collected.</div>
        )}

        {sortedDates.map(day => (
          <div key={day} className="report-day-group">
            <div className="report-day-header">
              <span>{day === "Undated" ? "UNDATED RECORDS" : day}</span>
              <span>{byDate[day].length} record{byDate[day].length !== 1 ? "s" : ""}</span>
            </div>
            {byDate[day].map((rec: any, idx: number) => {
              const isPos   = rec.predicted_label === "Positive";
              const isNeg   = rec.predicted_label === "Negative";
              const badgeC  = isPos ? "#22a06b" : isNeg ? "#c8372d" : "#8d55df";
              const confPct = rec.confidence ? `${(rec.confidence * 100).toFixed(0)}%` : null;
              const title   = (rec.title   || "").trim();
              const content = (rec.content || "").trim();
              const show    = content && content !== title;
              const uid     = rec.id ? `${rec.id}-${idx}` : `${rec.source}-${day}-${idx}`;
              return (
                <div key={uid} className="report-evidence-row">
                  <div className="report-evidence-meta">
                    <span style={{ color: sourceColor(rec.source), fontWeight: 700, textTransform: "uppercase", fontSize: "8.5px", fontFamily: "var(--mono)" }}>
                      {sourceLabel(rec.source)}
                    </span>
                    {rec.metadata?.item_type && (
                      <span className="report-evidence-type">{rec.metadata.item_type}</span>
                    )}
                    {rec.author && <span className="report-evidence-author">@{rec.author}</span>}
                    <span style={{ marginLeft: "auto", fontSize: "8px", fontFamily: "var(--mono)", color: "var(--muted)" }}>
                      {rec.published_at ? rec.published_at.substring(0, 16).replace("T", " ") : ""}
                    </span>
                  </div>
                  {title && (
                    <div style={{ fontSize: "11.5px", fontWeight: 600, color: "#171716", lineHeight: 1.4, margin: "4px 0 2px" }}>
                      {title}
                    </div>
                  )}
                  {show && (
                    <div style={{ fontSize: "10.5px", color: "#4b5563", lineHeight: 1.5, marginBottom: "4px" }}>
                      {content.length > 320 ? content.substring(0, 320) + "…" : content}
                    </div>
                  )}
                  <div className="report-evidence-footer">
                    <span style={{ color: badgeC, fontWeight: 700, fontSize: "8.5px", fontFamily: "var(--mono)" }}>
                      {rec.predicted_label ? rec.predicted_label.toUpperCase() : "UNCLASSIFIED"}
                      {confPct && <span style={{ color: "var(--muted)", fontWeight: 400, marginLeft: "4px" }}>({confPct})</span>}
                    </span>
                    {rec.likes != null    && <span className="report-evidence-stat">♥ {rec.likes}</span>}
                    {rec.comments != null && <span className="report-evidence-stat">💬 {rec.comments}</span>}
                    {rec.views != null    && <span className="report-evidence-stat">👁 {rec.views}</span>}
                    {rec.url && (
                      <a href={rec.url} target="_blank" rel="noopener noreferrer" className="report-evidence-link">
                        Source <ArrowUpRight size={10}/>
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ))}

        {/* ── 07 Key Observations ──────────────────────────────── */}
        <h3>07 / Key Observations</h3>
        <ol className="report-observations-list">
          <li>
            <b>Collection scope.</b>{" "}
            {totalRecords === 0
              ? "No records were collected. All sources either returned no results for this query or encountered errors."
              : <>
                  {totalRecords} records were retrieved:{" "}
                  {newsCount > 0 && `${newsCount} GDELT news articles; `}
                  {hnCount > 0 && `${hnCount} Hacker News items; `}
                  {bskyCount > 0 && `${bskyCount} Bluesky posts; `}
                  {((invResult as any)?.devcommunity_count ?? 0) > 0 && `${(invResult as any).devcommunity_count} DEV Community articles; `}
                  {((invResult as any)?.stackexchange_count ?? 0) > 0 && `${(invResult as any).stackexchange_count} Stack Exchange questions; `}
                  {((invResult as any)?.discourse_count ?? 0) > 0 && `${(invResult as any).discourse_count} Discourse topics.`}
                </>
            }
          </li>
          {totalRecords > 0 && <>
            <li>
              <b>Sentiment distribution.</b> Of the {posCount + negCount} classified records,{" "}
              {posCount} ({posPct}%) were labelled Positive and {negCount} (
              {posCount + negCount > 0 ? (100 - posPct) : 0}%) Negative.
              {uncCount > 0 ? ` ${uncCount} records were uncertain or could not be classified.` : ""}
            </li>
            {stats && (
              <li>
                <b>Source-level sentiment.</b> News records were {stats.news.positive_pct}% positive
                ({stats.news.positive} pos, {stats.news.negative} neg). Discussion records were{" "}
                {stats.discussions.positive_pct}% positive ({stats.discussions.positive} pos,{" "}
                {stats.discussions.negative} neg).
              </li>
            )}
            {peakDay && peakDay.all > 0 && (
              <li>
                <b>Activity peak.</b> The highest single-day record volume was{" "}
                <b>{peakDay.all}</b> on <b>{peakDay.day}</b> (
                {peakDay.positive} positive, {peakDay.negative} negative).
              </li>
            )}
            <li>
              <b>Authenticity signal.</b> {(authHon * 100).toFixed(0)}% of labelled records matched
              an honest-profile pattern; {(authFake * 100).toFixed(0)}% matched a fake-profile
              pattern. This reflects reviewer-associated linguistic patterns, not verified fraud.
            </li>
          </>}
          {Object.keys(errors).length > 0 && (
            <li>
              <b>Partial collection.</b> The following sources encountered errors:{" "}
              {Object.entries(errors).map(([k, v]) => `${k} (${v.substring(0, 80)})`).join("; ")}.
            </li>
          )}
          <li>
            <b>Limitations.</b> The earliest collected record is not necessarily the origin of a
            narrative. Similarity between records is a signal to investigate further, not proof of
            coordination. Engagement counts are presented without normalisation.
          </li>
        </ol>

        {/* ── Footer ───────────────────────────────────────────── */}
        <div className="report-doc-foot">
          <span>GHOSTSIGNAL · {invId} · {dateStr}</span>
          <span>Generated from live investigation data · {totalRecords} records</span>
        </div>

      </article>

      <div className="report-actions">
        <button className="outline-action" onClick={onBack}><ArrowLeft size={14}/> Back to findings</button>
      </div>
    </motion.main>
  );
}

function ReportsPage({ topic, investigationId, sources, onOpenReport }: { topic: string; investigationId?: string; sources?: string[]; onOpenReport: () => void }) {
  const invId = investigationId || "GS-0001";
  const sourceLabel = sources && sources.length > 0 ? sources.join(" / ") : "GDELT News / Hacker News / Bluesky";
  return <motion.main className="info-page library-page" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
    <div className="screen-index"><span>02 /</span><b>Reports</b></div>
    <section className="info-hero reports-hero"><div className="section-overline"><i/> INVESTIGATION ARCHIVE</div><h1>THE<br/>RECORD.</h1><p>Reports are the durable record of each investigation: what was examined, what was observed and what the evidence can support.</p></section>
    <section className="report-library-head"><span>RECENT INVESTIGATIONS</span><span>01 RECORD / LATEST</span></section>
    <button className="report-row" onClick={onOpenReport}>
      <span className="report-row-index">01</span><span className="report-row-main"><b>{topic || "Untitled investigation"}</b><small>{invId} · {sourceLabel}</small></span><span className="report-row-status">PREVIEW</span><span className="report-row-arrow"><ArrowUpRight size={19}/></span>
    </button>
    <div className="archive-note"><FileText size={20}/><div><b>Archive integration pending</b><p>Saved reports will appear here once report storage is connected to the backend. This entry is a local prototype preview.</p></div></div>
  </motion.main>;
}

function MethodPage() {
  const methods = [
    ["01", "Collect public material", "Retrieve permitted records from selected sources. Preserve source URLs, retrieval time, publication dates and available engagement metadata."],
    ["02", "Prepare comparable records", "Normalise text and dates, remove duplicates, identify missing fields and retain a traceable link to the original record."],
    ["03", "Classify the content", "Group records into news reporting, customer reviews, promotional content and organic discussion. These are content labels, not truth labels."],
    ["04", "Examine patterns", "Compare chronology, text similarity and source-specific engagement. Similarity is a signal to investigate, not proof of coordination."],
    ["05", "Report what the evidence supports", "Present observations with evidence references, source coverage and explicit limitations. Do not infer identity, sponsorship or intent without independent evidence."]
  ];
  return <motion.main className="info-page method-page" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
    <div className="screen-index"><span>03 /</span><b>Method</b></div>
    <section className="info-hero method-hero"><div className="section-overline"><i/> THE INVESTIGATION METHOD</div><h1>FROM SIGNAL<br/>TO EVIDENCE<span>.</span></h1><p>A transparent sequence for collecting, preparing and examining public information without overstating what the data can prove.</p></section>
    <section className="method-list">{methods.map(([number, title, description], index) => <motion.article className="method-row" key={number} initial={{ opacity: 0, x: -12 }} whileInView={{ opacity: 1, x: 0 }} viewport={{ once: true }} transition={{ delay: index * .07 }}><span>{number}</span><h2>{title}</h2><p>{description}</p><ArrowDownRight size={18}/></motion.article>)}</section>
    <div className="method-principle"><span>OPERATING PRINCIPLE</span><b>Coordination is not falsity. Similarity is not proof. Evidence comes first.</b></div>
  </motion.main>;
}

function AnalyserPage() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<AnalyserStatus>("idle");
  const [result, setResult] = useState<AnalyseResult | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const analyse = async () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setStatus("loading");
    setResult(null);
    setApiError(null);
    try {
      const res = await fetch("/api/analyse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: trimmed }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error(err.error ?? `HTTP ${res.status}`);
      }
      const data: AnalyseResult = await res.json();
      setResult(data);
      setStatus("done");
    } catch (e: any) {
      setApiError(e.message ?? "Unknown error");
      setStatus("error");
    }
  };

  const reset = () => { setText(""); setResult(null); setStatus("idle"); setApiError(null); };

  const sentimentColor = (label: string) =>
    label === "Positive" ? "#22a06b" : label === "Negative" ? "#c8372d" : "#8d55df";
  const authColor = (label: string) =>
    label === "HONEST" ? "#22a06b" : label === "FAKE" ? "#c8372d" : "#8d55df";

  return <motion.main className="info-page analyser-page" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
    <div className="screen-index"><span>02 /</span><b>Analyser</b></div>
    <section className="info-hero analyser-hero">
      <div className="section-overline"><i/> SINGLE-TEXT CLASSIFIER</div>
      <h1>ANALYSE<br/>THE TEXT<span>.</span></h1>
      <p>Paste any review or article excerpt to run it through the GhostSignal sentiment and authenticity classifiers directly.</p>
    </section>

    <div className="analyser-body">
      <div className="analyser-input-wrap">
        <label className="analyser-label" htmlFor="analyser-input">INPUT TEXT</label>
        <textarea
          id="analyser-input"
          className="analyser-textarea"
          placeholder="Paste a review, snippet or any text to classify…"
          value={text}
          onChange={e => { setText(e.target.value); if (status !== "idle") reset(); }}
          rows={6}
          maxLength={20000}
        />
        <div className="analyser-input-footer">
          <span className="analyser-char-count">{text.length.toLocaleString()} / 20,000</span>
          <div className="analyser-actions">
            {text && <button className="outline-action" onClick={reset}><X size={13}/> Clear</button>}
            <button
              className="dark-action"
              onClick={analyse}
              disabled={!text.trim() || status === "loading"}
            >
              {status === "loading"
                ? <><motion.span animate={{ rotate: 360 }} transition={{ duration: .9, repeat: Infinity, ease: "linear" }} style={{ display: "inline-flex" }}><Zap size={14}/></motion.span> Analysing…</>
                : <><Zap size={14}/> Run analysis</>}
            </button>
          </div>
        </div>
      </div>

      <AnimatePresence mode="wait">
        {status === "error" && (
          <motion.div className="analyser-error" key="error" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <AlertTriangle size={15}/> {apiError}
            <span className="analyser-error-hint">Make sure the Flask API is running: <code>python app.py</code></span>
          </motion.div>
        )}

        {status === "done" && result && (
          <motion.div className="analyser-results" key="results" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: .38 }}>
            <div className="analyser-preview">
              <span className="analyser-preview-label">ANALYSED TEXT</span>
              <p>{result.text_preview}</p>
            </div>

            <div className="analyser-cards">
              {/* Sentiment card */}
              <div className="analyser-card">
                <div className="analyser-card-head">
                  <span className="analyser-card-type">SENTIMENT</span>
                  <span className="analyser-card-verdict" style={{ color: sentimentColor(result.sentiment.label) }}>
                    {result.sentiment.is_uncertain && <HelpCircle size={13}/>}
                    {!result.sentiment.is_uncertain && result.sentiment.label === "Positive" && <CheckCircle2 size={13}/>}
                    {!result.sentiment.is_uncertain && result.sentiment.label === "Negative" && <AlertTriangle size={13}/>}
                    {result.sentiment.label.toUpperCase()}
                  </span>
                </div>
                <div className="analyser-prob-list">
                  {Object.entries(result.sentiment.probabilities).sort((a, b) => b[1] - a[1]).map(([cls, prob]) => (
                    <div className="analyser-prob-row" key={cls}>
                      <span>{cls}</span>
                      <div className="analyser-prob-track">
                        <motion.div
                          className="analyser-prob-fill"
                          style={{ background: cls === "Positive" ? "#22a06b" : "#c8372d" }}
                          initial={{ width: 0 }}
                          animate={{ width: `${(prob * 100).toFixed(1)}%` }}
                          transition={{ duration: .7, ease: "easeOut" }}
                        />
                      </div>
                      <b>{(prob * 100).toFixed(1)}%</b>
                    </div>
                  ))}
                </div>
                {result.sentiment.is_uncertain && <div className="analyser-uncertain"><HelpCircle size={11}/> Low confidence — treat as uncertain</div>}
                <p className="analyser-caveat">{result.sentiment.caveat}</p>
              </div>

              {/* Authenticity card */}
              <div className="analyser-card">
                <div className="analyser-card-head">
                  <span className="analyser-card-type">AUTHENTICITY</span>
                  <span className="analyser-card-verdict" style={{ color: authColor(result.authenticity.label) }}>
                    {result.authenticity.is_uncertain && <HelpCircle size={13}/>}
                    {!result.authenticity.is_uncertain && result.authenticity.label === "HONEST" && <CheckCircle2 size={13}/>}
                    {!result.authenticity.is_uncertain && result.authenticity.label === "FAKE" && <AlertTriangle size={13}/>}
                    {result.authenticity.label.toUpperCase()}
                  </span>
                </div>
                <div className="analyser-prob-list">
                  {Object.entries(result.authenticity.probabilities).sort((a, b) => b[1] - a[1]).map(([cls, prob]) => (
                    <div className="analyser-prob-row" key={cls}>
                      <span>{cls}</span>
                      <div className="analyser-prob-track">
                        <motion.div
                          className="analyser-prob-fill"
                          style={{ background: cls === "HONEST" ? "#22a06b" : "#c8372d" }}
                          initial={{ width: 0 }}
                          animate={{ width: `${(prob * 100).toFixed(1)}%` }}
                          transition={{ duration: .7, ease: "easeOut" }}
                        />
                      </div>
                      <b>{(prob * 100).toFixed(1)}%</b>
                    </div>
                  ))}
                </div>
                {result.authenticity.is_uncertain && <div className="analyser-uncertain"><HelpCircle size={11}/> Low confidence — treat as uncertain</div>}
                <p className="analyser-caveat">{result.authenticity.caveat}</p>
              </div>
            </div>

            {(!result.sentiment.model_available || !result.authenticity.model_available) && (
              <div className="analyser-model-warn">
                <ShieldAlert size={13}/> One or more models are not loaded. Run <code>python src/model/train.py</code> to generate artifacts.
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  </motion.main>;
}

function AboutPage() {
  return <motion.main className="info-page about-page" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
    <div className="screen-index"><span>04 /</span><b>About</b></div>
    <section className="info-hero about-hero"><div className="section-overline"><i/> ABOUT GHOSTSIGNAL</div><h1>UNDERSTAND<br/>THE SIGNAL.<br/>NOT THE NOISE<span>.</span></h1><p>GHOSTSIGNAL is a public-source intelligence platform for understanding how information emerges, spreads, evolves and gains influence across the internet.</p></section>
    <section className="about-grid">
      <div><span>WHAT IT DOES</span><h2>Investigates patterns.</h2><p>It collects permitted public information, classifies content, examines chronology and textual similarity, and produces evidence-backed reports.</p></div>
      <div><span>WHAT IT DOES NOT DO</span><h2>Intervene or accuse.</h2><p>It does not remove content, moderate platforms, message accounts or autonomously respond. It does not infer identity or intent from writing style or similar usernames.</p></div>
    </section>
    <div className="about-signoff"><span>GHOSTSIGNAL / 2026</span><b>OBSERVE THE SIGNAL.<br/>RESPECT THE EVIDENCE.</b></div>
  </motion.main>;
}

export default function App() {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [topic, setTopic] = useState("");
  const [selected, setSelected] = useState<Source[]>(["GDELT News", "Hacker News", "Bluesky"]);
  const [stage, setStage] = useState(0);

  // Real investigation state
  const [invStatus, setInvStatus]   = useState<InvestigationStatus>("idle");
  const [invResult, setInvResult]   = useState<InvestigationResult | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const navigate = useCallback((next: Page) => {
    setPage(next);
    window.history.pushState(null, "", `#${pageHash[next]}`);
  }, []);

  useEffect(() => {
    const handleHash = () => setPage(pageFromHash());
    window.addEventListener("popstate", handleHash);
    window.addEventListener("hashchange", handleHash);
    return () => { window.removeEventListener("popstate", handleHash); window.removeEventListener("hashchange", handleHash); };
  }, []);

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [page]);

  // Stage ticker — advances every 1.85s while investigating, capped at 4
  useEffect(() => {
    if (page !== "investigation") return;
    const timer = window.setInterval(() => setStage(s => Math.min(s + 1, 4)), 1850);
    return () => window.clearInterval(timer);
  }, [page]);

  // Navigate to results once BOTH the fetch is successfully done AND stage has reached 4
  useEffect(() => {
    if (page !== "investigation") return;
    if (invStatus === "done" && stage >= 4) {
      const timer = window.setTimeout(() => navigate("results"), 600);
      return () => window.clearTimeout(timer);
    }
  }, [page, invStatus, stage, navigate]);

  const start = useCallback(async (value: string, chosen: Source[]) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setTopic(value);
    setSelected(chosen);
    setStage(0);
    setInvResult(null);
    setFetchError(null);
    setInvStatus("running");
    navigate("investigation");

    try {
      const res = await fetch("/api/investigate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: value, sources: chosen }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        if (!controller.signal.aborted && err && typeof err === "object" && err.status === "error") {
          setInvResult(err as InvestigationResult);
        }
        throw new Error(err.error ?? `HTTP ${res.status}`);
      }
      const data: InvestigationResult = await res.json();
      if (!controller.signal.aborted) {
        setInvResult(data);
        setInvStatus("done");
      }
    } catch (e: any) {
      if (e.name === "AbortError") {
        return;
      }
      if (!controller.signal.aborted) {
        setFetchError(e.message ?? "Unknown error");
        setInvStatus("error");
      }
    }
  }, [navigate]);

  const newInvestigation = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setTopic("");
    setSelected(["GDELT News", "Hacker News", "Bluesky"]);
    setStage(0);
    setInvResult(null);
    setFetchError(null);
    setInvStatus("idle");
    navigate("home");
  }, [navigate]);

  return <div className={`app app-${page}`}>
    <Header page={page} topic={topic} stage={stage} investigationId={invResult?.investigation_id} onNavigate={navigate} onNew={newInvestigation}/>
    <AnimatePresence mode="wait">
      {page === "home" && <Home key="home" onStart={start}/>}
      {page === "investigation" && (
        <Investigation
          key="investigation"
          topic={topic}
          selected={selected}
          stage={stage}
          fetchError={fetchError}
          onBack={newInvestigation}
          onSkip={() => navigate("results")}
        />
      )}
      {page === "results" && (
        <Results
          key={`results-${invResult?.investigation_id || topic || "latest"}`}
          topic={topic}
          invResult={invResult}
          onHome={newInvestigation}
          onReport={() => navigate("report")}
        />
      )}
      {page === "report" && (
        <Report
          key="report"
          topic={topic}
          investigationId={invResult?.investigation_id}
          invResult={invResult}
          onBack={() => navigate("results")}
        />
      )}
      {page === "reports" && (
        <ReportsPage
          key="reports"
          topic={topic}
          investigationId={invResult?.investigation_id}
          sources={invResult?.sources}
          onOpenReport={() => navigate("report")}
        />
      )}
      {page === "analyser" && <AnalyserPage key="analyser"/>}
      {page === "method" && <MethodPage key="method"/>}
      {page === "about" && <AboutPage key="about"/>}
    </AnimatePresence>
  </div>;
}
