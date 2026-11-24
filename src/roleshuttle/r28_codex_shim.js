const {spawnSync} = require("node:child_process");

const args = process.argv.slice(2);
const routed = args[0] === "exec" && process.env.AGENT_CONSOLE_EXTRA_USAGE === "0";
const command = routed ? process.env.ROLESHUTTLE_CODEX_PYTHON : process.env.ROLESHUTTLE_CODEX_BINARY;
const argv = routed ? ["-B", process.env.ROLESHUTTLE_CODEX_HELPER, ...args.slice(1)] : args;
const result = spawnSync(command, argv, {stdio:"inherit", windowsHide:true});
if (result.error) console.error("Failed to run the selected Codex transport.");
process.exitCode = result.status ?? 1;
