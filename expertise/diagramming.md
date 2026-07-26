# How to design explanatory diagrams

Use a diagram when relationships, sequence, branching, containment, state change, or repeated mappings become materially easier to understand than they are in prose or a table. A diagram is a teaching surface, not an inventory of everything in the system.

Start with what the reader should learn, ground the picture in what the system actually does, and write the semantic story before arranging shapes. Keep the editable source and rendering process with the code so the picture can change with the system.

## Contents

- Decide whether a diagram helps
- Set the teaching contract
  - Name the reader outcome
  - Give each view one job
  - Ground every claim
- Write the semantic storyboard
  - List the nouns and relationships
  - Make arrows carry meaning
  - Show paths, loops, gates, and boundaries honestly
  - Remove information that does not teach
- Reveal detail progressively
- Establish a visual grammar
  - Choose the smallest useful form
  - Build hierarchy before decoration
  - Give color one stable meaning
  - Write labels that explain
- Use motion to teach time or flow
- Keep diagrams reproducible
  - Track sources, commands, and outputs
  - Keep theme variants equivalent
- Inspect the rendered artifact
  - Review meaning before appearance
  - Review at the destination size
  - Provide accessible alternatives
  - Make separate social compositions when needed
- Iterate with readers
- Review checklist

## Decide whether a diagram helps

Use prose for a fact, a short explanation, or a simple instruction. Use a table for exact mappings and repeated fields. Draw only when spatial structure makes an important relationship easier to see.

Good candidates include:

- one source or decision feeding several consumers
- three or more dependent steps or state transitions
- a loop whose return path changes future work
- hierarchy, ownership, containment, or deployment boundaries
- parallel paths that share a gate or converge on one result
- an interaction that is difficult to narrate without repeatedly saying “then” or “back to”

Do not draw a diagram only because a document has several sections. If a sentence or small table teaches the same thing faster, use it.

## Set the teaching contract

Write down what the picture must teach before choosing its layout. This gives every box, connector, label, and color a reason to exist.

### Name the reader outcome

Name the intended reader, what they already know, and where they will encounter the diagram. A README hero for a new visitor needs different vocabulary and density from an operations diagram for maintainers.

Complete this sentence:

> After seeing this diagram, the reader can explain, sketch, decide, or predict ______.

Use an observable result. “Understand the architecture” is too broad. “Explain how a signal becomes deployed code and how production findings return as new work” gives the picture a testable job.

Also write what the view should not teach. A high-level flow may omit retry limits, storage formats, and every reviewer role because later views cover them.

### Give each view one job

One diagram should carry one load-bearing lesson. A view can contain supporting detail, but every part should help the same lesson.

When a document teaches several related details, start with the smallest system picture that orients the reader. Follow it with narrower views that answer one question each. Do not concatenate the detailed views into a dense overview; distill them.

### Ground every claim

A visual claim can be stronger than a sentence because placement and arrows imply behavior without stating it. Check the picture against authoritative code, behavior specifications, operating documentation, or observed output.

Record the constraints that matter before drawing:

- Which paths always run, and which are optional?
- Which transitions happen automatically, and which need authorization?
- Where can a person be pulled into the flow?
- Which boundaries describe ownership, execution, trust, or deployment?
- Which loops exist today, and which describe an intended future state?
- What implication would be misleading even if every individual label were technically true?

Treat these as the diagram's truth constraints. Use them when reviewing every relationship the layout implies.

When a desired behavior has not shipped, label it, omit it, or change the system before presenting it as current behavior. Do not let an arrow quietly turn a possibility into a guarantee.

## Write the semantic storyboard

Draft the diagram as text before laying it out. The storyboard separates the explanation from the geometry and makes missing relationships easier to find.

### List the nouns and relationships

Start with domain nouns: actors, artifacts, states, queues, environments, and outputs. Use the same names as the product and surrounding prose.

Then write each relationship as a short statement:

```text
Production logs become Observations.
Conversation shapes an Observation into a Work Item.
Review findings return to the Writer.
Expertise informs future Writers and Reviewers.
```

This list is the candidate visual. It is not yet the final one. Group related statements, find the central spine, and decide which relationships belong in this view.

### Make arrows carry meaning

An arrow should communicate direction, transfer, causality, control, or return. If reversing or removing it would not change the meaning, it may be decoration.

Label a connector when its meaning is not obvious from the adjacent nouns. Place the label near the source of the transition so readers encounter the action before the destination. Use active phrases such as `schedule work`, `request a decision`, `return findings`, or `deploy code`.

Avoid labels that repeat the boxes without explaining the relationship. An arrow from `Writer` to `Reviewer` labeled `review` adds little. A return arrow labeled `findings to revise` explains why the flow goes backward.

### Show paths, loops, gates, and boundaries honestly

