# Hyperflow native-only public fixture compatibility

This report describes synthetic regression fixtures, not a user's local workflow
export. The exact original local bundle is preserved separately in a Git-ignored
private archive. Public JSON contains no local inspection evidence, personal
Profile identifiers, imported Reference filenames or private LoRA inventory.

## Architecture preserved

The synthetic initial graph has 28 nodes. The native-only successor has 25. Model
and native processing remain production-shaped:

```text
1 UNET → 58 example LoRA stack → 34 attention backend → 37 sparse attention
       → 27 chunked feed-forward → 47 HyperFlow → 17 preview wrapper → 11/model
2 CLIP + 4 video VAE + prompt + duration-derived length → 6 H3 conditioning
50 LoadImage → 51 image scaler → 52 image size → 6 width,height   [initial only]
51 → 6/first_frame                                             [initial only]
6:0 → 55 extender conditioning → 11 guider → 12 sampler → 56 AV Save
6:1 → 55/latent AND 12/latent_image
59 AV Load:0 → 55/prev_latent
8 noise + 9 Euler sampler + 47:1 sigmas → 12
56:0 → 13 video decode + 14 audio decode → 57 overlap trim
57:0 video + 57:1 audio → 15 CreateVideo → 16 SaveVideo           [successor]
```

The initial fixture intentionally retains the original architectural audio-bypass
shape `14:0 → 15/audio`, while the successor routes trimmed audio through `57:1`.
The initial metadata repair changes only Picture presence and seed mapping; its
graph is identical to the Optional-Picture fixture.

All model/codec filenames are clearly neutral examples. The example LoRA stack has
two entries with neutral filenames and exposes enable/strength controls for the
first. Both initial and successor retain identical model dependency declarations,
including HyperFlow, attention parameters and preview-wrapper VAE dependencies.
No application architecture or production compatibility checks were changed.

## Actual example mappings

| Semantic | Target | Authority |
| --- | --- | --- |
| Generated prompt | `6/prompt` | selected generated/native-only H3 prompt |
| Duration | `35/value` | current Generation duration; frame expression `36` retained |
| Renderer seed | `8/noise_seed` | imported value 123456789 in Prepare; selected seed resolved at Send |
| Native enable | `55/mode` | initial enabled `auto`, successor `always_extend`; disabled `disabled` |
| Context frames | `55/context_length` | default 22, allowed grid 5 + 17n through 39 |
| Save key | `56/filename_prefix` | fresh attempt namespace plus `/clip` |
| Save slot | `56/clip_index` | current canonical Generation slot |
| Load key | `59/latent_path` | exact accepted predecessor namespace |
| Load slot | `59/clip_index` | predecessor's recorded Save slot |
| Source link | `55/prev_latent` | exactly `59:0` |

Both synthetic Profiles use `example-hyperflow-native-av-v1`. The successor declares
`pictureFreeI2va` version 1, renderer contract `minimax_h3_extender_av_v1`, conditioning
node `6`, I2VA-only compatibility, no media bindings, no tail reference and no
endpoint anchor permission. No fps/extension mapping or audio control was invented.

## Recalculated model/codec identity

Unmodified production `miniconstruct.i2va_contract.state_identity` calculates:

| Synthetic configuration | Identity |
| --- | --- |
| Initial Optional-Picture fixture | `ad445d5c57e64f3b2440efc2ba55e747400594ab0ecd4d161298d91c7e50cec0` |
| Initial Required-Picture repair | Same identity |
| Successor fixture | Same identity |
| Effective prepared G1/G2/G3 with compatible controls | Same identity |

This identity was recalculated after sanitization. It is not the private workflow's
identity and does not assert compatibility with any real installed model stack.
Typed graph fingerprints were also recalculated. Full Profile identities differ
because initial and successor are independent examples.

Changing an effective LoRA control, the model, either video/audio codec declaration,
or the native format key must fail successor preparation/Send before transport.
The tests exercise these failures through production paths. Nothing bypasses,
weakens or forges the model compatibility check.

## Independent canvas

