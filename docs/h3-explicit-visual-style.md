# Reference-informed explicit Visual Style

The explicit-preset compiler previously supplied a short medium declaration, then either Style / Appearance observations or Subject observations. Its examples skewed toward 2D character art for every preset. Environment-only and general references had little useful guidance, first-frame appearance was omitted, and conversion between source and target media was underspecified. Auto already examined relevant roles independently and distinguished inference from a fixed medium.

`creative_controls.py` now shares Auto's role-scoped visual-evidence instructions with explicit presets. Auto's existing policy and output remain unchanged. Explicit presets keep medium/treatment authority, with medium-appropriate observation candidates rather than asserted traits. Notes and explicit user qualifications outrank speculative appearance readings within existing control authority. Custom is unchanged.

Style / Appearance Pictures are primary overall rendering evidence within the selected medium. Subject / Identity evidence is limited by focus and visible content to character, face, costume and relevant appearance; Environment evidence covers background art, atmosphere and lighting, never character eye design. General Pictures provide supporting evidence. First-frame appearance can contribute without granting permission to restyle an anchored frame or changing required alignment. Other structural roles are not promoted to style authority. Role and user intent resolve mixed references; contradictory traits are not averaged. Style analysis grants no additional composition authority and does not turn camera angle, pose or transient expression into global art direction.

On a medium mismatch, preserve transferable identity, outfit, color, design and scene semantics under the selected presentation. Observable appearance is preferred over invented studios, artists, film stocks, lenses, engines, shader techniques or production history. Without vision (including unknown capability), use user-authored Notes, descriptions, Reference Labels and explicit instructions only. Filenames and reference-sheet layout are not appearance evidence. With no useful evidence, retain concise preset guidance.

The writer places a few supported traits naturally in the opening of Shot 1, within the official structure and without crowding out temporal directing. Production generation, Story, selective revision and structural repair share this compilation. Revision's immutable selection boundaries and override authority, repair's minimal-change authority, image derivatives, numbering, context accounting, H3 modes, continuity and execution are unchanged.

## Optional controlled real-model evaluation (not run)

The opt-in `h3-explicit-visual-style` suite uses the same existing illustrated reference sheet and two-shot scene for Auto, explicit anime and explicit live action. Only the preset differs across its three cases. Identity Fidelity is Auto so it does not independently demand a feature inventory. No case claims that deterministic checks prove prose quality. Fixture loading and image routing can be verified offline with `tests/test_explicit_visual_style.py`.

For a before/after comparison, use baseline commit `a66e0bec1a3d8f7c501fbd89cd64dee8b0dfc28d` in a separate checkout and the modified working tree for the candidate. Leave this working tree uncommitted. Use the **same absolute benchmark root from this modified tree** for both runs via `--root`; it supplies identical workspaces, suite definitions, shared image bytes and local profile. Use the same Python environment, vision-enabled model/profile, backend version, temperature, fixed seed, reasoning and output limits. Keep temperature 0.4, seed 1 and one variation, following the harness convention. A provider accepting a seed does not guarantee reproducibility.

After explicit permission for real provider calls, run this once from each source checkout, substituting the same configured vision-enabled profile name:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark --root .\benchmarks run --suite h3-explicit-visual-style --profile <same-vision-profile>
```

Record the printed baseline and candidate run directories, then compare:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark compare <baseline-run-directory> <candidate-run-directory>
```

Verify recorded transmitted-image SHA-256 values, workspaces, settings and model identity match between each baseline/candidate case. Differences in effective writer text should be confined to explicit-style guidance; Auto is the control. Review the recorded raw H3 responses and case review questions for specific supported appearance, coherent conversion, restrained length, reference/section validity and action progression. Keep results local; the suite is excluded from default suites. No provider calls, ComfyUI sends or video renders were used to implement this change.

Additional controlled cases worth evaluating after permission: photo-to-anime conversion; matched 3D and stylized 3D references; watercolor; environment-only anime evidence; mixed Style/Subject/Environment media with explicit Notes; I2VA first-frame preservation; and paired vision-disabled runs with/without useful Notes. These require suitable reviewed fixtures, not guesses from placeholder images.

Offline regressions establish the assembled instruction contract and reference routing. They cannot guarantee which traits a model observes or how well it writes them. First-frame constraints can limit a requested medium conversion; existing frame authority still applies. No extra vision call, image captioning service or execution feature is introduced.
