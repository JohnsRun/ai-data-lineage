import { CanvasError } from "@github/copilot-sdk/extension";

const recommendations = {
  in_editor: {
    harness: "Copilot",
    reason: "A good default for interactive work in VS Code, with the least setup and the closest integration with the Copilot experience.",
    caveat: "The host and organization policy determine which models, tools, and execution capabilities are available.",
  },
  local_control: {
    harness: "Local",
    reason: "Choose this when the agent needs to run tools against files and processes on your own machine.",
    caveat: "Local tool execution does not guarantee local model inference; prompts or code context may still be sent to a model provider.",
  },
  background_work: {
    harness: "Cloud",
    reason: "Choose this for work that should run remotely or continue without keeping your local VS Code session active.",
    caveat: "Check repository access, data handling, network permissions, and any cloud usage limits before starting.",
  },
  data_sensitivity: {
    harness: "Local",
    reason: "Local execution can keep repository operations on your machine, which may help when local file access is a priority.",
    caveat: "This is not a privacy guarantee: verify where model inference happens and whether prompts, code, logs, or artifacts leave the machine.",
  },
};

export function recommendHarness(input) {
  const validGoals = Object.keys(recommendations);
  if (
    input === null ||
    typeof input !== "object" ||
    Array.isArray(input) ||
    Object.keys(input).length !== 1 ||
    !validGoals.includes(input.goal)
  ) {
    throw new CanvasError(
      "invalid_action_input",
      `Provide exactly one goal: ${validGoals.join(", ")}.`,
    );
  }

  return recommendations[input.goal];
}