The public Picture is generated in memory at **1200×800**. The retained example
scaler uses 0.80 × 1024 × 1024 target pixels and 32-pixel steps:

```text
s = sqrt(0.8 * 1024 * 1024 / (1200 * 800))
width  = round(1200 * s / 32) * 32 = 1120
height = round( 800 * s / 32) * 32 = 736
```

The successor uses independent scalar **1120×736**, with no first-frame, scaling
branch or media loader. Model identity deliberately excludes canvas. For a real
accepted state, separately inspect video tensor spatial shape and effective canvas;
an equal model identity cannot prove tensor geometry compatibility.

## Offline coverage and limitations

The self-contained public test uses only the six public JSON files and synthetic
Picture pixels. Its environment redirects configuration/Project writes to temporary
directories and rejects live HTTP. Tests preserve:

- The Optional-Picture failure caused by retained image-dependent dimensions.
- A Required-Picture metadata repair and valid image-conditioned G1 Save-only path.
- Typed Profile/ProfileCreate models, configuration API draft validation and native
  mapping validation; exactly preserved model/sampler/native topology.
- G2 with no Picture and G3 from the exact accepted G2 state, ignoring a newer
  unaccepted reroll.
- Positive directional Load/Save slots, fresh Save namespaces, exact accepted
  predecessor provenance, fixed renderer seed and duration authority.
- Matching production identities and rejection of incompatible model/codec/format
  declarations and effective LoRA controls.
- No successor imported media, independent scalar dimensions and exact mocked
  `/prompt` equality after normal send-time seed and fresh Save-attempt resolution.

No private ignored file, real Profile ID, machine-local media path, installed
custom-node checkout or model weights are used by the tests. Public fixtures include
an automated check for absolute local/home/network paths and a bounded neutral
LoRA inventory.

An additional independence check copied only the six public JSON fixtures, the
bundle test and its fixture definitions into a temporary tree with no private
archive. All seven bundle tests passed there using the repository's production
code and existing public test helpers.

Offline validation does not certify installed custom-node versions, native tensor
contents, runtime memory/model loading, output codec support, direct native Guide
reuse, trim behavior, audio continuity or rendered quality. Depending on the
installed extender, incompatible native Guide geometry can lead to internal
RGB/VAE conversion despite `always_extend`; test your renderer explicitly.
The 12-second example requests 294 raw frames on the 5+17n grid. With 22-frame head
overlap trim it may deliver 272 frames, about 11.33 seconds. The Profile's copied
audio capability flags do not themselves provide a renderer audio on/off mapping.

## Privacy separation

`/.local/private/` is excluded by the repository's `.gitignore`. The original nine
files are retained byte-for-byte with a preservation manifest in that ignored
archive. Ordinary `git add -A` excludes it; explicit force-adding private files
would override Git's ignore protection. The original private files were removed
from staging before the public material was rewritten. This does not rewrite
existing commits or change saved application settings.

Public review material consists of the six neutral JSON files above, this report,
the [setup guide](i2v-hyperflow-native-only-setup.md), the bundle regression test and
the ignore rule. No renderer success is inferred from offline validation.

## Separation validation results

| Check | Result |
| --- | --- |
| Focused bundle/native-only/initial/imported-media tests | 88 passed |
| Public-only temporary fixture tree | 7 bundle tests passed; no private files copied |
| Full Python suite | 2771 passed, 3 skipped; 324.64 seconds |
| Full JavaScript suite | 682 passed, no failures or skips |
| `git diff --check` and all nine untracked-file whitespace checks | Passed |
| Original private files | All nine byte-identical to preservation manifest |
| Ordinary `git add --dry-run -A` | Only ignore rule and nine sanitized files listed; archive excluded |
| Git-visible private-content scan and staged-file audit | Passed; staging area empty |

The Python run retained two environment warnings: the existing Starlette/httpx
deprecation and unavailable write access to the existing pytest cache. No dependency
or permission changes were made. Saved configuration and inspected renderer source
hashes remain unchanged. All changes remain uncommitted: one modified ignore file,
nine untracked sanitized public files, and the ignored private archive.
