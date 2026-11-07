# Advanced authoring and references

## Modes and references

H3 supports 4–15 second clips. T2VA is text-driven; I2VA requires a first-frame
Picture; FL2VA requires first and last Pictures; L2VA requires a last Picture.
Ref2VA uses semantic references. Pictures, Videos, and Audio are numbered separately.
A Subject represents a scene entity; multiple Pictures can inform the same Subject.
Use roles and Notes to explain how each reference should influence the result.
Identity-only Pictures belong with their Subject definition. A reference sheet
used for identity does not require reproducing its panel layout. Environment
references describe location and layout, not automatically the next camera angle.

Vision-enabled writing uses smaller Picture derivatives without changing execution
images. Ordinary Audio/Video references supply metadata and Notes only. Explicit
Selected Take analysis and Visual Handoff are separate execution tools.

## Controls and dialogue

Camera behaviors can be Avoid, Auto, or Prefer. Tone and performance guide mood
and acting without changing identity or wardrobe. Visual Style Auto uses available
evidence; presets set a treatment; Custom uses your written guidance. Music defaults
Off for new Generations. Seed behavior depends on backend support.

Creative Interpretation governs invention; H3 Prompt Detail governs how fully Main
Prompt generation describes the idea. Story uses Scene Plan Detail instead.
Natural liveliness remains subordinate to dialogue, continuity, and deliberate stillness.

Enter exact speech in Dialogue / Lyrics, separately from scene direction. Sequence
dialogue language defaults to As Written. Other languages use inspectable Spoken
Output translations; resolve stale or failed translations before generating.
See [dialogue language](story-planner.md#sequence-dialogue-language).

## Editing and saved prompts

Generate, edit or Revise Selection, then Validate. Repair Format attempts conservative
corrections and may ask the model to repair remaining structure. It is optional.
Show LLM Instructions displays the assembled text without keys or binary images.
Stop cancels the request, although a model server may take time to stop computation.

Save H3 Prompt updates the loaded saved entry; Save As New creates another. Load
asks before replacing unsaved edits. Generate and Regenerate start new candidates.
Libraries belong to individual Generations and hold up to 100 entries; updates
remain possible at capacity. Deletion does not renumber surviving entries.
Save .txt exports a separate file. Saving does not select a prompt for execution.
Use for execution explicitly adopts the desired text; after editing, use Update
execution selection to adopt the edited version.

## Projects and reference changes

Save creates a filesystem Project. Normal Save updates the active Generation.
New Generations start blank; Duplicate copies authoring rather than render history.
Full copy Save As retains history. Fresh working copy keeps authoring and references
while starting new execution history. Clear generated state requires confirmation
and affects MiniConstruct-owned files, not external ComfyUI outputs. See
[copying and clearing](execution-orchestration.md#fresh-working-copy-and-clear-generated-state).

Saved Project reference changes save separately from unrelated authoring edits.
Replace Picture preserves role, Subject bindings, and Notes. Reference changes
invalidate execution selection/preparation; select the prompt again before Prepare.
Historical renders and saved prompt text remain available. See
[reference authority](execution-orchestration.md#reference-asset-authority).

Portable exports describe the active Workspace, not a complete Sequence archive.
They exclude keys and raw Audio/Video. Reattach missing original media and back up
the complete filesystem Project directory. Legacy browser Projects can be imported
explicitly without deleting their originals.

## Continuity and sequences

Sequence Context describes durable facts; Incoming State and Intended Outgoing
State describe a Generation's planned transition. Relationship to Previous and
Camera / Shot Intent guide continuity and framing. Planned state is separate from
an actual Selected Take or its explicit observed-state analysis.

Infer from Previous and Revise Plan propose changes for review. Story Planner
proposals require Apply Draft. See [Story Planner](story-planner.md) for Subjects,
Character Profiles, Story Memory, planning styles, shot guidance, and scoped revision.

Tail continuation uses a previous clip's ending as bounded reference evidence;
it does not guarantee seamless native overlap. Consult
[Visual Handoff](execution-orchestration.md#visual-handoff-continuity-20-phase-d)
and [native continuation](i2va-native-continuation.md). Selecting a Take does not
analyze it; inspecting a Take does not select it.
