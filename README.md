# Vanguard Relay

**A local control room where a strong AI "lead" model plans the work and hands pieces to cheaper helper models, with tests as the referee and a human holding the only Merge button.**

<!-- TODO screenshot: docs/screenshot.png (goal page mid-run) -->

## Why I built it

Running several AI coding agents by hand meant copy-pasting between them all day and burning premium usage on simple work.
Relay lets one capable model direct the job while affordable models do the bulk of it, and it uses the subscription sign-ins
the tools already have rather than paid API keys.

## Highlights

- **Agent hand-off protocol.** Every turn ends with a structured hand-off (continue, pass to a teammate, back to the lead, done, or ask the human), so agents coordinate without a person in the loop.
- **Tests as the referee.** A baseline test run before any change; after every turn Relay commits and re-runs tests, and anything worse than the baseline goes back to be fixed.
- **Isolated work.** Each goal runs in its own git worktree and branch. Nothing reaches your branch until you press Merge, and Merge refuses on worse tests or a dirty folder.
- **Usage-limit failover.** When one provider hits its limit, the next turn moves to another provider's agent of the nearest tier.
- **Projects.** Larger work is split by the lead into a 3 to 12 piece plan that you approve before it starts.
- **Coordinator chat.** A cheap model answers questions about live status and can start, continue or stop goals, but can never merge.
- **No dependencies.** Python standard library backend (one file), single-page web UI with a Three.js visual, web push alerts.

## Architecture

```
 browser UI (web/index.html) ──HTTP──▶ relay.py (stdlib HTTP server, state in data/)
                                         │
                       ┌─────────────────┼──────────────────┐
                 lead agent CLI     helper agent CLIs    test command
                 (strong model)     (cheaper models)     (baseline + per turn)
                       └──── git worktree per goal: relay/<id> ────┘
```

## Tech

Python 3.10+, git worktrees, subprocess-driven agent CLIs, vanilla JS + Three.js, Web Push (VAPID, hand-rolled P-256 in `p256.py`), unittest + headless-browser checks.

## What I'd build next

- A replay viewer for finished goals.
- Provider limit patterns learned from real failures instead of configured by hand.
- Cost and usage reporting per goal.

---

## Start

Double-click `start.bat`, or run `python -B relay.py`, then open http://127.0.0.1:8940/

Needs Python 3.10+ and Git. No packages to install.

## How a goal runs

1. You pick a project folder (a Git repository) and describe the goal. Auto chooses the lead and helpers; manual picking remains available.
2. Relay makes a separate working copy on its own branch (`relay/<id>`, under `data/work`). Your folder is not touched.
3. If you gave a test command, it runs once before any change. That result is the baseline.
4. The lead takes the first turn. Every agent ends its turn with a hand-off: continue, pass to a teammate,
   send back to the lead, done, or a question for you.
5. After every turn Relay commits the changes and runs the tests. Worse than the baseline sends it back to be fixed.
6. Auto mode follows the hand-offs. Manual mode stops after each turn and shows you the buttons.
7. "Done" counts only when the lead says so and tests are no worse than the baseline. Then you press Merge.

With helpers, the lead starts with a short plan and passes defined pieces to affordable teammates, then reviews their work.
The goal shows the plan, checks off pieces after the assigned agent takes a later turn, and shows each agent's turn count.
Questions show who asked, the complete question, and **Answer and continue**. Other actions sit under **More options**.

Automatic team selection asks the coordinator to choose from installed agents whose subscription and usage checks allow them to run.
If the coordinator fails or returns an invalid choice, rules pick a strong lead with affordable help; short goals prefer a mid-tier lead.
Strata is considered only when its existing local tunnel answers. Selection never starts the tunnel.
The choice and its explanation appear on the goal. Auto-picked goals start in Auto mode.
For API clients, send `lead: "auto"` and/or `team: "auto"`; omitting both selects automatically. An explicit lead without a team remains solo.
An optional `agents.<id>.good_at` string in the config gives the coordinator a one-line description of that model's strengths.

