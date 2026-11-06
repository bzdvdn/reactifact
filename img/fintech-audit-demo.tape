# Demo GIF for fintech_audit: a deterministic, auditable, reproducible answer.
# Render with: vhs docs/img/fintech-audit-demo.tape
# (requires https://github.com/charmbracelet/vhs and `uv sync` already run)

Output docs/img/fintech-audit-demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1180
Set Height 940
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 35ms

Type "uv run python -m examples.fintech_audit.main"
Enter
Sleep 6s
Sleep 3s

Type "reactifact replay --help >/dev/null; echo 'verify a run:  reactifact replay <store> --session <id> --verify 24449f6f...'"
Enter
Sleep 2s
Sleep 3s
