// Feedback only: the application still owns eligibility and saved Project authority.
export function storyGenerationFeedback({ visibleScenePlan = "", savedScenePlan = "" }) {
  if (!visibleScenePlan.trim() && !savedScenePlan.trim())
    return "This Generation has no Scene Plan yet. Build/apply Story planning (Build Initial Range for a new Story), or write a Scene Plan, save the Project, then Generate from Story.";
  return "Save the current Scene Plan before Generate from Story.";
}
