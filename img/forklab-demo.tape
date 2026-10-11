# Demo GIF for forklab: branch/merge with explicit conflicts (§39-§40).
# Render with: vhs docs/img/forklab-demo.tape
# (requires https://github.com/charmbracelet/vhs and `uv sync` already run)

Output docs/img/forklab-demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1000
Set Height 620
Set Padding 20
Set Theme "Dracula"
Set TypingSpeed 40ms

Type "uv run python -m examples.forklab.main"
Enter
Sleep 2s
Sleep 3s

Type "uv run python -m examples.forklab.main --conflict"
Enter
Sleep 2s
Sleep 3s

Type "uv run python -m examples.forklab.main --mermaid | tail -15"
Enter
Sleep 2s
Sleep 4s
