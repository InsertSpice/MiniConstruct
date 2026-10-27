// Canonical Aspect Ratio choices exposed by H3 Setup. Custom Workspace values
// remain valid sources, but are intentionally outside this finite mapping UI.
export const H3_ASPECT_RATIO_CHOICES = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"];

// Same strict leading-token boundary as backend enum discovery/resolution.
export function aspectRatioToken(value) {
  return /^([0-9]+:[0-9]+)(?=$|\s|\()/.exec(value)?.[1] || null;
}
