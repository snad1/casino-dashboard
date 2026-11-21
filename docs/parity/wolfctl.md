# wolfctl parity

Every command in the `wolfctl` dispatcher and where it lives in the dashboard. Each row names the smallest role that can use it and the tests that cover it. `backend/tests/test_parity_doc.py` checks that every test named here exists. When `../wolfbet/wolfctl` is checked out next to this repo, it also checks that every dispatcher command has a row.

Pages are under `/<service>/`, for example `/wolfbet/control`. "Web terminal" means the owner opens `/terminal` and runs the command there. That needs the owner role, two-factor and a fresh code. Owner and admin need two-factor turned on before any control button works.

Every control button goes through `POST /{service}/control/<action>`. `test_every_action_for_every_role` in `backend/tests/test_control_api.py` presses every action as every role. The agent turns each action into the same `wolfctl` call the shell would make. `test_argv` in `agent/tests/test_wolfbet_commands.py` pins each argv.

| Command | Dashboard | Role | Tests |
|---|---|---|---|
| `setup` | N/A. The first-run wizard asks for the api key and writes `.env`, so it stays on the host. Web terminal | owner | `backend/tests/test_terminal.py::test_the_owner_opens_the_terminal` |
| `start` | Control > Service "Start" | operator | `agent/tests/test_wolfbet_commands.py::test_argv`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `stop` | Control > Service "Stop" | operator | `agent/tests/test_wolfbet_commands.py::test_argv`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `restart` | Control > Service "Restart" (runs as a job with live output) | operator | `agent/tests/test_wolfbet_commands.py::test_argv`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `status` | Live page: one card per lane with state, bets and stop reason | viewer | `backend/tests/test_resync.py::test_restart_mid_session_shows_the_running_totals`; `frontend/e2e/panels.spec.ts::each lane gets a card with its state, bet sizes and stop reason` |
| `pause` | Control > Lanes "Pause" (all lanes or one); the header kill switch | operator | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/control.spec.ts::an operator pauses and resumes a lane and cannot reach the danger zone`; `frontend/e2e/control.spec.ts::the header kill switch pauses every lane from the live page` |
| `resume` | Control > Lanes "Resume" | operator | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/control.spec.ts::an operator pauses and resumes a lane and cannot reach the danger zone` |
| `reload` | Control > Lanes "Reload" | operator | `agent/tests/test_wolfbet_commands.py::test_argv`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `monitor` | Live page: lanes, chart, bet tape, streaks and the system panel, updated over the socket | viewer | `frontend/e2e/live.spec.ts::the header shows live totals and each lane state, and follows ticks`; `frontend/e2e/panels.spec.ts::the system panel shows host, units, drops and history lag` |
| `lanes` | Lanes page "Apply", with a preview of each lane's share and a fresh code | operator | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/lanes.spec.ts::a preview shows the diff, each lane share and what restarts, then the change runs` |
| `logs` | Tools > Logs "Follow" (bot, Telegram or web unit) | operator | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/tools.spec.ts::logs load, filter, and follow until Stop cancels the job` |
| `logs-full` | Tools > Logs "Load" | operator | `backend/tests/test_control_api.py::test_reads_run_without_a_confirm_and_return_clean_output`; `frontend/e2e/tools.spec.ts::logs load, filter, and follow until Stop cancels the job` |
| `interactive` | N/A. A foreground TUI that bets in the terminal it runs in. Web terminal | owner | `backend/tests/test_terminal.py::test_the_owner_opens_the_terminal`; `frontend/e2e/terminal.spec.ts::the owner opens the terminal with a code, types, sees output and a close reason` |
| `tmux` | Web terminal. It opens its own tmux session; `tmux attach -t wolfbet` reaches the bot's | owner | `backend/tests/test_terminal.py::test_anyone_but_the_owner_is_refused_before_upgrade`; `frontend/e2e/terminal.spec.ts::an admin cannot use the terminal or replay recordings` |
| `stats` | Analytics page shows the same totals per window. The text report itself: web terminal | viewer | `backend/tests/test_analytics_api.py::test_viewers_get_the_bots_report_once_a_minute` |
| `reset` | Control > Danger zone "Reset statistics"; Lanes page. Needs the service name typed and a fresh code | admin | `backend/tests/test_control_api.py::test_config_and_destructive_actions_need_a_fresh_step_up`; `frontend/e2e/control.spec.ts::an admin reset needs the typed service name and a fresh code, then retries once` |
| `run-reset` | Control > Danger zone "Run reset"; Lanes page | admin | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/lanes.spec.ts::archives are listed and resets stay admin-only` |
| `archives` | Lanes page "List archives" | operator | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/lanes.spec.ts::archives are listed and resets stay admin-only` |
| `session` | Sessions list and session page, read from the history store | viewer | `backend/tests/test_history_api.py::test_session_detail_and_unknown_session`; `frontend/e2e/tape.spec.ts::live sessions are pinned and a past session opens with its bets` |
| `presets` | Not wired. The backtest form takes a preset name. List them in the web terminal | owner | `agent/tests/test_wolfbet_commands.py::test_argv` |
| `analytics` | Analytics page | viewer | `backend/tests/test_analytics_api.py::test_viewers_get_the_bots_report_once_a_minute`; `frontend/e2e/analytics.spec.ts::the report shows formatted like the bot and Apply filters by URL` |
| `last-bets` | Live page bet tape, newest first | viewer | `frontend/e2e/tape.spec.ts::the tape shows the newest bet first and keeps only visible rows in the page` |
| `export` | Tools > Export "Export", then download; Analytics export links | operator | `backend/tests/test_files_api.py::test_the_file_downloads_whole_as_an_attachment_with_a_fixed_name`; `frontend/e2e/tools.spec.ts::an export finishes and downloads its file from the api` |
| `config-show` | Config page (saved values, secrets left out) | operator | `backend/tests/test_config_api.py::test_writers_get_the_schema_and_saved_values_without_secrets`; `backend/tests/test_config_api.py::test_viewers_and_other_services_get_nothing` |
| `config-set` | Config page "Save config": preview first, then save with a fresh code | operator | `agent/tests/test_wolfbet_commands.py::test_config_set_runs_the_dry_run_first_then_the_set`; `frontend/e2e/config.spec.ts::a change is previewed before it can be saved, then saved and confirmed by the reload` |
| `pipeline` | Config page, the `pipeline_depth` field | operator | `frontend/e2e/config.spec.ts::the form is built from the schema, with lane tabs that can use the base` |
| `backtest` | Tools > Backtest "Run backtest" | operator | `backend/tests/test_control_api.py::test_every_action_for_every_role`; `frontend/e2e/tools.spec.ts::a backtest fills its progress bar, then shows the result` |
| `preset` | Not wired. Loading a preset rewrites the config without the Config page's preview. Web terminal | owner | `agent/tests/test_wolfbet_commands.py::test_argv` |
| `cleanup` | Control > Danger zone "Clean up" | admin | `agent/tests/test_wolfbet_commands.py::test_argv`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `benchmark` | Tools > Health "Benchmark" | operator | `frontend/e2e/tools.spec.ts::diagnose shows its report and benchmark streams its job output` |
| `diagnose` | Tools > Health "Diagnose" | operator | `frontend/e2e/tools.spec.ts::diagnose shows its report and benchmark streams its job output` |
| `doctor` | Tools > Health "Doctor"; "Doctor fix" for the fixes | operator; admin for the fixes | `backend/tests/test_control_api.py::test_each_action_sends_its_command_and_validated_args`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `version` | Tools > Health "Version" | operator | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands`; `frontend/e2e/tools.spec.ts::version shows the bot and wolfctl versions` |
| `update` | Control > Danger zone "Update" (branch optional, live output) | admin | `backend/tests/test_control_api.py::test_each_action_sends_its_command_and_validated_args`; `frontend/e2e/control.spec.ts::an update opens the job drawer and streams its output to the end` |
| `tg` | Control > Services, "Telegram bot" row: "Status", "Start", "Stop", "Restart". `tg env` and `tg logs`: web terminal and Tools > Logs | operator | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands`; `frontend/e2e/control.spec.ts::an operator restarts the Telegram bot and checks every service` |
| `web` | Control > Services, "Old web admin" row: "Status", "Start", "Stop", "Restart". `web install`: web terminal | operator | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands`; `frontend/e2e/control.spec.ts::a viewer sees the service buttons but cannot press them` |
| `dash` | N/A. It installs and runs this dashboard, so it stays on the host | owner | `backend/tests/test_terminal.py::test_the_owner_opens_the_terminal` |
| `start-all` | Control > Services "Start all" | operator | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands` |
| `stop-all` | Control > Danger zone "Stop all"; the header kill switch | admin | `agent/tests/test_wolfbet_commands.py::test_argv`; `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `status-all` | Control > Services "Check" | operator | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands`; `frontend/e2e/control.spec.ts::an operator restarts the Telegram bot and checks every service` |
| `help` | N/A. Prints the command list | | |

## Gaps

Reached only through the web terminal, so they need the owner:

- `stats` as text. The Analytics page covers the numbers.
- `presets` and `preset NAME`. A preset button would bypass the Config page's preview and diff.
- `tg env`, `web install`, `setup`, `interactive` and `dash`. These edit host files or need a foreground TTY.
