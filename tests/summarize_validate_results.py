import json

data = json.load(open("results/validate_examples.json"))
s = data["summary"]
print("| Status | Count |")
print("|--------|-------|")
icons = {
    "PASS": "✅",
    "FAIL": "❌",
    "XFAIL": "⚠️",
    "NOT_APPLICABLE": "➖",
    "SKIP": "⏭️",
    "ERROR": "💥",
}
for k, v in sorted(s.items()):
    print(f"| {icons.get(k, '?')} {k} | {v} |")
