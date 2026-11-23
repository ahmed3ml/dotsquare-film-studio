# Test results (2026-11-11)

## Unit tests (70)
```
test_economics_report (test_quality.Economics) ... ok
test_failed_attempt_needs_analysis_and_no_hard_cap (test_quality.Economics) ... ok
test_gate_approval_does_not_authorize_spend (test_quality.Economics) ... ok
test_record_refuses_nonexistent_output (test_quality.Economics) ... ok
test_spend_requires_authorization_and_is_capped_by_it (test_quality.Economics) ... ok
test_agent_teams_not_enabled_by_project (test_quality.HookGuard) ... ok
test_blocks_engine_owned_files (test_quality.HookGuard) ... ok
test_blocks_locked_file_allows_drafts_and_outside (test_quality.HookGuard) ... ok
test_settings_register_hook_and_script_is_executable_cli (test_quality.HookGuard) ... ok
test_inspect_extracts_frames_and_measures_audio (test_quality.Inspection) ... ok
test_inspect_reports_limitation_when_tools_missing (test_quality.Inspection) ... ok
test_inspect_unavailable_for_non_media (test_quality.Inspection) ... ok
test_techcheck_against_delivery_spec (test_quality.Inspection) ... ok
test_adapters_example_config_valid (test_quality.Providers) ... ok
test_command_provider_executes_without_shell (test_quality.Providers) ... ok
test_config_validation (test_quality.Providers) ... ok
test_connector_and_command_rules (test_quality.Providers) ... ok
test_default_route_is_prompt_file_and_logs_nothing (test_quality.Providers) ... ok
test_gate_required_for_phase (test_quality.Providers) ... ok
test_provider_replacement_by_route_mock_generates_synthetic_media (test_quality.Providers) ... ok
test_creator_cannot_review_own_work (test_quality.ReviewLoop) ... ok
test_criteria_standards_complete (test_quality.ReviewLoop) ... ok
test_evidence_is_mandatory_and_media_needs_files (test_quality.ReviewLoop) ... ok
test_fail_then_revision_round_then_accept (test_quality.ReviewLoop) ... ok
test_not_verified_requires_human_ack_with_note (test_quality.ReviewLoop) ... ok
test_review_stale_after_change_and_void_during_review (test_quality.ReviewLoop) ... ok
test_shot_cannot_be_approved_on_producers_word (test_quality.ReviewLoop) ... ok
test_submit_blocked_without_review (test_quality.ReviewLoop) ... ok
test_unknown_or_missing_criteria_rejected (test_quality.ReviewLoop) ... ok
test_every_advertised_command_is_implemented (test_quality.TeamsModelsUpgrade) ... ok
test_model_policy_strongest_for_all_and_detects_drift (test_quality.TeamsModelsUpgrade) ... ok
test_team_registry_detects_violations (test_quality.TeamsModelsUpgrade) ... ok
test_team_registry_valid_and_complete (test_quality.TeamsModelsUpgrade) ... ok
test_teams_disabled_by_default_and_detected_when_enabled (test_quality.TeamsModelsUpgrade) ... ok
test_upgrade_idempotent (test_quality.TeamsModelsUpgrade) ... ok
test_approval_log_audit_trail (test_studio.ApprovalEnforcement) ... ok
test_approve_requires_human_name (test_studio.ApprovalEnforcement) ... ok
test_blocked_external_tool (test_studio.ApprovalEnforcement) ... ok
test_cannot_approve_without_submit (test_studio.ApprovalEnforcement) ... ok
test_change_after_submit_blocks_approval (test_studio.ApprovalEnforcement) ... ok
test_forged_approval_without_lock_is_caught (test_studio.ApprovalEnforcement) ... ok
test_lock_detects_silent_overwrite_and_revise_reopens (test_studio.ApprovalEnforcement) ... ok
test_media_gate_requires_real_video_file (test_studio.ApprovalEnforcement) ... ok
test_order_enforced (test_studio.ApprovalEnforcement) ... ok
test_placeholder_blocks_submit (test_studio.ApprovalEnforcement) ... ok
test_reject_and_resubmit (test_studio.ApprovalEnforcement) ... ok
test_revise_makes_downstream_stale (test_studio.ApprovalEnforcement) ... ok
test_stage_requires_gate (test_studio.ApprovalEnforcement) ... ok
test_stage_state_forged_in_json_is_caught (test_studio.ApprovalEnforcement) ... ok
test_all_files_created_and_valid (test_studio.Init) ... ok
test_init_never_overwrites_and_validates_id (test_studio.Init) ... ok
test_actual_spend_requires_authorization_and_budget (test_studio.ShotListAndCost) ... ok
test_cost_tracker_append_only_lock (test_studio.ShotListAndCost) ... ok
test_estimate_refuses_to_guess_rates (test_studio.ShotListAndCost) ... ok
test_secret_scan (test_studio.ShotListAndCost) ... ok
test_shot_list_validation (test_studio.ShotListAndCost) ... ok
test_status_columns_can_change_after_lock_planning_cannot (test_studio.ShotListAndCost) ... ok
test_unauthorized_actual_row_caught_by_validate (test_studio.ShotListAndCost) ... ok
test_adapter_contract (test_studio.StaticConfig) ... ok
test_agent_configuration (test_studio.StaticConfig) ... ok
test_gates_and_stages_consistent (test_studio.StaticConfig) ... ok
test_reviewers_cannot_edit_work_products (test_studio.StaticConfig) ... ok
test_settings_and_commands (test_studio.StaticConfig) ... ok
test_skill_discovery (test_studio.StaticConfig) ... ok
test_templates_exist_and_csv_headers (test_studio.StaticConfig) ... ok
test_actual_log_requires_status (test_studio.TasksLogsAdaptersExport) ... ok
test_adapters_never_print_env_values (test_studio.TasksLogsAdaptersExport) ... ok
test_export_marks_draft_and_never_overwrites (test_studio.TasksLogsAdaptersExport) ... ok
test_status_is_arabic (test_studio.TasksLogsAdaptersExport) ... ok
test_task_dependencies (test_studio.TasksLogsAdaptersExport) ... ok

----------------------------------------------------------------------
Ran 70 tests in 3.757s

OK
```

