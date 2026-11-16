"use client";

import {useEffect, useMemo, useState} from "react";

const API = process.env.NEXT_PUBLIC_AGENCY_API ?? "http://127.0.0.1:8000";

type Overview = {active_tasks: number; pending_approvals: number; waiting_for_desktop: number; tasks_by_state: Record<string, number>; last_platform_sync: {source: string; completed_at: string | null; status: string} | null};
type Task = {id: string; agent_id: string; title: string; state: string; task_type: string; updated_at: string; workspace_path?: string};
type Agent = {agent_id: string; name: string; role: string; description: string; active_task_id?: string};
type Content = {external_id: string; title: string; platform: string; format: string; published_at?: string; views?: number; likes?: number; is_owned: number};
type Event = {id: number; task_id: string; event_type: string; state: string; created_at: string; detail: Record<string, unknown>};
type Schedule = {id: string; content_family_id: string; content_title: string; content_series: string; platform: string; format: string; scheduled_for: string; status: string};

const navigation = ["Overview", "Content Calendar", "Production Pipeline", "AI Agents", "Research & Competitors", "Content Library", "Analytics", "Approvals", "Agency Autopilot", "Virtual Office", "Settings"];
const columns = ["queued", "waiting_for_desktop", "validating", "awaiting_approval", "completed", "failed"];

