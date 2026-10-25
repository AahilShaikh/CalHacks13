# Aida UI redesign — plan for review

Goal: a light, focused, professional CAD studio where the agent is the main tool. Fewer toolbars than
Onshape / Fusion 360; the model is the hero, the agent is always one sentence away.

Decisions so far: workspaces persist on disk · agent replies show in a card above the prompt pill ·
References sit in the left panel under the explorer · neutral grays with a Fusion-style orange accent.

---

## 1. Screens

### Home (`/`)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ◆ Aida                                                   [+ New workspace]│
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│        ╭────────────────────────────────────────────────────────╮        │
│        │ Describe a part, or drop scans to start…            ➤ │        │
│        ╰────────────────────────────────────────────────────────╯        │
│                                                                          │
│  Workspaces                                              Sort: Recent ▾  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐             │
│  │ thumbnail  │ │ thumbnail  │ │ thumbnail  │ │ thumbnail  │             │
│  │ Bracket v2 │ │ Gearbox    │ │ Hinge      │ │ …          │             │
│  │ 3 parts·2h │ │ 5 parts·1d │ │ 1 part·3d  │ │            │             │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘             │
│                                                                          │
│  Recorded runs (replays)                                                 │
│  ─ "Rebuild the bracket scan…"   12 revisions · 4m 10s           ▶       │
└──────────────────────────────────────────────────────────────────────────┘
```

- Typing in the home pill (or dropping meshes on it) creates a workspace, adds the files, sends the
  prompt, and opens the workspace. "New workspace" opens an empty one.
- Card menu: Rename, Delete (asks to confirm). Thumbnail is the last viewer frame.
- Empty state: just the pill and a short line on what Aida does.

### Workspace (`/w/<id>`)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ◆  Home / Bracket v2 ✎        ● Building · add_feature          mm  ⤓ Export │
├───────────────┬──────────────────────────────────────────────┬───────────────┤
│ EXPLORER      │                                              │ STEPS         │
│ ▾ ▣ Assembly  │                                              │ You: "make the│
│   ▾ ◫ bracket │            3D viewer (full bleed,            │  hole 8 mm"   │
│     ▸ Params 7│            light background, grid)           │ ◉ Slice mesh  │
│     ▸ Features│                                              │   z = 12  0.4s│
│     ▸ History │                                 ┌──────────┐ │ ◉ Add feature │
│   ▸ ◫ pin ×4  │                                 │ p95 0.12 │ │   hole   1.2s │
│   ▸ ◫ base    │                                 │ ▁▂▃▅ ✓   │ │ ◉ Compare     │
│               │                                 └──────────┘ │   p95 0.12 ✓  │
│ REFERENCES    │   ╭ Aida ─────────────────────────────╮      │ ◌ Export …    │
│ ┌ drop files ┐│   │ Hole is 8 mm and centred. Want …  │      │               │
│ └────────────┘│   ╰───────────────────────────────────╯      │               │
│ ▤ bracket.stl │  ╭──────────────────────────────────────╮    │               │
│ ▤ photo1.jpg  │  │ Ask Aida to change or check…    ■  ➤ │    │               │
│ ⟳ Scan (off)  │  ╰──────────────────────────────────────╯    │               │
├───────────────┴──── [overlay|side|heatmap]  ◀ rev 7 ▶  follow ┴───────────────┤
```

**Left: Explorer** (like Photoshop layers / a file tree)
- Root is the assembly; children are parts (with instance count, e.g. `pin ×4`, and variants).
- Expand a part → Parameters, Features, History. Parameters are the deepest level: name, value with
  units, and a small dot for where it came from (measured / estimated / assumed). Click a parameter
  to see its evidence. This replaces the hidden "Model" tab.
- Click a part to select it (viewer highlights it, steps and accuracy follow it). Eye icon hides or
  shows a part in the viewer. A small marker shows which part the agent is working on.
- Values that just changed flash, as today.

**Left, below: References**
- Drop zone + list of everything the agent can look at: meshes (each one becomes a part, as today)
  and photos. Each row shows type, size, which part it belongs to, and whether it was uploaded or
  scanned.
- Units selector for meshes lives here (it describes the files, not the chat).
- "Scan turntable" row appears but is disabled with "Turntable not connected": `turntable.py` is a
  stub and the agent has no scan tool yet. When that lands, scans show up here with "Scanned by Aida".
- Replaces the Attach button.

**Center: Viewer**
- Full bleed, light background, subtle grid. Floating controls restyled: view mode, revision
  scrubber, follow-camera.
- Accuracy moves from the right panel into a small floating card (p95, verdict, sparkline); click to
  expand the full chart.

**Bottom center: Prompt pill**
- Floating pill, ~640px wide, grows to a few lines. Enter sends, Shift+Enter new line, Stop inside
  the pill while the agent works. Dropping files on it adds them to References.
