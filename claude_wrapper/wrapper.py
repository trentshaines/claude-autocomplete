#!/usr/bin/env python3
"""
Minimal PTY wrapper for Claude Code
"""

import os
import sys
import pty
import select
import tty
import termios
import argparse
import struct
import fcntl
import time


def generate_suggestion(text):
    """Generate a mock autocomplete suggestion."""
    suggestions = {
        "write": " a function to calculate fibonacci",
        "create": " a new Python class",
        "help": " me understand this code",
        "fix": " the bug in this function",
        "explain": " how this works",
        "show": " me an example"
    }

    # Find the last word
    words = text.strip().split()
    if not words:
        return ""

    last_word = words[-1].lower()
    return suggestions.get(last_word, "")


def get_terminal_size():
    """Get current terminal size."""
    try:
        size = struct.unpack('hh', fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, '1234'))
        return size[0], size[1]  # rows, cols
    except:
        return 24, 80  # Default fallback


def update_status_line(suggestion="", debug_info=""):
    """Update the status line with simple powerline styling."""
    rows, cols = get_terminal_size()

    # Save Claude's cursor position FIRST
    sys.stdout.write("\033[s")

    # Simple powerline colors - white on gray
    status_content = ""

    if suggestion:
        status_content = f"\033[37;48;5;240m 💡 {suggestion} \033[48;5;240;38;5;240m\033[40m⮀\033[0m"

    if debug_info:
        debug_short = debug_info[:50] + "..." if len(debug_info) > 50 else debug_info
        if status_content:
            status_content += f"\033[37;48;5;240m 🐛 {debug_short} \033[48;5;240;38;5;240m\033[40m⮀\033[0m"
        else:
            status_content = f"\033[37;48;5;240m 🐛 {debug_short} \033[48;5;240;38;5;240m\033[40m⮀\033[0m"

    # Fill rest with black
    if status_content:
        # Estimate visible length (rough)
        visible_len = len(suggestion) + len(debug_info[:50]) + 10
        remaining = max(0, cols - visible_len)
        status_content += "\033[40m" + " " * remaining + "\033[0m"

    # Position and render
    status_row = rows
    sys.stdout.write(f"\033[{status_row};1H")  # Move to status line
    sys.stdout.write("\033[K")  # Clear line
    if status_content:
        sys.stdout.write(status_content)

    # CRITICAL: Restore Claude's cursor position
    sys.stdout.write("\033[u")
    sys.stdout.flush()


def setup_terminal_with_status(debug=False):
    """Set up terminal with tmux-style persistent status bar."""
    rows, cols = get_terminal_size()

    # Reserve bottom lines for our status
    status_lines = 2 if debug else 1
    claude_rows = rows - status_lines

    # Set scrolling region for Claude (top area only) - this is CRITICAL
    sys.stdout.write(f"\033[1;{claude_rows}r")  # Set scrolling region

    # Clear only Claude's region, not our status area
    sys.stdout.write(f"\033[1;1H")  # Move to top of Claude's region
    sys.stdout.write(f"\033[0J")    # Clear from cursor to end of Claude's region

    # Initialize status bar area with background
    status_row = rows - status_lines + 1
    for i in range(status_lines):
        sys.stdout.write(f"\033[{status_row + i};1H")
        sys.stdout.write("\033[K")  # Clear the line
        if i == 0:  # First status line gets a subtle background
            sys.stdout.write("\033[48;5;236m" + " " * cols + "\033[0m")  # Dark gray background

    sys.stdout.write(f"\033[1;1H")  # Return cursor to Claude's area
    sys.stdout.flush()


def run_claude_with_pty(command, debug=False):
    """Run Claude in a PTY with startup message."""
    sys.stderr.flush()

    # Track current message being typed and suggestions
    current_message = ""
    current_suggestion = ""
    last_debug_info = ""

    def update_and_store_status(suggestion="", debug_info=""):
        """Update status line and store values for re-injection."""
        nonlocal last_debug_info
        last_debug_info = debug_info
        update_status_line(suggestion=suggestion, debug_info=debug_info)

    # Create a pseudo-terminal and run the command
    pid, master_fd = pty.fork()

    if pid == 0:  # Child process
        # Execute claude
        os.execvp("claude", command)

    # Parent process - set up terminal and copy data
    old_tty = None
    if sys.stdin.isatty():
        old_tty = termios.tcgetattr(sys.stdin)
        setup_terminal_with_status(debug)
        tty.setraw(sys.stdin.fileno())
        # Initial status message

    try:
        while True:
            r, w, e = select.select([sys.stdin, master_fd], [], [])

            if sys.stdin in r:
                data = os.read(sys.stdin.fileno(), 1024)

                # Track input and handle suggestions
                for byte in data:
                    char = chr(byte) if byte < 128 else '?'

                    if byte == 9:  # Tab key - accept suggestion
                        if current_suggestion:
                            # Inject suggestion as real keystrokes
                            os.write(master_fd, current_suggestion.encode())
                            current_message += current_suggestion
                            debug_info = f"Accepted suggestion, new text: '{current_message}'" if debug else ""
                            update_and_store_status(debug_info=debug_info)
                            current_suggestion = ""
                            continue  # Don't forward the Tab key itself

                    if byte == 13:  # Enter key (carriage return)
                        debug_info = f"Message sent: '{current_message}'" if debug else ""
                        update_and_store_status(debug_info=debug_info)
                        current_message = ""
                        current_suggestion = ""
                    elif byte == 127:  # Backspace
                        if current_message:
                            current_message = current_message[:-1]
                        # Clear old suggestion since user is editing
                        current_suggestion = ""
                        debug_info = f"Current: '{current_message}' (suggestion voided)" if debug else ""
                        update_and_store_status(debug_info=debug_info)
                    elif byte == 3:  # Ctrl+C
                        debug_info = "Ctrl+C pressed" if debug else ""
                        update_and_store_status(debug_info=debug_info)
                        current_message = ""
                        current_suggestion = ""
                    elif 32 <= byte <= 126:  # Printable ASCII
                        current_message += char
                        # Generate suggestion for the current text
                        current_suggestion = generate_suggestion(current_message)
                        debug_info = ""
                        if debug:
                            debug_info = f"Current: '{current_message}' | Suggestion: '{current_suggestion}'"
                        update_and_store_status(suggestion=current_suggestion, debug_info=debug_info)

                # Forward the data to Claude (unless it was Tab)
                if not (len(data) == 1 and data[0] == 9):  # Don't forward Tab
                    os.write(master_fd, data)

                # Suggestion display will be handled by status line (to be implemented)

            if master_fd in r:
                data = os.read(master_fd, 1024)
                if not data:
                    break

                # Write Claude's output first - don't interfere with it
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()

                # Always re-inject our status line after any Claude output
                # This ensures we don't miss any of Claude's drawing
                update_status_line(
                    suggestion=current_suggestion,
                    debug_info=last_debug_info if debug else ""
                )

    except (OSError, KeyboardInterrupt):
        pass
    finally:
        if old_tty:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_tty)

    # Wait for child to exit
    _, status = os.waitpid(pid, 0)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Claude Code Wrapper with input tracking",
        add_help=False  # Don't intercept --help, let Claude handle it
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug output for wrapper (shows input tracking)'
    )

    # Parse only known args, let Claude handle the rest
    args, remaining = parser.parse_known_args()

    # Build command for Claude (without our wrapper flags)
    command = ["claude"] + remaining

    print("Starting Claude Code with autocomplete!")
    if args.debug:
        print("Debug mode enabled")

    try:
        exit_code = run_claude_with_pty(command, debug=args.debug)
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