function endpoint(path: string) { return `${API}${path}`; }
function label(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function badge(state: string) { return `badge ${state.includes("wait") ? "waiting" : state === "completed" ? "completed" : state === "failed" ? "failed" : ""}`; }

export function AgencyDashboard({section = "Overview"}: {section?: string}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [content, setContent] = useState<Content[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const responses = await Promise.all(["/api/overview", "/api/agents", "/api/tasks", "/api/content", "/api/events", "/api/calendar"].map(async (path) => {
        const response = await fetch(endpoint(path), {cache: "no-store"});
        if (!response.ok) throw new Error(`${path}: ${response.status}`);
        return response.json();
      }));
      setOverview(responses[0] as Overview); setAgents(responses[1] as Agent[]); setTasks(responses[2] as Task[]); setContent(responses[3] as Content[]); setEvents(responses[4] as Event[]); setSchedules(responses[5] as Schedule[]); setError(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to load the local agency API."); }
    finally { setLoading(false); }
  };

  useEffect(() => { void load(); const source = new EventSource(endpoint("/api/events/stream")); source.onmessage = () => void load(); return () => source.close(); }, []);
  const byState = useMemo(() => new Map(columns.map((state) => [state, tasks.filter((task) => task.state === state)])), [tasks]);
  const runToday = async () => { try { const response = await fetch(endpoint("/api/autopilot/run"), {method: "POST"}); if (!response.ok) throw new Error(await response.text()); await load(); setError(null); } catch (caught) { setError(caught instanceof Error ? caught.message : "The local agency run could not start."); } };

  return <div className="shell">
    <aside className="sidebar"><div className="brand"><span>LOCAL CONTENT AGENCY</span>BUILD AI<br />WITH DINESH</div>{navigation.map((item) => <a key={item} className={`nav-item ${item === section ? "active" : ""}`} href={item === "Overview" ? "/" : `/${item.toLowerCase().replaceAll(" ", "-").replace("&-", "")}`}>{item}</a>)}</aside>
    <main className="main"><header className="topbar"><div><div className="eyebrow">localhost · real workflow state</div><h1>{section}</h1></div><button className="button primary" onClick={() => void runToday()}>Run Today&apos;s Agency</button></header>
      {loading ? <div className="loading">Connecting to the local agency…</div> : null}
      {error ? <div className="notice error">{error}</div> : null}
      {!loading && section === "Overview" ? <OverviewView overview={overview} tasks={tasks} events={events} /> : null}
      {!loading && section === "Production Pipeline" ? <PipelineView byState={byState} /> : null}
      {!loading && section === "AI Agents" ? <AgentsView agents={agents} tasks={tasks} /> : null}
      {!loading && ["Content Library", "Analytics", "Research & Competitors"].includes(section) ? <ContentView content={content} section={section} /> : null}
      {!loading && section === "Content Calendar" ? <CalendarView schedules={schedules} reload={load} /> : null}
      {!loading && ["Approvals", "Agency Autopilot", "Virtual Office", "Settings"].includes(section) ? <PendingView section={section} overview={overview} tasks={tasks} /> : null}
    </main>
  </div>;
}

function OverviewView({overview, tasks, events}: {overview: Overview | null; tasks: Task[]; events: Event[]}) { const cards = [["Active tasks", overview?.active_tasks ?? 0], ["Waiting for Desktop", overview?.waiting_for_desktop ?? 0], ["Pending approvals", overview?.pending_approvals ?? 0], ["Completed", overview?.tasks_by_state.completed ?? 0]]; return <><div className="grid">{cards.map(([name, value]) => <div className="panel" key={String(name)}><div className="muted">{name}</div><div className="metric">{value}</div></div>)}</div><div className="two-col section"><section className="panel"><div className="section-head"><h2>Production pipeline</h2><span className="muted">{tasks.length} recorded tasks</span></div><div className="list">{tasks.slice(0, 6).map((task) => <div className="item item-row" key={task.id}><div><strong>{task.title}</strong><div className="muted">{task.agent_id} · {task.id}</div></div><span className={badge(task.state)}>{label(task.state)}</span></div>)}{tasks.length === 0 ? <div className="notice">No production tasks exist yet. Create one through the agency workflow; this panel intentionally does not simulate activity.</div> : null}</div></section><section className="panel"><div className="section-head"><h2>Activity</h2><span className="muted">real events</span></div><div className="list">{events.slice(-6).reverse().map((event) => <div className="item" key={event.id}><strong>{label(event.event_type)}</strong><div className="muted">{event.task_id} · {event.created_at}</div></div>)}{events.length === 0 ? <div className="notice">No workflow events recorded.</div> : null}</div></section></div></> }
function PipelineView({byState}: {byState: Map<string, Task[]>}) { return <section className="kanban">{columns.map((state) => <div className="kanban-col" key={state}><h3>{label(state)}</h3>{(byState.get(state) ?? []).map((task) => <div className="task" key={task.id}><strong>{task.title}</strong><div className="muted">{task.agent_id}</div><div className="muted">{task.id}</div></div>)}{(byState.get(state) ?? []).length === 0 ? <div className="notice">None</div> : null}</div>)}</section> }
function AgentsView({agents, tasks}: {agents: Agent[]; tasks: Task[]}) { return <div className="agent-grid">{agents.map((agent) => { const task = tasks.find((candidate) => candidate.id === agent.active_task_id); return <article className="panel agent" key={agent.agent_id}><div className="eyebrow">{agent.role}</div><div className="agent-name">{agent.name}</div><div className="muted">{task ? task.title : "Idle — no active task"}</div>{task ? <span className={badge(task.state)}>{label(task.state)}</span> : null}</article>; })}</div> }
function ContentView({content, section}: {content: Content[]; section: string}) { const owned = content.filter((item) => item.is_owned); return <section className="panel"><div className="section-head"><h2>{section === "Analytics" ? "Owned-account content metrics" : section}</h2><span className="muted">{owned.length} locally indexed owned items</span></div>{owned.length ? <table className="table"><thead><tr><th>Content</th><th>Platform</th><th>Format</th><th>Views</th><th>Likes</th></tr></thead><tbody>{owned.map((item) => <tr key={`${item.platform}-${item.external_id}`}><td>{item.title || item.external_id}</td><td>{item.platform}</td><td>{item.format}</td><td>{item.views ?? "Unavailable"}</td><td>{item.likes ?? "Unavailable"}</td></tr>)}</tbody></table> : <div className="notice">No owned content has been synchronized into the selected local database yet.</div>}</section> }
function CalendarView({schedules, reload}: {schedules: Schedule[]; reload: () => Promise<void>}) { const [title, setTitle] = useState(""); const [series, setSeries] = useState("inside_the_machine"); const [platform, setPlatform] = useState("instagram"); const [format, setFormat] = useState("reel"); const [scheduledFor, setScheduledFor] = useState(""); const [message, setMessage] = useState<string | null>(null); const save = async (event: React.FormEvent<HTMLFormElement>) => { event.preventDefault(); setMessage(null); try { const familyResponse = await fetch(endpoint("/api/content-families"), {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({title, series})}); if (!familyResponse.ok) throw new Error(await familyResponse.text()); const family = await familyResponse.json() as {id: string}; const scheduleResponse = await fetch(endpoint("/api/calendar"), {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({content_family_id:family.id, platform, format, scheduled_for: new Date(scheduledFor).toISOString()})}); if (!scheduleResponse.ok) throw new Error(await scheduleResponse.text()); setTitle(""); setScheduledFor(""); setMessage("Scheduled locally. It has not been published or sent to an external platform."); await reload(); } catch (caught) { setMessage(caught instanceof Error ? caught.message : "Unable to create local schedule."); } }; return <><section className="panel"><div className="section-head"><h2>Plan content</h2><span className="muted">Creates a real local content family and schedule.</span></div><form className="calendar-form" onSubmit={(event) => void save(event)}><input aria-label="Content title" required value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Content title"/><select aria-label="Series" value={series} onChange={(event) => setSeries(event.target.value)}><option value="ai_detective">AI Detective</option><option value="inside_the_machine">Inside the Machine</option><option value="ai_city">AI City</option><option value="long_form_youtube">Long-Form YouTube</option></select><select aria-label="Platform" value={platform} onChange={(event) => setPlatform(event.target.value)}><option value="instagram">Instagram</option><option value="youtube">YouTube</option></select><input aria-label="Format" required value={format} onChange={(event) => setFormat(event.target.value)} placeholder="Format, e.g. reel"/><input aria-label="Scheduled date and time" type="datetime-local" required value={scheduledFor} onChange={(event) => setScheduledFor(event.target.value)}/><button className="button" type="submit">Add to calendar</button></form>{message ? <p className="muted">{message}</p> : null}</section><section className="panel section"><div className="section-head"><h2>Scheduled production</h2><span className="muted">{schedules.length} local records</span></div>{schedules.length ? <table className="table"><thead><tr><th>When</th><th>Content</th><th>Platform</th><th>Format</th><th>Status</th></tr></thead><tbody>{schedules.map((schedule) => <tr key={schedule.id}><td>{new Date(schedule.scheduled_for).toLocaleString()}</td><td>{schedule.content_title}</td><td>{schedule.platform}</td><td>{schedule.format}</td><td><span className={badge(schedule.status)}>{label(schedule.status)}</span></td></tr>)}</tbody></table> : <div className="notice">No planned content is scheduled in the selected local database.</div>}</section></> }
function PendingView({section, overview, tasks}: {section: string; overview: Overview | null; tasks: Task[]}) { const message: Record<string, string> = {Approvals: "Approval records will appear here after a validated task reaches editorial review.", "Agency Autopilot": "Run Today’s Agency creates or resumes scheduled dependency graphs and prepares only validated Desktop handoffs. Platform sync remains an explicit authorized operation.", "Virtual Office": "The office visualization is intentionally deferred until it can subscribe to real task events.", Settings: "Settings persistence is not implemented yet; credentials remain outside the dashboard."}; return <section className="panel"><h2>{section}</h2><div className="notice">{message[section]}</div><p className="muted">Current local state: {overview?.active_tasks ?? 0} active tasks, {tasks.length} total recorded tasks.</p></section> }
