# Old web parity

The old web was removed from `wolfbet` in wolfctl 3.18 and TG 1.25.0. This table stays as the record of where each of its features went.

Every route and template of the old WolfBet web (`wolfbet/web`, plus the sign-in routes it mounts from `casino-shared/web`) and where it lives in the dashboard. Each row names the tests that cover it. `backend/tests/test_parity_old_web.py` checks that every test named here exists. When `../wolfbet` and `../casino-shared` are checked out next to this repo, it also checks that every route and template has a row.

Telegram users sign in with the bot's `/web` link (the `/tg/login` page) and reach only their own pages under `/tg/<their uid>`: "My bot", "Sessions", a session, and "Settings". Owners and admins (with two-factor) reach any Telegram user there, and the "Telegram users" page lists them. Pages are checked by `frontend/e2e/tg.spec.ts` (stubbed) and `frontend/e2e-stack/tg.spec.ts` (the real stack).

## Routes

| Old route | Dashboard | Tests |
|---|---|---|
| `GET /` | The app's home: a Telegram account goes to its "My bot" page, everyone else to the service's live page | `backend/tests/test_tg_login.py::test_a_link_signs_in_once_and_makes_a_telegram_account` |
| `GET /login` | The dashboard's sign-in page (email, password, two-factor) | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers` |
| `POST /login` | `POST /auth/login`, with per-account and per-IP limits and lockout | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers`; `backend/tests/test_audit.py::test_every_unsafe_route_writes_an_audit_row` |
| `POST /logout` | `POST /auth/logout` | `backend/tests/test_route_auth.py::test_a_signed_in_browser_changes_nothing_without_the_csrf_token` |
| `GET /auth/telegram` | The `/tg/login` page, which sends `POST /auth/telegram` from the bot's `/web` link: one use, five minutes, the token in the URL fragment | `backend/tests/test_tg_login.py::test_a_link_signs_in_once_and_makes_a_telegram_account`; `backend/tests/test_tg_login.py::test_a_bad_or_expired_link_gets_one_message`; `backend/tests/test_tg_login.py::test_a_foreign_page_cannot_sign_the_browser_in` |
| `GET /admin/` | The service's live page and `/admin/status` (host, units, drops, lag) | `backend/tests/test_metrics.py::test_status_is_for_owners_and_admins` |
| `GET /admin/users` | The "Telegram users" page (`GET /tg/users`): every Telegram user with sessions, bets, profit and last session | `backend/tests/test_tg_api.py::test_the_admin_list_names_each_telegram_user_with_totals`; `backend/tests/test_tg_access.py::test_owners_and_admins_reach_any_telegram_user_and_others_do_not` |
| `GET /admin/users/{user_id}` | `GET /tg/{uid}/sessions`, `/status` and `/config` as owner or admin | `backend/tests/test_tg_access.py::test_owners_and_admins_reach_any_telegram_user_and_others_do_not` |
| `GET /admin/users/{user_id}/sessions/{session_id}` | `GET /tg/{uid}/sessions/{epoch_id}/{session_id}` and its `/bets` | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `POST /admin/users/{user_id}/tier` | Not carried: the tier was stored in the web database and never read by the bot | `backend/tests/test_tg_access.py::test_a_telegram_user_is_refused_every_route_but_their_own` |
| `POST /admin/users/{user_id}/block` | Not carried: the block was never enforced. A Telegram account can be disabled, which ends its sessions and refuses its links | `backend/tests/test_tg_login.py::test_a_disabled_telegram_account_cannot_sign_in` |
| `GET /admin/services` | Control page (start, stop, restart per unit) | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands` |
| `GET /admin/logs` | Tools > Logs | `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `GET /admin/audit` | `/admin/audit`, a hash-chained log of every state-changing request | `backend/tests/test_audit.py::test_every_unsafe_route_writes_an_audit_row` |
| `GET /admin/change-password` | Not carried: dashboard accounts change their password through the reset link | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers` |
| `POST /admin/change-password` | Not carried, as above | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers` |
| `GET /dashboard/` | The "My bot" page: live lane, chart, bet tape, running sessions and their controls (`/tg/{uid}/status`, the user's live topic) | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own`; `backend/tests/test_tg_api.py::test_status_and_config_ask_the_bot_for_the_path_uid` |
| `GET /dashboard/sessions` | The "Sessions" page (`GET /tg/{uid}/sessions`, newest first, and "Download CSV") | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `GET /dashboard/sessions/{session_id}` | A session page: its facts, bets and "Download bets CSV" | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `GET /dashboard/settings` | The "Settings" page (`GET /tg/{uid}/config`): the saved settings, read-only, without the API key or proxy | `backend/tests/test_tg_api.py::test_status_and_config_ask_the_bot_for_the_path_uid` |
| `GET /api/metrics` | `/admin/status` and the in-stack `/metrics` | `backend/tests/test_metrics.py::test_status_is_for_owners_and_admins` |
| `GET /api/services` | Control page menu (`GET /{service}/control`) | `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `POST /api/services/{label}/restart` | Control "Restart" | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands` |
| `POST /api/services/{label}/stop` | Control "Stop" | `backend/tests/test_control_api.py::test_side_unit_and_all_service_actions_send_their_wolfctl_commands` |
| `POST /api/services/update` | Control "Update" | `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `GET /api/stats` | `GET /tg/users` totals | `backend/tests/test_tg_api.py::test_the_admin_list_names_each_telegram_user_with_totals` |
| `GET /api/users` | `GET /tg/users` | `backend/tests/test_tg_api.py::test_the_admin_list_names_each_telegram_user_with_totals` |
| `GET /api/users/{user_id}/sessions` | `GET /tg/{uid}/sessions` | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `GET /api/users/{user_id}/sessions/csv` | `GET /tg/{uid}/sessions.csv`, formula-safe | `backend/tests/test_tg_api.py::test_csv_exports_hold_only_the_users_rows_and_never_run_as_formulas` |
| `GET /api/users/{user_id}/sessions/{session_id}` | `GET /tg/{uid}/sessions/{epoch_id}/{session_id}` | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `GET /api/users/{user_id}/sessions/{session_id}/bets` | `GET /tg/{uid}/sessions/{epoch_id}/{session_id}/bets` | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `GET /api/users/{user_id}/sessions/{session_id}/bets/csv` | `GET /tg/{uid}/sessions/{epoch_id}/{session_id}/bets.csv` | `backend/tests/test_tg_api.py::test_csv_exports_hold_only_the_users_rows_and_never_run_as_formulas` |
| `GET /api/users/{user_id}/sessions/{session_id}/chart` | `GET /tg/{uid}/series`, from the user's own bets | `backend/tests/test_tg_api.py::test_series_counts_only_the_users_bets` |
| `GET /api/audit` | `GET /audit` | `backend/tests/test_audit.py::test_every_unsafe_route_writes_an_audit_row` |
| `WS /ws/metrics` | The live socket's `svc:<service>:system` topic | `backend/tests/test_metrics.py::test_a_healthy_stack_is_all_ok` |
| `WS /ws/logs/{service_label}` | Tools > Logs "Follow" (a job streamed over the live socket) | `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `WS /ws/session/{user_id}/{session_id}` | The live socket's `svc:wolfbet-tg:user:<uid>` topics, own uid only | `backend/tests/test_tg_access.py::test_a_telegram_user_follows_only_their_own_topics`; `backend/tests/test_live_users.py::test_one_api_serves_the_daemon_and_the_telegram_users_on_one_socket` |

