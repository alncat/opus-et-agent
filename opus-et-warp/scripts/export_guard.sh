# Shared by the Phase 7 and M exporters after loading their run configuration.
lock_export_target() {
    local processing existing
    processing=$(python3 "$SCRIPT_DIR/scripts/warp_settings.py" processing-folder "$TILTSERIES_SETTINGS") || return 1
    export_lock="$processing/.subtomo-export.lock"
    if ! mkdir "$export_lock"; then
        echo "Cannot lock $processing for export. If a job was killed, verify no exporter is running before removing the empty lock directory." >&2
        return 1
    fi
    trap 'rmdir "$export_lock"' EXIT
    if [ -d "$processing/subtomo" ]; then
        existing=$(find -H "$processing/subtomo" -name '*.mrc' -print -quit) || return 1
        if [ -n "$existing" ] && [ "${ABORT_ON_EXISTING_SUBTOMO:-1}" != 0 ]; then
            echo "ERROR: existing particle $existing. Preserve the subtomo tree and update its STAR paths, or set ABORT_ON_EXISTING_SUBTOMO=0 for intentional replacement." >&2
            return 1
        fi
    fi
}
