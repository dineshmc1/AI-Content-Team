import {AgencyDashboard} from "../../components/AgencyDashboard";

const sectionNames: Record<string, string> = {
  "content-calendar": "Content Calendar", "production-pipeline": "Production Pipeline", "ai-agents": "AI Agents",
  "research-competitors": "Research & Competitors", "content-library": "Content Library", analytics: "Analytics",
  approvals: "Approvals", "agency-autopilot": "Agency Autopilot", "virtual-office": "Virtual Office", settings: "Settings",
};

export default async function SectionPage({params}: {params: Promise<{section: string}>}) {
  const {section} = await params;
  return <AgencyDashboard section={sectionNames[section] ?? "Overview"} />;
}
