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


def run_claude_with_pty(command, debug=False):
    """Run Claude in a PTY with startup message."""
    sys.stderr.flush()

    # Track current message being typed
    current_message = ""

    # Create a pseudo-terminal and run the command
    pid, master_fd = pty.fork()

    if pid == 0:  # Child process
        # Execute claude
        os.execvp("claude", command)

    # Parent process - set up terminal and copy data
    old_tty = None
    if sys.stdin.isatty():
        old_tty = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin.fileno())

    try:
        while True:
            r, w, e = select.select([sys.stdin, master_fd], [], [])

            if sys.stdin in r:
                data = os.read(sys.stdin.fileno(), 1024)

                # Track input and detect Enter key
                for byte in data:
                    char = chr(byte) if byte < 128 else '?'

                    if byte == 13:  # Enter key (carriage return)
                        if debug:
                            print(f"[DEBUG] Message sent: '{current_message}'", file=sys.stderr)
                        current_message = ""  # Reset for next message
                    elif byte == 127:  # Backspace
                        if current_message:
                            current_message = current_message[:-1]
                        if debug:
                            print(f"[DEBUG] Current: '{current_message}'", file=sys.stderr)
                    elif byte == 3:  # Ctrl+C
                        if debug:
                            print(f"[DEBUG] Ctrl+C pressed", file=sys.stderr)
                        current_message = ""
                    elif 32 <= byte <= 126:  # Printable ASCII
                        current_message += char
                        if debug:
                            print(f"[DEBUG] Current: '{current_message}'", file=sys.stderr)

                # Forward the data to Claude
                os.write(master_fd, data)

            if master_fd in r:
                data = os.read(master_fd, 1024)
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()
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