- While the agent works, the pill's border shows a slow orange sweep: the visible sign that the
  agent is active (respects reduced motion).
- **Reply card** above the pill: the agent's latest message, dismissible; "Earlier replies" opens
  the conversation history in a popover. Questions from the agent stay pinned until answered.

**Right: Steps**
- A compact list of tool calls, grouped under each prompt you sent. Each row: phase icon
  (inspect / build / verify), a readable name ("Slice mesh", not `mesh_slice`), the key argument,
  time, status.
- Click a row to expand: result, render thumbnail, code. Rows with a 3D view still replay in the
  viewer (existing behaviour). Panel can collapse to a thin rail.

**Top bar**: Home link, editable workspace name, agent status chip, units, Export (STEP / STL /
model.py). Cost and elapsed time move into a hover on the status chip.

---

## 2. Design language

- **Color**: white surfaces on a very light gray canvas (`#f6f6f7`), hairline borders (`#e4e4e7`),
  near-black text (`#18181b`), secondary text `#52525b`. One accent: orange `#f26b1d` for fills
  (primary button, active node, agent presence), darker `#c2410c` when orange is used as text so it
  passes contrast. Status colors only where they mean something: green pass, amber estimate, red fail.
- **Phases** stop being three loud colors; they become small monochrome icons, with orange only for
  the running step.
- **Type**: Inter for UI (13px base, 11px labels), JetBrains Mono only for numbers, params and code.
  Tabular numbers everywhere values are compared.
- **Shape**: 6–8px radius on panels and cards, full pill for the prompt bar; soft single-layer
  shadows only on floating things (pill, reply card, viewer overlays).
- **Icons**: add `lucide-react` for one consistent icon set (replaces the four custom SVGs).
- **Viewer**: light background; CAD parts in a neutral steel gray, reference scans translucent,
  selected part outlined in orange, heatmap colors kept.
- **Motion**: short (120–200ms) and functional only: expand/collapse, new step slides in, agent sweep.

---

## 3. Backend: persistent workspaces

Each session already writes most of what's needed to its folder in `artifacts/`: `workspace.json`
(parts + references), `parts/<name>/model.json` (each part's full parametric state), and the event
log used by replays. The work is to read it back.

1. **Workspace metadata**: `meta.json` per folder (name, created, updated, thumbnail).
2. **Routes** (`/api/v1/workspaces`): list, create, rename, delete (moves the folder to a trash
   folder rather than erasing it), upload thumbnail, and **open**.
3. **Open = rebuild**: start a CAD engine on the existing folder, load each part's `model.json`
   (new `ParametricModel.load`), rebuild the parts and assembly, then re-send the saved event log
   so the UI shows the same steps, revisions and replies as before.
4. **Stop closing on tab close**: the CAD engine is freed after an idle timeout instead (no viewers
   for N minutes); the workspace stays on disk and reopens on demand.
5. **Agent memory**: on reopen, resume the Claude conversation if the Agent SDK's resume works with
   our setup (to verify); otherwise start fresh with a short summary of the workspace (parts,
   parameters, last verdicts) as context.
6. **References list**: include references (meshes + photos, with source) in the workspace snapshot
   so the References panel has one source of truth.
7. Tests for load/rebuild, list/rename/delete, and reopening a workspace with an assembly.

The existing session routes and replays keep working.

---

## 4. Order of work

1. **Tokens + light theme**: new `@theme` tokens, base components (button, icon button, badge,
   panel, tree row), viewer colors.
2. **Workspace shell**: top bar, three-column layout, prompt pill, reply card, steps panel.
3. **Explorer + References**: tree from `workspace_state`, part visibility and selection in the
   viewer, References panel and drop handling.
4. **Backend persistence**: section 3, with tests.
5. **Home screen + routing**: small history-based router (`/`, `/w/<id>`, `/replay/<id>`; old
   `?replay=` links still work), workspace grid, home pill.
6. **States and polish**: empty / loading / reconnecting / failed states, copy pass, keyboard and
   screen-reader pass on the tree and pill, typecheck + build, and a click-through of every screen
   in the browser using a recorded replay and a live session.

Intent skills used along the way: `wireframe` (lock the layout before styling), `organize` (explorer
tree), `articulate` (labels and messages), `fortify` (empty / error / loading states), and
`evaluate` + `include` for a final heuristic and accessibility audit.

---

## 5. Open questions

1. **Part visibility toggles** (eye icon per part): planned; drop if you'd rather keep it minimal.
2. **Delete**: moves to a trash folder on disk. OK, or do you want real deletion?
3. **Accuracy**: floating card in the viewer (planned) vs. a section in the explorer per part.
4. **Figma**: there's an existing "Aida CAD Studio UI" Figma file. Update it to the new design
   first, or go straight to code (planned)?
5. **Branch**: this is large; do it on a new branch off `workspaces` (planned) in a few commits,
   one per step above.
