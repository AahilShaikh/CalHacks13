# Aida UI: stage log

Workflow artifact for the Figma track ("Idea to Demo"). One entry per stage, 200 words max each.
Each entry says which tool we used and why, roughly how long it took, how well it worked (1-5),
and how the output made it into the final project. We log rework and dead ends too.

Problem: engineers with a 3D scan or mesh of a part have no fast way to get an editable,
parametric CAD model of it. Aida is an agent that inspects the mesh and rebuilds it as build123d
CAD. The UI's job is to make that process visible: the mesh, the CAD as it is built, and every
tool call the agent makes.

---

## 0. Pre-design (code first): 2026-10-24

- **Tools:** Claude Code (agent-assisted coding), a plain HTML/three.js page written by Yusuf.
- **Why:** we built the backend first, so the first UI was a quick streaming viewer to see whether
  the agent's events (tool calls, mesh revisions) were enough to drive a UI.
- **Time:** ~2 h (Yusuf's streamer) + ~30 min (merge with the model-tools branch).
- **Rating:** 4/5. The event stream works, but the three-panel layout split attention away from
  the 3D view.
- **Output:** the backend event contract (`agent_text`, `tool_call`, `tool_result`, `mesh`,
  `render`, `turn_end`) carries over unchanged. The layout was dropped in favour of
  "viewer + one right panel", and the stack moved to React + @react-three/fiber so Figma's React
  output can feed it directly.

> Minutes below are wall-clock times for the agent-assisted session (Claude Code driving the Figma
> MCP connector). Adjust them if your own review time should count.

## 1. Ideation: 2026-10-24

- **Tool:** FigJam, via the Figma agent's `generate_diagram` (Mermaid → editable board).
- **Why:** before designing screens we needed one picture of what the agent actually does, so the
  UI could show it. The node names are the real tool names from `agent.py`
  (`slice_mesh`, `model_add_feature`, `compare_to_mesh`...), not invented steps.
- **Time:** ~5 min. **Rating:** 5/5.
- **Output:** board "Aida: user flow and agent loop". Its three colored groups (inspect, build,
  verify) became the UI's phase colors, and its dotted "feeds the viewer / panel" edges became
  the screen layout: viewer = mesh, CAD and slice plane; panel = tool calls and deviation.

## 2. Prototyping: 2026-10-24

- **Tool:** Figma Design, written by the Figma agent (`use_figma`, Plugin API).
- **Why:** to agree on the layout before code, and to define tokens and components once, so code
  could reuse them instead of guessing.
- **Built:** an `Aida` variable collection (colors, tints, spacing, radii), 10 components with
  variants (ToolCard, ParamRow, VerdictBadge, Composer, ViewModeToggle, RevisionScrubber,
  AccuracyChart, TopBar, PanelTabs, AgentMessage), three screens (Empty, Running, Model tab) and a
  click-through prototype (example part → running → Model tab).
- **Time:** ~25 min. **Rating:** 3/5.
- **Friction:** `resize()` silently reset "hug" sizing, so cards clipped. Paint-level opacity on
  variable-bound fills was dropped inside instances (chips rendered solid), fixed by adding alpha
  tint variables. Variants of one set share a single default text, so every instance needed its
  text set. One script failed because a text was linked to a property before its parent was
  inside the component.

## 3. Handoff: 2026-10-24

- **Tool:** Figma agent `get_variable_defs` and `get_design_context` on the Running screen.
- **Why:** to take exact tokens and component structure from the design instead of eyeballing a
  screenshot.
- **Time:** ~5 min. **Rating:** 4/5.
- **Output:** the variables became Tailwind `@theme` tokens in `frontend/src/styles.css` with the
  same names (`color/bg/raised` → `bg-raised`; tints → `bg-ok/16`). The returned React + Tailwind
  matched our stack, so components kept the design's structure; each file names its Figma node
  (for example `ToolCard (2:93)`). Icons were downloaded as SVG assets. The 3D scene placeholder
  in the design was replaced by the real three.js canvas on purpose.

## 4. Code generation: 2026-10-24

- **Tools:** Claude Code + the handoff above; the browser pane for checking.
- **Why:** the design defined what to build; the backend's event stream defined the data.
- **Time:** ~40 min. **Rating:** 4/5.
- **Output:** `frontend/` (React, @react-three/fiber viewer, Zustand store fed by SSE in
  per-frame batches), served by FastAPI at `/`. Tested end to end with a scripted fake agent so
  no API credits were spent.
- **Friction:** generated CAD first rendered black (the API's GLBs have no normals), fixed with
  flat shading. The camera didn't re-frame in side-by-side mode. Vite 6.3.5 had a Windows dev
  server advisory, so we bumped it to 6.4.5.

## 4b. Iterating in code (after the handoff)

- **Tools:** Claude Code; the Figma components stayed the reference for each new panel.
- **What we added:** the agent's slice planes and camera mirrored in 3D, a live Model tab (params
  with evidence, features, history), a deviation heatmap, Stop / Download / units, a pre-started
  CAD engine (first prompt ready in ~2 s instead of ~13 s), and replay of recorded runs.
- **Rating:** 4/5. Each feature was checked against a scripted fake agent before a real run.
- **Friction:** the first real run said "Not logged in" because the server was started from
  `frontend/` and missed the root `.env`. The CAD server only reads files under its working
  directory, which broke the first version of the pre-started engine. Both were fixed and are
  now covered by tests.
