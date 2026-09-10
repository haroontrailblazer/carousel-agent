import { Search, ListTree, Film, PenLine, PanelsTopLeft, MousePointer2 } from "lucide-react"
import { AGENT_LABELS, REWORKABLE_AGENTS } from "@/lib/pipeline"

const ICONS = [Search, ListTree, Film, PenLine, PanelsTopLeft, MousePointer2]

/** An introduction to the existing creative agents, not a live availability feed. */
export function StudioAgents() {
  return (
    <section className="studio-agents" aria-labelledby="studio-agents-title">
      <div className="studio-agents-heading">
        <h2 id="studio-agents-title">Meet your creative team</h2>
        <span>One brief. Every detail covered.</span>
      </div>
      <div className="studio-agent-grid">
        {REWORKABLE_AGENTS.map((agent, index) => {
          const Icon = ICONS[index]
          return (
            <div className="studio-agent" key={agent.name}>
              <span className="studio-agent-icon"><Icon size={17} strokeWidth={1.6} /></span>
              <div><h3>{AGENT_LABELS[agent.name]}</h3><p>{agent.hint}</p></div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