Use direction to distinguish a request from a response. If machinery pulls in a person and the person supplies context, draw two arrows rather than a neutral line.

Draw a loop when the return path is part of the lesson. Keep local correction loops visually close to the work they revise. Use a longer return path for information that affects a later cycle.

Show a gate as a state transition or decision point, not as an unlabeled gap. Distinguish “ready for review,” “approved,” “merged,” and “deployed” when those states have different authority.

Use containment only when the boundary has meaning. A muted region can show work that runs in a local sandbox or on remote machines. Do not enclose a group merely to fill space.

### Remove information that does not teach

Inventory every visible element and ask why it is present:

- Does this text teach something the shapes cannot?
- Does this connector show a real relationship?
- Does this label remove ambiguity?
- Does this border describe a boundary?
- Does this color carry a stable meaning?
- Does this icon help recognition at the final size?

Remove an element when its only defense is that the underlying system contains it. The picture should contain the minimum information needed for its teaching job.

## Reveal detail progressively

Let readers build one mental model instead of replacing it on every page. Keep the central nouns, directions, and visual meanings stable while later diagrams expose more detail.

A useful sequence is:

1. Show what enters, what comes out, and what returns.
2. Expand one stage into its internal flow.
3. Explain gates, optional paths, and failure loops.
4. Show deployment, operations, or storage boundaries when the reader needs them.

Each detailed view should feel like a zoom into the orienting picture. Use the same term for the same concept. Preserve left-to-right or top-to-bottom direction unless the new view has a reason to change it.

Do not force every detail into the first picture. Orientation comes from a clear spine and visible feedback, not from seeing every component at once.

## Establish a visual grammar

Define a small set of visual rules, then apply them consistently across the diagram set. Readers should learn the grammar from the first view and reuse it without a legend on every later view.

### Choose the smallest useful form

Match the form to the relationship:

- Use a flow for sequence and return paths.
- Use a timeline for state changing across events.
- Use a tree for hierarchy and ownership.
- Use lanes for work divided by actor, resource, or authority.
- Use containment for runtime, trust, or deployment boundaries.
- Use a table when the main relationship is exact comparison rather than space or time.

Prefer straight paths and right-angle elbows for technical flows. Use a snake layout only when the flow must wrap; preserve reading order at each turn.

### Build hierarchy before decoration

Set page size, margins, rows, columns, and a baseline grid before refining icons or colors. Align boxes to shared edges and centers. Give headings, diagram content, and captions a repeatable vertical rhythm.

Use size and placement to show importance. The main flow should be visible before a reader can read the body copy. Supporting descriptions should not compete with titles or arrows.

Test the picture without icons. If the structure stops making sense, the layout or labels are not doing enough work.

### Give color one stable meaning

Use color to encode one important class of information: for example, information entering or returning to the system, human authority, or the active path through a process. Keep that meaning stable across the set.

Use an accent sparingly. If every box, arrow, and heading is accented, nothing is emphasized. Do not rely on color alone; line style, direction, text, or shape should preserve the distinction for readers who cannot perceive the color.

Treat light and dark palettes as two renderings of one diagram. Geometry, copy, hierarchy, and semantics remain identical. Only the color tokens change.

### Write labels that explain

Use domain nouns for boxes and active phrases for transitions. Keep titles short enough to scan. Write descriptions as sentences when they explain behavior.

Avoid vague interface labels such as `chat with your agent` or abstract phrases such as `durable question` when the reader needs to know what happens. Prefer concrete actions such as `Shape what to build`, `Unblock agents when they are stuck`, and `Inspect and approve the result`.

Do not write every label in uppercase. Reserve uppercase or small caps for short categorical labels when the visual system needs them. Sentence case is easier to read for explanatory copy.

## Use motion to teach time or flow

Animate only when motion explains sequence, concurrency, direction, waiting, or recurrence. Decorative movement competes with reading.

Choose the motion model deliberately:

- One moving token shows a causal sequence.
- Several tokens show concurrent work or a system that continuously processes signals.
- A pulse can show a state change without implying that an object travels.

Keep the background fixed so the reader can orient while motion runs. Move tokens along connectors rather than across unrelated space. Use the same color meaning as the static diagram.

Make a repeating animation continuous. For a loop with duration `T`, sample frames over `[0, T)`; do not export a frame at `T` that duplicates the first frame and creates a pause. Every periodic track should complete a whole number of cycles during that interval. Fade a token near a path endpoint when it re-enters elsewhere. Across the last-to-first boundary, position, opacity, and easing should advance by one ordinary frame interval.

The first frame and a representative still must remain useful. Never put essential meaning only in movement. Do not use flashing that could cross accessibility thresholds.

