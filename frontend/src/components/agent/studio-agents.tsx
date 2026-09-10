import { ChevronDown } from "lucide-react"
import { AGENT_BLURBS, AGENT_LABELS, REWORKABLE_AGENTS, type ReworkableAgent } from "@/lib/pipeline"
import { StudioArtwork, type StudioArtworkName } from "@/components/layout/studio-artwork"

const ARTWORK: Record<ReworkableAgent, StudioArtworkName> = {
  research: "research-lens",
  planner: "research-lens",
  first_page_visual: "carousel-sculpture",
  phrasing: "design-stylus",
  template_design: "carousel-sculpture",
  cta: "design-stylus",
}

/** An introduction to the existing creative agents, not a live availability feed. */
export function StudioAgents() {
  return (
    <section className="studio-agents" aria-labelledby="studio-agents-title">
      <div className="studio-agents-heading">
        <h2 id="studio-agents-title">Meet your creative team</h2>
        <span>Explore what each agent does.</span>
      </div>
      <div className="studio-agent-grid">
        {REWORKABLE_AGENTS.map((agent) => {
          return (
            <details className="studio-agent studio-agent--illustrated" key={agent.name}>
              <summary>
                <StudioArtwork name={ARTWORK[agent.name]} sizes="(max-width: 767px) 56px, 64px" />
                <span className="studio-agent-copy"><span className="studio-agent-name">{AGENT_LABELS[agent.name]}</span><span className="studio-agent-hint">{agent.hint}</span></span>
                <ChevronDown className="studio-agent-expand" size={13} aria-hidden="true" />
              </summary>
              <p className="studio-agent-detail">{AGENT_BLURBS[agent.name]}</p>
            </details>
          )
        })}
      </div>
    </section>
  )
}
