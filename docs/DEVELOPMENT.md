# Developer guide

Notes for anyone changing this code. For what the tool does from a user's
point of view, see the [README](../README.md).

- [Set up](#set-up)
- [Code map](#code-map)
- [How one refresh works](#how-one-refresh-works)
- [The poll policy](#the-poll-policy)
- [The btop screen](#the-btop-screen)
- [History storage](#history-storage)
- [Common changes](#common-changes)
- [Tests](#tests)
- [Project quirks](#project-quirks)
- [Releasing](#releasing)

---

## Set up

```bash
git clone https://github.com/foxhats/claude-usage.git
cd claude-usage
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'      # editable install + pytest + numpy
python -m pytest -q           # whole suite, a few seconds, no network needed
claude-usage                  # run your working copy
```

`pip install -e` means edits to the source take effect the next time you run
`claude-usage`, with no reinstall needed.

## Code map

```
claude_usage/
├── monitor.py               entry point: parse args, pick Code or Console mode
├── code_mode/               ← everything for Claude Code (OAuth) users
│   ├── monitor.py           CodeMonitor: main loop, polling, poll policy, keys
│   ├── api.py               ClaudeAPIClient + ApiError (HTTP to api.anthropic.com)
│   ├── auth.py              OAuthManager: reads ~/.claude/.credentials.json / Keychain
│   ├── btop.py              ★ the btop-style screen (this fork)
│   ├── display.py           UsageRenderer: the original (--classic) screen and
│   │                        the pace-maker panels the btop screen reuses
│   ├── storage.py           CodeStorage: SQLite snapshots in ~/.claude-usage/
│   └── pacemaker_integration.py   PaceMakerReader: optional Claude Pace Maker data
├── console_mode/            Admin-API cost tracking (unchanged from upstream)
├── shared/                  storage base class, pace-maker bundle fetcher
└── tools/token_cost_analyzer/   offline analysis tool (needs numpy; not used by the monitor)
tests/                       pytest suite (see Tests below)
docs/                        this guide, screenshot + its generator, upstream README
```

What this fork added or changed:

| File | Change |
| --- | --- |
| `code_mode/btop.py` | **New.** The whole btop screen: theme, meters, braille graph, boxes, layout, history store. |
| `code_mode/monitor.py` | `style` option, `get_btop_display()`, poll policy (`_on_poll_error`, `_build_abort_report`, `_abort`), `q` to quit, full-screen mode, and no profile request with an expired token. |
| `code_mode/api.py` | `ApiError` and `fetch_usage(strict=True)`: one request, no retries, full error details kept. |
| `code_mode/display.py` | `_fmt_rules_breakdown` (no mid-value wrapping in the 22-column panel), shorter "log large". |
| `monitor.py` | `--classic` flag. |

## How one refresh works

`CodeMonitor.run()` is a simple loop, ticking once a second (`DISPLAY_REFRESH_INTERVAL`):

```
startup: fetch_usage() once          ── may set abort_report → exit 2
loop:
  handle keys (q / Ctrl+C quit; arrows scroll pace-maker panels)
  if pace-maker has fresh data → use it (_refresh_from_model, no HTTP)
  if now - last_poll >= self.poll_interval → fetch_usage()
        └─ if abort_report got set → leave the loop, print it, exit 2
  render = get_btop_display()   (or get_display() with --classic)
  Rich Live repaints the screen (screen=True → alternate screen, like btop)
```

`fetch_usage()` order of checks: refresh credentials → fetch profile if
missing → pace-maker data → no credentials? → **token expired? (show
"Token expired. Run 'claude' to refresh." and send nothing)** → pace-maker
backoff? → one HTTP request.

## The poll policy

Implemented in `CodeMonitor._on_poll_error`. State lives on the monitor:
`poll_interval`, `slowed_at`, `poll_log`, `successful_polls`, `abort_report`.

```
            any error                    HTTP 429
 ┌────────┐ ─────────► ┌────────────┐ ─────────► ┌────────────────────────┐
 │ 2 min  │            │   4 min    │            │ stop: print report,    │
 │ normal │            │ (rest of   │ ◄──┐       │ exit code 2            │
 └────────┘            │  session)  │ ───┘       └────────────────────────┘
                       └────────────┘  other errors stay at 4 min
```

Rules worth keeping if you change it:

- **One request per poll.** `fetch_usage(strict=True)` never retries or
  sleeps. Sleeping inside the loop freezes the screen.
- **It never speeds back up** within a session. If the API was unhappy once,
  stay gentle.
- **The report must not leak secrets.** `_build_abort_report` prints the
  *response* (status, headers, body) and redacts `set-cookie`. Never add the
  request headers: they contain the OAuth token.
- With pace-maker installed, pace-maker does the API polling, so the account
  box hides the poll and next rows.

Tests: `tests/test_poll_policy.py`.

## The btop screen

Everything is in `code_mode/btop.py`, built from small pieces that turn into
Rich renderables.

| Piece | What it is |
| --- | --- |
| theme constants (`MAIN_FG`, `LIMITS_BOX`, `CPU_GRADIENT`, …) | btop's default theme colours. Change them here only. |
| `gradient(stops, frac)` | colour at 0–1 along a 3-stop gradient |
| `meter(pct, width)` | row of `■`, each filled cell coloured by its position (btop style) |
| `braille_graph(values, w, h, stops, downward)` | area graph, 2 samples per character, 4 dot rows per line |
| `resample(samples, now, span, columns, max_gap)` | turns irregular samples into evenly spaced columns, with `None` for gaps longer than `max_gap` (monitor not running) |
| `Box` | rounded box with `¹title` notched into the top border, optional notches top-right / bottom-left / bottom-right. `height=None` fits the content. |
| `Columns` | boxes side by side; a width of `None` takes the leftover space |
| `Screen` | stacks rows to fill the terminal. One **elastic** box (the history graph) gets whatever height is left. |
| `HistoryBody` | the mirrored graph: 5-hour grows up, 7-day hangs down |
| `build_screen(width, …)` | assembles everything for one frame |
| `BtopView` | thin wrapper so the layout is computed at render time with the real terminal width |

Layout rules in `build_screen`:

- `width >= 96`: **limits** and **account** side by side (account is 38 wide),
  both padded to the same height. Narrower terminals stack them.
- Order top to bottom: limits/account → error box (only if there's an error) →
  history (elastic) → 7-day breakdown (only if the API sends it) → extra boxes
  (pace-maker, events).
- Key hints (`q quit`, …) are notched into the **bottom border of the last
  box**.

Box numbers (the superscripts) follow btop: 1 limits, 2 account, 3 history,
4 breakdown, 5 pace-maker, 6 events.

## History storage

`UtilHistory` in `btop.py`:

- SQLite file: `~/.claude-usage/usage_history.db` (shared with the original
  storage code). Table:
  ```sql
  CREATE TABLE btop_util_history (ts INTEGER PRIMARY KEY, five_hour REAL, seven_day REAL)
  ```
- One row per distinct `last_update`, so re-rendering the same data doesn't
  add rows. Rows older than 7 days are deleted on every insert. The graph shows
  the last 24 h.
- All SQLite errors are swallowed on purpose. A broken history file must
  never crash the monitor, it just means an empty graph.

## Common changes

**Change a colour:** edit the constants at the top of `btop.py`.

**Show a new limit** (say the API adds `seven_day_haiku`): add a row to
`_LIMITS` in `btop.py`: `("seven_day_haiku", "haiku", 7 * 86400)`.

**Add a new box:**

```python
# in build_screen(), before rows.extend(extra_boxes or [])
lines = [kv("label", "value", width - 2)]          # list of rich Text lines
rows.append(Box(7, "my box", "#556d59", lines))     # number, title, border colour, body
```

Keep the body narrower than `width - 2` (the borders take 2 columns). Use
`Text(no_wrap=True)` so a long line is cut off instead of wrapping and
breaking the box.

**Change poll timings:** `POLL_INTERVAL` / `SLOW_POLL_INTERVAL` at the top of
`CodeMonitor`, then update `tests/test_poll_policy.py`,
`tests/test_api_backoff.py::test_code_monitor_poll_interval_is_2_then_4_minutes`
and the README's "How it polls" section.

**Regenerate the README screenshot** after visual changes:

```bash
python docs/make_screenshot.py      # writes docs/screenshot.svg from fake demo data
```

## Tests

```bash
python -m pytest -q                         # everything
python -m pytest -q tests/test_btop_view.py # one file
```

- `test_btop_view.py`: screen layout, meters, graph, history store.
- `test_poll_policy.py`: 2 → 4 min → stop, abort report contents.
- `test_fork_fixes.py`: expired-token handling, panel wrapping.
- Everything else comes from upstream.

**Skipped tests are expected.** About 18 tests exercise the real
[Claude Pace Maker](https://github.com/LightspeedDMS/claude-pace-maker) package
(its SQLite model, fallback state, WAL handling). They're marked
`@requires_pacemaker` (see `tests/_pacemaker.py`) and only run when pace-maker
is importable. To run them, clone pace-maker **next to** this repo:

```
parent/
├── claude-usage/
└── claude-pace-maker/      git clone https://github.com/LightspeedDMS/claude-pace-maker
```

The token-cost analyzer tests skip unless numpy is installed
(`pip install -e '.[test]'` installs it). One console-mode test skips without
a real `ANTHROPIC_ADMIN_API_KEY`.

The suite makes **no network requests**: it passes with networking disabled.
Keep it that way. Mock `api_client` instead of letting a test reach
`api.anthropic.com`.

## Project quirks

- **Line endings.** Most files from upstream (all of `claude_usage/*.py`,
  `pyproject.toml`, `CLAUDE.md`) use **CRLF** (Windows) line endings. Keep
  them CRLF when editing, or every line shows as changed in the diff. Most
  test files and the files this fork added use LF. Check with `file <path>`.
- **`MagicMock/` folder** in the repo root is junk some upstream tests create
  (a mocked `Path.home()`). It's in `.gitignore`. Delete it whenever.
- **`CLAUDE.md` and `.claude/`** are upstream's instructions and settings for
  Claude Code. The hooks referenced there (`tdd-guard`, `~/.claude/hooks/…`)
  are the upstream author's local setup, so you don't need them.
- **Pace-maker imports** happen inside functions and are wrapped in
  `try/except ImportError`. pace-maker is optional and must never be required
  to start the monitor.

## Releasing

1. Bump the version in **both** `pyproject.toml` and
   `claude_usage/__init__.py`, and update `tests/test_monitor_panel.py`'s
   version test. Versions look like `2.19.6+btop.N`, meaning "upstream 2.19.6,
   btop release N".
2. Add a `CHANGELOG.md` entry.
3. Run the tests, commit and push. Users get it with `pipx upgrade claude-usage`.

To pull in upstream fixes:

```bash
git remote add upstream https://github.com/LightspeedDMS/claude-usage.git  # once
git fetch upstream
git merge upstream/main        # resolve conflicts, run tests, push
```