If movement starts automatically and lasts more than five seconds alongside other content, the destination must let the reader pause, stop, or hide it. When the surface cannot provide that control, as with an autoplaying README GIF, use a static presentation by default or make the animation stop within five seconds. Do not assume that the client will honor a reduced-motion preference. See [WCAG 2.2: Pause, Stop, Hide](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html).

## Keep diagrams reproducible

Treat a diagram like maintained source, not a pasted screenshot.

### Track sources, commands, and outputs

Keep these together in the repository:

- editable sources, such as SVG, diagram code, or a supported design format
- a manifest or clear naming convention that maps sources to outputs
- one documented render command
- required fonts and renderer versions, with a lock, version check, or other repeatable validation
- generated assets when the destination cannot render the source directly
- a freshness check that detects when published outputs differ from their sources

Do not leave the only editable source in an ignored scratch directory. Do not depend on a sequence of shell commands remembered by one author.

For example, a repository might keep editable SVGs in `documentation/diagrams/sources/`, publish PNGs in `documentation/assets/`, and provide `scripts/render-diagrams --check`. The render command should fail clearly when a required font or renderer version is unavailable; `--check` should render into a temporary directory and compare the result with the published files.

Prefer vector or code-native source for technical diagrams. Rasterize at the dimensions required by the destination. Render from one source of truth rather than manually reproducing light and dark layouts in different tools.

When paired theme sources are necessary, keep their geometry and text identical and check that only approved palette values differ.

### Keep theme variants equivalent

Review both themes after every content or geometry change. A label that fits on a dark render can still clip on a light render if fonts or rendering paths differ.

Use a solid background when the destination may place transparent media on unknown colors. Choose accent colors separately when one value does not have enough contrast in both themes, but preserve the accent's semantic role.

## Inspect the rendered artifact

Source review cannot replace render review. Inspect what the reader will see.

### Review meaning before appearance

First ask a fresh reader to narrate the diagram. Compare that narration with the teaching contract and truth constraints. Fix false implications, missing gates, unclear direction, and vocabulary drift before polishing spacing.

Then inspect every box, connector, label, color, icon, and boundary. Keep each element only when its purpose is clear.

### Review at the destination size

Open the artifact at the width where it will appear. Source resolution does not make small text legible after the platform scales the image down.

Check:

- titles can be read before zooming
- body copy remains useful at normal page width
- arrowheads and dashed lines survive downscaling
- labels do not collide with connectors or boxes
- line spacing and margins follow a consistent rhythm
- light and dark themes preserve hierarchy and contrast
- normal text reaches a 4.5:1 contrast ratio and meaningful graphical objects reach 3:1 against adjacent colors
- animation remains calm enough to read around it

### Provide accessible alternatives

Write alt text that explains the diagram's lesson and meaningful relationships. Do not list visual decoration. Surrounding prose should teach the same essential model so the diagram helps comprehension without becoming the only source of information.

Avoid encoding distinctions only by color, motion, or position. Pair them with labels, line styles, shapes, or prose. Use [WCAG's text contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html) and [non-text contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) when checking colors.

### Make separate social compositions when needed

A documentation diagram and a social preview have different jobs. A detailed README image may have ample pixel resolution but become unreadable in a feed or fit poorly into a wide link card.

Create a derivative composition for the social surface. Keep the same lesson and visual grammar, but remove body copy and secondary paths. Do not solve a density problem by padding the detailed image into a wider canvas or by increasing its source resolution.

## Iterate with readers

Work one diagram at a time. Review the semantic storyboard before layout, then review one class of visual element at a time: text, connectors, hierarchy, color, animation, and final-size rendering.

Render after each meaningful change. Compare versions at the real destination rather than judging only in an editor. Record accepted sources and outputs before moving to the next view.

When feedback says a label is vague or a loop is unclear, revisit the teaching contract and relationship statement. A cosmetic adjustment cannot fix a missing idea.

## Review checklist

Before publishing a diagram, check:

1. Does a diagram make this relationship clearer than prose or a table?
2. Have you named the reader, their starting knowledge, the destination, and the outcome in one sentence?
3. Does this view have one teaching job?
4. Does every implied path match the current system or carry an explicit status?
5. Can a reader identify the main flow before reading body copy?
6. Does every arrow communicate a real direction, transfer, cause, or return?
7. Do labels use the system's vocabulary and active phrases?
8. Do loops, gates, optional paths, and boundaries have distinct meanings?
9. Does each visual choice keep the same meaning across the diagram set?
10. Do light and dark variants preserve the same geometry and copy?
11. Does animation explain behavior, cross its loop boundary continuously, remain optional, and give readers control when it lasts more than five seconds?
12. Is the picture legible at its actual destination size?
13. Does alt text and surrounding prose preserve the essential lesson?
14. Are editable sources, render commands, dependencies, and published outputs tracked?
15. Can an automated or repeatable check detect a stale render?
