# Short (top-of-funnel) GIF for fintech_audit: the figure, the answer, the hash.
# Render with: vhs docs/img/fintech-audit-short.tape
# (requires https://github.com/charmbracelet/vhs and `uv sync` already run)

Output docs/img/fintech-audit-short.gif

Set Shell "bash"
Set FontSize 16
Set Width 1040
Set Height 400
Set Padding 16
Set Theme "Dracula"
Set TypingSpeed 30ms

Type "uv run python -m examples.fintech_audit.main --brief"
Enter
Sleep 5s
Sleep 3s
