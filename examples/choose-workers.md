# Choose workers for an export fix

Suppose a customer name containing a comma breaks an order export. Your project already has a CSV parser you can use for acceptance. In a client with an authorized worker executor, you can give the main agent the following request.

> Keep my current conversation model. Have a fast worker locate the exporter and return the file and function. Give a coding worker the failing input and ask for a patch and a round-trip test. Ask a fresh reviewer to try a name containing both a quote and a newline. Bring the patch and test results back here. Ask me if the export scope is ambiguous.

On a Claude plan, try Haiku for the lookup and Sonnet for the patch and routine review in separate contexts. With Gemini, try Flash for these bounded stages. Check the exact model your client can assign to each worker before sending the task.

With the Codex preset, Luna can locate the exporter, Sol at medium effort can implement the patch, and Astra can review it. The Go preset uses Kimi K2.7 Code for implementation and Kimi K3.1 Pro for review. On a Qwen plan, try Flash for lookup and Plus for the patch. Reserve Max for a high-impact review if your executor offers it.

If the same patch changes how invoice amounts are rounded, give the calculation and its acceptance rule to a stronger reviewer such as Opus or Gemini Pro. Keep the lookup and the chat on their original models. Compare the result against exact decimal expectations yourself. Record the actual model, retries and elapsed time per accepted change before deciding whether that routing helped.

This is a configuration exercise. The model names are examples for your own executor. [Role selection guide](../docs/cloud-presets.md#choose-equivalents-on-another-subscription)
