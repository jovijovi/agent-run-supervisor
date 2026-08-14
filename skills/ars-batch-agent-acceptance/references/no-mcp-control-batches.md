# No-MCP control batches for Codex permission forensics

Use this when a Codex permission Case reports an outer `execute` operation but tool-call correlation indicates the underlying operation traveled through an MCP tool or a JavaScript/code-mode wrapper.

## Purpose

Run a controlled comparison that changes only MCP availability. This is diagnostic evidence, not a replacement for the original formal verdict. If suppressing an internal MCP surface also disables broader Apps, plugins, instructions, or ordinary tools, classify the run as a **confounded tool-surface ablation**, not an MCP-only control.

## Important distinctions

- Tool invocation is not synonymous with the ACP `execute` capability: a valid read-only Case still needs a tool path classified and mediated as `read`.
- Removing the only usable read tool can eliminate a violation while making the task impossible; that proves neither read permission nor a fix.
- A Prompt saying “do not use MCP” is cooperative guidance, not enforced isolation.
- An empty ACP `mcpServers` list does not necessarily suppress MCP servers loaded from Codex user, project, system, or managed configuration.
- An empty runtime table such as `mcp_servers={}` may deep-merge with lower configuration layers and leave existing servers enabled.
- Codex can expose host-owned Apps/connectors and plugin resources through an internal MCP surface such as `codex_apps`; these do not appear in `codex mcp list` and are not disabled by turning off only named `mcp_servers`.
- Disabling MCP must be proven from effective runtime evidence before the fixed Case Prompt, not inferred from the model's response or from an external-server listing alone.
- A broad feature flag is not MCP-specific merely because it removes an MCP endpoint. Record every effective non-MCP change and downgrade causal claims accordingly.

## Preferred isolated design

1. Enumerate named external MCP servers without persisting their configuration contents. Separately enumerate effective Apps/plugins/features because `codex mcp list` is not a complete inventory of internal MCP surfaces.
2. Keep the same external Codex CLI, adapter version, model, effort, sandbox/mode, permission grant, fixed Case, timeout, and one-submission/no-retry policy.
3. Use a disposable daemon or separately registered temporary route; do not mutate the live route merely to run the control.
4. Prefer an MCP-specific control: explicitly disable each effective named server while preserving Apps, plugin, instruction, and ordinary-tool settings. If an internal MCP endpoint has no granular switch, use staged controls and keep the inference narrow:
   - named external MCP servers disabled, all other features unchanged;
   - internal Apps-owned MCP bridge disabled with plugin defaults preserved;
   - plugin discovery disabled only when evidence shows that it contributes the target MCP surface.

   For `codex-acp` versions supporting `CODEX_CONFIG`, an Apps-level fallback may resemble:

   ```json
   {
     "features": {
       "apps": false
     },
     "mcp_servers": {
       "server-name": {"enabled": false}
     }
   }
   ```

   This fallback is broader than MCP unless version-specific evidence proves otherwise. Do not add `plugins=false` or `recommended_plugins=false` merely to make the result cleaner. Feature names and merge behavior are version-sensitive: verify them against current official Codex documentation, then use installed-CLI readbacks before submitting a Run.
5. Preserve the user's existing `CODEX_HOME` for authentication only when the adapter/runtime override has higher precedence and is proven effective. Otherwise use an isolated configuration home without copying or exposing credentials.
6. Prove before the fixed Case Prompt that target external servers are disabled and Apps/plugins are unavailable. Confirm again from the structured Run transcript: Apps instructions absent, no internal MCP resource advertisement, and no MCP tool event. Do not add `/mcp` to the fixed acceptance Prompt.
7. Run the same fixed permission Case once. Preserve fresh Session, exact model/effort readback, durable events, workspace before/after evidence, and process reap proof.
8. Compare Case by Case with the prior batch. Do not project the control result back onto the original Run. If a partial control reveals another MCP surface, preserve that batch as intermediate evidence and create a separately identified full control; never resubmit the same Case to overwrite it.

## Interpretation

First classify the control itself:

- **Strict MCP-only:** every non-MCP feature, instruction surface, ordinary tool, mode, grant, and Prompt is unchanged.
- **Confounded tool-surface ablation:** suppressing MCP also changes Apps, plugins, instructions, or ordinary tools. This can localize a defect to the removed bundle, but cannot establish that MCP alone was necessary.

Then interpret the Case:

- Original FAIL → strict MCP-only PASS: MCP availability was a necessary trigger for the observed violation, and a separately mediated read path remained usable.
- Original FAIL → strict MCP-only UNSUPPORTED: the violating path disappeared and a read occurred, but measurable permission mediation is still absent.
- Original FAIL → INDETERMINATE with `TOOL_ATTEMPT_UNPROVEN`: the violating path disappeared because no usable read attempt remained. Report loss of capability; do not call this permission success or proof of MCP causality when the control was confounded.
- Original FAIL → FAIL with `execute`: correlate the new tool-call IDs before assigning ownership; MCP may not be the only trigger.
- A partial control that disables named external servers but leaves `codex_apps` or plugin resources available is not a no-MCP verdict; classify only what it isolated.
- A deny Case regressing under any control means the control changed a mediation dependency; stop rather than declaring the hypothesis proven.

## Safety and evidence rules

- Never add `execute` capability to make a read-only Case pass.
- Never edit the fixed controller Prompt to steer around a failing tool path.
- Never disable MCP globally in the user's persistent configuration for a one-round diagnostic.
- Never expose MCP arguments, environment values, credentials, tokens, or private paths in the shareable summary.
- Record adapter package version, external Codex CLI version, ARS package/API, execution plane, exact route literal, and how no-MCP state was proven.

## Version note from the 2026-08 investigation

With Codex CLI 0.147.0, a dotted runtime override for a named server (`mcp_servers.<name>.enabled=false`) reported that server disabled, while an empty `mcp_servers={}` override did not remove the lower-layer server. Host-owned Apps/plugin MCP resources remained available after the named CodeGraph server was disabled. A later run disabled `apps`, `plugins`, `recommended_plugins`, and the named server; it produced `apps_instructions=false`, no MCP tool event, and no permission violation, but the read Case became `INDETERMINATE/TOOL_ATTEMPT_UNPROVEN` because no ordinary file-read tool remained.

That later run was a **confounded Apps/plugins/tool-surface ablation**, not a strict MCP-only control. In that installed configuration, `plugins` changed from enabled to disabled while `recommended_plugins` was already disabled. Its result does not prove MCP causality; it proves only that the removed bundle contained the violating path and also the only usable read path. `codex-acp` 1.2.0 documents `CODEX_CONFIG` as JSON merged into session config. Revalidate every key, merge rule, default, and runtime surface after upgrades.
