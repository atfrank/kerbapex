---
name: claude-login
description: Check whether the Claude CLI that writes DriverDirector's commentary is signed in, and give the user the exact sign-in command for their own terminal. Use when a render stops with "signed out", "OAuth session expired" or "Failed to authenticate", when `dd health` shows claude_logged_in false, or when the user asks how to sign the commentary writer back in.
---

# Sign the commentary writer back in

DriverDirector writes its commentary lines with the Claude CLI that ships inside the Claude desktop app, using the user's own Claude login. That login expires now and then. When it has, no commentary can be written (a render stops at "commentary | writing", with "OAuth session expired" or "Failed to authenticate"), but nothing else is lost.

Helper: `python "${CLAUDE_PLUGIN_ROOT}/scripts/dd.py" <command>` (called `dd` below).

1. Run `dd claude-login`. It only reads the status. It prints `logged_in` (true, false, or null if it could not tell), the CLI's path, and, when signed out, the sign-in command.
2. If `logged_in` is true, say so and continue what you were doing (for a stopped render: `dd redo-commentary` or `dd run` again).
3. If it is false, **the user signs in, in their own terminal**; you never do it for them and never ask for their password or code. Show them the command for the terminal they use, each in its own code block:
   - Git Bash: `sign_in.git_bash`
   - PowerShell: `sign_in.powershell`

   Run it exactly as printed. It opens their browser to sign in with their Claude account. Two traps that have already happened:
   - The Claude desktop app is a packaged Windows app, so the CLI has two paths. Claude's own shell sees `...\AppData\Roaming\Claude\claude-code\...`; a normal terminal only sees the real one, `...\AppData\Local\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\...`. `dd claude-login` prints the real one. The short path gives "No such file or directory" in the user's terminal.
   - Git Bash needs forward slashes and quotes; PowerShell needs `&` in front of a quoted path.
4. Ask the user to tell you when it says it succeeded, then run `dd claude-login` again to confirm `logged_in` is true before retrying the job.

If the user is away or cannot sign in right now, a render does not have to wait: you can write the commentary lines yourself and hand them to
the app (see "Writing the commentary yourself" in the direct skill: `GET /api/commentary/scenes`, then `POST /api/commentary/script`, then
`commentary.reuse_script`). Tell the user afterwards that the lines came from you and not from the Claude CLI.

If the user would rather not sign in, the alternative is the API backend (`{"commentary":{"backend":"api"}}` with an `ANTHROPIC_API_KEY` environment variable that the user sets themselves); never ask for or type the key.
