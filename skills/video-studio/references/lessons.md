# Lessons from real productions

Each one cost an iteration. If the user corrects something twice, add it here or turn it into a check.

## Content
- **Borrow craft from an outside reference, not content.** Take the quality and timing of its animation, crispness
  of its boxes, transitions, and the way it combines photos, video, product, animation, and voice. The script,
  narrative structure, and text come from the project's own material (deck, the user's notes). Copying the
  reference's beats with different words is the opposite of the request.
- **Do not invent on-screen text.** Every on-screen phrase is either a verbatim excerpt from that scene's voice or
  its heading in the source material. No taglines invented by the treatment.
- **Every number on screen has a source.** If it has no source, write the sentence without a number. Two figures on
  one card must be mutually consistent and come from the same source.

## Product, objects, and data
- **Show the product from its code,** with real components and demo fixtures. Never start its infrastructure or
  record the screen. Screenshots are for reference only.
- **Show real objects with real photos** (full bleed, cut out, or with graphics over them), never drawn with SVG
  primitives. A tower drawn from triangles looks amateurish.
- **Any depicted phenomenon must be technically correct:** flow directions, units, scales.
- **Use geographic data and images with verified licenses** and record their credit (for example, TRAZO plots under
  CC BY 4.0 and Sentinel-2 Copernicus). Nothing from Google, Esri, or Mapbox, or from noncommercial sources.
- **Vary the demo data:** use many different names and cases, not the same four in every scene.
- **Draw each example series with the real behavior of its variable.** Two series never mirror each other or move
  in perfect lockstep: a specialist immediately sees that they are fabricated. Details in `product-and-data.md`.

## Motion
- **Dynamism does not mean everything moves.** One focus at a time, measured entrances, readable rests, and
  intentional shot changes. The opposite extreme—one entrance followed by seconds of stillness—also fails.
- **Items in a series appear one at a time,** at a constant interval or with monotonic acceleration, or all
  together in one motion. Never in batches tied to words. Implement this with one cascade primitive and a test that
  rejects pauses within a cascade.
- **Motion tells the story.** Every element comes from a meaningful place (its row in the UI, the previous shot)
  and changes state in time with the voice. A successful case: platform content detaches from its own row, rises in
  3D, and changes state in the air.
- **No motion metric is a target or gate** (still frames, events per second, energy): the agent will distort the
  animation to satisfy it. Metrics are useful for finding defects.
- **No one-frame jumps.** A level-of-detail change dissolves over at least 12 frames, and nothing appears instantly
  at full opacity.
- **A transition must read at real speed and show what the voice says at that moment.** If it moves too fast to
  understand, or shows something the voice is not discussing (a wall of charts nobody mentions), redo or remove it.
  Review every cut and fast movement at real speed, not only as isolated frames.
- **Seams between clips: the same shot is identical on both sides of the cut.** Position, scale, opacity, and state
  of every element match between the last frame of one clip and the first of the next. Even a 1.5% entrance scale
  (a "breath" starting at 0.985) reads as a jump; `jumps.py` flags it at the seam.

## Composition and volume
- **Compose every frame on a grid,** with balanced content. No half-empty frames, uneven rows, top-heavy headings,
  or orphaned words in a heading.
- **Use volume to your advantage:** scale contrast (one giant element multiplying into dozens, then hundreds that
  fill the frame), depth through size and opacity, and edge-to-edge fullness in mass shots.
- **Do not flatten what has volume:** organizing the composition does not mean turning a 3D shot into 2D cards.
  Compare each version frame by frame with the previous one.
- **Animated landscapes use 2D views:** draw a field, ground, or sky as a side view (sky, plants in profile, ground
  in layers), not a fake 3D perspective with furrows receding to the horizon. Each copy of multiplied elements
  (plants, curves) looks different.
- **Forbidden:**
  - corner labels and fixed corner logos;
  - centered titles over gradients;
  - everything entering with a fade;
  - frame borders;
  - glows and orbs;
  - decorative particles;
  - empty white cards waiting for content;
  - semi-transparent text as a "pending" state: it reads as a placeholder;
  - icons inside small colored squares.
- **Text over a photo:** use a local veil only behind the text, never darken the entire photo.

## Voice and timing
- **The voice never sounds cut off:** trim with 150 ms before and 250 ms after, use fades, and keep the first and
  last 30 ms of each take below −45 dBFS.
- **No sentence is cut by a shot change,** and every sentence has ≥ 0.4 s of breathing room after it.
- **The voice explains:** there are no long gaps with only UI on screen.
- **The voice tells a story that makes sense outside the client's world,** without cutting what the company does.
  Details in `voice.md`.
- **Mention technology sparingly** and professionally: talk about what the product does.

## Process
- **Before calling anything finished:** concurrency 1 and 4 produce identical frames, with no jumps or flicker, and
  3D-tilted UI is rasterized to a still at @2x before transforming it.
- **Show only work that meets the bar.** The director reviews it at actual size, against the request and the previous
  version, and rejects regressions before showing it to the user. When presenting, state what was rejected and why.
- **Videos shown for review include their audio,** verified with `ffprobe`. Open them in QuickTime.
- **For a subjective quality request:** finish a short segment to final quality first, and complete the rest only
  after approval.
- **One change per agent.** From a list of corrections, give each agent one and iterate until it is closed; assigning
  the whole batch leaves every request half-finished. Parallelize work that shares neither files nor moments (one
  agent per shot, each in its worktree, renders queued behind a lock, and one integrator at the end).
- **Agent briefs contain no commands that trigger permission prompts.** Claude Code asks for approval, even in
  bypass mode, for commands its verifier cannot analyze, and an unattended run then stalls until someone approves.
  The brief says:
  - no `cd` in compound commands: use absolute paths, `git -C`, and `npm --prefix`;
  - no `case … esac`, `{ …; break; }` after `||`, or multiline Python inside `$(...)`: put logic in a script;
  - use `rm` only on a literal path, never one assembled from a variable;
  - no `dangerouslyDisableSandbox` or manual writes in `.git/` or `.claude/`.
  The orchestrator also checks at every heartbeat that each agent's log is advancing: an agent stalled on a
  permission prompt does not report it.