## Smoke test (mock project, temp dir, no network, no spend)
```
PASS studio shot set-status --id SH-001-01 --status APPROVED -p MOCK-SMOKE
PASS studio inspect media/final/mock_final.mp4 --frames 3 -p MOCK-SMOKE
PASS studio review open --target shot:SH-001-01 --set shot_media --creator smoke-creator --files media/final/mock_final.mp4 -p MOCK-SMOKE
PASS studio review close RC-0010 --reviewer smoke-reviewer --verdict-file /var/folders/w1/1nlpp8zs3cd36kngdhh7vx7c0000gn/T/studio_smoke_l05ud93a/projects/MOCK-SMOKE/reviews/verdicts/RC-0010.json -p MOCK-SMOKE
PASS studio shot set-status --id SH-001-01 --status APPROVED -p MOCK-SMOKE
PASS studio report economics -p MOCK-SMOKE
PASS studio teams check -p MOCK-SMOKE
PASS studio models check -p MOCK-SMOKE
PASS studio validate -p MOCK-SMOKE
PASS studio pending -p MOCK-SMOKE
PASS studio export -p MOCK-SMOKE
PASS studio status -p MOCK-SMOKE

SMOKE RESULT: ALL STEPS PASSED (mock content, no external calls)
```

## MUTAH-PILOT
```
$ studio.py validate
VALID
$ studio.py teams check
Agent Teams (experimental): DISABLED — not enabled (experimental feature is off by default); team-mode work falls back to subagents
teams registry VALID
$ studio.py models check
model policy OK (strongest=opus)
$ studio.py status
== حالة الإنتاج: MUTAH-PILOT — The Battle of Mu'tah ==
المرحلة الحالية: لم يبدأ الإنتاج بعد

البوابات:
  GATE-01  لم تبدأ                  البحث التاريخي وفكرة القصة
  GATE-02  لم تبدأ                  المعالجة القصصية
  GATE-03  لم تبدأ                  السيناريو النهائي
  GATE-04  لم تبدأ                  تصميم الشخصيات والدليل البصري
  GATE-05  لم تبدأ                  اللوحة القصصية وقائمة اللقطات
  GATE-06  لم تبدأ                  فيديو إثبات المفهوم
  GATE-07  لم تبدأ                  الميزانية وخطة التنفيذ الكاملة
  GATE-08  لم تبدأ                  النسخة المبدئية (Rough Cut)
  GATE-09  لم تبدأ                  التسليم النهائي للفيلم

```