Starting, stopping, pausing and resuming a Telegram session is new: the old web could only look. `POST /tg/{uid}/control/{start|stop|pause|resume}` covers it (`backend/tests/test_tg_api.py::test_control_sends_the_path_uid_is_idempotent_and_audited`).

## Templates

| Old template | Dashboard | Tests |
|---|---|---|
| `templates/base.html` | The app shell (nav, sign-out) | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers` |
| `templates/login.html` | The sign-in page | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers` |
| `templates/admin/dashboard.html` | Live page and `/admin/status` | `backend/tests/test_metrics.py::test_status_is_for_owners_and_admins` |
| `templates/admin/users.html` | `GET /tg/users` | `backend/tests/test_tg_api.py::test_the_admin_list_names_each_telegram_user_with_totals` |
| `templates/admin/user_detail.html` | `/tg/{uid}/...` as owner or admin | `backend/tests/test_tg_access.py::test_owners_and_admins_reach_any_telegram_user_and_others_do_not` |
| `templates/admin/session_detail.html` | `/tg/{uid}/sessions/{epoch_id}/{session_id}` and its bets | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `templates/admin/services.html` | Control page | `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `templates/admin/logs.html` | Tools > Logs | `backend/tests/test_control_api.py::test_every_action_for_every_role` |
| `templates/admin/audit.html` | `/admin/audit` | `backend/tests/test_audit.py::test_every_unsafe_route_writes_an_audit_row` |
| `templates/admin/change_password.html` | Not carried: the reset link | `backend/tests/test_route_auth.py::test_every_route_but_the_public_ones_refuses_anonymous_callers` |
| `templates/user/dashboard.html` | `/tg/{uid}/sessions` and `/status` | `backend/tests/test_tg_api.py::test_status_and_config_ask_the_bot_for_the_path_uid` |
| `templates/user/history.html` | `/tg/{uid}/sessions` and `sessions.csv` | `backend/tests/test_tg_api.py::test_csv_exports_hold_only_the_users_rows_and_never_run_as_formulas` |
| `templates/user/session.html` | `/tg/{uid}/sessions/{epoch_id}/{session_id}`, its bets and `bets.csv` | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
| `templates/user/settings.html` | `/tg/{uid}/config` | `backend/tests/test_tg_api.py::test_status_and_config_ask_the_bot_for_the_path_uid` |
| `templates/components/chart.html` | `/tg/{uid}/series` and the live user topic | `backend/tests/test_tg_api.py::test_series_counts_only_the_users_bets` |
| `templates/components/pagination.html` | Cursor paging on `/tg/{uid}/sessions` and `before` paging on bets | `backend/tests/test_tg_api.py::test_sessions_and_bets_are_the_users_own` |
