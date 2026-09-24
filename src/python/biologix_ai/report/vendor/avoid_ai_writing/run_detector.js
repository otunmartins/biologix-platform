"use strict";
// Reads {"text": "...", "contextMode": "technical", "sourceMode": "rendered-markdown"} on stdin.
const AIDetector = require("./patterns.js");
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (raw += chunk));
process.stdin.on("end", () => {
  const input = JSON.parse(raw || "{}");
  const result = AIDetector.analyzeText(input.text || "", {
    contextMode: input.contextMode || "technical",
    sourceMode: input.sourceMode || "rendered-markdown",
  });
  process.stdout.write(JSON.stringify(result));
});
