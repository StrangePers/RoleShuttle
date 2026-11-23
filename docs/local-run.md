# A local two-model acceptance run

A controller in a Codex chat used an installed RoleShuttle wheel on Windows with Ollama 0.40.17. The server listened on loopback, with cloud features disabled. Both workers used local CPU inference. The input was a fictional order export.

| Order | Selected | Name |
| --- | --- | --- |
| A17 | Yes | Ash, Lee |
| B31 | No | Bob |
| C42 | Yes | Dee "Q" |

The research role was asked to identify the selected orders. It returned A17 and C42 on separate lines. The controller checked the IDs against the input and passed the finding to the review role along with the original rows.

The review model produced a CSV with three columns in the A17 row. The controller returned that failing case. A second answer kept the same error. Both calls completed normally. Parsing the CSV caught the problem.

| Stage | Prompt tokens | Generated tokens | CLI elapsed |
| --- | ---: | ---: | ---: |
| Find selected IDs | 96 | 13 | 3.67 s |
| Produce the corrected CSV | 183 | 25 | 7.53 s |
| Revise after parser feedback | 290 | 53 | 4.19 s |
| Review the controller's patch | 278 | 408 | 32.73 s |

The controller corrected the fictional exporter with Python's `csv.writer`. A regression test parsed the CSV back into rows, checked the selected IDs and compared both names, including the comma and quotes. It failed on the original exporter and passed on the patch. The installed `roleshuttle review` command returned the same fingerprint before and after the test. The final model review correctly identified the selection condition and the CSV writer, but also questioned a line the patch had already removed.

## Reproduce the check

1. Configure the research and review roles in a local profile. Record exact model tags and the installed Ollama version.
2. Give the research role the table above and ask for the selected IDs. Pass the IDs, source rows and quoting requirement to the review role.
3. Parse the proposed output with a CSV reader. The expected rows are `["id", "name"]`, `["A17", "Ash, Lee"]` and `["C42", "Dee \"Q\""]`. A field containing a comma or a quote must be enclosed and embedded quotes must be doubled.
4. Retain failed receipts and count the cost of any correction. For a code change, pin the base, run the project test and confirm the reviewed fingerprint remains stable.

The table records individual calls from one run of the first preview with its default token limit. It excludes controller usage and the time between calls. The models used, model loading and cached context affect timing. This run verifies delegation and the return path. Measuring savings requires repeated comparable direct and delegated runs with identical acceptance criteria.
