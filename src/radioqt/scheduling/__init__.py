from .cron_runtime import next_cron_occurrence, refresh_cron_schedule_entries
from .logic import (
    active_schedule_entry_at,
    normalize_overdue_one_shots,
    normalized_start,
    restore_active_missed_one_shots,
    schedule_entry_end_at,
    schedule_entry_window_details,
    sort_schedule_entries,
)
from .mutations import (
    create_cron_entry,
    create_schedule_entry,
    remove_cron_and_generated_schedule_entries,
    remove_schedule_entries_by_ids,
    ScheduleRemovalSelection,
    ScheduleStatusMutationResult,
    select_schedule_entries_for_removal,
    update_cron_expression,
    update_cron_enabled,
    update_cron_fade_in,
    update_cron_fade_out,
    update_schedule_fade_in,
    update_schedule_fade_out,
    update_schedule_status,
)
from .presentation import (
    current_schedule_entry_for_playback,
    initial_schedule_filter_date,
    runtime_cron_dates,
    schedule_entry_palette_tokens,
    visible_schedule_entries,
)
from .state import (
    PlaySchedulePreparation,
    StartupSchedulePreparation,
    prepare_schedule_entries_for_play,
    prepare_schedule_entries_for_startup,
)
from .workflows import (
    DEFAULT_CRON_RUNTIME_LOOKBACK,
    DEFAULT_CRON_RUNTIME_MAX_OCCURRENCES,
    DEFAULT_CRON_RUNTIME_MAX_RECENT_OCCURRENCES,
    enforce_hard_sync_always,
    is_schedule_entry_protected_from_removal,
    sync_cron_runtime_window,
)

try:
    from .runtime import RadioScheduler
except ModuleNotFoundError:
    RadioScheduler = None  # type: ignore[assignment]

__all__ = [
    "PlaySchedulePreparation",
    "RadioScheduler",
    "StartupSchedulePreparation",
    "active_schedule_entry_at",
    "create_cron_entry",
    "create_schedule_entry",
    "current_schedule_entry_for_playback",
    "DEFAULT_CRON_RUNTIME_LOOKBACK",
    "DEFAULT_CRON_RUNTIME_MAX_OCCURRENCES",
    "DEFAULT_CRON_RUNTIME_MAX_RECENT_OCCURRENCES",
    "enforce_hard_sync_always",
    "initial_schedule_filter_date",
    "is_schedule_entry_protected_from_removal",
    "normalize_overdue_one_shots",
    "normalized_start",
    "next_cron_occurrence",
    "remove_cron_and_generated_schedule_entries",
    "remove_schedule_entries_by_ids",
    "ScheduleRemovalSelection",
    "ScheduleStatusMutationResult",
    "select_schedule_entries_for_removal",
    "prepare_schedule_entries_for_play",
    "prepare_schedule_entries_for_startup",
    "refresh_cron_schedule_entries",
    "restore_active_missed_one_shots",
    "runtime_cron_dates",
    "schedule_entry_palette_tokens",
    "schedule_entry_end_at",
    "schedule_entry_window_details",
    "sort_schedule_entries",
    "sync_cron_runtime_window",
    "update_cron_expression",
    "update_cron_enabled",
    "update_cron_fade_in",
    "update_cron_fade_out",
    "update_schedule_fade_in",
    "update_schedule_fade_out",
    "update_schedule_status",
    "visible_schedule_entries",
]