It stops and asks you when: an agent has a question, something fails twice in a row, tests stay worse for three
turns, the hand-off limit is reached, or Relay itself hits an error.

## Settings: `data/config.json` (created on first start)

- `projects_root`: where the folder list comes from.
- `turn_minutes` (60), `idle_minutes` (20): limits for one agent turn.
- `handoff_cap` (6), `max_running_goals` (2).
- `notify_url`: optional. A short line is POSTed there when a goal needs you or finishes (works with an ntfy topic).
- `agents` and `providers`: add or change models, and the exact command each tool is started with. See the
  defaults at the top of `relay.py`. Change these if a tool's flags differ on your machine.

API key variables are removed from every agent's environment, so the tools use their subscription sign-in.

## Phone (Tailscale), same idea as before

1. `tailscale serve --bg --https=8444 http://127.0.0.1:8940`
2. In `data/config.json`: `"remote": {"enabled": true, "host": "<your-node>.<tailnet>.ts.net:8444", "login": "<your tailnet login>"}`
3. Keep Funnel off for that port.

## Safety, kept small on purpose

- Agents work in the separate copy. Nothing reaches your branch until you press Merge.
- Merge refuses if tests are worse than the baseline, or your folder has uncommitted changes.
- The page only accepts changes from itself (blocks other websites). It does not stop another program on this
  computer from calling it, and agents run as you, like a direct agent session does.

## Usage limits

When a turn fails with a usage-limit message that names a reset time, Relay marks that
provider as limited until the reset time (60 minutes if no time can be read). Limited agents show `limit · resets ...`, are not started, and
cannot be picked. The mark clears itself, or with "Recheck sign-ins". In auto mode the same note goes to another provider's agent (team first,
nearest tier first) without using a hand-off; if nobody is free the goal waits for you and says when the limit resets. Claude's
`rate_limit_event` warning (`allowed_warning`) makes the goal route its next turn elsewhere. Patterns are per provider under
`providers.<name>.limit.patterns` (+ `minutes`); only Claude has defaults, because the wording of the other tools is not confirmed.

## Tests

`python -B tests/test_relay.py` runs the whole loop with a stand-in agent.

## Attachments

Use Attach, paste a file, or drop files onto the chat input, a goal's note box, or the new goal form. Upload chips show progress and errors; remove an errored chip before sending. Image thumbnails open full size and other files download.

The defaults in `data/config.json` are:

```json
"uploads": {"max_file_bytes": 15728640, "max_files": 10, "max_total_bytes": 209715200}
```

The total includes uploaded originals and goal copies. Unattached uploads expire after 24 hours and are cleaned on the next upload. Removing a goal removes its attachment folder and originals that no other goal or chat message uses. Clearing chat releases its upload references. Text/code files must be UTF-8. Images and PDFs must match their extension's signature; archives and compiled executables are refused.

Files live in `data/uploads/<id>/<safe name>`. Goal copies live in `data/goals/<gid>/attachments/<id>/<safe name>`, outside the agent worktree. Agent briefs list absolute paths. Strata receives at most three text files as read-only context, never images. Muse also receives bounded inline text. The installed CLI help confirms image flags for Codex and Muse; visual input is unconfirmed for Claude, Grok and agy, and their briefs say so. See [the local tool report](handoff/ATTACHMENT-IMAGE-TOOLS.md).

Run `python -m unittest discover -s tests` for backend coverage. On this Windows machine, `node tests/check_attachments_browser.mjs` runs an isolated fixture with real headless Chrome and no provider calls, produces screenshots in `data/`, and checks desktop/mobile widths, picker, paste, drop, upload errors and previews. Requires Node with built-in WebSocket and Chrome at its standard installation path.

`node tests/check_goal_browser.mjs` checks Auto/manual creation and question answers through the real local HTTP handlers, plus plan progress, draft retention, and symbol sprites, working streams, docked dimming, and centre labels. Providers and Git writes are stubbed and no scheduler runs. It saves desktop and phone screenshots under `data/`. `node tests/model_icons_sheet.mjs` regenerates the eleven-symbol contact sheet.
