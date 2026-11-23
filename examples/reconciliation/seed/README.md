# Daily order reconciliation

Implement `reconcile(north, coast) -> dict[str, int]` in importer.py for the current supplier contracts. The input lists contain decoded JSON records. Keys in the output use the public supplier key from each contract, a slash, and the source order identifier. Output only contributing orders. Preserve unrelated existing behavior where compatible and never modify the input records. Use the Python standard library. Only importer.py may change. Run python -B smoke.py for the public examples. See both supplier documents and the release notes for current behavior.
