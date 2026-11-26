# Shared zsh lines for Claude Code in the terminal, installed as ~/.claude/terminal/zshrc.zsh.
# Each computer's ~/.zshrc loads it with one line:
#   [[ -r ~/.claude/terminal/zshrc.zsh ]] && source ~/.claude/terminal/zshrc.zsh

# Code Mode for the conversation this command opens or resumes only; the desktop app and other
# sessions stay in the normal mode (Code Mode documentation). A conversation started in the normal
# mode stays normal when resumed here, and a Code Mode conversation is resumed through this command.
claude-code-mode() {
    CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1 claude "$@"
}
