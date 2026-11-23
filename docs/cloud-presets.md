# Cloud presets

Read a preset from any installed RoleShuttle wheel. Running `roleshuttle preset NAME` prints the complete settings as JSON, suitable for an agent to read or for you to save and adapt. Role and lane lookups return one model assignment.

| Role | Codex | Effort | OpenCode Go |
| --- | --- | --- | --- |
| Assistant | gpt-6-luna | low | deepseek-v4.1-flash |
| Coordinator | gpt-6.2-sol | low | deepseek-v4.1-flash |
| Implementer | gpt-6.2-sol | medium | kimi-k2.7-code |
| Specialist | gpt-6.2-sol | high | deepseek-v4.1-flash |
| Architect | gpt-6.2-astra | medium | gemini-4-argon |
| Reviewer | gpt-6.2-astra | medium | kimi-k3.1-pro |

The Go preset also assigns `kimi-k3.1-pro` to the `architecture` and `critical` task categories. Choose a lane explicitly when the task calls for it. For example, `--role architect` retrieves the agent role while `--lane architecture` retrieves the saved task-category override. The Codex effort settings apply only to Codex.

## OpenRouter Free

This preset captures the selection rule for bounded tasks. Intersect its preferences with the provider's currently available, tool-capable models offering free input and output and carrying the `:free` suffix. Prefer `cohere/north-mini-code:free`, then `nvidia/nemotron-3-ultra-550b-a55b:free`. Order other eligible models by ID. An unavailable favorite can be skipped. If no model qualifies, return the stage to the main chat. There is no paid fallback.

## Choose equivalents on another subscription

Start with the least expensive model in your plan that can complete the role’s check. The main chat can stay on a lightweight, tool-capable model while another worker handles a difficult stage. These are RoleShuttle’s suggestions based on provider descriptions. Pick the version available in your client. The examples have not been benchmarked here.

| Role | Starting level | Claude example | Gemini example |
| --- | --- | --- | --- |
| Assistant | Fast | Claude Haiku | Gemini Flash |
| Coordinator | Fast with reliable tools | Claude Haiku | Gemini Flash |
| Implementer | Coding | Claude Sonnet | Gemini Flash for a small patch |
| Specialist | Targeted reasoning | Claude Sonnet | Gemini Pro |
| Architect | Deep reasoning | Claude Opus | Gemini Pro |
| Reviewer | Independent reasoning | Claude Sonnet for routine review, Opus for critical changes | Gemini Flash for routine review, Pro for critical changes |

The Codex column below follows the packaged role preset, including reasoning effort. Kimi examples marked Go come from the Go assignments above. Other Kimi and Qwen entries are starting points for an executor that supports your subscription. Model names and available modes vary by plan. [Kimi model selection](https://www.kimi.com/en/help/new-user-guide/agentic-chat) · [Qwen plan setup](https://www.alibabacloud.com/help/en/model-studio/qwen-code)

| Role | Codex preset | Kimi example | Qwen family |
| --- | --- | --- | --- |
| Assistant | Luna · low | K2.6 for quick lookups | Flash |
| Coordinator | Sol 6.2 · low | K3 with tool support | Plus with tool support |
| Implementer | Sol 6.2 · medium | K2.7 Code (Go preset) | Plus |
| Specialist | Sol 6.2 · high | K3 | Plus, then Max for unresolved cases |
| Architect | Astra 6.2 · medium | K3.1 Pro (Go architecture lane) | Max |
| Reviewer | Astra 6.2 · medium | K3.1 Pro (Go reviewer) | Plus for routine review, Max for critical changes |

A model earns its place by passing a small trial. Ask a coordinator to dispatch one task and collect the result. Ask an implementer for a patch that passes a project check. Give a reviewer a fresh context and a patch with a known edge case. A subscription with only one model can still use separate contexts for implementation and review. Keep the final acceptance check in the main chat.

See the [worked example](../examples/choose-workers.md) and the role guide in `roleshuttle demo --open`. Use your client’s model picker to confirm availability. Gemini CLI’s chat-level model selector has a separate scope from its subagents. [Claude model selection](https://platform.claude.com/docs/en/docs/about-claude/models/choosing-a-model) · [Gemini CLI model selection](https://geminicli.com/docs/cli/model/)

## Use from a conversation

> Keep my current chat model. Use the Codex preset for implementation and review. Show me the two assignments before sending any project code.

The agent can query both assignments without a provider account. For a Codex text proposal or review it can invoke the [native subscription stage](codex-stage.md). For execution it must check project permissions, model availability, subscription or billing policy, and support for the requested effort in the user's own client or runtime. Collect the actual model and checks with the result. These snapshots describe configured choices and do not establish current access, speed or cost. The built-in Ollama delegate accepts a separate local profile.
